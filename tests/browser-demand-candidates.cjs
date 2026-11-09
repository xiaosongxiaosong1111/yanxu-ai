/* Isolated static server and intercepted API fixtures; no application DB, keys or external requests. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require(process.env.FORMAL_PLAYWRIGHT_PATH||process.env.PLAYWRIGHT_PATH||(()=>{try{return require.resolve('playwright');}catch{return 'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright';}})());
const root=path.resolve(__dirname,'../static'),out=path.resolve(process.env.DEMAND_UI_OUTPUT||path.join(__dirname,'../data/runtime/demand-candidates'));
const id='k_'+'a'.repeat(24),sid='sg_'+'b'.repeat(32),wrongSid='sg_'+'c'.repeat(32),rid='r_'+'d'.repeat(32),pid='tp_'+'e'.repeat(24);
const quality={version:1,state:'accepted',reusable:true,material_type:'catalog',reasons:[]};
const entry={id,title:'[夹具] <img src=x onerror=bad()> 计算机目录',unit_id:'u_fixture',unit_name:'[夹具] 样本大学',year:2027,status:'search_lead',quality:{version:1,state:'pending',reusable:false},validity:'active',original_available:false,url:'https://example.edu.cn/catalog.pdf',excerpt:'待核摘要'};
const readyEntry={...entry,status:'official_original_read',quality,original_available:true,usable:true,index_state:'succeeded',extraction:{id:'ex_fixture',generation_id:'kg_fixture',extraction_status:'complete',segment_count:2,format:'pdf',page_kind:'physical'}};
const ref={entry_id:id,segment_id:sid,field:'exams',quote:'第2单元：201英语一或204英语二',original_url:entry.url};
const subjects=[{code:'201',name:'英语一',exam_unit:2,scope:'national',choice_group:'exam-unit-2'},{code:'204',name:'英语二',exam_unit:2,scope:'national',choice_group:'exam-unit-2'}];
const program={id:pid,unit_id:entry.unit_id,unit_name:entry.unit_name,name:'计算机',year:2027,subjects,candidate_origin:'official_original',verification_mode:'research_original',candidate_ref:{kind:'research',id:pid,research_id:rid},validation:{state:'computable'},evidence_refs:[ref]};
const reason={field:'subjects',message:'[夹具] 初试外国语存在备选',label:'考试科目',evidence_refs:[ref]};
const task={id:rid,research_id:rid,query:'[夹具] 补证研究',status:'completed',events:[],profile:{subjects:[{code:'201',name:'英语一'}]},report:{candidates:[program,{id:'formal_fixture',unit_id:entry.unit_id,unit_name:entry.unit_name,name:'已发布方向'}],algorithm:{matching:{summary:{input_count:1,unknown_count:1},needs_confirmation:[{program,status:'unknown',reasons:[{...reason,evidence_refs:undefined}]}]}},decisions:[{candidate_id:pid,reasons:[reason],evidence_refs:[ref]}],evidence:[{id:'same_source',knowledge_id:id,knowledge_saved:true,knowledge_current:true,knowledge_quality:quality,extraction:readyEntry.extraction,segments:[{id:wrongSid,generation_id:'kg_fixture'}]}]}};
const requests=[],errors=[],results=[];let server,browser,page,base,currentEntry=entry,holdSettings=false,releaseSettings;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function check(name,work){try{await work();results.push({name,passed:true});console.log('PASS '+name);}catch(error){results.push({name,passed:false,error:error.stack});console.error('FAIL '+name+': '+error.message);throw error;}}
async function run(){
 fs.mkdirSync(out,{recursive:true});
 server=http.createServer((request,response)=>{const url=new URL(request.url,'http://fixture'),filename=url.pathname==='/'?'index.html':path.basename(url.pathname),target=path.join(root,filename);if(!/\.(html|mjs|js|css|svg)$/.test(filename)||!fs.existsSync(target)){response.writeHead(404);response.end();return;}response.writeHead(200,{'content-type':/\.(mjs|js)$/.test(filename)?'text/javascript;charset=utf-8':filename.endsWith('.html')?'text/html;charset=utf-8':filename.endsWith('.svg')?'image/svg+xml':'text/css;charset=utf-8'});response.end(fs.readFileSync(target));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));base='http://127.0.0.1:'+server.address().port;
 browser=await chromium.launch({headless:true,...(process.env.FORMAL_BROWSER_PATH?{executablePath:process.env.FORMAL_BROWSER_PATH}:process.env.FORMAL_BROWSER_CHANNEL?{channel:process.env.FORMAL_BROWSER_CHANNEL}:{}),args:['--disable-extensions','--renderer-process-limit=1']});
 page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(6000);page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());if(url.origin!==base)return route.abort();if(!url.pathname.startsWith('/api/'))return route.continue();
  const body=req.postData()?req.postDataJSON():undefined;requests.push({path:url.pathname,query:Object.fromEntries(url.searchParams),method:req.method(),body});
  const good=data=>route.fulfill({json:{ok:true,data,error:null}});
  if(url.pathname==='/api/catalog-bootstrap')return good({revision:'fixture-r1',meta:{target_year:2028,reference_year:2027},units:[],coverage:{release_ready:false},service:{search_configured:false}});
  if(url.pathname==='/api/radar')return good({program_ids:[],unread_count:0});
  if(url.pathname==='/api/settings'){if(holdSettings)await new Promise(resolve=>{releaseSettings=resolve;});return good({ai:{has_key:false},yuanbao:{has_key:false}});}
  if(url.pathname==='/api/research')return good(req.method()==='POST'||url.searchParams.has('research_id')?task:{tasks:[],total:0});
  if(url.pathname==='/api/knowledge')return good(url.searchParams.has('entry_id')?{entry:currentEntry}:{entries:[currentEntry],total:1,page:1,summary:{total:1,quality_counts:{accepted:0,pending:1,excluded:0}},facets:{units:[],years:[2027]}});
  const segment={id:sid,entry_id:id,extraction_id:'ex_fixture',generation_id:'kg_fixture',text:ref.quote,char_start:100,char_end:125,page_start:3,page_end:3,page_kind:'physical',format:'pdf',source:readyEntry};
  if(url.pathname==='/api/knowledge/segments')return good({segments:[segment],total:1,page:1});
  if(url.pathname==='/api/knowledge/segment')return good({segment,contexts:[],neighbors:[],returned_chars:ref.quote.length,truncated:false});
  return route.fulfill({status:404,json:{ok:false,error:{message:'Unexpected fixture API '+url.pathname}}});
 });
 await check('pending knowledge opens an editable source-scoped draft without starting research',async()=>{
  await page.goto(base+'/#/knowledge?quality=pending&entry_id='+id);await page.locator('[data-knowledge-action="research"]').waitFor();
  const inventory=await page.locator('.yk-reader-content>.yk-inventory').innerText();assert.match(inventory,/原文：待补齐/);assert.match(inventory,/语义索引：待查询/);assert.equal(await page.locator('.yk-reader img').count(),0);
  await page.locator('[data-knowledge-action="research"]').click();await page.locator('#ya-query').waitFor();
  assert.equal(new URL(page.url()).hash,'#/research');assert.match(await page.locator('#ya-query').inputValue(),/请核对这份资料/);assert.equal(requests.filter(row=>row.method==='POST').length,0);
  assert.equal(await page.locator('.ya-knowledge-seed img').count(),0);await page.locator('#ya-query').fill('请核对这份资料，重点补齐英语科目备选。');
  await page.locator('#ya-query').evaluate(node=>{node.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',ctrlKey:true,isComposing:true,bubbles:true}));});
  assert.equal(requests.filter(row=>row.method==='POST').length,0);await page.locator('#ya-query').evaluate(node=>node.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})));
  await page.locator('#ya-create-form [type="submit"]').click();await page.locator('.ya-candidate-origin').waitFor();
  const created=requests.find(row=>row.method==='POST');assert.deepEqual(created.body,{query:'请核对这份资料，重点补齐英语科目备选。',knowledge_entry_id:id});
 });
 await check('temporary directions show honest origin and evidence actions, preserving formal save controls',async()=>{
  assert.equal(await page.locator('[data-agent-save="'+pid+'"],[data-agent-program="'+pid+'"]').count(),0);assert.equal(await page.locator('[data-agent-save="formal_fixture"]').count(),1);
  assert.match(await page.locator('.ya-section-title').filter({has:page.locator('h2', {hasText:'值得继续研究的学校'})}).innerText(),/1 所学校 · 2 个方向/);
  assert.match(await page.locator('.ya-candidate-origin').innerText(),/本轮从原文整理[\s\S]*资格待核/);
  await page.locator('.ya-candidate-fields>summary').click();assert.match(await page.locator('.ya-candidate-fields').innerText(),/英语一[\s\S]*或[\s\S]*英语二/);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.locator('.ya-school-card').screenshot({path:path.join(out,'demand-candidates-mobile.png')});await page.setViewportSize({width:1280,height:900});
 });
 await check('a reason opens its exact knowledge segment and keeps the position on refresh',async()=>{
  await page.locator('[data-agent-tab="methods"]').click();const link=page.locator('.ya-algorithm-records [data-agent-knowledge]');assert.equal(await link.getAttribute('data-agent-knowledge-segment'),sid);
  currentEntry=readyEntry;await link.click();await page.locator('.yk-focused-quote').waitFor();assert.match(page.url(),new RegExp('segment_id='+sid));assert.ok(!page.url().includes(wrongSid));
  await page.reload();await page.locator('.yk-focused-quote').waitFor();assert.match(page.url(),new RegExp('segment_id='+sid));assert.match(await page.locator('.yk-focused-quote').innerText(),/201英语一或204英语二/);
  assert.match(await page.locator('.yk-reader-content>.yk-inventory').innerText(),/可定位片段：可用/);await page.screenshot({path:path.join(out,'demand-evidence-desktop.png'),fullPage:false});
 });
 await check('ordinary research has no leaked source context and late settings preserve the composing node',async()=>{
  holdSettings=true;await page.goto(base+'/#/research');await page.locator('#ya-query').waitFor();await page.waitForFunction(()=>!!document.querySelector('#ya-query'));
  await page.locator('#ya-query').evaluate(node=>{window.demandImeNode=node;node.focus();node.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));node.value='普通中文研究';node.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true,data:'究'}));});
  for(let index=0;index<30&&!releaseSettings;index++)await delay(20);assert.ok(releaseSettings);releaseSettings();holdSettings=false;await delay(100);
  assert.equal(await page.evaluate(()=>window.demandImeNode===document.querySelector('#ya-query')),true);assert.equal(await page.locator('#ya-query').inputValue(),'普通中文研究');
  await page.locator('#ya-query').evaluate(node=>node.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})));await page.locator('#ya-create-form [type="submit"]').click();await page.locator('.ya-candidate-origin').waitFor();
  assert.deepEqual(requests.filter(row=>row.method==='POST').at(-1).body,{query:'普通中文研究'});assert.ok(!await page.evaluate(value=>JSON.stringify({...localStorage}).includes(value),id));
 });
 await check('withdrawn, excluded and private entries cannot open research even through a forged click',async()=>{
  const before=requests.filter(row=>row.method==='POST').length;
  for(const patch of [{validity:'revoked'},{quality:{version:1,state:'excluded',reusable:false}},{content_notice:'[夹具] 仅保存来源，不处理个人名单'},{text_origin:'metadata_only'}]){
   currentEntry={...entry,...patch};await page.goto(base+'/#/knowledge?quality=all&entry_id='+id);await page.locator('#yk-reader-title').waitFor();
   assert.equal(await page.locator('[data-knowledge-action="research"]').count(),0);assert.match(await page.locator('.yk-research-handoff').innerText(),/恢复有效版本|选择其他有效的官方资料/);
   const previous=page.url();await page.evaluate(()=>{const button=document.createElement('button');button.dataset.knowledgeAction='research';document.querySelector('#knowledge-root').append(button);button.click();button.remove();});
   await delay(20);assert.equal(page.url(),previous);assert.equal(requests.filter(row=>row.method==='POST').length,before);
  }
 });
 assert.deepEqual(errors,[]);
}
run().catch(error=>{console.error(error.stack);process.exitCode=1;}).finally(async()=>{releaseSettings?.();await browser?.close();if(server)await new Promise(resolve=>server.close(resolve));fs.writeFileSync(path.join(out,'browser-demand-candidates.json'),JSON.stringify({results,errors,requests},null,2));});
