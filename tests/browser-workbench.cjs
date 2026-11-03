/* Deterministic UI fixtures only: no real key reads, saves, model or Yuanbao calls. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const base=(process.env.AGENT_URL||'http://127.0.0.1:4180').replace(/\/+$/,'');
const origin=new URL(base).origin,output=path.resolve(__dirname,'../data/runtime/research-workbench-upgrade');
fs.mkdirSync(output,{recursive:true});
const id='r_22222222222222222222222222222222',results=[],errors=[];
const clone=value=>JSON.parse(JSON.stringify(value));
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const candidates=Array.from({length:12},(_,i)=>({id:'p'+i,unit_id:'u',unit_name:'[夹具] 南方大学',year:2027,name:'计算机科学与技术',department:'计算机学院',direction:'方向'+i,study_mode:'全日制',subjects:[{code:'101'},{code:'201'},{code:'301'},{code:'408'}]}));
const evidence=Array.from({length:19},(_,i)=>({id:'s'+i,title:'[夹具] 官方材料'+i,status:i<2?'official_original_read':'search_lead',url:'https://example.edu.cn/'+i,year:i<2?2027:null,excerpt:'仅供交互回归的原文摘录',knowledge_saved:true,knowledge_id:'k_'+i,knowledge_quality:{state:i<2?'accepted':'pending',reusable:i<2,material_type:i<2?'catalog':'unknown',reasons:[]}}));
function makeTask(status='running'){
 return {id,research_id:id,query:'[交互夹具] 我想考计算机，明年入学',turn_id:2,status,can_continue:status!=='running',target_year:2028,reference_year:2027,profile:{education:'unknown',study_mode:'unknown',subjects:[],provinces:[]},last_event_id:4,events:[{id:1,turn_id:1,type:'tool_started',message:'上一轮旧动作'},{id:2,turn_id:2,type:'progress',title:'开始本轮研究'},{id:3,turn_id:2,type:'tool_started',message:'正在核对这一轮的官方资料'},{id:4,turn_id:2,type:'tool_completed',message:'本轮已有候选',data:{tool:'find_candidates'}}],usage:{model_calls:1,searches:1,documents:1},report:{candidates:clone(candidates),evidence:clone(evidence),questions:[{field:'target_year',label:'确认目标入学年份',message:'当前按2028年入学研究，请确认“明年”的含义。',options:[{label:'按2028年入学继续',value:2028}]},{field:'education',label:'目前的最高学历？',options:[{label:'本科',value:'bachelor'},{label:'硕士',value:'master'}]}],knowledge:{saved_count:19,accepted_count:2,pending_count:17,filtered_count:0,cache_hits:2},skills:[{id:'understand-intent',label:'需求理解',loaded:true,status:'loaded'},{id:'compare-plans',label:'比较方案',loaded:true,status:'used',executed_tools:['compare_candidates']},{id:'assess-transfer-options',label:'复试与调剂',loaded:false,status:'not_loaded'}],algorithm:{matching:{summary:{input_count:12,matched_count:0,unknown_count:12,excluded_count:0},matches:[],needs_confirmation:candidates.map(program=>({program,status:'unknown',subject_changes:{count:0,uncertain:true},reasons:[{message:'备考科目不全，暂不能确认衔接'}]}))}},gaps:['[夹具] 目标年度目录仍需核对']}};
}
let browser;
async function fixture(options={}){
 const page=await browser.newPage({viewport:{width:1440,height:900}});page.setDefaultTimeout(6500);
 const world={task:makeTask(options.status||'running'),posts:[],reads:0,ready:options.ready!==false,cancelReads:0,...options};
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.origin!==origin)return route.abort();
  if(!url.pathname.startsWith('/api/'))return route.continue();
  const good=(data,status=200)=>route.fulfill({status,json:{ok:true,data,error:null}}).catch(()=>{});
  if(request.method()==='POST'){
   const body=request.postDataJSON();world.posts.push({path:url.pathname,body});
   if(url.pathname==='/api/research/cancel'){world.task={...world.task,status:'cancelled',can_continue:world.ready};return good(world.task);}
   if(url.pathname==='/api/research/continue'){
    world.task={...world.task,status:'completed',turn_id:world.task.turn_id+1,can_continue:true,report:{...world.task.report,profile_proposal:{requires_confirmation:false}},...(body.profile?{profile:{...world.task.profile,...body.profile}}:{})};
    if(world.continueHeld)await new Promise(resolve=>world.releaseContinue=resolve);
    return good(world.task,202);
   }
   return route.abort();
  }
  if(url.pathname==='/api/catalog-bootstrap')return good({revision:'fixture',meta:{target_year:2028,reference_year:2027},units:[],coverage:{index_total:0,program_count:0,program_units:0,release_ready:false},service:{search_configured:false}});
  if(url.pathname==='/api/settings')return good({ai:{status:'connected',has_key:true},yuanbao:{status:'connected',has_key:true}});
  if(url.pathname==='/api/research'&&url.searchParams.has('research_id')){
   world.reads++;if(world.task.status==='cancelled'){
    world.cancelReads++;
    if(world.holdReady)await new Promise(resolve=>world.releaseReady=resolve);
    if(world.readyAfter&&world.cancelReads>=world.readyAfter)world.task={...world.task,can_continue:true};
   }
   return good(world.task);
  }
  return good({tasks:[],watches:[],summary:{},total:0});
 });
 await page.addInitScript(()=>window.addEventListener('DOMContentLoaded',()=>{const mark=document.createElement('div');mark.textContent='研究工作台交互夹具 · 模拟接口 · 非真实模型 / 元宝成果';mark.style.cssText='position:fixed;bottom:0;left:0;right:0;background:#752b91;color:white;text-align:center;padding:6px;z-index:99999;font:12px sans-serif';document.body.append(mark);}));
 await page.goto(base+'/#/research?id='+id);await page.locator('#ya-followup').waitFor();return {page,world};
}
async function check(name,run){const start=Date.now();try{await run();results.push({name,passed:true,ms:Date.now()-start});console.log('PASS '+name);}catch(error){results.push({name,passed:false,error:error.message,ms:Date.now()-start});console.log('FAIL '+name+': '+error.message);}}
(async()=>{
 browser=await chromium.launch({channel:'msedge',headless:true});
 await check('running research has an editable first-screen composer, compact school groups and current-turn action',async()=>{
  const {page,world}=await fixture();try{
   const box=await page.locator('#ya-followup').boundingBox();assert.ok(box.y+box.height<768);
   assert.equal(await page.locator('.ya-school-card').count(),1);assert.equal(await page.locator('.ya-school-directions[open]').count(),0);
   assert.ok((await page.locator('.ya-current-action').innerText()).includes('本轮已有候选'));assert.ok(!(await page.locator('.ya-current-action').innerText()).includes('上一轮'));
   assert.equal(await page.locator('.ya-activity-disclosure[open]').count(),0);assert.equal(await page.locator('#ya-followup').isEnabled(),true);
   await page.locator('#ya-followup').fill('我准备了408');await page.locator('#ya-followup').press('End');await delay(1650);
   assert.equal(await page.locator('#ya-followup').inputValue(),'我准备了408');assert.equal(await page.locator(':focus').getAttribute('id'),'ya-followup');
   assert.ok((await page.locator('.ya-decision-summary').innerText()).includes('已完成条件核算'));
   await page.screenshot({path:path.join(output,'fixture-workbench-desktop.png'),fullPage:true});assert.equal(world.posts.length,0);
  }finally{await page.close();}
 });
 await check('candidate school expansion is independent and preserved by polling; responsive pages do not overflow',async()=>{
  const {page,world}=await fixture();try{
   world.task.report.candidates=Array.from({length:10},(_,i)=>({...candidates[i],id:'different'+i,unit_id:'school'+Math.floor(i/2),unit_name:'[夹具] 学校'+Math.floor(i/2)}));
   await page.reload();await page.locator('.ya-school-card').first().waitFor();assert.equal(await page.locator('.ya-school-card').count(),3);
   await page.locator('.ya-school-directions summary').first().click();await delay(1600);assert.equal(await page.locator('.ya-school-directions[open]').count(),1);
   await page.locator('[data-agent-action="schools"]').click();assert.equal(await page.locator('.ya-school-card').count(),5);
   for(const width of [320,390,1440,2552]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,width+'px overflow');}
   await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(output,'fixture-workbench-mobile.png'),fullPage:true});
  }finally{await page.close();}
 });
 await check('stop then update waits for readiness and posts the new research exactly once',async()=>{
  const {page,world}=await fixture({ready:false,readyAfter:2});try{
   await page.locator('#ya-followup').fill('希望留在天津');await page.locator('#ya-followup').press('Control+Enter');
   await page.locator('.ya-task-status').filter({hasText:'本轮研究已完成'}).waitFor();
   assert.equal(world.posts.filter(x=>x.path.endsWith('/cancel')).length,1);assert.equal(world.posts.filter(x=>x.path.endsWith('/continue')).length,1);
   assert.equal(world.posts.at(-1).body.message,'希望留在天津');assert.ok(world.cancelReads>=2);assert.equal(await page.locator('#ya-followup').inputValue(),'');
   await delay(700);assert.equal(world.posts.length,2);
  }finally{await page.close();}
 });
 await check('cancelling a waiting update preserves the draft and ignores late readiness',async()=>{
  const {page,world}=await fixture({ready:false,holdReady:true});try{
   await page.locator('#ya-followup').fill('这一条不能被自动提交');await page.locator('#ya-followup').press('Control+Enter');
   await page.locator('[data-agent-action="abort-revise"]').waitFor();
   while(!world.releaseReady)await delay(30);
   await page.locator('[data-agent-action="abort-revise"]').click();world.releaseReady();await delay(500);
   assert.equal(world.posts.filter(x=>x.path.endsWith('/continue')).length,0);assert.equal(await page.locator('#ya-followup').inputValue(),'这一条不能被自动提交');
  }finally{world.releaseReady?.();await page.close();}
 });
 await check('after a new turn is submitted the UI cannot claim to cancel the accepted update',async()=>{
  const {page,world}=await fixture({continueHeld:true});try{
   await page.locator('#ya-followup').fill('按新条件研究');await page.locator('#ya-followup').press('Control+Enter');
   while(!world.releaseContinue)await delay(30);
   assert.equal(await page.locator('[data-agent-action="abort-revise"]').count(),0);
   assert.match(await page.locator('.ya-revise-message').innerText(),/已被服务端接受/);
   await page.locator('#ya-followup').fill('提交期间继续编辑的新草稿');world.releaseContinue();await page.locator('.ya-task-status').filter({hasText:'本轮研究已完成'}).waitFor();
   assert.equal(await page.locator('#ya-followup').inputValue(),'提交期间继续编辑的新草稿');assert.equal(world.posts.filter(x=>x.path.endsWith('/continue')).length,1);
  }finally{world.releaseContinue?.();await page.close();}
 });
 await check('explicit year and education selections are staged before applying; skills and knowledge remain truthful',async()=>{
  const {page,world}=await fixture({status:'awaiting_input'});try{
   await page.locator('[data-agent-answer="0"]').click();await page.locator('[data-agent-answer="1"][data-agent-option="0"]').click();assert.equal(world.posts.length,0);
   await page.locator('[data-agent-action="apply-answers"]').click();await page.locator('.ya-task-status').filter({hasText:'本轮研究已完成'}).waitFor();
   assert.deepEqual(world.posts.at(-1).body.profile,{target_year:2028,education:'bachelor'});
   await page.locator('[data-agent-tab="methods"]').first().click();assert.ok((await page.locator('.ya-skill-list').innerText()).includes('本轮未加载'));assert.ok((await page.locator('.ya-skill-list').innerText()).includes('相关工具已执行'));
   await page.locator('[data-agent-tab="sources"]').click();assert.equal(await page.locator('.ya-source-card').count(),8);await page.locator('[data-agent-source-page="2"]').click();assert.equal(await page.locator('.ya-source-card').count(),8);
   await page.locator('[data-agent-source-filter="official_original_read"]').click();assert.equal(await page.locator('.ya-source-card').count(),2);assert.equal(await page.locator('[data-agent-knowledge]').count(),2);
   assert.ok((await page.locator('.ya-source-list').innerText()).includes('已入知识库'));assert.deepEqual(errors,[]);
  }finally{await page.close();}
 });
 await check('knowledge admission shows accepted, pending, filtered and legacy states without excluded excerpts',async()=>{
  const {page,world}=await fixture({status:'completed'});try{
   world.task.report.evidence=[
    {id:'qa',title:'[质量夹具] 通过记录',url:'https://example.edu.cn/a',status:'official_original_read',knowledge_id:'quality-accepted',knowledge_saved:true,knowledge_quality:{state:'accepted',reusable:true,material_type:'catalog',reasons:[]}},
    {id:'qp',title:'[质量夹具] 待核记录',url:'https://example.edu.cn/p',status:'search_lead',knowledge_id:'quality-pending',knowledge_saved:true,knowledge_quality:{state:'pending',reusable:false,material_type:'unknown',reasons:[{code:'scope_unknown',message:'适用范围待核对'}]}},
    {id:'qx',title:'[质量夹具] 排除记录',url:'https://example.edu.cn/x',status:'search_lead',knowledge_saved:false,knowledge_filtered:true,excerpt:'EXCLUDED_PRIVATE_EXCERPT_MUST_NOT_RENDER',knowledge_quality:{state:'excluded',reusable:false,material_type:'unrelated',reasons:[{code:'not_admissions',message:'不是招生资料'}]}},
    {id:'ql',title:'[质量夹具] 旧记录',url:'https://example.edu.cn/l',status:'search_lead',knowledge_id:'quality-legacy',knowledge_saved:true}
   ];
   world.task.report.knowledge={saved_count:2,accepted_count:1,pending_count:1,filtered_count:1,cache_hits:0};
   await page.reload();await page.locator('#ya-followup').waitFor();await page.locator('[data-agent-tab="sources"]').click();
   assert.match(await page.locator('.ya-sources-intro').innerText(),/已通过 1 份 · 待核 1 份 · 已过滤 1 份/);
   assert.match(await page.locator('[data-agent-knowledge="quality-accepted"]').innerText(),/已入知识库/);
   assert.match(await page.locator('[data-agent-knowledge="quality-pending"]').innerText(),/待核线索/);
   assert.match(await page.locator('[data-agent-knowledge="quality-legacy"]').innerText(),/准入状态待复核/);
   const excluded=page.locator('.ya-source-card').filter({hasText:'[质量夹具] 排除记录'});
   assert.match(await excluded.innerText(),/未入库：不是招生资料/);assert.equal(await excluded.locator('.ya-source-excerpt').count(),0);
   assert.ok(!(await page.content()).includes('EXCLUDED_PRIVATE_EXCERPT_MUST_NOT_RENDER'));
   assert.ok(!(await excluded.innerText()).includes('保存未完成'));
   await page.screenshot({path:path.join(output,'fixture-admission-sources.png'),fullPage:true});
  }finally{await page.close();}
 });
 await check('leaving during readiness wait prevents a late automatic continue request',async()=>{
  const {page,world}=await fixture({ready:false,holdReady:true});try{
   await page.locator('#ya-followup').fill('离开时保留的草稿');await page.locator('#ya-followup').press('Control+Enter');while(!world.releaseReady)await delay(30);
   await page.locator('.ya-workspace-top [data-agent-action="settings"]').click();world.releaseReady();world.holdReady=false;world.task.can_continue=true;await delay(400);assert.equal(world.posts.filter(x=>x.path.endsWith('/continue')).length,0);
   await page.goBack();await page.locator('#ya-followup').waitFor();assert.equal(await page.locator('#ya-followup').inputValue(),'离开时保留的草稿');
  }finally{world.releaseReady?.();await page.close();}
 });
})().catch(error=>{results.push({name:'harness',passed:false,error:error.stack});}).finally(async()=>{
 if(browser)await browser.close();const report={passed:results.length>0&&results.every(row=>row.passed),results,errors,scope:'All APIs intercepted. No real provider requests or user credentials.'};fs.writeFileSync(path.join(output,'frontend-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,checks:results.length}));process.exitCode=report.passed?0:1;
});
