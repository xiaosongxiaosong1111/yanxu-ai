from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch

from grad_engine import validate_profile
from research_tools import ResearchTools,LIMITS,gated_resolver
from research_store import ResearchError
from public_sources import SourceError
from tests.test_research_service import FakeAdmission,FakeSettings


class ResearchToolsTests(unittest.TestCase):
    def setUp(self):
        environment=patch.dict('os.environ',{'GRAD_SEARCH_API_KEY':''})
        environment.start();self.addCleanup(environment.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)/'data';self.data.mkdir()
        self.admission=FakeAdmission();self.settings=FakeSettings()
        self.task={'query':'本科，准备408，优先天津','follow_ups':[],'profile':validate_profile({}),
                   'unit_ids':[],'reference_year':2027,'target_year':2028}
        self.network=Mock()
        raw=b'official source bytes'
        self.fetcher=Mock(return_value={'url':'https://grad.example.edu.cn/catalog','raw':raw,'sha256':hashlib.sha256(raw).hexdigest(),
                        'document_title':'2027年招生目录','document_heading':'2027年招生目录','text':'学校官方原文',
                        'pages':[{'page':1,'text':'学校官方原文'}],'attribution_status':'verified','checked_at':'2026-11-02'})
        self.tools=ResearchTools(self.admission,self.settings,self.task,self.data,network=self.network,source_fetcher=self.fetcher)

    def search_response(self):
        self.settings.yuanbao_key=lambda:'fake-service-key'
        self.network.request_json.return_value={'Response':{'Pages':[
            {'title':'官方目录','url':'https://grad.example.edu.cn/catalog','passage':'2027目录'},
            {'title':'广告','url':'https://ads.invalid/x'},'not-json',{'url':'javascript:alert(1)'}],
            'RequestId':'r123'}}

    def test_unit_lookup_and_candidate_scope_have_honest_counts(self):
        result=self.tools.run('find_units',{'query':'测试','province':'天津'})
        self.assertEqual(result['total'],1)
        result=self.tools.run('find_candidates',{'query':'408','unit_id':'123'})
        self.assertEqual(result['candidates'][0]['id'],'p1');self.assertFalse(result['national_complete'])
        self.assertEqual(result['verification_mode'],'published_snapshot')
        self.assertEqual(self.tools.report()['coverage']['candidate_count'],1)

    def test_identical_repeated_calls_are_reused_without_extra_budget(self):
        self.search_response()
        first=self.tools.run('search_official_sources',{'query':'目录'})
        calls=self.network.request_json.call_count
        second=self.tools.run('search_official_sources',{'query':'目录'})
        self.assertNotIn('repeated_call',first)
        self.assertTrue(second['repeated_call'])
        self.assertIn('未重复消耗',second['notice'])
        self.assertEqual(second['results'],first['results'])
        self.assertEqual(self.network.request_json.call_count,calls)
        self.assertEqual(self.tools.usage['searches'],1)
        other=self.tools.run('search_official_sources',{'query':'招生简章'})
        self.assertNotIn('repeated_call',other)
        self.assertEqual(self.network.request_json.call_count,calls+1)

    def test_natural_language_candidate_query_retries_terms_and_labels_the_relaxation(self):
        class LiteralSpaceAdmission(FakeAdmission):
            def catalog_programs(self,**query):
                if ' ' in str(query.get('q','')).strip():
                    return {'programs':[],'total':0,'revision':'fixture-r1'}
                return super().catalog_programs(**query)
        tools=ResearchTools(LiteralSpaceAdmission(),self.settings,self.task,self.data,network=self.network,source_fetcher=self.fetcher)
        self.assertEqual(ResearchTools._relaxed_terms('南开大学 计算机 408 考研',('南开大学',)),['408','计算机','南开大学'])
        self.assertEqual(ResearchTools._relaxed_terms('南开大学计算机408考研',('南开大学',))[:3],['408','计算机','南开大学'])
        result=tools.find_candidates({'query':'计算机 408 考研','unit_id':'123'})
        self.assertTrue(result['query_relaxed'])
        self.assertEqual(result['requested_query'],'计算机 408 考研')
        self.assertEqual(result['effective_query'],'408')
        self.assertEqual([row['id'] for row in result['candidates']],['p1'])
        self.assertIn('放宽检索',result['notice'])
        self.assertTrue(tools.candidate_metrics['query_relaxed'])
        selection=tools.report()['coverage']['selection']
        self.assertEqual(selection['requested_query'],'计算机 408 考研')
        self.assertEqual(selection['effective_query'],'408')
        national=ResearchTools(LiteralSpaceAdmission(),self.settings,
                               {**self.task,'unit_ids':[],'profile':{**self.task['profile'],'provinces':[]}},
                               self.data,network=self.network,source_fetcher=self.fetcher)
        widened=national.find_candidates({'query':'计算机 408 考研'})
        self.assertTrue(widened['query_relaxed'])
        self.assertEqual(widened['scope_mode'],'national_query')
        self.assertEqual([row['id'] for row in widened['candidates']],['p1'])

    def test_scope_and_arguments_do_not_expand_silently(self):
        self.task['unit_ids']=['other']
        for name,args in [('find_candidates',{'query':'408','unit_id':'123'}),('find_candidates',{'query':'x','extra':True}),
                          ('no_such_tool',{}),('find_units',{'query':3})]:
            with self.subTest(name=name),self.assertRaises(ResearchError):self.tools.run(name,args)

    def test_yuanbao_search_keeps_only_matching_public_sources_and_never_personal_queries(self):
        self.search_response()
        result=self.tools.run('search_official_sources',{'query':'硕士专业目录','unit_id':'123'})
        self.assertEqual(len(result['results']),1);self.assertEqual(result['results'][0]['status'],'search_lead')
        self.assertIsNone(result['results'][0]['year']);self.assertEqual(result['request_id'],'r123')
        args=self.network.request_json.call_args.args
        self.assertEqual(args[0]['provider'],'yuanbao');self.assertEqual(args[2],'')
        self.assertNotIn('本科',args[3]['Query'])
        for query in ['电话13812345678','email@example.com']:
            with self.assertRaises(ResearchError):self.tools.search_sources({'query':query})

    def test_missing_search_key_bad_response_and_budget(self):
        with self.assertRaises(ResearchError):self.tools.search_sources({'query':'目录'})
        self.search_response();self.network.request_json.return_value={'Response':{'Error':{'Code':'AuthFailure'},'Pages':[]}}
        with self.assertRaises(ResearchError):self.tools.search_sources({'query':'目录'})
        self.network.request_json.return_value={'Response':{'Pages':[]}}
        self.tools.usage['searches']=LIMITS['max_searches']
        with self.assertRaises(ResearchError):self.tools.search_sources({'query':'目录'})
        with self.assertRaises(ResearchError):self.tools.search_sources({'query':'目录','year':True})

    def test_only_this_project_environment_key_is_an_explicit_fallback(self):
        self.network.request_json.return_value={'Response':{'Pages':[]}}
        with patch.dict('os.environ',{'GRAD_SEARCH_API_KEY':'fixture-project-key'}):
            self.tools.search_sources({'query':'目录'})
        self.assertEqual(self.network.request_json.call_args.args[0]['api_key'],'fixture-project-key')

    def test_broad_search_is_still_a_lead_and_never_a_candidate(self):
        self.search_response()
        result=self.tools.search_sources({'query':'计算机 硕士目录'})
        self.assertEqual(len(result['results']),1);self.assertEqual(self.tools.report()['candidates'],[])
        self.assertIsNone(result['results'][0]['unit_id'])

    def test_original_read_is_saved_but_never_adopted_as_a_program(self):
        self.search_response();sid=self.tools.search_sources({'query':'目录','unit_id':'123'})['results'][0]['id']
        result=self.tools.run('read_official_source',{'source_id':sid,'unit_id':'123'})
        self.assertEqual(result['status'],'official_original_read');self.assertEqual(result['year'],2027)
        self.assertTrue((self.data/'runtime/research-sources'/f"{result['sha256']}.bin").is_file())
        self.assertEqual(self.tools.report()['candidates'],[])
        self.tools.read_source({'source_id':sid,'unit_id':'123'})
        self.assertEqual(self.fetcher.call_count,1)
        self.assertNotIn('local_path',str(result));self.assertNotIn('raw',result)

    def test_wrong_identity_bad_hash_and_chsi_gate_are_rejected(self):
        for source in [{'url':'https://other.edu.cn/catalog','unit_id':'123','year':2027},
                       {'url':'https://yz.chsi.com.cn/zsml/index.htm','unit_id':'123','year':2027}]:
            sid=self.tools._source(source)
            with self.assertRaises(ResearchError):self.tools.read_source({'source_id':sid,'unit_id':'123'})
        self.fetcher.assert_not_called()
        sid=self.tools._source({'url':'https://grad.example.edu.cn/catalog','unit_id':'123','year':2027})
        self.fetcher.return_value={**self.fetcher.return_value,'sha256':'0'*64}
        with self.assertRaises(ResearchError):self.tools.read_source({'source_id':sid,'unit_id':'123'})
        with self.assertRaises(ResearchError):self.tools.read_source({'source_id':'s_unknown','unit_id':'123'})
        with patch('research_tools.socket.getaddrinfo') as resolver:
            with self.assertRaises(SourceError):gated_resolver('yz.chsi.com.cn',443)
            resolver.assert_not_called()
            gated_resolver('grad.example.edu.cn',443);resolver.assert_called_once()

    def test_document_and_tool_budgets_are_enforced_before_new_calls(self):
        sid=self.tools._source({'url':'https://grad.example.edu.cn/catalog','unit_id':'123','year':2027})
        self.tools.usage['documents']=LIMITS['max_documents']
        with self.assertRaises(ResearchError):self.tools.read_source({'source_id':sid,'unit_id':'123'})
        self.fetcher.assert_not_called()
        self.tools.usage['tool_calls']=LIMITS['max_tool_calls']
        with self.assertRaises(ResearchError):self.tools.run('read_knowledge',{})

    def test_actual_algorithms_cannot_consume_model_fabricated_program_ids(self):
        self.tools.find_candidates({'query':'计算机'})
        matching=self.tools.run('compare_candidates',{})
        self.assertEqual(matching['summary']['input_count'],1)
        routes=self.tools.run('plan_subject_routes',{})
        self.assertEqual(len(routes['routes']),3)
        self.tools.run('suggest_adjustments',{'allowed_changes':{'max_subject_changes':[0,1,2]}})
        self.assertIn('adjustments',self.tools.algorithm)
        with self.assertRaises(ResearchError):self.tools.compare({'program_ids':['invented']})
        for changes in ({},{'education':['bachelor']},{'max_subject_changes':[0,1,2,3,4]}):
            with self.subTest(changes=changes),self.assertRaises((ResearchError,ValueError)):
                self.tools.adjustments({'allowed_changes':changes})

    def test_decision_support_uses_real_candidates_and_stays_bounded(self):
        self.tools.find_candidates({'query':'408'})
        result=self.tools.run('analyze_decisions',{'top_k':3})
        self.assertEqual(result['version'],'decision_v1')
        self.assertEqual(result['value_of_information']['top_k'],3)
        self.assertLessEqual(len(result['stability']['scenarios']),8)
        self.assertIn('decision_intelligence',self.tools.algorithm)
        report=self.tools.report()
        self.assertIn('decision_intelligence',report)
        self.assertEqual(report['evidence_ledger']['version'],'evidence_ledger_v1')
        self.assertEqual(report['evidence_ledger']['summary']['claim_count'],
                         sum(len(row['reasons']) for row in report['decisions']))
        with self.assertRaises(ResearchError):self.tools.run('analyze_decisions',{'top_k':0})
        with self.assertRaises(ResearchError):self.tools.run('analyze_decisions',{'scenarios':[{'name':'x','changes':{'invented':1}}]})
        with self.assertRaises(ResearchError):self.tools.run('analyze_decisions',{'scenarios':[{'name':'x','changes':{'provinces':['天津']}}]*9})

    def test_unresolved_exam_records_are_excluded_from_computation(self):
        self.admission.rows[0]['exam_matching_supported']=False
        self.tools.find_candidates({'query':'408'})
        result=self.tools.compare({})
        self.assertEqual(result['summary']['input_count'],0)
        self.assertTrue(any('待核' in gap for gap in self.tools.gaps))

    def test_knowledge_topics_are_bounded_and_years_explicit(self):
        result=self.tools.run('read_knowledge',{'topic':'years'})
        self.assertEqual(result['rules'][0]['target_year'],2028)
        self.assertEqual(result['rules'][0]['reference_year'],2027)
        with self.assertRaises(ResearchError):self.tools.knowledge({'topic':'../../secret'})

    def test_condition_proposal_uses_user_quotes_and_does_not_change_profile(self):
        before=deepcopy(self.task['profile'])
        proposal=self.tools.run('propose_profile',{'updates':{'provinces':['天津']},'evidence':[{'field':'provinces','quote':'优先天津'}]})
        self.assertTrue(proposal['requires_confirmation']);self.assertEqual(proposal['updates'],{'provinces':['天津']})
        self.assertEqual(self.task['profile'],before)
        self.assertEqual(self.tools.report()['profile_proposal'],proposal)
        for payload in [{'updates':{'education':'bachelor'},'evidence':[{'field':'education','quote':'我已经本科毕业'}]},
                        {'updates':{'education':'bachelor'},'evidence':[]},
                        {'updates':{'secret':'x'},'evidence':[{'field':'secret','quote':'本科'}]}]:
            with self.subTest(payload=payload),self.assertRaises((ResearchError,ValueError)):self.tools.propose_profile(payload)

    def test_cancellation_and_deadline_reach_search_transport(self):
        self.search_response();stop=threading.Event();self.tools.stop_event=stop
        self.tools.search_sources({'query':'目录'})
        self.assertIs(self.network.request_json.call_args.kwargs['stop_event'],stop)
        before=self.network.request_json.call_count;stop.set()
        with self.assertRaises(ResearchError):self.tools.run('search_official_sources',{'query':'目录'})
        self.assertEqual(self.network.request_json.call_count,before)
        stop.clear();self.tools.deadline=time.monotonic()-1
        with self.assertRaises(ResearchError):self.tools.run('read_knowledge',{})

    def test_cancelled_original_is_not_saved_as_read(self):
        stop=threading.Event();self.tools.stop_event=stop
        sid=self.tools._source({'url':'https://grad.example.edu.cn/catalog','unit_id':'123','year':2027})
        result=self.fetcher.return_value
        def cancelled(*args,**kwargs):stop.set();return result
        self.tools.source_fetcher=cancelled
        with self.assertRaises(ResearchError):self.tools.read_source({'source_id':sid,'unit_id':'123'})
        self.assertNotEqual(self.tools.sources[sid].get('status'),'official_original_read')
        self.assertFalse((self.data/'runtime/research-sources').exists())
