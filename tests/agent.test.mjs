import test from 'node:test';
import assert from 'node:assert/strict';
import {mergeTaskEvents,shouldPoll,renderAgent,taskStatusLabel,parseExclusionList,formatExclusionList} from '../static/agent.mjs';

test('incremental events merge by identity and stale tasks do not contaminate a new research',()=>{
 const old={id:'a',events:[{id:1,title:'first'},{id:2,title:'second'}]};
 const next=mergeTaskEvents(old,{id:'a',events:[{id:2,title:'updated'},{id:3,title:'third'}]});
 assert.deepEqual(next.events.map(e=>e.id),[1,2,3]);assert.equal(next.events[1].title,'updated');
 assert.deepEqual(mergeTaskEvents(old,{id:'b',events:[{id:1,title:'new'}]}).events,[{id:1,title:'new'}]);
});

test('polling stops at terminal or configuration states and labels remain honest',()=>{
 for(const status of ['queued','running'])assert.equal(shouldPoll({status}),true);
 for(const status of ['completed','partial','failed','cancelled','waiting_configuration','awaiting_input','interrupted'])assert.equal(shouldPoll({status}),false);
 assert.equal(taskStatusLabel('waiting_configuration'),'等待连接服务');
 assert.equal(shouldPoll({status:'completed',has_more_events:true}),true);
 assert.equal(shouldPoll({status:'completed',has_more_events:false}),false);
});

test('service loading or errors never imply that credentials are missing',()=>{
 const loading=renderAgent({settingsStatus:'loading'}),failed=renderAgent({settingsStatus:'error'});
 assert.match(loading,/正在读取服务状态/);assert.ok(!loading.includes('AI 模型待配置'));
 assert.match(failed,/连接情况尚不能确认/);assert.match(failed,/重新读取/);assert.ok(!failed.includes('AI 模型待配置'));
});

test('route and adjustment tool results remain visible without a matching tool result',()=>{
 const html=renderAgent({task:{id:'a',status:'completed',query:'q',events:[],report:{algorithm:{routes:{routes:[{label:'最多调整 1 个考试单元',coverage:{program_count:7},needs_confirmation_count:3,added_ids:['a'],retained_ids:[]}]},adjustments:{suggestions:[{changes:{max_annual_tuition:15000},coverage:{program_count:9},needs_confirmation_count:2,added_ids:['b']}],note:'测试夹具：仅在已授权条件中比较'}}}},tab:'methods'});
 assert.match(html,/科目调整路线/);assert.match(html,/最小条件调整/);assert.match(html,/学费上限 15000 元/);assert.match(html,/>7<\/strong>/);assert.match(html,/>9<\/strong>/);assert.ok(!html.includes('还没有形成可计算结果'));
});

test('a missing configuration preserves the request without manufacturing an AI answer',()=>{
 const html=renderAgent({task:{id:'a',query:'我想考计算机',status:'waiting_configuration',events:[],report:{}},settings:{ai:{has_key:false},yuanbao:{has_key:false}},tab:'overview'});
 assert.match(html,/我想考计算机/);assert.match(html,/配置/);assert.match(html,/等待连接服务/);
 assert.ok(!html.includes('已完成检索'));assert.ok(!html.includes('推荐成功'));
});

test('search leads and official originals are distinguished and unsafe URLs never become links',()=>{
 const html=renderAgent({task:{id:'a',query:'test',status:'completed',events:[],report:{evidence:[{id:'a',title:'<script>bad</script>',url:'javascript:alert(1)',status:'search_lead',excerpt:'<img src=x>'},{id:'b',title:'官方目录',url:'https://example.edu.cn/catalog.pdf',status:'official_original_read',excerpt:'官方原文'}]}},tab:'sources'});
 assert.match(html,/检索线索/);assert.match(html,/原文已读取/);assert.match(html,/&lt;script&gt;/);
 assert.ok(!html.includes('href="javascript:'));assert.ok(!html.includes('<img src=x>'));
 assert.match(html,/href="https:\/\/example.edu.cn\/catalog.pdf"/);
});

test('published snapshots are labeled as collected material without implying an original was just read',()=>{
 const html=renderAgent({task:{id:'a',status:'completed',report:{evidence:[{status:'published_snapshot',title:'已收录目录',excerpt:'已收录内容',url:'https://example.edu.cn/catalog'}]}},tab:'sources'});
 assert.match(html,/已收录资料/);assert.match(html,/资料摘录/);assert.ok(!html.includes('ya-source-status is-original'));
});

test('algorithms show uncertainty instead of fabricated probability or zero changes',()=>{
 const html=renderAgent({task:{id:'a',status:'completed',query:'q',events:[],report:{candidates:[{id:'p',unit_name:'大学',name:'计算机'}],algorithm:{matching:{summary:{input_count:1,unknown_count:1},needs_confirmation:[{program:{id:'p'},reasons:['条件待核对']}]}}}},tab:'methods'});
 assert.match(html,/条件待核对/);assert.match(html,/资格三态/);assert.ok(!html.includes('录取概率 0'));
 assert.ok(!html.includes('需调整 0'));
});

test('interpreted conditions need an explicit confirmation and preserve quoted wording',()=>{
 const html=renderAgent({task:{id:'a',query:'我准备408，希望在天津读研',status:'awaiting_input',report:{profile_proposal:{updates:{subjects:[{code:'408',name:'计算机学科专业基础'}],provinces:['天津']},evidence:[{field:'subjects',quote:'我准备408'},{field:'provinces',quote:'希望在天津读研'}],requires_confirmation:true}}},tab:'overview'});
 assert.match(html,/识别到的备考条件/);assert.match(html,/确认后，算法才会/);assert.match(html,/确认条件并继续/);assert.match(html,/我准备408/);assert.match(html,/天津/);
 assert.ok(!html.includes('"updates"'));assert.ok(!html.includes('已应用这些条件'));
});

test('a running research keeps an editable composer before grouped candidates',()=>{
 const candidates=Array.from({length:12},(_,i)=>({id:'p'+i,unit_id:'u',unit_name:'同一所大学',name:'计算机',direction:'方向'+i,subjects:[{code:'408'}]}));
 const html=renderAgent({task:{id:'a',status:'running',turn_id:2,query:'计算机',report:{candidates},events:[]}});
 assert.ok(html.indexOf('id="ya-followup"')<html.indexOf('class="ya-report-column"'));
 assert.ok(!/<textarea[^>]*id="ya-followup"[^>]*disabled/.test(html));
 assert.match(html,/停止并更新研究/);assert.equal((html.match(/class="ya-school-card ya-candidate"/g)||[]).length,1);
 assert.match(html,/还有 11 个方向/);assert.match(html,/1 所学校/);
});

test('the initial result only expands three school groups and offers all remaining schools',()=>{
 const candidates=Array.from({length:5},(_,i)=>({id:'p'+i,unit_id:'u'+i,unit_name:'学校'+i,name:'计算机'}));
 const task={id:'a',status:'completed',report:{candidates}};
 const html=renderAgent({task});assert.equal((html.match(/class="ya-school-card ya-candidate"/g)||[]).length,3);
 assert.match(html,/查看其余 2 所学校/);assert.ok(!html.includes('<h3>学校4</h3>'));
 assert.equal((renderAgent({task,showAllSchools:true}).match(/class="ya-school-card ya-candidate"/g)||[]).length,5);
});

test('the current action is derived from the current turn and the complete log starts closed',()=>{
 const html=renderAgent({task:{id:'a',turn_id:2,status:'running',events:[{id:1,turn_id:1,type:'tool_started',message:'旧轮次不应作为当前动作'},{id:2,turn_id:2,type:'tool_started',message:'正在查这一轮的官方资料'}],report:{}}});
 const action=html.match(/class="ya-current-action"[\s\S]*?<\/section>/)?.[0]||'';
 assert.match(action,/正在查这一轮的官方资料/);assert.ok(!action.includes('旧轮次'));
 assert.match(html,/<details class="ya-activity-disclosure"[^>]*>/);assert.ok(!html.includes('class="ya-activity-disclosure" open'));
});

test('the overview exposes real algorithm results rather than only a separate method explanation',()=>{
 const html=renderAgent({task:{id:'a',status:'partial',report:{algorithm:{matching:{summary:{input_count:12,matched_count:0,unknown_count:12,excluded_count:0},needs_confirmation:[]}}}}});
 assert.match(html,/class="ya-decision-summary"/);assert.match(html,/已完成条件核算/);
 assert.match(html,/12<\/strong>/);assert.match(html,/仍需确认/);
 const pending=renderAgent({task:{id:'a',status:'partial',report:{}}});
 assert.match(pending,/算法尚未执行/);assert.ok(!pending.includes('已完成条件核算'));
});

test('skills show loaded and related tool use separately and sources expose knowledge persistence',()=>{
 const task={id:'a',status:'completed',report:{skills:[{id:'verify-evidence',label:'核对依据',loaded:true,status:'loaded',executed_tools:[]},{id:'compare-plans',label:'方案比较',loaded:true,status:'used',executed_tools:['compare_candidates']}],knowledge:{saved_count:1,cache_hits:0},evidence:[{title:'官方原文',url:'https://example.edu.cn/catalog',status:'official_original_read',knowledge_id:'k_1',knowledge_saved:true,knowledge_quality:{state:'accepted',reusable:true,material_type:'catalog',reasons:[]}}]}};
 const methods=renderAgent({task,tab:'methods'});assert.match(methods,/研究方法/);assert.match(methods,/规程已加载/);assert.match(methods,/相关工具已执行/);
 const sources=renderAgent({task,tab:'sources'});assert.match(sources,/已入知识库/);assert.match(sources,/data-agent-knowledge="k_1"/);
 assert.ok(!methods.includes('Skill 已验证成功'));
});

test('a method not loaded in this turn is never labelled as a loaded skill',()=>{
 const html=renderAgent({task:{id:'a',status:'completed',report:{skills:[{id:'assess-transfer-options',label:'调剂方法',loaded:false,status:'not_loaded'}]}},tab:'methods'});
 assert.match(html,/本轮未加载/);assert.ok(!html.includes('>规程已加载<'));
});

test('year-only proposals remain visible and the user can explicitly confirm study years',()=>{
 const html=renderAgent({task:{id:'a',status:'awaiting_input',report:{profile_proposal:{requires_confirmation:true,updates:{target_year:2027,reference_year:2026},evidence:[{field:'target_year',quote:'我说的是2027年入学'}]},questions:[{field:'target_year',label:'确认目标入学年份',options:[{label:'按2028入学继续',value:2028}]}]}}});
 assert.match(html,/目标入学年份/);assert.match(html,/历史参考目录年份/);assert.match(html,/2027/);assert.match(html,/确认条件并继续/);assert.match(html,/data-agent-answer="0"/);
});

test('sources are paged without silently changing the total and missing subjects never mean zero changes',()=>{
 const evidence=Array.from({length:40},(_,i)=>({id:'s'+i,title:'官方资料'+i,status:'search_lead',url:'https://example.edu.cn/'+i}));
 const html=renderAgent({task:{id:'a',status:'completed',report:{evidence}},tab:'sources'});
 assert.equal((html.match(/class="ya-source-card"/g)||[]).length,8);assert.match(html,/40 份/);assert.match(html,/data-agent-source-page="2"/);
 const program={id:'p',unit_name:'大学',name:'计算机'};
 const unknown=renderAgent({task:{id:'a',status:'completed',profile:{subjects:[]},report:{candidates:[program],algorithm:{matching:{matches:[{program,subject_changes:{count:0,uncertain:false}}]}}}}});
 assert.ok(!unknown.includes('需调整 0 个'));assert.match(unknown,/备考科目待补充/);
});

test('source admission separates accepted knowledge, pending leads and normal exclusions',()=>{
 const evidence=[
  {id:'accepted',title:'已通过资料',url:'https://example.edu.cn/accepted',knowledge_id:'k_accepted',knowledge_saved:true,knowledge_quality:{state:'accepted',reusable:true,material_type:'catalog',reasons:[]}},
  {id:'pending',title:'待核资料',url:'https://example.edu.cn/pending',knowledge_id:'k_pending',knowledge_saved:true,knowledge_quality:{state:'pending',reusable:false,material_type:'unknown',reasons:[{code:'scope_unknown',message:'需要核对资料范围'}]}},
  {id:'excluded',title:'排除资料',url:'https://example.edu.cn/excluded',knowledge_filtered:true,knowledge_saved:false,excerpt:'EXCLUDED_EXCERPT_MUST_NOT_RENDER',knowledge_quality:{state:'excluded',reusable:false,material_type:'unrelated',reasons:[{code:'wrong_material',message:'不是招生资料 <script>unsafe</script>'}]}}
 ];
 const html=renderAgent({task:{id:'a',status:'completed',report:{evidence,knowledge:{saved_count:2,accepted_count:1,pending_count:1,filtered_count:1}}},tab:'sources'});
 assert.match(html,/已通过 1 份/);assert.match(html,/待核 1 份/);assert.match(html,/已过滤 1 份/);
 assert.match(html,/data-agent-knowledge="k_accepted"[^>]*>[\s\S]*?已入知识库/);
 assert.match(html,/data-agent-knowledge="k_pending"[^>]*>[\s\S]*?待核线索/);
 assert.match(html,/未入库：不是招生资料 &lt;script&gt;unsafe&lt;\/script&gt;/);
 assert.ok(!html.includes('EXCLUDED_EXCERPT_MUST_NOT_RENDER'));
 assert.ok(!html.includes('知识库保存未完成'));assert.ok(!html.includes('2 份资料已存入知识库'));
});

test('legacy saved sources without admission quality never claim acceptance',()=>{
 const html=renderAgent({task:{id:'a',status:'completed',report:{knowledge:{saved_count:1},evidence:[{id:'legacy',title:'旧记录',knowledge_id:'k_old',knowledge_saved:true,excerpt:'旧资料摘录',url:'https://example.edu.cn/legacy'}]}},tab:'sources'});
 assert.match(html,/准入状态待复核/);assert.match(html,/准入统计尚待复核/);
 assert.ok(!html.includes('>已入知识库<'));assert.ok(!html.includes('1 份资料已存入知识库'));
});

test('knowledge links retain real accepted or pending routing context and omit unknown quality',()=>{
 const evidence=['accepted','pending',null].map((quality,index)=>({id:'s'+index,title:'资料'+index,knowledge_id:'k_'+index,knowledge_saved:true,...(quality?{knowledge_quality:{state:quality,reusable:quality==='accepted'}}:{})}));
 const html=renderAgent({task:{id:'a',status:'completed',report:{evidence}},tab:'sources'});
 assert.match(html,/data-agent-knowledge="k_0" data-agent-knowledge-quality="accepted"/);
 assert.match(html,/data-agent-knowledge="k_1" data-agent-knowledge-quality="pending"/);
 const legacy=html.match(/<button[^>]*data-agent-knowledge="k_2"[^>]*>/)?.[0];
 assert.ok(legacy);assert.ok(!legacy.includes('data-agent-knowledge-quality='));
});

test('research heading describes actual collaboration without a fixed nationwide disclaimer',()=>{
 const render=usage=>renderAgent({task:{id:'a',status:'running',usage,report:{}}});
 const both=render({model_calls:1,searches:2});
 assert.match(both,/AI × 元宝协同研究/);assert.ok(!both.includes('本轮有限范围，非全国完整推荐'));
 for(const usage of [{},{model_calls:1,searches:0},{model_calls:0,searches:1}]){
  const html=render(usage);assert.match(html,/官方资料研究/);assert.ok(!html.includes('AI × 元宝协同研究'));
 }
 assert.ok(!both.includes('已遍历全国'));assert.ok(!both.includes('全国完整推荐'));
});

test('soft regions and excluded subjects stay visible, confirmable and editable',()=>{
 const html=renderAgent({task:{id:'a',status:'awaiting_input',query:'只去天津，不考408',profile:{provinces:[],preferred_provinces:['天津'],excluded_subjects:[{code:'408',scope:'national'}]},report:{profile_proposal:{requires_confirmation:true,updates:{preferred_provinces:['天津'],excluded_subjects:[{code:'408',scope:'national'}]},evidence:[{field:'preferred_provinces',quote:'优先天津'},{field:'excluded_subjects',quote:'不考408'}]}}}});
 assert.match(html,/优先地区/);assert.match(html,/不考科目/);assert.match(html,/408@national/);
 assert.match(html,/确认条件并继续/);
 assert.match(html,/name="condition.preferred_provinces"/);assert.match(html,/name="condition.excluded_subjects"/);
});

test('exclusion editor values round-trip without losing scope',()=>{
 assert.deepEqual(parseExclusionList('408@national、901'),[{code:'408',scope:'national'},{code:'901'}]);
 assert.equal(formatExclusionList([{code:'408',scope:'national'},{code:'901'}]),'408@national、901');
 assert.deepEqual(parseExclusionList(''),[]);
});

test('invalid exclusion tokens are rejected instead of silently truncated',()=>{
 for(const value of ['901@@school-a','901@school-a@school-b','@school-a','901@']){
  assert.throws(()=>parseExclusionList(value),/不考科目|格式|作用域|代码/);
 }
 assert.deepEqual(parseExclusionList('408、901@school-a'),[{code:'408'},{code:'901',scope:'school-a'}]);
});

test('turn changes expose reused evidence, changed conditions and paused field conflicts',()=>{
 const report={inheritance:{reused:true,candidate_ids:['p1'],source_ids:['s1','s2'],stale_candidate_ids:['old'],profile_changed:['max_annual_tuition']},field_candidates:[{field:'tuition',label:'学费与收费口径',status:'conflict',value:null,values:[{url:'https://example.edu.cn/fees',title:'2027收费公示',year:2027,quote:'学费15000元/年'}]},{field:'tuition',label:'学费与收费口径',status:'verified',value:12000,period:'year',values:[{url:'https://example.edu.cn/fees',title:'2027收费公示',year:2027,quote:'学费12000元/年'}]}]};
 const html=renderAgent({task:{id:'a',status:'completed',turn_id:2,turn_changes:['max_annual_tuition'],report}});
 assert.match(html,/本轮继承与变化/);assert.match(html,/沿用上轮 1 个候选、2 份已核对来源/);
 assert.match(html,/每年学费上限/);assert.match(html,/1 个上轮候选因年份或来源不再有效/);
 assert.match(html,/来源冲突，已暂停/);assert.match(html,/两个独立原文一致/);
 assert.match(html,/href="https:\/\/example.edu.cn\/fees"/);assert.match(html,/回到原文/);
 const fresh=renderAgent({task:{id:'a',status:'completed',report:{}}});
 assert.ok(!fresh.includes('本轮继承与变化'));
});

test('the plan ledger lists checked goals, unknown facts, next actions and remaining budget',()=>{
 const report={plan:{version:1,goals:[{id:'scope',label:'确定可研究的合理范围',status:'done',completion:'取得有限、可解释的候选。'},{id:'evidence',label:'补齐关键事实',status:'partial',completion:'没有资料时保持 unknown。'}],facts:[{status:'known'},{status:'unknown'},{status:'unknown'}],next_actions:[{priority:1,tool:'search_official_sources',args:{query:'学费 收费标准'},reason:'费用未知时先核对收费公示。'}],budget:{remaining:{searches:2,documents:3,tool_calls:9}},completion:{ready:false,statement:'当前成果可以查看，但仍有开放目标。'}}};
 const html=renderAgent({task:{id:'a',status:'partial',report},tab:'methods'});
 assert.match(html,/研究计划与缺口/);assert.match(html,/事实已明确 <strong>1<\/strong>/);assert.match(html,/仍未知 <strong>2<\/strong>/);
 assert.match(html,/接下来优先核对/);assert.match(html,/费用未知时先核对收费公示/);
 assert.match(html,/剩余检索 2 次 · 文档 3 次 · 工具调用 9 次/);assert.match(html,/仍有开放目标/);
 const empty=renderAgent({task:{id:'a',status:'completed',report:{}},tab:'methods'});
 assert.ok(!empty.includes('研究计划与缺口'));
});

test('decision reasons and candidates return to the saved original without unsafe links',()=>{
 const program={id:'p1',unit_id:'u',unit_name:'大学',name:'计算机',code:'085404'};
 const report={candidates:[program],decisions:[{candidate_id:'p1',original_links:[{url:'https://example.edu.cn/original',title:'招生简章'},{url:'javascript:alert(1)'}],reasons:[{field:'tuition',message:'学费已由两个原文核对。',evidence:[{url:'https://example.edu.cn/original'}]}]}],algorithm:{matching:{summary:{input_count:1},matches:[{program,reasons:[{field:'tuition',label:'学费与收费口径',message:'学费已由两个原文核对。',evidence:[{url:'https://example.edu.cn/original'},{url:'javascript:alert(1)'}]}]}]}}};
 const overview=renderAgent({task:{id:'a',status:'completed',report}});
 assert.match(overview,/class="ya-candidate-links"/);assert.match(overview,/href="https:\/\/example.edu.cn\/original"/);
 assert.ok(!overview.includes('href="javascript:'));
 const methods=renderAgent({task:{id:'a',status:'completed',report},tab:'methods'});
 assert.match(methods,/学费已由两个原文核对/);assert.match(methods,/class="ya-original-links"/);
 assert.equal((methods.match(/href="https:\/\/example.edu.cn\/original"/g)||[]).length>=2,true);
 assert.ok(!methods.includes('href="javascript:'));
});
