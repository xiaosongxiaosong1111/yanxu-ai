"""Decode original southern-school tables, keeping decorations and mode evidence."""
from copy import deepcopy
import re

from public_sources import rendered_source_html
from .catalog_html import (_CODE, _clean, _department_heading, _header, _major,
    _major_heading, _row_records, _unrecognized_initial_table, catalog_pagination,
    visible_catalog_html)
from .catalog_html_national import _mode_document, _source_valid, _whole_school_mode
from .catalogs import evidence
from .history import _Tables
from service_verification import plain_text

PARSER = 'html_south_v1'
MAJOR = {'专业代码及专业名称', '专业代码、专业名称及研究方向', '专业代码、专业名称',
    '专业及研究方向', '专业名称及研究方向', '专业代码与名称及研究方向',
    '学科、专业代码及名称研究方向代码及名称', '招生专业代码及名称', '招生专业代码、名称',
    '学科专业代码及名称', '院系所、专业', '专业研究方向', '学科专业名称（代码）研究方向',
    '学科专业名称(代码)研究方向', '专业代码专业名称学习形式'}
DEPARTMENT = {'学院代码及学院名称', '学院代码学院名称联系电话', '院系所代码及名称',
    '培养单位', '院系所', '招生学院及联系方式'}
NAMES = {'学科专业名称', '专业（领域）', '专业(领域)', '专业名称', '学科专业'}
MODE = {'培养方式', '学习形式', '学习方式'}
REFERENCE_SECTIONS = {'参考书目及考试大纲', '考试大纲及参考书目'}
MODE_SUFFIX = r'[（(](?:非全日制|全日制)(?:[、,，](?:非全日制|全日制))*[）)]'


def _empty(reason):
    return {'programs': [], 'expected_records': 0, 'issues': [reason], 'retest_rules': [],
        'table_manifests': [], 'catalog_format': PARSER, 'excluded_records': [],
        'excluded_non_unified_exam': 0, 'excluded_recommendation_only': 0,
        'pagination': {'current': 1, 'total': 1}}


def _table_header(row):
    cells = [_clean(cell) for cell in row]
    value = _header(row)
    if value is None:
        major = next((i for i, cell in enumerate(cells) if cell in MAJOR), None)
        code = next((i for i, cell in enumerate(cells) if cell in {'专业代码', '招生专业代码'}), None)
        name = next((i for i, cell in enumerate(cells) if cell in NAMES), None)
        exam = next((i for i, cell in enumerate(cells) if cell in {'初试科目', '初试考试科目', '考试科目', '考试科目代码及名称'}), None)
        if exam is None or major is None and (code is None or name is None): return None
        value = {'exam': exam, 'slots': {}, 'major': major, 'code': code, 'name': name,
            'department': None, 'direction': None, 'mode': None, 'kind': None,
            'entry_method': None, 'degree': None, 'note': None, 'department_code': None,
            'direction_code': None, 'headers': cells, 'mode_plans': {}}
    result = deepcopy(value)
    aliases = {'department': DEPARTMENT, 'mode': MODE,
        'direction': {'研究方向', '专业研究方向'}, 'direction_code': {'研究方向序号'},
        'name': NAMES, 'degree': {'学位类别', '学位类型', '类型'}, 'note': {'备注', '报考条件'}}
    for field, choices in aliases.items():
        if result.get(field) is None:
            result[field] = next((i for i, cell in enumerate(cells) if cell in choices), None)
    result['explicit_full_time_header'] = next((cell for cell in cells if cell in {'拟招生名额（全日制）', '拟招生名额(全日制)'}), '')
    return result


def _cell(row, header, field):
    index = header.get(field)
    return row[index] if index is not None and index < len(row) else ''


def _department_row(row, header):
    known = _department_heading(row)
    if known: return known
    value = _cell(row, header, 'major')
    other = [_cell(row, header, field) for field in ('exam', 'direction', 'mode')]
    contacts = {cell for cell in other if cell}
    contact_only = len(contacts) == 1 and bool(re.match(r'联系人[:：]|联系电话[:：]|学院网站[:：]', next(iter(contacts))))
    if (re.fullmatch(r'\d{3}(?!\d)[\u4e00-\u9fff].+', value)
            and (not any(other) or contact_only)
            and ('院系' in value or re.search('学院|学部|研究所|研究院|中心', value))):
        return {'department': value, 'department_quote': value}
    return None


def _mode_cell(row, header, inherited, school_mode):
    explicit = _cell(row, header, 'mode')
    if re.search(r'非全日制|(?<!非)全日制', explicit): return explicit
    major = _cell(row, header, 'major') or _cell(row, header, 'name')
    if re.search(MODE_SUFFIX, major): return major
    degree = _cell(row, header, 'degree')
    if re.fullmatch(r'(?:非全日制|全日制)(?:学术学位|专业学位)', degree): return degree
    note = _cell(row, header, 'note')
    if re.fullmatch(r'(?:非全日制|全日制)(?:[、,，](?:非全日制|全日制))?', note): return note
    return inherited.get('mode_quote') or header.get('explicit_full_time_header') or school_mode


def _subject_cell(text):
    # Only published ornaments between an exam ordinal and its numeric code are grammar.
    return re.sub(r'([①②③④])\s*[▲△★☆]+(?=\s*[1-9]\d{2})', r'\1', text)


def _school_mode(text, year, unit):
    known = _whole_school_mode(text, year, unit)
    if known: return known
    value = _clean(plain_text(text))
    patterns = [str(year) + r'年仅招收全日制硕士研究生[，,]不招收非全日制硕士研究生[。；]',
        r'我校仅招收参加全国统一考试的全日制硕士研究生[。；]',
        r'培养的硕士研究生类型为全日制硕士[，,]学制\d年[，,。；]',
        re.escape(_clean(unit['name'])) + r'招收的硕士生为全日制研究生[；。]',
        str(year) + r'年我校招收全日制硕士研究生[，,]包括全日制定向就业和全日制非定向就业两种类型[。；]']
    return next((match[0] for pattern in patterns if (match := re.search(pattern, value))), '')


def _restore_proofs(value, replacements):
    if isinstance(value, dict):
        return {key: (replacements.get(child, child) if key == 'quote' and isinstance(child, str)
            else _restore_proofs(child, replacements)) for key, child in value.items()}
    if isinstance(value, list): return [_restore_proofs(child, replacements) for child in value]
    return value


def _parse_row(row, header, inherited, school_mode, unit, year, document, table_index, row_index):
    working, profile, replacements = [*row], deepcopy(header), {}
    department_index = profile.get('department')
    if department_index is not None and department_index < len(working):
        original = working[department_index]
        stripped = re.split(r'联系人[:：]|联系方式[:：]|联系电话[:：]|学院网站[、:：]', original, maxsplit=1)[0]
        if stripped != original:
            working[department_index] = stripped
            replacements[stripped] = original
    original_exam = _cell(row, header, 'exam')
    if profile['exam'] is not None and profile['exam'] < len(working):
        exam = _subject_cell(original_exam)
        working[profile['exam']] = exam
        if exam != original_exam: replacements[_clean(exam)] = original_exam
    mode = _mode_cell(row, header, inherited, school_mode)
    if mode and not _cell(row, header, 'mode'):
        profile['mode'] = len(working)
        if mode == school_mode or mode == header.get('explicit_full_time_header'):
            working.append('全日制')
            replacements['全日制'] = mode
        else:
            working.append(mode)
    major_index = profile.get('major')
    if profile.get('direction') is not None and major_index is not None and major_index < len(working):
        original = working[major_index]
        stripped = re.sub(MODE_SUFFIX, '', original)
        working[major_index] = stripped
        if stripped != original: replacements[stripped] = original
    degree_index = profile.get('degree')
    if degree_index is not None and degree_index < len(working):
        original = working[degree_index]
        stripped = re.sub(r'^(?:非全日制|全日制)(?=学术学位|专业学位)', '', original)
        working[degree_index] = stripped
        if stripped != original: replacements[stripped] = original
    direction_index = profile.get('direction') if profile.get('direction') is not None else major_index
    if direction_index is not None and direction_index < len(working):
        original = working[direction_index]
        stripped = re.sub(r'^[-—]{1,2}(?=\d{2}[^\d])', '', original)
        working[direction_index] = stripped
        if stripped != original: replacements[stripped] = original
    parent = _major(row, header)
    leaf = re.fullmatch(r'([0-9A-Z]{6})([^0-9]+)', _cell(row, header, 'direction'))
    child_major = bool(parent and len(parent[0]) == 4 and leaf and leaf[1].startswith(parent[0]))
    if child_major:
        working[major_index] = leaf[0]
        working[profile['direction']] = ''
    records, expected, issues, excluded = _row_records(working, profile, unit, year, document,
        table_index, row_index, inherited=inherited)
    output = []
    for record in records:
        restored = _restore_proofs(record, replacements)
        if child_major:
            restored = {**restored, 'evidence': [*restored['evidence'],
                evidence(document, _cell(row, header, 'major'))]}
        output.append({**restored, 'catalog_parser': PARSER, 'raw_source': {
            'initial_exam': original_exam, 'major': _cell(row, header, 'major'),
            'direction': _cell(row, header, 'direction'), 'mode_evidence': mode}})
    return output, expected, issues, excluded


def _split_direction_row(row, header):
    column = header.get('direction')
    value = row[column] if column is not None and column < len(row) else ''
    marks = list(re.finditer(r'(?<!\d)[1-9]\d?[.、．](?=[\u4e00-\u9fff])', value))
    if len(marks) < 2 or value[:marks[0].start()].strip('；;、，,'): return [row]
    return [[*row[:column], value[mark.start():marks[index + 1].start() if index + 1 < len(marks) else len(value)].strip('；;、，,。'),
        *row[column + 1:]] for index, mark in enumerate(marks)]


def _parent_major(row, header, mode):
    major = _major(row, header)
    if not major: return None
    code, name, degree, embedded = major
    name = re.sub(MODE_SUFFIX, '', name)
    return {'major': (code, name, degree, embedded), 'major_quote': _cell(row, header, 'major'),
        **({'mode_quote': mode} if mode else {})}


def _contains_initial_rows(table):
    if _unrecognized_initial_table(table): return True
    text = ''.join(''.join(row) for row in table)
    return bool(_CODE.search(text) and re.search(r'①[▲△★☆]*1\d{2}.+②[▲△★☆]*2\d{2}', text))


def parse_catalog_html_south(html, unit, year, document, *, project_root=None):
    text = visible_catalog_html(rendered_source_html(html, document.get('url', '')))
    if not _source_valid(text, unit, year, document):
        return _empty('学校、年度、原文归属或统考招生范围未通过核验')
    tables = _Tables(); tables.feed(text)
    mode_document, mode_quote = _mode_document(document, unit, year, project_root)
    school_mode = _school_mode(text, year, unit) or mode_quote
    records, issues, manifests, excluded = [], [], [], []
    expected, recommended = 0, 0
    for table_index, table in enumerate(tables.tables, 1):
        located = next(((i, found) for i, row in enumerate(table[:8]) if (found := _table_header(row))), None)
        if located is None:
            if _contains_initial_rows(table): issues.append(f'表{table_index}存在未适配的专业初试条目')
            continue
        start, header = located
        inherited = next((value for row in reversed(table[:start]) if (value := _department_heading(row))), {})
        table_expected, consumed, active, parent_used = 0, 0, True, True
        for row_index, row in enumerate(table[start + 1:], start + 2):
            if _table_header(row): active = True; continue
            values = {cell for cell in row if cell}
            if len(values) == 1 and next(iter(values)) in REFERENCE_SECTIONS:
                active = False; continue
            if not active: continue
            note = _cell(row, header, 'note')
            if re.search(r'(?:仅|只)(?:接收|招收|招生)(?:推荐免试|推免)|不接收统考', note):
                recommended += 1
                continue
            method = _cell(row, header, 'entry_method')
            if re.fullmatch(r'(?:23)?单独考试|23', method):
                excluded.append({'reason': '原文明确单独考试', 'evidence': [evidence(document, method)],
                    'source_locator': {'table': table_index, 'row': row_index}})
                continue
            department = _department_row(row, header)
            if department:
                inherited = {**inherited, **department}; continue
            own = _major(row, header)
            if _major_heading(row, header):
                if not parent_used: issues.append(f'表{table_index}有未消费的专业标题')
                inherited = {**inherited, **(_parent_major(row, header,
                    _mode_cell(row, header, {}, school_mode)) or {})}
                parent_used = False; continue
            if len(values) <= 1 or row[0] in {'合计', '总计'}: continue
            if not own and not inherited.get('major'):
                if _contains_initial_rows([row]):
                    expected += 1; table_expected += 1
                    issues.append(f'表{table_index}第{row_index}行：初试行的专业身份未能确定')
                continue
            parent_used = True; consumed += 1
            for direction_row in _split_direction_row(row, header):
                parsed, count, row_issues, row_excluded = _parse_row(direction_row, header, inherited,
                    school_mode, unit, year, document, table_index, row_index)
                expected += count; table_expected += count
                records.extend(parsed); excluded.extend(row_excluded)
                issues.extend(f'表{table_index}第{row_index}行：{reason}' for reason in row_issues)
        if not parent_used: issues.append(f'表{table_index}有未消费的专业标题')
        manifests.append({'table_index': table_index, 'headers': header['headers'],
            'source_rows': consumed, 'expected_records': table_expected})
    if not manifests: issues.append('未发现可核验的招生目录表头')
    if len({row['id'] for row in records}) != len(records): issues.append('专业方向身份重复，未静默合并')
    if mode_document and mode_quote:
        old = evidence(document, mode_quote)
        replacement = evidence(mode_document, mode_quote)
        records = [{**row, 'evidence': [replacement if ref == old else ref for ref in row['evidence']]}
            for row in records]
    return {'programs': records, 'expected_records': expected, 'issues': issues, 'retest_rules': [],
        'table_manifests': manifests, 'catalog_format': PARSER, 'excluded_records': excluded,
        'excluded_non_unified_exam': len(excluded), 'excluded_recommendation_only': recommended,
        'documents': [mode_document] if mode_document else [], 'pagination': catalog_pagination(text)}
