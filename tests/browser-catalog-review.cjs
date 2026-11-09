// Isolated real-browser regressions. All business APIs are local fixtures.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const http=require('node:http');
const path=require('node:path');
const {chromium}=require(process.env.FORMAL_PLAYWRIGHT_PATH||(()=>{try{return require.resolve('playwright');}catch{return 'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright';}})());
const staticRoot=path.resolve(process.env.CATALOG_REVIEW_STATIC||path.join(__dirname,'../static'));
const output=path.resolve(process.env.CATALOG_REVIEW_OUTPUT||path.join(__dirname,'../data/runtime/catalog-review/browser.json'));
const program={id:'p0',unit_id:'a',unit_name:'甲大学',department:'计算机学院',name:'计算机技术',code:'085404',direction:'人工智能',province:'天津',year:2027,study_mode:'非全日制',degree_type:'专业学位',subjects:[{code:'408',name:'计算机学科专业基础',scope:'national',exam_unit:4}]};
const bootstrap={revision:'fixture-review',meta:{reference_year:2027,target_year:2028,last_completed_admission_year:2026},units:[{id:'a',name:'甲大学',province:'天津',annual_status:'confirmed',program_count:1,official_url:'https://example.edu.cn/'}],coverage:{release_ready:false,index_total:1,annual_official_total:1,program_units:1,program_count:1},service:{search_configured:true}};
const report={staticRoot,checks:[],pageErrors:[],externalRequests:[],unexpectedApi:[],requests:[]};
const server=http.createServer((request,response)=>{
 const filename=path.resolve(staticRoot,'.'+new URL(request.url,'http://localhost').pathname.replace(/\/$/,'/index.html'));
 if(!filename.startsWith(staticRoot+path.sep)||!fs.existsSync(filename)||!fs.statSync(filename).isFile()){response.writeHead(404);response.end();return;}
 const types={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.svg':'image/svg+xml'};
 response.writeHead(200,{'Content-Type':types[path.extname(filename)]||'application/octet-stream'});fs.createReadStream(filename).pipe(response);
});

(async()=>{
 let browser;
 try{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base=`http://127.0.0.1:${server.address().port}/`;
  browser=await chromium.launch({headless:true,executablePath:process.env.FORMAL_BROWSER_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  report.browser=browser.version();
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(5000);
  page.on('pageerror',error=>report.pageErrors.push(error.message));
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin!==new URL(base).origin){report.externalRequests.push(request.url());await route.abort();return;}
   if(!url.pathname.startsWith('/api/')){await route.continue();return;}
   const body=request.postDataJSON();report.requests.push({path:url.pathname,method:request.method(),query:url.search,body});
   let data;
   if(url.pathname==='/api/catalog-bootstrap')data=bootstrap;
   else if(url.pathname==='/api/catalog-programs')data={revision:bootstrap.revision,total:1,programs:[program]};
   else if(url.pathname==='/api/catalog-program')data={revision:bootstrap.revision,program,score_lines:[],admitted_statistics:[],transfer_rules:[],retest_rules:[]};
   else if(url.pathname==='/api/radar')data={program_ids:[],unread_count:0};
   else if(url.pathname==='/api/match')data={revision:bootstrap.revision,matches:[],needs_confirmation:[{program,status:'unknown',reasons:[]}],excluded:[],summary:{}};
   else if(url.pathname==='/api/search')data={query:`甲大学 2027 ${body.kind}`,queries_tried:[`甲大学 2027 ${body.kind}`],request_count:1,results:[],failure_stage:'index_no_hit',outcome:'no_official_results',notice:'隔离浏览器夹具，未执行真实检索。'};
   else if(url.pathname==='/api/settings')data={model:{configured:false},search:{configured:false}};
   else if(url.pathname==='/api/research'&&request.method()==='GET')data={tasks:[],total:0};
   else{report.unexpectedApi.push({path:url.pathname,method:request.method()});await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({ok:false,error:{message:'Unexpected fixture API'}})});return;}
   await route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,data,error:null})});
  });
  const check=async(name,run)=>{
   try{await run();report.checks.push({name,passed:true});}
   catch(error){report.checks.push({name,passed:false,error:error.message});}
  };
  const discover=async query=>{
   await page.goto(base+'#/discover');
   await page.evaluate(query=>localStorage.setItem('yanshu-profile-v1',JSON.stringify({preferences:{query,unit_id:'a'}})),query);
   await page.reload();await page.locator('.program-row').waitFor();
   await page.waitForFunction(()=>!document.querySelector('[data-action="analyze"]')?.disabled);
  };
  for(const query of ['非全日制','专硕','专业学位','甲大学 专硕']){
   await check(`browse to analysis preserves ${query}`,async()=>{
    await discover(query);
    const before=await page.locator('.program-row').getAttribute('data-id');assert.equal(before,'p0');
    assert.ok(report.requests.some(row=>row.path==='/api/catalog-programs'&&new URLSearchParams(row.query).get('q')===query));
    await page.locator('[data-action="analyze"]').click();await page.locator('.analysis-summary').waitFor();
    assert.equal(await page.locator('.program-row').getAttribute('data-id'),'p0');
    assert.equal(await page.locator('#search-input').inputValue(),query);
    const match=report.requests.filter(row=>row.path==='/api/match').at(-1);assert.deepEqual(match.body.unit_ids,['a']);
   });
  }
  for(const [kind,label] of [['fees','学费与学制'],['retest','复试办法'],['transfer','调剂办法']]){
   await check(`official ${kind} lookup keeps intent in Agent draft`,async()=>{
    await discover('计算机');await page.locator('#source-kind').selectOption(kind);
    await page.locator('[data-action="search-source"]').click();await page.locator('#info-dialog[open]').waitFor();
    const request=report.requests.filter(row=>row.path==='/api/search').at(-1);assert.equal(request.body.kind,kind);assert.equal(request.body.year,2027);
    await page.locator('[data-action="research-unit"]').click();await page.locator('#ya-query').waitFor();
    assert.equal(await page.locator('#ya-query').inputValue(),`甲大学 2027年${label}`);
    assert.equal(report.requests.some(row=>row.path==='/api/research'&&row.method==='POST'),false);
   });
  }
  assert.deepEqual(report.pageErrors,[]);assert.deepEqual(report.externalRequests,[]);assert.deepEqual(report.unexpectedApi,[]);
  report.passed=report.checks.every(row=>row.passed);report.paidCalls=0;
 }catch(error){report.passed=false;report.error=error.stack;}
 finally{
  await browser?.close();await new Promise(resolve=>server.close(resolve));
  fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({passed:report.passed,checks:report.checks,pageErrors:report.pageErrors,unexpectedApi:report.unexpectedApi,output},null,2));
  process.exitCode=report.passed?0:1;
 }
})();
