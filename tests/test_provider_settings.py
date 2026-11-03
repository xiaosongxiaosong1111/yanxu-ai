import json
import sqlite3
from pathlib import Path
import tempfile
import unittest

from settings_store import SettingsService, SettingsError
from ai_transport import AITransport, TransportError
from provider_catalog import checked_config


class FakeTransport:
    def models(self, config):
        return {'models':[{'id':'test-model','label':'test-model'}], 'discovery':'remote', 'authentication_verified':False}

    def chat(self, config, messages, tools=None, stop_event=None):
        return {'message':{'role':'assistant','content':'OK','tool_calls':[]},'usage':{'total_tokens':2}}

    def test_yuanbao(self, key):
        return {'status':'connected','result_count':1}


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=SettingsService(Path(self.temp.name), transport=FakeTransport())
        self.payload={'kind':'ai','provider':'deepseek','api_key':'test-secret-12345','model':'test-model'}

    def test_key_never_public_and_validation_bound_to_model(self):
        proof=self.store.test(self.payload)
        saved=self.store.save({**self.payload,'validation_token':proof['validation_token']})
        self.assertEqual(saved['ai']['status'],'connected')
        self.assertNotIn(self.payload['api_key'],json.dumps(saved))
        self.assertEqual(self.store.runtime_ai()['api_key'],self.payload['api_key'])
        changed=self.store.save({**self.payload,'model':'changed','validation_token':proof['validation_token']})
        self.assertEqual(changed['ai']['status'],'not_tested')

    def test_saved_key_never_reused_for_different_provider(self):
        self.store.save(self.payload)
        with self.assertRaises(SettingsError):
            self.store.models({'provider':'openai','use_saved_key':True})

    def test_saved_custom_key_never_reused_for_new_endpoint(self):
        self.store.save({**self.payload,'provider':'custom','base_url':'https://first.example.com/v1'})
        with self.assertRaises(SettingsError):
            self.store.models({'provider':'custom','base_url':'https://second.example.com/v1','use_saved_key':True})

    def test_persistent_secret_reloads_without_plaintext(self):
        proof=self.store.test(self.payload)
        self.store.save({**self.payload,'validation_token':proof['validation_token']})
        second=SettingsService(Path(self.temp.name),transport=FakeTransport())
        if self.store.box.persistent:
            self.assertEqual(second.runtime_ai()['api_key'],self.payload['api_key'])
        else:
            with self.assertRaises(SettingsError):second.runtime_ai()

    def test_unverified_or_changed_model_cannot_start_paid_research(self):
        self.store.save(self.payload)
        with self.assertRaises(SettingsError) as caught:self.store.runtime_ai()
        self.assertEqual(caught.exception.code,'model_not_verified')
        self.assertEqual(caught.exception.status,503)
        proof=self.store.test(self.payload)
        self.store.save({**self.payload,'validation_token':proof['validation_token']})
        self.assertEqual(self.store.runtime_ai()['model'],'test-model')
        self.store.save({**self.payload,'model':'changed','validation_token':proof['validation_token']})
        with self.assertRaises(SettingsError) as caught:self.store.runtime_ai()
        self.assertEqual(caught.exception.code,'model_not_verified')

    def test_expired_receipt_and_invalid_fields(self):
        result=self.store.test(self.payload)
        self.store._tickets[result['validation_token']]['expires']=0
        saved=self.store.save({**self.payload,'validation_token':result['validation_token']})
        self.assertEqual(saved['ai']['status'],'not_tested')
        for field in [{'kind':[]},{'unknown':True},{'use_saved_key':'false'},{'validation_token':{}}]:
            with self.subTest(field=field),self.assertRaises(SettingsError):self.store.save({**self.payload,**field})

    def test_models_does_not_claim_generation_success(self):
        self.store.models(self.payload)
        self.assertEqual(self.store.public_settings()['ai']['status'],'not_configured')

    def test_yuanbao_separate_and_delete(self):
        key='yuanbao-secret'
        self.store.save({'kind':'yuanbao','api_key':key})
        self.assertEqual(self.store.yuanbao_key(),key)
        self.assertNotIn(key,json.dumps(self.store.public_settings()))
        self.store.delete({'kind':'yuanbao'})
        self.assertEqual(self.store.yuanbao_key(),'')

    def test_plaintext_secret_not_persisted(self):
        self.store.save(self.payload)
        for path in Path(self.temp.name).rglob('*'):
            if path.is_file():self.assertNotIn(self.payload['api_key'].encode(),path.read_bytes())

    def test_standard_pair_test_save_private_runtime_and_encrypted_restart(self):
        payload={'kind':'yuanbao','auth_mode':'standard','secret_id':'AKID-standard-fixture','secret_key':'standard-private-fixture'}
        seen=[]
        self.store.transport.test_yuanbao=lambda value:seen.append(value) or {'status':'connected','result_count':1}
        proof=self.store.test(payload)
        public=self.store.save({**payload,'validation_token':proof['validation_token']})
        self.assertEqual(public['yuanbao']['status'],'connected')
        self.assertEqual(public['yuanbao']['auth_mode'],'standard')
        self.assertTrue(public['yuanbao']['has_key'])
        self.assertEqual(seen[0]['secret_id'],payload['secret_id'])
        self.assertEqual(seen[0]['secret_key'],payload['secret_key'])
        self.assertEqual(self.store.runtime_yuanbao()['secret_key'],payload['secret_key'])
        self.assertEqual(self.store.yuanbao_key(),'')
        for secret in (payload['secret_id'],payload['secret_key']):
            self.assertNotIn(secret,json.dumps(public))
            for path in Path(self.temp.name).rglob('*'):
                if path.is_file():self.assertNotIn(secret.encode(),path.read_bytes())
        if self.store.box.persistent:
            second=SettingsService(Path(self.temp.name),transport=FakeTransport())
            self.assertEqual(second.runtime_yuanbao()['secret_id'],payload['secret_id'])
            self.assertEqual(second.runtime_yuanbao()['secret_key'],payload['secret_key'])

    def test_standard_saved_pair_reuse_is_atomic_and_mode_bound(self):
        payload={'kind':'yuanbao','auth_mode':'standard','secret_id':'AKID-saved-fixture','secret_key':'saved-private-fixture'}
        self.store.save(payload)
        _,config=self.store._draft({'kind':'yuanbao','auth_mode':'standard','use_saved_key':True})
        self.assertEqual(config['secret_id'],payload['secret_id'])
        self.assertEqual(config['secret_key'],payload['secret_key'])
        for change in [{'auth_mode':'api_key'},{'secret_id':'AKID-new-fixture'},
                       {'secret_key':'new-private-fixture'},{'base_url':'https://other.example/'}]:
            with self.subTest(change=change),self.assertRaises(SettingsError):
                self.store.test({'kind':'yuanbao','auth_mode':'standard','use_saved_key':True,**change})
        self.store.save({'kind':'yuanbao','api_key':'saved-api-key'})
        with self.assertRaises(SettingsError):
            self.store.test({'kind':'yuanbao','auth_mode':'standard','use_saved_key':True})

    def test_standard_receipt_binds_both_credentials_and_auth_mode(self):
        payload={'kind':'yuanbao','auth_mode':'standard','secret_id':'AKID-ticket-fixture','secret_key':'ticket-private-fixture'}
        proof=self.store.test(payload)
        for change in [{'secret_id':'AKID-other-fixture'},{'secret_key':'different-secret'}]:
            public=self.store.save({**payload,**change,'validation_token':proof['validation_token']})
            self.assertEqual(public['yuanbao']['status'],'not_tested')
        public=self.store.save({'kind':'yuanbao','api_key':'fixture-api-key','validation_token':proof['validation_token']})
        self.assertEqual(public['yuanbao']['status'],'not_tested')
        with self.assertRaises(SettingsError):self.store.save({**self.payload,'secret_id':'wrong-kind'})

    def test_legacy_encrypted_api_key_record_still_loads_without_migration(self):
        if not self.store.box.persistent:self.skipTest('Windows DPAPI persistence only')
        meta={'provider':'yuanbao','base_url':'https://api.wsa.cloud.tencent.com/SearchPro','model':'','status':'connected'}
        with self.store._connection() as connection:
            connection.execute('INSERT INTO settings VALUES(?,?,?)',('yuanbao',json.dumps(meta),self.store.box.encrypt('legacy-api-key-fixture')))
        public=self.store.public_settings()
        self.assertEqual(public['yuanbao']['auth_mode'],'api_key')
        self.assertEqual(self.store.yuanbao_key(),'legacy-api-key-fixture')
        self.assertEqual(self.store.runtime_yuanbao()['api_key'],'legacy-api-key-fixture')
        self.assertNotIn('legacy-api-key-fixture',json.dumps(public))


class TransportTests(unittest.TestCase):
    def test_preset_override_is_rejected(self):
        with self.assertRaises(TransportError):checked_config({'provider':'deepseek','base_url':'https://example.com','api_key':'a','model':'m'})

    def test_custom_private_and_injected_urls_rejected(self):
        for url in ['http://127.0.0.1/v1','https://169.254.169.254/v1','https://user:pass@example.com/v1','https://example.com/v1?key=secret']:
            with self.subTest(url=url),self.assertRaises(TransportError):
                checked_config({'provider':'custom','base_url':url,'api_key':'key','model':'m'})

    def test_openai_chat_normalizes_tools(self):
        captured=[]
        def request(config,method,path,body=None):
            captured.append(body)
            return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'t1','function':{'name':'search','arguments':'{"q":"天津"}'}}]}}],'usage':{}}
        transport=AITransport(request=request)
        result=transport.chat({'provider':'deepseek','api_key':'key','model':'m'},[{'role':'user','content':'hi'}])
        self.assertEqual(result['message']['tool_calls'][0]['arguments'],{'q':'天津'})

    def test_no_raw_provider_error_or_key_in_error(self):
        def request(*args,**kwargs):raise OSError('key-secret')
        with self.assertRaises(TransportError) as error:
            AITransport(request=request).chat({'provider':'deepseek','api_key':'key-secret','model':'m'},[{'role':'user','content':'hi'}])
        self.assertNotIn('key-secret',str(error.exception))


if __name__=='__main__':unittest.main()
