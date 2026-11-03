"""Replay public ASP.NET catalog UI requests and parse their original visible leaves."""
from functools import lru_cache
from html.parser import HTMLParser
from itertools import product
import hashlib
import json
import re
from urllib.parse import parse_qs,urljoin,urlsplit

from .catalogs import _program,compact,evidence,parse_subjects
from .catalog_public_forms import _read,_matrix
from service_verification import canonical

PARSER='public_aspx_v1'
PROFILES={
    'aspx_stu':{'unit':'368428','code':'10560','host':'www.gs.stu.edu.cn','entry':'/Open/RecruitTkss/ZsDir_View.aspx','heading':'年硕士研究生入学招生目录'},
    'aspx_scut':{'unit':'368416','code':'10561','host':'yanzhao.scut.edu.cn','entry':'/open/Master/Zsml_view.aspx','heading':'年硕士研究生招生专业目录'},
}
VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}


class _Node:
    def __init__(self,tag='',attrs=(),hidden=False):self.tag=tag;self.attrs=dict(attrs);self.children=[];self.hidden=hidden
    def text(self):return '' if self.hidden else ' '.join(child.text() if isinstance(child,_Node) else child for child in self.children)
    def find(self,tag=None):
        return [child for item in self.children if isinstance(item,_Node)
                for child in ([item] if tag is None or item.tag==tag else [])+item.find(tag)]


class _Tree(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.root=_Node();self.stack=[self.root]
    def handle_starttag(self,tag,attrs):
        values=dict(attrs);style=re.sub(r'\s+','',values.get('style','')).lower()
        hidden=(self.stack[-1].hidden or tag in {'script','style','template','noscript','textarea'} or 'hidden' in values
                or values.get('aria-hidden','').lower()=='true' or bool(re.search(r'(?:^|;)(?:display:none|visibility:hidden)(?:!important)?(?:;|$)',style)))
        node=_Node(tag,attrs,hidden);self.stack[-1].children.append(node)
        if tag not in VOID:self.stack.append(node)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID:self.handle_endtag(tag)
    def handle_endtag(self,tag):
        index=next((i for i in range(len(self.stack)-1,0,-1) if self.stack[i].tag==tag),None)
        if index is not None:self.stack=self.stack[:index]
    def handle_data(self,text):self.stack[-1].children.append(text)


@lru_cache(maxsize=256)
def _tree(text):
    parser=_Tree();parser.feed(text);parser.close();return parser.root


def decode_aspx_delta(text):
    """Read Microsoft's length-framed response in UTF-16 code units; execute nothing."""
    raw=text.encode('utf-16-le');offset=0;panels=[];hidden={}
    def field(start):
        end=raw.find(b'|\0',start)
        if end<0 or (end-start)%2:raise ValueError('公开分页响应的字段边界不完整')
        return raw[start:end].decode('utf-16-le'),end+2
    while offset<len(raw):
        length,offset=field(offset);kind,offset=field(offset);identity,offset=field(offset)
        if not length.isdecimal() or int(length)>32*1024*1024:raise ValueError('公开分页响应长度不正确')
        end=offset+int(length)*2
        if raw[end:end+2]!=b'|\0':raise ValueError('公开分页响应截断或长度不一致')
        value=raw[offset:end].decode('utf-16-le');offset=end+2
        if kind=='updatePanel':panels=[*panels,value]
        elif kind=='hiddenField':
            if identity in hidden:raise ValueError('公开分页响应的状态字段重复')
            hidden={**hidden,identity:value}
        elif kind in {'error','pageRedirect'}:raise ValueError('公开分页返回错误或转向，未取得目录')
    if not panels:raise ValueError('公开分页响应没有可见目录区域')
    return {'panels':panels,'hidden':hidden}


def _profile(unit,year,name):
    profile=PROFILES.get(name)
    if not profile or unit.get('id')!=profile['unit'] or str(unit.get('unit_code'))!=profile['code'] or year!=2027:
        raise ValueError('该学校、年度或公开分页格式尚未适配')
    return profile


def _address(document,profile,leaf=False):
    parsed=urlsplit(document.get('url',''));query=parse_qs(parsed.query,keep_blank_values=True)
    path=profile['entry'].rsplit('/',1)[0]+'/ZyYjfxView.aspx' if leaf else profile['entry']
    if (parsed.scheme!='https' or parsed.hostname!=profile['host'] or parsed.path.lower()!=path.lower()
            or parsed.username or parsed.password or parsed.port not in {None,443} or parsed.fragment
            or (not leaf and query) or (leaf and (set(query)!={'EID'} or len(query['EID'])!=1 or not query['EID'][0]))):
        raise ValueError('来源不是该学校的实际公开目录地址')


def _controls(tree):
    fields={node.attrs['name']:node.attrs.get('value','') for node in tree.find('input')
            if node.attrs.get('name') and node.attrs.get('type','text') not in {'button','submit','image','file','checkbox','radio'}}
    options={}
    for node in [node for node in tree.find('select') if not node.hidden]:
        name=node.attrs.get('name');rows=[{'value':opt.attrs.get('value',''),'name':compact(opt.text()),'selected':'selected' in opt.attrs}
                                        for opt in node.find('option') if not opt.hidden]
        if not name or not rows or len({row['value'] for row in rows})!=len(rows):raise ValueError('公开目录选择项为空或重复')
        selected=[row['value'] for row in rows if row['selected']]
        if len(selected)>1:raise ValueError('公开目录选择项不能唯一确认')
        options={**options,name:rows};fields={**fields,name:selected[0] if selected else rows[0]['value']}
    return fields,options


@lru_cache(maxsize=128)
def _links(tree,url):
    links=[]
    for node in tree.find('a'):
        if node.hidden:continue
        href=node.attrs.get('href','');target=urljoin(url,href)
        if '/zyyjfxview.aspx?' in target.lower():links=[*links,{'url':target,'text':compact(node.text())}]
    return links


def _pager(tree):
    anchors=[node for node in tree.find('a') if not node.hidden and re.search(r'Pager1_lnkPage\d+$',node.attrs.get('id',''))]
    if not anchors:
        if not any(node.attrs.get('id','').endswith('dgData') for node in tree.find('table')):raise ValueError('公开目录表或当前页缺失')
        return 1,False
    active=[node for node in anchors if not node.attrs.get('href')]
    if len(active)!=1 or not compact(active[0].text()).isdecimal():raise ValueError('公开目录当前页不能唯一确认')
    page=int(compact(active[0].text()))
    more=any(node.attrs.get('href') and compact(node.text()).isdecimal() and int(compact(node.text()))>page for node in anchors)
    more=more or any(node.attrs.get('id','').endswith('Pager1_lnkNextPages') and node.attrs.get('href') for node in tree.find('a') if not node.hidden)
    return page,bool(more)


def _request(document,root,unit,year):
    proof=document.get('request_document')
    if not isinstance(proof,dict):raise ValueError('公开分页原始请求未存档')
    saved=json.loads(_read(proof,root,unit,year));body=saved.get('body')
    if (saved.get('method')!='POST' or saved.get('url')!=document['url'] or not isinstance(body,str)
            or hashlib.sha256(body.encode()).hexdigest()!=document.get('request_sha256')):
        raise ValueError('公开分页请求或摘要不一致')
    fields=parse_qs(body,keep_blank_values=True)
    if any(len(values)!=1 for values in fields.values()):raise ValueError('公开分页请求含重复参数')
    return {key:values[0] for key,values in fields.items()}


@lru_cache(maxsize=256)
def _postback_proof(tree,options,prior_fields,prior_events,request_fields):
    previous={'tree':tree,'options':dict(options),'fields':dict(prior_fields),'events':dict(prior_events)};fields=dict(request_fields)
    target=fields.get('__EVENTTARGET');argument=fields.get('__EVENTARGUMENT','')
    modes=[key for key in previous['options'] if key.endswith('$drpXxfs')]
    event=previous['events'].get(target,'')
    changed_mode=len(modes)==1 and target==modes[0] and argument=='' and '__doPostBack' in event and target in event
    allowed=any(re.fullmatch(r"javascript:__doPostBack\('"+re.escape(target or '')+r"','"+re.escape(argument)+r"'\)",node.attrs.get('href',''))
                for node in previous['tree'].find('a') if not node.hidden)
    if not changed_mode and not allowed:raise ValueError('分页请求不来自上一公开页面的实际控件')
    for key,rows in previous['options'].items():
        selected=fields.get(key)
        if selected not in set(rows) or (key!=target and selected!=previous['fields'].get(key)):
            raise ValueError('分页请求改变了未授权的目录筛选条件')
    for key,value in previous['fields'].items():
        if (key.startswith('__VIEWSTATE') or key=='__EVENTVALIDATION' or key.endswith('$txtWord')) and fields.get(key)!=value:
            raise ValueError('公开分页请求未延续上一响应的真实状态')
    if fields.get('__ASYNCPOST')!='true':raise ValueError('此公开分页响应未对应正常 AJAX 请求')


def _postback(previous,fields):
    return _postback_proof(previous['tree'],tuple((key,tuple(row['value'] for row in rows)) for key,rows in previous['options'].items()),
                           tuple(previous['fields'].items()),tuple(previous['events'].items()),tuple(fields.items()))


@lru_cache(maxsize=128)
def _page_view(raw,delta):
    decoded=decode_aspx_delta(raw) if delta else {'panels':[raw],'hidden':{}}
    tree=_tree(''.join(decoded['panels']));fields,options=_controls(tree);page,more=_pager(tree)
    events={node.attrs.get('name'):node.attrs.get('onchange','') for node in tree.find('select') if not node.hidden}
    return {'tree':tree,'fields':fields,'options':options,'events':events,'page':page,'more':more,'hidden':decoded['hidden']}


def _page(document,unit,year,documents,root,profile,memo=None,trail=()):
    memo={} if memo is None else memo
    if document['id'] in memo:return memo[document['id']]
    if document['id'] in trail or len(trail)>100:raise ValueError('公开分页来源链存在循环或超过上限')
    _address(document,profile);raw=_read(document,root,unit,year);context=document.get('public_aspx_page') or {}
    if context.get('profile') not in PROFILES or _profile(unit,year,context['profile'])!=profile:raise ValueError('公开分页学校上下文不一致')
    if document.get('request_method')=='GET':
        view=_page_view(raw,False);tree=view['tree'];fields=view['fields'];options=view['options'];events=view['events'];forms=tree.find('form')
        if (context.get('previous_document_id') or len(forms)!=1 or urljoin(document['url'],forms[0].attrs.get('action',''))!=document['url']
                or canonical(unit['name']) not in canonical(tree.text()) or str(year)+profile['heading'] not in compact(tree.text())):
            raise ValueError('公开目录初始页面未证明学校、年度和表单')
        years=[key for key in options if key.endswith('$drpNd')];modes=[key for key in options if key.endswith('$drpXxfs')]
        if (len(years)!=1 or fields[years[0]]!=str(year) or len(modes)!=1
                or {row['value']:row['name'] for row in options[modes[0]]}!={'1':'全日制','2':'非全日制'}
                or any(value for key,value in fields.items() if key in options and key not in {years[0],modes[0]})
                or any(value for key,value in fields.items() if key.endswith('$txtWord'))):
            raise ValueError('公开目录初始查询不是正确年度及未限制院系专业的范围')
    elif document.get('request_method')=='POST':
        parent=documents.get(context.get('previous_document_id'))
        if not isinstance(parent,dict):raise ValueError('公开分页前一原文缺失')
        previous=_page(parent,unit,year,documents,root,profile,memo,(*trail,document['id']))
        request=_request(document,root,unit,year);_postback(previous,request)
        view=_page_view(raw,True);tree=view['tree'];options={**previous['options'],**view['options']}
        events={**previous['events'],**view['events']}
        fields={**previous['fields'],**{key:request[key] for key in options},**view['hidden']}
    else:raise ValueError('公开目录缺少实际请求方式')
    page=view['page'];more=view['more'];mode=fields[next(key for key in options if key.endswith('$drpXxfs'))]
    if context.get('page',page)!=page or context.get('mode',mode)!=mode:raise ValueError('记录的页码或模式与原文实际状态不一致')
    result={'tree':tree,'fields':fields,'options':options,'events':events,'page':page,'more':more,'mode':mode,
            'links':_links(tree,document['url']),'document':document}
    memo[document['id']]=result
    return result


def _leaf_fields(tree,unit,year):
    tables=[node for node in tree.find('table') if not node.hidden and 'tbline' in node.attrs.get('class','').split() and '研究方向名称' in compact(node.text())]
    if (len(tables)!=1 or canonical(unit['name']) not in canonical(tree.text())
            or str(year)+'年硕士研究生入学招生专业研究方向信息' not in compact(tree.text())):
        raise ValueError('公开方向详情缺少唯一身份表、学校或正确年度')
    fields={}
    for row in [node for node in tables[0].children if isinstance(node,_Node) and node.tag=='tr']:
        cells=[node for node in row.children if isinstance(node,_Node) and node.tag=='td']
        if len(cells)%2:raise ValueError('公开方向字段不能逐项配对')
        for label,value in zip(cells[::2],cells[1::2]):
            key=compact(label.text())
            if key in fields:raise ValueError('公开方向身份字段重复')
            fields={**fields,key:value}
    required={'研究方向名称','所属专业(领域)代码','所属专业(领域)名称','招生院系','学习方式','初试科目'}
    if not required<=set(fields):raise ValueError('公开方向身份、模式或初试科目字段缺失')
    return fields


def _exam_tokens(node):
    for child in node.children:
        if isinstance(child,str):yield child
        elif not child.hidden:
            if child.tag=='a':yield child
            else:yield from _exam_tokens(child)


def _special(fields,document):
    note=fields.get('备注');initial=fields['初试科目']
    note_text=compact(note.text()) if note else ''
    only=r'(?:^|[。；，,;])(?:本(?:研究)?方向|该方向|本专业)?(?:只|仅)(?:接受|接收|招收)'
    if re.search(r'\((?:单独考试|单考)\)',compact(initial.text())):
        reason='单独考试，不归为普通统考候选';proof=note if note and '单独考试' in note_text else initial
    elif re.search(only+r'长学制转段',note_text):
        reason='长学制转段专属，不归为普通统考候选';proof=note
    elif re.search(only+r'(?:推免生|推荐免试生)',note_text):
        reason='仅接收推免生，不归为普通统考候选';proof=note
    elif re.search(only+r'(?:符合)?单独考试',note_text):
        reason='单独考试，不归为普通统考候选';proof=note
    else:return None
    return {'document_id':document['id'],'reason':reason,'evidence':evidence(document,proof.text())}


def _exams(cell,unit,document):
    groups=[node for node in cell.find('span') if re.fullmatch(r'.*_dlistCskm(?:_ctl\d+_dlist|_dlist_\d+)',node.attrs.get('id','')) and not node.hidden]
    if not groups:raise ValueError('初试科目套组缺失')
    combinations=[];issues=[];refs=[]
    for group in groups:
        grouped={};declared=set();unused=set();prefix='';previous_slot=None
        for anchor in _exam_tokens(group):
            if isinstance(anchor,str):prefix+=anchor;continue
            value=compact(anchor.text());match=re.fullmatch(r'([①②③④])?([-—－]+|\d{3})\|(.+)',value)
            if not match:raise ValueError('初试科目单元、代码或名称不能确认')
            if match[1] is None and (previous_slot is None or compact(prefix)!='或'):raise ValueError('备选科目未明确标注所属初试单元')
            slot='①②③④'.index(match[1])+1 if match[1] else previous_slot
            previous_slot=slot;prefix='';declared=declared|{slot}
            if re.fullmatch(r'[-—－]+',match[2]) and match[3]=='无':unused=unused|{slot};continue
            if not re.fullmatch(r'\d{3}',match[2]):raise ValueError('初试科目代码无效')
            subjects,warnings=parse_subjects('①'+match[2]+match[3],unit['id'],document)
            subject={**subjects[0],'exam_unit':slot,'evidence':evidence(document,anchor.text())}
            grouped={**grouped,slot:[*grouped.get(slot,[]),subject]};issues=[*issues,*[warning for warning in warnings if '单元缺失' not in warning]]
            refs=[*refs,subject['evidence']]
        if (not declared or declared!=set(range(1,max(declared)+1)) or unused&set(grouped)
                or set(grouped) not in ({1,2},{1,2,3},{1,2,3,4})
                or any(len({(s['code'],s['name']) for s in row})!=len(row) for row in grouped.values())):
            raise ValueError('初试科目套组单元缺失、重复或冲突')
        size=1
        for row in grouped.values():size*=len(row)
        if len(combinations)+size>4096:raise ValueError('原文初试套组超过安全展开上限，须单独适配')
        combinations=[*combinations,*[list(row) for row in product(*(grouped[slot] for slot in sorted(grouped)))]]
    subjects,supported=_matrix(combinations)
    return subjects,combinations,supported,refs,issues


def parse_public_aspx_html(html,unit,year,document,*,project_root,documents):
    """Require original leaf, public href, exact form state chain and complete exam groups."""
    context=document.get('public_form_context') or {};profile=_profile(unit,year,context.get('profile'));_address(document,profile,True)
    if document.get('request_method')!='GET' or _read(document,project_root,unit,year)!=html:raise ValueError('方向详情并非保存的实际 GET 响应')
    listing=documents.get(context.get('listing_document_id'));entry=documents.get(context.get('entry_document_id'))
    if not isinstance(listing,dict) or not isinstance(entry,dict):raise ValueError('公开目录入口及方向链接原文缺失')
    memo={};initial=_page(entry,unit,year,documents,project_root,profile,memo);page=_page(listing,unit,year,documents,project_root,profile,memo)
    if entry.get('request_method')!='GET' or initial['page']!=1:raise ValueError('公开目录不是从真实第一页开始')
    links=[link for link in page['links'] if link['url']==document['url']]
    if len(links)!=1:raise ValueError('该方向没有出现在已保存的实际公开页面中')
    tree=_tree(html);fields=_leaf_fields(tree,unit,year);value=lambda key:compact(fields[key].text())
    raw_direction=value('研究方向名称');direction=re.fullmatch(r'([0-9A-Z]{2})\|(.+)',links[0]['text'])
    if direction is None and context['profile']=='aspx_scut':
        direction=re.fullmatch(r'([0-9A-Z]{2})\|(.+)',raw_direction)
        if direction is None or direction[2]!=links[0]['text']:raise ValueError('公开链接与详情的方向名称不一致')
    if direction is None:raise ValueError('公开原文缺少方向代码或名称')
    name=direction[2]
    if raw_direction not in {'',name,direction[1]+'|'+name} or value('学习方式')!={'1':'全日制','2':'非全日制'}[page['mode']]:
        raise ValueError('方向详情名称或学习方式与公开链接及真实查询状态不一致')
    code=value('所属专业(领域)代码');major=value('所属专业(领域)名称');department=value('招生院系')
    if not re.fullmatch(r'[0-9A-Z]{6}',code) or not major or not department:raise ValueError('专业或院系身份缺失')
    excluded=_special(fields,document)
    if excluded:return {'programs':[],'excluded_records':[excluded],'expected_records':0,'issues':[],'retest_rules':[]}
    subjects,groups,supported,exam_refs,issues=_exams(fields['初试科目'],unit,document)
    refs=[evidence(document,fields[key].text()) for key in ('研究方向名称','所属专业(领域)代码','所属专业(领域)名称','招生院系','学习方式') if compact(fields[key].text())]
    refs=[*refs,evidence(listing,links[0]['text']),evidence(document,fields['初试科目'].text())]
    degree={'学术型':'学术学位','专业型':'专业学位'}.get(value('专业类型')) if '专业类型' in fields else None
    note=value('备注') if '备注' in fields else ''
    row=_program(unit,year,department,code,major,direction[1]+' '+name,value('学习方式'),subjects,note,document,degree=degree)
    program={**row,'catalog_document_id':document['id'],'catalog_parser':PARSER,'evidence':refs,'exam_evidence':exam_refs,
             'exam_matching_supported':supported and not row.get('subject_identity_issues'),
             'exam_combinations':[{'subjects':group} for group in groups],
             'source_locator':{'public_leaf_url':document['url'],'listing_document_id':listing['id']}}
    return {'programs':[program],'expected_records':1,'issues':issues,'retest_rules':[]}


def public_aspx_scope(unit,year,documents,*,project_root):
    """Reconstruct both actual visible mode branches and every page and linked leaf."""
    issues=[];memo={};pages=[];expected=[]
    try:
        candidates=[doc for doc in documents.values() if doc.get('unit_id')==unit['id'] and doc.get('year')==year and doc.get('public_aspx_page')]
        if not candidates:raise ValueError('尚未保存真实公开目录分页')
        names={doc['public_aspx_page'].get('profile') for doc in candidates}
        if len(names)!=1:raise ValueError('目录分页格式不唯一')
        profile=_profile(unit,year,next(iter(names)))
        for doc in candidates:pages=[*pages,_page(doc,unit,year,documents,project_root,profile,memo)]
        entries=[page for page in pages if page['document'].get('request_method')=='GET']
        if len(entries)!=1 or entries[0]['page']!=1:raise ValueError('真实初始目录页缺失或重复')
        for mode in ('1','2'):
            branch=[page for page in pages if page['mode']==mode and not page['document']['public_aspx_page'].get('navigation_only')]
            if not branch or len({page['page'] for page in branch})!=len(branch):raise ValueError('学习方式分页缺失或重复')
            maximum=max(page['page'] for page in branch)
            if {page['page'] for page in branch}!=set(range(1,maximum+1)) or any(page['more']!=(page['page']<maximum) for page in branch):
                raise ValueError('学习方式未遍历到真实末页')
            expected=[*expected,*[link for page in branch for link in page['links']]]
        urls=[link['url'] for link in expected]
        if len(set(urls))!=len(urls):raise ValueError('不同公开目录页的方向链接重复，需核查')
        primary=[doc for doc in documents.values() if doc.get('unit_id')==unit['id'] and doc.get('year')==year and doc.get('catalog_parser')==PARSER]
        lookup={}
        for doc in primary:lookup={**lookup,doc['url']:[*lookup.get(doc['url'],[]),doc]}
        for link in expected:
            found=lookup.get(link['url'],[])
            if len(found)!=1:raise ValueError('部分公开方向详情尚未采集或有重复来源')
        ids=[lookup[link['url']][0]['id'] for link in expected];excluded=[]
        for identity in ids:
            doc=documents[identity];fields=_leaf_fields(_tree(_read(doc,project_root,unit,year)),unit,year)
            omitted=_special(fields,doc)
            if omitted:excluded=[*excluded,omitted]
        return {'complete':True,'expected_records':len(expected)-len(excluded),'expected_document_ids':ids,
                'excluded_records':excluded,'issues':[]}
    except (ValueError,KeyError,TypeError,IndexError) as error:issues=[str(error)]
    return {'complete':False,'expected_records':len(expected),'expected_document_ids':[],'issues':issues}
