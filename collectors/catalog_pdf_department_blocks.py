"""Read separate major and direction rows joined by a physical initial-exam cell."""
from collections import Counter
import hashlib
from pathlib import Path
import re

from public_sources import official_source_url
from .catalog_pdf_national import _plumber, _table_rows, extract_pdf_national_layout
from .catalog_pdf_national_v2 import _origin
from .catalog_pdf_sectioned import _same_page_refs
from .catalog_publication import verify_catalog_publication
from .catalog_strict_syntax import parse_strict_directions, parse_strict_subjects
from .catalogs import _program, compact
from .documents import DocumentError, PROJECT_ROOT


PARSER = 'pdf_table_department_blocks_v1'
JXAU_PARSER = 'pdf_table_academic_professional_v1'
_SUMMARY = ('学院代码', '学院名称', '专业代码', '专业名称(加★专业具有博士学位授予权)', '学位类型', '学习形式')
_DETAIL = ('学院、学科(类别)(专业(领域))、拟招生人数及研究方向', '初试科目', '初试自命题科目参考书', '复试、同等学力加试科目及参考书')
_DEPARTMENT = re.compile(r'([0-9]{3})(?![0-9])([\u4e00-\u9fff][^:：]{1,60}?)联系人[:：]')
_MAJOR = re.compile(r'([0-9A-Z]{6})(?![0-9A-Z])([\u4e00-\u9fff].{0,90}?)'
                    r'(?:\((学术学位|专业学位)\))?((?:拟招收(?:非全日制|全日制)硕士研究生[0-9]+名)+)')
_JXAU_SUMMARY = ('学院代码', '学院', '专业代码', '专业(领域、类别)名称', '所授学位', '学制', '学习形式')
_ACADEMIC = ('专业代码、名称及研究方向', '指导教师', '初试科目', '复试及同等学力加试科目')
_PROFESSIONAL = ('专业学位类别代码及名称', '专业领域代码、名称及研究方向', '初试科目', '复试及同等学力加试科目', '学习形式')
_JXAU_DEPARTMENT = re.compile(r'学院代码、名称及联系电话[:：]([0-9]{3})(?![0-9])(.{1,60}?)\([0-9]{3,4}[-－][0-9]{5,12}\)')
_MAJOR_CODE = re.compile(r'([0-9A-Z]{6})(?![0-9A-Z])([\u4e00-\u9fff].{0,90})')
_DIRECTION_START = re.compile(r'(?:[0-9]{2}|[0-9][A-Z]|[A-Z][0-9])(?![0-9A-Z])\s*[\u4e00-\u9fff]')
_ACADEMIC_MODE = '学术型专业仅招收全日制硕士研究生'


def _empty(reason=None, parser=PARSER):
    return {'programs': [], 'expected_records': 0, 'issues': [reason] if reason else [],
            'rejected_source_rows': [], 'retest_rules': [], 'documents': [], 'catalog_format': parser,
            'candidate_only': True, 'semantic_approved': False}


def _title_matches(text, unit, year):
    leading = [compact(line) for line in text.split('\f', 1)[0].splitlines()[:12] if line.strip()]
    leading = [line for line in leading if not line.isdigit()]
    expected = compact(unit.get('name')) + f'{year}年硕士研究生招生专业目录'
    code = str(unit.get('unit_code') or '')
    return bool(len(leading) > 1 and leading[0] == expected and re.fullmatch(r'[0-9]{5}', code)
                and re.match(r'单位代码[:：]' + code + r'(?:地址[:：]|$)', leading[1]))


def matches_nchu_pdf_layout(path, text, unit, year):
    if not _title_matches(text, unit, year):
        return False
    try:
        from pdfminer.psparser import PSException
    except ImportError:
        return False
    try:
        with _plumber().open(path) as pdf:
            tables = pdf.pages[0].find_tables() if pdf.pages else []
            rows = tables[0].extract(x_tolerance=2, y_tolerance=3) if tables else []
            return bool(rows and tuple(map(compact, rows[0])) == _SUMMARY)
    except (DocumentError, OSError, ValueError, TypeError, PSException):
        return False


def _table(page):
    if len(page['tables']) != 1:
        return None, (), []
    table = page['tables'][0]
    rows = _table_rows(table)
    return table, tuple(map(compact, rows[0])) if rows else (), rows


def _page_catalog_title(raw):
    lines = [compact(line) for line in raw.splitlines()[:10] if line.strip()]
    pattern = re.compile(r'.{1,50}20\d{2}年.{0,70}硕士.{0,30}招生专业目录')
    for line, following in zip(lines, [*lines[1:], '']):
        if not re.search(r'20\d{2}年', line):
            continue
        if pattern.fullmatch(line):
            return line
        if pattern.fullmatch(line + following):
            return line + following
    return None


def _department(row, table, page, raw_pages, document, pattern=_DEPARTMENT):
    box = row['cells'][0]
    if not box or any(row['cells'][1:]) or abs(box[0] - table['bbox'][0]) > 1 or abs(box[2] - table['bbox'][2]) > 1:
        return False, None
    match = pattern.match(compact(row['texts'][0]))
    if not match:
        return True, None
    refs = _same_page_refs(match[1] + match[2], raw_pages, document, page)
    return True, (match[1], match[2], refs, page['page']) if refs else None


def _faculty_name_matches(printed, summary):
    if printed == summary:
        return True
    extra = printed[len(summary):] if printed.startswith(summary) else ''
    return bool(re.fullmatch(r'\([\u4e00-\u9fff]{2,25}学院\)', extra)
                and not re.search(r'大学|科学院|研究所|党校', extra))


def _candidate(table, rows, index, major, faculty, membership, page, raw_pages, document, unit, year):
    code, name, degree, plan = major.groups()
    modes = re.findall(r'拟招收((?:非)?全日制)硕士研究生', plan)
    if not faculty:
        raise DocumentError('院系章标题无法核对为该招生单位')
    if faculty[3] != page['page'] and set(modes) - {
        mode for owner, owner_name, major_code, mode in membership if (owner, major_code) == (faculty[0], code)
        and _faculty_name_matches(faculty[1], owner_name)
    }:
        raise DocumentError('跨页学院与原总表专业身份无法核对')
    if re.search(r'仅(?:招收|接收)(?:推免|推荐免试)|单独考试|单考', rows[index][0]):
        raise DocumentError('原文明示特殊招生范围')
    if index + 1 >= len(rows):
        raise DocumentError('专业行后缺少同页印刷方向行')
    major_box, direction_box = (_origin(table, i, 0) for i in (index, index + 1))
    exam_box = _origin(table, index, 1)
    if not exam_box or not major_box or not direction_box or exam_box != _origin(table, index + 1, 1):
        raise DocumentError('专业与方向未由同一个物理初试科目格覆盖')
    if major_box == direction_box:
        raise DocumentError('专业与方向缺少独立物理单元格')
    directions = parse_strict_directions(rows[index + 1][0])
    if directions['issues']:
        raise DocumentError('；'.join(directions['issues']))
    exam = rows[index][1]
    parsed = parse_strict_subjects(''.join(exam.splitlines()), unit['id'], document)
    if parsed['issues'] or parsed['exam_scope'] != 'unified':
        raise DocumentError('；'.join(parsed['issues']) or '初试不是明确普通统考')
    values = [(rows[index][0], major_box), (rows[index + 1][0], direction_box), (exam, exam_box)]
    major_refs, direction_refs, exam_refs = (_same_page_refs(value, raw_pages, document, page, box) for value, box in values)
    if not major_refs or not direction_refs or not exam_refs:
        raise DocumentError('专业、方向或初试原格无法在原文页逐字核对')
    subjects = [{**subject, 'evidence': exam_refs} for subject in parsed['subjects']]
    exam_row = next(i for i, row in enumerate(table['rows'], 1) if row['cells'][1] == exam_box)
    locator = {'page': page['page'], 'table_top': table['bbox'][1], 'row': index + 1,
               'direction_row': index + 2, 'exam_row': exam_row}
    return [{**_program(unit, year, faculty[0] + faculty[1], code, name, compact(direction['label']), mode,
                        subjects, '', document, page['page'], degree=degree),
             'source_locator': locator, 'evidence': [*faculty[2], *major_refs, *direction_refs],
             'exam_evidence': exam_refs, 'catalog_parser': PARSER}
            for mode in dict.fromkeys(modes) for direction in directions['directions']]


def parse_nchu_layout(layout, unit, year, document):
    result = _empty()
    raw_pages = layout['raw_text'].rstrip('\f\r\n ').split('\f')
    if not _title_matches(layout['raw_text'], unit, year) or len(raw_pages) != len(layout['pages']):
        return _empty('原文首标题、单位代码、年度或物理页数无法核对')
    membership, programs, rejected, expected = set(), [], [], 0
    faculty, detailed = None, False
    own_title = compact(unit['name']) + f'{year}年硕士研究生招生专业目录'
    for page in layout['pages']:
        table, header, rows = _table(page)
        if not detailed and header == _SUMMARY:
            membership.update((code, faculty_name, major, mode) for code, faculty_name, major, _, _, mode
                              in [tuple(map(compact, row)) for row in rows[1:]]
                              if re.fullmatch(r'\d{3}', code) and re.fullmatch(r'[0-9A-Z]{6}', major)
                              and mode in {'全日制', '非全日制'})
            continue
        title = _page_catalog_title(raw_pages[page['page'] - 1])
        if not membership or header != _DETAIL or title not in {None, own_title}:
            faculty = None
            continue
        detailed = True
        owners = {item[:2] for item in membership}
        for index, row in enumerate(table['rows'][1:], 1):
            heading, new_faculty = _department(row, table, page, raw_pages, document)
            if heading:
                faculty = new_faculty if new_faculty and any(
                    code == new_faculty[0] and _faculty_name_matches(new_faculty[1], name) for code, name in owners) else None
                continue
            major_text = compact(row['texts'][0])
            if not re.match(r'[0-9A-Z]{6}(?![0-9A-Z])', major_text):
                continue
            expected += 1
            try:
                major = _MAJOR.fullmatch(major_text)
                if not major:
                    raise DocumentError('专业、学位或招生方式原格格式不明确')
                programs.extend(_candidate(table, rows, index, major, faculty, membership, page, raw_pages, document, unit, year))
            except DocumentError as error:
                rejected.append({'page': page['page'], 'row': index + 1, 'reason': str(error)})
    return _finish(result, programs, rejected, expected, detailed)


def _jxau_title_matches(text, unit, year):
    title = compact(unit.get('name')) + f'{year}年硕士研究生招生专业目录'
    lines = [compact(line) for line in text.split('\f', 1)[0].splitlines()[:8] if line.strip()]
    lines = [line for line in lines if not line.isdigit()]
    return bool(lines and (lines[0] == title or ''.join(lines[:2]) == title))


def matches_jxau_pdf_layout(path, text, unit, year):
    if not _jxau_title_matches(text, unit, year):
        return False
    try:
        from pdfminer.psparser import PSException
    except ImportError:
        return False
    try:
        with _plumber().open(path) as pdf:
            for page in pdf.pages[1:4]:
                for table in page.find_tables():
                    rows = table.extract(x_tolerance=2, y_tolerance=3)
                    if rows and tuple(map(compact, rows[0])) == _JXAU_SUMMARY:
                        return True
    except (DocumentError, OSError, ValueError, TypeError, PSException):
        return False
    return False


def _jxau_chapter(page, raw_pages, document, unit, year):
    prefix = compact(unit['name']) + f'{year}年'
    raw = raw_pages[page['page'] - 1]
    leading = [compact(line) for line in raw.splitlines()[:12]]
    title = _page_catalog_title(raw)
    if not title:
        return None
    if title == prefix + '学术型硕士研究生招生专业目录' and any(_ACADEMIC_MODE in line for line in leading):
        mode_refs = _same_page_refs(_ACADEMIC_MODE, raw_pages, document, page)
        return (_ACADEMIC, '学术学位', mode_refs) if mode_refs else ()
    if title == prefix + '专业学位硕士研究生招生专业目录':
        return _PROFESSIONAL, '专业学位', []
    return ()


def _jxau_major(values, professional):
    primary = values[1] if professional else values[0]
    lines = [line.strip() for line in primary.splitlines() if line.strip()]
    if professional and lines and lines[0] == '研究方向':
        major = _MAJOR_CODE.fullmatch(compact(values[0]))
        return major, '\n'.join(lines[1:])
    first_direction = next((i for i, line in enumerate(lines) if _DIRECTION_START.match(line)), len(lines))
    major = _MAJOR_CODE.fullmatch(compact(''.join(lines[:first_direction])))
    return major, '\n'.join(lines[first_direction:])


def _jxau_candidate(table, row, index, major, direction_text, faculty, chapter, membership, page, raw_pages, document, unit, year):
    code, name = major.groups()
    header, degree, mode_refs = chapter
    professional = header == _PROFESSIONAL
    values, cells = row['texts'], row['cells']
    if not faculty:
        raise DocumentError('院系章标题无法核对为该招生单位')
    if faculty[3] != page['page'] and (faculty[0], faculty[1].removeprefix(compact(unit['name'])), code) not in membership:
        raise DocumentError('跨页学院与原总表专业身份无法核对')
    if not cells[2] or not values[2].strip():
        raise DocumentError('本行初试格缺失，未跨页或借用邻行科目')
    major_columns = [1] if professional else [0]
    if professional and values[1].startswith('研究方向'):
        major_columns = [0, 1]
    identity_values = '；'.join(values[col] for col in major_columns)
    if re.search(r'仅(?:招收|接收)(?:推免|推荐免试)|单独考试|单考', identity_values):
        raise DocumentError('原文明示特殊招生范围')
    parsed = parse_strict_subjects(''.join(values[2].splitlines()), unit['id'], document)
    if parsed['issues'] or parsed['exam_scope'] != 'unified':
        raise DocumentError('；'.join(parsed['issues']) or '初试不是明确普通统考')
    if direction_text:
        directions = parse_strict_directions(direction_text)
        if directions['issues']:
            raise DocumentError('；'.join(directions['issues']))
        labels = [compact(direction['label']) for direction in directions['directions']]
    else:
        labels = ['']
    if professional:
        modes = [compact(value) for value in values[4].splitlines() if value.strip()]
        if not modes or set(modes) - {'全日制', '非全日制'} or not cells[4]:
            raise DocumentError('原专业学位行没有明确独立学习形式格')
        mode_refs = _same_page_refs(values[4], raw_pages, document, page, cells[4])
    else:
        modes = ['全日制']
    exam_refs = _same_page_refs(values[2], raw_pages, document, page, cells[2])
    identity_refs = [_same_page_refs(values[col], raw_pages, document, page, cells[col]) for col in major_columns]
    if not mode_refs or not exam_refs or not all(identity_refs):
        raise DocumentError('身份、初试或学习形式原格无法在原文页逐字核对')
    subjects = [{**subject, 'evidence': exam_refs} for subject in parsed['subjects']]
    locator = {'page': page['page'], 'table_top': table['bbox'][1], 'row': index + 1, 'exam_row': index + 1}
    return [{**_program(unit, year, faculty[0] + faculty[1], code, name, label, mode, subjects, '', document,
                        page['page'], degree=degree), 'source_locator': locator, 'catalog_parser': JXAU_PARSER,
             **({'direction_scope': 'program'} if not label else {}),
             'evidence': [*faculty[2], *[ref for refs in identity_refs for ref in refs], *mode_refs],
             'exam_evidence': exam_refs} for mode in dict.fromkeys(modes) for label in labels]


def parse_jxau_layout(layout, unit, year, document):
    result = _empty(parser=JXAU_PARSER)
    raw_pages = layout['raw_text'].rstrip('\f\r\n ').split('\f')
    if not _jxau_title_matches(layout['raw_text'], unit, year) or len(raw_pages) != len(layout['pages']):
        return _empty('原文首标题、年度或物理页数无法核对', JXAU_PARSER)
    membership, programs, rejected, expected = set(), [], [], 0
    faculty, chapter, detailed = None, None, False
    school_name = compact(unit['name'])
    for page in layout['pages']:
        table, header, rows = _table(page)
        if not detailed and header == _JXAU_SUMMARY:
            membership.update((compact(row[0]), compact(row[1]).removeprefix(school_name), compact(row[2]))
                              for row in rows[1:] if all(row[:3]))
            continue
        new_chapter = _jxau_chapter(page, raw_pages, document, unit, year)
        if new_chapter is not None:
            chapter, faculty = new_chapter, None
        if not chapter or not membership or header != chapter[0]:
            chapter, faculty = None, None
            continue
        detailed = True
        owners = {item[:2] for item in membership}
        for index, row in enumerate(table['rows'][1:], 1):
            heading, new_faculty = _department(row, table, page, raw_pages, document, _JXAU_DEPARTMENT)
            if heading:
                faculty = new_faculty if new_faculty and (new_faculty[0], new_faculty[1].removeprefix(school_name)) in owners else None
                continue
            major, direction_text = _jxau_major(row['texts'], header == _PROFESSIONAL)
            if not major:
                continue
            expected += 1
            try:
                programs.extend(_jxau_candidate(table, row, index, major, direction_text, faculty, chapter,
                                                membership, page, raw_pages, document, unit, year))
            except DocumentError as error:
                rejected.append({'page': page['page'], 'row': index + 1, 'reason': str(error)})
    return _finish(result, programs, rejected, expected, detailed)


def _finish(result, programs, rejected, expected, detailed):
    duplicates = {key for key, count in Counter(p['id'] for p in programs).items() if count > 1}
    rejected.extend({**p['source_locator'], 'reason': '方向完整身份重复'} for p in programs if p['id'] in duplicates)
    issues = [f'第{row["page"]}页第{row["row"]}行：{row["reason"]}' for row in rejected]
    if not detailed:
        issues.append('原文没有总表后可核对的分院系初试目录')
    return {**result, 'programs': [p for p in programs if p['id'] not in duplicates], 'expected_records': expected,
            'rejected_source_rows': rejected, 'issues': issues}


def _parse_official_pdf(path, unit, year, document, project_root, parser, parse_layout):
    if (year != 2027 or document.get('year') != year or str(document.get('unit_id')) != str(unit.get('id'))
            or not official_source_url(document.get('url'), unit)):
        return _empty('官方原件单位、年度或来源不一致', parser)
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(32 * 1024 * 1024 + 1)
        if (not raw.startswith(b'%PDF-') or len(raw) > 32 * 1024 * 1024
                or hashlib.sha256(raw).hexdigest() != document.get('sha256')):
            return _empty('PDF原件类型、大小或摘要不一致', parser)
        publication = verify_catalog_publication(document, unit, year, project_root=project_root)
        if not publication['verified'] or publication.get('mode_conflict'):
            return _empty('官方发布页与附件关系或学习形式无法重放：' + publication['reason'], parser)
        result = parse_layout(extract_pdf_national_layout(path), unit, year, document)
        return {**result, 'documents': publication['documents']}
    except (DocumentError, OSError, ValueError, TypeError, KeyError) as error:
        return _empty('原文目录重放失败：' + str(error), parser)


def parse_catalog_pdf_department_blocks(path, unit, year, document, *, project_root=PROJECT_ROOT):
    return _parse_official_pdf(path, unit, year, document, project_root, PARSER, parse_nchu_layout)


def parse_catalog_pdf_academic_professional(path, unit, year, document, *, project_root=PROJECT_ROOT):
    return _parse_official_pdf(path, unit, year, document, project_root, JXAU_PARSER, parse_jxau_layout)
