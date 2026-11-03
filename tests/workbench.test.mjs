import test from 'node:test';
import assert from 'node:assert/strict';
import {buildRouteRequest,buildAdjustmentRequest,createRequestGate,scopeKey,displayValue} from '../static/workbench-model.mjs';
import {renderRoutes,renderRadar,renderProofResult,renderProof,renderExamUnresolved} from '../static/workbench-views.mjs';
import {mountWorkbench} from '../static/workbench.mjs';

const context={profile:{subjects:[{code:'201',name:'英语一',scope:'national'}],education:'bachelor',work_years:0,provinces:['天津'],max_annual_tuition:0,max_subject_changes:0},units:[{id:'real-a',name:'真实甲校'},{id:'real-b',name:'真实乙校'}],programs:[],rawPrograms:[],savedIds:[],referenceYear:2027,searchConfigured:false};

test('unresolved exam notices explain missing candidates with safe original links in every result view',()=>{
 const pending=[{program_id:'raw-1',unit_name:'样本大学',name:'环境工程',code:'083000',
  reason:'301数学（二）代码名称冲突 <img src=x onerror=alert(1)>',url:'https://grad.example.edu.cn/catalog'},
 {program_id:'raw-2',name:'耦合套组方向',reason:'原文科目组合不能拆开比较',url:'javascript:alert(1)'}];
 const data={exam_unresolved_programs:pending};
 const html=renderExamUnresolved(data);
 assert.match(html,/2 个方向.*未纳入本次计算/);assert.match(html,/查看未参与计算的方向/);
 assert.match(html,/301数学（二）/);assert.match(html,/href="https:\/\/grad.example.edu.cn\/catalog"/);
 assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|href="javascript:|<button/);
 assert.equal(renderExamUnresolved({exam_unresolved_programs:[]}), '');
 const duplicates=renderExamUnresolved({exam_unresolved_programs:[pending[0],pending[0]]});assert.match(duplicates,/1 个方向/);
 for(const status of ['no_improvement','no_solution','ok']){
  const view=renderRoutes(context,{draft:{adjustmentOpen:true},routeResult:{...data,routes:[]},
   adjustmentResult:{...data,status,suggestions:[]}});
  assert.equal((view.match(/查看未参与计算的方向/g)||[]).length,2);
 }
 assert.doesNotMatch(renderExamUnresolved({exam_unresolved_programs:[{...pending[0],url:'https://user:secret@example.com/'}]}),/href=/);
});

test('workbench annual scope preserves pending index records but excludes units not independently listed this year',()=>{
 const annual={...context,units:[{id:'real-a',name:'真实甲校',annual_status:'confirmed',annual_year:2027,program_count:3},{id:'pending',name:'待入库学校',annual_status:'confirmed',annual_year:2027,program_count:0},{id:'not-listed',name:'保留索引学校',annual_status:'not_listed',annual_year:2027,program_count:0}]};
 const html=renderRoutes(annual,{draft:{scopeUnit:''}});
 assert.match(html,/<option value="not-listed"[^>]*disabled[^>]*>保留索引学校（2027未列独立招生单位）/);
 assert.match(html,/待入库学校（目录待入库）/);assert.doesNotMatch(html,/全部已收录学校（3 所）/);
 assert.deepEqual(buildRouteRequest(annual,'').unit_ids,['real-a','pending']);assert.throws(()=>buildRouteRequest(annual,'not-listed'),/范围/);
});
test('route requests include only real scope IDs and retain hard facts without mutation',()=>{
 const before=JSON.stringify(context);const payload=buildRouteRequest(context,'real-a');
 assert.deepEqual(payload.unit_ids,['real-a']);assert.equal(payload.profile.reference_year,2027);
 payload.profile.subjects[0].code='changed';assert.equal(JSON.stringify(context),before);
 assert.deepEqual(buildRouteRequest(context,'').unit_ids,['real-a','real-b']);
 assert.throws(()=>buildRouteRequest(context,'invented'),/范围/);
 assert.notEqual(scopeKey(context,'real-a'),scopeKey(context,'real-b'));
});
test('adjustments require explicit consent and cannot relax education or work facts',()=>{
 assert.throws(()=>buildAdjustmentRequest(context,{}),/勾选/);
 const result=buildAdjustmentRequest(context,{allowSubjects:true,allowRegions:true,regionInput:'北京、天津',allowBudget:true,budgetInput:'20000'});
 assert.deepEqual(result.allowed_changes,{max_subject_changes:[1,2],provinces:[['天津','北京']],max_annual_tuition:[20000]});
 assert.equal(result.profile.work_years,0);assert.equal(result.profile.education,'bachelor');
 assert.equal(result.profile.max_annual_tuition,0);assert.equal(result.goal_min_candidates,1);
 assert.throws(()=>buildAdjustmentRequest(context,{allowBudget:true,budgetInput:''}),/预算/);
 assert.throws(()=>buildAdjustmentRequest(context,{allowRegions:true,regionInput:'天津'}),/新增/);
 assert.throws(()=>buildAdjustmentRequest({...context,profile:{...context.profile,max_annual_tuition:null}},{allowBudget:true,budgetInput:'20000'}),/原预算/);
 assert.throws(()=>buildAdjustmentRequest({...context,profile:{...context.profile,max_subject_changes:null}},{allowSubjects:true}),/原换科上限/);
});
test('request gates abort replaced work and reject stale completions and cleanup',()=>{
 const gate=createRequestGate();const first=gate.begin('route');const next=gate.begin('route');
 assert.equal(first.signal.aborted,true);assert.equal(first.current(),false);assert.equal(next.current(),true);
 gate.abort('route');assert.equal(next.signal.aborted,true);assert.equal(next.current(),false);
 const proof=gate.begin('proof');gate.close();assert.equal(proof.current(),false);assert.equal(proof.signal.aborted,true);
});
test('route render separates distinct coverage and unknown qualification and has real actions',()=>{
 const route={id:'subjects-0',max_subject_changes:0,coverage:{program_count:10,department_program_count:7,unit_count:2},matched_count:0,needs_confirmation_count:10,unknown_subject_count:4,candidates:[],added_ids:[],removed_ids:[]};
 const html=renderRoutes(context,{draft:{scopeUnit:''},routeResult:{routes:[route],note:'官方范围'},selectedRoute:'subjects-0'});
 assert.match(html,/10/);assert.match(html,/院系专业/);assert.match(html,/0 个已知条件匹配/);assert.match(html,/10 个资格待核验/);assert.match(html,/4 个科目身份待完整/);
 assert.match(html,/data-wb-action="run-routes"/);assert.doesNotMatch(html,/录取概率为|可以报考/);
});

test('route coverage visualization uses the same full-scope counts as its candidate cards',()=>{
 const routes=[0,1,2].map((limit,index)=>({id:`subjects-${limit}`,max_subject_changes:limit,label:`最多调整${limit}科`,coverage:{program_count:[7,12,173][index],department_program_count:[4,7,53][index],unit_count:1},matched_count:0,needs_confirmation_count:[7,12,173][index],unknown_subject_count:2,candidates:[],added_ids:[],removed_ids:[]}));
 const html=renderRoutes(context,{draft:{scopeUnit:'real-a'},routeResult:{routes,note:'仅真实甲校2027参考目录'},selectedRoute:'subjects-0'});
 assert.match(html,/data-chart="route-coverage"/);
 assert.match(html,/173/);assert.match(html,/53/);assert.match(html,/资格待核验|待核实/);
 assert.doesNotMatch(html,/全国.*173|录取概率为|成功率/);
 assert.doesNotMatch(renderRoutes(context,{draft:{}}),/data-chart="route-coverage"/);
});
test('proof output escapes official text, rejects script links and retains zero values',()=>{
 const html=renderProofResult({status:'needs_review',candidates:[{field:'tuition',value:0,billing_period:'year',year:2027,page:1,quote:'<img src=x onerror=alert(1)>',source_url:'javascript:alert(1)',source_sha256:'abc',scope:{department:'<b>院系</b>'}}]});
 assert.match(html,/0 元\/年/);assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|href="javascript:/);assert.match(html,/待人工复核/);assert.match(html,/abc/);
 const empty=renderProof(context,{draft:{proofProgram:''},proofTasks:{tasks:[]}});
 assert.match(empty,/未配置/);assert.doesNotMatch(empty,/data-wb-action="run-proof"(?![^>]*disabled)/);
 assert.equal(displayValue({amount:0,period:'year'},'tuition'),'0 元/年');
 assert.equal(displayValue({amount:null,period:'year'},'tuition'),'未知');
 assert.equal(displayValue([{code:'201',name:'英语一',exam_unit:2}],'subjects'),'考试单元 2：201 英语一');
});
test('radar distinguishes initial baseline, failed reads and confirmed observations',()=>{
 const initial=renderRadar(context,{draft:{watchIds:[]},radar:{watch_count:0,baseline_count:0,last_checked_at:null,events:[],check_state:'idle'}});
 assert.match(initial,/首次关注只建立/);assert.match(initial,/站内提醒/);assert.doesNotMatch(initial,/暂无变化/);
 const failure=renderRadar(context,{draft:{watchIds:[]},radar:{watch_count:1,events:[{type:'check_failed',message:'读取失败',changes:{},program_ids:[]}]},errors:{radar:'无法读取'}});
 assert.match(failure,/无法读取/);assert.match(failure,/不能据此判断/);assert.match(failure,/读取失败/);
});

const settle=()=>new Promise(resolve=>setImmediate(resolve));
function harness(context,api,document={activeElement:null,querySelector:()=>({})}) {
 const listeners=new Map();
 const container={ownerDocument:document,innerHTML:'',contains:node=>Boolean(node),addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name),querySelectorAll:()=>[]};
 const cleanup=mountWorkbench(container,{api,getContext:()=>context});
 const click=(action,extra={})=>{const button={dataset:{wbAction:action,...extra},closest(){return this;}};listeners.get('click')?.({target:button,preventDefault(){},stopPropagation(){}});};
 const change=(name,value,checked)=>listeners.get('change')?.({target:{name,value,checked}});
 return {container,document,click,change,cleanup,listeners};
}
test('mounted routes discard stale scope responses and restore only matching profile results',async()=>{
 const pending=[];const api=(path,options)=>new Promise(resolve=>pending.push({path,options,resolve}));
 const current={...context,tool:'routes'},page=harness(current,api);
 page.click('run-routes');assert.deepEqual(pending[0].options.body.unit_ids,['real-a','real-b']);
 page.change('scopeUnit','real-b');assert.equal(pending[0].options.signal.aborted,true);
 pending[0].resolve({routes:[],note:'旧范围结果不得出现'});await settle();assert.doesNotMatch(page.container.innerHTML,/旧范围结果不得出现/);
 page.click('run-routes');assert.deepEqual(pending[1].options.body.unit_ids,['real-b']);
 pending[1].resolve({routes:[],note:'当前范围结果已保存'});await settle();assert.match(page.container.innerHTML,/当前范围结果已保存/);
 page.cleanup();assert.equal(page.listeners.size,0);
 const restored=harness(current,api,page.document);assert.match(restored.container.innerHTML,/当前范围结果已保存/);restored.cleanup();
 const changed=harness({...current,profile:{...current.profile,education:'master'}},api,page.document);
 assert.doesNotMatch(changed.container.innerHTML,/当前范围结果已保存/);changed.cleanup();
});
test('mounted radar never replaces unknown remote watches and serializes check with saving',async()=>{
 const pending=[];const api=(path,options)=>new Promise(resolve=>pending.push({path,options,resolve}));
 const current={...context,tool:'radar',programs:[{id:'p',name:'真实甲校',major:'真实专业'}],savedIds:['p']},page=harness(current,api);
 page.click('save-watches');assert.equal(pending.length,1);
 pending[0].resolve({program_ids:['p'],watch_count:1,baseline_count:1,events:[]});await settle();
 page.click('save-watches');assert.equal(pending[1].path,'/api/radar/watch');
 page.click('check-radar');assert.equal(pending.length,2);
 pending[1].resolve({program_ids:['p'],watch_count:1,baseline_count:1,events:[]});await settle();
 page.click('check-radar');assert.equal(pending[2].path,'/api/radar/check');
 page.cleanup();assert.equal(pending[2].options.signal.aborted,true);
 pending[2].resolve({events:[{message:'过期检查结果'}]});await settle();assert.doesNotMatch(page.container.innerHTML,/过期检查结果/);
});
test('editing adjustment consent invalidates an in-flight or previously displayed suggestion',async()=>{
 const pending=[];const api=(path,options)=>new Promise(resolve=>pending.push({path,options,resolve}));
 const page=harness({...context,tool:'routes'},api);
 page.change('allowSubjects','on',true);page.click('run-adjustments');assert.equal(pending.length,1);
 page.change('allowSubjects','on',false);assert.equal(pending[0].options.signal.aborted,true);
 pending[0].resolve({status:'no_improvement'});await settle();assert.doesNotMatch(page.container.innerHTML,/允许的调整未增加候选/);
 page.change('allowSubjects','on',true);page.click('run-adjustments');pending[1].resolve({status:'no_improvement'});await settle();
 assert.match(page.container.innerHTML,/允许的调整未增加候选/);page.change('allowSubjects','on',false);assert.doesNotMatch(page.container.innerHTML,/允许的调整未增加候选/);
 page.cleanup();
});
test('candidate and adjustment views keep qualification, fees and sources explicit',()=>{
 const program={id:'p',unit_name:'<img src=x>',name:'真实专业',department:'真实院系',year:2027,tuition:{amount:0,period:'year'}};
 const entry={program,status:'unknown',subject_changes:{count:1},reasons:[{field:'subjects',message:'仍需核对科目身份'}]};
 const route={id:'subjects-1',label:'调整一个考试单元',max_subject_changes:1,coverage:{program_count:1,department_program_count:1,unit_count:1},matched_count:0,needs_confirmation_count:1,unknown_subject_count:0,candidates:[entry],added_ids:['p'],retained_ids:[],removed_ids:[]};
 const html=renderRoutes({...context,programs:[{id:'p',name:'真实甲校',major:'真实专业'}],savedIds:['p']},{draft:{scopeUnit:'',adjustmentOpen:true},routeResult:{routes:[route],questions:[{label:'请补充学历',affected_count:1}]},selectedRoute:route.id,adjustmentResult:{status:'ok',suggestions:[{...route,cost:1,feasible:false,changes:{max_subject_changes:1},explanation:'费用未知仍需核验'}]}});
 assert.match(html,/0 元\/年/);assert.match(html,/资格仍待核验/);assert.match(html,/已收藏/);assert.match(html,/data-wb-action="open-program"/);assert.match(html,/请补充学历/);assert.match(html,/费用未知仍需核验/);assert.doesNotMatch(html,/<img/);
 assert.match(renderRoutes(context,{draft:{},adjustmentResult:{status:'no_solution'}}),/未达到候选目标/);
});
test('proof changes abort prior program research and keep results assigned to the requested kind',async()=>{
 const pending=[];const api=(path,options)=>new Promise(resolve=>pending.push({path,options,resolve}));
 const page=harness({...context,tool:'proof',searchConfigured:true,programs:[{id:'p',name:'甲校',major:'甲专业'},{id:'q',name:'乙校',major:'乙专业'}],savedIds:['p']},api);
 assert.equal(pending[0].path,'/api/source-tasks?program_id=p');
 pending[0].resolve({tasks:[{kind:'charter'},{kind:'fees'}]});await settle();page.click('run-proof');
 assert.deepEqual(pending[1].options.body,{program_id:'p',kind:'charter'});
 page.change('proofProgram','q');assert.equal(pending[1].options.signal.aborted,true);
 pending[1].resolve({candidates:[],notice:'旧方向不得展示'});pending[2].resolve({tasks:[{kind:'charter'},{kind:'fees'}]});await settle();
 assert.doesNotMatch(page.container.innerHTML,/旧方向不得展示/);page.click('run-proof');page.change('proofKind','fees');
 pending[3].resolve({candidates:[],notice:'章程结果只归章程'});await settle();assert.doesNotMatch(page.container.innerHTML,/章程结果只归章程/);
 page.change('proofKind','charter');assert.match(page.container.innerHTML,/章程结果只归章程/);
 page.click('run-proof');page.click('cancel-proof');assert.equal(pending[4].options.signal.aborted,true);
 pending[4].resolve({candidates:[],notice:'取消结果不得展示'});await settle();assert.doesNotMatch(page.container.innerHTML,/取消结果不得展示/);page.cleanup();
});
test('radar refresh preserves unsaved choices and completion stays readable',async()=>{
 const pending=[];const api=(path,options)=>new Promise(resolve=>pending.push({path,options,resolve}));
 const page=harness({...context,tool:'radar',programs:[{id:'p',name:'甲校',major:'甲专业'},{id:'q',name:'乙校',major:'乙专业'}],savedIds:['p','q']},api);
 pending[0].resolve({program_ids:['p'],watch_count:1,baseline_count:1,running:true,check_state:'checking',events:[]});await settle();
 assert.match(page.container.innerHTML,/data-wb-action="retry-radar"/);page.change('wb-watch','q',true);page.click('retry-radar');
 pending[1].resolve({program_ids:['p'],watch_count:1,baseline_count:1,running:false,check_state:'complete',events:[]});await settle();
 assert.match(page.container.innerHTML,/本次.*完成/);page.click('save-watches');assert.deepEqual(pending[2].options.body.program_ids,['p','q']);page.cleanup();
});
