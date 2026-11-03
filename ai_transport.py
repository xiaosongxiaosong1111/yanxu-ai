"""Bounded provider HTTP and normalized tool-calling messages."""
import json
import http.client
import math
import re
import socket
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from urllib.parse import quote,urlsplit

from collectors.discovery import public_target,_PinnedConnection,DiscoveryError
from provider_catalog import checked_config,TransportError,MODEL_ID_PATTERN
from public_sources import _DeadlineGuard,_DNS_POOL,SourceError
from yuanbao_auth import build_request,checked_response

REQUEST_DEADLINE_SECONDS=40
DNS_TIMEOUT_SECONDS=8


def _finite_float(value):
    result=float(value)
    if not math.isfinite(result):raise ValueError('nonfinite')
    return result


def _json(raw):
    return json.loads(raw,parse_float=_finite_float,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite')))


class AITransport:
    def __init__(self,request=None):self._request=request

    def request_json(self,config,method,path,body=None,stop_event=None):
        config=checked_config(config)
        checked_path=path.split('?',1)[0] if config['provider']=='siliconflow' and path=='/models?sub_type=chat' else path
        if method not in {'GET','POST'} or not isinstance(path,str) or (path and not path.startswith('/')) or path.startswith('//') or re.search(r'[\x00-\x20\\?#]',checked_path) or '..' in path:
            raise TransportError('invalid_request','接口请求不正确。')
        if config['provider']=='yuanbao' and (method!='POST' or path):
            raise TransportError('invalid_request','元宝搜索仅支持固定检索接口。')
        if self._request:
            try:
                value=self._request(config,method,path,body)
                if config['provider']=='yuanbao':checked_response(value)
                return value
            except TransportError:raise
            except Exception:raise TransportError('provider_unavailable','服务连接失败，请检查网络或稍后重试。',503) from None
        base=config['base_url']
        if config['provider']=='dashscope' and path=='/api/v1/models':
            endpoint=urlsplit(base);base='https://'+endpoint.netloc
        url=base+path
        headers={'Accept':'application/json','Content-Type':'application/json; charset=utf-8'}
        raw=None
        if config['provider']=='yuanbao':url,raw,headers=build_request(config,body)
        elif config['protocol']=='gemini':headers['x-goog-api-key']=config['api_key']
        else:headers['Authorization']='Bearer '+(config['api_key'] or 'ollama')
        parts=urlsplit(url)
        if config['protocol']=='anthropic':headers['anthropic-version']='2026-10-21'
        guard=_DeadlineGuard(REQUEST_DEADLINE_SECONDS);watch_done=threading.Event();watcher=None
        started=time.monotonic()
        if stop_event is not None:
            def watch():
                while not watch_done.wait(0.05):
                    if stop_event.is_set():guard._expire();return
            watcher=threading.Thread(target=watch,daemon=True);watcher.start()
        try:
            if config['provider']=='ollama':host,port,addresses='127.0.0.1',11434,('127.0.0.1',)
            else:
                future=_DNS_POOL.submit(public_target,url)
                dns_deadline=time.monotonic()+min(DNS_TIMEOUT_SECONDS,REQUEST_DEADLINE_SECONDS)
                while True:
                    if stop_event is not None and stop_event.is_set():
                        future.cancel();raise TransportError('cancelled','研究已取消。',409)
                    remaining=dns_deadline-time.monotonic()
                    if remaining<=0:future.cancel();raise FutureTimeout()
                    try:host,port,addresses=future.result(timeout=min(0.1,remaining));break
                    except FutureTimeout:continue
            remaining=max(0.01,REQUEST_DEADLINE_SECONDS-(time.monotonic()-started))
            connection=_PinnedConnection(host,port,addresses[0],min(8,remaining),parts.scheme=='https')
            try:
                guard.bind(connection)
                connection.connect()
                guard.bind(connection)
                connection.sock.settimeout(max(0.01,REQUEST_DEADLINE_SECONDS-(time.monotonic()-started)))
                if config['provider']!='yuanbao':raw=None if body is None else json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
                if raw is not None and len(raw)>1024*1024:raise TransportError('request_too_large','本轮上下文过大，请缩小研究范围。')
                connection.request(method,(parts.path or '/')+('?' + parts.query if parts.query else ''),body=raw,headers=headers)
                response=connection.getresponse()
                guard.bind(connection,response)
                if response.status>=300:
                    failures={401:('provider_auth_failed','鉴权未通过，请检查服务凭据和所属账号或地区。'),
                              403:('provider_permission_denied','凭据尚无此模型或服务权限，请检查开通状态。'),
                              402:('insufficient_quota','当前账户余额或模型额度不足，请在厂商控制台检查。'),
                              404:('model_or_endpoint_unavailable','模型或接口地址不存在，请核对模型 ID 和地址。'),
                              429:('provider_rate_limited','服务正在限流或达到配额，请稍后重试并检查额度。')}
                    code,message=failures.get(response.status,('provider_request_failed','服务未接受请求，请检查模型 ID、地址和开通状态。'))
                    raise TransportError(code,message,503)
                chunks=[];size=0;limit=4*1024*1024
                while size<=limit:
                    guard.bind(connection,response)
                    chunk=response.read(min(65536,limit+1-size))
                    if not chunk:break
                    chunks.append(chunk);size+=len(chunk)
                data=b''.join(chunks)
                guard.bind(connection,response)
                if len(data)>4*1024*1024:raise TransportError('response_too_large','服务响应超过安全上限。',502)
                value=_json(data)
                if not isinstance(value,dict):raise ValueError('shape')
                if config['provider']=='yuanbao':checked_response(value)
                return value
            finally:connection.close()
        except TransportError:raise
        except (DiscoveryError,OSError,ValueError,TypeError,UnicodeError,http.client.HTTPException,FutureTimeout,SourceError):
            if stop_event is not None and stop_event.is_set():raise TransportError('cancelled','研究已取消。',409) from None
            if guard.expired:raise TransportError('provider_timeout','服务响应超过总期限，请稍后重试。',504) from None
            raise TransportError('provider_unavailable','无法取得有效服务响应，请检查地址、网络和模型配置。',503) from None
        finally:
            watch_done.set()
            if watcher is not None:watcher.join(timeout=1)
            guard.close()

    def models(self,config):
        c=checked_config(config)
        if c['models_path'] is None:
            return {'models':[],'discovery':'manual','authentication_verified':False,'notice':c['note'] or '该服务需手动填写已开通模型 ID。'}
        value=self.request_json(c,'GET',c['models_path'])
        rows=value.get('models',[]) if c['protocol']=='gemini' else value.get('output',{}).get('models',[]) if c['provider']=='dashscope' else value.get('data',[])
        if not isinstance(rows,list):raise TransportError('invalid_response','模型列表格式无法识别。',502)
        result=[]
        for row in rows[:1000]:
            if not isinstance(row,dict):continue
            if c['protocol']=='gemini' and 'generateContent' not in row.get('supportedGenerationMethods',[]):continue
            identifier=row.get('name','').removeprefix('models/') if c['protocol']=='gemini' else row.get('model') or row.get('id') if c['provider']=='dashscope' else row.get('id')
            if c['provider']=='tokenhub' and row.get('status')!='online':continue
            if isinstance(identifier,str) and len(identifier)<=200 and re.fullmatch(MODEL_ID_PATTERN,identifier):
                result.append({'id':identifier,'label':str(row.get('displayName') or row.get('display_name') or identifier)[:200]})
        return {'models':list({row['id']:row for row in result}.values()),'discovery':'remote',
                'authentication_verified':False,'has_more':bool(value.get('has_more') or value.get('nextPageToken')),
                'notice':'已读取厂商模型目录；实际访问权限以生成测试为准。'}

    def chat(self,config,messages,tools=None,stop_event=None):
        c=checked_config(config)
        if not c['model']:raise TransportError('missing_model','请选择或填写模型 ID。')
        if stop_event is not None and stop_event.is_set():raise TransportError('cancelled','研究已取消。',409)
        if not isinstance(messages,list) or not 1<=len(messages)<=100 or any(not isinstance(m,dict) or m.get('role') not in {'system','user','assistant','tool'} for m in messages):
            raise TransportError('invalid_messages','模型消息格式不正确。')
        if tools is not None and (not isinstance(tools,list) or len(tools)>20):raise TransportError('invalid_tools','工具列表格式不正确。')
        protocol=c['protocol']
        if protocol=='anthropic':path,body=self._anthropic(c,messages,tools)
        elif protocol=='gemini':path,body=self._gemini(c,messages,tools)
        else:
            path='/chat/completions';body={'model':c['model'],'messages':[{k:v for k,v in m.items() if k!='provider_metadata'} for m in messages],'stream':False}
            body['max_completion_tokens' if c['provider']=='openai' else 'max_tokens']=2048
            if tools:body={**body,'tools':tools,'tool_choice':'auto'}
        value=self.request_json(c,'POST',path,body,stop_event=stop_event)
        if stop_event is not None and stop_event.is_set():raise TransportError('cancelled','研究已取消。',409)
        try:
            if protocol=='anthropic':
                blocks=value['content'];text=''.join(b.get('text','') for b in blocks if b.get('type')=='text')
                calls=[{'id':b['id'],'name':b['name'],'arguments':b['input']} for b in blocks if b.get('type')=='tool_use']
                usage=value.get('usage',{})
            elif protocol=='gemini':
                blocks=value['candidates'][0]['content']['parts'];text=''.join(b.get('text','') for b in blocks if not b.get('thought'))
                calls=[{'id':b['functionCall'].get('id') or 'gemini-'+str(i),'name':b['functionCall']['name'],'arguments':b['functionCall'].get('args',{})} for i,b in enumerate(blocks) if 'functionCall' in b]
                usage=value.get('usageMetadata',{})
            else:
                message=value['choices'][0]['message'];text=message.get('content') or ''
                calls=[{'id':call['id'],'name':call['function']['name'],'arguments':_json(call['function']['arguments']) if isinstance(call['function']['arguments'],str) else call['function']['arguments']} for call in message.get('tool_calls',[])]
                usage=value.get('usage',{})
            if not isinstance(text,str) or len(text)>100000 or len(calls)>12:raise ValueError('response shape')
            if any(not isinstance(t['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',t['name']) or not isinstance(t['arguments'],dict) for t in calls):raise ValueError('tool shape')
            clean_usage={k:v for k,v in usage.items() if isinstance(k,str) and isinstance(v,int) and not isinstance(v,bool) and 0<=v<10**9} if isinstance(usage,dict) else {}
            incoming=next((clean_usage[k] for k in ('input_tokens','prompt_tokens','promptTokenCount') if k in clean_usage),0)
            outgoing=next((clean_usage[k] for k in ('output_tokens','completion_tokens','candidatesTokenCount') if k in clean_usage),0)
            if protocol=='gemini':outgoing+=clean_usage.get('thoughtsTokenCount',0)
            total=max(incoming+outgoing,clean_usage.get('total_tokens',clean_usage.get('totalTokenCount',0)))
            clean_usage={**clean_usage,'input_tokens':incoming,'output_tokens':outgoing,'total_tokens':total}
            message={'role':'assistant','content':text,'tool_calls':calls}
            if protocol=='gemini':message['provider_metadata']={'gemini_parts':blocks}
            return {'message':message,'usage':clean_usage}
        except (KeyError,IndexError,TypeError,ValueError,AttributeError):
            raise TransportError('invalid_model_response','模型响应或工具参数无法识别，请更换支持工具调用的模型。',502) from None

    @staticmethod
    def _anthropic(c,messages,tools):
        result=[]
        for m in messages:
            if m['role']=='system':continue
            if m['role']=='tool':
                role='user';content=[{'type':'tool_result','tool_use_id':m.get('tool_call_id'),'content':m.get('content','')}]
            else:
                role=m['role'];content=[{'type':'text','text':m.get('content') or ''}] if m.get('content') else []
                for t in m.get('tool_calls',[]):
                    f=t.get('function',t);args=f.get('arguments',{})
                    content.append({'type':'tool_use','id':t['id'],'name':f['name'],'input':_json(args) if isinstance(args,str) else args})
            if result and result[-1]['role']==role:result[-1]={**result[-1],'content':[*result[-1]['content'],*content]}
            else:result.append({'role':role,'content':content})
        body={'model':c['model'],'max_tokens':2048,'messages':result,'system':'\n'.join(m.get('content','') for m in messages if m['role']=='system')}
        if tools:body['tools']=[{'name':t['function']['name'],'description':t['function'].get('description',''),'input_schema':t['function']['parameters']} for t in tools]
        return '/messages',body

    @staticmethod
    def _gemini(c,messages,tools):
        names={t['id']:t.get('function',t)['name'] for m in messages for t in m.get('tool_calls',[])}
        native_ids={part['functionCall']['id'] for m in messages for part in (m.get('provider_metadata') or {}).get('gemini_parts',[])
                    if isinstance(part,dict) and isinstance(part.get('functionCall'),dict) and isinstance(part['functionCall'].get('id'),str)}
        contents=[]
        for m in messages:
            if m['role']=='system':continue
            original=(m.get('provider_metadata') or {}).get('gemini_parts')
            if m['role']=='assistant' and isinstance(original,list):
                contents.append({'role':'model','parts':original});continue
            parts=[]
            if m['role']=='tool':
                identifier=m.get('tool_call_id')
                response={'name':names.get(identifier,'tool'),'response':{'result':m.get('content','')}}
                if identifier in native_ids:response['id']=identifier
                parts=[{'functionResponse':response}]
            else:
                if m.get('content'):parts.append({'text':m['content']})
                for t in m.get('tool_calls',[]):
                    f=t.get('function',t);args=f.get('arguments',{})
                    parts.append({'functionCall':{'name':f['name'],'args':_json(args) if isinstance(args,str) else args}})
            contents.append({'role':'model' if m['role']=='assistant' else 'user','parts':parts})
        body={'contents':contents,'generationConfig':{'maxOutputTokens':2048}}
        system='\n'.join(m.get('content','') for m in messages if m['role']=='system')
        if system:body['systemInstruction']={'parts':[{'text':system}]}
        if tools:body['tools']=[{'functionDeclarations':[{'name':t['function']['name'],'description':t['function'].get('description',''),'parameters':t['function']['parameters']} for t in tools]}]
        return '/models/'+quote(c['model'].removeprefix('models/'),safe='')+':generateContent',body

    def test_yuanbao(self,key):
        config=key if isinstance(key,dict) else {'provider':'yuanbao','api_key':key}
        if config.get('provider')!='yuanbao':raise TransportError('invalid_provider','请选择元宝搜索服务。')
        value=self.request_json(config,'POST','',{'Query':'教育部 研究生教育 学科专业目录'})
        body=checked_response(value)
        return {'status':'connected','result_count':len(body['Pages']),'request_id':str(body.get('RequestId',''))[:120]}
