import json
import threading
import time
import unittest
from unittest.mock import patch

from ai_transport import AITransport,TransportError
from provider_catalog import checked_config
from yuanbao_auth import build_request


class ProtocolTests(unittest.TestCase):
    def test_anthropic_tool_roundtrip(self):
        captured=[]
        def request(c,method,path,body):
            captured.append((path,body))
            return {'content':[{'type':'text','text':'计划'},{'type':'tool_use','id':'id1','name':'search','input':{'q':'目录'}}],'usage':{'input_tokens':12}}
        transport=AITransport(request=request)
        result=transport.chat({'provider':'anthropic','api_key':'key','model':'claude-fable-5-1[1m]'},[
            {'role':'system','content':'rules'},{'role':'user','content':'hi'},
            {'role':'assistant','content':'','tool_calls':[{'id':'a','function':{'name':'search','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':'a','content':'result'}],tools=[{'type':'function','function':{'name':'search','parameters':{'type':'object'}}}])
        self.assertEqual(captured[0][0],'/messages')
        self.assertEqual(captured[0][1]['messages'][-1]['content'][0]['type'],'tool_result')
        self.assertEqual(result['message']['tool_calls'][0]['name'],'search')

    def test_gemini_keeps_thought_signature_private_and_replays(self):
        original=[{'text':'private reasoning','thought':True},{'functionCall':{'name':'search','args':{}},'thoughtSignature':'opaque-signature'}]
        captured=[]
        def request(c,method,path,body):
            captured.append(body)
            return {'candidates':[{'content':{'parts':original}}]}
        transport=AITransport(request=request)
        config={'provider':'gemini','api_key':'key','model':'gemini-model'}
        result=transport.chat(config,[{'role':'user','content':'hi'}])
        self.assertEqual(result['message']['content'],'')
        message=result['message']
        transport.chat(config,[{'role':'user','content':'hi'},message,{'role':'tool','tool_call_id':'gemini-1','content':'done'}])
        self.assertEqual(captured[-1]['contents'][1]['parts'],original)

    def test_discovery_formats_and_manual(self):
        for provider,response,expected in [
            ('gemini',{'models':[{'name':'models/gem','supportedGenerationMethods':['generateContent']},{'name':'models/embed','supportedGenerationMethods':['embedContent']}]},['gem']),
            ('dashscope',{'output':{'models':[{'model':'qwen'}]}},['qwen']),
            ('tokenhub',{'data':[{'id':'online','status':'online'},{'id':'offline','status':'offline'}]},['online']),
            ('anthropic',{'data':[{'id':'claude-test[1m]'}],'has_more':True},['claude-test[1m]']),
        ]:
            with self.subTest(provider=provider):
                result=AITransport(request=lambda *args:response).models({'provider':provider,'api_key':'key'})
                self.assertEqual([m['id'] for m in result['models']],expected)
                self.assertFalse(result['authentication_verified'])
        self.assertEqual(AITransport().models({'provider':'ark','api_key':'key'})['discovery'],'manual')

    def test_bad_responses_and_messages(self):
        config={'provider':'deepseek','api_key':'key','model':'model'}
        for response in [{},{'choices':[]},{'choices':[{'message':{'content':123}}]},
                         {'choices':[{'message':{'tool_calls':[{'id':'x','function':{'name':'unsafe()','arguments':'{}'}}]}}]}]:
            with self.subTest(response=response),self.assertRaises(TransportError):
                AITransport(request=lambda *a:response).chat(config,[{'role':'user','content':'hi'}])
        for messages in [[],[{'role':'invalid'}]]:
            with self.assertRaises(TransportError):AITransport().chat(config,messages)
        event=threading.Event();event.set()
        with self.assertRaises(TransportError) as caught:AITransport().chat(config,[{'role':'user','content':'hi'}],stop_event=event)
        self.assertEqual(caught.exception.code,'cancelled')

    def test_usage_totals_are_normalized_for_all_protocols(self):
        for provider,response,total in [
            ('deepseek',{'choices':[{'message':{'content':'OK'}}],'usage':{'prompt_tokens':7,'completion_tokens':3,'total_tokens':10}},10),
            ('anthropic',{'content':[{'type':'text','text':'OK'}],'usage':{'input_tokens':9,'output_tokens':4}},13),
            ('gemini',{'candidates':[{'content':{'parts':[{'text':'OK'}]}}],'usageMetadata':{'promptTokenCount':8,'candidatesTokenCount':2,'thoughtsTokenCount':5,'totalTokenCount':15}},15),
        ]:
            with self.subTest(provider=provider):
                result=AITransport(request=lambda *a:response).chat({'provider':provider,'api_key':'key','model':'m'},[{'role':'user','content':'hi'}])
                self.assertEqual(result['usage']['total_tokens'],total)

    def test_native_gemini_tool_id_is_returned(self):
        messages=[{'role':'assistant','content':'','tool_calls':[{'id':'native-id','name':'search','arguments':{}}],
                   'provider_metadata':{'gemini_parts':[{'functionCall':{'name':'search','args':{},'id':'native-id'}}]}},
                  {'role':'tool','tool_call_id':'native-id','content':'result'}]
        _,body=AITransport._gemini({'model':'gemini'},messages,None)
        self.assertEqual(body['contents'][-1]['parts'][0]['functionResponse']['id'],'native-id')


class Socket:
    def __init__(self):self.closed=threading.Event()
    def settimeout(self,value):self.timeout=value
    def shutdown(self,*a):self.closed.set()
    def close(self):self.closed.set()


class Connection:
    response=None
    last=None
    def __init__(self,*args):self.sock=Socket();Connection.last=self
    def connect(self):pass
    def request(self,*args,**kwargs):self.request_args=(args,kwargs)
    def getresponse(self):self.response.connection=self;return self.response
    def close(self):self.sock.close()


class Response:
    status=200
    def __init__(self,raw=b'{"data":[]}'):self.raw=raw
    def read(self,size):result=self.raw[:size];self.raw=self.raw[size:];return result


class NetworkBoundaryTests(unittest.TestCase):
    def request(self,response,path='/models',stop=None):
        Connection.response=response
        with patch('ai_transport.public_target',return_value=('example.com',443,('1.1.1.1',))),patch('ai_transport._PinnedConnection',Connection):
            return AITransport().request_json({'provider':'custom','base_url':'https://example.com/v1','api_key':'secret'},'GET',path,stop_event=stop)

    def test_valid_json_closed_connection_and_no_redirect(self):
        self.assertEqual(self.request(Response()),{'data':[]})
        self.assertGreater(Connection.last.sock.timeout,8)
        self.assertTrue(Connection.last.sock.closed.is_set())
        for status in [302,401,403,429,500]:
            response=Response(b'secret');response.status=status
            with self.subTest(status=status),self.assertRaises(TransportError) as caught:self.request(response)
            self.assertNotIn('secret',str(caught.exception))

    def test_siliconflow_fixed_chat_filter_is_sent_without_allowing_other_queries(self):
        Connection.response=Response(b'{"data":[{"id":"Qwen/chat"}]}')
        with patch('ai_transport.public_target',return_value=('api.siliconflow.cn',443,('1.1.1.1',))),patch('ai_transport._PinnedConnection',Connection):
            models=AITransport().models({'provider':'siliconflow','api_key':'key'})
        self.assertEqual(models['models'][0]['id'],'Qwen/chat')
        self.assertEqual(Connection.last.request_args[0][1],'/v1/models?sub_type=chat')
        with self.assertRaises(TransportError):
            AITransport().request_json({'provider':'siliconflow','api_key':'key'},'GET','/models?key=secret')

    def test_invalid_oversized_and_nonfinite_json(self):
        for raw in [b'<html>failure</html>',b'[]',b'{"n":-1e9999}',b'x'*(4*1024*1024+1)]:
            with self.subTest(size=len(raw)),self.assertRaises(TransportError):self.request(Response(raw))

    def test_total_deadline_interrupts_slow_body(self):
        class Slow(Response):
            def read(self,size):
                self.connection.sock.closed.wait(2)
                raise OSError('closed')
        started=time.monotonic()
        with patch('ai_transport.REQUEST_DEADLINE_SECONDS',0.08),self.assertRaises(TransportError) as caught:self.request(Slow())
        self.assertEqual(caught.exception.code,'provider_timeout')
        self.assertLess(time.monotonic()-started,1)

    def test_cancel_interrupts_socket(self):
        class Slow(Response):
            def read(self,size):self.connection.sock.closed.wait(2);raise OSError('closed')
        event=threading.Event();timer=threading.Timer(0.08,event.set);timer.start()
        try:
            with self.assertRaises(TransportError) as caught:self.request(Slow(),stop=event)
            self.assertEqual(caught.exception.code,'cancelled')
        finally:timer.cancel();timer.join()

    def test_path_rejection_and_preset_addresses(self):
        for path in ['//evil','https://evil','/a?key=b','/../x','/bad\n']:
            with self.subTest(path=path),self.assertRaises(TransportError):self.request(Response(),path)
        for provider in ['deepseek','openai','anthropic','gemini','dashscope','moonshot','zhipu','ark','siliconflow','hunyuan','tokenhub','openrouter','minimax','xai','ollama']:
            self.assertEqual(checked_config({'provider':provider,'api_key':'key'})['provider'],provider)

    def test_standard_yuanbao_signs_exact_bytes_sent_to_the_fixed_endpoint(self):
        config={'provider':'yuanbao','auth_mode':'standard','secret_id':'AKIDfixture','secret_key':'fixture-private-key'}
        body={'Query':'天津 考研'}
        Connection.response=Response(b'{"Response":{"Pages":[],"RequestId":"fixture"}}')
        with patch('ai_transport.public_target',return_value=('wsa.tencentcloudapi.com',443,('1.1.1.1',))) as target,patch('ai_transport._PinnedConnection',Connection),patch('yuanbao_auth.time.time',return_value=1551113065):
            result=AITransport().request_json(config,'POST','',body)
        url,raw,headers=build_request(config,body,1551113065)
        target.assert_called_once_with(url)
        args,kwargs=Connection.last.request_args
        self.assertEqual(args,('POST','/'))
        self.assertEqual(kwargs['body'],raw)
        self.assertEqual(kwargs['headers'],headers)
        self.assertEqual(result['Response']['RequestId'],'fixture')
        self.assertTrue(Connection.last.sock.closed.is_set())

    def test_standard_yuanbao_http_200_errors_and_bearer_compatibility(self):
        standard={'provider':'yuanbao','auth_mode':'standard','secret_id':'AKIDfixture','secret_key':'fixture-private-key'}
        for code,expected in [('AuthFailure.SecretIdNotFound','provider_auth_failed'),('AuthFailure.SignatureExpire','signature_expired')]:
            Connection.response=Response(json.dumps({'Response':{'Error':{'Code':code,'Message':'do-not-leak-fixture-private-key'}}}).encode())
            with patch('ai_transport.public_target',return_value=('wsa.tencentcloudapi.com',443,('1.1.1.1',))),patch('ai_transport._PinnedConnection',Connection),self.assertRaises(TransportError) as caught:
                AITransport().request_json(standard,'POST','',{'Query':'fixture'})
            self.assertEqual(caught.exception.code,expected)
            self.assertNotIn('do-not-leak',str(caught.exception))
        calls=[]
        transport=AITransport(request=lambda *args:calls.append(args) or {'Response':{'Pages':[]}})
        self.assertEqual(transport.test_yuanbao(standard)['result_count'],0)
        self.assertEqual(calls[-1][0]['auth_mode'],'standard')
        self.assertEqual(set(calls[-1][3]),{'Query'})
        transport.test_yuanbao('fixture-api-key')
        self.assertEqual(calls[-1][0]['auth_mode'],'api_key')
        self.assertEqual(calls[-1][0]['api_key'],'fixture-api-key')

    def test_yuanbao_only_permits_search_post_and_cancel_still_interrupts(self):
        config={'provider':'yuanbao','auth_mode':'standard','secret_id':'AKIDfixture','secret_key':'fixture-private-key'}
        for method,path in [('GET',''),('POST','/models'),('POST','/SearchPro')]:
            with self.subTest(method=method,path=path),self.assertRaises(TransportError):
                AITransport().request_json(config,method,path,{'Query':'fixture'})
        class Slow(Response):
            def read(self,size):self.connection.sock.closed.wait(2);raise OSError('closed')
        Connection.response=Slow();stop=threading.Event();timer=threading.Timer(0.08,stop.set);timer.start()
        try:
            with patch('ai_transport.public_target',return_value=('wsa.tencentcloudapi.com',443,('1.1.1.1',))),patch('ai_transport._PinnedConnection',Connection),self.assertRaises(TransportError) as caught:
                AITransport().request_json(config,'POST','',{'Query':'fixture'},stop_event=stop)
            self.assertEqual(caught.exception.code,'cancelled')
        finally:timer.cancel();timer.join()


if __name__=='__main__':unittest.main()
