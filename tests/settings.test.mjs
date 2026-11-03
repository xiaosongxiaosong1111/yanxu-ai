import test from 'node:test';
import assert from 'node:assert/strict';
import {connectionLabel,settingsPayload,renderSettings,normalizeSettings} from '../static/settings.mjs';

// Dummy canaries check that rendered/public settings never expose credential fields.
const FIXTURE_RENDER_KEY='secret-canary';
const FIXTURE_SERVICE_KEY='fixture-service-key';
const FIXTURE_NORMALIZATION_KEY='never-echo-api';

test('a model directory is never reported as a verified generation connection',()=>{
 assert.equal(connectionLabel({status:'models_loaded',has_key:true}),'尚未验证');
 assert.equal(connectionLabel({status:'connected',has_key:true}),'连接已验证');
 assert.equal(connectionLabel({status:'not_configured'}),'尚未配置');
 assert.equal(connectionLabel({status:'not_tested',has_key:false}),'尚未验证');
});

test('failed provider loading offers a retry instead of an empty submittable form',()=>{
 const html=renderSettings({loadStatus:'error',error:'测试夹具：服务暂时不可用'});
 assert.match(html,/重新读取配置/);assert.match(html,/服务暂时不可用/);assert.ok(!html.includes('id="ys-settings-form"'));
});

test('save retry is explicit and a saved local model can be disconnected without a key',()=>{
 const html=renderSettings({saved:{ai:{provider:'ollama',has_key:false,status:'connected'},yuanbao:{}},providers:[{id:'ollama',label:'Ollama',requires_key:false,base_url:'http://127.0.0.1:11434/v1'}],draft:{provider:'ollama',model:'local-model'},tab:'ai',retrySave:true});
 assert.match(html,/重试保存/);assert.match(html,/移除连接/);assert.match(html,/本地模型无需填写/);
});

test('saved keys are reused only for the exact provider and never included in public state',()=>{
 const saved={ai:{provider:'deepseek',has_key:true,status:'connected',api_key:'never-echo'}};
 const publicState=normalizeSettings(saved);
 assert.ok(!JSON.stringify(publicState).includes('never-echo'));
 assert.equal(settingsPayload('ai',{provider:'deepseek',model:'deepseek-chat'},'',publicState).use_saved_key,true);
 assert.equal(settingsPayload('ai',{provider:'openai',model:'model'},'',publicState).use_saved_key,false);
 const draft=settingsPayload('ai',{provider:'openai',model:'model'},'fresh-secret',publicState);
 assert.equal(draft.api_key,'fresh-secret');assert.equal(draft.use_saved_key,false);
});

test('settings rendering escapes untrusted names and contains no credential value attribute',()=>{
 const html=renderSettings({providers:[{id:'x',label:'<img src=x onerror=alert(1)>',protocol:'openai',base_url:'https://example.com/v1'}],saved:{ai:{has_key:true,provider:'x',status:'connected',api_key:FIXTURE_RENDER_KEY},yuanbao:{}},draft:{provider:'x',model:'<model>'},tab:'ai',models:[]});
 assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('<img src=x'));
 assert.ok(!html.includes(FIXTURE_RENDER_KEY));assert.ok(!/name="api_key"[^>]*value=/.test(html));
 assert.match(html,/测试并保存/);assert.match(html,/读取模型/);
});

test('Yuanbao configuration is independent and untested settings do not imply activation',()=>{
 const state=normalizeSettings({ai:{provider:'a',status:'connected',has_key:true},yuanbao:{has_key:true,status:'saved'}});
 assert.equal(connectionLabel(state.yuanbao),'尚未验证');
 const body=settingsPayload('yuanbao',{},'',state);
 assert.equal(body.kind,'yuanbao');assert.equal(body.use_saved_key,true);assert.equal(Object.hasOwn(body,'model'),false);
 const html=renderSettings({saved:state,tab:'yuanbao',draft:{},providers:[],models:[]});
 assert.match(html,/元宝搜索/);assert.match(html,/真实检索/);assert.ok(!html.includes('name="model"'));
});

const pickerState={providers:[{id:'deepseek',label:'DeepSeek'}],tab:'ai',draft:{provider:'deepseek',model:'model-first'},models:[{id:'model-first',label:'First model'},{id:'model-second',label:'Second model'}]};

test('every discovered model remains selectable when the first model is already filled',()=>{
 const html=renderSettings(pickerState);
 const picker=html.match(/<select\b[^>]*id="ys-model"[^>]*>([\s\S]*?)<\/select>/);
 assert.ok(picker,'discovered models need an explicit picker, without prefix filtering');
 assert.match(picker[1],/<option value="model-first" selected>First model<\/option>/);
 assert.match(picker[1],/<option value="model-second"[^>]*>Second model<\/option>/);
 assert.ok(!html.includes('<datalist'));
 assert.match(html,/data-settings-action="model-mode"/);
});

test('a current model absent from the directory is preserved instead of silently replaced',()=>{
 const html=renderSettings({...pickerState,draft:{...pickerState.draft,model:'my-deployed-model'}});
 const picker=html.match(/<select\b[^>]*id="ys-model"[^>]*>([\s\S]*?)<\/select>/);
 assert.ok(picker);
 assert.match(picker[1],/<option value="my-deployed-model" selected>[^<]*my-deployed-model[^<]*<\/option>/);
 assert.match(picker[1],/value="model-first"/);
 assert.match(picker[1],/value="model-second"/);
});

test('manual model entry preserves the ID and offers a return to the fetched list',()=>{
 const html=renderSettings({...pickerState,manualModel:true,draft:{...pickerState.draft,model:'custom-model[1m]'}});
 assert.match(html,/<input id="ys-model" name="model"[^>]*value="custom-model\[1m\]"/);
 assert.match(html,/data-settings-action="model-mode"[^>]*>返回模型列表/);
 assert.ok(!/<select\b[^>]*id="ys-model"/.test(html));
 const unread=renderSettings({...pickerState,models:[]});
 assert.match(unread,/<input id="ys-model" name="model"/);
 assert.ok(!unread.includes('data-settings-action="model-mode"'));
});

test('model picker safely renders external model identifiers and names',()=>{
 const html=renderSettings({...pickerState,draft:{provider:'deepseek',model:'<current>'},models:[{id:'<other>',label:'<img src=x onerror=alert(1)>'}]});
 assert.match(html,/&lt;current&gt;/);assert.match(html,/&lt;other&gt;/);assert.match(html,/&lt;img/);
 assert.ok(!html.includes('<img src=x'));assert.ok(!html.includes('value="<current>"'));
});

test('standard Yuanbao payload contains only its credential pair and selected authentication mode',()=>{
 const body=settingsPayload('yuanbao',{auth_mode:'standard'},{secret_id:'fixture-id',secret_key:'fixture-secret'},{});
 assert.equal(body.auth_mode,'standard');assert.equal(body.secret_id,'fixture-id');assert.equal(body.secret_key,'fixture-secret');
 assert.equal(body.use_saved_key,false);assert.equal(Object.hasOwn(body,'api_key'),false);assert.equal(Object.hasOwn(body,'base_url'),false);
 const api=settingsPayload('yuanbao',{auth_mode:'api_key'},{api_key:FIXTURE_SERVICE_KEY,secret_key:'never-use'},{});
 assert.equal(api.api_key,FIXTURE_SERVICE_KEY);assert.equal(api.auth_mode,'api_key');assert.equal(Object.hasOwn(api,'secret_key'),false);
});

test('saved standard credentials are reused only as a complete pair for the same mode',()=>{
 const saved=normalizeSettings({yuanbao:{provider:'yuanbao',auth_mode:'standard',has_key:true,status:'connected'}});
 assert.equal(settingsPayload('yuanbao',{auth_mode:'standard'},{},saved).use_saved_key,true);
 for(const partial of [{secret_id:'new-id'},{secret_key:'new-key'}])assert.equal(settingsPayload('yuanbao',{auth_mode:'standard'},partial,saved).use_saved_key,false);
 assert.equal(settingsPayload('yuanbao',{auth_mode:'api_key'},{},saved).use_saved_key,false);
 const legacy=normalizeSettings({yuanbao:{provider:'yuanbao',has_key:true,status:'connected'}});
 assert.equal(legacy.yuanbao.auth_mode,'api_key');assert.equal(settingsPayload('yuanbao',{auth_mode:'standard'},{},legacy).use_saved_key,false);
});

test('public normalization preserves authentication mode without returning either standard credential',()=>{
 const saved=normalizeSettings({yuanbao:{auth_mode:'standard',has_key:true,secret_id:'never-echo-id',secret_key:'never-echo-key',api_key:FIXTURE_NORMALIZATION_KEY}});
 assert.equal(saved.yuanbao.auth_mode,'standard');assert.equal(saved.yuanbao.has_key,true);
 assert.doesNotMatch(JSON.stringify(saved),/never-echo/);
});

test('unconfigured Yuanbao defaults to clear standard credential fields and an explicit alternative mode',()=>{
 const html=renderSettings({tab:'yuanbao',saved:{},draft:{}});
 assert.match(html,/name="auth_mode" value="standard" checked/);assert.match(html,/name="auth_mode" value="api_key"/);
 assert.match(html,/id="ys-secret-id" name="secret_id" type="password"/);assert.match(html,/id="ys-secret-key" name="secret_key" type="password"/);
 assert.ok(!html.includes('name="api_key"'));assert.ok(!/name="secret_(?:id|key)"[^>]*value=/.test(html));
 assert.match(html,/腾讯云标准方式/);assert.match(html,/服务 API Key/);assert.match(html,/无需填写地域/);
});

test('saved legacy service keys retain API mode and busy auth switches are disabled',()=>{
 const saved={yuanbao:{provider:'yuanbao',has_key:true,status:'connected'}};
 const legacy=renderSettings({tab:'yuanbao',saved,draft:{}});
 assert.match(legacy,/name="auth_mode" value="api_key" checked/);assert.match(legacy,/id="ys-api-key" name="api_key"/);assert.ok(!legacy.includes('name="secret_key"'));
 const busy=renderSettings({tab:'yuanbao',yuanbaoAuthMode:'standard',busy:'test',saved:{},draft:{}});
 const radios=[...busy.matchAll(/<input[^>]*name="auth_mode"[^>]*>/g)];assert.equal(radios.length,2);assert.ok(radios.every(row=>row[0].includes('disabled')));
});
