/* Isolated local fixtures: no application DB, keys, fixed service port or provider calls. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require(process.env.FORMAL_PLAYWRIGHT_PATH||process.env.PLAYWRIGHT_PATH||(()=>{try{return require.resolve('playwright');}catch{return 'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright';}})());
const root=path.resolve(__dirname,'../static'),out=path.resolve(process.env.OFFICIAL_CLOSURE_UI_OUTPUT||path.join(__dirname,'../data/runtime/official-closure'));
const rid='r_'+'a'.repeat(32),secondRid='r_'+'d'.repeat(32),entryId='k_'+'b'.repeat(24),segmentId='sg_'+'c'.repeat(32);
const quote='本专业学费每年12000元，学制2.5年。具有国家承认的本科学历。';
const ref={entry_id:entryId,segment_id:segmentId,quote,url:'https://example.edu.cn/fees',field:'tuition'};
const program={id:'tp_fixture',unit_id:'u_fixture',unit_name:'[夹具] 样本大学',name:'计算机科学与技术',year:2027,candidate_origin:'official_original',subjects:[{code:'408',name:'计算机学科专业基础',exam_unit:4,scope:'national'}],eligibility_evidence_complete:false};
const source={title:'[夹具] 2027年招生章程与收费',url:ref.url,unit_id:program.unit_id,unit_name:program.unit_name,status:'official_original_read'};
const discovery={unit_id:program.unit_id,unit_name:program.unit_name,stage:'entry_verified',entry_verified:true,entry_sources:[{...source,title:'[夹具] 研究生院入口',url:'https://example.edu.cn'}],document_sources:[source],candidate_sources:[{title:'[夹具] 官方账号',url:'https://mp.weixin.qq.com/s/fixture',source_type:'official_account'},{title:'[夹具] <img src=x onerror=bad()>',url:'javascript:bad()'}]};
const facts=[{id:'af_fee',field:'tuition',program_id:program.id,state:'verified_single_source',value:{amount:12000,period:'year'},period:'year',evidence_refs:[ref]},{id:'af_duration',field:'duration_years',program_id:program.id,state:'verified_single_source',value:2.5,evidence_refs:[{...ref,field:'duration_years'}]},{id:'af_eligibility',field:'eligibility',program_id:program.id,state:'partial',value:[{field:'education',op:'in',value:['bachelor'],label:'具有国家承认的本科学历'}],eligibility_evidence_complete:false,missing:['身体健康要求尚不可由现有档案判断'],evidence_refs:[{...ref,field:'eligibility'}]}];
const report={candidates:[program],official_discovery:[discovery],admission_facts:{facts,applied_count:3,conflict_count:0,pending_count:0},algorithm:{matching:{summary:{input_count:1,matched_count:0,unknown_count:1,excluded_count:0},needs_confirmation:[{program,status:'unknown',reasons:[{field:'eligibility',message:'[夹具] 学历条款已核对，其余报考资格仍需确认',evidence_refs:[ref]}]}]}},evidence:[{...source,id:'s_fixture',year:2027,excerpt:quote}]};
const task={id:rid,research_id:rid,query:'[夹具] 官网发现与报考依据闭环',status:'running',turn_id:1,target_year:2027,reference_year:2027,events:[],report};
const entry={id:entryId,title:source.title,url:source.url,unit_id:program.unit_id,unit_name:program.unit_name,year:2027,status:'official_original_read',validity:'active',original_available:true,usable:true,index_state:'succeeded',quality:{version:1,state:'accepted',reusable:true,material_type:'charter',reasons:[]},extraction:{id:'ex_fixture',generation_id:'kg_fixture',extraction_status:'complete',segment_count:1,format:'html',page_kind:'logical'}};
const segment={id:segmentId,entry_id:entryId,extraction_id:'ex_fixture',generation_id:'kg_fixture',text:quote,char_start:100,char_end:100+quote.length,page_start:1,page_end:1,page_kind:'logical',format:'html',source:entry};
const results=[],errors=[],requests=[],externalRequests=[];
let browser,page,server,base,releasePoll,currentTask=task;
async function check(name,work){try{await work();results.push({name,passed:true});console.log('PASS '+name);}catch(error){results.push({name,passed:false,error:error.stack});throw error;}}
async function run(){
 fs.mkdirSync(out,{recursive:true});
 server=http.createServer((request,response)=>{const url=new URL(request.url,'http://fixture'),filename=url.pathname==='/'?'index.html':path.basename(url.pathname),target=path.join(root,filename);if(!/\.(html|mjs|js|css|svg)$/.test(filename)||!fs.existsSync(target)){response.writeHead(404);response.end();return;}response.writeHead(200,{'content-type':/\.(mjs|js)$/.test(filename)?'text/javascript;charset=utf-8':filename.endsWith('.html')?'text/html;charset=utf-8':filename.endsWith('.svg')?'image/svg+xml':'text/css;charset=utf-8'});response.end(fs.readFileSync(target));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));base='http://127.0.0.1:'+server.address().port;
 browser=await chromium.launch({headless:true,...(process.env.FORMAL_BROWSER_PATH?{executablePath:process.env.FORMAL_BROWSER_PATH}:process.env.FORMAL_BROWSER_CHANNEL?{channel:process.env.FORMAL_BROWSER_CHANNEL}:{}),args:['--disable-extensions','--renderer-process-limit=1']});
 page=await browser.newPage({viewport:{width:1365,height:950}});page.setDefaultTimeout(7000);page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());if(url.origin!==base){externalRequests.push(url.href);return route.abort();}if(!url.pathname.startsWith('/api/'))return route.continue();
  requests.push({path:url.pathname,query:Object.fromEntries(url.searchParams),method:req.method()});const good=data=>route.fulfill({json:{ok:true,data,error:null}});
  if(url.pathname==='/api/catalog-bootstrap')return good({revision:'fixture',meta:{target_year:2027,reference_year:2027},units:[],coverage:{release_ready:false},service:{search_configured:false}});
  if(url.pathname==='/api/radar')return good({program_ids:[],unread_count:0});
  if(url.pathname==='/api/settings')return good({ai:{has_key:false},yuanbao:{has_key:false}});
  if(url.pathname==='/api/research'){if(url.searchParams.has('after_event')&&currentTask.status==='running')await new Promise(resolve=>{releasePoll=resolve;});return good(url.searchParams.has('research_id')?currentTask:{tasks:[],total:0});}
  if(url.pathname==='/api/knowledge')return good(url.searchParams.has('entry_id')?{entry}:{entries:[entry],total:1,page:1,summary:{total:1,quality_counts:{accepted:1,pending:0,excluded:0}},facets:{units:[],years:[2027]}});
  if(url.pathname==='/api/knowledge/segments')return good({segments:[segment],total:1,page:1});
  if(url.pathname==='/api/knowledge/segment')return good({segment,contexts:[],neighbors:[],returned_chars:quote.length,truncated:false});
  return route.fulfill({status:404,json:{ok:false,error:{message:'Unexpected fixture API '+url.pathname}}});
 });
 await check('discovery remains collapsed and shows a concrete stage and next action',async()=>{
  await page.goto(base+'/#/research?id='+rid);await page.locator('.ya-official-discovery').waitFor();
  assert.equal(await page.locator('.ya-official-discovery').getAttribute('open'),null);assert.match(await page.locator('.ya-official-discovery>summary').innerText(),/官网已定位/);
  await page.locator('.ya-official-discovery>summary').click();assert.match(await page.locator('.ya-official-discovery').innerText(),/继续查找目标年/);assert.equal(await page.locator('.ya-official-discovery img').count(),0);
 });
 await check('late stage updates preserve the active Chinese composing node and do not submit a task',async()=>{
  await page.locator('#ya-followup').evaluate(node=>{window.closureImeNode=node;node.focus();node.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));node.value='补充中文条件';node.setSelectionRange(3,5);node.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true,data:'件'}));});
  await new Promise((resolve,reject)=>{const started=Date.now(),timer=setInterval(()=>{if(releasePoll){clearInterval(timer);resolve();}else if(Date.now()-started>5000){clearInterval(timer);reject(new Error('No fixture poll arrived'));}},25);});
  currentTask={...task,status:'completed',report:{...report,official_discovery:[{...discovery,stage:'document_found'}]}};releasePoll();releasePoll=undefined;
  await page.waitForFunction(()=>document.querySelector('.ya-official-discovery>summary').textContent.includes('目标年资料已找到'));
  assert.equal(await page.evaluate(()=>window.closureImeNode===document.querySelector('#ya-followup')),true);assert.equal(await page.locator('#ya-followup').inputValue(),'补充中文条件');assert.deepEqual(await page.locator('#ya-followup').evaluate(node=>[node.selectionStart,node.selectionEnd,node===document.activeElement]),[3,5,true]);assert.equal(await page.locator('.ya-official-discovery').getAttribute('open'),'');
  await page.locator('#ya-followup').evaluate(node=>node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',ctrlKey:true,isComposing:true,bubbles:true})));assert.equal(requests.filter(row=>row.method==='POST').length,0);
  await page.locator('#ya-followup').evaluate(node=>node.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})));assert.match(await page.locator('.ya-official-discovery').innerText(),/正文待补/);
 });
 await check('fee duration and eligibility retain exact evidence and uncertainty within the existing field disclosure',async()=>{
  assert.match(await page.locator('.ya-admission-brief').innerText(),/学费 12000 元 \/ 年 · 学制 2.5 年/);await page.locator('.ya-candidate-fields>summary').click();
  const text=await page.locator('.ya-candidate-fields').innerText();assert.match(text,/单一官方原文已核对/);assert.match(text,/身体健康要求尚不可由现有档案判断/);assert.match(text,/资格仍需结合全部条款/);assert.match(await page.locator('.ya-candidate-origin').innerText(),/资格待核/);
  assert.equal(await page.locator('[data-admission-field="tuition"] [data-agent-knowledge]').getAttribute('data-agent-knowledge-segment'),segmentId);
  await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'closure-desktop-fixture.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0);});await page.screenshot({path:path.join(out,'closure-mobile-fixture.png'),fullPage:true});await page.setViewportSize({width:1365,height:950});
 });
 await check('source roles and access restrictions stay explicit without promoting an account to a school website',async()=>{
  await page.locator('[data-agent-tab="sources"]').click();if(await page.locator('.ya-official-discovery').getAttribute('open')===null)await page.locator('.ya-official-discovery>summary').click();
  assert.match(await page.locator('.ya-official-discovery').innerText(),/官网入口[\s\S]*招生文档[\s\S]*官方账号线索/);assert.equal(await page.locator('.ya-official-discovery a[href^="javascript:"]').count(),0);
  currentTask={...currentTask,report:{...currentTask.report,official_discovery:[{...discovery,stage:'access_restricted'}]}};await page.reload();await page.waitForFunction(()=>document.querySelector('.ya-official-discovery>summary')?.textContent.includes('访问受限'));assert.match(await page.locator('.ya-official-discovery>summary').innerText(),/访问受限/);
  currentTask={...currentTask,report:{...currentTask.report,official_discovery:[{...discovery,stage:'entry_verified',entry_sources:[{title:'[夹具] 官方账号',url:'https://yz.chsi.com.cn/sch/viewZszc--schId-123.dhtml',source_role:'official_account'}],document_sources:[]}]}};await page.reload();await page.waitForFunction(()=>document.querySelector('.ya-official-discovery>summary')?.textContent.includes('官方发布账号已定位'));assert.ok(!(await page.locator('.ya-official-discovery>summary').innerText()).includes('官网已定位'));
 });
 await check('algorithm facts open their exact saved segment and survive refresh',async()=>{
  await page.locator('[data-agent-tab="methods"]').click();await page.locator('[data-persist-key="admission-facts"]>summary').click();await page.locator('[data-admission-field="tuition"] [data-agent-knowledge]').click();
  await page.locator('.yk-focused-quote').waitFor();assert.ok(page.url().includes('entry_id='+entryId));assert.ok(page.url().includes('segment_id='+segmentId));assert.match(await page.locator('.yk-focused-quote').innerText(),/12000元/);
  await page.reload();await page.locator('.yk-focused-quote').waitFor();assert.ok(page.url().includes('segment_id='+segmentId));
 });
 await check('a second task has no leaked composing draft and no external or paid requests occurred',async()=>{
  currentTask={...currentTask,id:secondRid,research_id:secondRid,query:'[夹具] 另一项独立研究'};await page.goto(base+'/#/research?id='+secondRid);await page.locator('#ya-followup').waitFor();assert.equal(await page.locator('#ya-followup').inputValue(),'');assert.deepEqual(externalRequests,[]);assert.equal(requests.filter(row=>row.method==='POST').length,0);assert.deepEqual(errors,[]);
 });
}
run().catch(error=>{console.error(error.stack);process.exitCode=1;}).finally(async()=>{releasePoll?.();await browser?.close();if(server)await new Promise(resolve=>server.close(resolve));fs.mkdirSync(out,{recursive:true});fs.writeFileSync(path.join(out,'browser-official-closure.json'),JSON.stringify({mode:'isolated_fixture',success:results.length===6&&results.every(row=>row.passed),results,errors,externalRequests,requests,notes:['Rendered fixtures only; no real database, Key or paid API.','Ephemeral localhost port; existing services are not used.']},null,2));});
