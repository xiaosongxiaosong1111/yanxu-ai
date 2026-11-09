import {esc} from './workbench-model.mjs';
import {icon} from './icons.mjs';
import {nextTabIndex} from './view-state.mjs';
import {createKnowledgeReader,currentValidity,validityLabel,structuredEntry,readerWorkbench,maintenanceView} from './knowledge-reader.mjs';

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
const validSegment=value=>/^sg_[a-f0-9]{32}$/.test(text(value))?value:'';
export function knowledgeLocation(filters={},page=1,detailId='',segmentId=''){
 const params=new URLSearchParams(),query=text(filters.q).trim();
 if(query)params.set('q',query);
 for(const key of ['status','year','unit_id'])if(text(filters[key]))params.set(key,text(filters[key]));
 const quality=qualityFilter(filters.quality);if(quality!=='accepted')params.set('quality',quality);
 if(Number.isInteger(page)&&page>1)params.set('page',String(page));
 if(text(detailId))params.set('entry_id',text(detailId));
 if(text(detailId)&&validSegment(segmentId))params.set('segment_id',segmentId);
 return '#/knowledge'+(params.size?'?'+params.toString():'');
}
const hasFilters=filters=>['q','status','year','unit_id'].some(key=>!!filters?.[key]);
const qualityDescription={accepted:'达到本地准入规则的资料，可供研究复用；具体报考字段仍需逐项核验。',pending:'来源、正文或适用年度仍待补查，暂不供研究自动复用。',excluded:'已拦截无效正文、导航片段或个人名单，仅保留来源与拦截理由。',all:'全部来源按质量分层保存，自动复用只采用已达到准入规则的资料。'};

const degradationNames={embedding_not_configured:'向量模型尚未配置',embedding_model_not_ready:'本机模型尚未准备',embedding_profile_unavailable:'索引绑定的模型配置已不可用',model_not_verified:'向量连接尚未验证',vector_not_ready:'语义索引尚未建立',vector_unavailable:'向量服务暂时不可用',vector_remote_requires_action:'远程语义检索需要主动提交',vector_profile_changed:'查询模型与索引不一致'};
const finite=value=>Number.isInteger(value)&&value>=0?value:null;
function hybridUsage(result){
 const usage=object(result.usage),known=result.usage_known===true&&finite(usage.input_tokens)!==null,tokens=finite(usage.input_tokens);
 const calls=finite(usage.embedding_calls),ok=finite(usage.successful_embedding_calls);
 return `向量调用 ${calls===null?'未知':calls} 次（成功 ${ok===null?'未知':ok}） · ${known?`输入 ${tokens} token`:'输入 token 未知'}`;
}
function hybridResultView(result){
 const retrieval=object(result.retrieval),segments=list(result.segments),degraded=retrieval.degraded===true,mode=retrieval.mode==='hybrid'?'混合检索':'关键词降级';
 const reason=degradationNames[retrieval.degraded_reason]||text(retrieval.degraded_reason)||'语义通道暂时不可用';
 const total=finite(result.returned_count),candidate=finite(result.candidate_count),latency=finite(retrieval.latency?.total_ms);
 return `<section class="yk-hybrid-results" aria-label="混合检索结果"><div class="yk-hybrid-head"><div><span class="yk-hybrid-mode ${degraded?'is-degraded':''}">${mode}</span><strong>当前范围结果</strong></div><span>返回 ${total===null?'未知':total} 条 · 有限候选 ${candidate===null?'未知':candidate} 条</span></div>${degraded?`<p class="yk-hybrid-degraded" role="status">语义通道未参与：${esc(reason)}。已如实回退到关键词检索，结果仍可精读原文。</p>`:''}<p class="yk-hybrid-usage">${esc(hybridUsage(result))}${latency===null?'':' · 总耗时 '+latency+' ms'}<span>· 主动提交才可能使用向量模型，输入和轮询不会后台计费</span></p><p class="yk-hybrid-notice">${esc(retrieval.notice||'仅在当前可核验资料的有限候选中检索，语义相近不等于科目、资格或报考条件相同。')}</p>${segments.length?`<div class="yk-hybrid-list">${segments.map(segment=>{const source=object(segment.source),methods=list(segment.retrieval_methods),semantic=methods.includes('semantic'),page=finite(segment.page_start);const reasonText=semantic&&methods.includes('keyword')?'关键词与语义同时命中':semantic?'语义候选定位':'关键词命中';return `<button type="button" class="yk-hybrid-hit" data-knowledge-open="${esc(segment.entry_id)}" data-knowledge-hybrid-segment="${esc(segment.id)}"><div><span class="yk-hybrid-hit-kind">${esc(reasonText)}</span><span>${esc(source.unit_name||'招生单位待确认')} · ${Number.isInteger(source.year)?source.year+' 年':'年度待核验'}${page===null?'':' · 第 '+page+' 页'}</span></div><strong>${esc(source.title||'资料标题待补齐')}</strong><p>${esc(text(segment.text).slice(0,220))}</p><small>规则重排 ${esc(text(segment.rerank?.version)||'rule_v1')} · 打开 N3 精读原文</small></button>`;}).join('')}</div>`:`<div class="yk-hybrid-empty" role="status"><strong>当前范围内没有可核验的匹配片段</strong><p>这表示已保存资料中的有限候选没有命中，不代表全国所有学校都没有相关资料。可调整学校、年度或关键词后重新提交。</p></div>`}<p class="yk-boundary-note">以上只代表当前范围的有限候选，不代表全国全部学校或全部材料；语义候选只用于定位，最终是否适用仍须回到原文核对学校、年度、科目与状态。</p></section>`;
}
function hybridPanel(state){
 const hybrid=object(state.hybrid);if(!hybrid.status||hybrid.status==='idle')return '';
 if(hybrid.status==='loading')return `<section class="yk-hybrid-results is-loading" role="status"><div class="yk-hybrid-head"><div><span class="yk-hybrid-mode">混合检索</span><strong>正在检索当前范围</strong></div></div><p class="yk-muted">这是你主动提交的一次检索；关键词列表保持原位。</p></section>`;
 if(hybrid.status==='error')return `<section class="yk-hybrid-results is-error" role="alert"><div class="yk-hybrid-head"><div><span class="yk-hybrid-mode is-degraded">检索未完成</span><strong>混合检索暂时不可用</strong></div></div><p>${esc(hybrid.error||'请稍后重试。')}</p><button type="button" class="yk-primary" data-knowledge-action="hybrid-retry">重新提交</button><p class="yk-boundary-note">关键词资料列表仍可使用，原分页不受影响。</p></section>`;
 return hybridResultView(object(hybrid.result));
}

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
 return `<section class="yk-quality-section"><div class="yk-quality-caption"><span>资料准入</span><small>全库统计</small></div><div class="yk-quality-tabs" role="tablist" aria-label="全库准入分区">${qualitySections.map(([value,label])=>{const amount=value==='all'?allCount:counts[value];return `<button type="button" role="tab" id="yk-quality-${value}" aria-controls="yk-library-results" data-knowledge-quality="${value}" aria-selected="${current===value}" tabindex="${current===value?0:-1}"><span>${label}</span><strong>${state.loadStatus==='ready'&&Number.isInteger(amount)&&amount>=0?amount:'—'}</strong></button>`;}).join('')}</div><p class="yk-quality-description">${qualityDescription[current]}</p></section>`;
}

function emptyLibrary(state){
 const quality=qualityFilter(state.filters?.quality),filtered=hasFilters(state.filters),title=quality==='accepted'?(filtered?'当前条件下没有可复用资料':'还没有可复用资料'):quality==='pending'?'当前没有相关待核线索':quality==='excluded'?'当前没有相关拦截记录':'还没有保存相关资料';
 const message=filtered?'试试其他关键词，或清除筛选查看当前分区。':quality==='accepted'?'来源会先经过质量检查，待核与已拦截资料不会混入这里。':qualityDescription[quality];
 return `<div class="yk-empty"><span>${icon('book')}</span><h2>${title}</h2><p>${message}</p>${filtered?'<button type="button" class="yk-primary" data-knowledge-action="reset">清除当前筛选</button>':quality==='accepted'?'<button type="button" class="yk-primary" data-knowledge-quality="pending">查看待核线索</button>':''}</div>`;
}

function filtersView(state){
 const filters=object(state.filters),facets=object(state.facets),units=list(facets.units),years=list(facets.years).filter(Number.isInteger),expansion=object(state.queryExpansion),expansionNotice=text(expansion.notice),requested=text(expansion.requested),effective=text(expansion.effective);
 return `<form id="yk-search-form" class="yk-search-form" role="search"><div class="yk-search-field"><label class="yk-sr-only" for="yk-query">搜索已保存的研究资料</label>${icon('search')}<input id="yk-query" name="q" type="search" maxlength="240" value="${esc(filters.q||'')}" placeholder="搜索学校、专业、科目或资料标题"><button type="submit">搜索</button><button type="button" class="yk-hybrid-trigger" data-knowledge-action="hybrid-search" ${object(state.hybrid).status==='loading'?'disabled':''}>${object(state.hybrid).status==='loading'?'检索中…':'混合检索'}</button></div><p class="yk-hybrid-hint">“搜索”只读关键词列表；“混合检索”由你主动提交一次，可能使用已保存的向量模型。</p>${expansionNotice?`<p class="yk-hybrid-hint" role="status">${esc(expansionNotice)}${requested&&effective?`（${esc(requested)} → ${esc(effective)}）`:''}</p>`:''}<div class="yk-filters"><label>资料类型<select name="status"><option value="">全部资料</option>${kinds.map(([value,title])=>`<option value="${value}"${selected(filters.status,value)}>${title}</option>`).join('')}</select></label><label>招生单位<select name="unit_id"><option value="">全部已保存单位</option>${units.map(unit=>`<option value="${esc(unit.unit_id)}"${selected(filters.unit_id,unit.unit_id)}>${esc(unit.unit_name||unit.unit_id)}</option>`).join('')}</select></label><label>来源年度<select name="year"><option value="">全部年度</option>${years.sort((a,b)=>b-a).map(value=>`<option value="${value}"${selected(filters.year,value)}>${value} 年</option>`).join('')}</select></label>${hasFilters(filters)?'<button type="button" class="yk-reset" data-knowledge-action="reset">清除筛选</button>':''}</div></form>`;
}

function entryView(entry,activeId,query=''){
 const link=safeLink(entry.url),broken=unavailable(entry),quality=qualityOf(entry),excerpt=currentValidity(entry)!=='active'?`${validityLabel(entry)}，暂不展示旧摘录。`:quality.state==='excluded'?'正文已拦截，仅保留来源信息与准入原因。':broken?'原文留档待复核，暂不展示摘录。':entry.text_origin==='metadata_only'?text(entry.content_notice)||'仅保留来源信息，不展示个人名单正文。':entry.status==='official_original_read'&&!text(query).trim()?'原文已留档，打开详情查看摘录与来源。':text(entry.excerpt).slice(0,220);
 return `<article class="yk-entry ${entry.id===activeId?'is-selected':''}" data-quality-state="${quality.state}"><button type="button" class="yk-entry-open" data-knowledge-open="${esc(entry.id)}" aria-pressed="${entry.id===activeId}"><div class="yk-entry-top"><div class="yk-entry-tags"><span class="yk-quality-badge is-${quality.state}">${qualityLabel(quality.state)}</span><span class="yk-kind ${entry.status==='official_original_read'&&!broken?'is-original':''}">${broken?'原文待复核':esc(label(entry.status))}</span></div><span>${esc(year(entry))}${!entry.year&&entry.requested_year?` · 检索目标 ${esc(entry.requested_year)}`:''}</span></div><h2>${esc(entry.title||'资料标题待补齐')}</h2><p class="yk-entry-meta">${esc(entry.unit_name||'招生单位待确认')}${link?`<span>·</span>${esc(new URL(link).hostname)}`:''}</p>${excerpt?`<p class="yk-entry-excerpt">${esc(excerpt)}</p>`:''}<div class="yk-entry-footer"><span>最近存入 ${esc(date(entry.last_seen_at||entry.first_seen_at))}</span><span>${sourceCount(entry)>1?`${sourceCount(entry)} 次研究引用`:'查看来源详情'} ${icon('arrow')}</span></div></button>${admissionDetails(entry,true)}</article>`;
}

function pageExcerpts(entry){
 const pages=list(entry.pages).filter(page=>Number.isInteger(page?.page)&&page.page>0&&typeof page.text==='string'&&page.text).slice(0,8);
 return pages.length?`<details class="yk-page-excerpts"><summary>查看页内摘录</summary>${pages.map(page=>`<section><h4>第 ${page.page} 页</h4><blockquote>${esc(page.text.slice(0,2400))}</blockquote></section>`).join('')}<p>仅展示已保存的页内摘录，完整页内容请查看官方来源。</p></details>`:'';
}

function reader(entry,readerState={}){
 const link=safeLink(entry.url),broken=unavailable(entry),original=entry.status==='official_original_read',lead=entry.status==='search_lead',metadataOnly=entry.text_origin==='metadata_only',quality=qualityOf(entry),excluded=quality.state==='excluded';
 const hideBody=metadataOnly||excluded||currentValidity(entry)!=='active',contentLabel=hideBody?'仅保留来源信息':lead?'搜索摘要':entry.status==='published_snapshot'?'已收录资料摘录':entry.text_origin==='captured_excerpt'?'留档原文摘录':'留档原文';
 const body=hideBody?'':lead?text(entry.excerpt):text(entry.text||entry.excerpt);
 const emptyBody=excluded?'正文已拦截，仅保留来源信息与准入原因。':metadataOnly?entry.content_notice||'材料可能涉及个人名单，仅保留来源信息，不在知识库展示正文。':'这份来源尚未保存可展示的正文，可通过官方链接继续核对。';
 const documentBody=broken?`<section class="yk-integrity-notice" role="status">${icon('shield')}<div><h3>原文留档需要重新核对</h3><p>文件缺失、内容改变或完整性尚未确认，暂不展示旧摘录。请先查看官方来源。</p></div></section>`:`<section class="yk-document"><div class="yk-document-title"><h3>${contentLabel}</h3>${original?`<span>留档 ${esc(date(entry.checked_at||entry.first_seen_at))}</span>`:''}</div>${body?`<div class="${original?'yk-reader-text':'yk-summary-text'}" tabindex="0" aria-label="${contentLabel}">${esc(body.slice(0,32000))}</div>`:`<p class="yk-muted">${esc(emptyBody)}</p>`}${original&&!hideBody?pageExcerpts(entry):''}${!hideBody&&(entry.text_truncated||entry.text_origin==='captured_excerpt')?'<p class="yk-excerpt-note">这里展示已保存的摘录，完整内容与版式请查看官方来源。</p>':''}</section>`;
 return `<header class="yk-reader-heading">
  <button type="button" class="yk-back" data-knowledge-action="close-detail">${icon('arrow')}返回资料列表</button>
  <div class="yk-entry-tags"><span class="yk-quality-badge is-${quality.state}">${quality.state==='accepted'&&currentValidity(entry)!=='active'?'准入已通过':qualityLabel(quality.state)}</span><span class="yk-kind ${original&&!broken?'is-original':''}">${broken?'原文待复核':esc(label(entry.status))}</span>${currentValidity(entry)!=='active'?`<span class="yk-validity-badge">${esc(validityLabel(entry))}</span>`:''}</div>
  <h2 id="yk-reader-title" tabindex="-1">${esc(entry.title||'资料标题待补齐')}</h2><p>${esc(entry.unit_name||'招生单位待确认')}<span>·</span>${esc(year(entry))}</p>
  ${!entry.year&&entry.requested_year?`<p class="yk-requested-year">检索目标 ${esc(entry.requested_year)} 年；来源本身的年度尚待核验。</p>`:''}
  ${link?`<a class="yk-original-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer">${lead?'打开来源网页':'查看官方来源'} ${icon('arrowUp')}</a>`:'<p class="yk-muted">来源链接尚待核对</p>'}
 </header><div class="yk-reader-content">
  <section class="yk-quality-notice is-${quality.state}"><p>${currentValidity(entry)!=='active'?`当前${esc(validityLabel(entry))}，不供研究复用。准入检查与资料有效性分别记录。`:qualityDescription[quality.state]}</p>${admissionDetails(entry)}</section>
  ${structuredEntry(entry)||currentValidity(entry)!=='active'?readerWorkbench(entry,readerState):documentBody}
  <div class="yk-boundary">${icon('shield')}<p>${lead?'搜索摘要只是发现线索，不能当作官方逐字原文或报考结论。':entry.status==='published_snapshot'?'已收录资料保留其原有核验范围，不表示本次重新联网核对。':'原文已留档，字段仍需逐项核验。保存不等于当年规则已确认。'}</p></div>
  <details class="yk-proof-details"><summary>来源与留档细节</summary><dl><div><dt>首次存入</dt><dd>${esc(date(entry.first_seen_at))}</dd></div><div><dt>最近收录</dt><dd>${esc(date(entry.last_seen_at))}</dd></div><div><dt>来源引用</dt><dd>${sourceCount(entry)} 次研究</dd></div><div><dt>内容版本</dt><dd>${Math.max(1,count(entry.version_count))} 个版本</dd></div>${entry.sha256?`<div class="yk-hash"><dt>SHA-256</dt><dd>${esc(entry.sha256)}</dd></div>`:''}${entry.source_group?`<div><dt>同源标识</dt><dd>${esc(entry.source_group)}</dd></div>`:''}</dl><p>相同原始内容的多个链接或多次引用，不算多份独立印证。</p></details>
  ${original?maintenanceView(entry,readerState):''}
 </div>`;
}

export function renderKnowledge(state={}){
 const entries=list(state.entries).map(entry=>state.detailStatus==='ready'&&entry.id===state.detail?.id?{...entry,...state.detail}:entry),ready=state.loadStatus==='ready',page=Math.max(1,count(state.page)),pages=Math.max(1,Math.ceil(count(state.total)/(count(state.pageSize)||12))),detailOpen=!!state.detailId;
 const current=qualityFilter(state.filters?.quality);
 const content=state.loadStatus==='error'?`<div class="yk-empty" role="alert"><span>${icon('info')}</span><h2>知识库暂时未能读取</h2><p>${esc(state.error||'请检查本地服务后重试。')}</p><button type="button" class="yk-primary" data-knowledge-action="retry">重新读取 ${icon('arrow')}</button></div>`:!ready?'<div class="yk-empty" role="status"><span class="yk-loading-dot"></span><h2>正在读取知识库</h2><p>查找已保存的来源与原文留档。</p></div>':!entries.length?emptyLibrary(state):`<div class="yk-entry-list">${entries.map(entry=>entryView(entry,state.detailId,state.filters?.q)).join('')}</div>`;
 const detail=detailOpen?`<section class="yk-reader" aria-label="资料阅读面板">${state.detailStatus==='ready'&&state.detail?reader(state.detail,state.reader):state.detailStatus==='error'?`<div class="yk-detail-state" role="alert"><button type="button" class="yk-back" data-knowledge-action="close-detail">${icon('arrow')}返回资料列表</button><h2 tabindex="-1">这份资料暂时无法读取</h2><p>${esc(state.detailError||'请稍后重试。')}</p><button type="button" class="yk-primary" data-knowledge-action="retry-detail">重新读取</button></div>`:'<div class="yk-detail-state" role="status"><button type="button" class="yk-back" data-knowledge-action="close-detail">返回资料列表</button><h2>正在打开来源</h2><p>读取留档信息与原文摘录。</p></div>'}</section>`:'';
 return `<section class="yk-page ${detailOpen?'has-reader':''}"><button type="button" class="yk-back" data-knowledge-action="back">${icon('arrow')}返回研究</button>
  <header class="yk-heading"><div><span class="yk-eyebrow">从一次查证，到持续积累</span><h1>研究知识库</h1><p>按质量准入积累，通过检查后再复用。<br>原文留档不等于字段已核验，年份与来源始终保留。</p></div><div class="yk-library-mark" aria-hidden="true">${icon('book')}</div></header>
  ${qualityTabs(state)}${filtersView(state)}${hybridPanel(state)}
  <div class="yk-list-heading"><span>${ready?`找到 ${count(state.total)} 份${current==='all'?'资料':qualityLabel(current)}`:'研究资料'}</span><span>当前筛选结果 · 资料类型与准入质量分别核对</span></div>
  <div class="yk-layout ${detailOpen?'is-reading':''}" id="yk-library-results" role="tabpanel" aria-labelledby="yk-quality-${current}"><div class="yk-list-column">${content}${ready&&count(state.total)>0?`<nav class="yk-pagination" aria-label="知识库分页"><span>第 ${page} / ${pages} 页</span><div><button type="button" data-knowledge-action="previous" ${page<=1?'disabled':''}>上一页</button><button type="button" data-knowledge-action="next" ${page>=pages?'disabled':''}>下一页</button></div></nav>`:''}</div>${detail}</div>
 </section>`;
}

function loadStyle(document){
 if(document.querySelector('link[data-yk-knowledge-style]'))return;
 const link=document.createElement('link');link.rel='stylesheet';link.href=new URL('./knowledge.css',import.meta.url).href;link.dataset.ykKnowledgeStyle='';document.head.append(link);
 const readerStyle=document.createElement('link');readerStyle.rel='stylesheet';readerStyle.href=new URL('./knowledge-reader.css',import.meta.url).href;document.head.append(readerStyle);
}

export function mountKnowledge(container,{api,onBack,initialEntryId='',initialSegmentId='',initialQuery='',initialQuality='accepted',initialStatus='',initialYear='',initialUnit='',initialPage=1}={}){
 const document=container.ownerDocument,view=document.defaultView;loadStyle(document);
 let disposed=false,listController,detailController,listGeneration=0,detailGeneration=0,hybridController,hybridGeneration=0,timer,composing=false,renderPending=false,returnPoint={scroll:0,id:''},detailPushed=false;
 let state={loadStatus:'loading',entries:[],summary:{},facets:{units:[],years:[]},total:0,page:Math.max(1,Number(initialPage)||1),pageSize:12,filters:{q:text(initialQuery),status:text(initialStatus),year:text(initialYear),unit_id:text(initialUnit),quality:qualityFilter(initialQuality)},error:'',queryExpansion:null,detailId:text(initialEntryId),detailSegmentId:validSegment(initialSegmentId),detail:null,detailStatus:initialEntryId?'loading':'none',detailError:'',hybrid:{status:'idle',result:null,error:'',query:''}};
 let listEntryKnown=!text(initialEntryId);
 const render=()=>{
  if(disposed)return;
  if(composing){renderPending=true;return;}
  const active=document.activeElement,focus=container.contains(active)?{name:active.name,start:active.selectionStart,end:active.selectionEnd,quality:active.dataset?.knowledgeQuality}:null,proofOpen=container.querySelector('.yk-proof-details')?.open,readerScroll=container.querySelector('.yk-reader')?.scrollTop||0;
  container.innerHTML=renderKnowledge(state);
  if(proofOpen&&container.querySelector('.yk-proof-details'))container.querySelector('.yk-proof-details').open=true;
  if(container.querySelector('.yk-reader'))container.querySelector('.yk-reader').scrollTop=readerScroll;
  if(focus?.name&&['q','status','unit_id','year','segment_query','maintenance_reason'].includes(focus.name)){const field=container.querySelector(`[name="${focus.name}"]`);field?.focus({preventScroll:true});if(field&&typeof focus.start==='number'&&(['text','search'].includes(field.type)||field.tagName==='TEXTAREA'))field.setSelectionRange(focus.start,focus.end);}
  else if(focus?.quality)container.querySelector(`[data-knowledge-quality="${qualityFilter(focus.quality)}"]`)?.focus({preventScroll:true});
 };
 const syncUrl=({push=false}={})=>{
  const target=knowledgeLocation(state.filters,state.page,state.detailId,state.detailSegmentId),history=view?.history;
  if(!history||view?.location?.hash===target)return false;
  try{if(push)history.pushState({ykKnowledgeDetail:text(state.detailId)||null},'',target);else history.replaceState(history.state,'',target);}catch{return false;}
  return true;
 };
 const restoreReturnPoint=()=>{view?.scrollTo?.(0,returnPoint.scroll);[...container.querySelectorAll('[data-knowledge-open]')].find(item=>item.dataset.knowledgeOpen===returnPoint.id)?.focus({preventScroll:true});};
 const readerControl=createKnowledgeReader({api,onChange:(reader,shouldRender)=>{state={...state,reader,...(state.detailStatus==='ready'?{detailSegmentId:reader.readStatus==='none'?'':validSegment(reader.readId||reader.read?.segment?.id)}:{})};if(state.detailStatus==='ready')syncUrl();if(shouldRender)render();},onEntryReload:async id=>{await openDetail(id,{remember:false,preserveReader:true});await loadList();}});
 const clearDetail=({sync=true}={})=>{detailGeneration++;detailController?.abort();detailPushed=false;state={...state,reader:{},detailId:'',detailSegmentId:'',detail:null,detailStatus:'none',detailError:''};readerControl.reset();if(sync)syncUrl();};
 const clearHybrid=()=>{hybridGeneration++;hybridController?.abort();state={...state,hybrid:{status:'idle',result:null,error:'',query:''}};};
 async function hybridSearch(){
  if(disposed)return;const query=text(state.filters.q).trim();
  if(!query){state={...state,hybrid:{status:'error',result:null,error:'请先输入学校、专业、科目或年度问题，再主动提交混合检索。',query:''}};render();container.querySelector('#yk-query')?.focus();return;}
  hybridController?.abort();hybridController=new AbortController();const ticket=++hybridGeneration;
  state={...state,hybrid:{status:'loading',result:null,error:'',query}};render();
  const body={q:query,limit:8},unit=text(state.filters.unit_id),year=text(state.filters.year);
  if(unit)body.unit_id=unit;if(/^[0-9]{4}$/.test(year))body.year=Number(year);
  try{
   const result=await api('/api/knowledge/retrieve',{body,signal:hybridController.signal});
   if(disposed||ticket!==hybridGeneration)return;
   if(!result||typeof result!=='object'||!Array.isArray(result.segments))throw new Error('混合检索返回的数据格式需要重新检查。');
   state={...state,hybrid:{status:'ready',result,error:'',query}};render();
  }catch(error){if(disposed||ticket!==hybridGeneration||error.name==='AbortError')return;state={...state,hybrid:{status:'error',result:null,error:error.message||'混合检索暂时不可用，请稍后重试。',query}};render();}
 }
 async function loadList(){
  clearTimeout(timer);listController?.abort();listController=new AbortController();const ticket=++listGeneration;
  state={...state,loadStatus:'loading',error:'',queryExpansion:null};render();
  const params=new URLSearchParams({...state.filters,page:String(state.page),page_size:String(state.pageSize)});
  try{
   const result=await api('/api/knowledge?'+params,{signal:listController.signal});
   if(disposed||ticket!==listGeneration)return;
   if(!Array.isArray(result?.entries)||!Number.isInteger(result?.total)||result.total<0)throw new Error('知识库返回的数据格式需要重新检查。');
   const found=object(result.facets),units=new Map([...list(state.facets.units),...list(found.units),...result.entries.filter(entry=>entry.unit_id).map(entry=>({unit_id:entry.unit_id,unit_name:entry.unit_name}))].map(unit=>[unit.unit_id,unit]));
   const years=[...new Set([...list(state.facets.years),...list(found.years),...result.entries.map(entry=>entry.year)].filter(Number.isInteger))];
   const expansion=object(result.query_expansion),expansionNotice=text(expansion.notice);
   state={...state,loadStatus:'ready',entries:result.entries,total:result.total,page:Number.isInteger(result.page)?result.page:state.page,summary:object(result.summary),facets:{units:[...units.values()],years},queryExpansion:expansionNotice?expansion:null};syncUrl();render();
  }catch(error){if(disposed||ticket!==listGeneration||error.name==='AbortError')return;state={...state,loadStatus:'error',error:error.message||'本地知识库暂时不可用。'};render();}
 }
 async function openDetail(id,{remember=true,preserveReader=false,segmentId='',fromHistory=false}={}){
  if(remember)returnPoint={scroll:view?.scrollY||0,id};
  const push=!state.detailId&&!fromHistory;
  const requestedSegment=validSegment(segmentId)||(preserveReader?state.detailSegmentId:id===initialEntryId?validSegment(initialSegmentId):'');
  detailController?.abort();detailController=new AbortController();const ticket=++detailGeneration;
  state={...state,reader:preserveReader?state.reader:{},detailId:id,detailSegmentId:requestedSegment,detail:null,detailStatus:'loading',detailError:''};if(!preserveReader)readerControl.reset();
  if(!fromHistory){const navigated=syncUrl({push});if(push){detailPushed=navigated;listEntryKnown=true;}}
  render();
  try{
   const result=await api('/api/knowledge?'+new URLSearchParams({entry_id:id}),{signal:detailController.signal});
   if(disposed||ticket!==detailGeneration)return;
   const detail=object(result?.entry||result);if(detail.id!==id)throw new Error('返回的资料与当前选择不一致，请重新读取。');
   state={...state,detail,detailStatus:'ready'};readerControl.open(detail);render();if(requestedSegment)readerControl.read(requestedSegment);if(id===initialEntryId)initialSegmentId='';container.querySelector('#yk-reader-title')?.focus({preventScroll:true});container.querySelector('.yk-reader')?.scrollIntoView({block:'nearest',behavior:'auto'});
  }catch(error){if(disposed||ticket!==detailGeneration||error.name==='AbortError')return;state={...state,detailStatus:'error',detailError:error.message||'这份资料暂时无法读取。'};render();}
 }
 const closeDetailView=()=>{const rewoundFromHistory=view?.history?.state?.ykKnowledgeDetail===state.detailId;if((detailPushed||rewoundFromHistory)&&view?.history){detailPushed=false;view.history.back();return;}clearDetail();render();restoreReturnPoint();};
 const syncFromLocation=()=>{
  if(disposed)return;
  const hash=view?.location?.hash||'';
  if(!(hash==='#/knowledge'||hash.startsWith('#/knowledge?')))return;
  const query=new URLSearchParams(hash.split('?')[1]||''),entryId=text(query.get('entry_id')),segmentId=validSegment(query.get('segment_id'));
  const filters={q:text(query.get('q')),status:text(query.get('status')),year:text(query.get('year')),unit_id:text(query.get('unit_id')),quality:qualityFilter(query.get('quality')||'accepted')};
  const page=Math.max(1,Number(query.get('page'))||1);
  const changed=JSON.stringify(filters)!==JSON.stringify(state.filters)||page!==state.page;
  state={...state,filters,page};
  if(!entryId){
   listEntryKnown=true;detailPushed=false;
   if(state.detailId||state.detail){detailGeneration++;detailController?.abort();state={...state,reader:{},detailId:'',detailSegmentId:'',detail:null,detailStatus:'none',detailError:''};readerControl.reset();}
   render();restoreReturnPoint();
   if(changed)loadList();
   return;
  }
  if(entryId!==state.detailId||segmentId!==state.detailSegmentId){
   detailPushed=listEntryKnown&&view?.history?.state?.ykKnowledgeDetail===entryId;
   openDetail(entryId,{remember:false,fromHistory:true,segmentId});
  }
  if(changed)loadList();
 };
 const updateFilters=filters=>{clearDetail();clearHybrid();state={...state,filters:{...state.filters,...filters},page:1};loadList();};
 const input=event=>{readerControl.input(event.target.name,event.target.value);if(event.target.id!=='yk-query')return;hybridGeneration++;hybridController?.abort();state={...state,filters:{...state.filters,q:event.target.value},page:1,hybrid:{status:'idle',result:null,error:'',query:''}};clearTimeout(timer);listGeneration++;listController?.abort();if(composing||event.isComposing)return;timer=setTimeout(()=>{clearDetail();loadList();},240);};
 const change=event=>{if(['status','year','unit_id'].includes(event.target.name))updateFilters({[event.target.name]:event.target.value});};
 const submit=event=>{if(['yk-search-form','yk-segment-search'].includes(event.target.id)&&composing){event.preventDefault();return;}if(event.target.id==='yk-segment-search'){event.preventDefault();readerControl.search();}if(event.target.id==='yk-search-form'){event.preventDefault();clearDetail();state={...state,page:1};loadList();}};
 const click=event=>{
  const button=event.target.closest?.('[data-knowledge-action],[data-knowledge-open],[data-knowledge-quality],[data-knowledge-segment],[data-knowledge-reader],[data-knowledge-maintenance],[data-knowledge-confirm]');if(!button||!container.contains(button)||button.disabled)return;
  if(readerControl.click(button))return;
  if(button.dataset.knowledgeOpen){openDetail(button.dataset.knowledgeOpen,{segmentId:text(button.dataset.knowledgeHybridSegment)});return;}
  if(button.dataset.knowledgeQuality){const next=qualityFilter(button.dataset.knowledgeQuality);if(next!==state.filters.quality)updateFilters({quality:next});return;}
  const action=button.dataset.knowledgeAction;
  if(action==='back')onBack?.();
  if(action==='retry')loadList();
  if(action==='retry-detail')openDetail(state.detailId,{remember:false});
  if(action==='close-detail'){closeDetailView();return;}
  if(action==='reset'){clearDetail();clearHybrid();state={...state,filters:{q:'',status:'',year:'',unit_id:'',quality:state.filters.quality},page:1};loadList();}
  if(action==='next'||action==='previous'){clearDetail();state={...state,page:Math.max(1,state.page+(action==='next'?1:-1))};loadList();container.querySelector('.yk-list-heading')?.scrollIntoView({block:'start'});}
  if(action==='hybrid-search'||action==='hybrid-retry')hybridSearch();
 };
 const compositionStart=()=>{composing=true;clearTimeout(timer);};
 const compositionEnd=event=>{composing=false;input(event);if(renderPending){renderPending=false;render();}};
 const toggle=event=>{if(event.target.matches?.('.yk-maintenance'))readerControl.toggle(event.target.open);};
 const keydown=event=>{const tab=event.target.closest?.('.yk-quality-tabs [role="tab"]');if(!tab||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const items=[...container.querySelectorAll('.yk-quality-tabs [role="tab"]')],next=nextTabIndex(event.key,items.indexOf(tab),items.length);if(next>=0){items[next].focus({preventScroll:true});items[next].click();}};
 container.addEventListener('input',input);container.addEventListener('change',change);container.addEventListener('submit',submit);container.addEventListener('click',click);container.addEventListener('keydown',keydown);container.addEventListener('compositionstart',compositionStart);container.addEventListener('compositionend',compositionEnd);container.addEventListener('toggle',toggle,true);view?.addEventListener?.('popstate',syncFromLocation);
 render();loadList();if(initialEntryId)openDetail(initialEntryId,{remember:false});
 return()=>{disposed=true;listGeneration++;detailGeneration++;hybridGeneration++;clearTimeout(timer);listController?.abort();detailController?.abort();hybridController?.abort();readerControl.dispose();container.removeEventListener('input',input);container.removeEventListener('change',change);container.removeEventListener('submit',submit);container.removeEventListener('click',click);container.removeEventListener('keydown',keydown);container.removeEventListener('compositionstart',compositionStart);container.removeEventListener('compositionend',compositionEnd);container.removeEventListener('toggle',toggle,true);view?.removeEventListener?.('popstate',syncFromLocation);};
}
