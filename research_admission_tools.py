"""Session-only admission facts backed by delivered, replayable original reads."""
from copy import deepcopy
import json
import re

from admission_facts import aggregate_facts, validate_fact_drafts
from research_store import ResearchError
import research_candidate_tools as candidate_tools


MAX_FACTS = 72
_FIELDS = {'tuition', 'duration_years', 'eligibility'}
_OWNED = ('tuition', 'duration_years', 'duration_evidence', 'conditions',
          'eligibility_evidence_complete', 'evidence_refs', 'field_conflicts', 'requires_review')
_RECEIPT_KEYS = ('segment_id', 'anchor_id', 'source_id', 'char_start', 'char_end',
                 'text_sha256', 'sha256', 'url', 'unit_id', 'year', 'before', 'after',
                 'max_chars', 'group', *candidate_tools._VERSIONS, *candidate_tools._BLOCK_STATE)


def initialize(tools):
    tools._admission_fact_records = {}
    tools._admission_fact_refreshing = False
    tools._admission_before_restore = None
    tools._admission_owned_ids = set()


def _key(record):
    return (record['program_id'], record['id'])


def _program(tools, identifier):
    return tools._research_pool.get(identifier) or tools.programs.get(identifier)


def _receipts(tools):
    pending = tools._pending_source_read
    if pending is not None:
        return list(pending['prior'].values())
    return list(tools._source_read_receipts.values())


def _effective(tools):
    identifiers = _targets(tools)
    return [{identifier: {key: deepcopy(rows[identifier][key]) for key in _OWNED if key in rows[identifier]}
             for identifier in identifiers if identifier in rows}
            for rows in (tools.programs, tools._research_pool)]


def _targets(tools):
    return tools._admission_owned_ids | {row['program_id'] for row in tools._admission_fact_records.values()}


def _restore_program(program):
    state = program.pop('_admission_fact_state', {})
    for key, saved in state.items():
        if key not in _OWNED or program.get(key) != saved['owned']:
            continue
        if saved['present']:
            program[key] = deepcopy(saved['value'])
        else:
            program.pop(key, None)


def restore(tools):
    """Restore before canonical candidate refresh and legacy excerpt overlays."""
    if tools._admission_before_restore is None:
        tools._admission_before_restore = _effective(tools)
    for rows in (tools.programs, tools._research_pool):
        for identifier in _targets(tools):
            if identifier in rows:
                _restore_program(rows[identifier])


def _write(program, key, value):
    state = deepcopy(program.get('_admission_fact_state') or {})
    if key not in state:
        state[key] = {'present': key in program, 'value': deepcopy(program.get(key))}
    state[key]['owned'] = deepcopy(value)
    program[key] = deepcopy(value)
    program['_admission_fact_state'] = state


def _references(fact):
    return [{**deepcopy(ref), 'field': fact['field'], 'origin': 'admission_facts',
             'document_id': ref.get('sha256'),
             'source_locator': {key: ref.get(key) for key in
                                ('segment_id', 'char_start', 'char_end', 'entry_id', 'generation_id')}}
            for ref in fact.get('evidence', [])]


def _canonical(fact, program_id, receipts):
    value = deepcopy(fact)
    # The resolver rebuilds all read contexts; retaining their bodies again
    # would duplicate entire windows once per fact in every saved report.
    value['proof'] = {key: deepcopy(fact['proof'][key]) for key in ('draft', 'clauses', 'sources')}
    value.update(program_id=program_id, state=fact['status'], read_receipts=deepcopy(receipts),
                 evidence_refs=_references(fact), validation={'state': 'current', 'status': 'valid'})
    if fact['field'] == 'tuition':
        value['period'] = fact['value']['period']
    if fact['field'] == 'eligibility':
        value['conditions'] = deepcopy(fact['value'])
    return value


def _stale(record, rejection=None):
    rejection = rejection or {}
    return {**record, 'value': None, 'conditions': [], 'evidence': [], 'evidence_refs': [],
            'state': 'stale', 'status': 'stale', 'eligibility_evidence_complete': False,
            'missing': ['original_proof_not_current'],
            'validation': {'state': 'stale', 'status': 'invalid',
                           'code': rejection.get('code', 'fact_proof_changed'),
                           'reason': rejection.get('reason', '原文证明已失效或发生变化，请重新定位并精读。')}}


def _same_source_receipts(record, current):
    receipts = list(record.get('read_receipts') or [])
    sources = {(row.get('source_id'), *(row.get(key) for key in candidate_tools._VERSIONS))
               for row in receipts}
    seen = {json.dumps(row, sort_keys=True, ensure_ascii=False) for row in receipts}
    return [*receipts, *[deepcopy(row) for row in current
                        if (row.get('source_id'), *(row.get(key) for key in candidate_tools._VERSIONS)) in sources
                        and json.dumps(row, sort_keys=True, ensure_ascii=False) not in seen]][-candidate_tools.MAX_RECEIPTS:]


def _replay(tools):
    records, cache = {}, {}
    current = _receipts(tools)
    for record in tools._admission_fact_records.values():
        program = _program(tools, record['program_id'])
        receipts = _same_source_receipts(record, current)
        resolve, _ = candidate_tools._resolver(tools, receipts, cache)
        draft = (record.get('proof') or {}).get('draft')
        result = validate_fact_drafts(tools.units.get((program or {}).get('unit_id')) or {},
                    tools.task['reference_year'], program, [draft], resolve)
        accepted = result.get('accepted') or []
        valid = len(accepted) == 1 and accepted[0]['id'] == record['id']
        row = (_canonical(accepted[0], record['program_id'], receipts) if valid else
               _stale(record, next(iter(result.get('rejected') or []), {})))
        records[_key(row)] = row
    tools._admission_fact_records = records


def _unique(values):
    return list({json.dumps(row, sort_keys=True, ensure_ascii=False): deepcopy(row) for row in values}.values())


def _only_catalog_eligibility_review(program):
    proof, validation = program.get('candidate_proof') or {}, program.get('validation') or {}
    fields = proof.get('fields') or {}
    return (str(program.get('id', '')).startswith('tp_')
            and program.get('candidate_origin') == 'official_original'
            and validation.get('state') == 'computable' and validation.get('status') == 'valid'
            and fields.get('row') and fields.get('exams') and proof.get('sources')
            and not any(program.get(key) for key in
                        ('notes', 'unparsed_condition_notes', 'subject_identity_issues', 'field_conflicts'))
            and program.get('exam_matching_supported') is not False)


def _apply_program(program, result):
    rows = result['accepted']
    refs = [ref for row in rows if row['status'] != 'conflict' for ref in _references(row)]
    if refs:
        _write(program, 'evidence_refs', _unique([*(program.get('evidence_refs') or []), *refs]))
    conflicts = result['conflicts']
    if conflicts:
        current = program.get('field_conflicts') or []
        owned = [{**deepcopy(row), 'origin': 'admission_facts',
                  'message': '同一适用范围的官方原文或已收录字段不一致，该字段暂停使用。'} for row in conflicts]
        _write(program, 'field_conflicts', [*current, *owned])
        _write(program, 'requires_review', True)
        for conflict in conflicts:
            field = conflict['field']
            if field in {'tuition', 'duration_years'}:
                _write(program, field, None)
                if field == 'duration_years':
                    _write(program, 'duration_evidence', [])
            else:
                _write(program, 'conditions', [])
                _write(program, 'eligibility_evidence_complete', False)
    if result['tuition'] is not None:
        facts = [row for row in rows if row['field'] == 'tuition']
        existing = program.get('tuition') if isinstance(program.get('tuition'), dict) else {}
        evidence = existing.get('evidence') or []
        evidence = evidence if isinstance(evidence, list) else [evidence]
        _write(program, 'tuition', {**deepcopy(existing), **deepcopy(result['tuition']),
               'evidence': _unique([*evidence, *[ref for row in facts for ref in _references(row)]]),
               'verified_by': facts[0]['status'], 'admission_fact_ids': [row['id'] for row in facts]})
    if result['duration_years'] is not None:
        _write(program, 'duration_years', result['duration_years'])
        _write(program, 'duration_evidence', [ref for row in rows if row['field'] == 'duration_years'
                                            for ref in _references(row)])
    if result['conditions']:
        _write(program, 'conditions', _unique([*(program.get('conditions') or []), *result['conditions']]))
    if any(row['field'] == 'eligibility' for row in rows):
        _write(program, 'eligibility_evidence_complete', result['eligibility_evidence_complete'])
        if result['eligibility_evidence_complete']:
            if _only_catalog_eligibility_review(program):
                _write(program, 'requires_review', False)


def apply(tools):
    """Apply revalidated facts to selected directions and the complete research pool."""
    before = tools._admission_before_restore
    if before is None:
        before = _effective(tools)
        for rows in (tools.programs, tools._research_pool):
            for identifier in _targets(tools):
                if identifier in rows:
                    _restore_program(rows[identifier])
    records = dict(tools._admission_fact_records)
    for identifier in sorted(_targets(tools)):
        program = _program(tools, identifier)
        if program is None:
            continue
        current = [row for row in records.values() if row['program_id'] == identifier
                   and row.get('validation', {}).get('status') == 'valid']
        result = aggregate_facts(program, current)
        for fact in result['accepted']:
            fact['state'] = fact['status']
            records[_key(fact)] = fact
        for collection in (tools.programs, tools._research_pool):
            if identifier in collection:
                _apply_program(collection[identifier], result)
    tools._admission_fact_records = records
    tools._admission_owned_ids = {identifier for identifier in _targets(tools)
                                 if any(identifier in rows and rows[identifier].get('_admission_fact_state')
                                        for rows in (tools.programs, tools._research_pool))}
    after = _effective(tools)
    changed = before != after
    if changed:
        tools.algorithm.clear()
        tools._algorithm_signature = None
        if before[1] != after[1]:
            tools._research_pool_version += 1
    tools._admission_before_restore = None
    return {'changed': changed, **snapshot(tools)}


def refresh(tools):
    """Replay proof drafts; stored values and asserted statuses grant no authority."""
    if tools._admission_fact_refreshing:
        return snapshot(tools)
    tools._admission_fact_refreshing = True
    try:
        restore(tools)
        _replay(tools)
        return apply(tools)
    finally:
        tools._admission_fact_refreshing = False


def propose(tools, args):
    if (not isinstance(args, dict) or set(args) != {'program_id', 'drafts'}
            or not isinstance(args['program_id'], str) or not isinstance(args['drafts'], list)
            or not 1 <= len(args['drafts']) <= 6 or _program(tools, args['program_id']) is None):
        raise ResearchError('invalid_admission_facts', '请指定本轮方向及最多六条完整原文事实草案。')
    tools.refresh_knowledge()
    restore(tools)
    program = _program(tools, args['program_id'])
    resolve, used = candidate_tools._resolver(tools, _receipts(tools))
    result = validate_fact_drafts(tools.units.get(program.get('unit_id')) or {},
                tools.task['reference_year'], program, args['drafts'], resolve)
    accepted, rejected = [], deepcopy(result['rejected'])
    records = dict(tools._admission_fact_records)
    for row in result['accepted']:
        value = _canonical(row, program['id'], list(used.values()))
        key = _key(value)
        if key not in records and len(records) >= MAX_FACTS:
            rejected.append({'code': 'admission_fact_limit', 'field': row['field'],
                             'reason': '本轮已保留七十二条事实审计，请在新研究中继续补证。'})
            continue
        records[key] = value
        accepted.append(value)
    tools._admission_fact_records = records
    refresh(tools)
    return {'facts': [deepcopy(tools._admission_fact_records[_key(row)]) for row in accepted],
            'accepted_count': len(accepted), 'rejected': rejected, 'rejected_count': len(rejected),
            'notice': '事实只用于本轮可追溯计算，未修改正式招生目录；资格未完整核验时仍为待确认。'}


def _saved_record(row):
    if (not isinstance(row, dict) or not isinstance(row.get('program_id'), str)
            or not 1 <= len(row['program_id']) <= 240
            or not isinstance(row.get('id'), str) or not re.fullmatch(r'af_[0-9a-f]{24}', row['id'])):
        return None
    proof = row.get('proof')
    draft = proof.get('draft') if isinstance(proof, dict) else None
    if (not isinstance(draft, dict) or set(draft) != {'field', 'citation'}
            or not isinstance(draft.get('field'), str) or draft['field'] not in _FIELDS
            or not isinstance(draft.get('citation'), dict)):
        return None
    citation = draft['citation']
    if (set(citation) != {'segment_id', 'char_start', 'char_end', 'quote'}
            or not isinstance(citation.get('quote'), str) or not 1 <= len(citation['quote']) <= 20000
            or not isinstance(citation.get('segment_id'), str)
            or not re.fullmatch(r'sg_[0-9a-f]{32}', citation['segment_id'])
            or type(citation.get('char_start')) is not int or type(citation.get('char_end')) is not int
            or not 0 <= citation['char_start'] < citation['char_end']
            or citation['char_end'] - citation['char_start'] != len(citation['quote'])):
        return None
    receipts = []
    offered = row.get('read_receipts')
    for receipt in offered[:candidate_tools.MAX_RECEIPTS] if isinstance(offered, list) else []:
        required = ('segment_id', 'anchor_id', 'source_id', 'text_sha256', 'sha256', 'url',
                    'unit_id', 'group', *candidate_tools._VERSIONS)
        if (not isinstance(receipt, dict) or any(not isinstance(receipt.get(key), str)
                or not 1 <= len(receipt[key]) <= (2048 if key == 'url' else 240) for key in required)
                or any(type(receipt.get(key)) is not int for key in
                       ('char_start', 'char_end', 'year', 'before', 'after', 'max_chars'))
                or not 0 <= receipt['char_start'] < receipt['char_end']
                or not 0 <= receipt['before'] <= 3 or not 0 <= receipt['after'] <= 3
                or not 1 <= receipt['max_chars'] <= 20000
                or receipt.get('group') not in {'segment', 'contexts', 'neighbors'}
                or not re.fullmatch(r'[0-9a-f]{64}', receipt['text_sha256'])
                or not re.fullmatch(r'[0-9a-f]{64}', receipt['sha256'])
                or any(key in receipt and receipt[key] is not None and type(receipt[key]) is not bool
                       for key in ('row_complete', 'continues_previous', 'continues_next', 'content_truncated'))
                or 'kind' in receipt and receipt['kind'] is not None and
                   (not isinstance(receipt['kind'], str) or len(receipt['kind']) > 80)):
            continue
        receipts.append({key: deepcopy(receipt[key]) for key in _RECEIPT_KEYS if key in receipt})
    return {'id': row['id'], 'program_id': row['program_id'], 'field': draft['field'],
            'proof': {'draft': deepcopy(draft)}, 'read_receipts': receipts,
            'validation': {'state': 'stale', 'status': 'invalid'}}


def inherit(tools, previous, info=None):
    report = previous.get('admission_facts') if isinstance(previous, dict) else None
    if not isinstance(report, dict) or not isinstance(report.get('facts'), list):
        return
    records = dict(tools._admission_fact_records)
    for row in report['facts'][:MAX_FACTS]:
        value = _saved_record(row)
        if value is not None:
            _restore_target(tools, value)
            records[_key(value)] = value
    tools._admission_fact_records = dict(list(records.items())[:MAX_FACTS])
    refresh(tools)


def _restore_target(tools, record):
    identifier = record['program_id']
    if _program(tools, identifier) is not None or identifier.startswith('tp_'):
        return
    admission = getattr(tools, 'admission', None)
    if not callable(getattr(admission, 'catalog_program', None)):
        return
    try:
        bundle = admission.catalog_program(identifier, revision=getattr(tools, 'revision', None))
        row = bundle.get('program') if isinstance(bundle, dict) and isinstance(bundle.get('program'), dict) else bundle
        selected = tools.task.get('unit_ids') or []
        infer = getattr(tools, 'infer_unit', None)
        if not selected and callable(infer):
            inferred = infer(tools.task.get('query', ''))
            selected = [inferred] if inferred else []
        if (not isinstance(row, dict) or row.get('id') != identifier
                or row.get('year') != tools.task['reference_year'] or row.get('unit_id') not in tools.units
                or selected and row['unit_id'] not in selected):
            return
        resolve, _ = candidate_tools._resolver(tools, record['read_receipts'])
        proof = validate_fact_drafts(tools.units[row['unit_id']], tools.task['reference_year'], row,
                                    [record['proof']['draft']], resolve)
        if len(proof['accepted']) != 1 or proof['accepted'][0]['id'] != record['id']:
            return
        tools._research_pool = {**tools._research_pool, identifier: deepcopy(row)}
        tools._research_pool_version += 1
        tools.algorithm.clear()
        tools._algorithm_signature = None
    except Exception:
        tools.gaps.append('上轮字段对应的方向当前不可读取，事实保留待核，不参与本轮计算。')


def snapshot(tools):
    rows = list(deepcopy(tools._admission_fact_records).values())[:MAX_FACTS]
    for row in rows:
        if row.get('field') == 'eligibility' and isinstance(row.get('value'), list):
            row['value'] = [_label(node) for node in row['value']]
            row['conditions'] = deepcopy(row['value'])
    return {'facts': rows,
            'applied_count': sum(row.get('state') in {'verified_single_source', 'corroborated', 'partial'} for row in rows),
            'conflict_count': sum(row.get('state') == 'conflict' for row in rows),
            'pending_count': sum(row.get('state') in {'stale', 'needs_review', 'partial'} for row in rows)}


def _label(node):
    value = deepcopy(node)
    for op, joiner in (('all', '且'), ('any', '或')):
        if op in node:
            value[op] = [_label(item) for item in node[op]]
            value['label'] = joiner.join(item['label'] for item in value[op])
            return value
    actual = node.get('value')
    if node.get('field') == 'education':
        value['label'] = '学历至少为' + {'bachelor': '本科', 'master': '硕士'}.get(actual, str(actual))
    elif node.get('field') == 'work_years':
        value['label'] = f'工作年限不少于{actual}年'
    elif node.get('field') == 'background':
        value['label'] = '本科专业为' + ('或'.join(actual) if isinstance(actual, list) else str(actual))
    else:
        value['label'] = '报考条款须继续结合个人条件核对'
    return value


def refresh_saved_facts(task, admission, knowledge_store, data_dir):
    """Return a fresh read view; never rewrite the saved journal or a source."""
    result = deepcopy(task)
    for key in ('report', 'previous_report'):
        report = result.get(key)
        audit = report.get('admission_facts') if isinstance(report, dict) else None
        if not isinstance(audit, dict) or not isinstance(audit.get('facts'), list) or not audit['facts']:
            continue
        from research_tools import ResearchTools
        tools = ResearchTools(admission, None, {**result, 'previous_report': report}, data_dir,
                              knowledge_store=knowledge_store)
        if not hasattr(tools, '_admission_fact_records'):
            initialize(tools)
            inherit(tools, report)
        else:
            refresh(tools)
        audit = snapshot(tools)
        current = {**report, 'admission_facts': audit,
                   'candidates': [tools._candidate(tools.programs[row['id']])
                                  for row in report.get('candidates', [])
                                  if isinstance(row, dict) and isinstance(row.get('id'), str)
                                  and row['id'] in tools.programs]}
        if audit != report.get('admission_facts') or current['candidates'] != report.get('candidates', []):
            notice = '已按当前原文重新核对资格、学费与学制；旧比较结果已失效，请重新比较。'
            current.update(algorithm={}, decisions=[], decision_intelligence=None, evidence_ledger={},
                           assistant_summary=notice, gaps=list(dict.fromkeys([*report.get('gaps', []), notice])))
            current['coverage'] = {**report.get('coverage', {}), 'candidate_count': len(current['candidates']),
                                   'candidate_unit_count': len({row.get('unit_id') for row in current['candidates']}),
                                   'research_pool_count': len(tools._research_pool), 'national_complete': False}
            if key == 'report' and result.get('status') == 'completed':
                result['status'] = 'partial'
        result[key] = current
    return result
