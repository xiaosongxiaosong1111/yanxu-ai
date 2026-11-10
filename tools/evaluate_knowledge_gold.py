"""Offline source/chunk evaluation; never reads the running application's database."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_store import KnowledgeStore
from public_sources import document_attribution, extract_saved_document
from research_tools import _document_scope


CORPUS_SHA256 = '0b46b370bb75215fac0fa3c028a80309f37b28c300b0b36f7fa9cd04448edcd9'
FREEZE_SHA256 = '922ab5e31cfee1db239fedc990e3e99b948b9c320b2a0b9b3371b9cd5323c1ce'
FIXTURE = PROJECT_ROOT / 'tests/fixtures/knowledge_gold'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def compact(text):
    positions = [index for index, char in enumerate(text) if not char.isspace()]
    return ''.join(text[index] for index in positions), positions


def _mapped_frozen_offset(text, evidence, frozen_text):
    if not isinstance(frozen_text, str): return None
    start, end = evidence.get('char_start'), evidence.get('char_end')
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(frozen_text): return None
    if frozen_text[start:end] != evidence['quote']: return None
    canonical, offsets = compact(text)
    reference, _ = compact(frozen_text)
    if canonical != reference: return None
    left = len(compact(frozen_text[:start])[0])
    right = len(compact(frozen_text[:end])[0])
    return (offsets[left], offsets[right - 1] + 1) if left < right else None


def locate_quote(extraction, evidence, *, frozen_text=None):
    pages = extraction.get('pages', [])
    page = evidence.get('page')
    location = next((item for item in pages if item['page'] == page), None) if page is not None else None
    if page is not None and location is None: return None
    start, end = (location['char_start'], location['char_end']) if location else (0, len(extraction['text']))
    haystack, offsets = compact(extraction['text'][start:end])
    needle, _ = compact(evidence['quote'])
    if not needle: return None
    found = list(re.finditer(re.escape(needle), haystack))
    if len(found) != 1:
        for region_start, region_end in ((start, end), (0, len(extraction['text']))):
            mapped = _mapped_frozen_offset(extraction['text'][region_start:region_end], evidence, frozen_text)
            if mapped:
                span = region_start + mapped[0], region_start + mapped[1]
                if start <= span[0] < span[1] <= end: return span
        return None
    return start + offsets[found[0].start()], start + offsets[found[0].end() - 1] + 1


def covered(span, intervals, text):
    if span is None: return False
    return all(char.isspace() or any(start <= position < end for start, end in intervals)
               for position, char in enumerate(text[span[0]:span[1]], span[0]))


def valid_span(value, extraction):
    start, end = value.get('char_start'), value.get('char_end')
    text = extraction['text']
    if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text): return False
    if value.get('text') != text[start:end]: return False
    if extraction.get('page_kind') != 'physical': return True
    pages = [page['page'] for page in extraction['pages']
             if page['char_start'] < end and page['char_end'] > start]
    return bool(pages and value.get('page_start') == pages[0] and value.get('page_end') == pages[-1])


def _local(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()): raise ValueError('Gold path escapes its root.')
    return path


def _checked_file(root, name, expected):
    raw = _local(root, name).read_bytes()
    if digest(raw) != expected: raise ValueError('Gold integrity mismatch: ' + name)
    return raw


def _selected_members(manifest, paths):
    entries = [(item['path'], item, package['name']) for package in manifest['packages']
               for item in package['files'] if item['path'] in paths]
    selected = {name: (item, package) for name, item, package in entries}
    if len(entries) != len(selected):
        raise ValueError('Gold input has duplicate bundle members.')
    if selected.keys() != paths:
        raise ValueError('Gold input is missing from the public bundle manifest.')
    return selected


def _checked_archive_input(project, package, name, expected):
    try:
        with zipfile.ZipFile(_local(project / 'data/bundles', package)) as archive:
            entries = [item for item in archive.infolist() if item.filename == name]
            if len(entries) != 1 or entries[0].file_size != expected['size']:
                raise ValueError('Gold archive input is missing, duplicated or has changed size: ' + name)
            raw = archive.read(entries[0])
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError('Gold archive input cannot be read: ' + name) from exc
    if digest(raw) != expected['sha256']:
        raise ValueError('Gold archive integrity mismatch: ' + name)


def _check_public_inputs(project, corpus, reviewed):
    paths = {'data/units.json', *(source['original_member'] for source in corpus['sources'])}
    frozen = _selected_members(reviewed, paths)
    current = _selected_members(json.loads(_local(project, 'data/bundles/manifest.json').read_bytes()), paths)
    for name in sorted(paths):
        expected, _ = frozen[name]
        member, package = current[name]
        if (type(member.get('size')) is not int or member['size'] != expected['size']
                or member.get('sha256') != expected['sha256']):
            raise ValueError('Gold public input changed from the reviewed bundle: ' + name)
        raw = _local(project, name).read_bytes()
        if len(raw) != expected['size'] or digest(raw) != expected['sha256']:
            raise ValueError('Gold public input integrity mismatch: ' + name)
        _checked_archive_input(project, package, name, expected)


def load_fixture(project=PROJECT_ROOT, fixture=FIXTURE):
    manifest = json.loads(_checked_file(fixture, 'manifest.json', FREEZE_SHA256))
    corpus = json.loads(_checked_file(fixture, 'corpus.json', CORPUS_SHA256))
    reviewed = json.loads(_checked_file(fixture, 'public_bundle_manifest.json',
                                        manifest['public_bundle_manifest_sha256']))
    # Pin the reviewed inputs, so unrelated catalog growth does not redefine the gold corpus.
    _check_public_inputs(project, corpus, reviewed)
    for name, expected in manifest['files'].items():
        if name.startswith('texts/'): _checked_file(fixture, name, expected)
    _check_questions(corpus, fixture)
    return corpus


def _check_questions(corpus, fixture):
    splits = {}
    sources = {source['id']: source for source in corpus['sources']}
    for question in corpus['questions']:
        for source_id in question['source_ids']:
            raw = sources[source_id]['raw_sha256']
            if splits.setdefault(raw, question['split']) != question['split']:
                raise ValueError('Shared source crosses the frozen development/holdout split.')
        for evidence in question['expected_evidence']:
            raw = _checked_file(fixture, evidence['text_path'], evidence['text_sha256'])
            text = raw.decode('utf-8')[evidence['char_start']:evidence['char_end']]
            if text != evidence['quote'] or digest(text.encode()) != evidence['quote_sha256']:
                raise ValueError('Frozen evidence span changed: ' + question['id'])


def _bundle_members(project):
    manifest = json.loads((project / 'data/bundles/manifest.json').read_text('utf-8'))
    return {item['path']: item for package in manifest['packages'] for item in package['files']}


def _read_original(project, source, members):
    member = members[source['original_member']]
    if member['sha256'] != source['raw_sha256']: raise ValueError('Original is not in the reviewed bundle.')
    raw = _checked_file(project, source['original_member'], source['raw_sha256'])
    if len(raw) != member['size']: raise ValueError('Original size changed.')
    return raw


def _index_source(project, store, source, unit, members):
    raw = _read_original(project, source, members)
    media = 'application/pdf' if source['format'] == 'pdf' else 'text/html'
    document = extract_saved_document(raw, media, source['url'], timeout=60)
    attribution = document_attribution(source['url'], unit, document['text'],
                                       document['document_title'], document['document_heading'])
    if attribution['status'] != 'verified': raise ValueError('Frozen source attribution failed.')
    # Offline replay reuses the frozen original and recorded timestamp; no fresh network check is claimed.
    document = {**document, 'attribution_status': attribution['status']}
    heading, year = _document_scope(document)
    record = {'title': heading or source['title'], 'url': source['url'], 'unit_id': source['unit_id'],
              'unit_name': source['unit_name'], 'year': year, 'requested_year': 2028,
              'status': 'official_original_read', 'sha256': source['raw_sha256'],
              'checked_at': source.get('checked_at')}
    saved = store.save_source(record, document=document)
    return {'entry': saved, 'extraction': document['extraction']}


def _index_corpus(project, corpus, store):
    units = {item['id']: item for item in json.loads((project / 'data/units.json').read_text('utf-8-sig'))['units']}
    members = _bundle_members(project)
    indexed = {}
    for source in corpus['sources']:
        indexed[source['id']] = _index_source(project, store, source, units[source['unit_id']], members)
    return indexed


def _read_hits(store, hits, indexed, entry_ids):
    intervals, violations, characters = {}, [], 0
    for hit in hits:
        source_id = entry_ids[hit['entry_id']]
        extraction = indexed[source_id]['extraction']
        result = store.read_segment(hit['id'], before=3, after=3, max_chars=12000)
        values = [result['segment'], *result['contexts'], *result['neighbors']]
        actual_chars = sum(len(value.get('text', '')) for value in values)
        if actual_chars > 12000 or result['returned_chars'] != actual_chars:
            violations.append({'source_id': source_id, 'code': 'read_budget_mismatch'})
        characters += actual_chars
        for value in values:
            if not value.get('text'): continue
            if valid_span(value, extraction):
                intervals.setdefault(source_id, []).append((value['char_start'], value['char_end']))
            else: violations.append({'source_id': source_id, 'code': 'runtime_span_or_page_invalid'})
    return intervals, violations, characters


def _evidence_results(question, indexed, intervals, frozen_texts):
    output = []
    for evidence in question['expected_evidence']:
        source_id = evidence['source_id']
        extraction = indexed[source_id]['extraction']
        span = locate_quote(extraction, evidence, frozen_text=frozen_texts[evidence['text_path']])
        output.append({'source_id': source_id, 'role': evidence['role'], 'expected_page': evidence['page'],
                       'extracted': span is not None, 'runtime_span': list(span) if span else None,
                       'read_covered': covered(span, intervals.get(source_id, []), extraction['text'])})
    return output


def _question_result(store, question, indexed, entry_ids, top_k, frozen_texts):
    result = store.search_segments(query=question['retrieval_query'], unit_id=question['unit_id'],
                                   year=question['reference_year'], page=1, page_size=top_k)
    hits = result['segments']
    intervals, violations, characters = _read_hits(store, hits, indexed, entry_ids)
    evidence = _evidence_results(question, indexed, intervals, frozen_texts)
    expected = set(question['source_ids'])
    retrieved = [entry_ids[hit['entry_id']] for hit in hits]
    return {'id': question['id'], 'split': question['split'], 'applicable': question['retrieval_recall_applicable'],
            'retrieval_query': question['retrieval_query'], 'source_recalled': bool(expected.intersection(retrieved)),
            'all_required_quotes_read': all(item['read_covered'] for item in evidence),
            'evidence': evidence, 'hits': len(hits), 'matching_segments': result['total'],
            'read_chars': characters, 'violations': violations,
            'source_year': indexed[question['source_id']]['entry'].get('year'),
            'source_quality': indexed[question['source_id']]['entry']['quality']['state'],
            'expected_result': question['expected_result']}


def _metrics(rows):
    eligible = [row for row in rows if row['applicable']]
    evidence = [value for row in rows for value in row['evidence']]
    trusted_evidence = [value for row in eligible for value in row['evidence']]
    return {'questions': len(rows), 'trusted_recall_denominator': len(eligible),
            'source_recall_hits': sum(row['source_recalled'] for row in eligible),
            'all_required_quotes_read': sum(row['all_required_quotes_read'] for row in eligible),
            'gold_quotes': len(evidence), 'quotes_present_after_extraction': sum(item['extracted'] for item in evidence),
            'quotes_read_with_valid_spans': sum(item['read_covered'] for item in evidence),
            'trusted_quote_denominator': len(trusted_evidence),
            'trusted_quotes_read': sum(item['read_covered'] for item in trusted_evidence),
            'runtime_span_or_budget_violations': sum(len(row['violations']) for row in rows)}


def _challenge_results(rows):
    output = []
    for row in rows:
        if row['applicable']: continue
        safe = row['source_year'] is None and row['source_quality'] != 'accepted'
        output.append({'id': row['id'], 'standalone_year_remains_unknown': safe,
                       'publication_chain_runtime_supported': False,
                       'note': 'Frozen publication links are auditable; runtime does not adopt their year automatically.'})
    return output


def evaluate(project=PROJECT_ROOT, fixture=FIXTURE, *, split='all', top_k=8):
    if split not in {'all', 'development', 'holdout'} or type(top_k) is not int or not 1 <= top_k <= 50:
        raise ValueError('Invalid evaluation split or hit limit.')
    corpus = load_fixture(project, fixture)
    frozen_texts = {item['text_path']: _local(fixture, item['text_path']).read_text('utf-8')
                    for question in corpus['questions'] for item in question['expected_evidence']}
    with tempfile.TemporaryDirectory(prefix='yanxu-real-gold-') as temporary:
        store = KnowledgeStore(temporary)
        indexed = _index_corpus(project, corpus, store)
        entry_ids = {value['entry']['id']: key for key, value in indexed.items()}
        questions = [q for q in corpus['questions'] if split == 'all' or q['split'] == split]
        rows = [_question_result(store, question, indexed, entry_ids, top_k, frozen_texts) for question in questions]
    sources = [{'id': source['id'], 'unit_id': source['unit_id'], 'actual_year': source['actual_year'],
                'inferred_year': indexed[source['id']]['entry'].get('year'),
                'quality': indexed[source['id']]['entry']['quality'],
                'extraction_status': indexed[source['id']]['extraction']['extraction_status'],
                'text_sha256': indexed[source['id']]['extraction']['text_sha256']}
               for source in corpus['sources']]
    return {'corpus_id': corpus['corpus_id'], 'corpus_sha256': CORPUS_SHA256,
            'freeze_manifest_sha256': FREEZE_SHA256, 'split': split, 'top_k': top_k,
            'split_notice': 'Original split labels are preserved; both groups were inspected during debugging. This is not a blind holdout evaluation.',
            'strategy': 'Frozen keyword query, strict school/year scope, first page, bounded adjacent reads; no LLM.',
            'metrics': _metrics(rows),
            'by_split': {name: _metrics([row for row in rows if row['split'] == name])
                         for name in ('development', 'holdout')},
            'source_quality_counts': dict(Counter(source['quality']['state'] for source in sources)),
            'challenges': _challenge_results(rows), 'sources': sources, 'questions': rows,
            'network_calls': 0, 'application_database_reads': 0, 'model_calls': 0,
            'limits': 'Historical public snapshots only. Keyword/read coverage is not answer accuracy, vector recall or live URL verification.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--fixture', type=Path, default=FIXTURE)
    parser.add_argument('--split', choices=['all', 'development', 'holdout'], default='all')
    parser.add_argument('--top-k', type=int, default=8)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    result = evaluate(args.project_root.resolve(), args.fixture.resolve(), split=args.split, top_k=args.top_k)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + '\n', 'utf-8')
    print(encoded)
    return 1 if result['metrics']['runtime_span_or_budget_violations'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
