"""Read ruled, row-spanning exam tables within a verified admissions brochure."""
import re

from collectors.catalog_pdf_sectioned import _same_page_refs, _subject_rows
from collectors.catalog_strict_syntax import parse_strict_directions
from collectors.catalogs import _program, compact, evidence
from service_verification import ordinary_catalog_scope


_FACULTY = r'[\u4e00-\u9fff]{0,32}(?:学院|研究所|研究院|中心|系|部)'
_DEPARTMENT = re.compile(rf'([0-9]{{3}})[-－]?({_FACULTY})')
_MAJOR = re.compile(r'([0-9]{4}[0-9A-Z]{2})([\u4e00-\u9fff][^0-9]{0,79}?)(?:\((学术学位|专业学位)\))?')
_CHAPTER = re.compile(r'(?:[一二三四五六七八九十]{1,3}、|第[一二三四五六七八九十]{1,3}章)(.*)')
_CATALOG = {'招生专业目录','硕士研究生招生专业目录'}
_SPECIAL = re.compile(r'单独考试|单考|(?:仅|只)(?:招收|接收)(?:推荐免试|推免)|不(?:招收|接收)统考')
_NO_DIRECTION = re.compile(r'(?:不区分|不设|不分)(?:研究)?方向')
_HEADERS = {
    'department': {'学院代码及名称','院系代码及名称','院系所代码及名称'},
    'major': {'专业','专业代码及名称','专业代码及专业名称'},
    'direction': {'研究方向','研究方向代码及名称'},
    'mode': {'学习方式','学习形式'},
    'exam': {'初试科目','初试考试科目','初试科目代码及名称'},
}


def _brochure_title(raw, unit, year):
    title = next((line.strip() for line in raw.splitlines() if line.strip()),'')
    pattern = rf'{re.escape(compact(unit["name"]))}({_FACULTY})?{year}年硕士研究生招生(?:简章|章程)'
    claim = re.fullmatch(pattern,compact(title))
    return (title,claim[1]) if claim and ordinary_catalog_scope(title) else None


def _columns(labels, boxes):
    if len(labels) != len(boxes) or not all(boxes):
        return None
    all_columns = [[index for index,label in enumerate(labels) if compact(label) in allowed]
                   for allowed in _HEADERS.values()]
    return tuple(matches[0] for matches in all_columns) if all(len(matches)==1 for matches in all_columns) else None


def _band(box):
    return tuple(round(box[index],2) for index in (1,3))


def _cover(rows, boxes, index, column, anchor):
    top,bottom = _band(anchor)
    matches = [(row[column],cell[column]) for row,cell in zip(rows[:index+1],boxes[:index+1])
               if cell[column] and _band(cell[column])[0]<=top and bottom<=_band(cell[column])[1]]
    return matches[0] if len(matches)==1 else ('',None)


def _direction(printed):
    value = compact(printed)
    if re.fullmatch(r'0不区分(?:研究)?方向',value):
        return value[1:]
    result = parse_strict_directions(value)
    if not result['issues'] and len(result['directions'])==1:
        return result['directions'][0]['label']
    return ''


def _read_row(rows, boxes, index, columns, faculty, major_code, refs):
    department_col,major_col,direction_col,mode_col,exam_col = columns
    printed,anchor = rows[index][direction_col],boxes[index][direction_col]
    direction = _direction(printed) if anchor else ''
    if not direction:
        return None
    covering = [_cover(rows,boxes,index,column,anchor) for column in range(len(rows[index]))]
    if _SPECIAL.search(' '.join(value for value,_ in covering)):
        return None
    dept,_ = covering[department_col]
    identity,identity_box = covering[major_col]
    mode,mode_box = covering[mode_col]
    exam,exam_box = covering[exam_col]
    college = _DEPARTMENT.fullmatch(compact(dept))
    major = _MAJOR.fullmatch(compact(identity))
    if (not major or major_code and major[1]!=major_code or not college or faculty and college[2]!=faculty
            or not all((identity_box,mode_box,exam_box)) or len({_band(box) for box in (identity_box,mode_box,exam_box)})!=1
            or compact(mode) not in {'全日制','非全日制'}):
        return None
    location_refs = [refs(value) for value in (dept,identity,printed,mode)]
    exam_refs = refs(exam)
    if not all(location_refs) or not exam_refs:
        return None
    return (college[0],major,direction,compact(mode),exam,exam_refs,
            [ref for group in location_refs for ref in group],identity_box,exam_box)


def _catalog_tables(pdf, raw_pages, major_code):
    for number, raw in enumerate(raw_pages,1):
        if (major_code and major_code not in raw
                or not any(match[1] in _CATALOG for text in raw.splitlines()
                           if (match:=_CHAPTER.fullmatch(compact(text))))):
            continue
        page = pdf.pages[number-1]
        headings = [(line['bottom'],line['text'],match[1]) for line in page.extract_text_lines()
                    if (match:=_CHAPTER.fullmatch(compact(line['text'])))]
        for order,table in enumerate(page.find_tables(),1):
            preceding = [head for head in headings if head[0]<=table.bbox[1]]
            if preceding and (closest:=max(preceding))[2] in _CATALOG:
                yield number,order,table,closest[1]


def parse_brochure_catalog(pdf, raw_pages, unit, year, document, major_code):
    claim = _brochure_title(raw_pages[0],unit,year)
    if not claim:
        return []
    title, faculty = claim
    programs = {}
    for number,order,table,heading in _catalog_tables(pdf,raw_pages,major_code):
        page = {'page':number}
        def refs(value):
            return _same_page_refs(value,raw_pages,document,page)

        rows = [[item or '' for item in row] for row in table.extract(x_tolerance=2,y_tolerance=3)]
        boxes = [row.cells for row in table.rows]
        if not rows or len(rows)!=len(boxes) or any(len(row)!=len(rows[0]) or len(box)!=len(rows[0])
                                                   for row,box in zip(rows,boxes)):
            continue
        columns = _columns(rows[0],boxes[0])
        chapter_refs = refs(heading)
        if not columns or not chapter_refs or not (header_refs:=refs(rows[0][columns[-1]])):
            continue
        for index in range(1,len(rows)):
            read = _read_row(rows,boxes,index,columns,faculty,major_code,refs)
            if not read:
                continue
            department,major,direction,mode,exam,exam_refs,row_refs,identity_box,exam_box = read
            subjects,issue = _subject_rows(exam,exam_refs,raw_pages,document,page,unit)
            if issue:
                continue
            row = _program(unit,year,department,major[1],major[2],direction,mode,subjects,'',document,number,degree=major[3])
            result = {**row,'direction_scope':'program' if _NO_DIRECTION.fullmatch(direction) else 'direction',
                      'exam_evidence':exam_refs,
                      'evidence':[evidence(document,title,1),*chapter_refs,*header_refs,*row_refs],
                      'source_locator':{'page':number,'table':order,'row':index+1,
                                        'identity_box':list(identity_box),'exam_box':list(exam_box)}}
            programs[row['id']] = result if row['id'] not in programs else None
    return [result for result in programs.values() if result]
