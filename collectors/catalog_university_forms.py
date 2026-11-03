"""Replay the actual Sichuan/Shenzhen public directory responses and their parents."""
from collections import Counter
from functools import lru_cache
import json
import re
from urllib.parse import urlsplit

from .catalog_html import _subjects, visible_catalog_html
from .catalog_public_forms import _Options, _read, _structured
from .catalogs import _program, compact, evidence
from .history import _Tables
from service_verification import plain_text


PARSER='university_form_v1'
PROFILES={'scu':('368479','10610','yz.scu.edu.cn'),
          'szu':('368417','10590','ehall.szu.edu.cn')}
SCU_HEADER=['学科专业代码、专业名称及研究方向','导师姓名','招生人数','考试科目','备注']
ONLY_RECOMMENDATION=re.compile(r'(?:^|[。；;，,])\s*(?:本专业(?:在本(?:学院|单位))?|本方向|该方向)?'
    r'(?:只|仅)(?:招收|接收|招|接)?(?:推荐免试|推免)(?:生)?')


def _profile(unit,year,document):
    context=document.get('public_form_context') or {}
    if not isinstance(context,dict):raise ValueError('公开目录上下文必须为明确的原文标识映射')
    profile=context.get('profile');known=PROFILES.get(profile)
    if not known or (unit.get('id'),str(unit.get('unit_code')))!=(known[0],known[1]) or year!=2027:
        raise ValueError('学校、年度或公开目录格式尚未适配')
    return profile,context


def _at(document,host,path):
    parts=urlsplit(document.get('url',''))
    if (parts.scheme!='https' or parts.hostname!=host or parts.path!=path or parts.query or parts.fragment
            or parts.username or parts.password or parts.port not in {None,443}
            or document.get('request_method')!='GET'):
        raise ValueError('原文地址不是已核准的正常公开目录请求')


def _context_source(context,key,documents,root,unit,year):
    identity=context.get(key);document=documents.get(identity)
    if not isinstance(document,dict) or document.get('id')!=identity or document.get('kind')!='catalog_context':
        raise ValueError('真实公开目录上下文原文缺失')
    return document,_read(document,root,unit,year)


def _select(html,identity):
    blocks=re.findall(r'<select\b([^>]*)>(.*?)</select>',visible_catalog_html(html),re.I|re.S)
    selected=[body for attrs,body in blocks if re.search(r'\bid\s*=\s*["\']'+re.escape(identity)+r'["\']',attrs,re.I)]
    if len(selected)!=1:raise ValueError('实际公开选择控件缺失或重复')
    parser=_Options();parser.feed(selected[0]);rows=parser.rows
    if len({r['code'] for r in rows})!=len(rows) or any(not r['name'] for r in rows):
        raise ValueError('公开目录选项代码重复或名称缺失')
    return rows


def _scu_context(unit,year,document,documents,root):
    _,context=_profile(unit,year,document)
    entry,html=_context_source(context,'entry_document_id',documents,root,unit,year)
    _at(entry,'yz.scu.edu.cn','/sszyml')
    if (unit['name'] not in plain_text(visible_catalog_html(html)) or '/sszyml/simpleiq/' not in html
            or not re.search(r'\$\(["\']#Yearlist["\']\)\.val\(["\']'+str(year)+r'["\']\)',html)
            or str(year) not in {row['code'] for row in _select(html,'Yearlist')}):
        raise ValueError('父页面没有证明本校本年度普通硕士公开目录表单')
    faculties=[row for row in _select(html,'yxslist') if row['code']!='0']
    if not faculties or any(not re.fullmatch(r'\d{3}',row['code']) or not compact(row['name']).startswith(row['code']) for row in faculties):
        raise ValueError('招生院系原始选项身份不明确')
    parts=urlsplit(document.get('url',''))
    match=re.fullmatch(r'/sszyml/simpleiq/(\d{3});0;0;'+str(year)+r';;;',parts.path)
    if not match:raise ValueError('叶子响应不是该年度真实院系全专业查询')
    _at(document,'yz.scu.edu.cn',parts.path)
    selected=[row for row in faculties if row['code']==match[1]]
    if len(selected)!=1:raise ValueError('目录院系不在真实公开选项中')
    return selected[0],faculties,entry


def _scu_program(row,department,major,degree,note,previous,unit,year,document,locator):
    direction=re.fullmatch(r'([0-9A-Z]{2})\s*[（(](全日制|非全日制)[）)]\s*(.+)',row[0])
    if not direction:raise ValueError('方向代码、名称或学习方式未明确')
    if ONLY_RECOMMENDATION.search(note) or re.search(r'(?:仅|只)招收单独考试|^单独考试$',note):
        return None,previous,{'reason':'原文明确为推免或单独考试专用条目，不混入普通统考',
            'evidence':[evidence(document,row[0]),evidence(document,note)],'source_locator':locator},[]
    own=row[3]
    if own=='同上':
        if previous is None:raise ValueError('同上没有同一专业内的明确科目组可引用')
        quote,origin=previous
        refs=[evidence(document,own),evidence(document,quote)]
    else:
        quote,origin=own,locator
        refs=[evidence(document,quote)]
    subjects,_,issues=_subjects(quote,unit['id'],document)
    if issues:raise ValueError('；'.join(issues))
    subjects=[{**subject,'evidence':refs[-1]} for subject in subjects]
    program=_program(unit,year,department,major[0],major[1],direction[1]+' '+direction[3],
        direction[2],subjects,note,document,degree=degree)
    return {**program,'catalog_document_id':document['id'],'catalog_parser':PARSER,
        'source_locator':{**locator,'profile':'scu','exam_origin':origin},
        'evidence':[evidence(document,department),evidence(document,major[0]+' '+major[1]),
                    evidence(document,row[0]),*refs,*([evidence(document,note)] if note else [])],
        'exam_evidence':refs},(quote,origin),None,[]


def _parse_scu(text,unit,year,document,documents,root):
    faculty,_,_= _scu_context(unit,year,document,documents,root)
    parser=_Tables();parser.feed(visible_catalog_html(text))
    if len(parser.tables)!=1:raise ValueError('公开院系目录表缺失或多出未审查表格')
    table=parser.tables[0]
    if not table or [compact(cell) for cell in table[0]]!=[compact(cell) for cell in SCU_HEADER]:
        return {'programs':[],'expected_records':0,'issues':['公开目录表头不匹配'],'retest_rules':[]}
    programs,excluded,issues=[],[],[]
    department,major,degree,previous=None,None,None,None
    previous_note=None
    faculty_note='';expected=0
    for index,row in enumerate(table[1:],1):
        first=row[0] if row else ''
        found_faculty=re.fullmatch(r'(\d{3})(?![0-9A-Z])\s*(.+)',first)
        found_major=re.fullmatch(r'([0-9A-Z]{6})(?![0-9A-Z])\s*(.+)',first)
        if found_faculty:
            if found_faculty[1]!=faculty['code'] or compact(first)!=compact(faculty['name']):
                issues=[*issues,'结果院系身份与实际筛选选项不一致'];department=None
            else:department=first
            major,previous,previous_note=None,None,None;continue
        if found_major:
            major=(found_major[1],found_major[2]);previous,previous_note=None,None
            label=re.fullmatch(r'(专业学位|学术学位)(?:含非全日制人数[:：]\d+)?',compact(row[4])) if len(row)>4 else None
            degree=label[1] if label else None
            continue
        if len(set(row))==1 and len(row)==5:
            faculty_note=first;continue
        if not first:continue
        if not re.match(r'^[0-9A-Z]{2}\s*[（(]',first):
            if re.search(r'[①②③④].*\d{3}', ''.join(row)):
                issues=[*issues,f'原表第{index+1}行存在未消费初试条目']
            continue
        expected+=1
        try:
            if len(row)!=5 or department is None or major is None:
                raise ValueError('方向的原始院系、专业或五列表格关系不完整')
            locator={'table':1,'row':index+1}
            own_note=row[4]
            if own_note=='同上':
                if previous_note is None:raise ValueError('备注同上没有同专业内的明确原文可引用')
                note=previous_note
                note_reference=[evidence(document,own_note),evidence(document,note)]
            else:
                note=own_note;note_reference=[evidence(document,note)] if note else []
                if note:previous_note=note
            program,new_previous,omitted,problems=_scu_program(row,department,major,degree,note,previous,
                unit,year,document,locator)
            previous=new_previous
            if omitted:
                excluded=[*excluded,{**omitted,'evidence':[*omitted['evidence'],*note_reference]}];expected-=1
            if program:
                refs=[*program['evidence'],*note_reference,*([evidence(document,faculty_note)] if faculty_note else [])]
                programs=[*programs,{**program,'evidence':refs,'notes':[*program['notes'],*([faculty_note] if faculty_note else [])]}]
            issues=[*issues,*problems]
        except ValueError as error:issues=[*issues,f'原表第{index+1}行：{error}'];previous=None
    if len({row['id'] for row in programs})!=len(programs):issues=[*issues,'原表招生方向身份重复']
    return {'programs':programs,'expected_records':expected,'excluded_records':excluded,'issues':issues,'retest_rules':[]}


@lru_cache(maxsize=8)
def _szu_entry_valid(html,name,year):
    return (name+str(year)+'年硕士研究生招生专业目录' in compact(plain_text(visible_catalog_html(html)))
            and bool(re.search(r'https?://ehall\.szu\.edu\.cn/gsapp/sys/zsjzapp/index\.do#/'+str(year)+r'(?:/|["\'])',html)))


def _szu_index(unit,year,document,documents,root):
    _,context=_profile(unit,year,document)
    entry,html=_context_source(context,'entry_document_id',documents,root,unit,year)
    _at(entry,'yz.szu.edu.cn','/info/1006/15196.htm')
    if not _szu_entry_valid(html,unit['name'],year):
        raise ValueError('官方公告没有证明本校本年度公开目录入口')
    index,text=_context_source(context,'index_document_id',documents,root,unit,year)
    _at(index,'ehall.szu.edu.cn',f'/gsapp/sys/zsjzapp/temphtml/{year}/data/1/indexdata.js')
    payload=json.loads(text)
    if not isinstance(payload,dict):raise ValueError('原始公开专业列表不是字段对象')
    rows=payload.get('zsjzDatas')
    if payload.get('success') is not True or payload.get('ZSND')!=str(year) or not isinstance(rows,list) or not rows:
        raise ValueError('原始公开专业列表缺失或错年')
    identities=[]
    for row in rows:
        if (not isinstance(row,dict) or not re.fullmatch(r'\d{3}',str(row.get('XBYXDM','')))
                or not re.fullmatch(r'[0-9A-Z]{6}',str(row.get('ZYDM',''))) or not row.get('ZYWID')
                or row.get('PC')!=payload.get('PCWID')):
            raise ValueError('原始公开专业列表院系、专业或批次不完整')
        identities=[*identities,(row['XBYXDM'],row['ZYDM'],row['ZYWID'])]
    if len(set(identities))!=len(identities) or len({i[:2] for i in identities})!=len(identities):
        raise ValueError('原始公开专业身份重复')
    return index,payload,rows


def _szu_context(text,unit,year,document,documents,root):
    index,payload,rows=_szu_index(unit,year,document,documents,root)
    path=urlsplit(document.get('url','')).path
    matched=re.fullmatch(r'/gsapp/sys/zsjzapp/temphtml/'+str(year)+r'/data/4/(\d{3})/([0-9A-Z]{6})/zszy\.js',path)
    if not matched:raise ValueError('科目原文不是实际院系专业叶子地址')
    _at(document,'ehall.szu.edu.cn',path)
    selected=[(i,row) for i,row in enumerate(rows) if (row['XBYXDM'],row['ZYDM'])==matched.groups()]
    if len(selected)!=1:raise ValueError('叶子专业不在真实公开列表中')
    position,row=selected[0];raw=json.loads(text)
    if not isinstance(raw,dict):raise ValueError('原始公开专业叶子不是字段对象')
    major=raw.get('zszy')
    if (not isinstance(major,dict) or major.get('ND')!=str(year) or major.get('PC_DISPLAY')!=str(year)
            or major.get('PC')!=payload['PCWID'] or major.get('XBYXDM')!=row['XBYXDM']
            or major.get('ZYDM')!=row['ZYDM'] or major.get('WID')!=row['ZYWID']
            or major.get('XWLX_DISPLAY')!=row.get('XWLX_DISPLAY')):
        raise ValueError('实际叶子专业年度、院系、身份或批次不一致')
    name=re.fullmatch(re.escape(row['ZYDM'])+r'\s*(.+?)[（(](全日制|非全日制)[）)]',row.get('ZYMC',''))
    department=row.get('YXMC');degree=row.get('XWLX_DISPLAY')
    if (not name or not department or compact(major.get('ZYMC'))!=compact(name[1])
            or compact(major.get('YXMC'))!=compact(row['XBYXDM']+department)
            or degree not in {'学术学位','专业学位'}):
        raise ValueError('专业名称、原始院系或学习方式不能唯一确认')
    return raw,major,row,position,index,name,degree


def _szu_exams(raw,major,unit,document):
    courses=raw.get('zskm')
    if not isinstance(courses,list):raise ValueError('原始初试与复试科目列表缺失')
    initial=[]
    for i,course in enumerate(courses):
        if (not isinstance(course,dict) or course.get('KMLB') not in {'1','2','3'}
                or course.get('ZYML')!=major['WID'] or course.get('PC')!=major['PC']):
            raise ValueError('原始科目所属专业、批次或初复试类别不明确')
        if course['KMLB']=='1':initial=[*initial,(i,course)]
    if len(initial) not in {2,3,4}:raise ValueError('初试科目个数未证明连续完整考试单元')
    subjects=[];refs=[]
    for slot,(i,course) in enumerate(initial,1):
        code=course.get('KMDM');name=course.get('KMMC')
        if not isinstance(code,str) or not re.fullmatch(r'\d{3}',code) or not isinstance(name,str) or not name:
            raise ValueError('初试科目代码或名称缺失')
        proof=_structured(document,f'zskm[{i}]',{key:course[key] for key in ('KMLB','KMDM','KMMC','ZYML','PC')})
        parsed,_,issues=_subjects('',unit['id'],document,{slot:code+name})
        problems=[issue for issue in issues if '初试单元缺失' not in issue]
        if len(parsed)!=1 or problems:raise ValueError('原始初试科目不能唯一解码')
        subjects=[*subjects,{**parsed[0],'name':name,'evidence':proof}];refs=[*refs,proof]
    if len({s['code'] for s in subjects})!=len(subjects):raise ValueError('原始初试科目存在重复代码')
    return subjects,refs


def _parse_szu(text,unit,year,document,documents,root):
    raw,major,row,position,index,name,degree=_szu_context(text,unit,year,document,documents,root)
    directions=raw.get('fxds')
    if not isinstance(directions,list) or not directions:raise ValueError('原始专业方向列表为空')
    note=plain_text(major.get('BKYQ') or '').strip();programs,excluded,issues=[],[],[]
    identity=_structured(index,f'zsjzDatas[{position}]',{key:row[key] for key in
        ('XBYXDM','YXMC','ZYDM','ZYMC','ZYWID','PC','XWLX_DISPLAY')})
    major_proof=_structured(document,'zszy',{key:major[key] for key in
        ('ND','PC_DISPLAY','PC','XBYXDM','YXMC','ZYDM','ZYMC','WID','XWLX_DISPLAY','BKYQ') if key in major})
    restricted=bool(ONLY_RECOMMENDATION.search(note))
    initial=any(isinstance(course,dict) and course.get('KMLB')=='1' for course in raw.get('zskm') or [])
    only=restricted and not initial
    def restricted_direction(direction):
        return isinstance(direction,dict) and bool(re.fullmatch(
            r'(?:只|仅)(?:招收|接收|招|接)?(?:推荐免试|推免)(?:生|招生)?[。.]?',compact(plain_text(direction.get('BZ') or ''))))
    if restricted and initial and not any(restricted_direction(direction) for direction in directions):
        raise ValueError('正文推免限制范围没有方向级原始备注可唯一定位')
    try:subjects,refs=_szu_exams(raw,major,unit,document) if not only else ([],[])
    except ValueError as error:return {'programs':[],'expected_records':len(directions),'excluded_records':[],
        'issues':[str(error)],'retest_rules':[]}
    for i,direction in enumerate(directions):
        try:
            if (not isinstance(direction,dict) or not re.fullmatch(r'[0-9A-Z]{2}',direction.get('ZYFXDM',''))
                    or not direction.get('ZYFXMC') or direction.get('ZYML')!=major['WID'] or direction.get('PC')!=major['PC']):
                raise ValueError('原始方向身份、名称或所属专业不一致')
            proof=_structured(document,f'fxds[{i}]',{key:direction[key] for key in ('ZYFXDM','ZYFXMC','ZYML','PC','BZ') if key in direction})
            if only or restricted_direction(direction):
                excluded=[*excluded,{'reason':'原文明确仅接收推免生','evidence':[identity,major_proof,proof]}];continue
            program=_program(unit,year,row['XBYXDM']+' '+row['YXMC'],row['ZYDM'],name[1],
                direction['ZYFXDM']+' '+direction['ZYFXMC'],name[2],subjects,note,document,degree=degree)
            programs=[*programs,{**program,'catalog_document_id':document['id'],'catalog_parser':PARSER,
                'source_locator':{'profile':'szu','field_path':f'fxds[{i}]'},
                'evidence':[identity,major_proof,proof,*refs],'exam_evidence':refs}]
        except ValueError as error:issues=[*issues,f'原始方向第{i+1}条：{error}']
    if len({row['id'] for row in programs})!=len(programs):issues=[*issues,'原始专业方向身份重复']
    return {'programs':programs,'expected_records':len(directions)-len(excluded),'excluded_records':excluded,
            'issues':issues,'retest_rules':[]}


def parse_university_form(text,unit,year,document,*,project_root,documents):
    profile,context=_profile(unit,year,document)
    if text!=_read(document,project_root,unit,year):raise ValueError('待解析内容不是实际保存的原始响应')
    parser={'scu':_parse_scu,'szu':_parse_szu}[profile]
    result=parser(text,unit,year,document,documents,project_root)
    related=[documents[value] for key,value in context.items() if key.endswith('_document_id') and value in documents]
    return {**result,'documents':related}


def university_form_scope(unit,year,documents,*,project_root):
    empty={'complete':False,'expected_records':0,'expected_document_ids':[]}
    leaves=[doc for doc in documents.values() if doc.get('catalog_parser')==PARSER and doc.get('unit_id')==unit.get('id')
            and doc.get('year')==year and doc.get('catalog_active',True)]
    try:
        if not leaves:raise ValueError('尚无可重放的本年度公开目录响应')
        profile,context=_profile(unit,year,leaves[0]);observed=[];records=0
        if profile=='scu':
            _,faculties,_=_scu_context(unit,year,leaves[0],documents,project_root)
            expected=[row['code'] for row in faculties]
        else:
            _,_,rows=_szu_index(unit,year,leaves[0],documents,project_root)
            expected=[(row['XBYXDM'],row['ZYDM'],row['ZYWID']) for row in rows]
        for leaf in leaves:
            other,leaf_context=_profile(unit,year,leaf)
            if other!=profile or leaf_context!=context:raise ValueError('同校公开目录父来源上下文不一致')
            text=_read(leaf,project_root,unit,year)
            if profile=='scu':
                faculty,_,_=_scu_context(unit,year,leaf,documents,project_root);key=faculty['code']
            else:
                _,_,row,_,_,_,_=_szu_context(text,unit,year,leaf,documents,project_root)
                key=row['XBYXDM'],row['ZYDM'],row['ZYWID']
            parsed=parse_university_form(text,unit,year,leaf,project_root=project_root,documents=documents)
            if parsed['issues']:raise ValueError('原始目录尚有未解决行、引用或科目问题')
            observed=[*observed,key];records+=len(parsed['programs'])
        complete=Counter(expected)==Counter(observed)
        return {'complete':complete,'expected_records':records,'expected_document_ids':sorted(doc['id'] for doc in leaves),
                'issues':[] if complete else ['真实公开选项与实际结果响应未逐项对账一致']}
    except (KeyError,ValueError,TypeError,OSError) as error:return {**empty,'issues':[str(error)]}
