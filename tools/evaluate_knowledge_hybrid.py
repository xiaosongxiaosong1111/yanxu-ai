"""Frozen 34-question fair comparison: N3 replay, human-question keyword and hybrid.

Real public originals are re-extracted into a temporary SQLite store, a pinned local BGE
model is loaded for real, and Milvus Lite serves a temporary index. The two human-question
arms share the same query, scope, limit, preprocessing, rule rerank and bounded N3 reads;
only semantic recall is switched. No application database, user Key or remote provider is used.
"""
import argparse
from collections import Counter
import contextlib
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time

# Keep the real model small and deterministic on this machine.
for _name in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ.setdefault(_name, '2')
os.environ.setdefault('ORT_NUM_THREADS', '2')

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'tools'))

from embedding_catalog import LOCAL_MODEL, LOCAL_REVISION, MODEL_SHA256, checked_embedding_config
from knowledge_retrieval import HybridRetriever
from knowledge_store import KnowledgeError, KnowledgeStore
from knowledge_vectors import VectorIndex
from embedding_transport import EmbeddingTransport
import knowledge_vector_store as ledger
import evaluate_knowledge_gold as gold


def _settings(profile_id):
    config = checked_embedding_config({'provider': 'local_bge', 'profile_id': profile_id})

    class Settings:
        @staticmethod
        def runtime_embedding(requested=None):
            if requested not in (None, profile_id):
                raise KnowledgeError('embedding_profile_unavailable', '临时评测配置不可用。', 503)
            return deepcopy(config)

    return config, Settings()


def _arm(store, retriever, question, indexed, entry_ids, top_k, frozen_texts, *, frozen=False):
    started = time.perf_counter()
    if frozen:
        result = store.search_segments(query=question['retrieval_query'], unit_id=question['unit_id'],
                                       year=question['reference_year'], page=1, page_size=top_k)
        retrieval = {'mode': 'keyword', 'degraded': False, 'degraded_reason': '',
                     'reranker': 'n3_segment_terms', 'methods': ['keyword'], 'notice': ''}
        hits = result['segments']
        candidate_count = None
        usage, usage_known = {}, True
    else:
        result = retriever.retrieve(question['human_question'], unit_id=question['unit_id'],
                                    year=question['reference_year'], limit=top_k,
                                    allow_remote=True)
        retrieval = result['retrieval']
        hits = result['segments']
        candidate_count = result['candidate_count']
        usage, usage_known = result['usage'], result['usage_known']
    retrieval_ms = round((time.perf_counter() - started) * 1000, 3)
    read_started = time.perf_counter()
    intervals, read_violations, characters = gold._read_hits(store, hits, indexed, entry_ids)
    read_ms = round((time.perf_counter() - read_started) * 1000, 3)
    evidence = gold._evidence_results(question, indexed, intervals, frozen_texts)
    expected = set(question['source_ids'])
    retrieved = [entry_ids[hit['entry_id']] for hit in hits]
    scope_violations = []
    for hit in hits:
        source = indexed[entry_ids[hit['entry_id']]]['entry']
        if source.get('unit_id') != question['unit_id']:
            scope_violations.append({'source_id': entry_ids[hit['entry_id']], 'code': 'unit_scope_mismatch'})
        if question.get('reference_year') is not None and source.get('year') != question['reference_year']:
            scope_violations.append({'source_id': entry_ids[hit['entry_id']], 'code': 'year_scope_mismatch'})
    return {
        'id': question['id'], 'split': question['split'], 'applicable': question['retrieval_recall_applicable'],
        'source_recalled': bool(expected.intersection(retrieved)),
        'all_required_quotes_read': all(item['read_covered'] for item in evidence),
        'evidence': evidence, 'hits': len(hits), 'matching_segments': len(hits),
        'candidate_count': candidate_count, 'returned_count': len(hits),
        'returned_source_ids': retrieved, 'read_chars': characters,
        'violations': read_violations, 'scope_violations': scope_violations,
        'latency_ms': round(retrieval_ms + read_ms, 3), 'retrieval_ms': retrieval_ms, 'read_ms': read_ms,
        'usage': usage if isinstance(usage, dict) else {}, 'usage_known': bool(usage_known),
        'retrieval_method': retrieval.get('mode'), 'degraded': bool(retrieval.get('degraded')),
        'degraded_reason': retrieval.get('degraded_reason', ''),
        'question': question['human_question'] if not frozen else question['retrieval_query'],
        'source_year': indexed[question['source_id']]['entry'].get('year'),
        'source_quality': indexed[question['source_id']]['entry']['quality']['state'],
        'expected_result': question['expected_result'],
    }


def _scope_available(store, question):
    rows = ledger.eligible(store, unit_id=question['unit_id'], year=question['reference_year'])
    return {'sources': len({row['entry_id'] for row in rows}), 'segments': len(rows),
            'windows_after_build': None}


def _operational(rows):
    usage = [row['usage'] for row in rows if isinstance(row['usage'], dict)]

    def total(key):
        values = [value.get(key) for value in usage if type(value.get(key)) is int]
        return sum(values) if values else 0

    latencies = sorted(row['latency_ms'] for row in rows)
    return {
        'retrieval_calls': len(rows),
        'embedding_calls': total('embedding_calls'),
        'successful_embedding_calls': total('successful_embedding_calls'),
        'input_tokens_known': total('input_tokens'),
        'usage_unknown_calls': sum(not row['usage_known'] for row in rows),
        'degraded_questions': sum(row['degraded'] for row in rows),
        'degraded_reasons': dict(Counter(row['degraded_reason'] for row in rows if row['degraded'])),
        'latency_ms_total': round(sum(latencies), 3),
        'latency_ms_mean': round(sum(latencies) / len(latencies), 3) if latencies else None,
        'latency_ms_p50': round(latencies[len(latencies) // 2], 3) if latencies else None,
        'latency_ms_max': round(max(latencies), 3) if latencies else None,
        'returned_segments': sum(row['returned_count'] for row in rows),
        'read_chars': sum(row['read_chars'] for row in rows),
    }


def _arm_summary(rows, description):
    applicable = [row for row in rows if row['applicable']]
    return {
        'description': description,
        'metrics': gold._metrics(rows),
        'by_split': {name: gold._metrics([row for row in rows if row['split'] == name])
                     for name in ('development', 'holdout')},
        'applicable_scope_violations': sum(len(row['scope_violations']) for row in applicable),
        'operational': _operational(rows),
        'questions': rows,
    }


def _delta(left_rows, right_rows):
    left = {row['id']: row for row in left_rows}
    right = {row['id']: row for row in right_rows}
    gained_recall, lost_recall, gained_quotes, lost_quotes = [], [], [], []
    for identifier, row in left.items():
        if not row['applicable']:
            continue
        other = right[identifier]
        if other['source_recalled'] and not row['source_recalled']:
            gained_recall.append(identifier)
        if row['source_recalled'] and not other['source_recalled']:
            lost_recall.append(identifier)
        if other['all_required_quotes_read'] and not row['all_required_quotes_read']:
            gained_quotes.append(identifier)
        if row['all_required_quotes_read'] and not other['all_required_quotes_read']:
            lost_quotes.append(identifier)
    return {'hybrid_gains_source_recall': gained_recall, 'hybrid_loses_source_recall': lost_recall,
            'hybrid_gains_all_quotes': gained_quotes, 'hybrid_loses_all_quotes': lost_quotes,
            'source_recall_delta': sum(right[key]['source_recalled'] for key in left if left[key]['applicable'])
                                   - sum(left[key]['source_recalled'] for key in left if left[key]['applicable']),
            'all_quotes_delta': sum(right[key]['all_required_quotes_read'] for key in left if left[key]['applicable'])
                                - sum(left[key]['all_required_quotes_read'] for key in left if left[key]['applicable'])}


def _vector_entry_ids(store, space_id):
    if not space_id:
        return set()
    with ledger.connection(store) as handle:
        if not ledger.has_table(handle, 'knowledge_vector_items'):
            return set()
        return {row[0] for row in handle.execute(
            'SELECT DISTINCT entry_id FROM knowledge_vector_items WHERE space_id=? AND committed=1',
            (space_id,))}


def _source_actual_year(corpus, source_id):
    return next(source['actual_year'] for source in corpus['sources'] if source['id'] == source_id)


def _source_metadata_year(corpus, source_id):
    return next(source.get('metadata_year') for source in corpus['sources'] if source['id'] == source_id)


def _challenges(corpus, indexed, arm_rows, vector_entries):
    by_arm = {name: {row['id']: row for row in rows} for name, rows in arm_rows.items()}
    links = corpus.get('publication_links', [])
    output = []
    for question in corpus['questions']:
        if question['retrieval_recall_applicable']:
            continue
        entries = {source_id: indexed[source_id]['entry'] for source_id in question['source_ids']}
        unsafe = [source_id for source_id in question['source_ids']
                  if _source_actual_year(corpus, source_id) is None]
        arms = {
            name: {
                'returned_source_ids': by_arm[name][question['id']]['returned_source_ids'],
                'unsafe_sources_absent': not (set(unsafe) & set(by_arm[name][question['id']]['returned_source_ids'])),
                'degraded': by_arm[name][question['id']]['degraded'],
                'degraded_reason': by_arm[name][question['id']]['degraded_reason'],
            }
            for name in by_arm
        }
        chain = []
        for link in links:
            if link.get('case_id') != question['id'] or 'year' not in link:
                continue
            attachment = link['attachment_source_id']
            runtime_year = indexed[attachment]['entry'].get('year') if attachment in indexed else None
            chain.append({'parent_source_id': link['parent_source_id'], 'attachment_source_id': attachment,
                          'audited_link_year': link['year'], 'runtime_inferred_year': runtime_year,
                          'runtime_adopted_link_year': runtime_year == link['year']})
        output.append({
            'id': question['id'], 'split': question['split'],
            'unit_id': question['unit_id'], 'reference_year': question.get('reference_year'),
            'question_year': question.get('question_year'), 'source_year': question.get('source_year'),
            'unsafe_year_source_ids': unsafe,
            'source_runtime_years': {key: value.get('year') for key, value in entries.items()},
            'source_runtime_quality': {key: value['quality']['state'] for key, value in entries.items()},
            'metadata_year_not_adopted': {
                key: {'metadata_year': _source_metadata_year(corpus, key), 'runtime_year': value.get('year')}
                for key, value in entries.items() if _source_metadata_year(corpus, key) is not None},
            'unsafe_sources_absent_from_eligible_vectors': not (set(unsafe) & vector_entries),
            'arms': arms,
            'publication_chain': chain,
            'publication_chain_runtime_supported': all(item['runtime_adopted_link_year'] for item in chain)
                                                    if chain else False,
            'publication_chain_note': 'Runtime support is asserted only when an audited publication-link year is actually reproduced by the runtime; unchanged, it stays false.',
        })
    return output


def evaluate(project=PROJECT_ROOT, fixture=gold.FIXTURE, *, split='all', top_k=8, profile_id='ep_local_eval'):
    if split not in {'all', 'development', 'holdout'} or type(top_k) is not int or not 1 <= top_k <= 50:
        raise ValueError('Invalid evaluation split or hit limit.')
    corpus = gold.load_fixture(project, fixture)
    if corpus['counts']['questions'] != 34 or corpus['counts']['sources'] != 29:
        raise ValueError('Frozen corpus counts changed.')
    frozen_texts = {item['text_path']: gold._local(fixture, item['text_path']).read_text('utf-8')
                    for question in corpus['questions'] for item in question['expected_evidence']}
    questions = [q for q in corpus['questions'] if split == 'all' or q['split'] == split]
    with tempfile.TemporaryDirectory(prefix='yanxu-n4-c2-gold-', ignore_cleanup_errors=True) as temporary, contextlib.ExitStack() as stack:
        store = KnowledgeStore(temporary)
        indexed = gold._index_corpus(project, corpus, store)
        entry_ids = {value['entry']['id']: key for key, value in indexed.items()}
        config, settings = _settings(profile_id)
        # The review model lives in the project's prepared model cache; only the store and
        # vector index are temporary. No user settings or application database is read.
        index = VectorIndex(store, settings, EmbeddingTransport(project / 'data'))
        stack.callback(index.close)
        started = time.perf_counter()
        index.build()
        build_seconds = round(time.perf_counter() - started, 3)
        status = index.status()
        space_id = status.get('active_space_id')
        vector_entries = _vector_entry_ids(store, space_id)
        hybrid = HybridRetriever(store, index)
        keyword = HybridRetriever(store, None)
        # Cold start is recorded and excluded from the measured paired arms.
        cold_started = time.perf_counter()
        cold = hybrid.retrieve('招生单位', limit=1, allow_remote=True)
        cold_ms = round((time.perf_counter() - cold_started) * 1000, 3)
        # A generic keyword query warms SQLite/FTS without touching frozen questions.
        keyword_warm_started = time.perf_counter()
        keyword.retrieve('招生单位', limit=1, allow_remote=True)
        keyword_warm_ms = round((time.perf_counter() - keyword_warm_started) * 1000, 3)
        scope = {q['id']: _scope_available(store, q) for q in questions}
        arms = {'frozen_retrieval_query': [], 'human_keyword': [], 'human_hybrid': []}
        order = []
        for number, question in enumerate(questions):
            arms['frozen_retrieval_query'].append(_arm(store, None, question, indexed, entry_ids,
                                                       top_k, frozen_texts, frozen=True))
            first, second = ('human_keyword', keyword), ('human_hybrid', hybrid)
            if number % 2:
                first, second = second, first
            order.append({'id': question['id'], 'first': first[0], 'second': second[0]})
            arms[first[0]].append(_arm(store, first[1], question, indexed, entry_ids, top_k, frozen_texts))
            arms[second[0]].append(_arm(store, second[1], question, indexed, entry_ids, top_k, frozen_texts))
        summaries = {
            'frozen_retrieval_query': _arm_summary(arms['frozen_retrieval_query'],
                'Original frozen retrieval_query replayed through N3 keyword search; compatibility baseline, not a natural-language arm.'),
            'human_keyword': _arm_summary(arms['human_keyword'],
                'Same original human_question with no vector index: HybridRetriever keyword channel, shared RRF/rule rerank and bounded reads.'),
            'human_hybrid': _arm_summary(arms['human_hybrid'],
                'Same original human_question with real local BGE + real Milvus Lite + keyword channel, shared RRF/rule rerank and bounded reads.'),
        }
        pair = _delta(arms['human_keyword'], arms['human_hybrid'])
        challenges = _challenges(corpus, indexed, arms, vector_entries)
        sources = [{'id': source['id'], 'unit_id': source['unit_id'], 'actual_year': source['actual_year'],
                    'metadata_year': source.get('metadata_year'),
                    'inferred_year': indexed[source['id']]['entry'].get('year'),
                    'quality': indexed[source['id']]['entry']['quality'],
                    'semantic_indexable': source.get('semantic_indexable'),
                    'extraction_status': indexed[source['id']]['extraction']['extraction_status'],
                    'text_sha256': indexed[source['id']]['extraction']['text_sha256']}
                   for source in corpus['sources']]
        result = {
            'corpus_id': corpus['corpus_id'], 'corpus_sha256': gold.CORPUS_SHA256,
            'freeze_manifest_sha256': gold.FREEZE_SHA256, 'split': split, 'top_k': top_k,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'split_notice': 'Original split labels are preserved; both groups were inspected during debugging. This is not a blind holdout evaluation.',
            'environment': {
                'model': LOCAL_MODEL, 'model_revision': LOCAL_REVISION, 'model_sha256': MODEL_SHA256,
                'provider': config['provider'], 'dimension': config['dimension'],
                'normalization': config['normalization'], 'input_version': config['input_version'],
                'cpu_threads_requested': 2, 'python': sys.version.split()[0],
            },
            'index': {'build_seconds': build_seconds, 'active_space_id': space_id,
                      'status': status['status'], 'backend': status['backend'],
                      'vector_entry_sources': len(vector_entries),
                      'vector_items': status['spaces'][0]['windows'] if status['spaces'] else None,
                      'last_build': status['last_build'],
                      'eligible_n3_entries': len({row['entry_id'] for row in ledger.eligible(store)}),
                      'eligible_n3_segments': len(ledger.eligible(store))},
            'warmup': {'cold_first_hybrid_query_ms': cold_ms,
                       'cold_degraded': cold['retrieval']['degraded'],
                       'cold_usage': cold['usage'], 'cold_usage_known': cold['usage_known'],
                       'keyword_warm_query_ms': keyword_warm_ms,
                       'note': 'Cold first hybrid query and a generic keyword warm-up are excluded from the measured pairs.'},
            'fairness': {
                'query': 'Both human arms use the exact frozen human_question; the frozen arm uses retrieval_query only as a compatibility baseline.',
                'scope': 'unit_id + reference_year preserved from each frozen question for all arms.',
                'limit': top_k, 'read_budget': {'before': 3, 'after': 3, 'max_chars': 12000},
                'shared': 'Keyword preprocessing, finite candidate window (40), equal-weight RRF, rule_v2 rerank and N3 bounded reads.',
                'only_variable': 'Presence of the real local semantic channel in human_hybrid.',
                'tie_break': 'RRF uses sum(1/(60+rank)); rows sort by (-rrf_score, segment id). rule_v2 chooses by (-retrieval_score, segment id) and measures novelty on the same bounded read window. No UUID or timestamp changes scores.',
                'order': 'Arms alternate first position by question number; order is recorded per question.',
                'order_by_question': order,
            },
            'arms': summaries,
            'paired_human_delta': pair,
            'scope_available_by_question': scope,
            'challenges': challenges,
            'sources': sources,
            'year_mismatches': [{'source_id': source['id'], 'actual_year': source['actual_year'],
                                 'runtime_year': indexed[source['id']]['entry'].get('year')}
                                for source in corpus['sources']
                                if source['actual_year'] is not None
                                and indexed[source['id']]['entry'].get('year') != source['actual_year']],
            'network_calls': 0, 'application_database_reads': 0, 'remote_model_calls': 0,
            'limits': 'Historical public snapshots only. Source/quote coverage is not answer accuracy; semantic similarity does not prove eligibility or subject correctness. Cost is not fabricated without prices.',
        }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--fixture', type=Path, default=gold.FIXTURE)
    parser.add_argument('--split', choices=['all', 'development', 'holdout'], default='all')
    parser.add_argument('--top-k', type=int, default=8)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    result = evaluate(args.project_root.resolve(), args.fixture.resolve(), split=args.split, top_k=args.top_k)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + '\n', 'utf-8')
    print(encoded)
    failed = (result['arms']['human_hybrid']['metrics']['runtime_span_or_budget_violations']
              or result['arms']['human_hybrid']['applicable_scope_violations']
              or result['year_mismatches']
              or any(not item['unsafe_sources_absent_from_eligible_vectors'] for item in result['challenges'])
              or any(not arm['unsafe_sources_absent'] for item in result['challenges'] for arm in item['arms'].values()))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
