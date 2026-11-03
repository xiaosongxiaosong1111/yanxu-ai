"""Read explicit school catalogue layouts without rewriting the saved HTML."""
import re
import hashlib
from copy import deepcopy
from pathlib import Path

from public_sources import official_source_url, rendered_source_html
from service_verification import canonical, html_catalog_year, ordinary_catalog_scope, plain_text
from .catalog_html import (_clean, _context_mode, _directions, _header, _major,
                           _row_records, _unrecognized_initial_table, visible_catalog_html,
                           catalog_pagination)
from .catalogs import evidence
from .history import _Tables
from .documents import PROJECT_ROOT, decode_text


PARSER = 'html_national_v1'
CODE = re.compile(r'(?<![0-9A-Z])(?:[0-9A-Z]{6}|(?:0[1-9]|1[0-4])[0-9]{2})(?![0-9A-Z])')
MAJOR_HEADERS = {'专业/专业领域', '招生代码及专业', '专业代码和名称', '专业名称',
                 '专业名称（专业代码）', '专业名称(专业代码)', '专业、研究方向',
                 '专业代码、名称、研究方向'}
DEPARTMENT_HEADERS = {'系所代码及名称', '招生院所', '培养单位', '院系所', '学院代码名称'}
EXAM_HEADERS = {'考试科目代码及名称', '初试考试科目', '初试科目', '考试科目'}
QUOTA_HEADERS = {'拟招生人数', '招生总人数（包括推免生人数）', '招生总人数(包括推免生人数)'}
IMAC_HEADERS = ['院系所', '专业', '研究方向', '学习方式', '普通计划/退役大学生士兵计划',
                '考试科目', '参考书目', '备注']


def _empty(reason):
    return {'programs': [], 'expected_records': 0, 'issues': [reason], 'retest_rules': [],
            'table_manifests': [], 'catalog_format': PARSER, 'excluded_records': [],
            'excluded_non_unified_exam': 0, 'excluded_recommendation_only': 0,
            'pagination': {'current': 1, 'total': 1}}


def _source_valid(text, unit, year, document):
    if (type(year) is not int or year != 2027 or document.get('unit_id') != str(unit.get('id'))
            or document.get('year') != year or not official_source_url(document.get('url'), unit)):
        return False
    if canonical(unit.get('name', '')) not in canonical(plain_text(text)):
        return False
    if not html_catalog_year(text, year) or not ordinary_catalog_scope(text, html=True):
        return False
    headings = [match['body'] for match in re.finditer(
        r'<(?P<tag>div|span|h[1-6]|p)\b[^>]*class=["\'](?:[^"\']*\s)?'
        r'(?:article-title|sch-sspe-swtit|ch-title|news-title|content-title)'
        r'(?:\s[^"\']*)?["\'][^>]*>(?P<body>.*?)</(?P=tag)>', text, re.I | re.S)]
    if not headings:
        headings = re.findall(r'<(?:h[1-6]|title)\b[^>]*>(.*?)</(?:h[1-6]|title)>', text, re.I | re.S)
    for heading in headings:
        value = plain_text(heading)
        years = set(re.findall(r'(?<!\d)20\d{2}(?!\d)', value))
        if years and re.search('硕士|招生|专业目录', value):
            return years == {str(year)}
    return False


def _profile_header(row):
    headers = [_clean(cell) for cell in row]
    known = _header(row)
    if known is None:
        major = next((i for i, text in enumerate(headers) if text in MAJOR_HEADERS or text == '专业'), None)
        exam = next((i for i, text in enumerate(headers) if text in EXAM_HEADERS), None)
        if major is None or exam is None:
            return None
        known = {'exam': exam, 'slots': {}, 'major': major, 'code': None, 'name': None,
                 'department': None, 'direction': None, 'mode': None, 'kind': None,
                 'entry_method': None, 'degree': None, 'note': None, 'department_code': None,
                 'direction_code': None, 'headers': headers, 'mode_plans': {}}
    additions = {
        'department': DEPARTMENT_HEADERS,
        'direction': {'研究方向', '研究方向代码及名称'},
        'mode': {'学习方式', '学习形式'},
        'degree': {'学位类型', '学位类别', '类别', '类型'},
        'note': {'备注', '报考条件'},
    }
    result = deepcopy(known)
    if headers.count('专业/专业领域') > 1:
        result['major_candidates'] = [i for i, value in enumerate(headers) if value == '专业/专业领域']
    for key, aliases in additions.items():
        if result.get(key) is None:
            result[key] = next((i for i, text in enumerate(headers) if text in aliases), None)
    # These columns establish a mode only when the original cell spells it out.
    if result.get('mode') is None:
        result['mode'] = next((i for i, text in enumerate(headers) if text in QUOTA_HEADERS), None)
    return result


def _whole_school_mode(text, year, unit):
    explicit = _context_mode(text, year)
    if explicit:
        return explicit
    value = _clean(plain_text(text))
    match = re.search(r'(?:学校|我校)[^。；]{0,100}招收全日制非定向就业和全日制定向就业硕士研究生[。；]', value)
    if match and not re.search(r'部分|其中|除|校区|学院|学部', match[0]):
        return match[0]
    title = re.escape(_clean(unit['name'])) + str(year) + r'年全日制硕士研究生招生(?:专业)?目录'
    match = re.search(title, value)
    return match[0] if match else ''


def _context_policy(text):
    value = _clean(plain_text(text))
    for clause in re.split(r'(?<=[。；])', value):
        if re.search(r'部分|除|其中|校区|学院|学部|学术学位|专业学位|学术型|专业型', clause):
            continue
        pattern = (r'我校(?:各学科专业)?硕士研究生(?:学习方式均为全日制|采用全日制学习方式)'
                   r'[^。；]*[。；]?')
        match = re.search(pattern, clause)
        if match and '非全日制' not in match[0]:
            return match[0]
    return ''


def _mode_document(document, unit, year, root):
    context = document.get('learning_mode_document')
    if not isinstance(context, dict):
        return None, ''
    try:
        root = Path(root or PROJECT_ROOT).resolve()
        project = root.parent if root.name == 'data' else root
        path = Path(context['local_path'])
        path = (project / path if path.parts and path.parts[0] == 'data' else root / path) if not path.is_absolute() else path
        path = path.resolve()
        if not path.is_relative_to(project / 'data/snapshots') or not 0 < path.stat().st_size <= 8 * 1024 * 1024:
            return None, ''
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != context.get('sha256'):
            return None, ''
        text = visible_catalog_html(rendered_source_html(decode_text(raw, context.get('content_type', '')), context.get('url', '')))
        if not _source_valid(text, unit, year, context):
            return None, ''
        quote = _context_policy(text)
        if not quote:
            return None, ''
        return {**context, 'kind': 'catalog_context'}, quote
    except (OSError, ValueError, TypeError, KeyError):
        return None, ''


def _imac_continuation(table, previous, unit, document):
    if not (unit.get('id') == '2311267403' and '/jzml/' in document.get('url', '')
            and previous and previous['headers'] == IMAC_HEADERS and table):
        return False
    # The second table is a published continuation, not an arbitrary adjacent table.
    return all(len(row) == len(IMAC_HEADERS) and re.fullmatch(r'\d{3}[\u4e00-\u9fff].+', row[0])
               and re.match(r'[0-9A-Z]{6}', row[1]) and re.match(r'[0-9A-Z]{2}', row[2])
               and (not row[3] or re.fullmatch(r'[（(]1[）)]全日制', row[3]))
               and re.search(r'①101.+②', row[5]) for row in table)


def _repair_department(record, row, header):
    column = header.get('department')
    value = row[column] if column is not None and column < len(row) else ''
    match = re.fullmatch(r'(\d{2,4})(?!\d)([\u4e00-\u9fff].+)', value)
    if not match:
        return record
    code, name = match.groups()
    parts = record['id'].split(':')
    return {**record, 'id': ':'.join([*parts[:2], code, *parts[3:]]),
            'department_code': code, 'department': name}


def _parse_row(row, header, unit, year, document, table_index, row_index, mode):
    header = _specific_major(row, header)
    direction_column = header.get('direction')
    direction = row[direction_column] if direction_column is not None and direction_column < len(row) else ''
    parent = _major(row, header)
    child = re.fullmatch(r'([0-9A-Z]{6})([\u4e00-\u9fff].+)', direction)
    field = bool(child and parent and len(parent[0]) == 4 and child[1].startswith(parent[0]))
    unnumbered = bool(direction and not _directions(direction))
    # The shared decoder reads the other original cells; this decoder retains an unnumbered name.
    shared_row = [*row]
    exam_column = header.get('exam')
    if exam_column is not None and exam_column < len(shared_row):
        shared_row[exam_column] = re.split(r'复试(?:科目)?[:：]|同等学力', shared_row[exam_column], maxsplit=1)[0]
    if unnumbered or field:
        shared_row[direction_column] = ''
    records, expected, issues, excluded = _row_records(shared_row, header, unit, year, document,
        table_index, row_index, context_mode=mode)
    output = []
    for record in records:
        if field:
            parts = record['id'].split(':')
            record = {**record, 'id': ':'.join([*parts[:3], child[1], '', parts[-1]]),
                      'code': child[1], 'name': child[2], 'direction': '', 'direction_scope': 'program',
                      'evidence': [*record['evidence'], evidence(document, direction)]}
            record = {key: value for key, value in record.items() if key != 'code_level'}
        elif unnumbered:
            parts = record['id'].split(':')
            record = {**record, 'id': ':'.join([*parts[:-2], direction, parts[-1]]),
                      'direction': direction, 'evidence': [*record['evidence'], evidence(document, direction)]}
            record = {key: value for key, value in record.items() if key != 'direction_scope'}
        output.append({**_repair_department(record, row, header), 'catalog_parser': PARSER})
    return output, expected, issues, excluded


def _specific_major(row, header):
    choices = header.get('major_candidates', [])
    values = [(i, re.match(r'([0-9A-Z]{6}|\d{4})(?![0-9A-Z])', row[i]))
              for i in choices if i < len(row)]
    explicit = [(i, match[1]) for i, match in values if match]
    leaves = [(i, code) for i, code in explicit if len(code) == 6]
    if len(leaves) == 1 and all(leaves[0][1].startswith(code) for _, code in explicit):
        return {**header, 'major': leaves[0][0]}
    return header


def _has_identity(row, header):
    if _major(row, header):
        return True
    return any(CODE.search(cell) for cell in row) and any(re.search(r'[①②③④]', cell) for cell in row)


def _excluded(row, header):
    fields = [row[index] for key in ('note', 'direction')
              if (index := header.get(key)) is not None and index < len(row)]
    if any(re.search(r'(?:仅|只)(?:接收|招收|招生)(?:推荐免试|推免)|不接收统考', text) for text in fields):
        return 'recommendation'
    method_column = header.get('entry_method')
    method = row[method_column] if method_column is not None and method_column < len(row) else ''
    if method in {'23', '单独考试', '23单独考试'}:
        return 'single'
    return None


def _foreign_unit(row, header, unit):
    column = header.get('department')
    if column is None or header['headers'][column] != '招生单位' or column >= len(row):
        return False
    value = re.sub(r'^\d{5}', '', row[column])
    return canonical(value) != canonical(unit['name'])


def parse_catalog_html_national(html, unit, year, document, *, project_root=None):
    """Return original-cell identities and complete exam groups; never infer absent mode."""
    text = visible_catalog_html(rendered_source_html(html, document.get('url', '')))
    if not _source_valid(text, unit, year, document):
        return _empty('学校、年度、官方原文归属或统考招生范围未通过核验')
    parser = _Tables()
    parser.feed(text)
    records, issues, manifests, excluded_records = [], [], [], []
    expected, excluded_recommendation, excluded_single = 0, 0, 0
    context_document, context_quote = _mode_document(document, unit, year, project_root)
    mode, previous = _whole_school_mode(text, year, unit) or context_quote, None
    for table_index, table in enumerate(parser.tables, 1):
        located = next(((i, found) for i, row in enumerate(table[:5]) if (found := _profile_header(row))), None)
        continuation = located is None and _imac_continuation(table, previous, unit, document)
        if located is None and not continuation:
            if _unrecognized_initial_table(table):
                issues.append(f'表{table_index}有初试专业条目但缺少表头或布局未适配')
            previous = None
            continue
        start, header = (0, previous) if continuation else (located[0] + 1, located[1])
        previous = header
        table_expected, consumed = 0, 0
        for row_index, row in enumerate(table[start:], start + 1):
            if _profile_header(row) or not _has_identity(row, header):
                continue
            if _foreign_unit(row, header, unit):
                issues.append(f'表{table_index}第{row_index}行：招生单位为其他独立单位，不能作为本校院系借用')
                continue
            excluded = _excluded(row, header)
            if excluded:
                excluded_recommendation += int(excluded == 'recommendation')
                excluded_single += int(excluded == 'single')
                excluded_records.append({'reason': '原文明确推免专用' if excluded == 'recommendation' else '原文明确单独考试',
                    'evidence': [evidence(document, cell) for cell in row if cell],
                    'source_locator': {'table': table_index, 'row': row_index}})
                continue
            parsed, count, gaps, excluded_rows = _parse_row(row, header, unit, year, document,
                table_index, row_index, mode)
            if context_document and mode == context_quote:
                parsed = [{**record, 'evidence': [evidence(context_document, context_quote)
                    if ref['quote'] == context_quote else ref for ref in record['evidence']]} for record in parsed]
            records.extend(parsed)
            expected += count
            table_expected += count
            consumed += 1
            excluded_single += len(excluded_rows)
            excluded_records.extend(excluded_rows)
            issues.extend(f'表{table_index}第{row_index}行：{gap}' for gap in gaps)
        manifests.append({'table_index': table_index, 'headers': header['headers'],
            'source_rows': consumed, 'expected_records': table_expected, 'continuation': continuation})
    if not manifests:
        issues.append('未发现可核验的招生目录表头')
    if len({row['id'] for row in records}) != len(records):
        issues.append('原文包含重复专业方向身份，尚未完成目录对账')
    return {'programs': records, 'expected_records': expected, 'issues': issues, 'retest_rules': [],
            'documents': [context_document] if context_document else [],
            'table_manifests': manifests, 'catalog_format': PARSER,
            'excluded_recommendation_only': excluded_recommendation,
            'excluded_non_unified_exam': excluded_single, 'excluded_records': excluded_records,
            'pagination': catalog_pagination(text)}
