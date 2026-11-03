"""Typed public catalog forms: replay actual options, requests and visible results."""
from collections import Counter
from html.parser import HTMLParser
from itertools import product
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from .catalogs import _program, compact, evidence, parse_subjects
from .catalog_html import visible_catalog_html
from .documents import decode_text
from service_verification import canonical, plain_text


PARSER = 'public_form_v1'
BUAA = {'host':'yzb.buaa.edu.cn','unit_id':'367883','unit_code':'10006',
        'entry':'/xlss/zymlcx.htm','result':'/ssmlcxjg.jsp','options':'/system/resource/importdata/getData.jsp'}
COMMON = {'siteName':'1403782683','templateid':'2'}


def _read(document, root, unit, year):
    if document.get('unit_id')!=unit['id'] or document.get('year')!=year:
        raise ValueError('公开表单原文的学校或年度不一致')
    path=(Path(root)/document.get('local_path','')).resolve()
    if not path.is_relative_to(Path(root).resolve()/'data/snapshots') or not path.is_file() or not 0<path.stat().st_size<=32*1024*1024:
        raise ValueError('公开表单原文缺失、越界或超过读取上限')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=document.get('sha256'):
        raise ValueError('公开表单原文摘要不一致')
    return decode_text(raw,document.get('content_type',''))


def _url(document,path,query=None):
    parts=urlsplit(document.get('url',''))
    if (parts.scheme!='https' or parts.hostname!=BUAA['host'] or parts.username or parts.password
            or parts.port not in {None,443} or parts.path!=path or parts.fragment
            or parse_qs(parts.query,keep_blank_values=True)!=(query or {})):
        raise ValueError('公开表单地址不属于已核准的实际页面')


def _request(document,params):
    body=document.get('request_body')
    if (document.get('request_method')!='POST' or not isinstance(body,str)
            or hashlib.sha256(body.encode()).hexdigest()!=document.get('request_sha256')
            or parse_qs(body,keep_blank_values=True)!={key:[value] for key,value in params.items()}):
        raise ValueError('公开表单请求参数、上下文或摘要不一致')


class _Options(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows,self.current=[],None
    def handle_starttag(self,tag,attrs):
        if tag=='option':
            self.current={'code':dict(attrs).get('value',''),'name':''}
    def handle_data(self,data):
        if self.current is not None:self.current={**self.current,'name':self.current['name']+data}
    def handle_endtag(self,tag):
        if tag=='option' and self.current is not None:
            if self.current['code']:self.rows=[*self.rows,{**self.current,'name':self.current['name'].strip()}]
            self.current=None


def _options(document,params,root,unit,year):
    _url(document,BUAA['options']);_request(document,{**COMMON,**params})
    html=visible_catalog_html(_read(document,root,unit,year))
    parser=_Options();parser.feed(html);parser.close()
    rows=parser.rows
    if not rows or len({row['code'] for row in rows})!=len(rows) or any(not row['name'] for row in rows):
        raise ValueError('公开目录选项为空、重复或名称不明确')
    return rows


def _entry(document,root,unit,year):
    _url(document,BUAA['entry'])
    html=_read(document,root,unit,year)
    visible=visible_catalog_html(html)
    title='招收'+str(year)+'年学历硕士专业目录及初试考试科目查询'
    if (document.get('request_method')!='GET' or title not in compact(plain_text(visible))
            or unit['name'] not in html or BUAA['options'] not in html
            or not all(value in html for value in COMMON.values())
            or not re.search(r'<form\b[^>]*action=["\'][^"\']*ssmlcxjg\.jsp\?wbtreeid=1018',visible,re.I)
            or not all(re.search(r'<select\b[^>]*name=["\']'+name+r'["\']',visible,re.I) for name in ('xydm','zydm','yjfxdm'))):
        raise ValueError('年度入口未证明该公开选择表单及其请求接口')


def _profile(unit,year,document):
    context=document.get('public_form_context')
    if (unit.get('id')!=BUAA['unit_id'] or str(unit.get('unit_code'))!=BUAA['unit_code']
            or year!=2027 or not isinstance(context,dict) or context.get('profile')!='buaa'):
        raise ValueError('此学校、年度或公开表单格式尚未适配')
    return context


def _chain(unit,year,document,documents,root):
    context=_profile(unit,year,document)
    _url(document,BUAA['result'],{'wbtreeid':['1018']})
    params=parse_qs(document.get('request_body',''),keep_blank_values=True)
    if set(params)!={'xydm','zydm','yjfxdm'} or any(len(value)!=1 for value in params.values()):
        raise ValueError('目录查询必须保留唯一院系、专业及方向')
    query={key:value[0] for key,value in params.items()}
    if not re.fullmatch(r'\d{3}',query['xydm']) or not re.fullmatch(r'[0-9A-Z]{6}',query['zydm']) or not re.fullmatch(r'[0-9A-Z]{2}',query['yjfxdm']):
        raise ValueError('目录查询代码格式不正确')
    _request(document,query)
    def source(key):
        value=documents.get(context.get(key))
        if not isinstance(value,dict):raise ValueError('公开目录选项证明缺失')
        return value
    _entry(source('entry_document_id'),root,unit,year)
    departments=_options(source('department_options_id'),{'code_root_mc':'xymc','code_root_dm':'xydm'},root,unit,year)
    majors=_options(source('major_options_id'),{'parentCode':'xydm','parentValue':query['xydm'],'code_root_mc':'zymc','code_root_dm':'zydm'},root,unit,year)
    directions=_options(source('direction_options_id'),{'parentCode':'xydm,zydm','parentValue':query['xydm']+','+query['zydm'],'code_root_mc':'yjfxmc','code_root_dm':'yjfxdm'},root,unit,year)
    selected=[]
    for rows,key in ((departments,'xydm'),(majors,'zydm'),(directions,'yjfxdm')):
        matches=[row for row in rows if row['code']==query[key]]
        if len(matches)!=1:raise ValueError('查询身份不在正常公开选项中')
        selected=[*selected,matches[0]]
    return query,selected


def _buaa_fields(html,year):
    visible=visible_catalog_html(html)
    title='招收'+str(year)+'年学历硕士专业目录及初试考试科目查询'
    if title not in compact(plain_text(visible)):raise ValueError('公开目录结果缺少正确年度标题')
    blocks=re.findall(r'<ul\b[^>]*class=["\']zyinfo["\'][^>]*>(.*?)</ul>',visible,re.S|re.I)
    if len(blocks)!=1:raise ValueError('公开目录身份区域缺失或重复')
    pairs=re.findall(r'<li\b[^>]*>\s*<b\b[^>]*>(.*?)</b>(.*?)</li>',blocks[0],re.S|re.I)
    fields={compact(plain_text(label)).rstrip('：:'):plain_text(value).strip() for label,value in pairs}
    if len(fields)!=len(pairs):raise ValueError('公开目录身份字段重复')
    required={'学院代码及名称','专业代码及名称','研究方向名称','专业备注'}
    if not required<=set(fields):raise ValueError('公开目录身份、方向或学习方式说明缺失')
    return visible,fields


def _buaa_exams(visible,unit,document):
    tables=re.findall(r'<table\b[^>]*class=["\']ksinfo["\'][^>]*>(.*?)</table>',visible,re.S|re.I)
    if len(tables)!=1:raise ValueError('初试科目表缺失或重复')
    rows=re.findall(r'<tr\b[^>]*>(.*?)</tr>',tables[0],re.S|re.I)
    cells=[[plain_text(value).strip() for value in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>',row,re.S|re.I)] for row in rows]
    if not cells or [compact(value) for value in cells[0]]!=['考试科目单元','考试科目代码','考试科目名称']:
        raise ValueError('初试科目表头无法确认')
    grouped={};declared=set();unused=set();previous_slot=None
    for row in cells[1:]:
        match=re.fullmatch(r'第([一二三四])门考试科目',compact(row[0])) if len(row)==3 else None
        alternative=len(row)==3 and compact(row[0])=='或' and previous_slot in grouped
        if (not match and not alternative) or not row[2]:raise ValueError('初试单元、代码或名称缺失')
        slot='一二三四'.index(match[1])+1 if match else previous_slot
        previous_slot=slot
        declared=declared|{slot}
        if re.fullmatch(r'[-—－]+',row[1]) and compact(row[2])=='无':
            unused=unused|{slot};continue
        codes=re.split(r'\s*或\s*',row[1]);names=re.split(r'\s*或\s*',row[2])
        if len(codes)!=len(names) or any(not re.fullmatch(r'\d{3}',code) or not name for code,name in zip(codes,names)):
            raise ValueError('备选科目的代码和名称不能逐项配对')
        grouped={**grouped,slot:[*grouped.get(slot,[]),*zip(codes,names)]}
    if declared!=set(range(1,max(declared,default=0)+1)) or unused&set(grouped):raise ValueError('初试单元声明有缺口或相互冲突')
    if set(grouped) not in ({1,2},{1,2,3},{1,2,3,4}):raise ValueError('初试考试单元不完整')
    if any(len(set(rows))!=len(rows) for rows in grouped.values()):raise ValueError('同一考试单元存在重复科目')
    text=''.join('①②③④'[slot-1]+'或'.join(code+name for code,name in grouped[slot]) for slot in sorted(grouped))
    subjects,issues=parse_subjects(text,unit['id'],document)
    if any('单元缺失' in issue or '无法解析' in issue for issue in issues):raise ValueError('初试科目不能完整解析')
    quote=plain_text(tables[0]).strip()
    return [{**subject,'evidence':evidence(document,quote)} for subject in subjects],quote,issues


def _buaa_rows(fields,query,major,subjects,refs,quote,unit,year,document):
    modes=set(re.findall(r'(?<!非)全日制|非全日制',fields['专业备注']))
    if not modes:raise ValueError('专业或学习方式不能唯一确认')
    mode_sections=list(re.finditer(r'(?<!非)全日制|非全日制',fields['专业备注']))
    rows=[]
    for mode in sorted(modes):
        note=fields['专业备注']
        if len(modes)>1:
            sections=[note[m.start():mode_sections[i+1].start() if i+1<len(mode_sections) else len(note)]
                      for i,m in enumerate(mode_sections) if m.group()==mode]
            section=sections[0] if len(sections)==1 else ''
        else:section=note
        durations=re.findall(r'学制\s*([1-9](?:\.5)?)\s*年',section)
        duration=durations[0] if len(set(durations))==1 else None
        row=_program(unit,year,fields['学院代码及名称'],major[1],major[2],query['yjfxdm']+' '+fields['研究方向名称'],
            mode,subjects,note,document,degree=major[3])
        rows=[*rows,{**row,'catalog_document_id':document['id'],'catalog_parser':PARSER,
            'source_locator':{'form':query},'evidence':refs,'exam_evidence':evidence(document,quote),
            'notes':[note,*(['专业拟招收人数：'+fields['专业拟招收人数']+'；原文专业口径，不归为方向名额。'] if fields.get('专业拟招收人数') else [])],
            **({'duration_years':float(duration) if '.' in duration else int(duration),
                'duration_evidence':evidence(document,section)} if duration else {})}]
    return rows


def parse_public_form_html(html,unit,year,document,*,documents,project_root):
    """Parse one real result; the original option chain is mandatory and independently replayed."""
    profile=(document.get('public_form_context') or {}).get('profile')
    if profile=='ezui':return _parse_ezui(html,unit,year,document,documents,project_root)
    if profile=='zju':return _parse_zju(html,unit,year,document,documents,project_root)
    query,selected=_chain(unit,year,document,documents,project_root)
    original=_read(document,project_root,unit,year)
    if original!=html:raise ValueError('传入解析文本不是保存的原始响应')
    visible,fields=_buaa_fields(html,year)
    raw_exam=re.findall(r'<table\b[^>]*class=["\']ksinfo["\'][^>]*>(.*?)</table>',html,re.S|re.I)
    visible_exam=re.findall(r'<table\b[^>]*class=["\']ksinfo["\'][^>]*>(.*?)</table>',visible,re.S|re.I)
    if len(raw_exam)!=1 or len(visible_exam)!=1 or len(re.findall(r'<tr\b',raw_exam[0],re.I))!=len(re.findall(r'<tr\b',visible_exam[0],re.I)):
        raise ValueError('初试表含未公开的行，尚未核准完整可见结构')
    if any(canonical(fields[key])!=canonical(choice['name']) for key,choice in zip(
            ('学院代码及名称','专业代码及名称','研究方向名称'),selected)):
        raise ValueError('结果身份与正常公开选项不一致')
    major=re.fullmatch(r'([0-9A-Z]{6})(.+?)(?:[（(](学术学位|专业学位)[）)])?',fields['专业代码及名称'])
    if not major:raise ValueError('专业代码或名称不能唯一确认')
    subjects,quote,issues=_buaa_exams(visible,unit,document)
    refs=[evidence(document,label+'：'+fields[label]) for label in ('学院代码及名称','专业代码及名称','研究方向名称','专业备注')]
    refs=[*refs,evidence(document,quote)]
    rows=_buaa_rows(fields,query,major,subjects,refs,quote,unit,year,document)
    return {'programs':rows,'expected_records':len(rows),'issues':issues,'retest_rules':[]}


def public_form_scope(unit,year,documents,*,project_root):
    """Rebuild every selectable identity from the saved option tree; no saved counter is trusted."""
    leaves=[doc for doc in documents.values() if doc.get('catalog_parser')==PARSER
            and doc.get('unit_id')==unit.get('id') and doc.get('year')==year and doc.get('catalog_active',True)]
    empty={'complete':False,'expected_records':0,'expected_document_ids':[],'issues':['尚无可重放的公开表单结果']}
    if not leaves:return empty
    if (leaves[0].get('public_form_context') or {}).get('profile')=='ezui':
        return _ezui_scope(unit,year,documents,project_root)
    if (leaves[0].get('public_form_context') or {}).get('profile')=='zju':
        return _zju_scope(unit,year,documents,project_root)
    try:
        first=leaves[0];context=_profile(unit,year,first)
        _entry(documents[context['entry_document_id']],project_root,unit,year)
        departments=_options(documents[context['department_options_id']],{'code_root_mc':'xymc','code_root_dm':'xydm'},project_root,unit,year)
        expected=set();observed=[];records=0
        for department in departments:
            major_params={**COMMON,'parentCode':'xydm','parentValue':department['code'],'code_root_mc':'zymc','code_root_dm':'zydm'}
            majors=[doc for doc in documents.values() if doc.get('request_method')=='POST'
                    and parse_qs(doc.get('request_body',''))=={key:[value] for key,value in major_params.items()}]
            if len(majors)!=1:raise ValueError('学院专业选项原文缺失或重复')
            for major in _options(majors[0],{key:value for key,value in major_params.items() if key not in COMMON},project_root,unit,year):
                direction_params={**COMMON,'parentCode':'xydm,zydm','parentValue':department['code']+','+major['code'],'code_root_mc':'yjfxmc','code_root_dm':'yjfxdm'}
                direction_docs=[doc for doc in documents.values() if doc.get('request_method')=='POST'
                    and parse_qs(doc.get('request_body',''))=={key:[value] for key,value in direction_params.items()}]
                if len(direction_docs)!=1:raise ValueError('专业方向选项原文缺失或重复')
                directions=_options(direction_docs[0],{key:value for key,value in direction_params.items() if key not in COMMON},project_root,unit,year)
                expected=expected|{(department['code'],major['code'],direction['code']) for direction in directions}
        for leaf in leaves:
            query,_=_chain(unit,year,leaf,documents,project_root)
            parsed=parse_public_form_html(_read(leaf,project_root,unit,year),unit,year,leaf,documents=documents,project_root=project_root)
            if parsed['issues']:raise ValueError('目录结果科目仍有未解决问题')
            records+=len(parsed['programs'])
            observed=[*observed,tuple(query[key] for key in ('xydm','zydm','yjfxdm'))]
        complete=Counter(observed)==Counter(expected)
        return {'complete':complete,'expected_records':records if complete else len(expected),'expected_document_ids':sorted(doc['id'] for doc in leaves),
                'issues':[] if complete else ['公开目录选项与实际结果集合尚未对账一致']}
    except (KeyError,ValueError,TypeError,OSError) as error:
        return {**empty,'issues':[str(error)]}


EZUI_HOSTS={'368278':'yanzhao.ujn.edu.cn','368298':'yjszs.sdfmu.edu.cn'}


def _same_host(document,host,path):
    url=urlsplit(document.get('url',''))
    if url.scheme!='https' or url.hostname!=host or url.path!=path or url.username or url.password or url.port not in {None,443}:
        raise ValueError('结构化目录来源不属于已适配的实际公开地址')


def _structured(document,path,value):
    return {'document_id':document['id'],'url':document['url'],'kind':'structured_fields',
            'field_path':path,'structured_fields':value}


def _matrix(groups):
    alternatives={};signatures=set();count=None
    for subjects in groups:
        slots=[s['exam_unit'] for s in subjects]
        if len(slots) not in {2,3,4} or slots!=list(range(1,len(slots)+1)):
            raise ValueError('实际初试科目套组单元不完整')
        if count is not None and count!=len(slots):raise ValueError('初试套组的考试结构不一致')
        count=len(slots);signature=[]
        for subject in subjects:
            identity=subject['code'],subject['name'],subject['scope'];signature.append(identity)
            slot=subject['exam_unit'];alternatives={**alternatives,slot:{**alternatives.get(slot,{}),identity:subject}}
        signatures=signatures|{tuple(signature)}
    if count is None:raise ValueError('实际初试科目套组为空')
    expected=1
    for slot in range(1,count+1):expected*=len(alternatives[slot])
    supported=expected<=4096 and expected==len(signatures) and signatures==set(product(*(alternatives[slot] for slot in range(1,count+1))))
    subjects=[{**subject,**({'choice_group':f'exam-unit-{slot}'} if len(alternatives[slot])>1 else {})}
              for slot in range(1,count+1) for subject in alternatives[slot].values()]
    return subjects,supported


def _json_request(document,expected):
    body=document.get('request_body')
    if not isinstance(body,str):raise ValueError('公开JSON查询请求未留档')
    parsed=parse_qs(body,keep_blank_values=True)
    if set(parsed)!={'conditions','_gridInfo'} or any(len(value)!=1 for value in parsed.values()):
        raise ValueError('公开JSON查询参数结构不正确')
    if json.loads(parsed['conditions'][0])!=expected or json.loads(parsed['_gridInfo'][0])!={}:
        raise ValueError('公开JSON查询条件不属于实际可见范围')
    _request(document,{key:value[0] for key,value in parsed.items()})


def _ezui_context(unit,year,document,documents,root):
    host=EZUI_HOSTS.get(unit.get('id'));context=document.get('public_form_context') or {}
    if not host or year!=2027 or context.get('profile')!='ezui':raise ValueError('此公开JSON目录尚未适配')
    entry=documents.get(context.get('entry_document_id'),{});listing=documents.get(context.get('listing_document_id'),{})
    _same_host(entry,host,'/zsml/enterViewZsml')
    html=_read(entry,root,unit,year)
    if (parse_qs(urlsplit(entry['url']).query)!={'nd':[str(year)]} or unit['name']+str(year)+'年硕士研究生招生专业目录' not in compact(plain_text(html))
            or 'zsZsmlJbxxMng/queryViewZsmlList' not in html or 'get("zsmlForm").getData()' not in html):
        raise ValueError('入口未证明该年度公开目录查询')
    _same_host(listing,host,'/zsZsmlJbxxMng/queryViewZsmlList')
    _json_request(listing,{'nd':str(year),'yxsbh':None,'xkbh':None,'xxfs':None})
    rows=json.loads(_read(listing,root,unit,year))
    if not isinstance(rows,list) or not rows:raise ValueError('公开目录列表为空或结构不正确')
    matches=[(index,row) for index,row in enumerate(rows) if isinstance(row,dict) and row.get('zsmlbh')==context.get('row_identity')]
    if len(matches)!=1:raise ValueError('科目响应未绑定唯一公开目录方向')
    index,row=matches[0]
    if (not re.fullmatch(r'[0-9A-Z]{3}',str(row.get('organCode',''))) or not re.fullmatch(r'[0-9A-Z]{6}',str(row.get('xkdm','')))
            or not re.fullmatch(r'[0-9A-Z]{2}',str(row.get('yjfxdm',''))) or not row.get('organName') or not row.get('xkmc') or not row.get('yjfxmc')):
        raise ValueError('公开目录院系、专业或方向身份不完整')
    modes={}
    for identity in context.get('dictionary_document_ids',[]):
        dictionary=documents.get(identity,{})
        _same_host(dictionary,host,'/sysCommon/getContextData')
        if parse_qs(dictionary.get('request_body','')).get('key')==['select_dict_XXFS']:
            _request(dictionary,{'key':'select_dict_XXFS'})
            values=json.loads(_read(dictionary,root,unit,year))
            modes={item['value']:item['text'] for item in values if isinstance(item,dict)}
    if modes.get(row.get('xxfs')) not in {'全日制','非全日制'}:raise ValueError('学习方式原始字典缺失或未明确')
    page=documents.get(context.get('exam_page_id'),{})
    _same_host(page,host,'/zsZsmlJbxxMng/enterViewZsmlKskm')
    if parse_qs(urlsplit(page['url']).query)!={key:[str(row[key])] for key in ('zsmlbh','yjfx','xk')}:
        raise ValueError('科目按钮参数与实际方向不一致')
    page_html=_read(page,root,unit,year)
    if (canonical('专业方向：'+row['xk']+'_'+row['yjfx']) not in canonical(plain_text(page_html))
            or 'zsZsmlJbxxMng/queryZsmlKskmList' not in page_html
            or not re.search(r'conditions\.zsmlbh\s*=\s*["\']'+re.escape(row['zsmlbh'])+r'["\']',page_html)):
        raise ValueError('实际按钮页面未公开对应方向考试科目')
    _same_host(document,host,'/zsZsmlJbxxMng/queryZsmlKskmList');_json_request(document,{'zsmlbh':row['zsmlbh']})
    return row,index,listing,modes[row['xxfs']]


def _parse_ezui(text,unit,year,document,documents,root):
    row,index,listing,mode=_ezui_context(unit,year,document,documents,root)
    if text!=_read(document,root,unit,year):raise ValueError('科目JSON不是实际保存的响应')
    raw=json.loads(text)
    if not isinstance(raw,list) or not raw:raise ValueError('原始科目响应为空')
    grouped={};refs=[];issues=[]
    for position,item in enumerate(raw):
        if not isinstance(item,dict) or item.get('kskmjd') not in {None,'1','2','3'}:raise ValueError('科目考试阶段未明确')
        if item.get('kskmjd') in {'2','3'}:continue
        slot=item.get('kscc');course=item.get('kskm');group=item.get('kmzbh')
        if slot not in {'1','2','3','4'} or not isinstance(group,str) or not group or not isinstance(course,str):
            raise ValueError('原始科目套组、单元或名称缺失')
        if re.fullmatch(r'[-—－]+(?:无|不考)',compact(course)):continue
        subjects,problems=parse_subjects('①②③④'[int(slot)-1]+course,unit['id'],document)
        # A single slot deliberately does not satisfy the whole-paper contiguity check.
        issues=[*issues,*[problem for problem in problems if '初试单元缺失' not in problem]]
        if len(subjects)!=1:raise ValueError('单条科目无法唯一解析')
        proof=_structured(document,f'$[{position}]',{key:item[key] for key in ('kmzbh','kscc','kskm','kskmjd') if key in item})
        subject={**subjects[0],'evidence':proof}
        grouped={**grouped,group:[*grouped.get(group,[]),subject]};refs=[*refs,proof]
    groups=[]
    for group in grouped.values():
        slots={slot:[subject for subject in group if subject['exam_unit']==slot] for slot in {subject['exam_unit'] for subject in group}}
        if len(slots) not in {2,3,4} or set(slots)!=set(range(1,len(slots)+1)):raise ValueError('初试单元存在未公布的缺口')
        count=1
        for values in slots.values():count*=len(values)
        if count>4096:raise ValueError('科目组合数量超过可核验上限')
        groups=[*groups,*[list(combination) for combination in product(*(slots[slot] for slot in sorted(slots)))]]
    subjects,supported=_matrix(groups)
    identity=_structured(listing,f'$[{index}]',{key:row[key] for key in
        ('organCode','organName','xkdm','xkmc','yjfxdm','yjfxmc','xxfs','zsmlbh','xz') if key in row})
    program=_program(unit,year,row['organCode']+' '+plain_text(row['organName']).strip(),row['xkdm'],row['xkmc'],
        row['yjfxdm']+' '+row['yjfxmc'],mode,subjects,plain_text(row.get('zybz') or ''),document)
    return {'programs':[{**program,'catalog_document_id':document['id'],'catalog_parser':PARSER,
        'source_locator':{'profile':'ezui','zsmlbh':row['zsmlbh']},'evidence':[identity,*refs],'exam_evidence':refs,
        'exam_combinations':[{'subjects':group} for group in groups],
        'exam_matching_supported':supported and not bool(program.get('subject_identity_issues')),
        **({'exam_matching_note':'原文初试套组包含跨单元条件，保留原始组合，不展开为任意可选。'} if not supported else {})}],
        'expected_records':1,'issues':issues,'retest_rules':[]}


def _ezui_scope(unit,year,documents,root):
    leaves=[document for document in documents.values() if document.get('catalog_parser')==PARSER
            and document.get('unit_id')==unit['id'] and document.get('year')==year
            and document.get('catalog_active',True) and (document.get('public_form_context') or {}).get('profile')=='ezui']
    try:
        if not leaves:raise ValueError('暂无完整公开目录结果')
        context=leaves[0]['public_form_context'];listing=documents[context['listing_document_id']]
        rows=json.loads(_read(listing,root,unit,year));expected=[row['zsmlbh'] for row in rows]
        observed=[]
        for leaf in leaves:
            parsed=_parse_ezui(_read(leaf,root,unit,year),unit,year,leaf,documents,root)
            if parsed['issues']:raise ValueError('存在尚待核实的科目名称或代码')
            observed=[*observed,leaf['public_form_context']['row_identity']]
        complete=len(set(expected))==len(expected) and Counter(expected)==Counter(observed)
        return {'complete':complete,'expected_records':len(expected),'expected_document_ids':sorted(d['id'] for d in leaves),
                'issues':[] if complete else ['公开目录方向集合与科目响应尚未对账一致']}
    except (KeyError,ValueError,TypeError,OSError) as error:
        return {'complete':False,'expected_records':0,'expected_document_ids':[],'issues':[str(error)]}


def _zju_context(unit,year,document,documents,root):
    if unit.get('id')!='368213' or year!=2027:raise ValueError('此浙江大学公开目录年度尚未适配')
    host='yjsy.zju.edu.cn';context=document.get('public_form_context') or {}
    entry=documents.get(context.get('entry_document_id'),{});annual=documents.get(context.get('annual_check_id'),{})
    script=documents.get(context.get('interface_script_id'),{});options=documents.get(context.get('faculty_options_id'),{})
    _same_host(entry,host,'/open/zsss/ZsssTksList')
    if entry.get('request_method')!='GET' or parse_qs(urlsplit(entry['url']).query)!={'zsnf':[str(year)]}:
        raise ValueError('公开页面未定位统考年度')
    entry_html=_read(entry,root,unit,year)
    script_url=urlsplit(script.get('url',''))
    if (script_url.scheme!='https' or script_url.hostname!=host or not script_url.path.startswith('/js/')
            or script.get('request_method')!='GET' or script_url.path not in entry_html):
        raise ValueError('目录界面脚本不属于原始入口实际引用')
    code=_read(script,root,unit,year)
    if any(token not in code for token in ['ZsssTksList','年硕士研究生招生专业目录查询（统考生）','/open/zsss/zsml?mllx=0','/open/zsss/checkZsmlNf','this.$route.query.zsnf']):
        raise ValueError('界面未证明统考目录与年度的公开查询绑定')
    _same_host(annual,host,'/dataapi/open/zsss/checkZsmlNf')
    if parse_qs(urlsplit(annual['url']).query).get('zsnf')!=[str(year)]:raise ValueError('年度公开状态请求错年')
    publication=json.loads(_read(annual,root,unit,year))
    if publication.get('success') is not True or publication.get('result')!='1':raise ValueError('该年度目录未公开')
    _same_host(options,host,'/dataapi/open/zsss/queryXyList')
    option_query=parse_qs(urlsplit(options['url']).query)
    if option_query.get('mllx')!=['0'] or option_query.get('zsnf')!=[str(year)]:raise ValueError('招生学院选项范围错年或非统考')
    selection=json.loads(_read(options,root,unit,year))
    faculties=selection.get('result') if selection.get('success') is True else None
    if (not isinstance(faculties,list) or not faculties or any(not isinstance(row,dict) or not row.get('id') for row in faculties)
            or len({row['id'] for row in faculties})!=len(faculties)):
        raise ValueError('公开学院选项缺失或重复')
    _same_host(document,host,'/dataapi/open/zsss/zsml')
    query=parse_qs(urlsplit(document['url']).query,keep_blank_values=True)
    if (document.get('request_method')!='GET' or query.get('mllx')!=['0'] or query.get('zsnf')!=[str(year)]
            or query.get('pageNo')!=['1'] or query.get('pageSize')!=['30']
            or set(query)-{'mllx','zsnf','sysOrgCode','column','order','field','pageNo','pageSize','_t'}):
        raise ValueError('目录响应未绑定正常可见的年度学院选择')
    chosen=[row for row in faculties if query.get('sysOrgCode')==[row['id']]]
    if len(chosen)!=1 or not re.fullmatch(r'\d{3}',chosen[0].get('zsdm','')) or not chosen[0].get('departName'):
        raise ValueError('招生学院及其实际招生代码未明确')
    return chosen[0],faculties


def _zju_row(row,index,faculty,unit,year,document):
    fields=('id','zsnf','sysOrgCode','sysOrgCode_dictText','ejxkdm','ejxkmc','zyId_dictText','yjfxXh','yjfx','xxfs','xxfs_dictText','xwlb','xwlb_dictText','ksfs','ksfs_dictText')
    identity=_structured(document,f'result[{index}]',{key:row[key] for key in fields if key in row})
    if (row.get('zsnf')!=str(year) or row.get('sysOrgCode')!=faculty['id'] or row.get('sysOrgCode_dictText')!=faculty['departName']
            or row.get('fbzt')!='1' or row.get('spzt')!='1'):
        raise ValueError('方向所属年度、学院或公开状态不一致')
    if row.get('yjfxXh')=='JH' and '合计' in row.get('yjfx','') and not row.get('kmxx'):
        return None,{'reason':'原文明确为该专业招生数合计行，不作为可报名研究方向','evidence':[identity]},[]
    if row.get('ksfs')=='3' and row.get('ksfs_dictText')=='单独考试':
        return None,{'reason':'原文明确为单独考试，不混入普通统考目录','evidence':[identity]},[]
    if (row.get('ksfs')!='1' or row.get('ksfs_dictText')!='统考' or not re.fullmatch(r'[0-9A-Z]{6}',row.get('ejxkdm',''))
            or not re.fullmatch(r'[0-9A-Z]{2}',row.get('yjfxXh','')) or not row.get('yjfx')
            or row.get('ejxkmc')!=row.get('zyId_dictText')):
        raise ValueError('专业、方向或统考身份无法确认')
    mode={'1':'全日制','2':'非全日制'}.get(row.get('xxfs'));degree={'0':'学术学位','1':'专业学位'}.get(row.get('xwlb'))
    if not mode or mode!=row.get('xxfs_dictText') or not degree or degree!=row.get('xwlb_dictText'):
        raise ValueError('学习方式或学位类别原始值不一致')
    raw=row.get('kmxx')
    if not isinstance(raw,list) or not raw:raise ValueError('本方向未公布完整初试套组')
    groups=[];refs=[];issues=[]
    for group_index,group in enumerate(raw):
        if not isinstance(group,dict):raise ValueError('原始科目套组类型不正确')
        fields={key:group[key] for key in ('kskm1dm','kskm1mc','kskm2dm','kskm2mc','ywk1dm','ywk1mc','ywk2dm','ywk2mc') if key in group}
        proof=_structured(document,f'result[{index}].kmxx[{group_index}]',fields);subjects=[]
        for slot,prefix in enumerate(['kskm1','kskm2','ywk1','ywk2'],1):
            code=group.get(prefix+'dm');name=group.get(prefix+'mc')
            if name=='无' and isinstance(code,str) and re.fullmatch(r'[-—－]+',code):continue
            if not isinstance(code,str) or not isinstance(name,str) or not re.fullmatch(r'\d{3}',code) or not name:
                raise ValueError('套组科目代码与名称缺失')
            parsed,problems=parse_subjects('①②③④'[slot-1]+code+name,unit['id'],document)
            if len(parsed)!=1:raise ValueError('套组科目无法唯一解析')
            subjects=[*subjects,{**parsed[0],'evidence':proof}]
            issues=[*issues,*[problem for problem in problems if '初试单元缺失' not in problem]]
        groups=[*groups,subjects];refs=[*refs,proof]
    subjects,supported=_matrix(groups)
    program=_program(unit,year,faculty['zsdm']+' '+faculty['departName'],row['ejxkdm'],row['ejxkmc'],
        row['yjfxXh']+' '+row['yjfx'],mode,subjects,row.get('tkbz') or '',document,degree=degree)
    return {**program,'catalog_document_id':document['id'],'catalog_parser':PARSER,
        'source_locator':{'profile':'zju','source_record_id':row['id'],'field_path':f'result[{index}]'},
        'evidence':[identity,*refs],'exam_evidence':refs,'exam_combinations':raw,
        'exam_matching_supported':supported and not bool(program.get('subject_identity_issues')),
        **({'exam_matching_note':'原文套组存在跨单元条件，保留全部原始组合，不展开任意搭配。'} if not supported else {})},None,issues


def _parse_zju(text,unit,year,document,documents,root):
    faculty,_=_zju_context(unit,year,document,documents,root)
    if text!=_read(document,root,unit,year):raise ValueError('解析文本不是原始目录响应')
    payload=json.loads(text);rows=payload.get('result') if payload.get('success') is True else None
    if not isinstance(rows,list):raise ValueError('原始学院目录响应不可用')
    programs=[];excluded=[];issues=[]
    for index,row in enumerate(rows):
        try:
            if not isinstance(row,dict):raise ValueError('目录条目类型不正确')
            program,omitted,problems=_zju_row(row,index,faculty,unit,year,document)
            programs=[*programs,*([program] if program else [])];excluded=[*excluded,*([omitted] if omitted else [])];issues=[*issues,*problems]
        except ValueError as error:issues=[*issues,f'原始第{index+1}条：{error}']
    if len({row['id'] for row in programs})!=len(programs):issues=[*issues,'同学院原始目录身份重复']
    return {'programs':programs,'excluded_records':excluded,'expected_records':len(rows)-len(excluded),'issues':issues,'retest_rules':[]}


def _zju_scope(unit,year,documents,root):
    leaves=[doc for doc in documents.values() if doc.get('catalog_parser')==PARSER and doc.get('unit_id')==unit['id']
            and doc.get('year')==year and doc.get('catalog_active',True) and (doc.get('public_form_context') or {}).get('profile')=='zju']
    try:
        _,faculties=_zju_context(unit,year,leaves[0],documents,root)
        observed=[];count=0
        for leaf in leaves:
            faculty,_=_zju_context(unit,year,leaf,documents,root)
            parsed=_parse_zju(_read(leaf,root,unit,year),unit,year,leaf,documents,root)
            if parsed['issues']:raise ValueError('存在未解决的原始目录条目')
            count+=len(parsed['programs']);observed=[*observed,faculty['id']]
        complete=Counter(observed)==Counter(row['id'] for row in faculties)
        return {'complete':complete,'expected_records':count,'expected_document_ids':sorted(doc['id'] for doc in leaves),
                'issues':[] if complete else ['公开招生学院集合与实际目录响应未对账一致']}
    except (IndexError,KeyError,ValueError,TypeError,OSError) as error:
        return {'complete':False,'expected_records':0,'expected_document_ids':[],'issues':[str(error)]}
