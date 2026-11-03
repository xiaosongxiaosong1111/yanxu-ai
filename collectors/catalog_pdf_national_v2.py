"""Additional explicit PDF schemas, preserving the frozen national-v1 adapter.

Derived column labels are internal parsing metadata. Public quotations always
come from the unchanged raw PDF pages through national-v1's citation checks.
"""
from copy import deepcopy
import re

from .catalog_pdf_national import extract_pdf_national_layout, parse_catalog_pdf_national, _table_rows, _raw_quotes, _canonical_header
from .catalog_html import _header
from .documents import PROJECT_ROOT
from .catalogs import compact
from service_verification import canonical

PARSER='pdf_table_national_v2'
_ALIASES={
    '学院(代码)':'学院','招生院所(代码)及联系方式':'学院',
    '招生学院代码名称及联系方式':'学院','招生专业代码及名称':'专业代码及名称',
    '学科代码、名称及学科方向':'院系所、专业、研究方向',
    '学院、专业名称(代码)研究方向':'院系所、专业、研究方向',
    '学科、专业名称(代码)、研究方向':'专业代码、名称及研究方向',
    '初试科目代码及名称':'初试科目','报考学习方式':'学习方式',
    '研究方向(代码)':'研究方向','专业(代码)':'专业代码及名称',
    '学位类型及学制':'学位类型',
    '专业代码专业名称':'院系所、专业、研究方向',
    '考试科目(包括统考和自命题考试科目)':'初试科目',
}


def extract_pdf_national_layout_v2(path):
    return extract_pdf_national_layout(path)


def _mode_note(value):
    first=str(value).splitlines()[0].strip() if value else ''
    match=re.fullmatch(r'(?:[（(]([12])[）)])?(非全日制|全日制)',compact(first))
    if not match or match[1] and (match[1]=='1')!=(match[2]=='全日制'):return ''
    return match[2]


def _parts(value):
    if re.search(r'任选一组|考试科目组合|方案[一二123]|科目组|成套选择|对应选择',value):return None
    value=re.split(r'参考书目[:：]',value,maxsplit=1)[0]
    value=re.sub(r'[（(]([1-4])[）)]\s*(?=[1-9]\d{2})',lambda m:'①②③④'[int(m[1])-1],value)
    value=re.sub(r'(?<=[①②③④])\s*[▲△*＊]\s*(?=[1-9]\d{2})','',value)
    marks=list(re.finditer(r'[①②③④]|(?<!\d)[1-4][.．、](?=\s*[1-9]\d{2})',value))
    if not marks:return None
    result={}
    for index,match in enumerate(marks):
        slot='①②③④'.index(match[0])+1 if match[0] in '①②③④' else int(match[0][0])
        if slot in result:return None
        text=value[match.end():marks[index+1].start() if index+1<len(marks) else len(value)].strip()
        if not re.match(r'[1-9]\d{2}|[-—－/]|无|不考',compact(text)):return None
        result[slot]=text
    return result


def _origin(table,row_index,column):
    row=table['rows'][row_index]
    if column>=len(row['cells']):return None
    if row['cells'][column]:return row['cells'][column]
    present=[b for b in row['cells'] if b]
    if not present:return None
    y=(min(b[1] for b in present)+min(b[3] for b in present))/2
    matches=[r['cells'][column] for r in table['rows'] if column<len(r['cells'])
             and r['cells'][column] and r['cells'][column][1]-.05<y<r['cells'][column][3]+.05]
    return matches[0] if len(matches)==1 else None


def _header_row(rows):
    for i,row in enumerate(rows[:8]):
        labels=_canonical_header([_ALIASES.get(canonical(value),value) for value in row['texts']])
        names=[canonical(v) for v in labels]
        if _header(labels):return i
        if any(v in {'学科','专业代码及名称','专业','院系所、专业、研究方向'} for v in names) and any(
                v in {'初试科目','考试科目','初试统考科目','初试自命题科目'} for v in names):return i
    return None


def _metadata(table,header_index,resolved):
    rows=table['rows'];header=rows[header_index]['texts'];names=[canonical(v) for v in header];profiles=[]
    compound=next((i for i,v in enumerate(_canonical_header(header)) if '专业' in v and '研究方向' in v),None)
    faculty=next((i for i,v in enumerate(names) if v in {'学院','学院代码及名称'}),None)
    degree=next((i for i,v in enumerate(names) if v in {'学位类别','学位类型'}),None)
    for row in rows[header_index+1:]:
        if compound is not None:
            value=row['texts'][compound]
            if re.match(r'^\s*[0-9A-Z]{6}(?![0-9A-Z])',value) and re.search(r'\n\s*[0-9A-Z]{2}\s+(?=[\u4e00-\u9fff（(])',value):
                row['texts'][compound]=re.sub(r'\s+','',value)
        if faculty is not None and row['texts'][faculty]:
            lines=row['texts'][faculty].splitlines();end=next((i for i,v in enumerate(lines)
                if re.match(r'^[\u4e00-\u9fff、]{1,12}老师|^(?:1\d{10}|0\d{2,3}[-－]\d{7,8})$',v.strip())),len(lines))
            row['texts'][faculty]='\n'.join(lines[:end])
        if degree is not None:
            value=canonical(row['texts'][degree]);match=re.fullmatch(r'(学术学位|专业学位)(?:\d+(?:\.\d+)?年(?:制)?)?',value)
            if match:row['texts'][degree]=match[1]
    major=next((i for i,v in enumerate(names) if v in {'专业','专业代码及名称','专业代码、名称及研究方向'}),None)
    if major is not None and not any(v in {'学习方式','学习形式'} for v in names):
        modes=[re.search(r'\((非全日制|全日制)\)',canonical(row[major])) for row in resolved]
        if any(modes[header_index+1:]):
            for i,row in enumerate(rows):
                row['texts'].append('学习方式' if i==header_index else modes[i][1] if i>header_index and modes[i] else '')
                row['cells'].append(_origin(table,i,major))
            profiles.append('explicit_parenthesized_major_mode')
    return profiles


def _post_enrolment_directions(table,header_index):
    rows=table['rows'];header=rows[header_index]['texts'];names=[canonical(v) for v in header]
    column=next((i for i,v in enumerate(names) if v=='研究方向'),None)
    if column is None:return []
    applied=False
    for row in rows[header_index+1:]:
        value=row['texts'][column]
        if re.fullmatch(r'本专业下设[^。；]+研究方向[，,]入学之后根据实际情况再分方向[。]?',compact(value)):
            row['post_enrolment_direction_note']=value;row['texts'][column]='';applied=True
    return ['explicit_post_enrolment_direction_assignment'] if applied else []


def _hierarchy(table,original,header_index):
    rows=table['rows'];header=rows[header_index]['texts'];names=[canonical(v) for v in header]
    if '学科' not in names or '学科/专业领域' not in names:return []
    parent=names.index('学科');field=names.index('学科/专业领域');values=_table_rows(original)
    header[parent]='专业代码及名称';header[field]='研究方向'
    for i,row in enumerate(rows[header_index+1:],header_index+1):
        row['published_discipline_context']=values[i][parent]
        if re.match(r'^\d{6}(?!\d)[\u4e00-\u9fff]',compact(values[i][field])):
            row['texts'][parent]=values[i][field];row['cells'][parent]=_origin(original,i,field)
            row['texts'][field]='';row['cells'][field]=_origin(original,i,field)
    return ['literal_discipline_or_professional_field']


def _normalize_table(original):
    table=deepcopy(original);rows=table['rows'];profiles=[]
    header_index=_header_row(rows)
    if header_index is None:return table,profiles
    header=rows[header_index]['texts']
    for index,value in enumerate(header):header[index]=_ALIASES.get(canonical(value),value)
    profiles.extend(_hierarchy(table,original,header_index))
    if any(canonical(v)=='院系所、专业、研究方向' for v in header):
        col=next(i for i,v in enumerate(header) if canonical(v)=='院系所、专业、研究方向')
        for row in rows[header_index+1:]:
            if re.match(r'^学院[:：]\s*\d{3}(?!\d)',row['texts'][col]):
                row['texts'][col]=re.sub(r'^学院[:：]\s*','',re.split(r'联系人|联系电话|办公地点',row['texts'][col],maxsplit=1)[0]).strip()
    names=[canonical(v) for v in header]
    if '招生专业' in names and '专业领域' in names:
        header[names.index('招生专业')]='学科类别'
        header[names.index('专业领域')]='专业代码及名称'
        profiles.append('explicit_professional_field')
    resolved=_table_rows(table)
    profiles.extend(_metadata(table,header_index,resolved))
    profiles.extend(_post_enrolment_directions(table,header_index))
    names=[canonical(v) for v in header]
    note=next((i for i,v in enumerate(names) if v in {'备注','报考条件补充说明'}),None)
    has_mode=any(v in {'学习方式','学习形式','报考学习方式','学习方式/学制'} for v in names)
    if note is not None and not has_mode:
        mode_column=len(header)
        for i,row in enumerate(rows):
            text='学习方式' if i==header_index else _mode_note(resolved[i][note]) if i>header_index else ''
            row['texts'].append(text);row['cells'].append(_origin(original,i,note))
        profiles.append('explicit_note_mode')
    # A named pair of INITIAL-exam columns is split by its printed unit markers,
    # so a shared national cell cannot borrow a neighbouring local course.
    exams=[i for i,v in enumerate(names) if re.fullmatch(r'初试统考科目|初试自命题科目',v)]
    if len(exams)!=2 and header_index+1<len(rows):
        sub=[canonical(v) for v in resolved[header_index+1]]
        if '统考科目' in sub and '自命题科目' in sub:
            exams=[sub.index('统考科目'),sub.index('自命题科目')]
    if len(exams)!=2:
        exams=[i for i,v in enumerate(names) if v in {'考试科目','初试科目','初试考试科目'}]
        if len(exams)==1 and not any(re.search(r'[（(][1-4][）)]\s*\d{3}|参考书目[:：]|[①②③④]\s*[▲△*＊]',row[exams[0]]) for row in resolved[header_index+1:]):exams=[]
    if exams:
        base=len(rows[header_index]['texts'])
        for col in exams:header[col]='原始初试列'
        for i,row in enumerate(rows):
            items={};bad=False
            if i>header_index:
                for col in exams:
                    parts=_parts(resolved[i][col])
                    if not parts:bad=True;continue
                    for slot,value in parts.items():
                        if slot in items:bad=True
                        items[slot]=(value,_origin(original,i,col))
            for slot in range(1,5):
                value,box=items.get(slot,('',None))
                row['texts'].append('初试科目'+'一二三四'[slot-1] if i==header_index else '' if bad else value)
                row['cells'].append(row['cells'][exams[0]] if i==header_index else box)
        profiles.append('printed_initial_unit_columns')
    return table,profiles


def prepare_layout(layout):
    result=deepcopy(layout);profiles=[];previous=None
    for page in result.get('pages',[]):
        tables=[]
        for index,table in enumerate(page.get('tables',[]),1):
            head=_header_row(table['rows']);template=table['rows'][head]['texts'] if head is not None else None
            continuation=previous and index==1 and previous['page']==page['page']-1 and table['rows'] and table['bbox'][1]<page['height']*.35 and len(table['rows'][0]['texts'])==len(previous['header']) and all(abs(table['bbox'][i]-previous['bbox'][i])<2 for i in [0,2])
            inherited=head is None and continuation
            if inherited:
                template=previous['header']
                table={**table,'rows':[{'texts':list(template),'cells':[None]*len(template)},*table['rows']]}
            converted,applied=_normalize_table(table)
            if inherited:converted={**converted,'rows':converted['rows'][1:]}
            tables.append(converted)
            profiles.extend({'page':page['page'],'table':index,'profile':value} for value in applied)
            previous={'page':page['page'],'header':list(template),'bbox':table['bbox']} if template else None
        page['tables']=tables
    return result,profiles


def parse_catalog_pdf_national_v2(layout,unit,year,document,*,project_root=PROJECT_ROOT):
    prepared,profiles=prepare_layout(layout)
    parsed=parse_catalog_pdf_national(prepared,unit,year,document,project_root=project_root)
    rejected=[program for program in parsed['programs'] if _conditional_mode_only(program)]
    chapters=_degree_chapters(prepared,unit,year)
    programs=[_source_annotations(program,prepared,document,chapters) for program in parsed['programs'] if program not in rejected]
    return {**parsed,'catalog_format':PARSER,'normalization_profiles':profiles,
            'issues':[*parsed['issues'],*['报考学历条件中的全日制不是招生学习方式，未采用该方向' for _ in rejected],
                      *['目录附注与 PDF 原文未完整核对，未采用该专业' for value in programs if value is None]],
            'programs':[{**program,'catalog_parser':PARSER} for program in programs if program is not None]}


def _degree_chapters(layout,unit,year):
    result={};active=None;number=0
    for page in layout['pages']:
        tables=page.get('tables',[])
        if len(tables)!=1:active=None;number+=len(tables);continue
        table=tables[0];number+=1
        header=next((_header(_canonical_header(row['texts'])) for row in table['rows'][:8]
                     if _header(_canonical_header(row['texts']))),None)
        if not header:active=None;continue
        lines=[line['text'] for line in page.get('lines',[]) if line['y1']<=table['bbox'][1]+1]
        fresh=None
        for previous,current in zip(lines,lines[1:]):
            label=re.fullmatch(r'\((学术型|专业型)\)招生专业目录及简介',canonical(current))
            if label and re.fullmatch(re.escape(canonical(unit['name']))+str(year)+r'年招收攻读硕士学位研究生',canonical(previous)):
                fresh={'degree':{'学术型':'学术学位','专业型':'专业学位'}[label[1]],
                       'quote':previous+'\n'+current,'source_page':page['page'],'header':header['headers'],'bbox':table['bbox']}
        if fresh:active=fresh
        elif not active or active['page']!=page['page']-1 or active['header']!=header['headers'] or any(
                abs(active['bbox'][i]-table['bbox'][i])>=2 for i in [0,2]):active=None
        if active:
            active={**active,'page':page['page']};result[number]=active
    return result


def _source_annotations(program,layout,document,chapters):
    location=program['source_locator'];tables=[table for page in layout['pages'] for table in page.get('tables',[])]
    row=tables[location['table']-1]['rows'][location['row']-1];result={**program}
    note=row.get('post_enrolment_direction_note');context=row.get('published_discipline_context','')
    degree=re.search(r'\((学术型学位|学术学位|专业学位)\)',canonical(context)) if not program.get('degree_type') else None
    for value in [*([note] if note else []),*([context] if degree else [])]:
        refs=_raw_quotes(value,layout['raw_text'].rstrip('\f\r\n ').split('\f'),document,location['page'])
        if not refs:return None
        result={**result,'evidence':[*result['evidence'],*refs]}
    if note:result={**result,'direction_scope':'program','notes':[*result.get('notes',[]),note]}
    if degree:result={**result,'degree_type':{'学术型学位':'学术学位'}.get(degree[1],degree[1])}
    chapter=chapters.get(location['table'])
    if chapter and not result.get('degree_type'):
        refs=_raw_quotes(chapter['quote'],layout['raw_text'].rstrip('\f\r\n ').split('\f'),document,chapter['source_page'])
        if not refs:return None
        result={**result,'degree_type':chapter['degree'],'evidence':[*result['evidence'],*refs]}
    return result


def _conditional_mode_only(program):
    quotes=[canonical(ref.get('quote','')) for ref in program.get('evidence',[])]
    conditional=lambda value:bool(re.search(r'(?:仅|只)(?:招收|招|接收).*(?:非全日制|全日制).*本科毕业生',value))
    if not any(conditional(value) for value in quotes):return False
    return not any(not conditional(value) and (value in {'全日制','非全日制'}
        or re.search(r'\((?:非)?全日制\)|(?:全日制|非全日制).*(?:招生|专业).*目录|(?:学习方式|学习形式|均招收|全部招收)(?:为|是|均为|全部为|[:：])?(?:非)?全日制',value)) for value in quotes)
