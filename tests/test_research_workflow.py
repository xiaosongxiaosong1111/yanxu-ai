"""Deterministic research workflow regression; all stores and models are local fixtures."""
from copy import deepcopy
import json
import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

from grad_engine import validate_profile
from research_service import ResearchService
from research_tools import ResearchTools
from knowledge_store import KnowledgeStore
from tests.test_research_service import FakeAdmission, FakeAI, FakeSettings, wait_done


class DiverseAdmission(FakeAdmission):
    def __init__(self):
        super().__init__()
        original=self.rows[0]
        self.units=[{'id':str(index),'name':f'学校{index}','province':'天津' if index==1 else '北京',
                     'program_count':30 if index==1 else 2,'official_url':f'https://s{index}.edu.cn'} for index in range(1,5)]
        self.rows=[{**deepcopy(original),'id':f'{unit["id"]}-{index}','unit_id':unit['id'],'unit_name':unit['name']}
                   for unit in self.units for index in range(unit['program_count'])]
        self.requests=[]

    def catalog_programs(self,**query):
        self.requests.append(query)
        rows=[r for r in self.rows if not query.get('unit_id') or r['unit_id']==query['unit_id']]
        start=query.get('offset',0)
        return {'programs':deepcopy(rows[start:start+query.get('limit',20)]),'total':len(rows),'revision':'fixture-r1'}


class FakeKnowledge:
    def __init__(self):self.saved=[];self.entries=[];self.failure=False;self.searches=[]
    def save_source(self,source,**kwargs):
        if self.failure:raise OSError('private database detail')
        self.saved.append((deepcopy(source),kwargs))
        accepted=source.get('status') in {'published_snapshot','official_original_read'} and type(source.get('year')) is int
        quality={'version':1,'state':'accepted' if accepted else 'pending','reusable':accepted,'material_type':'catalog','reasons':[]}
        return {**source,'id':'k_'+'a'*24,'saved':True,'quality':quality,'original_available':source.get('status')=='official_original_read'}
    def search(self,**kwargs):
        self.searches.append(kwargs)
        return {'entries':deepcopy(self.entries),'total':len(self.entries),'summary':{}}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)

    def service(self,messages,admission=None,profile=None):
        ai=FakeAI(messages)
        service=ResearchService(self.data,admission or FakeAdmission(),FakeSettings(),ai)
        self.addCleanup(service.close)
        task=service.create({'query':'想考计算机','profile':profile or {}})
        return service,ai,task

    def test_candidates_always_receive_real_algorithm_and_actionable_unknown_questions(self):
        service,ai,task=self.service([
            {'role':'assistant','tool_calls':[{'id':'one','name':'find_candidates','arguments':{'query':'计算机'}}]},
            {'role':'assistant','content':'结束。'}])
        done=wait_done(service,task['id'])
        report=done['report']
        self.assertIn('matching',report['algorithm'])
        self.assertEqual(report['algorithm']['matching']['summary']['input_count'],1)
        self.assertNotIn('routes',report['algorithm'])
        self.assertTrue(any(q['field']=='subjects' for q in report['questions']))
        self.assertLessEqual(len(report['questions']),3)
        comparison=next(s for s in report['skills'] if s['id']=='compare-plans')
        self.assertEqual(comparison['status'],'used')
        self.assertTrue(any(e.get('data',{}).get('invocation')=='system' for e in done['events']))
        self.assertEqual(done['profile']['subjects'],[])
        self.assertEqual(len(ai.calls),2)

    def test_token_budget_partial_keeps_comparison_and_questions_without_more_model_calls(self):
        ai=FakeAI([])
        ai.chat=Mock(return_value={'message':{'role':'assistant','tool_calls':[{'id':'one','name':'find_candidates','arguments':{'query':'计算机'}}]},'usage':{'total_tokens':50000}})
        service=ResearchService(self.data,FakeAdmission(),FakeSettings(),ai)
        self.addCleanup(service.close)
        second=service.create({'query':'计算机'})
        done=wait_done(service,second['id'])
        self.assertEqual(done['status'],'partial')
        self.assertIn('matching',done['report']['algorithm'])
        self.assertTrue(done['report']['questions'])
        self.assertEqual(done['usage']['model_calls'],1)

    def test_complete_prepared_subjects_enable_real_route_calculation(self):
        prepared=[{'code':code,'name':code,'scope':'national','exam_unit':index} for index,code in enumerate(('101','201','301','408'),1)]
        service,_,task=self.service([
            {'role':'assistant','tool_calls':[{'id':'one','name':'find_candidates','arguments':{'query':'计算机'}}]},
            {'role':'assistant','content':'结束。'}],profile={'subjects':prepared})
        done=wait_done(service,task['id'])
        self.assertIn('routes',done['report']['algorithm'])
        self.assertEqual(len(done['report']['algorithm']['routes']['routes']),3)

    def test_broad_candidates_round_robin_schools_before_repeating_directions(self):
        admission=DiverseAdmission()
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],'reference_year':2027,'target_year':2028}
        tools=ResearchTools(admission,FakeSettings(),task,self.data,knowledge_store=FakeKnowledge())
        result=tools.find_candidates({'query':'计算机'})
        self.assertEqual(len(tools.programs),12)
        self.assertEqual(len({r['unit_id'] for r in result['candidates']}),4)
        self.assertLessEqual(len(admission.requests),8)
        self.assertTrue(all(r.get('limit',0)<=200 for r in admission.requests))
        before=deepcopy(task)
        task['profile']=validate_profile({'provinces':['天津']})
        other=ResearchTools(admission,FakeSettings(),task,self.data,knowledge_store=FakeKnowledge())
        self.assertEqual({r['unit_id'] for r in other.find_candidates({'query':'计算机'})['candidates']},{'1'})
        self.assertEqual(before['profile']['provinces'],[])

    def test_turn_events_and_readiness_do_not_inherit_old_completed_algorithms(self):
        service,ai,task=self.service([{'role':'assistant','content':'完成。'}])
        done=wait_done(service,task['id'])
        while task['id'] in service._controls:time.sleep(.005)
        self.assertEqual(done['turn_id'],1)
        self.assertTrue(service.get(task['id'])['can_continue'])
        service.store.update(task['id'],report={'algorithm':{'matching':{'summary':{'input_count':99}}},'skills':[{'status':'used'}]})
        entered=threading.Event();release=threading.Event()
        def blocking(*args,**kwargs):
            entered.set();release.wait(2)
            return {'message':{'role':'assistant','content':'完成。'}}
        ai.chat=blocking
        again=service.continue_research({'research_id':task['id'],'message':'再查一下'})
        self.assertEqual(again['turn_id'],2)
        self.assertFalse(again['can_continue'])
        self.assertNotIn('matching',again['report'].get('algorithm',{}))
        self.assertTrue(entered.wait(1))
        cancelled=service.cancel({'research_id':task['id']})
        self.assertFalse(cancelled['can_continue']);release.set()
        for _ in range(200):
            if service.get(task['id'])['can_continue']:break
            time.sleep(.005)
        self.assertTrue(service.get(task['id'])['can_continue'])
        events=service.get(task['id'])['events']
        self.assertEqual({e['turn_id'] for e in events},{1,2})

    def test_knowledge_cache_is_reused_without_a_network_read_or_program_promotion(self):
        cache=FakeKnowledge();cache.entries=[{'id':'k_'+'a'*24,'title':'已留档官网原文','url':'https://grad.example.edu.cn/catalog',
            'unit_id':'123','year':2027,'status':'official_original_read','excerpt':'计算机官方目录摘录','checked_at':'2026-11-01',
            'sha256':'a'*64,'original_available':True,'field_verification':'not_structured',
            'quality':{'version':1,'state':'accepted','reusable':True,'material_type':'catalog','reasons':[]}}]
        task={'id':'r_'+'1'*32,'query':'计算机','profile':validate_profile({}),'unit_ids':[],'reference_year':2027,'target_year':2028}
        network=Mock()
        tools=ResearchTools(FakeAdmission(),FakeSettings(),task,self.data,knowledge_store=cache,network=network)
        result=tools.run('search_knowledge',{'query':'计算机'})
        self.assertTrue(result['results'][0]['cache_hit'])
        self.assertEqual(tools.usage['documents'],0);self.assertEqual(tools.usage['searches'],0)
        self.assertEqual(tools.report()['candidates'],[])
        self.assertEqual(tools.report()['knowledge']['cache_hits'],1)
        network.request_json.assert_not_called()

    def test_knowledge_write_failure_is_visible_without_losing_source(self):
        cache=FakeKnowledge();cache.failure=True
        task={'id':'r_'+'1'*32,'query':'计算机','profile':validate_profile({}),'unit_ids':[],'reference_year':2027,'target_year':2028}
        network=Mock();network.request_json.return_value={'Response':{'Pages':[{'title':'目录','url':'https://grad.example.edu.cn/catalog','passage':'计算机目录'}]}}
        settings=FakeSettings();settings.yuanbao_key=lambda:'fixture-key'
        tools=ResearchTools(FakeAdmission(),settings,task,self.data,knowledge_store=cache,network=network)
        result=tools.search_sources({'query':'计算机','unit_id':'123'})
        self.assertEqual(len(result['results']),1)
        self.assertFalse(result['results'][0]['knowledge_saved'])
        self.assertTrue(tools.report()['knowledge']['errors'])
        self.assertNotIn('private database detail',str(tools.report()))

    def test_real_store_unknown_year_search_lead_stays_pending_instead_of_reusable_cache(self):
        store=KnowledgeStore(self.data)
        entry=store.save_source({'title':'计算机招生目录','url':'https://grad.example.edu.cn/catalog','unit_id':'123',
                                'year':None,'requested_year':2027,'status':'search_lead','excerpt':'计算机招生官方线索'})
        task={'id':'r_'+'2'*32,'query':'计算机','profile':validate_profile({}),'unit_ids':[],'reference_year':2027,'target_year':2028}
        network=Mock()
        tools=ResearchTools(FakeAdmission(),FakeSettings(),task,self.data,knowledge_store=store,network=network)
        result=tools.run('search_knowledge',{'query':'计算机'})
        self.assertEqual(result['results'],[])
        saved=store.get(entry['id'])
        self.assertEqual(saved['quality']['state'],'pending')
        self.assertFalse(saved['quality']['reusable'])
        self.assertIsNone(saved['year'])
        self.assertEqual(tools.report()['knowledge']['cache_hits'],0)
        self.assertEqual(tools.report()['knowledge']['errors'],[])
        self.assertEqual(tools.usage['searches'],0);self.assertEqual(tools.usage['documents'],0)
        network.request_json.assert_not_called()

    def test_cache_consumer_defensively_rejects_pending_excluded_and_forged_accepted_leads(self):
        store=FakeKnowledge()
        accepted={'version':1,'state':'accepted','reusable':True,'material_type':'catalog','reasons':[]}
        base={'url':'https://grad.example.edu.cn/catalog','title':'计算机专业目录','unit_id':'123','year':2027,
              'status':'official_original_read','original_available':True,'excerpt':'可复用目录正文','quality':accepted}
        store.entries=[{**base,'id':'k_a'},
            {**base,'id':'k_pending','excerpt':'PENDING_BODY','quality':{**accepted,'state':'pending','reusable':False}},
            {**base,'id':'k_excluded','excerpt':'EXCLUDED_BODY','quality':{**accepted,'state':'excluded','reusable':False}},
            {**base,'id':'k_false','excerpt':'FALSE_REUSABLE','quality':{**accepted,'reusable':False}},
            {**base,'id':'k_missing','excerpt':'MISSING_QUALITY','quality':None},
            {**base,'id':'k_invalid','excerpt':'INVALID_QUALITY','quality':{**accepted,'version':False}},
            {**base,'id':'k_lead','excerpt':'FORGED_LEAD','status':'search_lead'},
            {**base,'id':'k_broken','excerpt':'MISSING_ORIGINAL','original_available':False},
            {**base,'id':'k_year','excerpt':'UNKNOWN_YEAR','year':None,'requested_year':2027},
            {**base,'id':'k_published','url':'https://grad.example.edu.cn/published','status':'published_snapshot'}]
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],'reference_year':2027,'target_year':2028}
        tools=ResearchTools(FakeAdmission(),FakeSettings(),task,self.data,knowledge_store=store)
        result=tools.search_knowledge({'query':'计算机'})
        self.assertEqual(store.searches[0]['quality'],'accepted')
        self.assertEqual({r['knowledge_id'] for r in result['results']},{'k_a','k_published'})
        self.assertEqual(tools.report()['knowledge']['cache_hits'],2)
        model=ResearchService._model_result(result)
        for forbidden in ('PENDING_BODY','EXCLUDED_BODY','FALSE_REUSABLE','MISSING_QUALITY','INVALID_QUALITY','FORGED_LEAD','MISSING_ORIGINAL','UNKNOWN_YEAR'):
            self.assertNotIn(forbidden,model)

    def test_pending_source_is_saved_once_per_identity_but_not_counted_as_accepted(self):
        store=FakeKnowledge()
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],'reference_year':2027,'target_year':2028}
        tools=ResearchTools(FakeAdmission(),FakeSettings(),task,self.data,knowledge_store=store)
        sid=tools._source({'url':'https://grad.example.edu.cn/catalog','title':'计算机目录','status':'search_lead','excerpt':'只有摘要，等待原文核对'})
        tools._save_source(sid);tools._save_source(sid)
        source=tools.sources[sid];summary=tools.report()['knowledge']
        self.assertTrue(source['knowledge_saved']);self.assertFalse(source['knowledge_quality']['reusable'])
        self.assertEqual(source['knowledge_quality']['state'],'pending')
        self.assertEqual((summary['saved_count'],summary['accepted_count'],summary['pending_count'],summary['filtered_count']),(1,0,1,0))
        self.assertEqual(summary['errors'],[])

    def test_excluded_source_is_an_expected_filter_and_never_enters_model_body_or_retry_loop(self):
        store=FakeKnowledge()
        quality={'version':1,'state':'excluded','reusable':False,'material_type':'navigation',
                 'reasons':[{'code':'navigation_noise','message':'只有导航链接，没有招生正文。'}]}
        store.save_source=Mock(return_value={'id':None,'saved':False,'quality':quality})
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],'reference_year':2027,'target_year':2028}
        tools=ResearchTools(FakeAdmission(),FakeSettings(),task,self.data,knowledge_store=store)
        sid=tools._source({'url':'https://grad.example.edu.cn/nav','title':'学校招生导航','status':'search_lead',
                           'excerpt':'FILTERED_BODY_MARKER','quote':'FILTERED_EXTRA_QUOTE'})
        tools._save_source(sid);tools._save_source(sid)
        source=tools.sources[sid];summary=tools.report()['knowledge']
        self.assertFalse(source['knowledge_saved']);self.assertTrue(source['knowledge_filtered'])
        self.assertEqual(source['knowledge_quality'],quality)
        self.assertIn('只有导航链接',source['knowledge_filter_reason'])
        self.assertEqual(summary['errors'],[]);self.assertEqual(summary['filtered_count'],1)
        self.assertEqual(summary['saved_count'],0)
        self.assertEqual(store.save_source.call_count,1)
        self.assertNotIn('FILTERED_BODY_MARKER',ResearchService._model_result({'results':[source]}))
        self.assertNotIn('FILTERED_EXTRA_QUOTE',ResearchService._model_result({'results':[source]}))
        self.assertEqual(source['url'],'https://grad.example.edu.cn/nav')

    def test_knowledge_quality_totals_count_unique_records_and_latest_state(self):
        store=FakeKnowledge()
        accepted={'version':1,'state':'accepted','reusable':True,'material_type':'catalog','reasons':[]}
        pending={**accepted,'state':'pending','reusable':False}
        excluded={**accepted,'state':'excluded','reusable':False,'reasons':[{'code':'noise','message':'导航噪声'}]}
        store.save_source=Mock(side_effect=[{'id':'k_one','saved':True,'quality':pending},
            {'id':'k_one','saved':True,'quality':accepted},{'id':'k_two','saved':True,'quality':pending},
            {'id':None,'saved':False,'quality':excluded}])
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],'reference_year':2027,'target_year':2028}
        tools=ResearchTools(FakeAdmission(),FakeSettings(),task,self.data,knowledge_store=store)
        sources=[tools._source({'url':f'https://grad.example.edu.cn/{index}','title':'招生目录','status':'search_lead'}) for index in range(3)]
        for sid in (sources[0],sources[0],sources[1],sources[2]):tools._save_source(sid)
        summary=tools.report()['knowledge']
        self.assertEqual((summary['accepted_count'],summary['pending_count'],summary['filtered_count'],summary['saved_count']),(1,1,1,2))
        self.assertEqual(summary['errors'],[])

    def test_search_and_read_filter_keep_metadata_without_retrying_duplicate_results(self):
        store=FakeKnowledge()
        quality={'version':1,'state':'excluded','reusable':False,'material_type':'access_gate',
                 'reasons':[{'code':'access_gate','message':'页面只有访问门禁，没有招生正文。'}]}
        store.save_source=Mock(return_value={'id':None,'saved':False,'quality':quality})
        lead={'title':'招生资料入口','url':'https://grad.example.edu.cn/gate','passage':'FILTERED_SEARCH_BODY'}
        network=Mock();network.request_json.return_value={'Response':{'Pages':[lead,lead]}}
        raw=b'fixture access page'
        fetcher=Mock(return_value={'raw':raw,'sha256':hashlib.sha256(raw).hexdigest(),'url':lead['url'],
            'attribution_status':'verified','document_title':'2027年招生资料入口','document_heading':'2027年招生资料入口',
            'text':'FILTERED_READ_BODY','pages':[],'checked_at':'2026-11-02'})
        settings=FakeSettings();settings.yuanbao_key=lambda:'fixture-key'
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],'reference_year':2027,'target_year':2028}
        tools=ResearchTools(FakeAdmission(),settings,task,self.data,knowledge_store=store,network=network,source_fetcher=fetcher)
        found=tools.search_sources({'query':'招生目录','unit_id':'123'})
        self.assertEqual(store.save_source.call_count,1)
        self.assertNotIn('FILTERED_SEARCH_BODY',ResearchService._model_result(found))
        source_id=found['results'][0]['id']
        read=tools.read_source({'source_id':source_id,'unit_id':'123'})
        self.assertEqual(store.save_source.call_count,2)
        self.assertEqual(store.save_source.call_args.kwargs['document']['text'],'FILTERED_READ_BODY')
        self.assertEqual(read['status'],'official_original_read');self.assertEqual(read['url'],lead['url'])
        self.assertTrue(read['knowledge_filtered']);self.assertIsNone(read['knowledge_id'])
        self.assertNotIn('FILTERED_READ_BODY',ResearchService._model_result(read))
        self.assertNotIn('FILTERED_READ_BODY',str(tools.report()))
        tools.read_source({'source_id':source_id,'unit_id':'123'})
        fetcher.assert_called_once()
        self.assertEqual(store.save_source.call_count,2)
        self.assertEqual(tools.report()['knowledge']['filtered_count'],1)
        self.assertEqual(tools.report()['knowledge']['errors'],[])

    def test_search_reuses_existing_canonical_record_without_promoting_incoming_excerpt(self):
        store=KnowledgeStore(self.data)
        official={'title':'示例大学2027年硕士招生专业目录','url':'https://grad.example.edu.cn/catalog','unit_id':'123',
            'unit_name':'示例大学','year':2027,'requested_year':2027,'status':'published_snapshot',
            'excerpt':'081200计算机科学与技术，初试科目为101思想政治理论、201英语一、301数学一、408计算机学科专业基础。',
            'checked_at':'2026-10-01'}
        canonical=store.save_source(official)
        self.assertEqual(canonical['quality']['state'],'accepted')
        network=Mock();network.request_json.return_value={'Response':{'Pages':[{
            'title':'访问验证','url':official['url'],'passage':'INCOMING_JUNK 请完成人机验证后继续访问'}]}}
        settings=FakeSettings();settings.yuanbao_key=lambda:'fixture-key'
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],'reference_year':2027,'target_year':2028}
        tools=ResearchTools(FakeAdmission(),settings,task,self.data,knowledge_store=store,network=network)
        result=tools.search_sources({'query':'计算机目录','unit_id':'123'})
        source=result['results'][0]
        self.assertEqual(source['status'],'published_snapshot')
        self.assertEqual(source['year'],2027);self.assertEqual(source['title'],official['title'])
        self.assertEqual(source['checked_at'],'2026-10-01')
        self.assertEqual(source['excerpt'],official['excerpt'])
        self.assertEqual(source['knowledge_id'],canonical['id'])
        self.assertTrue(source['cache_hit']);self.assertEqual(source['cached_at'],'2026-10-01')
        self.assertEqual(tools.usage['searches'],2);self.assertEqual(tools.usage['documents'],0)
        self.assertEqual(network.request_json.call_count,2)
        self.assertEqual(result['request_count'],2)
        self.assertEqual(result['target_status'],'navigation_only')
        self.assertEqual(tools.report()['knowledge']['cache_hits'],1)
        self.assertNotIn('INCOMING_JUNK',ResearchService._model_result(result))
        self.assertEqual(tools.report()['candidates'],[])
        # The store also keeps a stronger same-version record when only the body weakens.
        weak=tools._source({**official,'sha256':'','title':'访问验证','excerpt':'SAME_STATUS_JUNK 请完成人机验证后继续访问'})
        tools._save_source(weak)
        self.assertEqual(tools.sources[weak]['excerpt'],official['excerpt'])
        self.assertNotIn('SAME_STATUS_JUNK',ResearchService._model_result(tools.sources[weak]))
        self.assertEqual(tools.report()['knowledge']['cache_hits'],1)

    def test_new_published_save_is_not_reported_as_a_cache_hit(self):
        store=KnowledgeStore(self.data)
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],'reference_year':2027,'target_year':2028}
        tools=ResearchTools(FakeAdmission(),FakeSettings(),task,self.data,knowledge_store=store)
        sid=tools._source({'title':'2027年硕士招生专业目录','url':'https://grad.example.edu.cn/new','unit_id':'123',
            'year':2027,'status':'published_snapshot','excerpt':'081200计算机科学与技术，101思想政治理论、201英语一、301数学一、408计算机学科专业基础。'})
        tools._save_source(sid)
        self.assertEqual(tools.sources[sid]['knowledge_quality']['state'],'accepted')
        self.assertEqual(tools.report()['knowledge']['cache_hits'],0)
        self.assertFalse(tools.sources[sid].get('cache_hit',False))

    def test_comparison_recomputes_when_later_candidates_arrive_and_does_not_consume_tool_budget(self):
        admission=DiverseAdmission()
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],'reference_year':2027,'target_year':2028}
        tools=ResearchTools(admission,FakeSettings(),task,self.data,knowledge_store=FakeKnowledge())
        tools.find_candidates({'query':'计算机','unit_id':'2'})
        self.assertEqual(tools.ensure_algorithms(),['compare_candidates','analyze_decisions'])
        self.assertEqual(tools.algorithm['matching']['summary']['input_count'],2)
        first=tools.usage['algorithm_calls']
        self.assertEqual(tools.ensure_algorithms(),[])
        self.assertEqual(tools.usage['algorithm_calls'],first)
        tools.find_candidates({'query':'计算机','unit_id':'3'})
        tools.ensure_algorithms()
        self.assertEqual(tools.algorithm['matching']['summary']['input_count'],4)
        self.assertEqual(tools.usage['tool_calls'],0)
        task['profile']=validate_profile({'education':'bachelor'})
        self.assertEqual(tools.ensure_algorithms(),['compare_candidates','analyze_decisions'])

    def test_relative_year_confirmation_is_explicit_and_does_not_mutate_default_year(self):
        task={'query':'想明年考计算机','profile':validate_profile({}),'unit_ids':[],
              'reference_year':2027,'target_year':2028,'confirmed_fields':[]}
        tools=ResearchTools(FakeAdmission(),FakeSettings(),task,self.data,knowledge_store=FakeKnowledge())
        self.assertEqual(tools.questions()[0]['field'],'target_year')
        self.assertEqual(tools.questions()[0]['options'][0]['value'],2028)
        self.assertEqual(task['profile']['target_year'],2028)
        task['confirmed_fields']=['target_year']
        self.assertFalse(any(q['field']=='target_year' for q in tools.questions()))

    def test_context_compression_keeps_tool_pairs_and_returns_bounded_excerpts(self):
        service,_,task=self.service([{'role':'assistant','content':'完成。'}])
        wait_done(service,task['id'])
        tools=ResearchTools(FakeAdmission(),FakeSettings(),{'query':'q','profile':validate_profile({}),
            'unit_ids':[],'reference_year':2027,'target_year':2028},self.data,knowledge_store=FakeKnowledge())
        base=[{'role':'system','content':'规则'},{'role':'user','content':'需求'}]
        history=[*base]
        for index in range(4):
            history.extend([{'role':'assistant','tool_calls':[{'id':str(index)}]},
                            {'role':'tool','tool_call_id':str(index),'content':'资料摘要'}])
        compressed=service._context(base,history,tools)
        self.assertEqual(compressed[-3:-1],history[-2:])
        self.assertIn('next_actions',json.loads(compressed[-1]['content'])['research_plan'])
        self.assertLess(len(compressed),len(history))
        self.assertLessEqual(len(service._model_result({'text':'原文'*20000,'rows':[{'text':'条目'*900}]*50})),6000)
        self.assertIn('截断',service._model_result({'text':'原文'*20000}))

    def test_token_limit_prevents_new_paid_tools_but_still_runs_local_comparison(self):
        ai=FakeAI([])
        ai.chat=Mock(return_value={'message':{'role':'assistant','tool_calls':[
            {'id':'find','name':'find_candidates','arguments':{'query':'计算机'}},
            {'id':'search','name':'search_official_sources','arguments':{'query':'计算机目录'}}]},'usage':{'total_tokens':50000}})
        service=ResearchService(self.data,FakeAdmission(),FakeSettings(),ai);self.addCleanup(service.close)
        done=wait_done(service,service.create({'query':'计算机'})['id'])
        self.assertEqual(done['status'],'partial')
        self.assertEqual(done['usage']['searches'],0)
        self.assertIn('matching',done['report']['algorithm'])
        self.assertTrue(any('停止新增联网' in gap for gap in done['report']['gaps']))

    def test_personal_roster_excerpts_do_not_enter_public_research_or_model_summary(self):
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],
              'reference_year':2027,'target_year':2028}
        tools=ResearchTools(FakeAdmission(),FakeSettings(),task,self.data,knowledge_store=FakeKnowledge())
        sid=tools._source({'url':'https://grad.example.edu.cn/notice','title':'2027年拟录取名单',
                          'excerpt':'姓名 考生编号 总分 张某某 123456789 350','status':'search_lead'})
        self.assertEqual(tools.sources[sid]['excerpt'],'')
        self.assertNotIn('张某某',str(tools.report()))
        self.assertEqual(tools.sources[sid]['text_origin'],'metadata_only')

    def test_catalog_sidebar_roster_links_do_not_hide_real_exam_subject_excerpt(self):
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],
              'reference_year':2027,'target_year':2028}
        tools=ResearchTools(FakeAdmission(),FakeSettings(),task,self.data,knowledge_store=FakeKnowledge())
        excerpt='2027年硕士研究生招生专业目录\n计算机技术 101思想政治理论 201英语一 301数学一 408计算机学科专业基础\n相关链接：2026年拟录取名单、复试名单、录取结果公示'
        sid=tools._source({'url':'https://grad.example.edu.cn/catalog','title':'2027年硕士研究生招生专业目录',
                          'excerpt':excerpt,'status':'search_lead'})
        self.assertEqual(tools.sources[sid]['excerpt'],excerpt)
        self.assertIn('408计算机学科专业基础',str(tools.report()))
        self.assertNotEqual(tools.sources[sid].get('text_origin'),'metadata_only')

    def test_generic_notice_with_actual_identity_table_remains_metadata_only(self):
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],
              'reference_year':2027,'target_year':2028}
        tools=ResearchTools(FakeAdmission(),FakeSettings(),task,self.data,knowledge_store=FakeKnowledge())
        sid=tools._source({'url':'https://grad.example.edu.cn/results','title':'学院研究生招生工作公告',
                          'excerpt':'姓名\n考生编号\n初试总分\n张某某 100001234567890 350','status':'search_lead'})
        self.assertEqual(tools.sources[sid]['excerpt'],'')
        self.assertNotIn('张某某',str(tools.report()))

    def test_successful_retry_clears_a_resolved_knowledge_save_warning(self):
        store=FakeKnowledge();store.failure=True
        task={'query':'计算机','profile':validate_profile({}),'unit_ids':[],
              'reference_year':2027,'target_year':2028}
        tools=ResearchTools(FakeAdmission(),FakeSettings(),task,self.data,knowledge_store=store)
        sid=tools._source({'url':'https://grad.example.edu.cn/catalog','title':'目录','status':'search_lead'})
        tools._save_source(sid)
        self.assertTrue(tools.report()['knowledge']['errors'])
        store.failure=False;tools._save_source(sid)
        self.assertEqual(tools.report()['knowledge']['errors'],[])
        self.assertFalse(any('保存尚未完成' in gap for gap in tools.report()['gaps']))

    def test_transfer_skill_loads_when_it_is_requested_in_a_later_turn(self):
        service,ai,task=self.service([{'role':'assistant','content':'完成。'}])
        wait_done(service,task['id'])
        while task['id'] in service._controls:time.sleep(.005)
        ai.messages=[{'role':'assistant','content':'完成。'}]
        service.continue_research({'research_id':task['id'],'message':'想看看调剂规则'})
        done=wait_done(service,task['id'])
        transfer=next(item for item in done['report']['skills'] if item['id']=='assess-transfer-options')
        self.assertTrue(transfer['loaded'])

    def test_unknown_conditions_pause_inside_a_batch_before_later_paid_searches(self):
        ai=FakeAI([{'role':'assistant','tool_calls':[
            {'id':'find','name':'find_candidates','arguments':{'query':'计算机'}},
            {'id':'search1','name':'search_official_sources','arguments':{'query':'计算机目录'}},
            {'id':'search2','name':'search_official_sources','arguments':{'query':'计算机招生'}},
            {'id':'search3','name':'search_official_sources','arguments':{'query':'计算机专业课'}}]}])
        settings=FakeSettings();settings.yuanbao_key=lambda:'fixture-key'
        network=Mock();network.request_json.return_value={'Response':{'Pages':[]}}
        with patch('ai_transport.AITransport',return_value=network):
            service=ResearchService(self.data,FakeAdmission(),settings,ai);self.addCleanup(service.close)
            done=wait_done(service,service.create({'query':'计算机'})['id'])
        self.assertEqual(done['status'],'awaiting_input')
        self.assertEqual(done['usage']['model_calls'],1)
        self.assertEqual(done['usage']['searches'],1)
        self.assertEqual(done['usage']['tool_calls'],2)
        self.assertEqual(network.request_json.call_count,1)
        self.assertTrue(done['report']['questions'])
        self.assertIn('matching',done['report']['algorithm'])

