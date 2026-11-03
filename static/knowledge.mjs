import {esc} from './workbench-model.mjs';
import {icon} from './icons.mjs';

const list=value=>Array.isArray(value)?value:[];
const object=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
const text=value=>typeof value==='string'?value:'';
const count=value=>Number.isInteger(value)&&value>=0?value:0;
const kinds=[['official_original_read','原文留档'],['search_lead','搜索线索'],['published_snapshot','已收录资料']];
const label=status=>kinds.find(([value])=>value===status)?.[1]||'来源待核对';
const safeLink=value=>{try{const url=new URL(value);return ['https:','http:'].includes(url.protocol)&&!url.username&&!url.password?url.href:'';}catch{return '';}};
const date=value=>{const result=new Date(value||'');return Number.isNaN(result.getTime())?'时间待记录':result.toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'});};
const year=entry=>Number.isInteger(entry.year)?`${entry.year} 年`:'年度待核验';
const unavailable=entry=>entry.snapshot_status==='missing_or_changed'||entry.status==='official_original_read'&&entry.original_available!==true;
const sourceCount=entry=>new Set(list(entry.provenance).filter(item=>typeof item==='string')).size;
const selected=(a,b)=>String(a??'')===String(b??'')?' selected':'';
const qualitySections=[['accepted','可复用资料'],['pending','待核线索'],['excluded','已拦截'],['all','全部来源']];
const qualityLabel=state=>qualitySections.find(([value])=>value===state)?.[1]||'待核线索';
const qualityFilter=value=>qualitySections.some(([state])=>state===value)?value:'accepted';
const hasFilters=filters=>['q','status','year','unit_id'].some(key=>!!filters?.[key]);
const qualityDescription={accepted:'达到本地准入规则的资料，可供研究复用；具体报考字段仍需逐项核验。',pending:'来源、正文或适用年度仍待补查，暂不供研究自动复用。',excluded:'已拦截无效正文、导航片段或个人名单，仅保留来源与拦截理由。',all:'全部来源按质量分层保存，自动复用只采用已达到准入规则的资料。'};

function qualityOf(entry){
 const quality=object(entry.quality),state=quality.state==='excluded'?'excluded':quality.version===1&&quality.state==='accepted'&&quality.reusable===true?'accepted':'pending';
 const reasons=list(quality.reasons).filter(reason=>typeof reason?.message==='string'&&reason.message).slice(0,8);
 return {state,reasons,materialType:text(quality.material_type),reusable:state==='accepted'};
}

function reasonList(entry){
 const quality=qualityOf(entry),fallback=quality.state==='accepted'?'已达到本地准入规则，具体报考字段仍需另行核验。':quality.state==='excluded'?'未满足准入条件，仅保留来源与版本信息。':'尚未完成质量检查，暂不供研究自动复用。';
 return `<ul>${quality.reasons.length?quality.reasons.map(reason=>`<li>${esc(reason.message.slice(0,600))}</li>`).join(''):`<li>${fallback}</li>`}</ul>`;
}

function admissionDetails(entry,row=false){
 const quality=qualityOf(entry),type={catalog:'招生目录',subjects:'考试科目',fees:'费用与学制',transfer:'调剂规则',interview:'复试规则',admissions:'招生规则',scores:'分数线',unknown:'资料类型待确认'}[quality.materialType]||'资料类型待确认';
 return `<details class="${row?'yk-entry-quality':'yk-admission-details'}" ${!row&&quality.state==='excluded'?'open':''}><summary>${row?quality.state==='excluded'?'拦截原因':quality.state==='pending'?'为什么待核':'查看准入依据':'准入依据'}</summary>${!row?`<p class="yk-material-type">材料类型：${esc(type)}</p>`:''}${reasonList(entry)}</details>`;
}

function qualityTabs(state){
 const current=qualityFilter(state.filters?.quality),counts=object(state.summary?.quality_counts),allCount=['accepted','pending','excluded'].every(key=>Number.isInteger(counts[key])&&counts[key]>=0)?counts.accepted+counts.pending+counts.excluded:state.summary?.total;
 return `<section class="yk-quality-section"><div class="yk-quality-caption"><span>资料准入</span><small>全库统计</small></div><div class="yk-quality-tabs" role="tablist" aria-label="全库准入分区">${qualitySections.map(([value,label])=>{const amount=value==='all'?allCount:counts[value];return `<button type="button" role="tab" id="yk-quality-${value}" aria-controls="yk-library-results" data-knowledge-quality="${value}" aria-selected="${current===value}"><span>${label}</span><strong>${state.loadStatus==='ready'&&Number.isInteger(amount)&&amount>=0?amount:'—'}</strong></button>`;}).join('')}</div><p class="yk-quality-description">${qualityDescription[current]}</p></section>`;
}

function emptyLibrary(state){
 const quality=qualityFilter(state.filters?.quality),filtered=hasFilters(state.filters),title=quality==='accepted'?(filtered?'当前条件下没有可复用资料':'还没有可复用资料'):quality==='pending'?'当前没有相关待核线索':quality==='excluded'?'当前没有相关拦截记录':'还没有保存相关资料';
 const message=filtered?'试试其他关键词，或清除筛选查看当前分区。':quality==='accepted'?'来源会先经过质量检查，待核与已拦截资料不会混入这里。':qualityDescription[quality];
 return `<div class="yk-empty"><span>${icon('book')}</span><h2>${title}</h2><p>${message}</p>${filtered?'<button type="button" class="yk-primary" data-knowledge-action="reset">清除当前筛选</button>':quality==='accepted'?'<button type="button" class="yk-primary" data-knowledge-quality="pending">查看待核线索</button>':''}</div>`;
}

function filtersView(state){
 const filters=object(state.filters),facets=object(state.facets),units=list(facets.units),years=list(facets.years).filter(Number.isInteger);
 return `<form id="yk-search-form" class="yk-search-form" role="search"><div class="yk-search-field"><label class="yk-sr-only" for="yk-query">搜索已保存的研究资料</label>${icon('search')}<input id="yk-query" name="q" type="search" maxlength="240" value="${esc(filters.q||'')}" placeholder="搜索学校、专业、科目或资料标题"><button type="submit">搜索</button></div><div class="yk-filters"><label>资料类型<select name="status"><option value="">全部资料</option>${kinds.map(([value,title])=>`<option value="${value}"${selected(filters.status,value)}>${title}</option>`).join('')}</select></label><label>招生单位<select name="unit_id"><option value="">全部已保存单位</option>${units.map(unit=>`<option value="${esc(unit.unit_id)}"${selected(filters.unit_id,unit.unit_id)}>${esc(unit.unit_name||unit.unit_id)}</option>`).join('')}</select></label><label>来源年度<select name="year"><option value="">全部年度</option>${years.sort((a,b)=>b-a).map(value=>`<option value="${value}"${selected(filters.year,value)}>${value} 年</option>`).join('')}</select></label>${hasFilters(filters)?'<button type="button" class="yk-reset" data-knowledge-action="reset">清除筛选</button>':''}</div></form>`;
}

function entryView(entry,activeId,query=''){
 const link=safeLink(entry.url),broken=unavailable(entry),quality=qualityOf(entry),excerpt=quality.state==='excluded'?'正文已拦截，仅保留来源信息与准入原因。':broken?'原文留档待复核，暂不展示摘录。':entry.text_origin==='metadata_only'?text(entry.content_notice)||'仅保留来源信息，不展示个人名单正文。':entry.status==='official_original_read'&&!text(query).trim()?'原文已留档，打开详情查看摘录与来源。':text(entry.excerpt).slice(0,220);
 return `<article class="yk-entry ${entry.id===activeId?'is-selected':''}" data-quality-state="${quality.state}"><button type="button" class="yk-entry-open" data-knowledge-open="${esc(entry.id)}" aria-pressed="${entry.id===activeId}"><div class="yk-entry-top"><div class="yk-entry-tags"><span class="yk-quality-badge is-${quality.state}">${qualityLabel(quality.state)}</span><span class="yk-kind ${entry.status==='official_original_read'&&!broken?'is-original':''}">${broken?'原文待复核':esc(label(entry.status))}</span></div><span>${esc(year(entry))}${!entry.year&&entry.requested_year?` · 检索目标 ${esc(entry.requested_year)}`:''}</span></div><h2>${esc(entry.title||'资料标题待补齐')}</h2><p class="yk-entry-meta">${esc(entry.unit_name||'招生单位待确认')}${link?`<span>·</span>${esc(new URL(link).hostname)}`:''}</p>${excerpt?`<p class="yk-entry-excerpt">${esc(excerpt)}</p>`:''}<div class="yk-entry-footer"><span>最近存入 ${esc(date(entry.last_seen_at||entry.first_seen_at))}</span><span>${sourceCount(entry)>1?`${sourceCount(entry)} 次研究引用`:'查看来源详情'} ${icon('arrow')}</span></div></button>${admissionDetails(entry,true)}</article>`;
}

function pageExcerpts(entry){
 const pages=list(entry.pages).filter(page=>Number.isInteger(page?.page)&&page.page>0&&typeof page.text==='string'&&page.text).slice(0,8);
 return pages.length?`<details class="yk-page-excerpts"><summary>查看页内摘录</summary>${pages.map(page=>`<section><h4>第 ${page.page} 页</h4><blockquote>${esc(page.text.slice(0,2400))}</blockquote></section>`).join('')}<p>仅展示已保存的页内摘录，完整页内容请查看官方来源。</p></details>`:'';
}

function reader(entry){
 const link=safeLink(entry.url),broken=unavailable(entry),original=entry.status==='official_original_read',lead=entry.status==='search_lead',metadataOnly=entry.text_origin==='metadata_only',quality=qualityOf(entry),excluded=quality.state==='excluded';
 const hideBody=metadataOnly||excluded,contentLabel=hideBody?'仅保留来源信息':lead?'搜索摘要':entry.status==='published_snapshot'?'已收录资料摘录':entry.text_origin==='captured_excerpt'?'留档原文摘录':'留档原文';
 const body=hideBody?'':lead?text(entry.excerpt):text(entry.text||entry.excerpt);
 const emptyBody=excluded?'正文已拦截，仅保留来源信息与准入原因。':metadataOnly?entry.content_notice||'材料可能涉及个人名单，仅保留来源信息，不在知识库展示正文。':'这份来源尚未保存可展示的正文，可通过官方链接继续核对。';
 const documentBody=broken?`<section class="yk-integrity-notice" role="status">${icon('shield')}<div><h3>原文留档需要重新核对</h3><p>文件缺失、内容改变或完整性尚未确认，暂不展示旧摘录。请先查看官方来源。</p></div></section>`:`<section class="yk-document"><div class="yk-document-title"><h3>${contentLabel}</h3>${original?`<span>留档 ${esc(date(entry.checked_at||entry.first_seen_at))}</span>`:''}</div>${body?`<div class="${original?'yk-reader-text':'yk-summary-text'}" tabindex="0" aria-label="${contentLabel}">${esc(body.slice(0,32000))}</div>`:`<p class="yk-muted">${esc(emptyBody)}</p>`}${original&&!hideBody?pageExcerpts(entry):''}${!hideBody&&(entry.text_truncated||entry.text_origin==='captured_excerpt')?'<p class="yk-excerpt-note">这里展示已保存的摘录，完整内容与版式请查看官方来源。</p>':''}</section>`;
 return `<header class="yk-reader-heading">
  <button type="button" class="yk-back" data-knowledge-action="close-detail">${icon('arrow')}返回资料列表</button>
  <div class="yk-entry-tags"><span class="yk-quality-badge is-${quality.state}">${qualityLabel(quality.state)}</span><span class="yk-kind ${original&&!broken?'is-original':''}">${broken?'原文待复核':esc(label(entry.status))}</span></div>
  <h2 id="yk-reader-title" tabindex="-1">${esc(entry.title||'资料标题待补齐')}</h2><p>${esc(entry.unit_name||'招生单位待确认')}<span>·</span>${esc(year(entry))}</p>
  ${!entry.year&&entry.requested_year?`<p class="yk-requested-year">检索目标 ${esc(entry.requested_year)} 年；来源本身的年度尚待核验。</p>`:''}
  ${link?`<a class="yk-original-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer">${lead?'打开来源网页':'查看官方来源'} ${icon('arrowUp')}</a>`:'<p class="yk-muted">来源链接尚待核对</p>'}
 </header><div class="yk-reader-content">
  <section class="yk-quality-notice is-${quality.state}"><p>${qualityDescription[quality.state]}</p>${admissionDetails(entry)}</section>
  ${documentBody}
  <div class="yk-boundary">${icon('shield')}<p>${lead?'搜索摘要只是发现线索，不能当作官方逐字原文或报考结论。':entry.status==='published_snapshot'?'已收录资料保留其原有核验范围，不表示本次重新联网核对。':'原文已留档，字段仍需逐项核验。保存不等于当年规则已确认。'}</p></div>
  <details class="yk-proof-details"><summary>来源与留档细节</summary><dl><div><dt>首次存入</dt><dd>${esc(date(entry.first_seen_at))}</dd></div><div><dt>最近收录</dt><dd>${esc(date(entry.last_seen_at))}</dd></div><div><dt>来源引用</dt><dd>${sourceCount(entry)} 次研究</dd></div><div><dt>内容版本</dt><dd>${Math.max(1,count(entry.version_count))} 个版本</dd></div>${entry.sha256?`<div class="yk-hash"><dt>SHA-256</dt><dd>${esc(entry.sha256)}</dd></div>`:''}${entry.source_group?`<div><dt>同源标识</dt><dd>${esc(entry.source_group)}</dd></div>`:''}</dl><p>相同原始内容的多个链接或多次引用，不算多份独立印证。</p></details>
 </div>`;
}

export function renderKnowledge(state={}){
 const entries=list(state.entries).map(entry=>state.detailStatus==='ready'&&entry.id===state.detail?.id?{...entry,...state.detail}:entry),ready=state.loadStatus==='ready',page=Math.max(1,count(state.page)),pages=Math.max(1,Math.ceil(count(state.total)/(count(state.pageSize)||12))),detailOpen=!!state.detailId;
 const current=qualityFilter(state.filters?.quality);
 const content=state.loadStatus==='error'?`<div class="yk-empty" role="alert"><span>${icon('info')}</span><h2>知识库暂时未能读取</h2><p>${esc(state.error||'请检查本地服务后重试。')}</p><button type="button" class="yk-primary" data-knowledge-action="retry">重新读取 ${icon('arrow')}</button></div>`:!ready?'<div class="yk-empty" role="status"><span class="yk-loading-dot"></span><h2>正在读取知识库</h2><p>查找已保存的来源与原文留档。</p></div>':!entries.length?emptyLibrary(state):`<div class="yk-entry-list">${entries.map(entry=>entryView(entry,state.detailId,state.filters?.q)).join('')}</div>`;
 const detail=detailOpen?`<section class="yk-reader" aria-label="资料阅读面板">${state.detailStatus==='ready'&&state.detail?reader(state.detail):state.detailStatus==='error'?`<div class="yk-detail-state" role="alert"><button type="button" class="yk-back" data-knowledge-action="close-detail">${icon('arrow')}返回资料列表</button><h2 tabindex="-1">这份资料暂时无法读取</h2><p>${esc(state.detailError||'请稍后重试。')}</p><button type="button" class="yk-primary" data-knowledge-action="retry-detail">重新读取</button></div>`:'<div class="yk-detail-state" role="status"><button type="button" class="yk-back" data-knowledge-action="close-detail">返回资料列表</button><h2>正在打开来源</h2><p>读取留档信息与原文摘录。</p></div>'}</section>`:'';
 return `<section class="yk-page"><button type="button" class="yk-back" data-knowledge-action="back">${icon('arrow')}返回研究</button>
  <header class="yk-heading"><div><span class="yk-eyebrow">从一次查证，到持续积累</span><h1>研究知识库</h1><p>按质量准入积累，通过检查后再复用。<br>原文留档不等于字段已核验，年份与来源始终保留。</p></div><div class="yk-library-mark" aria-hidden="true">${icon('book')}</div></header>
  ${qualityTabs(state)}${filtersView(state)}
  <div class="yk-list-heading"><span>${ready?`找到 ${count(state.total)} 份${current==='all'?'资料':qualityLabel(current)}`:'研究资料'}</span><span>当前筛选结果 · 资料类型与准入质量分别核对</span></div>
  <div class="yk-layout ${detailOpen?'is-reading':''}" id="yk-library-results" role="tabpanel" aria-labelledby="yk-quality-${current}"><div class="yk-list-column">${content}${ready&&count(state.total)>0?`<nav class="yk-pagination" aria-label="知识库分页"><span>第 ${page} / ${pages} 页</span><div><button type="button" data-knowledge-action="previous" ${page<=1?'disabled':''}>上一页</button><button type="button" data-knowledge-action="next" ${page>=pages?'disabled':''}>下一页</button></div></nav>`:''}</div>${detail}</div>
 </section>`;
}

function loadStyle(document){
 if(document.querySelector('link[data-yk-knowledge-style]'))return;
 const link=document.createElement('link');link.rel='stylesheet';link.href=new URL('./knowledge.css',import.meta.url).href;link.dataset.ykKnowledgeStyle='';document.head.append(link);
}

export function mountKnowledge(container,{api,onBack,initialEntryId='',initialQuery='',initialQuality='accepted'}={}){
 const document=container.ownerDocument,view=document.defaultView;loadStyle(document);
 let disposed=false,listController,detailController,listGeneration=0,detailGeneration=0,timer,returnPoint={scroll:0,id:''};
 let state={loadStatus:'loading',entries:[],summary:{},facets:{units:[],years:[]},total:0,page:1,pageSize:12,filters:{q:text(initialQuery),status:'',year:'',unit_id:'',quality:qualityFilter(initialQuality)},error:'',detailId:text(initialEntryId),detail:null,detailStatus:initialEntryId?'loading':'none',detailError:''};
 const render=()=>{
  if(disposed)return;
  const active=document.activeElement,focus=container.contains(active)?{name:active.name,start:active.selectionStart,end:active.selectionEnd,quality:active.dataset?.knowledgeQuality}:null;
  container.innerHTML=renderKnowledge(state);
  if(focus?.name){const field=container.querySelector(`[name="${focus.name}"]`);field?.focus({preventScroll:true});if(field&&typeof focus.start==='number'&&['text','search'].includes(field.type))field.setSelectionRange(focus.start,focus.end);}
  else if(focus?.quality)container.querySelector(`[data-knowledge-quality="${qualityFilter(focus.quality)}"]`)?.focus({preventScroll:true});
 };
 const clearDetail=()=>{detailGeneration++;detailController?.abort();state={...state,detailId:'',detail:null,detailStatus:'none',detailError:''};};
 async function loadList(){
  clearTimeout(timer);listController?.abort();listController=new AbortController();const ticket=++listGeneration;
  state={...state,loadStatus:'loading',error:''};render();
  const params=new URLSearchParams({...state.filters,page:String(state.page),page_size:String(state.pageSize)});
  try{
   const result=await api('/api/knowledge?'+params,{signal:listController.signal});
   if(disposed||ticket!==listGeneration)return;
   if(!Array.isArray(result?.entries)||!Number.isInteger(result?.total)||result.total<0)throw new Error('知识库返回的数据格式需要重新检查。');
   const found=object(result.facets),units=new Map([...list(state.facets.units),...list(found.units),...result.entries.filter(entry=>entry.unit_id).map(entry=>({unit_id:entry.unit_id,unit_name:entry.unit_name}))].map(unit=>[unit.unit_id,unit]));
   const years=[...new Set([...list(state.facets.years),...list(found.years),...result.entries.map(entry=>entry.year)].filter(Number.isInteger))];
   state={...state,loadStatus:'ready',entries:result.entries,total:result.total,page:Number.isInteger(result.page)?result.page:state.page,summary:object(result.summary),facets:{units:[...units.values()],years}};render();
  }catch(error){if(disposed||ticket!==listGeneration||error.name==='AbortError')return;state={...state,loadStatus:'error',error:error.message||'本地知识库暂时不可用。'};render();}
 }
 async function openDetail(id,{remember=true}={}){
  if(remember)returnPoint={scroll:view?.scrollY||0,id};
  detailController?.abort();detailController=new AbortController();const ticket=++detailGeneration;
  state={...state,detailId:id,detail:null,detailStatus:'loading',detailError:''};render();
  try{
   const result=await api('/api/knowledge?'+new URLSearchParams({entry_id:id}),{signal:detailController.signal});
   if(disposed||ticket!==detailGeneration)return;
   const detail=object(result?.entry||result);if(detail.id!==id)throw new Error('返回的资料与当前选择不一致，请重新读取。');
   state={...state,detail,detailStatus:'ready'};render();container.querySelector('#yk-reader-title')?.focus({preventScroll:true});container.querySelector('.yk-reader')?.scrollIntoView({block:'nearest',behavior:'auto'});
  }catch(error){if(disposed||ticket!==detailGeneration||error.name==='AbortError')return;state={...state,detailStatus:'error',detailError:error.message||'这份资料暂时无法读取。'};render();}
 }
 const updateFilters=filters=>{clearDetail();state={...state,filters:{...state.filters,...filters},page:1};loadList();};
 const input=event=>{if(event.target.id!=='yk-query')return;state={...state,filters:{...state.filters,q:event.target.value},page:1};clearTimeout(timer);listGeneration++;listController?.abort();timer=setTimeout(()=>{clearDetail();loadList();},240);};
 const change=event=>{if(['status','year','unit_id'].includes(event.target.name))updateFilters({[event.target.name]:event.target.value});};
 const submit=event=>{if(event.target.id==='yk-search-form'){event.preventDefault();clearDetail();state={...state,page:1};loadList();}};
 const click=event=>{
  const button=event.target.closest?.('[data-knowledge-action],[data-knowledge-open],[data-knowledge-quality]');if(!button||!container.contains(button)||button.disabled)return;
  if(button.dataset.knowledgeOpen){openDetail(button.dataset.knowledgeOpen);return;}
  if(button.dataset.knowledgeQuality){const next=qualityFilter(button.dataset.knowledgeQuality);if(next!==state.filters.quality)updateFilters({quality:next});return;}
  const action=button.dataset.knowledgeAction;
  if(action==='back')onBack?.();
  if(action==='retry')loadList();
  if(action==='retry-detail')openDetail(state.detailId,{remember:false});
  if(action==='close-detail'){clearDetail();render();view?.scrollTo(0,returnPoint.scroll);[...container.querySelectorAll('[data-knowledge-open]')].find(item=>item.dataset.knowledgeOpen===returnPoint.id)?.focus({preventScroll:true});}
  if(action==='reset'){clearDetail();state={...state,filters:{q:'',status:'',year:'',unit_id:'',quality:state.filters.quality},page:1};loadList();}
  if(action==='next'||action==='previous'){clearDetail();state={...state,page:Math.max(1,state.page+(action==='next'?1:-1))};loadList();container.querySelector('.yk-list-heading')?.scrollIntoView({block:'start'});}
 };
 container.addEventListener('input',input);container.addEventListener('change',change);container.addEventListener('submit',submit);container.addEventListener('click',click);
 render();loadList();if(initialEntryId)openDetail(initialEntryId,{remember:false});
 return()=>{disposed=true;listGeneration++;detailGeneration++;clearTimeout(timer);listController?.abort();detailController?.abort();container.removeEventListener('input',input);container.removeEventListener('change',change);container.removeEventListener('submit',submit);container.removeEventListener('click',click);};
}
