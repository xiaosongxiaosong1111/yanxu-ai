"""Resolve explicit exam blocks in catalog tables with vertically merged cells."""
import re

from collectors.catalog_pdf_sectioned import _same_page_refs, _subject_rows
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
