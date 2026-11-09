from copy import deepcopy
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import Mock
from unittest.mock import patch

from provider_catalog import TransportError
from research_service import ResearchService, ResearchError


class FakeAdmission:
    def __init__(self):
        self.units=[{'id':'123','name':'测试大学','province':'天津','official_url':'https://grad.example.edu.cn'}]
        self.rows=[{'id':'p1','unit_id':'123','unit_name':'测试大学','year':2027,'name':'计算机科学与技术',
                    'code':'081200','department':'计算机学院','direction':'00不区分方向','study_mode':'全日制',
                    'subjects':[{'code':'408','name':'计算机学科专业基础','scope':'national','exam_unit':4}],
                    'requires_review':True,'eligibility_evidence_complete':False,'tuition':None}]
    def catalog_bootstrap(self):return {'units':deepcopy(self.units),'revision':'fixture-r1','meta':{'reference_year':2027,'target_year':2028},'coverage':{'catalog_complete':False}}
    def catalog_programs(self,**query):return {'programs':deepcopy(self.rows),'total':1,'revision':'fixture-r1'}


class FakeSettings:
    def __init__(self,enabled=True):self.enabled=enabled
    def runtime_ai(self):
        if not self.enabled:raise ValueError('not configured')
        return {'provider':'fixture','model':'fixture-model','api_key':'secret-test-not-for-output'}
    def yuanbao_key(self):return ''


class FakeAI:
    def __init__(self,messages):self.messages=list(messages);self.calls=[]
    def chat(self,config,messages,tools=None,stop_event=None):
        self.calls.append(deepcopy(messages))
        return {'message':self.messages.pop(0),'usage':{'total_tokens':12}}


def wait_done(service,identity):
    for _ in range(150):
        task=service.get(identity)
        if task['status'] not in {'queued','running'}:return task
        time.sleep(.01)
    raise AssertionError('finite fixture task did not finish')


class ResearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)/'data';self.data.mkdir()
    def make(self,messages=(),enabled=True):
        ai=FakeAI(messages);settings=FakeSettings(enabled)
        service=ResearchService(self.data,FakeAdmission(),settings=settings,ai_client=ai)
        self.addCleanup(service.close)
        return service,ai,settings

    def test_no_configuration_saves_request_without_fake_generation(self):
        service,ai,_=self.make(enabled=False)
        task=service.create({'query':'我想考计算机'})
        self.assertEqual(task['status'],'waiting_configuration');self.assertEqual(ai.calls,[])
        self.assertIn('配置',task['events'][0]['message'])
        self.assertNotIn('secret-test',str(service.get(task['id'])))

    def test_real_tool_loop_uses_local_candidates_and_deterministic_algorithm(self):
        messages=[{'role':'assistant','content':'','tool_calls':[{'id':'c1','name':'find_candidates','arguments':{'query':'计算机','unit_id':'123'}}]},
                  {'role':'assistant','content':'','tool_calls':[{'id':'c2','name':'compare_candidates','arguments':{'program_ids':['p1']}}]},
                  {'role':'assistant','content':'仍需补齐报考条件。','tool_calls':[]}]
        service,ai,_=self.make(messages)
        task=wait_done(service,service.create({'query':'我想考计算机','unit_ids':['123']})['id'])
        self.assertEqual(task['status'],'awaiting_input');self.assertEqual(task['report']['candidates'][0]['id'],'p1')
        self.assertIn('matching',task['report']['algorithm'])
        self.assertEqual(task['report']['algorithm']['matching']['summary']['input_count'],1)
        self.assertFalse(task['report']['coverage']['national_complete'])
        self.assertEqual(len(ai.calls),3);self.assertIn('tool',str(ai.calls[-1]))

    def test_final_model_text_cannot_create_fictional_candidates_or_sources(self):
        service,_,_=self.make([{'role':'assistant','content':'{"summary":"虚构大学2028复试350分，学费1万元，保证录取","candidates":[{"id":"fake"}],"url":"https://evil.example/"}','tool_calls':[]}])
        task=wait_done(service,service.create({'query':'408'})['id'])
        self.assertEqual(task['report']['candidates'],[]);self.assertEqual(task['report']['evidence'],[])
        self.assertNotIn('https://evil.example/',task['report']['assistant_summary'])
        self.assertNotIn('虚构大学',str(task['report']));self.assertNotIn('350',str(task['report']))
        self.assertEqual(task['status'],'partial')

    def test_unknown_tool_and_bad_arguments_are_finite_tool_feedback(self):
        service,ai,_=self.make([{'role':'assistant','content':'','tool_calls':[{'id':'c1','name':'run_shell','arguments':{'command':'x'}}]},
                               {'role':'assistant','content':'未取得可核验资料。','tool_calls':[]}])
        task=wait_done(service,service.create({'query':'408'})['id'])
        self.assertEqual(task['status'],'partial');self.assertEqual(len(ai.calls),2)
        self.assertTrue(any(e['type']=='tool_error' for e in task['events']))

    def test_skill_texts_name_every_search_failure_stage_and_keep_files_bounded(self):
        from research_service import _skills
        base=Path(__file__).resolve().parent.parent/'skills'
        stages=('binding_missing','attribution_unverified','third_party_only','domain_mismatch','index_no_hit')
        names,_=_skills('408')
        self.assertEqual(names,('understand-intent','research-official-sources','verify-evidence','compare-plans'))
        official=(base/'research-official-sources'/'SKILL.md').read_text('utf-8')
        verify=(base/'verify-evidence'/'SKILL.md').read_text('utf-8')
        for body,name in ((official,'research-official-sources'),(verify,'verify-evidence')):
            for stage in stages:
                self.assertIn(stage,body,msg=name)
            self.assertIn('官网没有资料',body,msg=name)
        transfer_names,_=_skills('准备调剂')
        self.assertEqual(transfer_names[-1],'assess-transfer-options')
        for name in transfer_names:
            self.assertLessEqual((base/name/'SKILL.md').stat().st_size,20000)

    def test_system_knowledge_prefetch_stays_inside_the_named_school(self):
        service,_,_=self.make(enabled=False)
        task=service.create({'query':'国科大 计算机 2027 招生目录'})
        tools=Mock();tools.task={'unit_ids':[]};tools.infer_unit.return_value='367929'
        tools.search_knowledge.return_value={'results':[]}
        service._reuse_knowledge(task['id'],tools)
        tools.search_knowledge.assert_called_once_with({'query':'计算机','unit_id':'367929'})
        tools.search_knowledge.reset_mock();tools.task={'unit_ids':['123']}
        service._reuse_knowledge(task['id'],tools)
        tools.search_knowledge.assert_called_once_with({'query':'计算机'})
        tools.search_knowledge.reset_mock();tools.task={'unit_ids':['367929']}
        service._reuse_knowledge(task['id'],tools)
        tools.search_knowledge.assert_called_once_with({'query':'计算机','unit_id':'367929'})

    def test_continue_after_configuration_preserves_query(self):
        service,_,settings=self.make([{'role':'assistant','content':'请补充意向地区。','tool_calls':[]}],enabled=False)
        task=service.create({'query':'考计算机'});settings.enabled=True
        service.continue_research({'research_id':task['id'],'message':'优先天津'})
        done=wait_done(service,task['id'])
        self.assertEqual(done['query'],'考计算机');self.assertEqual(done['follow_ups'],['优先天津'])

    def test_cancel_waiting_and_validate_inputs(self):
        service,_,_=self.make(enabled=False)
        task=service.create({'query':'408'})
        self.assertEqual(service.cancel({'research_id':task['id']})['status'],'cancelled')
        for payload in ({'query':''},{'query':'x'*4001},{'query':'Bearer secretABCDEF'},{'query':'408','unit_ids':['unregistered']},{'query':'408','unexpected':1}):
            with self.subTest(payload=payload),self.assertRaises(ResearchError):service.create(payload)

    def test_delete_requires_terminal_task_and_valid_payload(self):
        service,_,_=self.make(enabled=False)
        task=service.create({'query':'408'})
        self.assertEqual(service.delete({'research_id':task['id']}),{'deleted':True,'research_id':task['id']})
        with self.assertRaises(ResearchError) as error:service.get(task['id'])
        self.assertEqual(error.exception.status,404)
        for payload in ({},{'research_id':''},{'research_id':'r_'+'0'*32},{'research_id':task['id'],'extra':1}):
            with self.subTest(payload=payload),self.assertRaises(ResearchError):service.delete(payload)

    def test_delete_refuses_a_task_whose_worker_is_still_active(self):
        service,ai,_=self.make()
        entered=threading.Event();release=threading.Event()
        def block(*args,**kwargs):
            entered.set();release.wait(2)
            return {'message':{'role':'assistant','content':'完成','tool_calls':[]}}
        ai.chat=block
        task=service.create({'query':'408'});self.assertTrue(entered.wait(1))
        with self.assertRaises(ResearchError) as error:service.delete({'research_id':task['id']})
        self.assertEqual((error.exception.code,error.exception.status),('research_running',409))
        service.cancel({'research_id':task['id']});release.set()
        for _ in range(100):
            if task['id'] not in service._controls:break
            time.sleep(.01)
        self.assertEqual(service.delete({'research_id':task['id']})['deleted'],True)

    def test_polling_never_calls_models_and_exception_text_is_private(self):
        service,ai,_=self.make();ai.chat=Mock(side_effect=RuntimeError('secret-test-not-for-output C:/private'))
        done=wait_done(service,service.create({'query':'408'})['id'])
        self.assertEqual(done['status'],'failed');before=ai.chat.call_count
        service.get(done['id']);service.list(10)
        self.assertEqual(ai.chat.call_count,before)
        self.assertNotIn('secret-test-not-for-output',str(done));self.assertNotIn('C:/private',str(done))

    def test_recoverable_provider_failure_keeps_partial_results_and_a_safe_reason(self):
        service,ai,_=self.make()
        ai.chat=Mock(side_effect=TransportError('provider_unavailable','模型服务暂时不可用，请稍后重试。',503))
        done=wait_done(service,service.create({'query':'408'})['id'])
        self.assertEqual(done['status'],'partial')
        self.assertIsNone(done['error'])
        self.assertTrue(any('模型服务暂时不可用' in gap for gap in done['report']['gaps']))
        self.assertNotIn('secret-test-not-for-output',str(done))

    def test_cancel_and_immediate_continue_cannot_overlap_the_old_call(self):
        service,ai,_=self.make()
        entered=threading.Event();release=threading.Event()
        def block(*args,**kwargs):
            entered.set();release.wait(2)
            return {'message':{'role':'assistant','content':'迟到回复不能覆盖取消','tool_calls':[]}}
        ai.chat=block
        task=service.create({'query':'408'});self.assertTrue(entered.wait(1))
        service.cancel({'research_id':task['id']})
        with self.assertRaises(ResearchError) as error:service.continue_research({'research_id':task['id']})
        self.assertEqual(error.exception.code,'research_stopping');release.set()
        for _ in range(100):
            if task['id'] not in service._controls:break
            time.sleep(.01)
        self.assertEqual(service.get(task['id'])['status'],'cancelled')

    def test_profile_proposal_waits_for_user_confirmation_before_algorithm_use(self):
        service,ai,_=self.make([
            {'role':'assistant','content':'','tool_calls':[{'id':'p','name':'propose_profile','arguments':{
                'updates':{'provinces':['天津']},'evidence':[{'field':'provinces','quote':'优先天津'}]}}]},
            {'role':'assistant','content':'请确认。','tool_calls':[]}])
        task=wait_done(service,service.create({'query':'想考计算机，优先天津'})['id'])
        self.assertEqual(task['status'],'awaiting_input')
        self.assertEqual(task['profile']['provinces'],[])
        self.assertEqual(task['report']['profile_proposal']['updates'],{'provinces':['天津']})
        while task['id'] in service._controls:time.sleep(.01)
        ai.messages=[{'role':'assistant','content':'已确认。','tool_calls':[]}]
        continued=service.continue_research({'research_id':task['id'],'profile':task['report']['profile_proposal']['updates']})
        done=wait_done(service,continued['id'])
        self.assertEqual(done['profile']['provinces'],['天津'])

    def test_round_budget_and_native_usage_end_without_unbounded_calls(self):
        messages=[{'role':'assistant','content':'','tool_calls':[{'id':f't{i}','name':'read_knowledge','arguments':{}}]} for i in range(8)]
        service,ai,_=self.make(messages)
        done=wait_done(service,service.create({'query':'408'})['id'])
        self.assertEqual(done['status'],'partial');self.assertEqual(len(ai.calls),8)
        self.assertTrue(any('轮次上限' in gap for gap in done['report']['gaps']))
        service2,ai2,_=self.make([{'role':'assistant','content':'','tool_calls':[{'id':'t','name':'read_knowledge','arguments':{}}]}])
        original=ai2.chat
        ai2.chat=lambda *args,**kwargs:{**original(*args,**kwargs),'usage':{'input_tokens':30000,'output_tokens':20000}}
        done=wait_done(service2,service2.create({'query':'408'})['id'])
        self.assertEqual(done['usage']['total_tokens'],50000);self.assertEqual(len(ai2.calls),1)

    def test_deadline_is_passed_to_the_model_and_persists_partial_results(self):
        service,ai,_=self.make()
        def bounded(*args,**kwargs):
            while not kwargs['stop_event'].is_set():time.sleep(.001)
            raise RuntimeError('provider deadline')
        ai.chat=bounded
        from research_tools import LIMITS
        with patch.dict(LIMITS,{'max_seconds':.03}):
            done=wait_done(service,service.create({'query':'408'})['id'])
        self.assertEqual(done['status'],'partial');self.assertIsNone(done['error'])
        self.assertTrue(any('时间预算' in gap for gap in done['report']['gaps']))

    def test_model_schema_is_strict_and_provider_metadata_is_preserved_in_private_history(self):
        from research_service import ResearchService
        for message in [None,{'content':[]},{'tool_calls':[{'id':'x','name':'read_knowledge','arguments':'{}'}]},
                        {'tool_calls':[{'id':'x','name':'a','arguments':{}},{'id':'x','name':'b','arguments':{}}]}]:
            with self.subTest(message=message),self.assertRaises(ValueError):ResearchService._message(message)
        row={'role':'assistant','content':'','tool_calls':[],'provider_metadata':{'gemini_parts':[{'thoughtSignature':'native-signature'}]}}
        history=ResearchService._history_message(row,'',[])
        self.assertEqual(history['provider_metadata'],row['provider_metadata'])

    def test_profile_cannot_be_replaced_by_invalid_followup_payload(self):
        service,_,_=self.make(enabled=False);task=service.create({'query':'408'})
        for payload in [{'research_id':task['id'],'profile':None},{'research_id':task['id'],'profile':{'education':'invented'}},
                        {'research_id':task['id'],'message':'sk-abcdefghijklmno'}]:
            with self.assertRaises(ResearchError):service.continue_research(payload)
