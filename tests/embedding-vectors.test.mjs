import test from 'node:test';
import assert from 'node:assert/strict';
import {renderVectorSettings,vectorStatus,vectorStateLabel} from '../static/embedding-vectors.mjs';

const saved={status:'connected',provider:'local_bge',model:'BAAI/bge-small-zh-v1.5',dimension:512,profile_id:'ep_a'};
const space={id:'vs_a',state:'ready',provider:'local_bge',model:'BAAI/bge-small-zh-v1.5',dimension:512,profile_id:'ep_a',sources:3,segments:9,windows:12,completed_count:12,expected_count:12};
const ready={status:'ready',backend:'milvus_lite',active_space_id:'vs_a',active_profile_id:'ep_a',active_provider:'local_bge',spaces:[space],jobs:{pending:0,running:0,succeeded:12,failed:0,cancelled:0,uncertain:0},dirty:false,building:false,error_code:'',last_build:{embedded_inputs:12,cache_hits:0,provider_calls:1,input_tokens:1200,usage_known:true},current:{sources:3,segments:9,windows:null},embedding_model:{state:'ready',files_verified:5,total_files:5}};
const base={provider:{id:'local_bge',requires_key:false,local:true},saved,vectors:ready};

test('serving index, desired identity and actual build counts are shown without inventing totals',()=>{
 const html=renderVectorSettings(base);
 assert.match(html,/目标模型（desired）/);assert.match(html,/当前服务索引（serving）/);assert.match(html,/BAAI\/bge-small-zh-v1\.5 · 512 维/);
 assert.match(html,/已登记 3 份资料 · 9 个片段 · 12 个窗口/);assert.match(html,/完成 12 \/ 12 个窗口/);
 assert.match(html,/实际调用 1 次/);assert.match(html,/缓存复用 0 个窗口/);assert.match(html,/输入 1200 token/);
 assert.match(html,/已校验 5 \/ 5 个模型文件/);assert.match(html,/已有可用语义索引/);
 assert.ok(!html.includes('全国'));assert.ok(!html.includes('全部学校'));
});

test('desired model changes are labelled pending while the old serving index remains explicit',()=>{
 const html=renderVectorSettings({...base,saved:{...saved,profile_id:'ep_b',model:'other-model'},vectors:{...ready,dirty:true}});
 assert.equal(vectorStatus(ready,{...saved,model:'other-model'}).outdated,true);
 assert.equal(vectorStateLabel({...ready,dirty:true},{...saved,model:'other-model'}),'模型或资料已变化，待更新索引');
 assert.match(html,/目标模型或资料范围已变化/);assert.match(html,/新模型失败不会把目标标签贴到旧索引上/);assert.match(html,/更新索引/);
});

test('a staging space is separate from serving and failed builds keep the old generation',()=>{
 const staging={...space,id:'vs_b',state:'staging',profile_id:'ep_b',model:'other-model'};
 const html=renderVectorSettings({...base,vectors:{...ready,spaces:[space,staging],building:true,dirty:true,jobs:{pending:2,running:1,succeeded:0,failed:0,cancelled:0,uncertain:0}}});
 assert.match(html,/正在建立（staging）/);assert.match(html,/staging · 尚未切换服务/);assert.match(html,/正在建立新索引/);assert.match(html,/停止构建/);
 assert.ok(!html.includes('vectors-build"'),'a running build cannot be started twice');
});

test('an unprepared local model requires an explicit prepare action and polling renders no implicit work',()=>{
 const html=renderVectorSettings({...base,vectors:{...ready,embedding_model:{state:'not_prepared',files_verified:0,total_files:5}}});
 assert.match(html,/本机模型尚未准备/);assert.match(html,/data-embedding-action="vectors-prepare"/);assert.match(html,/准备本机模型/);
 assert.match(html,/data-embedding-action="vectors-build" disabled/);assert.ok(!html.includes('data-embedding-action="vectors-build" >'));
});

test('unknown usage stays unknown and unconfigured connections never claim a serving index',()=>{
 const unknown=renderVectorSettings({...base,saved:{status:'not_configured'},vectors:{...ready,last_build:{provider_calls:1,embedded_inputs:2,cache_hits:0,input_tokens:null,usage_known:false}}});
 assert.match(unknown,/输入 token 未知/);assert.match(unknown,/部分调用用量未知/);
 assert.match(unknown,/连接已移除，现有索引保留/);assert.ok(!unknown.includes('输入 0 token'));
});

test('model and error strings are escaped and no credential field is rendered',()=>{
 const html=renderVectorSettings({...base,saved:{...saved,model:'<img src=x onerror=alert(1)>'},vectors:{...ready,error_code:'<script>bad()</script>',spaces:[{...space,model:'<script>bad()</script>'}]}});
 assert.ok(!html.includes('<script>bad()</script>'));assert.ok(!html.includes('<img src=x'));assert.match(html,/&lt;script&gt;bad\(\)&lt;\/script&gt;/);
 assert.ok(!/api[_-]?key|sk-/i.test(html));
});

import {createVectorSettings} from '../static/embedding-vectors.mjs';

function apiFixture(responses){const calls=[];const fn=async(path,{body}={})=>{calls.push({path,body});const value=responses[path];if(typeof value==='function')return value(path,body);if(value instanceof Error)throw value;return value;};fn.calls=calls;return fn;}
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));

test('index loading keeps building state separate and a local unprepared model opens the section',async()=>{
 const building={...ready,building:true,jobs:{...ready.jobs,running:1},embedding_model:{state:'preparing'}};
 const api=apiFixture({'/api/knowledge/vectors':building});const states=[];
 const vectors=createVectorSettings({api,onChange:value=>states.push(value),isLocal:()=>true});
 await vectors.load();await tick();
 assert.equal(vectors.snapshot().vectorOpen,true);assert.equal(vectors.snapshot().vectorLoading,false);assert.equal(states.at(-1).vectors.building,true);
 vectors.dispose();await tick();
});

test('invalid index status and request errors are surfaced without pretending the index is ready',async()=>{
 const invalid=createVectorSettings({api:apiFixture({'/api/knowledge/vectors':[]}),onChange:()=>{}});
 await invalid.load();assert.match(invalid.snapshot().vectorError,/格式/);invalid.dispose();
 const rejected=createVectorSettings({api:apiFixture({'/api/knowledge/vectors':new Error('状态服务不可用')}),onChange:()=>{}});
 await rejected.load();assert.match(rejected.snapshot().vectorError,/状态服务不可用/);rejected.dispose();
});

test('build, cancel and prepare expose started, already-running and failed outcomes',async()=>{
 let buildResponse={started:true};
 const api=apiFixture({'/api/knowledge/vectors':ready,'/api/knowledge/vectors/build':()=>buildResponse,'/api/knowledge/vectors/cancel':{started:true},'/api/knowledge/embedding/prepare':{state:'ready',files_verified:5,total_files:5}});
 const vectors=createVectorSettings({api,onChange:()=>{},getProfileId:()=>'ep_a'});
 await vectors.click('vectors-confirm');await tick();
 assert.equal(vectors.snapshot().vectorBusy,'');assert.match(vectors.snapshot().vectorNotice,/已提交索引构建/);
 assert.equal(api.calls.find(call=>call.path.endsWith('/build')).body.profile_id,'ep_a');
 await vectors.click('vectors-cancel');await tick();assert.match(vectors.snapshot().vectorNotice,/已请求服务端停止/);
 await vectors.click('vectors-prepare');await tick();assert.match(vectors.snapshot().vectorNotice,/本机模型已就绪/);
 assert.equal(api.calls.filter(call=>call.path==='/api/knowledge/embedding/prepare').length,1);
 vectors.dispose();

 buildResponse={building:true};
 const running=createVectorSettings({api:apiFixture({'/api/knowledge/vectors':ready,'/api/knowledge/vectors/build':buildResponse}),onChange:()=>{}});
 await running.click('vectors-build');assert.equal(running.snapshot().vectorConfirm,true);
 await running.click('vectors-confirm');await tick();assert.match(running.snapshot().vectorNotice,/已有构建在进行/);running.dispose();

 const failed=createVectorSettings({api:apiFixture({'/api/knowledge/vectors/build':new Error('无法开始构建')}),onChange:()=>{}});
 await failed.click('vectors-confirm');await tick();assert.match(failed.snapshot().vectorError,/无法开始构建/);failed.dispose();
});

test('cancel and prepare failures stay visible and no click action is silently invented',async()=>{
 const api=apiFixture({'/api/knowledge/vectors/cancel':new Error('无法停止'),'/api/knowledge/embedding/prepare':new Error('无法准备')});
 const vectors=createVectorSettings({api,onChange:()=>{}});
 await vectors.click('vectors-cancel');await tick();assert.match(vectors.snapshot().vectorError,/无法停止/);
 await vectors.click('vectors-prepare');await tick();assert.match(vectors.snapshot().vectorError,/无法准备/);
 assert.equal(vectors.click('unknown'),false);assert.equal(vectors.click('vectors-dismiss'),true);assert.equal(vectors.snapshot().vectorConfirm,false);
 vectors.open(true);vectors.open(false);assert.equal(vectors.snapshot().vectorOpen,false);vectors.dispose();
});

test('model preparation failures, unknown usage and partial counts are named without zeros',()=>{
 const invalid=renderVectorSettings({...base,vectors:{...ready,embedding_model:{state:'invalid'}}});
 assert.match(invalid,/模型文件校验未通过/);
 const failed=renderVectorSettings({...base,vectors:{...ready,embedding_model:{state:'failed',error_code:'checksum_mismatch'}}});
 assert.match(failed,/本机模型准备失败 · checksum_mismatch/);
 const unknown=renderVectorSettings({...base,vectors:{...ready,last_build:{provider_calls:1,usage_known:false},current:{sources:2,segments:4,windows:null}}});
 assert.match(unknown,/输入 token 未知/);assert.match(unknown,/已登记 3 份资料/);
 const partial=renderVectorSettings({...base,vectors:{...ready,last_build:{provider_calls:1,cache_hits:2,embedded_inputs:3,input_tokens:null,usage_known:true}}});
 assert.match(partial,/缓存复用 2 个窗口/);assert.match(partial,/生成 3 个输入向量/);assert.match(partial,/输入 token 未知/);
});

test('index states distinguish failed generations, removed connections and dirty ready spaces',()=>{
 assert.equal(vectorStateLabel({...ready,building:true,jobs:{running:0,failed:1}},saved),'新索引未完成，保留上一代');
 assert.equal(vectorStateLabel({...ready,building:true,jobs:{running:0,failed:0}},saved),'正在准备新索引');
 assert.equal(vectorStateLabel({status:'failed',spaces:[]},saved),'语义索引未完成');
 assert.equal(vectorStateLabel({...ready,spaces:[space],active_space_id:'vs_a'},{status:'not_configured'}),'连接已移除，现有索引保留');
 assert.equal(vectorStateLabel({...ready,dirty:true},saved),'模型或资料已变化，待更新索引');
 const html=renderVectorSettings({...base,vectors:{...ready,error_code:'build_failed',jobs:{...ready.jobs,uncertain:1},last_build:{provider_calls:1,usage_known:false}}});
 assert.match(html,/索引任务未完成 · build_failed/);assert.match(html,/待确认/);assert.match(html,/不会在打开页面时自动重复执行或计费/);
 assert.match(html,/部分调用用量未知，不推算费用/);
});

test('explicit build confirmation, notices and disabled actions are rendered from real state',()=>{
 const blocked=renderVectorSettings({...base,vectors:{...ready,embedding_model:{state:'not_prepared'}},vectorBusy:'build'});
 assert.match(blocked,/data-embedding-action="vectors-build" disabled/);assert.match(blocked,/正在提交…/);
 const confirm=renderVectorSettings({...base,vectorConfirm:true,vectorNotice:'已提交构建。'});
 assert.match(confirm,/开始建立语义索引/);assert.match(confirm,/data-embedding-action="vectors-confirm"/);assert.match(confirm,/data-embedding-action="vectors-dismiss"/);assert.match(confirm,/已提交构建。|已提交索引/);
 const error=renderVectorSettings({...base,vectorError:'<script>bad()</script>',vectorLoading:true});
 assert.match(error,/状态暂时无法读取/);assert.match(error,/重新读取状态/);assert.ok(!error.includes('<script>bad()</script>'));
 const remote=renderVectorSettings({...base,provider:{id:'remote',requires_key:true},saved:{...saved,provider:'remote'},vectors:{...ready,embedding_model:{state:'ready'}}});
 assert.match(remote,/云端模型可能消耗服务商额度/);assert.ok(!remote.includes('准备本机模型'));
 const disconnected=renderVectorSettings({...base,saved:{status:'not_configured'},vectors:{status:'not_ready',spaces:[],jobs:{}}});
 assert.match(disconnected,/先测试并保存上方的向量模型连接/);assert.ok(!disconnected.includes('data-embedding-action="vectors-build"'));
});
