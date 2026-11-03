"""Conservative parsers for source-backed PDF catalogue table layouts."""
import re

from .catalogs import _program, compact, evidence, parse_subjects


FORMAT = 'row-v1'
MODE = re.compile(r'\(([12])\)(全日制|非全日制)')
DEPARTMENT = re.compile(r'^(\d{3})([^0-9]+?)(?=[0-9A-Z]{6}|$)')
PROGRAM = re.compile(r'^([0-9A-Z]{6})(.+)$')
DIRECTION = re.compile(r'([0-9A-Z]{2})(?![0-9A-Z])(.+)$')
FOOTER = re.compile(r'第\s*(\d+)\s*页\s*[，,]?\s*共\s*(\d+)\s*页')


def sniff_catalog_pdf(text):
    """Recognize a full column-labelled row table; never guess from a filename."""
    cleaned = compact(text)
    header = '院系所专业研究方向学习方式考试方式拟招生人数考试科目'
    return FORMAT if header in cleaned and '21全国统考' in cleaned and MODE.search(cleaned) else None


def _references(*refs):
    return list({(ref['url'], ref['page'], ref['quote']): ref for ref in refs if ref}.values())


def _row(front, tail, state, unit, year, document, raw, page):
    issues = []
    prefix = PROGRAM.match(front)
    if prefix:
        direction = DIRECTION.search(prefix[2])
        if not direction or not prefix[2][:direction.start()]:
            return None, ['专业或方向边界不明确'], state
        name = prefix[2][:direction.start()]
        original_name = re.search(re.escape(prefix[1]) + r'\s*' + r'\s*'.join(re.escape(char) for char in name), raw)
        if not original_name:
            return None, ['专业代码及名称未能定位到逐字原文'], state
        state = {**state, 'code': prefix[1], 'name': prefix[2][:direction.start()],
                 'program_evidence': evidence(document, original_name[0], page)}
        front = prefix[2][direction.start():]
    direction = DIRECTION.fullmatch(front)
    if not direction or not state.get('department') or not state.get('code'):
        return None, ['方向缺少可追溯的院系或专业上下文'], state
    mode, suffix = tail
    exam = re.fullmatch(r'21全国统考(\d+)(①.+)', suffix)
    if not exam:
        return None, ['考试方式、方向人数或考试单元无法完整解析'], state
    subjects, subject_issues = parse_subjects(exam[2], unit['id'], document, page)
    issues.extend(subject_issues)
    program = _program(unit, year, state['department'], state['code'], state['name'],
                       direction[1] + ' ' + direction[2], mode, subjects, '', document, page)
    program['evidence'] = _references(state.get('department_evidence'), state.get('program_evidence'),
                                      evidence(document, raw, page))
    # Use the original row, not a whitespace-normalized reconstruction, for literal provenance.
    start = raw.find('①')
    program['exam_evidence'] = evidence(document, raw[start:], page)
    program['planned_enrollment'] = {'count': int(exam[1]), 'scope': 'direction', 'status': 'provisional',
        'notes': ['原目录拟招生人数，仅供参考，实际计划以该校当年公告为准。'],
        'evidence': evidence(document, raw, page)}
    return program, issues, state


def _parse_rows(text, unit, year, document):
    cleaned = compact(text)
    expected = len(MODE.findall(cleaned))
    issues, programs, state = [], [], {}
    pages = text.rstrip('\f\r\n ').split('\f')
    page_marks = [tuple(map(int, match)) for match in FOOTER.findall(text)]
    page_total = max((total for _, total in page_marks), default=0)
    if not page_total or {number for number, _ in page_marks} != set(range(1, page_total + 1)) or page_total != len(pages):
        issues.append('原文总页数与提取页面未完整对账')
    if compact(unit.get('name')) + str(year) not in cleaned:
        issues.append('原文主标题未明确所选学校和年度')
    if sniff_catalog_pdf(text) != FORMAT:
        issues.append('未识别完整的院系/专业/方向/考试单元列头')
    for page, source in enumerate(pages, 1):
        for raw in source.splitlines():
            raw = raw.strip()
            line = compact(raw)
            if not line or FOOTER.fullmatch(raw):
                continue
            department = DEPARTMENT.match(line)
            if department:
                state = {'department': department[1] + department[2],
                         'department_evidence': evidence(document, raw, page)}
                line = line[department.end():]
            mode = MODE.search(line)
            if mode:
                if (mode[1] == '1') != (mode[2] == '全日制'):
                    issues.append(f'第{page}页学习方式编号与名称不一致')
                    continue
                program, row_issues, state = _row(line[:mode.start()], (mode[2], line[mode.end():]), state,
                                           unit, year, document, raw, page)
                if program:
                    programs.append(program)
                issues.extend(f'第{page}页：{issue}' for issue in row_issues)
            elif line:
                program = PROGRAM.fullmatch(line)
                if program:
                    state = {**state, 'code': program[1], 'name': program[2],
                             'program_evidence': evidence(document, raw, page)}
    if not expected or expected != len(programs):
        issues.append(f'原文{expected}个学习方式/方向标记，实际完整解析{len(programs)}条')
    if len({row['id'] for row in programs}) != len(programs):
        issues.append('原文存在重复的院系、专业、方向与学习方式身份')
    return {'programs': programs, 'expected_records': expected, 'issues': list(dict.fromkeys(issues)),
            'retest_rules': [], 'reconciliation': {'parser': 'pdf_row_v1', 'source_direction_markers': expected,
                'source_exam_markers': cleaned.count('21全国统考'), 'expected_pages': page_total,
                'parsed_pages': len(pages), 'institution_scope': 'unit'}}


def parse_catalog_pdf(text, unit, year, document, *, format=FORMAT):
    """Parse source rows and retain unresolved markers as completeness failures."""
    if format != FORMAT:
        raise ValueError('不支持该PDF目录格式')
    return _parse_rows(text, unit, year, document)
