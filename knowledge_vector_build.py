"""Explicit incremental embedding jobs; external work is outside SQLite transactions."""
import hashlib
import time
import uuid

from knowledge_store import KnowledgeError
from knowledge_vector_cache import checked_vector,identifier
import knowledge_vector_store as ledger


def check_stop(event):
    if event is not None and event.is_set():raise KnowledgeError('vector_cancelled','索引构建已停止，已完成内容与旧可用索引保留。',409)


def prepare(index,segments,config,space):
    """Validate every window as an exact sub-span and require the windows of one
    segment to tile it completely — no gap, no overlap, no substitute text."""
    result=[]
    for segment in segments:
        windows=index._prepare(segment,segment['contexts'],config)
        ordered=sorted(windows,key=lambda window:(window.get('char_start'),window.get('char_end')))
        cursor=segment['char_start']
        for window in ordered:
            start,end=window.get('char_start'),window.get('char_end')
            if (type(start) is not int or type(end) is not int or start!=cursor or end<=start
                    or end>segment['char_end']
                    or window.get('text')!=segment['text'][start-segment['char_start']:end-segment['char_start']]):
                raise ValueError('Embedding windows must cover the source span exactly once.')
            cursor=end
            text=window.get('embedding_text')
            if not isinstance(text,str) or not text:raise ValueError('Embedding window has no model input.')
            digest=hashlib.sha256((config.get('document_prefix','')+text).encode()).hexdigest()
            if digest!=window.get('input_sha'):raise ValueError('Embedding input hash does not match.')
            result.append({**window,'id':identifier('vi_',space['id'],segment['id'],start,end,digest),
                           'segment_id':segment['id'],'entry_id':segment['entry_id'],'generation_id':segment['generation_id'],
                           'unit_id':segment['source']['unit_id'],'year':segment['source']['year']})
        if not ordered or cursor!=segment['char_end']:
            raise ValueError('Embedding windows must cover the whole source segment.')
    return result


def _still_current(index,item):
    rows=ledger.eligible(index.store,entry_id=item['entry_id'])
    return any(row['id']==item['segment_id'] and row['generation_id']==item['generation_id'] for row in rows)


def _embedding(index,space,config,item,event,stats):
    vector=index.cache.get(space['id'],item['input_sha'],space['dimension'])
    if vector is not None:
        stats['cache_hits']+=1
        return vector,{},True
    stats['provider_calls']+=1
    response=index._transport().embed(config,[item['embedding_text']],purpose='document',stop_event=event)
    values=response.get('vectors',[])
    if len(values)!=1 or response.get('dimension')!=space['dimension']:raise ValueError('Embedding response mismatch.')
    vector=checked_vector(values[0],space['dimension'])
    # Record usage before the derived cache/ANN writes so an external write failure
    # can never discard usage the provider already reported (or its absence).
    known=response.get('usage_known',bool(response.get('usage')))
    usage=response.get('usage',{})
    stats['usage_known']=stats['usage_known'] and known
    if known:stats['input_tokens']+=usage.get('input_tokens',usage.get('total_tokens',0))
    else:stats['usage_unknown_calls']+=1
    stats['embedded_inputs']+=1
    index.cache.put(space['id'],item['input_sha'],vector,space['dimension'])
    return vector,usage,known


def process_item(index,space,config,item,build_id,event,stats):
    check_stop(event)
    token,previous=ledger.claim(index.store,space['id'],item['id'],build_id)
    attempted=False;returned=False;usage={};known=False
    try:
        if not _still_current(index,item):
            ledger.finish_item(index.store,space['id'],item,'cancelled',token,error_code='source_changed');return
        attempted=True
        vector,usage,known=_embedding(index,space,config,item,event,stats)
        returned=True
        check_stop(event)
        if not _still_current(index,item):
            ledger.finish_item(index.store,space['id'],item,'cancelled',token,error_code='source_changed');return
        if previous=='succeeded' and item['id'] in index._backend().contains(space,[item['id']]):
            # The deterministic vector id binds space+segment+window+input_sha, so a
            # surviving row for a succeeded job already holds this exact vector; the
            # external write can be skipped. After a store loss `contains` is empty
            # and every vector is restored from the derived cache.
            ledger.finish_item(index.store,space['id'],item,'succeeded',token,usage,known)
            return
        record={'id':item['id'],'vector':vector,'segment_id':item['segment_id'],'entry_id':item['entry_id'],
                'generation_id':item['generation_id'],'unit_id':item['unit_id'],'year':item['year'],'visibility':'public'}
        index._backend().upsert(space,[record])
        state='succeeded' if _still_current(index,item) else 'cancelled'
        if not ledger.finish_item(index.store,space['id'],item,state,token,usage,known):
            raise KnowledgeError('vector_lease_lost','该片段的索引任务已被其他执行轮次接管，本轮停止。',409)
    except Exception as error:
        cancelled=getattr(error,'code','')=='vector_cancelled'
        if attempted and not returned:
            # The request was sent but no validated response came back: whether the
            # provider billed this call stays unknown for the whole build.
            stats['usage_known']=False
            stats['usage_unknown_calls']+=1
        state='cancelled' if cancelled else 'uncertain' if attempted and not returned and config.get('local') is not True and not isinstance(error,(ValueError,KnowledgeError)) else 'failed'
        ledger.finish_item(index.store,space['id'],item,state,token,usage,known,error_code='cancelled' if cancelled else 'vector_write_failed')
        raise


def build(index,profile_id=None,stop_event=None,build_id=None):
    """One explicit build round; `build_id` must be the identifier already reported
    to the caller so the async contract and the persisted job are the same task."""
    check_stop(stop_event)
    build_id=build_id or 'vb_'+uuid.uuid4().hex
    config=index._config(profile_id);metadata=index._profile(config)
    space,revision=ledger.begin_build(index.store,metadata,config.get('profile_id'),build_id)
    stats={'embedded_inputs':0,'cache_hits':0,'provider_calls':0,'input_tokens':0,
           'usage_known':True,'usage_unknown_calls':0,'latency_ms':0}
    started=time.monotonic();items=[]
    try:
        index._backend().ensure_space(space)
        items=prepare(index,ledger.eligible(index.store),config,space)
        ledger.register_items(index.store,space['id'],items)
        for item in items:process_item(index,space,config,item,build_id,stop_event,stats)
        check_stop(stop_event)
        error=ledger.finish_build(index.store,space['id'],build_id,revision,items,
                                  {**stats,'latency_ms':int((time.monotonic()-started)*1000)},
                                  profile_id=config.get('profile_id'))
        if error:raise KnowledgeError('vector_source_changed','资料已变化，请重新构建当前有效片段。',409)
        return {**index.status(),'build_id':build_id}
    except Exception as error:
        try:
            ledger.finish_build(index.store,space['id'],build_id,revision,items,
                                {**stats,'latency_ms':int((time.monotonic()-started)*1000)},
                                'cancelled' if getattr(error,'code','')=='vector_cancelled' else 'vector_build_failed')
        except KnowledgeError:pass
        if isinstance(error,KnowledgeError):raise
        raise KnowledgeError('vector_build_failed','向量索引构建失败，旧可用索引仍保留，可检查配置后重试。',503) from None
