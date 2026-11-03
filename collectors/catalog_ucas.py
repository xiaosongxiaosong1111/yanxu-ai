"""UCAS public master's catalog: real index pagination, training units and exact row scope."""
from collections import Counter
from functools import lru_cache
from itertools import product
import re
from urllib.parse import parse_qs,urljoin,urlsplit

from .catalog_public_aspx import _tree as _aspx_tree,_Node
from .catalog_public_forms import _read,_matrix
from .catalogs import _program,compact,evidence,parse_subjects

PARSER='ucas_public_v1'
HOST='admission.ucas.ac.cn'
INDEX='/info/ZhaoshengDanwei/9e780c52-baf5-4020-b453-bc4510579559'
DETAIL=INDEX.replace('/ZhaoshengDanwei/','/ZhaoshengDanweiDetail/')+'/'
INDEX_HEADER=['单位代码','单位名称','单位地址','邮政编码','联系电话','联系人']
BASE_HEADER=['学科、专业名称(代码)研究方向','预计招生','考试科目','备注']


@lru_cache(maxsize=8)
def _tree(text):
    return _aspx_tree.__wrapped__(text)


def _profile(unit,year):
    if unit.get('id')!='367929' or str(unit.get('unit_code'))!='14430' or year!=2027:
        raise ValueError('尚未适配该招生单位或年度的国科大公开目录')


def _url(document,leaf=False):
    parsed=urlsplit(document.get('url',''));query=parse_qs(parsed.query,keep_blank_values=True)
    if (parsed.scheme!='https' or parsed.hostname!=HOST or parsed.username or parsed.password or parsed.port not in {None,443}
            or parsed.fragment or document.get('request_method')!='GET'):
        raise ValueError('来源不是正常公开的国科大 GET 页面')
    if leaf:
        match=re.fullmatch(re.escape(DETAIL)+r'(\d{5})1(2027)',parsed.path)
        if not match or query:raise ValueError('培养单位详情没有真实五位代码及硕士招生年度')
        return match[1]
    if parsed.path!=INDEX or query and (set(query)!={'page'} or len(query['page'])!=1 or not re.fullmatch(r'[1-9]\d?',query['page'][0])):
        raise ValueError('公开索引地址含未知的目录、年度或筛选参数')
    return int(query.get('page',['1'])[0])


def _cells(row,metadata=False):
    cells=[node for node in row.children if isinstance(node,_Node) and node.tag in {'td','th'}]
    if any(node.hidden or not metadata and (node.attrs.get('rowspan','1')!='1' or node.attrs.get('colspan','1')!='1') for node in cells):
        raise ValueError('本版本未核准隐藏或跨列跨行目录数据')
    return cells


@lru_cache(maxsize=192)
def _index_rows(raw,url):
    tree=_tree(raw)
    if '中国科学院大学' not in compact(tree.text()):raise ValueError('公开目录索引缺少招生单位归属')
    annual=[compact(node.text()) for node in tree.find('li') if 'erji_list' in node.attrs.get('class','').split() and not node.hidden]
    if annual!=['招生单位-2027']:raise ValueError('公开索引原文年度与硕士招生年度不一致')
    tables=[node for node in tree.find('table') if node.find('tr') and [compact(c.text()) for c in _cells(node.find('tr')[0])]==INDEX_HEADER]
    pagers=[node for node in tree.find('div') if 'pager' in node.attrs.get('class','').split() and not node.hidden]
    if len(tables)!=1 or len(pagers)!=1:raise ValueError('公开培养单位列表及分页区域缺失或重复')
    current=[compact(node.text()) for node in pagers[0].find('span') if 'current' in node.attrs.get('class','').split()]
    if len(current)!=1 or not re.fullmatch(r'[1-9]\d?',current[0]):raise ValueError('公开索引当前页不能唯一确认')
    page=int(current[0]);declared={page};links=[]
    for anchor in pagers[0].find('a'):
        if anchor.hidden:raise ValueError('公开索引分页含不可见控件')
        target=urljoin(url,anchor.attrs.get('href',''));number=_url({'url':target,'request_method':'GET'})
        if compact(anchor.text()).isdigit() and int(compact(anchor.text()))!=number:raise ValueError('索引分页数字与实际链接不一致')
        declared=declared|{number}
    for row in tables[0].find('tr')[1:]:
        cells=_cells(row)
        if len(cells)!=len(INDEX_HEADER):raise ValueError('培养单位索引列数不完整')
        code=compact(cells[0].text());name=compact(cells[1].text());anchors=[node for node in cells[1].find('a') if not node.hidden]
        if not re.fullmatch(r'\d{5}',code) or len(anchors)!=1 or not name:raise ValueError('培养单位真实代码、名称或详情链接缺失')
        target=urljoin(url,anchors[0].attrs.get('href',''))
        if _url({'url':target,'request_method':'GET'},True)!=code:raise ValueError('公开培养单位链接的代码或年度不一致')
        links=[*links,{'code':code,'name':name,'url':target,'quote':cells[1].text(),'address':cells[2].text().strip()}]
    if not links or len({link['code'] for link in links})!=len(links):raise ValueError('索引培养单位为空或重复')
    return {'page':page,'declared_pages':declared,'links':links}


def _index(document,unit,year,root):
    page=_url(document);raw=_read(document,root,unit,year);result=_index_rows(raw,document['url'])
    if result['page']!=page:raise ValueError('实际公开索引页码与请求不一致')
    return result


def _context(unit,year,document,documents,root):
    _profile(unit,year);code=_url(document,True);context=document.get('public_form_context') or {}
    if context.get('profile')!='ucas':raise ValueError('培养单位详情缺少公开目录上下文')
    ids=context.get('index_document_ids')
    if (not isinstance(ids,list) or not ids or len(ids)>100 or any(not isinstance(identity,str) for identity in ids)
            or len(set(ids))!=len(ids)):raise ValueError('年度公开索引原文集合缺失或重复')
    entry=documents.get(context.get('entry_document_id'));listing=documents.get(context.get('listing_document_id'))
    if not isinstance(entry,dict) or not isinstance(listing,dict) or entry['id'] not in ids or listing['id'] not in ids or _url(entry)!=1:
        raise ValueError('公开年度入口或当前培养单位所属索引页缺失')
    first=_index(entry,unit,year,root);page=_index(listing,unit,year,root)
    declared=[]
    for identity in ids:
        doc=documents.get(identity)
        if not isinstance(doc,dict):raise ValueError('声明的原始索引上下文不存在')
        context_page=_index(doc,unit,year,root)
        if context_page['page'] not in first['declared_pages']:raise ValueError('声明的索引不在年度入口真实分页中')
        declared=[*declared,context_page['page']]
    if len(set(declared))!=len(declared):raise ValueError('声明的原始索引页重复')
    if page['page'] not in first['declared_pages']:raise ValueError('培养单位所在页不在入口实际可见分页范围内')
    matches=[link for link in page['links'] if link['url']==document['url'] and link['code']==code]
    if len(matches)!=1:raise ValueError('培养单位详情不属于保存的真实公开索引链接')
    return matches[0]


def _metadata(tree,code,name):
    tables=[table for table in tree.find('table') if table.attrs.get('id')!='detail' and '单位代码' in compact(table.text()) and '目录类别' in compact(table.text())]
    if len(tables)!=1 or name not in compact(tree.text()) or '中国科学院大学' not in compact(tree.text()):
        raise ValueError('培养单位详情缺少唯一的单位归属信息')
    fields={}
    for row in tables[0].find('tr'):
        cells=_cells(row,metadata=True)
        if len(cells)%2:raise ValueError('培养单位元数据字段无法配对')
        for key,value in zip(cells[::2],cells[1::2]):
            label=compact(key.text())
            if label in fields:raise ValueError('培养单位元数据字段重复')
            fields={**fields,label:value}
    if compact(fields.get('单位代码',_Node()).text())!=code or compact(fields.get('目录类别',_Node()).text())!='硕士':
        raise ValueError('原文培养单位代码或招生类别不一致')
    return fields


def _exam(cell,unit,document):
    raw=cell.text();subjects,issues=parse_subjects(raw,unit['id'],document)
    if issues or raw.count('①')!=1:raise ValueError('初试科目不能完整逐单元核对：'+'；'.join(issues))
    slots=sorted({subject['exam_unit'] for subject in subjects});choices=[[subject for subject in subjects if subject['exam_unit']==slot] for slot in slots]
    size=1
    for items in choices:size*=len(items)
    if size>4096:raise ValueError('原文初试科目组合超过本版本安全展开上限')
    proof=evidence(document,raw);subjects=[{**subject,'evidence':proof} for subject in subjects]
    combos=[list(group) for group in product(*[[subject for subject in subjects if subject['exam_unit']==slot] for slot in slots])]
    return subjects,combos,proof


def _only_recommendation(note):
    only=r'(?:^|[。；，,;])(?:本(?:研究)?方向|该方向|本专业)?(?:只|仅)(?:招(?:收)?|接受|接收)'
    return bool(re.search(only+r'(?:免试推荐的|推荐免试的|推免的|推免|推荐免试|免试推荐)(?:硕博连读生|研究生|生)',compact(note)))


def _variants(table,unit,document):
    rows=table.find('tr');header=[compact(cell.text()) for cell in _cells(rows[0])] if rows else []
    if header not in [BASE_HEADER,[BASE_HEADER[0],'指导教师',*BASE_HEADER[1:]]]:raise ValueError('培养单位目录表头未核准')
    indexes={key:header.index(key) for key in header};major=None;anchor=None;groups={}
    for index,row in enumerate(rows[1:],1):
        cells=_cells(row)
        if len(cells)!=len(header):raise ValueError('培养单位专业方向列数缺失')
        text=compact(cells[0].text());matched=re.fullmatch(r'([0-9A-Z]{6})(.+)',text)
        if matched:
            major={'code':matched[1],'name':matched[2],'evidence':evidence(document,cells[0].text()),
                   'note':cells[indexes['备注']].text().strip(),'quota':cells[indexes['预计招生']].text().strip(),'row':index}
            anchor=None;continue
        direction=re.fullmatch(r'([0-9A-Z]{2})\((全日制|非全日制)\)(.+)',text)
        if not direction or major is None:raise ValueError('原始方向缺少专业作用域、方向代码或明确学习方式')
        exam=cells[indexes['考试科目']];inherit=compact(exam.text())=='同上'
        if inherit and anchor is None:raise ValueError('同上不能跨专业或在未公布科目时继承')
        if not inherit:
            subjects,combos,proof=_exam(exam,unit,document);anchor={'subjects':subjects,'combos':combos,'proof':proof,'row':index}
        notes=[value for value in [major['note'],cells[indexes['备注']].text().strip()] if value]
        teacher=cells[indexes['指导教师']].text().strip() if '指导教师' in indexes else None
        refs=[major['evidence'],evidence(document,cells[0].text()),*[evidence(document,note) for note in notes],
              *([evidence(document,teacher)] if teacher else [])]
        exam_refs=[anchor['proof'],*([evidence(document,exam.text())] if inherit else [])]
        variant={'row':index,'major':major,'direction_code':direction[1],'direction_name':direction[3],'mode':direction[2],
                 'advisor':teacher,'notes':notes,'subjects':anchor['subjects'],'combos':anchor['combos'],
                 'exam_anchor_row':anchor['row'],'evidence':refs,'exam_evidence':exam_refs,
                 'recommendation_only':any(_only_recommendation(note) for note in notes)}
        key=(major['code'],direction[1],direction[2])
        if key in groups and groups[key][0]['direction_name']!=direction[3]:raise ValueError('同一报考方向代码存在不同名称，不能自动合并')
        groups={**groups,key:[*groups.get(key,[]),variant]}
    if not groups:raise ValueError('培养单位未公布可核对的硕士方向')
    return groups


def _unique_refs(refs):
    result=[];seen=set()
    for ref in refs:
        key=(ref['document_id'],ref['quote'])
        if key not in seen:result=[*result,ref];seen=seen|{key}
    return result


def _degree(notes):
    labels=set()
    for note in notes:
        text=compact(note)
        if text in {'专业学位','只招专业学位硕士研究生','全日制专业学位硕士生专业'}:labels=labels|{'专业学位'}
        if text in {'学术学位','只招学术学位硕士研究生'}:labels=labels|{'学术学位'}
    return next(iter(labels)) if len(labels)==1 else None


def _program_rows(groups,training,metadata,listing,unit,year,document):
    programs=[];excluded=[]
    for key,variants in groups.items():
        ordinary=[variant for variant in variants if not variant['recommendation_only']]
        if not ordinary:
            excluded=[*excluded,{'document_id':document['id'],'training_unit_code':training['code'],'code':key[0],'direction_code':key[1],
                'reason':'仅招免试推荐生，不归为普通统考候选','evidence':_unique_refs([ref for variant in variants for ref in variant['evidence']])}]
            continue
        first=ordinary[0];bundles={};exam_signatures=set()
        for variant in ordinary:
            own=[]
            for combo in variant['combos']:
                signature=tuple((s['exam_unit'],s['code'],s['name'],s['scope']) for s in combo);bundles={**bundles,signature:combo};own=[*own,signature]
            exam_signatures=exam_signatures|{tuple(own)}
        combos=list(bundles.values());subjects,supported=_matrix(combos)
        notes=list(dict.fromkeys(note for variant in variants for note in variant['notes']))
        degrees=[_degree(variant['notes']) for variant in ordinary]
        degree=degrees[0] if len(set(degrees))==1 else None
        row=_program(unit,year,training['name'],key[0],first['major']['name'],key[1]+' '+first['direction_name'],key[2],subjects,'；'.join(notes),document,degree=degree)
        identity=evidence(document,metadata['单位代码'].text());listing_ref=evidence(listing,training['quote'])
        address_refs=[evidence(listing,training['address'])] if training['address'] else []
        refs=_unique_refs([identity,listing_ref,*address_refs,*[ref for variant in variants for ref in variant['evidence']]])
        exam_refs=_unique_refs([ref for variant in ordinary for ref in variant['exam_evidence']])
        public_variants=[{'source_row_index':variant['row'],'advisor':variant['advisor'],'notes':variant['notes'],
            'exam_anchor_row_index':variant['exam_anchor_row'],'evidence':variant['evidence'],'exam_evidence':variant['exam_evidence'],
            'recommendation_only':variant['recommendation_only']} for variant in variants]
        programs=[*programs,{**row,'id':':'.join([unit['id'],str(year),training['code'],key[0],key[1],key[2]]),
            'department_code':training['code'],'department':training['name'],'province':None,'notes':notes,'evidence':refs,
            'catalog_parser':PARSER,'catalog_document_id':document['id'],'exam_evidence':exam_refs,
            'exam_combinations':[{'subjects':combo} for combo in combos],
            'exam_matching_supported':supported and len(exam_signatures)==1 and not bool(row.get('subject_identity_issues')),
            'advisors':list(dict.fromkeys(variant['advisor'] for variant in ordinary if variant['advisor'])),
            'catalog_variants':public_variants,'training_unit_address':training['address'],
            'source_locator':{'table_id':'detail','training_unit_code':training['code'],'source_row_indices':[v['row'] for v in variants]}}]
    return programs,excluded


def parse_ucas_public(text,unit,year,document,*,project_root,documents):
    training=_context(unit,year,document,documents,project_root)
    if text!=_read(document,project_root,unit,year):raise ValueError('解析输入并非保存的公开目录原始响应')
    tree=_tree(text);metadata=_metadata(tree,training['code'],training['name'])
    tables=[table for table in tree.find('table') if table.attrs.get('id')=='detail' and not table.hidden]
    if len(tables)!=1:raise ValueError('培养单位完整目录表缺失或重复')
    groups=_variants(tables[0],unit,document)
    listing=documents[document['public_form_context']['listing_document_id']]
    programs,excluded=_program_rows(groups,training,metadata,listing,unit,year,document)
    return {'programs':programs,'expected_records':len(programs),'excluded_records':excluded,'issues':[],'retest_rules':[],
            'raw_direction_rows':sum(len(rows) for rows in groups.values())}


def ucas_public_scope(unit,year,documents,*,project_root):
    try:
        _profile(unit,year)
        leaves=[doc for doc in documents.values() if doc.get('unit_id')==unit['id'] and doc.get('year')==year
                and doc.get('catalog_parser')==PARSER and doc.get('catalog_active',True)]
        if not leaves:raise ValueError('尚未保存国科大真实培养单位目录')
        context=leaves[0]['public_form_context'];ids=context['index_document_ids'];entry=documents[context['entry_document_id']]
        first=_index(entry,unit,year,project_root);indexes=[documents[identity] for identity in ids]
        pages=[_index(doc,unit,year,project_root) for doc in indexes]
        expected=first['declared_pages']
        if expected!=set(range(1,max(expected)+1)) or Counter(p['page'] for p in pages)!=Counter(expected):raise ValueError('实际索引所有分页尚未完整采集')
        if any(page['declared_pages']!=expected for page in pages):raise ValueError('公开索引各分页声明的范围不一致')
        links=[link for page in pages for link in page['links']]
        if len({link['code'] for link in links})!=len(links) or Counter(d['url'] for d in leaves)!=Counter(link['url'] for link in links):
            raise ValueError('全部公开培养单位与真实详情来源没有逐一对账')
        programs=[];excluded=[]
        for leaf in leaves:
            other=leaf['public_form_context']
            if set(other['index_document_ids'])!=set(ids) or other['entry_document_id']!=entry['id']:raise ValueError('培养单位目录使用了不同年度索引上下文')
            parsed=parse_ucas_public(_read(leaf,project_root,unit,year),unit,year,leaf,project_root=project_root,documents=documents)
            programs=[*programs,*parsed['programs']];excluded=[*excluded,*parsed['excluded_records']]
        if len({row['id'] for row in programs})!=len(programs):raise ValueError('真实培养单位专业方向身份重复')
        return {'complete':True,'expected_records':len(programs),'expected_document_ids':[leaf['id'] for leaf in leaves],
                'training_units':len(links),'excluded_records':excluded,'issues':[]}
    except (ValueError,KeyError,TypeError,IndexError,OSError) as error:
        return {'complete':False,'expected_records':0,'expected_document_ids':[],'issues':[str(error)]}
