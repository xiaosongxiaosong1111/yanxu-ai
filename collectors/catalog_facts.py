"""Original catalogue facts with missing identity fields; never admissions candidates."""
from collections import Counter
import hashlib
from html.parser import HTMLParser
import re

from .catalog_html import (_VisibleCatalog, _header, _major, _subjects, _modes, _CODE,
    _context_mode, _exam_method, _subject_exam_scope, _national)
from .documents import decode_text
from .national_sources import _html_info
from public_sources import rendered_source_html, document_attribution
from service_verification import ordinary_catalog_scope


PARSER = 'catalog_facts_v2'
CONDITIONAL = re.compile(r'(?:\d{2}(?:[-—至~～]\d{2})?方向|方向[:：]|按(?:研究)?方向|对应(?:研究)?方向)')
LINKED_EXAMS = re.compile(r'(?:成套|配套|成组)(?:选择|选考|报考)|(?:[③④]|科目[三四]|业务课[一二]).{0,18}(?:对应|组合)(?:选择|选考|报考)|(?:对应|组合)选(?:择|考)')
PUBLIC_NOTICE = '仅展示原文公布的专业与初试科目；学习方式及报考范围待核，不能用于报考匹配。'


def _compact(value):
    return re.sub(r'\s+', '', str(value or '')).replace('（', '(').replace('）', ')')


class _VisibleFacts(_VisibleCatalog):
    def _hidden(self, tag, attrs):
        classes = dict(attrs).get('class', '') or ''
        return super()._hidden(tag, attrs) or bool(re.search(
            r'(?:^|\s)(?:hidden|hide|d-none|sr-only|tooltip(?:-[\w-]+)?|popover(?:-[\w-]+)?)(?:\s|$)', classes, re.I))


def _visible(raw, url):
    parser = _VisibleFacts()
    parser.feed(rendered_source_html(raw, url))
    return ''.join(parser.parts)


class _Cells(HTMLParser):
    """Keep literal cell text and explicit rowspan origins; no neighbouring-row inference."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.tables = [], []

    def handle_starttag(self, tag, attrs):
        if tag == 'table': self.stack.append({'rows': [], 'pending': {}, 'row': None, 'cell': None})
        if not self.stack: return
        table = self.stack[-1]
        if tag == 'tr':
            table['row'] = {col: value for col, (value, remaining) in table['pending'].items()}
            table['pending'] = {col: (value, remaining-1) for col, (value, remaining) in table['pending'].items() if remaining > 1}
        if tag in {'td', 'th'}:
            attributes = dict(attrs)
            def span(name):
                try: return min(100, max(1, int(attributes.get(name, 1))))
                except (ValueError, TypeError): return 1
            table['cell'] = {'parts': [], 'cols': span('colspan'), 'rows': span('rowspan')}

    def handle_data(self, value):
        if self.stack and self.stack[-1]['cell'] is not None:
            self.stack[-1]['cell']['parts'].append(value)

    def handle_endtag(self, tag):
        if not self.stack: return
        table = self.stack[-1]
        if tag in {'td', 'th'} and table['cell'] is not None:
            if table['row'] is None: table['row'] = {}
            raw, row = table['cell'], table['row']
            col = 0
            while col in row: col += 1
            value = {'text': ''.join(raw['parts']), 'origin_row': len(table['rows'])+1,
                     'column': col+1, 'rowspan': raw['rows'], 'colspan': raw['cols']}
            for offset in range(raw['cols']):
                row[col+offset] = value
                if raw['rows'] > 1: table['pending'][col+offset] = value, raw['rows']-1
            table['cell'] = None
        if tag == 'tr' and table['row'] is not None:
            row = table['row']
            if row: table['rows'].append([row.get(col) for col in range(max(row)+1)])
            table['row'] = None
        if tag == 'table': self.tables.append(self.stack.pop()['rows'])


def _scope(raw, unit, year, document):
    if (not isinstance(unit, dict) or not isinstance(document, dict)
            or not isinstance(raw, bytes) or not 0 < len(raw) <= 32*1024*1024 or type(year) is not int
            or year != 2027 or str(document.get('unit_id')) != str(unit.get('id'))
            or document.get('year') is not None and document.get('year') != year
            or hashlib.sha256(raw).hexdigest() != document.get('sha256')
            or not isinstance(document.get('id'), str) or not document['id']
            or not isinstance(document.get('url'), str)
            or raw.startswith((b'%PDF-', b'PK\x03\x04', b'\xd0\xcf\x11\xe0'))):
        return None, '原文摘要、单位、参考年度或文档身份未通过核验。'
    html = _visible(decode_text(raw, document.get('content_type', '')), document.get('url', ''))
    title, source_year, _, _ = _html_info(html, unit, year, document.get('url', ''))
    if source_year != year or set(re.findall(r'(?<!\d)(20\d{2})(?!\d)', title)) != {str(year)}:
        return None, '原文主标题未明确单一参考年度，未采用历史链接推断。'
    if (not ordinary_catalog_scope(html, html=True) or re.search(r'拟录取|录取名单|调剂|成绩公示', title)
            or re.search(r'复试|加试', title) and not re.search(r'初试', title)):
        return None, '原文不是本年度普通硕士初试目录范围。'
    if document_attribution(document.get('url', ''), unit, '', title, title)['status'] != 'verified':
        return None, '原文归属不能与该招生单位一致核对。'
    return html, None


def _reference(document, cell, table, row, field):
    return {'document_id': document['id'], 'url': document['url'], 'quote': cell['text'],
        'field': field, 'source_locator': {'table_order': 'postorder', 'table': table, 'row': row,
            'column': cell['column'], 'origin_row': cell['origin_row']}}


def _get(row, index):
    return row[index]['text'] if index is not None and index < len(row) and row[index] else ''


def _excluded_row(value):
    text = _compact(value)
    return bool(re.search(r'(?<!不)(?:仅|只)(?:接受|接收|招收|招|限)?(?:推荐免试|推免)(?:生|研究生|考生)?|'
        r'(?:不|拒绝)(?:接收|接受|招收|招)(?:全国)?(?:统考|统一考试)(?:生|考生|研究生)?', text))


def _exam(row, header, unit, document, table, row_number):
    single = header['exam']
    columns = [single] if single is not None else list(header['slots'].values())
    raw = '\n'.join(_get(row, col) for col in columns)
    if (CONDITIONAL.search(_compact(raw)) or LINKED_EXAMS.search(_compact('；'.join(cell['text'] for cell in row if cell)))
            or re.fullmatch(r'同上(?:科目)?[。；]?', _compact(raw))):
        return [], [], '科目含未分清的方向条件或同上引用，未借用相邻行。'
    slots = {slot: _get(row, col) for slot, col in header['slots'].items()} if single is None else None
    subjects, _, issues = _subjects(_get(row, single), str(unit['id']), document, slots)
    count = len({s['exam_unit'] for s in subjects})
    if count == 2 and {s['code'] for s in subjects if s['exam_unit'] == 1} != {'199'}:
        issues = [*issues, '两单元结构未能证明是完整的管理类初试组合。']
    if issues or _subject_exam_scope(subjects) != 'unified':
        return [], [], '完整初试单元或普通统考科目范围尚未核对。'
    refs = [_reference(document, row[col], table, row_number, 'subjects') for col in columns if col < len(row) and row[col]]
    typed = []
    for subject in subjects:
        column = single if single is not None else header['slots'][subject['exam_unit']]
        typed.append({**subject, 'evidence': _reference(document, row[column], table, row_number, 'subjects')})
    return typed, refs, None


def _fact(row, header, unit, year, document, table, row_number, header_row):
    values = [_compact(cell['text']) if cell else '' for cell in row]
    identity_col = header['code'] if header['code'] is not None else header['major']
    identity_text = _get(row, identity_col)
    if len(_CODE.findall(identity_text)) != 1:
        return None, '本行未明确唯一专业代码，未从多个专业或新旧代码中代选。'
    major = _major(values, header)
    if not major: return None, '本行没有完整的专业代码及名称。'
    code, name, _, embedded = major
    method_col = header['entry_method'] if header['entry_method'] is not None else header['kind']
    method = _get(row, method_col)
    if (_exam_method(method) in {'single', 'recommendation'}
            or _excluded_row('；'.join(cell['text'] for cell in row if cell))):
        return None, '本行明确为单考、仅推免或不接收统考范围。'
    subjects, subject_refs, issue = _exam(row, header, unit, document, table, row_number)
    if issue: return None, issue
    fields = {'code': [header['code']] if header['code'] is not None else [header['major']],
              'name': [header['name']] if header['code'] is not None else [header['major']],
              'department': [header['department']], 'direction': [header['direction']],
              'admission_method': [method_col], 'notes': [header['note']]}
    refs = {field: [_reference(document, row[col], table, row_number, field)
                    for col in columns if col is not None and col < len(row) and row[col] and row[col]['text']]
            for field, columns in fields.items()}
    if embedded and not refs['direction']:
        refs['direction'] = [_reference(document, row[identity_col], table, row_number, 'direction')]
    refs['subjects'] = subject_refs
    groups = [{'exam_unit': slot, 'selection': 'one_of' if len(options) > 1 else 'required',
               'options': [{'code': item['code'], 'name': item['name'], 'scope': item['scope']} for item in options]}
              for slot in sorted({s['exam_unit'] for s in subjects})
              if (options := [s for s in subjects if s['exam_unit'] == slot])]
    identity = f"{unit['id']}|{year}|{document['sha256']}|{table}|{row_number}"
    return {'fact_id': 'catalog-fact-'+hashlib.sha256(identity.encode()).hexdigest()[:24],
        'unit_id': str(unit['id']), 'unit_name': unit['name'], 'year': year,
        'code': code, 'name': name, 'department': _get(row, header['department']) or None,
        'direction': _get(row, header['direction']) or embedded or None, 'study_mode': None,
        'admission_method': method or None, 'notes': _get(row, header['note']) or None,
        'subjects': subjects, 'exam_groups': groups, 'field_evidence': refs,
        'row_evidence': [_reference(document, cell, table, row_number, 'original_row') for cell in row if cell and cell['text']],
        'source_headers': header['headers'],
        'source_document_id': document['id'], 'source_sha256': document['sha256'],
        'source_locator': {'table_order': 'postorder', 'table': table, 'row': row_number, 'header_row': header_row},
        'missing_fields': ['study_mode', *([] if _exam_method(method) == 'unified' else ['admission_method'])],
        'matching_allowed': False, 'fact_status': 'pending_independent_audit',
        'notice': PUBLIC_NOTICE}, None


def parse_catalog_facts(raw, unit, year, document):
    """Return separately typed facts, never fall back to a fabricated study mode."""
    html, failure = _scope(raw, unit, year, document)
    if failure: return {'parser': PARSER, 'facts': [], 'gaps': [failure], 'matching_allowed': False}
    parser = _Cells(); parser.feed(html)
    context = _context_mode(html, year)
    facts, gaps, preferred = [], [], 0
    for table_number, table in enumerate(parser.tables, 1):
        candidates = [(i, h) for i, row in enumerate(table[:8]) if (h := _header([_get(row, col) for col in range(len(row))]))]
        if not candidates: continue
        header_index, header = max(candidates, key=lambda item: (len(item[1]['slots']), -item[0]))
        for row_number, row in enumerate(table[header_index+1:], header_index+2):
            values = [_get(row, col) for col in range(len(row))]
            if _header(values): continue
            if _modes(values, header, context)[0]: preferred += 1; continue
            fact, issue = _fact(row, header, unit, year, document, table_number, row_number, header_index+1)
            if fact: facts.append(fact)
            elif any(re.search(r'\d{6}|[①②③④]', value) for value in values):
                gaps.append({'table': table_number, 'row': row_number, 'reason': issue})
    return {'parser': PARSER, 'facts': facts, 'gaps': gaps, 'normal_program_rows_preferred': preferred,
            'matching_allowed': False, 'source_sha256': document['sha256']}


def _dom_reference(reference, snapshot, source_row):
    locator = reference.get('source_locator', {})
    if any(type(locator.get(key)) is not int or locator[key] < 1 for key in ('table', 'row', 'column', 'origin_row')):
        return False
    try:
        cell = snapshot['tables'][locator['table']-1][locator['row']-1][locator['column']-1]
        return (locator['row'] == source_row and cell is not None and cell['text'] == reference['quote']
                and cell['origin_row'] == locator['origin_row'] and cell['column'] == locator['column'])
    except (IndexError, KeyError, TypeError):
        return False


def _literal_exam_pairs(text, declared_slot=None):
    """An independent token walk consumes every source code and its whole intervening name."""
    text = re.split(r'复试科目[:：]|同等学力(?:加试|考生)', _compact(text), maxsplit=1)[0]
    codes = list(re.finditer(r'(?<!\d)([1-9]\d{2})(?!\d)', text))
    markers = list(re.finditer(r'[①②③④⑴⑵⑶⑷]|科目[一二三四][:：]|(?<!\d)[1-4][、.．](?=[1-9]\d{2})', text))
    result, slot = [], 0
    for index, code in enumerate(codes):
        if declared_slot is not None: slot = declared_slot
        elif markers:
            mark = next((m[0] for m in reversed(markers) if m.end() <= code.start()), '')
            if mark in '①②③④' and mark: slot = '①②③④'.index(mark)+1
            elif mark in '⑴⑵⑶⑷' and mark: slot = '⑴⑵⑶⑷'.index(mark)+1
            elif mark.startswith('科目'): slot = '一二三四'.index(mark[2])+1
            elif mark: slot = int(mark[0])
            else: return []
        elif index == 0 or not re.search(r'(?:或者|或|/|、)$', text[codes[index-1].end():code.start()]): slot += 1
        end = codes[index+1].start() if index+1 < len(codes) else len(text)
        end = min([end, *[marker.start() for marker in markers if marker.start() >= code.end()]])
        name = text[code.end():end]
        name = re.sub(r'(?:[①②③④⑴⑵⑶⑷]|科目[一二三四][:：]|[1-4][、.．])$', '', name)
        name = re.sub(r'(?:或者|或|/|、)$', '', name).strip('；;、，,。-－—:：|')
        result.append((slot, code[1], name))
    return result


def _literal_identity(code_quote, name_quote, combined):
    """Consume the complete source identity, including any embedded directions."""
    token = r'(?:[0-9A-Z]{6}|(?:0[1-9]|1[0-4])[0-9]{2})'
    clean_name = lambda value: re.sub(r'\((?:学术学位|专业学位)\)', '', value).strip('★☆ ')
    if not combined:
        code = _compact(code_quote)
        return (code, clean_name(_compact(name_quote)), '') if re.fullmatch(token, code) else None
    value = _compact(code_quote)
    matches = list(re.finditer(r'(?<![0-9A-Z])'+token+r'(?![0-9A-Z])', value))
    if len(matches) != 1: return None
    match = matches[0]; before, after = value[:match.start()].strip('★☆ '), value[match.end():]
    if before.endswith('(') and after.startswith(')'): before, after = before[:-1], after[1:]
    if before and after and not re.match(r'(?:[0-9A-Z]{2}|[1-9][.、．])(?=[\u4e00-\u9fff])', after): return None
    name, directions = (before, after) if before else (after, '')
    embedded = re.search(r'(?<![0-9A-Z])[0-9A-Z]{2}(?=[.、．:]?[\u4e00-\u9fff])', name)
    if embedded: name, directions = name[:embedded.start()], name[embedded.start():]
    return match[0], clean_name(name), directions


def _audit_fields(fact, snapshot):
    loc = fact['source_locator']; matrix = snapshot['tables'][loc['table']-1]
    headers = [cell['text'] if cell else '' for cell in matrix[loc['header_row']-1]]
    header = _header(headers)
    if not header or loc['header_row'] >= loc['row']: return False
    row = matrix[loc['row']-1]
    method_col = header['entry_method'] if header['entry_method'] is not None else header['kind']
    code_col = header['code'] if header['code'] is not None else header['major']
    name_col = header['name'] if header['code'] is not None else header['major']
    identity = _literal_identity(_get(row, code_col), _get(row, name_col), code_col == name_col)
    if not identity or (fact.get('code'), fact.get('name')) != identity[:2]: return False
    columns = {'code': code_col, 'name': name_col, 'department': header['department'],
        'direction': header['direction'], 'admission_method': method_col, 'notes': header['note']}
    expected = {field: _get(row, col) or None for field, col in columns.items() if field not in {'code', 'name'}}
    if not expected['direction'] and identity[2]:
        expected['direction'], columns['direction'] = identity[2], code_col
    if any(fact.get(field) != value for field, value in expected.items()): return False
    for field, col in columns.items():
        refs = fact['field_evidence'].get(field, [])
        present = col is not None and bool(_get(row, col))
        if len(refs) != int(present): return False
        if present and (refs[0].get('field') != field or refs[0]['source_locator']['column'] != col+1): return False
    text = '；'.join(cell['text'] for cell in row if cell)
    expected_missing = ['study_mode', *([] if _exam_method(expected['admission_method']) == 'unified' else ['admission_method'])]
    return (not _excluded_row(text) and not LINKED_EXAMS.search(_compact(text))
        and fact.get('missing_fields') == expected_missing and fact.get('unit_name') is not None)


def _audit_exam(fact, snapshot):
    locator = fact['source_locator']
    headers = snapshot['tables'][locator['table']-1][locator['header_row']-1]
    if [_compact(cell['text']) if cell else '' for cell in headers] != [_compact(value) for value in fact.get('source_headers', [])]:
        return False
    expected = []
    for ref in fact['field_evidence'].get('subjects', []):
        column = ref['source_locator']['column']-1
        label = _compact(headers[column]['text'])
        match = re.fullmatch(r'(?:初试科目|考试科目)([一二三四1234])|业务课[（(]?([一二12])[）)]?', label)
        slot = None
        if match:
            value = match[1] or match[2]
            slot = ('一二三四'.index(value)+1 if value in '一二三四' else int(value)) + (2 if match[2] else 0)
        expected.extend(_literal_exam_pairs(ref['quote'], slot))
    actual = [(s.get('exam_unit'), s.get('code'), _compact(s.get('name'))) for s in fact.get('subjects', [])]
    if not expected or Counter(expected) != Counter(actual): return False
    for group in fact.get('exam_groups', []):
        slot = group.get('exam_unit')
        selected = [s for s in fact['subjects'] if s['exam_unit'] == slot]
        options = [{'code':s['code'], 'name':s['name'], 'scope':s['scope']} for s in selected]
        if group.get('options') != options or group.get('selection') != ('one_of' if len(selected) > 1 else 'required'):
            return False
    return {g.get('exam_unit') for g in fact.get('exam_groups', [])} == {p[0] for p in expected}


def _audit_one(fact, unit, year, document, snapshot):
    row, table = fact['source_locator']['row'], fact['source_locator']['table']
    if any(type(value) is not int or value < 1 for value in (row, table, fact['source_locator']['header_row'])): return False
    refs = [ref for values in fact['field_evidence'].values() for ref in values] + fact['row_evidence']
    refs += [subject['evidence'] for subject in fact['subjects']]
    if (fact.get('unit_id') != str(unit['id']) or fact.get('unit_name') != unit['name']
            or fact.get('year') != year or fact.get('matching_allowed') is not False
            or fact.get('study_mode') is not None or 'study_mode' not in fact.get('missing_fields', [])
            or fact.get('source_document_id') != document['id'] or fact.get('source_sha256') != document['sha256'] or not refs
            or not all(ref.get('url') == document['url'] and ref.get('document_id') == document['id']
                and ref['source_locator'].get('table') == table and _dom_reference(ref, snapshot, row) for ref in refs)):
        return False
    identity = f"{unit['id']}|{year}|{document['sha256']}|{table}|{row}"
    if fact.get('fact_id') != 'catalog-fact-'+hashlib.sha256(identity.encode()).hexdigest()[:24]: return False
    if not _audit_fields(fact, snapshot) or not _audit_exam(fact, snapshot): return False
    return all(subject.get('scope') == ('national' if _national(subject.get('code'), subject.get('name')) else str(unit['id']))
               for subject in fact['subjects'])


def audit_catalog_facts(raw, unit, year, document, facts, snapshot):
    """Audit against a separately produced native-browser DOM matrix, not this parser's rows."""
    if not isinstance(facts, list) or not facts or any(not isinstance(fact, dict) for fact in facts) or not isinstance(snapshot, dict):
        return {'verified': False, 'reason': '没有可独立核对的目录事实行；不能据此认定该校没有专业。'}
    _, failure = _scope(raw, unit, year, document)
    if failure or snapshot.get('source_sha256') != document['sha256'] or snapshot.get('auditor') != 'browser_dom_v1':
        return {'verified': False, 'reason': failure or '独立DOM快照与实际原文摘要不一致。'}
    errors = []
    for fact in facts:
        try: valid = _audit_one(fact, unit, year, document, snapshot)
        except (AttributeError, KeyError, TypeError, IndexError, ValueError): valid = False
        if not valid: errors.append(fact.get('fact_id'))
    return {'verified': not errors, 'fact_count': len(facts), 'invalid_fact_ids': list(dict.fromkeys(errors)),
            'method': 'independent_browser_dom_cells_and_literal_subject_pairs'}
