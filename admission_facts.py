"""Pure admission facts from fully read, current canonical official clauses.

Drafts contain only a field and exact full-block citation. Values, rule operators,
scope, coverage and trust come from deterministic parsing and fresh read receipts.
The older excerpt-based research_fields path keeps its separate two-source rule.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from math import isfinite
import re

from grad_engine import OPERATORS, PROFILE_FIELDS
from research_candidates import _Rejected, _REASONS, _resolve, _complete


MAX_FACT_DRAFTS = 12
_FIELDS = {'eligibility', 'tuition', 'duration_years'}
_CODE = re.compile(r'(?<![0-9A-Z])[0-9A-Z]{6}(?![0-9A-Z])')
_MODE = re.compile(r'非全日制|(?<!非)全日制')
_DEGREE = {'学硕': '学术学位', '学术型': '学术学位', '学术学位': '学术学位',
           '专硕': '专业学位', '专业型': '专业学位', '专业学位': '专业学位'}
_EXCEPTION = re.compile(r'除|但|例外|其中|分别|另行|另须|另需|跨专业|专项|特殊计划|士兵|单独考试|推免|中外合作|详见|参见|见附件|按.*规定')
_NEGATION = re.compile(r'不接收|不接受|不招收|不得|不能|禁止|同等学力|本科结业')
_SCOPE_EXCEPTION = re.compile(r'除|但|例外|其中|分别|专项|特殊计划|士兵|单独考试|推免|中外合作|跨专业')
_NUMERIC_QUALIFIER = re.compile(r'最高|最低|上限|下限|不超过|不低于|不高于|不少于|至少|至多|大约|约|左右|以内|以上|以下|不执行|不适用|不采用|不代表|不是')
_TOPIC = {'tuition': r'学费|收费', 'duration_years': r'学制|修业年限',
          'eligibility': r'报考|资格|学历|工作年限|本科专业|国籍|健康|纪律|品德|宪法'}
_REJECT = {
    'invalid_request': '请提供同校同年度的明确专业及至多十二条事实草案。',
    'invalid_draft': '草案仅可包含字段名和完整原文引用，不能提交数值、条件或资格结论。',
    'incomplete_clause': '请引用当前片段的完整条款或完整表格行，不能裁掉适用范围或后续条件。',
    'unread_scope': '该行的规范标题或表头尚未完整读取，请补读当前原文。',
    'unsupported_scope': '原文未明确覆盖该专业、年度、学习方式或学位类别，继续保留未知。',
    'unsupported_tuition': '无法从完整同一范围原文确定唯一学费金额与计费周期。',
    'unsupported_duration': '学制包含未支持的例外、区间、续文或多个数值，继续保留未知。',
    'unsupported_condition': '资格含否定、例外、特殊计划或暂不能执行的关联条件。',
    'fact_context_conflict': '已读同范围原文给出相互冲突的数值，该字段暂停使用。',
    'eligibility_batch_incomplete': '本批资格依据尚有未通过核验的条款，本批资格事实暂停采纳。',
}


class _FactRejected(ValueError):
    pass


def _require(value, code):
    if not value:
        raise _FactRejected(code)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()[:24]


def _parent_proofs(block, contexts):
    parents = []
    spans = block.get('context_spans') or []
    _require(isinstance(spans, list) and len(spans) <= 32, 'unread_scope')
    for span in spans:
        _require(isinstance(span, dict) and span.get('role') in {'heading', 'table_header'}, 'unread_scope')
        found = next((ref for ref in contexts if ref['char_start'] == span.get('char_start') and
                      ref['char_end'] == span.get('char_end') and
                      ('text' not in span or ref['quote'] == span['text'])), None)
        _require(found is not None and not any(found.get(key) for key in
                 ('content_truncated', 'continues_previous', 'continues_next')), 'unread_scope')
        parents.append(deepcopy(found))
    return parents


def _scope(program, year, text, parents, contexts):
    joined = '\n'.join([*(row['quote'] for row in parents), text])
    _require(not any(_SCOPE_EXCEPTION.search(row['quote']) or _NEGATION.search(row['quote'])
                     for row in parents), 'unsupported_scope')
    years = re.findall(r'(20\d{2})(?:年(?:硕士|招生|报考|入学)|级)', joined)
    _require(all(int(value) == year for value in years), 'unsupported_scope')
    codes = set(_CODE.findall(joined))
    wide = bool(re.search(r'(?:我校|本校|全校)(?:所有|全部|各)?(?:非全日制|全日制)?(?:硕士研究生|专业)', joined))
    _require((codes == {program['code']}) if codes else wide, 'unsupported_scope')
    modes = set(_MODE.findall(joined))
    if not modes:
        # A previously read explicit universal statement is a scope proof; an
        # unrelated sibling row or an unread section_path is never one.
        pattern = r'(?:我校|本校|全校)(?:所有|全部|各)专业均(?:招收(非全日制|全日制)硕士研究生|为(非全日制|全日制))[。；;]?'
        universal = [row for row in contexts if re.fullmatch(pattern, row['quote'].strip())]
        for row in universal:
            modes.update(_MODE.findall(row['quote']))
        parents = [*parents, *[deepcopy(row) for row in universal if row not in parents]]
    all_modes = bool(re.search(r'所有学习方式|全部学习方式', joined)) or wide and not codes and not modes
    _require(all_modes or modes == {program.get('study_mode')}, 'unsupported_scope')
    degrees = {_DEGREE[value] for value in re.findall('|'.join(_DEGREE), joined)}
    degree = _DEGREE.get(program.get('degree_type'), program.get('degree_type'))
    _require(not degrees or degrees == {degree}, 'unsupported_scope')
    for row in parents:
        heading = row['quote'].strip()
        if re.fullmatch(r'(?:\d{3}\s*)?[^\t\n]{2,40}(?:学院|研究院|学部|系)', heading):
            actual = re.sub(r'^\d{3}\s*', '', str(program.get('department') or ''))
            _require(re.sub(r'^\d{3}\s*', '', heading) == actual, 'unsupported_scope')
        if re.fullmatch(r'[0-9A-Z]{2}[^0-9A-Z][^\n]{1,80}', heading):
            _require(heading == program.get('direction'), 'unsupported_scope')
    return {'unit_id': str(program['unit_id']), 'year': year, 'code': program['code'],
            'study_mode': program.get('study_mode'), 'degree_type': degree,
            'department': program.get('department'), 'direction': program.get('direction'),
            'scope_basis': 'program_code' if codes else 'university_wide'}, parents


def _related(context, selected, field, program):
    if context['char_start'] == selected['char_start'] and context['char_end'] == selected['char_end']:
        return False
    codes = set(_CODE.findall(context['quote']))
    return (not codes or program['code'] in codes) and bool(re.search(_TOPIC[field], context['quote']))


def _tuition(text, headers):
    _require(not (_EXCEPTION.search(text) or _NUMERIC_QUALIFIER.search(text)) and
             not re.search(r'暂定|待定|预计|拟定|待批|示例|参考', text), 'unsupported_tuition')
    # A table cell has its meaning only under its exact, already-read header.
    _require('学费' in text or any('学费' in ref['quote'] for ref in headers), 'unsupported_tuition')
    pattern = r'(?<![\d.])(\d{1,7}(?:\.\d{1,2})?)\s*(万?元)\s*(?:/|／|每)\s*(?:生[·./]?)?\s*(学年|年|全程|全学程|总额|合计)'
    matches = list(re.finditer(pattern, text))
    _require(len(matches) == 1, 'unsupported_tuition')
    match = matches[0]
    prefix = text[:match.start()]
    _require(not re.search(r'(?:住宿费|报名费|助学金|奖学金)[^。；;\t]*$', prefix), 'unsupported_tuition')
    try:
        value = Decimal(match[1]) * (10000 if match[2] == '万元' else 1)
    except InvalidOperation:
        raise _FactRejected('unsupported_tuition') from None
    _require(0 < value <= 10000000, 'unsupported_tuition')
    # Do not silently ignore a second price, range, credit cost or later period.
    remainder = text[:match.start()] + text[match.end():]
    _require(not re.search(r'\d+(?:\.\d+)?\s*万?元|学分|待定|另|至|—|~|～', remainder), 'unsupported_tuition')
    return {'amount': int(value) if value == value.to_integral() else float(value),
            'period': 'year' if match[3] in {'学年', '年'} else 'total'}


def _number_word(value):
    return int(value) if value.isdigit() else {'一': 1, '二': 2, '三': 3, '四': 4,
        '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}.get(value)


def _duration(text, headers):
    _require(not (_EXCEPTION.search(text) or _NUMERIC_QUALIFIER.search(text)), 'unsupported_duration')
    _require('学制' in text or any('学制' in ref['quote'] for ref in headers), 'unsupported_duration')
    values = list(re.finditer(r'(?<![\d.])([1-9](?:\.5)?|[一二三四五六七八九十])\s*年', text))
    _require(len(values) == 1 and not re.search(r'最长|最短|弹性|至|—|~|～', text), 'unsupported_duration')
    raw = values[0][1]
    value = float(raw) if '.' in raw else _number_word(raw)
    _require(value is not None and 0 < value <= 10, 'unsupported_duration')
    return value


def _atom(text, evidence):
    education = re.fullmatch(r'学历(?:要求)?(?:须|应|必须)?(?:为|达到)(本科|大学本科|硕士)(及以上)?(?:学历)?', text)
    if education:
        node = {'field': 'education', 'op': 'gte' if education[2] else 'eq',
                'value': 'master' if education[1] == '硕士' else 'bachelor'}
    else:
        years = re.fullmatch(r'工作年限(?:须|应)?(?:不少于|至少|满)(\d{1,2}|[一二三四五六七八九十])年', text)
        background = re.fullmatch(r'本科专业(?:须|应|必须)?(?:为|限于)([\u3400-\u9fff、]+)', text)
        if years:
            node = {'field': 'work_years', 'op': 'gte', 'value': _number_word(years[1])}
        elif background:
            values = re.split(r'或者|或|、', background[1])
            if not (1 <= len(values) <= 12 and all(2 <= len(value) <= 40 and
                    not re.search(r'学历|要求|须|年限|条件|且|并|为|达到|不少于|相关|相近|等|不限|其他|其它|及其|学科门类', value) for value in values)):
                return None
            node = {'field': 'background', 'op': 'eq' if len(values) == 1 else 'in',
                    'value': values[0] if len(values) == 1 else values}
        else:
            return None
    _require(node['field'] in PROFILE_FIELDS and node['op'] in OPERATORS, 'unsupported_condition')
    return {**node, 'evidence': deepcopy(evidence)}


def _expression(text, evidence):
    direct = _atom(text, evidence)
    if direct is not None:
        return direct
    has_or, has_and = bool(re.search(r'或者|或', text)), bool(re.search(r'并且|且', text))
    _require(not (has_or and has_and), 'unsupported_condition')
    parts = re.split(r'或者|或' if has_or else r'并且|且', text)
    if len(parts) == 1:
        return None
    nodes = [_atom(part, evidence) for part in parts]
    _require(all(node is not None for node in nodes), 'unsupported_condition')
    return {'any' if has_or else 'all': nodes, 'evidence': deepcopy(evidence)}


def _eligibility(text, scope, evidence, program, contexts, block):
    _require(not (_EXCEPTION.search(text) or _NEGATION.search(text)), 'unsupported_condition')
    marker = re.search(r'(?:完整)?报考条件(?:如下|为)?[:：]', text)
    if marker:
        _require(not re.search(r'学历|工作年限|本科专业|须|健康|国籍|纪律|只招|仅招|仅限',
                               text[:marker.start()]), 'unsupported_condition')
        body = text[marker.end():]
    else:
        match = re.search(r'学历(?:要求)?|工作年限|本科专业', text)
        _require(match is not None, 'unsupported_condition')
        body = text[match.start():]
    conditions, missing = [], []
    for part in re.split(r'[；;。\n]+', body):
        atom = part.strip()
        if not atom:
            continue
        condition = _expression(atom, evidence)
        if condition is None:
            missing.append('unsupported_condition:' + atom)
        else:
            conditions.append(condition)
    _require(bool(conditions), 'unsupported_condition')
    if scope['scope_basis'] != 'program_code' or marker is None or not marker.group().startswith('完整'):
        missing.append('eligibility_scope_incomplete')
    if program.get('unparsed_condition_notes') or program.get('notes'):
        missing.append('program_conditions_unresolved')
    relevant = [row for row in contexts if _related(row, block, 'eligibility', program)
                and row not in evidence and row.get('kind') not in {'heading', 'table_header'}]
    if relevant:
        missing.append('additional_eligibility_clauses_unresolved')
    return conditions, sorted(set(missing)), not missing


def _check_numeric_contexts(field, value, contexts, parents, block, program, year):
    for row in contexts:
        if row in parents or not _related(row, block, field, program):
            continue
        try:
            _scope(program, year, row['quote'], [], contexts)
            other = _tuition(row['quote'], []) if field == 'tuition' else _duration(row['quote'], [])
        except _FactRejected:
            continue
        _require(other == value, 'fact_context_conflict')


def _validated(unit, year, program, draft, resolver):
    _require(isinstance(draft, dict) and set(draft) == {'field', 'citation'} and
             isinstance(draft.get('field'), str) and draft['field'] in _FIELDS, 'invalid_draft')
    block, citation, source, contexts = _resolve(draft['citation'], resolver, unit, year, with_context=True)
    _complete(block)
    _require(draft['citation']['quote'] == block['text'].strip(), 'incomplete_clause')
    text, field = citation['quote'], draft['field']
    if field == 'duration_years' and (_EXCEPTION.search(text) or _NEGATION.search(text)):
        raise _FactRejected('unsupported_duration')
    if field == 'eligibility' and re.search(r'专项|特殊计划|士兵|单独考试|推免|中外合作', text):
        raise _FactRejected('unsupported_condition')
    parents = _parent_proofs(block, contexts)
    scope, parents = _scope(program, year, text, parents, contexts)
    evidence = [citation, *parents]
    for row in contexts:
        if field != 'eligibility' and row not in parents and _related(row, block, field, program) and _EXCEPTION.search(row['quote']):
            raise _FactRejected('unsupported_duration' if field == 'duration_years' else 'unsupported_tuition')
    missing, complete = [], False
    if field == 'eligibility':
        try:
            value, missing, complete = _eligibility(text, scope, evidence, program, contexts, block)
        except _FactRejected as error:
            if str(error) != 'unsupported_condition':
                raise
            value, missing = [], ['unsupported_condition:' + text]
    else:
        value = _tuition(text, parents) if field == 'tuition' else _duration(text, parents)
        _check_numeric_contexts(field, value, contexts, parents, block, program, year)
    proof = {'draft': deepcopy(draft), 'clauses': deepcopy(evidence),
             'read_contexts': deepcopy(contexts), 'sources': [source]}
    identity = {'scope': scope, 'proof': {key: proof[key] for key in ('draft', 'clauses', 'sources')},
                'value': _semantic_value(value), 'missing': missing, 'complete': complete}
    return {'id': 'af_' + _digest(identity), 'field': field, 'value': value,
            'scope': scope, 'status': ('needs_review' if field == 'eligibility' and not value else
                'partial' if field == 'eligibility' and not complete else 'verified_single_source'),
            'evidence': deepcopy(evidence), 'proof': proof,
            'eligibility_evidence_complete': complete, 'missing': missing}


def _aggregate(program, facts, rejected):
    missing = sorted({value for row in facts for value in row['missing']})
    conflicts, conditions, tuition, duration = [], [], None, None
    for field in _FIELDS:
        rows = [row for row in facts if row['field'] == field]
        values = {_digest(_semantic_value(row['value'])) for row in rows}
        existing = program.get(field)
        if field == 'tuition' and isinstance(existing, dict) and _known_number(existing.get('amount')) and existing.get('period') in {'year', 'total'}:
            values.add(_digest({key: existing.get(key) for key in ('amount', 'period')}))
        elif field == 'duration_years' and _known_number(existing) and existing > 0:
            values.add(_digest(existing))
        # Separate partial condition bundles can add requirements. Different
        # complete bundles cannot both prove the one complete qualification.
        conflict = len(values) > 1 and (field != 'eligibility' or
                   sum(row['eligibility_evidence_complete'] for row in rows) > 1)
        if field == 'eligibility':
            atoms = {}
            for row in rows:
                for node in row['value']:
                    if 'field' in node:
                        atoms.setdefault(node['field'], set()).add(_digest(_semantic_value(node)))
            conflict = conflict or any(len(values) > 1 for values in atoms.values())
        if rows and conflict:
            conflicts.append({'field': field, 'code': 'fact_conflict', 'paused': True,
                              'fact_ids': [row['id'] for row in rows]})
            for row in rows:
                row.update(status='conflict', eligibility_evidence_complete=False)
            missing.append(field + '_conflict')
            continue
        if not rows:
            continue
        for row in rows:
            digests = {other['proof']['sources'][0]['sha256'] for other in rows
                       if _semantic_value(other['value']) == _semantic_value(row['value'])}
            if len(digests) > 1 and row['status'] in {'verified_single_source', 'corroborated'}:
                row['status'] = 'corroborated'
        if field == 'tuition':
            tuition = {**deepcopy(rows[0]['value']), 'evidence': [deepcopy(ref) for row in rows for ref in row['evidence']]}
        elif field == 'duration_years':
            duration = rows[0]['value']
        else:
            unique = {_digest(_semantic_value(node)): deepcopy(node) for row in rows for node in row['value']}
            conditions = list(unique.values())
    eligible = [row for row in facts if row['field'] == 'eligibility']
    complete = bool(eligible) and not any(row['field'] == 'eligibility' for row in conflicts) and all(
        row['eligibility_evidence_complete'] for row in eligible)
    complete = complete and not any(row.get('field') == 'eligibility' for row in rejected)
    if not complete and 'eligibility_scope_incomplete' not in missing:
        missing.append('eligibility_scope_incomplete')
    return {'accepted': facts, 'rejected': rejected, 'conditions': conditions,
            'tuition': tuition, 'duration_years': duration,
            'eligibility_evidence_complete': complete, 'missing': sorted(set(missing)), 'conflicts': conflicts}


def _semantic_value(value):
    if isinstance(value, dict):
        return {key: _semantic_value(item) for key, item in value.items() if key != 'evidence'}
    return [_semantic_value(item) for item in value] if isinstance(value, list) else value


def _known_number(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


def aggregate_facts(program, accepted_facts):
    """Combine freshly revalidated facts across calls without changing inputs.

    This helper takes only server-validated facts. It does not confer trust on
    arbitrary JSON; callers must replay each proof.draft and match its stable ID.
    Restore this module's previous overlay before comparing with base facts.
    """
    return _aggregate(program, deepcopy(accepted_facts), [])


def validate_fact_drafts(unit, year, program, drafts, resolve_citation):
    """Validate per-program proposals without fetching or changing any input.

    Resolver contract and offsets are identical to research_candidates.validate_drafts.
    Revalidate stored proof.draft against current receipts before every reuse;
    matching IDs bind source SHA, extraction/generation and exact canonical spans.
    Only the canonical path may label a single scoped fee verified_single_source.
    This is a deliberately bounded Chinese rule grammar, not an arbitrary prose
    parser: special plans, negation, linked alternatives and exceptions stay unknown.
    """
    valid = (isinstance(unit, dict) and unit.get('id') and type(year) is int and 2020 <= year <= 2100 and
             isinstance(program, dict) and program.get('unit_id') == str(unit.get('id')) and
             program.get('year') == year and isinstance(program.get('code'), str) and
             _CODE.fullmatch(program['code']) and callable(resolve_citation) and
             isinstance(drafts, list) and 1 <= len(drafts) <= MAX_FACT_DRAFTS)
    if not valid:
        return _aggregate(program if isinstance(program, dict) else {}, [],
                          [{'index': -1, 'code': 'invalid_request', 'reason': _REJECT['invalid_request']}])
    facts, rejected, seen = [], [], set()
    for index, draft in enumerate(drafts):
        try:
            fact = _validated(unit, year, program, draft, resolve_citation)
            if fact['id'] not in seen:
                facts.append(fact)
                seen.add(fact['id'])
        except (_FactRejected, _Rejected) as error:
            code = str(error)
            rejected.append({'index': index, 'field': draft.get('field') if isinstance(draft, dict) else None,
                             'code': code, 'reason': _REJECT.get(code, _REASONS.get(code, code))})
    if any(row.get('field') == 'eligibility' for row in rejected):
        for fact in [row for row in facts if row['field'] == 'eligibility']:
            index = next(index for index, draft in enumerate(drafts) if draft == fact['proof']['draft'])
            rejected.append({'index': index, 'field': 'eligibility', 'code': 'eligibility_batch_incomplete',
                             'reason': _REJECT['eligibility_batch_incomplete']})
        facts = [row for row in facts if row['field'] != 'eligibility']
    return _aggregate(program, facts, rejected)
