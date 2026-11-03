import test from 'node:test';
import assert from 'node:assert/strict';
import { buildHistorySeries, buildComparisonMetrics, renderHistoryCharts, renderComparisonCharts, renderRouteCoverage } from '../static/charts.mjs';

const source={url:'https://grad.example.edu.cn/results',quote:'原文公示结果'};
const program=(changes={})=>({id:'p',unit_id:'u',name:'测试大学',department:'计算机学院',code:'081200',major:'计算机科学与技术 · 00 不区分方向',mode:'全日制',year:2027,historyWindow:[2026,2025,2024],history:[],scoreLines:[],annualFee:null,totalFee:null,duration:null,raw:{unit_id:'u',department:'计算机学院',code:'081200',name:'计算机科学与技术',direction:'00 不区分方向',study_mode:'全日制'},...changes});
const statistic=(changes={})=>({id:'s26',unit_id:'u',department:'计算机学院',program_code:'081200',program_name:'计算机科学与技术',study_mode:'全日制',year:2026,cohort:'院系专业公示样本',plan_type:'unmarked',admission_source:'not_specified',sample_count:4,initial:{min:330,max:380,median:350,full_score:500},evidence:source,evidence_status:'derived',...changes});
const history=(stat)=>({year:stat.year,min:stat.initial?.min,max:stat.initial?.max,median:stat.initial?.median,count:stat.sample_count,statistic:stat});

test('unverified, tampered or mismatched history values are retained as pending and never plotted',()=>{
 for(const status of [undefined,null,'needs_review','invalid_status','missing','conflict',true]){
  const value=program({history:[history(statistic({evidence_status:status,evidence_note:'来源已变化，统计值与公示不一致。'}))]});
  const model=buildHistorySeries(value);
  assert.equal(model.admitted.length,0,String(status));
  assert.equal(model.pendingEvidenceCount,1);
  const html=renderHistoryCharts(value);assert.match(html,/依据待核/);assert.doesNotMatch(html,/data-chart="admitted-range"/);
 }
 for(const evidence_status of ['derived','verified']){
  assert.equal(buildHistorySeries(program({history:[history(statistic({evidence_status}))]})).admitted.length,1);
 }
});

test('evidence verification and reference applicability are independent chart gates',()=>{
 const line={id:'line',unit_id:'u',year:2026,kind:'university',discipline_code:'08',degree_type:'学术学位',total:290,evidence:source,applicability:'unknown'};
 for(const evidence_status of [undefined,'needs_review','invalid_status']){
  const value=program({referenceScoreLines:[{...line,evidence_status}]});
  assert.equal(buildHistorySeries(value).referenceLines.length,0);
  assert.match(renderHistoryCharts(value),/依据待核/);
 }
 const verified=program({referenceScoreLines:[{...line,evidence_status:'verified'}]});
 assert.equal(buildHistorySeries(verified).referenceLines.length,1);
 assert.match(renderHistoryCharts(verified),/适用关系待核/);
 assert.equal(buildHistorySeries(program({scoreLines:[{...line,evidence_status:'needs_review'}]})).scoreLines.length,0);
});

test('different numbered directions with the same name never share plotted history',()=>{
 const value=program({raw:{unit_id:'u',department:'计算机学院',code:'081200',name:'计算机科学与技术',direction:'01 软件工程',study_mode:'全日制'},history:[history(statistic({direction:'02 软件工程',evidence_status:'derived'}))]});
 assert.equal(buildHistorySeries(value).admitted.length,0);
 const matching=program({...value,history:[history(statistic({direction:'01 软件工程',evidence_status:'derived'}))]});
 assert.equal(buildHistorySeries(matching).admitted.length,1);
});

test('one actual year keeps two missing years without a fabricated trend',()=>{
 const value=program({history:[history(statistic())]}),before=structuredClone(value);
 const model=buildHistorySeries(value);
 assert.deepEqual(model.years,[2024,2025,2026]);
 assert.equal(model.admitted.length,1);assert.equal(model.admitted[0].points.length,1);
 const html=renderHistoryCharts(value);
 assert.match(html,/data-chart="admitted-range"/);assert.match(html,/2024/);assert.match(html,/2025/);assert.match(html,/未收录/);
 assert.doesNotMatch(html,/class="ych-trend"/);assert.match(html,/330/);assert.match(html,/380/);assert.match(html,/350/);
 assert.match(html,/data-claim-id="history:s26"/);assert.match(html,/data-program-id="p"/);
 assert.deepEqual(value,before);
});

test('trend segments require adjacent years, matching cohorts and explicit matching full scores',()=>{
 const points=[statistic({id:'s24',year:2024}),statistic({id:'s25',year:2025}),statistic()];
 const html=renderHistoryCharts(program({history:points.map(history)}));
 assert.equal((html.match(/class="ych-trend"/g)||[]).length,2);
 const gap=renderHistoryCharts(program({history:[history(points[0]),history(points[2])]}));
 assert.doesNotMatch(gap,/class="ych-trend"/);
 const unknown=renderHistoryCharts(program({history:points.map(s=>history({...s,initial:{min:330,max:380,median:350}}))}));
 assert.doesNotMatch(unknown,/class="ych-trend"/);assert.match(unknown,/满分.*未注明/);
});

test('different admission sources, plans, full score scales and directions never share a series',()=>{
 const base=statistic();
 const variants=[base,{...base,id:'transfer',admission_source:'transfer'},
  {...base,id:'special',plan_type:'少干计划'},
  {...base,id:'threehundred',initial:{min:210,max:260,median:240,full_score:300}},
  {...base,id:'direction',direction:'00 不区分方向'}];
 const model=buildHistorySeries(program({history:variants.map(history)}));
 assert.equal(model.admitted.length,5);
 assert.equal(new Set(model.admitted.map(group=>group.key)).size,5);
 const html=renderHistoryCharts(program({history:variants.map(history)}));
 assert.match(html,/调剂/);assert.match(html,/计划类别未单列/);assert.match(html,/少干计划/);
 assert.match(html,/300/);assert.match(html,/500/);
});

test('records from other schools, departments, codes or learning modes are excluded',()=>{
 const variants=[{unit_id:'other'},{department:'软件学院'},{program_code:'085404'},
  {study_mode:'非全日制'},{direction:'01 其他方向'},{year:2023}];
 for(const change of variants){
  assert.equal(buildHistorySeries(program({history:[history(statistic(change))]})).admitted.length,0,JSON.stringify(change));
 }
 assert.equal(buildHistorySeries(program({history:[history(statistic({program_code:null}))]})).admitted.length,1);
});

test('two publications in one year are kept separately instead of combining samples',()=>{
 const model=buildHistorySeries(program({history:[history(statistic()),history(statistic({id:'other-publication',sample_count:12}))]}));
 assert.equal(model.admitted.length,2);
 assert.ok(model.admitted.every(group=>group.points.length===1));
 assert.match(renderHistoryCharts(program({history:[history(statistic()),history(statistic({id:'other-publication'}))]})),/分开保留/);
});

test('invalid numbers, unavailable sources and out of window years never become zero data',()=>{
 const cases=[{initial:{min:380,max:330}},{initial:{min:NaN,max:380}},
  {initial:{min:330,max:Infinity}},{initial:{min:330,max:380,full_score:300}},
  {evidence:{url:'javascript:alert(1)'}},{evidence:null},{year:2023}];
 for(const change of cases)assert.equal(buildHistorySeries(program({history:[history(statistic(change))]})).admitted.length,0);
 const html=renderHistoryCharts(program());assert.match(html,/尚未收录可绘制的录取公示样本/);
 assert.doesNotMatch(html,/data-chart="admitted-range"/);
 assert.match(renderHistoryCharts(program({historyWindow:[]})),/历史年度尚未明确/);
});

test('retest lines remain their own chart with a separate evidence claim',()=>{
 const line={id:'line26',unit_id:'u',program_code:'081200',department:'计算机学院',study_mode:'全日制',year:2026,kind:'program',total:320,full_score:500,evidence:source,evidence_status:'verified'};
 const value=program({history:[history(statistic())],scoreLines:[line]});
 const model=buildHistorySeries(value);
 assert.equal(model.admitted.length,1);assert.equal(model.scoreLines.length,1);
 const html=renderHistoryCharts(value);assert.match(html,/data-chart="retest-line"/);
 assert.match(html,/复试线.*录取/s);assert.match(html,/data-claim-id="score:line26"/);
 assert.equal(buildHistorySeries(program({scoreLines:[{...line,study_mode:'非全日制'}]})).scoreLines.length,0);
 assert.equal(buildHistorySeries(program({scoreLines:[{...line,department:'其他学院'}]})).scoreLines.length,0);
});

test('explicit full score aliases are accepted without inferring missing maxima',()=>{
 for(const field of ['full_score','full_marks','score_max']){
  const initial={min:330,max:380,median:350,[field]:500};
  assert.equal(buildHistorySeries(program({history:[history(statistic({initial}))]})).admitted[0].fullScore,500);
 }
 const group=buildHistorySeries(program({history:[history(statistic({initial:{min:330,max:380}}))]})).admitted[0];
 assert.equal(group.fullScore,null);
});

test('school discipline reference lines remain separate and never become a program threshold',()=>{
 const lines=[2024,2025,2026].map((year,index)=>({id:`school${year}`,unit_id:'u',year,kind:'university',discipline_code:'08',degree_type:'学术学位',total:index===0?300:290,evidence:source,evidence_status:'verified',applicability:'unknown'}));
 const value=program({referenceScoreLines:lines});
 const model=buildHistorySeries(value);
 assert.equal(model.scoreLines.length,0);assert.equal(model.referenceLines.length,1);
 assert.equal(model.referenceLines[0].points.length,3);
 const html=renderHistoryCharts(value);
 assert.match(html,/data-chart="school-reference-line"/);assert.doesNotMatch(html,/data-chart="retest-line"/);
 assert.match(html,/学校门类参考线 · 适用关系待核/);assert.match(html,/各学院可能提高要求/);
 assert.match(html,/data-claim-id="score:school2024"/);assert.doesNotMatch(html,/class="ych-trend"/);
});

test('academic and professional school references retain exact code coverage changes',()=>{
 const base={unit_id:'u',kind:'university',evidence:source,evidence_status:'verified',applicability:'unknown'};
 const references=[{...base,id:'academic26',year:2026,degree_type:'学术学位',discipline_code:'08',total:290},
  {...base,id:'professional26',year:2026,degree_type:'专业学位',discipline_code:'08',total:290},
  {...base,id:'professional24',year:2024,degree_type:'专业学位',code_prefixes:['085','086'],total:310,scope_note:'本年明确列085与086，口径与后续门类行不同。'}];
 const model=buildHistorySeries(program({referenceScoreLines:references}));
 assert.equal(model.referenceLines.length,3);
 const html=renderHistoryCharts(program({referenceScoreLines:references}));
 assert.match(html,/学术学位/);assert.match(html,/专业学位/);assert.match(html,/代码范围 085 \/ 086/);
 assert.match(html,/口径与后续门类行不同/);assert.doesNotMatch(html,/class="ych-trend"/);
});

test('history chart provides actual accessible tables and escapes all labels and source identifiers',()=>{
 const value=program({id:'p" onclick="evil',history:[history(statistic({id:'s<script>',cohort:'<img src=x onerror=evil>'}))]});
 const html=renderHistoryCharts(value);
 assert.match(html,/<svg[^>]*role="img"/);assert.match(html,/<caption>/);assert.match(html,/查看数据表/);
 assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|<script|onclick="evil/);
 assert.match(html,/data-claim-id="history:s&lt;script&gt;"/);
 assert.match(html,/公示样本/);
});

test('mobile history keeps all three years and source controls in view without a wide SVG',()=>{
 const value=program({history:[history(statistic())]});
 const html=renderHistoryCharts(value),mobile=html.match(/<div class="ych-mobile-plot">.*?<\/div><div class="ych-year-sources">/s)?.[0];
 assert.ok(mobile);assert.match(mobile,/2024/);assert.match(mobile,/2025/);assert.match(mobile,/2026/);
 assert.match(mobile,/330—380/);assert.match(mobile,/中位 350/);
 assert.match(mobile,/<button[^>]+data-claim-id="history:s26"/);
 assert.doesNotMatch(mobile,/NaN|Infinity/);
});

test('fee charts keep annual and total amounts separate, unknown distinct and literal zero valid',()=>{
 const input=[program({annualFee:8000,duration:3}),program({id:'p2',name:'乙大学',totalFee:40000,duration:2}),program({id:'p3',name:'丙大学',annualFee:0})];
 const metrics=buildComparisonMetrics(input),annual=metrics.find(metric=>metric.kind==='annual_fee'),total=metrics.find(metric=>metric.kind==='total_fee');
 assert.deepEqual(annual.rows.map(row=>row.value),[8000,null,0]);
 assert.deepEqual(total.rows.map(row=>row.value),[null,40000,null]);
 const html=renderComparisonCharts(input);assert.match(html,/元 \/ 年/);assert.match(html,/元 \/ 全程/);
 assert.match(html,/未知/);assert.match(html,/>0 元/);assert.doesNotMatch(html,/24,000/);
 assert.match(html,/data-claim-id="tuition"/);assert.match(html,/data-claim-id="duration"/);
});

test('different fee reference years get separate charts and uncertainty does not look free',()=>{
 const metrics=buildComparisonMetrics([program({annualFee:8000}),program({id:'old',year:2026,annualFee:7000})]);
 assert.equal(metrics.filter(metric=>metric.kind==='annual_fee').length,2);
 assert.deepEqual(metrics.filter(metric=>metric.kind==='annual_fee').map(metric=>metric.year),[2027,2026]);
 const html=renderComparisonCharts([program(),program({id:'p2'})]);
 assert.match(html,/费用尚未收录/);assert.doesNotMatch(html,/>0 元/);
 assert.equal(renderComparisonCharts([]),'');
});

test('subject adjustment counts use uncertainty and never invent zero changes',()=>{
 const values=[program({evaluation:{subject_changes:{count:0,uncertain:false}}}),
  program({id:'uncertain',evaluation:{subject_changes:{count:0,uncertain:true}}}),
  program({id:'one',evaluation:{subject_changes:{count:1,uncertain:false}}})];
 const metric=buildComparisonMetrics(values).find(item=>item.kind==='subject_changes');
 assert.deepEqual(metric.rows.map(row=>row.value),[0,null,1]);
 const html=renderComparisonCharts(values);assert.match(html,/考试单元/);assert.match(html,/科目/);assert.match(html,/data-claim-id="subjects"/);
});

test('comparison labels escape markup, zero widths stay finite, and only first three candidates render',()=>{
 const values=[program({name:'<svg onload=evil>',annualFee:0}),program({id:'p2',annualFee:0}),program({id:'p3'}),program({id:'fourth',name:'第四所不应出现',annualFee:12345})];
 const html=renderComparisonCharts(values);
 assert.match(html,/&lt;svg/);assert.doesNotMatch(html,/<svg onload|NaN|Infinity|第四所不应出现/);
 assert.match(html,/参考年度/);assert.match(html,/查看.*依据/);
});

test('route coverage plots actual direction counts while keeping school, major and pending counts distinct',()=>{
 const result={routes:[{id:'subjects-0',max_subject_changes:0,coverage:{program_count:7,department_program_count:4,unit_count:1},matched_count:0,needs_confirmation_count:7,unknown_subject_count:2},
  {id:'subjects-1',max_subject_changes:1,coverage:{program_count:12,department_program_count:7,unit_count:1},matched_count:1,needs_confirmation_count:11,unknown_subject_count:2},
  {id:'subjects-2',max_subject_changes:2,coverage:{program_count:173,department_program_count:53,unit_count:2},matched_count:2,needs_confirmation_count:171,unknown_subject_count:2}]};
 const html=renderRouteCoverage(result);
 assert.match(html,/data-chart="route-coverage"/);assert.match(html,/173/);assert.match(html,/53 个院系专业/);assert.match(html,/2 所学校/);
 assert.match(html,/171 个待核验/);assert.match(html,/2 个已知条件相容/);
 assert.match(html,/科目身份.*未计入/s);assert.doesNotMatch(html,/录取率|成功率|NaN|Infinity/);
 assert.equal(renderRouteCoverage(null),'');assert.equal(renderRouteCoverage({routes:[]}), '');
});
