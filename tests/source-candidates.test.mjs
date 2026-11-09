import test from 'node:test';
import assert from 'node:assert/strict';
import {renderAgent,mountAgent} from '../static/agent.mjs';
import {renderKnowledge,mountKnowledge} from '../static/knowledge.mjs';

const entryId='k_'+'a'.repeat(24),segmentId='sg_'+'b'.repeat(32),otherSegment='sg_'+'c'.repeat(32);
const ref={entry_id:entryId,segment_id:segmentId,field:'exams',quote:'101思想政治理论；201英语一',original_url:'https://example.edu.cn/catalog'};
const program={id:'tp_'+'1'.repeat(24),candidate_origin:'official_original',verification_mode:'research_original',candidate_ref:{kind:'research',id:'tp_'+'1'.repeat(24),research_id:'r_fixture'},validation:{state:'computable'},unit_id:'u',unit_name:'样本大学',name:'计算机',code:'081200',year:2027,eligibility:{status:'unknown'},subjects:[{code:'201',name:'英语一',exam_unit:2,scope:'national'}],evidence_refs:[ref]};
const task=report=>({id:'r_fixture',status:'completed',query:'测试研究',report});
const knowledge={id:entryId,title:'<img src=x onerror=bad()>',unit_id:'u',unit_name:'样本大学',year:2027,status:'search_lead',original_available:false,quality:{version:1,state:'pending',reusable:false},validity:'active',url:'https://example.edu.cn/catalog'};
const knowledgeState=detail=>({loadStatus:'ready',entries:[detail],total:1,filters:{quality:'pending'},detailId:detail.id,detailStatus:'ready',detail});

test('original candidates share school grouping but never expose catalog or old save actions',()=>{
 const html=renderAgent({task:task({candidates:[program,{id:'formal',unit_id:'u',unit_name:'样本大学',name:'正式方向'}]})});
 assert.match(html,/1 所学校 · 2 个方向/);assert.match(html,/本轮从原文整理/);assert.match(html,/资格待核/);assert.match(html,/查看依据/);assert.match(html,/科目与字段/);
 assert.ok(!html.includes(`data-agent-save="${program.id}"`));assert.ok(!html.includes(`data-agent-program="${program.id}"`));
 assert.match(html,/data-agent-save="formal"/);assert.match(html,/data-agent-program="formal"/);
 assert.match(html,new RegExp(`data-agent-knowledge="${entryId}"[^>]*data-agent-knowledge-segment="${segmentId}"`));
 assert.ok(!html.includes('2 个已收录方向'));
});

test('reason links use their own evidence segment and never a same-source first segment',()=>{
 const reason={message:'科目已有原文',evidence_refs:[ref]},report={candidates:[program],algorithm:{matching:{matches:[{program,reasons:[reason]}]}},evidence:[{id:'source',knowledge_id:entryId,segments:[{id:otherSegment}]}]};
 const html=renderAgent({task:task(report),tab:'methods'});
 assert.match(html,new RegExp(`data-agent-knowledge="${entryId}"[^>]*data-agent-knowledge-segment="${segmentId}"`));assert.ok(!html.includes(`data-agent-knowledge-segment="${otherSegment}"`));
 const fallback=renderAgent({task:task({candidates:[{...program,evidence_refs:[{original_url:'https://example.edu.cn/actual'}]}]})});
 assert.match(fallback,/href="https:\/\/example.edu.cn\/actual"/);
 const missing=renderAgent({task:task({candidates:[{...program,evidence_refs:[]}]})});
 assert.match(missing,/依据定位待补齐/);assert.ok(!missing.includes('data-agent-knowledge="'));
});

test('projected reasons use only the matching decision evidence and choices remain explicit',()=>{
 const plain={field:'subjects',message:'科目备选',label:'考试科目'},unknown={field:'eligibility',message:'资格待核',label:'报考资格'};
 const report={candidates:[{...program,subjects:[{code:'201',name:'英语一',exam_unit:2,choice_group:'exam-unit-2'},{code:'204',name:'英语二',exam_unit:2,choice_group:'exam-unit-2'}]}],algorithm:{matching:{matches:[{program,reasons:[plain,unknown]}]}},decisions:[{candidate_id:program.id,reasons:[{...plain,evidence_refs:[ref]},{...unknown,evidence_refs:[]}],evidence_refs:[ref]}]};
 const methods=renderAgent({task:task(report),tab:'methods'});assert.match(methods,new RegExp(`data-agent-knowledge-segment="${segmentId}"`));assert.match(methods,/<li>资格待核<\/li>/);
 const overview=renderAgent({task:task(report)});assert.match(overview,/201 英语一 或 204 英语二/);
});

test('stale temporary evidence and HTML metadata stay visibly unconfirmed and escaped',()=>{
 const html=renderAgent({task:task({candidates:[{...program,name:'<script>bad()</script>',validation:{state:'stale'}}]})});
 assert.match(html,/依据已失效/);assert.match(html,/资格待核/);assert.ok(!html.includes('<script>bad()'));
});

test('knowledge inventory distinguishes metadata, original, locatable text and unknown semantic state',()=>{
 const html=renderKnowledge(knowledgeState(knowledge));
 for(const label of ['来源信息','原文','可定位片段','语义索引','待查询','让 Agent 补证'])assert.ok(html.includes(label),label);
 assert.match(html,/data-knowledge-action="research"/);assert.ok(!html.includes('<img src=x'));
 const accepted={...knowledge,status:'official_original_read',quality:{version:1,state:'accepted',reusable:true},index_state:'succeeded',extraction:{extraction_status:'complete',segment_count:4},usable:true};
 const missing=renderKnowledge(knowledgeState(accepted));assert.ok(!missing.includes('可定位片段：可用'));
 const ready=renderKnowledge(knowledgeState({...accepted,original_available:true}));assert.match(ready,/可定位片段：可用/);assert.ok(!ready.includes('语义索引：可用'));
});

function dom(){
 const handlers=new Map(),document={activeElement:null,querySelector:()=>null,createElement:()=>({dataset:{}}),head:{append(){}}};
 const container={ownerDocument:document,innerHTML:'',contains:node=>!!node,querySelector:()=>null,querySelectorAll:()=>[],addEventListener:(name,fn)=>handlers.set(name,fn),removeEventListener(){}};
 return {container,fire:(name,event)=>handlers.get(name)?.(event)};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const blockedKnowledge=[
 ['revoked',{validity:'revoked'}],['conflict',{validity:'conflict'}],['superseded',{validity:'superseded'}],
 ['excluded',{quality:{version:1,state:'excluded',reusable:false}}],['private notice',{content_notice:'仅保存来源，不处理逐人名单'}],['metadata only',{text_origin:'metadata_only'}],
 ['invalid id',{id:'k_bad'}],['missing unit',{unit_id:''}],['unsafe url',{url:'javascript:alert(1)'}],['credential url',{url:'https://user:password@example.edu.cn/source'}]
];

test('unavailable knowledge hides the research action and explains how to choose usable evidence',()=>{
 for(const [label,patch] of blockedKnowledge){
  const html=renderKnowledge(knowledgeState({...knowledge,...patch}));assert.ok(!html.includes('data-knowledge-action="research"'),label);assert.match(html,/选择.*有效.*资料|恢复.*有效.*版本/,label);
 }
});

test('forged knowledge research clicks cannot bypass current source eligibility',async()=>{
 for(const [label,patch] of blockedKnowledge){
  const blocked={...knowledge,...patch},ui=dom(),seeds=[],cleanup=mountKnowledge(ui.container,{initialEntryId:blocked.id,onResearch:seed=>seeds.push(seed),api:async path=>path.includes('entry_id=')?{entry:blocked}:{entries:[blocked],total:1}});
  await settle();ui.fire('click',{target:{closest:()=>({dataset:{knowledgeAction:'research'},disabled:false})}});assert.deepEqual(seeds,[],label);cleanup();
 }
});

test('knowledge補证 hands off a draft only and cannot start research on open',async()=>{
 const ui=dom(),calls=[],seeds=[];
 const cleanup=mountKnowledge(ui.container,{initialEntryId:entryId,onResearch:seed=>seeds.push(seed),api:async(path,options)=>{calls.push({path,options});return path.includes('entry_id=')?{entry:knowledge}:{entries:[knowledge],total:1};}});
 await settle();assert.equal(seeds.length,0);
 const button={dataset:{knowledgeAction:'research'},disabled:false};ui.fire('click',{target:{closest:()=>button}});
 assert.equal(seeds.length,1);assert.equal(seeds[0].entry_id,entryId);assert.equal(seeds[0].title,knowledge.title);assert.ok(calls.every(row=>!row.options?.body));cleanup();
});

test('editable seed sends only validated entry identity on explicit create and clears it for a fresh study',async()=>{
 for(const id of [entryId,'k_bad']){
  const ui=dom(),calls=[];
  const cleanup=mountAgent(ui.container,{initialQuery:'请核对这份资料',initialKnowledgeEntryId:id,api:async(path,options)=>{calls.push({path,options});return path==='/api/settings'?{}:path.startsWith('/api/research?')?{tasks:[]}:task({});}});
  await settle();assert.ok(!calls.some(row=>row.options?.body));
  ui.fire('submit',{target:{id:'ya-create-form'},preventDefault(){}});await settle();
  const submitted=calls.find(row=>row.options?.body)?.options.body;assert.equal(submitted.query,'请核对这份资料');assert.equal(submitted.knowledge_entry_id,id===entryId?entryId:undefined);assert.deepEqual(Object.keys(submitted).sort(),id===entryId?['knowledge_entry_id','query']:['query']);cleanup();
 }
 const ui=dom(),calls=[],cleanup=mountAgent(ui.container,{initialQuery:'补证',initialKnowledgeEntryId:entryId,api:async(path,options)=>{calls.push({path,options});return path==='/api/settings'?{}:{tasks:[]};}});
 await settle();ui.fire('click',{target:{closest:()=>({dataset:{agentAction:'home'}})}});
 ui.fire('input',{target:{name:'query',value:'普通研究'}});ui.fire('submit',{target:{id:'ya-create-form'},preventDefault(){}});await settle();
 assert.equal(calls.find(row=>row.options?.body)?.options.body.knowledge_entry_id,undefined);cleanup();
});

test('forged temporary catalog and save events never reach legacy callbacks',async()=>{
 const ui=dom(),opened=[],saved=[],cleanup=mountAgent(ui.container,{api:async path=>path==='/api/settings'?{}:{tasks:[]},onOpenProgram:id=>opened.push(id),onToggleSaved:id=>saved.push(id)});
 await settle();
 for(const dataset of [{agentProgram:program.id},{agentSave:program.id}])ui.fire('click',{target:{closest:()=>({dataset})}});
 assert.deepEqual(opened,[]);assert.deepEqual(saved,[]);cleanup();
});
