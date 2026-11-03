import {safeHttpUrl} from './data.mjs';
import {escapeHtml as esc} from './details.mjs';
import {mountCatalogFacts} from './catalog-facts.mjs';

const labels={recorded_verified:'上次目录对账通过',partial:'部分目录已收录',not_collected:'目录尚未入库',pending:'尚待采集',running:'正在采集',crawling:'正在采集',sources_collected:'原文已采集',collected:'原文已采集',needs_binding:'官网入口待核准',truncated:'仍有页面待采集',restricted:'来源访问受限',failed:'采集失败待重试',not_found:'原文尚未找到'};
const parsingLabels={pending:'尚待解析',ready_for_ingestion:'解析候选待核验',ready:'解析候选待核验',needs_adapter:'原文格式待适配',source_check_failed:'源校验失败',failure:'校验或解析失败',no_catalog_rows:'本批原文未提取目录条目',not_catalog:'本批原文未提取目录条目',restricted:'原文访问受限'};
const count=value=>Number.isFinite(value)?value:0;

function renderFactsDisclosure(unit){
 return Number.isInteger(unit.fact_count)&&unit.fact_count>0?`<details class="collection-facts" data-collection-facts="${esc(unit.unit_id)}" data-facts-year="${esc(unit.year)}"><summary><span>目录信息待完善</span><span>${unit.fact_count} 条原文表格行</span></summary><div class="catalog-facts-panel" data-catalog-facts-panel></div></details>`:'';
}

function renderParsingProgress(unit){
 const gaps=Array.isArray(unit.parsing_gaps)?unit.parsing_gaps.slice(0,12):[];
 const totals=[['ready','解析候选待核验'],['needs_adapter','待适配'],['failure','校验或解析失败'],['not_catalog','未提取目录条目'],['restricted','访问受限']]
  .filter(([key])=>Number.isInteger(unit.parsing_summary?.[key])&&unit.parsing_summary[key]>0)
  .map(([key,label])=>`${label} ${unit.parsing_summary[key]} 份`).join(' · ');
 return `<p class="collection-unit-parsing">原文处理：<strong>${esc(parsingLabels[unit.parsing_status]||'尚待解析')}</strong>${totals?`<br><span class="field-note">${totals}</span>`:''}</p><p class="field-note">解析候选须通过原文与语义核验后入库；未提取条目不代表没有招生目录。</p>${gaps.length?`<details class="collection-parsing-gaps"><summary>查看原文处理缺口（${gaps.length}）</summary><p class="field-note">以下为尚待处理的原文；解析候选通过核验后才会进入招生目录。</p><ul>${gaps.map(gap=>{
  const url=safeHttpUrl(gap.url);
  return `<li>${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">查看这份官方原文 ↗</a>`:'<span>原文链接待核验</span>'}<small>${esc(parsingLabels[gap.status]||'原文处理待核验')}</small><p>${esc(gap.reason||'具体处理原因尚待核验。')}</p></li>`;
 }).join('')}</ul></details>`:''}`;
}
export function renderCollectionSummary(summary={}){
 return `<div class="coverage-facts"><div><span>院校索引</span><strong>${count(summary.indexed_units)}</strong><small>含高校及科研单位</small></div><div><span>当年招生已确认</span><strong>${count(summary.annual_confirmed_units)}</strong><small>${summary.annual_official_total==null?'年度招生单位全集尚未核准':'已核准年度单位总数 '+count(summary.annual_official_total)}</small></div><div><span>有专业目录</span><strong>${count(summary.units_with_programs)}</strong><small>共 ${count(summary.program_count)} 条专业或方向</small></div><div><span>${summary.annual_scope_status==='verified'?'当年目录待入库':'索引中目录待入库'}</span><strong>${count(summary.annual_scope_status==='verified'?summary.annual_units_without_programs:summary.catalog_states?.not_collected)}</strong><small>不代表未招生或没有专业</small></div></div><p class="info-callout"><strong>全国完整目录发布门槛尚未通过</strong><br>单位、院系、专业及全部初试科目逐项对账；已发现或下载原文不等于目录已收全。</p>`;
}

export function renderCollectionUnits(units=[]){
 if(!units.length)return '<p class="field-note">该筛选条件下没有索引记录，可更换名称或状态查询。</p>';
 return `<ul class="collection-unit-list">${units.map(unit=>{
  const sources=(unit.sources||[]).filter(source=>safeHttpUrl(source.url));
  const official=safeHttpUrl(unit.official_url);
  return `<li class="collection-unit"><div class="collection-unit-heading"><strong>${esc(unit.unit_name)}</strong><span>${esc(unit.province||'地区待核准')} · ${esc(unit.unit_code||'代码待核准')}</span></div><p class="collection-unit-state">${esc(labels[unit.catalog_status]||'目录待核验')} · ${esc(labels[unit.collection_status]||'尚待采集')}${unit.program_count?' · '+count(unit.program_count)+' 条':''}</p>${unit.annual_status==='not_listed'?`<p class="info-callout">${esc(unit.year)} 年公开目录未列为独立招生单位，保留索引供核对；不据此推定该校停止招生。</p>`:''}<p>${esc(unit.missing_reason||'目录状态尚待核验。')}</p>${renderParsingProgress(unit)}${unit.pending_pages?`<p class="field-note">尚有 ${count(unit.pending_pages)} 个页面或附件待处理。</p>`:''}<div class="collection-unit-actions">${official?`<a href="${esc(official)}" target="_blank" rel="noopener noreferrer">官方资料入口 ↗</a>`:''}${unit.program_count?`<button type="button" class="text-link" data-collection-unit="${esc(unit.unit_id)}">查看已收录专业 →</button>`:''}</div>${sources.length?`<details><summary>已保存 ${count(unit.source_count??sources.length)} 份来源</summary><ul>${sources.map(source=>`<li><a href="${esc(safeHttpUrl(source.url))}" target="_blank" rel="noopener noreferrer">${esc(source.title||'查看官方原文')} ↗</a><small>${source.year?esc(source.year)+' 年':'年度待核验'} · ${esc(source.kind||'资料')}</small></li>`).join('')}</ul></details>`:''}${renderFactsDisclosure(unit)}</li>`;
 }).join('')}</ul>`;
}

export function mountCollectionProgress(container,{api,onOpenUnit=()=>{}}){
 let sequence=0,controller,timer,page=1,disposed=false;
 const factsViews=new Map();
 const clearFacts=()=>{for(const cleanup of factsViews.values())cleanup();factsViews.clear();};
 container.innerHTML=`<div data-collection-summary></div><div class="collection-filters"><label class="form-field">查询招生单位索引<input id="coverage-query" type="search" maxlength="120" placeholder="输入学校、研究所或招生单位代码"></label><label class="form-field">采集状态<select name="collection-status"><option value="">全部单位</option><option value="not_collected">目录尚未入库</option><option value="partial">部分目录已收录</option><option value="recorded_verified">上次目录对账通过</option><option value="truncated">仍有页面待采集</option><option value="restricted">来源访问受限</option><option value="failed">采集失败待重试</option><option value="pending">尚待采集</option></select></label></div><p class="field-note" data-collection-total role="status">正在读取全国采集进度…</p><div id="coverage-units"></div><div class="collection-pages" data-collection-pages></div><p class="field-note" data-collection-note></p>`;
 const search=container.querySelector('#coverage-query'),status=container.querySelector('[name="collection-status"]');
 async function load(){
  clearFacts();
  controller?.abort();controller=new AbortController();const id=++sequence;
  try{
   const data=await api('/api/collection-status?'+new URLSearchParams({q:search.value,status:status.value,page:String(page),page_size:'20'}),{signal:controller.signal});
   if(disposed||id!==sequence)return;
   container.querySelector('[data-collection-summary]').innerHTML=renderCollectionSummary(data.summary);
   container.querySelector('#coverage-units').innerHTML=renderCollectionUnits(data.units);
   container.querySelector('[data-collection-total]').textContent=`找到 ${count(data.total)} 个单位 · 第 ${page} 页`;
   container.querySelector('[data-collection-pages]').innerHTML=`<button type="button" class="btn btn-secondary" data-collection-page="${page-1}" ${page===1?'disabled':''}>上一页</button><button type="button" class="btn btn-secondary" data-collection-page="${page+1}" ${!data.has_more?'disabled':''}>下一页</button><button type="button" class="text-link" data-collection-refresh>刷新进度</button>`;
   container.querySelector('[data-collection-note]').textContent=data.notice||'';
  }catch(error){if(!disposed&&id===sequence&&error.name!=='AbortError')container.querySelector('[data-collection-total]').textContent='进度暂未读取：'+error.message;}
 }
 const input=event=>{if(event.target===search){clearTimeout(timer);page=1;++sequence;controller?.abort();timer=setTimeout(load,240);}};
 const change=event=>{if(event.target===status){page=1;load();}};
 const click=event=>{const unit=event.target.closest('[data-collection-unit]');if(unit){onOpenUnit(unit.dataset.collectionUnit);return;}const target=event.target.closest('[data-collection-page]');if(target&&!target.disabled){page=Number(target.dataset.collectionPage);load();}else if(event.target.closest('[data-collection-refresh]'))load();};
 const toggle=event=>{
  const disclosure=event.target;if(!disclosure.matches?.('[data-collection-facts]'))return;
  const panel=disclosure.querySelector('[data-catalog-facts-panel]');
  if(!disclosure.open){factsViews.get(disclosure)?.();factsViews.delete(disclosure);if(panel)panel.innerHTML='';return;}
  if(panel&&!factsViews.has(disclosure))factsViews.set(disclosure,mountCatalogFacts(panel,{api,
   unitId:disclosure.dataset.collectionFacts,year:Number(disclosure.dataset.factsYear)}));
 };
 container.addEventListener('input',input);container.addEventListener('change',change);container.addEventListener('click',click);container.addEventListener('toggle',toggle,true);load();
 return ()=>{disposed=true;++sequence;clearTimeout(timer);controller?.abort();clearFacts();container.removeEventListener('input',input);container.removeEventListener('change',change);container.removeEventListener('click',click);container.removeEventListener('toggle',toggle,true);};
}
