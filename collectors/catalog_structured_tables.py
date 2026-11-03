"""Strict original-cell schemas for HTML tables and real Excel merge ranges."""
from collections import defaultdict
import hashlib
import json
import re

from .catalog_facts import _Cells, _scope, _excluded_row, LINKED_EXAMS, _visible
from .catalog_html import _header, _row_records, _CODE, _national
from .catalog_html_national import _source_valid
from .catalog_office_national import workbook_layout, _range
from .catalog_publication import verify_catalog_publication, whole_document_mode
from .catalogs import compact, evidence
from .documents import PROJECT_ROOT, decode_text
from public_sources import official_source_url, document_attribution
from service_verification import plain_text

HTML_PARSER='html_structured_v1'
OFFICE_PARSER='office_structured_v1'
ALIASES={'招生学院':'学院','学院名称':'学院','培养单位':'学院','招生学院联系方式':'学院',
    '招生专业':'专业代码及名称','专业代码及专业名称':'专业代码及名称',
    '学科专业代码及名称':'专业代码及名称','研究方向代码及名称':'研究方向',
    '招生专业代码及名称':'专业代码及名称','专业研究方向':'研究方向',
    '初试考试科目':'初试科目','学位类型':'学位类别',
    '专业代码与名称及研究方向':'专业代码、名称及研究方向',
    '专业名称及研究方向':'专业代码、名称及研究方向','培养方式':'学习方式'}
DEGREES={'学术型':'学术学位','学术型学位':'学术学位','学硕':'学术学位',
         '专业型':'专业学位','专业型学位':'专业学位','专硕':'专业学位'}


def _empty(parser,reason=None):
    return {'programs':[],'expected_records':0,'issues':[reason] if reason else [],
        'retest_rules':[],'documents':[],'excluded_records':[],'catalog_format':parser,'table_manifests':[]}


def _get(row,column):
    return row[column]['text'] if column is not None and column<len(row) and row[column] else ''


def _schema(row):
    values=[ALIASES.get(compact(_get(row,i)),_get(row,i)) for i in range(len(row))]
    return _header(values)


def _whole_mode(text,unit,year):
    blocks=[plain_text(part) for part in re.split(r'</(?:p|div|h[1-6]|li|tr)\s*>|<br\s*/?>',text,flags=re.I)]
    prefix=r'(?:(?:[（(]?[一二三四五六七八九十\d]+[）).、])?(?:招生类别及学制|学习方式与修业年限|学习方式及学制|学制及学习方式|学习方式|学习形式)[:：]?)?'
    body=r'(?:我校硕士研究生招生专业学习方式为|我校学术学位和专业学位硕士研究生的学习方式为)(非全日制|全日制)(?:[，,]学制\d+年)?'
    claims=[]
    for block in blocks:
        claims.extend(whole_document_mode(block,unit,year)['claims'])
        for sentence in re.findall(r'[^。；;\n]+[。；;\n]?',block):
            match=re.fullmatch(prefix+body,compact(sentence).rstrip('。；;'))
            if match:claims.append({'mode':match[1],'quote':sentence.strip()})
    modes={claim['mode'] for claim in claims}
    return (claims[0]['mode'],claims[0]['quote']) if len(modes)==1 else (None,'')


def _mode(row,header,context):
    value=compact(_get(row,header['mode']))
    match=re.fullmatch(r'(?:\([12]\))?(非全日制|全日制)',value)
    if match:return match[1] if not context or match[1]==context else None
    if value:return None
    declared=set()
    for column in [header['direction'],header['major'],header['name']]:
        declared.update(re.findall(r'\((非全日制|全日制)\)',compact(_get(row,column))))
    if len(declared)>1:return None
    mode=next(iter(declared)) if declared else context
    return mode if not context or not mode or mode==context else None


def _exclusive_scope(text):
    text=compact(text)
    text=re.sub(r'(?:并非|并不|不是|不只|不仅|非仅)(?:仅|只)?(?:接受|接收|招收|招)?(?:推荐免试|推免)(?:考生|研究生|生)?','',text)
    text=re.sub(r'(?:并非|并不|不是|不)(?:不|拒绝)(?:接收|接受|招收|招)(?:全国)?(?:统考|统一考试)(?:考生|研究生|生)?','',text)
    return _excluded_row(text)


def _linked_exam_units(text):
    text=compact(text)
    return bool(LINKED_EXAMS.search(text) or re.search(
        r'(?:③|④|第[三四34]单元|科目[三四34]).{0,50}(?:搭配|配对|对应)|'
        r'(?<!\d)[1-9]\d{2}(?!\d).{0,15}(?:搭配|配对|一一对应)',text))


def _faculty(value):
    return compact(re.split(r'拟招生|招生计划|联系人|咨询电话|联系电话|联系方式|网址[:：]|网站[:：]',value,maxsplit=1)[0]).rstrip('（(:：')


def _reference(document,cell,table,row,sheet=None):
    return {**evidence(document,cell['text']), 'quote':cell['text'], 'source_locator':{
        'table':table,'row':row,'column':cell['column'],'origin_row':cell['origin_row'],
        **({'sheet':sheet} if sheet else {})}}


def _national_language_list(text):
    value=compact(text);section=re.search(r'②([^①③④]+)',value)
    if not section:return text
    codes=list(re.finditer(r'(?<!\d)(2\d{2})(?!\d)',section[1]))
    if len(codes)<2:return text
    pairs=[(code[1],section[1][code.end():codes[i+1].start() if i+1<len(codes) else len(section[1])]) for i,code in enumerate(codes)]
    if not all(_national(code,name) for code,name in pairs):return text
    return value[:section.start(1)]+'或'.join(code+name for code,name in pairs)+value[section.end(1):]


def _program_row(row,header,unit,year,document,table,row_number,context,sheet):
    original=[_get(row,i) for i in range(max(len(row),len(header['headers']))) ]
    if _exclusive_scope('；'.join(original)):
        return [],0,[],[{'reason':'原文明示仅推免或不接收统考','table':table,'row':row_number}]
    if _linked_exam_units('；'.join(original)):
        return [],1,['跨单元成套科目组合未独立适配，未平铺备选'],[]
    identity=original[header['code']] if header['code'] is not None else original[header['major']]
    if len(_CODE.findall(identity))!=1:return [],1,['原格未声明唯一专业代码，未代选多个父子或新旧代码'],[]
    mode=_mode(row,header,context)
    if not mode:return [],1,['原文未明确本行学习方式'],[]
    values=list(original)
    if header['department'] is not None:
        values[header['department']]=_faculty(values[header['department']])
        if not values[header['department']]:return [],1,['学院列存在但本行无真实院系归属'],[]
    if header['degree'] is not None:
        value=compact(values[header['degree']]);values[header['degree']]=DEGREES.get(value,value)
    if header['exam'] is not None:values[header['exam']]=_national_language_list(values[header['exam']])
    adjusted={**header,'mode':len(values)}
    records,count,issues,excluded=_row_records([*values,mode],adjusted,unit,year,document,table,row_number)
    semantic=[header.get(key) for key in ['department','department_code','major','code','name','direction','direction_code','mode','degree','entry_method','note']]
    columns=[header['exam']] if header['exam'] is not None else list(header['slots'].values())
    refs=[_reference(document,row[i],table,row_number,sheet) for i in dict.fromkeys(semantic) if i is not None and i<len(row) and row[i] and row[i]['text']]
    exams=[_reference(document,row[i],table,row_number,sheet) for i in columns if i<len(row) and row[i] and row[i]['text']]
    result=[]
    for record in records:
        if any(re.search(r'(?<!\d)[1-9]\d{2}(?!\d)',subject['name']) for subject in record['subjects']):
            issues=[*issues,'科目名称仍含未分解的三位考试代码，未漏列备选'];continue
        updated={**record,'evidence':refs,'exam_evidence':exams,
            'subjects':[{**subject,'evidence':exams} for subject in record['subjects']],
            'source_locator':{'table':table,'row':row_number,**({'sheet':sheet} if sheet else {})}}
        if header['department'] is not None and not record.get('department_code'):
            name=values[header['department']];identity='name-'+hashlib.sha256(name.encode()).hexdigest()[:16]
            parts=record['id'].split(':');parts[2]=identity
            updated={**updated,'id':':'.join(parts),'department':name,'department_scope':'named_department'}
        result.append(updated)
    return result,count,issues,excluded


def _parse_tables(tables,unit,year,document,parser,text='',publication=None):
    result=_empty(parser);context,quote=_whole_mode(text,unit,year)
    for number,table in enumerate(tables,1):
        rows=table['rows'];selected=next(((i,_schema(row)) for i,row in enumerate(rows[:8]) if _schema(row)),None)
        if selected is None:
            contents=[''.join(_get(row,i) for i in range(len(row))) for row in rows]
            if any(re.search(r'[①②③④]\s*[1-9]\d{2}',value) or (_CODE.search(value) and re.search(
                    r'(?<!\d)(?:101|199)(?!\d)',value) and len(re.findall(r'(?<!\d)[1-9]\d{2}(?!\d)',value))>=2) for value in contents):
                result['issues'].append(f'表{number}含初试内容但表头尚未适配')
            continue
        start,header=selected;before=len(result['programs']);expected=0
        for index,row in enumerate(rows[start+1:],start+2):
            if _schema(row) or not any(_get(row,i).strip() for i in range(len(row))):continue
            major=_get(row,header['code'] if header['code'] is not None else header['major'])
            exam=''.join(_get(row,i) for i in ([header['exam']] if header['exam'] is not None else header['slots'].values()))
            direction_only=any(re.match(r'^[0-9A-Z]{2}(?![0-9A-Z])[.、．]?[\u4e00-\u9fff]',compact(_get(row,i))) for i in range(len(row)))
            if not _CODE.search(major) and not re.search(r'[①②③④]\s*\d{3}',exam) and not direction_only:continue
            programs,count,issues,excluded=_program_row(row,header,unit,year,document,number,index,context,table.get('sheet'))
            if quote:
                programs=[{**p,'evidence':[*p['evidence'],evidence(document,quote)]} for p in programs]
            result['programs'].extend({**p,'catalog_parser':parser} for p in programs)
            result['issues'].extend(f'表{number}第{index}行：'+issue for issue in issues)
            result['excluded_records'].extend(excluded);expected+=count
        result['expected_records']+=expected
        result['table_manifests'].append({'table':number,'header_row':start+1,'physical_rows':len(rows),
            'parsed_records':len(result['programs'])-before,'expected_records':expected})
    return _deduplicate(result,publication)


def _deduplicate(result,publication):
    from .catalog_semantic_audit import _facts
    groups=defaultdict(list)
    for program in result['programs']:groups[program['id']].append(program)
    accepted=[]
    for group in groups.values():
        facts={json.dumps(_facts({key:value for key,value in p.items() if key!='source_locator'}),ensure_ascii=False,sort_keys=True) for p in group}
        if len(facts)>1:result['issues'].append('相同方向身份存在不同原始事实，冲突行均未采用');continue
        result['expected_records']-=len(group)-1
        first=group[0]
        accepted.append({**first,'duplicate_source_locators':[p['source_locator'] for p in group]} if len(group)>1 else first)
    if not result['table_manifests'] and not result['issues']:result['issues'].append('未发现可核验的初试目录原始表头')
    return {**result,'programs':accepted,'documents':(publication or {}).get('documents',[])}


def parse_html_catalog_structured(raw,unit,year,document,*,project_root=PROJECT_ROOT):
    html,failure=_scope(raw,unit,year,document)
    if failure=='原文主标题未明确单一参考年度，未采用历史链接推断。':
        candidate=_visible(decode_text(raw,document.get('content_type','')),document['url'])
        headings=[plain_text(value) for value in re.findall(r'<h1\b[^>]*>(.*?)</h1>',candidate,re.I|re.S)]
        own=[value for value in headings if compact(unit['name']) in compact(value)
             and set(re.findall(r'(?<!\d)20\d{2}(?!\d)',value))=={str(year)} and re.search(r'硕士.*(?:招生|专业).*目录',value)]
        if own and _source_valid(candidate,unit,year,document) and document_attribution(
                document['url'],unit,'',own[0],own[0])['status']=='verified':html,failure=candidate,None
    if failure:return _empty(HTML_PARSER,failure)
    native=_Cells();native.feed(html)
    return _parse_tables([{'rows':rows} for rows in native.tables],unit,year,document,HTML_PARSER,html)


def _office_tables(sheets):
    tables=[]
    for sheet in sheets:
        maximum=max((r for r,c in sheet['cells']),default=0);width=max((c for r,c in sheet['cells']),default=0)
        rows=[]
        for row in range(1,maximum+1):
            values=[]
            for column in range(1,width+1):
                area=_range(sheet,row,column);value=sheet['cells'].get((area[0],area[1]),'')
                values.append({'text':value,'origin_row':area[0],'column':area[1]})
            rows.append(values)
        tables.append({'sheet':sheet['name'],'rows':rows})
    return tables


def parse_office_catalog_structured(raw,unit,year,document,*,project_root=PROJECT_ROOT):
    if (not isinstance(unit,dict) or not isinstance(document,dict)
            or not isinstance(raw,bytes) or not 0<len(raw)<=32*1024*1024 or type(year) is not int or year!=2027
            or document.get('year')!=year or str(document.get('unit_id'))!=str(unit.get('id'))
            or hashlib.sha256(raw).hexdigest()!=document.get('sha256') or not official_source_url(document.get('url'),unit)):
        return _empty(OFFICE_PARSER,'Office原文摘要、官方归属、单位或年度未匹配')
    sheets=workbook_layout(raw)
    titles=list(dict.fromkeys(value for sheet in sheets for (row,_),value in sheet['cells'].items()
        if row<=5 and '目录' in value and re.search(r'20\d{2}',value)))
    if any(set(re.findall(r'(?<!\d)20\d{2}(?!\d)',value))!={str(year)} or re.search(
            r'单独考试|强军|推免|推荐免试|博士招生|港澳台',value) for value in titles):
        return _empty(OFFICE_PARSER,'Office主标题年度或普通统考范围不一致')
    publication=None
    if not titles or not any(compact(unit['name']) in compact(value) for value in titles):
        publication=verify_catalog_publication(document,unit,year,project_root=project_root)
        if not publication['verified']:return _empty(OFFICE_PARSER,'Office校名或原始发布父页未独立核验')
    return _parse_tables(_office_tables(sheets),unit,year,document,OFFICE_PARSER,'\n'.join(titles),publication)
