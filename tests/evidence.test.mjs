import test from 'node:test';
import assert from 'node:assert/strict';
import {buildSourceQuery,normalizeSourceFilters,groupEvidence,formatClaimValue,scopeText} from '../static/evidence-model.mjs';
import {renderEvidence,renderSources,mountEvidence,mountSources} from '../static/evidence.mjs';

const quote={id:'e1',document_id:'d1',title:'甲大学2027年收费标准',publisher:'甲大学',url:'https://grad.example.edu.cn/fees.pdf',year:2027,page:2,quote:'学硕专业 8000',relation:'supports',verification:'exact_quote',source_group:'sha-1',checked_at:'2026-10-29T01:00:00Z'};
const payload={program:{id:'p1',unit_name:'甲大学',department:'计算机学院',name:'计算机科学与技术',code:'081200',year:2027,study_mode:'全日制'},claims:[{id:'tuition',field:'tuition',label:'年学费',value:{amount:8000,period:'year',currency:'CNY'},status:'verified',scope:{year:2027,department:'计算机学院',study_mode:'全日制'},evidence:[quote,{...quote,id:'e2',quote:'按学年缴费'}],note:'仅适用于2027参考年度。'},{id:'duration',field:'duration',label:'学制',value:null,status:'missing',evidence:[]}],notice:'只核对本地保存的官方材料。'};

test('source filters are bounded and serialize only public supported fields',()=>{
 assert.deepEqual(normalizeSourceFilters({q:' 电子信息 ',unit_id:'a',year:'2027',kind:'fees',api_key:'secret'}),{q:'电子信息',unit_id:'a',year:'2027',kind:'fees'});
 assert.equal(buildSourceQuery({q:'计算机&数学',year:2027,kind:'fees'}),'/api/catalog-sources?q=%E8%AE%A1%E7%AE%97%E6%9C%BA%26%E6%95%B0%E5%AD%A6&year=2027&kind=fees&offset=0&limit=12');
 assert.match(buildSourceQuery({unit_id:'u'},{offset:12,limit:12,revision:'pub-1'}),/offset=12&limit=12&revision=pub-1/);
 assert.deepEqual(normalizeSourceFilters({q:[],year:1900,kind:'invented',unit_id:{}}),{q:'',unit_id:'',year:'',kind:''});
 assert.equal(normalizeSourceFilters({q:'a'.repeat(500)}).q.length,120);
});

test('one source file with different quotes or URLs remains one evidence group',()=>{
 const groups=groupEvidence([quote,{...quote,id:'e2',url:'https://mirror.example.edu.cn/copy.pdf',quote:'按学年缴费'},{...quote,id:'e3',document_id:'d2',source_group:'sha-2'}]);
 assert.equal(groups.length,2);assert.equal(groups[0].items.length,2);assert.equal(groups[0].urls.length,2);
 assert.equal(groupEvidence([quote,quote]).length,1);assert.equal(groupEvidence([quote,quote])[0].items.length,1);
 assert.equal(groupEvidence(null).length,0);
});

const structured={...quote,kind:'structured_fields',verification:'structured_fields',quote:'',page:null,
 original_format:'structured_fields',field_path:'mixin.data.kskm[0].km1Vo',
 structured_fields:{kskmdm:'101',kskmmc:'思想政治理论'},
 url:'https://yz.chsi.com.cn/zsml/yjfxdetail?id=public-direction',
 detail_url:'https://yz.chsi.com.cn/zsml/yjfxdetail?id=public-direction'};

test('a verified public form links to the actual GET query page and retains the POST fact as escaped text',()=>{
 const api='https://yanzhao.ujn.edu.cn/zsZsmlJbxxMng/queryZsmlKskmList';
 const page='https://yanzhao.ujn.edu.cn/zsZsmlJbxxMng/enterViewZsmlKskm?zsmlbh=public';
 const source={...structured,url:api,request_url:api,request_method:'POST',detail_url:page,
  detail_url_kind:'query_page',field_path:'$[0]',structured_fields:{kscc:'1',kskm:'101思想政治理论'}};
 const html=renderEvidence({program:{unit_name:'济南大学',code:'085404'},claims:[{id:'subjects',label:'初试科目',status:'verified',value:[],evidence:[source]}]});
 assert.match(html,/打开官方查询页/);
 assert.ok(html.includes(`href="${page}"`));
 assert.ok(!html.includes(`href="${api}"`));
 assert.match(html,/原始请求/);assert.match(html,/POST/);
 assert.match(html,/queryZsmlKskmList/);assert.doesNotMatch(html,/<blockquote>/);
});

test('structured references deduplicate by field path and original value without swallowing empty-quote fields',()=>{
 const groups=groupEvidence([structured,{...structured,structured_fields:{kskmmc:'思想政治理论',kskmdm:'101'}},
  {...structured,field_path:'mixin.data.kskm[0].km2Vo'},
  {...structured,structured_fields:{kskmdm:'201',kskmmc:'英语（一）'}}]);
 assert.equal(groups.length,1);assert.equal(groups[0].items.length,3);
});

test('structured originals show the real field path and escaped raw values without invented prose quotes',()=>{
 const source={...structured,field_path:'detail.labels.<script>field</script>',
  structured_fields:{'招生单位':'(10001)北京大学','备注':'<script>globalThis.injected=true</script><img src=x onerror=alert(1)>',
   '科目':[{code:'101',name:'思想政治理论'}]}};
 const html=renderEvidence({...payload,claims:[{...payload.claims[0],evidence:[source]}]});
 assert.match(html,/原文结构化字段/);assert.match(html,/字段路径/);assert.match(html,/detail.labels.&lt;script&gt;/);
 assert.match(html,/kskm|科目/);assert.match(html,/北京大学/);assert.match(html,/思想政治理论/);assert.match(html,/&lt;script&gt;globalThis/);
 assert.match(html,/<code>\[<br>/);
 assert.match(html,/https:\/\/yz.chsi.com.cn\/zsml\/yjfxdetail\?id=public-direction/);
 assert.doesNotMatch(html,/<blockquote>|<script|<img|该项没有可展示的原文摘录|来源仍需核验/);
});

test('structured values never turn dangerous detail URLs into links or unverified fields into adopted evidence',()=>{
 for(const detail_url of ['javascript:alert(1)','data:text/html,<script>x</script>','https://user:secret@example.com/']){
  const html=renderEvidence({...payload,claims:[{...payload.claims[0],evidence:[{...structured,detail_url}]}]});
  assert.doesNotMatch(html,/href="(?:javascript:|data:|https:\/\/user:)/);
  assert.match(html,/原文链接待补齐/);
 }
 const html=renderEvidence({...payload,claims:[{...payload.claims[0],status:'needs_review',evidence:[{
  ...structured,verification:'unavailable',structured_fields:{secret:'不应作为已核对原值展示'}}]}]});
 assert.doesNotMatch(html,/不应作为已核对原值展示/);assert.match(html,/留档暂不可核对/);
});

test('source center preserves published-snapshot wording while marking the structured original format',()=>{
 const html=renderSources(sourceResponse([{...structured,id:'structured-doc',kind:'catalog',verification:'published_snapshot'}]));
 assert.match(html,/原文结构化字段/);assert.match(html,/上次发布留档/);
 assert.doesNotMatch(html,/保存原文已核对|刚刚核验|当前官网访问正常/);
});

test('claims preserve annual and total units, unknown values and actual scopes',()=>{
 assert.equal(formatClaimValue(payload.claims[0]),'8,000 元 / 年');
 assert.equal(formatClaimValue({field:'tuition',value:{amount:0,period:'total'}}),'0 元 / 全程');
 assert.equal(formatClaimValue({field:'duration',value:3}),'3 年');
 assert.equal(formatClaimValue({field:'subjects',value:[{code:'201',name:'英语一',exam_unit:2}]}),'201 英语一');
 assert.equal(formatClaimValue({field:'subjects',value:{exam_combinations:[{},{}]}}),'原文保留 2 组科目组合 · 换科计算待核');
 assert.equal(formatClaimValue({field:'tuition',value:null}),'尚未收录');
 assert.match(scopeText(payload.claims[0].scope),/2027 年.*计算机学院.*全日制/);
 assert.equal(formatClaimValue({field:'enrollment',value:{count:18,scope:'program'}}),'18 人 · 共享计划口径');
 assert.match(formatClaimValue({field:'history',value:{initial:{min:330,max:400},sample_count:12}}),/330—400.*12 人/);
 assert.equal(formatClaimValue({field:'score',value:{total:330}}),'330 分');
 assert.match(scopeText({year:2026,admission_source:'transfer',plan_type:'unmarked'}),/计划类别未单列.*调剂公示样本/);
 const scoped=scopeText({year:2026,degree_type:'学术学位',discipline_code:'08',discipline_name:'工学',code_prefixes:['08'],plan_type:'ordinary_base_line'});
 assert.match(scoped,/学术学位.*工学.*08.*08\*.*学校公布的基本线/);assert.doesNotMatch(scoped,/ordinary_base_line/);
});

test('field evidence contains full quote, source URL, page and distinct document count',()=>{
 const html=renderEvidence(payload,{claimId:'tuition'});
 assert.match(html,/8,000 元 \/ 年/);assert.match(html,/学硕专业 8000/);assert.match(html,/按学年缴费/);
 assert.match(html,/https:\/\/grad.example.edu.cn\/fees.pdf/);assert.match(html,/第 2 页/);assert.match(html,/1 组原始文件/);
 assert.match(html,/原文已核对/);assert.match(html,/留档读取/);assert.doesNotMatch(html,/可信度.*%|当前官网访问正常/);
 assert.match(html,/name="evidence-claim"/);assert.match(html,/data-ev-action="close"/);
 assert.ok(html.indexOf('class="ev-original"')<html.indexOf('class="ev-excerpt"'),'official source entry precedes all excerpts');
 assert.equal(html.match(/class="ev-original"/g).length,1);assert.match(html,/fees.pdf#page=2/);
});

test('conflicting and missing claims remain explicit and unsupported URLs never become links',()=>{
 const hostile={...payload,claims:[{...payload.claims[0],status:'conflict',label:'<script>x</script>',evidence:[{...quote,url:'javascript:alert(1)',quote:'<img src=x onerror=alert(1)>'}]}]};
 const html=renderEvidence(hostile,{claimId:'tuition'});
 assert.match(html,/存在冲突/);assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|<script|href="javascript:/);assert.match(html,/原文链接待补齐/);
 const missing=renderEvidence(payload,{claimId:'duration'});assert.match(missing,/尚未收录/);assert.match(missing,/没有可展示的原文依据/);
 const invalid=renderEvidence({...payload,claims:[{...payload.claims[0],evidence:[{...quote,verification:'invalid_source'}]}]});assert.match(invalid,/来源校验未通过/);
 const unknown=renderEvidence({...payload,claims:[{...payload.claims[0],applicability:'unknown',evidence:[{...quote,note:'学院专业可提高要求'}]}]});assert.match(unknown,/本专业适用范围待核验/);assert.match(unknown,/学院专业可提高要求/);
});

test('source center filters and related directions are readable without inventing verification',()=>{
 const html=renderSources({sources:[{...quote,id:'d1',kind:'fees',unit_id:'u',related_program_ids:['p1']}],total:1,notice:'全国目录尚未齐全。'},{filters:{q:'<b>',year:'2027'},units:[{id:'u',name:'甲大学'}],programs:[payload.program]});
 assert.match(html,/来源与核验/);assert.match(html,/甲大学2027年收费标准/);assert.match(html,/计算机科学与技术/);assert.match(html,/全国目录尚未齐全/);assert.match(html,/name="source-q"/);
 assert.match(html,/value="&lt;b&gt;"/);assert.doesNotMatch(html,/当前官网访问正常|可信度.*%/);
 assert.match(renderSources(null,{filters:{q:'计算机 '}}),/value="计算机 "/);
 assert.match(renderEvidence({...payload,claims:[{...payload.claims[0],evidence:[{...quote,quote:'',verification:'reference_link',relation:'applicability',note:'章程引用该收费文件'}]}]}),/章程引用该收费文件/);
});

test('source cards target actual related claims and distinguish replaced or pending versions',()=>{
 const source={...quote,id:'d1',kind:'charter',version_state:'superseded',related_program_ids:['p1'],related_claims:[{program_id:'p1',claim_id:'duration',label:'学制'}]};
 const html=renderSources({sources:[source],total:1},{programs:[payload.program]});
 assert.match(html,/data-claim-id="duration"/);assert.doesNotMatch(html,/data-claim-id="eligibility"/);assert.match(html,/已被替代/);
 assert.match(renderEvidence({...payload,claims:[{...payload.claims[0],evidence:[{...quote,version_state:'superseded'}]}]}),/已被替代/);
 assert.match(renderSources({sources:[{...source,version_state:'pending_review'}]}),/新版本待复核/);
 const history=renderSources({sources:[{...source,kind:'admission_results',related_claims:[{program_id:'p1',claim_id:'history:2026:a',label:'2026公示样本'}]}]});
 assert.match(history,/data-claim-id="history:2026:a"/);
 assert.match(renderSources({sources:[{...source,kind:'catalog',version_state:'current'}]}),/当前目录版本/);
 const fee=renderSources({sources:[{...source,kind:'fees',version_state:'current'}]});assert.match(fee,/当前留档版本/);assert.doesNotMatch(fee,/当前目录版本/);
});

test('out-of-scope original quotes remain readable and can never be labelled direct support',()=>{
 for(const flags of [{verification:'out_of_scope',relation:'supports'},{verification:'exact_quote',relation:'out_of_scope'}]){
  const html=renderEvidence({...payload,claims:[{...payload.claims[0],status:'needs_review',source_count:0,evidence:[{...quote,...flags,note:'该条款只适用于另一个学院。'}]}]});
  assert.match(html,/不适用于当前结论/);assert.match(html,/学硕专业 8000/);assert.match(html,/该条款只适用于另一个学院/);assert.match(html,/https:\/\/grad.example.edu.cn\/fees.pdf/);
  assert.doesNotMatch(html,/直接依据|直接支持|已采用/);
 }
});

const settle=()=>new Promise(resolve=>setImmediate(resolve));
const sourceResponse=(sources=[],total=sources.length,offset=0,revision='pub-1')=>({sources,total,offset,limit:12,revision,verification_mode:'published_snapshot'});
function harness(mount,options,document={activeElement:null,querySelector:()=>({})}){
 const events=new Map();
 const node={ownerDocument:document,innerHTML:'',classList:{add(){},remove(){}},setAttribute(){},contains:()=>true,querySelectorAll:()=>[],querySelector:()=>null,addEventListener:(name,fn)=>events.set(name,fn),removeEventListener:name=>events.delete(name)};
 const cleanup=mount(node,options);
 const change=(name,value)=>events.get('change')?.({target:{name,value}});
 const click=(action,extra={})=>{const button={dataset:{evAction:action,...extra},closest(){return this;}};events.get('click')?.({target:button,preventDefault(){},stopPropagation(){}});};
 return {node,events,cleanup,change,click};
}

test('closing evidence aborts loading and a late result cannot repaint a disposed dialog',async()=>{
 const requests=[];const p=harness(mountEvidence,{api:(path,options)=>new Promise(resolve=>requests.push({path,options,resolve})),programId:'p1',claimId:'tuition'});
 assert.equal(requests[0].path,'/api/evidence?program_id=p1');assert.match(p.node.innerHTML,/正在核对/);
 p.cleanup();assert.equal(requests[0].options.signal.aborted,true);const before=p.node.innerHTML;
 requests[0].resolve(payload);await settle();assert.equal(p.node.innerHTML,before);assert.equal(p.events.size,0);
});

test('field switches do not refetch and close is delegated to the owning dialog',async()=>{
 let calls=0,closed=0;const p=harness(mountEvidence,{api:async()=>{calls++;return payload;},programId:'p1',claimId:'tuition',onClose:()=>closed++});
 await settle();assert.match(p.node.innerHTML,/学硕专业 8000/);p.change('evidence-claim','duration');assert.match(p.node.innerHTML,/没有可展示的原文依据/);assert.equal(calls,1);
 p.click('close');assert.equal(closed,1);p.cleanup();
});

test('source filters abort old requests and stale responses never replace a new scope',async()=>{
 const requests=[];const p=harness(mountSources,{api:(path,options)=>new Promise(resolve=>requests.push({path,options,resolve})),units:[]});
 p.change('source-unit','u2');assert.equal(requests[0].options.signal.aborted,true);assert.match(requests[1].path,/unit_id=u2/);
 requests[0].resolve(sourceResponse([{...quote,title:'旧范围不得显示'}]));await settle();assert.doesNotMatch(p.node.innerHTML,/旧范围不得显示/);
 requests[1].resolve(sourceResponse([{...quote,title:'当前单位来源'}]));await settle();assert.match(p.node.innerHTML,/当前单位来源/);p.cleanup();
});

test('failed source loading is readable and explicit retry can recover',async()=>{
 let calls=0;const p=harness(mountSources,{api:async()=>{if(!calls++)throw new Error('连接失败');return sourceResponse();}});
 await settle();assert.match(p.node.innerHTML,/连接失败/);p.click('retry');await settle();assert.match(p.node.innerHTML,/没有匹配到已收录来源/);assert.equal(calls,2);p.cleanup();
});

test('Chinese composition is not rerendered or queried until input is committed',async()=>{
 const calls=[];const p=harness(mountSources,{api:(path,options)=>new Promise(resolve=>calls.push({path,options,resolve}))});
 p.events.get('compositionstart')?.();assert.equal(calls[0].options.signal.aborted,true);
 const before=p.node.innerHTML;p.events.get('input')?.({target:{name:'source-q',value:'计算'}});assert.equal(p.node.innerHTML,before);assert.equal(calls.length,1);
 p.events.get('compositionend')?.({target:{name:'source-q',value:'计算机'}});await new Promise(resolve=>setTimeout(resolve,300));
 assert.equal(calls.length,2);assert.match(calls[1].path,/%E8%AE%A1%E7%AE%97%E6%9C%BA/);p.cleanup();
});

test('source pagination is bounded and query state survives remount without leaking extra fields',async()=>{
 const navigation={location:{hash:'#/sources?unit_id=u&year=2027&page=2&api_key=secret'},history:{state:null,replaceState(_state,_title,hash){navigation.location.hash=hash;}}};
 const document={activeElement:null,querySelector:()=>({}),defaultView:navigation};
 const sources=Array.from({length:25},(_,index)=>({...quote,id:'d'+index,title:'来源 '+index}));
 const paths=[];const options={api:async path=>{paths.push(path);const offset=Number(new URL(path,'https://local').searchParams.get('offset'));return sourceResponse(sources.slice(offset,offset+12),25,offset);}};
 const p=harness(mountSources,options,document);await settle();assert.match(paths[0],/unit_id=u&year=2027/);assert.match(p.node.innerHTML,/第 2 \/ 3 页/);assert.doesNotMatch(navigation.location.hash,/api_key/);
 assert.match(paths[0],/offset=12&limit=12/);assert.match(p.node.innerHTML,/来源 12/);assert.doesNotMatch(p.node.innerHTML,/<h2>来源 0<\/h2>/);
 p.click('next');await settle();assert.match(p.node.innerHTML,/第 3 \/ 3 页/);assert.match(paths[1],/offset=24&limit=12&revision=pub-1/);p.click('next');await settle();assert.match(p.node.innerHTML,/第 3 \/ 3 页/);assert.equal(paths.length,2);assert.match(navigation.location.hash,/page=3/);
 p.cleanup();const restored=harness(mountSources,options,document);await settle();assert.match(restored.node.innerHTML,/第 3 \/ 3 页/);restored.change('source-kind','fees');await settle();assert.doesNotMatch(navigation.location.hash,/page=/);assert.match(restored.node.innerHTML,/第 1 \/ 3 页/);restored.cleanup();
});

test('source ready callback runs after the current successful render only',async()=>{
 const pending=[],ready=[];const p=harness(mountSources,{api:(path,options)=>new Promise(resolve=>pending.push({path,options,resolve})),onReady:data=>ready.push(data.total)});
 p.change('source-kind','fees');pending[0].resolve(sourceResponse([],999));await settle();assert.deepEqual(ready,[]);
 pending[1].resolve(sourceResponse());await settle();assert.deepEqual(ready,[0]);p.cleanup();
});

test('server source pages retain published status and never claim a live original readback',()=>{
 const html=renderSources(sourceResponse([{...quote,id:'d',kind:'catalog',verification:'published_snapshot'}],37,12),{page:2});
 assert.match(html,/第 2 \/ 4 页/);assert.match(html,/37<\/strong> 条来源记录/);assert.match(html,/上次发布留档/);
 assert.doesNotMatch(html,/37.*组原始文件|正在核对已收录/);
});

test('a legacy whole-array response is rejected instead of silently becoming page one',async()=>{
 const p=harness(mountSources,{api:async()=>({sources:Array.from({length:25},(_,id)=>({...quote,id:'d'+id})),total:25})});
 await settle();assert.match(p.node.innerHTML,/分页|响应不完整/);assert.doesNotMatch(p.node.innerHTML,/ev-library-grid/);p.cleanup();
});

test('source page responses cannot repaint a newer page or disposed center',async()=>{
 const pending=[];const p=harness(mountSources,{api:(path,options)=>new Promise(resolve=>pending.push({path,options,resolve}))});
 pending[0].resolve(sourceResponse(Array.from({length:12},(_,id)=>({...quote,id:'d'+id,title:'第一页'})),25));await settle();p.click('next');
 p.change('source-year','2026');assert.equal(pending[1].options.signal.aborted,true);
 pending[1].resolve(sourceResponse([{...quote,title:'旧第二页'}],25,12));await settle();assert.doesNotMatch(p.node.innerHTML,/旧第二页/);
 pending[2].resolve(sourceResponse([{...quote,title:'新年度第一条'}],1));await settle();assert.match(p.node.innerHTML,/新年度第一条/);
 p.click('retry');const before=p.node.innerHTML;p.cleanup();pending[3].resolve(sourceResponse([{...quote,title:'销毁后结果'}]));await settle();assert.equal(p.node.innerHTML,before);
});

test('a changed publication is explicit and retry starts at page one of the new revision',async()=>{
 const paths=[],first=Array.from({length:12},(_,id)=>({...quote,id:'d'+id}));
 const p=harness(mountSources,{api:async path=>{paths.push(path);if(paths.length===1)return sourceResponse(first,25);if(paths.length===2){const error=new Error('资料已更新');error.code='revision_changed';throw error;}return sourceResponse([{...quote,title:'新版本来源'}],1,0,'pub-2');}});
 await settle();p.click('next');await settle();assert.match(p.node.innerHTML,/资料已更新/);assert.match(p.node.innerHTML,/读取最新资料/);
 p.click('retry');await settle();assert.match(paths[2],/offset=0&limit=12/);assert.doesNotMatch(paths[2],/revision=/);assert.match(p.node.innerHTML,/新版本来源/);p.cleanup();
});

test('an out-of-range bookmarked page is fetched again at the valid last server page',async()=>{
 const paths=[],navigation={location:{hash:'#/sources?page=99'},history:{replaceState(_s,_t,hash){navigation.location.hash=hash;}}};
 const p=harness(mountSources,{api:async path=>{paths.push(path);const offset=Number(new URL(path,'https://local').searchParams.get('offset'));return sourceResponse(offset===24?[{...quote,title:'最后一条'}]:[],25,offset);}}, {activeElement:null,querySelector:()=>({}),defaultView:navigation});
 await settle();await settle();assert.equal(paths.length,2);assert.match(paths[0],/offset=1176/);assert.match(paths[1],/offset=24/);assert.match(p.node.innerHTML,/最后一条/);assert.match(p.node.innerHTML,/第 3 \/ 3 页/);assert.match(navigation.location.hash,/page=3/);p.cleanup();
});

test('related evidence and pagination keyboard focus preserve source context',async()=>{
 const focused=[],opened=[],first=Array.from({length:12},(_,id)=>({...quote,id:'d'+id}));
 const p=harness(mountSources,{api:async path=>{const offset=Number(new URL(path,'https://local').searchParams.get('offset'));return sourceResponse(offset?[{...quote,title:'最后一条'}]:first,13,offset);},onOpenEvidence:(...args)=>opened.push(args)});
 p.node.querySelector=selector=>selector.includes('data-ev-action')?{focus:()=>focused.push(selector)}:null;
 await settle();p.click('open-evidence',{programId:'not-cached',claimId:'tuition'});assert.deepEqual(opened,[['not-cached','tuition','pub-1']]);
 p.click('next');await settle();assert.ok(focused.some(selector=>selector.includes('next')));p.cleanup();
});

test('field evidence accepts a pinned source revision without changing the original API',async()=>{
 const paths=[];const p=harness(mountEvidence,{api:async path=>{paths.push(path);return payload;},programId:'p1',revision:'pub-1'});
 await settle();assert.equal(paths[0],'/api/evidence?program_id=p1&revision=pub-1');p.cleanup();
});
