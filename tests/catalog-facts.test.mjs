import test from 'node:test';
import assert from 'node:assert/strict';
import {renderCatalogFacts,mountCatalogFacts} from '../static/catalog-facts.mjs';
import {renderCollectionUnits} from '../static/collection-progress.mjs';

const evidence={document_id:'source',url:'https://grad.sample.edu.cn/catalog',quote:'081200 计算机科学与技术'};
const fact={fact_id:'fact-1',unit_id:'123',unit_name:'样本大学',year:2027,code:'081200',name:'计算机科学与技术',
 direction:'01人工智能',department:null,study_mode:null,matching_allowed:false,fact_status:'verified_original_fact',
 missing_fields:['study_mode','admission_method'],subjects:[{code:'201',name:'英语（一）',exam_unit:2}],
 exam_groups:[{exam_unit:2,selection:'one_of',options:[{code:'201',name:'英语（一）'},{code:'203',name:'日语'}]}],
 field_evidence:{code:[evidence],name:[evidence],direction:[{...evidence,quote:'01人工智能'}],subjects:[{...evidence,quote:'②201英语（一）或203日语'}]}};
const payload=(facts=[fact],offset=0,total=facts.length)=>({unit_id:'123',unit_name:'样本大学',year:2027,facts,
 total,offset,limit:6,has_more:offset+6<total,matching_allowed:false,catalog_complete:false});
const settle=()=>new Promise(resolve=>setImmediate(resolve));

function harness(api){
 const events=new Map(),content={innerHTML:''},input={value:''};
 const node={innerHTML:'',querySelector:selector=>selector==='[data-facts-content]'?content:input,
  addEventListener:(name,fn)=>events.set(name,fn),removeEventListener:name=>events.delete(name)};
 const cleanup=mountCatalogFacts(node,{api,unitId:'123',year:2027});
 const click=(offset,retry=false)=>{const button={disabled:false,dataset:{factOffset:String(offset)},
  closest:selector=>selector==='[data-fact-retry]'?(retry?button:null):selector==='[data-fact-offset]'&&!retry?button:null};
  events.get('click')?.({target:button});};
 return {node,content,input,events,cleanup,click};
}

test('original rows show exact year, unknown mode, OR choices and field-level original quotes',()=>{
 const html=renderCatalogFacts(payload());
 assert.match(html,/2027/);assert.match(html,/原文表格行/);assert.match(html,/目录信息待完善/);
 assert.match(html,/学习方式.*待核实/);assert.match(html,/考试方式.*待核实/);assert.match(html,/任选一门/);
 assert.match(html,/201.*英语（一）.*或.*203.*日语/);assert.match(html,/②201英语（一）或203日语/);
 assert.match(html,/href="https:\/\/grad.sample.edu.cn\/catalog"/);assert.match(html,/逐项查看原文依据/);
 assert.doesNotMatch(html,/收藏|加入对比|换科|报名概率|data-program|data-match/);
});

test('all untrusted text is escaped and unsafe source links never become anchors',()=>{
 const html=renderCatalogFacts(payload([{...fact,name:'<img src=x onerror=alert(1)>',notes:'<script>bad()</script>',
  field_evidence:{name:[{...evidence,url:'javascript:alert(1)',quote:'<iframe>bad</iframe>'}],
   subjects:[{...evidence,url:'https://user:secret@example.com/',quote:'保留逐字内容'}]}}]));
 assert.match(html,/&lt;img/);assert.match(html,/&lt;script/);assert.match(html,/&lt;iframe/);
 assert.doesNotMatch(html,/<img|<script|<iframe|href="javascript:|href="https:\/\/user:/);
});

test('mounted facts require the requested school and year, and never render oversized or matching-enabled data',async()=>{
 for(const data of [{...payload(),unit_id:'456'},{...payload(),year:2028},
  {...payload(),facts:Array.from({length:7},(_,i)=>({...fact,fact_id:String(i)}))},
  payload([{...fact,matching_allowed:true}])]){
  const view=harness(async()=>data);await settle();assert.match(view.content.innerHTML,/需要重新核验/);
  assert.doesNotMatch(view.content.innerHTML,/class="catalog-fact-card"/);view.cleanup();
 }
});

test('disposing an expanded unit aborts fetch and ignores late responses',async()=>{
 const requests=[];const view=harness((path,options)=>new Promise(resolve=>requests.push({path,options,resolve})));
 assert.match(requests[0].path,/unit_id=123&year=2027/);view.cleanup();
 assert.equal(requests[0].options.signal.aborted,true);const before=view.content.innerHTML;
 requests[0].resolve(payload());await settle();assert.equal(view.content.innerHTML,before);assert.equal(view.events.size,0);
});

test('unit pagination carries only its scope and stale previous-page responses cannot repaint',async()=>{
 const requests=[];const view=harness((path,options)=>new Promise(resolve=>requests.push({path,options,resolve})));
 requests[0].resolve(payload([fact],0,13));await settle();view.click(6);view.click(12);
 assert.equal(requests[1].options.signal.aborted,true);assert.match(requests[2].path,/offset=12&limit=6/);
 requests[2].resolve(payload([{...fact,name:'当前页面'}],12,13));await settle();
 requests[1].resolve(payload([{...fact,name:'过期页面'}],6,13));await settle();
 assert.match(view.content.innerHTML,/当前页面/);assert.doesNotMatch(view.content.innerHTML,/过期页面/);view.cleanup();
});

test('a failed request can retry without losing the unit and empty rows never mean no admissions',async()=>{
 let calls=0;const view=harness(async()=>{if(!calls++)throw new Error('<连接失败>');return payload([]);});
 await settle();assert.match(view.content.innerHTML,/&lt;连接失败&gt;/);view.click(0,true);await settle();
 assert.match(view.content.innerHTML,/不代表该校没有招生/);assert.doesNotMatch(view.content.innerHTML,/该校无专业/);view.cleanup();
});

test('collection unit facts remain a separate optional disclosure from admitted program counts',()=>{
 const html=renderCollectionUnits([{unit_id:'123',unit_name:'样本大学',year:2027,program_count:0,fact_count:320}]);
 assert.match(html,/data-collection-facts="123"/);assert.match(html,/320 条原文表格行/);
 assert.match(html,/目录信息待完善/);assert.doesNotMatch(html,/320 条专业|查看已收录专业|data-collection-unit="123"/);
});

test('an emptied source on a later page has a zero row range and a clear route back',()=>{
 const html=renderCatalogFacts(payload([],6,0));assert.match(html,/0—0 \/ 共 0 条/);
 assert.match(html,/data-fact-offset="0"/);assert.doesNotMatch(html,/7—6|0—6/);
});
