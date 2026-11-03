"""NUAA's public, client-rendered catalog: constrained JSON literals and exact direction groups."""
from collections import Counter
import hashlib
import json
import re
from urllib.parse import parse_qs,urlsplit

from .catalog_public_forms import _read,_structured
from .catalog_public_aspx import _tree
from .catalogs import _program,compact,evidence,parse_subjects
from service_verification import plain_text

PARSER='nuaa_public_v1'
ENTRY_SHA='f9f6030b2f079837269b14636a5879056316f8d49033aaa4dfc8d24b0530c644'
CONTROLLER_SHA='0e989e5bd430e522367de349e523840787fa286ef34fa99377366d1486f5277e'
SYSTEM='yzsbm.nuaa.edu.cn'
PUBLICATION='/2026/0928/c13487a411935/page.htm'


def _json_object(pairs):
    if len({key for key,value in pairs})!=len(pairs):raise ValueError('JSON字面量对象含重复字段')
    return dict(pairs)


def _non_json_constant(value):
    raise ValueError('JSON字面量含非JSON数值常量：'+value)


def decode_nuaa_literals(text):
    """Accept only three JSON-literal assignments. Never evaluate downloaded JavaScript."""
    if not isinstance(text,str) or len(text)>2*1024*1024:raise ValueError('原始目录脚本类型或大小超出核准范围')
    decoder=json.JSONDecoder(object_pairs_hook=_json_object,parse_constant=_non_json_constant);position=0;values={};names=[]
    while text[position:].strip():
        match=re.match(r'\s*var\s+(treeData|mainData|zsml)\s*=\s*',text[position:])
        if not match:raise ValueError('目录数据包含未核准的JavaScript语句，未执行')
        name=match[1]
        if name in values:raise ValueError('目录数据的变量重复')
        try:value,end=decoder.raw_decode(text,position+match.end())
        except (json.JSONDecodeError,RecursionError) as error:raise ValueError('原始目录JSON字面量无法安全解析') from error
        separator=re.match(r'\s*;\s*',text[end:])
        if not separator or not isinstance(value,dict):raise ValueError('目录变量必须是以分号结尾的JSON对象字面量')
        position=end+separator.end();values={**values,name:value};names=[*names,name]
    if names!=['treeData','mainData','zsml']:raise ValueError('公开目录三个原始变量缺失或顺序不一致')
    return values


def _url(document,path):
    parsed=urlsplit(document.get('url',''));query=parse_qs(parsed.query,keep_blank_values=True)
    if (parsed.scheme!='https' or parsed.hostname!=SYSTEM or parsed.path!=path or parsed.fragment or parsed.username
            or parsed.password or parsed.port not in {None,443} or document.get('request_method')!='GET'):
        raise ValueError('目录来源不属于真实南航公开GET地址')
    if path.endswith('.js'):
        if set(query)!={'v'} or len(query['v'])!=1 or not re.fullmatch(r'\d{13}',query['v'][0]):raise ValueError('目录脚本没有保存正常界面的实际版本请求')
    elif query:raise ValueError('公开目录入口含未核准筛选参数')
    return query


def _context(unit,year,document,documents,root):
    context=document.get('public_form_context') or {}
    required={'profile','entry_document_id','controller_document_id','publication_document_id'}
    if (unit.get('id')!='368181' or str(unit.get('unit_code'))!='10287' or year!=2027
            or not isinstance(context,dict) or set(context)!=required or context.get('profile')!='nuaa'):
        raise ValueError('南航招生单位、年度或公开原文链尚未匹配')
    version=_url(document,'/sszsml/js/data.js')
    ids=[context[key] for key in required if key!='profile']
    if any(not isinstance(identity,str) for identity in ids) or len(set(ids))!=3:raise ValueError('目录原文上下文身份缺失或重复')
    sources={key:documents.get(context[key]) for key in required if key!='profile'}
    if any(not isinstance(source,dict) for source in sources.values()):raise ValueError('公开查询入口、界面脚本或年度公告原文缺失')
    entry=sources['entry_document_id'];controller=sources['controller_document_id'];publication=sources['publication_document_id']
    _url(entry,'/sszsml/index.html')
    if _url(controller,'/sszsml/js/zsml.js')!=version:raise ValueError('数据和界面脚本不是同一正常页面加载链')
    entry_text=_read(entry,root,unit,year);controller_text=_read(controller,root,unit,year)
    if (hashlib.sha256(entry_text.encode()).hexdigest()!=ENTRY_SHA or hashlib.sha256(controller_text.encode()).hexdigest()!=CONTROLLER_SHA):
        raise ValueError('公开界面模板或解释脚本版本变化，须重新审核可见字段及转换规则')
    parts=urlsplit(publication.get('url',''))
    if (parts.scheme!='https' or parts.hostname!='www.graduate.nuaa.edu.cn' or parts.path!=PUBLICATION
            or parts.query or parts.fragment or parts.username or parts.password or parts.port not in {None,443}
            or publication.get('request_method')!='GET'):
        raise ValueError('目录未链接到已核准的2027官方年度公告')
    tree=_tree(_read(publication,root,unit,year))
    articles=[node for node in tree.find('div') if node.attrs.get('portletmode')=='simpleArticleAttri' and not node.hidden]
    if len(articles)!=1:raise ValueError('年度公告正文区域缺失或重复')
    headings=[compact(node.text()) for node in articles[0].find('h3') if 'title' in node.attrs.get('class','').split() and not node.hidden]
    title='2027年硕士研究生招生专业目录'
    bodies=[node for node in articles[0].find('div') if 'wp_articlecontent' in node.attrs.get('class','').split() and not node.hidden]
    links=[node for body in bodies for node in body.find('a') if not node.hidden and node.attrs.get('href')=='http://yzsbm.nuaa.edu.cn/sszsml/index.html']
    if headings!=[title] or len(links)!=1 or '南京航空航天大学' not in tree.text():raise ValueError('公告自身年度、单位或正常目录入口链接无法核对')
    return publication,title


def _originals(values,unit,year,document):
    meta=values['zsml'];tree=values['treeData'];main=values['mainData']
    if meta!={'name':unit['name'],'nd':str(year),'zslb':'1','mllb':'12'}:
        raise ValueError('原始目录自身的招生学校、年度、硕士类别或目录范围不匹配')
    if tree.get('text')!=unit['name'] or tree.get('value')!='0' or set(main)!={'mllb_12'}:
        raise ValueError('可见学院树与目录类型不一致')
    options=tree.get('items');faculties=main['mllb_12']
    if (not isinstance(options,list) or not options or not isinstance(faculties,list)
            or any(not isinstance(item,dict) for item in [*options,*faculties])):raise ValueError('原始可见学院列表缺失或条目类型无效')
    choices={}
    for row in options:
        code=row.get('value');name=row.get('text')
        if (not isinstance(code,str) or not re.fullmatch(r'\d{3}',code) or row.get('YXSH')!=code
                or not isinstance(name,str) or not name.startswith(code+' ') or code in choices or row.get('feature')!='dict'):
            raise ValueError('可见学院选项代码、名称或唯一性未核对')
        choices={**choices,code:name[len(code)+1:]}
    if Counter(f.get('yxsh') for f in faculties)!=Counter(choices.keys()):raise ValueError('全部可见学院与实际目录记录未一一对账')
    for faculty in faculties:
        if (faculty.get('zslb')!='1' or faculty.get('yxmc')!=choices[faculty['yxsh']] or not isinstance(faculty.get('exportZsmlYxZys'),list)
                or not isinstance(faculty.get('yxbz'),str)):
            raise ValueError('学院目录名称、硕士类型或专业列表不一致')
    return faculties


def _subject_groups(raw,directions,unit,document,proof):
    if not isinstance(raw,str):raise ValueError('原始专业考试科目不是文本')
    text=compact(plain_text(raw))
    placeholder='①188只招收推免生②266只招收推免生③799只招收推免生④899只招收推免生'
    if text==placeholder:return None
    codes=[compact(d.get('fxm')) for d in directions]
    if len(set(codes))!=len(codes):raise ValueError('同专业方向代码重复，不能猜测科目归属')
    parts=[compact(plain_text(part)) for part in re.split(r'<br\s*/?>',raw,flags=re.I) if compact(plain_text(part))]
    result={}
    for part in parts:
        match=re.fullmatch(r'([0-9A-Z]{2}(?:[、，,][0-9A-Z]{2})*)方向[:：](.+)',part)
        if match:selected=re.split('[、，,]',match[1]);exam=match[2]
        elif len(parts)==1 and part.startswith('①'):selected=codes;exam=part
        else:raise ValueError('专业科目包含未明确适配的方向条件或多组选择')
        subjects,issues=parse_subjects(exam,unit['id'],document)
        if issues or exam.count('①')!=1 or any(re.match(r'\d',subject['name']) for subject in subjects):
            raise ValueError('原文初试单元、代码及完整科目名称不能逐项核对')
        subjects=[{**subject,'evidence':proof} for subject in subjects]
        for code in selected:
            if code not in codes or code in result:raise ValueError('方向条件重复、引用不存在方向或未明确适用范围')
            result={**result,code:subjects}
    if set(result)!=set(codes):raise ValueError('部分原始方向没有唯一对应的初试科目')
    return result


def _major_rows(faculty,major,fi,mi,unit,year,document,base_refs):
    if not isinstance(major,dict):raise ValueError('原始专业条目不是公开JSON对象')
    code=major.get('zydm');directions=major.get('exportZsmlYxZyYjfxs');mode_labels={'1':'全日制','2':'非全日制'}
    if (major.get('yxsh')!=faculty['yxsh'] or not isinstance(code,str) or not re.fullmatch(r'[0-9A-Z]{6}',code)
            or not isinstance(major.get('zymc'),str) or not major['zymc'] or major.get('xwlb') not in {'1','2'}
            or not isinstance(major.get('zybz'),str) or not isinstance(directions,list) or not directions
            or any(not isinstance(direction,dict) for direction in directions)):raise ValueError('专业身份、明确学位类别或方向列表缺失')
    path=f'mainData.mllb_12[{fi}].exportZsmlYxZys[{mi}]'
    fields={key:major[key] for key in ['yxsh','zydm','zymc','xwlb','kskm','zybz'] if key in major}
    major_ref=_structured(document,path,fields);exam_ref=_structured(document,path,{'kskm':major['kskm']})
    groups=_subject_groups(major['kskm'],directions,unit,document,exam_ref);programs=[];excluded=[]
    notes=[plain_text(value).strip() for value in [faculty.get('yxbz',''),major.get('zybz','')] if plain_text(value).strip()]
    for di,direction in enumerate(directions):
        if (direction.get('yxsh')!=faculty['yxsh'] or direction.get('zydm')!=code or direction.get('xxfs') not in mode_labels
                or not isinstance(direction.get('fxm'),str) or not re.fullmatch(r'[0-9A-Z]{2}',compact(direction['fxm']))
                or not isinstance(direction.get('fxmc'),str) or not direction['fxmc']):raise ValueError('方向原文的学院、专业、代码、名称或学习方式不一致')
        identity=_structured(document,path+f'.exportZsmlYxZyYjfxs[{di}]',{key:direction[key] for key in ['yxsh','zydm','fxm','fxmc','xxfs']})
        if groups is None:
            excluded=[*excluded,{'document_id':document['id'],'reason':'原始科目明确只招收推免生，非统考初试课程',
                                'field_path':identity['field_path'],'evidence':[major_ref,identity]}];continue
        subjects=groups[compact(direction['fxm'])]
        row=_program(unit,year,faculty['yxsh']+' '+faculty['yxmc'],code,major['zymc'],compact(direction['fxm'])+' '+direction['fxmc'],
            mode_labels[direction['xxfs']],subjects,'；'.join(notes),document,degree={'1':'学术学位','2':'专业学位'}[major['xwlb']])
        programs=[*programs,{**row,'catalog_parser':PARSER,'catalog_document_id':document['id'],'evidence':[*base_refs,major_ref,identity],
            'exam_evidence':[exam_ref],'exam_matching_supported':not bool(row.get('subject_identity_issues')),'notes':notes,
            'source_locator':{'profile':'nuaa','field_path':identity['field_path'],'exam_major_path':path}}]
    return programs,excluded


def parse_nuaa_public(text,unit,year,document,*,project_root,documents):
    publication,title=_context(unit,year,document,documents,project_root)
    if text!=_read(document,project_root,unit,year):raise ValueError('原始目录脚本与传入解析文字不一致')
    values=decode_nuaa_literals(text);faculties=_originals(values,unit,year,document);programs=[];excluded=[];majors=0
    global_ref=_structured(document,'zsml',values['zsml'])
    for fi,faculty in enumerate(faculties):
        faculty_ref=_structured(document,f'mainData.mllb_12[{fi}]',{key:faculty[key] for key in ['yxsh','yxmc','zslb','yxbz']})
        refs=[evidence(publication,title),global_ref,faculty_ref]
        for mi,major in enumerate(faculty['exportZsmlYxZys']):
            rows,omitted=_major_rows(faculty,major,fi,mi,unit,year,document,refs)
            programs=[*programs,*rows];excluded=[*excluded,*omitted];majors+=1
    if len({row['id'] for row in programs})!=len(programs):raise ValueError('完整公开目录内的报考方向身份重复')
    return {'programs':programs,'expected_records':len(programs),'excluded_records':excluded,'issues':[],'retest_rules':[],
            'faculty_count':len(faculties),'major_count':majors}


def nuaa_public_scope(unit,year,documents,*,project_root):
    try:
        originals=[document for document in documents.values() if document.get('unit_id')==unit.get('id') and document.get('year')==year
                   and document.get('catalog_parser')==PARSER and document.get('catalog_active',True)]
        if len(originals)!=1:raise ValueError('南航全目录原始数据脚本缺失或重复')
        document=originals[0];parsed=parse_nuaa_public(_read(document,project_root,unit,year),unit,year,document,project_root=project_root,documents=documents)
        return {'complete':True,'expected_records':len(parsed['programs']),'expected_document_ids':[document['id']],
                'faculty_count':parsed['faculty_count'],'major_count':parsed['major_count'],'issues':[]}
    except (ValueError,KeyError,TypeError,IndexError,OSError) as error:
        return {'complete':False,'expected_records':0,'expected_document_ids':[],'issues':[str(error)]}
