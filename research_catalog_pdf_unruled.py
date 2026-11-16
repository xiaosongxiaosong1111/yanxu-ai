"""Resolve explicit exam blocks in catalog tables with vertically merged cells."""
import re

from collectors.catalog_pdf_sectioned import _same_page_refs, _subject_rows
from collectors.catalog_strict_syntax import parse_strict_directions
from collectors.catalogs import _program, compact
from service_verification import ordinary_catalog_scope


_HEADERS = ('学科、专业名称(代码)、研究方向', '指导教师', '指导教师职称', '考试科目')
_FACULTY = re.compile(r'\d{3}[\u3400-\u9fff]{1,50}(?:学院|系|部|中心|研究院|研究所)')
_MAJOR_LABEL = re.compile(r'^(?:学科|专业)(?:\((?:专业|学科)\))?[:：]')
_DEGREE = r'\((学术型|学术学位|专业型|专业学位)\)'
_MAJOR_NAME_FIRST = re.compile(r'([\u3400-\u9fff]{1,35})\(([0-9A-Z]{6})\)'+_DEGREE)
_MAJOR_CODE_FIRST = re.compile(r'([0-9A-Z]{6})([\u3400-\u9fff]{1,35})'+_DEGREE)
_DIRECTION = re.compile(r'(?:\d{2}|\d[A-Z]|[A-Z]\d)[\u3400-\u9fff].{1,75}')
_SPECIAL = re.compile(r'单独考试|单考|(?:仅|只)(?:招收|接收)(?:推荐免试|推免)|不(?:招收|接收)统考')
_GROUP_HEADERS = ('专业代码、名称及研究方向','学习方式及招生人数','考试科目代码及名称','备注')
_GROUP_TITLE = re.compile(r'20\d{2}年.{0,40}硕士.{0,80}(?:招生(?:专业)?目录|专业目录)')
_GROUP_FACULTY = re.compile(r'(?:\d{3})?[\u3400-\u9fff]{1,50}(?:学院|系|部|中心|研究院|研究所)')
_GROUP_MAJOR = re.compile(r'(\d{4}[0-9A-Z]{2})([\u3400-\u9fff][^;；\d]{0,69}?)(?:\((学术学位|专业学位)\))?')
_GROUP_COUNT = re.compile(r'\d{1,5}人')


def _major(label):
    suffix = _MAJOR_LABEL.sub('',label,count=1)
    if match := _MAJOR_NAME_FIRST.fullmatch(suffix):
        name, code, degree = match.groups()
    elif match := _MAJOR_CODE_FIRST.fullmatch(suffix):
        code, name, degree = match.groups()
    else:
        return None
    degree_type = '学术学位' if degree.startswith('学术') else '专业学位'
    return code, name, degree_type


def _frame(page, raw, expected_title):
    leading = next((line.strip() for line in raw.splitlines() if line.strip()),'')
    if compact(leading) != expected_title or not ordinary_catalog_scope(leading):
        return None
    matches = []
    for table_index, table in enumerate(page.find_tables(),1):
        rows = table.extract(x_tolerance=2,y_tolerance=3)
        if not rows or tuple(compact(value) for value in rows[0]) != _HEADERS:
            continue
        if len(rows) != 2 or len(table.rows) != 2:
            continue
        boxes = table.rows[1].cells
        if len(boxes) != len(_HEADERS) or not all(boxes):
            continue
        identity_box, *_, exam_box = boxes
        if abs(identity_box[1]-exam_box[1]) > .1 or abs(identity_box[3]-exam_box[3]) > .1:
            continue
        identity = page.crop(identity_box).extract_text_lines()
        exams = page.crop(exam_box).extract_text_lines()
        matches.append((table_index, boxes, identity, exams, leading))
    return matches[0] if len(matches) == 1 else None


def _faculty_label(text):
    return re.match(r'\d{3}(?![0-9A-Z])',text)


def _prior_faculty(number, boxes, frame, raw_pages, document):
    # shortcut: verify at most four adjacent same-layout pages when the faculty heading is not repeated.
    for past in range(number-1,max(0,number-5),-1):
        old = frame(past)
        if not old or any(max(abs(a-c),abs(b-d)) > 2
                          for (a,_,b,_),(c,_,d,_) in zip(boxes,old[1])):
            break
        for line in reversed(old[2]):
            text = compact(line['text'])
            if not _faculty_label(text):
                continue
            if not _FACULTY.fullmatch(text):
                return None, []
            refs = _same_page_refs(line['text'],raw_pages,document,{'page':past})
            return text, refs
    return None, []


def _exam_block(exams, identity, following):
    start = identity['top']
    stop = following['top'] if following else float('inf')
    block = [line for line in exams if start-1 <= line['top'] < stop-1]
    if (not block or abs(start-block[0]['top']) > 1
            or not compact(block[0]['text']).startswith('①')
            or any(line['bottom'] > stop or compact(line['text']) == '同上' for line in block)):
        return []
    return block


def parse_unruled_catalog(pdf,raw_pages,unit,year,document,major_code):
    """Find explicit direction and subject lines with the same first printed baseline."""
    if not major_code:
        return []
    expected_title = f"{compact(unit['name'])}{year}年硕士研究生招生专业目录"
    frames, records = {}, {}

    def frame(number):
        if number not in frames:
            frames[number] = _frame(pdf.pages[number-1],raw_pages[number-1],expected_title)
        return frames[number]

    for number, raw in enumerate(raw_pages,1):
        if major_code not in raw or not (current := frame(number)):
            continue
        table, boxes, identity, exams, title = current
        page = {'page':number}
        header_refs = _same_page_refs(title+'\n考试科目',raw_pages,document,page)
        if not header_refs:
            continue
        department, faculty_refs = _prior_faculty(number,boxes,frame,raw_pages,document)
        major, major_refs = None, []
        for index,line in enumerate(identity):
            text = compact(line['text'])
            if _faculty_label(text):
                department = text if _FACULTY.fullmatch(text) else None
                faculty_refs = _same_page_refs(line['text'],raw_pages,document,page) if department else []
                major, major_refs = None, []
                continue
            if _MAJOR_LABEL.match(text):
                major = _major(text) if not _SPECIAL.search(text) else None
                major_refs = _same_page_refs(line['text'],raw_pages,document,page) if major else []
                continue
            if not _DIRECTION.fullmatch(text) or _SPECIAL.search(text):
                major, major_refs = None, []
                continue
            if not faculty_refs or not major_refs or not major or major[0] != major_code:
                continue
            following = identity[index+1] if index+1 < len(identity) else None
            if following and not (_DIRECTION.fullmatch(compact(following['text']))
                    or _MAJOR_LABEL.match(compact(following['text'])) or _faculty_label(compact(following['text']))):
                continue
            lines = _exam_block(exams,line,following)
            exam = '\n'.join(item['text'] for item in lines)
            exam_refs = _same_page_refs(exam,raw_pages,document,page)
            direction_refs = _same_page_refs(line['text'],raw_pages,document,page)
            if not exam_refs or not direction_refs or _SPECIAL.search(exam):
                continue
            subjects, issue = _subject_rows(exam,exam_refs,raw_pages,document,page,unit)
            if issue:
                continue
            code, name, degree = major
            row = _program(unit,year,department,code,name,text,'',subjects,'',document,number,degree=degree)
            locator = {'page':number,'table':table,'row':index+1,
                       'identity_box':[boxes[0][0],line['top'],boxes[0][2],line['bottom']],
                       'exam_box':[boxes[-1][0],lines[0]['top'],boxes[-1][2],lines[-1]['bottom']]}
            records[row['id']] = (None if row['id'] in records else
                {**row,'study_mode':None,'direction_scope':'direction','source_locator':locator,
                 'evidence':[*major_refs,*direction_refs,*faculty_refs,*header_refs],'exam_evidence':exam_refs})
    return [row for row in records.values() if row]


def _grouped_table(page):
    matches = []
    for index, table in enumerate(page.find_tables(),1):
        rows = table.extract(x_tolerance=2,y_tolerance=3)
        if rows and tuple(map(compact,rows[0])) == _GROUP_HEADERS and len(table.rows) == len(rows):
            headers = table.rows[0].cells
            if len(headers) == 4 and all(headers):
                matches.append((index,table,rows[0]))
    return matches[0] if len(matches) == 1 else None


def _grouped_block(lines, start, stop):
    return [line for line in lines if start-1 <= line['top'] < stop-1]


def _grouped_facts(names, modes, exams, notes, begin, end, floor):
    start = names[begin]['top']
    stop = names[end]['top'] if end < len(names) else floor
    mode = _grouped_block(modes,start,stop)
    exam = _grouped_block(exams,start,stop)
    numbered = ''.join(compact(line['text'])[:1] for line in exam)
    if (len(mode) != 2 or compact(mode[0]['text']) not in {'全日制','非全日制'}
            or not _GROUP_COUNT.fullmatch(compact(mode[1]['text']))
            or numbered not in {'①②','①②③','①②③④'}
            or any(abs(items[0]['top']-start) > 1 for items in (mode,exam))
            or any(line['bottom'] > stop for line in [*mode,*exam])
            or any(re.search(r'同上|复试|加试',line['text']) for line in exam)
            or any(_SPECIAL.search(compact(line['text'])) for line in [*names[begin:end],*exam,*notes])):
        return None
    tail = names[begin+1:end]
    if any(not _DIRECTION.fullmatch(compact(line['text'])) for line in tail):
        return None
    directions = parse_strict_directions('\n'.join(item['text'] for item in tail)) if tail else None
    if directions and directions['issues']:
        return None
    return mode[0]['text'], exam, [value['label'] for value in directions['directions']] if directions else ['']


def _grouped_row(page, boxes, headers, locator, refs, unit, year, document, raw_pages, major_code, base_refs):
    if (len(boxes) != 4 or not all(boxes) or any(abs(box[1]-boxes[0][1]) > .1
            or abs(box[3]-boxes[0][3]) > .1 or max(abs(box[0]-header[0]),abs(box[2]-header[2])) > .1
            for box,header in zip(boxes,headers))):
        return []
    names, modes, exams, notes = (page.crop(box).extract_text_lines() for box in boxes)
    if not names or not _GROUP_FACULTY.fullmatch(department := compact(names[0]['text'])):
        return []
    faculty_refs = refs(names[0]['text'])
    anchors = [(i,match) for i,line in enumerate(names) if (match := _GROUP_MAJOR.fullmatch(compact(line['text'])))]
    programs = []
    for index,(begin,match) in enumerate(anchors):
        if match[1] != major_code:
            continue
        end = anchors[index+1][0] if index+1 < len(anchors) else len(names)
        facts = _grouped_facts(names,modes,exams,notes,begin,end,boxes[0][3])
        identity_refs = refs(names[begin]['text'])
        if not facts or not identity_refs or not faculty_refs:
            continue
        mode, exam, directions = facts
        printed = '\n'.join(line['text'] for line in exam)
        exam_refs, mode_refs = refs(printed), refs(mode)
        subjects, issue = _subject_rows(printed,exam_refs,raw_pages,document,locator,unit)
        if issue or not exam_refs or not mode_refs:
            continue
        for direction in directions:
            direction_refs = refs(direction) if direction else []
            if direction and not direction_refs:
                continue
            row = _program(unit,year,department,match[1],match[2],direction,compact(mode),subjects,'',
                           document,locator['page'],degree=match[3])
            programs.append({**row,'direction_scope':'direction' if direction else 'program','exam_evidence':exam_refs,
                'evidence':[*identity_refs,*faculty_refs,*direction_refs,*mode_refs,*base_refs],
                'source_locator':{**locator,'identity_box':[boxes[0][0],names[begin]['top'],boxes[0][2],names[end-1]['bottom']],
                                  'exam_box':[boxes[2][0],exam[0]['top'],boxes[2][2],exam[-1]['bottom']]}})
    return programs


def parse_grouped_catalog(pdf, raw_pages, unit, year, document, major_code):
    """Bind a local study mode and numbered exam block inside a faculty's merged grid row."""
    opening = [line.strip() for line in raw_pages[0].splitlines() if line.strip()]
    # Some extractors emit the first page's footer before its title.
    title = next(iter(opening[1:] if opening[:1] == ['1'] else opening),'')
    expected = f"{compact(unit['name'])}{year}年硕士研究生招生专业目录"
    if not major_code or compact(title) != expected or not ordinary_catalog_scope(title) or not _grouped_table(pdf.pages[0]):
        return []
    title_refs = _same_page_refs(title,raw_pages,document,{'page':1})
    if not title_refs:
        return []
    records = {}
    for n,raw in enumerate(raw_pages,1):
        if any(_GROUP_TITLE.search(line) and line != expected
               for line in map(compact,raw.splitlines())):
            break
        if major_code not in raw or not (grid := _grouped_table(pdf.pages[n-1])):
            continue
        index, table, labels = grid
        def refs(value): return _same_page_refs(value,raw_pages,document,{'page':n})
        header_refs = refs(labels[2])
        if not header_refs:
            continue
        for number,entry in enumerate(table.rows[1:],2):
            locator = {'page':n,'table':index,'row':number}
            for row in _grouped_row(pdf.pages[n-1],entry.cells,table.rows[0].cells,locator,refs,unit,year,
                                    document,raw_pages,major_code,[*title_refs,*header_refs]):
                records[row['id']] = row if row['id'] not in records else None
    return [record for record in records.values() if record]
