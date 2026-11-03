"""Read explicit initial-exam columns in public school catalog tables."""
import re
from copy import deepcopy
from html.parser import HTMLParser

from .catalogs import NATIONAL, _program, compact, evidence
from .history import _Tables
from service_verification import plain_text
from public_sources import rendered_source_html


PARSER = 'html_table_v1'
_CODE = re.compile(r'(?<![0-9A-Z])([0-9A-Z]{6}|(?:0[1-9]|1[0-4])[0-9]{2})(?![0-9A-Z])')


class _VisibleCatalog(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
    INERT = {'script', 'style', 'template', 'textarea', 'noscript'}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack, self.parts = [], []

    def hidden(self):
        return bool(self.stack and self.stack[-1][1])

    def _hidden(self, tag, attrs):
        values = {key.lower(): value for key, value in attrs}
        style = re.sub(r'\s+', '', values.get('style', '') or '').lower()
        return (self.hidden() or tag in self.INERT or 'hidden' in values
                or str(values.get('aria-hidden', '')).lower() == 'true'
                or values.get('role') == 'tooltip'
                or bool(re.search(r'(?:^|;)(?:display:none|visibility:hidden)(?:!important)?(?:;|$)', style)))

    def handle_starttag(self, tag, attrs):
        hidden = self._hidden(tag, attrs)
        if not hidden: self.parts.append(self.get_starttag_text())
        if tag not in self.VOID: self.stack.append((tag, hidden))

    def handle_startendtag(self, tag, attrs):
        if not self._hidden(tag, attrs): self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        hidden = self.hidden()
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if index is not None: self.stack = self.stack[:index]
        if not hidden: self.parts.append('</' + tag + '>')

    def handle_data(self, data):
        if not self.hidden(): self.parts.append(data)

    def handle_entityref(self, name):
        if not self.hidden(): self.parts.append('&' + name + ';')

    def handle_charref(self, name):
        if not self.hidden(): self.parts.append('&#' + name + ';')


def visible_catalog_html(text):
    parser = _VisibleCatalog()
    parser.feed(text)
    return ''.join(parser.parts)


def _clean(value):
    return re.sub(r'\s+', '', value or '')


def _find(headers, expressions):
    return next((index for index, text in enumerate(headers) if any(re.search(expr, text) for expr in expressions)), None)


def _header(row):
    cells = [_clean(value) for value in row]
    exam = _find(cells, (r'^初试(?:考试)?科目(?:及代码|代码|（代码）)?$', r'^考试科目$', r'^考试科目或组织形式$',
                         r'^初试考试代码及科目$', r'^考试科目代码及名称$', r'^考试科目[（(]加粗的为我校自命题科目[）)]$'))
    slots = {}
    for index, text in enumerate(cells):
        match = re.fullmatch(r'(?:初试科目|考试科目)([一二三四1234])', text)
        if match:
            slots['一二三四'.find(match[1]) + 1 if match[1] in '一二三四' else int(match[1])] = index
        if re.fullmatch(r'业务课[（(]一[）)]', text): slots[3] = index
        if re.fullmatch(r'业务课[（(]二[）)]', text): slots[4] = index
    major = _find(cells, (r'^专业$', r'代码与名称', r'^专业代码、名称及研究方向$', r'^专业代码及名称$', r'^专业名称及代码$', r'^招生专业[（(]领域[）)]$', r'^专业名称[（(]代码[）)]、研究方向$', r'^专业学位领域名称及代码$', r'^招生专业$', r'^专业代码、名称$', r'^学科、专业名称[（(]代码[）)]研究方向$', r'^学科、专业代码和名称及研究方向$', r'^学科专业及代码$', r'^二级学科名称$', r'^招生学科专业代码名称$', r'^招生专业及研究方向$', r'^院系所、专业、研究方向[:：]?$', r'^学院、专业、研究方向$'))
    if major is None:
        major = _find(cells, (r'^招生专业/领域[（(]标★的为学硕[）)]$', r'^专业名称[（(]含代码[）)]$', r'^招生领域$'))
    code = _find(cells, (r'^专业代码$',))
    name = _find(cells, (r'^专业名称$', r'^专业$'))
    if exam is None and set(slots) not in ({1, 2, 3, 4}, {1, 2, 3}, {1, 2}): return None
    if major is None and (code is None or name is None): return None
    return {'exam': exam, 'slots': slots, 'major': major, 'code': code, 'name': name,
            'department': _find(cells, (r'^院系所$', r'^学院$', r'^培养学院$', r'^二级学院名称$', r'^招生学院$', r'^学院代码及名称$', r'^院系代码名称$', r'^招生单位$', r'^学院及联系电话$', r'^学院代码和名称$', r'^系所代码及名称$', r'^招生院所$')),
            'direction': _find(cells, (r'^(?:招生)?研究方向(?:名称)?$', r'^研究方向[（(]学习方式[）)]$', r'^研究方向代码及名称$', r'^领域方向$')),
            'mode': _find(cells, (r'^学习方式$', r'^学习形式$')),
            'kind': _find(cells, (r'^考试类型$',)),
            'entry_method': _find(cells, (r'^(?:招生)?考试方式(?:代码)?$',)),
            'degree': _find(cells, (r'^学位类别$', r'^学位类型$', r'^类型$', r'^专业类型$', r'^招生类型$')),
            'note': _find(cells, (r'^备注$', r'^报考条件$')),
            'department_code': _find(cells, (r'^院系码$', r'^院系所代码$', r'^学院代码$')),
            'direction_code': _find(cells, (r'^研究方向码$', r'^方向代码$')),
            'headers': cells, 'mode_plans': {index: value for index, value in enumerate(cells) if value in {'全日制', '非全日制'}}}


def _directions(text):
    value = _clean(text).strip('；;。')
    if not value: return []
    marks = list(re.finditer(r'(?<![0-9A-Z])(?:[0-9A-Z]{2})(?![0-9A-Z])(?=[.、．:]?[\u4e00-\u9fff(（])', value))
    if not marks:
        marks = list(re.finditer(r'(?<!\d)[1-9][.、．](?=[\u4e00-\u9fff])', value))
    if not marks:
        marks = list(re.finditer(r'(?<![0-9A-Z])[1-9](?=[\u4e00-\u9fff])', value))
    if not marks: return [value]
    if value[:marks[0].start()].strip('；;、，,'): return []
    return [value[mark.start():marks[index + 1].start() if index + 1 < len(marks) else len(value)].strip('；;、，,。')
            for index, mark in enumerate(marks)]


def _exam_method(value):
    value = _clean(value)
    if re.fullmatch(r'(?:21[、:：-]?)?(?:全国统考|全国统一考试|统考)', value) or value == '21': return 'unified'
    if re.fullmatch(r'(?:23[、:：-]?)?单独考试', value) or value == '23': return 'single'
    if re.fullmatch(r'(?:22[、:：-]?)?(?:推荐免试|推免)', value): return 'recommendation'
    return 'unknown'


def _single_exam(value):
    return bool(re.search(r'单独考试|强军单考', value))


def _subject_exam_scope(subjects):
    flags = [(subject['exam_unit'], bool(re.search(r'^(?:单独考试|强军单考)|[（(](?:单独考试|强军单考)[）)]', _clean(subject['name'])))) for subject in subjects]
    if any({flag for unit, flag in flags if unit == slot} == {False, True} for slot, _ in flags): return 'mixed'
    return 'single' if any(flag for _, flag in flags) else 'unified'


def _national(code, name):
    alias = compact(name).strip('；;。')
    alias = re.sub(r'^(英语|数学)([一二三])$', r'\1(\2)', alias)
    return code in NATIONAL and alias in NATIONAL[code]


def _unmarked_sections(text):
    codes = list(re.finditer(r'(?<![0-9A-Z])([1-9]\d{2})(?!\d)', text))
    if not codes or codes[0][1] not in {'101', '111', '199'}: return []
    prefix = text[:codes[0].start()].strip('：:')
    if prefix not in {'', '初试科目', '考试科目'}: return []
    starts = [codes[0].start()]
    for previous, current in zip(codes, codes[1:]):
        between = text[previous.end():current.start()]
        if not re.search(r'(?:或者|或|/|、)$', between): starts.append(current.start())
    if len(starts) not in {2, 3, 4}: return []
    return [(index + 1, text[start:starts[index + 1] if index + 1 < len(starts) else len(text)], text)
            for index, start in enumerate(starts)]


def _subjects(raw, unit_id, document, slots=None):
    issue, subjects, quotes = [], [], []
    if slots is None:
        text = _clean(raw)
        text = re.split(r'复试科目[:：]|同等学力(?:加试|考生)', text, maxsplit=1)[0]
        marks = list(re.finditer(r'[①②③④⑴⑵⑶⑷]|(?<!\d)[1-4][、.．](?=[1-9]\d{2})|科目[一二三四][:：]', text))
        slot_of = lambda value: ('①②③④'.index(value) + 1 if value in '①②③④' else
                                 '⑴⑵⑶⑷'.index(value) + 1 if value in '⑴⑵⑶⑷' else
                                 '一二三四'.index(value[2]) + 1 if value.startswith('科目') else int(value[0]))
        sections = [(slot_of(mark[0]),
                     text[mark.end():marks[index + 1].start() if index + 1 < len(marks) else len(text)], text)
                    for index, mark in enumerate(marks)]
        if not sections: sections = _unmarked_sections(text)
    else:
        sections = [(slot, _clean(text), _clean(text)) for slot, text in slots.items()]
    for slot, section, quote in sections:
        alternatives = re.split(r'(?:或者|或|/|、)(?=\d{3})', section.strip('；;、，,。'))
        if len(alternatives) == 1 and re.fullmatch(r'[-—－/]*(?:无|不考)?', alternatives[0]): continue
        quotes.append(evidence(document, quote))
        for alternative in alternatives:
            match = re.fullmatch(r'([1-9]\d{2})[-－—:：|]?(.+)', alternative.strip('；;、，,。'))
            if not match:
                issue.append(f'第{slot}考试单元无法解析'); continue
            code, name = match.groups()
            name = name.strip('；;、，,。')
            item = {'code': code, 'name': name, 'scope': 'national' if _national(code, name) else str(unit_id),
                    'exam_unit': slot, 'evidence': evidence(document, quote)}
            if len(alternatives) > 1: item['choice_group'] = f'exam-unit-{slot}'
            subjects.append(item)
    units = {subject['exam_unit'] for subject in subjects}
    if len(units) not in {2, 3, 4} or units != set(range(1, len(units) + 1)):
        issue.append('初试单元缺失或顺序不连续')
    if len({(s['exam_unit'], s['code']) for s in subjects}) != len(subjects): issue.append('考试单元存在重复科目')
    return subjects, list({(r['quote'], r.get('page')): r for r in quotes}.values()), issue


def _major(row, header):
    get = lambda index: row[index] if index is not None and index < len(row) else ''
    if header['code'] is not None and _CODE.fullmatch(_clean(get(header['code']))):
        code, value = _clean(get(header['code'])), get(header['name']) or get(header['major'])
        name, embedded = value, ''
    else:
        value = get(header['major'])
        match = _CODE.search(value)
        if not match: return None
        code = match[1]
        before, after = value[:match.start()].strip('★☆ '), value[match.end():].strip()
        if before.endswith(('（', '(')):
            before = before[:-1]
            after = after[1:] if after.startswith(('）', ')')) else after
        name = after or before
        if before and re.match(r'(?:[0-9A-Z]{2}|[1-9][.、．])(?=[\u4e00-\u9fff])', after):
            return code, before, None, after
        embedded_match = re.search(r'(?<![0-9A-Z])[0-9A-Z]{2}(?=[.、．:]?[\u4e00-\u9fff])', name)
        embedded = name[embedded_match.start():] if embedded_match else ''
        if embedded_match: name = name[:embedded_match.start()]
    degree = '学术学位' if '学术学位' in name else '专业学位' if '专业学位' in name else None
    name = re.sub(r'[（(](?:学术学位|专业学位)[）)]', '', name).strip('★☆ ')
    if len(code) == 4 and re.match(r'(?:不设|不区分)(?:研究)?方向', name): return None
    return (code, name, degree, embedded) if name else None


def _context_mode(text, year):
    value = _clean(plain_text(text))
    patterns = (
        r'(?:我院|我所)招收的(?:硕士研究生|研究生)(?:培养方式是|为)全日制[^。]{0,60}。',
        r'(?:中监所|我所|我校|本所|本校)硕士研究生学习方式均为全日制[^。]{0,50}。',
        r'录取研究生学习方式为全日制[^。]{0,70}。',
        r'研究生学习方式为全日制。',
        r'学习形式为全日制。',
        r'我所研究生录取类别为非定向培养研究生，学习形式为全日制[^。]{0,100}。',
        r'[^。]{0,15}招收的是国家计划内全日制[^。]{0,50}硕士[^。]{0,60}。',
        str(year) + r'年[（(]?全日制[）)]?(?:学术学位|专业学位)?硕士研究生(?:拟)?招生专业目录',
        str(year) + r'年硕士研究生[（(]全日制[）)]招生专业目录',
        r'(?:我所|我校|本所|本校)(?:' + str(year) + r'年)?(?:招收的)?硕士研究生(?:为|均为|全部为)全日制[^。；]{0,30}[。；]',
        r'(?:我所|我校|本所|本校)' + str(year) + r'年招收的硕士研究生为全日制[^。；]{0,30}[。；]',
        str(year) + r'年我校[^。；]{0,45}计划招收全日制硕士研究生[^。；]{0,60}[。；]',
        r'(?:我所|我校|本所|本校)[^。；]{0,20}(?:只|仅|全部|均)招收全日制[^。；]{0,30}硕士研究生[^。；]{0,30}[。；]',
    )
    for pattern in patterns:
        match = re.search(pattern, value)
        if match and '非全日制' not in match[0]: return match[0]
    if '非全日制' not in value:
        general = r'(?:学习方式|学习形式|培养方式|培养形式)(?:均为|全部为|为|是|[:：])全日制|招生(?:类别|类型)为[“"]?(?:国家计划内|普通计划|非定向)?全日制|(?:硕士研究生|硕士生)(?:均为|全部为)(?:国家计划内|普通计划)?全日制|(?:我院|我所|我校)培养的(?:硕士研究生|研究生)为全日制'
        for match in re.finditer(general, value):
            prefix = re.split(r'[。；]', value[:match.start()])[-1][-60:]
            if re.search(r'学术学位|专业学位|报考|毕业生|文职|部分|其中', prefix) and not ('学术学位' in prefix and '专业学位' in prefix): continue
            return match[0]
    return ''


def _modes(row, header, context_mode):
    column = header.get('mode')
    value = row[column] if column is not None and column < len(row) else ''
    modes = list(dict.fromkeys(re.findall(r'非全日制|(?<!非)全日制', value)))
    quotes = [value] if modes else []
    if not modes and header['mode_plans']:
        for index, mode in header['mode_plans'].items():
            value = row[index] if index < len(row) else ''
            if re.fullmatch(r'\d+', value) and int(value) > 0:
                modes.append(mode); quotes.append(mode)
        modes = list(dict.fromkeys(modes))
    if not modes and header.get('direction') is not None:
        value = row[header['direction']] if header['direction'] < len(row) else ''
        modes = list(dict.fromkeys(re.findall(r'非全日制|(?<!非)全日制', value)))
        if modes: quotes.append(value)
    if not modes and header.get('direction') is None and header.get('major') is not None:
        value = row[header['major']] if header['major'] < len(row) else ''
        modes = list(dict.fromkeys(re.findall(r'非全日制|(?<!非)全日制', value)))
        if modes: quotes.append(value)
    if not modes and context_mode: return ['全日制'], [context_mode]
    return modes, quotes


def _direction_exam(text, direction):
    marks = list(re.finditer(r'(?<!\d)((?:[0-9A-Z]{2}[（(](?:全日制|非全日制)[）)](?:[、,，](?=[0-9A-Z]{2}))?)+)方向[:：]|[（(](?:方向)?(\d{2})(?:[-—－~～至](\d{2}))?(?:方向)?[）)]', text))
    if not marks: return text, []
    code = re.match(r'[0-9A-Z]{2}', direction)
    selected = []
    for index, mark in enumerate(marks):
        start, end = mark[2], mark[3]
        listed = re.findall(r'([0-9A-Z]{2})[（(]', mark[1]) if mark[1] else []
        matches = code and (code[0] in listed if listed else code[0] == start if not end else code[0].isdigit() and int(start) <= int(code[0]) <= int(end))
        if matches: selected.append(text[mark.start():marks[index + 1].start() if index + 1 < len(marks) else len(text)])
    return (selected[0], []) if len(selected) == 1 else ('', ['研究方向对应的初试分段无法唯一确定'])


def _row_records(row, header, unit, year, document, table_index, row_index, *, inherited=None, context_mode=''):
    get = lambda key: row[header[key]] if header.get(key) is not None and header[key] < len(row) else ''
    own_major = _major(row, header)
    major = own_major or (inherited or {}).get('major')
    if not major: return [], 1, ['专业代码或名称缺失'], []
    code, name, degree, embedded = major
    if get('degree') in {'学术学位', '专业学位'}: degree = get('degree')
    if header['direction'] is not None:
        direction_text = get('direction')
    elif own_major:
        direction_text = embedded
    else:
        direction_text = get('major')
    direction_number = get('direction_code')
    if direction_number and not direction_text.startswith(direction_number): direction_text = direction_number + direction_text
    directions = _directions(direction_text)
    program_level = not direction_text and own_major is not None
    if program_level: directions = ['']
    modes, mode_quotes = _modes(row, header, context_mode)
    direction_modes = [(re.sub(r'[（(](?:非全日制|全日制)[）)]', '', direction),
                        list(dict.fromkeys(re.findall(r'非全日制|(?<!非)全日制', direction))) or modes) for direction in directions]
    expected = sum(max(1, len(values)) for _, values in direction_modes) if direction_modes else max(1, len(modes))
    issues = []
    if not directions: issues.append('原文研究方向无法完整解析')
    if not modes: issues.append('原文未明确学习方式')
    method = get('entry_method') if header.get('entry_method') is not None else get('kind')
    if (header.get('entry_method') is not None or header.get('kind') is not None and method != '初试') and _exam_method(method) != 'unified':
        issues.append('考试方式未明确或混合，不能确认统考报考范围')
    if any(_single_exam(direction) for direction in directions):
        issues.append('同一单元格混列统考与单考方向，需逐项核对')
    department = get('department') or (inherited or {}).get('department', '')
    department_number = get('department_code')
    if department_number and not department.startswith(department_number): department = department_number + department
    if header['department'] is not None and header['headers'][header['department']] == '招生单位':
        named_unit = re.sub(r'^\d{5}', '', department)
        if compact(named_unit) == compact(unit['name']): department = ''
    department = re.split(r'\d{3,4}[-—]\d{5,}|[（(](?:Tel|电话)[:：]', department, maxsplit=1, flags=re.I)[0].rstrip('（(')
    raw_exam = get('exam')
    same_above = bool(re.fullmatch(r'同上(?:科目)?[。；]?', raw_exam))
    resolved_exam = (inherited or {}).get('last_exam', '') if same_above and (inherited or {}).get('last_exam_major') == code else ''
    separate = {slot: row[index] for slot, index in header['slots'].items() if index < len(row)} if header['exam'] is None else None
    if issues: return [], expected, issues, []
    source_cells = list(dict.fromkeys(value for value in [get('department'), get('department_code'), get('major'), get('code'), get('name'),
                                                        get('direction'), get('direction_code'), get('degree'), method, *mode_quotes, (inherited or {}).get('major_quote'), (inherited or {}).get('department_quote')] if value))
    records, excluded = [], []
    for direction, values in direction_modes:
        selected, selection_issues = _direction_exam(resolved_exam if same_above else raw_exam, direction) if separate is None else (raw_exam, [])
        subjects, exam_evidence, subject_issues = _subjects(selected, unit['id'], document, separate)
        if same_above: exam_evidence = [evidence(document, raw_exam), *exam_evidence]
        if selection_issues or subject_issues:
            issues.extend([*selection_issues, *subject_issues]); continue
        scope = _subject_exam_scope(subjects)
        if scope == 'mixed':
            issues.append('初试备选混合统考与单考科目，未明确适用人群'); continue
        if scope == 'single':
            expected -= len(values)
            excluded.extend({'reason': '初试科目明确单独考试或强军单考', 'evidence': exam_evidence,
                'source_locator': {'table': table_index, 'row': row_index}, 'direction': direction, 'study_mode': mode} for mode in values)
            continue
        for mode in values:
            program = _program(unit, year, department, code, name, direction, mode, subjects,
                               get('note'), document, degree=degree)
            program['department'] = re.sub(r'^\d{3}[-—]?', '', department)
            short_department = re.match(r'^(\d{2})(?!\d)(?=[\u4e00-\u9fff])', department)
            if short_department:
                published = short_department[1]
                program = {**program, 'department_code': published, 'department': department[2:],
                           'id': ':'.join([str(unit['id']), str(year), published, code,
                               (re.match(r'[0-9A-Z]{2}', direction)[0] if re.match(r'[0-9A-Z]{2}', direction) else direction), mode])}
            published_code = re.match(r'^(\d{4})(?=[^0-9A-Z])', direction)
            if published_code:
                value = published_code[1]
                identity = program['id'].rsplit(':', 2)
                program['id'] = ':'.join([identity[0], value, identity[2]])
                program['direction_code'] = value
                note = f'官方原文方向编号为{value}，保留原样；非常见两位编号，报名前需向学院核对。'
                program['notes'] = [*program['notes'], note]
                program['unparsed_condition_notes'] = [*program['unparsed_condition_notes'], note]
                issues.append('原文方向编号非常规，需核对，未改写或截断')
            if not department: program['department_scope'] = 'unit'
            if program_level: program['direction_scope'] = 'program'
            if len(code) == 4: program['code_level'] = 'discipline'
            program['evidence'] = [evidence(document, value) for value in source_cells]
            program['exam_evidence'] = exam_evidence
            program['catalog_parser'] = PARSER
            program['source_locator'] = {'table': table_index, 'row': row_index}
            records.append(program)
    return records, expected, issues, excluded


def _major_heading(row, header):
    if header['direction'] is not None or header['major'] is None or _major(row, header) is None: return False
    column = header['exam']
    value = row[column] if column is not None and column < len(row) else ''
    major_text = row[header['major']]
    if value and value != major_text: return False
    return all(not cell or cell == major_text or re.fullmatch(r'\d+(?:人)?', cell) for cell in row)


def _code_header(row):
    return [_clean(cell) for cell in row[:5]] == ['编码', '名称', '拟招人数', '考试科目', '备注']


def _code_program(row, context, unit, year, document, table_index, row_index):
    subjects, exam_refs, issues = _subjects(row[3], unit['id'], document)
    if not context.get('department_code') or not context.get('major_code') or not context.get('modes'):
        issues.append('方向缺少可核验的前页院系、专业或学习方式上下文')
    if issues: return [], issues
    records = []
    for mode in context['modes']:
        program = _program(unit, year, context['department_code'] + context['department'],
                           context['major_code'], context['major_name'], row[0] + row[1], mode,
                           subjects, row[4], document)
        program['evidence'] = [*context['department_evidence'], *context['major_evidence'],
                               evidence(document, row[0]), evidence(document, row[1])]
        program['exam_evidence'] = exam_refs
        program['catalog_parser'] = PARSER
        program['catalog_document_id'] = document['id']
        program['source_locator'] = {'table': table_index, 'row': row_index}
        records.append(program)
    return records, []


def _parse_code_table(table, unit, year, document, table_index, context, has_next):
    state = deepcopy(context or {})
    programs, issues, expected, excluded, excluded_single, excluded_records = [], [], 0, 0, 0, []
    for row_index, row in enumerate(table[1:], 2):
        if _code_header(row) or len(set(cell for cell in row if cell)) <= 1: continue
        if len(row) < 5:
            issues.append(f'表{table_index}第{row_index}行：层级目录列缺失'); expected += 1; continue
        code, name = row[:2]
        if re.fullmatch(r'\d{3}', code):
            state = {'department_code': code, 'department': name,
                     'department_evidence': [evidence(document, code), evidence(document, name)]}
            continue
        if _CODE.fullmatch(code):
            if state.get('major_code') and not state.get('major_used'):
                issues.append(f'表{table_index}第{row_index}行：前一专业缺少可核验方向'); expected += 1
            modes = list(dict.fromkeys(re.findall(r'非全日制|(?<!非)全日制', name)))
            state = {**state, 'major_code': code, 'major_name': re.sub(r'[（(](?:非全日制|全日制)[）)]', '', name),
                     'modes': modes, 'major_evidence': [evidence(document, code), evidence(document, name)],
                     'major_used': False}
            continue
        if not re.fullmatch(r'[0-9A-Z]{2}', code):
            issues.append(f'表{table_index}第{row_index}行：未识别的层级目录身份'); expected += 1; continue
        state = {**state, 'major_used': True}
        if re.search(r'(?:仅|只)(?:接收|招收)推免生', row[4]):
            excluded += 1; continue
        if _single_exam(name) or _single_exam(state.get('major_name', '')):
            excluded_single += 1; continue
        subjects, refs, errors = _subjects(row[3], unit['id'], document)
        scope = _subject_exam_scope(subjects) if not errors else 'unified'
        if scope == 'single':
            excluded_single += max(1, len(state.get('modes', [])))
            excluded_records.append({'reason': '初试科目明确单独考试或强军单考', 'evidence': refs,
                                     'source_locator': {'table': table_index, 'row': row_index}})
            continue
        expected += max(1, len(state.get('modes', [])))
        if scope == 'mixed':
            issues.append(f'表{table_index}第{row_index}行：初试备选混合统考与单考科目，未明确适用人群'); continue
        records, errors = _code_program(row, state, unit, year, document, table_index, row_index)
        programs.extend(records)
        issues.extend(f'表{table_index}第{row_index}行：{error}' for error in errors)
    if state.get('major_code') and not state.get('major_used') and not has_next:
        issues.append(f'表{table_index}末尾专业缺少可核验方向'); expected += 1
    return {'programs': programs, 'issues': issues, 'expected_records': expected,
            'excluded_recommendation_only': excluded, 'excluded_non_unified_exam': excluded_single,
            'excluded_records': excluded_records, 'continuation_context': state}


def _paired_header(table):
    required = {'专业代码', '专业名称', '学习方式', '方向代码', '专业方向名称'}
    for row in table[:12]:
        if not required.issubset(row): continue
        if row.count('科目代码') == 4 and row.count('科目名称') == 4:
            pairs = list(zip([i for i, value in enumerate(row) if value == '科目代码'],
                             [i for i, value in enumerate(row) if value == '科目名称']))
            return {**{value: row.index(value) for value in required}, 'pairs': pairs,
                    'note': next((i for i, value in enumerate(row) if value in {'备注', '复试要求及相关说明'}), None)}
    return None


def _paired_subjects(row, pairs, unit_id, document, table_index, row_index):
    subjects, quotes, issues = [], [], []
    for slot, (code_col, name_col) in enumerate(pairs, 1):
        code, name = row[code_col], row[name_col]
        if (code in {'', '/', '无', '不考'} or re.fullmatch(r'[-—－]+', code)) and name in {'', '/', '无', '不考'}: continue
        codes = re.split(r'或|/|、', code)
        names = re.split(r'或|/|、', name) if len(codes) > 1 else [name]
        if len(codes) != len(names) or any(not re.fullmatch(r'[1-9]\d{2}', value) for value in codes):
            issues.append(f'第{slot}单元代码与名称备选无法逐项对应'); continue
        refs = [{**evidence(document, value), 'source_locator': {'table': table_index, 'row': row_index,
                 'column': col + 1}} for value, col in [(code, code_col), (name, name_col)]]
        quotes.extend(refs)
        for value, label in zip(codes, names):
            subject = {'code': value, 'name': label, 'scope': 'national' if _national(value, label) else unit_id,
                       'exam_unit': slot, 'evidence': refs}
            if len(codes) > 1: subject['choice_group'] = f'exam-unit-{slot}'
            subjects.append(subject)
    slots = {subject['exam_unit'] for subject in subjects}
    if len(slots) not in {2, 3, 4} or slots != set(range(1, len(slots) + 1)):
        issues.append('初试单元缺失或不连续')
    return subjects, quotes, issues


def _parse_paired_table(table, header, unit, year, document, table_index):
    programs, issues, expected, department, department_quote, excluded_records = [], [], 0, '', '', []
    for row_index, row in enumerate(table, 1):
        values = list(dict.fromkeys(cell for cell in row if cell))
        for value in values:
            if re.fullmatch(r'\d{3}[\u4e00-\u9fff].*(?:学院|学部|研究院|中心|校区|部|院)', value):
                department, department_quote = value, value
        code = row[header['专业代码']] if header['专业代码'] < len(row) else ''
        if code == '专业代码' or '招生专业目录' in ''.join(values) or '招生专业' in ''.join(values) and '目录' in ''.join(values): continue
        if not _CODE.fullmatch(code):
            if len(values) > 2: issues.append(f'表{table_index}第{row_index}行：配对目录专业身份未识别')
            continue
        expected += 1
        if max(index for pair in header['pairs'] for index in pair) >= len(row):
            issues.append(f'表{table_index}第{row_index}行：配对考试列缺失'); continue
        subjects, exam_refs, errors = _paired_subjects(row, header['pairs'], str(unit['id']), document, table_index, row_index)
        mode, direction_code = row[header['学习方式']], row[header['方向代码']]
        if mode not in {'全日制', '非全日制'}: errors.append('学习方式未明确')
        if not re.fullmatch(r'[0-9A-Z]{2}', direction_code): errors.append('方向代码未明确')
        if errors:
            issues.extend(f'表{table_index}第{row_index}行：{error}' for error in errors); continue
        scope = _subject_exam_scope(subjects)
        if scope == 'single':
            expected -= 1
            excluded_records.append({'reason': '初试科目明确单独考试或强军单考', 'evidence': exam_refs,
                                     'source_locator': {'table': table_index, 'row': row_index}})
            continue
        if scope == 'mixed':
            issues.append(f'表{table_index}第{row_index}行：初试备选混合统考与单考科目，未明确适用人群'); continue
        name, direction_name = row[header['专业名称']], row[header['专业方向名称']]
        note = row[header['note']] if header['note'] is not None else ''
        program = _program(unit, year, department, code, name, direction_code + direction_name, mode,
                           subjects, note, document)
        if not department: program['department_scope'] = 'unit'
        program['evidence'] = [evidence(document, value) for value in [department_quote, code, name, mode, direction_code, direction_name] if value]
        program['exam_evidence'] = exam_refs
        program['catalog_parser'] = PARSER
        program['catalog_document_id'] = document['id']
        program['source_locator'] = {'table': table_index, 'row': row_index}
        programs.append(program)
    return {'programs': programs, 'expected_records': expected, 'issues': issues,
            'excluded_non_unified_exam': len(excluded_records), 'excluded_records': excluded_records}


def catalog_pagination(text):
    anchors = re.findall(r'<a\b([^>]*lnkPage(\d+)[^>]*)>.*?</a>', text, flags=re.S | re.I)
    if not anchors: return {'current': 1, 'total': 1}
    current = [int(number) for attrs, number in anchors
               if 'aspNetDisabled' in attrs or not re.search(r'\bhref=', attrs)]
    return {'current': current[0] if len(set(current)) == 1 else None,
            'total': max(int(number) for _, number in anchors)}


def _department_heading(row):
    values = {_clean(cell) for cell in row if _clean(cell)}
    if len(row) < 2 or len(values) != 1: return None
    quote = next(iter(values))
    value = re.split(r'学院网站[、:：]|联系人[:：]|联系(?:方式|电话)[:：]|电话[:：]|邮箱[:：]', quote, maxsplit=1)[0]
    match = re.fullmatch(r'(?:院系所代码及名称[:：])?((?:\d{2,3})(?!\d)[\u4e00-\u9fff].*(?:学院|学部|研究院|研究所|中心))', value)
    return {'department': match[1], 'department_quote': quote} if match else None


def _unrecognized_initial_table(table):
    text = ''.join(''.join(row) for row in table)
    if not _CODE.search(text): return False
    return (bool(re.search(r'[①⑴]1\d{2}.+[②⑵]2\d{2}', text))
            or any(_unmarked_sections(_clean(cell)) for row in table for cell in row))


def parse_catalog_html(text, unit, year, document, context=None):
    """Only declared initial-exam columns are parsed; incomplete rows remain gaps."""
    text = visible_catalog_html(rendered_source_html(text, document.get('url', '')))
    parser = _Tables()
    parser.feed(re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>', '', text, flags=re.S | re.I))
    programs, issues, manifests, expected = [], [], [], 0
    context_mode = _context_mode(text, year)
    continuation_context, excluded, excluded_single, excluded_records = {}, 0, 0, []
    pagination = catalog_pagination(text)
    has_next = pagination['current'] is not None and pagination['current'] < pagination['total']
    if pagination['current'] is None: issues.append('目录分页的当前页无法核对')
    for table_index, table in enumerate(parser.tables, 1):
        paired = _paired_header(table)
        if paired:
            parsed = _parse_paired_table(table, paired, unit, year, document, table_index)
            programs.extend(parsed['programs']); issues.extend(parsed['issues']); expected += parsed['expected_records']
            excluded_single += parsed['excluded_non_unified_exam']; excluded_records.extend(parsed['excluded_records'])
            manifests.append({'table_index': table_index, 'expected_records': parsed['expected_records'], 'format': 'paired_code_name'})
            continue
        if table and _code_header(table[0]):
            parsed = _parse_code_table(table, unit, year, document, table_index, context, has_next)
            programs.extend(parsed['programs']); issues.extend(parsed['issues']); expected += parsed['expected_records']
            continuation_context = parsed['continuation_context']; excluded += parsed['excluded_recommendation_only']
            excluded_single += parsed['excluded_non_unified_exam']
            excluded_records.extend(parsed['excluded_records'])
            manifests.append({'table_index': table_index, 'headers': table[0], 'expected_records': parsed['expected_records']})
            continue
        header_at = next(((i, value) for i, row in enumerate(table[:5]) if (value := _header(row))), None)
        if header_at is None:
            headings = ''.join(''.join(row) for row in table[:5])
            if (re.search(r'专业|学科|研究方向', headings) and re.search(r'初试科目|考试科目', headings)
                    or _unrecognized_initial_table(table)):
                issues.append(f'表{table_index}疑似招生目录但表头未适配，不能确认完整')
            continue
        index, header = header_at
        source_rows, table_expected = 0, 0
        inherited = next((value for row in reversed(table[:index]) if (value := _department_heading(row))), {})
        heading_used = True
        catalog_section = True
        for row_index, row in enumerate(table[index + 1:], index + 2):
            if _header(row):
                catalog_section = True
                continue
            values = {cell for cell in row if cell}
            if len(values) == 1 and next(iter(values)) in {'参考书目及考试大纲', '考试大纲及参考书目'}:
                catalog_section = False
                continue
            if not catalog_section: continue
            department_heading = _department_heading(row)
            if department_heading:
                inherited = {**inherited, **department_heading}
                continue
            identity_columns = [column for column in [header.get('major'), header.get('name')] if column is not None]
            if any(column < len(row) and _single_exam(row[column]) for column in identity_columns):
                excluded_single += 1
                continue
            major_column = header.get('major')
            major_text = row[major_column] if major_column is not None and major_column < len(row) else ''
            compound_header = header['direction'] is None and major_column is not None and ('学院' in header['headers'][major_column] or '院系' in header['headers'][major_column] or header['headers'][major_column] == '专业代码、名称及研究方向')
            if compound_header and re.match(r'^\d{3}(?!\d)[\u4e00-\u9fff]', major_text):
                inherited = {'department': major_text, 'department_quote': major_text}
                continue
            following = table[row_index] if row_index < len(table) else []
            next_major = following[major_column] if major_column is not None and major_column < len(following) else ''
            own = _major(row, header)
            parent_with_children = header['direction'] is None and own and not own[3] and (re.match(r'^[0-9A-Z]{2}(?![0-9A-Z])(?=[（(.、．\u4e00-\u9fff])', next_major) or re.match(r'^[0-9A-Z]{4}(?:不设|不区分)(?:研究)?方向', next_major))
            if _major_heading(row, header) or parent_with_children:
                if not heading_used:
                    expected += 1; table_expected += 1
                    issues.append(f'表{table_index}专业标题后没有可核验的初试方向行')
                heading_used = False
                inherited = {**{key: value for key, value in inherited.items() if key not in {'last_exam', 'last_exam_major'}},
                             'major': _major(row, header), 'major_quote': row[header['major']]}
                continue
            if len(set(cell for cell in row if cell)) <= 1: continue
            if row and row[0] in {'合计', '总计'}: continue
            if header['kind'] is not None and header['kind'] < len(row) and row[header['kind']] in {'复试', '加试', '同等学力加试'}: continue
            own_major = _major(row, header)
            has_initial = (header['exam'] is not None and header['exam'] < len(row) and bool(row[header['exam']])) or any(row[index] for index in header['slots'].values() if index < len(row))
            if not own_major and not inherited and not has_initial: continue
            if not own_major and inherited and not has_initial and not _directions(major_text): continue
            heading_used = True
            note = row[header['note']] if header['note'] is not None and header['note'] < len(row) else ''
            direction_value = row[header['direction']] if header['direction'] is not None and header['direction'] < len(row) else ''
            method_col = header['entry_method'] if header['entry_method'] is not None else header['kind']
            method = _exam_method(row[method_col]) if method_col is not None and method_col < len(row) else None
            if method == 'single' or len(_directions(direction_value)) == 1 and _single_exam(direction_value):
                excluded_single += 1
                continue
            only_recommendation_direction = len(_directions(direction_value)) == 1 and re.search(r'仅从推免生选拔|(?:仅|只)(?:接收|招收)(?:推免|推荐免试)生', direction_value)
            if method == 'recommendation' or only_recommendation_direction or re.search(r'(?:仅|只)(?:接收|招收)(?:推免|推荐免试)生|不(?:接收|招收)统考生', note):
                excluded += 1
                continue
            source_rows += 1
            records, row_expected, row_issues, row_excluded = _row_records(row, header, unit, year, document, table_index, row_index, inherited=inherited, context_mode=context_mode)
            excluded_single += len(row_excluded); excluded_records.extend(row_excluded)
            expected += row_expected; table_expected += row_expected
            programs.extend(records)
            if records and header['exam'] is not None and row[header['exam']] and not re.fullmatch(r'同上(?:科目)?[。；]?', row[header['exam']]):
                inherited = {**inherited, 'last_exam': row[header['exam']], 'last_exam_major': records[0]['code']}
            issues.extend(f'表{table_index}第{row_index}行：{issue}' for issue in row_issues)
        if not heading_used:
            expected += 1; table_expected += 1
            issues.append(f'表{table_index}专业标题后没有可核验的初试方向行')
        manifests.append({'table_index': table_index, 'headers': header['headers'],
                          'source_rows': source_rows, 'expected_records': table_expected})
    if not manifests: issues.append('未发现可核验的招生目录表头')
    if len({p['id'] for p in programs}) != len(programs): issues.append('专业方向身份重复，不能确认目录完整')
    return {'programs': programs, 'expected_records': expected, 'issues': issues, 'retest_rules': [],
            'table_manifests': manifests, 'catalog_format': PARSER, 'continuation_context': continuation_context,
            'excluded_recommendation_only': excluded, 'excluded_non_unified_exam': excluded_single,
            'excluded_records': excluded_records, 'pagination': pagination}
