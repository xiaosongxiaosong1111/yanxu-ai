"""Incrementally parse saved national originals, without network calls or publication."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
from urllib.parse import unquote, urlsplit

from public_sources import official_source_url, rendered_source_html
from service_verification import SourceVerifier, plain_text
from .catalogs import compact
from .documents import PROJECT_ROOT, DocumentError, decode_text, extract_pdf_text


MAX_BYTES=32*1024*1024
STATES={'ready','needs_adapter','not_catalog','restricted','failure'}


def _encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode()


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def parser_revision():
    names=('catalogs.py','catalog_batch.py','catalog_html.py','catalog_office.py','catalog_enrichment.py',
           'catalog_pdf.py','catalog_pdf_tables.py','catalog_zsml.py','catalog_source_set.py','catalog_publication.py')
    paths=[Path(__file__).parent/name for name in names]
    paths += [Path(__file__),PROJECT_ROOT/'service_verification.py',PROJECT_ROOT/'public_sources.py']
    return hashlib.sha256(b''.join(path.read_bytes() for path in paths if path.is_file())).hexdigest()[:24]


def _atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_bytes(_encoded(value)+b'\n')
    temporary.replace(path)


def _read_original(document,root,limit=MAX_BYTES):
    value=document.get('local_path') or document.get('snapshot_path')
    if not isinstance(value,str) or not value:
        raise DocumentError('缺少已保存原始文件路径')
    path=(root/value).resolve()
    if not path.is_relative_to((root/'data/snapshots').resolve()):
        raise DocumentError('原文路径超出本项目来源快照目录')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=limit:
        raise DocumentError('原文不是有效普通文件或超过32MB上限')
    with path.open('rb') as stream: raw=stream.read(limit+1)
    if len(raw)>limit or hashlib.sha256(raw).hexdigest()!=document.get('sha256'):
        raise DocumentError('原文摘要不一致，未采用解析缓存')
    return path,raw


def _unit_candidates(unit,row,root,names):
    from .official_bindings import binding_candidates
    found={}
    for document in row.get('pages',[]):
        if urlsplit(document.get('url','')).hostname!='yz.chsi.com.cn': continue
        try:
            _,raw=_read_original(document,root,8*1024*1024)
            for candidate in binding_candidates(unit,document,raw,foreign_names=names):
                if candidate['status']=='declared_official_origin': found[candidate['url']]=candidate
        except (OSError,ValueError,TypeError):
            continue
    return list(found.values())


def _owned_unit(unit,url,bindings):
    if official_source_url(url,unit): return unit,None
    for binding in bindings:
        candidate={**unit,'official_url':binding['url']}
        if official_source_url(url,candidate): return candidate,binding
    return None,None


def _parent(document,pages,root,unit,year):
    from .catalog_publication import verify_catalog_publication
    parent=document.get('publication_page')
    if not isinstance(parent,dict):
        parent=next((page for page in pages if page.get('url')==document.get('source_url')),None)
    if not parent: return None,{'verified':False,'reason':'尚无可重放的发布父页'}
    candidate={**document,'publication_page':{**parent,'unit_id':str(unit['id'])},'unit_id':str(unit['id']),'year':year}
    proof=verify_catalog_publication(candidate,unit,year,project_root=root)
    return (candidate['publication_page'] if proof['verified'] else None),proof


def _has_table(html):
    text=plain_text(html)
    return bool(re.search(r'<table\b',html,re.I) and re.search(r'专业|学科|编码',text)
                and re.search(r'初试|考试科目|业务课|[①②③④]',text))


def _scope_status(document,headings=()):
    values=[document.get('title') or '',unquote(urlsplit(document['url']).path.rsplit('/',1)[-1]),*headings]
    for value in values:
        value=re.sub(r'不(?:含|招收|接收)(?:推荐免试|推免)(?:硕士研究生|研究生|生)?(?:招生计划|人数)?','',value)
        if not re.search(r'单独考试|单考|强军计划|港澳台|港澳臺|留学生|国际学生|推免|推荐免试|博士(?:研究生)?(?:招生|专业|目录)',value):
            continue
        mixed=bool(re.search(r'统考|全国统一考试|硕博|硕士.{0,6}博士|博士.{0,6}硕士',value))
        return ('needs_adapter' if mixed else 'not_catalog',
                '原始标题、文件名或发布链接明示混合招生范围，尚未分离统考行' if mixed else
                '原始标题、文件名或发布链接明示单考、推免、博士或境外招生范围，不纳入普通统考目录')
    return None,''


def _html_scope(document,html):
    headings=[plain_text(match) for match in re.findall(r'<(?:h1|title)\b[^>]*>(.*?)</(?:h1|title)>',html,re.I|re.S)]
    return _scope_status(document,headings)


def _adapter(raw,path,document,unit,root):
    status,reason=_scope_status(document)
    if status: return None,status,reason
    if raw.startswith(b'%PDF-'):
        from .catalog_pdf import sniff_catalog_pdf
        text=extract_pdf_text(path)
        status,reason=_scope_status(document,[line.strip() for line in text.splitlines()[:8]])
        if status: return None,status,reason
        if not re.search(r'初试|考试科目|[①②③④]',text): return None,'not_catalog','PDF未见招生初试目录列'
        if sniff_catalog_pdf(text): return 'pdf_row_v1',None,''
        if '考试方式' in text and '21全国统考' in text and unit['name']+str(document['year']) in compact(text):
            return 'nankai_raw_v1',None,''
        return 'pdf_table_v1',None,''
    if raw.startswith((b'PK',bytes.fromhex('d0cf11e0a1b11ae1'))):
        from .catalog_office import extract_office_html
        html=extract_office_html(raw)
        status,reason=_html_scope(document,html)
        if status: return None,status,reason
        return ('office_table_v1',None,'') if _has_table(html) else (None,'not_catalog','Office原文未见招生目录与初试列')
    html=rendered_source_html(decode_text(raw,document.get('content_type','')),document['url'])
    status,reason=_html_scope(document,html)
    if status: return None,status,reason
    if _has_table(html): return 'html_table_v1',None,''
    from .catalog_source_set import parse_catalog_index
    proof=parse_catalog_index(html,document['url'],unit,document['year'])
    if proof['groups']:
        return 'catalog_index_v1',None,''
    if re.search(r'招生.*(?:专业)?目录|(?:招生)?专业目录',plain_text(html)) and document.get('kind') in {'catalog','subjects'}:
        return None,'needs_adapter','目录为动态入口、图片或当前未适配布局，未推定目录为空'
    return None,'not_catalog','该份已保存原文未见可解析招生目录表格'


def parse_saved_document(document,unit,root):
    """Replay typed originals once, then verify every adopted row and its complete ORs."""
    from .catalog_source_set import parse_catalog_index
    verifier=SourceVerifier(root)
    docs={document['id']:document}
    adapter=document.get('catalog_parser')
    if adapter=='catalog_index_v1':
        raw=verifier.read(document)
        proof=parse_catalog_index(rendered_source_html(decode_text(raw[1],document.get('content_type','')),document['url']),document['url'],unit,document['year'])
        parsed={'programs':[],'expected_records':0,'issues':proof['issues'],'retest_rules':[]}
    else:
        parsed=verifier._source_programs(document,unit,document['year'],{},docs)
    if parsed is None:
        return {'status':'needs_adapter','reason':'原文年度、表头或招生范围未能独立重放','documents':[document],
                'programs':[],'expected_records':0,'issues':['未通过严格目录解析'],'retest_rules':[]}
    for additional in parsed.get('documents',[]): docs[additional['id']]=additional
    valid=[program for program in parsed['programs'] if verifier.program(program,docs,unit)]
    issues=[*parsed.get('issues',[])]
    if len(valid)!=len(parsed['programs']): issues.append('存在无法从原文独立重放的专业方向，未纳入ready entry')
    ready=bool(valid) or adapter=='catalog_index_v1' and not issues
    return {'status':'ready' if ready else 'needs_adapter','reason':'已逐条核对原文，学校全集另行对账' if ready else '已保存原文，尚无通过核验的目录行',
            'documents':list(docs.values()),'programs':valid,'expected_records':parsed.get('expected_records',0),
            'issues':issues,'retest_rules':parsed.get('retest_rules',[])}


def _cached(path,key):
    try:
        wrapped=json.loads(path.read_text('utf-8'))
        value=wrapped['value']
        if wrapped.get('key')==key and wrapped.get('sha256')==_digest(value) and value.get('status') in STATES:
            return value
    except (OSError,ValueError,KeyError,TypeError):
        pass
    return None


def _source_context(document):
    if not isinstance(document,dict): return None
    keys=('id','unit_id','url','sha256','local_path','year','content_type','title','source_url')
    return {key:document.get(key) for key in keys}


def _process(source,unit,pages,bindings,root,cache,version,refresh):
    base={key:source.get(key) for key in ('url','sha256','local_path','kind','year')}
    base={**base,'source_id':source.get('id'),'saved_original_valid':False,'cache_hit':False}
    if source.get('status') in {'restricted','access_restricted'}:
        return {**base,'status':'restricted','reason':'原采集记录为访问受限，未尝试联网或假造原文'}
    try:
        path,raw=_read_original(source,root)
        owned,binding=_owned_unit(unit,source.get('url'),bindings)
        if not owned: raise DocumentError('该来源尚无准确的本单位官方域名归属依据')
        document={**source,'id':'saved-catalog-'+str(unit['id'])+'-'+hashlib.sha256(source['url'].encode()).hexdigest()[:12]+'-'+source['sha256'][:12],
            'unit_id':str(unit['id']),'year':2027,'kind':'catalog','status':'parsed',
            'local_path':path.relative_to(root).as_posix()}
        parent,proof=_parent(document,pages,root,owned,2027)
        if parent: document={**document,'publication_page':parent}
        base={**base,'saved_original_valid':True,'publication_verified':proof['verified'],
              'publication_reason':proof['reason']}
        key=_digest({'source':_source_context(document),
                     'unit':{key:owned.get(key) for key in ('id','name','unit_code','official_url')},
                     'parser_revision':version,'parent':_source_context(parent),
                     'parent_verified':proof['verified'],'publication_reason':proof['reason'],'parser_dispatch':2})
        target=cache/'files'/(key+'.json')
        value=None if refresh else _cached(target,key)
        if value is not None:
            return {**value,'source_id':source.get('id'),'cache_hit':True}
        excluded_publication=proof['reason'] in {'发布页标题属于其他招生范围，不能当普通统考目录',
                                              '附件链接明示留学生、推免或单考等不同招生范围'}
        adapter,status,reason=(None,'not_catalog','已重放的发布原文明示其他招生范围：'+proof['reason']) if excluded_publication else _adapter(raw,path,document,owned,root)
        common={**base,'saved_original_valid':True,'cache_path':target.relative_to(root).as_posix(),
                'parser':adapter,'parser_revision':version,'binding':binding,'unit':owned,
                'publication_verified':proof['verified'],'publication_reason':proof['reason']}
        if status:
            value={**common,'status':status,'reason':reason,'programs':[],'documents':[],'issues':[],'expected_records':0}
        else:
            if adapter!='nankai_raw_v1': document={**document,'catalog_parser':adapter}
            value={**common,**parse_saved_document(document,owned,root)}
        _atomic(target,{'key':key,'sha256':_digest(value),'value':value})
        return value
    except (OSError,ValueError,TypeError,KeyError,ImportError) as error:
        return {**base,'status':'needs_adapter' if isinstance(error,ImportError) else 'failure',
                'reason':str(error) if isinstance(error,DocumentError) else '原文或解析器不可用：'+type(error).__name__}


def _school(unit,row,files,root,entry_path):
    ready=[file for file in files if file.get('status')=='ready']
    documents={doc['id']:doc for file in ready for doc in file.get('documents',[])}
    programs={}
    conflicts=[]
    for file in ready:
        for program in file.get('programs',[]):
            old=programs.get(program['id'])
            if old and SourceVerifier._signature(old)!=SourceVerifier._signature(program):
                conflicts.append(program['id'])
            else: programs.setdefault(program['id'],program)
    scopes=[file.get('unit',unit) for file in ready]
    owned=scopes[0] if scopes else unit
    all_ready=all(file['status'] in {'ready','not_catalog'} for file in files)
    # A saved title alone cannot establish the nationwide scheduler's school-source census.
    explicit_set=any(doc.get('catalog_parser') in {'catalog_index_v1','zsml_tree'} for doc in documents.values())
    verified=bool(programs and explicit_set and all_ready and not conflicts and row.get('status') in {'collected','sources_collected'}
        and SourceVerifier(root).unit_reconciles(owned,2027,list(programs.values()),documents))
    status='verified' if verified else 'needs_review' if conflicts else 'partial' if programs else 'not_collected'
    entry={'programs':list(programs.values()),'documents':list(documents.values()),
        'expected_records':len(programs) if verified else sum(file.get('expected_records',0) for file in ready),
        'issues':[issue for file in ready for issue in file.get('issues',[])]+(['同一目录身份存在冲突原文版本'] if conflicts else []),
        'retest_rules':[rule for file in ready for rule in file.get('retest_rules',[])],
        'status':{'unit_id':unit['id'],'unit_name':unit['name'],'year':2027,'status':status,
                  'expected_records':len(programs) if verified else None,'parsed_records':len(programs),
                  'verified_records':len(programs) if verified else 0,
                  'notes':['离线解析不修改招生资料；ready记录仍须经独立ingest核验。','已解析方向不等于资格通过或全校目录齐全。']}}
    _atomic(entry_path,entry)
    public_files=[{key:value for key,value in file.items() if key not in {'programs','documents','retest_rules','unit'}} for file in files]
    return {'unit_id':unit['id'],'unit_name':unit['name'],'year':2027,'catalog_status':status,
            'program_count':len(programs),'source_count':sum(file.get('saved_original_valid') is True for file in files),
            'files':public_files,'ready_entry_path':entry_path.relative_to(root).as_posix(),'conflicting_program_ids':conflicts}


def _report(rows,total,selected,version,processed,hits,limited,complete):
    return {'metadata':{'schema_version':1,'reference_year':2027,'target_year':2028,'parser_revision':version,
        'checked_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'index_units':total,'selected_units':selected,
        'processed_units':len(rows),'pending_units':selected-len(rows),'traversal_complete':complete,
        'processed_files':processed,'cache_hits':hits,'limited':limited,
        'units_with_sources':sum(row['source_count']>0 for row in rows),'units_with_programs':sum(row['program_count']>0 for row in rows),
        'catalog_verified_units':sum(row['catalog_status']=='verified' for row in rows),'program_count':sum(row['program_count'] for row in rows),
        'catalog_complete':False,'notes':['仅解析已保存原文，无联网，无招生库写入。','分母为通用院校索引；完整招生单位名单与专业目录分别验收。']},'units':rows}


def collect_saved(registry,national,output,*,project_root=PROJECT_ROOT,cache_dir=None,unit_ids=None,max_files=None,refresh=False,progress=None):
    root=Path(project_root).resolve()
    output=Path(output).resolve()
    protected={root/'data'/name for name in ('admissions.json','units.json','history.json','program_enrichment.json','national_sources.json')}
    cache=Path(cache_dir or root/'data/runtime/national_catalog').resolve()
    if output in protected or not output.is_relative_to(root/'data') or not cache.is_relative_to(root/'data/runtime'):
        raise DocumentError('离线调度器只写自身报告及runtime缓存，不能覆盖原始招生数据')
    units={str(unit['id']):unit for unit in registry.get('units',[])}
    if unit_ids is not None and (not isinstance(unit_ids,list) or any(value not in units for value in unit_ids)):
        raise DocumentError('筛选学校必须来自现有招生单位索引')
    if max_files is not None and (type(max_files) is not int or max_files<1): raise DocumentError('文件上限必须为正整数')
    source_units={str(row['unit_id']):row for row in national.get('units',[])}
    version=parser_revision()
    rows,processed,hits,limited=[],0,0,False
    names=[unit['name'] for unit in units.values()]
    selected=[identity for identity in units if unit_ids is None or identity in unit_ids]
    for identity,unit in units.items():
        if unit_ids is not None and identity not in unit_ids: continue
        if max_files is not None and processed>=max_files:
            limited=True
            break
        row=source_units.get(identity,{})
        sources=list({(doc.get('id'),doc.get('url'),doc.get('sha256'),doc.get('status')):doc for doc in row.get('documents',[]) if isinstance(doc,dict)}.values())
        bindings=_unit_candidates(unit,row,root,names) if sources and not unit.get('official_url') else []
        files=[]
        for source in sources:
            if max_files is not None and processed>=max_files:
                limited=True
                break
            files.append(_process(source,unit,row.get('pages',[]),bindings,root,cache,version,refresh))
            processed+=1
            hits+=files[-1].get('cache_hit') is True
        school=_school(unit,row,files,root,cache/'units'/(identity+'-2027.json'))
        rows.append(school)
        _atomic(output,_report(rows,len(units),len(selected),version,processed,hits,limited,False))
        if progress: progress({key:school[key] for key in ('unit_id','unit_name','program_count','catalog_status')})
    report=_report(rows,len(units),len(selected),version,processed,hits,limited,not limited)
    _atomic(output,report)
    return report


def main(argv=None):
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--root',type=Path,default=PROJECT_ROOT)
    cli.add_argument('--units',type=Path,default=PROJECT_ROOT/'data/units.json')
    cli.add_argument('--sources',type=Path,default=PROJECT_ROOT/'data/national_sources.json')
    cli.add_argument('--output',type=Path,default=PROJECT_ROOT/'data/national_catalog.json')
    cli.add_argument('--cache-dir',type=Path)
    cli.add_argument('--unit-id',action='append')
    cli.add_argument('--max-files',type=int)
    cli.add_argument('--refresh',action='store_true')
    args=cli.parse_args(argv)
    report=collect_saved(json.loads(args.units.read_text('utf-8-sig')),json.loads(args.sources.read_text('utf-8-sig')),
        args.output,project_root=args.root,cache_dir=args.cache_dir,unit_ids=args.unit_id,max_files=args.max_files,refresh=args.refresh,
        progress=lambda value:print(json.dumps(value,ensure_ascii=False),flush=True))
    print(json.dumps(report['metadata'],ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__': raise SystemExit(main())
