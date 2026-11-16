"""Read exam rows from ruled PDF tables grouped by explicit learning-mode headings."""
import re

from .catalog_pdf_national import _plumber, _raw_quotes, _table_rows, extract_pdf_national_layout
from .catalog_pdf_national_v2 import _origin
from .catalog_pdf_tables import _cell_lines, _references
from .catalog_publication import verify_catalog_publication
from .catalog_strict_syntax import parse_strict_subjects
from .catalogs import _program, compact
from .documents import DocumentError, PROJECT_ROOT


PARSER = 'pdf_table_sectioned_v1'
_HEADER = ('学院名称', '专业代码及专业名称', '研究方向', '预估招生人数',
           '初试科目', '复试科目', '同等学力加试科目')
_TITLE_CLAIM = re.compile(r'^.{0,60}20\d{2}年.{0,40}硕士.{0,40}(?:招生|专业).{0,30}目录.{0,40}$')
_DEPARTMENT = re.compile(r'\d{3}(?!\d)[\u4e00-\u9fff][^\d]{1,60}')
_MAJOR = re.compile(r'([0-9A-Z]{6})(?![0-9A-Z])([\u4e00-\u9fff].{1,70}?)'
                    r'(?:\((学术学位|专业学位)\))?')
_DIRECTION = re.compile(r'[0-9A-Z]{2}(?![0-9A-Z])[^\d\W_].{1,100}')
_EQUIPMENT_NOTE = re.compile(r'(?m)^\s*(?=请考生自备)')


def _title_pattern(unit, year):
    return re.compile(re.escape(compact(unit['name'])) + str(year)
                      + r'年(非全日制|全日制)硕士研究生招生专业目录')


def matches_sectioned_pdf_title(text, unit, year):
    """Choose the profile from a standalone leading source title only."""
    lines = text.split('\f', 1)[0].splitlines()
    heading = next((compact(line) for line in lines if line.strip()), '')
    return bool(_title_pattern(unit, year).fullmatch(heading))



def matches_sectioned_pdf_layout(path, text, unit, year):
    if not matches_sectioned_pdf_title(text, unit, year):
        return False
    try:
        plumber = _plumber()
        from pdfminer.psparser import PSException
    except (DocumentError, ImportError):
        return False
    try:
        with plumber.open(path) as pdf:
            tables = pdf.pages[0].find_tables() if pdf.pages else []
            rows = tables[0].extract(x_tolerance=2, y_tolerance=3) if tables else []
            return bool(rows and tuple(compact(cell) for cell in rows[0]) == _HEADER)
    except (DocumentError, OSError, ValueError, TypeError, PSException):
        return False


def _same_page_refs(value, raw_pages, document, page, box=None):
    number = page['page']
    refs = _raw_quotes(value, [raw_pages[number - 1]], document, 1)
    if refs:
        return [{**ref, 'page': number} for ref in refs]
    if not box or not page.get('native'):
        return []
    rows = _cell_lines(page['native'], box)
    if compact(''.join(line['text'] for _, line in rows)) != compact(value):
        return []
    refs, missing = _references(rows, document, raw_pages)
    return [] if missing else refs


def _physical_items(page):
    tables = page['tables']
    headings = [line for line in page['lines'] if _TITLE_CLAIM.fullmatch(compact(line['text']))
                and not any(table['bbox'][1] <= line['y0'] <= table['bbox'][3] for table in tables)]
    items = [(line['y1'], 'heading', line) for line in headings]
    items.extend((table['bbox'][1], 'table', table) for table in tables)
    return sorted(items, key=lambda item: (item[0], item[1]))


def _mode_claim(line, page, title_pattern, raw_pages, document):
    match = title_pattern.fullmatch(compact(line['text']))
    refs = _same_page_refs(line['text'], raw_pages, document, page)
    return (match[1], refs) if match and refs else None


def _subject_rows(exam, exam_refs, raw_pages, document, page, unit):
    parsed = parse_strict_subjects(''.join(exam.splitlines()), str(unit['id']), document)
    if parsed['issues'] or parsed['exam_scope'] != 'unified':
        return [], '、'.join(parsed['issues']) or '初试范围不是明确统考'
    subjects = []
    for subject in parsed['subjects']:
        quote = subject['code'] + subject['name']
        refs = _same_page_refs(quote, raw_pages, document, page)
        subjects.append({**subject, 'evidence': refs or exam_refs})
    return subjects, None


def _physical_row(table, row_index, values, chapter, *, page, raw_pages, document, unit, year):
    dept, major, direction, _, exam, _, _ = values
    dept, major, direction = map(compact, (dept, major, direction))
    identity = _MAJOR.fullmatch(major)
    if not (_DEPARTMENT.fullmatch(dept) and identity and _DIRECTION.fullmatch(direction)):
        return None, '学院、专业或方向未能从独立物理列确认'
    if re.search(r'单独考试|单考|仅(?:招收|接收)(?:推免|推荐免试)|不(?:招收|接收)统考', dept + major + direction):
        return None, '招生身份明示特殊范围'
    refs = [_same_page_refs(values[col], raw_pages, document, page, _origin(table, row_index, col))
            for col in (0, 1, 2)]
    exam, *trailing = _EQUIPMENT_NOTE.split(exam, maxsplit=1)
    note = trailing[0] if trailing else ''
    note_text = compact(note)
    if note and not re.fullmatch(r'请考生自备[^。；①②③④⑴⑵⑶⑷]{1,100}。', note_text):
        return None, '初试栏附注存在其他编号或内容边界未确认'
    exam_box = _origin(table, row_index, 4)
    exam_refs = _same_page_refs(exam, raw_pages, document, page, exam_box)
    note_refs = _same_page_refs(note, raw_pages, document, page, exam_box) if note else []
    if not all(refs) or not exam_refs or (note and not note_refs):
        return None, '身份、初试单元格或附注无法在同一原文页逐字对照'
    dept_refs, major_refs, direction_refs = refs
    subjects, issue = _subject_rows(exam, exam_refs, raw_pages, document, page, unit)
    if issue:
        return None, issue
    mode, title_refs = chapter
    program = _program(unit, year, dept, identity[1], identity[2], direction, mode, subjects,
                       note_text, document, page['page'], degree=identity[3])
    return {**program, 'catalog_parser': PARSER,
            'source_locator': {'page': page['page'], 'table_top': table['bbox'][1], 'row': row_index + 1},
            'evidence': [*dept_refs, *major_refs, *direction_refs, *title_refs, *note_refs],
            'exam_evidence': exam_refs}, None


def _table_candidates(table, chapter, *, page, raw_pages, document, unit, year):
    rows = _table_rows(table)
    if not rows or tuple(map(compact, rows[0])) != _HEADER or any(len(row) != len(_HEADER) for row in rows):
        return [], 0, ['物理表格表头与分学习方式目录版式不符']
    body = [(index, values) for index, values in enumerate(rows[1:], 1) if any(values)]
    if body and not chapter:
        return [], len(body), ['表格前没有可绑定本招生单位年度的明确学习方式标题']
    programs, issues = [], []
    for index, values in body:
        program, issue = _physical_row(table, index, values, chapter, page=page, raw_pages=raw_pages,
                                       document=document, unit=unit, year=year)
        if issue:
            issues.append(f'第{index + 1}行：{issue}')
        else:
            programs.append(program)
    return programs, len(body), issues


def parse_catalog_pdf_sectioned(path, unit, year, document, *, project_root=PROJECT_ROOT):
    result = {'programs': [], 'expected_records': 0, 'issues': [], 'retest_rules': [],
              'documents': [], 'catalog_format': PARSER}
    if year != 2027 or document.get('year') != year or document.get('unit_id') != unit.get('id'):
        return {**result, 'issues': ['PDF招生学校或2027原文年度未匹配']}
    layout = extract_pdf_national_layout(path)
    raw_pages = layout['raw_text'].rstrip('\f\r\n ').split('\f')
    if not layout['pages'] or len(raw_pages) != len(layout['pages']):
        return {**result, 'issues': ['原文与物理网格页数无法逐页对应']}
    if not matches_sectioned_pdf_title(layout['raw_text'], unit, year):
        return {**result, 'issues': ['原文首标题未明确该单位的年度及学习方式']}
    if document.get('publication_page'):
        publication = verify_catalog_publication(document, unit, year, project_root=project_root)
        if not publication['verified'] or publication.get('mode_conflict'):
            return {**result, 'issues': ['原件的官方发布页链或学习方式未能复核']}
        result = {**result, 'documents': publication['documents']}
    else:
        publication = {}

    chapter, programs, issues, expected = None, [], [], 0
    title_pattern = _title_pattern(unit, year)
    for page in layout['pages']:
        for _, kind, value in _physical_items(page):
            if kind == 'heading':
                chapter = _mode_claim(value, page, title_pattern, raw_pages, document)
                if not chapter or publication.get('mode') not in {None, chapter[0]}:
                    return {**result, 'issues': [f'第{page["page"]}页的目录标题与招生单位、年度、范围或发布页不符']}
                continue
            rows, count, errors = _table_candidates(value, chapter, page=page, raw_pages=raw_pages,
                                                    document=document, unit=unit, year=year)
            programs.extend(rows)
            expected += count
            issues.extend(f'第{page["page"]}页：{error}' for error in errors)
    if len({program['id'] for program in programs}) != len(programs):
        return {**result, 'expected_records': expected, 'issues': [*issues, '同学习方式的专业方向身份重复']}
    return {**result, 'programs': programs, 'expected_records': expected, 'issues': issues}
