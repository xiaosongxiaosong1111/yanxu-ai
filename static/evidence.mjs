import {esc} from './workbench-model.mjs';
import {icon} from './icons.mjs';
import {buildSourceQuery,normalizeSourceFilters,groupEvidence,formatClaimValue,scopeText,claimStatuses,verificationLabels,sourceKinds,officialEvidenceUrl,originalSourceUrl,isStructuredEvidence,programSourceLabel,sourcePage,validateSourcePage,SOURCE_PAGE_SIZE} from './evidence-model.mjs';

const relations={supports:'直接依据',applicability:'适用依据',context:'补充背景',supplement:'补充说明',superseded:'历史版本',conflicts:'存在分歧',out_of_scope:'不适用于当前结论'};
const list=value=>Array.isArray(value)?value:[];
const date=value=>{if(!value)return '未注明';const parsed=new Date(value);return Number.isNaN(parsed.getTime())?'未注明':parsed.toLocaleDateString('zh-CN');};
const safeStatus=value=>Object.hasOwn(claimStatuses,value)?value:'needs_review';
const badge=(status,label)=>`<span class="ev-badge ev-${safeStatus(status)}">${esc(label||claimStatuses[safeStatus(status)])}</span>`;

function versionBadge(source){
 if(source.version_state==='superseded'||source.catalog_active===false&&source.version_state!=='pending_review')return '<span class="ev-version is-history">历史版本 · 已被替代</span>';
 if(source.version_state==='pending_review')return '<span class="ev-version is-pending">新版本待复核</span>';
 if(source.stale===true)return '<span class="ev-version is-pending">来源更新尚未完成</span>';
 if(source.version_state==='current')return `<span class="ev-version">${source.kind==='catalog'?'当前目录版本':'当前留档版本'}</span>`;
 return '';
}

function originalLink(source,label='打开官方原文'){
 const url=originalSourceUrl(source);if(!url)return '<span class="ev-muted">原文链接待补齐</span>';
 if(source.detail_url_kind==='query_page')label='打开官方查询页';
 const target=new URL(url);if(source.page&&target.pathname.toLowerCase().endsWith('.pdf'))target.hash='page='+encodeURIComponent(source.page);
 return `<a class="ev-original" href="${esc(target.href)}" target="_blank" rel="noopener noreferrer">${esc(label)} ${icon('arrowUp')}</a><a class="ev-url" href="${esc(target.href)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`;
}

function requestDetails(source){
 const url=officialEvidenceUrl(source.request_url),method=source.request_method;
 if(!url||!['GET','POST'].includes(method))return '';
 return `<p>原始请求：<code>${esc(method)} ${esc(url)}</code></p>${source.detail_url_kind==='query_page'?'<p>官方查询页可按学院、专业和方向复核；下方字段保留自已保存的查询结果，不冒充正文逐字引语。</p>':''}`;
}

function quoteView(item){
 const outOfScope=item.verification==='out_of_scope'||item.relation==='out_of_scope';
 const relation=outOfScope?relations.out_of_scope:relations[item.relation]||'关联材料',verification=verificationLabels[item.verification]||'核验状态待确认';
 const structured=isStructuredEvidence(item);
 const body=structured?structuredView(item):item.quote?`<blockquote>${esc(item.quote)}</blockquote>`:item.verification==='reference_link'?`<p class="ev-reference-note">${esc(item.note||'此处核对的是原文之间的引用链接，用于证明适用关系；字段数值请看对应直接依据的逐字原文。')}</p>`:'<p class="ev-warning">该项没有可展示的原文摘录，不能仅凭链接确认结论。</p>';
 return `<div class="ev-excerpt"><div class="ev-excerpt-meta"><strong>${esc(relation)}</strong><span>${structured?'字段路径定位':item.page?`第 ${esc(item.page)} 页`:'章节定位见原文'}</span><span>${esc(verification)}</span></div>${body}${item.note&&!structured&&(item.quote||item.verification!=='reference_link')?`<p class="ev-claim-note">${esc(item.note)}</p>`:''}${item.scope?`<p class="ev-scope">依据作用范围：${esc(scopeText(item.scope))}${item.scope.direction_specific===false?' · 专业或单位共用条款':''}</p>`:''}</div>`;
}

function structuredView(item){
 const fields=Object.entries(item.structured_fields).map(([key,value])=>`<p><strong>${esc(key)}</strong><code>${JSON.stringify(value,null,2).split('\n').map(esc).join('<br>')}</code></p>`).join('');
 return `<section class="ev-reference-note ev-file-details" aria-label="原文结构化字段"><strong>原文结构化字段</strong><p>按官方页面中的字段路径与原始值核对，以下内容不是正文引语。</p><p>字段路径<code>${esc(item.field_path)}</code></p>${fields}</section>`;
}

function sourceCard(group){
 const source=group.primary,checked=group.items.some(item=>isStructuredEvidence(item)||Boolean(item.quote)&&(item.verification==='exact_quote'||item.verification==='derived_statistics')),linked=group.items.some(item=>item.verification==='reference_link');
 return `<article class="ev-source-card" data-source-id="${esc(source.document_id||source.id||'')}"><div class="ev-card-top"><span class="ev-publisher">${icon('book')}${esc(source.publisher||'发布单位待核验')}</span><span class="ev-mini-tag">${checked?'保存原文已核对':linked?'引用关系已核对':'来源仍需核验'}</span></div><h3>${esc(source.title||'官方资料，标题待补齐')}</h3>${versionBadge(source)}<dl class="ev-source-meta"><div><dt>适用年度</dt><dd>${source.year?esc(source.year)+' 年':'未明确'}</dd></div><div><dt>发布日期</dt><dd>${esc(date(source.published_at))}</dd></div><div><dt>留档读取</dt><dd>${esc(date(source.checked_at))}</dd></div></dl><div class="ev-source-actions">${originalLink(source,checked||linked?'打开官方原文':'打开来源页面（待核验）')}</div>${group.items.map(quoteView).join('')}${group.urls.length>1?`<details class="ev-file-details"><summary>同一原始文件还有 ${group.urls.length-1} 处引用</summary>${group.urls.filter(url=>url!==originalSourceUrl(source)).map(url=>`<a class="ev-url" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`).join('')}<p>相同原始文件按一组计算，引用次数不代表独立佐证。</p></details>`:''}${source.source_group?`<details class="ev-file-details"><summary>留档详情</summary><p>读取时间表示已保存版本的核对时点，不承诺原网页当前可访问或政策仍适用。</p>${requestDetails(source)}<code>${esc(source.source_group)}</code></details>`:''}</article>`;
}

export function renderEvidence(data,{claimId}={}){
 const claims=list(data?.claims),claim=claims.find(item=>item.id===claimId)||claims[0],program=data?.program||{};
 const head=`<header class="ev-drawer-head"><div><span class="ev-eyebrow">来源与核验</span><h2 id="evidence-title">${esc(claim?.label||'原文依据')}</h2></div><button type="button" class="icon-button" data-ev-action="close" aria-label="关闭来源依据">${icon('close')}</button></header>`;
 if(!claim)return `${head}<div class="ev-drawer-body"><p class="ev-empty">该方向暂无可展示的字段依据。资料缺失不代表专业不存在或无法报考。</p><button type="button" class="btn btn-secondary" data-ev-action="open-sources">浏览已收录来源</button></div>`;
 const status=safeStatus(claim.status),groups=groupEvidence(claim.evidence);
 return `${head}<div class="ev-drawer-body"><p class="ev-program">${esc(programSourceLabel(program))}</p><label class="ev-select-label">核验字段<select name="evidence-claim" aria-label="选择核验字段">${claims.map(item=>`<option value="${esc(item.id)}" ${item.id===claim.id?'selected':''}>${esc(item.label||item.field||'字段')} · ${esc(claimStatuses[safeStatus(item.status)])}</option>`).join('')}</select></label><section class="ev-claim-summary">${badge(status)}${claim.applicability==='unknown'?'<span class="ev-applicability">本专业适用范围待核验</span>':''}<p class="ev-claim-value">${esc(formatClaimValue(claim))}</p><p class="ev-scope">${esc(scopeText(claim.scope||program))}</p>${claim.note?`<p class="ev-claim-note">${esc(claim.note)}</p>`:''}</section>${status==='conflict'?'<p class="ev-conflict" role="status">同一口径存在未解决的分歧。请逐份核对原文，当前不能采用唯一确定值。</p>':''}<div class="ev-evidence-heading"><h3>对应原文</h3><span>${groups.length} 组原始文件</span></div>${groups.length?groups.map(sourceCard).join(''):'<div class="ev-empty"><strong>没有可展示的原文依据</strong><p>当前字段保留未知；后续需要补齐适用范围和官方原文。</p></div>'}<p class="ev-footnote">${esc(data.notice||'依据以已保存原文为准，仍需核对目标年度和具体方向。')}</p><button type="button" class="ev-center-link" data-ev-action="open-sources">浏览全部已收录来源 ${icon('arrow')}</button></div>`;
}

function sourceOverviewCard(source,programs){
 const links=list(source.related_claims).filter(item=>typeof item.program_id==='string'&&typeof item.claim_id==='string');
 const related=[...new Set([...list(source.related_program_ids),...links.map(item=>item.program_id)])];
 const items=related.slice(0,6).map(id=>({id,program:programs.find(item=>item.id===id)}));
 const fallback={catalog:'catalog',fees:'tuition',duration:'duration',enrollment:'enrollment'}[source.kind];
 const primary=links[0]||(fallback&&related.length?{program_id:related[0],claim_id:fallback,label:'对应字段依据'}:null);
 const relatedClaim=link=>{const program=programs.find(item=>item.id===link.program_id);return `<button type="button" data-ev-action="open-evidence" data-program-id="${esc(link.program_id)}" data-claim-id="${esc(link.claim_id)}"><span><strong>${esc(link.label||'对应字段依据')}</strong>${program?`<small>${esc(programSourceLabel(program))}</small>`:''}</span>${icon('arrow')}</button>`;};
 return `<article class="ev-library-card"><div class="ev-card-top"><span class="ev-publisher">${esc(source.publisher||source.unit_name||'发布单位待核验')}</span><span class="ev-mini-tag">${esc(sourceKinds[source.kind]||'招生材料')}</span></div>${versionBadge(source)}<h2>${esc(source.title||'官方材料标题待收录')}</h2><p class="ev-library-scope">${source.year?esc(source.year)+' 年':'年度待确认'} · ${esc(verificationLabels[source.verification]||'保存版本核验状态待确认')}</p>${source.original_format==='structured_fields'?'<p class="ev-muted">原文结构化字段 · 按字段路径与原始值定位</p>':''}<p class="ev-dates">发布 ${esc(date(source.published_at))} <span>·</span> 留档读取 ${esc(date(source.checked_at))}</p><div class="ev-source-actions">${originalLink(source)}${primary?`<button type="button" class="ev-inline-button" data-ev-action="open-evidence" data-program-id="${esc(primary.program_id)}" data-claim-id="${esc(primary.claim_id)}">查看${esc(primary.label||'对应字段依据')} ${icon('arrow')}</button>`:''}</div>${links.length?`<details class="ev-related"><summary>此材料支撑的字段</summary><div>${links.slice(0,6).map(relatedClaim).join('')}${links.length>6?'<p class="ev-muted">此处展示前6项引用；同一材料不按关联字段重复计数。</p>':''}</div></details>`:''}${related.length?`<details class="ev-related"><summary>关联 ${related.length} 个已收录方向</summary><div>${items.map(({id,program},index)=>`<button type="button" data-ev-action="open-program" data-program-id="${esc(id)}">${esc(program?programSourceLabel(program):'查看关联方向 '+(index+1))}${icon('arrow')}</button>`).join('')}${related.length>items.length?`<p class="ev-muted">此处展示前 ${items.length} 个关联方向；同一材料不会按关联方向重复计为多份来源。</p>`:''}</div></details>`:''}<details class="ev-file-details"><summary>来源留档信息</summary><p>原文摘录与字段适用范围，请从关联字段的“查看依据”打开。留档读取时间不能证明原网页当前可访问。</p>${requestDetails(source)}${source.sha256?`<code>${esc(source.sha256)}</code>`:''}</details></article>`;
}

export function renderSources(data,{filters={},units=[],programs=[],page=1,loading=false,error='',revisionChanged=false,showHeading=true}={}){
 const current=normalizeSourceFilters(filters),rows=sourcePage(data,page);
 const inputQuery=typeof filters?.q==='string'?filters.q.slice(0,120):current.q;
 const years=[...new Set([current.year,...list(data?.sources).map(item=>String(item.year||'')),...['2027','2026','2025','2024']])].filter(Boolean).sort().reverse();
 return `<section class="ev-center">${showHeading?'<div class="ev-center-heading"><span class="ev-eyebrow">官方资料库</span><h1>来源与核验</h1><p>从每一份原始材料，找到数据的来处。</p></div>':''}<form class="ev-filters" data-ev-search><label class="ev-search"><span>查找官方材料</span><div>${icon('search')}<input name="source-q" value="${esc(inputQuery)}" placeholder="搜索学校、材料标题" maxlength="120" autocomplete="off"></div></label><label><span>招生单位</span><select name="source-unit"><option value="">全部已收录单位</option>${units.map(unit=>`<option value="${esc(unit.id)}" ${current.unit_id===unit.id?'selected':''}>${esc(unit.name)}</option>`).join('')}</select></label><label><span>资料年度</span><select name="source-year"><option value="">全部年度</option>${years.map(year=>`<option value="${esc(year)}" ${year===current.year?'selected':''}>${esc(year)} 年</option>`).join('')}</select></label><label><span>材料类型</span><select name="source-kind"><option value="">全部类型</option>${Object.entries(sourceKinds).map(([kind,label])=>`<option value="${kind}" ${current.kind===kind?'selected':''}>${esc(label)}</option>`).join('')}</select></label></form><div class="ev-library-summary"><p>${loading?'正在读取已发布资料索引…':error?'来源索引尚未读取成功':`当前筛选 <strong>${Number.isInteger(data?.total)?data.total:rows.total}</strong> 条来源记录 · 本页 <strong>${rows.items.length}</strong> 条`}</p><span>同源转载不算新增独立佐证</span></div>${error?`<div class="ev-load-error" role="alert"><p>${esc(error)}</p><button type="button" class="btn btn-secondary" data-ev-action="retry">${revisionChanged?'读取最新资料':'重新读取来源'}</button></div>`:loading?'<div class="ev-loading" role="status">正在读取已发布资料索引…</div>':rows.items.length?`<div class="ev-library-grid">${rows.items.map(source=>sourceOverviewCard(source,programs)).join('')}</div>`:'<div class="ev-empty"><strong>没有匹配到已收录来源</strong><p>可以调整学校、年份或关键词。没有收录不代表没有官方材料。</p></div>'}${!loading&&!error&&rows.pages>1?`<nav class="ev-pagination" aria-label="来源列表分页"><button type="button" class="btn btn-secondary" data-ev-action="previous" ${rows.page===1?'disabled':''}>上一页</button><span>第 ${rows.page} / ${rows.pages} 页</span><button type="button" class="btn btn-secondary" data-ev-action="next" ${rows.page===rows.pages?'disabled':''}>下一页</button></nav>`:''}<p class="ev-center-note">${esc(data?.notice||'这是上次发布的资料索引。打开具体字段依据后会单独核对保存原文；原网页当前状态与目标年度适用性仍需确认。')}</p></section>`;
}

function loadStyle(document){
 if(document.querySelector('link[data-evidence-style]'))return;
 const link=document.createElement('link');link.rel='stylesheet';link.href=new URL('./evidence.css',import.meta.url).href;link.dataset.evidenceStyle='';document.head.append(link);
}

export function mountEvidence(dialog,{api,programId,claimId,revision,onClose,onOpenSources}={}){
 loadStyle(dialog.ownerDocument);dialog.classList.add('evidence-dialog');dialog.setAttribute('aria-labelledby','evidence-title');
 let data=null,selected=claimId,disposed=false,controller=null,generation=0;
 const render=()=>{if(!disposed)dialog.innerHTML=renderEvidence(data,{claimId:selected});};
 async function load(){
  controller?.abort();controller=new AbortController();const current=++generation;
  dialog.innerHTML=`<header class="ev-drawer-head"><h2 id="evidence-title">原文与依据</h2><button type="button" class="icon-button" data-ev-action="close" aria-label="关闭来源依据">${icon('close')}</button></header><div class="ev-drawer-body ev-loading" role="status">正在核对保存的原文依据…</div>`;
  const version=typeof revision==='string'&&revision&&revision.length<=128?'&revision='+encodeURIComponent(revision):'';
  try{const value=await api('/api/evidence?program_id='+encodeURIComponent(programId||'')+version,{signal:controller.signal});if(!disposed&&current===generation){data=value;render();}}
  catch(error){if(!disposed&&current===generation&&error.name!=='AbortError')dialog.innerHTML=`<header class="ev-drawer-head"><h2 id="evidence-title">原文与依据</h2><button type="button" class="icon-button" data-ev-action="close" aria-label="关闭来源依据">${icon('close')}</button></header><div class="ev-drawer-body ev-load-error" role="alert"><p>${esc(error.message||'暂时无法读取依据。')}</p><button type="button" class="btn btn-secondary" data-ev-action="retry">重新读取依据</button></div>`;}
 }
 const change=event=>{if(event.target.name==='evidence-claim'){selected=event.target.value;render();dialog.querySelector('[name="evidence-claim"]')?.focus({preventScroll:true});}};
 const click=event=>{const button=event.target.closest?.('[data-ev-action]');if(!button||!dialog.contains(button))return;event.preventDefault();event.stopPropagation();if(button.dataset.evAction==='close')onClose?.();if(button.dataset.evAction==='retry')load();if(button.dataset.evAction==='open-sources')onOpenSources?.({unit_id:data?.program?.unit_id,year:data?.program?.year});};
 dialog.addEventListener('change',change);dialog.addEventListener('click',click);load();
 return ()=>{disposed=true;generation++;controller?.abort();dialog.removeEventListener('change',change);dialog.removeEventListener('click',click);};
}

export function mountSources(container,{api,units=[],programs=[],onOpenProgram,onOpenEvidence,onReady,filters:initialFilters={}}={}){
 loadStyle(container.ownerDocument);
 const navigation=container.ownerDocument.defaultView,params=new URLSearchParams(navigation?.location?.hash.split('?')[1]||'');
 let data=null,filters=normalizeSourceFilters({...Object.fromEntries(params),...initialFilters}),page=/^[1-9]\d*$/.test(params.get('page'))?Number(params.get('page')):1,disposed=false,loading=false,error='',errorCode='',revision='',focusPageAction='',timer=null,controller=null,generation=0,composing=false;
 if(!Number.isSafeInteger(page)||page>833334)page=1;
 const syncLocation=()=>{if(!navigation?.history?.replaceState)return;const query=new URLSearchParams();for(const [key,value]of Object.entries(filters))if(value)query.set(key,value);if(page>1)query.set('page',page);navigation.history.replaceState(navigation.history.state,'',(navigation.location.hash.split('?')[0]||'#/sources')+(query.size?'?'+query:''));};
 const render=()=>{
  if(disposed)return;
  const active=container.ownerDocument.activeElement,focus=container.contains(active)?{name:active?.name,start:active?.selectionStart,end:active?.selectionEnd}:null;
  container.innerHTML=renderSources(data,{filters,units,programs,page,loading,error,revisionChanged:errorCode==='revision_changed',showHeading:false});
  if(focus?.name){const input=[...container.querySelectorAll('input,select')].find(item=>item.name===focus.name);input?.focus({preventScroll:true});if(input&&focus.start!==undefined&&input.type!=='select-one')input.setSelectionRange?.(focus.start,focus.end);}
  if(!loading&&!error&&focusPageAction){
   const target=focusPageAction==='filter'?container.querySelector('[name="source-q"]'):container.querySelector(`[data-ev-action="${focusPageAction}"]:not(:disabled)`)||container.querySelector('.ev-pagination button:not(:disabled)')||container.querySelector('[name="source-q"]');
   target?.focus({preventScroll:true});focusPageAction='';
  }
 };
 async function load(){
  controller?.abort();controller=new AbortController();const current=++generation,offset=(page-1)*SOURCE_PAGE_SIZE,expectedRevision=revision;
  loading=true;error='';errorCode='';render();
  try{
   const result=await api(buildSourceQuery(filters,{offset,limit:SOURCE_PAGE_SIZE,revision:expectedRevision}),{signal:controller.signal});
   if(disposed||current!==generation)return;
   const value=validateSourcePage(result,offset);
   if(expectedRevision&&value.revision!==expectedRevision){const failure=new Error('资料已发布新版本，请读取最新资料。');failure.code='revision_changed';throw failure;}
   const rows=sourcePage(value,page);
   if(page>rows.pages){page=rows.pages;revision=value.revision;syncLocation();return load();}
   data=value;revision=value.revision;page=rows.page;syncLocation();loading=false;render();onReady?.(data);
  }catch(failure){
   if(!disposed&&current===generation&&failure.name!=='AbortError'){loading=false;error=failure.message||'暂时无法读取来源。';errorCode=['revision_changed','stale_revision'].includes(failure.code)?'revision_changed':'';render();}
  }
 }
 function update(name,value){const fields={'source-q':'q','source-unit':'unit_id','source-year':'year','source-kind':'kind'};if(!fields[name])return;filters=normalizeSourceFilters({...filters,[fields[name]]:value});if(name==='source-q')filters={...filters,q:typeof value==='string'?value.slice(0,120):''};page=1;revision='';data=null;focusPageAction='';clearTimeout(timer);controller?.abort();generation++;syncLocation();if(name==='source-q')timer=setTimeout(load,280);else load();}
 const input=event=>{if(event.target.name==='source-q'&&!composing)update(event.target.name,event.target.value);};
 const compositionStart=()=>{composing=true;clearTimeout(timer);controller?.abort();generation++;};
 const compositionEnd=event=>{composing=false;if(event.target.name==='source-q')update(event.target.name,event.target.value);};
 const change=event=>{if(event.target.name!=='source-q')update(event.target.name,event.target.value);};
 const submit=event=>{event.preventDefault();if(composing)return;clearTimeout(timer);load();};
 const click=event=>{
  const button=event.target.closest?.('[data-ev-action]');if(!button||!container.contains(button)||button.disabled)return;
  event.preventDefault();event.stopPropagation();const action=button.dataset.evAction;
  if(action==='retry'){if(errorCode==='revision_changed'){page=1;revision='';data=null;syncLocation();}focusPageAction='filter';load();}
  if(action==='open-program')onOpenProgram?.(button.dataset.programId);
  if(action==='open-evidence')onOpenEvidence?.(button.dataset.programId,button.dataset.claimId,revision);
  if(['next','previous'].includes(action)&&!loading&&!error){const rows=sourcePage(data,page),target=page+(action==='next'?1:-1);if(target<1||target>rows.pages)return;page=target;focusPageAction=action;syncLocation();load();container.querySelector('.ev-filters')?.scrollIntoView({block:'start',behavior:'smooth'});}
 };
 container.addEventListener('input',input);container.addEventListener('change',change);container.addEventListener('submit',submit);container.addEventListener('click',click);container.addEventListener('compositionstart',compositionStart);container.addEventListener('compositionend',compositionEnd);load();
 return ()=>{disposed=true;generation++;clearTimeout(timer);controller?.abort();container.removeEventListener('input',input);container.removeEventListener('change',change);container.removeEventListener('submit',submit);container.removeEventListener('click',click);container.removeEventListener('compositionstart',compositionStart);container.removeEventListener('compositionend',compositionEnd);};
}

export const mountSourceCenter=mountSources;
