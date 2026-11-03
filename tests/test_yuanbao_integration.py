"""Both Yuanbao credentials reach every search path without real network or stored keys."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from grad_engine import validate_profile
from research_tools import ResearchTools, text
from research_store import ResearchError
from service import AdmissionService, ServiceError
from tests.test_research_service import FakeAdmission, FakeAI, wait_done


STANDARD = {'provider':'yuanbao','auth_mode':'standard',
            'secret_id':'AKIDFIXTUREONLY1234567890','secret_key':'fixture-standard-secret-not-real'}


class Settings:
    def __init__(self, config=None, status='connected'):
        self.config=config
        self.status=status

    def runtime_yuanbao(self):
        return deepcopy(self.config)

    def yuanbao_key(self):
        return (self.config or {}).get('api_key','')

    def public_settings(self):
        return {'ai':{'status':'not_configured'},'yuanbao':{
            'auth_mode':(self.config or {}).get('auth_mode','api_key'),
            'status':self.status if self.config else 'not_configured','has_key':bool(self.config)}}


class Response(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


class YuanbaoIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)
        self.settings=Settings(STANDARD)
        self.requests=[]
        self.upstream={'Response':{'Pages':[
            {'title':'官方目录','url':'https://grad.example.edu.cn/catalog','passage':'2027目录'}],
            'RequestId':'fixture-request-id'}}
        environment=patch.dict('os.environ',{'GRAD_SEARCH_API_KEY':''},clear=True)
        environment.start();self.addCleanup(environment.stop)

    def admission(self, **kwargs):
        def opener(request,timeout):
            self.requests.append(request)
            return Response(json.dumps(self.upstream).encode('utf-8'))
        service=AdmissionService(self.data,settings=self.settings,search_opener=opener,**kwargs)
        self.addCleanup(service.close)
        service._catalog.available=Mock(return_value=False)
        registry={'units':[{'id':'123','name':'测试大学','official_url':'https://grad.example.edu.cn'}]}
        service._snapshot=Mock(return_value=(registry,{}))
        service._query=Mock(return_value=('123',2027,None))
        return service

    def search(self,service):
        return service.search({'unit_id':'123','year':2027,'kind':'catalog'})

    def tools(self,settings=None):
        network=Mock()
        network.request_json.return_value=self.upstream
        task={'profile':validate_profile({}),'query':'计算机','follow_ups':[],
              'unit_ids':[],'reference_year':2027,'target_year':2028}
        return ResearchTools(FakeAdmission(),settings or self.settings,task,self.data,network=network),network

    def test_standard_search_uses_signed_fixed_endpoint_and_public_result_has_no_credentials(self):
        result=self.search(self.admission())
        request=self.requests[0]
        self.assertEqual(request.full_url,'https://wsa.tencentcloudapi.com/')
        self.assertTrue(request.get_header('Authorization').startswith('TC3-HMAC-SHA256 Credential='))
        self.assertEqual(request.get_header('X-tc-action'),'SearchPro')
        self.assertEqual(request.get_header('X-tc-version'),'2025-05-08')
        self.assertEqual(json.loads(request.data)['Site'],'example.edu.cn')
        self.assertEqual(set(json.loads(request.data)),{'Query','Site'})
        self.assertEqual(len(result['results']),1)
        for secret in (STANDARD['secret_id'],STANDARD['secret_key']):self.assertNotIn(secret,json.dumps(result))

    def test_saved_encrypted_standard_pair_reaches_search_after_settings_reopen(self):
        from settings_store import SettingsService
        from tests.test_provider_settings import FakeTransport
        stored=SettingsService(self.data,transport=FakeTransport())
        payload={'kind':'yuanbao',**STANDARD}
        proof=stored.test(payload)
        public=stored.save({**payload,'validation_token':proof['validation_token']})
        self.assertEqual(public['yuanbao']['auth_mode'],'standard')
        self.assertEqual(public['yuanbao']['status'],'connected')
        for secret in (STANDARD['secret_id'],STANDARD['secret_key']):self.assertNotIn(secret,json.dumps(public))
        self.settings=SettingsService(self.data,transport=FakeTransport()) if stored.box.persistent else stored
        result=self.search(self.admission())
        self.assertEqual(self.requests[-1].full_url,'https://wsa.tencentcloudapi.com/')
        self.assertEqual(result['request_id'],'fixture-request-id')

    def test_explicit_api_key_override_and_explicit_empty_override_keep_priority(self):
        result=self.search(self.admission(search_api_key='fixture-explicit-api-key'))
        self.assertEqual(result['request_id'],'fixture-request-id')
        self.assertEqual(self.requests[-1].full_url,'https://api.wsa.cloud.tencent.com/SearchPro')
        self.assertEqual(self.requests[-1].get_header('Authorization'),'Bearer fixture-explicit-api-key')
        count=len(self.requests)
        service=self.admission(search_api_key='')
        with self.assertRaises(ServiceError) as caught:self.search(service)
        self.assertEqual(caught.exception.code,'not_configured')
        self.assertEqual(len(self.requests),count)
        self.assertFalse(service._connection_status()['search_configured'])

    def test_saved_standard_precedes_project_environment_and_status_does_not_fake_verification(self):
        self.settings.status='not_tested'
        service=self.admission()
        with patch.dict('os.environ',{'GRAD_SEARCH_API_KEY':'fixture-env-api-key',
                                    'TENCENTCLOUD_SECRET_ID':'other-project-id',
                                    'TENCENTCLOUD_SECRET_KEY':'other-project-secret'}):
            self.search(service)
            status=service._connection_status()
        self.assertEqual(self.requests[0].full_url,'https://wsa.tencentcloudapi.com/')
        self.assertTrue(status['search_configured'])
        self.assertEqual(status['search_status'],'not_tested')
        self.assertNotIn(STANDARD['secret_id'],json.dumps(status))

    def test_standard_cloud_errors_are_safe_and_distinguish_signature_from_rate_limit(self):
        service=self.admission()
        for code,expected in [('AuthFailure.SignatureFailure','search_auth_failed'),
                              ('UnauthorizedOperation','search_auth_failed'),
                              ('RequestLimitExceeded','search_rate_limited')]:
            self.upstream={'Response':{'Error':{'Code':code,'Message':STANDARD['secret_key']}}}
            with self.subTest(code=code),self.assertRaises(ServiceError) as caught:self.search(service)
            self.assertEqual(caught.exception.code,expected)
            self.assertNotIn(STANDARD['secret_key'],str(caught.exception))

    def test_agent_sends_saved_standard_config_only_to_transport_and_never_public_report(self):
        tools,network=self.tools()
        with patch.dict('os.environ',{'GRAD_SEARCH_API_KEY':'fixture-env-api-key'}):
            result=tools.search_sources({'query':'招生目录','unit_id':'123'})
        config=network.request_json.call_args.args[0]
        self.assertEqual(set(network.request_json.call_args.args[3]),{'Query','Site'})
        self.assertEqual(config['auth_mode'],'standard')
        self.assertEqual(config['secret_id'],STANDARD['secret_id'])
        self.assertEqual(config['secret_key'],STANDARD['secret_key'])
        self.assertNotIn('api_key',config)
        for secret in (STANDARD['secret_id'],STANDARD['secret_key']):
            self.assertNotIn(secret,json.dumps(result));self.assertNotIn(secret,json.dumps(tools.report()))

    def test_agent_project_environment_fallback_and_no_global_tencent_credentials(self):
        tools,network=self.tools(Settings())
        with patch.dict('os.environ',{'TENCENTCLOUD_SECRET_ID':'fixture-global-id',
                                    'TENCENTCLOUD_SECRET_KEY':'fixture-global-secret'}):
            with self.assertRaises(ResearchError):tools.search_sources({'query':'招生目录'})
        self.assertEqual(network.request_json.call_count,0)
        with patch.dict('os.environ',{'GRAD_SEARCH_API_KEY':'fixture-env-api-key'}):
            tools.search_sources({'query':'招生目录'})
        self.assertEqual(network.request_json.call_args.args[0]['api_key'],'fixture-env-api-key')

    def test_proof_callback_uses_the_same_saved_standard_configuration(self):
        service=self.admission()
        self.upstream={'Response':{'Pages':[],'RequestId':'fixture-proof-id'}}
        result=service.proof.investigate({'id':'p1','unit_id':'123','year':2027,'name':'计算机',
                                         'code':'081200','department':'计算机学院'},
                                        {'id':'123','name':'测试大学','official_url':'https://grad.example.edu.cn'},'fees')
        self.assertEqual(result['status'],'not_found')
        self.assertEqual(self.requests[-1].full_url,'https://wsa.tencentcloudapi.com/')
        for secret in (STANDARD['secret_id'],STANDARD['secret_key']):
            self.assertNotIn(secret,json.dumps(result))
            for path in self.data.rglob('*.json'):self.assertNotIn(secret,path.read_text('utf-8'))

    def test_explicit_empty_admission_override_also_blocks_agent_search(self):
        admission=self.admission(search_api_key='')
        admission.catalog_bootstrap=FakeAdmission().catalog_bootstrap
        network=Mock()
        tools=ResearchTools(admission,self.settings,{'profile':validate_profile({}),'query':'计算机',
                            'follow_ups':[],'unit_ids':[],'reference_year':2027,'target_year':2028},
                            self.data,network=network)
        with self.assertRaises(ResearchError):tools.search_sources({'query':'招生目录'})
        self.assertEqual(network.request_json.call_count,0)

    def test_legacy_settings_fakes_continue_to_supply_api_keys(self):
        legacy=type('LegacySettings',(),{'yuanbao_key':lambda self:'fixture-legacy-key'})()
        tools,network=self.tools(legacy)
        tools.search_sources({'query':'招生目录'})
        self.assertEqual(network.request_json.call_args.args[0]['api_key'],'fixture-legacy-key')

    def test_search_transport_exception_cannot_publish_standard_secrets_in_task(self):
        from research_service import ResearchService
        self.settings.runtime_ai=lambda:{'provider':'fixture','api_key':'fixture-ai-key','model':'fixture-model'}
        ai=FakeAI([{'role':'assistant','content':'','tool_calls':[{
            'id':'search-1','name':'search_official_sources','arguments':{'query':'招生目录'}}]},
            {'role':'assistant','content':'','tool_calls':[]}])
        service=ResearchService(self.data,FakeAdmission(),settings=self.settings,ai_client=ai)
        self.addCleanup(service.close)
        network=Mock()
        network.request_json.side_effect=RuntimeError(STANDARD['secret_id']+' '+STANDARD['secret_key'])
        with patch('ai_transport.AITransport',return_value=network):
            task=wait_done(service,service.create({'query':'计算机'})['id'])
        self.assertTrue(network.request_json.called)
        self.assertIn('暂时无法核对',json.dumps(task,ensure_ascii=False))
        for secret in (STANDARD['secret_id'],STANDARD['secret_key']):
            self.assertNotIn(secret,json.dumps(task));self.assertNotIn(secret,json.dumps(ai.calls))

    def test_pasted_standard_credentials_do_not_become_research_queries(self):
        for value in (STANDARD['secret_id'], 'SecretKey='+STANDARD['secret_key'],
                      json.dumps({'secret_key':STANDARD['secret_key']}),
                      'TC3-HMAC-SHA256 Credential=fixture-id/2026-11-02/wsa/tc3_request'):
            with self.subTest(value=value),self.assertRaises(ResearchError):text(value)


if __name__=='__main__':unittest.main()
