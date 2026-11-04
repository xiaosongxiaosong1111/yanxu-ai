"""Bounded derivations of official documents; offsets refer to canonical Unicode text."""
import hashlib
import io
from pathlib import PurePosixPath
import re
import stat
import struct
import xml.etree.ElementTree as ET
import zipfile


EXTRACTION_VERSION = 'canonical-text-v1'
MAX_TEXT_CHARS = 2_000_000
MAX_PAGES = 1000
MAX_ZIP_MEMBERS = 2000
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_XML_NODES = 250_000
MAX_XML_DEPTH = 64
MAX_OFFICE_ROWS = 50_000
MAX_OFFICE_CELLS = 200_000
MAX_OFFICE_COLUMNS = 1024
OLE_MAGIC = bytes.fromhex('d0cf11e0a1b11ae1')
OFFICE_TYPES = {
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'application/vnd.ms-excel': 'xls', 'application/msword': 'doc',
}
_FORMATS = {'pdf', 'html', 'docx', 'xlsx', 'xls', 'doc', 'text'}


def _clean(value):
    if not isinstance(value, str):
        raise ValueError('Document text must be a string.')
    if any(ord(char) < 32 and char not in '\n\r\t' or ord(char) == 127 for char in value):
        raise ValueError('Document text contains unsupported control characters.')
    value.encode('utf-8')
    return value.replace('\r\n', '\n').replace('\r', '\n')


def _limit(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError('Extraction limits must be positive bounded integers.')
    return value


def _format(document):
    media = document.get('content_type', '')
    if media is None:
        media = ''
    if not isinstance(media, str):
        raise ValueError('Document media type must be a string.')
    media = media.split(';', 1)[0].lower().strip()
    declared = document.get('format')
    if declared is not None and (not isinstance(declared, str) or declared not in _FORMATS):
        raise ValueError('Unsupported document format identifier.')
    raw = document.get('raw', b'')
    if not isinstance(raw, bytes):
        raise ValueError('Original document bytes are malformed.')
    if 'raw' in document:
        validate_media(raw, media)
    if media in OFFICE_TYPES:
        return OFFICE_TYPES[media]
    if media == 'application/pdf' or raw.startswith(b'%PDF-'):
        return 'pdf'
    if media in {'text/html', 'application/xhtml+xml'}:
        return 'html'
    return declared or 'text'


def _source_pages(document, format):
    supplied = document.get('pages')
    text = document.get('text', document.get('excerpt', ''))
    if text is None:
        text = ''
    if not isinstance(text, str):
        raise ValueError('Document aggregate text must be a string.')
    if supplied is not None and not isinstance(supplied, list):
        raise ValueError('Document pages must be an ordered list.')
    if supplied:
        pages, previous = [], 0
        for item in supplied:
            if (not isinstance(item, dict) or type(item.get('page')) is not int or item['page'] <= previous
                    or not isinstance(item.get('text'), str)):
                raise ValueError('Document page identities must be positive and increasing.')
            previous = item['page']
            for key in ('sheet', 'location_kind'):
                if key in item and (not isinstance(item[key], str) or len(item[key]) > 200):
                    raise ValueError('Document logical location is malformed.')
            pages.append({key: item[key] for key in ('page', 'text', 'sheet', 'location_kind') if key in item})
        return pages
    if format == 'pdf' and isinstance(text, str) and '\f' in text:
        pieces = text.split('\f')
        if not pieces[-1].strip():
            pieces = pieces[:-1]
        return [{'page': index, 'text': value} for index, value in enumerate(pieces, 1)]
    return [{'page': 1, 'text': text}] if text else []


def _canonical_pages(pages, max_chars, max_pages):
    values, parts, length = [], [], 0
    partial = len(pages) > max_pages
    for item in pages[:max_pages]:
        text = _clean(item.get('text', ''))
        separator = '\n' if values else ''
        if length + len(separator) > max_chars:
            partial = True; break
        start = length + len(separator)
        kept = text[:max_chars - start]
        parts.extend([separator, kept]); length = start + len(kept)
        values.append({**item, 'text': kept, 'char_start': start, 'char_end': length})
        if len(kept) != len(text):
            partial = True; break
    if len(values) != len(pages):
        partial = True
    return ''.join(parts), values, partial


def _consistent_text(document, pages, format):
    text = document.get('text')
    if text in (None, '') or not document.get('pages'):
        return
    text = _clean(text.replace('\f', '\n') if format == 'pdf' and isinstance(text, str) else text)
    joined = '\n'.join(_clean(page.get('text', '')) for page in pages)
    if text.rstrip('\n') != joined.rstrip('\n'):
        raise ValueError('Aggregate text and page extraction do not agree.')


def canonical_document(document, *, max_text_chars=MAX_TEXT_CHARS, max_pages=MAX_PAGES):
    """Keep complete bounded text separately from legacy summary response limits."""
    if not isinstance(document, dict):
        raise ValueError('Document must be an object.')
    maximum = _limit(max_text_chars, MAX_TEXT_CHARS)
    page_limit = _limit(max_pages, MAX_PAGES)
    format = _format(document)
    if format != 'pdf' and '\f' in str(document.get('text', '')):
        raise ValueError('Page separators are only valid for PDF extraction.')
    source_pages = _source_pages(document, format)
    _consistent_text(document, source_pages, format)
    text, pages, truncated = _canonical_pages(source_pages, maximum, page_limit)
    missing_pages = format == 'pdf' and any(page['page'] != index for index, page in enumerate(source_pages, 1))
    excerpt = document.get('text_origin') == 'captured_excerpt' or 'text' not in document and 'excerpt' in document
    declared = document.get('extraction_state', document.get('extraction_status'))
    status = 'partial' if truncated or excerpt or missing_pages or declared == 'partial' else 'complete'
    if declared == 'unsupported' or format == 'doc':
        status = 'unsupported'
    elif format == 'pdf' and not text.strip():
        status = 'needs_ocr'
    elif not text.strip():
        status = 'partial'
    return {'text': text, 'pages': pages, 'format': format, 'extraction_status': status,
            'extractor_version': EXTRACTION_VERSION,
            'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'page_kind': 'physical' if format == 'pdf' else 'logical',
            'full_extraction': not excerpt and status == 'complete',
            'text_truncated': truncated, 'missing_pages': missing_pages, 'total_pages_seen': len(source_pages),
            'limits': {'max_text_chars': maximum, 'max_pages': page_limit}}


def validate_media(raw, media):
    """Do not let binary signatures be interpreted as visible HTML or another office type."""
    if not isinstance(raw, bytes):
        raise ValueError('Original bytes are malformed.')
    kind = OFFICE_TYPES.get(media)
    if media == 'application/pdf' and not raw.startswith(b'%PDF-'):
        raise ValueError('PDF MIME and file signature do not agree.')
    if kind in {'docx', 'xlsx'} and not raw.startswith(b'PK\x03\x04'):
        raise ValueError('Office MIME and ZIP signature do not agree.')
    if kind in {'doc', 'xls'} and not raw.startswith(OLE_MAGIC):
        raise ValueError('Legacy office MIME and binary signature do not agree.')
    if media in {'text/html', 'application/xhtml+xml'} and raw.startswith((b'%PDF-', b'PK\x03\x04', OLE_MAGIC)):
        raise ValueError('HTML MIME does not agree with the original file signature.')


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _children(node, kind):
    return [child for child in node if _local(child.tag) == kind]


def _xml(raw):
    if len(raw) > MAX_XML_BYTES or re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', raw.replace(b'\0', b''), re.I):
        raise ValueError('Office XML exceeds limits or contains declarations.')
    try:
        depth, count = 0, 0
        parser = ET.iterparse(io.BytesIO(raw), events=('start', 'end'))
        for event, _ in parser:
            depth += 1 if event == 'start' else -1
            count += event == 'start'
            if depth > MAX_XML_DEPTH or count > MAX_XML_NODES:
                raise ValueError('Office XML exceeds its structure limit.')
        return parser.root
    except ET.ParseError:
        raise ValueError('Office XML is malformed.') from None


def _package(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_MEMBERS or sum(item.file_size for item in infos) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError('Office archive exceeds the expansion limit.')
            result, seen = {}, set()
            for item in infos:
                parts = PurePosixPath(item.filename).parts
                if (not parts or item.filename.startswith('/') or '\\' in item.filename or ':' in item.filename
                        or '..' in parts or item.flag_bits & 1 or stat.S_ISLNK(item.external_attr >> 16)):
                    raise ValueError('Office archive has an unsafe member.')
                if item.filename in seen:
                    raise ValueError('Office archive has duplicate members.')
                seen.add(item.filename)
                if item.filename.endswith(('.xml', '.rels')):
                    if item.file_size > MAX_XML_BYTES:
                        raise ValueError('Office XML exceeds the member limit.')
                    with archive.open(item) as stream:
                        value = stream.read(MAX_XML_BYTES + 1)
                    if len(value) != item.file_size:
                        raise ValueError('Office archive member size is inconsistent.')
                    result[item.filename] = value
            if '[Content_Types].xml' not in result:
                raise ValueError('Office package type metadata is missing.')
            _xml(result['[Content_Types].xml'])
            return result
    except (zipfile.BadZipFile, RuntimeError, OSError, KeyError):
        raise ValueError('Office archive is malformed or unreadable.') from None


def _paragraph(node):
    if _local(node.tag) in {'del', 'instrText', 'pPr', 'rPr'}:
        return ''
    if _local(node.tag) == 'r' and any(_local(child.tag) == 'vanish' for child in node.iter()):
        return ''
    kind = _local(node.tag)
    if kind == 't': return node.text or ''
    if kind == 'tab': return '\t'
    if kind in {'br', 'cr'}: return '\n'
    return ''.join(_paragraph(child) for child in node)


def _word_blocks(container):
    for node in container:
        kind = _local(node.tag)
        if kind in {'p', 'tbl'}:
            yield node
        elif kind == 'sdt':
            content = _children(node, 'sdtContent')
            if len(content) == 1:
                yield from _word_blocks(content[0])
            else:
                yield None
        elif kind not in {'sectPr', 'tcPr', 'bookmarkStart', 'bookmarkEnd', 'permStart', 'permEnd', 'proofErr'}:
            yield None


def _word_cell(cell):
    blocks = list(_word_blocks(cell))
    partial = any(block is None or _local(block.tag) != 'p' for block in blocks)
    return '\n'.join(_paragraph(block) for block in blocks if block is not None and _local(block.tag) == 'p'), partial


def _docx(package):
    if 'word/document.xml' not in package:
        raise ValueError('Word MIME does not match this package.')
    root = _xml(package['word/document.xml'])
    body = next((node for node in root.iter() if _local(node.tag) == 'body'), None)
    if body is None:
        raise ValueError('Word document body is missing.')
    omitted = any(re.fullmatch(r'word/(?:footnotes|endnotes|header\d+|footer\d+)\.xml', name) for name in package)
    lines, rows, cells, partial, clipped = [], 0, 0, False, False
    for node in _word_blocks(body):
        if node is None:
            partial = True; continue
        if _local(node.tag) == 'p':
            lines.append(_paragraph(node))
        elif _local(node.tag) == 'tbl':
            partial = partial or any(_local(child.tag) not in {'tr', 'tblPr', 'tblGrid'} for child in node)
            for row in _children(node, 'tr'):
                partial = partial or any(_local(child.tag) not in {'tc', 'trPr'} for child in row)
                content = [_word_cell(cell) for cell in _children(row, 'tc')]
                values = [value for value, _ in content]
                partial = partial or any(missing for _, missing in content)
                rows += 1; cells += len(values)
                if rows > MAX_OFFICE_ROWS or cells > MAX_OFFICE_CELLS:
                    partial = clipped = True; break
                lines.append('\t'.join(values))
        if clipped: break
    return [{'page': 1, 'text': '\n'.join(lines), 'location_kind': 'document_paragraph'}], partial or omitted


def _attribute(node, key):
    return next((value for name, value in node.attrib.items() if _local(name) == key), '')


def _sheet_paths(package):
    if 'xl/workbook.xml' not in package or 'xl/_rels/workbook.xml.rels' not in package:
        raise ValueError('Spreadsheet MIME does not match this package.')
    relations = {_attribute(node, 'Id'): node for node in _xml(package['xl/_rels/workbook.xml.rels'])}
    values, hidden = [], False
    for sheet in _xml(package['xl/workbook.xml']).iter():
        if _local(sheet.tag) != 'sheet': continue
        if _attribute(sheet, 'state').lower() in {'hidden', 'veryhidden'}:
            hidden = True; continue
        relation = relations.get(_attribute(sheet, 'id'))
        if relation is None or _attribute(relation, 'TargetMode').lower() == 'external':
            raise ValueError('Spreadsheet sheet target is missing or external.')
        target = _attribute(relation, 'Target')
        if '\\' in target or ':' in target or '..' in PurePosixPath(target).parts:
            raise ValueError('Spreadsheet sheet target is unsafe.')
        target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        if not target.startswith('xl/worksheets/') or target not in package:
            raise ValueError('Spreadsheet sheet target is unavailable.')
        values.append((_attribute(sheet, 'name'), target))
    return values, hidden


def _column(reference):
    match = re.fullmatch(r'([A-Z]{1,3})[1-9]\d{0,6}', reference)
    if not match:
        raise ValueError('Spreadsheet cell reference is malformed.')
    value = 0
    for char in match[1]: value = value * 26 + ord(char) - ord('A') + 1
    return value


def _cell_text(cell, shared):
    kind = _attribute(cell, 't')
    if kind == 'inlineStr':
        return ''.join(node.text or '' for node in cell.iter() if _local(node.tag) == 't')
    value = next((node.text or '' for node in cell if _local(node.tag) == 'v'), '')
    if kind == 's':
        if not re.fullmatch(r'\d{1,7}', value) or int(value) >= len(shared):
            raise ValueError('Spreadsheet shared string reference is invalid.')
        return shared[int(value)]
    return value


def _hidden_columns(root):
    boundaries = [0] * (MAX_OFFICE_COLUMNS + 2)
    for node in root.iter():
        if _local(node.tag) != 'col' or _attribute(node, 'hidden') not in {'1', 'true'}:
            continue
        left, right = _attribute(node, 'min'), _attribute(node, 'max')
        if not re.fullmatch(r'\d{1,5}', left) or not re.fullmatch(r'\d{1,5}', right):
            raise ValueError('Spreadsheet hidden column range is malformed.')
        first, last = int(left), int(right)
        if not 1 <= first <= last <= 16384:
            raise ValueError('Spreadsheet hidden column range is unsupported.')
        if first <= MAX_OFFICE_COLUMNS:
            boundaries[first] += 1
            boundaries[min(last, MAX_OFFICE_COLUMNS) + 1] -= 1
    hidden, active = set(), 0
    for column, delta in enumerate(boundaries):
        active += delta
        if active: hidden.add(column)
    return hidden


def _sheet_text(root, shared, remaining_cells):
    hidden = _hidden_columns(root)
    lines, count, partial = [], 0, bool(hidden)
    for ordinal, row in enumerate((node for node in root.iter() if _local(node.tag) == 'row'), 1):
        if ordinal > MAX_OFFICE_ROWS:
            partial = True; break
        if _attribute(row, 'hidden') in {'1', 'true'}:
            partial = True; continue
        cells = _children(row, 'c')
        if count + len(cells) > remaining_cells:
            partial = True; break
        values, prior = [], 0
        for cell in cells:
            column = _column(_attribute(cell, 'r'))
            if column > MAX_OFFICE_COLUMNS or column <= prior:
                raise ValueError('Spreadsheet column range is unsupported or unordered.')
            values.extend([''] * (column - prior - 1))
            values.append('' if column in hidden else _cell_text(cell, shared)); prior = column
            if _children(cell, 'f') and not _children(cell, 'v'): partial = True
        lines.append('\t'.join(values)); count += len(cells)
    return '\n'.join(lines), count, partial


def _xlsx(package):
    shared = []
    if 'xl/sharedStrings.xml' in package:
        shared = [''.join(part.text or '' for part in node.iter() if _local(part.tag) == 't')
                  for node in _xml(package['xl/sharedStrings.xml']) if _local(node.tag) == 'si']
    targets, hidden = _sheet_paths(package)
    pages, cells, partial = [], 0, hidden or len(targets) > MAX_PAGES
    for name, target in targets[:MAX_PAGES]:
        text, count, clipped = _sheet_text(_xml(package[target]), shared, MAX_OFFICE_CELLS - cells)
        cells += count; partial = partial or clipped
        pages.append({'page': len(pages) + 1, 'text': text, 'sheet': name, 'location_kind': 'worksheet'})
        if clipped or cells >= MAX_OFFICE_CELLS:
            partial = partial or len(pages) < len(targets); break
    return pages, partial


def _xls(raw):
    try:
        import xlrd
        from xlrd.compdoc import CompDocError
    except ImportError:
        return [], False, 'unsupported'
    try:
        book = xlrd.open_workbook(file_contents=raw, on_demand=True)
    except (xlrd.XLRDError, CompDocError, OSError, ValueError, IndexError, struct.error):
        raise ValueError('Legacy spreadsheet is unreadable.') from None
    pages, count, partial = [], 0, book.nsheets > MAX_PAGES
    try:
        for index in range(min(book.nsheets, MAX_PAGES)):
            sheet = book.sheet_by_index(index)
            if sheet.ncols > MAX_OFFICE_COLUMNS:
                raise ValueError('Spreadsheet column range exceeds the bound.')
            available = min(sheet.nrows, MAX_OFFICE_ROWS, (MAX_OFFICE_CELLS - count) // max(1, sheet.ncols))
            text = '\n'.join('\t'.join(str(value) for value in sheet.row_values(row)) for row in range(available))
            count += available * sheet.ncols; partial = partial or available < sheet.nrows
            pages.append({'page': index + 1, 'text': text, 'sheet': sheet.name, 'location_kind': 'worksheet'})
            if partial: break
    finally:
        book.release_resources()
    return pages, partial, 'partial' if partial else 'complete'


def extract_office_document(raw, media):
    """Read supported Office containers without opening package links or running formulas."""
    validate_media(raw, media)
    kind = OFFICE_TYPES.get(media)
    if kind is None:
        raise ValueError('Office media type is not supported.')
    state = 'complete'
    if kind == 'doc':
        pages, partial, state = [], False, 'unsupported'
    elif kind == 'xls':
        pages, partial, state = _xls(raw)
    else:
        pages, partial = (_docx if kind == 'docx' else _xlsx)(_package(raw))
    return {'format': kind, 'pages': pages, 'text': '\n'.join(page['text'] for page in pages),
            'extraction_state': 'partial' if partial else state}
