"""Strict physical-grid PDF adapter with original raw-page citations.

Cell inheritance is geometric, never a forward fill from the previous row.
Optional pdfplumber supplies the physical grid; publication still needs separate
source replay and root semantic approval before any records may be adopted.
"""
import importlib
import json
from collections import defaultdict
from pathlib import Path
import re
import sys

from .catalog_html import _header, _major, _directions, _row_records, _exam_method
from .catalog_pdf_tables import _source_conflict, extract_pdf_layout, _cell_lines, _references
from .catalog_publication import verify_catalog_publication
from .catalogs import compact, evidence
from .documents import PROJECT_ROOT, DocumentError
from service_verification import canonical, ordinary_catalog_scope

PARSER = 'pdf_table_national_v1'
_HEADERS = {
    '学科专业代码、名称、研究方向':'专业代码、名称及研究方向',
    '学科、专业名称(代码)研究方向':'专业代码、名称及研究方向',
    '学科专业(代码)、研究方向':'专业代码、名称及研究方向',
    '学科、专业名称(代码)、研究方向':'专业代码、名称及研究方向',
    '专业、研究方向':'专业代码、名称及研究方向',
    '专业代码、名称、研究方向':'专业代码、名称及研究方向',
    '院系所代码及名称':'学院代码及名称',
    '院系所名称':'学院', '培养单位':'学院',
    '初试考试科目':'初试科目', '初试科目及代码':'初试科目',
    '考试科目代码及名称':'考试科目', '系所代码及名称':'学院代码及名称',
    '院系名称':'学院', '院系代码':'院系所代码', '专业类别及代码':'专业代码及名称',
    '学科专业代码':'专业代码', '学科专业名称':'专业名称',
    '学院代码、名称、专业代码、名称及研究方向':'院系所、专业、研究方向',
    '院系、专业及方向':'院系所、专业、研究方向',
    '学科专业代码、名称、研究领域':'专业代码、名称及研究方向',
    '学习方式/学制':'学习方式', '学习方式及学制':'学习方式',
    '学院(中心、所)、专业、研究方向':'院系所、专业、研究方向',
    '类别(领域)代码、名称及研究方向':'院系所、专业、研究方向',
    '专业代码、专业名称及研究方向':'专业代码、名称及研究方向',
    '专业代码、名称研究方向':'专业代码、名称及研究方向',
    '初试考试科目参考书目':'初试科目', '学习形式学费学制':'学习方式',
    '考试科目及代码':'考试科目', '初试科目(带*者为自命题科目)':'初试科目',
    '招生培养单位及联系方式':'学院',
    '系所名称':'学院', '专业(领域)':'专业代码及名称',
}


def _plumber():
    try:
        return importlib.import_module('pdfplumber')
    except ImportError:
        target = PROJECT_ROOT / '.runtime-deps'
        if not (target/'pdfplumber/__init__.py').is_file():
            raise DocumentError('PDF真实网格适配需要项目隔离依赖pdfplumber==0.11.7')
        sys.path.insert(0, str(target))
        try: return importlib.import_module('pdfplumber')
        except ImportError as error: raise DocumentError('项目隔离PDF网格依赖不可加载') from error


def extract_pdf_national_layout(path):
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= 32*1024*1024:
        raise DocumentError('PDF原文不存在或超过32MB')
    native = extract_pdf_layout(path)
    raw_text = native['raw_text']
    pages = []
    try:
        with _plumber().open(path) as pdf:
            if not pdf.doc.is_extractable:
                raise DocumentError('PDF原文禁止文本提取，保留公开入口待人工核验')
            if len(pdf.pages) > 1500: raise DocumentError('PDF超过1500页，须分批处理')
            for index, page in enumerate(pdf.pages, 1):
                tables = []
                for table in page.find_tables():
                    texts = table.extract(x_tolerance=2,y_tolerance=3)
                    tables.append({'bbox':list(table.bbox),'rows':[
                        {'texts':[v or '' for v in values],
                         'cells':[list(cell) if cell else None for cell in row.cells]}
                        for row,values in zip(table.rows,texts)]})
                lines = [{'text':line['text'],'x0':line['x0'],'x1':line['x1'],
                    'y0':line['top'],'y1':line['bottom']} for line in page.extract_text_lines()]
                pages.append({'page':index,'width':page.width,'height':page.height,
                              'tables':tables,'lines':lines,'native':native['pages'][index-1]})
    except DocumentError: raise
    except (ValueError, OSError, TypeError, IndexError) as error:
        raise DocumentError('PDF真实网格提取失败，未回退猜测相邻行') from error
    return {'pages':pages,'raw_text':raw_text}


def _cell_value(table, row_index, column):
    rows = table['rows']
    row = rows[row_index]
    cells = row['cells']
    if column >= len(cells): return ''
    own = cells[column]
    if own is not None: return row['texts'][column] or ''
    present = [cell for cell in cells if cell]
    if not present: return ''
    top = min(cell[1] for cell in present)
    lower = min(cell[3] for cell in present)
    middle = (top+lower)/2
    matches = [(i, r['texts'][column]) for i,r in enumerate(rows)
               if column < len(r['cells']) and (box:=r['cells'][column])
               and box[1]-.05 < middle < box[3]+.05]
    return matches[0][1] or '' if len(matches)==1 else ''



def _cell_semantics(value):
    first=compact(str(value).splitlines()[0]) if value else ''
    postfix=re.fullmatch(r'(.+?)\(([0-9A-Z]{3}|[0-9A-Z]{4}|[0-9A-Z]{6})\)(?:\((学术型|专业型|学术学位|专业学位)\))?',first)
    if postfix and (len(postfix[2])!=3 or re.search(r'学院|系|部|中心|研究所|研究院',postfix[1])):
        degree={'学术型':'学术学位','专业型':'专业学位'}.get(postfix[3],postfix[3])
        return postfix[2]+postfix[1]+('（'+degree+'）' if degree else '')
    direction=re.match(r'^\(([0-9A-Z]{2})\)(.+)$',compact(value))
    return direction[1]+direction[2] if direction else value


def _document_mode(raw,year):
    lines=raw.splitlines()[:10]
    for index,line in enumerate(lines):
        if not re.search(str(year)+r'.*(?:硕士|研究生).*目录',line):continue
        text=line
        if index+1<len(lines) and re.fullmatch(r'[（(]?(?:非全日制|全日制)(?:培养)?[）)]?',compact(lines[index+1])):
            text+='\n'+lines[index+1]
        modes=set(re.findall(r'非全日制|(?<!非)全日制',text))
        if len(modes)==1:return next(iter(modes)),text
    match=re.search(r'(?:我校|本校|本目录)(?:所有|全部|各)(?:专业|学科)(?:只|仅)招收(非全日制|全日制)硕士研究生[。；]',raw)
    return (match[1],match[0]) if match else (None,'')


def _table_rows(table):
    return [[_cell_value(table,i,j) for j in range(len(row['cells']))]
            for i,row in enumerate(table['rows'])]


def _canonical_header(row):
    return [_HEADERS.get(canonical(value),value) for value in row]


def _raw_quotes(text, pages, document, preferred):
    """Locate exact bytes-as-text fragments; never return transformed table text."""
    parts=[part.strip() for part in str(text).splitlines() if part.strip()]
    result=[]
    for part in parts:
        needle=compact(part)
        if not needle:continue
        pattern=r'\s*'.join('[（(]' if c=='(' else '[）)]' if c==')' else re.escape(c) for c in needle)
        found=None
        for page_number in [preferred,*[n for n in range(1,len(pages)+1) if n!=preferred]]:
            match=re.search(pattern,pages[page_number-1])
            if match:
                found=evidence(document,match[0],page_number);break
        if found is None:return None
        result.append(found)
    return result


def _mapped_refs(refs, cells, raw_pages, document, page, native_refs=None):
    result=[]
    for ref in refs if isinstance(refs,list) else [refs] if isinstance(refs,dict) else []:
        quote=ref.get('quote','')
        direct=_raw_quotes(quote,raw_pages,document,page)
        if direct is None:
            matching=next((value for value in cells if canonical(value)==canonical(quote)),None)
            direct=_raw_quotes(matching,raw_pages,document,page) if matching else None
        if direct is None and native_refs:
            direct=next((refs for value,refs in native_refs if canonical(value)==canonical(quote)
                         or canonical(quote) in canonical(value)),None)
        if direct is None:return None
        result.extend(direct)
    return list({(r['page'],r['quote']):r for r in result}.values())



def _department_value(value):
    cleaned=re.split(r'联系人|联系电话|咨询电话|联系方式|网址[:：]|邮箱|QQ交流',str(value),maxsplit=1)[0]
    cleaned=compact(cleaned).rstrip('（(:：')
    return _cell_semantics(cleaned)


def _page_context(page, table, raw_pages, publication):
    above=[line for line in page.get('lines',[]) if line['y1'] <= table['bbox'][1]+1]
    department=next((line['text'] for line in reversed(above)
        if re.match(r'^\s*(?:招生学院[:：])?\d{3}(?!\d).*(?:学院|学部|中心|研究所|研究院)',compact(line['text']))), '')
    department=re.sub(r'^招生学院[:：]','',compact(department))
    mode_lines=[line['text'] for line in above if re.search(r'(?:全日制|非全日制).*(?:招生|专业|目录)',line['text'])]
    modes=re.findall(r'非全日制|(?<!非)全日制',mode_lines[-1]) if mode_lines else []
    mode=modes[0] if len(set(modes))==1 else publication.get('mode')
    return department,mode,(mode_lines[-1] if modes else '')



def _chapter_degree(page,table,previous):
    headings=[line['text'] for line in page.get('lines',[]) if line['y1']<=table['bbox'][1]+1
              and re.search(r'(?:学术学位|专业学位).*招生.*(?:目录|领域)',line['text'])]
    if headings:
        values=set(re.findall(r'学术学位|专业学位',headings[-1]))
        if len(values)==1:return next(iter(values)),headings[-1]
    return ((previous or {}).get('degree'),(previous or {}).get('degree_quote',''))


def _scope_issue(layout,unit,year,publication):
    if _source_conflict(layout,unit,year):return _source_conflict(layout,unit,year)
    raw=layout.get('raw_text','')
    if not ordinary_catalog_scope(raw):return '原文主标题为特殊招生范围，不作普通统考目录'
    title=''.join(raw.splitlines()[:12])
    own=canonical(unit.get('name',''))
    if not publication['verified'] and not (own and own+str(year) in canonical(title)
            and re.search(r'硕士.*(?:招生|专业).*目录',title)):
        return 'PDF或官方父页未能独立证明学校和年度'
    if publication.get('mode_conflict'):return '官方发布页学习方式相互矛盾'
    return None


def _parse_table(table,page,unit,year,document,raw_pages,publication,table_index,previous=None):
    originals=_table_rows(table)
    rows=[[_cell_semantics(value) for value in row] for row in originals]
    native_refs=[]
    for original in table['rows']:
        for value,box in zip(original['texts'],original['cells']):
            if not box or not value:continue
            refs=_raw_quotes(value,raw_pages,document,page['page'])
            if refs is None and page.get('native'):
                fragments=_cell_lines(page['native'],box)
                candidate,missing=_references(fragments,document,raw_pages)
                if not missing and canonical(''.join(line['text'] for _,line in fragments))==canonical(value):refs=candidate
            if refs:
                native_refs.extend([(value,refs),(_cell_semantics(value),refs)])
    result={'programs':[],'expected_records':0,'issues':[],'excluded_records':[]}
    selected=next(((i,_header(_canonical_header(row))) for i,row in enumerate(rows[:8])
                   if _header(_canonical_header(row))),None)
    continuation=previous and previous.get('page')==page['page']-1 and table['bbox'][1]<page['height']*.35 and previous.get('column_count')==len(rows[0]) and abs(previous['left']-table['bbox'][0])<2 and abs(previous['right']-table['bbox'][2])<2
    if selected is None and continuation:
        selected=(-1,previous['header'])
    if selected is None:
        if any(re.search(r'[①②③④]\s*\d{3}',value) for row in rows for value in row):
            result['issues'].append('含初试内容的表格表头未适配，不能忽略')
        return result
    start,header=selected
    continuation=bool(continuation and (start==-1 or header['headers']==previous['header']['headers']))
    if header['department'] is not None:
        col=header['department']
        rows=[[(_department_value(value) if j==col and i>start else value) for j,value in enumerate(row)] for i,row in enumerate(rows)]
        native_refs=[*native_refs,*[(_department_value(value),refs) for value,refs in native_refs]]
    department,mode,mode_quote=_page_context(page,table,raw_pages,publication)
    degree,degree_quote=_chapter_degree(page,table,previous if continuation else None)
    inherited={**previous.get('inherited',{})} if continuation else {}
    if department:inherited={**inherited,'department':department,'department_quote':department}
    if not mode and previous and continuation:mode=previous.get('mode')
    if not mode:
        mode,mode_quote=_document_mode('\n'.join(raw_pages),year)
    for index,row in enumerate(rows[start+1:],start+2):
        if _header(_canonical_header(row)):continue
        values=list(dict.fromkeys(value for value in row if value))
        if not values:continue
        if len(values)==1 and (re.fullmatch(r'[-—\d]+',compact(values[0])) or re.match(r'^拟招人数|^其中推免|^招生人数',compact(values[0]))):continue
        if row[0] in {'合计','总计'}:continue
        get=lambda field:row[header[field]] if header.get(field) is not None and header[field]<len(row) else ''
        if re.search(r'(?:仅|只)(?:接收|招收)(?:推免|推荐免试)生|不(?:接收|招收)统考生',get('note')+' '+get('direction')):
            result['excluded_records'].append({'reason':'原文明示仅推免或不接收统考','page':page['page'],'row':index});continue
        major_text=get('major')
        field_row=re.match(r'^\s*([0-9A-Z]{2})\s+([0-9A-Z]{6})\s*[\u4e00-\u9fff]',major_text)
        field_row=field_row if inherited.get('major') and len(inherited['major'][0])==4 else None
        major=None if field_row else _major(row,header)
        if not major and header['major'] is not None and re.match(r'^\d{3}(?!\d)[^\d].*(?:学院|学部|学系|研究所|研究院|中心|部|系)',compact(major_text)):
            inherited={'department':compact(major_text),'department_quote':major_text};continue
        if get('department'):
            inherited={**inherited,'department':get('department'),'department_quote':get('department')}
        if header['department'] is not None and not get('department') and not inherited.get('department'):
            result['expected_records']+=1;result['issues'].append('学院列存在但归属缺失，不能降为全校范围');continue
        if major and (not re.search(r'[\u4e00-\u9fff]',major[1])
                or re.search(r'电话|联系方式|咨询|^\d',major[1])):
            result['expected_records']+=1;result['issues'].append('专业身份疑似电话或非专业内容，未采用');continue
        next_row=rows[index] if index<len(rows) else []
        next_major=next_row[header['major']] if header['major'] is not None and header['major']<len(next_row) else ''
        parent=header['direction'] is None and major and not major[3] and bool(re.match(r'^[0-9A-Z]{2}(?![0-9A-Z])(?=[（(.、．\u4e00-\u9fff])',compact(next_major)) or re.match(r'^\s*[0-9A-Z]{2}\s+[0-9A-Z]{6}\s*[\u4e00-\u9fff]',next_major))
        if parent:
            inherited={**inherited,'major':major,'major_quote':major_text};continue
        if not major and not inherited.get('major') and not get('exam'):continue
        if header['entry_method'] is not None:
            method=_exam_method(get('entry_method'))
            if method in {'single','recommendation'}:
                result['excluded_records'].append({'reason':'原文明确非普通统考范围','page':page['page'],'row':index});continue
        adjusted,record_header=row,header
        active_inherited={k:v for k,v in inherited.items() if k not in {'major','major_quote'}} if major else inherited
        if field_row:
            adjusted=[*row,major_text];record_header={**header,'major':None,'code':None,'name':None,'direction':len(row)}
        if mode and not get('mode'):
            record_header={**record_header,'mode':len(adjusted)};adjusted=[*adjusted,mode]
        records,expected,issues,excluded=_row_records(adjusted,record_header,unit,year,document,table_index,index,inherited=active_inherited)
        result['expected_records']+=expected;result['issues'].extend(issues);result['excluded_records'].extend(excluded)
        if re.search(r'任选一组|考试科目组合|方案[一二123]|科目组|成套选择|对应选择',get('exam')+' '+get('note')):
            result['issues'].append('跨考试单元组合尚未独立适配，未平铺为自由备选');continue
        for program in records:
            if publication.get('mode') and program['study_mode']!=publication['mode']:
                result['issues'].append('原文学习方式与官方整文件声明矛盾，未采用');continue
            refs=_mapped_refs(program['evidence'],[*values,*inherited.values()],raw_pages,document,page['page'],native_refs)
            exam=_mapped_refs(program['exam_evidence'],values,raw_pages,document,page['page'],native_refs)
            if refs is None or exam is None or not exam:
                result['issues'].append('PDF单元格与原文页无法逐字核对，未采用该方向');continue
            if mode_quote:
                mode_refs=_raw_quotes(mode_quote,raw_pages,document,page['page'])
                if mode_refs:refs+=mode_refs
            updated={**program,'catalog_parser':PARSER,'evidence':refs+publication.get('evidence',[])+publication.get('mode_evidence',[]),
                     'exam_evidence':exam,'source_locator':{'page':page['page'],'table':table_index,'row':index}}
            if degree and not updated.get('degree_type'):
                degree_refs=_raw_quotes(degree_quote,raw_pages,document,page['page'])
                if degree_refs:updated={**updated,'degree_type':degree,'evidence':[*updated['evidence'],*degree_refs]}
            if field_row:updated={**updated,'published_field_code':field_row[2],'parent_code_level':'discipline'}
            updated['subjects']=[{**s,'evidence':_mapped_refs(s.get('evidence'),values,raw_pages,document,page['page'],native_refs) or exam} for s in program['subjects']]
            result['programs'].append(updated)
        if records and major:inherited={**inherited,'major':major,'major_quote':major_text}
    result['continuation']={'header':header,'column_count':len(rows[0]),'inherited':inherited,'mode':mode,'page':page['page'],'left':table['bbox'][0],'right':table['bbox'][2],'degree':degree,'degree_quote':degree_quote}
    return result


def parse_catalog_pdf_national(layout,unit,year,document,*,project_root=PROJECT_ROOT):
    result={'programs':[],'expected_records':0,'issues':[],'retest_rules':[],'documents':[],
            'table_manifests':[],'catalog_format':PARSER,'excluded_records':[]}
    if year!=2027 or document.get('year')!=year or str(document.get('unit_id'))!=str(unit.get('id')):
        return {**result,'issues':['招生学校或2027原文年度未匹配']}
    publication=verify_catalog_publication(document,unit,year,project_root=project_root)
    issue=_scope_issue(layout,unit,year,publication)
    if issue:return {**result,'issues':[issue]}
    pages=layout.get('raw_text','').rstrip('\f\r\n ').split('\f')
    if len(pages)!=len(layout.get('pages',[])):
        return {**result,'issues':['原始文字与PDF网格页数不一致']}
    if publication.get('verified'):result['documents']=publication.get('documents',[])
    table_index=0;previous=None
    for page in layout.get('pages',[]):
        for table in page.get('tables',[]):
            table_index+=1
            parsed=_parse_table(table,page,unit,year,document,pages,publication,table_index,previous=previous)
            previous=parsed.get('continuation')
            result['programs'].extend(parsed['programs']);result['expected_records']+=parsed['expected_records']
            result['issues'].extend(f'第{page["page"]}页表{table_index}：'+text for text in parsed['issues'])
            result['excluded_records'].extend(parsed['excluded_records'])
            result['table_manifests'].append({'page':page['page'],'table':table_index,'physical_rows':len(table['rows']),
                'parsed_records':len(parsed['programs']),'expected_records':parsed['expected_records']})
    if not table_index:result['issues'].append('未找到可独立核对的真实PDF表格网格')
    if not result['programs'] and not result['issues']:result['issues'].append('已保存PDF表格，但当前表头、文字层或学习方式尚未适配')
    from .catalog_semantic_audit import _facts
    grouped=defaultdict(list)
    for program in result['programs']:grouped[program['id']].append(program)
    accepted=[]
    for group in grouped.values():
        keys={json.dumps(_facts({k:v for k,v in program.items() if k!='source_locator'}),ensure_ascii=False,sort_keys=True) for program in group}
        if len(keys)>1:
            result['issues'].append('多个物理记录具有相同方向身份但事实冲突，全部冲突记录暂不采用');continue
        program=group[0]
        if len(group)>1:
            program={**program,'duplicate_source_locators':[p['source_locator'] for p in group]}
            result['expected_records']-=len(group)-1
        accepted.append(program)
    result['programs']=accepted
    return result
