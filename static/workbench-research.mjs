import {esc,sourceLink,displayValue,programLabel} from './workbench-model.mjs';

const kinds={charter:'报考条件与招生章程',fees:'学费与学制',enrollment:'招生人数与推免口径',score:'复试分数线',transfer:'调剂办法',change:'考试科目调整'};
const fields={subjects:'考试科目',name:'专业名称',direction:'研究方向',directions:'新增方向',study_mode:'学习形式',tuition:'学费',duration:'学制',duration_years:'学制',conditions:'报考条件',notes:'备注',enrollment:'招生人数',planned_enrollment:'招生计划',score_line:'复试线',department:'院系',code:'专业代码'};
const states={checking:'正在检查',complete:'本次核对完成',idle:'等待检查',cancelled:'检查已取消',check_failed:'检查未完成',needs_review:'需要人工复核',unchanged:'本次已核对来源',updated:'发现来源更新',known:'已有字段',missing:'资料缺失',not_found:'未找到可用候选',not_configured:'检索未配置'};
const amount=value=>Number.isFinite(value)&&value>=0?String(value):'未知';
const error=(state,key)=>state.errors?.[key]?`<p class="wb-error" role="alert">${esc(state.errors[key])}</p>`:'';
const date=value=>{if(!value)return '尚未检查';const parsed=new Date(value);return Number.isNaN(parsed.getTime())?String(value):parsed.toLocaleString('zh-CN',{hour12:false});};
const metadata=program=>programLabel(program)||program?.id||'方向名称待核验';
const observationValue=(value,field,context)=>typeof value==='string'&&(context.programs||[]).some(program=>program.id===value)?metadata(context.programs.find(program=>program.id===value)):displayValue(value,field);

function radarOverview(state) {
 const radar=state.radar,busy=state.busy?.radar||state.busy?.watch;
 const refresh=`<button type="button" class="text-link" data-wb-action="retry-radar" ${busy?'disabled':''}>刷新状态</button>`;
 if(!radar)return `<div class="wb-empty" role="status">${busy?'正在读取关注状态…':'关注状态尚未读入。请重试检查连接。'}${refresh}</div>`;
 const pending=busy||radar.running;
 return `<div class="wb-toolbar"><div class="wb-meta"><span>已关注 <strong>${esc(amount(radar.watch_count))}</strong> 个方向</span><span>已有 ${esc(amount(radar.baseline_count))} 份来源基线</span><span>${esc(states[radar.check_state]||radar.check_state||'等待检查')}</span></div><div class="wb-actions"><button type="button" class="btn btn-primary" data-wb-action="check-radar" ${pending||!radar.watch_count?'disabled':''}>${pending?'正在核对来源…':'立即检查关注来源'}</button>${refresh}${state.busy?.radar?'<button type="button" class="text-link" data-wb-action="cancel-radar">取消本次等待</button>':''}</div></div><p class="wb-muted">最近检查：${esc(date(radar.last_checked_at))}。${radar.watch_count?'检查结果仅对应本次成功读取的来源。':'先从已收藏方向中选择关注。'}</p>`;
}

function watchManager(context,state) {
 const draft=state.draft||{},saved=new Set(context.savedIds||[]),watched=new Set(state.radar?.program_ids||[]);
 const items=(context.programs||[]).filter(program=>saved.has(program.id)||watched.has(program.id));
 const ids=new Set(draft.watchIds||[]),busy=state.busy?.watch||state.busy?.radar||!state.radar;
 return `<details class="wb-panel" data-wb-watch-details ${draft.watchOpen?'open':''}><summary>管理关注方向 <span class="wb-muted">${ids.size} 个已选</span></summary><p class="wb-muted">从收藏中选择。保存会更新整个关注集合；清空后保存即可关闭关注。</p>${items.length?`<div class="wb-check-list">${items.map(program=>`<label class="wb-check-row"><input type="checkbox" name="wb-watch" value="${esc(program.id)}" ${ids.has(program.id)?'checked':''} ${busy?'disabled':''}><span>${esc(metadata(program))}${!saved.has(program.id)?'<small class="wb-muted">当前关注，已移出收藏</small>':''}</span></label>`).join('')}</div>`:'<p class="wb-empty">先收藏想持续了解的方向，再在这里开启关注。</p>'}<div class="wb-actions"><button type="button" class="btn btn-secondary" data-wb-action="save-watches" ${busy?'disabled':''}>${state.busy?.watch?'正在保存…':'保存关注设置'}</button><button type="button" class="text-link" data-wb-action="select-all-watches" ${!saved.size||busy?'disabled':''}>选中全部收藏</button><button type="button" class="text-link" data-wb-action="clear-watches" ${!ids.size||busy?'disabled':''}>清空选择</button></div>${error(state,'watch')}</details>`;
}

function eventEvidence(evidence) {
 if(!evidence)return '';
 return `<div class="wb-evidence">${[['before','变更前'],['after','本次来源']].map(([key,label])=>{const source=evidence[key];return source?`<div><strong>${label}</strong> · ${sourceLink(source)}<span class="wb-muted"> ${esc(source.year??'年度待核验')}</span>${source.sha256?`<p class="wb-meta">SHA-256：<code>${esc(source.sha256)}</code></p>`:''}</div>`:'';}).join('')}</div>`;
}

function radarEvent(event,context) {
 const failed=['check_failed','fetch_error','parse_failed','source_unavailable'].includes(event.type);
 const labels={program_changed:'目录字段变化',source_updated:'官方来源更新',program_removed:'方向变化待复核',program_added:'新增方向记录',direction_missing:'原方向在本次目录中未出现',directions_added:'官方目录新增方向',baseline_created:'已建立来源基线',check_failed:'来源读取失败'};
 const names=(event.program_ids||[]).map(id=>(context.programs||[]).find(program=>program.id===id)).filter(Boolean);
 const changes=Object.entries(event.changes||{});
 return `<article class="wb-card"><div class="wb-subhead"><h3>${esc(labels[event.type]||'来源观察记录')}</h3><span class="wb-muted">${esc(date(event.created_at))}</span></div><p>${esc(event.message||'请核对对应官方来源。')}</p>${failed?'<p class="wb-notice">本次读取或解析未完成，不能据此判断无变化、停招或删除方向。</p>':''}${names.length?`<p class="wb-muted">影响方向：${esc(names.map(metadata).join('；'))}</p>`:''}${changes.length?`<dl class="wb-list">${changes.map(([field,change])=>`<div><dt>${esc(fields[field]||field)}</dt><dd><span class="wb-muted">原记录</span> ${esc(observationValue(change?.before,field,context))} <span aria-hidden="true">→</span> <span class="wb-muted">本次来源</span> ${esc(observationValue(change?.after,field,context))}</dd></div>`).join('')}</dl>`:''}${eventEvidence(event.evidence)}</article>`;
}

export function renderRadar(context,state) {
 const radar=state.radar,events=Array.isArray(radar?.events)?radar.events:[];
 return `<section class="wb-tool" data-wb-tool="radar"><div class="wb-top"><div><span class="wb-eyebrow">来源动态</span><h2>重要变化，有据可查。</h2><p>只关注你选定的方向，保存官方目录的前后差异。</p></div></div><div class="wb-panel">${error(state,'radar')}${state.errors?.radar?'<p class="wb-notice">状态读取失败，不能据此判断无变化或停招。已有观察记录仍需逐条核对。</p>':''}${radarOverview(state)}<p class="wb-notice">首次关注只建立来源基线。此后本机服务运行时每小时检查一次，通过站内提醒查看；关闭服务后不会继续检查。</p></div>${watchManager(context,state)}<div class="wb-subhead"><h2>已记录的观察</h2><span class="wb-muted">${esc(amount(radar?.unread_count))} 条未读</span></div>${events.length?`<div class="wb-result">${events.map(event=>radarEvent(event,context)).join('')}</div>`:`<div class="wb-empty">${radar?'尚无已记录的来源变化。首次基线和未完成的检查均不能证明没有变化。':'读取成功后，在这里查看真实观察记录。'}</div>`}${radar?.limitations?.length?`<details class="wb-panel"><summary>检查范围与限制</summary><ul>${radar.limitations.map(value=>`<li>${esc(value)}</li>`).join('')}</ul></details>`:''}</section>`;
}

function proofPicker(context,state) {
 const draft=state.draft||{},saved=new Set(context.savedIds||[]);
 const programs=[...(context.programs||[])].sort((a,b)=>Number(saved.has(b.id))-Number(saved.has(a.id)));
 const selected=state.proofTasks?.tasks?.find(task=>task.kind===draft.proofKind);
 return `<div class="wb-toolbar"><label class="wb-field">需要补证的方向<select name="proofProgram" aria-label="选择需要补证的方向"><option value="">请选择已收录方向</option>${programs.map(program=>`<option value="${esc(program.id)}" ${draft.proofProgram===program.id?'selected':''}>${saved.has(program.id)?'已收藏 · ':''}${esc(metadata(program))}</option>`).join('')}</select></label><label class="wb-field">补证内容<select name="proofKind" aria-label="选择补证内容">${Object.entries(kinds).map(([kind,label])=>`<option value="${kind}" ${draft.proofKind===kind?'selected':''}>${label}</option>`).join('')}</select></label></div>${selected?`<p class="wb-muted">本次检索：${esc(selected.query||'以方向的招生年度与招生单位为准')}</p>`:''}<div class="wb-actions"><button type="button" class="btn btn-primary" data-wb-action="run-proof" ${!context.searchConfigured||!draft.proofProgram||!selected||state.busy?.proof||state.busy?.tasks?'disabled':''}>${state.busy?.proof?'正在读取官方依据…':'查找官方依据'}</button>${state.busy?.proof?'<button type="button" class="text-link" data-wb-action="cancel-proof">取消本次等待</button>':''}</div>`;
}

function knownEvidence(task) {
 const evidence=Array.isArray(task.known_evidence)?task.known_evidence:[];
 return evidence.length?`<details class="wb-evidence"><summary>已有 ${evidence.length} 份对应来源</summary>${evidence.map(source=>`<p>${sourceLink(source,source.title||'查看已有来源')} <span class="wb-muted">${esc(source.year??'年度待核验')}</span></p>`).join('')}</details>`:'';
}

function proofTasks(state) {
 if(state.busy?.tasks)return '<div class="wb-empty" role="status">正在读取该方向的资料缺口…</div>';
 const tasks=state.proofTasks?.tasks;
 if(!Array.isArray(tasks))return '<div class="wb-empty">选择一个方向，先看哪些依据仍需补齐。</div>';
 if(!tasks.length)return '<div class="wb-empty">该方向暂未返回资料任务，请重新读取。</div>';
 return `<div class="wb-subhead"><h2>资料核验清单</h2><span class="wb-muted">${esc(state.proofTasks.year??'年度待核验')} 年资料</span></div><div class="wb-proof-grid">${tasks.map(task=>`<article class="wb-card"><div class="wb-subhead"><h3>${esc(task.label||kinds[task.kind]||task.kind)}</h3><span class="wb-tag">${esc(states[task.status]||'待核验')}</span></div><p>${esc(task.reason||'请核对官方原文与适用范围。')}</p>${knownEvidence(task)}${task.last_check||task.last_result?`<p class="wb-muted">最近补查：${esc(date((task.last_check||task.last_result).checked_at))} · ${esc(states[(task.last_check||task.last_result).status]||'记录待复核')}</p>`:''}</article>`).join('')}</div>`;
}

function proofCandidate(candidate) {
 const title=fields[candidate.field]||kinds[String(candidate.field).replace(/_evidence$/,'')]||candidate.field||'证据候选';
 const scope=candidate.scope&&typeof candidate.scope==='object'?Object.entries(candidate.scope).filter(([,value])=>value!==undefined&&value!==null&&value!=='').map(([field,value])=>`${fields[field]||field}：${displayValue(value)}`).join(' · '):'适用范围待复核';
 return `<article class="wb-card"><div class="wb-subhead"><h3>${esc(title)}</h3><span class="wb-tag">待人工复核</span></div><p class="wb-candidate-value">${esc(displayValue(candidate.value,candidate.field,candidate.billing_period))}</p><blockquote class="wb-quote">${esc(candidate.quote||'原文摘录待核验')}</blockquote><p class="wb-meta">${esc(candidate.year??'年度待核验')} 年 · ${candidate.page!==null&&candidate.page!==undefined?`第 ${esc(candidate.page)} 页`:'页码待核验'} · ${esc(scope)}</p><div class="wb-evidence">${sourceLink(candidate.source_url)}<p class="wb-meta">SHA-256：<code>${esc(candidate.source_sha256??'尚未提供')}</code></p></div></article>`;
}

function proofChecks(result) {
 const checks=Array.isArray(result.checks)?result.checks:[];
 return checks.length?`<details class="wb-panel"><summary>查看 ${checks.length} 条来源读取记录</summary><div class="wb-list">${checks.map(check=>`<div><p>${sourceLink(check.url,check.document_title||'查看检索来源')}</p><p>${esc(check.reason||'来源适用性待复核')}</p>${check.source_sha256?`<p class="wb-meta">SHA-256：<code>${esc(check.source_sha256)}</code></p>`:''}</div>`).join('')}</div></details>`:'';
}

export function renderProofResult(result) {
 if(!result)return '';
 const candidates=Array.isArray(result.candidates)?result.candidates:[];
 return `<section class="wb-result" aria-label="补证结果"><div class="wb-subhead"><h2>本次补证结果</h2><span class="wb-tag">${candidates.length} 条待人工复核候选</span></div>${result.error?`<p class="wb-error" role="alert">${esc(result.error)}</p>`:''}<p class="wb-notice">${esc(result.notice||'候选仅供核对，不会自动写入正式招生资料，也不会自动判定资格通过。')}</p>${candidates.length?candidates.map(proofCandidate).join(''):'<div class="wb-empty">本次未找到满足完整口径的待复核候选。未找到不等于不存在；请核对年度、院系、专业和计费口径。</div>'}${proofChecks(result)}</section>`;
}

export function renderProof(context,state) {
 const kind=state.draft?.proofKind||'charter',result=state.proofResult||state.proofResults?.[kind];
 return `<section class="wb-tool" data-wb-tool="proof"><div class="wb-top"><div><span class="wb-eyebrow">资料补证</span><h2>把未知项，变成可核对的依据。</h2><p>按招生年度和方向查找官方原文，数值、口径与出处放在一起。</p></div></div><div class="wb-panel">${!context.searchConfigured?'<p class="wb-notice">元宝官方检索尚未配置。仍可查看资料缺口与已有来源，配置后可主动补查。</p>':''}${proofPicker(context,state)}${error(state,'proof')}${error(state,'tasks')}${state.errors?.tasks?'<button type="button" class="text-link" data-wb-action="load-tasks">重新读取资料清单</button>':''}</div>${proofTasks(state)}${renderProofResult(result)}</section>`;
}
