import {annualUnitOptionLabel} from './data.mjs';
import {esc,count,sourceLink,displayValue,programLabel} from './workbench-model.mjs';
import {renderRouteCoverage} from './charts.mjs';
import {officialEvidenceUrl} from './evidence-model.mjs';
export {renderRadar,renderProof,renderProofResult} from './workbench-research.mjs';

const action=(name,label,primary=false,disabled=false,extra='')=>`<button type="button" class="btn ${primary?'btn-primary':'btn-secondary'}" data-wb-action="${name}" ${disabled?'disabled':''} ${extra}>${label}</button>`;
const error=(state,slot)=>state.errors?.[slot]?`<p class="wb-error" role="alert">${esc(state.errors[slot])}</p>`:'';
const coverage=view=>`<div class="wb-coverage"><span><strong>${count(view?.coverage?.program_count)}</strong> 个方向</span><span>${count(view?.coverage?.department_program_count)} 个院系专业</span><span>${count(view?.coverage?.unit_count)} 所学校</span></div>`;

export function renderExamUnresolved(result){
 const input=result?.exam_unresolved_programs||result?.coverage?.exam_unresolved_programs;
 const rows=[...new Map((Array.isArray(input)?input:[]).filter(row=>row&&typeof row==='object').map((row,index)=>[row.program_id||row.id||index,row])).values()];
 if(!rows.length)return '';
 const items=rows.map(row=>{
  const label=[row.unit_name,row.department,row.name,row.direction,row.code].filter(value=>typeof value==='string'&&value).join(' · ')||'待核实方向';
  const url=officialEvidenceUrl(row.url);
  return `<li><strong>${esc(label)}</strong><p>${esc(row.reason||'原文科目组合或代码名称尚待核实，当前不能计算换科。')}</p>${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">查看原文 ↗</a>`:'<span class="wb-muted">原文链接待核验</span>'}</li>`;
 }).join('');
 return `<aside class="wb-notice" role="status"><strong>${rows.length} 个方向的科目资料待核实，未纳入本次计算。</strong><details><summary>查看未参与计算的方向</summary><ul class="wb-plain-list">${items}</ul></details></aside>`;
}

export function renderRoutes(context,state) {
 const prepared=context.profile?.subjects||[],busy=state.busy?.routes,draft=state.draft||{};
 const result=state.routeResult,selected=result?.routes?.find(route=>route.id===state.selectedRoute)||result?.routes?.[0];
 return `<div class="wb-tool" data-wb-tool="routes"><section class="wb-panel"><div class="wb-top"><div><span class="wb-eyebrow">从已有备考出发</span><h2>少换一点科目，多看清一些选择。</h2><p class="wb-muted">比较 0、1、2 个考试单元调整，学历、背景与工作年限始终按你的真实条件核对。</p></div><span class="wb-tag">${esc(context.referenceYear??'未知')} 年资料</span></div>${renderPrepared(prepared)}<div class="wb-toolbar"><label class="wb-field">分析范围<select name="scopeUnit" aria-label="路线分析学校范围"><option value="">全部已收录资料范围</option>${(context.units||[]).map(unit=>`<option value="${esc(unit.id)}" ${unit.annual_status==='not_listed'?'disabled':''} ${draft.scopeUnit===unit.id?'selected':''}>${esc(annualUnitOptionLabel(unit))}</option>`).join('')}</select></label>${prepared.length?action('run-routes',busy?'正在比较路线…':'生成 3 条备考路线',true,!!busy||!(context.units||[]).length):action('edit-profile','先填写我的备考科目',true)}${busy?action('cancel-routes','取消本次试算'):''}</div><p class="wb-meta">仅分析明确选中的已收录范围；未收录不等于不存在。计数分别按方向、院系专业和学校去重。</p>${error(state,'routes')}</section>${result?`<section class="wb-results" aria-label="路线比较结果"><div class="wb-subhead"><h2>三条路线，同一套事实。</h2><p class="wb-muted">已知条件相容与资格待核验分别展示，数量不代表可报考或录取概率。</p></div>${renderExamUnresolved(result)}<div class="wb-route-grid">${(result.routes||[]).map(route=>renderRouteCard(route,selected?.id)).join('')}</div>${renderRouteCoverage(result)}${selected?renderRouteDetail(context,selected):'<p class="wb-empty">当前资料无法形成路线，请核对已准备科目和分析范围。</p>'}${result.questions?.length?renderQuestions(result.questions):''}<p class="wb-meta">${esc(result.note||'候选仅展示前20个方向；待核验不代表可申请。')}</p></section>`:`<div class="wb-empty"><span class="wb-empty-mark" aria-hidden="true">↗</span><h3>先确定范围，再展开路线。</h3><p>你的科目和官方目录逐项对齐后，在这里查看换入、换出与待确认事项。</p></div>`}${renderAdjustments(context,state)}</div>`;
}
function renderPrepared(subjects) {
 return `<div class="wb-prepared"><div class="wb-subhead"><h3>已准备科目</h3><button type="button" class="text-link" data-wb-action="edit-profile">编辑条件</button></div>${subjects.length?`<div class="wb-pills">${subjects.map(subject=>`<span class="wb-subject">${esc(subject.code)} ${esc(subject.name)}<small>${Number.isInteger(subject.exam_unit)?`考试单元 ${subject.exam_unit} · `:''}${subject.scope==='national'?'统考':`自命题 · ${esc(subject.scope||'身份待核验')}`}</small></span>`).join('')}</div>`:'<p class="wb-muted">尚未填写备考科目。先按招生单位与考试单元录入，避免同代码自命题被当作同一门。</p>'}</div>`;
}
function renderRouteCard(route,selected) {
 const active=route.id===selected,changes=route.max_subject_changes;
 return `<button type="button" class="wb-route-card ${active?'is-active':''}" data-wb-action="select-route" data-wb-route="${esc(route.id)}" aria-pressed="${active}"><span class="wb-route-name">${changes===0?'沿用现有备考':`最多调整 ${esc(changes)} 个单元`}</span><strong class="wb-route-number">${count(route.coverage?.program_count)}<small>个方向</small></strong><span class="wb-meta">${count(route.coverage?.department_program_count)} 个院系专业 · ${count(route.coverage?.unit_count)} 所学校</span><span class="wb-route-status">${count(route.matched_count)} 个已知条件匹配<br>${count(route.needs_confirmation_count)} 个资格待核验</span><span class="wb-meta">${count(route.unknown_subject_count)} 个科目身份待完整，未计入本路线</span><span class="wb-route-select">${active?'正在查看':'查看这条路线'} <span aria-hidden="true">→</span></span></button>`;
}
function renderRouteDetail(context,route) {
 return `<section class="wb-panel wb-route-detail"><div class="wb-subhead"><div><h3>${esc(route.label||'路线候选')}</h3><p class="wb-muted">${count(route.matched_count)} 个已知条件匹配 · ${count(route.needs_confirmation_count)} 个资格待核验</p></div><span class="wb-tag">参考年度核对</span></div><div class="wb-delta"><span>相对原条件新增 <strong>${count(route.added_ids?.length??0)}</strong></span><span>保留 <strong>${count(route.retained_ids?.length??0)}</strong></span><span>换出 <strong>${count(route.removed_ids?.length??0)}</strong></span></div>${renderDeltas(context,route)}${renderCandidates(context,route.candidates||[])}<p class="wb-meta">显示前 ${(route.candidates||[]).length} 个方向；完整覆盖 ${count(route.coverage?.program_count)} 个方向。资格、费用或科目未知时，仍需查看官方依据。</p></section>`;
}
function renderDeltas(context,route) {
 const named=id=>{const program=(context.programs||[]).find(item=>item.id===id)||(context.rawPrograms||[]).find(item=>item.id===id);return program?programLabel(program):id;};
 return `<div class="wb-delta-details">${[['added_ids','换入方向'],['removed_ids','换出方向']].map(([field,label])=>`<details><summary>${label}（${route[field]?.length??0}）</summary>${route[field]?.length?`<ul class="wb-plain-list">${route[field].slice(0,20).map(id=>`<li>${esc(named(id))}</li>`).join('')}</ul>${route[field].length>20?`<p class="wb-meta">先列出前20个，共 ${route[field].length} 个。</p>`:''}`:'<p class="wb-meta">此项没有增减。</p>'}</details>`).join('')}</div>`;
}
export function renderCandidates(context,candidates) {
 if(!candidates.length)return '<div class="wb-empty"><h3>这条路线暂未形成候选。</h3><p>只核对当前范围；请先补齐待确认的条件。</p></div>';
 return `<div class="wb-candidates">${candidates.map(entry=>renderCandidate(context,entry)).join('')}</div>`;
}
function renderCandidate(context,entry) {
 const raw=entry.program||{},saved=(context.savedIds||[]).includes(raw.id),status=entry.status==='matched'?'参考年已知条件相容':'资格仍待核验';
 const reason=(entry.reasons||[]).filter(item=>item.status==='unknown'||item.field==='subjects').map(item=>item.message).filter(Boolean).slice(0,2);
 const fee=raw.tuition?.amount,period=raw.tuition?.period;
 return `<article class="wb-candidate"><div class="wb-candidate-copy"><p class="wb-meta">${esc(raw.unit_name||'学校待核验')} · ${esc(raw.department||'院系待核验')}</p><h4>${esc([raw.name,raw.direction].filter(Boolean).join(' · ')||'方向待核验')}</h4><p class="wb-meta">${esc(raw.code||'代码待核验')} · ${esc(raw.study_mode||'学习形式待核验')} · ${esc(raw.year??'年度待核验')}</p><span class="wb-tag ${entry.status==='matched'?'':'is-pending'}">${status}</span>${reason.map(message=>`<p class="wb-meta">${esc(message)}</p>`).join('')}</div><div class="wb-candidate-facts"><span>科目调整 <strong>${count(entry.subject_changes?.count)}</strong> 个单元</span><span>学费 ${esc(displayValue(fee,'tuition',period))}</span></div><div class="wb-actions">${action('open-program','查看资料',false,false,`data-wb-id="${esc(raw.id)}"`)}<button type="button" class="text-link" data-wb-action="save-program" data-wb-id="${esc(raw.id)}" aria-pressed="${saved}">${saved?'已收藏':'收藏方向'}</button></div></article>`;
}
function renderQuestions(questions) {
 return `<details class="wb-panel wb-questions"><summary>下一步优先核对什么（${questions.length} 项）</summary><ul class="wb-plain-list">${questions.slice(0,8).map(question=>`<li>${esc(question.question||question.message||question.label||question.field)}${Number.isFinite(question.affected_count)?` <span class="wb-meta">涉及 ${question.affected_count} 个方向</span>`:''}</li>`).join('')}</ul></details>`;
}
function renderAdjustments(context,state) {
 const draft=state.draft||{},profile=context.profile||{},result=state.adjustmentResult,busy=state.busy?.adjustments;
 return `<details class="wb-panel wb-adjustments" data-wb-adjust-details ${draft.adjustmentOpen?'open':''}><summary>候选不够时，试算最小调整</summary><p class="wb-muted">只枚举你明确勾选的改变。试算不修改已保存条件，也不会放宽学历、背景或工作年限。</p><div class="wb-check-list"><label class="wb-check-row"><input type="checkbox" name="allowSubjects" ${draft.allowSubjects?'checked':''} ${!Number.isInteger(profile.max_subject_changes)||profile.max_subject_changes>=2?'disabled':''}><span>允许增加换科上限<small>${Number.isInteger(profile.max_subject_changes)?`当前 ${profile.max_subject_changes} 个，比较至多 1、2 个考试单元。`:'未设置原换科上限，请先在个人条件中设置。'}</small></span></label><label class="wb-check-row"><input type="checkbox" name="allowRegions" ${draft.allowRegions?'checked':''} ${!profile.provinces?.length?'disabled':''}><span>允许扩大意向地区<small>当前：${esc(profile.provinces?.join('、')||'未限制地区，无需扩大')}</small></span></label>${draft.allowRegions?`<label class="wb-field wb-indented">新增地区<input name="regionInput" type="text" maxlength="120" value="${esc(draft.regionInput||'')}" placeholder="例如：北京、河北"></label>`:''}<label class="wb-check-row"><input type="checkbox" name="allowBudget" ${draft.allowBudget?'checked':''} ${!Number.isFinite(profile.max_annual_tuition)?'disabled':''}><span>允许提高年学费预算<small>当前：${Number.isFinite(profile.max_annual_tuition)?`${esc(profile.max_annual_tuition)} 元/年`:'未设置原预算，请先在条件中填写'}</small></span></label>${draft.allowBudget?`<label class="wb-field wb-indented">允许提高到（元/年）<input name="budgetInput" type="number" min="0" max="10000000" step="100" value="${esc(draft.budgetInput||'')}" inputmode="decimal" placeholder="填写真实可接受的年预算"></label>`:''}</div><div class="wb-toolbar">${action('run-adjustments',busy?'正在比较允许的调整…':'查看最小调整建议',false,!!busy)}${busy?action('cancel-adjustments','取消试算'):''}</div>${error(state,'adjustments')}${result?renderAdjustmentResult(context,result):''}</details>`;
}
function renderAdjustmentResult(context,result) {
 const pending=renderExamUnresolved(result);
 if(result.status==='no_improvement')return pending+'<div class="wb-notice"><strong>允许的调整未增加候选。</strong><p>在本次已收录范围与授权选项内，未找到改善。未知资格或费用仍需补证。</p></div>';
 if(result.status==='no_solution')return pending+'<div class="wb-notice"><strong>当前授权调整未达到候选目标。</strong><p>这不表示实际没有可选专业；请核对资料缺口与分析范围。</p></div>';
 return `${pending}<div class="wb-adjustment-results">${(result.suggestions||[]).map(suggestion=>`<section class="wb-result"><div class="wb-subhead"><h3>${suggestion.cost??suggestion.changed_fields?.length??'未知'} 项条件调整</h3><span class="wb-tag ${suggestion.feasible?'':'is-pending'}">${suggestion.feasible?'达到已知条件匹配目标':'新增方向仍待核验'}</span></div><p>${esc(changesLabel(suggestion.changes||{}))}</p>${coverage(suggestion)}<p class="wb-meta">${count(suggestion.matched_count)} 个已知条件匹配 · ${count(suggestion.needs_confirmation_count)} 个资格待核验</p><p class="wb-muted">${esc(suggestion.explanation||'费用未知和资格待核验不会因提高预算而自动通过。')}</p>${renderDeltas(context,suggestion)}${renderCandidates(context,suggestion.candidates||[])}</section>`).join('')}<p class="wb-meta">${esc(result.note||'试算只供参考；没有修改个人条件。')}</p></div>`;
}
function changesLabel(changes) {
 return Object.entries(changes).map(([field,value])=>field==='max_subject_changes'?`换科上限调整为 ${value} 个考试单元`:field==='provinces'?`意向地区扩大为 ${value.join('、')}`:field==='max_annual_tuition'?`年学费预算提高到 ${value} 元`:'').filter(Boolean).join('；');
}

