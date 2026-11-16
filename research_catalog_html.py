"""Read explicit subject cells from current official HTML exam directories."""
import re

from collectors.catalog_facts import _Cells
from collectors.catalog_html import visible_catalog_html
from collectors.catalog_strict_syntax import parse_strict_directions, parse_strict_subjects
from collectors.catalogs import _program, compact, evidence
from collectors.history import _Tables
from public_sources import document_attribution, official_source_url
from service_verification import ordinary_catalog_scope, plain_text


_HEADED_COLUMNS = ('研究方向', '拟招生计划', '学习方式', '学制', '初试科目', '复试科目', '备注')
_LISTED_COLUMNS = ('专业代码、名称及研究方向', '拟招生人数', '考试科目', '复试笔试科目及备注')
_BROCHURE_COLUMNS = ('学科、专业名称（代码）研究方向', '考试科目', '同等学力加试科目', '专业科目参考书目')
_COLLEGE_COLUMNS = ('序号', '招生院系', '招生专业代码、名称', '研究方向', '拟招生人数', '招生院系联系方式',
                    '初试考试科目', '复试内容', '同等学力加试科目', '初试参考书目', '复试、加试参考书目')
_SPLIT_COLUMNS = ('专业代码', '专业名称', '拟招生人数', '初试科目', '复试科目')
_DEPARTMENT = re.compile(r'(?:\(([0-9]{3})\)|([0-9]{3}))([\u3400-\u9fff][^0-9]{1,55}(?:学院|学部|研究院|研究所|中心|系))')
_MAJOR_CODE = r'[0-9]{4}[A-Z0-9][0-9]'
_MAJOR = re.compile(rf'({_MAJOR_CODE})([\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z0-9·+－、/（）() -]{{0,80}})(?:\[(学术学位|专业学位)\])?')
_DIRECTION = re.compile(r'([0-9A-Z]{2})\((非全日制|全日制)\)(.+)')
_SPECIAL = re.compile(r'(?:仅|只)(?:招收|接收)(?:推免|推荐免试)|不(?:招收|接收)统考|停止招生|暂停招生|不招生|单独考试|强军|联合培养')
_PARAGRAPH = r'<p\b[^>]*>((?:(?!</?p\b).)*?)</p\s*>'


class _PhysicalTables(_Tables):
    def handle_starttag(self, tag, attrs):
        # Keep cells on the rows where they appear in the original.
        super().handle_starttag(tag, [(key, value) for key, value in attrs if key not in {'rowspan', 'colspan'}])


class _BrochureTable(_Tables):
    def __init__(self):
        super().__init__()
        self.carried_exam = False

    def handle_starttag(self, tag, attrs):
        if tag == 'tr' and self.stack and {0, 1} & self.stack[-1]['pending'].keys():
            self.carried_exam = True
        super().handle_starttag(tag, attrs)


class _SplitCatalogCells(_Cells):
    def _scope_span(self, tag):
        if tag in {'thead', 'tbody', 'tfoot'} and self.stack:
            self.stack[-1]['pending'] = {}

    def handle_starttag(self, tag, attrs):
        self._scope_span(tag)
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self._scope_span(tag)
        super().handle_endtag(tag)


def _catalog_title(html, unit, year):
    name = re.escape(compact(unit['name']))
    title = re.compile(rf'(?:{name}{year}年|{year}年{name})硕士(?:研究生)?(?:招生)?专业目录(?=$|[-—_|(])')
    headings = re.findall(r'<(?:title|h[1-6])\b[^>]*>(.*?)</(?:title|h[1-6])\s*>', html, re.I | re.S)[:50]
    return any(title.match(compact(plain_text(heading))) for heading in headings)


def _department(raw):
    entry = _DEPARTMENT.fullmatch(compact(raw))
    return (entry[1] or entry[2]) + entry[3] if entry else None


def _major(raw):
    entry = _MAJOR.fullmatch(raw)
    return entry if entry and not re.search(_MAJOR_CODE, entry[2]) and not _SPECIAL.search(raw) else None


def _ref(document, value, table, row, column):
    return {**evidence(document, value), 'table_index': table, 'row_index': row, 'column_index': column}


def _record(unit, year, document, table, department, heading, direction, mode, exams, *, degree=None, note=''):
    faculty, faculty_raw, faculty_no = department
    major, major_raw, major_no, major_column = heading
    label, direction_raw, direction_no = direction
    exam_refs = [_ref(document, raw, table, row, col) for row, col, raw in exams]
    parsed = (parse_strict_subjects(exams[0][2], str(unit['id']), document) if len(exams) == 1 else
              parse_strict_subjects('', str(unit['id']), document, {index: item[2] for index, item in enumerate(exams, 1)}))
    if parsed['exam_scope'] != 'unified' or parsed['issues'] or _SPECIAL.search(direction_raw + note):
        return None
    refs = [_ref(document, faculty_raw, table, faculty_no, 1), _ref(document, major_raw, table, major_no, major_column),
            _ref(document, direction_raw, table, direction_no, 1)]
    if major_column == 2:
        refs.append(_ref(document, mode, table, direction_no, 3))
    subjects = [{**item, 'evidence': exam_refs[item['exam_unit'] - 1 if len(exams) > 1 else 0]}
                for item in parsed['subjects']]
    row = _program(unit, year, faculty, major[1], major[2], label, mode, subjects, note, document, degree=major[3] or degree)
    return {**row, 'evidence': refs, 'exam_evidence': exam_refs,
            'source_locator': {'table': table, 'row': direction_no, 'major_row': major_no, 'department_row': faculty_no}}


def _headed_rows(rows, unit, year, document, table):
    department, major = None, None
    for index, row in enumerate(rows, 1):
        first = row[0]
        if len(row) == 1 and (faculty := _department(first)):
            department, major = (faculty, first, index), None
        elif len(row) == 2 and first.rstrip('：:') == '专业代码及名称':
            heading = _major(row[1])
            major = ((heading, row[1], index, 2) if heading and index < len(rows) and
                     tuple(rows[index]) == _HEADED_COLUMNS else None)
        elif tuple(row) == _HEADED_COLUMNS:
            continue
        elif department and major and re.match(r'[0-9A-Z]{2}(?![0-9A-Z])', first):
            if (len(row) != len(_HEADED_COLUMNS) or row[2] not in {'全日制', '非全日制'}
                    or re.findall('[①②③④]', row[4]) != list('①②③④')):
                continue
            directions = parse_strict_directions(first)['directions']
            if len(directions) == 1 and (record := _record(unit, year, document, table, department, major,
                    (directions[0]['label'], first, index), row[2], [(index, 5, row[4])], note=row[6])):
                yield record
        elif any(row):
            department, major = None, None


def _listed_rows(rows, unit, year, document, table):
    department, degree = None, None
    headings = {f'{year}年{value}类专业目录': value for value in ('学术学位', '专业学位')}
    for index, row in enumerate(rows):
        first = row[0]
        if len(row) == 1 and first.endswith('专业目录'):
            department = None
            degree = headings.get(first) if index + 1 < len(rows) and tuple(rows[index + 1]) == _LISTED_COLUMNS else None
        if not degree or len(row) != len(_LISTED_COLUMNS):
            continue
        if not any(row[1:]) and (faculty := _department(first)):
            department = faculty, first, index + 1
        elif re.match(r'(?:\([0-9]{3}\)|[0-9]{3}(?!\d))', compact(first)):
            department = None
        major = _major(first)
        if not department or not major or any(row[2:]):
            continue
        section = []
        end = min(len(rows), index + 8)
        for following in rows[index + 1:end]:
            if (re.match(_MAJOR_CODE, following[0]) or _department(following[0])
                    or len(following) == 1 and following[0].endswith('专业目录')):
                break
            section.append(following)
        else:
            if end < len(rows): continue
        while section and len(section[-1]) == 1 and not section[-1][0]:
            section.pop()
        if len(section) not in {2, 3, 4} or any(len(line) != 4 or line[1] for line in section):
            continue
        direction = _DIRECTION.fullmatch(compact(section[0][0]))
        if not direction or any(line[0] for line in section[1:]):
            continue
        if any(not line[2].startswith(mark) for mark, line in zip('①②③④', section)):
            continue
        exams = [(index + 2 + offset, 3, line[2]) for offset, line in enumerate(section)]
        if record := _record(unit, year, document, table, department, (major, first, index + 1, 1),
                (direction[1] + direction[3], section[0][0], index + 2), direction[2], exams, degree=degree,
                note='；'.join(line[3] for line in section if line[3])):
            yield record


def _brochure_table(html, match, expected_departments):
    before = html[max(0, match.start() - 4096):match.start()]
    heading = re.search(_PARAGRAPH + r'\s*(?:<div\b[^>]*>\s*)?$', before, re.I | re.S)
    if not heading or not re.fullmatch(r'(?:\([一二三四五六七八九十]+\))?招生专业及考试科目',
                                       compact(plain_text(heading[1]))):
        return None
    after = html[match.end():match.end() + 4096]
    owner = re.match(r'\s*(?:</div>\s*)?' + _PARAGRAPH, after, re.I | re.S)
    owner_text = compact(plain_text(owner[1])) if owner else ''
    faculty = re.fullmatch(r'招生单位名称[:：](.+)', owner_text)
    if not faculty or faculty[1] not in expected_departments:
        return None
    tables = _BrochureTable()
    tables.feed(match[0])
    if not tables.carried_exam and not tables.stack and len(tables.tables) == 1 and tables.tables[0]:
        rows = tables.tables[0]
        if tuple(rows[0]) == _BROCHURE_COLUMNS:
            return faculty[1], owner_text, rows[1:]
    return None


def _brochure_rows(html, unit, year, document):
    headings = re.findall(r'<h[1-6]\b[^>]*>(.*?)</h[1-6]\s*>', html, re.I | re.S)[:50]
    label = re.compile(r'(.{2,120}?)(20\d{2})年硕士研究生招生简章')
    subjects = {match.groups() for value in headings if (match := label.fullmatch(compact(plain_text(value))))}
    if len(subjects) != 1 or unit['name'] not in plain_text(html):
        return
    owners, printed_year = next(iter(subjects))
    if printed_year != str(year):
        return
    departments = owners.split('、')
    for index, match in enumerate(re.finditer(r'<table\b[^>]*>.*?</table\s*>', html, re.I | re.S), 1):
        layout = _brochure_table(html, match, departments)
        if not layout:
            continue
        faculty, owner, rows = layout
        for number, row in enumerate(rows, 2):
            if (len(row) != len(_BROCHURE_COLUMNS) or not (major := _major(row[0]))
                    or re.findall('[①②③④]', row[1]) != list('①②③④')):
                continue
            parsed = parse_strict_subjects(row[1], str(unit['id']), document)
            if parsed['exam_scope'] != 'unified' or parsed['issues']:
                continue
            course_ref = _ref(document, row[1], index, number, 2)
            courses = [{**item, 'evidence': course_ref} for item in parsed['subjects']]
            program = _program(unit, year, faculty, major[1], major[2], '', '', courses, '', document)
            yield {**program, 'study_mode': None, 'direction': None, 'direction_scope': 'program',
                   'evidence': [evidence(document, owner), _ref(document, row[0], index, number, 1)],
                   'exam_evidence': [course_ref], 'source_locator': {'table': index, 'row': number}}


def _college_heading(html, unit, year):
    university = re.escape(compact(unit['name']))
    heading = re.compile(rf'({university}([\u3400-\u9fff]{{2,55}}(?:学院|学部|研究院|研究所))'
                         rf'{year}年硕士研究生招生专业目录)(?:[-—_|]\2)?')
    values = re.findall(r'<(?:title|h[1-6])\b[^>]*>(.*?)</(?:title|h[1-6])\s*>', html, re.I | re.S)[:50]
    owners = {result.groups() for value in values if (result := heading.fullmatch(compact(plain_text(value))))}
    return next(iter(owners)) if len(owners) == 1 else None


def _college_rows(logical, physical, heading, unit, year, document, table):
    title, faculty = heading
    if (len(logical) != len(physical) or len(physical) < 3 or physical[0] != [title]
            or tuple(physical[1]) != _COLLEGE_COLUMNS or logical[1] != physical[1]):
        return
    department_row = None
    for row_no, (row, cells) in enumerate(zip(logical[2:], physical[2:]), 3):
        if len(row) != len(_COLLEGE_COLUMNS) or row[1] != faculty or not re.fullmatch('[0-9]{1,3}', row[0]):
            department_row = None
            continue
        if row == cells:
            department_row, carried = row_no, 0
        elif department_row and cells == [row[0], *row[2:]]:
            carried = 1
        else:
            department_row = None
            continue
        major, direction = _major(row[2]), parse_strict_directions(row[3])['directions']
        if (not major or len(direction) != 1 or not re.match(r'[0-9A-Z]{2}(?![0-9A-Z])', row[3])
                or _SPECIAL.search(row[3]) or re.findall('[①②③④]', row[6]) != list('①②③④')):
            continue
        exam = re.sub(r'[▲★](?=[①②③④])', '', row[6])
        parsed = parse_strict_subjects(exam, str(unit['id']), document)
        if parsed['exam_scope'] != 'unified' or parsed['issues']:
            continue
        exam_ref = _ref(document, row[6], table, row_no, 7 - carried)
        subjects = [{**item, 'evidence': exam_ref} for item in parsed['subjects']]
        label = direction[0]['label']
        record = _program(unit, year, faculty, major[1], major[2], label, '', subjects, '', document, degree=major[3])
        yield {**record, 'study_mode': None,
               'evidence': [_ref(document, faculty, table, department_row, 2),
                            _ref(document, row[2], table, row_no, 3 - carried),
                            _ref(document, row[3], table, row_no, 4 - carried)],
               'exam_evidence': [exam_ref],
               'source_locator': {'table': table, 'row': row_no, 'department_row': department_row}}


def _split_catalog_heading(html, unit, year, url):
    name = re.escape(compact(unit['name']))
    heading = re.compile(rf'(?:{name})?([0-9]{{3}}[\u3400-\u9fff]{{2,55}}(?:学院|学部|研究院|研究所|中心|系))'
                         r'(20\d{2})年硕士研究生招生(?:专业)?目录(?:[-—_|](?:招生网|研究生招生信息网))?')
    texts = re.findall(r'<(?:title|h[1-6])\b[^>]*>(.*?)</(?:title|h[1-6])\s*>', html, re.I | re.S)[:50]
    titles = {}
    for value in texts:
        printed = plain_text(value).strip()
        if match := heading.fullmatch(compact(printed)):
            faculty, edition = match.groups()
            if _department(faculty):
                titles[faculty, edition] = printed
    if len(titles) != 1:
        return None
    (faculty, edition), printed = next(iter(titles.items()))
    if edition == str(year) and document_attribution(url, unit, document_heading=printed)['status'] == 'verified':
        return faculty, printed
    return None


def _split_rows(rows, heading, unit, year, document, table):
    if not rows or tuple(compact(cell['text']) for cell in rows[0] if cell) != _SPLIT_COLUMNS:
        return
    if any(not cell or cell['origin_row'] != 1 or cell['rowspan'] != 1 or cell['colspan'] != 1 for cell in rows[0]):
        return
    faculty, title = heading
    for number, row in enumerate(rows[1:], 2):
        if len(row) != len(_SPLIT_COLUMNS) or any(not cell or cell['column'] != index or cell['colspan'] != 1
                                                   for index, cell in enumerate(row, 1)):
            continue
        code, name, seats, exam, _ = row
        if any(cell['origin_row'] != number or cell['rowspan'] != 1 for cell in (code, name, seats)):
            continue
        if not 2 <= exam['origin_row'] <= number < exam['origin_row'] + exam['rowspan'] <= len(rows) + 1:
            continue
        identity = compact(code['text'])
        major = _major(identity + compact(name['text'])) if re.fullmatch(_MAJOR_CODE, identity) else None
        marks = ''.join(re.findall('[①②③④]', exam['text']))
        if not major or marks not in {'①②', '①②③', '①②③④'}:
            continue
        parsed = parse_strict_subjects(exam['text'], str(unit['id']), document)
        if parsed['exam_scope'] != 'unified' or parsed['issues']:
            continue
        exam_ref = _ref(document, exam['text'], table, exam['origin_row'], exam['column'])
        subjects = [{**item, 'evidence': exam_ref} for item in parsed['subjects']]
        record = _program(unit, year, faculty, major[1], major[2], '', '', subjects, '', document)
        yield {**record, 'study_mode': None, 'direction': None, 'direction_scope': 'program',
               'evidence': [evidence(document, title), _ref(document, code['text'], table, number, 1),
                            _ref(document, name['text'], table, number, 2)],
               'exam_evidence': [exam_ref],
               'source_locator': {'table': table, 'row': number, 'exam_row': exam['origin_row']}}


def parse_catalog_html_blocks(html, unit, year, document):
    """Return year-bound candidates for replay against the source."""
    empty = {'programs': [], 'expected_records': 0, 'issues': []}
    if (type(year) is not int or not isinstance(html, str) or len(html) > 8 * 1024 * 1024
            or not official_source_url(document.get('url', ''), unit)):
        return empty
    visible = visible_catalog_html(html)
    if not ordinary_catalog_scope(visible, html=True):
        return empty
    programs = list(_brochure_rows(visible, unit, year, document))
    if _catalog_title(visible, unit, year):
        tables = _PhysicalTables()
        tables.feed(visible)
        for index, rows in enumerate(tables.tables, 1):
            headers = {tuple(row) for row in rows if len(row) in (len(_LISTED_COLUMNS), len(_HEADED_COLUMNS))}
            supported = {_LISTED_COLUMNS, _HEADED_COLUMNS} & headers
            if supported == {_HEADED_COLUMNS}:
                programs.extend(_headed_rows(rows, unit, year, document, index))
            elif supported == {_LISTED_COLUMNS}:
                programs.extend(_listed_rows(rows, unit, year, document, index))
    if college := _college_heading(visible, unit, year):
        logical = _Tables()
        physical = _PhysicalTables()
        logical.feed(visible)
        physical.feed(visible)
        for index, (rows, cells) in enumerate(zip(logical.tables, physical.tables), 1):
            programs.extend(_college_rows(rows, cells, college, unit, year, document, index))
    if split := _split_catalog_heading(visible, unit, year, document['url']):
        tables = _SplitCatalogCells()
        tables.feed(visible)
        for index, rows in enumerate(tables.tables, 1):
            programs.extend(_split_rows(rows, split, unit, year, document, index))
    # The same physical identity printed twice is unresolved, even if some fields agree.
    counts = {}
    for row in programs:
        counts[row['id']] = counts.get(row['id'], 0) + 1
    selected = [row for row in programs if counts[row['id']] == 1]
    return {'programs': selected, 'expected_records': len(programs),
            'issues': ['目录存在重复的方向身份'] if len(selected) < len(programs) else []}
