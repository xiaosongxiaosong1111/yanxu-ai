/* Run: AGENT_URL=http://127.0.0.1:4180 node tests/browser-agent.cjs
 * All successful external-service responses below are labelled browser fixtures.
 * Real service checks never submit a key or start a configured AI task.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const base = (process.env.AGENT_URL || 'http://127.0.0.1:4180').replace(/\/+$/, '');
const fixturesOnly=process.env.AGENT_FIXTURES_ONLY==='1';
const origin = new URL(base).origin;
const output = path.resolve(__dirname, '../data/runtime/agent-upgrade');
// Explicit dummy values used only by intercepted browser fixtures.
const FIXTURE_AI_KEY = 'E2E-FIXTURE-ONLY-NOT-A-REAL-KEY';
const FIXTURE_REDACTED_KEY = '<fixture-key>';
const FIXTURE_VALIDATION_TOKEN = 'fixture-validation-ticket';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const bounded = promise => Promise.race([promise,delay(6000).then(()=>{throw new Error('Expected fixture request did not start within 6s');})]);
fs.mkdirSync(output, {recursive:true});
const results=[], requests=[], responses=[], errors=[], screenshots=[];
let browser, live, providers, liveSettings;
const clone = value => JSON.parse(JSON.stringify(value));
const emptySettings = () => ({ai:{provider:'',model:'',base_url:'',status:'not_configured',has_key:false},yuanbao:{provider:'yuanbao',status:'not_configured',has_key:false},storage:{mode:'browser_fixture',persistent:false}});
const envelope = data => ({ok:true,data,error:null});
const good = (route,data,status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(envelope(data))});
const bad = (route,code,message,status=400) => route.fulfill({status,contentType:'application/json',body:JSON.stringify({ok:false,data:null,error:{code,message}})});
async function check(name,work) {
  if(fixturesOnly&&name.startsWith('live '))return;
  const start=Date.now();
  try {await work();results.push({name,ok:true,ms:Date.now()-start});console.log('PASS '+name);}
  catch(error) {results.push({name,ok:false,ms:Date.now()-start,error:error.message});console.log('FAIL '+name+' — '+error.message);if(live&&!live.isClosed())await shot(live,'failure-'+results.length).catch(()=>{});}
}
async function ready(page,selector='#ya-query') {await page.locator(selector).waitFor({state:'visible',timeout:10000});}
async function shot(page,name) {
  const filename='e2e-'+name+'.png';
  await page.screenshot({path:path.join(output,filename),fullPage:true});screenshots.push(filename);
}
async function watch(page,label) {
  page.setDefaultTimeout(6000);
  page.on('pageerror',error=>errors.push({page:label,message:error.message}));
  page.on('response',response=>{if(response.url().startsWith(origin+'/')&&response.status()>=400)responses.push({page:label,path:response.url().slice(origin.length),status:response.status()});});
  page.on('request',request=>{
    if(!request.url().startsWith(origin+'/api/'))return;
    let body;try {body=request.postDataJSON();}catch{}
    if(body){body={...body};if(body.api_key)body.api_key=FIXTURE_REDACTED_KEY;if(body.validation_token)body.validation_token='<fixture-ticket>';}
    requests.push({page:label,method:request.method(),path:request.url().slice(origin.length),...(body?{body}:{})});
  });
  await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.fallback():route.abort('blockedbyclient'));
}
async function json(page,path,body) {
  return page.evaluate(async ({path,body})=>{const response=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},...(body?{body:JSON.stringify(body)}:{})});return {status:response.status,...await response.json()};},{path,body});
}
async function fixturePage(label,handler) {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});await watch(page,label);
  await page.route('**/api/settings',route=>good(route,emptySettings()));
  await page.route('**/api/research?*',route=>good(route,{tasks:[]}));
  if(handler)await page.route('**/api/**',handler);
  await page.addInitScript(()=>{window.addEventListener('DOMContentLoaded',()=>{const mark=document.createElement('div');mark.id='e2e-fixture-label';mark.textContent='浏览器测试夹具 · 模拟接口响应 · 非真实 AI / 元宝运行成果';mark.style.cssText='position:fixed;left:0;right:0;bottom:0;padding:7px;background:#752b91;color:white;text-align:center;z-index:999999;font:13px sans-serif';document.body.append(mark);});});
  return page;
}
async function settingsPage(page) {await page.goto(base+'/#/settings');await ready(page,'#ys-provider');await page.locator('#ys-provider option').first().waitFor({state:'attached'});}
async function noSecretLeak(page) {
  const leak=await page.evaluate(value=>({url:location.href.includes(value),storage:JSON.stringify({...localStorage}).includes(value),attributes:[...document.querySelectorAll('*')].some(node=>[...node.attributes].some(attribute=>attribute.value.includes(value))),text:document.body.innerText.includes(value)}),FIXTURE_AI_KEY);
  assert.deepEqual(leak,{url:false,storage:false,attributes:false,text:false});
}
async function overflow(page,label) {
  for(const width of [320,390,1440,2552]) {
    await page.setViewportSize({width,height:900});
    const dimensions=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,offenders:[...document.querySelectorAll('main *,header *,footer *')].filter(node=>{const r=node.getBoundingClientRect();return r.width&&r.right>innerWidth+1;}).slice(0,8).map(node=>({tag:node.tagName,class:node.className,right:Math.round(node.getBoundingClientRect().right)}))}));
    assert.ok(dimensions.scroll<=width+1,label+' '+width+'px overflow '+JSON.stringify(dimensions));
  }
  await page.setViewportSize({width:1440,height:1000});
}
function task(status='running',extra={}) {
  return {id:'r_11111111111111111111111111111111',research_id:'r_11111111111111111111111111111111',query:'[测试夹具] 408 备考与天津地区研究',status,target_year:2028,reference_year:2027,created_at:'2026-11-02T00:00:00Z',updated_at:'2026-11-02T00:00:00Z',last_event_id:1,events:[{id:1,title:'[测试夹具] 需求已保存',message:'模拟任务，仅用于交互验证。'}],usage:{model_calls:0,searches:0,documents:0},report:{},...extra};
}
const program={id:'e2e-fixture-program',unit_name:'[测试夹具] 示例大学',year:2027,name:'计算机技术',code:'085404',department:'计算机学院',direction:'00 不区分方向',study_mode:'全日制',subjects:[{exam_unit:4,scope:'national',code:'408',name:'计算机学科专业基础'}]};
const report={summary:'[测试夹具] 已形成候选与待确认条件',assistant_summary:'这是一份用于验证交互的模拟报告。',candidates:[program],evidence:[{title:'[测试夹具] 2027 年招生目录',unit_name:program.unit_name,year:2027,status:'official_original_read',url:'https://example.edu.cn/e2e-fixture',excerpt:'模拟原文，仅用于验证来源展示。'},{title:'[测试夹具] 检索线索',status:'search_lead',url:'https://example.edu.cn/e2e-search',excerpt:'模拟搜索摘要。'},{title:'[测试夹具] 不安全链接不得渲染',url:'javascript:alert(1)',excerpt:'<img src=x onerror=alert(1)>'}],gaps:['[测试夹具] 2028 正式目录尚未核验'],algorithm:{matching:{summary:{input_count:1,matched_count:0,unknown_count:1},matches:[],needs_confirmation:[{program,subject_changes:{count:0,uncertain:false},reasons:['[测试夹具] 科目一致，报考资格仍需核实。']}],excluded:[]}},profile_proposal:{requires_confirmation:true,updates:{provinces:['天津'],subjects:program.subjects},evidence:[{field:'provinces',quote:'希望留在天津'}]}};

(async()=>{
  browser=await chromium.launch({channel:'msedge',headless:true});
  live=await browser.newPage({viewport:{width:1440,height:1000}});await watch(live,'live');
  await check('live home: rough input, service status, keyboard and screenshot',async()=>{
    await live.goto(base+'/#/');await ready(live);
    const settings=await json(live,'/api/settings');assert.equal(settings.ok,true);liveSettings=settings.data;
    providers=(await json(live,'/api/settings/providers')).data.providers;assert.equal(providers.length,16);
    await live.locator('[data-agent-example="0"]').click();assert.equal(await live.locator('#ya-query').inputValue(),'我想考计算机');await live.locator('#ya-query').fill('');
    await live.keyboard.press('Tab');assert.equal(await live.locator(':focus').getAttribute('type'),'submit');
    await live.locator('.ya-connection-strip').filter({hasText:'AI 模型'}).waitFor();assert.ok((await live.locator('.ya-connection-strip').innerText()).includes('待配置'));
    await shot(live,'live-home');
  });
  await check('live home responsive: 320/390/1440/2552',()=>overflow(live,'home'));
  await check('live settings: all 16 providers, endpoint hints, local key rules, secret clearing',async()=>{
    await live.getByRole('button',{name:'AI与元宝设置',exact:true}).click();await ready(live,'#ys-provider');
    await live.waitForFunction(()=>document.querySelector('#ys-provider')?.options.length===16);
    for(const provider of providers){await live.locator('#ys-provider').selectOption(provider.id);assert.equal(await live.locator('#ys-provider').inputValue(),provider.id);assert.equal(await live.locator('#ys-base-url').inputValue(),provider.default_base_url||'');}
    await live.locator('#ys-provider').selectOption('custom');assert.equal(await live.locator('.ys-advanced').getAttribute('open'),'');assert.ok((await live.locator('.ys-advanced').innerText()).includes('必填'));
    await live.locator('#ys-api-key').fill(FIXTURE_AI_KEY);await live.locator('[data-settings-action="reveal"]').click();assert.equal(await live.locator('#ys-api-key').getAttribute('type'),'text');await live.locator('[data-settings-action="reveal"]').click();assert.equal(await live.locator('#ys-api-key').getAttribute('type'),'password');await noSecretLeak(live);
    await live.locator('#ys-provider').selectOption('ollama');assert.equal(await live.locator('#ys-api-key').inputValue(),'');assert.ok((await live.locator('label[for="ys-api-key"]').innerText()).includes('本地模型无需填写'));
    await live.locator('#ys-api-key').fill(FIXTURE_AI_KEY);await live.locator('[data-settings-tab="yuanbao"]').click();await live.locator('[name="auth_mode"][value="api_key"]').check();assert.equal(await live.locator('#ys-api-key').inputValue(),'');await noSecretLeak(live);
    await live.locator('[data-settings-tab="ai"]').click();await live.locator('#ys-provider').selectOption('deepseek');await shot(live,'live-settings');
  });
  await check('live settings responsive: 320/390/1440/2552',()=>overflow(live,'settings'));
  await check('live real POST validation rejects empty key without any external call',async()=>{
    if(liveSettings.ai.has_key)throw new Error('Skip required: real saved Key exists; refusing any live provider operation.');
    await live.locator('#ys-model').fill('e2e-invalid-no-key');await live.getByRole('button',{name:'测试并保存',exact:true}).click();await live.locator('.ys-feedback.is-error').waitFor();assert.ok((await live.locator('.ys-feedback').innerText()).includes('Key'));
    const rejected=await json(live,'/api/settings/test',{kind:'ai',provider:'deepseek',model:'e2e-invalid-no-key',use_saved_key:false});assert.equal(rejected.ok,false);assert.ok(rejected.status>=400);
    const recent=requests.filter(x=>x.page==='live'&&x.method==='POST');assert.equal(recent.filter(x=>x.path==='/api/settings/test').length,1);assert.equal(recent.filter(x=>x.path==='/api/settings/save').length,0);
    const current=(await json(live,'/api/settings')).data;assert.equal(current.ai.has_key,false);assert.equal(current.ai.status,'not_configured');
  });
  await check('live research: save rough intent, wait for config, continue and cancel via actual server',async()=>{
    assert.equal(liveSettings.ai.has_key,false,'Never start real AI research with a saved Key');
    await live.locator('[data-settings-action="back"]').click();await ready(live);await live.locator('#ya-query').fill('[浏览器测试] 我想考计算机');await live.keyboard.press('Control+Enter');await live.locator('.ya-setup-callout').waitFor();assert.ok((await live.locator('.ya-task-heading h1').innerText()).includes('我想考计算机'));
    const before=requests.filter(x=>x.page==='live'&&x.path==='/api/research'&&x.method==='POST').length;assert.equal(before,1);
    const taskUrl=live.url();await live.locator('.ya-setup-callout [data-agent-action="settings"]').click();await ready(live,'#ys-provider');await live.locator('[data-settings-action="back"]').click();await live.locator('.ya-setup-callout').waitFor();assert.equal(live.url(),taskUrl,'Settings back must preserve the current research task');
    await live.locator('[data-agent-action="continue"]').click();await live.locator('.ya-setup-callout').waitFor();
    const id=new URLSearchParams(new URL(live.url()).hash.split('?')[1]).get('id');assert.ok(id);
    const detail=await json(live,'/api/research?research_id='+id);assert.equal(detail.data.status,'waiting_configuration');assert.equal(detail.data.usage?.model_calls||0,0);
    assert.equal((await json(live,'/api/research/cancel',{research_id:id})).data.status,'cancelled');
    await shot(live,'live-waiting-configuration');
  });

  await check('fixture home: late recent tasks response preserves already-loaded connection status',async()=>{
    const page=await fixturePage('fixture-home-race',async route=>{const url=new URL(route.request().url());if(url.pathname==='/api/research'&&url.searchParams.has('limit')){await delay(200);return good(route,{tasks:[]});}return route.fallback();});
    const recentResponse=page.waitForResponse(response=>response.url().includes('/api/research?limit='));await page.goto(base+'/#/');await ready(page);await recentResponse;await page.locator('.ya-connection-strip').filter({hasText:'AI 模型'}).waitFor();assert.ok((await page.locator('.ya-connection-strip').innerText()).includes('待配置'));await page.close();
  });

  const state={saved:emptySettings(),mode:'models-error',posts:[],token:FIXTURE_VALIDATION_TOKEN,modelsStarted:null,releaseModels:null};
  const settings=await fixturePage('fixture-settings',async route=>{
    const request=route.request(),url=new URL(request.url()),p=url.pathname;
    if(p==='/api/settings')return good(route,state.saved);
    if(!p.startsWith('/api/settings/'))return route.fallback();
    if(p==='/api/settings/providers')return route.fallback();
    const body=request.postDataJSON();state.posts.push({path:p,body:clone(body)});
    if(p.endsWith('/models')){
      if(state.mode==='models-error')return bad(route,'fixture_model_error','[测试夹具] 当前 Key 无法读取模型');
      if(state.mode==='models-delay'){state.modelsStarted?.();await new Promise(resolve=>state.releaseModels=resolve);return good(route,{models:[{id:'stale-fixture-model'}],discovery:'account'});}
      return good(route,{models:[{id:'fixture-model-1',label:'测试夹具模型'}],discovery:'account'});
    }
    if(p.endsWith('/test')){await delay(100);if(state.mode==='test-error')return bad(route,'fixture_test_error','[测试夹具] 模型鉴权失败');return good(route,{validation_token:state.token});}
    if(p.endsWith('/save')){
      if(state.saveFailOnce){state.saveFailOnce=false;return bad(route,'fixture_save_unavailable','[测试夹具] 暂时无法保存，请重试',503);}
      if(state.mode==='save-error')return bad(route,'validation_expired','[测试夹具] 验证票据已失效，请重新测试');
      assert.equal(body.validation_token,state.token);state.saved={...state.saved,[body.kind]:{provider:body.provider,model:body.model||'',base_url:body.base_url||'',has_key:true,status:'connected'}};return good(route,state.saved);
    }
    if(p.endsWith('/delete')){state.saved={...state.saved,[body.kind]:emptySettings()[body.kind]};return good(route,state.saved);}
    return route.fallback();
  });
  await check('fixture settings: discovery error then success never marks connection verified',async()=>{
    await settingsPage(settings);await settings.locator('#ys-api-key').fill(FIXTURE_AI_KEY);await settings.locator('[data-settings-action="models"]').click();await settings.locator('.ys-feedback.is-error').waitFor();assert.ok((await settings.locator('.ys-feedback').innerText()).includes('测试夹具'));
    state.mode='models-good';await settings.locator('[data-settings-action="models"]').click();await settings.locator('#ys-model option[value="fixture-model-1"]').waitFor({state:'attached'});assert.equal(await settings.locator('#ys-model').inputValue(),'fixture-model-1');assert.equal(await settings.locator('.ys-status-dot.is-connected').count(),0);await noSecretLeak(settings);
  });
  await check('fixture settings: auth failure, rejected save ticket, valid test/save once',async()=>{
    state.mode='test-error';await settings.getByRole('button',{name:'测试并保存',exact:true}).click();await settings.locator('.ys-feedback.is-error').waitFor();assert.equal(state.posts.filter(x=>x.path.endsWith('/save')).length,0);
    state.mode='save-error';await settings.getByRole('button',{name:'测试并保存',exact:true}).click();await settings.locator('.ys-feedback.is-error').waitFor();assert.ok((await settings.locator('.ys-feedback').innerText()).includes('票据'));
    state.mode='success';const count=state.posts.length;await settings.getByRole('button',{name:'测试并保存',exact:true}).click();await settings.locator('#ys-settings-form').dispatchEvent('submit');await settings.locator('.ys-feedback.is-success').waitFor();assert.deepEqual(state.posts.slice(count).map(x=>x.path),['/api/settings/test','/api/settings/save']);assert.equal(await settings.locator('#ys-api-key').inputValue(),'');await noSecretLeak(settings);await shot(settings,'fixture-settings-verified');
    await settings.locator('[data-settings-action="model-mode"]').click();await settings.locator('#ys-model').fill('edited-fixture-model');assert.equal(await settings.locator('.ys-feedback.is-success').count(),0);
    await settings.locator('#ys-provider').selectOption('openai');await settings.locator('#ys-model').fill('fixture-openai');const before=state.posts.length;await settings.getByRole('button',{name:'测试并保存',exact:true}).click();await settings.locator('.ys-feedback.is-error').waitFor();assert.equal(state.posts.length,before,'A different provider must require its own Key');await settings.locator('#ys-api-key').fill(FIXTURE_AI_KEY);await settings.getByRole('button',{name:'测试并保存',exact:true}).click();await settings.locator('.ys-feedback.is-success').waitFor();assert.equal(state.posts.at(-1).body.use_saved_key,false,'A different provider must not borrow the saved Key');
  });
  await check('fixture settings: stale model discovery cannot overwrite changed provider',async()=>{
    state.mode='models-delay';const started=new Promise(resolve=>state.modelsStarted=resolve);await settings.locator('[data-settings-action="models"]').click();await bounded(started);await settings.locator('#ys-provider').selectOption('gemini');state.releaseModels();await delay(150);assert.equal(await settings.locator('#ys-provider').inputValue(),'gemini');assert.equal(await settings.locator('#ys-model option[value="stale-fixture-model"]').count(),0);assert.equal(await settings.locator('#ys-api-key').inputValue(),'');
  });
  await check('fixture settings: retrying transient save does not repeat chargeable connection test',async()=>{
    state.mode='success';state.saveFailOnce=true;await settings.locator('#ys-api-key').fill(FIXTURE_AI_KEY);await settings.locator('#ys-model').fill('fixture-gemini');const before=state.posts.length;
    await settings.getByRole('button',{name:'测试并保存',exact:true}).click();await settings.getByRole('button',{name:'重试保存',exact:true}).waitFor();assert.deepEqual(state.posts.slice(before).map(x=>x.path),['/api/settings/test','/api/settings/save']);
    await settings.getByRole('button',{name:'重试保存',exact:true}).click();await settings.locator('.ys-feedback.is-success').waitFor();assert.deepEqual(state.posts.slice(before).map(x=>x.path),['/api/settings/test','/api/settings/save','/api/settings/save']);await noSecretLeak(settings);
  });
  await check('fixture settings: Yuanbao has separate key, test and verified status',async()=>{
    await settings.locator('[data-settings-tab="yuanbao"]').click();await settings.locator('[name="auth_mode"][value="api_key"]').check();assert.equal(await settings.locator('#ys-api-key').inputValue(),'');await settings.locator('#ys-api-key').fill(FIXTURE_AI_KEY);const before=state.posts.length;await settings.getByRole('button',{name:'测试并保存',exact:true}).click();await settings.locator('.ys-feedback.is-success').waitFor();assert.deepEqual(state.posts.slice(before).map(x=>x.body.kind),['yuanbao','yuanbao']);assert.equal(state.posts.at(-1).body.provider,'yuanbao');assert.equal(state.posts.at(-1).body.use_saved_key,false);assert.equal(await settings.locator('.ys-status-dot.is-connected').count(),2);assert.equal(await settings.locator('#ys-api-key').inputValue(),'');await noSecretLeak(settings);
    await settings.locator('[data-settings-action="delete"]').click();await settings.getByText('连接已移除，你可以重新选择服务并配置。').waitFor();assert.equal(state.saved.yuanbao.has_key,false);await settings.locator('[data-settings-tab="ai"]').click();
  });

  const flow={value:task(),reads:0,posts:[],pollStarted:null,releasePoll:null,late:false};
  const agent=await fixturePage('fixture-agent',async route=>{
    const request=route.request(),url=new URL(request.url()),p=url.pathname;
    if(p==='/api/research'&&request.method()==='POST'){flow.posts.push({path:p,body:request.postDataJSON()});return good(route,flow.value,202);}
    if(p==='/api/research'&&url.searchParams.has('research_id')){
      flow.reads++;
      if(flow.late&&url.searchParams.has('after_event')){flow.pollStarted?.();await new Promise(resolve=>flow.releasePoll=resolve);return good(route,task('completed',{report:{summary:'STALE RESULT MUST NOT REPLACE CANCEL'}}));}
      return good(route,flow.value);
    }
    if(p==='/api/research/cancel'){flow.posts.push({path:p,body:request.postDataJSON()});flow.value=task('cancelled');return good(route,flow.value);}
    if(p==='/api/research/continue'){flow.posts.push({path:p,body:request.postDataJSON()});flow.value=task('completed',{report:{...report,profile_proposal:{requires_confirmation:false}}});return good(route,flow.value,202);}
    return route.fallback();
  });
  await check('fixture agent: polling, keyboard submission, cancellation ignores late completed response',async()=>{
    await agent.goto(base+'/#/');await ready(agent);await agent.locator('#ya-query').fill('408');flow.late=true;const started=new Promise(resolve=>flow.pollStarted=resolve);await agent.keyboard.press('Control+Enter');await agent.locator('[data-agent-action="cancel"]').waitFor();await bounded(started);
    await agent.locator('[data-agent-action="cancel"]').click();await agent.locator('.ya-task-status').filter({hasText:'研究已取消'}).waitFor();flow.releasePoll();await delay(150);assert.equal(await agent.getByText('STALE RESULT MUST NOT REPLACE CANCEL').count(),0);assert.equal(flow.posts.filter(x=>x.path==='/api/research').length,1);assert.equal(flow.posts.filter(x=>x.path==='/api/research/cancel').length,1);
    const reads=flow.reads;await delay(1650);assert.equal(flow.reads,reads,'Cancelled task must stop polling');
  });
  await check('fixture agent: candidates, provenance, safe excerpts, algorithm and proposal confirmation',async()=>{
    flow.late=false;flow.value=task('awaiting_input',{report});await agent.reload();await agent.locator('[data-agent-action="confirm-profile"]').waitFor();assert.ok((await agent.locator('.ya-candidate').innerText()).includes('408'));
    await agent.locator('[data-agent-tab="sources"]').click();assert.equal(await agent.locator('[data-agent-tab="sources"]').getAttribute('aria-selected'),'true');assert.equal(await agent.locator('.ya-source-card a').count(),2);assert.equal(await agent.locator('.ya-source-card img').count(),0);await agent.locator('.ya-source-card').filter({hasText:'不安全链接'}).locator('.ya-source-excerpt summary').click();assert.ok((await agent.locator('.ya-source-list').innerText()).includes('<img src=x onerror=alert(1)>'));
    await agent.locator('[data-agent-tab="methods"]').click();assert.equal(await agent.locator('.ya-algorithm-counts strong').first().innerText(),'1');assert.ok((await agent.locator('.ya-algorithm-records').innerText()).includes('科目一致'));await shot(agent,'fixture-algorithm');
    await agent.locator('[data-agent-tab="overview"]').click();await shot(agent,'fixture-report');await agent.locator('#ya-followup').fill('未提交的草稿不可重复夹带');await agent.locator('[data-agent-action="confirm-profile"]').click();await agent.locator('.ya-task-status').filter({hasText:'本轮研究已完成'}).waitFor();assert.deepEqual(flow.posts.at(-1).body.profile,report.profile_proposal.updates);assert.ok(!JSON.stringify(flow.posts.at(-1).body).includes('未提交的草稿不可重复夹带'));
    await agent.locator('#ya-followup').fill('优先天津');await agent.keyboard.press('Control+Enter');await agent.waitForFunction(()=>document.querySelector('#ya-followup')?.value==='');assert.equal(flow.posts.at(-1).body.message,'优先天津');
  });
  await check('fixture report responsive: 320/390/1440/2552',()=>overflow(agent,'report'));
  await check('fixture keyboard: input edit, tab focus survives tab activation, no JS errors',async()=>{
    await agent.locator('[data-agent-tab="sources"]').focus();await agent.keyboard.press('Enter');assert.equal(await agent.locator(':focus').getAttribute('data-agent-tab'),'sources');assert.equal(await agent.locator('[role="tabpanel"]').getAttribute('aria-labelledby'),'ya-tab-sources');
    await settings.locator('#ys-model').focus();await settings.keyboard.type('abc');assert.equal(await settings.locator(':focus').getAttribute('id'),'ys-model');assert.deepEqual(errors,[]);
  });
})().catch(error=>{results.push({name:'harness',ok:false,error:error.stack});console.error(error);}).finally(async()=>{
  if(browser)await browser.close();
  const report={base,mode:fixturesOnly?'fixtures_only':'full',at:new Date().toISOString(),success:results.length>0&&results.every(x=>x.ok),results,errors,requests,responses,screenshots,notes:['Successful external AI and Yuanbao scenarios are explicitly marked route fixtures. No paid provider was called by this harness.',fixturesOnly?'Live scenarios skipped; provider success and saved settings are route fixtures.':'Real service POST checks use an empty key and an unconfigured AI task only. No real key is saved.','No Chromium unsafe-port override is enabled. Default URL uses port 4180.']};
  const reportPath=path.join(output,fixturesOnly?'e2e-fixtures-results.json':'e2e-results.json');fs.writeFileSync(reportPath,JSON.stringify(report,null,2));
  console.log(JSON.stringify({success:report.success,passed:results.filter(x=>x.ok).length,failed:results.filter(x=>!x.ok).length,report:reportPath}));
  process.exitCode=report.success?0:1;
});
