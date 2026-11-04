"""Finite RRF retrieval over current evidence; ANN payload is never original text."""
from copy import deepcopy
import math
import re
import time

from knowledge_store import KnowledgeError, _text
from knowledge_query_terms import analyze as analyze_query, bigrams as query_bigrams

_PII=re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{17}[\dXx](?!\d)')
_MATH={'301','302','303'}
_CODES=re.compile(r'(?<!\d)(101|199|201|202|203|204|301|302|303|396|408)(?!\d)')
_SOURCE=('id','url','title','unit_id','unit_name','year','requested_year','status','sha256','quality',
         'validity','usable','original_available','snapshot_status','field_verification','extraction','text_origin')
_SEGMENT=('id','entry_id','extraction_id','generation_id','ordinal','kind','text','char_start','char_end',
          'page_start','page_end','section_path','context_spans','format','page_kind','offset_units','text_sha256',
          'parent_id','previous_id','next_id','row_complete','continues_previous','continues_next')
_DEGRADATIONS={'embedding_not_configured','embedding_model_not_ready','embedding_profile_unavailable',
               'model_not_verified','vector_not_ready','vector_unavailable','vector_remote_requires_action'}
NOTICE='仅在当前可核验资料的有限候选中检索；语义相近不等于科目、资格或报考条件相同，仍须精读原文。'


def _stable(row):
    """Content-based ordering key; random store ids must never decide a tie."""
    source=row.get('source') if isinstance(row,dict) else None
    source=source if isinstance(source,dict) else {}
    ordinal=row.get('ordinal') if isinstance(row,dict) else None
    return (str(source.get('sha256') or ''),str(source.get('url') or ''),
            str(row.get('entry_id') or ''),ordinal if type(ordinal) is int else -1,
            str(row.get('id') or ''))


def reciprocal_rank_fusion(channels):
    fused={}
    for method,rows in channels.items():
        seen=set()
        for rank,row in enumerate(rows,1):
            identifier=row.get('id') if isinstance(row,dict) else None
            if not isinstance(identifier,str) or identifier in seen:continue
            seen.add(identifier);current=fused.get(identifier,{'id':identifier,'rrf_score':0.,'ranks':{},'methods':[]})
            fused[identifier]={**current,'rrf_score':current['rrf_score']+1/(60+rank),
                'ranks':{**current['ranks'],method:rank},'methods':[*current['methods'],method],
                'tie':current.get('tie') or _stable(row)}
    return sorted(fused.values(),key=lambda row:(-row['rrf_score'],row['tie']))


def _parameters(query,unit_id,year,entry_id,limit,allow_remote):
    query=_text(query,240,False);unit_id=_text(unit_id,100)
    if _PII.search(query):raise KnowledgeError('private_search_query','请只输入公开学校、专业、科目和年度问题。')
    if type(limit) is not int or not 1<=limit<=20 or type(allow_remote) is not bool:
        raise KnowledgeError('invalid_query','检索数量应为1至20，调用方式需要明确。')
    if year is not None and (type(year) is not int or not 1900<=year<=2100):
        raise KnowledgeError('invalid_query','检索年度格式不正确。')
    if entry_id is not None and (not isinstance(entry_id,str) or not re.fullmatch(r'k_[0-9a-f]{24}',entry_id)):
        raise KnowledgeError('invalid_query','知识资料编号不正确。')
    return query,{'unit_id':unit_id,'year':year,'entry_id':entry_id}


def _usable(source,scope):
    if not isinstance(source,dict):return False
    quality=source.get('quality') or {}
    if (quality.get('state')!='accepted' or quality.get('reusable') is not True or
        source.get('validity','active')!='active' or source.get('usable') is False or
        source.get('status')!='official_original_read' or source.get('original_available') is not True or
        source.get('text_origin')=='metadata_only'):return False
    return all(value is None or value=='' or source.get(key if key!='entry_id' else 'id')==value for key,value in scope.items())


def _positive_codes(query):
    return {code for code in _CODES.findall(query) if not re.search(r'(?:不(?:想|打算|愿意)?考|排除|不要|不含)\s*'+code,query)}


def _code_compatible(query,body):
    requested=_positive_codes(query)&_MATH;actual=set(_CODES.findall(body))&_MATH
    return not requested or not actual or bool(requested&actual)


def _pairs(query):
    analyzed=analyze_query(query)
    if analyzed:
        return [(item['term'].lower(),item['weight']) for item in analyzed]
    return [(term.lower(),1) for term in query.split()[:8]]


def _window_text(row):
    value=row.get('_window_text')
    return (value if isinstance(value,str) and value else row['text']).lower()


def _features(query,row,seen_entries,seen_text,pairs,grams,total,state):
    """Deterministic lexical agreement plus marginal window coverage.

    Every selected window is read by the caller together with its neighbours, so
    novelty is measured on the same window body. A candidate that only repeats
    already-covered terms is less useful than one that exposes a missing code,
    subject or fee line. No model score enters the ranking.
    """
    body=row['text'].lower();window=_window_text(row);codes=_positive_codes(query)
    own=sum(weight for term,weight in pairs if term in body)
    hit_weights=sum(weight for term,weight in pairs if term in window)
    new=sum(weight for term,weight in pairs if term in window and term not in state['terms'])
    own_grams={gram for gram in grams if gram in body}
    hit_grams={gram for gram in grams if gram in window}
    new_grams=hit_grams-state['grams']
    source=row.get('source') or {}
    path=row.get('section_path') or []
    if isinstance(path,list):path=' '.join(str(item) for item in path)
    heading=(str(source.get('title') or '')+' '+str(path)).lower()
    ordinal=row.get('ordinal')
    overlapping=any(entry==row['entry_id'] and type(ordinal) is int and abs(value-ordinal)<=2
                    for entry,value in state['ordinals'])
    return {'rrf':row['rrf_score'],'phrase':.01 if query.lower() in body else 0.,
            'codes':.01*len(codes&set(_CODES.findall(body)))/max(1,len(codes)),
            'coverage':.008*own/total,
            'window':.03*hit_weights/total,
            'bigrams':.006*len(own_grams)/max(1,len(grams)),
            'window_bigrams':.006*len(hit_grams)/max(1,len(grams)),
            'novelty':.03*new/total,
            'novel_bigrams':.006*len(new_grams)/max(1,len(grams)),
            'heading':.004 if any(term in heading for term,_ in pairs) else 0.,
            'entry':.01 if state['entries'] and row['entry_id'] not in state['entries'] else 0.,
            'overlap':-.03 if overlapping else 0.,
            'redundancy':-.008 if body in seen_text else -.004 if row['entry_id'] in seen_entries else 0.}


def rule_rerank(query,rows,limit):
    remaining=list(rows);selected=[];seen_entries=set();seen_text=set()
    pairs=_pairs(query);grams=query_bigrams(query);total=sum(weight for _,weight in pairs) or 1
    state={'terms':set(),'grams':set(),'entries':set(),'ordinals':[]}
    while remaining and len(selected)<limit:
        scored=[{**row,'rerank':{'version':'rule_v2',**_features(query,row,seen_entries,seen_text,
                                                                  pairs,grams,total,state)}} for row in remaining]
        scored=[{**row,'retrieval_score':sum(value for value in row['rerank'].values() if type(value) is float)} for row in scored]
        chosen=min(scored,key=lambda row:(-row['retrieval_score'],_stable(row)))
        window=_window_text(chosen)
        state['terms'].update(term for term,_ in pairs if term in window)
        state['grams'].update(gram for gram in grams if gram in window)
        state['entries'].add(chosen['entry_id']);state['ordinals'].append((chosen['entry_id'],chosen.get('ordinal')))
        selected.append({key:value for key,value in chosen.items() if key!='_window_text'})
        seen_entries.add(chosen['entry_id']);seen_text.add(chosen['text'].lower())
        remaining=[row for row in remaining if row['id']!=chosen['id']]
    return selected


def _usage(values):
    """Only finite, non-negative accounting facts survive; never text, keys or paths."""
    if not isinstance(values,dict):return {}
    result={}
    for key,value in values.items():
        if not re.fullmatch(r'[a-z_]{1,40}',str(key)) or key=='usage_known':continue
        if value is None and key in {'input_tokens','total_tokens'}:result[key]=None
        elif type(value) in {int,float} and math.isfinite(value) and value>=0:result[key]=value
    return result


def _known(values,fallback):
    if isinstance(values,dict) and type(values.get('usage_known')) is bool:return values['usage_known']
    return fallback



class HybridRetriever:
    def __init__(self,store,vector_index=None):
        self.store,self.vector_index,self._closed=store,vector_index,False

    def close(self):
        self._closed=True

    def _check(self,stop):
        if self._closed:raise KnowledgeError('retrieval_closed','检索服务已关闭。',503)
        if stop is not None and stop.is_set():raise KnowledgeError('retrieval_cancelled','检索已取消。',409)

    def _semantic(self,query,scope,allow_remote,stop):
        self._check(stop)
        if self.vector_index is None:return [],{'usage':{},'usage_known':True,'space_id':None},'vector_not_ready'
        try:
            result=self.vector_index.search(query,**scope,limit=40,stop_event=stop,allow_remote=allow_remote)
            self._check(stop)
            usage=_usage(result.get('usage'))
            fallback=_known(result.get('usage'),bool(result.get('usage')))
            known=result['usage_known'] if type(result.get('usage_known')) is bool else fallback
            return result.get('segments',[])[:40],{'usage':usage,'usage_known':known,
                'space_id':result.get('space_id'),'latency_ms':result.get('latency_ms')},''
        except Exception as error:
            self._check(stop)
            code=getattr(error,'code','vector_unavailable')
            metadata=getattr(error,'retrieval_usage',None)
            if 'cancel' in str(code):
                failure=KnowledgeError('retrieval_cancelled','检索已取消。',409)
                failure.retrieval_usage=metadata
                raise failure from None
            usage=_usage(metadata)
            known=_known(metadata,False)
            latency=metadata.get('latency_ms') if isinstance(metadata,dict) else None
            return [],{'usage':usage,'usage_known':known,'space_id':None,'latency_ms':latency},code if code in _DEGRADATIONS else 'vector_unavailable'

    def _hydrate(self,hit,scope):
        identifier=hit.get('id') if isinstance(hit,dict) else None
        if not isinstance(identifier,str) or not re.fullmatch(r'sg_[0-9a-f]{32}',identifier):return None
        try:
            window=self.store.read_segment(identifier,before=2,after=2,max_chars=12000,trusted_only=True)
            value=window['segment']
            source=self.store.get(value['entry_id']);extraction=source.get('extraction') or {}
            if not _usable(source,scope) or source.get('id')!=value['entry_id']:return None
            if any(value.get(key)!=extraction.get(field) for key,field in [('extraction_id','id'),('generation_id','generation_id')]):return None
            if any(key in hit and hit[key]!=value.get(key) for key in ('entry_id','extraction_id','generation_id')):return None
            start,end=value.get('char_start'),value.get('char_end');body=value.get('text')
            if not isinstance(body,str) or type(start) is not int or type(end) is not int or end-start!=len(body):return None
            public={key:deepcopy(value[key]) for key in _SEGMENT if key in value}
            public['source']={key:deepcopy(source[key]) for key in _SOURCE if key in source}
            public['match_mode']=hit.get('match_mode','segment_terms')
            public['_window_text']=' '.join(item.get('text','') for item in [value,*window.get('neighbors',[])]
                                            if isinstance(item.get('text'),str))
            return public
        except Exception:return None

    def retrieve(self,query,*,unit_id='',year=None,entry_id=None,limit=8,allow_remote=False,stop_event=None):
        query,scope=_parameters(query,unit_id,year,entry_id,limit,allow_remote);self._check(stop_event)
        started=time.perf_counter();keyword=self.store.search_segments(query=query,**scope,page=1,page_size=40,quality='accepted')
        keyword_ms=(time.perf_counter()-started)*1000;semantic_started=time.perf_counter()
        semantic,vector,reason=self._semantic(query,scope,allow_remote,stop_event)
        channels={'keyword':keyword.get('segments',[])[:40],**({'semantic':semantic} if not reason else {})}
        semantic_ms=(time.perf_counter()-semantic_started)*1000;ranked=reciprocal_rank_fusion(channels)
        hits={row['id']:row for rows in reversed(list(channels.values())) for row in rows if isinstance(row,dict) and row.get('id')}
        hydrated=[];rerank_started=time.perf_counter()
        for rank in ranked:
            self._check(stop_event);row=self._hydrate(hits[rank['id']],scope)
            if row is None or not _code_compatible(query,row['text']):continue
            mode=hits[rank['id']].get('match_mode','semantic' if rank['methods']==['semantic'] else 'segment_terms')
            hydrated.append({**row,'match_mode':mode,'rrf_score':rank['rrf_score'],'retrieval_methods':rank['methods'],'retrieval_ranks':rank['ranks']})
        results=rule_rerank(query,hydrated,limit);self._check(stop_event)
        usage=dict(vector.get('usage') or {})
        usage_known=vector.get('usage_known',not reason)
        return {'segments':results,'returned_count':len(results),'candidate_count':len(hydrated),
            'usage':usage,'usage_known':bool(usage_known),
            'retrieval':{'mode':'keyword' if reason else 'hybrid','methods':list(channels),'space_id':vector.get('space_id'),
                'degraded':bool(reason),'degraded_reason':reason,'partial_scope':True,'usage':usage,
                'usage_known':bool(usage_known),'reranker':'rule_v2','notice':NOTICE,
                'latency':{'keyword_ms':keyword_ms,'semantic_ms':semantic_ms,
                    'rerank_ms':(time.perf_counter()-rerank_started)*1000,'total_ms':(time.perf_counter()-started)*1000},
                **({'embedding_latency_ms':vector['latency_ms']} if type(vector.get('latency_ms')) is int else {})}}
