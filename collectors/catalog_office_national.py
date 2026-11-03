"""Replay explicit worksheet geometry; merged major scopes never become global ORs."""
import io
from pathlib import PurePosixPath
import posixpath
import re
import zipfile

from .catalog_office import (S, R, _xml, _coordinate, _number_display,
                             _xlrd_dependency, extract_office_html)
from .catalog_html import _subjects
from .catalogs import _program, compact, evidence
from .documents import DocumentError


PARSER = 'office_national_v1'


def _formats(archive):
    if 'xl/styles.xml' not in archive.namelist(): return ['General']
    root = _xml(archive, 'xl/styles.xml')
    custom = {int(node.get('numFmtId')): node.get('formatCode', '')
              for node in root.findall('./'+S+'numFmts/'+S+'numFmt')}
    known = {0: 'General', 1: '0', 2: '0.00', 3: '#,##0', 4: '#,##0.00', 49: '@', **custom}
    return [known.get(int(node.get('numFmtId', '0')), '')
            for node in root.findall('./'+S+'cellXfs/'+S+'xf')] or ['General']


def _xlsx_cells(archive, path, shared, formats):
    sheet = _xml(archive, path)
    def value(cell):
        kind, text = cell.get('t'), cell.findtext(S+'v', '')
        if kind == 's': return shared[int(text)]
        if kind == 'inlineStr': return ''.join(node.text or '' for node in cell.iter(S+'t'))
        if kind == 'str' or not text: return text
        return _number_display(float(text), formats[int(cell.get('s', '0'))])
    cells = {_coordinate(cell.get('r')): value(cell)
             for cell in sheet.findall('./'+S+'sheetData/'+S+'row/'+S+'c')}
    merges = [(*_coordinate(node.get('ref').split(':')[0]),
               *_coordinate(node.get('ref').split(':')[1]))
              for node in sheet.findall('./'+S+'mergeCells/'+S+'mergeCell')]
    return cells, merges


def workbook_layout(raw):
    """The existing safe reader validates visibility, archive paths, formulas and limits first."""
    extract_office_html(raw)
    if raw.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
        xlrd = _xlrd_dependency()
        book = xlrd.open_workbook(file_contents=raw, formatting_info=True, on_demand=True, logfile=io.StringIO())
        try:
            result = []
            for sheet in book.sheets():
                def value(row, col):
                    cell = sheet.cell(row, col)
                    return (_number_display(cell.value, book.format_map[book.xf_list[cell.xf_index].format_key].format_str)
                            if cell.ctype == xlrd.XL_CELL_NUMBER else str(cell.value))
                cells = {(row+1,col+1):value(row,col) for row in range(sheet.nrows) for col in range(sheet.ncols)
                         if sheet.cell_value(row,col) != ''}
                merges = [(a+1,c+1,b,d) for a,b,c,d in sheet.merged_cells]
                result = [*result, {'name':sheet.name,'cells':cells,'merges':merges}]
            return result
        finally: book.release_resources()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        workbook = _xml(archive, 'xl/workbook.xml')
        relations = {node.get('Id'):node.get('Target') for node in _xml(archive, 'xl/_rels/workbook.xml.rels')}
        shared = ([] if 'xl/sharedStrings.xml' not in archive.namelist() else
                  [''.join(node.text or '' for node in item.iter(S+'t'))
                   for item in _xml(archive, 'xl/sharedStrings.xml')])
        result = []
        for sheet in workbook.findall('./'+S+'sheets/'+S+'sheet'):
            target = relations[sheet.get(R+'id')]
            path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
            if '..' in PurePosixPath(path).parts: raise DocumentError('工作表路径越界')
            cells, merges = _xlsx_cells(archive, path, shared, _formats(archive))
            result = [*result, {'name':sheet.get('name',''),'cells':cells,'merges':merges}]
        return result


def _range(sheet, row, col):
    return next((value for value in sheet['merges'] if value[0]<=row<=value[2] and value[1]<=col<=value[3]),
                (row,col,row,col))


def _cell(sheet, row, col):
    area = _range(sheet,row,col)
    return sheet['cells'].get((area[0],area[1]), '')


def _record(unit, year, doc, sheet, department, code, name, direction, mode, degree, exams, refs, row):
    text = ''.join(value for _,value in exams)
    subjects, _, issues = _subjects(text, unit['id'], doc)
    if issues: return None, issues
    by_slot = {slot:value for slot,value in exams}
    subjects = [{**subject, 'evidence':evidence(doc, by_slot[subject['exam_unit']])} for subject in subjects]
    program = _program(unit, year, department[0]+department[1], code, name, direction, mode,
                       subjects, '', doc, degree=degree)
    return {**program, 'evidence':[evidence(doc, value) for value in dict.fromkeys(refs) if value],
            'exam_evidence':[evidence(doc,value) for _,value in exams],
            'catalog_document_id':doc['id'], 'catalog_parser':PARSER,
            'source_locator':{'sheet':sheet['name'],'row':row}}, []


def _exam_blocks(sheet, first, last, column):
    values = [(row, sheet['cells'].get((row,column), '')) for row in range(first,last+1)]
    values = [(row,value) for row,value in values if value.strip()]
    starts = [index for index,(_,value) in enumerate(values) if re.match(r'^\s*①',value)]
    blocks = []
    for number, start in enumerate(starts):
        end = starts[number+1] if number+1<len(starts) else len(values)
        part = values[start:end]
        slots = [(re.match(r'^\s*([①②③④])',value),value) for _,value in part]
        if any(match is None for match,_ in slots): return [], ['初试合并范围含无法识别的科目行']
        exams = [('①②③④'.index(match[1])+1,value) for match,value in slots]
        if [slot for slot,_ in exams] not in [[1,2],[1,2,3],[1,2,3,4]]:
            return [], ['初试合并范围的单元缺失、重复或顺序不明']
        blocks = [*blocks, {'first':part[0][0], 'last':part[-1][0], 'exams':exams}]
    if not blocks or starts[0]!=0: return [], ['专业原始合并区域缺少完整初试科目起点']
    return blocks, []


def _vertical_sheet(sheet, unit, year, doc):
    maximum = max((r for r,_ in sheet['cells']),default=0)
    programs, issues, department = [], [], None
    degree_column=next((column for (row,column),value in sheet['cells'].items()
                       if compact(value) in {'学位类别','学位类型','专业类型'}),None)
    for row in range(1,maximum+1):
        raw = sheet['cells'].get((row,1),'')
        faculty = re.search(r'学院代码[:：]\s*(\d{3}).*?学院名称[:：]\s*(.+?)(?:咨询电话|联系电话|$)',raw,re.S)
        if faculty:
            department = (faculty[1],faculty[2].strip(),raw)
            continue
        code = raw.strip()
        if not re.fullmatch(r'\d{4}[0-9A-Z]{2}',code): continue
        area = _range(sheet,row,1)
        if area[:2]!=(row,1): continue
        name = _cell(sheet,row,2).strip()
        if not department or not name:
            issues = [*issues, f'{sheet["name"]}!A{row} 缺少已声明院系或专业名称'];continue
        blocks, errors = _exam_blocks(sheet,row,area[2],8)
        if errors:
            issues = [*issues, *[f'{sheet["name"]}!A{row}: {error}' for error in errors]];continue
        directions = [(index,sheet['cells'].get((index,3),'')) for index in range(row,area[2]+1)
                      if sheet['cells'].get((index,3),'').strip()]
        for index,direction in directions:
            direction = direction.strip()
            if not re.match(r'^[0-9A-Z]{2}(?![0-9A-Z])',direction):
                issues = [*issues,f'{sheet["name"]}!C{index} 研究方向标识不明'];continue
            span = _range(sheet,index,3)
            matches = (blocks if len(blocks)==1 else
                       [block for block in blocks if block['first']<=span[0] and span[2]<=block['last']])
            modes = re.findall(r'非全日制|(?<!非)全日制',_cell(sheet,index,4))
            if len(matches)!=1 or len(set(modes))!=1:
                issues = [*issues,f'{sheet["name"]}!C{index} 方向与完整初试组合或学习方式无法唯一对应'];continue
            degree_text = _cell(sheet,index,degree_column) if degree_column else ''
            declared=set(re.findall(r'[（(](专业学位|学术学位)[）)]',name))
            if degree_text in {'专业学位','学术学位'}: declared={*declared,degree_text}
            if len(declared)>1:
                issues=[*issues,f'{sheet["name"]}!B{row} 学位列与专业原文附注明确冲突'];continue
            degree=next(iter(declared)) if declared else None
            program, errors = _record(unit,year,doc,sheet,department,code,name,direction,modes[0],degree,
                matches[0]['exams'],[department[2],raw,_cell(sheet,row,2),direction,_cell(sheet,index,4),degree_text],index)
            issues = [*issues,*errors]
            if program: programs = [*programs,program]
    return programs,issues


def _hierarchical_sheet(sheet, unit, year, doc, context_mode=None):
    programs, issues, department, major, column = [], [], None, None, None
    maximum = max((r for r,_ in sheet['cells']),default=0)
    for row in range(1,maximum+1):
        raw = sheet['cells'].get((row,1),'').strip()
        if re.fullmatch(r'(?:学院[（(]培养单位[）)]|培养单位)、专业及研究方向',compact(raw)):
            column = next((c for (r,c),value in sheet['cells'].items()
                           if r==row and compact(value) in {'考试科目','初试科目'}),None)
            department,major = None,None
            continue
        if column is None: continue
        faculty = re.match(r'^(\d{3})\s+(.+?(?:学院|研究院|研究所|中心|学部|实验室|校区))(?:\s|[（(]|$)',raw)
        if faculty:
            department,major = (faculty[1],faculty[2],raw),None;continue
        heading = re.match(r'^(\d{4}[0-9A-Z]{2})\s*(\D.+)$',raw)
        if heading:
            major = (heading[1],heading[2].strip(),raw);continue
        direction = re.match(r'^([0-9A-Z]{2})(?![0-9])\s*(.+)$',raw)
        if not direction or not department or not major: continue
        mode_values = set(re.findall(r'非全日制|(?<!非)全日制',raw))
        mode_refs = []
        if not mode_values and context_mode:
            mode_values,mode_refs = {context_mode[0]},[context_mode[1]]
        if len(mode_values)!=1:
            issues = [*issues,f'{sheet["name"]}!A{row} 方向未明确单一学习方式'];continue
        exam = _cell(sheet,row,column)
        exam = re.split(r'复试(?:科目)?[:：]|同等学力(?:加试|考生)',exam,maxsplit=1)[0]
        marks = list(re.finditer(r'([①②③④])',exam))
        exams = [('①②③④'.index(mark[1])+1,exam[mark.start():marks[n+1].start() if n+1<len(marks) else len(exam)].strip())
                 for n,mark in enumerate(marks)]
        if [slot for slot,_ in exams] not in [[1,2],[1,2,3],[1,2,3,4]]:
            issues = [*issues,f'{sheet["name"]}!A{row} 原始初试组合不完整'];continue
        name = re.sub(r'[（(](?:非全日制|全日制)[）)]','',raw).replace(' ','')
        program,errors = _record(unit,year,doc,sheet,department,major[0],major[1],name,next(iter(mode_values)),None,
            exams,[department[2],major[2],raw,*mode_refs],row)
        issues = [*issues,*errors]
        if program: programs = [*programs,program]
    return programs,issues


def _flat_sheet(sheet, unit, year, doc):
    programs, issues, active = [], [], False
    maximum = max((r for r,_ in sheet['cells']),default=0)
    for row in range(1,maximum+1):
        if (_cell(sheet,row,1),_cell(sheet,row,2),_cell(sheet,row,3),_cell(sheet,row,4),_cell(sheet,row,10)) == ('院系所','专业','研究方向','学习方式','考试科目'):
            active = True;continue
        if not active: continue
        original = sheet['cells'].get((row,3),'')
        if not original: continue
        faculty = re.fullmatch(r'(\d{3})(.+)',_cell(sheet,row,1).strip())
        major = re.fullmatch(r'([0-9A-Z]{6})(.+)',_cell(sheet,row,2).strip())
        mode = re.findall(r'非全日制|(?<!非)全日制',_cell(sheet,row,4))
        if not faculty or not major or len(set(mode))!=1 or not re.match(r'^[0-9A-Z]{2}(?![0-9A-Z])',original):
            issues = [*issues,f'{sheet["name"]}!C{row} 学院、专业、方向或学习方式身份缺失'];continue
        exam = _cell(sheet,row,10)
        marks = list(re.finditer(r'([①②③④])',exam))
        exams = [('①②③④'.index(mark[1])+1,exam[mark.start():marks[n+1].start() if n+1<len(marks) else len(exam)].strip())
                 for n,mark in enumerate(marks)]
        if [slot for slot,_ in exams] not in [[1,2],[1,2,3],[1,2,3,4]]:
            issues = [*issues,f'{sheet["name"]}!J{row} 原始初试组合不完整'];continue
        program,errors = _record(unit,year,doc,sheet,(faculty[1],faculty[2],faculty[0]),major[1],major[2],
            original,mode[0],None,exams,[faculty[0],major[0],original,_cell(sheet,row,4)],row)
        issues = [*issues,*errors]
        if program: programs = [*programs,program]
    return programs,issues


def parse_catalog_office_national(raw, unit, year, document, *, project_root=None):
    sheets = workbook_layout(raw)
    headings = list(dict.fromkeys(value for sheet in sheets for (row,_),value in sheet['cells'].items()
                                 if row<=5 and '目录' in value and re.search(r'20\d{2}',value)))
    result = {'programs':[],'issues':[],'expected_records':0,'retest_rules':[],'catalog_format':PARSER}
    if (year!=2027 or document.get('year')!=year or document.get('unit_id')!=unit.get('id')
            or any(set(re.findall(r'(?<!\d)(20\d{2})(?!\d)',value))!={str(year)} for value in headings)
            or any(re.search(r'单独考试|强军|推免专用|港澳台|博士招生',value) for value in headings)):
        return {**result,'issues':['原始工作簿未声明唯一年度普通硕士目录']}
    publication = {}
    if not headings or not any(compact(unit['name']) in compact(value) for value in headings):
        if project_root is None: return {**result,'issues':['原始工作簿缺少校名且未核验发布页']}
        from .catalog_publication import verify_catalog_publication
        publication = verify_catalog_publication(document,unit,year,project_root=project_root)
        if not publication['verified']:
            return {**result,'issues':['原始工作簿学校归属未通过发布页核验']}
    from .catalog_publication import _label_mode
    mode_claims = [(mode,value) for value in headings if len(compact(value))<=300 and (mode:=_label_mode(value))]
    context_mode = mode_claims[0] if len({mode for mode,_ in mode_claims})==1 else None
    for sheet in sheets:
        values = list(sheet['cells'].values())
        if '专业代码' in values and '研究方向' in values and any('初试科目' in value for value in values[:120]):
            programs,issues = _vertical_sheet(sheet,unit,year,document)
        elif any(re.fullmatch(r'(?:学院[（(]培养单位[）)]|培养单位)、专业及研究方向',compact(value)) for value in values):
            programs,issues = _hierarchical_sheet(sheet,unit,year,document,context_mode)
        elif all(value in values[:120] for value in ['院系所','专业','研究方向','学习方式','考试科目']):
            programs,issues = _flat_sheet(sheet,unit,year,document)
        else:
            programs,issues = [], ['工作表未通过已适配的原始列布局核验']
        result = {**result,'programs':[*result['programs'],*programs], 'issues':[*result['issues'],*issues]}
    groups = {program['id']:[row for row in result['programs'] if row['id']==program['id']] for program in result['programs']}
    conflicts = [identifier for identifier,rows in groups.items() if len({repr(row) for row in rows})>1]
    return {**result,'expected_records':len(result['programs']), 'documents':publication.get('documents',[]),
            'programs':[rows[0] for identity,rows in groups.items() if identity not in conflicts],
            'issues':[*result['issues'],*(['同一方向身份出现不同原始记录，尚待核验'] if conflicts else [])]}
