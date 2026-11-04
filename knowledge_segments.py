"""Exact-span structural segments derived from one canonical extraction."""
from bisect import bisect_left, bisect_right
import hashlib
import re

SEGMENT_VERSION = 'structure-v1'
MAX_SEGMENTS = 20000
_FORMATS = {'pdf', 'html', 'docx', 'xlsx', 'xls', 'doc', 'text'}
_STATUSES = {'complete', 'partial', 'needs_ocr', 'unsupported'}
_EXCEPTION = re.compile(r'^(?:但|但是|除|其中|惟|不含|仅限|例外|特别说明)')
_COLUMNS = re.compile(r'专业代码|专业名称|院系|学制|学费|收费标准|总分|单科|政治|外语|英语|业务课|专业课|考试科目|备注|招生人数|研究方向|类别|培养方式|学位')
_END = re.compile(r'[。；！？.!?;]$')
_COLLEGE = re.compile(r'^(?:(?:\d{1,4}|[一二三四五六七八九十]+[、．.])\s*)?'
                      r'[\u3400-\u9fffA-Za-z·& -]{1,60}(?:学院|学部|研究院|研究所)'
                      r'(?:[（(][^（）()\t]{2,60}[）)])?$')
_CLAUSE = re.compile(r'考生|本专业|本学院|须|必须|不得|接受|接收|仅限|限于|适用于|毕业于|'
                     r'报名|报考|填报|提交|以.+为准')
_LINE_BREAKS = '\r\n\f\x85\u2028\u2029'


def _validated(extraction, max_chars):
    if not isinstance(extraction, dict) or type(max_chars) is not int or not 64 <= max_chars <= 12000:
        raise ValueError('Invalid extraction or segment character limit.')
    text = extraction.get('text')
    if (not isinstance(text, str) or len(text) > 2000000 or
            any(ord(char) < 32 and char not in '\r\n\t\f' for char in text)):
        raise ValueError('Extraction text must be bounded Unicode text.')
    status = extraction.get('extraction_status', 'complete')
    format = extraction.get('format', 'text')
    if status not in _STATUSES or format not in _FORMATS:
        raise ValueError('Invalid extraction format or status.')
    expected = extraction.get('text_sha256')
    if expected is not None and expected != hashlib.sha256(text.encode('utf-8')).hexdigest():
        raise ValueError('Extraction text does not match its SHA.')
    pages = _validated_pages(text, extraction.get('pages', []))
    return text, pages, status


def _validated_pages(text, pages):
    if not isinstance(pages, list) or len(pages) > 1000:
        raise ValueError('Page mapping must be a bounded ordered list.')
    previous_end, previous_number = 0, 0
    result = []
    for page in pages:
        if not isinstance(page, dict): raise ValueError('Invalid page mapping.')
        number, start, end = (page.get(key) for key in ('page', 'char_start', 'char_end'))
        if (type(number) is not int or number <= previous_number or
                type(start) is not int or type(end) is not int or
                not previous_end <= start <= end <= len(text)):
            raise ValueError('Page coordinates are invalid or overlap.')
        if page.get('text') != text[start:end] or text[previous_end:start].strip():
            raise ValueError('Page text does not match its exact extraction coordinates.')
        result.append({'page': number, 'char_start': start, 'char_end': end})
        previous_end, previous_number = end, number
    if pages and text[previous_end:].strip():
        raise ValueError('Page mapping leaves extraction content unlocated.')
    return result


def _heading_level(value):
    if len(value) > 120 or '\t' in value or re.search(r'[。；！？]', value): return None
    if _COLLEGE.fullmatch(value) and not _CLAUSE.search(value): return 1
    if re.match(r'^\d{6}\s*[^\d\s].*$', value) and len(re.findall(r'\d+', value)) == 1: return 2
    if re.fullmatch(r'[^\d。；]{2,40}[（(]\d{6}[）)]', value): return 2
    if re.fullmatch(r'(?:非全日制|全日制)?(?:专业学位|学术学位)?(?:硕士|博士)(?:研究生)?(?:收费标准)?', value): return 2
    markdown = re.match(r'^(#{1,6})\s+\S', value)
    if markdown: return 2 + len(markdown[1])
    if re.match(r'^第[一二三四五六七八九十百零〇\d]+[章节篇部分]', value): return 3
    if re.match(r'^[一二三四五六七八九十百]+[、．.]', value): return 3
    if re.match(r'^[（(][一二三四五六七八九十\d]+[）)]', value): return 4
    if re.match(r'^\d{1,2}[、．.]\s*\D', value): return 5
    return None


def _noise(value):
    contact = re.match(r'^(?:联系电话|咨询电话|邮编|邮政编码|版权所有|Copyright|传真)\s*[:：\d]', value, re.I)
    if contact and not re.search(r'须|必须|不得|申请|报名|材料|提交|资格', value): return True
    navigation = re.sub(r'首页|招生信息|联系我们|返回顶部|上一页|下一页|更多|[\s>|/·]+', '', value)
    return not navigation


def _table_header(value):
    if len(value) > 600 or len(set(_COLUMNS.findall(value))) < 2: return False
    residue = _COLUMNS.sub('', value)
    return re.fullmatch(r'[\s/|:：、（）()\[\]【】·一二三四五六七八九十0-9%％]*', residue) is not None


def _line_kind(value):
    if _noise(value): return 'boilerplate', None
    level = _heading_level(value)
    if level is not None: return 'heading', level
    if _table_header(value): return 'table_header', None
    tabular = '\t' in value and len(value.split('\t')) >= 2
    numeric_row = len(re.findall(r'(?<!\S)\d+(?:\.\d+)?(?!\S)', value)) >= 2
    if not _END.search(value) and (tabular or numeric_row and re.search(r'[\u3400-\u9fff]', value)):
        return 'table_row', None
    return 'paragraph', None


def _lines(text):
    offset = 0
    for raw in text.splitlines(keepends=True):
        content = raw.rstrip(_LINE_BREAKS)
        tabular = '\t' in content and bool(content.strip())
        value = content if tabular else content.strip()
        start = offset if tabular else offset + len(content) - len(content.lstrip())
        end = offset + len(content) if tabular else offset + len(content.rstrip())
        offset += len(raw)
        kind, level = _line_kind(value) if value else ('blank', None)
        yield {'char_start': start, 'char_end': end, 'text': value, 'kind': kind, 'level': level}


def _program_title(left, right, text):
    code, name = (left, right) if re.fullmatch(r'\d{6}', left['text']) else (right, left)
    if (not re.fullmatch(r'\d{6}', code['text']) or name['kind'] != 'paragraph' or
            not re.fullmatch(r'[\u3400-\u9fffA-Za-z·（）() -]{2,40}', name['text']) or
            re.search(r'须|不得|要求|提交|报考|申请|材料', name['text'])):
        return None
    return {'char_start': left['char_start'], 'char_end': right['char_end'],
            'text': text[left['char_start']:right['char_end']], 'kind': 'heading', 'level': 2}


def _structured_lines(text):
    pending = None
    for following in _lines(text):
        if pending is None:
            pending = following
            continue
        joined = _program_title(pending, following, text)
        if joined is not None:
            yield joined
            pending = None
        else:
            yield pending
            pending = following
    if pending is not None: yield pending


def _blocks(text):
    pending = None
    gap = False
    for line in _structured_lines(text):
        if line['kind'] == 'blank':
            gap = True
            continue
        if line['kind'] != 'paragraph':
            if pending is not None: yield pending
            pending = None
            yield line
        else:
            if pending is not None and gap and not _EXCEPTION.match(line['text']):
                yield pending
                pending = None
            pending = ({**line} if pending is None else
                       {**pending, 'char_end': line['char_end']})
        gap = False
    if pending is not None: yield pending


def _split_range(text, start, end, limit, preserve_cells=False):
    position = start
    while position < end:
        stop = min(position + limit, end)
        if stop < end:
            boundaries = list(re.finditer(r'[。；！？.!?;\n]', text[position:stop]))
            if boundaries and boundaries[-1].end() >= limit // 2:
                stop = position + boundaries[-1].end()
        left = position if preserve_cells else position + len(text[position:stop]) - len(text[position:stop].lstrip())
        right = stop if preserve_cells else position + len(text[position:stop].rstrip())
        if left < right: yield left, right
        position = stop


def _span(block, role):
    return {key: block[key] for key in ('char_start', 'char_end', 'text')} | {'role': role}


def _page_bounds(index, start, end):
    pages, starts, ends = index
    if not pages: return None, None
    first = bisect_right(ends, start)
    last = bisect_left(starts, end) - 1
    if first > last or first >= len(pages): return None, None
    return pages[first]['page'], pages[last]['page']


def _block_ranges(text, page_index, block, limit, physical_pages):
    start, end = block['char_start'], block['char_end']
    ranges = [(start, end)]
    pages, starts, ends = page_index
    if physical_pages and pages:
        first, last = bisect_right(ends, start), bisect_left(starts, end)
        ranges = [(max(start, page['char_start']), min(end, page['char_end']))
                  for page in pages[first:last]]
    preserve_cells = '\t' in block['text'] and block['kind'] in {'table_header', 'table_row'}
    for left, right in ranges:
        yield from _split_range(text, left, right, limit, preserve_cells)


def _segment(text, pages, block, start, end, ordinal, stack, table, limit):
    contexts = [_span(item['block'], 'heading') for item in stack]
    if table is not None and block['kind'] == 'table_row':
        contexts += [_span(table, 'table_header')]
    page_start, page_end = _page_bounds(pages, start, end)
    return {'ordinal': ordinal, 'kind': block['kind'], 'char_start': start, 'char_end': end,
            'text': text[start:end], 'page_start': page_start, 'page_end': page_end,
            'section_path': [item['block']['text'] for item in stack],
            'parent_ordinal': stack[-1]['ordinal'] if stack else None,
            'context_spans': contexts, 'search_text': '\n'.join([item['text'] for item in contexts] + [text[start:end]]),
            'searchable': block['kind'] not in {'heading', 'table_header', 'boilerplate'},
            'table_context_unresolved': block['kind'] == 'table_row' and table is None,
            'row_complete': (start == block['char_start'] and end == block['char_end'])
                            if block['kind'] == 'table_row' else None,
            'continues_previous': start > block['char_start'],
            'continues_next': end < block['char_end'], 'max_chars': limit}


def _collect_segments(text, pages, limit, physical_pages=False):
    result, stack, table = [], [], None
    page_index = (pages, [page['char_start'] for page in pages], [page['char_end'] for page in pages])
    for block in _blocks(text):
        block = {**block, 'text': text[block['char_start']:block['char_end']]}
        if block['kind'] == 'heading':
            stack = [item for item in stack if item['level'] < block['level']]
            table = None
        first_ordinal = len(result)
        for start, end in _block_ranges(text, page_index, block, limit, physical_pages):
            row = _segment(text, page_index, block, start, end, len(result), stack, table, limit)
            if block['kind'] == 'heading':
                row = {**row, 'section_path': [item['block']['text'] for item in stack] + [block['text']]}
            result.append(row)
            if len(result) > MAX_SEGMENTS: raise ValueError('Extraction has too many structural segments.')
        if block['kind'] == 'heading' and len(result) > first_ordinal:
            stack = stack + [{'block': block, 'level': block['level'], 'ordinal': first_ordinal}]
        elif block['kind'] == 'table_header': table = block
    return [{**row, 'previous_ordinal': index - 1 if index else None,
             'next_ordinal': index + 1 if index + 1 < len(result) else None}
            for index, row in enumerate(result)]


def build_segments(extraction, *, max_chars=1800):
    text, pages, status = _validated(extraction, max_chars)
    if status in {'needs_ocr', 'unsupported'} or not text.strip(): return []
    return _collect_segments(text, pages, max_chars, physical_pages=extraction.get('format') == 'pdf')
