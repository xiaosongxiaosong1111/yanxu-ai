"""Exact subject and direction facts from a scoped original PDF table."""
import hashlib
from pathlib import Path
import re

from collectors.catalog_pdf_national import _plumber
from collectors.catalog_pdf_sectioned import _same_page_refs, _subject_rows
from collectors.catalog_strict_syntax import parse_strict_directions
from collectors.catalogs import _program, compact, evidence
from collectors.documents import DocumentError, extract_pdf_text
from public_sources import official_source_url
from research_catalog_pdf_unruled import parse_unruled_catalog
from service_verification import ordinary_catalog_scope


_CATALOG_HEAD = re.compile(r'(.{1,70}?)(20\d{2})年.{0,40}硕士.{0,30}专业目录(?:\((非全日制|全日制)\))?')
_MODE_TITLE = re.compile(r'(非全日制|全日制)(?:学术学位|专业学位)?硕士')
_MODE_HEAD = re.compile(r'学习(?:方式|形式)[:：](非全日制|全日制)')
_DEPARTMENT = re.compile(r'(\d{3})(?!\d)\s*(?:[-－]\s*)?([^（(\d]{1,60}?(?:学院|研究所|研究院|教学部|中心|系|部))(?=\s*[（(:：]|$)')
_MAJOR = re.compile(r'(\d{4}[0-9A-Z]{2})(?![0-9A-Z])\s*([\u4e00-\u9fff].{0,79}?)(?:[（(](学术学位|专业学位)[）)])?')
_DIRECTION = re.compile(r'(?:\d{2}|\d[A-Z]|[A-Z]\d)(?!\d)\s*[\u4e00-\u9fff]')
_SPECIAL = re.compile(r'单独考试|单考|(?:仅|只)(?:招收|接收)(?:推荐免试|推免)|不(?:招收|接收)统考')
_IDENTITY_HEADERS = {'学院、专业及研究方向','学院、专业代码、名称及研究方向','院系、专业及方向',
                     '学院、专业、研究方向','专业代码、名称及研究方向','学科、专业代码、名称及研究方向'}
_EXAM_HEADERS = {'初试科目','考试科目','初试考试科目','初试科目代码及名称'}
_COUNT_HEADERS = {'人数','拟招生数','拟招生人数','招生人数'}


def _mode_in_page(raw, unit, year, page_number, document, owned):
    source_lines = raw.splitlines()
    lines = [line.strip() for line in source_lines[:12] if line.strip()]
    for index, quote in enumerate(lines):
        title = _CATALOG_HEAD.fullmatch(compact(quote))
        if title is None:
            if owned and index == 0 and (mode := _MODE_HEAD.fullmatch(compact(quote))):
                return owned, mode[1], [evidence(document, quote, page_number)]
            continue
        if (title[1] != compact(unit['name']) or title[2] != str(year) or not ordinary_catalog_scope(quote)):
            return False, None, []
        inline = _MODE_TITLE.match(title[0], title.end(2) + 1)
        modes = {mode for mode in (title[3], inline[1] if inline else None) if mode}
        mode_quote = lines[index+1] if index + 1 < len(lines) else ''
        followup = _MODE_HEAD.fullmatch(compact(mode_quote))
        if followup:
            modes.add(followup[1])
        if len(modes) > 1:
            return False, None, []
        refs = [evidence(document, quote, page_number)]
        return True, next(iter(modes), None), refs + ([evidence(document,mode_quote,page_number)] if followup else [])
    if owned and any(_CATALOG_HEAD.fullmatch(compact(line)) or _MODE_HEAD.fullmatch(compact(line))
                     for line in source_lines[12:]):
        # A mid-page section change has no established ordering relative to the grid.
        return False, None, []
    return None


def _columns(cells):
    present = [cell for cell in cells if cell]
    return tuple((cell[0],cell[2]) for cell in present) if len(present) == len(cells) else ()


def _similar(left, right):
    return bool(left) and len(left) == len(right) and all(max(abs(a-c),abs(b-d)) <= 2
        for (a,b),(c,d) in zip(left,right))


def _table_profile(first, cells):
    labels = [compact(value) for value in first]
    if (not labels or labels[0] not in _IDENTITY_HEADERS
            or sum(label in _EXAM_HEADERS for label in labels) != 1):
        return None
    exam = next(i for i,label in enumerate(labels) if label in _EXAM_HEADERS)
    count = next((i for i,label in enumerate(labels) if label in _COUNT_HEADERS),None)
    geometry = _columns(cells)
    return (exam,count,geometry,first[exam]) if len(geometry) == len(labels) else None


def _aligned(cell1, cell2):
    return bool(cell1 and cell2 and abs(cell1[1]-cell2[1]) <= .1 and abs(cell1[3]-cell2[3]) <= .1)


def _faculty(value):
    matches = [match for line in value.splitlines() if (match := _DEPARTMENT.match(line.strip()))]
    return matches[0] if len(matches) == 1 else None


def _program_groups(value, counts, *, allow_continuation):
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    starts = [(i,m) for i,line in enumerate(lines) if (m := _MAJOR.fullmatch(line))]
    if not starts:
        return []
    prefix = lines[:starts[0][0]]
    if prefix and (not allow_continuation or not all(_DIRECTION.match(line) for line in prefix)):
        return []
    if len(starts) > 1:
        # Multiple explicit majors may share an exam cell only where each has its own count and no printed directions.
        if (prefix or len(starts) != len(lines) or len(starts) != len(counts.splitlines())
                or not all(re.fullmatch(r'\s*\d+\s*',line) for line in counts.splitlines())):
            return []
        return [(m,'',bool(prefix)) for _,m in starts]
    index, major = starts[0]
    tail = lines[index+1:]
    if not tail:
        return [(major,'',bool(prefix))]
    result = parse_strict_directions('\n'.join(tail))
    return [(major,direction['label'],bool(prefix)) for direction in result['directions']] if not result['issues'] else []


def _program_rows(values,cells,profile,faculty,chapter,page,raw_pages,document,unit,year,locator,major_code,continue_major):
    exam_column,count_column,_,header_refs = profile
    exam = values[exam_column]
    if not exam or not _aligned(cells[0],cells[exam_column]) or _SPECIAL.search(' '.join(values)):
        return []
    if not faculty or not chapter or not chapter[0] or (major_code and major_code not in values[0]):
        return []
    desc = {'page':page}
    exam_refs = _same_page_refs(exam,raw_pages,document,desc)
    subjects, issue = _subject_rows(exam,exam_refs,raw_pages,document,desc,unit)
    if issue or not exam_refs:
        return []
    groups = _program_groups(values[0],values[count_column] if count_column is not None else '',
                             allow_continuation=continue_major)
    programs = []
    for match,direction,has_leading in groups:
        code,name,degree = match.groups()
        if major_code and code != major_code:
            continue
        identity = _same_page_refs(match[0],raw_pages,document,desc)
        direction_refs = _same_page_refs(direction,raw_pages,document,desc) if direction else []
        if not identity or direction and not direction_refs:
            continue
        if has_leading and not any(compact(match[0]) in compact(line) and compact(exam_refs[0]['quote']) in compact(line)
                                   for line in raw_pages[page-1].splitlines()):
            continue
        row = _program(unit,year,faculty[0],code,name.strip(),direction,chapter[0],subjects,'',document,page,degree=degree)
        programs.append({**row,'direction_scope':'direction' if direction else 'program',
                         'evidence':[*identity,*direction_refs,*faculty[1],*chapter[1],*header_refs],
                         'exam_evidence':exam_refs,'source_locator':{**locator,'page':page,
                             'identity_box':list(cells[0]),'exam_box':list(cells[exam_column])}})
    return programs


def _parse_pdf(pdf,raw_pages,unit,year,document,major_code):
    targets = {i for i,page in enumerate(raw_pages,1) if not major_code or major_code in page}
    needed = targets | {i-1 for i in targets if i>1}
    chapter,owned,profile,faculty,previous = None,False,None,None,None
    programs = {}
    for number,raw in enumerate(raw_pages,1):
        heading = _mode_in_page(raw,unit,year,number,document,owned)
        if heading:
            owned,mode,refs = heading
            chapter,faculty,previous = ((mode,refs) if owned and mode else None),None,None
            if not owned:
                profile = None
        if not owned or not chapter or not (number in needed or heading):
            continue
        for table_index,table in enumerate(pdf.pages[number-1].find_tables(),1):
            values = [[cell or '' for cell in row] for row in table.extract(x_tolerance=2,y_tolerance=3)]
            if not values or len(table.rows) != len(values):
                faculty,previous = None,None
                continue
            cells = [row.cells for row in table.rows]
            new_profile = _table_profile(values[0],cells[0])
            first_full = next((_columns(row) for row in cells if _columns(row)),())
            if not new_profile and not (profile and table.bbox[1] < pdf.pages[number-1].height*.23
                                       and _similar(first_full,profile[2])):
                faculty,previous = None,None
                continue
            if new_profile:
                header_refs = _same_page_refs(new_profile[3],raw_pages,document,{'page':number})
                if not header_refs:
                    profile,faculty,previous = None,None,None
                    continue
                active = (*new_profile[:3],header_refs)
            else:
                active = profile
            continued = bool(faculty and previous and previous[0] == number-1 and table_index == 1
                             and new_profile and _similar(active[2],previous[1]))
            if not continued:
                faculty = None
            profile = active
            for i,(row,boxes) in enumerate(zip(values,cells)):
                if not row or len(row) <= profile[0] or i == 0 and new_profile:
                    continue
                if row[0] and boxes[0] and not any(boxes[1:]) and boxes[0][2] >= table.bbox[2]-.1:
                    match = _faculty(row[0])
                    proof = _same_page_refs(match[0],raw_pages,document,{'page':number}) if match else []
                    faculty = (match[1]+match[2].strip(),proof) if proof else None
                    continue
                if number not in targets:
                    continue
                locator = {'table':table_index,'row':i+1}
                for result in _program_rows(row,boxes,profile,faculty,chapter,number,raw_pages,document,unit,year,
                                            locator,major_code,continued and i == 1):
                    if result['id'] in programs:
                        programs[result['id']] = None
                    else:
                        programs[result['id']] = result
            previous = (number,profile[2])
    return [value for value in programs.values() if value]


_SPLIT_MAJOR = {'专业代码及名称','专业代码、专业名称、研究方向代码、研究方向名称'}
_SPLIT_EXAMS = _EXAM_HEADERS | {'初试考试科目代码及科目名称'}
_INLINE_DIRECTION = re.compile(r'(\d{2}|\d[A-Z]|[A-Z]\d)[.、．]?\s*[（(](全日制|非全日制)[）)]\s*(.*)')
_CELL_MODE = re.compile(r'(?:\(([12])\))?(全日制|非全日制)')
_ANON_CATALOG = re.compile(r'(20\d{2})年招收攻读硕士学位研究生专业目录')


def _split_profile(labels, boxes):
    labels = [compact(cell) for cell in labels]
    names = {'major':_SPLIT_MAJOR, 'exam':_SPLIT_EXAMS, 'department':{'学院代码及名称'},
             'direction':{'研究方向'}, 'mode':{'学习方式','学习形式'}}
    columns = {}
    for role, allowed in names.items():
        matches = [index for index, name in enumerate(labels) if name in allowed]
        if len(matches) > 1:
            return None
        if matches:
            columns[role] = matches[0]
    geometry = _columns(boxes)
    if geometry and (set(columns) == {'major','exam','department'}
                     or set(columns) == {'major','exam','direction','mode'}):
        return columns, geometry
    return None


def _covering_cell(rows, cells, row, column, anchor):
    covering = [(i, boxes[column]) for i, boxes in enumerate(cells[:row+1])
                if column < len(boxes) and boxes[column]
                and boxes[column][1] <= anchor[1]+.1 and boxes[column][3] >= anchor[3]-.1]
    if len(covering) != 1:
        return '', None
    index, box = covering[0]
    return rows[index][column], box


def _split_directions(major, values, cells, columns, refs):
    if 'direction' in columns:
        direction_col, mode_col, exam_col = (columns[name] for name in ('direction','mode','exam'))
        direction, mode = values[direction_col], values[mode_col]
        match = _CELL_MODE.fullmatch(compact(mode))
        parsed = parse_strict_directions(compact(direction))
        if (not match or parsed['issues'] or len(parsed['directions']) != 1
                or not all(_aligned(cells[col],cells[exam_col]) for col in (direction_col,mode_col))):
            return []
        if match[1] and (match[1] == '1') != (match[2] == '全日制'):
            return []
        proof = refs(direction), refs(mode)
        return [(parsed['directions'][0]['label'],match[2],[*proof[0],*proof[1]])] if all(proof) else []
    directions = []
    for line in major.splitlines()[1:]:
        match = _INLINE_DIRECTION.fullmatch(line.strip())
        proof = refs(line)
        if not match or not proof or not (match[3] or match[1] == '00'):
            return []
        directions.append((match[1]+compact(match[3]) if match[3] else '',match[2],proof))
    return directions


def _split_program(rows, cells, index, columns, faculty, *, raw_pages, document, unit, year,
                   locator, major_code, title_refs, title_mode, header_refs):
    page = {'page':locator['page']}
    def refs(value):return _same_page_refs(value,raw_pages,document,page)
    values, boxes = rows[index], cells[index]
    exam_col = columns['exam']
    exam = values[exam_col]
    exam_box = boxes[exam_col]
    if not exam or not exam_box or _SPECIAL.search(' '.join(values)):
        return []
    printed, identity_box = _covering_cell(rows,cells,index,columns['major'],exam_box)
    if not printed or len(printed.splitlines())>80 or _SPECIAL.search(printed):
        return []
    identity = printed.splitlines()[0] if 'department' in columns else printed
    major = _MAJOR.fullmatch(compact(identity).replace('【','(').replace('】',')'))
    if not major or (major_code and major[1]!=major_code):
        return []
    if 'department' in columns:
        dept, _ = _covering_cell(rows,cells,index,columns['department'],exam_box)
        faculty = (_faculty(compact(dept)),refs(dept))
        if not _aligned(identity_box,exam_box):
            return []
    if not faculty or not faculty[0] or not faculty[1]:
        return []
    identity_refs, exam_refs = refs(identity), refs(exam)
    if not identity_refs or not exam_refs:
        return []
    subjects, issue = _subject_rows(exam,exam_refs,raw_pages,document,page,unit)
    if issue:
        return []
    programs = []
    for label, mode, proof in _split_directions(printed,values,boxes,columns,refs):
        if title_mode and title_mode!=mode:
            continue
        department = faculty[0][1]+faculty[0][2].strip()
        row = _program(unit,year,department,major[1],major[2],label,mode,subjects,'',document,page['page'],degree=major[3])
        programs.append({**row,'direction_scope':'direction' if label else 'program',
                         'evidence':[*identity_refs,*faculty[1],*proof,*title_refs,*header_refs],
                         'exam_evidence':exam_refs,'source_locator':{**locator,
                             'identity_box':list(identity_box),'exam_box':list(exam_box)}})
    return programs


def _parse_split_catalog(pdf, raw_pages, unit, year, document, major_code):
    heading = next((line.strip() for line in raw_pages[0].splitlines() if line.strip()),'')
    first = _mode_in_page(raw_pages[0],unit,year,1,document,False)
    anonymous = _ANON_CATALOG.fullmatch(compact(heading))
    if not (first and first[0] or anonymous and anonymous[1]==str(year)):
        return []
    title_mode, title_refs = first[1:] if first else (None,[evidence(document,heading,1)])
    targets = {number for number,raw in enumerate(raw_pages,1) if not major_code or major_code in raw}
    adjacent, table_profile, faculty, programs = 0,None,None,{}
    for number in range(1,max(targets,default=0)+1):
        page = pdf.pages[number-1]
        change = _mode_in_page(raw_pages[number-1],unit,year,number,document,True)
        if number>1 and change and not change[0]:
            break
        if change and change[1]:
            title_mode, title_refs = change[1:]
        for table_index, table in enumerate(page.find_tables(),1):
            rows = [[cell or '' for cell in row] for row in table.extract(x_tolerance=2,y_tolerance=3)]
            cells = [row.cells for row in table.rows]
            if not rows or len(rows)!=len(cells) or any(len(row)!=len(cells[i]) for i,row in enumerate(rows)):
                continue
            explicit = _split_profile(rows[0],cells[0])
            if explicit:
                columns, geometry = explicit
                header_refs = _same_page_refs(rows[0][columns['exam']],raw_pages,document,{'page':number})
                if not header_refs or anonymous and 'department' not in columns:
                    continue
                table_profile = columns, geometry, header_refs
            elif not (table_index==1 and number==adjacent+1 and table_profile and table.bbox[1]<page.height*.23
                      and _similar(next((_columns(row) for row in cells if _columns(row)),()),table_profile[1])):
                table_profile, faculty = None,None
                continue
            else:
                columns, _, header_refs = table_profile
            if number!=adjacent+1 or table_index!=1 or explicit:
                faculty = None
            start = 1 if explicit else 0
            for i in range(start,len(rows)):
                values, boxes = rows[i], cells[i]
                if boxes[0] and not any(boxes[1:]) and boxes[0][2]>=table.bbox[2]-.1:
                    name = _faculty(compact(values[0]))
                    proof = _same_page_refs(values[0],raw_pages,document,{'page':number}) if name else []
                    faculty = name, proof
                    continue
                if number not in targets or len(values)!=len(table_profile[1]):
                    continue
                locator = {'table':table_index,'row':i+1,'page':number}
                for result in _split_program(rows,cells,i,columns,faculty,raw_pages=raw_pages,document=document,
                                             unit=unit,year=year,locator=locator,major_code=major_code,
                                             title_refs=title_refs,title_mode=title_mode,header_refs=header_refs):
                    programs[result['id']] = result if result['id'] not in programs else None
            adjacent = number
        if number==1 and table_profile is None:
            break
    return [value for value in programs.values() if value]


def parse_live_catalog_pdf(path,unit,year,document,*,major_code=''):
    """Require an official original and explicit exam/identity geometry before returning live research rows."""
    if (not isinstance(unit,dict) or not unit.get('id') or not unit.get('name')
            or not isinstance(document,dict) or document.get('year') != year or type(year) is not int
            or str(document.get('unit_id','')) != str(unit.get('id',''))
            or not isinstance(document.get('id'),str) or not isinstance(document.get('url'),str)
            or not official_source_url(document['url'],unit) or not isinstance(major_code,str)
            or major_code and not re.fullmatch(r'\d{4}[0-9A-Z]{2}',major_code)):
        return {'programs':[],'issues':['PDF来源学校、目录年度或查询专业身份不符。']}
    try:
        path = Path(path)
        if not path.is_file() or not 0 < path.stat().st_size <= 8*1024*1024:
            raise DocumentError('PDF原文缺失或超出本轮大小上限。')
        raw = path.read_bytes()
        if not raw.startswith(b'%PDF-') or hashlib.sha256(raw).hexdigest() != document.get('sha256'):
            raise DocumentError('PDF原件字节和已核验来源摘要不一致。')
        raw_pages = extract_pdf_text(path).rstrip('\f\r\n ').split('\f')
        from pdfminer.psparser import PSException
        try:
            with _plumber().open(path) as pdf:
                if not pdf.doc.is_extractable or not 1 <= len(pdf.pages) == len(raw_pages) <= 100:
                    raise DocumentError('PDF原文文字不可提取或页数超出实时核验范围。')
                programs = []
                if len(pdf.pages) <= 80:
                    programs = (_parse_pdf(pdf,raw_pages,unit,year,document,major_code)
                                or _parse_split_catalog(pdf,raw_pages,unit,year,document,major_code))
                if not programs:
                    programs = parse_unruled_catalog(pdf,raw_pages,unit,year,document,major_code)
        except PSException as error:
            raise DocumentError('PDF表格网格未能可靠提取。') from error
    except (DocumentError,OSError,ValueError,TypeError,KeyError,ImportError,IndexError) as error:
        return {'programs':[],'issues':[str(error) if isinstance(error,DocumentError) else 'PDF原文单元格暂未核验。']}
    return {'programs':programs,'issues':[] if programs else ['原文中未找到学习方式、院系、专业和完整初试科目均可核验的表格行。']}
