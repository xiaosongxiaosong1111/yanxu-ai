"""Deterministic field candidates distilled from already-read official originals.

A field candidate is only a *proposal*: it keeps the exact quote, source identity,
unit/year scope and excerpt offsets. Nothing here edits the published catalog or the
knowledge store. A tuition proposal becomes usable only when two independent saved
originals agree on the same amount and period; a conflict pauses the affected
candidate instead of picking one value. Eligibility prose is never upgraded to a
verified admission condition, so an unknown eligibility state stays unknown.
"""
from copy import deepcopy
import hashlib
import re

FIELD_LABELS = {
    'tuition': '学费与收费口径',
    'eligibility': '报考资格与适用范围',
}

_YEAR = re.compile(r'(\d{2,6}(?:\.\d{1,2})?)\s*元\s*(?:/|每)?\s*(?:生\s*[·./]?\s*)?(?:学年|年)')
_TOTAL = re.compile(r'(\d{3,7}(?:\.\d{1,2})?)\s*元\s*(?:/|每)?\s*(?:生)?\s*(?:全程|全学程|总额|合计)')
_FREE = re.compile(r'(?:不收取?|免收|不收|暂不收)\s*学费')
_ELIGIBILITY = re.compile(r'报考条件|招生对象|学历要求|同等学力|工作年限|报考资格')
_SCOPE_WIDE = re.compile(r'各专业|所有专业|全体|全校|收费标准一致|学术型、专业型|全日制学生与非全日制学生')
_CODES = re.compile(r'(?<!\d)\d{6}(?!\d)')


def _text(value):
    return value if isinstance(value, str) else ''


def _source_ok(source):
    """Only a saved original with a real year, unit scope and digest may propose fields."""
    if not isinstance(source, dict):
        return False
    if source.get('status') != 'official_original_read':
        return False
    if source.get('field_verification') == 'published_snapshot':
        return False
    if type(source.get('year')) is not int or not 2020 <= source['year'] <= 2100:
        return False
    if not isinstance(source.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', source['sha256']):
        return False
    if not isinstance(source.get('unit_id'), str) or not source['unit_id'].strip():
        return False
    return bool(_text(source.get('excerpt')).strip())


def _quote_at(text, start, end):
    left = max(0, start - 80)
    left = max(text.rfind('\n', left, start) + 1, left)
    right = text.find('\n', end)
    right = len(text) if right < 0 else right
    excerpt = text[left:right].strip()
    return excerpt[:300], left, min(right, left + len(excerpt))


def _scope_basis(quote, title):
    if _SCOPE_WIDE.search(quote) or _SCOPE_WIDE.search(title):
        return 'university_wide'
    if _CODES.search(quote):
        return 'program_code'
    if re.search(r'收费标准|收费公示|学费标准', title or ''):
        return 'fee_notice_unbound'
    return 'unconfirmed'


def _digest(source, field, quote):
    identity = '|'.join(str(part) for part in (source.get('id'), source.get('sha256'), field, quote))
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def _draft(source, field, value, period, quote, start, end, scope_basis):
    citation = {
        'source_id': source.get('id'), 'knowledge_id': source.get('knowledge_id'),
        'url': source.get('url'), 'title': source.get('title'), 'year': source.get('year'),
        'sha256': source.get('sha256'), 'quote': quote,
        'excerpt_start': start, 'excerpt_end': end,
    }
    return {
        'id': 'fc_' + _digest(source, field, quote)[:20],
        'field': field, 'label': FIELD_LABELS[field], 'value': value, 'period': period,
        'unit_id': source['unit_id'], 'unit_name': source.get('unit_name'),
        'year': source['year'], 'scope': {'unit_id': source['unit_id'], 'year': source['year']},
        'scope_basis': scope_basis, 'quote': quote,
        'source_id': source.get('id'), 'knowledge_id': source.get('knowledge_id'),
        'url': source.get('url'), 'sha256': source.get('sha256'),
        'excerpt_start': start, 'excerpt_end': end,
        'source': citation, 'status': 'draft', 'paused': True,
        'notice': '字段来自已读取原文的摘录，尚未参与推荐；需要独立来源核对后才能使用。',
    }


def _tuition_drafts(source):
    text = _text(source.get('excerpt'))
    title = _text(source.get('title'))
    drafts, seen = [], set()
    matches = []
    matches.extend(('year', match, float(match.group(1))) for match in _YEAR.finditer(text))
    matches.extend(('total', match, float(match.group(1))) for match in _TOTAL.finditer(text))
    for period, match, amount in matches:
        if amount <= 0 or amount > 10_000_000:
            continue
        quote, start, end = _quote_at(text, match.start(), match.end())
        key = (period, amount, quote)
        if key in seen:
            continue
        seen.add(key)
        drafts.append(_draft(source, 'tuition', amount, period, quote, start, end, _scope_basis(quote, title)))
    for match in _FREE.finditer(text):
        quote, start, end = _quote_at(text, match.start(), match.end())
        key = ('year', 0, quote)
        if key in seen:
            continue
        seen.add(key)
        drafts.append(_draft(source, 'tuition', 0, 'year', quote, start, end, _scope_basis(quote, title)))
    return drafts


def _eligibility_drafts(source):
    text = _text(source.get('excerpt'))
    title = _text(source.get('title'))
    if not re.search(r'简章|章程|招生说明|报考须知|招生公告', title):
        return []
    drafts, seen = [], set()
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 400 or not _ELIGIBILITY.search(line):
            continue
        if line in seen:
            continue
        seen.add(line)
        start = text.find(line)
        draft = _draft(source, 'eligibility', line, None, line, max(0, start), max(0, start) + len(line),
                       'university_wide' if _SCOPE_WIDE.search(line) else 'unconfirmed')
        draft.update(status='needs_review', paused=True,
                     notice='资格条款仍需与当年招生简章逐项核对；草案不会把未知资格改成已知。')
        drafts.append(draft)
    return drafts


def extract_field_candidates(sources):
    """Return de-duplicated drafts from every eligible saved original."""
    values = sources.values() if isinstance(sources, dict) else sources or ()
    drafts, seen = [], set()
    for source in values:
        if not _source_ok(source):
            continue
        for draft in [*_tuition_drafts(source), *_eligibility_drafts(source)]:
            if draft['id'] in seen:
                continue
            seen.add(draft['id'])
            drafts.append(draft)
    return drafts


def _identity(draft):
    return draft.get('knowledge_id') or draft.get('sha256') or draft.get('url') or draft.get('source_id')


def _public(row):
    return {key: deepcopy(row.get(key)) for key in
            ('source_id', 'knowledge_id', 'url', 'title', 'year', 'sha256', 'quote',
             'excerpt_start', 'excerpt_end', 'value', 'period', 'scope_basis')}


def _group_id(unit_id, year, field, period, scope_basis):
    payload = '|'.join(str(part) for part in (unit_id, year, field, period, scope_basis))
    return 'fc_' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]


def merge_field_candidates(drafts):
    """Merge drafts by scope. Agreement needs two independent sources; disagreement pauses."""
    groups = {}
    for draft in drafts:
        if not isinstance(draft, dict) or draft.get('field') not in FIELD_LABELS:
            continue
        key = (draft.get('unit_id'), draft.get('year'), draft.get('field'), draft.get('period'),
               draft.get('scope_basis'))
        groups.setdefault(key, []).append(draft)
    merged = []
    for (unit_id, year, field, period, scope_basis), rows in groups.items():
        sources = [dict(row) for row in rows]
        if field == 'eligibility':
            merged.append({
                'id': _group_id(unit_id, year, field, period, 'unconfirmed'),
                'field': field, 'label': FIELD_LABELS[field], 'unit_id': unit_id, 'year': year,
                'period': None, 'scope_basis': 'unconfirmed', 'status': 'needs_review',
                'paused': True, 'value': None, 'values': [_public(row) for row in sources[:6]],
                'source_count': len({_identity(row) for row in sources}),
                'notice': '资格条款仅作为待核线索；未知资格保持 unknown，不参与推荐判定。',
            })
            continue
        values = {}
        for row in sources:
            values.setdefault((row.get('value'), row.get('period')), []).append(row)
        distinct = len(values)
        identities = {_identity(row) for row in sources}
        base = {'field': field, 'label': FIELD_LABELS[field], 'unit_id': unit_id, 'year': year,
                'period': period, 'scope_basis': scope_basis, 'source_count': len(identities),
                'scope': {'unit_id': unit_id, 'year': year},
                'id': _group_id(unit_id, year, field, period, scope_basis)}
        if distinct > 1:
            merged.append({**base, 'status': 'conflict', 'paused': True, 'value': None,
                           'values': [_public(row) for row in sources[:8]],
                           'notice': '来源对同一收费口径给出不同数值，字段暂停；受影响候选不参与推荐。'})
            continue
        value, _ = next(iter(values))
        status = 'verified' if len(identities) >= 2 else 'draft'
        merged.append({**base, 'status': status, 'paused': status != 'verified', 'value': value,
                       'values': [_public(row) for row in sources[:8]],
                       'notice': ('两个独立原文一致，可作为带出处的参考字段。' if status == 'verified'
                                  else '仅有一个来源，保留为草案，不参与推荐。')})
    return merged


def _known_tuition(program):
    tuition = program.get('tuition')
    return (isinstance(tuition, dict) and isinstance(tuition.get('amount'), (int, float))
            and not isinstance(tuition.get('amount'), bool))


def apply_field_candidates(programs, merged):
    """Apply only verified, year-scoped tuition; conflicts pause the affected candidate."""
    applied, paused = [], []
    by_unit = {}
    for candidate in merged:
        if candidate.get('field') == 'tuition':
            by_unit.setdefault((candidate.get('unit_id'), candidate.get('year')), []).append(candidate)
    values = programs.values() if isinstance(programs, dict) else programs or ()
    for program in values:
        if not isinstance(program, dict):
            continue
        rows = by_unit.get((program.get('unit_id'), program.get('year')), [])
        conflicts = [row for row in rows if row.get('status') == 'conflict']
        if conflicts:
            value = {'field': 'tuition', 'message': conflicts[0].get('notice'),
                     'candidate_id': conflicts[0].get('id'), 'paused': True}
            existing = program.get('field_conflicts') if isinstance(program.get('field_conflicts'), list) else []
            if not any(item.get('candidate_id') == value['candidate_id'] for item in existing if isinstance(item, dict)):
                program['field_conflicts'] = [*existing, value]
                program['requires_review'] = True
                paused.append({'program_id': program.get('id'), **value})
            continue
        if _known_tuition(program):
            continue
        ordered = sorted(rows, key=lambda item: (item.get('scope_basis') != 'program_code',
                                                 -item.get('source_count', 0)))
        for row in ordered:
            if row.get('status') != 'verified' or row.get('period') != 'year':
                continue
            if row.get('scope_basis') == 'program_code':
                quotes = ' '.join(item.get('quote', '') for item in row.get('values', []))
                if str(program.get('code')) not in quotes:
                    continue
            elif row.get('scope_basis') != 'university_wide':
                continue
            evidence = [{'url': item.get('url'), 'title': item.get('title'), 'year': item.get('year'),
                         'sha256': item.get('sha256'), 'source_id': item.get('source_id'),
                         'knowledge_id': item.get('knowledge_id'), 'quote': item.get('quote'),
                         'excerpt_start': item.get('excerpt_start'), 'excerpt_end': item.get('excerpt_end')}
                        for item in row.get('values', [])]
            program['tuition'] = {'amount': row.get('value'), 'period': 'year', 'evidence': evidence,
                                  'verified_by': 'two_source_agreement', 'field_candidate_id': row.get('id')}
            applied.append({'program_id': program.get('id'), 'candidate_id': row.get('id'),
                            'amount': row.get('value'), 'period': 'year'})
            break
    return {'applied': applied, 'paused': paused}


def field_candidates(programs, sources):
    """Convenience: extract, merge and apply in one deterministic step."""
    merged = merge_field_candidates(extract_field_candidates(sources))
    result = apply_field_candidates(programs, merged)
    return {'candidates': merged, **result,
            'notice': '字段草案须有独立来源一致且年度范围匹配才参与推荐；冲突字段暂停，缺失仍为未知。'}
