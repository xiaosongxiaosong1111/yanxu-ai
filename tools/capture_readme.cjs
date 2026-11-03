/* Real localhost screenshots; requires populated local source/catalog stores. No API mocks or paid calls. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
const base=(process.env.README_URL||'http://127.0.0.1:4180').replace(/\/$/,'');
const origin=new URL(base).origin,root=path.resolve(__dirname,'..');
const output=path.join(root,'docs/assets/screenshots'),reportDir=path.join(root,'data/runtime/repo-presentation');
fs.mkdirSync(output,{recursive:true});fs.mkdirSync(reportDir,{recursive:true});
const report={captured_at:new Date().toISOString(),mode:'Actual local application and official published data; no response mocks',shots:[],errors:[],blocked:[],localPosts:[]};
const allowedPosts=new Set(['/api/match','/api/route-plan','/api/adjustments','/api/sensitivity','/api/history/match']);
let browser;
(async()=>{
 browser=await chromium.launch({headless:true,...(process.env.README_BROWSER_CHANNEL?{channel:process.env.README_BROWSER_CHANNEL}:{})});
 const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1});
 const page=await context.newPage();page.setDefaultTimeout(20000);
 page.on('pageerror',error=>report.errors.push(error.message));
 await context.route('**/*',route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.origin!==origin){report.blocked.push({kind:'external',host:url.hostname});return route.abort();}
  if(request.method()!=='GET'){
   if(request.method()!=='POST'||!allowedPosts.has(url.pathname)){report.blocked.push({kind:'mutation',path:url.pathname});return route.abort();}
   report.localPosts.push({path:url.pathname,scope:'deterministic local algorithm; demonstration inputs'});
  }
  return route.continue();
 });
 const get=async endpoint=>{const response=await context.request.get(base+endpoint);assert.equal(response.status(),200,endpoint);const body=await response.json();assert.equal(body.ok,true,endpoint);return body.data;};
 const light=await get('/api/catalog-bootstrap');report.revision=light.revision;
 const programs=async(unit_id,q)=>{const data=await get('/api/catalog-programs?'+new URLSearchParams({unit_id,q,year:'2027',limit:'200',revision:light.revision}));return data.programs;};
 const nankai=(await programs('367949','081200')).find(p=>p.subjects?.some(s=>s.code==='408'));
 const zhengzhou=(await programs('368317','081200')).find(p=>p.tuition?.amount===8000);
 assert.ok(nankai&&zhengzhou,'Real published programs and fee evidence are required');
 report.demonstrationProfile={education:'bachelor',study_mode:'全日制',prepared_subjects:nankai.subjects.map(s=>s.code),scope:'南开大学2027参考目录',max_subject_changes:0};
 const profile={target_year:2028,reference_year:2027,education:'bachelor',background:'计算机科学与技术',study_mode:'全日制',ranking_mode:'subjects',preferences:{cost:1,subjects:5,location:1},max_subject_changes:0,subjects:nankai.subjects};
 await context.addInitScript(value=>localStorage.setItem('yanshu-profile-v1',JSON.stringify(value)),{saved:[nankai.id,zhengzhou.id],profile,preferences:{unit_id:nankai.unit_id,query:'',priority:'subjects',examCount:4}});
 async function goto(hash,selector){await page.goto(base+'/'+hash);await page.locator(selector).first().waitFor();await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(150);}
 async function shot(filename,description,options={}){
  await page.locator(':focus').evaluateAll(nodes=>nodes.forEach(node=>node.blur()));
  if(options.scroll)await page.locator(options.scroll).first().evaluate(node=>scrollTo(0,Math.max(0,scrollY+node.getBoundingClientRect().top-32)));else await page.evaluate(()=>scrollTo(0,0));
  const full=options.fullPage===true;
  const region=options.range?await page.evaluate(({from,to})=>{const first=document.querySelector(from).getBoundingClientRect(),last=document.querySelector(to).getBoundingClientRect();return {top:scrollY+first.top,height:Math.ceil(last.bottom-first.top+56)};},options.range):null;
  if(region){await page.setViewportSize({width:1440,height:region.height});await page.evaluate(top=>scrollTo(0,Math.max(0,top-28)),region.top);}
  await page.screenshot({path:path.join(output,filename),fullPage:full,animations:'disabled'});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,filename+' overflows');
  report.shots.push({file:'docs/assets/screenshots/'+filename,description,hash:new URL(page.url()).hash,viewport:page.viewportSize(),capture:region?'native DOM region':full?'full-page':'viewport',actual_application:true});
  if(region)await page.setViewportSize({width:1440,height:1000});
  console.log('CAPTURE '+filename);
 }
 await goto('#/','#ya-query');
 await shot('01-research-home.png','真实研究首页；当前连接状态与既有研究入口，无新增研究调用。');
 const tasks=await get('/api/research?limit=100');
 const task=tasks.tasks.filter(row=>row.report?.evidence?.length).sort((a,b)=>b.report.evidence.length-a.report.evidence.length)[0];
 assert.ok(task,'A real existing source-bearing research task is required; this script never creates one');
 await goto('#/research?id='+encodeURIComponent(task.id),'#ya-followup');
 await page.locator('[data-agent-tab="sources"]').click();await page.locator('.ya-source-card').first().waitFor();
 await shot('02-agent-sources.png','真实既有研究来源页，显示AI/元宝实际历史调用和来源取得状态；不冒充新一轮完成。');
 await goto('#/discover','.program-row');
 await shot('03-official-catalog.png','真实已发布南开目录列表；浏览器演示偏好，未进行额外检索。');
 await goto('#/plan?tool=routes','[data-wb-action="run-routes"]');
 await page.locator('[data-wb-action="run-routes"]').click();await page.locator('.wb-route-card').first().waitFor({timeout:60000});
 await shot('04-subject-routes.png','使用真实目录与演示条件运行本地科目路线算法，比较0/1/2个考试单元的调整。',{range:{from:'.wb-results>.wb-subhead',to:'[data-chart="route-coverage"]'}});
 await goto('#/program/'+encodeURIComponent(zhengzhou.id),'#detail-title');
 await page.locator('.detail-v2-facts [data-claim-id="tuition"]').click();await page.locator('[name="evidence-claim"]').waitFor();
 await shot('05-original-evidence.png','郑州大学真实8000元/年学费字段依据：官方原句、适用范围、页码与链接。');
 await page.keyboard.press('Escape');
 await goto('#/program/'+encodeURIComponent(nankai.id)+'?tab=history','[data-chart="admitted-range"]');
 await shot('06-historical-scores.png','真实录取公示初试样本图；缺失年份保持空缺，不生成分数。',{range:{from:'.ych-history>.ych-section-heading',to:'[data-chart="admitted-range"]'}});
 await goto('#/knowledge','[data-knowledge-open]');
 await shot('07-knowledge-admission.png','实际本机知识库质量分区，显示通过/待核/拦截的真实数量。');
 await page.locator('[data-knowledge-quality="excluded"]').first().click();await page.locator('[data-knowledge-open]').first().waitFor();
 await page.locator('[data-knowledge-open]').first().click();await page.locator('#yk-reader-title').waitFor();
 await shot('08-knowledge-rejection.png','真实已拦截来源与准入理由；个人或低质正文不显示。',{scroll:'.yk-quality-section'});
 await goto('#/settings','#ys-provider');
 assert.ok((await page.locator('input[type="password"]').evaluateAll(nodes=>nodes.map(node=>node.value))).every(value=>value===''),'Saved credentials must not be present');
 await shot('09-ai-settings.png','真实模型服务配置页，模型与连接状态可见；已保存凭据不回显。');
 await page.locator('[data-settings-tab="yuanbao"]').click();await page.locator('[name="auth_mode"][value="standard"]').check();
 await shot('10-yuanbao-settings.png','元宝腾讯云标准鉴权编辑表单，显示双凭据入口；没有点击测试、保存，不代表本轮标准方式验证成功。');
 await page.setViewportSize({width:390,height:1130});
 await goto('#/knowledge','[data-knowledge-open]');
 await shot('11-knowledge-mobile.png','390像素实际移动知识库，显示质量分区、筛选与首条真实来源。');
 report.passed=report.shots.length===11&&report.errors.length===0&&report.blocked.filter(row=>row.kind==='mutation').length===0;
 assert.ok(report.passed,JSON.stringify(report.errors));
})().catch(error=>{report.passed=false;report.failure=error.stack;console.error(error);process.exitCode=1;}).finally(async()=>{
 await browser?.close();fs.writeFileSync(path.join(reportDir,'screenshots-report.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify({passed:report.passed,shots:report.shots.length,errors:report.errors,localPosts:report.localPosts}));
});
