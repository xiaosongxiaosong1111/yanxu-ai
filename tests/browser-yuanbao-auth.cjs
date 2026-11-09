/* All API calls are intercepted. No real credentials, searches or saves are used. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const base=(process.env.AGENT_URL||'http://127.0.0.1:4180').replace(/\/+$/,'');
const output=path.resolve(__dirname,'../data/runtime/yuanbao-auth');
fs.mkdirSync(output,{recursive:true});
const results=[],errors=[];
const FIXTURE_YUANBAO_SECRET_ID='AKID-YUANBAO-BROWSER-FIXTURE';
const FIXTURE_YUANBAO_SECRET_KEY='YUANBAO-SECRET-BROWSER-FIXTURE';
const FIXTURE_YUANBAO_API_KEY='YUANBAO-API-BROWSER-FIXTURE';
const id=FIXTURE_YUANBAO_SECRET_ID,key=FIXTURE_YUANBAO_SECRET_KEY,apiKey=FIXTURE_YUANBAO_API_KEY;
const mode=page=>page.locator('[name="auth_mode"]:checked').inputValue();
const setMode=async(page,value)=>{
 await page.locator(`[name="auth_mode"][value="${value}"]`).check();
 assert.deepEqual(await page.locator('[name="auth_mode"]').evaluateAll(rows=>rows.map(node=>node.value)),['standard','api_key']);
 assert.equal(await mode(page),value);
};
let browser;
async function fixture(savedYuanbao={},options={}){
 const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(6000);
 page.on('pageerror',error=>errors.push(error.message));
 let saved={ai:{status:'not_configured',has_key:false},yuanbao:{provider:'yuanbao',status:'not_configured',has_key:false,...savedYuanbao},storage:{mode:'fixture',persistent:false}};
 const posts=[];let release,started;
 const control={started:new Promise(resolve=>started=resolve),release:()=>release?.()};
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());if(url.origin!==new URL(base).origin)return route.abort();
  if(!url.pathname.startsWith('/api/'))return route.continue();
  const good=data=>route.fulfill({json:{ok:true,data,error:null}});
  if(request.method()==='POST'){
   const body=request.postDataJSON();posts.push({path:url.pathname,body});
   if(url.pathname==='/api/settings/test'){
    if(options.holdTest&&posts.filter(row=>row.path.endsWith('/test')).length===1){const wait=new Promise(resolve=>release=resolve);started();await wait;}
    return good({status:'connected',validation_token:'browser-fixture-ticket-'+posts.length});
   }
   if(url.pathname==='/api/settings/save'){
    if(options.failFirstSave&&posts.filter(row=>row.path.endsWith('/save')).length===1)return route.fulfill({status:503,json:{ok:false,data:null,error:{code:'settings_save_failed',message:'测试夹具：保存暂时失败。'}}});
    saved={...saved,[body.kind]:{provider:body.provider,model:body.model||'',base_url:body.base_url||'',auth_mode:body.auth_mode||'api_key',status:'connected',has_key:true}};
    return good(saved);
   }
   if(url.pathname==='/api/settings/delete'){saved={...saved,[body.kind]:{provider:'yuanbao',status:'not_configured',has_key:false}};return good(saved);}
   return route.abort('blockedbyclient');
  }
  if(url.pathname==='/api/settings/providers')return good({providers:[{id:'deepseek',label:'测试夹具 DeepSeek',default_base_url:'https://api.deepseek.com',requires_key:true},{id:'openai',label:'测试夹具 OpenAI',default_base_url:'https://api.openai.com/v1',requires_key:true}]});
  if(url.pathname==='/api/settings')return good(saved);
  if(url.pathname==='/api/catalog-bootstrap')return good({revision:'auth-browser-fixture',meta:{target_year:2028,reference_year:2027},units:[],coverage:{index_total:0,program_count:0,program_units:0,release_ready:false},service:{search_configured:saved.yuanbao.has_key}});
  return good({tasks:[],watches:[],total:0});
 });
 await page.addInitScript(()=>window.addEventListener('DOMContentLoaded',()=>{
  const label=document.createElement('div');label.textContent='双鉴权浏览器夹具 · 模拟接口 · 非真实密钥保存或元宝检索';label.style.cssText='position:fixed;bottom:0;left:0;right:0;padding:7px;text-align:center;background:#752b91;color:white;z-index:99999;font:13px sans-serif';document.body.append(label);
 }));
 await page.goto(base+'/#/settings');await page.locator('#ys-provider').waitFor();await page.locator('[data-settings-tab="yuanbao"]').click();
 return {page,posts,control};
}
async function fillPair(page,newKey=key){await page.locator('#ys-secret-id').fill(id);await page.locator('#ys-secret-key').fill(newKey);}
async function submit(page){await page.locator('#ys-settings-form [type="submit"]').click();}
async function noLeak(page){
 const found=await page.evaluate(values=>values.some(value=>location.href.includes(value)||JSON.stringify({...localStorage}).includes(value)||document.body.innerText.includes(value)||[...document.querySelectorAll('*')].some(node=>[...node.attributes].some(attr=>attr.value.includes(value)))),[id,key,apiKey]);
 assert.equal(found,false,'Credentials must remain out of URL, storage, text and value attributes');
}
async function check(name,run){try{await run();results.push({name,passed:true});console.log('PASS '+name);}catch(error){results.push({name,passed:false,error:error.message});console.log('FAIL '+name+': '+error.message);}}
(async()=>{
 browser=await chromium.launch({headless:true,channel:'msedge'});
 await check('new Yuanbao settings default to standard and reject empty or incomplete pairs without requests',async()=>{
  const {page,posts}=await fixture();try{
   assert.equal(await mode(page),'standard');assert.equal(await page.locator('#ys-api-key').count(),0);
   await submit(page);assert.equal(posts.length,0);assert.equal(await page.locator(':focus').getAttribute('name'),'secret_id');
   await page.locator('#ys-secret-id').fill(id);await submit(page);assert.equal(posts.length,0);assert.equal(await page.locator(':focus').getAttribute('name'),'secret_key');
   await page.locator('#ys-secret-key').fill(key);await noLeak(page);
   await page.locator('[data-secret-field="secret_key"]').click();assert.equal(await page.locator('#ys-secret-key').getAttribute('type'),'text');await page.locator('[data-secret-field="secret_key"]').click();assert.equal(await page.locator('#ys-secret-key').getAttribute('type'),'password');
  }finally{await page.close();}
 });
 await check('standard test and save carry both credentials, mode and no unrelated API key',async()=>{
  const {page,posts}=await fixture();try{
   await fillPair(page);await submit(page);await page.locator('.ys-feedback.is-success').waitFor();
   assert.deepEqual(posts.map(row=>row.path),['/api/settings/test','/api/settings/save']);
   for(const row of posts){assert.equal(row.body.auth_mode,'standard');assert.equal(row.body.secret_id,id);assert.equal(row.body.secret_key,key);assert.equal(row.body.use_saved_key,false);assert.equal(Object.hasOwn(row.body,'api_key'),false);}
   assert.equal(await page.locator('#ys-secret-id').inputValue(),'');assert.equal(await page.locator('#ys-secret-key').inputValue(),'');await noLeak(page);
   await page.screenshot({path:path.join(output,'fixture-standard-saved-desktop.png'),fullPage:true});
  }finally{await page.close();}
 });
 await check('legacy service keys restore API mode and never become a standard secret',async()=>{
  const {page,posts}=await fixture({has_key:true,status:'connected'});try{
   assert.equal(await mode(page),'api_key');assert.equal(await page.locator('#ys-secret-key').count(),0);
   await submit(page);await page.locator('.ys-feedback.is-success').waitFor();assert.equal(posts[0].body.auth_mode,'api_key');assert.equal(posts[0].body.use_saved_key,true);assert.equal(Object.hasOwn(posts[0].body,'api_key'),false);
   await setMode(page,'standard');await submit(page);assert.equal(posts.length,2);assert.equal(await page.locator(':focus').getAttribute('name'),'secret_id');
  }finally{await page.close();}
 });
 await check('saved standard credentials are reused only when both fields are blank and the mode matches',async()=>{
  const {page,posts}=await fixture({auth_mode:'standard',has_key:true,status:'connected'});try{
   assert.equal(await mode(page),'standard');await submit(page);await page.locator('.ys-feedback.is-success').waitFor();
   assert.equal(posts[0].body.use_saved_key,true);assert.equal(Object.hasOwn(posts[0].body,'secret_id'),false);assert.equal(Object.hasOwn(posts[0].body,'secret_key'),false);
   await page.locator('#ys-secret-id').fill(id);await submit(page);assert.equal(posts.length,2);assert.match(await page.locator('.ys-feedback').innerText(),/完整|配对|只更换/);
   await setMode(page,'api_key');await page.locator('#ys-api-key').fill(apiKey);await submit(page);await page.locator('.ys-feedback.is-success').waitFor();
   assert.equal(posts[2].body.auth_mode,'api_key');assert.equal(posts[2].body.api_key,apiKey);assert.equal(posts[2].body.use_saved_key,false);assert.equal(Object.hasOwn(posts[2].body,'secret_id'),false);assert.equal(Object.hasOwn(posts[2].body,'secret_key'),false);
  }finally{await page.close();}
 });
 await check('mode drafts stay separate in memory and leaving settings clears all three credential fields',async()=>{
  const {page}=await fixture();try{
   await fillPair(page);await setMode(page,'api_key');assert.equal(await page.locator('#ys-api-key').inputValue(),'');await page.locator('#ys-api-key').fill(apiKey);
   await setMode(page,'standard');assert.equal(await page.locator('#ys-secret-id').inputValue(),id);assert.equal(await page.locator('#ys-secret-key').inputValue(),key);
   await page.locator('[data-settings-tab="ai"]').click();assert.equal(await page.locator('#ys-api-key').inputValue(),'');await page.locator('#ys-api-key').fill('AI-FIXTURE-ONLY');await page.locator('[data-settings-tab="yuanbao"]').click();assert.equal(await page.locator('#ys-secret-key').inputValue(),key);await noLeak(page);
   await page.locator('.main-nav [data-route="/"]').click();await page.locator('#ya-query').waitFor();await page.getByRole('button',{name:'AI与元宝设置',exact:true}).click();await page.locator('#ys-provider').waitFor();await page.locator('[data-settings-tab="yuanbao"]').click();
   assert.equal(await page.locator('#ys-secret-id').inputValue(),'');assert.equal(await page.locator('#ys-secret-key').inputValue(),'');await setMode(page,'api_key');assert.equal(await page.locator('#ys-api-key').inputValue(),'');
  }finally{await page.close();}
 });
 await check('busy requests lock authentication mode and changing modes invalidates a pending save receipt',async()=>{
  const {page,posts,control}=await fixture({}, {holdTest:true,failFirstSave:true});try{
   await fillPair(page);await submit(page);await control.started;
   assert.equal(await page.locator('#ys-secret-id').isDisabled(),true);assert.equal(await page.locator('#ys-secret-key').isDisabled(),true);
   assert.equal(await page.locator('[name="auth_mode"]:disabled').count(),2);control.release();await page.getByRole('button',{name:'重试保存',exact:true}).waitFor();
   await setMode(page,'api_key');await page.locator('#ys-api-key').fill(apiKey);await submit(page);await page.locator('.ys-feedback.is-success').waitFor();
   assert.equal(posts.filter(row=>row.path.endsWith('/test')).length,2);assert.equal(posts.at(-1).body.auth_mode,'api_key');assert.equal(posts.at(-1).body.api_key,apiKey);assert.equal(Object.hasOwn(posts.at(-1).body,'secret_key'),false);
  }finally{control.release();await page.close();}
 });
 await check('both authentication modes are readable at 320, 390 and 1440 pixels without overflow',async()=>{
  const {page}=await fixture();try{
   for(const selected of ['standard','api_key']){
    await setMode(page,selected);
    for(const width of [320,390,1440]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,selected+' '+width+'px overflow');}
   }
   await setMode(page,'standard');await page.setViewportSize({width:390,height:900});await page.screenshot({path:path.join(output,'fixture-standard-mobile.png'),fullPage:true});
   await setMode(page,'api_key');await page.setViewportSize({width:1440,height:1100});await page.screenshot({path:path.join(output,'fixture-api-key-desktop.png'),fullPage:true});assert.deepEqual(errors,[]);
  }finally{await page.close();}
 });
})().catch(error=>{results.push({name:'harness',passed:false,error:error.stack});}).finally(async()=>{
 if(browser)await browser.close();const report={passed:results.length>0&&results.every(row=>row.passed),results,errors,scope:'All API routes use explicit fixtures; no actual keys read, no real searches and no real configuration writes.'};fs.writeFileSync(path.join(output,'frontend-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,checks:results.length}));process.exitCode=report.passed?0:1;
});
