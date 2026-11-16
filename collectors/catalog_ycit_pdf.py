"""Replay the reviewed 2027 YCIT PDF with its physical five-column plan."""
from hashlib import sha256
import re

from .catalog_ycit_vsb import PARENT_SHA, PARENT_URL, RECIPE, SOURCE_SHA
from .catalog_pdf_national import _table_rows, extract_pdf_national_layout
from .catalog_pdf_national_v2 import _origin
from .catalog_pdf_sectioned import _same_page_refs
from .catalog_publication import verify_catalog_publication
from .catalog_strict_syntax import parse_strict_subjects
from .catalogs import _program, compact
from .documents import DocumentError, PROJECT_ROOT


PARSER = 'pdf_table_ycit_exact_v1'
_TITLE = '2027年硕士研究生招生专业目录'
_OWNER = re.compile(r'([0-9]{3})(?![0-9])([\u4e00-\u9fff]+学院)')
_MAJOR = re.compile(r'([0-9]{6})(?![0-9])(.+)')
_DIRECTION = re.compile(r'([0-9]{2})(?![0-9])([\u4e00-\u9fff].+)')
_EDGES = (52.32, 284.52, 428.64, 570.84, 638.64, 788.64)
_CONTINUATIONS = {2:'002',3:'003',4:'005',7:'009',9:'036',10:'037'}
_MODE = {'全日制拟招生':'全日制','非全日制拟招生':'非全日制'}


def _empty(issue=None):
    return {'catalog_format':PARSER,'programs':[],'documents':[],'retest_rules':[],
            'expected_records':0,'physical_candidate_rows':0,'issues':[issue] if issue else [],
            'rejected_source_rows':[],'candidate_only':True,'semantic_approved':False}


def _supports(unit, document):
    return (unit.get('id'),unit.get('unit_code'),unit.get('name'),document.get('year'),document.get('sha256')) == (
        '2124779846','10305','盐城工学院',2027,SOURCE_SHA) and document.get('unit_id') == unit['id']


def matches_ycit_catalog(raw, unit, document):
    parent = document.get('publication_page')
    return (raw.startswith(b'%PDF-') and _supports(unit,document)
            and isinstance(parent,dict) and parent.get('url') == PARENT_URL and parent.get('sha256') == PARENT_SHA
            and sha256(raw).hexdigest() == SOURCE_SHA)


def _column(box, column):
    return bool(box and abs(box[0]-_EDGES[column]) < .1 and abs(box[2]-_EDGES[column+1]) < .1)


def _refs(value, page, raw_pages, document, row, col, box):
    refs = _same_page_refs(value, raw_pages, document, page, box)
    if not refs:
        raise DocumentError('物理格内容不能与本页原文逐字核对')
    return [{**ref,'source_locator':{'page':page['page'],'table':1,'row':row,'column':col,'bbox':box}}
            for ref in refs]


def _make_row(table, row, page, data, owner, mode, major, raw_pages, unit, document):
    direction_box, exam_box = (_origin(table,row,index) for index in (0,1))
    if not _column(direction_box,0) or not _column(exam_box,1):
        raise DocumentError('方向或初试格不在已核对列边界内')
    if direction_box[1] < exam_box[1]-.1 or direction_box[3] > exam_box[3]+.1 or exam_box[1] < major['top']-.1 and not major['shared']:
        raise DocumentError('初试格与方向的合并范围不一致')
    exam = parse_strict_subjects(data[1],unit['id'],document)
    if exam['issues'] or exam['exam_scope'] != 'unified':
        raise DocumentError('初试原格不含可核对统考单元：'+'；'.join(exam['issues']))
    direction_refs = _refs(data[0],page,raw_pages,document,row+1,1,direction_box)
    exam_refs = _refs(data[1],page,raw_pages,document,row+1,2,exam_box)
    subjects = [{**s,'evidence':exam_refs} for s in exam['subjects']]
    program = _program(unit,2027,owner['text'],major['code'],major['name'],compact(data[0]),mode['name'],
                       subjects,'',document,page['page'])
    return {**program,'catalog_parser':PARSER,'evidence':[*owner['refs'],*mode['refs'],*major['refs'],*direction_refs],
            'exam_evidence':exam_refs,'source_locator':{'page':page['page'],'table':1,'row':row+1,
                                                        'bbox':direction_box,'major_bbox':major['box']}}


def parse_ycit_layout(layout, unit, document):
    if not _supports(unit,document):
        return _empty('盐城工学院官方目录原件、招生单位或年度未核对')
    pages,raw_pages = layout['pages'],layout['raw_text'].rstrip('\f\r\n ').split('\f')
    if [page['page'] for page in pages] != list(range(1,11)) or len(raw_pages) != 10 or _TITLE not in compact(raw_pages[0]):
        return _empty('目录主标题或10个原件物理页未核对')
    result,owner,mode = _empty(),None,None
    for page in pages:
        number = page['page']
        if len(page['tables']) != 1:
            return _empty('目录页没有唯一原始表格')
        table = page['tables'][0]
        raw_rows = table['rows']
        first = raw_rows[0]
        if not _OWNER.fullmatch(compact(first['texts'][0])):
            if not owner or _CONTINUATIONS.get(number) != owner['code'] or abs(table['bbox'][1]-39.84) > .1:
                return _empty('跨页院系边界未核对')
        major = None
        for row,data in enumerate(_table_rows(table)):
            try:
                value,physical = compact(data[0]),raw_rows[row]
                if identity := _OWNER.fullmatch(value):
                    box = physical['cells'][0]
                    if not box or abs(box[0]-_EDGES[0]) >= .1 or abs(box[2]-_EDGES[-1]) >= .1 or any(physical['cells'][1:]):
                        raise DocumentError('院系标题没有合并在全表宽度内')
                    owner = {'code':identity[1],'text':value,'refs':_refs(data[0],page,raw_pages,document,row+1,1,box)}
                    mode,major = None,None
                    continue
                if value == '专业代码、名称及研究方向':
                    normalized = list(map(compact,data))
                    if (normalized[1:3] != ['初试科目代码及名称','复试科目'] or normalized[4] != '备注'
                            or normalized[3] not in _MODE or not all(_column(box,index) for index,box in enumerate(physical['cells']))
                            or not owner):
                        raise DocumentError('学习方式标题未与院系五列表头同一物理行绑定')
                    mode = {'name':_MODE[normalized[3]],'refs':_refs(data[3],page,raw_pages,document,row+1,4,physical['cells'][3])}
                    major = None
                    continue
                if identity := _MAJOR.fullmatch(value):
                    box = physical['cells'][0]
                    exam_box = _origin(table,row,1)
                    if not owner or not mode or not _column(box,0) or not _column(exam_box,1):
                        raise DocumentError('专业行没有已核准院系、学习方式或初试范围')
                    major = {'code':identity[1],'name':identity[2],'box':box,'top':box[1],
                             'shared':not physical['cells'][1],
                             'refs':_refs(data[0],page,raw_pages,document,row+1,1,box)}
                    continue
                if not _DIRECTION.fullmatch(value) or not major or not mode or not owner:
                    raise DocumentError('未能确认方向及最近专业行')
                result['physical_candidate_rows'] += 1
                result['programs'].append(_make_row(table,row,page,data,owner,mode,major,raw_pages,unit,document))
            except DocumentError as error:
                result['rejected_source_rows'].append({'page':number,'row':row+1,'reason':str(error)})
                if not _DIRECTION.fullmatch(compact(data[0])):
                    major = None
    if len({p['id'] for p in result['programs']}) != len(result['programs']):
        return _empty('目录原文发生方向身份冲突')
    result['expected_records'] = result['physical_candidate_rows']
    return result


def parse_catalog_ycit_exact(path, unit, year, document, *, project_root=PROJECT_ROOT):
    if year != 2027 or not _supports(unit,document):
        return _empty('盐城工学院官方目录原件、招生单位或年度未核对')
    raw = path.read_bytes()
    if not matches_ycit_catalog(raw,unit,document):
        return _empty('盐城工学院原始PDF签名或内容摘要未核对')
    proof = verify_catalog_publication(document,unit,year,project_root=project_root)
    links = proof.get('link_evidence',[])
    if (not proof['verified'] or proof['mode_conflict'] or len(links) != 1
            or links[0].get('recipe') != RECIPE or links[0].get('url') != document['url']):
        return _empty('盐城工学院锁定发布关系或PDF查看器证据未重放')
    result = parse_ycit_layout(extract_pdf_national_layout(path),unit,document)
    if (result['issues'] or result['rejected_source_rows'] or result['physical_candidate_rows'] != 51
            or len(result['programs']) != 51):
        return _empty('锁定原件的物理方向行计数偏离独立审校结果')
    return {**result,'documents':proof['documents']}
