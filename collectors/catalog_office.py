"""Extract original DOCX/XLSX table cells without running Office or formulas."""
import io
import importlib.util
import math
import sys
from html import escape
from pathlib import Path, PurePosixPath
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

from .documents import DocumentError
from .catalog_html import parse_catalog_html

PARSER = 'office_table_v1'
MAX_XML = 32 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_ROWS, MAX_COLS, MAX_CELLS = 20000, 256, 250000
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def _xml(archive, name):
    try:
        with archive.open(name) as stream: raw = stream.read(MAX_XML + 1)
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise DocumentError('Office原文缺少可读取的XML成员') from error
    if len(raw) > MAX_XML or re.search(br'<!DOCTYPE|<!ENTITY', raw, re.I):
        raise DocumentError('Office XML过大或包含不允许的实体定义')
    try: return ET.fromstring(raw)
    except ET.ParseError as error: raise DocumentError('Office XML原文格式错误') from error


def _paragraph(node):
    return ''.join(element.text or '' for element in node.iter() if element.tag == W + 't')


def _docx_table(node):
    rows, vertical, cells = [], {}, 0
    for row in node.findall(W + 'tr'):
        values, column = [], 0
        for cell in row.findall(W + 'tc'):
            span = cell.find('./' + W + 'tcPr/' + W + 'gridSpan')
            count = int(span.get(W + 'val', '1')) if span is not None else 1
            if count < 1 or column + count > MAX_COLS: raise DocumentError('Word表格合并范围越界')
            text = '\n'.join(_paragraph(p) for p in cell.findall(W + 'p'))
            merge = cell.find('./' + W + 'tcPr/' + W + 'vMerge')
            if merge is not None and merge.get(W + 'val') != 'restart':
                if column not in vertical: raise DocumentError('Word表格纵向合并缺少原始单元格')
                text = vertical[column]
            vertical = {**vertical, column: text} if merge is not None else {k: v for k, v in vertical.items() if k != column}
            values.append((text, count)); column += count; cells += count
        rows.append(values)
        if len(rows) > MAX_ROWS or cells > MAX_CELLS: raise DocumentError('Word表格超过解析上限')
    return _table(rows)


def _table(rows):
    return '<table>' + ''.join('<tr>' + ''.join('<td' + (f' colspan="{span}"' if span != 1 else '') + '>' + escape(text) + '</td>' for text, span in row) + '</tr>' for row in rows) + '</table>'


def _docx(archive):
    root = _xml(archive, 'word/document.xml')
    body = root.find(W + 'body')
    if body is None: raise DocumentError('Word原文缺少正文')
    fragments, titled = [], False
    for node in body:
        if node.tag == W + 'p':
            text = _paragraph(node)
            if not text.strip(): continue
            tag = 'h1' if not titled else 'p'
            fragments.append('<' + tag + '>' + escape(text) + '</' + tag + '>'); titled = True
        elif node.tag == W + 'tbl': fragments.append(_docx_table(node))
    return ''.join(fragments)


def _coordinate(value):
    match = re.fullmatch(r'([A-Z]{1,3})([1-9]\d*)', value or '')
    if not match: raise DocumentError('Excel单元格坐标无效')
    column = 0
    for letter in match[1]: column = column * 26 + ord(letter) - 64
    row = int(match[2])
    if row > MAX_ROWS or column > MAX_COLS: raise DocumentError('Excel单元格超出解析范围')
    return row, column


def _sheet(archive, path, shared, formats):
    root = _xml(archive, path)
    if any(node.get('hidden', '').lower() in {'1', 'true'} for node in root.findall('./' + S + 'cols/' + S + 'col') + root.findall('./' + S + 'sheetData/' + S + 'row')):
        raise DocumentError('Excel包含隐藏行或列，需核对原始公开显示范围')
    entries, seen = [], set()
    for cell in root.findall('.//' + S + 'sheetData/' + S + 'row/' + S + 'c'):
        coordinate = _coordinate(cell.get('r'))
        if coordinate in seen: raise DocumentError('Excel存在重复单元格坐标')
        seen.add(coordinate)
        if cell.find(S + 'f') is not None: raise DocumentError('Excel包含公式，不能把缓存值视为官方确定数据')
        kind = cell.get('t')
        value = cell.findtext(S + 'v', '')
        if kind == 's':
            if not value.isdigit() or int(value) >= len(shared): raise DocumentError('Excel共享字符串引用越界')
            value = shared[int(value)]
        elif kind == 'inlineStr': value = ''.join(e.text or '' for e in cell.findall('.//' + S + 't'))
        elif kind not in {None, 'n', 'str'}: raise DocumentError('Excel含日期、布尔或错误单元格，不能当作目录文字')
        elif kind != 'str' and value:
            style = cell.get('s', '0')
            if not style.isdigit() or int(style) >= len(formats): raise DocumentError('Excel数字样式引用越界')
            value = _number_display(float(value), formats[int(style)])
        entries.append((coordinate, value))
        if len(entries) > MAX_CELLS: raise DocumentError('Excel表格超过解析上限')
    values = dict(entries)
    expanded, merged = [], set()
    for merge in root.findall('./' + S + 'mergeCells/' + S + 'mergeCell'):
        coordinates = merge.get('ref', '').split(':')
        if len(coordinates) != 2: raise DocumentError('Excel合并范围格式无效')
        (first_row, first_col), (last_row, last_col) = map(_coordinate, coordinates)
        if last_row < first_row or last_col < first_col or (last_row - first_row + 1) * (last_col - first_col + 1) > MAX_CELLS:
            raise DocumentError('Excel合并范围无效或过大')
        text = values.get((first_row, first_col), '')
        for row in range(first_row, last_row + 1):
            for col in range(first_col, last_col + 1):
                coordinate = (row, col)
                if coordinate in merged: raise DocumentError('Excel存在重叠合并区域')
                merged.add(coordinate); expanded.append((coordinate, text))
                if len(expanded) > MAX_CELLS: raise DocumentError('Excel合并区域累计超过解析上限')
    values = dict([*entries, *expanded])
    if not values: return '<table></table>'
    max_row, max_col = max(r for r, _ in values), max(c for _, c in values)
    if max_row * max_col > MAX_CELLS: raise DocumentError('Excel稀疏范围超过解析上限')
    return _table([[(values.get((r, c), ''), 1) for c in range(1, max_col + 1)] for r in range(1, max_row + 1)])


def _xlsx(archive):
    workbook = _xml(archive, 'xl/workbook.xml')
    relations = _xml(archive, 'xl/_rels/workbook.xml.rels')
    targets = {node.get('Id'): node.get('Target') for node in relations if node.get('TargetMode') != 'External'}
    shared = []
    if 'xl/sharedStrings.xml' in archive.namelist():
        shared = [''.join(e.text or '' for e in item.iter(S + 't')) for item in _xml(archive, 'xl/sharedStrings.xml')]
    formats = ['General']
    if 'xl/styles.xml' in archive.namelist():
        styles = _xml(archive, 'xl/styles.xml')
        numbered = {0: 'General', 1: '0', 2: '0.00', 3: '#,##0', 4: '#,##0.00', 49: '@',
                    **{int(node.get('numFmtId')): node.get('formatCode', '') for node in styles.findall('./' + S + 'numFmts/' + S + 'numFmt')}}
        formats = [numbered.get(int(node.get('numFmtId', '0')), '') for node in styles.findall('./' + S + 'cellXfs/' + S + 'xf')]
        if not formats: formats = ['General']
    fragments, total_cells = [], 0
    sheets = workbook.findall('./' + S + 'sheets/' + S + 'sheet')
    if len(sheets) > 128: raise DocumentError('Excel工作表数量超过解析上限')
    for sheet in workbook.findall('./' + S + 'sheets/' + S + 'sheet'):
        if sheet.get('state', 'visible') != 'visible': raise DocumentError('Excel包含隐藏工作表，需核对原始公开显示范围')
        target = targets.get(sheet.get(R + 'id'))
        if not target or '://' in target: raise DocumentError('Excel工作表不是可读取的本地成员')
        path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
        if not path.startswith('xl/') or '..' in PurePosixPath(path).parts: raise DocumentError('Excel工作表关系越界')
        html = _sheet(archive, path, shared, formats)
        total_cells += html.count('<td')
        if total_cells > MAX_CELLS: raise DocumentError('Excel所有工作表累计超过单元格上限')
        fragments.append('<h1>' + escape(sheet.get('name', '')) + '</h1>' + html)
    return ''.join(fragments)


def _xlrd_dependency():
    try:
        import xlrd
        return xlrd
    except ImportError:
        path = Path(__file__).resolve().parent.parent / '.runtime-deps/xlrd/__init__.py'
        if not path.is_file(): raise DocumentError('旧版XLS需要项目隔离依赖xlrd==2.0.2，请按运行文档安装')
        spec = importlib.util.spec_from_file_location('xlrd', path, submodule_search_locations=[str(path.parent)])
        module = importlib.util.module_from_spec(spec)
        sys.modules['xlrd'] = module
        spec.loader.exec_module(module)
        return module


def _number_display(value, pattern):
    if not math.isfinite(value): raise DocumentError('Excel数值不是有限数')
    pattern = re.sub(r'_.', '', pattern.split(';')[0]).strip()
    if pattern.lower() in {'general', '@'}: return str(int(value)) if value.is_integer() else str(value)
    if re.fullmatch(r'0{1,12}', pattern) and value.is_integer(): return f'{int(value):0{len(pattern)}d}'
    match = re.fullmatch(r'([#0,]+)(?:\.(0{1,6}))?', pattern)
    if match:
        decimals = len(match[2] or '')
        return format(value, (',' if ',' in match[1] else '') + '.' + str(decimals) + 'f')
    raise DocumentError('Excel存在尚未适配的数字显示格式，不能猜测编码或显示值')


def _xls(raw):
    xlrd = _xlrd_dependency()
    try:
        book = xlrd.open_workbook(file_contents=raw, formatting_info=True, on_demand=True,
                                  logfile=io.StringIO(), ignore_workbook_corruption=False)
        if book.nsheets > 128: raise DocumentError('Excel工作表数量超过解析上限')
        fragments, total = [], 0
        for sheet in book.sheets():
            if sheet.visibility or any(info.hidden for info in sheet.rowinfo_map.values()) or any(info.hidden for info in sheet.colinfo_map.values()):
                raise DocumentError('Excel包含隐藏工作表、行或列，需核对原始公开显示范围')
            total += sheet.nrows * sheet.ncols
            if sheet.nrows > MAX_ROWS or sheet.ncols > MAX_COLS or total > MAX_CELLS:
                raise DocumentError('Excel表格超过解析上限')
            rows = []
            for row_index in range(sheet.nrows):
                values = []
                for col_index in range(sheet.ncols):
                    cell = sheet.cell(row_index, col_index)
                    value = cell.value
                    if cell.ctype == xlrd.XL_CELL_NUMBER:
                        style = book.xf_list[cell.xf_index]
                        value = _number_display(value, book.format_map[style.format_key].format_str)
                    elif cell.ctype not in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_TEXT, xlrd.XL_CELL_BLANK}:
                        raise DocumentError('Excel含日期、布尔或错误单元格，不能当作目录文字')
                    values.append(str(value))
                rows.append(values)
            for first_row, last_row, first_col, last_col in sheet.merged_cells:
                if first_row < 0 or first_col < 0 or last_row > sheet.nrows or last_col > sheet.ncols:
                    raise DocumentError('Excel合并范围越界')
                value = rows[first_row][first_col]
                for row_index in range(first_row, last_row):
                    for col_index in range(first_col, last_col): rows[row_index][col_index] = value
            title = next((cell for row in rows[:5] for cell in row if re.search(r'20\d{2}', cell) and '招生' in cell and '目录' in cell), sheet.name)
            fragments.append('<h1>' + escape(title) + '</h1>' + _table([[(cell, 1) for cell in row] for row in rows]))
        book.release_resources()
        return ''.join(fragments)
    except DocumentError: raise
    except (xlrd.XLRDError, xlrd.compdoc.CompDocError, ValueError, TypeError, OSError, IndexError, KeyError) as error:
        raise DocumentError('旧版Excel原文无法可靠解析') from error


def extract_office_html(raw):
    if isinstance(raw, bytes) and raw.startswith(bytes.fromhex('d0cf11e0a1b11ae1')) and len(raw) <= MAX_XML:
        return _xls(raw)
    if not isinstance(raw, bytes) or not raw.startswith(b'PK') or len(raw) > MAX_XML:
        raise DocumentError('仅支持无加密DOCX/XLSX；旧版DOC/XLS二进制原文尚未适配')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len({entry.filename for entry in entries}) != len(entries): raise DocumentError('Office压缩包存在重复成员名')
            if len(entries) > 4096 or sum(entry.file_size for entry in entries) > MAX_TOTAL:
                raise DocumentError('Office压缩包超过解析上限')
            for entry in entries:
                parts = PurePosixPath(entry.filename).parts
                if entry.flag_bits & 1 or entry.filename.startswith(('/', '\\')) or '..' in parts or '\\' in entry.filename or 'vbaproject' in entry.filename.lower():
                    raise DocumentError('Office压缩包包含加密、越界路径或宏')
                if entry.file_size > MAX_XML: raise DocumentError('Office成员超过解析上限')
            if 'word/document.xml' in archive.namelist(): return _docx(archive)
            if 'xl/workbook.xml' in archive.namelist(): return _xlsx(archive)
    except DocumentError: raise
    except (zipfile.BadZipFile, ValueError, OSError, OverflowError) as error:
        raise DocumentError('Office原文无法安全解析') from error
    raise DocumentError('Office原文缺少Word正文或Excel工作簿')


def parse_catalog_office(raw, unit, year, document):
    parsed = parse_catalog_html(extract_office_html(raw), unit, year, document)
    return {**parsed, 'catalog_format': PARSER,
            'programs': [{**program, 'catalog_parser': PARSER} for program in parsed['programs']]}
