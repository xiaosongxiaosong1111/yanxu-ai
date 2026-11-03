import {escapeHtml as esc} from './details.mjs';
import {officialEvidenceUrl} from './evidence-model.mjs';

const PAGE_SIZE=6;
const labels={code:'专业代码',name:'专业名称',department:'招生院系',direction:'研究方向',
 admission_method:'考试方式',notes:'报考备注',subjects:'初试科目',study_mode:'学习方式'};
const list=value=>Array.isArray(value)?value:[];

function valid(data,unitId,year){
 return data&&data.unit_id===unitId&&data.year===year&&data.matching_allowed===false&&data.catalog_complete===false
  &&Number.isInteger(data.total)&&data.total>=0&&Number.isInteger(data.offset)&&data.offset>=0
  &&data.limit===PAGE_SIZE&&Array.isArray(data.facts)&&data.facts.length<=PAGE_SIZE
  &&new Set(data.facts.map(row=>row?.fact_id)).size===data.facts.length
  &&data.facts.every(row=>row&&typeof row.fact_id==='string'&&typeof row.code==='string'&&typeof row.name==='string'
   &&row.unit_id===unitId&&row.year===year&&row.study_mode===null&&row.matching_allowed===false
   &&row.fact_status==='verified_original_fact'&&Array.isArray(row.subjects)&&row.field_evidence&&typeof row.field_evidence==='object');
}

function references(refs){
 return list(refs).filter(ref=>ref&&typeof ref.quote==='string'&&ref.quote.trim()).map(ref=>{
  const url=officialEvidenceUrl(ref.url),where=ref.source_locator;
  return `<div class="catalog-fact-reference"><blockquote>${esc(ref.quote)}</blockquote>${where?.table&&where?.row?`<span>原表 ${esc(where.table)} · 第 ${esc(where.row)} 行${where.column?' · 第 '+esc(where.column)+' 列':''}</span>`:''}${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">打开这条依据的官方原文 ↗</a>`:'<span>原文链接待核验</span>'}</div>`;
 }).join('');
}

function card(fact){
 const fields=Object.entries(labels).filter(([field])=>list(fact.field_evidence[field]).length);
 const groups=list(fact.exam_groups);
 return `<article class="catalog-fact-card"><header><span class="catalog-fact-code">${esc(fact.code)}</span><span class="catalog-fact-year">${esc(fact.year)} 年原文</span></header><h4>${esc(fact.name)}</h4>${fact.direction?`<p class="catalog-fact-direction">${esc(fact.direction)}</p>`:''}${fact.department?`<p class="field-note">原文院系：${esc(fact.department)}</p>`:''}<div class="catalog-fact-missing">${list(fact.missing_fields).map(field=>`<span>${esc(labels[field]||'目录字段')}待核实</span>`).join('')}</div><div class="catalog-fact-exams">${groups.map(group=>`<section><div class="catalog-fact-exam-title">初试第 ${esc(group.exam_unit)} 单元${group.selection==='one_of'?'<span>任选一门</span>':''}</div><p>${list(group.options).map(option=>`<span><code>${esc(option.code)}</code> ${esc(option.name)}</span>`).join('<em>或</em>')}</p></section>`).join('')}</div>${fact.notes?`<details class="catalog-fact-notes"><summary>原文报考备注</summary><p>${esc(fact.notes)}</p></details>`:''}<details class="catalog-fact-proof"><summary>逐项查看原文依据</summary><dl>${fields.map(([field,label])=>`<div><dt>${esc(label)}</dt><dd>${references(fact.field_evidence[field])}</dd></div>`).join('')}</dl></details></article>`;
}

export function renderCatalogFacts(data){
 if(!valid(data,data?.unit_id,data?.year))throw new Error('原文行数据格式需要重新核验，请重试。');
 const start=data.facts.length?data.offset+1:0,end=data.facts.length?data.offset+data.facts.length:0;
 return `<div class="catalog-facts-heading"><strong>目录信息待完善</strong><span>${esc(data.year)} 年历史参考</span></div><p class="catalog-facts-notice">这些是官方原文表格行，学习方式及报考范围仍待核实，不参与报考匹配。原文有多种科目时，保留“或”的选择关系。</p>${data.facts.length?`<div class="catalog-facts-list">${data.facts.map(card).join('')}</div>`:'<p class="catalog-facts-empty" role="status">当前没有可展示的原文表格行；不代表该校没有招生或没有相关专业。</p>'}<div class="catalog-facts-pages"><span>${start}—${end} / 共 ${data.total} 条原文表格行</span><div><button type="button" class="btn btn-secondary" data-fact-offset="${Math.max(0,data.offset-PAGE_SIZE)}" ${!data.offset?'disabled':''}>上一组</button><button type="button" class="btn btn-secondary" data-fact-offset="${data.offset+PAGE_SIZE}" ${!data.has_more?'disabled':''}>下一组</button></div></div>`;
}

export function mountCatalogFacts(container,{api,unitId,year=2027}){
 let offset=0,sequence=0,controller,disposed=false,timer;
 container.innerHTML='<div class="catalog-facts-toolbar"><label>在这所学校的原文行中查找<input type="search" maxlength="120" placeholder="专业名称、代码或考试科目" data-facts-query></label></div><div data-facts-content aria-live="polite"></div>';
 const content=container.querySelector('[data-facts-content]'),search=container.querySelector('[data-facts-query]');
 async function load(){
  controller?.abort();controller=new AbortController();const id=++sequence;
  content.innerHTML='<p class="catalog-facts-loading" role="status">正在核对原文表格行…</p>';
  try{
   const data=await api('/api/catalog-facts?'+new URLSearchParams({unit_id:unitId,year:String(year),q:search.value,
    offset:String(offset),limit:String(PAGE_SIZE)}),{signal:controller.signal});
   if(disposed||id!==sequence)return;
   if(!valid(data,unitId,year)||data.offset!==offset)throw new Error('原文行数据格式需要重新核验，请重试。');
   content.innerHTML=renderCatalogFacts(data);
  }catch(error){
   if(!disposed&&id===sequence&&error.name!=='AbortError')content.innerHTML=`<p class="catalog-facts-error" role="status">暂未读取原文表格行：${esc(error.message||'请稍后重试。')}</p><button type="button" class="text-link" data-fact-retry>重新读取 →</button>`;
  }
 }
 const input=event=>{if(event.target===search){clearTimeout(timer);offset=0;++sequence;controller?.abort();timer=setTimeout(load,240);}};
 const click=event=>{
  if(event.target.closest('[data-fact-retry]')){load();return;}
  const target=event.target.closest('[data-fact-offset]');if(!target||target.disabled)return;
  const next=Number(target.dataset.factOffset);
  if(Number.isInteger(next)&&next>=0&&next<=1000000&&next%PAGE_SIZE===0){offset=next;load();}
 };
 container.addEventListener('input',input);container.addEventListener('click',click);load();
 return ()=>{disposed=true;++sequence;clearTimeout(timer);controller?.abort();container.removeEventListener('input',input);container.removeEventListener('click',click);};
}
