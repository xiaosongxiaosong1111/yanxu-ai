"""Explicit column metadata for wrapped modes; original PDF evidence is unchanged."""
from copy import deepcopy
import math
import re
import unicodedata

from .catalog_html import _header
from .catalog_pdf_national import _canonical_header, _table_rows
from .catalog_pdf_national_v2 import _header_row, _origin, extract_pdf_national_layout_v2, parse_catalog_pdf_national_v2
from .catalogs import compact
from .documents import PROJECT_ROOT
from service_verification import canonical


PARSER = 'pdf_table_national_v3'
MODE_HEADERS = frozenset({'学习方式', '学习形式', '报考学习方式'})


def extract_pdf_national_layout_v3(path):
    return extract_pdf_national_layout_v2(path)


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


def _continuation(page, table, index, previous, grid):
    return bool(previous and index == 1 and previous['page'] == page['page'] - 1
        and table['rows'] and table['bbox'][1] < page['height'] * .35
        and len(table['rows'][0]['texts']) == previous['count']
        and _same_edges([table['bbox'][0], table['bbox'][2]], previous['outer'])
        and grid is not None and previous['grid'] is not None and len(grid) == len(previous['grid'])
        and all(_same_edges(left, right) for left, right in zip(grid, previous['grid'])))


def _normalize_modes(table, schema, *, page, index, global_index, inherited):
    rows, changes = table['rows'], []
    for row_index, row in enumerate(rows, 1):
        for column in schema['mode_columns']:
            if column >= len(row['texts']) or column >= len(row['cells']):
                continue
            value = row['texts'][column]
            normalized = compact(value)
            if (normalized not in {'全日制', '非全日制'} or value == normalized
                    or not _same_edges(_edges(row['cells'][column]), schema['mode_edges'][column])):
                continue
            changes.append({'profile': 'wrapped_learning_mode_cell', 'page': page['page'],
                'table': index, 'table_index': global_index, 'row': row_index, 'column': column,
                'original': value, 'normalized': normalized, 'bbox': deepcopy(row['cells'][column]),
                'header_proof': deepcopy(schema['origin']), 'inherited_column_role': inherited})
            row['texts'] = [normalized if col == column else text for col, text in enumerate(row['texts'])]
    return changes


def _prepare(layout):
    result, changes, roles, previous, global_index = deepcopy(layout), [], {}, None, 0
    for page in result.get('pages', []):
        for index, table in enumerate(page.get('tables', []), 1):
            global_index += 1
            header_index, grid = _header_row(table['rows']), _grid(table)
            continued = _continuation(page, table, index, previous, grid)
            if header_index is not None:
                row = table['rows'][header_index]
                original_labels = list(row['texts'])
                for column, label in enumerate(row['texts']):
                    if canonical(label) == '院、系所':
                        changes.append({'profile': 'explicit_department_header', 'page': page['page'],
                            'table': index, 'table_index': global_index, 'row': header_index + 1,
                            'column': column, 'original': label, 'normalized': '学院',
                            'bbox': deepcopy(row['cells'][column]), 'inherited_column_role': False})
                        row['texts'] = ['学院' if col == column else text for col, text in enumerate(row['texts'])]
                columns = [col for col, label in enumerate(row['texts']) if canonical(label) in MODE_HEADERS]
                header = _header(_canonical_header(row['texts'])) or {}
                schema = {'mode_columns': columns, 'department': header.get('department'), 'header': header,
                    'mode_edges': {col: _edges(row['cells'][col]) for col in columns},
                    'origin': {'page': page['page'], 'table': index, 'row': header_index + 1,
                               'labels': original_labels}}
                inherited = False
            elif continued:
                schema, inherited = previous['schema'], True
            else:
                roles[global_index] = {'blocked': True}
                previous = None
                continue
            changes.extend(_normalize_modes(table, schema, page=page, index=index,
                global_index=global_index, inherited=inherited))
            roles[global_index] = {'table': table, 'schema': schema, 'blocked': False,
                'page': page['page'], 'data_start': header_index + 1 if header_index is not None else 0,
                'continued_from': previous['table_index'] if continued else None}
            previous = {'page': page['page'], 'count': len(table['rows'][0]['texts']),
                'outer': [table['bbox'][0], table['bbox'][2]], 'grid': grid, 'schema': schema, 'table_index': global_index}
    return result, changes, roles


def prepare_layout(layout):
    """Return an immutable copy plus exact changed-cell manifests; never fill values."""
    result, changes, _ = _prepare(layout)
    return result, changes


def _identity_issue(program, roles):
    location = program.get('source_locator', {})
    role = roles.get(location.get('table'), {})
    if role.get('blocked'):
        return '续表列角色未由相邻页一致物理网格证明，未采用该方向'
    column = role.get('schema', {}).get('department')
    if column is not None and (not program.get('department') or program.get('department_scope') == 'unit'):
        return '原表明示院系身份列但输出院系缺失，未降为全校范围'
    rows = _table_rows(role.get('table', {'rows': []}))
    row_index = location.get('row', 0) - 1
    if not 0 <= row_index < len(rows):
        return '原表院系行定位缺失，未采用该方向'
    match = re.match(r'^(\d{3})(?!\d)', compact(rows[row_index][column])) if column is not None else None
    if match and str(program.get('department_code')) != match[1]:
        return '原表院系代码与输出不一致，未采用该方向'
    mode_columns = role.get('schema', {}).get('mode_columns', [])
    if not mode_columns:
        return '原表缺少明确学习方式列，未按标题或章程推断招生学习方式'
    values = {compact(rows[row_index][col]) for col in mode_columns}
    if len(values) != 1 or not values <= {'全日制', '非全日制'} or program.get('study_mode') not in values:
        return '原表学习方式整格缺失或含条件说明，未只取首行或借用全校声明'
    return None


def _exam_columns(header):
    return [header['exam']] if header.get('exam') is not None else list(header.get('slots', {}).values())


def _cell_rows(table, row_index, column):
    box = _origin(table, row_index, column)
    return [index + 1 for index in range(len(table['rows']))
            if _origin(table, index, column) == box] if box else [row_index + 1]


def _same_identity(value, previous):
    value, previous = canonical(value), canonical(previous)
    code = re.match(r'^([0-9a-z]{6}|[0-9a-z]{4})(?![0-9a-z])', value)
    before = re.match(r'^([0-9a-z]{6}|[0-9a-z]{4})(?![0-9a-z])', previous)
    return value == previous or bool(code and before and code[1] == before[1])


def _split_exam_rows(roles):
    """Reject visible page-spanning initial cells; never guess how many units exist."""
    rejected = {}
    for table_index, current in roles.items():
        previous_index = current.get('continued_from')
        if previous_index is None:
            continue
        previous = roles[previous_index]
        header = previous['schema']['header']
        columns = _exam_columns(header)
        # Repeated department/mode labels do not establish a new program.
        identity = [header[key] for key in ('major', 'code', 'name') if header.get(key) is not None]
        rows = current['table']['rows']
        start = current['data_start']
        if not columns or not identity or start >= len(rows):
            continue
        first = rows[start]['texts']
        last_index = len(previous['table']['rows']) - 1
        before = _table_rows(previous['table'])[last_index]
        if (any(column >= len(first) for column in [*columns, *identity])
                or any(compact(first[col]) and not _same_identity(first[col], before[col]) for col in identity)):
            continue
        text = '\n'.join(first[col] or '' for col in columns)
        fresh = (bool(first[header['slots'][1]]) if 1 in header.get('slots', {}) else
            bool(re.match(r'^\s*(?:①|1[.、．]|[（(]1[）)])\s*\d{3}', text)))
        if not text.strip() or fresh:
            continue
        if not any(before[col] for col in columns):
            continue
        reason = '初试单元格在相邻页仍有续格，当前未拼接并核验完整原文，未采用截断方向'
        for index, context, row_index in [(previous_index, previous, last_index), (table_index, current, start)]:
            for column in columns:
                for row in _cell_rows(context['table'], row_index, column):
                    rejected[(index, row)] = {'reason': reason, 'page': context['page'], 'table': index,
                        'row': row, 'continuation_page': current['page']}
    return rejected


def _direction_labels(text):
    """Printed top-level labels, retaining parentheses and ASCII-name boundaries."""
    text = unicodedata.normalize('NFKC', text)
    marker = re.compile(r'(?:\d{2}(?!\d)|[A-Z]\d|\d[A-Z])[ \t]*[.、．:]?(?=[\u4e00-\u9fffA-Za-z(])')
    depth, starts = 0, []
    for index, char in enumerate(text):
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                return None
        prefix = text[text.rfind('\n', 0, index) + 1:index].rstrip()
        boundary = not prefix or prefix.endswith(('；', ';'))
        if depth == 0 and boundary and marker.match(text, index):
            starts.append(index)
    if depth or starts and text[:starts[0]].strip(' \t\n\r；;、，,。'):
        return None
    return {canonical(text[start:starts[i + 1] if i + 1 < len(starts) else len(text)]).strip('；;、，,。')
            for i, start in enumerate(starts)}


def _direction_rejections(programs, roles):
    rejected, expected = {}, {}
    for table_index, role in roles.items():
        column = role.get('schema', {}).get('header', {}).get('direction')
        if column is None or role.get('blocked'):
            continue
        rows = _table_rows(role['table'])
        for index, row in enumerate(rows[role['data_start']:], role['data_start']):
            expected[(table_index, index + 1)] = _direction_labels(row[column])
    for program in programs:
        locator = program.get('source_locator', {})
        key = locator.get('table'), locator.get('row')
        if key not in expected:
            continue
        labels = expected[key]
        # A prose acronym has no independent printed code proof in this profile.
        alpha_code = re.match(r'^[A-Za-z]{2}', program.get('direction', ''))
        if not alpha_code and labels is not None and (not labels or canonical(program.get('direction', '')).strip('；;、，,。') in labels):
            continue
        role = roles[key[0]]
        column = role['schema']['header']['direction']
        for row in _cell_rows(role['table'], key[1] - 1, column):
            rejected[(key[0], row)] = {'reason': '原文方向括号未闭合或印刷编号与完整名称无法逐项对账，未采用该方向整格',
                'page': role['page'], 'table': key[0], 'row': row}
    return rejected


def parse_catalog_pdf_national_v3(layout, unit, year, document, *, project_root=PROJECT_ROOT):
    prepared, changes, roles = _prepare(layout)
    split_rows = _split_exam_rows(roles)
    parsed = parse_catalog_pdf_national_v2(prepared, unit, year, document, project_root=project_root)
    direction_rows = _direction_rejections(parsed['programs'], roles)
    programs, rejected = [], []
    for program in parsed['programs']:
        locator = program.get('source_locator', {})
        key = locator.get('table'), locator.get('row')
        split = split_rows.get(key) or direction_rows.get(key)
        issue = split['reason'] if split else _identity_issue(program, roles)
        if issue:
            rejected.append(issue)
        else:
            programs.append({**program, 'catalog_parser': PARSER})
    return {**parsed, 'programs': programs, 'catalog_format': PARSER,
        'normalization_manifest': changes, 'rejected_source_rows': [*split_rows.values(), *direction_rows.values()],
        'issues': [*parsed['issues'], *dict.fromkeys(rejected)]}
