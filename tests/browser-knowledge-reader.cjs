/* Isolated browser fixture: no app DB, API credentials or external network calls. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const root=path.resolve(__dirname,'../static'),out=path.resolve(__dirname,'../data/runtime/node3/reader-browser');
fs.mkdirSync(out,{recursive:true});
const id='k_'+'a'.repeat(24),sid='sg_'+'b'.repeat(32),otherId='sg_'+'c'.repeat(32);
const quality={version:1,state:'accepted',reusable:true,material_type:'catalog',reasons:[{message:'[夹具] 官方目录准入。'}]};
const baseEntry={id,title:'[夹具] 2027 年计算机学院招生目录',unit_id:'u_fixture',unit_name:'[夹具] 样本大学',year:2027,url:'https://example.edu.cn/catalog.pdf',status:'official_original_read',original_available:true,usable:true,validity:'active',quality,index_state:'succeeded',extraction:{id:'ex_fixture',generation_id:'kg_fixture',format:'pdf',page_kind:'physical',extraction_status:'complete',segment_count:24},text:'LEGACY_CANARY',sha256:'a'.repeat(64),provenance:['r_fixture'],versions:[]};
const rawSegment={id:sid,entry_id:id,extraction_id:'ex_fixture',generation_id:'kg_fixture',text:'计算机学院仅接受以下专业；其他专业不接受。\n<script>window.readerUnsafe=true</script>',char_start:32001,char_end:32080,page_start:21,page_end:21,page_kind:'physical',format:'pdf',kind:'table_row',section_path:['计算机学院','报考要求']};
const results=[],errors=[],requests=[],held=[];
let browser,page,server,base,entry={...baseEntry},reindexMode='succeeded',holdRead=false,holdJobs=false;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const clone=value=>JSON.parse(JSON.stringify(value));
const segment=()=>({...rawSegment,source:entry,format:entry.extraction.format,page_kind:entry.extraction.page_kind});
const readValue=selected=>({segment:selected,contexts:[{role:'table_header',text:'专业代码\t专业名称\t报考要求',char_start:30001,char_end:30020,page_start:20,page_end:20}],neighbors:[{...selected,id:otherId,text:'但不接受同等学力考生。',page_start:22,page_end:22}],returned_chars:120,truncated:false});
async function check(name,run){try{await run();results.push({name,passed:true});console.log('PASS '+name);}catch(error){results.push({name,passed:false,error:error.stack});console.log('FAIL '+name+': '+error.message);await page.screenshot({path:path.join(out,'failure-'+results.length+'.png'),fullPage:false}).catch(()=>{});}}
async function open(){await page.goto(base+'/fixture?entry_id='+id);await page.locator('#yk-reader-title').waitFor();if(entry.index_state==='succeeded'&&entry.usable)await page.locator('[data-knowledge-segment]').first().waitFor();}
async function maintenance(){if(!await page.locator('.yk-maintenance').evaluate(node=>node.open))await page.locator('.yk-maintenance>summary').click();}
async function release(){for(const item of held)item.resolve();}
async function run(){
 server=http.createServer((request,response)=>{const url=new URL(request.url,'http://fixture');if(url.pathname==='/app'){response.writeHead(200,{'content-type':'text/html;charset=utf-8'});response.end(fs.readFileSync(path.join(root,'index.html')));return;}if(url.pathname==='/fixture'){response.writeHead(200,{'content-type':'text/html;charset=utf-8'});response.end(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/styles.css"><body><main id="fixture"></main><script type="module">import {mountKnowledge} from '/knowledge.mjs';import {createApi} from '/data.mjs';window.cleanup=mountKnowledge(document.querySelector('#fixture'),{api:createApi(),initialEntryId:new URL(location).searchParams.get('entry_id')||''});</script></body></html>`);return;}const target=path.join(root,path.basename(url.pathname));if(!/\.(mjs|css|svg)$/.test(target)||!fs.existsSync(target)){response.writeHead(404);response.end();return;}response.writeHead(200,{'content-type':target.endsWith('.mjs')?'text/javascript;charset=utf-8':target.endsWith('.svg')?'image/svg+xml':'text/css;charset=utf-8'});response.end(fs.readFileSync(target));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));base='http://127.0.0.1:'+server.address().port;
 browser=await chromium.launch({headless:true,channel:'msedge'});page=await browser.newPage({viewport:{width:1440,height:1050}});page.setDefaultTimeout(6000);page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());if(url.origin!==base)return route.abort();if(!url.pathname.startsWith('/api/'))return route.continue();
  const body=req.postDataJSON();requests.push({path:url.pathname,query:Object.fromEntries(url.searchParams),method:req.method(),body});
  const good=async data=>route.fulfill({json:{ok:true,data,error:null}}).catch(error=>{if(!/closed|handled|cancel|abort|Invalid Interception/i.test(error.message))throw error;});
  if(url.pathname==='/api/catalog-bootstrap')return good({revision:'fixture-r1',meta:{target_year:2028,reference_year:2027},units:[],coverage:{release_ready:false},service:{search_configured:false}});
  if(url.pathname==='/api/radar')return good({program_ids:[],unread_count:0});
  if(url.pathname==='/api/settings')return good({ai:{has_key:false},yuanbao:{has_key:false}});
  if(url.pathname==='/api/research')return good(url.searchParams.has('research_id')?{id:'r_'+'a'.repeat(32),status:'completed',query:'[夹具] 计算机专业研究',events:[],report:{evidence:[{...baseEntry,id:'source_fixture',knowledge_id:id,knowledge_saved:true,knowledge_current:true,knowledge_quality:quality,segments:[{id:sid,generation_id:'kg_fixture',page_start:21}]}]}}:{tasks:[]});
  if(url.pathname==='/api/knowledge')return good(url.searchParams.has('entry_id')?clone(entry):{entries:entry.validity==='active'?[entry]:[],total:entry.validity==='active'?1:0,page:1,page_size:12,summary:{total:1,quality_counts:{accepted:1,pending:0,excluded:0}},facets:{units:[{unit_id:entry.unit_id,unit_name:entry.unit_name}],years:[2027]}});
  if(url.pathname==='/api/knowledge/segments')return good({segments:[segment(),{...segment(),id:otherId,text:'NEXT_POSITION_CANARY'}],total:2,page:1});
  if(url.pathname==='/api/knowledge/segment'){
   const selected={...segment(),id:url.searchParams.get('segment_id')};if(holdRead&&selected.id===sid){await new Promise(resolve=>held.push({key:'read',resolve}));}
   if(selected.id===otherId)selected.text='NEXT_POSITION_CANARY';return good(readValue(selected));
  }
  if(url.pathname==='/api/knowledge/jobs'){if(holdJobs)await new Promise(resolve=>held.push({key:'jobs',resolve}));return good({entry_id:id,jobs:[{state:entry.index_state,attempts:2,error_code:entry.index_state==='failed'?'fixture_failure':''}],backend:'local_segments'});}
  if(url.pathname==='/api/knowledge/state'){entry={...entry,validity:body.validity,usable:body.validity==='active',validity_reason:body.reason};return good({entry});}
  if(url.pathname==='/api/knowledge/select-version'){entry={...entry,validity:'active',usable:true,validity_reason:body.reason};return good({entry});}
  if(url.pathname==='/api/knowledge/reindex'){entry={...entry,index_state:reindexMode};return good({state:reindexMode,rebuilt:reindexMode==='succeeded'});}
  return route.fulfill({status:404,json:{ok:false,error:{message:'未知夹具API'}}});
 });
 await check('PDF hit opens page 21 with a separate page 20 table header and safe source strings',async()=>{
  await open();await page.locator('[data-knowledge-segment="'+sid+'"]').click();await page.locator('.yk-focused-quote').waitFor();
  assert.match(await page.locator('.yk-focused-quote').innerText(),/第 21 页/);assert.match(await page.locator('.yk-contexts').innerText(),/第 20 页/);
  assert.equal(await page.locator('.yk-focused-quote a').getAttribute('href'),entry.url+'#page=21');assert.equal(await page.locator('.yk-reader script').count(),0);assert.equal(await page.evaluate(()=>window.readerUnsafe),undefined);
  assert.ok(!(await page.locator('.yk-reader').innerText()).includes('LEGACY_CANARY'));await page.screenshot({path:path.join(out,'fixture-reader-desktop.png'),fullPage:false});
 });
 await check('bounded neighbor expansion asks for a larger real window',async()=>{
  assert.match(await page.locator('.yk-neighbors').innerText(),/但不接受同等学力/);await page.locator('[data-knowledge-reader="expand"]').click();await page.locator('.yk-focused-quote').waitFor();assert.equal(requests.filter(item=>item.path==='/api/knowledge/segment').at(-1).query.before,'2');
 });
 await check('maintenance requires a reason and confirmation and withdrawal hides old quotes',async()=>{
  await maintenance();await page.locator('[data-knowledge-maintenance="revoke"]').click();assert.match(await page.locator('.yk-maintenance-message').innerText(),/填写维护原因/);assert.equal(requests.filter(item=>item.method==='POST').length,0);
  await page.locator('[name="maintenance_reason"]').fill('官网已更正，以新公告为准');await page.locator('[data-knowledge-maintenance="revoke"]').click();assert.equal(requests.filter(item=>item.method==='POST').length,0);
  await page.locator('[data-knowledge-confirm]').click();await page.locator('.yk-reader-unavailable h3').filter({hasText:'已撤信'}).waitFor();
  assert.equal(await page.locator('.yk-focused-quote,.yk-segment-hits').count(),0);assert.ok(!(await page.locator('.yk-reader').innerText()).includes(rawSegment.text));
  assert.equal(requests.find(item=>item.path==='/api/knowledge/state').body.replacement_id,undefined);assert.equal(await page.locator('.yk-maintenance').evaluate(node=>node.open),true);
 });
 await check('restoring and selecting a version retain explicit provenance and refresh the reader',async()=>{
  await page.locator('[name="maintenance_reason"]').fill('已复核留档原件');await page.locator('[data-knowledge-maintenance="restore"]').click();await page.locator('[data-knowledge-confirm]').click();await page.locator('[data-knowledge-segment]').first().waitFor();
  await page.locator('[name="maintenance_reason"]').fill('对照原文采用这一版本');await page.locator('[data-knowledge-maintenance="select-version"]').click();await page.locator('[data-knowledge-confirm]').click();await page.locator('.yk-maintenance-message').filter({hasText:'维护已保存'}).waitFor();assert.equal(requests.filter(item=>item.path==='/api/knowledge/select-version').length,1);
 });
 await check('failed index jobs show an honest outcome and permit retry',async()=>{
  reindexMode='failed';await page.locator('[data-knowledge-maintenance="reindex"]').click();await page.locator('.yk-maintenance-message').filter({hasText:'重建未完成'}).waitFor();assert.match(await page.locator('.yk-index-status').innerText(),/失败/);assert.equal(await page.locator('.yk-focused-quote').count(),0);
  reindexMode='succeeded';await page.locator('[data-knowledge-maintenance="reindex"]').click();await page.locator('.yk-maintenance-message').filter({hasText:'本地索引重建已完成'}).waitFor();await page.locator('[data-knowledge-segment]').first().waitFor();
 });
 await check('an asynchronous jobs response preserves a composing Chinese textarea node',async()=>{
  await open();holdJobs=true;await maintenance();await page.waitForFunction(()=>document.querySelector('.yk-maintenance')?.open);
  await page.locator('[name="maintenance_reason"]').evaluate(node=>{window.imeNode=node;node.focus();node.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));node.value='正在输入中文';node.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true,data:'文'}));});
  await delay(80);held.find(item=>item.key==='jobs')?.resolve();holdJobs=false;await delay(100);
  assert.equal(await page.evaluate(()=>window.imeNode===document.querySelector('[name="maintenance_reason"]')),true);assert.equal(await page.locator('[name="maintenance_reason"]').inputValue(),'正在输入中文');
  await page.locator('[name="maintenance_reason"]').evaluate(node=>node.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'中文'})));assert.equal(await page.locator('[name="maintenance_reason"]').inputValue(),'正在输入中文');
 });
 await check('a delayed older position cannot replace a more recently selected quote',async()=>{
  holdRead=true;await page.locator('[data-knowledge-segment="'+sid+'"]').click();await page.locator('.yk-reading-state').waitFor();await page.locator('[data-knowledge-reader="close-read"]').click();await page.locator('[data-knowledge-segment="'+otherId+'"]').click();await page.locator('.yk-focused-quote').waitFor();
  held.find(item=>item.key==='read')?.resolve();holdRead=false;await delay(120);assert.match(await page.locator('.yk-focused-quote').innerText(),/NEXT_POSITION_CANARY/);
 });
 await check('real app Agent callback carries a legal segment through the hash route into the focused reader',async()=>{
  entry={...baseEntry};await page.goto(base+'/app#/research?id=r_'+'a'.repeat(32));await page.locator('[data-agent-tab="sources"]').click();
  await page.locator('[data-agent-knowledge-segment="'+sid+'"]').click();await page.locator('.yk-focused-quote').waitFor();
  const params=new URLSearchParams((await page.evaluate(()=>location.hash)).split('?')[1]);assert.equal(params.get('entry_id'),id);assert.equal(params.get('segment_id'),sid);
  assert.equal(requests.filter(item=>item.path==='/api/knowledge/segment').at(-1).query.segment_id,sid);assert.match(await page.locator('.yk-focused-quote').innerText(),/第 21 页/);
  await page.goto(base+'/app#/knowledge?entry_id='+id+'&segment_id='+sid);await page.locator('.yk-focused-quote').waitFor();assert.match(await page.locator('.yk-focused-quote').innerText(),/第 21 页/);
 });
 await check('HTML originals have semantic positions without invented PDF anchors',async()=>{
  entry={...baseEntry,url:'https://example.edu.cn/catalog.html',extraction:{...baseEntry.extraction,format:'html',page_kind:'logical'}};await open();await page.locator('[data-knowledge-segment="'+sid+'"]').click();await page.locator('.yk-focused-quote').waitFor();
  assert.equal(await page.locator('.yk-focused-quote a').getAttribute('href'),entry.url);assert.match(await page.locator('.yk-focused-quote').innerText(),/计算机学院 \/ 报考要求/);
 });
 await check('320, 390 and desktop layouts remain readable and disposal prevents late rendering',async()=>{
  for(const width of [320,390,1440,2552]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`${width}px overflow`);}
  await page.setViewportSize({width:390,height:900});await page.screenshot({path:path.join(out,'fixture-reader-mobile.png'),fullPage:true});
  holdRead=true;await page.locator('[data-knowledge-reader="close-read"]').click();await page.locator('[data-knowledge-segment="'+sid+'"]').click();await delay(80);await page.evaluate(()=>{window.cleanup();document.querySelector('#fixture').innerHTML='<p id="disposed">DISPOSED</p>';});await release();await delay(100);assert.equal(await page.locator('#disposed').innerText(),'DISPOSED');assert.deepEqual(errors,[]);
 });
}
run().catch(error=>{console.error(error);results.push({name:'harness',passed:false,error:error.stack});}).finally(async()=>{await release();if(browser)await browser.close();if(server)await new Promise(resolve=>server.close(resolve));const report={passed:results.length>0&&results.every(item=>item.passed),results,errors,requests,scope:'Temporary static server on random loopback port, all API responses mocked; no real DB or provider calls.'};fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,checks:results.length,report:path.join(out,'report.json')}));process.exitCode=report.passed?0:1;});
