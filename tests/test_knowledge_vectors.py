"""Vector governance uses disposable databases and deterministic model fixtures."""
from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest

import knowledge_vectors
from knowledge_store import KnowledgeError, KnowledgeStore
from knowledge_vector_cache import VectorCache, identifier
from knowledge_vector_store import begin_build, claim, connection as vector_connection, finish_item, register_items
from knowledge_vectors import VectorIndex


ROOT = Path(__file__).resolve().parents[1]


class FakeSettings:
    def __init__(self):
        base={'profile_id':'ep_fixture','provider':'fixture','base_url':'https://embedding.example.com/v1',
              'model':'fixture-v1','revision':'1','dimension':3,'normalization':'l2','api_key':'sk-fixture',
              'document_prefix':'','query_prefix':'','input_version':'v1'}
        self.config=dict(base);self.saved={'ep_fixture':dict(base)}

    def add(self, profile_id, **overrides):
        value={**self.config,'profile_id':profile_id,**overrides}
        self.saved[profile_id]=value
        return value

    def runtime_embedding(self, profile_id=None):
        if profile_id and profile_id not in self.saved:
            raise KnowledgeError('embedding_profile_unavailable','该向量配置已不可用，请重新连接。',503)
        return deepcopy(self.saved[profile_id] if profile_id else self.config)


class FakeEmbedding:
    """Fake remote contract; vectors are chosen by marker text, never by a real model."""
    def __init__(self):
        self.calls=[];self.after_embed=None;self.fail=False
        self.usage_known=None;self.usage=None
        self.prepare_mode='single';self.gate=None

    @staticmethod
    def vector_for(text):
        digest=hashlib.sha256(('v'+text).encode()).digest()
        return [digest[0]/255.,digest[1]/255.,1.0]

    def embed(self, config, texts, purpose='document', stop_event=None):
        self.calls.append((config['profile_id'],tuple(texts),purpose))
        if self.gate is not None:
            gate_started,gate_release=self.gate
            gate_started.set()
            while not gate_release.wait(0.05):
                pass
            self.gate=None
            if stop_event is not None and stop_event.is_set():
                raise KnowledgeError('vector_cancelled','索引构建已停止，已完成内容与旧可用索引保留。',409)
        if self.fail:raise OSError('private key/path must not leak')
        vectors=[self.vector_for(text) for text in texts]
        if self.after_embed:self.after_embed()
        usage=self.usage if self.usage is not None else {'input_tokens':len(texts)}
        known=self.usage_known if self.usage_known is not None else bool(usage)
        return {'vectors':vectors,'dimension':3,'model':config['model'],'usage':usage,'usage_known':known}

    @staticmethod
    def profile(config):
        # Mirror the A contract: the serving profile id and the API key never enter
        # the model fingerprint, so a key rotation keeps the same vector space.
        clean={key:value for key,value in config.items() if key not in {'api_key','profile_id','_data_dir'}}
        return {**clean,'fingerprint':hashlib.sha256(json.dumps(clean,sort_keys=True).encode()).hexdigest()}

    def prepare_inputs(self, segment, contexts, config):
        text=segment['text'];start,end=segment['char_start'],segment['char_end']
        prefix=config.get('document_prefix','')
        def window(left,right):
            body=text[left:right]
            return {'segment_id':segment['id'],'text':body,'embedding_text':body,
                    'char_start':start+left,'char_end':start+right,
                    'input_sha':hashlib.sha256((prefix+body).encode()).hexdigest()}
        if self.prepare_mode=='single':
            return [window(0,len(text))]
        if self.prepare_mode=='split3':
            cut=len(text)//3
            return [window(0,cut),window(cut,2*cut),window(2*cut,len(text))]
        if self.prepare_mode=='gap':
            cut=len(text)//3
            return [window(0,cut),window(2*cut,len(text))]
        if self.prepare_mode=='sha':
            only=window(0,len(text));only['input_sha']='0'*64;return [only]
        if self.prepare_mode=='text':
            only=window(0,len(text));only['text']=only['text']+'篡改';return [only]
        if self.prepare_mode=='empty':
            return []
        raise AssertionError('unknown prepare mode '+self.prepare_mode)


class MemoryBackend:
    def __init__(self):
        self.rows={};self.queries=[];self.upserts=0;self.fail=False;self.search_fail=False;self.closed=False

    def ensure_space(self,space):self.rows.setdefault(space['id'],{})

    def contains(self,space,identifiers):
        return {value for value in identifiers if value in self.rows.get(space['id'],{})}

    def upsert(self,space,records):
        if self.fail:raise OSError('private backend path')
        self.upserts+=len(records)
        self.rows[space['id']].update({row['id']:deepcopy(row) for row in records})

    def search(self,space,vector,allowed_ids,limit):
        self.queries.append(tuple(allowed_ids));assert allowed_ids
        if self.search_fail:raise OSError('private backend search path')
        return sorted([{'id':value,'score':sum(a*b for a,b in zip(vector,row['vector']))}
                       for value,row in self.rows.get(space['id'],{}).items() if value in allowed_ids],
                      key=lambda row:(-row['score'],row['id']))[:limit]

    def close(self):self.closed=True


class KnowledgeVectorTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='n4-vectors-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.store=KnowledgeStore(self.root)
        self.settings=FakeSettings();self.embedding=FakeEmbedding();self.backend=MemoryBackend()
        self.index=VectorIndex(self.store,self.settings,self.embedding,lambda:self.backend)
        self.addCleanup(self.index.close)

    def save(self,name='one',unit='u1',year=2027,extra='跨专业考生须加试。',raw_suffix=None):
        text=f'{year}年硕士招生专业目录\n081200 计算机科学与技术\n初试科目101、201、301、408。\n\n'+extra
        raw=('%PDF-1.4\nvector fixture '+(name if raw_suffix is None else raw_suffix)).encode()
        source={'url':'https://grad.example.edu.cn/'+name+'.pdf','title':f'{year}年硕士招生专业目录',
                'unit_id':unit,'unit_name':'合成大学','year':year,'status':'official_original_read',
                'sha256':hashlib.sha256(raw).hexdigest()}
        return self.store.save_source(source,document={'raw':raw,'text':text,'pages':[{'page':1,'text':text}],
                    'url':source['url'],'attribution_status':'verified'})

    def wait_idle(self, timeout=15):
        deadline=time.time()+timeout
        while self.index.status()['building'] and time.time()<deadline:
            time.sleep(0.02)
        return not self.index.status()['building']

    # ---- explicit build, idempotence and N3 isolation -------------------------------
    def test_build_is_explicit_idempotent_and_keeps_n3_generation(self):
        source=self.save();generation=source['extraction']['generation_id']
        self.assertEqual(self.index.status()['status'],'not_ready')
        result=self.index.build();self.assertEqual(result['status'],'ready')
        calls=len(self.embedding.calls);upserts=self.backend.upserts
        self.index.build()
        self.assertEqual((len(self.embedding.calls),self.backend.upserts),(calls,upserts))
        self.assertEqual(self.store.get(source['id'])['extraction']['generation_id'],generation)
        self.assertTrue(self.index.search('跨考',unit_id='u1',year=2027,allow_remote=True)['segments'])

    def test_scope_and_revocation_filter_before_ann_and_empty_scope_is_no_call(self):
        wanted=self.save();other=self.save('other','u2');older=self.save('older',year=2026)
        self.index.build();self.store.change_validity(other['id'],'revoked','来源失效')
        result=self.index.search('跨考',unit_id='u1',year=2027,limit=1,allow_remote=True)
        self.assertEqual(result['segments'][0]['entry_id'],wanted['id'])
        self.assertEqual((result['usage']['embedding_calls'],result['usage']['successful_embedding_calls']),(1,1))
        self.assertTrue(result['usage_known'])
        count=len(self.embedding.calls);queries=len(self.backend.queries)
        empty=self.index.search('跨考',unit_id='missing',year=2027)
        self.assertEqual(empty['segments'],[]);self.assertEqual(empty['usage'],{});self.assertTrue(empty['usage_known'])
        self.assertEqual((len(self.embedding.calls),len(self.backend.queries)),(count,queries))
        self.store.change_validity(wanted['id'],'revoked','不再使用')
        self.assertEqual(self.index.search('跨考',unit_id='u1',year=2027)['segments'],[])

    def test_new_model_failure_keeps_old_serving_profile(self):
        self.save();self.index.build();active=self.index.status()['active_space_id']
        self.settings.config=self.settings.add('ep_new',model='fixture-v2')
        self.backend.fail=True
        with self.assertRaises(KnowledgeError):self.index.build()
        self.backend.fail=False
        self.assertEqual(self.index.status()['active_space_id'],active)
        self.index.search('跨考',year=2027,allow_remote=True)
        self.assertEqual(self.embedding.calls[-1][0],'ep_fixture')

    def test_old_space_refuses_queries_when_bound_model_identity_is_broken(self):
        self.save();self.index.build()
        with vector_connection(self.store,True) as handle:
            handle.execute('UPDATE knowledge_vector_spaces SET fingerprint=?',
                           ('f'*64,))
        calls=len(self.embedding.calls)
        with self.assertRaises(KnowledgeError) as caught:
            self.index.search('跨考',allow_remote=True)
        self.assertEqual(caught.exception.code,'vector_profile_changed')
        self.assertEqual(len(self.embedding.calls),calls)

    def test_revocation_during_embedding_cannot_activate_stale_vectors(self):
        saved=self.save()
        self.embedding.after_embed=lambda:self.store.change_validity(saved['id'],'revoked','构建期间撤信')
        with self.assertRaises(KnowledgeError):self.index.build()
        self.assertIsNone(self.index.status()['active_space_id'])
        self.assertTrue(self.index.status()['dirty'])

    def test_failed_build_retries_without_reembedding_completed_inputs(self):
        self.save();self.backend.fail=True
        with self.assertRaises(KnowledgeError):self.index.build()
        first={call[1] for call in self.embedding.calls}
        self.assertTrue(first)
        self.backend.fail=False
        self.index.build()
        retried=[call[1] for call in self.embedding.calls[len(first):]]
        self.assertTrue(all(texts not in first for texts in retried))
        self.assertEqual(self.index.status()['status'],'ready')

    def test_cancelled_build_does_not_switch_pointer(self):
        self.save();event=threading.Event();event.set()
        with self.assertRaises(KnowledgeError):self.index.build(stop_event=event)
        self.assertIsNone(self.index.status()['active_space_id'])

    # ---- planning 1: same fingerprint, key rotation ---------------------------------
    def test_same_fingerprint_key_rotation_switches_profile_only_after_success(self):
        self.save();self.index.build()
        space=self.index.status()['active_space_id']
        self.assertEqual(self.index.status()['active_profile_id'],'ep_fixture')
        calls=len(self.embedding.calls)
        self.settings.config=self.settings.add('ep_rotated',api_key='sk-rotated')
        # The failed round must reuse the cached vectors (no re-billing) and keep the
        # old serving profile; only a verified round adopts the renewed key. The ANN
        # store is emptied so the round still has real external writes to perform.
        self.backend.rows.clear()
        self.backend.fail=True
        with self.assertRaises(KnowledgeError):self.index.build()
        self.assertEqual(len(self.embedding.calls),calls)
        self.assertEqual(self.index.status()['active_profile_id'],'ep_fixture')
        self.assertEqual(self.index.status()['active_space_id'],space)
        self.backend.fail=False
        self.index.build()
        self.assertEqual(len(self.embedding.calls),calls)
        self.assertEqual(self.index.status()['active_profile_id'],'ep_rotated')
        self.assertEqual(self.index.status()['active_space_id'],space)
        self.index.search('跨考',year=2027,allow_remote=True)
        self.assertEqual(self.embedding.calls[-1][0],'ep_rotated')

    # ---- planning 2: dirty coverage --------------------------------------------------
    def test_reassess_and_rebuild_mark_vectors_dirty_but_serve_current_generation(self):
        source=self.save();self.index.build()
        self.assertFalse(self.index.status()['dirty'])
        self.store.reassess()
        self.assertTrue(self.index.status()['dirty'])
        result=self.index.search('跨考',unit_id='u1',year=2027,allow_remote=True)
        self.assertTrue(result['segments'])
        self.store.rebuild_segments(source['id'])
        self.assertTrue(self.index.status()['dirty'])
        self.assertTrue(self.index.search('跨考',unit_id='u1',year=2027,allow_remote=True)['segments'])

    def test_new_same_family_version_during_build_blocks_stale_activation(self):
        saved=self.save();self.index.build()
        active=self.index.status()['active_space_id']
        def replace():
            # Same family/year but a genuinely different extracted text: the two
            # complete versions must conflict and leave the old vectors unservable.
            self.save(raw_suffix='tampered-version',extra='跨专业考生须加试三门课程。')
        self.embedding.after_embed=replace
        # Force a real provider round so the mid-build change is actually observed.
        import shutil
        shutil.rmtree(self.root/'runtime/vector-index/cache')
        self.backend.rows.clear()
        with self.assertRaises(KnowledgeError):self.index.build()
        self.assertEqual(self.index.status()['active_space_id'],active)
        self.assertTrue(self.index.status()['dirty'])
        self.assertEqual(self.index.search('跨考',unit_id='u1',year=2027,allow_remote=True)['segments'],[])

    # ---- planning 3: lease, takeover, async identity ---------------------------------
    def test_expired_lease_takeover_rejects_the_stale_attempt(self):
        metadata={'fingerprint':'a'*64,'provider':'fixture','model':'fixture-v1','dimension':3}
        space,_=begin_build(self.store,metadata,'ep_fixture','vb_test')
        items=[{'id':identifier('vi_',space['id'],'sg',0,10,'0'*64),'segment_id':'sg','entry_id':'k_x',
                'generation_id':'g','input_sha':'0'*64,'char_start':0,'char_end':10}]
        register_items(self.store,space['id'],items)
        stale,_=claim(self.store,space['id'],items[0]['id'],'vb_test')
        with vector_connection(self.store,True) as handle:
            handle.execute('UPDATE knowledge_vector_jobs SET lease_until=? WHERE space_id=? AND item_id=?',
                           (time.time()-1,space['id'],items[0]['id']))
        fresh,_=claim(self.store,space['id'],items[0]['id'],'vb_test')
        self.assertNotEqual(stale,fresh)
        self.assertFalse(finish_item(self.store,space['id'],items[0],'succeeded',stale))
        self.assertFalse(finish_item(self.store,space['id'],items[0],'succeeded','wrong-token'))
        self.assertTrue(finish_item(self.store,space['id'],items[0],'succeeded',fresh))

    def test_start_build_reports_the_persisted_build_id_and_cancel_stops_embeddings(self):
        self.save()
        started,release=threading.Event(),threading.Event()
        self.embedding.gate=(started,release)
        result=self.index.start_build()
        self.assertTrue(result['started'] and started.wait(10))
        status=self.index.status()
        self.assertTrue(status['building'])
        self.assertEqual(status['build_id'],result['build_id'])
        calls=len(self.embedding.calls)
        cancelled=self.index.cancel_build()
        self.assertEqual(cancelled['state'],'cancelling')
        self.assertEqual(cancelled['build_id'],result['build_id'])
        release.set()
        self.assertTrue(self.wait_idle())
        self.assertEqual(len(self.embedding.calls),calls)
        self.assertIsNone(self.index.status()['active_space_id'])
        self.assertEqual(self.index.cancel_build()['state'],'not_running')

    def test_concurrent_build_is_rejected_visibly(self):
        self.save()
        started,release=threading.Event(),threading.Event()
        self.embedding.gate=(started,release)
        self.assertTrue(self.index.start_build()['started'])
        self.assertTrue(started.wait(10))
        other=VectorIndex(self.store,self.settings,self.embedding,lambda:self.backend)
        self.addCleanup(other.close)
        with self.assertRaises(KnowledgeError) as caught:
            other.build()
        self.assertEqual(caught.exception.code,'vector_build_running')
        release.set()
        self.assertTrue(self.wait_idle())

    # ---- planning 4: unknown usage never becomes a fake zero -------------------------
    def test_provider_timeout_keeps_usage_unknown_not_fake_zero(self):
        self.save();self.embedding.fail=True;self.embedding.usage_known=False
        with self.assertRaises(KnowledgeError):self.index.build()
        last=self.index.status()['last_build']
        self.assertFalse(last['usage_known'])
        self.assertEqual(last['usage_unknown_calls'],1)
        self.assertEqual(last['input_tokens'],0)
        self.assertEqual(self.index.status()['jobs']['uncertain'],1)

    def test_missing_usage_in_a_successful_response_stays_unknown(self):
        self.save();self.embedding.usage_known=False;self.embedding.usage={}
        result=self.index.build()
        self.assertEqual(result['status'],'ready')
        last=self.index.status()['last_build']
        self.assertFalse(last['usage_known'])
        self.assertEqual(last['embedded_inputs'],last['usage_unknown_calls'])

    def test_provider_usage_survives_a_vector_write_failure(self):
        self.save();self.backend.fail=True
        with self.assertRaises(KnowledgeError):self.index.build()
        last=self.index.status()['last_build']
        self.assertEqual(last['input_tokens'],1)
        self.assertTrue(last['usage_known'])
        with vector_connection(self.store) as handle:
            row=handle.execute("SELECT state,usage,usage_known FROM knowledge_vector_jobs WHERE state='failed'").fetchone()
        self.assertEqual(json.loads(row['usage'])['input_tokens'],1)
        self.backend.fail=False
        self.index.build()
        last=self.index.status()['last_build']
        self.assertEqual((last['cache_hits'],last['input_tokens'],last['usage_known']),(1,2,True))
        self.assertEqual(self.index.status()['status'],'ready')

    # ---- planning 5: incremental rebuild and store-loss recovery ---------------------
    def test_repeat_build_reuses_cache_and_recovers_after_store_loss(self):
        self.save();self.index.build()
        calls=len(self.embedding.calls);upserts=self.backend.upserts
        self.index.build()
        self.assertEqual((len(self.embedding.calls),self.backend.upserts),(calls,upserts))
        self.backend.rows.clear()
        self.index.build()
        self.assertEqual(len(self.embedding.calls),calls)
        self.assertGreater(self.backend.upserts,upserts)
        self.assertTrue(self.index.search('跨考',unit_id='u1',year=2027,allow_remote=True)['segments'])

    # ---- planning 6: window tiling and input binding ---------------------------------
    def test_windows_must_tile_the_segment_and_bind_the_actual_model_input(self):
        self.save()
        for mode in ('gap','sha','text','empty'):
            with self.subTest(mode=mode):
                self.embedding.prepare_mode=mode
                with self.assertRaises(KnowledgeError):self.index.build()
                self.assertIsNone(self.index.status()['active_space_id'])
                self.backend.rows.clear()
        self.embedding.prepare_mode='split3'
        self.assertEqual(self.index.build()['status'],'ready')

    # ---- planning 7: adversarial scope, batching, remote gate ------------------------
    def test_remote_search_requires_explicit_action_before_any_inference(self):
        self.save();self.index.build()
        calls=len(self.embedding.calls)
        with self.assertRaises(KnowledgeError) as caught:
            self.index.search('跨考')
        self.assertEqual(caught.exception.code,'vector_remote_requires_action')
        self.assertEqual(len(self.embedding.calls),calls)

    def test_batched_allowlist_merge_keeps_the_best_parent_window(self):
        self.embedding.prepare_mode='split3'
        first=self.save();second=self.save('two','u2')
        self.index.build()
        previous=knowledge_vectors.BATCH_SIZE;knowledge_vectors.BATCH_SIZE=2
        self.addCleanup(setattr,knowledge_vectors,'BATCH_SIZE',previous)
        result=self.index.search('任意查询',limit=10,allow_remote=True)
        self.assertEqual(result['candidate_count'],18)
        self.assertEqual(len(self.backend.queries),9)
        self.assertTrue(all(len(batch)<=2 for batch in self.backend.queries))
        self.assertEqual(len(result['segments']),6)
        # Re-derive each segment's best window independently (same normalized query and
        # window vectors as the facade/backend compute) and confirm the global merge
        # kept exactly that window, not merely the best inside one batch.
        query_raw=self.embedding.vector_for('任意查询')
        query_norm=sum(value*value for value in query_raw)**0.5
        query=[value/query_norm for value in query_raw]
        for segment in result['segments']:
            span=segment['embedding_span']
            start,end=segment['char_start'],segment['char_end']
            self.assertTrue(start<=span['char_start']<span['char_end']<=end)
            matched=segment['text'][span['char_start']-start:span['char_end']-start]
            self.assertTrue(matched)
            text=segment['text'];cut=max(1,len(text)//3)
            windows=[(start,start+cut),(start+cut,start+2*cut),(start+2*cut,end)]
            def score(bounds):
                raw=self.embedding.vector_for(text[bounds[0]-start:bounds[1]-start])
                norm=sum(value*value for value in raw)**0.5
                return sum(a*b for a,b in zip((value/norm for value in raw),query))
            best=max(windows,key=score)
            self.assertEqual((span['char_start'],span['char_end']),best)
            self.assertEqual(segment['match_mode'],'semantic')
        self.assertEqual({row['entry_id'] for row in result['segments']},{first['id'],second['id']})

    def test_same_length_tampered_original_is_not_served(self):
        saved=self.save();self.index.build()
        queries=len(self.backend.queries)
        blob=self.store._blob_path(saved['sha256'])
        raw=bytearray(blob.read_bytes());raw[-1]=raw[-1]^1
        blob.write_bytes(bytes(raw))
        self.assertEqual(self.index.search('跨考',unit_id='u1',year=2027,allow_remote=True)['segments'],[])
        self.assertEqual(len(self.backend.queries),queries)
        self.assertTrue(self.index.status()['dirty'])

    # ---- planning 8: dedicated directory boundary and cache hygiene ------------------
    def _junction(self,link,target):
        done=subprocess.run(['cmd','/c','mklink','/J',str(link),str(target)],capture_output=True)
        return done.returncode==0 and link.exists()

    def test_outward_junction_is_rejected_before_any_write(self):
        outside=tempfile.TemporaryDirectory(prefix='n4-outside-');self.addCleanup(outside.cleanup)
        external=Path(outside.name)/'target';external.mkdir()
        (self.root/'runtime').mkdir()
        link=self.root/'runtime/vector-index'
        if not self._junction(link,external):
            self.skipTest('此系统不允许创建目录联接')
            return
        self.addCleanup(lambda:subprocess.run(['cmd','/c','rmdir',str(link)],capture_output=True))
        with self.assertRaises(ValueError):VectorCache(self.root)
        from milvus_backend import MilvusBackend
        with self.assertRaises(ValueError):MilvusBackend(self.root)
        with self.assertRaises(KnowledgeError):
            VectorIndex(self.store,self.settings,self.embedding,lambda:self.backend)
        self.assertEqual(list(external.iterdir()),[])

    def test_query_vectors_never_enter_the_persistent_cache(self):
        self.save();self.index.build()
        space=self.index.status()['active_space_id']
        cached=list((self.root/'runtime/vector-index/cache'/space).glob('*.bin'))
        self.assertEqual(len(cached),3)
        self.assertTrue(self.index.search('跨考',unit_id='u1',year=2027,allow_remote=True)['segments'])
        self.assertEqual(list((self.root/'runtime/vector-index/cache'/space).glob('*.bin')),cached)
        corrupted=cached[0];corrupted.write_bytes(corrupted.read_bytes()[:-1])
        self.index.cache.__init__(self.root)
        self.assertEqual(self.index.cache.get(space,corrupted.stem,3),None)

    # ---- rework round 1: committed content, usage facts, explicit tables ------------
    def served(self):
        return {row['id'] for row in self.index.search('跨考',year=2027,limit=100,allow_remote=True)['segments']}

    def test_n3_save_does_not_create_vector_tables(self):
        self.save()
        with closing(sqlite3.connect(self.store.path)) as connection:
            tables=[row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'knowledge_vector_%'")]
        self.assertEqual(tables,[])

    def test_same_space_cancel_preserves_the_complete_previous_serving_set(self):
        self.save();self.save('two','u2')
        self.index.build()
        baseline=self.served();active=self.index.status()['active_space_id']
        self.assertTrue(baseline)
        shutil.rmtree(self.root/'runtime/vector-index/cache')
        started,release=threading.Event(),threading.Event()
        self.embedding.gate=(started,release)
        stopped=threading.Event();errors=[]
        def run():
            try:self.index.build(stop_event=stopped)
            except KnowledgeError as error:errors.append(error)
        worker=threading.Thread(target=run);worker.start()
        self.assertTrue(started.wait(10))
        stopped.set();release.set();worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors and errors[0].code=='vector_cancelled')
        self.assertEqual(self.index.status()['active_space_id'],active)
        self.assertEqual(self.served(),baseline)

    def test_failed_same_space_rebuild_preserves_the_complete_previous_serving_set(self):
        self.save();self.save('two','u2')
        self.index.build()
        baseline=self.served()
        class FailingCache:
            def get(self,*args):return None
            def put(self,*args):raise OSError('disk full')
        self.index.cache=FailingCache()
        with self.assertRaises(KnowledgeError):self.index.build()
        self.assertEqual(self.served(),baseline)

    def test_cache_write_failure_keeps_the_known_provider_receipt(self):
        self.save();self.index.build()
        class FailingCache:
            def get(self,*args):return None
            def put(self,*args):raise OSError('disk full')
        self.index.cache=FailingCache()
        with self.assertRaises(KnowledgeError):self.index.build()
        last=self.index.status()['last_build']
        self.assertTrue(last['usage_known'])
        self.assertEqual(last['input_tokens'],1)
        self.assertEqual(last['embedded_inputs'],1)
        with closing(sqlite3.connect(self.store.path)) as connection:
            rows=connection.execute("SELECT usage,usage_known FROM knowledge_vector_jobs WHERE state IN ('failed','uncertain')").fetchall()
        self.assertTrue(rows)
        self.assertEqual(rows[0][1],1)
        self.assertEqual(json.loads(rows[0][0])['input_tokens'],1)

    def test_ann_failure_after_embedding_keeps_request_usage(self):
        self.save();self.index.build();self.backend.search_fail=True
        calls=len(self.embedding.calls)
        with self.assertRaises(KnowledgeError) as caught:
            self.index.search('跨考',year=2027,allow_remote=True)
        self.assertEqual(caught.exception.code,'vector_unavailable')
        usage=caught.exception.retrieval_usage
        self.assertEqual(usage['embedding_calls'],1)
        self.assertEqual(usage['successful_embedding_calls'],1)
        self.assertEqual(usage['input_tokens'],1)
        self.assertTrue(usage['usage_known'])
        self.assertEqual(usage['stage'],'ann')
        self.assertEqual(len(self.embedding.calls),calls+1)
        encoded=json.dumps(usage,ensure_ascii=False)
        self.assertNotIn('sk-fixture',encoded)
        self.assertNotIn('跨考',encoded)

    def test_provider_timeout_keeps_attempted_call_with_unknown_usage(self):
        self.save();self.index.build();self.embedding.fail=True
        queries=len(self.backend.queries)
        with self.assertRaises(KnowledgeError) as caught:
            self.index.search('跨考',year=2027,allow_remote=True)
        usage=caught.exception.retrieval_usage
        self.assertEqual(usage['embedding_calls'],1)
        self.assertEqual(usage['successful_embedding_calls'],0)
        self.assertIsNone(usage['input_tokens'])
        self.assertFalse(usage['usage_known'])
        self.assertEqual(len(self.backend.queries),queries)

    def test_cancel_after_embedding_return_keeps_known_usage_and_cancels(self):
        self.save();self.index.build()
        stopped=threading.Event();self.embedding.after_embed=stopped.set
        queries=len(self.backend.queries)
        with self.assertRaises(KnowledgeError) as caught:
            self.index.search('跨考',year=2027,allow_remote=True,stop_event=stopped)
        self.assertEqual(caught.exception.code,'vector_cancelled')
        usage=caught.exception.retrieval_usage
        self.assertEqual(usage['successful_embedding_calls'],1)
        self.assertEqual(usage['input_tokens'],1)
        self.assertTrue(usage['usage_known'])
        self.assertEqual(len(self.backend.queries),queries)


    def test_existing_vector_table_migrates_committed_without_revoking_success(self):
        temporary=tempfile.TemporaryDirectory(prefix='n4-vector-migrate-')
        self.addCleanup(temporary.cleanup)
        database=Path(temporary.name)/'knowledge.sqlite'
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("""CREATE TABLE knowledge_vector_items(space_id TEXT NOT NULL,item_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,entry_id TEXT NOT NULL,generation_id TEXT NOT NULL,input_sha TEXT NOT NULL,
                char_start INTEGER NOT NULL,char_end INTEGER NOT NULL,state TEXT NOT NULL,updated_at TEXT NOT NULL,
                PRIMARY KEY(space_id,item_id))""")
            connection.execute("INSERT INTO knowledge_vector_items VALUES('vs_a','vi_a','sg_a','k_a','kg_a','sha',0,1,'succeeded','now')")
            connection.execute("INSERT INTO knowledge_vector_items VALUES('vs_a','vi_b','sg_b','k_b','kg_b','sha',0,1,'failed','now')")
            connection.commit()
            from knowledge_vector_schema import ensure_vector_schema
            ensure_vector_schema(connection);ensure_vector_schema(connection)
            committed=dict(connection.execute("SELECT item_id,committed FROM knowledge_vector_items"))
        self.assertEqual(committed,{'vi_a':1,'vi_b':0})


from embedding_transport import EmbeddingTransport
from embedding_catalog import LOCAL_REVISION, checked_embedding_config

MODEL_DIR=ROOT/'data/runtime/embedding-models/bge-small-zh-v1.5'/LOCAL_REVISION


@unittest.skipUnless(MODEL_DIR.is_dir(),'pinned local model not prepared in this workspace')
class LocalModelPipelineTests(unittest.TestCase):
    """prepare → real local BGE embedding → real Milvus Lite → authoritative segment."""

    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='n4-vectors-local-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.store=KnowledgeStore(self.root)
        config=checked_embedding_config({'provider':'local_bge','profile_id':'ep_local_pipeline'})
        class LocalSettings:
            @staticmethod
            def runtime_embedding(profile_id=None):
                if profile_id not in (None,'ep_local_pipeline'):
                    raise KnowledgeError('embedding_profile_unavailable','该向量配置已不可用，请重新连接。',503)
                return deepcopy(config)
        self.settings=LocalSettings()
        self.embedding=EmbeddingTransport(ROOT/'data')
        self.index=VectorIndex(self.store,self.settings,self.embedding)
        self.addCleanup(self.index.close)

    def save(self):
        body='经研究决定，2027年我校硕士研究生招生继续接受跨学科考生报考，'+ \
             '初试科目设置与往年保持一致，复试将综合考查专业基础与外语能力。'*12
        text='2027年硕士研究生招生专业目录\n081200 计算机科学与技术\n初试科目101、201、301、408。\n\n'+ \
             body+'除专项计划外，退役大学生士兵专项计划考生不享受以上加试的豁免政策🙂。'
        raw=('%PDF-1.4\nlocal pipeline fixture').encode()
        source={'url':'https://grad.example.edu.cn/local-pipeline.pdf','title':'2027年硕士招生专业目录',
                'unit_id':'u1','unit_name':'合成大学','year':2027,'status':'official_original_read',
                'sha256':hashlib.sha256(raw).hexdigest()}
        return self.store.save_source(source,document={'raw':raw,'text':text,'pages':[{'page':1,'text':text}],
                    'url':source['url'],'attribution_status':'verified'})

    def test_real_chain_serves_authoritative_tail_window(self):
        source=self.save()
        result=self.index.build()
        self.assertEqual(result['status'],'ready')
        status=self.index.status()
        self.assertEqual(status['active_provider'],'local_bge')
        self.assertTrue(status['last_build']['usage_known'])
        self.assertGreater(status['last_build']['embedded_inputs'],0)
        hit=self.index.search('退役大学生士兵专项计划是否豁免加试',unit_id='u1',year=2027)
        self.assertTrue(hit['segments'])
        self.assertTrue(hit['usage_known'])
        segment=hit['segments'][0]
        self.assertEqual(segment['entry_id'],source['id'])
        span=segment['embedding_span']
        start,end=segment['char_start'],segment['char_end']
        self.assertTrue(start<=span['char_start']<span['char_end']<=end)
        matched=segment['text'][span['char_start']-start:span['char_end']-start]
        # the winning window sits in the tail half and is exact original text
        self.assertGreater(span['char_end'],(start+end)//2)
        self.assertIn('专项',matched)
        # windows tile the whole segment; query vectors never persist
        cached=list((self.root/'runtime/vector-index/cache').rglob('*.bin'))
        self.assertGreater(len(cached),0)
        self.index.search('初试科目',unit_id='u1',year=2027)
        self.assertEqual(list((self.root/'runtime/vector-index/cache').rglob('*.bin')),cached)



if __name__=='__main__':
    unittest.main()
