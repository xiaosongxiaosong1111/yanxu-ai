"""Deterministic admission of bounded drafts from fully read official originals.

The resolver owns fresh trust/read receipts. This module never fetches, persists,
repairs offsets, joins neighboring rows, or accepts model-supplied authority.
"""
from copy import deepcopy
import hashlib
import json
import re

from collectors.catalog_html import _national, _subjects
from collectors.catalogs import NATIONAL, _program


PROOF_VERSION = 1
MAX_DRAFTS = 6
_FIELDS = {'code', 'name', 'department', 'direction', 'study_mode', 'row', 'exams', 'supplements'}
_CITATION = {'segment_id', 'char_start', 'char_end', 'quote'}
_MAJOR = re.compile(r'(?<![0-9A-Z])(?:[0-9A-Z]{6}|(?:0[1-9]|1[0-4])[0-9]{2})(?![0-9A-Z])')
_MARK = re.compile(r'[①②③④⑴⑵⑶⑷]|(?<!\d)[1-4][、.．](?=[1-9]\d{2})|科目[一二三四][:：]')
_LABELS = {'code': '专业代码', 'name': '专业名称', 'department': '(?:院系|学院|院系所)',
           'direction': '研究方向', 'study_mode': '学习方式', 'exams': '(?:初试科目|考试科目)'}
_COUPLED = re.compile(r'整组|套组|组合|方案|对应|搭配|配套|同时|分别|选择.{0,40}则|选.{0,40}则')
_NUMBERED = re.compile(r'(?m)^[ \t]*(?:[1-9]\d{0,2}|[一二三四五六七八九十]+)[.、．][ \t]*')
_EXCEPTION = re.compile(r'部分|除|例外|其中|分别|仅限|针对|专业学位|学术学位')
_COURSE_ATOM = re.compile(r'[\u3400-\u9fffA-Za-z0-9·&+\-/]{2,60}(?:[（(][一二三四五六七八九十A-Za-z0-9]{1,8}[）)])?')
_REASONS = {
    'invalid_request': '学校、年度或片段参数不合法。',
    'batch_limit': '每批最多提交六条专业草案。',
    'invalid_draft': '草案仅可包含专业、方向、学习方式及规定引用，不可提交学校、费用、资格或候选身份。',
    'invalid_citation': '引用必须仅含片段、绝对起止位置和逐字原句。',
    'untrusted_citation': '依据尚未完整读取，或当前原件不可可信复用；请补读当前有效原文。',
    'source_scope': '依据的学校或实际招生年度与当前候选不一致。',
    'stale_generation': '引文不属于当前原件的有效提取代次，请重新读取。',
    'citation_mismatch': '原句与已读原文的绝对字符范围不一致。',
    'incomplete_row': '只接受完整且清晰的单条目录行；截断、续行或跨行内容仍待核。',
    'cross_row': '初试科目须完整属于同一实际专业目录行，不能借用其他行。',
    'ambiguous_identity': '目录行包含多个或不明确的专业代码，不能确定单一专业关系。',
    'unsupported_row_scope': '目录行明确涉及其他年度、示例或暂停招生，不能作为本年度在招专业。',
    'unsupported_identity': '专业名称、院系、方向或学习方式未获同一目录行的精确完整支持。',
    'invalid_exams': '初试科目必须完整、无重复且按连续的二、三或四个考试单元给出。',
    'national_identity_conflict': '原文全国统考代码与名称存在冲突，须向招生单位核实。',
    'correlated_exams': '原文包含关联科目套组，当前不能安全转换为独立选项，暂不参与计算。',
    'unsupported_supplement': '补充依据必须明确适用于同校同年度全部专业且没有例外。',
}


class _Rejected(ValueError):
    pass


def _require(condition, code):
    if not condition:
        raise _Rejected(code)


def _citation_shape(ref):
    _require(isinstance(ref, dict) and set(ref) == _CITATION, 'invalid_citation')
    start, end, quote = (ref.get(key) for key in ('char_start', 'char_end', 'quote'))
    _require(isinstance(ref.get('segment_id'), str) and bool(ref['segment_id']) and
             type(start) is int and type(end) is int and 0 <= start < end and
             isinstance(quote, str) and 0 < len(quote) <= 6000, 'invalid_citation')


def _resolve(ref, resolver, unit, year, expected_source=None, with_context=False):
    _citation_shape(ref)
    try:
        value = resolver(deepcopy(ref))
    except Exception:
        raise _Rejected('untrusted_citation') from None
    _require(isinstance(value, dict) and all(value.get(key) is True for key in
             ('read_complete', 'trusted', 'current')), 'untrusted_citation')
    block, source = value.get('block'), value.get('source')
    _require(isinstance(block, dict) and isinstance(source, dict), 'untrusted_citation')
    quality = source.get('quality') or source.get('knowledge_quality') or {}
    _require(isinstance(quality, dict) and quality.get('state') == 'accepted' and
             source.get('status') == 'official_original_read' and source.get('usable') is True and
             source.get('original_available') is True and source.get('validity') == 'active', 'untrusted_citation')
    _require(source.get('unit_id') == str(unit['id']) and source.get('year') == year, 'source_scope')
    extraction = source.get('extraction') or {}
    entry = source.get('knowledge_id') or source.get('id')
    sid = value.get('source_id')
    _require(isinstance(extraction, dict) and isinstance(entry, str) and bool(entry) and
             isinstance(sid, str) and bool(sid) and
             re.fullmatch(r'[0-9a-f]{64}', str(source.get('sha256') or '')), 'untrusted_citation')
    # source.text_truncated also denotes the UI preview cap, not canonical loss.
    _require(extraction.get('extraction_status') == 'complete' and
             not extraction.get('text_truncated'), 'untrusted_citation')
    _require(block.get('entry_id') == entry and bool(block.get('generation_id')) and
             bool(block.get('extraction_id')) and block['generation_id'] == extraction.get('generation_id') and
             block['extraction_id'] == extraction.get('id'), 'stale_generation')
    _require(expected_source is None or sid == expected_source, 'cross_row')
    start, end, text = (block.get(key) for key in ('char_start', 'char_end', 'text'))
    _require(type(start) is int and type(end) is int and isinstance(text, str) and
             start >= 0 and end - start == len(text) and
             (block.get('id') or block.get('segment_id')) == ref['segment_id'], 'citation_mismatch')
    _require(start <= ref['char_start'] < ref['char_end'] <= end and
             text[ref['char_start'] - start:ref['char_end'] - start] == ref['quote'], 'citation_mismatch')
    origin = {'source_id': sid, 'entry_id': entry, 'extraction_id': block['extraction_id'],
              'generation_id': block['generation_id'], 'sha256': source['sha256'], 'source_sha256': source['sha256'],
              'extraction_status': extraction['extraction_status'],
              'url': source.get('url'), 'unit_id': str(unit['id']), 'year': year}
    proof = {**deepcopy(ref), **origin, 'page_start': block.get('page_start'),
             'page_end': block.get('page_end'), 'section_path': deepcopy(block.get('section_path', []))}
    contexts = _read_contexts(value, block, origin, ref) if with_context else []
    return deepcopy(block), proof, origin, contexts


def _read_contexts(value, selected, origin, ref):
    blocks = value.get('read_blocks', [selected])
    _require(isinstance(blocks, list) and 1 <= len(blocks) <= 64, 'untrusted_citation')
    contexts, size = {}, 0
    for block in blocks:
        _require(isinstance(block, dict) and all(block.get(key) == origin[key] for key in
                 ('entry_id', 'extraction_id', 'generation_id')), 'untrusted_citation')
        start, end, text = (block.get(key) for key in ('char_start', 'char_end', 'text'))
        _require(type(start) is int and type(end) is int and isinstance(text, str) and
                 start >= 0 and end - start == len(text), 'untrusted_citation')
        size += len(text)
        _require(size <= 128000, 'untrusted_citation')
        sid = block.get('id') or block.get('segment_id') or ref['segment_id']
        _require(isinstance(sid, str) and bool(sid), 'untrusted_citation')
        contexts[(start, end, sid)] = {**origin, 'segment_id': sid, 'char_start': start,
            'char_end': end, 'quote': text, 'kind': block.get('kind'), 'role': block.get('role'),
            **{key: block.get(key) for key in ('content_truncated', 'continues_previous', 'continues_next')}}
    _require(any(item['char_start'] == selected['char_start'] and item['char_end'] == selected['char_end']
                 and item['quote'] == selected['text'] for item in contexts.values()), 'untrusted_citation')
    return [contexts[key] for key in sorted(contexts)]


def _complete(block):
    _require(not any(block.get(key) for key in
             ('content_truncated', 'continues_previous', 'continues_next')) and
             block.get('row_complete') is not False, 'incomplete_row')


def _cells(text):
    delimiter = r'[^\t]+' if '\t' in text else r'[^|]+' if '|' in text else r'\S+'
    return [(part.group().strip(), part.start() + len(part.group()) - len(part.group().lstrip()))
            for part in re.finditer(delimiter, text) if part.group().strip()]


def _field_span(row_proof, value, position):
    start = row_proof['char_start'] + position
    return {**deepcopy(row_proof), 'char_start': start, 'char_end': start + len(value), 'quote': value}


def _labeled(text, label):
    matches = list(re.finditer(r'(?:^|[；;])\s*' + label + r'[:：]\s*([^；;]+)', text))
    return (matches[0][1].strip(), matches[0].start(1)) if len(matches) == 1 else None


def _unlabelled_field(field, value):
    if field == 'department':
        return re.fullmatch(r'(?:\d{3}\s*)?[\u3400-\u9fffA-Za-z·（）() -]{2,}(?:学院|研究院|研究所|中心|学部|教学部|系|所|院|部)', value) is not None
    if field == 'direction':
        return (re.fullmatch(r'(?:[0-9A-Z]{2}(?![0-9A-Z])|[1-9][.、．])\s*[^0-9A-Z].+', value) is not None or
                value in {'不区分研究方向', '不区分方向', '不设研究方向', '不设方向'})
    return True


def _identity(draft, row, proof):
    text = row['text']
    matches = list(_MAJOR.finditer(text))
    _require(len(matches) == 1, 'ambiguous_identity')
    _require(matches[0][0] == draft['code'], 'unsupported_identity')
    fields = {'code': _field_span(proof, draft['code'], matches[0].start())}
    labelled = _labeled(text, _LABELS['code']) is not None
    cells = _cells(text)
    if labelled:
        major_name = _labeled(text, _LABELS['name'])
        _require(major_name is not None and major_name[0] == draft['name'], 'unsupported_identity')
        fields['name'] = _field_span(proof, draft['name'], major_name[1])
    else:
        major_index = next((i for i, (cell, _) in enumerate(cells) if draft['code'] in cell), None)
        _require(major_index is not None, 'unsupported_identity')
        cell, position = cells[major_index]
        _require(cell.startswith(draft['code']), 'unsupported_identity')
        tail = cell[len(draft['code']):].strip()
        if tail:
            name, name_position = tail, position + cell.index(tail)
        else:
            _require(major_index + 1 < len(cells), 'unsupported_identity')
            name, name_position = cells[major_index + 1]
        _require(name == draft['name'], 'unsupported_identity')
        fields['name'] = _field_span(proof, name, name_position)
    for field in ('department', 'direction', 'study_mode'):
        value = draft.get(field)
        if not value:
            continue
        if labelled:
            found = _labeled(text, _LABELS[field])
            found = found if found and found[0] == value else None
        else:
            _require(_unlabelled_field(field, value), 'unsupported_identity')
            found = next(((value, position + cell.index(value)) for cell, position in cells
                          if cell == value or field == 'department' and
                          re.fullmatch(r'\d{3}\s*' + re.escape(value), cell)), None)
        if found:
            fields[field] = _field_span(proof, value, found[1])
        else:
            _require(field == 'study_mode', 'unsupported_identity')
    modes = re.findall(r'非全日制|(?<!非)全日制', text)
    _require(not modes or set(modes) == {draft['study_mode']}, 'unsupported_identity')
    return fields


def _exam_extent(row, row_proof, ref):
    start, end = ref['char_start'] - row_proof['char_start'], ref['char_end'] - row_proof['char_start']
    text = row['text']
    _require(0 <= start < end <= len(text), 'cross_row')
    _require(all(start <= mark.start() < end for mark in _MARK.finditer(text)), 'invalid_exams')
    # A model cannot silently discard an OR suffix inside the same exam cell.
    if '\t' in text or '|' in text:
        delimiter = '\t' if '\t' in text else '|'
        _require((start == 0 or text[start - 1] == delimiter) and
                 (end == len(text) or text[end] == delimiter), 'invalid_exams')
    elif _labeled(text, _LABELS['exams']):
        found = _labeled(text, _LABELS['exams'])
        _require(found == (ref['quote'], start), 'invalid_exams')
    else:
        _require((start == 0 or text[start - 1].isspace()) and end == len(text), 'invalid_exams')


def _supplements(draft, resolve, unit, year):
    values = draft.get('supplements', [])
    _require(isinstance(values, list) and len(values) <= 2, 'invalid_draft')
    _require(all(isinstance(item, dict) and set(item) == {'field', 'citation'} and
                 isinstance(item.get('field'), str) and item['field'] in {'politics', 'study_mode'}
                 for item in values), 'invalid_draft')
    _require(len({item['field'] for item in values}) == len(values), 'invalid_draft')
    fields, origins = [], []
    for item in values:
        block, proof, origin, contexts = _resolve(item['citation'], resolve, unit, year, with_context=True)
        _complete(block)
        quote = item['citation']['quote']
        left = item['citation']['char_start'] - block['char_start']
        right = item['citation']['char_end'] - block['char_start']
        prefix, suffix = block['text'][:left].rstrip(' \t'), block['text'][right:].lstrip(' \t')
        _require((not prefix or prefix[-1] in '。；;\n') and
                 (not suffix or quote[-1] in '。；;' or suffix[0] in '。；;\n'), 'unsupported_supplement')
        scope = _supplement_scope(block['text'], left, right, item['field'])
        _check_read_contexts(contexts, block, item['field'], draft['study_mode'])
        if item['field'] == 'politics':
            pattern = r'(?:[1一][.、．]\s*)?(?:我校|本校|全校)?(?:所有|全部|各)?(?:专业)?(?:的)?(?:初试)?考试科目一均为[:：]\s*①101思想政治理论[。；;]?'
        else:
            mode = re.escape(draft['study_mode'])
            pattern = r'(?:我校|本校|全校)(?:所有|全部|各)专业均(?:招收' + mode + r'硕士研究生|为' + mode + r'|采用' + mode + r'(?:学习方式)?)[。；;]?'
        _require(re.fullmatch(pattern, quote) is not None, 'unsupported_supplement')
        fields.append({'field': item['field'], 'read_contexts': contexts, 'citation': {**proof, 'field': item['field'],
                       'clause_span': {'char_start': block['char_start'] + scope[0],
                                       'char_end': block['char_start'] + scope[1],
                                       'quote': block['text'][scope[0]:scope[1]]}}})
        origins.append(origin)
    return fields, origins


def _check_read_contexts(contexts, selected, field, mode):
    topic = r'科目一|政治|(?<!\d)(?:101|199)(?!\d)' if field == 'politics' else r'全日制|学习方式|学习形式|培养方式'
    for context in contexts:
        if (context['char_start'], context['char_end']) == (selected['char_start'], selected['char_end']):
            continue
        text = context['quote']
        _require(not (context.get('kind') == 'heading' and _EXCEPTION.search(text)), 'unsupported_supplement')
        markers = list(_NUMBERED.finditer(text))
        starts = [0, *(mark.start() for mark in markers if mark.start() > 0)]
        for i, start in enumerate(starts):
            clause = text[start:starts[i + 1] if i + 1 < len(starts) else len(text)]
            if '\t' not in clause and _EXCEPTION.search(clause):
                _require(not re.search(topic + r'|上述|以上|前述|本条|前条', clause), 'unsupported_supplement')
            if field == 'study_mode':
                _require(all(value == mode for value in re.findall(r'非全日制|(?<!非)全日制', clause)),
                         'unsupported_supplement')
            else:
                _require(not re.search(r'科目一.{0,25}(?<!\d)(?!101)[1-9]\d{2}(?!\d)|[①⑴]\s*(?!101)[1-9]\d{2}', clause) and
                         not re.search(r'(?:不考|免考|不设|无需).{0,12}(?:政治|科目一)|(?:政治|科目一).{0,12}(?:不考|免考|不设|无需)', clause),
                         'unsupported_supplement')


def _supplement_scope(text, left, right, field):
    """Keep numbered continuation lines; unrelated sibling notes have own scope."""
    markers = list(_NUMBERED.finditer(text))
    line_start = text.rfind('\n', 0, left) + 1
    line_end = text.find('\n', left)
    line_end = len(text) if line_end < 0 else line_end
    index = next((i for i, marker in enumerate(markers) if marker.start() == line_start), None)
    if index is None:
        _require(not _EXCEPTION.search(text), 'unsupported_supplement')
        return 0, len(text)
    _require(text[left:right] == text[line_start:line_end].strip() and
             left == line_start + len(text[line_start:line_end]) - len(text[line_start:line_end].lstrip()),
             'unsupported_supplement')
    clauses = [(marker.start(), markers[i + 1].start() if i + 1 < len(markers) else len(text))
               for i, marker in enumerate(markers)]
    start, end = clauses[index]
    _require(not _EXCEPTION.search(text[:markers[0].start()] + text[start:end]), 'unsupported_supplement')
    topic = r'科目一|政治|(?<!\d)(?:101|199)(?!\d)' if field == 'politics' else r'全日制|学习方式|学习形式|培养方式'
    for i, (other_start, other_end) in enumerate(clauses):
        other = text[other_start:other_end]
        if i != index and _EXCEPTION.search(other):
            _require(not re.search(topic + r'|上述|以上|前述|本条|前条', other), 'unsupported_supplement')
    return start, end


def _courses(exams, politics, unit, document):
    _require(not _COUPLED.search(exams) and
             len([mark for mark in _MARK.finditer(exams) if mark[0] in {'①', '⑴', '科目一：', '科目一:'}]) <= 1,
             'correlated_exams')
    raw = ('①101思想政治理论' if politics else '') + exams
    def slot(value):
        if value[0] in '①②③④': return '①②③④'.index(value[0]) + 1
        if value[0] in '⑴⑵⑶⑷': return '⑴⑵⑶⑷'.index(value[0]) + 1
        return '一二三四'.index(value[2]) + 1 if value.startswith('科目') else int(value[0])
    marks = [slot(mark[0]) for mark in _MARK.finditer(raw)]
    _require(len(marks) == len(set(marks)), 'correlated_exams')
    _require(marks == sorted(marks), 'invalid_exams')
    subjects, _, issues = _subjects(raw, unit['id'], document)
    _require(bool(subjects) and not issues, 'invalid_exams')
    _require(all(subject['code'] not in NATIONAL or _national(subject['code'], subject['name'])
                 for subject in subjects), 'national_identity_conflict')
    # Local names are atomic; long annotations cannot become invented courses.
    _require(all(subject['code'] in NATIONAL or
                 _COURSE_ATOM.fullmatch(subject['name']) and not re.search(
                     r'\d{3}|或|复试|加试|选考|(?:选|若|当).{0,30}(?:考|须|应|则|需)', subject['name'])
                 for subject in subjects), 'invalid_exams')
    return subjects


def _subject_spans(subjects):
    result = []
    for subject in subjects:
        ref = subject['evidence']
        pattern = (r'(?<![0-9A-Z])' + re.escape(subject['code']) + r'\s*' +
                   r'\s*'.join(re.escape(char) for char in subject['name']))
        found = re.search(pattern, ref['quote'])
        _require(found is not None, 'invalid_exams')
        start = found.start()
        name_start = start + len(subject['code'])
        while name_start < found.end() and ref['quote'][name_start].isspace():
            name_start += 1
        result.append({'exam_unit': subject['exam_unit'], 'scope': subject['scope'],
                       'code': {**_field_span(ref, ref['quote'][start:start + 3], start), 'field': 'subject_code'},
                       'name': {**_field_span(ref, ref['quote'][name_start:found.end()], name_start), 'field': 'subject_name'}})
    return result


def _validated(unit, year, source_id, segment_id, draft, resolver):
    _require(isinstance(draft, dict) and set(draft) <= _FIELDS and
             {'code', 'name', 'study_mode', 'row', 'exams'} <= set(draft), 'invalid_draft')
    for field in ('code', 'name', 'department', 'direction', 'study_mode'):
        value = draft.get(field, '')
        _require(isinstance(value, str) and len(value) <= 120 and not re.search(r'[\r\n\t]', value), 'invalid_draft')
    _require(bool(draft['code']) and bool(draft['name']), 'invalid_draft')
    _require(draft['study_mode'] in {'全日制', '非全日制'}, 'unsupported_identity')
    row, row_proof, origin, _ = _resolve(draft['row'], resolver, unit, year, source_id)
    _require(draft['row']['segment_id'] == segment_id, 'cross_row')
    _complete(row)
    _require(isinstance(row.get('kind'), str) and row['kind'] in {'table_row', 'paragraph'} and
             (row.get('kind') != 'table_row' or row.get('row_complete') is True) and
             not re.search(r'[\r\n\f]', row['text']) and
             row_proof['quote'] == row['text'] and row_proof['char_start'] == row['char_start'] and
             row_proof['char_end'] == row['char_end'], 'incomplete_row')
    years = re.findall(r'(?<!\d)((?:19|20|21)\d{2})\s*年', row['text'])
    _require(all(int(value) == year for value in years) and
             not re.search(r'示例|历史目录|往年目录|仅供参考|停招|(?:停止|暂停|取消)招生|不再招生', row['text']),
             'unsupported_row_scope')
    _require(not _COUPLED.search(row['text']), 'correlated_exams')
    row_proof = {**row_proof, 'field': 'row'}
    fields = {key: {**value, 'field': key} for key, value in _identity(draft, row, row_proof).items()}
    _, exam_proof, exam_origin, _ = _resolve(draft['exams'], resolver, unit, year, source_id)
    _require(all(exam_origin[key] == origin[key] for key in origin) and
             row_proof['segment_id'] == exam_proof['segment_id'], 'cross_row')
    exam_proof = {**exam_proof, 'field': 'exams'}
    _exam_extent(row, row_proof, exam_proof)
    supplements, origins = _supplements(draft, resolver, unit, year)
    mode = next((item['citation'] for item in supplements if item['field'] == 'study_mode'), None)
    politics = next((item['citation'] for item in supplements if item['field'] == 'politics'), None)
    _require('study_mode' in fields or mode is not None, 'unsupported_identity')
    if mode is not None:
        fields = {**fields, 'study_mode': mode}
    if politics is not None:
        _require(not re.search(r'[①⑴]|科目一[:：]|(?<!\d)101(?!\d)', exam_proof['quote']), 'unsupported_supplement')
    document = {'id': origin['entry_id'], 'url': origin['url']}
    subjects = _courses(exam_proof['quote'], politics, unit, document)
    subjects = [{**subject, 'evidence': deepcopy(politics if subject['exam_unit'] == 1 and politics else exam_proof)}
                for subject in subjects]
    sources = {json.dumps(item, sort_keys=True): item for item in [origin, exam_origin, *origins]}
    proof = {'version': PROOF_VERSION, 'draft': deepcopy(draft),
             'fields': {**fields, 'row': row_proof, 'exams': exam_proof, 'supplements': supplements,
                        'subjects': _subject_spans(subjects)},
             'sources': [sources[key] for key in sorted(sources)]}
    identity_proof = {**proof, 'fields': {**proof['fields'], 'supplements':
        [{key: value for key, value in item.items() if key != 'read_contexts'} for item in supplements]}}
    identity = json.dumps({'unit_id': str(unit['id']), 'year': year, 'proof': identity_proof},
                          ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    identifier = 'tp_' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
    result = _program(unit, year, draft.get('department', ''), draft['code'], draft['name'],
                      draft.get('direction', ''), draft['study_mode'], subjects, '', document)
    refs = [row_proof, exam_proof, *(item['citation'] for item in supplements)]
    return {**result, 'id': identifier, 'candidate_origin': 'official_original',
            'verification_mode': 'research_original', 'exam_matching_supported': True,
            'evidence': deepcopy(row_proof), 'exam_evidence': [deepcopy(exam_proof), *([deepcopy(politics)] if politics else [])],
            'evidence_refs': deepcopy(refs), 'candidate_proof': proof}


def validate_drafts(unit, year, source_id, segment_id, drafts, resolve_citation):
    """Return new accepted programs and per-index rejection codes/reasons.

    ``resolve_citation(ref)`` returns ``block/source/source_id`` and explicit true
    ``read_complete/trusted/current`` flags. The block is the actual complete
    selected or context block; source is current accepted official metadata.
    Cite offsets are Python character offsets in that extraction, never offsets
    in the concatenated read response. Revalidate stored ``candidate_proof.draft``
    with fresh receipts; the returned ID must match before reusing a candidate.
    Only explicit single rows and labelled paragraphs are supported. Ambiguous
    layouts or linked choice descriptions stay pending; this is not a general
    natural-language proof that arbitrary catalog layouts have independent ORs.
    Every local OR option must be an atomic course name, with at most a simple
    suffix such as (一)/(A); longer notes and embedded exam codes stay pending.
    Supplement resolvers include fresh ``read_blocks`` from their already-read
    windows. Known related exceptions stop admission; this does not establish
    that unread portions of the original contain no exceptions. These windows
    are retained in proof, while their source versions bind the stable ID.
    """
    if not (isinstance(unit, dict) and unit.get('id') and unit.get('name') and type(year) is int and
            2000 <= year <= 2200 and isinstance(source_id, str) and bool(source_id) and
            isinstance(segment_id, str) and bool(segment_id) and callable(resolve_citation) and
            isinstance(drafts, list)):
        return {'accepted': [], 'rejected': [{'index': -1, 'code': 'invalid_request', 'reason': _REASONS['invalid_request']}]}
    if len(drafts) > MAX_DRAFTS:
        return {'accepted': [], 'rejected': [{'index': -1, 'code': 'batch_limit', 'reason': _REASONS['batch_limit']}]}
    accepted, rejected, seen = [], [], set()
    for index, draft in enumerate(drafts):
        try:
            program = _validated(unit, year, source_id, segment_id, draft, resolve_citation)
            if program['id'] not in seen:
                accepted.append(program)
                seen.add(program['id'])
        except _Rejected as error:
            code = str(error)
            rejected.append({'index': index, 'code': code, 'reason': _REASONS[code]})
    return {'accepted': accepted, 'rejected': rejected}
