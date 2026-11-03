/* Route fixtures validate settings -> existing catalog integration, without real keys. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const base=(process.env.AGENT_URL||'http://127.0.0.1:4180').replace(/\/+$/,'');
const out=path.resolve(__dirname,'../data/runtime/agent-upgrade/settings-integration.json');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage();page.setDefaultTimeout(7000);
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const trace=[];
 let result={passed:false,scope:'browser route fixtures; no real provider calls or key saves'};
 try{
  const light=(await (await page.request.get(base+'/api/catalog-bootstrap')).json()).data;
  const providers=(await (await page.request.get(base+'/api/settings/providers')).json()).data;
  let configured=false;
  const settings=()=>({ai:{status:'not_configured',has_key:false},yuanbao:{provider:'yuanbao',status:configured?'connected':'not_configured',has_key:configured},storage:{mode:'fixture',persistent:false}});
  const good=(route,data)=>route.fulfill({json:{ok:true,data,error:null}});
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.origin!==new URL(base).origin)return route.abort();
   if(url.pathname==='/api/catalog-bootstrap'){trace.push({request:'bootstrap',configured});return good(route,{...light,service:{...light.service,search_configured:configured}});}
   if(url.pathname==='/api/settings/providers')return good(route,providers);
   if(url.pathname==='/api/settings')return good(route,settings());
   if(url.pathname==='/api/settings/test')return good(route,{status:'connected',validation_token:'fixture-ticket'});
   if(url.pathname==='/api/settings/save'){configured=true;trace.push({request:'save'});return good(route,settings());}
   if(url.pathname==='/api/settings/delete'){configured=false;return good(route,settings());}
   if(url.pathname.startsWith('/api/settings/'))return route.abort();
   return route.continue();
  });
  await page.goto(base+'/#/settings');
  await page.locator('#ys-provider').waitFor();
  await page.locator('[data-settings-tab="yuanbao"]').click();
  await page.locator('[name="auth_mode"][value="api_key"]').check();
  await page.locator('#ys-api-key').fill('FIXTURE-ONLY-NOT-A-REAL-KEY');
  await page.locator('#ys-settings-form [type="submit"]').click();
  await page.locator('.ys-feedback.is-success').waitFor();
  await page.locator('.main-nav [data-route="/discover"]').click();
  await page.locator('#unit-filter').selectOption('367949');
  await page.locator('.program-row').first().waitFor();
  await page.waitForFunction(()=>!document.querySelector('[data-action="search-source"]')?.disabled,null,{timeout:2500});
  await page.getByRole('button',{name:'AI与元宝设置',exact:true}).click();
  await page.locator('#ys-provider').waitFor();
  await page.locator('[data-settings-tab="yuanbao"]').click();
  await page.locator('[data-settings-action="delete"]').click();
  await page.getByText('连接已移除，你可以重新选择服务并配置。',{exact:true}).waitFor();
  await page.locator('.main-nav [data-route="/discover"]').click();
  await page.locator('#unit-filter').selectOption('367949');
  await page.waitForFunction(()=>document.querySelector('[data-action="search-source"]')?.disabled===true);
  assert.deepEqual(errors,[]);
  result={...result,passed:true,checks:['saved connection enables existing catalog search without reload','removed connection disables existing catalog search without reload'],errors};
 }catch(error){result={...result,error:error.message,errors,trace,body:(await page.locator('body').innerText()).slice(-4500),unit:await page.locator('#unit-filter').inputValue().catch(()=>null)};throw error;}
 finally{fs.writeFileSync(out,JSON.stringify(result,null,2));await browser.close();}
 console.log(JSON.stringify(result));
})().catch(error=>{console.error(error);process.exitCode=1});
