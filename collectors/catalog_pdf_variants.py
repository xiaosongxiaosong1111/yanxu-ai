"""Versioned PDF cell parser: explicit column bounds precede text recognition.

This adapter intentionally leaves pdf_table_v1 and its citations unchanged.
"""
from pathlib import Path
import re

from .catalog_html import _subjects
from .catalog_publication import verify_catalog_publication
from .catalog_pdf_tables import (
    _cell, _cell_lines, _clean, _has_title, _initial_rows, _mode, _pdf_grid,
    _references, _select, _source_conflict, extract_pdf_layout,
)
from .catalogs import _program, compact, evidence
from .documents import PROJECT_ROOT


PARSER = 'pdf_table_v2'
_CODE = r'\d{4}(?:[0-9A-Z]{2})?'
_PREFIX = re.compile(r'^(' + _CODE + r')(?![0-9A-Z])\s*(.*)$')
_POSTFIX = re.compile(r'^(?:\d+[.．、])?(.+?)[（(](' + _CODE + r')[）)]$')
_DIRECTION = re.compile(r'^[0-9A-Z]{2}(?![0-9A-Z])\s*[.．、]?\s*[“‘（(]?[^\d\W_]', re.UNICODE)
_CHAPTER = re.compile(r'^[一二三四五六七八九十]+[、．.](?:非全日制|全日制).*(?:招生专业|招生学科).*(?:目录|领域)')


def extract_pdf_variant_layout(path):
    """Keep the legacy raw pages, adding SVG cells for numeric-slot tables."""
    layout = extract_pdf_layout(path)
    import os
    import shutil
    local = PROJECT_ROOT / 'tools/poppler-26.07.0' / ('pdftotext.exe' if os.name == 'nt' else 'pdftotext')
    executable = os.environ.get('GRAD_PDFTOTEXT') or (str(local) if local.is_file() else shutil.which('pdftotext'))
    cairo = Path(executable).with_name('pdftocairo.exe' if os.name == 'nt' else 'pdftocairo') if executable else None
    pages = []
    for page in layout['pages']:
        numeric = any(re.search(r'^[1-4][.．]\s*[（(][1-9]\d{2}[）)]', w['text']) for w in page['words'])
        new_header = any(_clean(l['text']) == '专业、研究方向' for l in page['lines'])
        grid = _pdf_grid(cairo, path, page['page']) if not page.get('grid') and cairo and cairo.is_file() and (numeric or new_header) else page.get('grid', [])
        pages.append({**page, 'grid': grid})
    return {**layout, 'pages': pages}


def _major(text):
    text = _clean(text).lstrip('★☆*＊')
    if re.match(r'^20\d{2}年',text):
        return None
    postfix = _POSTFIX.fullmatch(text)
    if postfix:
        return postfix[2], postfix[1]
    match = _PREFIX.fullmatch(text)
    if not match:
        return None
    name = match[2]
    # A telephone area code or an alphanumeric college label is not a major.
    if name and not re.match(r'^[\u4e00-\u9fff（(]', name):
        return None
    return match[1], name


def _header_at(page, initial):
    near = [l for l in page['lines'] if abs(l['y0']-initial['y0']) < 30]
    near += [{**w,'words':[w]} for l in near for w in l['words']]
    major = next((l for l in near if re.search(r'专业代码|学科.*代码|专业名称|^专业$|^专业、研究方向$', _clean(l['text']))), None)
    if not major:
        return None
    box = lambda row: _cell(page, (row['x0']+row['x1'])/2, (row['y0']+row['y1'])/2)
    major_box, initial_box = box(major), box(initial)
    if not major_box or not initial_box or major_box[2] > initial_box[0]+1:
        return None
    direction = next((l for l in near if _clean(l['text']) == '研究方向'), None)
    direction_box = box(direction) if direction else None
    department = next((l for l in near if l['x0'] < major_box[0] and re.search(r'院系所|学院|培养学院', _clean(l['text']))), None)
    department_box = box(department) if department else None
    mode = next((l for l in near if re.search(r'^学习(?:方式|形式)$', _clean(l['text']))), None)
    mode_box = box(mode) if mode else None
    # Some tables put the three header labels code/name/mode in ONE column.
    if mode_box and mode_box[0] < major_box[2]-.8:
        mode_box = None
    counts = [box(l) for l in near if re.search(r'拟招|总人数|总计划', _clean(l['text']))]
    return {'top': min(major_box[1], initial_box[1]), 'y': max(major_box[3], initial_box[3]),
            'major': major_box[:], 'initial': initial_box[:],
            'direction': direction_box[: ] if direction_box else None,
            'department': department_box[: ] if department_box else None,
            'mode': mode_box[: ] if mode_box else None,
            'count': next((b for b in counts if b and major_box[2] <= b[0] < initial_box[0]), None),
            'page': page['page']}


def _bands(layout):
    bands, previous = [], None
    for page in layout['pages']:
        initials = sorted([l for l in page['lines'] if _clean(l['text']) in {'考试科目','初试科目'}], key=lambda l:l['y0'])
        headers = [h for line in initials if (h := _header_at(page, line))]
        unique = {(round(h['top'],1), round(h['initial'][0],1)):h for h in headers}
        headers = sorted(unique.values(), key=lambda h:h['top'])
        if previous and page.get('grid') and (not headers or headers[0]['top'] > 180):
            bands.append({'page':page, 'header':previous, 'top':0, 'bottom':headers[0]['top'] if headers else page['height']-20})
        for index, header in enumerate(headers):
            bands.append({'page':page, 'header':header, 'top':header['y'],
                          'bottom':headers[index+1]['top'] if index+1 < len(headers) else page['height']-20})
        if headers:
            previous = headers[-1]
    return bands


def _section_department(text):
    match = re.match(r'^(?:招生学院[:：]|院系所代码及名称[:：])?(\d{3})(?!\d)\s*([^\d].*)$', _clean(text))
    if not match or not re.search(r'学院|学部|学系|中心|研究所|实验室|研究院', match[2]):
        return None
    return match[1] + re.split(r'联系人|联系电话|拟招生|[（(]联系电话',match[2])[0]


def _major_cell(page, line, header):
    middle = (header['major'][0]+header['major'][2])/2
    return _cell(page, middle, (line['y0']+line['y1'])/2)


def _regions(layout, bands):
    regions, current, department, chapter = [], None, None, None
    contexts = []
    for page in layout['pages']:
        for line in sorted(page['lines'],key=lambda l:(l['y0'],l['x0'])):
            if _CHAPTER.match(_clean(line['text'])) and len(_mode(_clean(line['text']))) == 1:
                contexts.append((page['page'], line['y0'], {'mode':_mode(_clean(line['text']))[0], 'rows':[(page['page'],line)]}))
    for band in bands:
        page, header = band['page'], band['header']
        edges=[g[3] for g in page.get('grid',[]) if abs(g[0]-g[2])<.8 and
               any(abs(g[0]-x)<1 for x in header['major'][::2]) and g[3]>band['top']]
        bottom=min(band['bottom'],max(edges,default=band['bottom']))
        body = sorted([l for l in page['lines'] if band['top'] <= l['y0'] < bottom],key=lambda l:(l['y0'],l['x0']))
        majors = []
        for line in body:
            selected = _select(line, header['major'][0], header['major'][2])
            match = _major(selected['text']) if selected else None
            if match:
                cell = _major_cell(page,selected,header)
                if cell:
                    majors.append((selected,match,cell))
        page_regions = []
        for line, (code,name), cell in majors:
            prior_contexts = [ctx for pn,y,ctx in contexts if (pn,y) <= (page['page'], cell[1]+1)]
            chapter = prior_contexts[-1] if prior_contexts else None
            sections = [(l,_section_department(l['text'])) for l in page['lines'] if l['y0'] <= cell[1]+1 and l['x0'] < header['initial'][0]]
            sections = [(l,n) for l,n in sections if n and (not header['department'] or l['x1'] > header['department'][2]+15)]
            if sections:
                l,n = max(sections,key=lambda x:x[0]['y0']);department={'name':n,'rows':[(page['page'],l)]}
            region = {'page':page['page'],'top':cell[1],'marker_y':line['y0'],'code':code,'name':name,
                      'name_rows':[(page['page'],line)],'header':header,'identity':[],'scope_rows':[],
                      'department':department,'chapter':chapter,'major_cell':cell,
                      'name_cell_rows':_cell_lines(page,cell)}
            cell_rows=region['name_cell_rows']
            full_postfix=_POSTFIX.fullmatch(_clean(''.join(l['text'] for _,l in cell_rows)))
            if full_postfix and full_postfix[2]==code:
                region={**region,'name':full_postfix[1],'name_rows':cell_rows,'name_prejoined':True}
            regions.append(region);page_regions.append(region)
        prior = current
        for line in body:
            selected = _select(line,header['major'][0],(header['direction'] or header['major'])[2])
            current = next((r for r in reversed(page_regions) if r['top'] <= line['y0']+.5),prior)
            if current and selected:
                current['identity'].append((page['page'],selected,header))
            if current:
                current['scope_rows'].append((page['page'],line))
    return regions


def _raw_departments(layout,bands,regions):
    """Resolve split/continued college cells by the PDF's original text order.

    A continued merged college cell may have no bottom border on this page.
    Only labels physically inside the verified college COLUMN are candidates;
    each complete label must also occur continuously in the original raw page.
    """
    raw_pages=layout.get('raw_text','').rstrip('\f\r\n ').split('\f')
    labels=[]
    for band in bands:
        page,header=band['page'],band['header'];bound=header['department']
        if not bound:continue
        raw=compact(raw_pages[page['page']-1])
        rows=[]
        for line in page['lines']:
            selected=_select(line,bound[0],bound[2])
            if selected and compact(selected['text']) in raw:
                rows.append((raw.find(compact(selected['text'])),selected))
        rows=sorted({(round(line['y0'],2),compact(line['text'])):(pos,line) for pos,line in rows}.values(),key=lambda x:x[1]['y0'])
        for index,(position,line) in enumerate(rows):
            value=_clean(line['text'])
            if not re.match(r'^\d{3}(?!\d)',value):continue
            parts=[value];refs=[(page['page'],line)]
            for _,next_line in rows[index+1:]:
                nxt=_clean(next_line['text'])
                if re.search(r'\d|老师|联系|代码|名称|院系所|[:：]',nxt):break
                parts.append(nxt);refs.append((page['page'],next_line))
                if re.search(r'学院|学部|学系|中心|研究所|研究院|实验室',''.join(parts)):break
            name=''.join(parts)
            if re.fullmatch(r'\d{3}[^\d]{2,80}',name) and re.search(r'学院|学部|学系|中心|研究所|研究院|实验室',name) and compact(name) in raw:
                labels.append((page['page'],raw.find(compact(name)),{'name':name,'rows':refs}))
    labels=sorted(labels,key=lambda item:(item[0],item[1]))
    enriched=[]
    for region in regions:
        pn,line=region['name_rows'][0];needle=compact(line['text']);raw=compact(raw_pages[pn-1])
        positions=[m.start() for m in re.finditer(re.escape(needle),raw)]
        preceding=[d for page,pos,d in labels if len(positions)==1 and (page,pos)<=(pn,positions[0])]
        enriched.append({**region,'raw_department':preceding[-1] if preceding else None})
    return enriched


def _direction_targets(region):
    targets, name_rows = [], list(region['name_rows'])
    text_parts = [region['name']] if region['name'] else []
    for pn,line in ([] if region.get('name_prejoined') else region['name_cell_rows']):
        value=_clean(line['text'])
        if _DIRECTION.match(value):break
        if not value or _major(value) or _mode(value):continue
        value=re.sub(r'[（(](?:学术学位|专业学位)[）)]','',value)
        if not value or re.fullmatch(r'\d+',value):continue
        text_parts.append(value);name_rows.append((pn,line))
    last_page = None
    for pn,line,header in region['identity']:
        direction_box = header['direction']
        if not direction_box and (pn,line['y0'],line['x0']) == (name_rows[0][0],name_rows[0][1]['y0'],name_rows[0][1]['x0']):
            continue
        row = _select(line, direction_box[0],direction_box[2]) if direction_box else line
        value = _clean(row['text']) if row else ''
        if row and _DIRECTION.match(value):
            marks=list(re.finditer(r'(?:^|\s)(\d{2})(?!\d)\s*[.．、]?\s*(?=[\u4e00-\u9fffA-Za-z])',row['text']))
            parts=[row['text'][mark.start():marks[i+1].start() if i+1<len(marks) else len(row['text'])] for i,mark in enumerate(marks)] if len(marks)>1 else [value]
            targets.extend({'text':re.sub(r'^([0-9A-Z]{2})[.．、]',r'\1',_clean(part)),'rows':[(pn,row)],'anchor':(pn,row,header)} for part in parts)
        elif row and targets and value and not _major(value) and not _section_department(value) and not re.search(r'学术学位|专业学位|招生学院|备注|^第\d+页|^\d+$|^包括',value):
            targets[-1]['text'] += value; targets[-1]['rows'].append((pn,row))
        last_page = pn
    topics = []
    zero = [t for t in targets if t['text'].startswith('00不区分研究方向')]
    includes = any('包括' in _clean(l['text']) for _,l,_ in region['identity'])
    if len(zero)==1 and includes:
        topics = [t['text'] for t in targets if t is not zero[0]]
        targets = zero
    if not targets:
        targets=[{'text':'','rows':[],'anchor':(region['page'],region['name_rows'][0][1],region['header'])}]
    name = ''.join(text_parts)
    degree = '专业学位' if '专业学位' in name else '学术学位' if '学术学位' in name else None
    name=re.sub(r'[（(](?:专业学位|学术学位|全日制|非全日制)[）)]','',name)
    return name, degree, targets, topics, name_rows


def _column_rows(page, bound, y):
    if not bound:
        return []
    cell = _cell(page,(bound[0]+bound[2])/2,y)
    return _cell_lines(page,cell) if cell else []


def _department(region, page, header, y):
    if not header['department']:
        return region['department'], []
    rows = _column_rows(page,header['department'],y)
    parts=[];selected=[]
    for pn,line in rows:
        value=_clean(line['text'])
        if re.match(r'^\d{3}(?!\d)',value):
            parts=[value];selected=[(pn,line)]
        elif parts and not re.search(r'\d{4}|老师|联系|[:：]|学术学位|专业学位',value):
            parts.append(value);selected.append((pn,line))
    name=''.join(parts)
    if not re.fullmatch(r'\d{3}[^\d]{2,80}',name) or not re.search(r'学院|学部|学系|中心|研究所|研究院|实验室',name):
        if region.get('raw_department'):
            return region['raw_department'],[]
        return None,['学院单元格未能独立重建，不能降为全校范围']
    if region.get('raw_department') and region['raw_department']['name']!=name:
        return None,['学院网格与原文顺序有冲突，不能选择其中一个']
    return {'name':name,'rows':selected},[]


def _subject_text(text):
    text = _clean(text)
    text = re.sub(r'(?<!\d)([1-4])[.．][（(]([1-9]\d{2})[）)]',lambda m:'①②③④'[int(m[1])-1]+m[2],text)
    return re.sub(r'(?<=[或者])[（(]([1-9]\d{2})[）)]',r'\1',text)


def _exam(region,target,pages,document,raw_pages,unit_id):
    pn,line,header=target['anchor'];page=pages[pn]
    rows=_initial_rows(_column_rows(page,header['initial'],(line['y0']+line['y1'])/2))
    if not rows:
        return [],[],['初试单元格缺少真实网格，不能按相邻文字猜测']
    text=_subject_text(''.join(l['text'] for _,l in rows))
    if re.search(r'任选一组|考试科目组合|方案[一二123]|科目组[一二123]|仅招收推免|单独考试',text):
        return [],[],['跨单元成套组合或特殊招生范围需独立适配']
    subjects,_,issues=_subjects(text,unit_id,document)
    refs,missing=_references(rows,document,raw_pages)
    if missing:
        issues.append('初试原文坐标与原PDF页不能逐字对照')
    subjects=[{**s,'evidence':evidence(document,s['code']+s['name'] if compact(s['code']+s['name']) in compact(raw_pages[pn-1]) else s['code'],pn)} for s in subjects]
    return subjects,refs,issues


def _unlabeled_exam_branches(region,pages,unit_id,document):
    page=pages[region['page']];bound=region['header']['initial'];major_box=region['major_cell']
    boxes={tuple(box) for word in page['words'] if bound[0] <= word['x0'] < bound[2]
           and major_box[1] < word['y0'] < major_box[3]
           and (box:=_cell(page,(bound[0]+bound[2])/2,(word['y0']+word['y1'])/2))}
    sets=set()
    invalid=False
    for box in boxes:
        rows=_initial_rows(_cell_lines(page,box));text=_subject_text(''.join(l['text'] for _,l in rows))
        subjects,_,issues=_subjects(text,unit_id,document)
        if re.search(r'[①②③④]\d{3}',text) and issues:
            invalid=True
        if subjects and not issues:
            sets.add(tuple((s['exam_unit'],s['code'],compact(s['name'])) for s in subjects))
    return len(sets)>1 or invalid


def _parse_region(region,pages,unit,year,document,raw_pages,publication):
    name,degree,targets,topics,name_rows=_direction_targets(region)
    programs=[];issues=[];expected=0
    scope_text=_clean(''.join(line['text'] for _,line in region['scope_rows']))
    special=bool(re.search(r'任选一组|考试科目组合|方案[一二123]|科目组[一二123]|仅招收推免|只招收推免|单独考试',scope_text))
    if (topics or all(not t['text'] for t in targets)) and _unlabeled_exam_branches(region,pages,unit['id'],document):
        return [],len(targets),['同一专业单元格覆盖多套未命名初试分支，不能仅按专业名中心位置选择一套']
    identity_refs,missing=_references(name_rows,document,raw_pages)
    if missing or not name:
        return [],len(targets),['专业名称原文未能逐字核对']
    for target in targets:
        pn,line,header=target['anchor'];page=pages[pn];y=(line['y0']+line['y1'])/2
        dept,errors=_department(region,page,header,y)
        mode_rows=_column_rows(page,header['mode'],y)
        # In code/name/mode combined cells, use only the actual major cell.
        if not mode_rows:
            mode_rows=_column_rows(page,header['major'],y)
        modes=_mode(_clean(''.join(l['text'] for _,l in mode_rows)))
        if not modes and header['count']:
            mode_rows=_column_rows(page,header['count'],y)
            modes=_mode(_clean(''.join(l['text'] for _,l in mode_rows)))
        if not modes and region['chapter']:
            modes=[region['chapter']['mode']];mode_rows=region['chapter']['rows']
        if not modes and publication['mode']:
            modes=[publication['mode']]
        expected+=max(1,len(modes))
        if special:
            errors.append('跨单元成套组合或特殊招生范围需独立适配')
        if publication['mode'] and modes and set(modes)!={publication['mode']}:
            errors.append('明确整文件学习方式与专业行相矛盾')
        if not modes:
            errors.append('原文未明确该专业方向的学习方式')
        subjects,exam_refs,exam_errors=_exam(region,target,pages,document,raw_pages,unit['id'])
        errors.extend(exam_errors)
        extra_rows=[*target['rows'],*((dept or {}).get('rows',[])),*mode_rows]
        refs,miss=_references(extra_rows,document,raw_pages)
        if miss:
            errors.append('身份或学习方式原文无法逐字定位')
        if errors:
            issues.extend(target['text']+'：'+e for e in errors);continue
        for mode in modes:
            p=_program(unit,year,(dept or {}).get('name',''),region['code'],name,target['text'],mode,subjects,'',document,pn,degree=degree)
            p={**p,'catalog_parser':PARSER,'evidence':[*identity_refs,*refs,*publication['evidence'],*publication['mode_evidence']],
               'exam_evidence':exam_refs,'source_locator':{'page':pn,'top':region['top'],'code':region['code']}}
            if not dept:p['department_scope']='unit'
            if not target['text'] or topics:p['direction_scope']='program'
            if topics:p['research_topics']=topics
            if len(region['code'])==4:p['code_level']='discipline'
            programs.append(p)
    return programs,expected,issues


def parse_catalog_pdf_variants(layout,unit,year,document,*,project_root=PROJECT_ROOT):
    result={'programs':[],'expected_records':0,'issues':[],'retest_rules':[],'documents':[],
            'table_manifests':[],'catalog_format':PARSER}
    if year!=2027 or document.get('year')!=year or str(document.get('unit_id'))!=str(unit.get('id')):
        return {**result,'issues':['招生学校或2027原文年度未匹配']}
    publication=verify_catalog_publication(document,unit,year,project_root=project_root)
    conflict=_source_conflict(layout,unit,year)
    if conflict or (not _has_title(layout,unit,year) and not publication['verified']):
        return {**result,'issues':[conflict or '原PDF及官方父页未能独立证明学校年度']}
    if publication['mode_conflict']:
        return {**result,'issues':['官方父页的整文件学习方式相互矛盾']}
    result['documents']=publication['documents'] if publication['verified'] else []
    bands=_bands(layout);regions=_raw_departments(layout,bands,_regions(layout,bands))
    result['table_manifests']=[{'page':b['page']['page'],'top':b['top'],'bottom':b['bottom'],'major_x':b['header']['major'][:2],
                                'initial_x':b['header']['initial'][::2]} for b in bands]
    if not bands:
        return {**result,'issues':['未发现可独立核对专业列和初试列的真实PDF网格']}
    pages={p['page']:p for p in layout['pages']};raw_pages=layout.get('raw_text','').rstrip('\f\r\n ').split('\f')
    if len(raw_pages)!=len(layout['pages']):result['issues'].append('原文与坐标页数不一致')
    for region in regions:
        programs,expected,issues=_parse_region(region,pages,unit,year,document,raw_pages,publication)
        result['programs'].extend(programs);result['expected_records']+=expected
        result['issues'].extend(f'{region["code"]}第{region["page"]}页：'+i for i in issues)
    consumed={(r['page'],round(r['marker_y'],2),r['code']) for r in regions}
    for page in layout['pages']:
        exam_markers=any(re.search(r'[①②③④]\s*\d{3}|^[1-4][.．]\s*[（(]\d{3}',l['text']) for l in page['lines'])
        direction_markers=[l for l in page['lines'] if _DIRECTION.match(_clean(l['text']))]
        if exam_markers and direction_markers and not any(b['page']['page']==page['page'] for b in bands):
            result['issues'].append(f'第{page["page"]}页为含方向和初试的继续页，缺少可核对表格边界，不能忽略')
            result['expected_records']+=len(direction_markers)
        for line in page['lines']:
            marker=_major(line['text'])
            exam=any(re.search(r'[①②③④]\s*\d{3}|^[1-4][.．]\s*[（(]\d{3}',l['text']) for l in page['lines'])
            if marker and exam and (page['page'],round(line['y0'],2),marker[0]) not in consumed:
                # A marker in a note/tutor column is not counted as an exam identity.
                relevant=[b for b in bands if b['page']['page']==page['page'] and b['top']<=line['y0']<b['bottom']]
                if not relevant or any(b['header']['major'][0] <= line['x0'] < b['header']['major'][2] for b in relevant):
                    result['expected_records']+=1;result['issues'].append(f'第{page["page"]}页专业标记{marker[0]}未消费，不能认定整校完整')
    if not regions:result['issues'].append('目录网格已识别但专业身份尚未适配')
    if len({p['id'] for p in result['programs']})!=len(result['programs']):
        result['issues'].append('专业方向身份重复，需保留冲突而非覆盖')
    return result
