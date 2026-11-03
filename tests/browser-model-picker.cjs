/* All API responses are fixtures. This test never reads or saves real credentials. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const base=(process.env.AGENT_URL||'http://127.0.0.1:4180').replace(/\/+$/,'');
const origin=new URL(base).origin;
const output=path.resolve(__dirname,'../data/runtime/model-picker-fix');
const FIXTURE_REDACTED_KEY='<fixture-key>';
const models=[{id:'fixture-flash',label:'Fixture Flash'},{id:'fixture-reasoner',label:'Fixture Reasoner'}];
const providers=[{id:'deepseek',label:'Fixture DeepSeek',default_base_url:'https://api.deepseek.com/v1',requires_key:true},{id:'openai',label:'Fixture OpenAI',default_base_url:'https://api.openai.com/v1',requires_key:true}];
fs.mkdirSync(output,{recursive:true});
const results=[],errors=[],posts=[];
let browser;
async function fixture(savedModel='',options={}){
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.setDefaultTimeout(6000);page.on('pageerror',error=>errors.push(error.message));
 let saved={ai:{provider:savedModel?'deepseek':'',model:savedModel,base_url:savedModel?providers[0].default_base_url:'',has_key:!!savedModel,status:savedModel?'connected':'not_configured'},yuanbao:{provider:'yuanbao',status:'not_configured',has_key:false},storage:{mode:'fixture',persistent:false}};
 const localPosts=[];let resolveStarted,releaseTest;
 const control={testStarted:new Promise(resolve=>resolveStarted=resolve),releaseTest:()=>releaseTest?.()};
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.origin!==origin)return route.abort();
  if(!url.pathname.startsWith('/api/'))return route.continue();
  const good=data=>route.fulfill({json:{ok:true,data,error:null}});
  if(request.method()==='POST'){
   const body=request.postDataJSON();localPosts.push({path:url.pathname,body});posts.push({path:url.pathname,body:{...body,...(body.api_key?{api_key:FIXTURE_REDACTED_KEY}:{})}});
   if(url.pathname==='/api/settings/models')return good({models,discovery:'remote',authentication_verified:false,notice:'测试夹具：两个可选模型，调用权限以生成测试为准。'});
   if(url.pathname==='/api/settings/test'){
    if(options.holdFirstTest&&localPosts.filter(row=>row.path==='/api/settings/test').length===1){const pending=new Promise(resolve=>releaseTest=resolve);resolveStarted();await pending;}
    return good({status:'connected',model:body.model,validation_token:'fixture-validation-ticket'});
   }
   if(url.pathname==='/api/settings/save'){
    if(options.failFirstSave&&localPosts.filter(row=>row.path==='/api/settings/save').length===1)return route.fulfill({status:503,json:{ok:false,data:null,error:{code:'settings_save_failed',message:'测试夹具：临时保存失败。'}}});
    saved={...saved,ai:{provider:body.provider,model:body.model,base_url:body.base_url,status:'connected',has_key:true}};
    return good(saved);
   }
   return route.abort('blockedbyclient');
  }
  if(url.pathname==='/api/settings/providers')return good({providers});
  if(url.pathname==='/api/settings')return good(saved);
  if(url.pathname==='/api/catalog-bootstrap')return good({revision:'fixture-revision',meta:{target_year:2028,reference_year:2027},units:[],coverage:{index_total:0,program_count:0,program_units:0,release_ready:false},service:{search_configured:false}});
  return good({tasks:[],watches:[],summary:{},total:0});
 });
 await page.addInitScript(()=>window.addEventListener('DOMContentLoaded',()=>{
  const mark=document.createElement('div');mark.textContent='模型选择回归夹具 · 模拟 API · 非真实模型调用或密钥保存';mark.style.cssText='position:fixed;bottom:0;left:0;right:0;padding:7px;text-align:center;background:#752b91;color:white;z-index:99999;font:13px sans-serif';document.body.append(mark);
 }));
 await page.goto(base+'/#/settings');await page.locator('#ys-provider').waitFor();
 return {page,localPosts,control};
}
async function readModels(page){
 if(!(await page.locator('#ys-api-key').getAttribute('placeholder')).includes('已保存'))await page.locator('#ys-api-key').fill('FIXTURE-KEY-NEVER-SENT-TO-A-PROVIDER');
 await page.locator('[data-settings-action="models"]').click();
 await page.locator('.ys-feedback').filter({hasText:'已读取 2 个模型'}).waitFor();
}
async function check(name,run){
 const started=Date.now();try{await run();results.push({name,passed:true,ms:Date.now()-started});console.log('PASS '+name);}catch(error){results.push({name,passed:false,error:error.message,ms:Date.now()-started});console.log('FAIL '+name+': '+error.message);}
}
(async()=>{
 browser=await chromium.launch({channel:'msedge',headless:true});
 await check('two discovered models are selectable and the second model is tested and saved',async()=>{
  const {page,localPosts}=await fixture();try{
   await readModels(page);assert.equal(await page.locator('#ys-model').evaluate(node=>node.tagName),'SELECT');
   assert.deepEqual(await page.locator('#ys-model option').evaluateAll(rows=>rows.map(row=>row.value)),models.map(row=>row.id));
   await page.locator('#ys-model').selectOption(models[1].id);
   await page.locator('#ys-settings-form [type="submit"]').click();await page.locator('.ys-feedback.is-success').waitFor();
   assert.equal(localPosts.find(row=>row.path==='/api/settings/test').body.model,models[1].id);
   assert.equal(localPosts.find(row=>row.path==='/api/settings/save').body.model,models[1].id);
   await page.screenshot({path:path.join(output,'fixture-selected-second-model.png'),fullPage:true});
  }finally{await page.close();}
 });
 await check('manual model IDs survive switching to the complete model list and back',async()=>{
  const {page,localPosts}=await fixture();try{
   await readModels(page);await page.locator('[data-settings-action="model-mode"]').click();
   assert.equal(await page.locator('#ys-model').evaluate(node=>node.tagName),'INPUT');
   await page.locator('#ys-model').fill('fixture-custom-model[1m]');
   await page.locator('[data-settings-action="model-mode"]').click();
   assert.equal(await page.locator('#ys-model').inputValue(),'fixture-custom-model[1m]');
   assert.equal(await page.locator('#ys-model option').count(),3);
   assert.match(await page.locator('#ys-model option:checked').innerText(),/当前/);
   await page.locator('[data-settings-action="model-mode"]').click();
   assert.equal(await page.locator('#ys-model').inputValue(),'fixture-custom-model[1m]');
   await page.locator('#ys-settings-form [type="submit"]').click();await page.locator('.ys-feedback.is-success').waitFor();
   assert.equal(localPosts.find(row=>row.path==='/api/settings/test').body.model,'fixture-custom-model[1m]');
  }finally{await page.close();}
 });
 await check('a saved model absent from discovery is preserved until an explicit new selection',async()=>{
  const {page,localPosts}=await fixture('fixture-saved-absent');try{
   await readModels(page);assert.equal(await page.locator('#ys-model').evaluate(node=>node.tagName),'SELECT');
   assert.equal(await page.locator('#ys-model').inputValue(),'fixture-saved-absent');
   assert.match(await page.locator('#ys-model option:checked').innerText(),/当前/);
   await page.locator('#ys-settings-form [type="submit"]').click();await page.locator('.ys-feedback.is-success').waitFor();
   assert.equal(localPosts.find(row=>row.path==='/api/settings/test').body.model,'fixture-saved-absent');
  }finally{await page.close();}
 });
 await check('keyboard selection, provider reset and 320/390/1440 layouts remain usable',async()=>{
  const {page,localPosts}=await fixture();try{
   await readModels(page);await page.locator('#ys-model').focus();await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');
   assert.equal(await page.locator('#ys-model').inputValue(),models[1].id);
   assert.equal(localPosts.some(row=>['/api/settings/test','/api/settings/save'].includes(row.path)),false,'Selecting a model with the keyboard must not submit a connection test or save');
   for(const width of [320,390,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`${width}px horizontal overflow`);}
   await page.setViewportSize({width:390,height:900});await page.screenshot({path:path.join(output,'fixture-model-picker-mobile.png'),fullPage:true});
   await page.locator('#ys-provider').selectOption('openai');assert.equal(await page.locator('#ys-model').evaluate(node=>node.tagName),'INPUT');
   assert.equal(await page.locator('#ys-model').inputValue(),'');assert.equal(await page.locator('[data-settings-action="model-mode"]').count(),0);
   assert.deepEqual(errors,[]);
  }finally{await page.close();}
 });
 await check('testing locks the model picker and a changed model invalidates an older save receipt',async()=>{
  const {page,localPosts,control}=await fixture('',{holdFirstTest:true,failFirstSave:true});try{
   await readModels(page);await page.locator('#ys-settings-form [type="submit"]').click();await control.testStarted;
   assert.equal(await page.locator('#ys-model').isDisabled(),true);
   assert.equal(await page.locator('[data-settings-action="model-mode"]').isDisabled(),true);
   control.releaseTest();await page.locator('.ys-feedback.is-error').filter({hasText:'临时保存失败'}).waitFor();
   assert.match(await page.locator('#ys-settings-form [type="submit"]').innerText(),/重试保存/);
   await page.locator('#ys-model').selectOption(models[1].id);
   assert.match(await page.locator('#ys-settings-form [type="submit"]').innerText(),/测试并保存/);
   await page.locator('#ys-settings-form [type="submit"]').click();await page.locator('.ys-feedback.is-success').waitFor();
   const tests=localPosts.filter(row=>row.path==='/api/settings/test'),saves=localPosts.filter(row=>row.path==='/api/settings/save');
   assert.equal(tests.length,2);assert.equal(saves.length,2);assert.equal(tests[1].body.model,models[1].id);assert.equal(saves[1].body.model,models[1].id);
  }finally{control.releaseTest();await page.close();}
 });
})().catch(error=>{results.push({name:'harness',passed:false,error:error.stack});console.error(error);}).finally(async()=>{
 if(browser)await browser.close();
 const report={at:new Date().toISOString(),base,passed:results.every(row=>row.passed),results,errors,posts,scope:'All APIs intercepted as fixtures; no real credential reads, real key saves or provider calls.'};
 fs.writeFileSync(path.join(output,'browser-report.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify({passed:report.passed,checks:results.length,report:path.join(output,'browser-report.json')}));process.exitCode=report.passed?0:1;
});
