"""Explicit vector build and scoped semantic retrieval over authoritative source spans."""
import threading
import time
import uuid

from knowledge_store import KnowledgeError, _text, _year
from knowledge_vector_cache import VectorCache, checked_vector
import knowledge_vector_store as ledger
from knowledge_vector_build import build,check_stop

BATCH_SIZE=256
# Stable adapter codes raised while validating the request, before any provider
# request is sent. They must not be reported as attempted calls.
_BEFORE_REQUEST={'invalid_embedding_input','invalid_embedding_config','invalid_embedding_dimension',
    'invalid_provider','invalid_auth_mode','invalid_credentials','invalid_settings','invalid_key',
    'invalid_model','missing_credentials','missing_key','mixed_credentials','unsafe_endpoint',
    'invalid_endpoint','embedding_input_too_long','missing_model','embedding_model_not_ready',
    'embedding_model_invalid'}


class VectorIndex:
    def __init__(self, knowledge_store,settings,embedding=None,backend_factory=None):
        self.store, self.settings = knowledge_store, settings
        self.embedding, self.backend_factory = embedding, backend_factory
        try:self.cache=VectorCache(self.store.data_dir)
        except ValueError:
            raise KnowledgeError('vector_unavailable','向量索引目录不在项目数据目录内，已拒绝初始化。',503) from None
        self._client=None;self._resource_lock=threading.RLock();self._build_lock=threading.Lock()
        self._thread=None;self._stop=threading.Event();self._closed=False
        self._async_error='';self._release_error_code=''

    def status(self):
        summary=ledger.summary(self.store)
        if self._async_error and not summary['error_code']:
            summary={**summary,'error_code':self._async_error}
        if self._release_error_code and not summary['error_code']:
            summary={**summary,'error_code':self._release_error_code}
        return summary

    def _transport(self):
        if self.embedding is None:
            from embedding_transport import EmbeddingTransport
            self.embedding=EmbeddingTransport(self.store.data_dir)
        return self.embedding

    def _profile(self,config):
        return self._transport().profile(config)

    def _prepare(self,segment,contexts,config):
        custom=getattr(self._transport(),'prepare_inputs',None)
        if callable(custom):return custom(segment,contexts,config)
        from embedding_transport import prepare_inputs
        return prepare_inputs(segment,contexts,{**config,'_data_dir':str(self.store.data_dir)})

    def _config(self,profile_id=None):
        try:return self.settings.runtime_embedding(profile_id)
        except KnowledgeError:raise
        except Exception as error:
            raise KnowledgeError('embedding_not_configured','请先连接并验证独立的 Embedding 服务。',409) from None

    def _backend(self):
        with self._resource_lock:
            if self._closed:raise KnowledgeError('vector_unavailable','向量索引服务已关闭。',503)
            if self._client is None:
                if self.backend_factory:self._client=self.backend_factory()
                else:
                    from milvus_backend import MilvusBackend
                    self._client=MilvusBackend(self.store.data_dir)
            return self._client

    def build(self, profile_id=None, stop_event=None, build_id=None):
        if not self._build_lock.acquire(blocking=False):raise KnowledgeError('vector_build_running','已有索引构建正在执行。',409)
        try:return build(self,profile_id,stop_event or self._stop,build_id)
        finally:self._build_lock.release()

    def start_build(self,profile_id=None):
        """Explicit async build; the returned build_id is the identifier the worker
        persists, so callers can poll the exact task they started."""
        self._config(profile_id)
        with self._resource_lock:
            if self._closed:raise KnowledgeError('vector_unavailable','向量索引服务已关闭。',503)
            if self._thread and self._thread.is_alive():
                status=self.status()
                return {**status,'started':False,'build_id':status.get('build_id')}
            self._async_error=''
            self._stop=threading.Event()
            stop,build_id=self._stop,'vb_'+uuid.uuid4().hex
            def run():
                try:self.build(profile_id,stop,build_id)
                except KnowledgeError as error:self._async_error=error.code
            self._thread=threading.Thread(target=run,name='yanxu-vector-build',daemon=True)
            self._thread.start()
            return {**self.status(),'started':True,'build_id':build_id}

    def cancel_build(self):
        """Ask the running worker to stop before its next embedding call; results the
        provider already returned are dropped by the transport, never published. This
        reports what happened on the server, not that an HTTP request was aborted."""
        status=self.status()
        if not status.get('building') and not (self._thread and self._thread.is_alive()):
            return {'state':'not_running','build_id':status.get('build_id'),'started':False}
        self._stop.set()
        return {'state':'cancelling','build_id':status.get('build_id'),'started':True}

    def search(self,query,unit_id='',year=None,entry_id=None,limit=40,stop_event=None,allow_remote=False):
        """One query attempt with request-local, safe usage accounting.

        Every failure carries a `retrieval_usage` metadata dict: calls attempted,
        calls whose validated receipt returned, known input tokens (or None), whether
        usage is known, the failing stage and latency. Upstream degradations must not
        read `{}` as a free/known-zero call.
        """
        started=time.monotonic()
        accounting={'embedding_calls':0,'successful_embedding_calls':0,'input_tokens':None,
                    'usage_known':True,'stage':'input'}
        try:
            return self._search(query,unit_id,year,entry_id,limit,stop_event,allow_remote,accounting)
        except KnowledgeError as error:
            if getattr(error,'retrieval_usage',None) is None:
                error.retrieval_usage={**accounting,'latency_ms':int((time.monotonic()-started)*1000)}
            raise
        except Exception:
            failure=KnowledgeError('vector_unavailable','语义索引暂时不可用，可继续使用关键词检索。',503)
            failure.retrieval_usage={**accounting,'latency_ms':int((time.monotonic()-started)*1000)}
            raise failure from None

    def _search(self,query,unit_id,year,entry_id,limit,stop_event,allow_remote,accounting):
        query=_text(query,240,False);unit_id=_text(unit_id,100);year=_year(year)
        if entry_id is not None:self.store._entry_id(entry_id)
        if type(limit) is not int or not 1<=limit<=100 or type(allow_remote) is not bool:
            raise KnowledgeError('invalid_vector_query','向量检索参数不正确。')
        accounting['stage']='scope'
        status=self.status();active=status['active_space_id']
        if not active:raise KnowledgeError('vector_not_ready','向量索引尚未就绪，请先显式构建。',409)
        space=ledger.space(self.store,active)
        if space is None:raise KnowledgeError('vector_not_ready','向量索引尚未就绪，请先显式构建。',409)
        segments=ledger.eligible(self.store,unit_id,year,entry_id)
        items=ledger.completed_items(self.store,active,segments)
        empty={'segments':[],'total':0,'returned_count':0,'candidate_count':0,'space_id':active,
               'profile_id':space['profile_id'],'usage':{},'usage_known':True,
               'latency_ms':0,'mode':'semantic','degraded':False}
        if not items:return empty
        config=self._config(space['profile_id'])
        if self._profile(config)['fingerprint']!=space['fingerprint']:
            raise KnowledgeError('vector_profile_changed','当前查询模型与索引不一致，请重建后再采用。',409)
        # Remote embedding always costs money: refuse before any inference unless the
        # user explicitly started this action. Local capability comes from the A
        # contract (`local`), never from guessing by provider name.
        accounting['stage']='remote_gate'
        if config.get('local') is not True and not allow_remote:
            raise KnowledgeError('vector_remote_requires_action','远程语义检索需要你主动发起，不会在页面读取时计费。',409)
        started=time.monotonic();check_stop(stop_event)
        accounting['stage']='embedding';accounting['embedding_calls']=1
        try:
            response=self._transport().embed(config,[query],purpose='query',stop_event=stop_event)
        except KnowledgeError as error:
            # Input validation failures happen before any provider request; they are
            # not an attempted, billable call. Anything else stays unknown.
            if getattr(error,'code','') in _BEFORE_REQUEST:
                accounting.update(embedding_calls=0,usage_known=True,input_tokens=None,stage='embedding')
            else:
                accounting.update(usage_known=False,input_tokens=None,stage='embedding')
            raise
        except Exception:
            # Sent or not, no validated receipt exists: do not pretend zero billing.
            accounting.update(usage_known=False,input_tokens=None,stage='embedding')
            raise
        try:
            if response.get('dimension')!=space['dimension'] or len(response.get('vectors',[]))!=1:
                raise ValueError('Query dimension mismatch.')
            vector=checked_vector(response['vectors'][0],space['dimension'])
        except Exception:
            # A response that failed contract validation is not a usable receipt.
            accounting.update(usage_known=False,input_tokens=None,stage='embedding_response')
            raise
        usage=response.get('usage',{})
        known=response.get('usage_known',bool(usage))
        accounting.update(successful_embedding_calls=1,usage_known=known,
                          input_tokens=(usage.get('input_tokens',usage.get('total_tokens')) if known else None))
        accounting['stage']='ann'
        rows=self._search_batches(space,vector,items,stop_event)
        accounting['stage']='verify'
        result,candidate=self._current_hits(rows,items,unit_id,year,entry_id,limit)
        accounting['stage']='done'
        return {**empty,'segments':result,'total':len(result),'returned_count':len(result),
                'candidate_count':candidate,'usage':usage,'usage_known':known,
                'latency_ms':int((time.monotonic()-started)*1000)}

    def _search_batches(self,space,vector,items,event):
        identifiers=[item['item_id'] for item in items];rows=[]
        for offset in range(0,len(identifiers),BATCH_SIZE):
            check_stop(event);batch=identifiers[offset:offset+BATCH_SIZE]
            rows.extend(self._backend().search(space,vector,batch,len(batch)))
        return rows

    def _current_hits(self,rows,items,unit_id,year,entry_id,limit):
        import math
        parents={item['item_id']:item for item in items}
        current={row['id']:row for row in ledger.eligible(self.store,unit_id,year,entry_id)}
        scores={};candidate=0
        for row in rows:
            item=parents.get(row.get('id'));score=row.get('score')
            if not item or type(score) not in (int,float) or not math.isfinite(score):continue
            candidate+=1
            parent=current.get(item['segment_id'])
            if not parent or parent['generation_id']!=item['generation_id']:continue
            previous=scores.get(parent['id'])
            if previous is None or score>previous['semantic_score']:
                public={key:value for key,value in parent.items() if key not in {'embedding_text','contexts'}}
                scores[parent['id']]={**public,'semantic_score':float(score),'match_mode':'semantic',
                                      'embedding_span':{'char_start':item['char_start'],'char_end':item['char_end']}}
        return sorted(scores.values(),key=lambda row:(-row['semantic_score'],row['id']))[:limit],candidate

    def close(self):
        self._stop.set()
        thread=self._thread
        if thread is not None and thread.is_alive():thread.join(timeout=5)
        report={'released':False,'error_code':''}
        with self._resource_lock:
            self._closed=True
            if self._client is not None:
                value=self._client.close();self._client=None
                if isinstance(value,dict):report={**report,**value}
        if report.get('error_code'):self._release_error_code=report['error_code']
        return report
