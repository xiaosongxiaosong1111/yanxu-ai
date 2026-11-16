"""Read three QAU admissions chapters without mixing mode, degree or exam rows."""
import math
import re

from .catalog_pdf_national import _table_rows, extract_pdf_national_layout
from .catalog_pdf_national_v2 import _origin
from .catalog_pdf_sectioned import _same_page_refs, _subject_rows
from .catalogs import _program, compact


PARSER = 'pdf_table_qau_chapter_v1'
_CHAPTERS = (('全日制', '学术学位'), ('全日制', '专业学位'), ('非全日制', '专业学位'))
_HEADERS = ('招生专业代码、名称及研究方向', '招生领域代码、名称及研究方向')
_TAIL = ('拟招生计划数', '初试科目', '备注')
_DEPARTMENT = re.compile(r'[0-9]{3}(?![0-9])[\u4e00-\u9fff]{1,60}学院(?:\([^)]{1,60}\))?')
_MAJOR = re.compile(r'([0-9A-Z]{6})(?![0-9A-Z])([\u4e00-\u9fff].{1,80})')
_DIRECTION = re.compile(r'([0-9]{2})(?![0-9])[\u4e00-\u9fffA-Za-z《“‘(].{1,110}')
_SCOPED_FOURTH = re.compile(r'((?:[0-9]{2}方向(?:、|和|及)?)+)[:：]')
_SPECIAL = re.compile(r'工程硕博专项|仅(?:招收|接收)(?:推免|推荐免试)|单独考试|不(?:招收|接收)统考')


def _edges(box):
    if (not isinstance(box, (list, tuple)) or len(box) != 4
            or any(type(v) not in {int, float} or not math.isfinite(v) for v in box)
            or box[2] <= box[0] or box[3] <= box[1]):
        return None
    return [box[0], box[2]]


def _same_edges(left, right):
    return left is not None and right is not None and all(abs(a - b) < 2 for a, b in zip(left, right))


def _grid(table):
    rows = table.get('rows', [])
    if not rows:
        return None
    count = len(rows[0]['texts'])
    if not count or any(len(row['texts']) != count or len(row['cells']) != count for row in rows):
        return None
    columns = []
    for column in range(count):
        boxes = [edge for row in rows if (edge := _edges(row['cells'][column]))]
        if not boxes:
            return None
        narrowest = min(boxes, key=lambda edge: edge[1] - edge[0])
        narrow = [edge for edge in boxes if abs((edge[1] - edge[0]) - (narrowest[1] - narrowest[0])) < 2]
        if any(not _same_edges(edge, narrowest) for edge in narrow):
            return None
        columns.append(narrowest)
    return columns


def _chapter_title(year, mode, degree):
    return f'{year}年{mode}{degree}硕士研究生招生专业目录'


def _cover(raw, pages, unit, year):
    name = compact(unit['name'])
    content = compact(raw)
    heading = name + str(year) + '年硕士研究生招生专业目录'
    if not content.startswith(heading) or len(pages) < 4 or pages[0]['tables']:
        return None
    rest = content[len(heading):]
    ranges = []
    next_start = 1
    for index, (mode, degree) in enumerate(_CHAPTERS):
        title = _chapter_title(year, mode, degree)
        prefix = re.escape('一二三'[index] + '、' + name + title)
        match = re.match(prefix + r'([0-9]{2})[-—－]([0-9]{2})页', rest)
        if not match:
            return None
        start, end = map(int, match.groups())
        if start != next_start or end < start:
            return None
        ranges.append((start + 1, end + 1, mode, degree))
        next_start = end + 1
        rest = rest[match.end():]
    return ranges if next_start == len(pages) else None


def _physical_heading(page, table, unit, year, mode, degree, raw, document):
    leading = [line['text'] for line in page['lines']
               if line['y1'] <= table['bbox'][1] and line['text'].strip()]
    title = _chapter_title(year, mode, degree)
    if len(leading) < 2 or list(map(compact, leading[:2])) != [compact(unit['name']), title]:
        return []
    return _same_page_refs(leading[1], raw, document, page)


def _identity(value):
    lines = [compact(line) for line in value.splitlines() if line.strip()]
    if not lines:
        return None
    chunks = []
    for line in lines:
        if not chunks or _DIRECTION.fullmatch(line):
            chunks.append(line)
        else:
            chunks[-1] += line
    major = _MAJOR.fullmatch(chunks[0])
    directions = chunks[1:]
    codes = [int(value[:2]) for value in directions]
    if not major or any(right != left + 1 for left, right in zip(codes, codes[1:])):
        return None
    return major.groups(), directions or ['']


def _course_variants(value, directions):
    exam = compact(value)
    base, fourth, tail = exam.partition('④')
    labels = list(_SCOPED_FOURTH.finditer(tail))
    if not labels:
        return {direction: value for direction in directions}
    if not fourth or labels[0].start() != 0:
        return None
    by_code = {}
    for index, label in enumerate(labels):
        end = labels[index + 1].start() if index + 1 < len(labels) else len(tail)
        course = tail[label.end():end]
        for code in re.findall(r'([0-9]{2})方向', label[1]):
            if code in by_code or not course:
                return None
            by_code[code] = base + fourth + course
    if set(by_code) != {direction[:2] for direction in directions}:
        return None
    return {direction: by_code[direction[:2]] for direction in directions}


def _row_records(table, index, row, department, chapter, *, page, raw, document, unit, year):
    identity = _identity(row[0])
    if not identity or not department:
        return [], [], '缺少可信的物理院系、专业或方向身份'
    (code, name), directions = identity
    exam = _course_variants(row[2], directions)
    refs = [_same_page_refs(row[col], raw, document, page, _origin(table, index, col)) for col in (0, 2)]
    if not exam or not all(refs):
        return [], [], '方向对应科目或同页物理目录引文未逐项核对'
    mode, degree, chapter_refs = chapter
    dep, dep_refs = department
    position = {'page': page['page'], 'row': index + 1, 'table_top': table['bbox'][1]}
    records, excluded = [], []
    for direction in directions:
        if _SPECIAL.search(name + direction):
            excluded.append({**position, 'direction': direction, 'reason': '原文明示专项或非普通统考方向'})
            continue
        subjects, error = _subject_rows(exam[direction], refs[1], raw, document, page, unit)
        if error:
            return [], excluded, error
        program = _program(unit, year, dep, code, name, direction, mode, subjects, '', document,
                           page['page'], degree=degree)
        records.append({**program, 'catalog_parser': PARSER, 'source_locator': position,
            **({'direction_scope': 'program'} if not direction else {}),
            'evidence': [*chapter_refs, *dep_refs, *refs[0]], 'exam_evidence': refs[1]})
    return records, excluded, None


def _page_records(page, table, rows, first, department, chapter, *, raw, document, unit, year):
    programs, issues, exclusions = [], [], []
    for index, row in enumerate(rows[first:], first):
        if not any(row):
            continue
        identity = compact(row[0])
        if re.match(r'^[0-9]{3}(?![0-9A-Z])', identity):
            quotes = _same_page_refs(row[0], raw, document, page, _origin(table, index, 0))
            if _DEPARTMENT.fullmatch(identity) and '联系人' in row[1] and not any(row[2:]) and quotes:
                department = (identity, quotes)
                continue
            department = None
            issues.append(f'第{page["page"]}页第{index + 1}行：院系头或物理引文未核准')
            continue
        records, excluded, issue = _row_records(table, index, row, department, chapter,
            page=page, raw=raw, document=document, unit=unit, year=year)
        programs.extend(records)
        exclusions.extend(excluded)
        if issue:
            issues.append(f'第{page["page"]}页第{index + 1}行：{issue}')
    return programs, issues, exclusions, department


def parse_qau_layout(layout, unit, year, document):
    empty = {'programs': [], 'issues': [], 'documents': [], 'retest_rules': [],
             'excluded_source_rows': [], 'expected_records': 0, 'catalog_format': PARSER}
    raw = layout['raw_text'].rstrip('\f\r\n ').split('\f')
    pages = layout['pages']
    if not pages or len(raw) != len(pages) or not (ranges := _cover(raw[0], pages, unit, year)):
        return {**empty, 'issues': ['原目录封面院校年度、章节类型与连续物理页码未核对']}
    programs, issues, exclusions = [], [], []
    for start, end, mode, degree in ranges:
        chapter, first_edges, department = None, None, None
        for number in range(start, end + 1):
            page = pages[number - 1]
            if page['page'] != number or len(page['tables']) != 1:
                return {**empty, 'issues': [f'第{number}页没有唯一连续物理目录网格']}
            table = page['tables'][0]
            rows, edges = _table_rows(table), _grid(table)
            if not edges or not rows or any(len(row) != 4 for row in rows):
                return {**empty, 'issues': [f'第{number}页物理目录列数或坐标未核准']}
            if number == start:
                proof = _physical_heading(page, table, unit, year, mode, degree, raw, document)
                header = (_HEADERS[degree == '专业学位'], *_TAIL)
                if not proof or tuple(map(compact, rows[0])) != header:
                    return {**empty, 'issues': [f'第{number}页物理标题、学习方式或目录表头未核准']}
                chapter, first_edges = (mode, degree, proof), edges
            elif (len(edges) != len(first_edges) or not all(_same_edges(a, b) for a, b in zip(edges, first_edges))
                    or any('硕士研究生招生专业目录' in compact(line['text']) and line['y1'] <= table['bbox'][1]
                           for line in page['lines'])):
                return {**empty, 'issues': [f'第{number}页目录范围或网格不连续，停止跨页继承']}
            found, errors, excluded, department = _page_records(
                page, table, rows, 1 if number == start else 0, department, chapter,
                raw=raw, document=document, unit=unit, year=year)
            programs.extend(found)
            issues.extend(errors)
            exclusions.extend(excluded)
    if len({row['id'] for row in programs}) != len(programs):
        return {**empty, 'issues': [*issues, '同一物理目录的专业方向身份重复']}
    return {**empty, 'programs': programs, 'issues': issues, 'excluded_source_rows': exclusions,
            'expected_records': None if issues else len(programs)}


def parse_catalog_qau_pdf(path, unit, year, document, *, project_root=None):
    """Replay geometry after the caller has independently verified the official download chain."""
    if year != 2027 or document.get('year') != year or document.get('unit_id') != unit.get('id'):
        return {'programs': [], 'expected_records': 0, 'retest_rules': [],
                'issues': ['PDF招生学校或2027原文年度未匹配']}
    return parse_qau_layout(extract_pdf_national_layout(path), unit, year, document)
