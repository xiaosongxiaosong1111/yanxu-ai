"""HIT's complete public physical PDF grid and its original HTML pdfsrc publication."""
import hashlib
from pathlib import Path
import re
from urllib.parse import urljoin,urlsplit

from .catalog_public_aspx import _tree
from .catalog_pdf_national import extract_pdf_national_layout,_raw_quotes
from .catalog_pdf_tables import _cell_lines,_references
from .catalog_public_forms import _matrix
from .catalogs import _program,compact,evidence,parse_subjects
from .documents import decode_text,PROJECT_ROOT

PARSER='pdf_table_hit_v1'
HOST='yzb.hit.edu.cn'
ENTRY='/2026/0924/c22272a401989/page.htm'
PDF='/_upload/article/files/12/cb/333643d84636a30ab6b586266704/501748af-b6cb-4851-8931-49196a409f82.pdf'
TITLE='2027年统考硕士研究生招生目录'
HEADER=['专业(标注*号为专业学位)','研究方向','学习方式','考试科目']


def _squash(text):return re.sub(r'\s+','',str(text))


def _profile(unit,year):
    if unit.get('id')!='368087' or str(unit.get('unit_code'))!='10213' or unit.get('name')!='哈尔滨工业大学' or year!=2027:
        raise ValueError('尚未核准该学校或年度的哈工大统考公开目录')


def _address(record,path):
    if not isinstance(record,dict):raise ValueError('原文地址描述缺失')
    parsed=urlsplit(record.get('url',''))
    if (parsed.scheme!='https' or parsed.hostname!=HOST or parsed.path!=path or parsed.query or parsed.fragment
            or parsed.username or parsed.password or parsed.port not in {None,443} or record.get('request_method')!='GET'):
        raise ValueError('来源不是已核准的真实哈工大公开 GET 地址')


def _source(record,unit,year,root,limit):
    if not isinstance(record,dict) or record.get('unit_id')!=unit['id'] or record.get('year')!=year:
        raise ValueError('原文学校或招生年度不一致')
    base=Path(root).resolve();path=(base/record.get('local_path','')).resolve()
    if not path.is_relative_to(base/'data/snapshots') or not path.is_file() or not 0<path.stat().st_size<=limit:
        raise ValueError('原文缺失、越界或超过读取上限')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=record.get('sha256'):raise ValueError('原文 SHA 摘要不一致')
    return raw


def verify_hit_publication(document,unit,year,*,project_root=PROJECT_ROOT):
    """The actual visible pdf player attribute is a link relation, never an invented quote."""
    try:
        _profile(unit,year);_address(document,PDF);raw=_source(document,unit,year,project_root,32*1024*1024)
        if not raw.startswith(b'%PDF-'):raise ValueError('公开附件不是原始 PDF')
        parent=document.get('publication_page')
        if not isinstance(parent,dict):raise ValueError('缺少 PDF 的真实官方发布页')
        _address(parent,ENTRY);html=decode_text(_source(parent,unit,year,project_root,8*1024*1024),parent.get('content_type',''))
        tree=_tree(html);articles=[node for node in tree.find('div') if not node.hidden and node.attrs.get('portletmode')=='simpleArticleAttri']
        if len(articles)!=1:raise ValueError('官方页面没有唯一的实际发布文章区域')
        titles=[node for node in articles[0].find('div') if not node.hidden and 'tit' in node.attrs.get('class','').split()]
        bodies=[node for node in articles[0].find('div') if not node.hidden and 'wp_articlecontent' in node.attrs.get('class','').split()]
        if (len(titles)!=1 or compact(titles[0].text())!=TITLE or unit['name'] not in compact(tree.text()) or len(bodies)!=1):
            raise ValueError('官方发布页缺少唯一的本校2027统考目录标题及正文')
        players=[node for node in bodies[0].find('div') if not node.hidden and 'wp_pdf_player' in node.attrs.get('class','').split()]
        if len(players)!=1 or urljoin(parent['url'],players[0].attrs.get('pdfsrc',''))!=document['url']:
            raise ValueError('PDF 没有由正文中唯一的原始 pdfsrc 播放器公开关联')
        locator={'tag':'div','class':'wp_pdf_player','attribute':'pdfsrc','attribute_value':players[0].attrs['pdfsrc'],
                 'element_id':players[0].attrs.get('id'),'url':document['url']}
        return {'verified':True,'reason':None,'evidence':[evidence(parent,titles[0].text())],'documents':[parent],
                'attachment_locator':locator,'mode':None,'mode_evidence':[],'mode_conflict':False}
    except (ValueError,OSError,TypeError,KeyError) as error:
        return {'verified':False,'reason':str(error),'evidence':[],'documents':[],
                'mode':None,'mode_evidence':[],'mode_conflict':False}


def extract_hit_public_layout(path):
    return extract_pdf_national_layout(path)


def _refs(value,box,page,document,raw_pages):
    native=page.get('native')
    if native:
        fragments=_cell_lines(native,box)
        refs,missing=_references(fragments,document,raw_pages)
        if not missing and compact(''.join(line['text'] for _,line in fragments))==compact(value) and refs:return refs
    refs=_raw_quotes(value,raw_pages,document,page['page'])
    if not refs or any(ref['page']!=page['page'] for ref in refs):raise ValueError('物理单元格无法逐字对应当前原始 PDF 页')
    if compact(''.join(ref['quote'] for ref in refs))!=compact(value):raise ValueError('PDF 引用没有保留完整原始单元格内容')
    return refs


def _box(box,page):
    if (not isinstance(box,(list,tuple)) or len(box)!=4 or any(type(value) not in {int,float} for value in box)
            or not 0<=box[0]<box[2]<=page['width'] or not 0<=box[1]<box[3]<=page['height']):
        raise ValueError('PDF 物理单元格边界不完整或越界')


def _grid(page):
    if len(page.get('tables',[]))!=1:raise ValueError('每个原始目录页必须只有一张完整物理表')
    table=page['tables'][0];rows=table.get('rows',[])
    if not rows or [compact(value) for value in rows[0]['texts']]!=HEADER or len(rows[0]['cells'])!=4:
        raise ValueError('PDF 四列表头与专业学位星号说明不匹配')
    for box in rows[0]['cells']:_box(box,page)
    bounds=[rows[0]['cells'][0][0],*[box[2] for box in rows[0]['cells']]]
    if any(abs(rows[0]['cells'][i][0]-bounds[i])>.1 for i in range(4)):raise ValueError('PDF 表头四列没有连续物理边界')
    return table,rows,bounds


def _department(row,bounds,page,document,raw_pages):
    values=row['texts'];box=row['cells'][0]
    if (any(compact(value) for value in values[1:]) or row['cells'][1:]!=[None,None,None]
            or abs(box[0]-bounds[0])>.1 or abs(box[2]-bounds[-1])>.1):
        raise ValueError('院系章标题不是实际横跨四列的物理单元格')
    match=re.fullmatch(r'(\d{3})(.+)',_squash(values[0]))
    if not match:raise ValueError('院系章代码或名称无法确认')
    return {'value':match[1]+' '+match[2],'evidence':_refs(values[0],box,page,document,raw_pages),
            'page':page['page'],'bbox':list(box)}


def _courses(value,refs,unit,document):
    subjects,issues=parse_subjects(value,unit['id'],document)
    if issues or value.count('①')!=1 or any(value.count(marker)>1 for marker in '②③④'):
        raise ValueError('原文初试单元缺失、冲突或尚未核准')
    subjects=[{**subject,'evidence':refs} for subject in subjects]
    slots=sorted({subject['exam_unit'] for subject in subjects});groups=[[]]
    for slot in slots:
        choices=[subject for subject in subjects if subject['exam_unit']==slot]
        if len(groups)*len(choices)>4096:raise ValueError('原文备选科目超过安全展开上限')
        groups=[[*group,subject] for group in groups for subject in choices]
    normalized,supported=_matrix(groups)
    return normalized,groups,supported


def _program_row(values,boxes,page,row_index,department,legend,unit,year,document,raw_pages,publication):
    major=re.fullmatch(r'(\*)?([0-9A-Z]{6})(.+)',_squash(values[0]))
    direction=re.fullmatch(r'([0-9A-Z]{2})(.+)',_squash(values[1]));mode=re.fullmatch(r'\(([12])\)(全日制|非全日制)',compact(values[2]))
    if (not major or not direction or not mode or (mode[1]=='1')!=(mode[2]=='全日制')):
        raise ValueError('物理方向的专业、方向代码或明确学习方式不完整')
    refs=[_refs(value,box,page,document,raw_pages) for value,box in zip(values,boxes)]
    subjects,groups,supported=_courses(values[3],refs[3],unit,document)
    degree='专业学位' if major[1] else None
    row=_program(unit,year,department['value'],major[2],major[3],direction[1]+' '+direction[2],mode[2],subjects,'',document,degree=degree)
    locations=list(dict.fromkeys(match[1] for match in re.finditer(r'[（(]([^（）()]*(?:校区|基地)[^（）()]*)[）)]',direction[2])))
    evidence_refs=[*department['evidence'],*[ref for column in refs for ref in column],
                   *([] if degree is None else legend),*publication['evidence']]
    return {**row,'catalog_parser':PARSER,'catalog_document_id':document['id'],'province':None,
            'campus':locations[0] if len(locations)==1 else None,'training_locations':locations,
            **({'campus_evidence':refs[1]} if locations else {}),'evidence':evidence_refs,'exam_evidence':refs[3],
            'exam_combinations':[{'subjects':group} for group in groups],
            'exam_matching_supported':supported and not bool(row.get('subject_identity_issues')),
            'source_locator':{'page':page['page'],'table':1,'row':row_index,'cell_bboxes':[list(box) for box in boxes],
                              'department_page':department['page'],'department_bbox':department['bbox']}}


def parse_hit_public(layout,unit,year,document,*,project_root=PROJECT_ROOT):
    publication=verify_hit_publication(document,unit,year,project_root=project_root)
    if not publication['verified']:raise ValueError(publication['reason'])
    raw_pages=layout.get('raw_text','').rstrip('\f\r\n ').split('\f');pages=layout.get('pages',[])
    if not pages or len(raw_pages)!=len(pages):raise ValueError('原始 PDF 文字页数与物理网格页数不一致')
    programs=[];manifests=[];department=None;sections=0
    for number,page in enumerate(pages,1):
        if page.get('page')!=number:raise ValueError('原始 PDF 页码必须从第一页完整连续')
        table,rows,bounds=_grid(page);legend=_refs(rows[0]['texts'][0],rows[0]['cells'][0],page,document,raw_pages)
        previous_bottom=max(box[3] for box in rows[0]['cells']);count=0
        for index,row in enumerate(rows[1:],1):
            values=row.get('texts',[]);boxes=row.get('cells',[])
            if len(values)!=4 or len(boxes)!=4:raise ValueError('真实目录物理行缺少四列')
            present=[box for box in boxes if box is not None]
            for box in present:_box(box,page)
            if not present or min(box[1] for box in present)<previous_bottom-.1:raise ValueError('PDF 物理行重叠或没有边界')
            previous_bottom=max(box[3] for box in present)
            if re.match(r'^\d{3}(?!\d)',_squash(values[0])):
                department=_department(row,bounds,page,document,raw_pages);sections+=1;continue
            if (len(present)!=4 or any(abs(boxes[i][0]-bounds[i])>.1 or abs(boxes[i][2]-bounds[i+1])>.1 for i in range(4))
                    or len({round(box[1],2) for box in boxes})!=1 or len({round(box[3],2) for box in boxes})!=1):
                raise ValueError('方向及科目没有独立、完整且对齐的四格物理边界')
            if department is None:raise ValueError('首个方向没有原始院系章标题，不猜测归属')
            programs=[*programs,_program_row(values,boxes,page,index,department,legend,unit,year,document,raw_pages,publication)];count+=1
        if raw_pages[number-1].count('①')!=count:raise ValueError('当前原始页初试起始标记与物理方向记录未逐一对账')
        manifests=[*manifests,{'page':number,'table':1,'physical_rows':len(rows),'physical_direction_rows':count,'parsed_records':count,'expected_records':count}]
    if not programs or len({row['id'] for row in programs})!=len(programs):raise ValueError('完整目录为空或存在重复报考身份，不能宣称覆盖')
    return {'programs':programs,'expected_records':len(programs),'issues':[],'retest_rules':[],
            'documents':publication['documents'],'table_manifests':manifests,'excluded_records':[],
            'source_scope':{'type':'full_document','pages':len(pages),'physical_direction_rows':len(programs),'department_sections':sections},
            'publication_proof':publication['attachment_locator']}
