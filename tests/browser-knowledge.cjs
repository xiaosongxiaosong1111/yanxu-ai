/* All API responses below are fixtures; no real keys, searches or knowledge writes. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const base=(process.env.AGENT_URL||'http://127.0.0.1:4180').replace(/\/+$/,'');
const origin=new URL(base).origin,out=path.resolve(__dirname,'../data/runtime/knowledge-ui');
fs.mkdirSync(out,{recursive:true});
const common={unit_id:'u1',unit_name:'[夹具] 样本大学',year:2027,requested_year:2028,checked_at:'2026-10-20T01:00:00Z',first_seen_at:'2026-10-20T01:00:00Z',last_seen_at:'2026-11-02T01:00:00Z',provenance:['r_fixture1','r_fixture2'],version_count:2,source_group:'same-original-fixture',field_verification:'not_structured'};
const accepted={version:1,state:'accepted',reusable:true,material_type:'catalog',reasons:[{code:'admitted',message:'[夹具] 来源、有效正文和年度达到本地准入规则。'}]};
const pending={version:1,state:'pending',reusable:false,material_type:'unknown',reasons:[{code:'year_unknown',message:'[夹具] 来源年度或正文仍需补查，暂不供自动复用。'}]};
const excluded={version:1,state:'excluded',reusable:false,material_type:'unknown',reasons:[{code:'navigation_only',message:'[夹具] 正文主要为导航片段，已拦截。'}]};
const entries=[
 {...common,quality:accepted,id:'k_a',title:'[夹具] 计算机专业招生目录',url:'https://example.edu.cn/catalog.pdf',status:'official_original_read',original_available:true,sha256:'a'.repeat(64),excerpt:'学校原文的真实片段（夹具）。',text:'考生应核对当年公布的考试科目。\n<script>window.knowledgeUnsafe=true</script>',text_origin:'full_extraction'},
 {...common,quality:pending,id:'k_b',unit_id:'u2',unit_name:'[夹具] 第二大学',year:null,title:'[夹具] 计算机招生检索线索',url:'https://example.edu.cn/search',status:'search_lead',original_available:false,excerpt:'这是搜索摘要，不是官网逐字原文。'},
 {...common,quality:accepted,id:'k_c',title:'[夹具] 已收录计算机目录',url:'https://example.edu.cn/published',status:'published_snapshot',original_available:false,excerpt:'这是已收录资料摘录。'},
 {...common,quality:excluded,id:'k_blocked',unit_id:'u2',title:'[夹具] 已拦截导航页',url:'https://example.edu.cn/navigation',status:'official_original_read',original_available:true,excerpt:'BLOCKED_EXCERPT_CANARY',text:'BLOCKED_BODY_CANARY',pages:[{page:1,text:'BLOCKED_PERSONAL_PAGE_CANARY'}]},
 {...common,id:'k_legacy',title:'[夹具] 尚未进行质量检查的旧资料',url:'https://example.edu.cn/legacy',status:'published_snapshot',original_available:false,excerpt:'旧记录不能自动显示准入通过。'},
 ...Array.from({length:22},(_,i)=>({...common,quality:pending,id:'k_fill'+i,unit_id:'u2',unit_name:'[夹具] 第二大学',title:i===0?'[夹具] 计算机长标题用于验证列表限制展示行数 '.repeat(12):'[夹具] 计算机资料 '+(i+1),url:'https://example.edu.cn/lead/'+i,status:'search_lead',original_available:false,excerpt:i===0?'这是用于验证长摘录视觉截断的真实片段夹具，不修改储存正文。'.repeat(20):'有限搜索线索，年度与字段仍需核验。'}))
];
const facets={units:[{unit_id:'u1',unit_name:common.unit_name},{unit_id:'u2',unit_name:'[夹具] 第二大学'}],years:[2027]};
const summary={total:25,search_lead:23,official_original_read:1,published_snapshot:1};
const results=[],errors=[],requests=[],held=[];
let browser,page,failOnce=false,breakSnapshot=false,emptyAccepted=false;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const clone=value=>JSON.parse(JSON.stringify(value));
async function check(name,run){const started=Date.now();try{await run();results.push({name,passed:true,ms:Date.now()-started});console.log('PASS '+name);}catch(error){results.push({name,passed:false,ms:Date.now()-started,error:error.message});console.log('FAIL '+name+': '+error.message);await page.screenshot({path:path.join(out,'failure-'+results.length+'.png'),fullPage:false}).catch(()=>{});}}
async function open(query=''){
 await page.goto(base+'/knowledge-fixture'+query);
 await page.locator('.yk-entry').first().waitFor();
}
async function releaseAll(){for(const item of held)item.release();}
(async()=>{
 browser=await chromium.launch({headless:true,channel:'msedge'});page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(6000);
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.origin!==origin)return route.abort();
  if(url.pathname==='/knowledge-fixture')return route.fulfill({contentType:'text/html',body:`<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/styles.css"><body><main id="knowledge-fixture-root"></main><div style="position:fixed;bottom:0;left:0;right:0;padding:6px;background:#752b91;color:white;text-align:center;z-index:99999;font:13px sans-serif">知识库界面回归夹具 · 模拟API · 非真实招生或检索成果</div><script type="module">import {mountKnowledge} from '/knowledge.mjs';const params=new URL(location).searchParams;window.__back=0;window.__cleanup=mountKnowledge(document.getElementById('knowledge-fixture-root'),{initialQuery:params.get('q')||'',initialEntryId:params.get('entry_id')||'',initialQuality:params.get('quality')||'accepted',onBack:()=>window.__back++,api:async(path,{signal}={})=>{const response=await fetch(path,{signal});const value=await response.json();if(!value.ok)throw new Error(value.error.message);return value.data;}});</script></body></html>`});
  if(!url.pathname.startsWith('/api/'))return route.continue();
  requests.push({method:request.method(),path:url.pathname,query:Object.fromEntries(url.searchParams)});
  if(request.method()!=='GET'||url.pathname!=='/api/knowledge')return route.abort();
  const good=async data=>{await route.fulfill({json:{ok:true,data,error:null}}).catch(error=>{if(!/closed|handled|cancel|abort|Invalid Interception/i.test(error.message))throw error;});};
  const id=url.searchParams.get('entry_id'),q=url.searchParams.get('q')||'';
  if(q==='old'||id==='k_slow'){await new Promise(resolve=>held.push({key:id||q,release:resolve}));}
  if(id){
   let entry=clone(entries.find(item=>item.id===id)||entries[0]);entry.id=id;
   if(id==='k_slow')entry.title='[夹具] STALE DETAIL';
   if(breakSnapshot&&id==='k_a')entry={...entry,quality:pending,original_available:false,snapshot_status:'missing_or_changed'};
   return good(entry);
  }
  if(q==='failure'&&failOnce){failOnce=false;return route.fulfill({status:503,json:{ok:false,data:null,error:{code:'fixture_failure',message:'[夹具] 本地知识库暂不可用'}}});}
  const sourceRows=emptyAccepted?entries.filter(entry=>entry.quality?.state!=='accepted'):entries;
  let rows=sourceRows;
  if(q==='old'||q==='latest')rows=[{...entries[0],title:q==='old'?'[夹具] STALE LIST':'[夹具] LATEST LIST'}];
  else if(q)rows=rows.filter(item=>(item.title+' '+item.unit_name).includes(q));
  for(const field of ['status','unit_id','year'])if(url.searchParams.get(field))rows=rows.filter(item=>String(item[field])===url.searchParams.get(field));
  const section=url.searchParams.get('quality')||'accepted';
  if(section!=='all')rows=rows.filter(item=>(item.quality?.state||'pending')===section);
  const pageNumber=Number(url.searchParams.get('page')||1),pageSize=Number(url.searchParams.get('page_size')||12);
  return good({entries:rows.slice((pageNumber-1)*pageSize,pageNumber*pageSize),total:rows.length,page:pageNumber,page_size:pageSize,summary:{...summary,total:sourceRows.length,quality_counts:{accepted:sourceRows.filter(row=>row.quality?.state==='accepted').length,pending:sourceRows.filter(row=>!row.quality||row.quality.state==='pending').length,excluded:sourceRows.filter(row=>row.quality?.state==='excluded').length}},facets});
 });
 await check('all source types, school/year/status filters, query and pagination use the public library API',async()=>{
  await open();assert.equal(requests.at(-1).query.quality,'accepted');assert.equal(await page.locator('.yk-entry').count(),2);assert.equal(await page.locator('.yk-entry[data-quality-state="pending"]').count(),0);
  await page.locator('[data-knowledge-quality="all"]').click();await page.waitForFunction(()=>document.querySelectorAll('.yk-entry').length===12);
  assert.match(await page.locator('[data-knowledge-open="k_a"] .yk-entry-excerpt').innerText(),/原文已留档，打开详情/);
  const clipped=await page.locator('[data-knowledge-open="k_fill0"]').evaluate(node=>['h2','.yk-entry-excerpt'].map(selector=>{const item=node.querySelector(selector),style=getComputedStyle(item);return {height:item.getBoundingClientRect().height,lineHeight:parseFloat(style.lineHeight),scroll:item.scrollHeight};}));
  assert.ok(clipped.every(item=>item.height<=item.lineHeight*2+1&&item.scroll>item.height),'long list titles and excerpts stay within two visible lines');
  assert.match(await page.locator('[name="status"]').innerText(),/原文留档/);assert.match(await page.locator('.yk-entry').nth(1).innerText(),/年度待核验/);
  await page.locator('[data-knowledge-action="next"]').click();await page.waitForFunction(()=>document.querySelector('.yk-pagination')?.innerText.includes('第 2 / 3 页'));
  assert.equal(requests.at(-1).query.page,'2');await page.locator('[data-knowledge-action="previous"]').click();await page.waitForFunction(()=>document.querySelector('.yk-pagination')?.innerText.includes('第 1 / 3 页'));
  await page.locator('#yk-query').fill('计算机');await page.locator('#yk-search-form').dispatchEvent('submit');
  await page.locator('[name="unit_id"]').selectOption('u1');await page.locator('[name="year"]').selectOption('2027');await page.locator('[name="status"]').selectOption('official_original_read');
  await page.waitForFunction(()=>document.querySelectorAll('.yk-entry').length===1);
  assert.equal(requests.at(-1).query.q,'计算机');assert.equal(requests.at(-1).query.status,'official_original_read');assert.equal(requests.at(-1).query.year,'2027');assert.equal(requests.at(-1).query.unit_id,'u1');
  await page.screenshot({path:path.join(out,'fixture-library-desktop.png'),fullPage:false});
 });
 await check('reading original text is safe, provenance is inspectable and returning preserves filters and focus',async()=>{
  await page.locator('[data-knowledge-open="k_a"]').click();await page.locator('#yk-reader-title').waitFor();
  assert.match(await page.locator('.yk-reader-text').innerText(),/<script>/);assert.equal(await page.locator('.yk-reader script').count(),0);assert.equal(await page.evaluate(()=>window.knowledgeUnsafe),undefined);
  assert.equal(await page.locator('.yk-original-link').getAttribute('href'),entries[0].url);
  await page.locator('.yk-proof-details summary').click();assert.match(await page.locator('.yk-proof-details').innerText(),/2 次研究/);assert.match(await page.locator('.yk-proof-details').innerText(),/2 个版本/);
  await page.screenshot({path:path.join(out,'fixture-library-reader-desktop.png'),fullPage:false});
  await page.locator('[data-knowledge-action="close-detail"]').click();assert.equal(await page.locator('[name="status"]').inputValue(),'official_original_read');assert.equal(await page.locator(':focus').getAttribute('data-knowledge-open'),'k_a');
 });
 await check('an invalidated original suppresses old text in both the reader and the selected list row',async()=>{
  breakSnapshot=true;await page.locator('[data-knowledge-open="k_a"]').click();await page.locator('#yk-reader-title').waitFor();
  assert.match(await page.locator('.yk-integrity-notice').innerText(),/原文留档需要重新核对/);
  assert.equal(await page.locator('.yk-reader-text').count(),0);assert.ok(!(await page.locator('.yk-page').innerText()).includes(entries[0].excerpt));
  breakSnapshot=false;await page.locator('[data-knowledge-action="close-detail"]').click();
 });
 await check('new searches and new details discard delayed older responses',async()=>{
  await open();const oldStarted=page.waitForRequest(request=>new URL(request.url()).searchParams.get('q')==='old');await page.locator('#yk-query').fill('old');await oldStarted;
  await page.locator('#yk-query').fill('latest');await page.getByRole('heading',{name:'[夹具] LATEST LIST',exact:true}).waitFor();held.find(item=>item.key==='old').release();await delay(100);
  assert.equal(await page.getByRole('heading',{name:'[夹具] STALE LIST',exact:true}).count(),0);
  await page.evaluate(()=>{const button=document.querySelector('[data-knowledge-open]');button.dataset.knowledgeOpen='k_slow';});
  const slowStarted=page.waitForRequest(request=>new URL(request.url()).searchParams.get('entry_id')==='k_slow');await page.locator('[data-knowledge-open="k_slow"]').click();await slowStarted;
  await page.evaluate(()=>document.querySelector('[data-knowledge-open]').dataset.knowledgeOpen='k_b');
  await page.locator('[data-knowledge-open="k_b"]').click();await page.locator('#yk-reader-title').filter({hasText:'检索线索'}).waitFor();held.find(item=>item.key==='k_slow').release();await delay(100);
  assert.equal(await page.getByText('[夹具] STALE DETAIL',{exact:true}).count(),0);assert.match(await page.locator('.yk-reader-content').innerText(),/搜索摘要/);
 });
 await check('quality sections remain independent from source type and clearing filters preserves the selected section',async()=>{
  await open('?quality=pending');assert.equal(await page.locator('[role="tab"][data-knowledge-quality="pending"]').getAttribute('aria-selected'),'true');
  assert.equal(requests.at(-1).query.quality,'pending');assert.equal(await page.locator('.yk-entry[data-quality-state="accepted"]').count(),0);
  await page.locator('#yk-query').fill('计算机');await page.locator('#yk-search-form').dispatchEvent('submit');await page.locator('[name="status"]').selectOption('search_lead');
  await page.locator('[data-knowledge-action="reset"]').click();await page.waitForFunction(()=>document.querySelectorAll('.yk-entry').length===12);
  assert.equal(requests.at(-1).query.quality,'pending');assert.equal(requests.at(-1).query.status,'');assert.equal(await page.locator('#yk-query').inputValue(),'');
  await page.locator('[role="tab"][data-knowledge-quality="excluded"]').click();await page.locator('[data-knowledge-open="k_blocked"]').waitFor();
  await page.locator('.yk-entry-quality summary').click();assert.match(await page.locator('.yk-entry-quality').innerText(),/正文主要为导航片段/);
  await page.locator('[data-knowledge-open="k_blocked"]').click();await page.locator('#yk-reader-title').waitFor();
  assert.equal(await page.locator('.yk-reader-text,.yk-summary-text,.yk-page-excerpts').count(),0);
  for(const value of ['BLOCKED_EXCERPT_CANARY','BLOCKED_BODY_CANARY','BLOCKED_PERSONAL_PAGE_CANARY'])assert.ok(!(await page.locator('.yk-page').innerText()).includes(value));
  assert.match(await page.locator('.yk-admission-details').innerText(),/正文主要为导航片段/);assert.equal(await page.locator('.yk-original-link').getAttribute('href'),'https://example.edu.cn/navigation');
  await page.screenshot({path:path.join(out,'fixture-quality-excluded-desktop.png'),fullPage:false});
  await open('?quality=pending&entry_id=k_legacy');await page.locator('#yk-reader-title').waitFor();
  assert.equal(await page.locator('.yk-reader .yk-quality-badge.is-accepted').count(),0);assert.match(await page.locator('.yk-reader .yk-quality-notice').innerText(),/暂不供研究自动复用/);
 });
 await check('an empty accepted section routes to pending material without misreporting an empty library',async()=>{
  emptyAccepted=true;try{
   await page.goto(base+'/knowledge-fixture');await page.getByRole('heading',{name:'还没有可复用资料',exact:true}).waitFor();
   assert.equal(await page.getByRole('heading',{name:'还没有保存相关资料',exact:true}).count(),0);
   await page.locator('.yk-empty [data-knowledge-quality="pending"]').click();await page.locator('.yk-entry').first().waitFor();assert.equal(requests.at(-1).query.quality,'pending');
  }finally{emptyAccepted=false;}
 });
 await check('failed reads recover, empty results can reset and initial query/detail are supported',async()=>{
  await open();failOnce=true;await page.locator('#yk-query').fill('failure');await page.locator('#yk-search-form').dispatchEvent('submit');await page.locator('[data-knowledge-action="retry"]').waitFor();
  await page.locator('[data-knowledge-action="retry"]').click();await page.getByRole('heading',{name:'当前条件下没有可复用资料',exact:true}).waitFor();
  await page.locator('[data-knowledge-action="reset"]').last().click();await page.locator('.yk-entry').first().waitFor();assert.equal(await page.locator('#yk-query').inputValue(),'');
  await open('?q='+encodeURIComponent('计算机')+'&entry_id=k_a');await page.locator('#yk-reader-title').waitFor();assert.equal(await page.locator('#yk-query').inputValue(),'计算机');
 });
 await check('320/390/1440/2552 layouts, keyboard access and disposal remain usable',async()=>{
  for(const width of [320,390,1440,2552]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`${width}px overflow`);}
  await page.setViewportSize({width:390,height:900});await page.screenshot({path:path.join(out,'fixture-library-reader-mobile.png'),fullPage:true});
  await page.locator('[data-knowledge-action="close-detail"]').focus();await page.keyboard.press('Enter');assert.equal(await page.locator('.yk-reader').count(),0);
  await page.locator('[data-knowledge-action="back"]').click();assert.equal(await page.evaluate(()=>window.__back),1);
  await page.evaluate(()=>document.querySelector('[data-knowledge-open]').dataset.knowledgeOpen='k_slow');const disposeRequest=page.waitForRequest(request=>new URL(request.url()).searchParams.get('entry_id')==='k_slow');await page.locator('[data-knowledge-open="k_slow"]').click();await disposeRequest;
  await page.evaluate(()=>{window.__cleanup();document.getElementById('knowledge-fixture-root').innerHTML='<p id="disposed">DISPOSED</p>';});
  await releaseAll();await delay(100);assert.equal(await page.locator('#disposed').innerText(),'DISPOSED');assert.deepEqual(errors,[]);
  assert.equal(requests.some(item=>item.method!=='GET'),false);
 });
})().catch(error=>{results.push({name:'harness',passed:false,error:error.stack});console.error(error);}).finally(async()=>{
 await releaseAll();if(browser)await browser.close();
 const report={passed:results.length>0&&results.every(item=>item.passed),results,errors,requests,scope:'Module mounted in a labelled fixture page; every API request intercepted; no real searches, credentials or library writes.'};
 fs.writeFileSync(path.join(out,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,checks:results.length,report:path.join(out,'browser-report.json')}));process.exitCode=report.passed?0:1;
});
