"""N4 bounded hybrid retrieval never treats ANN payload as original evidence."""
from copy import deepcopy
import threading
import unittest

from knowledge_store import KnowledgeError
from knowledge_retrieval import HybridRetriever, reciprocal_rank_fusion, rule_rerank


QUALITY={'version':1,'state':'accepted','reusable':True}


def record(number,body,unit='u',year=2027):
    entry='k_'+f'{number:024x}';extraction='ex_'+f'{number:032x}';generation='kg_'+f'{number:032x}'
    source={'id':entry,'url':'https://example.edu.cn/catalog.pdf','title':'招生目录','unit_id':unit,'year':year,
        'quality':deepcopy(QUALITY),'validity':'active','usable':True,'original_available':True,
        'status':'official_original_read','sha256':'a'*64,'extraction':{'id':extraction,'generation_id':generation}}
    return {'id':'sg_'+f'{number:032x}','entry_id':entry,'extraction_id':extraction,'generation_id':generation,
        'text':body,'char_start':100,'char_end':100+len(body),'page_start':21,'page_end':21,
        'format':'pdf','page_kind':'physical','section_path':['计算机学院'],'context_spans':[],'source':source}


class Store:
    def __init__(self,rows,keyword=None):
        self.rows={row['id']:deepcopy(row) for row in rows};self.keyword=keyword or list(self.rows);self.calls=[]
    def get(self,identifier):
        return next(deepcopy(row['source']) for row in self.rows.values() if row['entry_id']==identifier)
    def search_segments(self,**kwargs):
        self.calls.append(kwargs)
        rows=[deepcopy(self.rows[key]) for key in self.keyword if all(not kwargs.get(field) or
              self.rows[key]['source'].get(field)==kwargs[field] for field in ('unit_id','year'))
              and (not kwargs.get('entry_id') or self.rows[key]['entry_id']==kwargs['entry_id'])]
        return {'segments':rows,'total':len(rows),'match_mode':'segment_terms'}
    def read_segment(self,identifier,**kwargs):
        return {'segment':deepcopy(self.rows[identifier]),'contexts':[],'neighbors':[]}


class Index:
    def __init__(self,rows=(),error=None):self.rows=list(rows);self.error=error;self.calls=[]
    def search(self,query,**kwargs):
        self.calls.append((query,kwargs))
        if self.error:raise self.error
        return {'segments':deepcopy(self.rows),'space_id':'vs_fixture','usage':{'embedding_calls':1,'usage_known':False},'latency_ms':2}


class HybridTests(unittest.TestCase):
    def test_rrf_uses_both_ranks_equal_weights_and_deduplicates_within_channel(self):
        a,b,c=record(1,'甲'),record(2,'乙'),record(3,'丙')
        result=reciprocal_rank_fusion({'keyword':[a,b,a],'semantic':[b,c]})
        self.assertEqual([item['id'] for item in result],[b['id'],a['id'],c['id']])
        self.assertAlmostEqual(result[0]['rrf_score'],1/62+1/61)
        self.assertEqual(result[0]['ranks'],{'keyword':2,'semantic':1})

    def test_hybrid_hydrates_text_and_position_from_current_store_not_ann(self):
        a,b=record(1,'计算机考试科目301'),record(2,'计算机研究方向')
        store=Store([a,b],[a['id']]);index=Index([{**b,'text':'VECTOR_PAYLOAD_CANARY','char_start':0}])
        value=HybridRetriever(store,index).retrieve('计算机',unit_id='u',year=2027,limit=8,allow_remote=True)
        self.assertEqual(value['retrieval']['mode'],'hybrid')
        self.assertEqual(value['returned_count'],2);self.assertEqual(value['candidate_count'],2)
        self.assertNotIn('VECTOR_PAYLOAD_CANARY',str(value))
        self.assertEqual(next(row for row in value['segments'] if row['id']==b['id'])['char_start'],100)
        self.assertEqual(index.calls[0][1]['allow_remote'],True)
        self.assertEqual(store.calls[0]['quality'],'accepted')

    def test_unavailable_index_is_keyword_with_honest_safe_reason_and_zero_fake_usage(self):
        row=record(1,'计算机招生')
        for code in ['embedding_not_configured','vector_not_ready','vector_remote_requires_action','vector_unavailable']:
            index=Index(error=KnowledgeError(code,'private-key-canary C:/private'))
            value=HybridRetriever(Store([row]),index).retrieve('计算机')
            self.assertEqual(value['retrieval']['mode'],'keyword');self.assertTrue(value['retrieval']['degraded'])
            self.assertEqual(value['retrieval']['degraded_reason'],code)
            self.assertEqual(value['retrieval']['usage'],{})
            self.assertNotIn('private-key-canary',str(value))

    def test_scope_quality_generation_and_privacy_are_rechecked_after_ann(self):
        valid,foreign,old,pending,private=[record(n,'计算机招生') for n in range(1,6)]
        foreign['source']['unit_id']='other';old['generation_id']='kg_'+'f'*32
        pending['source']['quality']={'version':1,'state':'pending','reusable':False}
        private['source']['text_origin']='metadata_only'
        rows=[valid,foreign,old,pending,private];store=Store(rows,[valid['id']])
        result=HybridRetriever(store,Index(rows)).retrieve('计算机',unit_id='u',year=2027)
        self.assertEqual([row['id'] for row in result['segments']],[valid['id']])

    def test_revoked_or_hash_unavailable_source_is_not_reintroduced_from_ann(self):
        for update in [{'validity':'revoked'},{'validity':'conflict'},{'validity':'superseded'},
                       {'usable':False},{'original_available':False}]:
            row=record(1,'OLD_BODY_CANARY');row['source'].update(update)
            result=HybridRetriever(Store([row]),Index([row])).retrieve('计算机')
            self.assertEqual(result['segments'],[]);self.assertNotIn('OLD_BODY_CANARY',str(result))

    def test_math_codes_are_not_interchanged_and_negative_408_does_not_exclude_whole_document(self):
        one,two,avoid=record(1,'计算机专业考试301数学一'),record(2,'计算机专业考试302数学二'),record(3,'不考408，业务课为814。')
        store=Store([one,two,avoid],[one['id'],two['id'],avoid['id']])
        result=HybridRetriever(store,Index()).retrieve('计算机 301')
        self.assertNotIn(two['id'],[row['id'] for row in result['segments']])
        other=HybridRetriever(store,Index()).retrieve('不考408')
        self.assertIn(avoid['id'],[row['id'] for row in other['segments']])

    def test_rule_rank_is_recomputable_and_candidate_count_is_not_national_total(self):
        rows=[record(n,'计算机招生'+str(n)) for n in range(1,5)]
        result=HybridRetriever(Store(rows),Index(rows[::-1])).retrieve('计算机招生',limit=2)
        self.assertEqual(result['returned_count'],2);self.assertEqual(result['candidate_count'],4)
        self.assertTrue(result['retrieval']['partial_scope']);self.assertNotIn('total',result)
        self.assertEqual(result['retrieval']['reranker'],'rule_v2')
        for row in result['segments']:
            rule=row['rerank']
            self.assertAlmostEqual(row['retrieval_score'],sum(rule[key] for key in ('rrf','phrase','codes','coverage','window','bigrams','window_bigrams','novelty','novel_bigrams','heading','entry','overlap','redundancy')),7)

    def test_rerank_prefers_a_window_with_new_terms_and_strips_internal_window_text(self):
        def candidate(identifier,ordinal,window):
            return {'id':identifier,'entry_id':'k_'+'a'*24,'ordinal':ordinal,'text':'计算机',
                    '_window_text':window,'rrf_score':0.,'source':{},'section_path':[]}
        first=candidate('sg_'+'1'*32,10,'计算机 085404')
        repeat=candidate('sg_'+'2'*32,20,'计算机 085404')
        fresh=candidate('sg_'+'3'*32,30,'计算机 301')
        result=rule_rerank('计算机 085404 301',[first,repeat,fresh],2)
        self.assertEqual([row['id'] for row in result],['sg_'+'1'*32,'sg_'+'3'*32])
        self.assertTrue(all('_window_text' not in row for row in result))
        self.assertGreater(result[1]['rerank']['novelty'],0)

    def test_rerank_penalises_overlapping_read_windows_in_the_same_original(self):
        def candidate(identifier,ordinal):
            return {'id':identifier,'entry_id':'k_'+'b'*24,'ordinal':ordinal,'text':'计算机',
                    '_window_text':'计算机','rrf_score':0.,'source':{},'section_path':[]}
        near=candidate('sg_'+'4'*32,10);overlap=candidate('sg_'+'5'*32,11);far=candidate('sg_'+'6'*32,40)
        result=rule_rerank('计算机',[near,overlap,far],2)
        self.assertEqual([row['id'] for row in result],['sg_'+'4'*32,'sg_'+'6'*32])

    def test_limits_private_queries_cancel_and_closing_fail_before_embedding(self):
        row=record(1,'计算机');index=Index([row]);retriever=HybridRetriever(Store([row]),index)
        for args in [{'query':'13812345678'},{'query':'sk-123456789abcdef'},{'query':'计算机','limit':True},
                     {'query':'计算机','limit':21},{'query':'计算机','year':True}]:
            with self.subTest(args=args),self.assertRaises(KnowledgeError):retriever.retrieve(**args)
        stop=threading.Event();stop.set()
        with self.assertRaises(KnowledgeError):retriever.retrieve('计算机',stop_event=stop)
        retriever.close()
        with self.assertRaises(KnowledgeError):retriever.retrieve('计算机')
        self.assertEqual(index.calls,[])


if __name__=='__main__':unittest.main()
