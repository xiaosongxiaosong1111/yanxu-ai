"""Bounded, replayable temporary directions from explicitly read official blocks."""
from copy import deepcopy
import hashlib
import json

from public_sources import official_source_url
from research_store import ResearchError
import research_candidates


MAX_RECEIPTS = 64
MAX_CANDIDATES = 24
_VERSIONS = ('entry_id', 'extraction_id', 'generation_id')
_BLOCK_STATE = ('kind', 'row_complete', 'continues_previous', 'continues_next', 'content_truncated')


def initialize(tools):
    tools._source_read_receipts = {}
    tools._pending_source_read = None
    tools._source_candidate_records = {}
    tools._candidate_refreshing = False


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _key(receipt):
    return (receipt['segment_id'], receipt['char_start'], receipt['char_end'],
            receipt['anchor_id'], receipt['before'], receipt['after'], receipt['max_chars'])


def _trusted(tools, source):
    if not isinstance(source, dict):
        return False
    quality = source.get('quality') or source.get('knowledge_quality') or {}
    if (source.get('status') != 'official_original_read' or source.get('original_available') is not True
            or quality.get('state') != 'accepted' or quality.get('reusable') is not True
            or source.get('validity', 'active') != 'active' or source.get('usable') is False
            or source.get('year') != tools.task['reference_year']):
        return False
    try:
        unit = tools._unit(source.get('unit_id'))
    except ResearchError:
        return False
    return official_source_url(str(source.get('url') or ''), unit)


def _blocks(payload):
    selected = payload.get('segment') or {}
    identity = {key: selected.get(key) for key in _VERSIONS}
    result = []
    for group in ('segment', 'contexts', 'neighbors'):
        items = [selected] if group == 'segment' else payload.get(group, [])
        for block in items:
            if not isinstance(block, dict):
                continue
            result.append({**identity, **block, 'id': block.get('id') or selected.get('id'),
                           'kind': block.get('kind') or block.get('role'), '_group': group})
    return result


def _delivery_signature(result):
    return tuple((block.get('id'), block.get('char_start'), block.get('char_end'),
                  _digest(block['text']) if isinstance(block.get('text'), str) else None)
                 for block in _blocks(result))


def register_read(tools, result, before, after, limit):
    """Keep pointers and digests only; a search hit never reaches this function."""
    tools._pending_source_read = None
    selected = result.get('segment') or {}
    source_id = selected.get('source_id')
    source = selected.get('source') or {}
    if not source_id or not _trusted(tools, source):
        return
    pending = {'signature': _delivery_signature(result), 'prior': dict(tools._source_read_receipts), 'keys': set()}
    for block in _blocks(result):
        body = block.get('text')
        start, end = block.get('char_start'), block.get('char_end')
        if (not isinstance(body, str) or not body or block.get('content_truncated')
                or type(start) is not int or type(end) is not int or end-start != len(body)):
            continue
        receipt = {'segment_id': block['id'], 'anchor_id': selected['id'], 'source_id': source_id,
                   'char_start': start, 'char_end': end, 'text_sha256': _digest(body),
                   'sha256': source.get('sha256'), 'url': source.get('url'),
                   'unit_id': source.get('unit_id'), 'year': source.get('year'),
                   'before': before, 'after': after, 'max_chars': limit, 'group': block['_group'],
                   **{key: block.get(key) for key in (*_VERSIONS, *_BLOCK_STATE)}}
        key = _key(receipt)
        pending['keys'].add(key)
        tools._source_read_receipts = {**{k: v for k, v in tools._source_read_receipts.items() if k != key},
                                       key: receipt}
        if len(tools._source_read_receipts) > MAX_RECEIPTS:
            tools._source_read_receipts = dict(list(tools._source_read_receipts.items())[-MAX_RECEIPTS:])
    tools._pending_source_read = pending


def confirm_delivery(tools, original, delivered):
    """A model projection authorizes only blocks it delivered in full."""
    pending = tools._pending_source_read
    if pending is None or pending['signature'] != _delivery_signature(original):
        return
    delivered_hashes = {}
    for block in _blocks(delivered):
        body = block.get('text')
        if isinstance(body, str) and not block.get('content_truncated'):
            delivered_hashes[(block.get('id'), block.get('char_start'), block.get('char_end'))] = _digest(body)
    current = dict(pending['prior'])
    for key, receipt in tools._source_read_receipts.items():
        if key in pending['keys'] and delivered_hashes.get(key[:3]) == receipt['text_sha256']:
            current = {**{old_key: value for old_key, value in current.items() if old_key != key}, key: receipt}
    tools._source_read_receipts = dict(list(current.items())[-MAX_RECEIPTS:])
    tools._pending_source_read = None


def _window_key(receipt):
    return tuple(receipt.get(key) for key in ('anchor_id', 'source_id', *_VERSIONS, 'before', 'after', 'max_chars'))


def _matches_block(block, receipt):
    body = block.get('text')
    return (block.get('id') == receipt.get('segment_id') and block.get('_group') == receipt.get('group')
            and all(block.get(key) == receipt.get(key) for key in
                    (*_VERSIONS, *_BLOCK_STATE, 'char_start', 'char_end'))
            and isinstance(body, str) and not block.get('content_truncated')
            and len(body) == receipt['char_end']-receipt['char_start'] and _digest(body) == receipt['text_sha256'])


def _load_receipt(tools, receipt, receipts):
    """Reread the exact window and recheck metadata, domain, versions and content."""
    try:
        store = tools._knowledge()
        source = store.get(receipt['entry_id'])
        if not _trusted(tools, source) or source.get('id') != receipt['entry_id']:
            return None
        if any(source.get(key) != receipt.get(key) for key in ('unit_id', 'year', 'sha256', 'url')):
            return None
        extraction = source.get('extraction') or {}
        if extraction.get('id') != receipt['extraction_id'] or extraction.get('generation_id') != receipt['generation_id']:
            return None
        ledger = tools.sources.get(receipt['source_id']) or {}
        if (ledger.get('knowledge_id') != receipt['entry_id'] or ledger.get('knowledge_current') is False
                or any(ledger.get(key) != source.get(key) for key in ('unit_id', 'year', 'sha256', 'url'))):
            return None
        payload = store.read_segment(receipt['anchor_id'], before=receipt['before'], after=receipt['after'],
                                     max_chars=receipt['max_chars'], trusted_only=True)
        if (payload.get('segment') or {}).get('id') != receipt['anchor_id']:
            return None
        blocks = _blocks(payload)
        for block in blocks:
            if not _matches_block(block, receipt):
                continue
            current = store.get(receipt['entry_id'])
            if current != source:
                return None
            read_blocks, read_receipts = [], []
            for other in receipts:
                if _window_key(other) != _window_key(receipt):
                    continue
                sibling = next((value for value in blocks if _matches_block(value, other)), None)
                if sibling is None:
                    return None
                read_blocks.append({key: value for key, value in sibling.items() if key not in {'source', '_group'}})
                read_receipts.append(deepcopy(other))
            return {'block': {key: value for key, value in block.items() if key != '_group'},
                    'source': deepcopy(source), 'source_id': receipt['source_id'],
                    'read_blocks': read_blocks, '_read_receipts': read_receipts,
                    'read_complete': True, 'trusted': True, 'current': True}
    except Exception:
        return None
    return None


def _resolver(tools, receipts, cache=None):
    used, cache = {}, {} if cache is None else cache

    def resolve(citation):
        if not isinstance(citation, dict):
            return None
        start, end = citation.get('char_start'), citation.get('char_end')
        if type(start) is not int or type(end) is not int or start >= end:
            return None
        for receipt in sorted(receipts, key=lambda row: (-(row.get('before', 0)+row.get('after', 0)),
                                                        -row.get('max_chars', 0))):
            if (receipt.get('segment_id') != citation.get('segment_id')
                    or receipt.get('char_start', end) > start or receipt.get('char_end', start) < end):
                continue
            window = [row for row in receipts if _window_key(row) == _window_key(receipt)]
            token = json.dumps([receipt, window], sort_keys=True, ensure_ascii=False)
            if token not in cache:
                cache[token] = _load_receipt(tools, receipt, window)
            value = cache[token]
            if value is not None:
                contexts, context_receipts, seen = {}, {}, set()
                related = [row for row in receipts if all(row.get(key) == receipt.get(key)
                           for key in ('source_id', *_VERSIONS))]
                for other in related:
                    window_key = _window_key(other)
                    if window_key in seen:
                        continue
                    seen.add(window_key)
                    siblings = [row for row in related if _window_key(row) == window_key]
                    window_token = json.dumps([other, siblings], sort_keys=True, ensure_ascii=False)
                    if window_token not in cache:
                        cache[window_token] = _load_receipt(tools, other, siblings)
                    context = cache[window_token]
                    if context is None:
                        return None
                    for block in context['read_blocks']:
                        contexts[(block.get('id'), block.get('char_start'), block.get('char_end'))] = deepcopy(block)
                    for row in context['_read_receipts']:
                        context_receipts[json.dumps(row, sort_keys=True, ensure_ascii=False)] = deepcopy(row)
                used.update(context_receipts)
                return {**{key: deepcopy(item) for key, item in value.items() if key != '_read_receipts'},
                        'read_blocks': list(contexts.values())}
        return None

    return resolve, used


def _validation(valid, code='', reason=''):
    return {'state': 'computable' if valid else 'stale', 'status': 'valid' if valid else 'invalid',
            'code': code, 'reason': reason or ('已从当前有效官方原文核对方向与科目；资格仍待核对。' if valid
                                              else '原文证明当前不可复用，请重新定位并精读。')}


def _public_program(tools, program):
    return {**deepcopy(program), 'candidate_ref': {'kind': 'research', 'id': program['id'],
            'research_id': tools.task.get('id')}, 'validation': _validation(True)}


def reason_refs(program, field):
    """A row/subject proof cannot stand in for unknown eligibility or fees."""
    if field in {'subjects', 'excluded_subjects', 'max_subject_changes'}:
        return [deepcopy(ref) for ref in program.get('evidence_refs', [])
                if ref.get('field') in {'exams', 'politics'}]
    if field == 'study_mode':
        ref = ((program.get('candidate_proof') or {}).get('fields') or {}).get('study_mode')
        return [deepcopy(ref)] if isinstance(ref, dict) else []
    return []


def propose(tools, args):
    if (not isinstance(args, dict) or set(args)-{'source_id', 'segment_id', 'drafts'}
            or not isinstance(args.get('source_id'), str) or not isinstance(args.get('segment_id'), str)
            or not isinstance(args.get('drafts'), list) or not 1 <= len(args['drafts']) <= 6):
        raise ResearchError('invalid_source_candidates', '请指定已读来源、片段及最多六个方向草案。')
    tools.refresh_knowledge()
    source = tools.sources.get(args['source_id']) or {}
    unit = tools.units.get(source.get('unit_id')) or {}
    resolve, used = _resolver(tools, list(tools._source_read_receipts.values()))
    result = research_candidates.validate_drafts(unit, tools.task['reference_year'], args['source_id'],
                                                 args['segment_id'], args['drafts'], resolve)
    accepted, rejected = [], deepcopy(result['rejected'])
    for program in result['accepted']:
        identifier = program['id']
        if identifier not in tools._source_candidate_records and len(tools._source_candidate_records) >= MAX_CANDIDATES:
            rejected.append({'code': 'candidate_limit', 'reason': '本轮临时方向已达到二十四个上限。'})
            continue
        value = _public_program(tools, program)
        refs = program.get('evidence_refs') or []
        referenced = [receipt for receipt in used.values() if any(
            ref.get('segment_id') == receipt['segment_id'] and ref.get('source_id') == receipt['source_id']
            and receipt['char_start'] <= ref.get('char_start', -1)
            and receipt['char_end'] >= ref.get('char_end', receipt['char_end']+1) for ref in refs)]
        sources = {(receipt['source_id'], *(receipt[key] for key in _VERSIONS)) for receipt in referenced}
        value['read_receipts'] = [deepcopy(receipt) for receipt in used.values()
                                if (receipt['source_id'], *(receipt[key] for key in _VERSIONS)) in sources]
        tools._source_candidate_records = {**tools._source_candidate_records, identifier: value}
        accepted.append(value)
    _sync_pool(tools)
    return {'candidates': [tools._candidate(row) for row in accepted], 'accepted_count': len(accepted),
            'rejected': rejected, 'rejected_count': len(rejected),
            'research_original_count': sum(row['validation']['state'] == 'computable'
                                           for row in tools._source_candidate_records.values()),
            'national_complete': False, 'notice': '临时方向仅用于本轮可追溯比较，未写入正式目录；报考资格仍未知。'}


def _sync_pool(tools):
    temporary = set(tools._source_candidate_records)
    pool = {key: value for key, value in tools._research_pool.items() if key not in temporary}
    for identifier, record in tools._source_candidate_records.items():
        if record.get('validation', {}).get('state') == 'computable':
            pool[identifier] = {key: deepcopy(value) for key, value in record.items() if key != 'read_receipts'}
    if pool == tools._research_pool:
        return
    tools._research_pool = pool
    tools._research_pool_version += 1
    from grad_engine import match_programs
    result = match_programs(list(pool.values()), tools.task['profile'])
    selection = tools._select_candidates(result['matches'] + result['needs_confirmation'])
    tools.programs = {row['id']: deepcopy(row) for row in selection}
    tools.algorithm.clear()
    tools._algorithm_signature = None


def refresh(tools):
    if tools._candidate_refreshing:
        return
    tools._candidate_refreshing = True
    try:
        cache = {}
        for identifier, record in list(tools._source_candidate_records.items()):
            proof = record.get('candidate_proof') or {}
            fields = proof.get('fields') or {}
            row = fields.get('row') or {}
            source_id = row.get('source_id') or next(iter(proof.get('sources') or [{}])).get('source_id')
            draft = proof.get('draft') or {}
            segment_id = (draft.get('row') or {}).get('segment_id')
            receipts = list(record.get('read_receipts') or [])
            sources = {(row.get('source_id'), *(row.get(key) for key in _VERSIONS)) for row in receipts}
            existing = {json.dumps(row, sort_keys=True, ensure_ascii=False) for row in receipts}
            receipts.extend(row for row in tools._source_read_receipts.values()
                            if (row.get('source_id'), *(row.get(key) for key in _VERSIONS)) in sources
                            and json.dumps(row, sort_keys=True, ensure_ascii=False) not in existing)
            resolve, _ = _resolver(tools, receipts, cache)
            result = research_candidates.validate_drafts(tools.units.get(record.get('unit_id')) or {},
                        tools.task['reference_year'], source_id, segment_id, [draft], resolve)
            accepted = result.get('accepted') or []
            valid = len(accepted) == 1 and accepted[0].get('id') == identifier
            rejection = next(iter(result.get('rejected') or []), {})
            validation = _validation(valid, rejection.get('code', ''), rejection.get('reason', ''))
            current = ({**accepted[0], 'candidate_ref': {'kind': 'research', 'id': identifier,
                        'research_id': (record.get('candidate_ref') or {}).get('research_id') or tools.task.get('id')},
                        'read_receipts': deepcopy(receipts)} if valid else {**record, 'read_receipts': deepcopy(receipts)})
            tools._source_candidate_records = {**tools._source_candidate_records,
                                               identifier: {**current, 'validation': validation}}
        _sync_pool(tools)
    finally:
        tools._candidate_refreshing = False


def inherit(tools, previous, info):
    rows = previous.get('research_candidates') or []
    if not isinstance(rows, list):
        return
    for row in rows[:MAX_CANDIDATES]:
        if not isinstance(row, dict) or not str(row.get('id', '')).startswith('tp_'):
            continue
        tools._source_candidate_records = {**tools._source_candidate_records, row['id']: deepcopy(row)}
    refresh(tools)
    for identifier, record in tools._source_candidate_records.items():
        key = 'candidate_ids' if record['validation']['state'] == 'computable' else 'stale_candidate_ids'
        if identifier not in info[key]:
            info[key].append(identifier)


def snapshot(tools):
    return list(deepcopy(tools._source_candidate_records).values())


def seed_source(tools):
    context = tools.task.get('source_context') or {}
    if not isinstance(context, dict) or not context.get('entry_id'):
        return
    try:
        source = tools._knowledge().get(context['entry_id'])
        unit = tools._unit(source.get('unit_id'))
        if source.get('id') != context['entry_id'] or not official_source_url(str(source.get('url') or ''), unit):
            raise ValueError('unbound source')
        value = {key: deepcopy(value) for key, value in source.items() if key not in {'id', 'text', 'pages'}}
        value.update(knowledge_id=source['id'], knowledge_quality=deepcopy(source.get('quality')),
                     knowledge_saved=True, knowledge_current=_trusted(tools, source), seeded=True)
        if not _trusted(tools, source):
            value.update(excerpt='', status='search_lead')
            tools.gaps.append('指定资料尚未通过当前年度的原文核验；先取得可复用原文，不能直接生成方向。')
        tools._source(value)
    except Exception:
        tools.gaps.append('指定资料当前不可用；请重新选择有效原文并核对学校与年度。')


def refresh_saved_candidates(task, admission, knowledge_store, data_dir):
    """Return a current read view, preserving the persisted original proof audit."""
    result = deepcopy(task)
    for key in ('report', 'previous_report'):
        report = result.get(key)
        if not isinstance(report, dict) or not report.get('research_candidates'):
            continue
        from research_tools import ResearchTools
        current_task = {**result, 'previous_report': report}
        tools = ResearchTools(admission, None, current_task, data_dir, knowledge_store=knowledge_store)
        audit = snapshot(tools)
        invalid = {row['id'] for row in audit if row.get('validation', {}).get('state') != 'computable'}
        by_id = {row['id']: row for row in audit}
        candidates = [tools._candidate(by_id[row['id']]) if row.get('id') in by_id else deepcopy(row)
                      for row in report.get('candidates', []) if row.get('id') not in invalid]
        evidence = {row.get('id'): deepcopy(row) for row in report.get('evidence', []) if isinstance(row, dict)}
        evidence.update(deepcopy(tools.sources))
        for source_id in tools.inheritance.get('stale_source_ids', []):
            if source_id in evidence:
                evidence[source_id] = {**evidence[source_id], 'knowledge_current': False, 'usable': False, 'excerpt': ''}
        current = {**report, 'candidates': candidates, 'research_candidates': audit, 'evidence': list(evidence.values())}
        if invalid:
            current.update(algorithm={}, decisions=[], decision_intelligence=None, evidence_ledger={},
                           assistant_summary='部分临时方向的原文证明已失效，已停止参与比较；请重新定位并精读。')
            current['gaps'] = list(dict.fromkeys([*report.get('gaps', []), current['assistant_summary']]))
            current['plan'] = tools.plan()
            originals = sum(row.get('candidate_origin') == 'official_original' for row in candidates)
            current['coverage'] = {**report.get('coverage', {}), 'candidate_count': len(candidates),
                                   'candidate_unit_count': len({row.get('unit_id') for row in candidates}),
                                   'research_original_count': originals, 'published_count': len(candidates)-originals,
                                   'research_pool_count': len(tools._research_pool),
                                   'research_original_pool_count': sum(row.get('candidate_origin') == 'official_original'
                                                                       for row in tools._research_pool.values()),
                                   'national_complete': False}
            current['summary'] = f'本轮保留{len(candidates)}个当前可用方向；部分官方原文临时方向需重新核对。'
            if key == 'report' and result.get('status') == 'completed':
                result['status'] = 'partial'
        result[key] = current
    return result
