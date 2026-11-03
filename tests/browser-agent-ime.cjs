/* Explicit API fixtures and Chromium native composition only; never real model/search calls. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const base=process.env.AGENT_URL||'http://127.0.0.1:4180',origin=new URL(base).origin;
const output=path.resolve(__dirname,'../data/runtime/agent-ime');fs.mkdirSync(output,{recursive:true});
const results=[],errors=[],id='r_33333333333333333333333333333333';let browser;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function fixture(home=false){
 const page=await browser.newPage({viewport:{width:1440,height:900}});page.setDefaultTimeout(7000);
 const world={reads:0,posts:[],releaseSettings:null,releaseRecent:null,task:{id,research_id:id,status:'running',turn_id:1,can_continue:false,query:'[IME夹具] 天津计算机研究',target_year:2028,reference_year:2027,profile:{provinces:[]},events:[],usage:{model_calls:1,searches:1,documents:0},report:{candidates:[],evidence:[]}}};
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());if(url.origin!==origin)return route.abort();
  if(!url.pathname.startsWith('/api/'))return route.continue();
  const good=(data,status=200)=>route.fulfill({status,json:{ok:true,data,error:null}}).catch(()=>{});
  if(request.method()==='POST'){
   const body=request.postDataJSON();world.posts.push({path:url.pathname,body});
   if(url.pathname.endsWith('/cancel'))world.task={...world.task,status:'cancelled',can_continue:true};
   else if(url.pathname.endsWith('/continue')){world.task={...world.task,status:'completed',can_continue:true,turn_id:2};if(world.holdContinue)await new Promise(resolve=>world.releaseContinue=resolve);}
   else if(url.pathname==='/api/research')world.task={...world.task,status:'completed',can_continue:true};
   else return route.abort();
   return good(world.task,202);
  }
  if(url.pathname==='/api/catalog-bootstrap')return good({revision:'fixture',meta:{target_year:2028,reference_year:2027},units:[],coverage:{release_ready:false},service:{search_configured:false}});
  if(url.pathname==='/api/settings'){
   if(home)await new Promise(resolve=>world.releaseSettings=resolve);
   return good({ai:{status:'connected',has_key:true},yuanbao:{status:'connected',has_key:true}});
  }
  if(url.pathname==='/api/research'&&url.searchParams.has('research_id')){
   if(world.failNextRead){world.failNextRead=false;return route.fulfill({status:503,json:{ok:false,data:null,error:{code:'fixture_unavailable',message:'[夹具] 研究进度暂时不可读'}}});}
   world.reads++;world.task={...world.task,usage:{...world.task.usage,documents:world.reads}};return good(world.task);
  }
  if(url.pathname==='/api/research'&&home)await new Promise(resolve=>world.releaseRecent=resolve);
  return good({tasks:[],total:0,watches:[],summary:{}});
 });
 await page.addInitScript(()=>window.addEventListener('DOMContentLoaded',()=>{const mark=document.createElement('div');mark.textContent='输入法回归夹具 · 模拟接口 · 无真实AI / 元宝调用';mark.style.cssText='position:fixed;bottom:0;left:0;right:0;background:#752b91;color:white;padding:5px;text-align:center;z-index:99999';document.body.append(mark);}));
 await page.goto(base+(home?'/#/':'/#/research?id='+id));await page.locator(home?'#ya-query':'#ya-followup').waitFor();
 return {page,world,cdp:await page.context().newCDPSession(page),close:async()=>{world.releaseSettings?.();world.releaseRecent?.();await page.close();}};
}
async function track(page,selector){await page.locator(selector).focus();await page.locator(selector).evaluate(node=>{
 window.imeAnchor=node;window.imeDetached=false;window.imeEvents=[];
 window.imeObserver=new MutationObserver(records=>{for(const record of records)for(const removed of record.removedNodes)if(removed===node||removed.contains?.(node))window.imeDetached=true;});
 window.imeObserver.observe(document.getElementById('app'),{subtree:true,childList:true});
 for(const type of ['compositionstart','compositionupdate','compositionend'])node.addEventListener(type,e=>window.imeEvents.push({type,data:e.data}));
});}
async function stable(page,selector){return page.locator(selector).evaluate(node=>({same:node===window.imeAnchor,connected:window.imeAnchor.isConnected,focused:document.activeElement===node,detached:window.imeDetached,value:node.value,start:node.selectionStart,end:node.selectionEnd,direction:node.selectionDirection,events:window.imeEvents}));}
async function polls(world,count){const start=Date.now();while(world.reads<count){assert.ok(Date.now()-start<8000,'poll did not arrive');await delay(50);}}
async function check(name,run){try{await run();results.push({name,passed:true});console.log('PASS '+name);}catch(error){results.push({name,passed:false,error:error.stack});console.log('FAIL '+name+': '+error.message);}}
(async()=>{
 browser=await chromium.launch({channel:'msedge',headless:true});
 await check('native follow-up composition survives two live polls, progress updates and submits committed Chinese once',async()=>{
  const f=await fixture();const {page,world,cdp}=f;try{
   await track(page,'#ya-followup');await cdp.send('Input.insertText',{text:'我不是'});
   await cdp.send('Input.imeSetComposition',{text:'shuo',selectionStart:4,selectionEnd:4});
   const first=world.reads;await polls(world,first+2);await delay(100);
   const during=await stable(page,'#ya-followup');assert.equal(during.same,true);assert.equal(during.connected,true);assert.equal(during.detached,false);assert.equal(during.focused,true);
   assert.equal(during.events.filter(e=>e.type==='compositionend').length,0);assert.equal(during.value,'我不是shuo');
   assert.ok((await page.locator('.ya-current-action').innerText()).includes('原文读取 '+world.reads));
   await page.locator('#ya-followup').evaluate(node=>{node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',ctrlKey:true,bubbles:true,cancelable:true}));node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',ctrlKey:true,isComposing:true,bubbles:true,cancelable:true}));node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',metaKey:true,keyCode:229,bubbles:true,cancelable:true}));});
   assert.equal(world.posts.length,0);
   await cdp.send('Input.imeSetComposition',{text:'说了',selectionStart:2,selectionEnd:2});await cdp.send('Input.insertText',{text:'说了'});
   assert.equal(await page.locator('#ya-followup').inputValue(),'我不是说了');
   await page.screenshot({path:path.join(output,'fixture-native-ime-desktop.png'),animations:'disabled'});
   await page.locator('#ya-followup').press('Control+Enter');await page.waitForFunction(()=>document.querySelector('#ya-followup')?.value==='');
   const continued=world.posts.filter(p=>p.path.endsWith('/continue'));assert.equal(continued.length,1);assert.equal(continued[0].body.message,'我不是说了');
   assert.equal(world.posts.filter(p=>p.path.endsWith('/cancel')).length,1);
  }finally{await f.close();}
 });
 await check('focused ordinary editing retains native selection and undo while polling',async()=>{
  const f=await fixture();const {page,world}=f;try{
   await track(page,'#ya-followup');await page.keyboard.type('abcdef');await page.locator('#ya-followup').evaluate(node=>node.setSelectionRange(2,4,'backward'));
   const first=world.reads;await polls(world,first+2);await delay(100);const value=await stable(page,'#ya-followup');
   assert.equal(value.same,true);assert.equal(value.detached,false);assert.equal(value.start,2);assert.equal(value.end,4);assert.equal(value.direction,'backward');
   await page.keyboard.type('X');const second=world.reads;await polls(world,second+1);await page.keyboard.press('Control+z');
   assert.equal(await page.locator('#ya-followup').inputValue(),'abcdef');assert.equal(world.posts.length,0);
  }finally{await f.close();}
 });
 await check('native home query survives delayed settings/recent responses and creates with committed Chinese',async()=>{
  const f=await fixture(true);const {page,world,cdp}=f;try{
   await track(page,'#ya-query');await cdp.send('Input.imeSetComposition',{text:'tianjin',selectionStart:7,selectionEnd:7});
   assert.ok(world.releaseSettings&&world.releaseRecent);world.releaseSettings();world.releaseRecent();await delay(250);
   const during=await stable(page,'#ya-query');assert.equal(during.same,true);assert.equal(during.detached,false);assert.equal(during.focused,true);
   assert.equal(during.events.filter(e=>e.type==='compositionend').length,0);
   await cdp.send('Input.insertText',{text:'天津考研'});assert.equal(await page.locator('#ya-query').inputValue(),'天津考研');
   await page.locator('#ya-query').press('Control+Enter');await page.locator('#ya-followup').waitFor();
   const created=world.posts.filter(p=>p.path==='/api/research');assert.equal(created.length,1);assert.equal(created[0].body.query,'天津考研');
  }finally{await f.close();}
 });
 await check('native condition composition survives polling and applies the committed provinces',async()=>{
  const f=await fixture();const {page,world,cdp}=f;try{
   await page.locator('.ya-condition-editor summary').click();const selector='[name="condition.provinces"]';await track(page,selector);
   await cdp.send('Input.imeSetComposition',{text:'tianjin',selectionStart:7,selectionEnd:7});const first=world.reads;await polls(world,first+2);await delay(100);
   const during=await stable(page,selector);assert.equal(during.same,true);assert.equal(during.detached,false);assert.equal(during.focused,true);
   await cdp.send('Input.insertText',{text:'天津、北京'});await page.locator('#ya-conditions-form button[type="submit"]').click();
   await page.waitForFunction(()=>document.querySelector('.ya-task-status')?.textContent.includes('已完成'));
   const continued=world.posts.filter(p=>p.path.endsWith('/continue'));assert.equal(continued.length,1);assert.deepEqual(continued[0].body.profile.provinces,['天津','北京']);
  }finally{await f.close();}
 });
 await check('a delayed final input after compositionend is not overwritten or automatically submitted',async()=>{
  const f=await fixture();const {page,world}=f;try{
   await track(page,'#ya-followup');await page.locator('#ya-followup').evaluate(node=>{
    node.dispatchEvent(new CompositionEvent('compositionstart',{data:'kao',bubbles:true}));node.value='我不kao';node.dispatchEvent(new InputEvent('input',{data:'kao',isComposing:true,bubbles:true}));
    node.dispatchEvent(new CompositionEvent('compositionend',{data:'考',bubbles:true}));node.value='我不考408';
   });
   const first=world.reads;await polls(world,first+1);await delay(100);const value=await stable(page,'#ya-followup');
   assert.equal(value.value,'我不考408');assert.equal(value.detached,false);assert.equal(world.posts.length,0);
   await page.locator('#ya-followup').evaluate(node=>node.dispatchEvent(new InputEvent('input',{data:'考408',bubbles:true})));
   await page.locator('#ya-followup').press('Control+Enter');await page.waitForFunction(()=>document.querySelector('#ya-followup')?.value==='');
   const continued=world.posts.filter(p=>p.path.endsWith('/continue'));assert.equal(continued.length,1);assert.equal(continued[0].body.message,'我不考408');
  }finally{await f.close();}
 });
 await check('a polling error can insert a sibling without detaching a native composing input',async()=>{
  const f=await fixture();const {page,world,cdp}=f;try{
   await track(page,'#ya-followup');await cdp.send('Input.imeSetComposition',{text:'wobukao',selectionStart:7,selectionEnd:7});world.failNextRead=true;
   await page.getByRole('alert').filter({hasText:'[夹具] 研究进度暂时不可读'}).waitFor();
   const value=await stable(page,'#ya-followup');assert.equal(value.same,true);assert.equal(value.detached,false);assert.equal(value.focused,true);
   assert.equal(value.events.filter(e=>e.type==='compositionend').length,0);
   await cdp.send('Input.insertText',{text:'我不考408'});assert.equal(await page.locator('#ya-followup').inputValue(),'我不考408');assert.equal(world.posts.length,0);
  }finally{await f.close();}
 });
 await check('an accepted reply preserves new native draft text whose final input event has not arrived',async()=>{
  const f=await fixture();const {page,world}=f;try{
   world.holdContinue=true;await page.locator('#ya-followup').fill('先发送');await page.locator('#ya-followup').press('Control+Enter');
   const start=Date.now();while(!world.releaseContinue){assert.ok(Date.now()-start<5000);await delay(20);}
   await page.locator('#ya-followup').evaluate(node=>{node.value='迟到input的新草稿';});world.releaseContinue();
   await page.waitForFunction(()=>document.querySelector('#ya-continue-form button[type="submit"]')?.disabled===false);
   assert.equal(await page.locator('#ya-followup').inputValue(),'迟到input的新草稿');
   await page.locator('#ya-followup').evaluate(node=>node.dispatchEvent(new InputEvent('input',{data:'新草稿',bubbles:true})));
   assert.equal(world.posts.filter(p=>p.path.endsWith('/continue')).length,1);
  }finally{world.releaseContinue?.();await f.close();}
 });
})().catch(error=>results.push({name:'harness',passed:false,error:error.stack})).finally(async()=>{
 await browser?.close();const report={passed:results.length>0&&results.every(r=>r.passed)&&!errors.length,results,errors,scope:'Chromium native composition via Edge CDP plus explicit API fixtures; not a manual Windows Microsoft Pinyin test; no real provider call'};
 const name=process.env.IME_REPORT_NAME||'browser-report.json';fs.writeFileSync(path.join(output,name),JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,checks:results.length}));process.exitCode=report.passed?0:1;
});
