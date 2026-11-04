"""Scoped segment tools and current-source refresh for a bounded research turn."""
from copy import deepcopy
from itertools import zip_longest
import re
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

from research_store import ResearchError

SOURCE_FIELDS = ('url','title','unit_id','unit_name','year','requested_year','status','sha256',
                 'checked_at','original_available','field_verification','text_origin','text_truncated',
                 'snapshot_status','validity','usable','extraction','source_group','index_state')
SEGMENT_FIELDS = ('id','entry_id','extraction_id','generation_id','ordinal','kind','text',
                  'char_start','char_end','page_start','page_end','section_path','context_spans',
                  'row_complete','continues_previous','continues_next','content_truncated','full_char_end','match_mode')
CURRENT_NOTICE = '部分知识来源的状态或版本已变化；旧引文已停止复用，请补查当前有效原文。'
DISTRIBUTED_NOTICE = '关键词在同份原文分散，片段须结合上下文核对，不能据此合并事实。'


def _mark_unavailable(tools, source_id, source):
    identifier=source.get('knowledge_id')
    if identifier in tools._trusted_knowledge_ids:
        tools._knowledge_invalidated.add(identifier)
    tools.sources[source_id]={**source,'excerpt':'','knowledge_current':False,'usable':False,
                              'cache_hit':False,'notice':CURRENT_NOTICE}
    if CURRENT_NOTICE not in tools.gaps:tools.gaps.append(CURRENT_NOTICE)


def _refresh_one(tools, source_id, source, record, reusable):
    old=source.get('extraction') or {}; current=record.get('extraction') or {}
    changed=any(source.get(key)!=record.get(key) for key in ('sha256','year','unit_id'))
    changed=changed or bool(old.get('generation_id') and old.get('generation_id')!=current.get('generation_id'))
    usable=reusable(record) and record.get('id')==source.get('knowledge_id')
    value={**source,**{key:deepcopy(record[key]) for key in SOURCE_FIELDS if key in record},
           'knowledge_quality':deepcopy(record.get('quality')),'knowledge_current':usable}
    if changed and source.get('knowledge_id') in tools._trusted_knowledge_ids:
        tools._knowledge_invalidated.add(source['knowledge_id']);value['segments']=[]
        if CURRENT_NOTICE not in tools.gaps:tools.gaps.append(CURRENT_NOTICE)
    if not usable:
        value={**value,'excerpt':'','usable':False,'cache_hit':False}
        if source.get('knowledge_id') in tools._trusted_knowledge_ids:_mark_unavailable(tools,source_id,value)
        else:tools.sources[source_id]=value
        return
    if usable:
        value['excerpt']=str(record.get('excerpt') or '')[:1800]
        tools._trusted_knowledge_ids.add(record['id'])
    tools.sources[source_id]=value


def refresh_sources(tools, reusable):
    linked=[(sid,source) for sid,source in tools.sources.items() if source.get('knowledge_id')]
    if not linked:return bool(tools._knowledge_invalidated)
    getter=getattr(tools._knowledge(),'get',None)
    if not callable(getter):return bool(tools._knowledge_invalidated)
    current={}
    for source_id,source in linked:
        identifier=source['knowledge_id']
        try:
            if identifier not in current:current[identifier]=getter(identifier)
            record=current[identifier]
            if not isinstance(record,dict):raise ValueError('invalid metadata')
            _refresh_one(tools,source_id,source,record,reusable)
        except Exception:_mark_unavailable(tools,source_id,source)
    return bool(tools._knowledge_invalidated)


def _saved_metadata(getter, cache, identifier):
    if identifier not in cache:
        try:cache[identifier]=getter(identifier)
        except Exception:cache[identifier]=None
    return cache[identifier]


def _refresh_saved_report(report, getter, reusable, cache):
    sources={str(index):deepcopy(source) for index,source in enumerate(report.get('evidence',[])) if isinstance(source,dict)}
    trusted={source['knowledge_id'] for source in sources.values() if source.get('knowledge_id') and
             (source.get('knowledge_quality') or source.get('quality') or {}).get('state') not in {'pending','excluded'}}
    store=SimpleNamespace(get=lambda identifier:_saved_metadata(getter,cache,identifier))
    ledger=SimpleNamespace(sources=sources,gaps=list(report.get('gaps',[])),_trusted_knowledge_ids=trusted,
                           _knowledge_invalidated=set(),_knowledge=lambda:store)
    invalidated=refresh_sources(ledger,reusable)
    current={**deepcopy(report),'evidence':list(ledger.sources.values()),'gaps':ledger.gaps}
    if invalidated:
        current.update(assistant_summary=CURRENT_NOTICE,knowledge={**current.get('knowledge',{}),
                       'invalidated_count':max(len(ledger._knowledge_invalidated),current.get('knowledge',{}).get('invalidated_count',0))})
    return current,invalidated


def refresh_saved_task(task, getter, reusable, cache=None):
    """Recheck public history without rewriting the immutable persisted audit."""
    result=deepcopy(task);cache={} if cache is None else cache
    for key in ('report','previous_report'):
        report=result.get(key)
        if not isinstance(report,dict) or not isinstance(report.get('evidence'),list):continue
        result[key],changed=_refresh_saved_report(report,getter,reusable,cache)
        if changed and key=='report' and result.get('status')=='completed':result['status']='partial'
    return result


def _source_in_scope(tools, source, year, selected):
    if source.get('year')!=year or selected and source.get('unit_id')!=selected:return False
    try:tools._unit(source.get('unit_id'))
    except ResearchError:return False
    return True


def _current_segment(tools, segment, year, selected, reusable):
    source=segment.get('source') if isinstance(segment,dict) else None
    if not isinstance(source,dict) or not reusable(source):return None
    identifier=segment.get('entry_id')
    if source.get('id')!=identifier or not _source_in_scope(tools,source,year,selected):return None
    if any(not isinstance(segment.get(key),str) or not re.fullmatch(prefix+r'_[0-9a-f]{32}',segment[key])
           for key,prefix in (('generation_id','kg'),('extraction_id','ex'))):return None
    try:current=tools._knowledge().get(identifier)
    except Exception:return None
    if not isinstance(current,dict) or current.get('id')!=identifier or not reusable(current):return None
    if not _source_in_scope(tools,current,year,selected):return None
    extraction=current.get('extraction') or {}
    if (segment.get('generation_id')!=extraction.get('generation_id') or
            segment.get('extraction_id')!=extraction.get('id')):return None
    return current


def _original_link(segment, source):
    parsed=urlsplit(source.get('url') or '')
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
        return ''
    page=segment.get('page_start');extraction=source.get('extraction') or {}
    fragment=f'page={page}' if extraction.get('format')=='pdf' and type(page) is int and page>0 else ''
    return urlunsplit(parsed._replace(fragment=fragment))


def _public_segment(segment, source):
    public={key:deepcopy(segment[key]) for key in SEGMENT_FIELDS if key in segment}
    public['context_spans']=_public_spans(segment.get('context_spans',[]))
    public['source']={key:deepcopy(source[key]) for key in (*SOURCE_FIELDS,'id','quality') if key in source}
    public['original_url']=_original_link(segment,source)
    return public


def _public_spans(spans):
    allowed=('role','char_start','char_end','page_start','page_end','section_path')
    return [{key:deepcopy(span[key]) for key in allowed if key in span}
            for span in spans if isinstance(span,dict)] if isinstance(spans,list) else []


def _remember_segment(tools, segment, source):
    public=_public_segment(segment,source);identifier=public.get('id')
    if not isinstance(identifier,str) or not re.fullmatch(r'sg_[0-9a-f]{32}',identifier):return None
    value={**{key:deepcopy(source[key]) for key in SOURCE_FIELDS if key in source},
           'knowledge_id':source['id'],'knowledge_quality':deepcopy(source['quality']),
           'knowledge_saved':True,'knowledge_reused':True,'cache_hit':True,
           'knowledge_current':True,'excerpt':str(public.get('text') or '')[:1800]}
    source_id=tools._source(value)
    if not source_id:return None
    previous=tools.sources[source_id];old=(previous.get('extraction') or {}).get('generation_id')
    if old and old!=segment['generation_id']:
        tools._knowledge_invalidated.add(source['id'])
        if CURRENT_NOTICE not in tools.gaps:tools.gaps.append(CURRENT_NOTICE)
    reference={key:public[key] for key in ('id','generation_id','page_start','page_end','char_start','char_end','section_path','original_url') if key in public}
    references={item['id']:item for item in previous.get('segments',[]) if item.get('generation_id')==segment['generation_id']}
    tools.sources[source_id]={**previous,**value,'id':source_id,'segments':list({**references,identifier:reference}.values())}
    tools._knowledge_segments[identifier]={'entry_id':source['id'],'year':source['year'],
        'unit_id':source['unit_id'],'generation_id':segment['generation_id'],'match_mode':segment.get('match_mode','segment_terms')}
    tools._trusted_knowledge_ids.add(source['id']);tools.cache_hits.add(source['id'])
    public['source_id']=source_id
    return public


def search_segments(tools, query, unit_id, year, entry_id, reusable):
    groups=[];query_total=0
    for selected in ([unit_id] if unit_id else tools.task['unit_ids'] or ['']):
        rows=[]
        for page in range(1,5):
            tools._check_control()
            batch=tools._knowledge().search_segments(query=query,entry_id=entry_id,unit_id=selected,
                        year=year,page=page,page_size=8,quality='accepted')
            total=batch.get('total',0)
            for segment in batch.get('segments',[]):
                if entry_id and (not isinstance(segment,dict) or segment.get('entry_id')!=entry_id):continue
                source=_current_segment(tools,segment,year,selected,reusable)
                if source:rows.append((segment,source))
            if len(rows)>=8 or page*8>=total:break
        groups.append(rows);query_total+=total
    results=[];seen=set()
    for row in (row for group in zip_longest(*groups) for row in group if row is not None):
        segment,source=row
        if segment.get('id') in seen:continue
        public=_remember_segment(tools,segment,source)
        if public:results.append(public);seen.add(public['id'])
        if len(results)>=8:break
    distributed=any(item.get('match_mode')=='document_terms' for item in results)
    return {'segments':results,'total':len(results),'query_total':query_total,
            'notice':'命中来自当前有效知识留档；字符位置属于所标提取版本，表头/上下文与正文分别引用。'+(DISTRIBUTED_NOTICE if distributed else '')}


def _bounded_result(payload, limit):
    segment=payload.get('segment')
    if not isinstance(segment,dict):raise ValueError('invalid segment')
    blocks=[segment,*payload.get('contexts',[]),*payload.get('neighbors',[])]
    if any(not isinstance(block,dict) or not isinstance(block.get('text'),str) for block in blocks):
        raise ValueError('invalid context')
    size=sum(len(block['text']) for block in blocks)
    if size>limit:raise ValueError('oversized context')
    for block in blocks:
        start,end=block.get('char_start'),block.get('char_end')
        if type(start) is not int or type(end) is not int or start<0 or end-start!=len(block['text']):
            raise ValueError('invalid span')
        if block.get('entry_id',segment['entry_id'])!=segment['entry_id']:
            raise ValueError('mixed source')
        if block.get('generation_id',segment['generation_id'])!=segment['generation_id']:
            raise ValueError('mixed generation')
        if block.get('extraction_id',segment['extraction_id'])!=segment['extraction_id']:
            raise ValueError('mixed extraction')
    return segment,size


def _public_context(block, selected, source):
    public={key:deepcopy(block[key]) for key in (*SEGMENT_FIELDS,'role') if key in block}
    public['context_spans']=_public_spans(block.get('context_spans',[]))
    public={**public,**{key:public.get(key,selected[key]) for key in ('entry_id','extraction_id','generation_id')}}
    public['original_url']=_original_link(public,source)
    return public


def _load_segment(tools, identifier, before, after, limit):
    try:
        return tools._knowledge().read_segment(identifier,before=before,after=after,max_chars=limit,trusted_only=True)
    except Exception as error:
        if getattr(error,'code',None) in {'knowledge_not_reusable','unknown_segment','invalid_segment_query'}:
            raise ResearchError('knowledge_not_current','片段当前不可可信复用；请重新检索有效原文。',409) from None
        raise


def read_segment(tools, identifier, before, after, limit, reusable):
    known=tools._knowledge_segments.get(identifier)
    if known is None:raise ResearchError('unknown_knowledge_segment','请先在本轮知识检索中发现这份片段。')
    tools.refresh_knowledge()
    payload=_load_segment(tools,identifier,before,after,limit)
    try:
        segment,size=_bounded_result(payload,limit)
        if known.get('match_mode')=='document_terms':segment={**segment,'match_mode':'document_terms'}
        source=_current_segment(tools,segment,known['year'],known['unit_id'],reusable)
        if not source or segment.get('id')!=identifier or segment.get('generation_id')!=known['generation_id']:
            raise ValueError('stale segment')
        public=_remember_segment(tools,segment,source)
        if public is None:raise ValueError('invalid identity')
    except (ValueError,TypeError,KeyError):
        raise ResearchError('knowledge_not_current','片段当前不可可信复用；请重新检索有效原文。',409) from None
    return {'segment':public,'contexts':[_public_context(block,segment,source) for block in payload.get('contexts',[])],
            'neighbors':[_public_segment(block,source) for block in payload.get('neighbors',[])],'returned_chars':size,
            'truncated':bool(payload.get('truncated')),'notice':'已补读当前有效片段与所列上下文；不是新联网核验或正式招生字段。'+
            (DISTRIBUTED_NOTICE if public.get('match_mode')=='document_terms' else '')}
