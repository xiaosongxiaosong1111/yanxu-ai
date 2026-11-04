import {esc} from './workbench-model.mjs';
import {icon} from './icons.mjs';

const array=value=>Array.isArray(value)?value:[];
const text=value=>typeof value==='string'?value:'';
const number=value=>Number.isInteger(value)&&value>=0?value:0;
const quality=entry=>entry?.quality?.version===1&&entry.quality.state==='accepted'&&entry.quality.reusable===true;
export const currentValidity=entry=>entry?.validity===undefined?'active':['active','revoked','conflict','superseded'].includes(entry.validity)?entry.validity:'unknown';
export const validityLabel=entry=>({active:'当前有效',revoked:'已撤信',conflict:'版本待核对',superseded:'已被替代',unknown:'有效性待核对'})[currentValidity(entry)];
export const readableEntry=entry=>quality(entry)&&currentValidity(entry)==='active'&&entry?.usable===true&&entry?.original_available===true&&entry?.snapshot_status!=='missing_or_changed';
export const structuredEntry=entry=>entry?.status==='official_original_read'&&(!!entry.extraction||typeof entry.index_state==='string');

export function sourceLocation(segment={},entry={}){
 const path=array(segment.section_path).filter(value=>typeof value==='string').join(' / ');
 const physical=segment.format==='pdf'&&segment.page_kind==='physical';
 const start=number(segment.page_start),end=number(segment.page_end);
 if(physical&&start>0)return `第 ${start}${end>start?`—${end}`:''} 页`;
 return path||'规范提取稿';
}

export function originalAt(segment={},entry={}){
 try{
  const url=new URL(entry.url||segment.source?.url);
  if(!['https:','http:'].includes(url.protocol)||url.username||url.password)return '';
  if(segment.format==='pdf'&&segment.page_kind==='physical'&&number(segment.page_start)>0)url.hash=`page=${segment.page_start}`;
  return url.href;
 }catch{return '';}
}

function spanLabel(span){
 return Number.isInteger(span?.char_start)&&Number.isInteger(span?.char_end)?`字符 ${span.char_start}–${span.char_end}`:'位置待核对';
}

function quoteCard(segment,entry,{focused=false,neighbor=false}={}){
 const link=originalAt(segment,entry),kind={table_row:'表格行',table_header:'表头',paragraph:'正文',heading:'章节'}[segment.kind]||'原文片段';
 return `<article class="${focused?'yk-focused-quote':neighbor?'yk-neighbor':'yk-segment-hit'}"><div class="yk-segment-meta"><span>${esc(sourceLocation(segment,entry))}</span><small>${kind} · ${esc(spanLabel(segment))}</small></div><blockquote>${esc(text(segment.text))}</blockquote>${focused?`${segment.content_truncated?'<p class="yk-muted">当前片段按阅读预算截取，末尾不是原文结尾。</p>':''}<div class="yk-quote-footer">${link?`<a href="${esc(link)}" target="_blank" rel="noopener noreferrer">在官方原文中核对 ${icon('arrowUp')}</a>`:''}<span>规范提取稿 · 字符按 Unicode 计数</span></div>`:`<button type="button" class="yk-segment-open" data-knowledge-segment="${esc(segment.id)}">${neighbor?'精读此处':'定位阅读'} ${icon('arrow')}</button>`}</article>`;
}

function contextsView(contexts,entry,segment){
 const labels={table_header:'表头依据',heading:'章节依据',parent:'章节依据'};
 return array(contexts).length?`<section class="yk-contexts"><h4>这条内容的适用上下文</h4><p>以下为独立原文位置，与命中片段分别引用。</p>${contexts.map(span=>{const located={...segment,...span,page_start:span.page_start,page_end:span.page_end};return `<details open><summary>${esc(labels[span.role]||'原文依据')} · ${esc(sourceLocation(located,entry))}</summary><blockquote>${esc(text(span.text))}</blockquote><small>${esc(spanLabel(span))}</small></details>`;}).join('')}</section>`:'';
}

function focusedView(state,entry){
 if(state.readStatus==='loading')return '<div class="yk-reading-state" role="status">正在定位原文与相邻条款…<button type="button" data-knowledge-reader="close-read">返回片段</button></div>';
 if(state.readStatus==='error')return `<div class="yk-reading-state is-error" role="alert"><p>${esc(state.readError||'这条原文暂时无法读取。')}</p><button type="button" data-knowledge-reader="retry-read">重新定位</button><button type="button" data-knowledge-reader="close-read">返回片段</button></div>`;
 if(state.readStatus!=='ready'||!state.read?.segment)return '';
 const read=state.read,segment=read.segment;
 return `<section class="yk-precision-reader" aria-label="精确原文阅读"><div class="yk-section-title"><h3>定位到的原文</h3><button type="button" data-knowledge-reader="close-read">返回片段</button></div>${quoteCard(segment,entry,{focused:true})}${contextsView(read.contexts,entry,segment)}${array(read.neighbors).length?`<section class="yk-neighbors"><h4>相邻原文</h4>${read.neighbors.map(item=>quoteCard(item,entry,{neighbor:true})).join('')}</section>`:''}<div class="yk-read-more">${number(state.window)<3?'<button type="button" data-knowledge-reader="expand">多读前后条款</button>':''}<span>${read.truncated?'仅显示本次预算内内容':'同一提取版本的原文窗口'} · ${number(read.returned_chars)} 字符</span></div></section>`;
}

function segmentsView(state,entry){
 const page=Math.max(1,number(state.page)),pages=Math.max(1,Math.ceil(number(state.total)/8));
 const documentMatch=state.matchMode==='document_terms'||array(state.segments).some(segment=>segment.match_mode==='document_terms');
 const result=state.status==='error'?`<div class="yk-reading-state is-error" role="alert">${esc(state.error||'片段暂时无法读取。')}<button type="button" data-knowledge-reader="retry-search">重新读取</button></div>`:state.status!=='ready'?'<div class="yk-reading-state" role="status">正在读取原文片段…</div>':!array(state.segments).length?'<div class="yk-reading-state"><strong>没有找到匹配的原文片段</strong><p>试试专业代码、考试科目或更短的关键词。</p></div>':`<div class="yk-segment-hits">${state.segments.map(item=>quoteCard(item,entry)).join('')}</div><nav class="yk-segment-pagination" aria-label="原文片段分页"><span>${number(state.total)} 个片段 · ${page} / ${pages}</span><div><button type="button" data-knowledge-reader="previous" ${page<=1?'disabled':''}>上一页</button><button type="button" data-knowledge-reader="next" ${page>=pages?'disabled':''}>下一页</button></div></nav>`;
 return `<section class="yk-structured-reader"><div class="yk-section-title"><h3>精读原文</h3><span>${number(entry.extraction?.segment_count)} 个结构片段</span></div><p class="yk-muted">规范提取稿 · 章节、表格与原始位置保留</p><form id="yk-segment-search" role="search"><label class="yk-sr-only" for="yk-segment-query">在这份原文中查找</label><div class="yk-search-field">${icon('search')}<input id="yk-segment-query" name="segment_query" type="search" maxlength="240" placeholder="在这份原文中查找" value="${esc(state.query||'')}"><button type="submit">查找</button></div></form>${documentMatch?'<p class="yk-document-match" role="status">关键词分布在同份原文中，请结合上下文核对。</p>':''}${state.readStatus&&state.readStatus!=='none'?focusedView(state,entry):result}</section>`;
}

export function extractionStatus(entry){
 const state=entry?.extraction?.extraction_status;
 return {complete:'提取完整',partial:'提取不完整',needs_ocr:'扫描件待 OCR',unsupported:'暂不支持此格式'}[state]||'尚未保存完整提取稿';
}

function indexLabel(entry){
 return {succeeded:'本地索引已完成',pending:'本地索引待处理',running:'本地索引正在处理',failed:'本地索引失败',cancelled:'本地索引已取消',unindexed:'尚未建立本地索引'}[entry.index_state]||'尚未建立本地索引';
}

function jobDetails(state,entry){
 const jobs=array(state.jobs?.jobs||state.jobs),job=jobs[0]||entry.latest_index_job;
 return `<div class="yk-index-status"><strong>${esc(indexLabel(entry))}</strong><p>只重建留档原文的结构索引，不会联网采集，也不会生成新的招生事实。</p>${job?`<small>最近任务：${esc({succeeded:'已完成',failed:'失败',cancelled:'已取消',running:'进行中',pending:'待处理'}[job.state]||'状态待核对')} · 尝试 ${number(job.attempts)} 次${job.error_code?` · ${esc(text(job.error_code).slice(0,100))}`:''}</small>`:''}${state.jobsError?`<p role="alert">${esc(state.jobsError)}</p>`:''}</div>`;
}

function versionsView(entry){
 const versions=array(entry.versions);
 return versions.length>1?`<div class="yk-version-list"><h4>同一来源、同一年度的版本</h4>${versions.map(version=>`<button type="button" data-knowledge-open="${esc(version.id)}" ${version.id===entry.id?'disabled':''}><span>${version.id===entry.id?'正在查看':validityLabel(version)} · ${esc(text(version.sha256).slice(0,12)||'SHA 待核')}</span><small>${esc(version.title||'来源版本')}</small></button>`).join('')}<p>不同版本不算独立印证，采用前请对照原文。</p></div>`:'';
}

export function maintenanceView(entry,state={}){
 const busy=state.busy?'disabled':'',active=currentValidity(entry)==='active',admitted=quality(entry)&&entry.original_available===true;
 const action={revoke:'撤信这份资料',restore:'恢复为有效资料','select-version':'采用这个版本'}[state.confirmAction];
 return `<details class="yk-maintenance" ${state.maintenanceOpen?'open':''}><summary>维护与版本 <span>${esc(validityLabel(entry))}</span></summary><div class="yk-maintenance-content"><div class="yk-extraction-states"><span>${esc(extractionStatus(entry))}</span><span>${esc(indexLabel(entry))}</span></div>${entry.validity_reason?`<p class="yk-validity-reason">${esc(entry.validity_reason)}</p>`:''}${jobDetails(state,entry)}${versionsView(entry)}<label class="yk-maintenance-reason">维护原因<textarea name="maintenance_reason" maxlength="500" rows="2" placeholder="记录原文变化、撤信或版本选择的依据">${esc(state.reason||'')}</textarea></label><div class="yk-maintenance-actions">${active?`<button type="button" data-knowledge-maintenance="revoke" ${busy}>撤信</button>`:admitted?`<button type="button" data-knowledge-maintenance="restore" ${busy}>恢复有效</button>`:''}${admitted?`<button type="button" data-knowledge-maintenance="select-version" ${busy}>采用此版本</button>`:''}<button type="button" data-knowledge-maintenance="reindex" ${busy}>重建本地索引</button></div>${action?`<section class="yk-maintenance-confirm" role="alert"><strong>确认${action}？</strong><p>${state.confirmAction==='revoke'?'研究将停止复用这份资料，来源与维护记录仍会保留。':'只影响这一来源与实际年度；不会跳过原文完整性和质量检查。'}</p><div><button type="button" data-knowledge-confirm ${busy}>确认${action}</button><button type="button" data-knowledge-reader="cancel-maintenance" ${busy}>取消</button></div></section>`:''}${state.maintenanceError?`<p class="yk-maintenance-message is-error" role="alert">${esc(state.maintenanceError)}</p>`:''}${state.notice?`<p class="yk-maintenance-message" role="status">${esc(state.notice)}</p>`:''}${state.busy?'<p class="yk-maintenance-message" role="status">正在处理，请稍候…</p>':''}<p class="yk-maintenance-help">准入质量、有效性、提取完整性与索引状态分别记录。恢复或重建不会把待核资料自动升级为可信资料。</p></div></details>`;
}

export function readerWorkbench(entry,state={}){
 if(currentValidity(entry)!=='active'||structuredEntry(entry)&&!readableEntry(entry))return `<section class="yk-reader-unavailable" role="status"><h3>${currentValidity(entry)==='active'?'这份资料暂不能精读复用':validityLabel(entry)}</h3><p>${esc(entry.validity_reason||'需要先核对准入质量与原文完整性。来源信息仍可查看。')}</p></section>`;
 if(!structuredEntry(entry))return '';
 if(entry.index_state!=='succeeded')return `<section class="yk-reader-unavailable"><h3>${esc(indexLabel(entry))}</h3><p>${esc(extractionStatus(entry))}。可在下方“维护与版本”中查看任务，或从可验证的留档原件重建。</p></section>`;
 return segmentsView(state,entry);
}

function reindexNotice(state){
 return {needs_reextract:'留档原件需要重新提取，尚未完成索引。',needs_review:'这份资料仍需核对，未建立可复用索引。',excluded:'这份材料已被准入规则拦截，未建立可复用索引。',unsupported:'暂不支持提取这一格式，未完成索引。',needs_ocr:'扫描件仍需 OCR，未完成索引。',cancelled:'索引任务已取消，资料有效状态保持原样。'}[state]||'重建未完成，原有可用版本已保留。请查看任务记录后重试。';
}

const freshReaderState=()=>({status:'none',query:'',segments:[],page:1,total:0,matchMode:'',readStatus:'none',read:null,window:1,reason:'',busy:false,jobs:null,notice:'',maintenanceOpen:false,maintenanceError:'',confirmAction:'',jobsError:''});

function readerRuntime({api,onChange}){
 let current={entry:null,state:freshReaderState(),epoch:0,disposed:false,tickets:{search:0,read:0,jobs:0,maintenance:0}};
 const controllers=new Set();
 const update=(patch,render=true)=>{current={...current,state:{...current.state,...patch}};if(!current.disposed)onChange?.(current.state,render);};
 const invalidate=channel=>{current={...current,tickets:{...current.tickets,[channel]:current.tickets[channel]+1}};};
 const cancel=()=>{current={...current,epoch:current.epoch+1};for(const control of controllers)control.abort();controllers.clear();};
 const begin=channel=>{invalidate(channel);const control=new AbortController();controllers.add(control);return {channel,ticket:current.tickets[channel],epoch:current.epoch,control};};
 const live=operation=>!current.disposed&&operation.epoch===current.epoch&&operation.ticket===current.tickets[operation.channel];
 async function request(path,options,operation){try{return await api(path,{...options,signal:operation.control.signal});}finally{controllers.delete(operation.control);}}
 return {
  entry:()=>current.entry,state:()=>current.state,disposed:()=>current.disposed,update,begin,live,request,invalidate,
  open(entry){const state=current.state,kept=current.entry?.id===entry?.id?{notice:state.notice,maintenanceOpen:state.maintenanceOpen,reason:state.reason}:{};cancel();current={...current,entry};update({...freshReaderState(),...kept});},
  reset(){cancel();current={...current,entry:null,state:freshReaderState()};},
  dispose(){cancel();current={...current,disposed:true};},
 };
}

async function searchSegments(runtime,page=1){
 const entry=runtime.entry(),state=runtime.state();
 if(runtime.disposed()||!entry||!readableEntry(entry)||entry.index_state!=='succeeded')return;
 const operation=runtime.begin('search');runtime.invalidate('read');
 runtime.update({status:'loading',error:'',page,readStatus:'none',read:null});
 try{
  const params=new URLSearchParams({entry_id:entry.id,q:state.query.trim(),page:String(page),page_size:'8',quality:'accepted'});
  const result=await runtime.request('/api/knowledge/segments?'+params,{},operation);
  if(!runtime.live(operation))return;
  if(!Array.isArray(result?.segments)||!Number.isInteger(result.total))throw new Error('原文片段返回格式需要重新检查。');
  const segments=result.segments.filter(item=>item.entry_id===entry.id&&item.extraction_id===entry.extraction?.id&&(!entry.extraction?.generation_id||item.generation_id===entry.extraction.generation_id)&&sourceMatches(item.source,entry));
  if(segments.length!==result.segments.length)throw new Error('原文版本已经变化，请重新打开这份资料。');
  runtime.update({status:'ready',segments,total:result.total,page:result.page||page,matchMode:result.match_mode==='document_terms'?'document_terms':'segment_terms'});
 }catch(error){if(!runtime.live(operation)||error.name==='AbortError')return;runtime.update({status:'error',error:error.message||'原文片段暂时无法读取。'});}
}

function sourceMatches(source,entry){
 if(source===undefined)return true;
 if(!source||typeof source!=='object'||Array.isArray(source))return false;
 for(const field of ['id','unit_id','year'])if(source[field]!==undefined&&source[field]!==entry[field])return false;
 if(currentValidity(source)!=='active'||source.usable!==undefined&&source.usable!==true)return false;
 if(source.quality!==undefined&&!quality(source))return false;
 if(source.original_available===false||source.snapshot_status==='missing_or_changed')return false;
 return source.status===undefined||source.status==='official_original_read';
}

function checkReadVersion(result,entry,id){
 if(result?.segment?.id!==id||result.segment.entry_id!==entry.id||result.segment.extraction_id!==entry.extraction?.id||entry.extraction?.generation_id&&result.segment.generation_id!==entry.extraction.generation_id)throw new Error('返回的原文位置与当前版本不一致，请重新打开资料。');
 const generation=result.segment.generation_id;
 if(!sourceMatches(result.segment.source,entry)||array(result.neighbors).some(item=>!sourceMatches(item.source,entry))||array(result.contexts).some(item=>!sourceMatches(item.source,entry)))throw new Error('来源有效性或适用范围已经变化，请重新打开资料。');
 if(array(result.neighbors).some(item=>item.entry_id!==entry.id||item.extraction_id!==entry.extraction.id||item.generation_id!==generation))throw new Error('相邻原文版本不一致，请重新读取。');
 if(array(result.contexts).some(item=>item.entry_id!==undefined&&item.entry_id!==entry.id||item.extraction_id!==undefined&&item.extraction_id!==entry.extraction.id||item.generation_id!==undefined&&item.generation_id!==generation))throw new Error('引用上下文的来源版本不一致，请重新读取。');
}

async function readSegment(runtime,id,window=1){
 const entry=runtime.entry();
 if(runtime.disposed()||!entry||!readableEntry(entry)||entry.index_state!=='succeeded')return;
 const operation=runtime.begin('read');runtime.update({readStatus:'loading',readId:id,readError:'',window});
 try{
  const params=new URLSearchParams({segment_id:id,before:String(window),after:String(window),max_chars:'9000'});
  const result=await runtime.request('/api/knowledge/segment?'+params,{},operation);
  if(!runtime.live(operation))return;
  checkReadVersion(result,entry,id);runtime.update({readStatus:'ready',read:result});
 }catch(error){if(!runtime.live(operation)||error.name==='AbortError')return;runtime.update({readStatus:'error',read:null,readError:error.message||'原文暂时无法定位。'});}
}

async function loadJobs(runtime){
 const entry=runtime.entry();if(runtime.disposed()||!entry)return;
 const operation=runtime.begin('jobs');
 try{const result=await runtime.request('/api/knowledge/jobs?'+new URLSearchParams({entry_id:entry.id}),{},operation);if(runtime.live(operation))runtime.update({jobs:result,jobsError:''});}
 catch(error){if(runtime.live(operation)&&error.name!=='AbortError')runtime.update({jobsError:error.message||'任务记录暂时无法读取。'});}
}

async function maintainSource(runtime,action,onEntryReload){
 const entry=runtime.entry(),state=runtime.state();
 if(runtime.disposed()||!entry||state.busy||!['reindex','revoke','restore','select-version'].includes(action))return;
 if(action!=='reindex'&&!state.reason.trim()){runtime.update({maintenanceError:'请填写维护原因，保留这次判断的依据。',maintenanceOpen:true});return;}
 const operation=runtime.begin('maintenance'),id=entry.id;runtime.update({busy:true,maintenanceError:'',notice:'',confirmAction:''});
 const body=action==='reindex'?{entry_id:id}:action==='select-version'?{entry_id:id,reason:state.reason.trim()}:{entry_id:id,validity:action==='revoke'?'revoked':'active',reason:state.reason.trim()};
 const path=action==='reindex'?'reindex':action==='select-version'?'select-version':'state';
 try{
  const result=await runtime.request('/api/knowledge/'+path,{body},operation);
  if(!runtime.live(operation))return;
  const rebuilt=result?.state==='succeeded'&&result?.rebuilt===true;
  runtime.update({busy:false,notice:action==='reindex'?(rebuilt?'本地索引重建已完成。':reindexNotice(result?.state)):'维护已保存，研究将按当前有效状态使用资料。',reason:'',maintenanceOpen:true});
  await onEntryReload?.(id);loadJobs(runtime);
 }catch(error){if(!runtime.live(operation)||error.name==='AbortError')return;runtime.update({busy:false,maintenanceError:error.message||'维护未完成，请稍后重试。'});}
}

function stageMaintenance(runtime,action,onEntryReload){
 const state=runtime.state();if(state.busy)return;
 if(action==='reindex'){maintainSource(runtime,action,onEntryReload);return;}
 if(!['revoke','restore','select-version'].includes(action))return;
 if(!state.reason.trim()){runtime.update({maintenanceError:'请先填写维护原因。',maintenanceOpen:true});return;}
 runtime.update({confirmAction:action,maintenanceError:'',maintenanceOpen:true});
}

export function createKnowledgeReader({api,onChange,onEntryReload}={}){
 const runtime=readerRuntime({api,onChange}),state=()=>runtime.state();
 const search=page=>searchSegments(runtime,page),read=(id,window)=>readSegment(runtime,id,window),maintain=action=>maintainSource(runtime,action,onEntryReload);
 const actions={previous:()=>search(Math.max(1,state().page-1)),next:()=>search(state().page+1),'retry-search':()=>search(state().page),'retry-read':()=>read(state().readId,state().window),expand:()=>read(state().readId,Math.min(3,state().window+1)),'close-read':()=>{runtime.invalidate('read');runtime.update({readStatus:'none',read:null});},'cancel-maintenance':()=>runtime.update({confirmAction:'',maintenanceError:''})};
 return {
  open(entry){runtime.open(entry);search();},
  input(name,value){if(name==='segment_query')runtime.update({query:text(value).slice(0,240)},false);if(name==='maintenance_reason')runtime.update({reason:Array.from(text(value)).slice(0,500).join(''),confirmAction:''},false);},
  search:()=>search(1),read,
  toggle(open){if(open===state().maintenanceOpen)return;runtime.update({maintenanceOpen:open},false);if(open)loadJobs(runtime);},
  click(button){const data=button.dataset;if(data.knowledgeSegment){read(data.knowledgeSegment);return true;}if(data.knowledgeMaintenance){stageMaintenance(runtime,data.knowledgeMaintenance,onEntryReload);return true;}if('knowledgeConfirm' in data){maintain(state().confirmAction);return true;}if(data.knowledgeReader){actions[data.knowledgeReader]?.();return true;}return false;},
  reset:()=>runtime.reset(),dispose:()=>runtime.dispose(),snapshot:state,
 };
}
