import test from 'node:test';
import assert from 'node:assert/strict';
import {renderKnowledge} from '../static/knowledge.mjs';
import {createKnowledgeReader,readerWorkbench,maintenanceView,originalAt,sourceLocation} from '../static/knowledge-reader.mjs';
import {renderAgent} from '../static/agent.mjs';

const entry={id:'k_'+'a'.repeat(24),title:'2027 年招生目录',unit_id:'u',unit_name:'样本大学',year:2027,status:'official_original_read',url:'https://example.edu.cn/catalog.pdf',original_available:true,usable:true,quality:{version:1,state:'accepted',reusable:true},validity:'active',index_state:'succeeded',extraction:{id:'ex_a',format:'pdf',page_kind:'physical',extraction_status:'complete',segment_count:24,extractor_version:'pdf-1',segmenter_version:'structure-v1',text_sha256:'f'.repeat(64)},text:'LEGACY_BODY_MUST_NOT_OVERRIDE_SEGMENTS'};
const base={loadStatus:'ready',entries:[entry],total:1,page:1,pageSize:12,filters:{quality:'accepted'},detailId:entry.id,detailStatus:'ready',detail:entry};
const segment={id:'sg_'+'b'.repeat(32),entry_id:entry.id,extraction_id:'ex_a',generation_id:'kg_a',text:'计算机学院仅接受以下专业；其他专业不接受。',char_start:32001,char_end:32026,page_start:21,page_end:21,page_kind:'physical',format:'pdf',kind:'table_row',section_path:['计算机学院','报考要求'],previous_id:'sg_previous',next_id:'sg_next',source:entry};

test('indexed originals open a focused segment reader rather than the legacy text dump',()=>{
 const html=renderKnowledge({...base,reader:{status:'ready',query:'计算机',segments:[segment],total:1,page:1}});
 assert.match(html,/id="yk-segment-search"/);assert.match(html,/data-knowledge-segment=/);assert.match(html,/第 21 页/);
 assert.ok(!html.includes(entry.text));assert.match(html,/32001/);assert.match(html,/规范提取稿/);
});
test('separate table context and neighboring clauses keep their own quotes and exact source location',()=>{
 const html=renderKnowledge({...base,reader:{status:'ready',segments:[segment],readStatus:'ready',read:{segment,contexts:[{role:'table_header',text:'专业代码\t专业名称',char_start:30001,char_end:30010,page_start:20,page_end:20}],neighbors:[{...segment,id:'sg_neighbor',text:'但不接受同等学力考生。',page_start:22,page_end:22}],truncated:true}}});
 assert.match(html,/表头依据/);assert.match(html,/专业代码/);assert.match(html,/相邻原文/);assert.match(html,/第 20 页/);assert.match(html,/预算内/);
 assert.match(html,/href="https:\/\/example.edu.cn\/catalog.pdf#page=21"/);
});
test('inactive sources suppress bodies regardless of their former admission quality',()=>{
 for(const validity of ['revoked','conflict','superseded']){
  const html=renderKnowledge({...base,detail:{...entry,validity,validity_reason:'需要重新核对',text:'STALE_CANARY',pages:[{page:1,text:'STALE_PAGE'}]},reader:{status:'ready',segments:[segment],readStatus:'ready',read:{segment}}});
  assert.ok(!html.includes('STALE_CANARY'));assert.ok(!html.includes('STALE_PAGE'));assert.ok(!html.includes(segment.text));
  assert.match(html,/需要重新核对/);assert.match(html,/data-knowledge-maintenance/);
 }
});

test('unknown validity and missing modern usability fail closed',()=>{
 for(const patch of [{validity:'unrecognized'},{usable:undefined}]){
  const html=renderKnowledge({...base,detail:{...entry,...patch},reader:{status:'ready',segments:[segment],readStatus:'ready',read:{segment}}});
  assert.ok(!html.includes(segment.text));assert.ok(!html.includes(entry.text));
 }
});

test('real Store unindexed metadata on search leads and published snapshots preserves their bounded summaries',()=>{
 for(const [status,label] of [['search_lead','搜索摘要'],['published_snapshot','已收录资料摘录']]){
  const detail={...entry,status,index_state:'unindexed',extraction:null,original_available:false,usable:false,text:'',excerpt:'已有的有界来源摘要。'};
  const html=renderKnowledge({...base,detail});assert.match(html,/已有的有界来源摘要/);assert.ok(html.includes(label));assert.ok(!html.includes('精读原文'));assert.ok(!html.includes('尚未建立本地索引</h3>'));
  const excluded=renderKnowledge({...base,detail:{...detail,quality:{version:1,state:'excluded',reusable:false}}});assert.ok(!excluded.includes(detail.excerpt));
  const inactive=renderKnowledge({...base,detail:{...detail,validity:'revoked',validity_reason:'已失效'}});assert.ok(!inactive.includes(detail.excerpt));
 }
});
test('maintenance is a secondary collapsed surface with separate validity and extraction/index states',()=>{
 const html=renderKnowledge({...base,reader:{status:'ready',segments:[]}});
 assert.match(html,/<details class="yk-maintenance"[^>]*>/);assert.ok(!html.includes('<details class="yk-maintenance" open'));
 assert.match(html,/提取完整/);assert.match(html,/本地索引已完成/);assert.match(html,/维护与版本/);
 assert.match(html,/name="maintenance_reason"/);assert.match(html,/data-knowledge-maintenance="reindex"/);assert.ok(!html.includes('向量就绪'));
});

test('document-wide terms are explained instead of declaring a single clause satisfies the whole query',()=>{
 const html=renderKnowledge({...base,reader:{status:'ready',query:'专业 科目',matchMode:'document_terms',segments:[{...segment,match_mode:'document_terms'}],total:1}});
 assert.match(html,/关键词分布在同份原文中，请结合上下文核对/);
});
test('HTML pages never receive made-up PDF anchors and exact source strings remain escaped',()=>{
 const htmlEntry={...entry,url:'https://example.edu.cn/catalog.html',extraction:{...entry.extraction,format:'html',page_kind:'synthetic'}};
 const htmlSegment={...segment,page_kind:'synthetic',format:'html',text:'<script>unsafe()</script>',source:htmlEntry};
 const html=renderKnowledge({...base,detail:htmlEntry,reader:{status:'ready',segments:[htmlSegment],readStatus:'ready',read:{segment:htmlSegment}}});
 assert.ok(!html.includes('#page='));assert.ok(!html.includes('<script>unsafe'));assert.match(html,/&lt;script&gt;unsafe/);
});

const flush=()=>new Promise(resolve=>setImmediate(resolve));
const click=(control,dataset)=>control.click({dataset});
function harness(handler){
 const calls=[],changes=[],reloads=[];
 const control=createKnowledgeReader({api:async(path,options)=>{calls.push({path,options});return handler(path,options);},onChange:(state,render)=>changes.push({state,render}),onEntryReload:async id=>reloads.push(id)});
 return {control,calls,changes,reloads};
}
const segmentResult={segments:[segment],total:1,page:1};
const readResult={segment,contexts:[],neighbors:[],returned_chars:25,truncated:false};

test('controller scopes search to current entry and accepted quality and bounds every read window',async()=>{
 const h=harness(path=>path.startsWith('/api/knowledge/segments?')?segmentResult:readResult);
 h.control.open(entry);await flush();assert.equal(h.control.snapshot().status,'ready');
 const search=new URL(h.calls[0].path,'http://fixture');assert.equal(search.searchParams.get('quality'),'accepted');assert.equal(search.searchParams.get('entry_id'),entry.id);
 h.control.input('segment_query','408');h.control.search();await flush();assert.match(h.calls.at(-1).path,/q=408/);
 click(h.control,{knowledgeSegment:segment.id});await flush();assert.equal(h.control.snapshot().readStatus,'ready');
 for(let i=0;i<5;i++){click(h.control,{knowledgeReader:'expand'});await flush();}
 assert.equal(new URL(h.calls.at(-1).path,'http://fixture').searchParams.get('before'),'3');
 click(h.control,{knowledgeReader:'close-read'});assert.equal(h.control.snapshot().readStatus,'none');
 click(h.control,{knowledgeReader:'next'});await flush();click(h.control,{knowledgeReader:'previous'});await flush();h.control.dispose();
});

test('untrusted entries and unsupported index states do not request trusted segment APIs',async()=>{
 for(const value of [{...entry,quality:{state:'pending'}},{...entry,usable:false},{...entry,validity:'conflict'},{...entry,index_state:'unindexed'}]){
  const h=harness(()=>{throw Error('unexpected network');});h.control.open(value);await h.control.read(segment.id);assert.equal(h.calls.length,0);h.control.dispose();
 }
 assert.match(readerWorkbench({...entry,extraction:{...entry.extraction,extraction_status:'needs_ocr'},index_state:'failed'}),/扫描件待 OCR/);
 assert.match(readerWorkbench({...entry,extraction:{...entry.extraction,extraction_status:'unsupported'},index_state:'unindexed'}),/暂不支持此格式/);
 assert.equal(readerWorkbench({...entry,extraction:undefined,index_state:undefined}), '');
});

test('outdated search and read responses cannot replace a newer entry or position',async()=>{
 let releaseSearch,releaseRead;
 const h=harness(path=>{if(path.startsWith('/api/knowledge/segments?'))return new Promise(resolve=>{releaseSearch=resolve;});return new Promise(resolve=>{releaseRead=resolve;});});
 h.control.open(entry);const oldSearch=releaseSearch;
 const second={...entry,id:'k_'+'c'.repeat(24)};h.control.open(second);releaseSearch({segments:[],total:0,page:1});await flush();oldSearch(segmentResult);await flush();assert.equal(h.control.snapshot().segments.length,0);
 h.control.open(entry);releaseSearch(segmentResult);await flush();h.control.read(segment.id);const oldRead=releaseRead;h.control.read('sg_new');releaseRead({...readResult,segment:{...segment,id:'sg_new'}});await flush();oldRead(readResult);await flush();assert.equal(h.control.snapshot().read.segment.id,'sg_new');
 h.control.reset();assert.equal(h.control.snapshot().status,'none');h.control.dispose();
});

test('foreign entry, extraction or neighboring generation is rejected instead of displayed',async()=>{
 const h=harness(path=>path.startsWith('/api/knowledge/segments?')?{segments:[{...segment,entry_id:'foreign'}],total:1}:readResult);
 h.control.open(entry);await flush();assert.equal(h.control.snapshot().status,'error');assert.match(h.control.snapshot().error,/版本已经变化/);
 let result={segment:{...segment,extraction_id:'old'},neighbors:[]};
 const x=harness(path=>path.startsWith('/api/knowledge/segments?')?segmentResult:result);x.control.open(entry);await flush();await x.control.read(segment.id);assert.equal(x.control.snapshot().readStatus,'error');
 result={...readResult,neighbors:[{...segment,generation_id:'old'}]};await x.control.read(segment.id);assert.match(x.control.snapshot().readError,/相邻原文版本不一致/);x.control.dispose();h.control.dispose();
});

test('a self-consistent old generation is rejected against the current generation',async()=>{
 const h=harness(path=>path.startsWith('/api/knowledge/segments?')?{segments:[],total:0}:{...readResult,segment:{...segment,generation_id:'kg_old'}});
 h.control.open({...entry,extraction:{...entry.extraction,generation_id:'kg_current'}});await flush();await h.control.read(segment.id);
 assert.equal(h.control.snapshot().readStatus,'error');assert.match(h.control.snapshot().readError,/版本不一致/);h.control.dispose();
});

test('explicitly foreign context identities cannot be quoted as part of this source',async()=>{
 for(const identity of [{entry_id:'foreign'},{extraction_id:'old'},{generation_id:'old'}]){
  const h=harness(path=>path.startsWith('/api/knowledge/segments?')?segmentResult:{...readResult,contexts:[{...identity,text:'FOREIGN_CONTEXT_CANARY',char_start:1,char_end:23}]});
  h.control.open(entry);await flush();await h.control.read(segment.id);assert.equal(h.control.snapshot().readStatus,'error');assert.equal(h.control.snapshot().read,null);h.control.dispose();
 }
});

test('fresh read and search source metadata can withdraw trust or reveal a foreign source',async()=>{
 for(const patch of [{validity:'revoked',usable:false},{id:'k_foreign'},{year:2026},{unit_id:'other-school'},{quality:{version:1,state:'pending',reusable:false}},{original_available:false}]){
  const changed={...segment,source:{...entry,...patch}};
  const h=harness(path=>path.startsWith('/api/knowledge/segments?')?{segments:[changed],total:1}:{...readResult,segment:changed});
  h.control.open(entry);await flush();assert.equal(h.control.snapshot().status,'error');await h.control.read(segment.id);assert.equal(h.control.snapshot().readStatus,'error');assert.equal(h.control.snapshot().read,null);h.control.dispose();
 }
});

test('agent deep links choose a legal segment from the current extraction generation only',()=>{
 const source={id:'s',title:'官方原文',status:'official_original_read',knowledge_id:entry.id,knowledge_saved:true,knowledge_current:true,knowledge_quality:entry.quality,extraction:{generation_id:'kg_current'},segments:[{id:'sg_old',generation_id:'kg_old'},{id:sidForTest(),generation_id:'kg_old'},{id:segment.id,generation_id:'kg_current'}]};
 const render=value=>renderAgent({task:{id:'r',status:'completed',report:{evidence:[value]}},tab:'sources'});
 assert.match(render(source),new RegExp('data-agent-knowledge-segment="'+segment.id+'"'));
 for(const patch of [{knowledge_current:false},{extraction:{generation_id:'other'}},{segments:[{id:'javascript:alert(1)',generation_id:'kg_current'}]}])assert.ok(!render({...source,...patch}).includes('data-agent-knowledge-segment='));
});
function sidForTest(){return 'sg_'+'d'.repeat(32);}

test('reindex only claims completion for a confirmed rebuilt successful generation',async()=>{
 for(const result of [{state:'needs_reextract',rebuilt:false},{state:'excluded',rebuilt:false},{state:'failed',rebuilt:false},{state:'succeeded',rebuilt:false},{state:'unsupported',rebuilt:false}]){
  const h=harness(path=>path.startsWith('/api/knowledge/segments?')?segmentResult:path.startsWith('/api/knowledge/jobs?')?{jobs:[]}:result);
  h.control.open(entry);await flush();click(h.control,{knowledgeMaintenance:'reindex'});await flush();assert.ok(!h.control.snapshot().notice.includes('重建已完成'));h.control.dispose();
 }
 const html=maintenanceView(entry,{});assert.match(html,/maxlength="500"/);
});

test('network errors remain recoverable and aborted requests do not render failures',async()=>{
 let fail=true;
 const h=harness(path=>{if(fail)throw Error('本地服务故障');return path.startsWith('/api/knowledge/segments?')?segmentResult:readResult;});
 h.control.open(entry);await flush();assert.equal(h.control.snapshot().status,'error');fail=false;click(h.control,{knowledgeReader:'retry-search'});await flush();assert.equal(h.control.snapshot().status,'ready');
 fail=true;await h.control.read(segment.id);assert.equal(h.control.snapshot().readStatus,'error');fail=false;click(h.control,{knowledgeReader:'retry-read'});await flush();assert.equal(h.control.snapshot().readStatus,'ready');
 h.control.dispose();const count=h.changes.length;await h.control.read(segment.id);await flush();assert.equal(h.changes.length,count);
});

test('maintenance requires a reason and explicit confirmation and sends no null replacement',async()=>{
 const h=harness(path=>path.startsWith('/api/knowledge/segments?')?segmentResult:path.startsWith('/api/knowledge/jobs?')?{jobs:[],backend:'local_segments'}:{entry:{...entry,validity:'revoked'}});
 h.control.open(entry);await flush();click(h.control,{knowledgeMaintenance:'revoke'});assert.match(h.control.snapshot().maintenanceError,/填写维护原因/);assert.equal(h.calls.length,1);
 h.control.input('maintenance_reason','官网已更正');click(h.control,{knowledgeMaintenance:'revoke'});assert.equal(h.control.snapshot().confirmAction,'revoke');assert.equal(h.calls.length,1);
 click(h.control,{knowledgeReader:'cancel-maintenance'});assert.equal(h.control.snapshot().confirmAction,'');
 click(h.control,{knowledgeMaintenance:'revoke'});click(h.control,{knowledgeConfirm:''});await flush();const mutation=h.calls.find(call=>call.path==='/api/knowledge/state');assert.deepEqual(mutation.options.body,{entry_id:entry.id,validity:'revoked',reason:'官网已更正'});assert.deepEqual(h.reloads,[entry.id]);
 assert.match(h.control.snapshot().notice,/维护已保存/);h.control.dispose();
});

test('index jobs and retry failures are visible without pretending a vector index exists',async()=>{
 let failed=true;
 const h=harness(path=>{if(path.startsWith('/api/knowledge/segments?'))return segmentResult;if(path.startsWith('/api/knowledge/jobs?')){if(failed)throw Error('记录读取失败');return {jobs:[{state:'succeeded',attempts:2}],backend:'local_segments'};}if(failed)throw Error('本地索引重建失败');return {state:'succeeded',rebuilt:true};});
 h.control.open(entry);await flush();h.control.toggle(true);await flush();assert.match(h.control.snapshot().jobsError,/记录读取失败/);
 click(h.control,{knowledgeMaintenance:'reindex'});await flush();assert.match(h.control.snapshot().maintenanceError,/重建失败/);
 failed=false;click(h.control,{knowledgeMaintenance:'reindex'});await flush();assert.match(h.control.snapshot().notice,/重建已完成/);assert.equal(h.control.snapshot().jobs.jobs[0].attempts,2);
 h.control.toggle(false);assert.equal(h.control.snapshot().maintenanceOpen,false);h.control.dispose();
});

test('source URLs and contexts remain honest for unknown physical positions and malicious links',()=>{
 assert.equal(originalAt(segment,{url:'javascript:alert(1)'}),'');assert.equal(originalAt(segment,{url:'https://user:pass@example.edu.cn/'}),'');assert.equal(originalAt(segment,{url:'invalid'}),'');
 assert.equal(sourceLocation({...segment,page_start:0,section_path:[]}), '规范提取稿');
 const html=renderKnowledge({...base,reader:{status:'ready',segments:[segment],readStatus:'ready',read:{segment,contexts:[{text:'表头',role:'table_header',char_start:10,char_end:12}]}}});
 assert.match(html,/表头依据 · 计算机学院 \/ 报考要求/);
 const versions=maintenanceView({...entry,versions:[{...entry},{...entry,id:'k_second',validity:'superseded',sha256:'abc'}]},{confirmAction:'restore',jobs:{jobs:[{state:'failed',attempts:1,error_code:'EXTRACT_FAILED'}]}});
 assert.match(versions,/同一来源、同一年度的版本/);assert.match(versions,/确认恢复为有效资料/);assert.match(versions,/EXTRACT_FAILED/);
});
