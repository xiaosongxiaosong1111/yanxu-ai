import test from 'node:test';
import assert from 'node:assert/strict';
import { adaptProgram, matchingStatistics, matchingRules, historicalFacts, subjectIdentity, safeHttpUrl, createApi, restoreClientState, createCatalogClient, catalogPageWindow,declaredExamUnits,matchesCatalogQuery,annualUnitOptionLabel,annualUnitCount,catalogAliasMap,inferCatalogUnit,sourceKinds } from '../static/data.mjs';
import { renderDetail,renderComparison,renderReasons } from '../static/details.mjs';
import { parseRoute,paginate,nextTabIndex } from '../static/view-state.mjs';
import { toggleSaved,toggleCompare,latestLine } from '../static/model.mjs';

test('department display omits contact details while preserving the original record',()=>{
 const raw={id:'p',department:'蒙古学学院咨询电话：0471-4992281',subjects:[]};
 const view=adaptProgram(raw);
 assert.equal(view.department,'蒙古学学院');
 assert.equal(view.raw.department,raw.department);
 assert.equal(adaptProgram({id:'q',department:'文学院联系人：蔡老师0476-8300570',subjects:[]}).department,'文学院');
 assert.equal(adaptProgram({id:'empty',department:'',department_scope:'unit',subjects:[]}).department,'院系资料未收录');
});

test('annual admission labels distinguish an index record from an independently listed unit',()=>{
 assert.equal(annualUnitOptionLabel({name:'样本大学',annual_year:2027,annual_status:'not_listed',program_count:0}),'样本大学（2027未列独立招生单位）');
 assert.equal(annualUnitOptionLabel({name:'样本大学',annual_status:'confirmed',program_count:0}),'样本大学（目录待入库）');
 assert.equal(annualUnitOptionLabel({name:'样本大学',annual_status:'confirmed',program_count:5}),'样本大学');
 assert.equal(annualUnitCount({units:[{annual_status:'confirmed'},{annual_status:'not_listed'}]}),1);
 assert.equal(annualUnitCount({coverage:{annual_official_total:931},units:[]}),931);
 assert.equal(annualUnitCount({units:[{name:'尚待确认'}]}),null);
});

test('light catalog bootstrap needs no nationwide programs or original documents',async()=>{
 const paths=[],client=createCatalogClient(async path=>{paths.push(path);return {revision:'r1',meta:{reference_year:2027},units:[{id:'a',program_count:99}],coverage:{program_count:99}};});
 const data=await client.bootstrap();assert.equal(data.units.length,1);assert.deepEqual(client.programs(),[]);
 assert.deepEqual(paths,['/api/catalog-bootstrap']);assert.equal(client.revision(),'r1');
 const page=catalogPageWindow([1,2,3,4,5,6],99,3,6);assert.equal(page.totalPages,17);assert.equal(page.page,3);assert.equal(page.items.length,6);
});

test('catalog pages request only six records and a listing row does not pretend to include history',async()=>{
 const paths=[],client=createCatalogClient(async path=>{paths.push(path);if(path==='/api/catalog-bootstrap')return {revision:'r1'};if(path.startsWith('/api/catalog-programs'))return {revision:'r1',total:99,programs:[{id:'p',name:'专业',unit_id:'a'}]};return {revision:'r1',program:{id:'p',name:'专业',unit_id:'a'},admitted_statistics:[{year:2026}]};});
 await client.bootstrap();await client.page({unit_id:'a',year:2027,q:'人工智能',page:3});
 const query=new URL(paths[1],'http://local').searchParams;assert.equal(query.get('offset'),'12');assert.equal(query.get('limit'),'6');assert.equal(query.get('revision'),'r1');
 assert.equal(client.bundle('p'),null);await client.program('p');await client.program('p');
 assert.equal(paths.filter(path=>path.startsWith('/api/catalog-program?')).length,1);assert.equal(client.bundle('p').admitted_statistics[0].year,2026);
});

test('lazy program loading deduplicates requests and a new revision clears all prior cached context',async()=>{
 let revision='r1',requests=0;
 const client=createCatalogClient(async path=>{if(path==='/api/catalog-bootstrap')return {revision};requests++;await Promise.resolve();return {revision,program:{id:'p',unit_id:'a'}};});
 await client.bootstrap();await Promise.all([client.program('p'),client.program('p')]);assert.equal(requests,1);
 revision='r2';await client.bootstrap();assert.deepEqual(client.programs(),[]);assert.equal(client.bundle('p'),null);
});

test('a stale catalog response never combines program data from different published versions',async()=>{
 const client=createCatalogClient(async path=>path==='/api/catalog-bootstrap'?{revision:'r1'}:{revision:'r2',total:1,programs:[{id:'other'}]});
 await client.bootstrap();await assert.rejects(client.page({year:2027}),error=>error.code==='stale_revision');assert.deepEqual(client.programs(),[]);
});

test('subject catalogs page all real exam identities without scanning cached programs',async()=>{
 const calls=[],client=createCatalogClient(async path=>{calls.push(path);if(path==='/api/catalog-bootstrap')return {revision:'r1'};const offset=Number(new URL(path,'http://local').searchParams.get('offset'));return {revision:'r1',total:201,subjects:Array.from({length:offset?1:200},(_,i)=>({code:String(i+offset),scope:'a',exam_units:[3,4]}))};});
 await client.bootstrap();const result=await client.subjects({unit_id:'a',year:2027});assert.equal(result.subjects.length,201);
 assert.ok(calls.every(path=>path==='/api/catalog-bootstrap'||path.startsWith('/api/catalog-subjects?')));assert.deepEqual(client.programs(),[]);
});

test('lazy saved records preserve individual failures and retry a previously missing bundle',async()=>{
 let fixed=false;const client=createCatalogClient(async path=>{if(path==='/api/catalog-bootstrap')return {revision:'r1'};const id=new URL(path,'http://local').searchParams.get('program_id');if(id==='missing'&&!fixed)throw new Error('目录待复核');return {revision:'r1',program:{id}};});
 await client.bootstrap();const result=await client.many(['a','missing','b','a']);assert.equal(result.loaded.length,2);assert.equal(result.failed[0].id,'missing');fixed=true;assert.equal((await client.program('missing')).program.id,'missing');
});

test('subject failures and cancellation cannot present a partial catalog as fully loaded',async()=>{
 const signal=new AbortController();signal.abort();
 const client=createCatalogClient(async path=>path==='/api/catalog-bootstrap'?{revision:'r1'}:{revision:'r1',total:201,subjects:Array.from({length:200},(_,i)=>({code:String(i),scope:'a'}))});
 await client.bootstrap();await assert.rejects(client.subjects({signal:signal.signal}),error=>error.name==='AbortError');
 const invalid=createCatalogClient(async path=>path==='/api/catalog-bootstrap'?{revision:'r1'}:{revision:'r1',total:201,subjects:[]});await invalid.bootstrap();await assert.rejects(invalid.subjects({}),/页面|完整/);
});

test('server exam units and complete algorithm results do not depend on the six visible records',()=>{
 assert.deepEqual(declaredExamUnits({exam_units:[3,4]}),[3,4]);assert.deepEqual(declaredExamUnits({exam_unit:2}),[2]);assert.deepEqual(declaredExamUnits({code:'408'}),[]);
 const all=[{id:'outside-page',name:'计算机科学与技术',subjects:[]},{id:'another',name:'物理学',subjects:[]}];
 assert.deepEqual(all.filter(program=>matchesCatalogQuery(program,'计算机')).map(program=>program.id),['outside-page']);assert.equal(matchesCatalogQuery(all[1],''),true);
 assert.equal(matchesCatalogQuery(all[1],'物理 数学'),false);
 assert.equal(matchesCatalogQuery({unit_name:'甲大学',name:'计算机科学与技术',subjects:[{code:'408',name:'计算机学科专业基础'}]},'甲大学 408'),true);
 assert.equal(matchesCatalogQuery({unit_name:'甲大学',name:'计算机科学与技术',subjects:[{code:'408',name:'计算机学科专业基础'}]},'甲大学 数学'),false);
 const aliases=catalogAliasMap([{id:'u1',name:'中国社会科学院大学',aliases:['社科大']}]);
 assert.equal(matchesCatalogQuery({unit_name:'中国社会科学院大学',name:'计算机科学与技术'},'社科大 计算机',aliases),true);
 assert.equal(catalogAliasMap([{name:'甲大学',aliases:['甲大']}]).get('甲大'),'甲大学');
 assert.equal(inferCatalogUnit('社科大 计算机',[{id:'u1',name:'中国社会科学院大学',aliases:['社科大']}]),'u1');
 assert.equal(inferCatalogUnit('北京大学医学部 临床',[{id:'peking',name:'北京大学'},{id:'medical',name:'北京大学医学部'}]),'medical');
 assert.equal(inferCatalogUnit('北京大学 临床',[{id:'peking',name:'北京大学'},{id:'medical',name:'北京大学医学部'}]),'peking');
 assert.equal(inferCatalogUnit('中国人民大学 计算机',[{id:'ruc',name:'中国人民大学'},{id:'suzhou',name:'中国人民大学苏州校区'}]),'ruc');
 assert.equal(inferCatalogUnit('南开大学计算机',[{id:'nankai',name:'南开大学'}]),'nankai');
 assert.equal(inferCatalogUnit('计算机',[{id:'u1',name:'甲大学'}]),'');
 assert.equal(restoreClientState({preferences:{sourceKind:'score'}}).preferences.sourceKind,'score');
 assert.equal(restoreClientState({preferences:{sourceKind:'made-up'}}).preferences.sourceKind,'catalog');
 assert.equal(sourceKinds.some(([key])=>key==='catalog'),true);
 assert.equal(catalogPageWindow([],0,2).page,1);
});

test('analysis preserves catalog hits for degree and study-mode search terms',()=>{
 const professional={id:'p0',unit_name:'甲大学',name:'计算机技术',department:'计算机学院',study_mode:'非全日制',degree_type:'专业学位',subjects:[{code:'408',name:'计算机学科专业基础'}]};
 const academic={...professional,id:'p1',study_mode:'全日制',degree_type:'学术学位'};
 for(const query of ['非全日制','非全','专业学位','专硕','甲大学 专硕','甲大学 非全 408']){
  assert.deepEqual([professional,academic].filter(program=>matchesCatalogQuery(program,query)).map(program=>program.id),['p0'],query);
 }
 assert.deepEqual([professional,academic].filter(program=>matchesCatalogQuery(program,'甲大学 学硕')).map(program=>program.id),['p1']);
 assert.equal(matchesCatalogQuery(professional,'甲大学 学硕'),false);
 assert.equal(matchesCatalogQuery(professional,'甲大学 专硕 数学'),false);
 assert.equal(matchesCatalogQuery({...academic,name:'专硕改革试点'},'专硕'),true,'a synonym also retains the exact literal match');
 const aliases=catalogAliasMap([{name:'甲大学',aliases:['甲大']}]);
 assert.equal(matchesCatalogQuery(professional,'甲大 专硕',aliases),true);
});

test('continuing in Agent keeps the selected official material and reference year',async()=>{
 const {sourceResearchSeed}=await import('../static/data.mjs');
 assert.equal(typeof sourceResearchSeed,'function');
 for(const [kind,label] of sourceKinds){
  const seed=sourceResearchSeed({name:'甲大学'},kind,2027);
  assert.equal(seed,`甲大学 2027年${label}${kind==='catalog'?' 考试科目':''}`);
 }
 assert.equal(sourceResearchSeed({name:'甲大学'},'fees',null),'甲大学 学费与学制');
 assert.equal(sourceResearchSeed({name:'甲大学'},'fees','2027'),'甲大学 学费与学制');
 assert.equal(sourceResearchSeed({name:'甲大学'},'made-up',2027),'甲大学 2027年招生目录 考试科目');
 assert.equal(sourceResearchSeed(null,'transfer',2027),'');
});

test('real programs retain missing admission statistics and scoped subjects', () => {
  const value = {id:'real-1',unit_id:'367949',unit_name:'南开大学',year:2027,name:'计算机科学与技术',code:'081200',subjects:[{code:'408',name:'计算机学科专业基础',scope:'national',exam_unit:4}],tuition:null};
  const item = adaptProgram(value, [{unit_id:'367949',program_code:'081200',year:2026,kind:'program',total:330,evidence_status:'verified'}],null,[],2026);
  assert.equal(item.name, '南开大学');
  assert.equal(item.annualFee, null);
  assert.equal(item.seats, null);
  assert.deepEqual(item.history, []);
  assert.equal(item.scoreLines[0].total,330);
  assert.equal(item.subjectObjects[0].scope,'national');
  assert.equal(item.status,'not_assessed');
});

test('self-set course identity includes institution and examination unit', () => {
  assert.notEqual(subjectIdentity({code:'901',scope:'a',exam_unit:4}),subjectIdentity({code:'901',scope:'b',exam_unit:4}));
  assert.notEqual(subjectIdentity({code:'901',scope:'a',exam_unit:3}),subjectIdentity({code:'901',scope:'a',exam_unit:4}));
  assert.equal(safeHttpUrl('javascript:alert(1)'), '');
  assert.equal(safeHttpUrl('https://yzb.nankai.edu.cn/a'), 'https://yzb.nankai.edu.cn/a');
});

test('official program-level catalogs keep missing departments and directions explicit',()=>{
 const raw={id:'p',unit_id:'u',unit_name:'研究所',year:2027,name:'材料科学与工程',code:'0805',code_level:'discipline',department:'',department_scope:'unit',direction:'',direction_scope:'program',subjects:[]};
 const item=adaptProgram(raw);
 assert.equal(item.department,'院系资料未收录');
 assert.equal(item.code,'0805');
 assert.equal(item.major,'材料科学与工程');
 assert.match(renderDetail(item),/目录未单列方向/);
 assert.equal(item.raw.direction,'');
});

test('API envelopes surface configured errors and serialize only explicit request bodies', async () => {
  const requests=[];
  const api=createApi(async (path,options)=>{requests.push({path,options});return {ok:true,json:async()=>({ok:true,data:{total:2},error:null})};});
  assert.deepEqual(await api('/api/match',{body:{profile:{education:'unknown'}}}),{total:2});
  assert.equal(requests[0].options.method,'POST');
  assert.deepEqual(JSON.parse(requests[0].options.body),{profile:{education:'unknown'}});
  const failing=createApi(async()=>({ok:false,json:async()=>({ok:false,error:{code:'not_configured',message:'平台检索未配置'}})}));
  await assert.rejects(failing('/api/search',{body:{unit_id:'a'}}),error=>error.code==='not_configured');
});

test('malformed local preferences cannot introduce keys or unknown subject identities', () => {
  const state=restoreClientState({profile:{education:'bachelor',subjects:[{code:'901',name:'结构',scope:'a',exam_unit:4}],api_key:'secret'},saved:['x','x',2],preferences:{query:'<img>',unit_id:'a'}});
  assert.equal(state.profile.education,'bachelor');
  assert.equal(Object.hasOwn(state.profile,'api_key'),false);
  assert.deepEqual(state.saved,['x']);
  assert.equal(state.profile.subjects[0].scope,'a');
  assert.equal(restoreClientState([]).profile.education,'unknown');
});

test('ranking mode restores safely and stays aligned with the selected priority',()=>{
 assert.equal(restoreClientState(null).profile.ranking_mode,'balanced');
 for(const mode of ['balanced','subjects','cost']){
  const restored=restoreClientState({profile:{ranking_mode:mode},preferences:{priority:mode}});
  assert.equal(restored.profile.ranking_mode,mode);assert.equal(restored.preferences.priority,mode);
 }
 assert.equal(restoreClientState({profile:{ranking_mode:'unsupported'}}).profile.ranking_mode,'balanced');
 assert.equal(restoreClientState({preferences:{priority:'subjects'}}).profile.ranking_mode,'subjects');
});

test('ranking evidence is visible and unknown cost never looks like a complete score',()=>{
 const result={status:'unknown',rank_score:92,rank_interval:[60,100],components:{subjects:1,cost:null},reasons:[],ranking_basis:{mode:'subjects',explanation:'按已知科目衔接排序；费用尚未核验'}};
 const program=adaptProgram({id:'p',unit_id:'u',unit_name:'甲大学',name:'专业',year:2027,subjects:[]},[],result);
 const html=renderDetail(program);
 assert.match(html,/按已知科目衔接排序；费用尚未核验/);assert.doesNotMatch(html,/偏好匹配分 92/);
 assert.doesNotMatch(renderComparison([program]),/>92</);assert.match(renderComparison([program]),/排序依据/);
});

test('tuition and duration retain their separate official evidence without repeated links',()=>{
 const fee={document_id:'fees',url:'https://example.edu.cn/fees.pdf',page:2,quote:'每年8000元'},duration={document_id:'duration',url:'https://example.edu.cn/duration.pdf',page:3,quote:'学制3年'};
 const program=adaptProgram({id:'p',unit_id:'u',unit_name:'甲大学',name:'专业',year:2027,subjects:[],tuition:{amount:8000,period:'year',evidence:[fee]},duration_years:3,duration_evidence:[duration],enrichment_evidence:[fee,duration]});
 const html=renderDetail(program);
 assert.match(html,/8,000 元 \/ 年/);assert.match(html,/3 年/);assert.match(html,/href="https:\/\/example.edu.cn\/fees.pdf"/);assert.match(html,/href="https:\/\/example.edu.cn\/duration.pdf"/);
 assert.equal(html.split('href="https://example.edu.cn/fees.pdf"').length-1,1);assert.match(html,/第 2 页/);assert.match(html,/学制3年/);
});

test('admitted samples require explicit institution, department, program and learning type identity', () => {
  const raw={id:'x',unit_id:'367949',unit_name:'南开大学',department:'035计算机学院',code:'081200',name:'计算机科学与技术',study_mode:'全日制',subjects:[]};
  const sample={unit_id:'367949',department:'计算机学院',program_name:'计算机科学与技术',program_code:null,study_mode:'全日制',year:2026,sample_count:4,initial:{min:366,max:418,median:378.5},admission_source:'not_specified'};
  assert.equal(matchingStatistics(raw,[sample]).length,1);
  assert.equal(matchingStatistics(raw,[{...sample,department:'软件学院'}]).length,0);
  assert.equal(matchingStatistics({...raw,department:''},[{...sample,department:''}]).length,0);
  assert.equal(matchingStatistics({...raw,study_mode:undefined},[{...sample,study_mode:undefined}]).length,0);
  const transfer={...sample,admission_source:'transfer',initial:{min:330,max:370,median:350}};
  const adapted=adaptProgram(raw,[],null,[sample,transfer],2026);
  assert.equal(adapted.history.length,2);
  assert.notEqual(adapted.history[0].statistic.admission_source,adapted.history[1].statistic.admission_source);
});

test('API transport and invalid JSON failures remain visible rather than becoming empty data',async()=>{
  await assert.rejects(createApi(async()=>{throw new Error('offline');})('/api/bootstrap'),error=>error.code==='network_error');
  await assert.rejects(createApi(async()=>({ok:true,json:async()=>{throw new Error('bad');}}))('/api/bootstrap'),error=>error.code==='invalid_response');
  const abort=new Error('cancelled');abort.name='AbortError';
  await assert.rejects(createApi(async()=>{throw abort;})('/api/bootstrap'),error=>error.name==='AbortError');
  assert.equal(safeHttpUrl('bad-url'),'');
  await assert.rejects(createApi(async()=>({ok:true,json:async()=>null}))('/api/bootstrap'),error=>error.code==='invalid_response');
});

test('stored hard limits remain distinct and invalid personal fields do not survive restoration',()=>{
 const valid=restoreClientState({profile:{education:'master',background:'材料工程',study_mode:'非全日制',work_years:0,max_annual_tuition:0,max_subject_changes:1,provinces:['天津',null],preferences:{cost:0,subjects:5,location:1},subjects:[{code:'101',name:'政治',scope:'national',exam_unit:1},{code:'204',name:'英语二',scope:'national',exam_unit:2}]} ,preferences:{priority:'cost',examCount:2}});
 assert.equal(valid.profile.max_annual_tuition,0);assert.equal(valid.profile.max_subject_changes,1);assert.equal(valid.profile.work_years,0);assert.deepEqual(valid.profile.provinces,['天津']);assert.equal(valid.preferences.examCount,2);
 const invalid=restoreClientState({profile:{education:'bad',background:8,max_subject_changes:10,max_annual_tuition:-1,subjects:[{code:'1',scope:'x',exam_unit:0},{code:'1',scope:'',exam_unit:1}]},preferences:{priority:'bad',examCount:9},saved:[2,null]});
 assert.equal(invalid.profile.education,'unknown');assert.deepEqual(invalid.profile.subjects,[]);assert.equal(Object.hasOwn(invalid.profile,'max_annual_tuition'),false);assert.equal(invalid.preferences.priority,'balanced');assert.equal(invalid.preferences.examCount,4);assert.deepEqual(invalid.saved,[]);
});

test('history fallback is restricted to the last three completed years and retains the actual year',()=>{
 const raw={id:'x',unit_id:'a',unit_name:'甲大学',department:'计算机学院',name:'计算机科学与技术',code:'081200',study_mode:'全日制',subjects:[]};
 const sample={unit_id:'a',department:'计算机学院',program_name:'计算机科学与技术',study_mode:'全日制',sample_count:2,initial:{min:320,max:350,median:335}};
 const result=adaptProgram(raw,[],null,[{...sample,year:2025},{...sample,year:2023}],2026);
 assert.deepEqual(result.history.map(item=>item.year),[2025]);
 assert.deepEqual(result.historyWindow,[2026,2025,2024]);
 assert.deepEqual(adaptProgram(raw,[],null,[{...sample,year:2025}]).history,[]);
});

test('shared enrollment counts retain scope and are not fabricated as direction seats',()=>{
 const raw={id:'p',unit_id:'u',unit_name:'甲大学',name:'专业',year:2027,department:'学院',study_mode:'全日制',subjects:[],planned_enrollment:{count:6,scope:'department_program',includes_recommendation_exemption:false,estimated:true,evidence:{url:'https://example.edu.cn/catalog'}}};
 const item=adaptProgram(raw,[],null,[],2026);
 assert.equal(item.seats,null);assert.equal(item.plannedEnrollment.count,6);
 const html=renderDetail(item);
 assert.match(html,/院系专业合计/);assert.match(html,/非本方向单独名额/);assert.match(renderComparison([item]),/不含推免/);
});

test('historical score inputs only appear for actual source rules and keep the selected source year',()=>{
 const item=adaptProgram({id:'p',unit_id:'u',unit_name:'甲大学',name:'专业',code:'145200',year:2027,department:'学院',study_mode:'全日制',subjects:[]},[],null,[],2026);
 assert.doesNotMatch(renderDetail({...item,transferRules:[]},{tab:'transfer'}),/id="history-match-form"/);
 const html=renderDetail({...item,transferRules:[{id:'r',unit_id:'u',year:2026,title:'真实历史公告',requirements:[],requires_review:true,evidence:{url:'https://example.edu.cn/rule'}}]},{tab:'transfer'});
 assert.match(html,/id="history-match-form"/);assert.match(html,/name="first_choice_program_code"/);assert.match(html,/name="total"/);assert.match(html,/value="2026"/);
 assert.match(html,/name="first_choice_study_mode"/);
});

test('historical rule discovery matches explicit school, department and program across different directory years',()=>{
 const program={unit_id:'a',department:'037密码学院',code:'145200',name:'密码',year:2027};
 const rule={unit_id:'a',department:'密码学院',program_code:'145200',program_name:'密码',year:2026};
 assert.equal(matchingRules(program,[rule]).length,1);
 assert.equal(matchingRules(program,[{...rule,unit_id:'b'},{...rule,department:'数学学院'},{...rule,program_code:'081200',program_name:'计算机'}]).length,0);
 assert.equal(matchingRules({...program,code:'new-code'},[rule]).length,1);
 assert.equal(matchingRules({...program,code:undefined},[{...rule,program_code:undefined,program_name:'计算机'}]).length,0);
 assert.deepEqual(historicalFacts({education:'bachelor',background:'计算机',subjects:[],api_key:'secret',target_year:2028},{total:'0',politics:'',first_choice_program_code:'081200'},program),{education:'bachelor',background:'计算机',subjects:[],total:0,first_choice_program_code:'081200',program_code:'145200'});
 assert.throws(()=>historicalFacts({}, {total:'-1'},program),/成绩/);
 assert.throws(()=>historicalFacts({}, {first_choice_program_code:'123'},program),/专业代码/);
 assert.equal(historicalFacts({study_mode:'非全日制'}, {first_choice_study_mode:'全日制'},program).study_mode,'全日制');
 assert.equal(Object.hasOwn(historicalFacts({study_mode:'非全日制'}, {},program),'study_mode'),false);
});

test('incomplete preference scores show a bounded range and keep unknown cost distinct from zero',()=>{
 const result={status:'unknown',reasons:[{field:'catalog',label:'catalog',message:'章程尚待核验'}],missing:['学费待核验'],rank_score:null,rank_interval:[60,100],components:{subjects:1,cost:null,location:null},pareto:null};
 const program=adaptProgram({id:'x',unit_id:'u',unit_name:'甲大学',name:'专业',department:'学院',year:2027,subjects:[]},[],result,[],2026);
 const html=renderDetail(program);
 assert.match(html,/60—100/);assert.match(html,/分项缺失/);assert.match(html,/科目衔接/);assert.match(html,/费用.*资料不足/s);assert.doesNotMatch(html,/费用<\/dt><dd>0/);
 assert.match(html,/资格依据完整性/);assert.doesNotMatch(html,/>catalog</);
});

test('official source rendering escapes untrusted text and rejects executable URLs',()=>{
 const item=adaptProgram({id:'safe',unit_id:'u',unit_name:'<img src=x onerror=alert(1)>',name:'<script>alert(1)</script>',department:'院系',year:2027,study_mode:'全日制',subjects:[{code:'408',name:'科目',scope:'national',exam_unit:4}],tuition:{amount:0,period:'year'},duration_years:3,planned_enrollment:{count:0,scope:'direction',includes_recommendation_exemption:true,estimated:false},notes:['<img src=x>'],evidence:[{url:'javascript:alert(1)',quote:'<script>'},{url:'https://example.edu.cn/original',page:3,quote:'按原文核验'}]},[],{status:'matched',rank_score:0,rank_interval:[0,0],components:{subjects:0,cost:0,location:1},pareto:true,reasons:[{field:'subjects',message:'<svg onload=alert(1)>',evidence:[{url:'https://example.edu.cn/rule',quote:'原文'}]}],missing:[],subject_changes:{count:0,added:[],uncertain:false}},[],2026);
 const html=renderDetail(item,{saved:true,profile:{subjects:[{code:'408',name:'基础'}]}});
 assert.doesNotMatch(html,/<script|<img src=x|href="javascript:/);assert.match(html,/偏好匹配分 0/);assert.match(html,/0 元 \/ 年/);assert.match(html,/原始链接待补充/);assert.match(html,/第 3 页/);assert.match(html,/未发现另一候选/);
 const changed=renderDetail({...item,evaluation:{...item.evaluation,status:'excluded',subject_changes:{count:1,added:[{code:'901',name:'专业课'}],uncertain:false}}});assert.match(changed,/存在明确条件冲突/);assert.match(changed,/需要调整 1 门科目/);
 const uncertain=renderComparison([{...item,evaluation:{...item.evaluation,subject_changes:{count:null,uncertain:true}}},adaptProgram({id:'b',unit_id:'u',unit_name:'乙大学',name:'专业',subjects:[],tuition:{amount:40000,period:'total'}})]);assert.match(uncertain,/信息不足/);assert.match(uncertain,/40,000 元 \/ 全程/);
 assert.match(renderComparison([]),/请先选择两个专业/);assert.match(uncertain,/存在差异/);assert.match(uncertain,/蓝色底纹/);
});

test('routes decode real program IDs and pagination clamps stale pages without mutating saved plans',()=>{
 const id='367949:2027:037:145200:00:全日制';assert.equal(parseRoute('#/program/'+encodeURIComponent(id)+'?tab=history',[id]).id,id);assert.equal(parseRoute('#/program/%E0%A4%A',[id]).page,'discover');assert.equal(parseRoute('#/program/missing',[id]).page,'discover');assert.equal(parseRoute('#/program/'+encodeURIComponent(id)+'?tab=bad',[id]).tab,'overview');assert.equal(parseRoute('#/plan').page,'plan');assert.equal(parseRoute('#/compare').page,'compare');assert.equal(parseRoute('').page,'home');assert.equal(parseRoute('#/bad').page,'discover');
 const rows=[1,2,3,4,5,6,7];assert.deepEqual(paginate(rows,99).items,[7]);assert.equal(paginate(rows,NaN).page,1);assert.equal(paginate([],0).totalPages,1);assert.equal(paginate(rows,1.5).page,1);
 assert.equal(nextTabIndex('ArrowRight',0,3),1);assert.equal(nextTabIndex('ArrowLeft',0,3),2);assert.equal(nextTabIndex('Home',2,3),0);assert.equal(nextTabIndex('End',0,3),2);assert.equal(nextTabIndex('ArrowRight',-1,3),-1);assert.equal(nextTabIndex('ArrowRight',0,0),-1);assert.equal(nextTabIndex('Enter',0,3),-1);
 const ids=['a'];assert.deepEqual(toggleSaved(ids,'b'),['a','b']);assert.deepEqual(ids,['a']);assert.deepEqual(toggleSaved(ids,'a'),[]);assert.deepEqual(toggleCompare(['a','b','c'],'d'),['a','b','c']);assert.deepEqual(toggleCompare(['a'],'b'),['a','b']);assert.deepEqual(toggleCompare(['a'],'a'),[]);assert.equal(latestLine({scoreLines:[{year:2024},{year:2026},{year:2025}]}).year,2026);assert.equal(latestLine({}),null);
});

test('nested historical rules expose the selected route, leaf status and official score evidence',()=>{
 const html=renderReasons([{field:'requirements',label:'满足任一完整原初试路线',status:'matched',children:[{label:'计算机科学与技术路线',status:'matched',children:[{label:'政治理论最低要求',status:'matched',message:'已知情况符合这项明示条件',evidence:[{url:'https://example.edu.cn/rule',quote:'总分290；政治理论50'}]}]},{label:'其他路线',status:'excluded',children:[{label:'<script>',status:'excluded',message:'存在冲突'}]}]}]);
 assert.match(html,/政治理论最低要求/);assert.match(html,/总分290；政治理论50/);assert.match(html,/reason-branch" open/);assert.match(html,/条件冲突/);assert.doesNotMatch(html,/<script>/);
 const sample={unit_id:'u',department:'学院',program_name:'专业',study_mode:'全日制',year:2026,sample_count:1,initial:{min:320,max:320}};
 const program=adaptProgram({id:'p',unit_id:'u',unit_name:'甲大学',department:'学院',name:'专业',study_mode:'全日制',subjects:[]},[],null,[sample],2026);
 assert.match(renderDetail(program,{tab:'history'}),/满分口径未注明，比较前需核对/);
});

test('source center is a first class route without introducing another primary navigation',()=>{
 assert.equal(parseRoute('#/sources?q=学费&year=2027').page,'sources');
 assert.equal(parseRoute('#/sources').id,null);
});

test('research knowledge has a dedicated route without requiring a program selection',()=>{
 assert.equal(parseRoute('#/knowledge?q=408&entry_id=k-example').page,'knowledge');
 assert.equal(parseRoute('#/knowledge').id,null);
});

test('important detail fields open the precise evidence claim with escaped identities',()=>{
 const program=adaptProgram({id:'p"<',unit_id:'u',unit_name:'甲大学',name:'专业',department:'学院',year:2027,study_mode:'全日制',subjects:[],tuition:{amount:8000,period:'year'},duration_years:3,planned_enrollment:{count:18,scope:'department_program'}});
 const html=renderDetail(program);
 for(const claim of ['catalog','tuition','duration','subjects','enrollment','eligibility']){
  assert.match(html,new RegExp('data-claim-id="'+claim+'"'));
 }
 assert.match(html,/data-action="evidence"/);
 assert.match(html,/data-program-id="p&quot;&lt;"/);
 assert.doesNotMatch(html,/data-program-id="p"</);
 assert.match(html,/data-action="sources"/);
});

test('historical rows preserve a direct evidence claim even when only one completed year exists',()=>{
 const sample={id:'stat-2026',unit_id:'u',department:'学院',program_name:'专业',study_mode:'全日制',year:2026,sample_count:2,initial:{min:320,max:350,median:335}};
 const program=adaptProgram({id:'p',unit_id:'u',unit_name:'甲大学',department:'学院',name:'专业',study_mode:'全日制',year:2027,subjects:[]},[],null,[sample],2026);
 const html=renderDetail(program,{tab:'history'});
 assert.match(html,/data-claim-id="history:stat-2026"/);
 assert.match(html,/2026/);assert.match(html,/2025/);assert.match(html,/2024/);
 assert.doesNotMatch(html,/2025[^<]*320/);
});

test('explicit department, study mode and direction scopes cannot leak into another program chart',()=>{
 const raw={id:'p',unit_id:'u',unit_name:'甲大学',department:'035计算机学院',name:'电子信息',code:'085400',direction:'01 软件工程',study_mode:'全日制',subjects:[]};
 const line={id:'line',unit_id:'u',department:'计算机学院',program_code:'085400',study_mode:'全日制',year:2026,kind:'program',total:320,evidence_status:'verified'};
 const badLines=[{...line,id:'department',department:'软件学院'},{...line,id:'mode',study_mode:'非全日制'},{...line,id:'direction',direction:'02 网络安全'}];
 const sample={id:'sample',unit_id:'u',department:'计算机学院',program_code:'085400',study_mode:'全日制',year:2026,direction:'02 网络安全',sample_count:3,initial:{min:320,max:350}};
 const item=adaptProgram(raw,[line,...badLines],null,[sample],2026);
 assert.deepEqual(item.scoreLines.map(row=>row.id),['line']);
 assert.equal(item.history.length,0);
});

test('school discipline cutoffs preserve unknown applicability and exclude different degree or discipline scopes',()=>{
 const raw={id:'p',unit_id:'u',name:'电子信息',code:'085400',study_mode:'全日制',subjects:[]};
 const line={id:'school-08',unit_id:'u',year:2026,kind:'university',discipline_code:'08',degree_type:'学术学位',total:290,evidence_status:'verified'};
 const unknown=adaptProgram(raw,[line],null,[],2026);
 assert.equal(unknown.scoreLines.length,0);assert.equal(unknown.referenceScoreLines.length,1);
 assert.equal(unknown.referenceScoreLines[0].applicability,'unknown');assert.match(unknown.referenceScoreLines[0].applicability_note,/适用/);
 assert.equal(adaptProgram({...raw,degree_type:'专业学位'},[line],null,[],2026).referenceScoreLines.length,0);
 assert.equal(adaptProgram({...raw,code:'030100'},[line],null,[],2026).referenceScoreLines.length,0);
 assert.equal(adaptProgram({...raw,degree_type:'学术学位'},[line],null,[],2026).scoreLines.length,1);
 const plan={...line,degree_type:undefined,plan_type:'普通计划'};
 assert.equal(adaptProgram(raw,[plan],null,[],2026).referenceScoreLines.length,1);
 assert.equal(adaptProgram({...raw,plan_type:'专项计划'},[plan],null,[],2026).referenceScoreLines.length,0);
 const engineering={...line,discipline_code:undefined,degree_type:'专业学位',code_prefixes:['085','086']};
 assert.equal(adaptProgram({...raw,code:'030100'},[engineering],null,[],2026).referenceScoreLines.length,0);
 assert.equal(adaptProgram({...raw,code:'085400'},[engineering],null,[],2026).referenceScoreLines.length,1);
 assert.equal(adaptProgram({...raw,code:'086000',degree_type:'专业学位'},[engineering],null,[],2026).scoreLines.length,1);
});

test('unverified history and cutoffs retain evidence context without presenting their numbers as facts',()=>{
 const raw={id:'p',unit_id:'u',unit_name:'甲大学',department:'学院',name:'专业',code:'081200',study_mode:'全日制',subjects:[]};
 const sample={id:'bad',unit_id:'u',department:'学院',program_code:'081200',study_mode:'全日制',year:2026,sample_count:3,initial:{min:360,max:410,median:380},evidence_status:'needs_review',evidence_note:'原文未通过核对'};
 const line={id:'bad-line',unit_id:'u',program_code:'081200',kind:'program',year:2026,total:350,evidence_status:'needs_review',evidence_note:'原文哈希已改变'};
 const item=adaptProgram(raw,[line],null,[sample],2026);
 assert.equal(item.history[0].min,null);assert.equal(item.history[0].count,null);
 assert.equal(item.history[0].statistic.initial.min,360);
 assert.equal(item.scoreLines.length,0);assert.equal(item.unverifiedScoreLines.length,1);
 const html=renderDetail(item,{tab:'history'});assert.match(html,/依据待核/);assert.match(html,/原文哈希已改变/);assert.doesNotMatch(html,/>360—410</);
});

test('different research direction codes never merge even when their names match',()=>{
 const raw={id:'p',unit_id:'u',unit_name:'甲大学',department:'学院',name:'专业',code:'081200',direction:'01 软件工程',study_mode:'全日制',subjects:[]};
 const sample={id:'other',unit_id:'u',department:'学院',program_code:'081200',direction:'02 软件工程',study_mode:'全日制',year:2026,sample_count:3,initial:{min:320,max:350}};
 const line={id:'other-line',unit_id:'u',program_code:'081200',kind:'program',direction:'02 软件工程',year:2026,total:320,evidence_status:'verified'};
 assert.equal(matchingStatistics(raw,[sample]).length,0);
 assert.equal(adaptProgram(raw,[line],null,[],2026).scoreLines.length,0);
});
