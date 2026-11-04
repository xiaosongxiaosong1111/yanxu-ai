import test from 'node:test';
import assert from 'node:assert/strict';
import {renderSettings,normalizeSettings} from '../static/settings.mjs';

const provider={id:'local_bge',label:'本机中文向量模型',requires_key:false,default_model:'BAAI/bge-small-zh-v1.5',default_base_url:'',local:true};
const state={loadStatus:'ready',tab:'embedding',providers:[{id:'deepseek',label:'DeepSeek'}],embeddingProviders:[provider],saved:{ai:{provider:'deepseek',model:'chat-model',status:'connected',has_key:true},yuanbao:{status:'connected',has_key:true},embedding:{provider:'local_bge',model:provider.default_model,status:'not_configured',has_key:false}},embedding:{providers:[provider],draft:{provider:'local_bge',model:provider.default_model},loadStatus:'ready'}};

test('a third knowledge tab shows independent embedding configuration and a local-first explanation',()=>{
 const html=renderSettings(state);
 assert.match(html,/data-settings-tab="embedding"/);assert.match(html,/知识检索/);assert.match(html,/本机中文向量模型/);
 assert.match(html,/首次/);assert.match(html,/95/);assert.match(html,/测试并保存/);
 assert.ok(!html.includes('DeepSeek</option>'));assert.ok(!html.includes('name="secret_key"'));
});

test('public embedding state preserves observed identity without leaking credentials',()=>{
 const privateFixture='PRIVATE_CANARY', tokenFixture='TOKEN_CANARY';
 const value=normalizeSettings({embedding:{provider:'local_bge',model:'bge',dimension:512,status:'connected',response_model:'observed-bge',api_key:privateFixture,token:tokenFixture}});
 assert.equal(value.embedding?.dimension,512);assert.equal(value.embedding?.response_model,'observed-bge');
 assert.ok(!JSON.stringify(value).includes('PRIVATE_CANARY'));assert.ok(!JSON.stringify(value).includes('TOKEN_CANARY'));
});

test('unconfigured embedding never labels the semantic index as ready',()=>{
 const html=renderSettings(state);assert.match(html,/语义索引/);assert.ok(!html.includes('语义索引已就绪'));assert.ok(!html.includes('向量生成已验证'));
});

// Controller tests use an in-memory API; no DOM and no network are involved.
import {createEmbeddingConnection,normalizeEmbedding,embeddingPayload} from '../static/embedding-settings.mjs';

const providers=[{id:'local_bge',requires_key:false,local:true,default_model:'BAAI/bge-small-zh-v1.5',default_base_url:''},{id:'custom',requires_key:false,default_model:'',default_base_url:''},{id:'remote',requires_key:true,default_model:'m1',default_base_url:'https://api.example.com/'}];

function fakeApi(responses){const calls=[];const fn=async(path,{body}={})=>{calls.push({path,body});const value=responses[path];if(typeof value==='function')return value(path,body);if(value instanceof Error)throw value;return value;};fn.calls=calls;return fn;}

test('embedding connection validates provider, model, key and custom endpoint before any request',async()=>{
 const api=fakeApi({});const changes=[];const connection=createEmbeddingConnection({api,providers,saved:{status:'not_configured'},onChange:value=>changes.push(value)});
 connection.input('embedding_model','');
 await connection.models();assert.equal(api.calls.length,0);assert.match(connection.snapshot().error,/请选择或填写向量模型/);
 connection.choose('remote');assert.equal(connection.snapshot().draft.provider,'remote');
 connection.input('embedding_model','');
 await connection.models();assert.match(connection.snapshot().error,/向量模型 ID/);
 connection.input('embedding_model','m1');
 await connection.models();assert.match(connection.snapshot().error,/API Key/);
 connection.input('embedding_api_key','secret');
 connection.choose('custom');connection.input('embedding_model','c1');
 await connection.models();assert.match(connection.snapshot().error,/自定义向量接口地址/);
 connection.input('embedding_base_url','https://vector.example.com');
 assert.equal(connection.snapshot().advanced,true);
 connection.dispose();
});

test('model discovery records a finite list and never treats it as a passing vector test',async()=>{
 const api=fakeApi({'/api/settings/models':{models:[{id:'m1'},null,{id:7}],notice:'目录仅作候选'}});
 const connection=createEmbeddingConnection({api,providers,saved:{status:'not_configured'},onChange:()=>{}});
 connection.choose('remote');connection.input('embedding_api_key','secret');
 await connection.models();await new Promise(resolve=>setTimeout(resolve,0));
 assert.deepEqual(connection.snapshot().models,[{id:'m1'}]);assert.match(connection.snapshot().notice,/仍需实际向量测试/);
 assert.equal(connection.snapshot().busy,'');connection.dispose();
});

test('a passing test creates a receipt and the save response alone marks the connection connected',async()=>{
 const saved={provider:'local_bge',model:'m',status:'connected',has_key:false,base_url:''};
 const api=fakeApi({'/api/settings/test':{validation_token:'ticket',dimension:768},'/api/settings/save':{embedding:{provider:'local_bge',model:'m',status:'connected',dimension:768,profile_id:'ep_new'}}});
 let savedPayload=null;const connection=createEmbeddingConnection({api,providers,saved,onChange:()=>{},onSaved:value=>savedPayload=value});
 await connection.save();await new Promise(resolve=>setTimeout(resolve,0));
 assert.deepEqual(api.calls.map(call=>call.path),['/api/settings/test','/api/settings/save']);
 assert.equal(api.calls[1].body.validation_token,'ticket');assert.equal(api.calls[1].body.dimension,undefined);
 assert.equal(connection.snapshot().saved.profile_id,'ep_new');assert.equal(connection.snapshot().success,true);
 assert.equal(connection.secret(),'');assert.ok(savedPayload);
 connection.dispose();
});

test('save failures keep a test receipt for retry and named model failures get a specific title',async()=>{
 const failing=new Error('模型未准备');failing.code='embedding_model_not_ready';
 const api=fakeApi({'/api/settings/test':{validation_token:'ticket',dimension:512},'/api/settings/save':failing});
 const connection=createEmbeddingConnection({api,providers,saved:{provider:'local_bge',model:'m',status:'connected',has_key:false,base_url:''},onChange:()=>{}});
 await connection.save();await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(connection.snapshot().retrySave,true);assert.equal(connection.snapshot().errorTitle,'本机模型尚未准备');
 api.calls.length=0;connection.dispose();
});

test('reusing a saved key is explicit and deleting a connection clears the receipt',async()=>{
 const saved={provider:'remote',model:'m1',base_url:'https://api.example.com/',status:'connected',has_key:true};
 assert.deepEqual(embeddingPayload({provider:'remote',model:'m1',base_url:'https://api.example.com'},'',saved),{kind:'embedding',provider:'remote',model:'m1',base_url:'https://api.example.com',use_saved_key:true});
 assert.equal(normalizeEmbedding({status:'weird'}).status,'weird');
 const api=fakeApi({'/api/settings/delete':{embedding:{status:'not_configured'}}});
 const connection=createEmbeddingConnection({api,providers,saved,onChange:()=>{}});
 await connection.remove();await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(api.calls[0].path,'/api/settings/delete');assert.match(connection.snapshot().notice,/向量连接已移除/);
 connection.dispose();
});

test('cancelled or disposed requests cannot update the connection state',async()=>{
 let reject;const api=(path,{signal})=>new Promise((resolve,fail)=>{signal.addEventListener('abort',()=>{const error=new Error('aborted');error.name='AbortError';fail(error);});reject=fail;});
 const connection=createEmbeddingConnection({api,providers,saved:{provider:'local_bge',model:'m',status:'connected',has_key:false,base_url:''},onChange:()=>{}});
 const pending=connection.save();connection.dispose();reject?.(Object.assign(new Error('late'),{name:'AbortError'}));
 await pending;
 assert.equal(connection.snapshot().success,false);assert.equal(connection.secret(),'');
});

import {renderEmbeddingSettings} from '../static/embedding-settings.mjs';

test('embedding form branches show manual model entry, stored keys and custom endpoints',()=>{
 const remote={id:'remote',label:'远端向量',requires_key:true,models_path:'/v1/models',note:'按厂商文档填写'};
 const manual=renderEmbeddingSettings({providers:[remote],saved:{provider:'remote',model:'m1',base_url:'https://api.example.com/',status:'connected',has_key:true},draft:{provider:'remote',model:'m1',base_url:'https://api.example.com/'},models:[],manualModel:true});
 assert.match(manual,/填写可用向量模型 ID/);assert.match(manual,/已安全保存/);assert.match(manual,/留空继续使用这项连接已保存的 Key/);assert.match(manual,/接口与高级设置/);assert.match(manual,/按厂商文档填写/);
 const selected=renderEmbeddingSettings({providers:[remote],saved:{provider:'remote',model:'m2',status:'not_configured'},draft:{provider:'remote',model:'m2',base_url:''},models:[{id:'m1',label:'模型一'},{id:'m2'}]});
 assert.match(selected,/<option value="m2" selected>/);assert.match(selected,/手动填写模型 ID/);
 const local=renderEmbeddingSettings({providers:[providers[0]],saved:{provider:'local_bge',model:'BAAI/bge-small-zh-v1.5',status:'connected',dimension:512,model_revision:'rev-1'},draft:{provider:'local_bge',model:'BAAI/bge-small-zh-v1.5'}});
 assert.match(local,/首次部署需准备约 95 MB/);assert.match(local,/已保存连接/);assert.match(local,/rev-1/);assert.match(local,/移除连接/);
 assert.ok(!local.includes('name="embedding_api_key"'));
});

test('embedding feedback distinguishes errors, success, busy states and an unavailable provider list',()=>{
 const empty=renderEmbeddingSettings({providers:[]});
 assert.match(empty,/知识检索连接尚未准备好/);
 const error=renderEmbeddingSettings({providers:[providers[0]],saved:{status:'not_configured'},error:'<script>bad()</script>',errorTitle:'测试未通过'});
 assert.match(error,/测试未通过/);assert.ok(!error.includes('<script>bad()</script>'));
 const success=renderEmbeddingSettings({providers:[providers[0]],saved:{status:'not_configured'},success:true,notice:'已保存并验证。'});
 assert.match(success,/向量连接已验证并保存/);assert.match(success,/已保存并验证/);
 const busy=renderEmbeddingSettings({providers:[providers[0]],saved:{status:'not_configured'},busy:'test'});
 assert.match(busy,/正在测试向量/);assert.match(busy,/disabled/);
 const retry=renderEmbeddingSettings({providers:[providers[0]],saved:{status:'not_configured'},retrySave:true,notice:'保存待重试。'});
 assert.match(retry,/重试保存/);
});
