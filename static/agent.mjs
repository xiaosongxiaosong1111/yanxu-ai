import {esc} from './workbench-model.mjs';
import {icon} from './icons.mjs';

const list=value=>Array.isArray(value)?value:[];
const object=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
const text=value=>typeof value==='string'?value:'';
const number=value=>Number.isInteger(value)&&value>=0?value:null;
const examples=['我想考计算机','已经准备408，想看看有哪些选择','想在天津附近读研'];
const statusLabels={queued:'等待开始',running:'正在研究',waiting_configuration:'等待连接服务',awaiting_input:'等你补充一点信息',completed:'本轮研究已完成',partial:'已形成部分结果',failed:'本轮研究未完成',cancelled:'研究已取消',interrupted:'研究已中断'};
export const taskStatusLabel=status=>statusLabels[status]||'研究状态待确认';
const isRunning=task=>['queued','running'].includes(task?.status);
export const shouldPoll=task=>isRunning(task)||task?.has_more_events===true;
const taskId=task=>text(task?.research_id||task?.id);
const safeLink=value=>{try{const url=new URL(value);return ['https:','http:'].includes(url.protocol)&&!url.username&&!url.password?url.href:'';}catch{return '';}};
const date=value=>{const parsed=new Date(value||'');return Number.isNaN(parsed.getTime())?'刚刚':parsed.toLocaleDateString('zh-CN',{month:'long',day:'numeric'});};
const statusDot=status=>['completed'].includes(status)?'is-complete':['running','queued'].includes(status)?'is-running':status==='failed'?'is-failed':'is-pending';
export function mergeTaskEvents(previous,next){
 if(!previous||taskId(previous)!==taskId(next))return {...next,events:list(next?.events)};
 const events=new Map(list(previous.events).map(event=>[event.id,event]));for(const event of list(next.events))events.set(event.id,event);
 return {...previous,...next,events:[...events.values()].sort((a,b)=>typeof a.id==='number'&&typeof b.id==='number'?a.id-b.id:0)};
}
function connectionStatus(settings,status='ready',error=''){
 if(status==='loading')return '<div class="ya-connection-strip" role="status"><span>正在读取服务状态…</span></div>';
 if(status==='error')return `<div class="ya-connection-strip is-state-error" role="status"><span>${esc(error||'暂时无法读取服务状态，连接情况尚不能确认。')}</span><button type="button" data-agent-action="settings-status">重新读取 ${icon('arrow')}</button></div>`;
 const ai=settings?.ai?.status==='connected',search=settings?.yuanbao?.status==='connected',label=item=>item?.status==='connected'?'已连接':item?.has_key||item?.status==='not_tested'?'待验证':'待配置';
 return `<div class="ya-connection-strip"><div><span class="ya-dot ${ai?'is-complete':''}"></span>AI 模型${label(settings?.ai)}<span class="ya-connection-divider"></span><span class="ya-dot ${search?'is-complete':''}"></span>元宝搜索${label(settings?.yuanbao)}</div><button type="button" data-agent-action="settings">${ai&&search?'管理连接':'配置服务'} ${icon('arrow')}</button></div>`;
}
function recentView(state){const tasks=list(state.recent);if(!tasks.length&&!state.recentError)return '';return `<section class="ya-recent"><div class="ya-section-title"><h2>最近的研究</h2><span>从上次的想法继续</span></div>${state.recentError?`<p class="ya-muted">${esc(state.recentError)} <button type="button" class="ya-inline" data-agent-action="recent">重试</button></p>`:`<div class="ya-recent-list">${tasks.slice(0,4).map(task=>`<button type="button" class="ya-recent-item" data-agent-task="${esc(taskId(task))}"><span class="ya-recent-icon">${icon('book')}</span><span class="ya-recent-copy"><strong>${esc(task.query||'未命名研究')}</strong><small><i class="ya-dot ${statusDot(task.status)}"></i>${esc(taskStatusLabel(task.status))}<span>·</span>${esc(date(task.updated_at||task.created_at))}</small></span>${icon('arrow')}</button>`).join('')}</div>`}</section>`;}
function welcome(state){return `<section class="ya-welcome"><div class="ya-welcome-heading"><span class="ya-eyebrow"><i></i>你的考研研究伙伴</span><h1>把下一站，<br><span>研究得更清楚。</span></h1><p>从一个大致想法开始。找资料、比较路线，<br class="ya-desktop-break">把每个选择的依据留清楚。</p></div><div class="ya-composer-card"><form id="ya-create-form"><label class="ya-sr-only" for="ya-query">描述你的考研想法</label><textarea id="ya-query" name="query" maxlength="4000" required rows="3" placeholder="我想考计算机，准备408，希望尽量少换专业课……">${esc(state.query||'')}</textarea><div class="ya-composer-bottom"><span>${icon('sparkles')}条件可以慢慢补充</span><button class="ya-primary" type="submit" ${state.busy?'disabled':''}>${state.busy?'正在创建研究…':'开始研究'} ${icon('arrow')}</button></div></form>${state.error?`<div class="ya-error" role="alert">${esc(state.error)}</div>`:''}${connectionStatus(state.settings,state.settingsStatus,state.settingsError)}</div><div class="ya-example-list" aria-label="试试这些研究想法"><span>也可以试试</span>${examples.map((example,index)=>`<button type="button" data-agent-example="${index}">${esc(example)} ${icon('arrowUp')}</button>`).join('')}</div><div class="ya-promise-row"><div>${icon('search')}<span><strong>从官方资料出发</strong><small>元宝发现来源，原文承载依据</small></span></div><div>${icon('sliders')}<span><strong>把取舍算清楚</strong><small>科目衔接、条件与个人偏好</small></span></div><div>${icon('shield')}<span><strong>未知，也清楚可见</strong><small>年份、范围和缺口始终保留</small></span></div></div>${recentView(state)}<div class="ya-explore-footer"><span>想先自己看看？</span><button type="button" data-agent-action="discover">浏览院校资料 ${icon('arrow')}</button><button type="button" data-agent-action="knowledge">研究知识库</button><button type="button" data-agent-action="coverage">查看资料范围</button></div></section>`;}
export function currentTurnEvents(task){
 const events=list(task?.events),turn=number(task?.turn_id);
 if(turn!==null)return events.filter(event=>event.turn_id===turn);
 const plan=events.findLastIndex(event=>event.stage==='plan'||event.title==='继续研究');
 return events.slice(Math.max(0,plan));
}
function activityLog(task,key='sidebar'){
 const all=list(task.events),current=currentTurnEvents(task),previous=all.filter(event=>!current.includes(event));
 const rows=events=>events.filter(event=>event.title||event.message).map(event=>`<li><span class="ya-log-dot ${event.type==='tool_error'?'is-error':''}"></span><div><strong>${esc(event.message||event.title||'研究记录')}</strong><small>${esc(event.type==='tool_started'?'已开始':event.type==='tool_completed'?'已完成':event.type==='tool_error'?'待补查':event.title||'研究记录')}${event.data?.invocation==='system'?' · 程序自动核算':''}</small></div></li>`).join('');
 return `<details class="ya-activity-disclosure" data-persist-key="log-${key}"><summary>实际执行记录 <span>本轮 ${current.length} 条</span>${icon('chevron')}</summary><div class="ya-activity-body"><ol class="ya-timeline">${rows(current)||'<li>本轮尚无执行记录。</li>'}</ol>${previous.length?`<details class="ya-previous-turns"><summary>较早轮次 · ${previous.length} 条记录</summary><ol class="ya-timeline">${rows(previous)}</ol></details>`:''}<p class="ya-muted">记录的是实际工具动作，不展示模型隐藏思考。</p></div></details>`;
}
function activity(task,state={}){
 const events=currentTurnEvents(task),last=[...events].reverse().find(event=>event.type==='tool_started'||event.type==='tool_completed'||event.type==='tool_error');
 const running=isRunning(task),message=state.revising?'正在结束当前调用，随后按你的新条件研究。':running?(last?.message||'正在梳理已有条件与资料'):task.status==='partial'?'本轮已停止，已有成果保留在右侧。':task.status==='awaiting_input'?'补充一两项条件，比较会更有针对性。':taskStatusLabel(task.status);
 const next=running?'找到的资料和算法结果会逐步更新。你可以先写下新的想法。':list(task.report?.questions).length?'先回答最影响选择的问题，再继续比较。':'可以调整条件、核对来源，或从本轮继续。';
 return `<section class="ya-current-action" aria-live="polite"><div><i class="ya-dot ${statusDot(task.status)}"></i><span>${state.revising?'正在更新研究':running?'当前正在做':'接下来'}</span>${running&&!state.revising?'<button type="button" data-agent-action="cancel">停止</button>':''}</div><h2>${esc(message)}</h2><p>${esc(next)}</p><div class="ya-usage-pills">${number(task.usage?.searches)!==null?`<span>元宝检索 ${task.usage.searches}</span>`:''}${number(task.usage?.documents)!==null?`<span>原文读取 ${task.usage.documents}</span>`:''}${number(task.report?.knowledge?.cache_hits)!==null?`<span>知识复用 ${task.report.knowledge.cache_hits}</span>`:''}</div></section>`;
}

function knowledgeAdmissionSummary(report,sources){
 const knowledge=object(report.knowledge),states=['accepted','pending','excluded'];
 const explicit=states.some(state=>sources.some(source=>source.knowledge_quality?.state===state));
 const reported=['accepted_count','pending_count','filtered_count'].some(field=>number(knowledge[field])!==null);
 if(!explicit&&!reported)return '准入统计尚待复核。';
 const count=(field,state)=>number(knowledge[field])??sources.filter(source=>source.knowledge_quality?.state===state).length;
 return `本轮准入：已通过 ${count('accepted_count','accepted')} 份 · 待核 ${count('pending_count','pending')} 份 · 已过滤 ${count('filtered_count','excluded')} 份。`;
}
function knowledgeAdmissionControl(source){
 const quality=object(source.knowledge_quality),stored=source.knowledge_saved===true&&source.knowledge_id;
 if(quality.state==='excluded'||source.knowledge_filtered===true){
  const reasons=list(quality.reasons).map(reason=>typeof reason==='string'?reason:text(reason?.message)).filter(Boolean);
  return `<span class="ya-save-state">未入库：${esc(reasons.join('；')||'准入规则已过滤，详细原因待补充')}</span>`;
 }
 const label=quality.state==='accepted'?'已入知识库':quality.state==='pending'?'待核线索':'准入状态待复核';
 if(stored)return `<button type="button" data-agent-knowledge="${esc(source.knowledge_id)}"${['accepted','pending'].includes(quality.state)?` data-agent-knowledge-quality="${quality.state}"`:''}>${icon('book')}${label}</button>`;
 return `<span class="ya-save-state">${quality.state==='accepted'?'准入通过，保存尚未完成':label}</span>`;
}

function sourcesView(report,state={}){
 const all=list(report.evidence),filter=state.sourceFilter||'all',sources=all.filter(source=>filter==='all'||source.status===filter).sort((a,b)=>({official_original_read:0,published_snapshot:1,search_lead:2}[a.status]??3)-({official_original_read:0,published_snapshot:1,search_lead:2}[b.status]??3)),page=Math.min(Math.max(1,state.sourcePage||1),Math.max(1,Math.ceil(sources.length/8))),visible=sources.slice((page-1)*8,page*8);
 const filters=[['all','全部'],['official_original_read','已读取原文'],['published_snapshot','已收录资料'],['search_lead','检索线索']];
 return `<div class="ya-sources-intro"><h2>依据与积累，放在一起核对。</h2><p>来源身份与准入质量分别标注。${knowledgeAdmissionSummary(report,all)}待核保存不代表可直接用于建议，原文已读取也不代表字段已核验。</p><button type="button" class="ya-inline" data-agent-action="knowledge">打开研究知识库 ${icon('arrow')}</button></div>${all.length?`<div class="ya-source-filters">${filters.map(([value,label])=>`<button type="button" data-agent-source-filter="${value}" aria-pressed="${filter===value}">${label}<span>${value==='all'?all.length:all.filter(row=>row.status===value).length}</span></button>`).join('')}</div><div class="ya-source-list">${visible.map(source=>{const url=safeLink(source.url),original=source.status==='official_original_read',excluded=source.knowledge_quality?.state==='excluded'||source.knowledge_filtered===true;return `<article class="ya-source-card"><div class="ya-source-top"><span class="ya-source-status ${original?'is-original':''}">${original?'原文已读取':source.status==='published_snapshot'?'已收录资料':'检索线索'}</span><span>${source.year?`${esc(source.year)} 年`:'原文年份待核对'}</span>${source.cache_hit&&!excluded&&source.knowledge_quality?.state==='accepted'&&source.knowledge_quality?.reusable===true?'<span class="ya-cache-badge">来自知识库</span>':''}</div><h3>${esc(source.title||'资料标题待补齐')}</h3>${source.unit_name?`<p class="ya-source-unit">${esc(source.unit_name)}</p>`:''}${source.excerpt&&!excluded?`<details class="ya-source-excerpt" data-persist-key="source-${esc(source.id||source.url||source.title)}"><summary>${original?'查看原文摘录':source.status==='published_snapshot'?'查看资料摘录':'查看搜索摘要'}</summary><blockquote><span>${original?'原文摘录':source.status==='published_snapshot'?'资料摘录':'搜索摘要'}</span>${esc(source.excerpt)}</blockquote></details>`:''}<div class="ya-source-footer">${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${original?'查看原文':'查看来源'} ${icon('arrowUp')}</a>`:'<span>来源链接尚待核对</span>'}${knowledgeAdmissionControl(source)}</div>${source.checked_at?`<small class="ya-source-time">原文读取：${esc(date(source.checked_at))}${source.cache_hit?' · 本次未重新联网读取':''}</small>`:''}</article>`;}).join('')}</div>${!visible.length?'<p class="ya-muted">这一类暂时没有资料。</p>':''}${sources.length>8?`<nav class="ya-source-pagination" aria-label="来源分页"><button type="button" data-agent-source-page="${page-1}" ${page===1?'disabled':''}>上一页</button><span>第 ${page} / ${Math.ceil(sources.length/8)} 页 · ${sources.length} 份</span><button type="button" data-agent-source-page="${page+1}" ${page>=Math.ceil(sources.length/8)?'disabled':''}>下一页</button></nav>`:''}`:empty('还没有可展示的来源','相关材料查到后，会保留链接、年份和摘录，并逐步积累到知识库。')}`;
}
export function groupCandidates(candidates){
 const groups=new Map();
 for(const program of list(candidates)){const id=text(program.unit_id)||text(program.unit_name)||'unknown';if(!groups.has(id))groups.set(id,{id,name:program.unit_name||'招生单位待确认',programs:[]});groups.get(id).programs.push(program);}
 return [...groups.values()];
}
const evaluations=report=>[...list(report.algorithm?.matching?.matches),...list(report.algorithm?.matching?.needs_confirmation),...list(report.algorithm?.matching?.excluded)];
const evaluation=(program,report)=>evaluations(report).find(item=>(item.program?.id||item.program_id)===program.id);
function matchText(program,report,profile={}){const row=evaluation(program,report),count=number(row?.subject_changes?.count);return row?.status==='excluded'?'存在明确条件冲突':!list(profile.subjects).length?'备考科目待补充，尚不能判断换科':count!==null&&!row?.subject_changes?.uncertain?`需调整 ${count} 个考试单元`:'科目条件仍需确认';}
function algorithmSummary(report){
 const matching=object(report.algorithm?.matching),summary=object(matching.summary),done=number(summary.input_count)!==null,rows=evaluations(report),pareto=rows.filter(row=>row.pareto===true).length,knownPareto=rows.some(row=>typeof row.pareto==='boolean');
 const count=value=>number(value)===null?'—':String(value);
 return `<section class="ya-decision-summary"><div class="ya-decision-heading"><h2>${done?'已完成条件核算':'算法尚未执行'}</h2><button type="button" data-agent-action="methods">查看计算依据 ${icon('arrow')}</button></div>${done?`<div class="ya-decision-counts"><div><strong>${count(summary.input_count)}</strong><span>参与计算</span></div><div><strong>${count(summary.matched_count)}</strong><span>明确条件匹配</span></div><div><strong>${count(summary.unknown_count)}</strong><span>仍需确认</span></div><div><strong>${count(summary.excluded_count)}</strong><span>明确冲突</span></div></div><p>${knownPareto?`多目标比较中有 ${pareto} 个方向未被其他候选全面优于。`:'费用或个人偏好资料不足，暂不据此给出综合优劣。'}${report.algorithm?.routes?' 科目路线已计算，可查看每条路线的新增方向。':' 科目尚未明确时，不把未知算成零换科。'}</p>${list(report.skills).length?`<div class="ya-skill-brief"><span>${list(report.skills).filter(skill=>skill.loaded===true).length} 份 Skill 规程已加载</span><button type="button" data-agent-action="methods">查看方法执行记录 ${icon('arrow')}</button></div>`:''}`:'<p>已有候选不等于已经比较。待必要资料整理后，程序会核对条件、科目衔接和取舍；尚未计算的结果不显示为推荐。</p>'}</section>`;
}
function schoolCard(group,index,report,task){
 const first=group.programs[0],rest=group.programs.slice(1),rows=group.programs.map(program=>evaluation(program,report)).filter(Boolean),unknown=rows.filter(row=>row.status==='unknown'||list(report.algorithm?.matching?.needs_confirmation).includes(row)).length;
 return `<article class="ya-school-card ya-candidate"><div class="ya-school-heading"><span class="ya-school-index">${String(index+1).padStart(2,'0')}</span><h3>${esc(group.name)}</h3><small>${group.programs.length} 个方向</small></div><p class="ya-school-scope">${first.year?`${esc(first.year)} 年参考目录 · `:''}${rows.length?`${rows.length} 个方向参与比较 · ${unknown} 个方向条件待确认`:'方向已收录，个人条件尚待比较'}</p>${candidateCard(first,0,report,task)}${rest.length?`<details class="ya-school-directions" data-persist-key="school-${esc(group.id)}"><summary>还有 ${rest.length} 个方向 ${icon('chevron')}</summary>${rest.map((program,i)=>candidateCard(program,i+1,report,task)).join('')}</details>`:''}</article>`;
}

function candidateCard(program,index,report,task){
 const match=evaluation(program,report),reason=list(match?.reasons).map(reason=>typeof reason==='string'?reason:reason.message||reason.label||'').find(Boolean);
 return `<div class="ya-direction"><strong class="ya-candidate-name">${esc(program.name||'专业名称待确认')}${program.code?`<small>${esc(program.code)}</small>`:''}</strong><p class="ya-candidate-meta">${[program.department,program.direction,program.study_mode].filter(Boolean).map(esc).join(' · ')}</p>${list(program.subjects).length?`<div class="ya-subjects">${list(program.subjects).map(subject=>`<span title="${esc(subject.name||subject.code||'')}">${esc(subject.code||subject.name||'待核')}</span>`).join('')}</div>`:''}${reason?`<p class="ya-candidate-reason">${esc(reason)}</p>`:''}<div class="ya-candidate-footer"><span>${esc(matchText(program,report,task?.profile))}</span>${program.id?`<button type="button" data-agent-program="${esc(program.id)}">专业档案 ${icon('arrow')}</button>`:''}</div></div>`;
}

function overview(report,task,state){
 const groups=groupCandidates(report.candidates),visible=state.showAllSchools?groups:groups.slice(0,3);
 return `${algorithmSummary(report)}<div class="ya-section-title"><div><h2>值得继续研究的学校</h2><p>${groups.length?`${groups.length} 所学校 · ${list(report.candidates).length} 个已收录方向`:'先整理可靠资料，再形成可比较的方向。'}</p></div>${isRunning(task)?'<span class="ya-updating">持续整理中</span>':''}</div>${groups.length?`<div class="ya-candidate-list">${visible.map((group,index)=>schoolCard(group,index,report,task)).join('')}</div>${groups.length>3?`<button type="button" class="ya-more-schools" data-agent-action="schools">${state.showAllSchools?'收起其余学校':`查看其余 ${groups.length-3} 所学校`} ${icon('chevron')}</button>`:''}`:empty(isRunning(task)?'正在寻找与你的想法相关的资料':'这一轮暂未形成候选',isRunning(task)?'你可以在左侧补充条件，研究结果会在这里更新。':'先补充备考科目或意向地区，再从本轮继续。')}${list(report.gaps).length?`<details class="ya-gaps"><summary>${icon('shield')}还需要核对的资料 <span>${list(report.gaps).length}</span></summary><ul>${list(report.gaps).map(gap=>`<li>${esc(typeof gap==='string'?gap:gap.message||gap.label||'有资料尚待核验')}</li>`).join('')}</ul></details>`:''}${report.assistant_summary?`<details class="ya-report-note"><summary>查看本轮研究说明</summary><p>${esc(report.assistant_summary)}</p></details>`:''}`;
}

const empty=(title,message)=>`<div class="ya-empty"><span class="ya-empty-mark">${icon('book')}</span><h2>${esc(title)}</h2><p>${esc(message)}</p></div>`;
function algorithmDetails(report){
 const algorithm=object(report.algorithm),matching=object(algorithm.matching),summary=object(matching.summary),items=[...list(matching.matches),...list(matching.needs_confirmation),...list(matching.excluded)];
 const routes=list(algorithm.routes?.routes),suggestions=list(algorithm.adjustments?.suggestions),values=[['参与本轮计算',summary.input_count],['明确条件匹配',summary.matched_count],['仍需确认',summary.unknown_count]];
 const count=value=>number(value)===null?'—':esc(value);
 const coverage=item=>`<div class="ya-route-counts"><span>可计算方向<strong>${count(item.coverage?.program_count)}</strong></span><span>仍需确认条件<strong>${count(item.needs_confirmation_count)}</strong></span>${number(item.unknown_subject_count)!==null?`<span>科目尚未明确<strong>${count(item.unknown_subject_count)}</strong></span>`:''}</div>`;
 const changeText=changes=>Object.entries(object(changes)).map(([key,value])=>key==='max_subject_changes'?`最多调整 ${value} 个考试单元`:key==='max_annual_tuition'?`每年学费上限 ${value} 元`:key==='provinces'?`意向地区：${list(value).join('、')}`:'已授权条件调整').join('；');
 return `<div class="ya-sources-intro"><h2>建议的理由，能说清，也能复算。</h2><p>这里展示程序的实际比较结果。偏好排序不是录取概率，资料缺失不会被当作符合条件。</p></div><div class="ya-method-principles"><article><span>01</span><strong>资格三态</strong><p>明确符合、明确冲突与尚待确认，分别保留。</p></article><article><span>02</span><strong>完整科目组合</strong><p>按考试单元和命题学校核对，保留条件备选。</p></article><article><span>03</span><strong>多目标取舍</strong><p>比较科目、费用与地区偏好，不制造录取概率。</p></article></div>${values.some(([,value])=>number(value)!==null)?`<div class="ya-algorithm-counts">${values.map(([label,value])=>`<div><span>${label}</span><strong>${count(value)}</strong></div>`).join('')}</div>`:''}${items.length?`<div class="ya-algorithm-records">${items.slice(0,18).map(item=>{const program=item.program||{},reasons=list(item.reasons);return `<article><h3>${esc(program.unit_name||'候选方向')}${program.name?` · ${esc(program.name)}`:''}</h3>${reasons.length?`<ul>${reasons.map(reason=>`<li>${esc(typeof reason==='string'?reason:reason.message||reason.label||'该条件仍需核对')}</li>`).join('')}</ul>`:'<p>本项没有返回可展示的理由。</p>'}</article>`;}).join('')}</div>`:''}${routes.length?`<section class="ya-route-results"><h3>科目调整路线</h3><p>仅改变允许调整的考试单元数，其他已确认条件保持原样。</p>${routes.map(route=>`<article><h4>${esc(route.label||'科目衔接路线')}</h4>${coverage(route)}<p>相对当前方案新增 ${list(route.added_ids).length} 个方向，保留 ${list(route.retained_ids).length} 个方向。</p></article>`).join('')}</section>`:''}${algorithm.adjustments?`<section class="ya-route-results"><h3>最小条件调整</h3><p>${esc(algorithm.adjustments.note||'只在已授权的条件中比较，不自动放宽学历或报考资格。')}</p>${suggestions.length?suggestions.map((item,index)=>`<article><h4>调整方案 ${index+1}</h4><p>${esc(changeText(item.changes))}</p>${coverage(item)}<p>新增 ${list(item.added_ids).length} 个方向；${item.needs_confirmation_count?'部分条件仍需核验。':'以实际报考规则为准。'}</p></article>`).join(''):'<p>本轮已执行条件调整比较，未找到满足目标的新增方案。</p>'}</section>`:''}${!items.length&&!routes.length&&!algorithm.adjustments?empty('还没有形成可计算结果','候选与必要条件核对后，Agent 会调用比较工具；不会用模型猜测代替算法结果。'):''}`;
}
function skillsView(report,task){
 const labels={'understand-intent':'理解需求','research-official-sources':'研究官方资料','verify-evidence':'核对依据','compare-plans':'比较方案','assess-transfer-options':'复试与调剂'};
 const historical=list(task.events).findLast(event=>list(event.data?.skills).length)?.data.skills||[];
 const rows=list(report.skills).length?list(report.skills):historical.map(id=>({id,label:labels[id]||id,loaded:true,status:'loaded',historical:true}));
 return `<section class="ya-skills"><div class="ya-section-title"><div><h2>研究方法 · Skills</h2><p>规程负责约束研究步骤，算法负责实际计算。</p></div></div>${rows.length?`<div class="ya-skill-list">${rows.map(skill=>`<details class="ya-skill" data-persist-key="skill-${esc(skill.id)}"><summary><span class="ya-skill-icon">${icon(skill.status==='used'?'check':'book')}</span><strong>${esc(skill.label||labels[skill.id]||skill.id)}</strong><small>${skill.status==='used'?'相关工具已执行':skill.loaded===false||skill.status==='not_loaded'?'本轮未加载':skill.historical?'规程已加载（历史）':'规程已加载'}</small>${icon('chevron')}</summary><p>${skill.status==='used'?'本轮已有相关工具成功执行。':skill.loaded===false||skill.status==='not_loaded'?'这项方法可用于其他研究，本轮没有加载。':'加载规程不等于相关工具已经执行。'}不代表其中每项招生事实都已验证。</p>${list(skill.executed_tools).length?`<p>已执行：${list(skill.executed_tools).map(esc).join('、')}</p>`:''}</details>`).join('')}</div>`:'<p class="ya-muted">这份任务还没有可追溯的方法加载记录，不补造执行状态。</p>'}</section>`;
}
function methodsView(report,task){return skillsView(report,task)+algorithmDetails(report)+activityLog(task,'methods');}
function questionsView(report,state){
 const questions=list(report.questions).slice(0,3),draft=object(state.conditionDraft),changes=Object.keys(draft).length;
 if(!questions.length)return '';
 return `<section class="ya-next-questions"><h2>再明确一点</h2><p>先补最影响选择的条件，也可以直接在上方说明。</p>${questions.map((question,index)=>{const item=typeof question==='string'?{label:question}:question,options=list(item.options).filter(option=>option&&Object.hasOwn(option,'value')&&typeof option.label==='string');return `<div class="ya-question" data-question-field="${esc(item.field||'')}"><strong>${esc(item.label||item.message||'有一项条件待确认')}</strong>${item.message&&item.message!==item.label?`<p>${esc(item.message)}</p>`:''}${number(item.affected_count)!==null?`<small>影响 ${item.affected_count} 个候选方向</small>`:''}${options.length?`<div class="ya-question-options">${options.map((option,optionIndex)=>`<button type="button" data-agent-answer="${index}" data-agent-option="${optionIndex}" aria-pressed="${draft[item.field]===option.value}" ${state.busy?'disabled':''}>${esc(option.label)}</button>`).join('')}</div>`:`<button type="button" data-agent-question="${index}">补充这一项 ${icon('arrow')}</button>`}</div>`;}).join('')}${changes?`<button type="button" class="ya-secondary" data-agent-action="apply-answers" ${state.busy?'disabled':''}>应用已选条件（${changes}项） ${icon('arrow')}</button>`:''}</section>`;
}
const editableFields=['target_year','reference_year','education','study_mode','provinces','max_subject_changes','max_annual_tuition'];
function conditionsView(task,state){
 const profile=object(task.profile),draft=object(state.conditionDraft),value=name=>Object.hasOwn(draft,name)?draft[name]:profile[name];
 const education={unknown:'尚未确认',bachelor:'本科',master:'硕士',associate:'专科'},subjects=list(profile.subjects).map(subject=>subject.code||subject.name).filter(Boolean).join(' / ');
 const options=(rows,current)=>rows.map(([id,label])=>`<option value="${esc(id)}" ${String(current??'')===String(id)?'selected':''}>${esc(label)}</option>`).join('');
 return `<details class="ya-condition-editor" ${state.conditionsOpen?'open':''}><summary><span>${icon('filter')}我的已确认条件</span><span>编辑 ${icon('chevron')}</span></summary><div class="ya-confirmed-chips"><span>${esc(education[profile.education]||'学历待确认')}</span><span>${list(profile.provinces).length?esc(profile.provinces.join('、')):'地区未限定'}</span><span>${subjects?esc(subjects):'备考科目待补充'}</span></div><form id="ya-conditions-form"><label>目标入学年份<input type="number" name="condition.target_year" min="2020" max="2050" value="${esc(value('target_year')||task.target_year||2028)}"></label><label>历史参考目录年份<input type="number" name="condition.reference_year" min="2020" max="2050" value="${esc(value('reference_year')||task.reference_year||2027)}"></label><label>当前学历<select name="condition.education">${options(Object.entries(education),value('education')||'unknown')}</select></label><label>学习方式<select name="condition.study_mode">${options([['unknown','尚未确认'],['全日制','全日制'],['非全日制','非全日制']],value('study_mode')||'unknown')}</select></label><label>意向地区<input name="condition.provinces" maxlength="100" value="${esc(Array.isArray(value('provinces'))?value('provinces').join('、'):value('provinces')||'')}" placeholder="如：天津、北京；留空不限定"></label><label>最多调整考试单元<select name="condition.max_subject_changes">${options([['','尚未设置'],['0','全部沿用'],['1','最多 1 个'],['2','最多 2 个'],['3','最多 3 个'],['4','最多 4 个']],value('max_subject_changes'))}</select></label><label>每年学费上限<input type="number" name="condition.max_annual_tuition" min="0" max="1000000" step="100" value="${esc(value('max_annual_tuition')??'')}" placeholder="留空不设上限"></label><p>只应用你明确填写的条件。考试科目可在研究输入中继续说明。</p><button type="submit" class="ya-secondary" ${state.busy?'disabled':''}>${isRunning(task)?'停止并应用条件':'应用条件并重新比较'} ${icon('arrow')}</button></form></details>`;
}
function composer(task,state){return `<form id="ya-continue-form" class="ya-continue-composer"><div class="ya-compose-heading"><span>${icon('spark')}一起缩小选择范围</span><kbd>Ctrl ↵</kbd></div><label for="ya-followup">有什么想补充或调整的？</label><textarea id="ya-followup" name="followup" maxlength="4000" rows="3" placeholder="比如：我准备了408，希望留在天津，尽量少换专业课。">${esc(state.followup||'')}</textarea>${state.revising?`<p class="ya-revise-message" role="status">${state.revisionPhase==='submitting'?'正在提交新条件。已被服务端接受的研究，不会因离开页面而撤销。':'正在结束上轮调用，再按你的新条件研究。不会重复提交。'}</p>`:''}<div><span>${isRunning(task)?'可以先写下想法，再更新研究':'条件可以逐步补充'}</span><button type="submit" class="ya-primary" ${state.busy?'disabled':''}>${state.revising?'正在更新…':isRunning(task)?'停止并更新研究':'继续研究'} ${icon('arrow')}</button></div>${state.revising&&state.revisionPhase==='stopping'?'<button type="button" class="ya-inline ya-abort-update" data-agent-action="abort-revise">取消这次更新，保留草稿</button>':''}</form>`;}
function workspace(state){
 const task=state.task,report=object(task.report),current=state.tab||'overview',waiting=task.status==='waiting_configuration',groups=groupCandidates(report.candidates);
 return `<section class="ya-workspace"><div class="ya-workspace-top"><button type="button" class="ya-back" data-agent-action="home">${icon('arrow')}全部研究</button><div><button type="button" class="ya-inline" data-agent-action="knowledge">${icon('book')}研究知识库</button><button type="button" class="ya-inline" data-agent-action="settings">服务连接 ${icon('arrow')}</button></div></div><header class="ya-task-heading"><div><span class="ya-eyebrow"><i></i>${esc(task.target_year||'目标年')} 入学规划${task.reference_year?` · ${esc(task.reference_year)} 年参考资料`:''}</span><h1>${esc(task.query||'这一次的考研研究')}</h1><p>${groups.length} 所学校 · ${list(report.candidates).length} 个方向 · ${list(report.evidence).length} 份资料<span>${number(task.usage?.model_calls)>0&&number(task.usage?.searches)>0?'AI × 元宝协同研究':'官方资料研究'}</span></p></div><span class="ya-task-status"><i class="ya-dot ${statusDot(task.status)}"></i>${esc(taskStatusLabel(task.status))}</span></header><div class="ya-workspace-grid"><aside class="ya-research-desk">${state.error?`<div class="ya-error" role="alert">${esc(state.error)} <button type="button" class="ya-inline" data-agent-action="refresh">重新读取</button></div>`:''}${composer(task,state)}${activity(task,state)}${questionsView(report,state)}${conditionsView(task,state)}${activityLog(task)}</aside><div class="ya-report-column">${task.turn_id===0?'<p class="ya-legacy-note">这是旧流程保留的研究。下方只展示当时实际成果，可在左侧补条件，按新流程继续。</p>':''}${task.previous_report?`<details class="ya-previous-report"><summary>上轮成果已保留 · ${list(task.previous_report.candidates).length} 个方向 / ${list(task.previous_report.evidence).length} 份资料</summary><p>上轮结果尚未按本轮新条件重新计算。本轮结果会独立更新。</p></details>`:''}${waiting?`<div class="ya-setup-callout"><div><h2>想法已保存，先连接研究工具。</h2><p>尚未执行的检索和模型调用不会显示为成功。</p><div><button type="button" class="ya-primary" data-agent-action="settings">配置 AI 与元宝 ${icon('arrow')}</button><button type="button" class="ya-inline" data-agent-action="continue" ${state.busy?'disabled':''}>已配置，继续研究</button></div></div></div>`:''}${profileProposalView(report,task,state)}<div class="ya-report-tabs" role="tablist" aria-label="研究结果">${[['overview','方案与候选'],['sources','来源依据'],['methods','算法与方法']].map(([key,label])=>`<button type="button" role="tab" id="ya-tab-${key}" aria-controls="ya-report-panel" aria-selected="${current===key}" data-agent-tab="${key}">${label}${key==='sources'&&list(report.evidence).length?`<span>${list(report.evidence).length}</span>`:''}</button>`).join('')}</div><div id="ya-report-panel" class="ya-report-panel" role="tabpanel" aria-labelledby="ya-tab-${current}">${current==='sources'?sourcesView(report,state):current==='methods'?methodsView(report,task):overview(report,task,state)}</div>${task.error?`<div class="ya-error" role="alert"><strong>这一步尚未完成</strong><p>${esc(task.error.message||'本轮研究未完成，已保留现有记录。')}</p></div>`:''}<div class="ya-scope-note">${icon('shield')}<p>未收录不代表不存在。资料保留真实年份，历史资料不等于目标年度报考条件。</p></div></div></div></section>`;
}

function profileProposalView(report,task,state={}){
 const proposal=object(report.profile_proposal);if(proposal.requires_confirmation!==true)return '';
 const updates=object(proposal.updates),labels={target_year:'目标入学年份',reference_year:'历史参考目录年份',subjects:'已准备科目',provinces:'意向地区',max_annual_tuition:'每年学费上限',max_total_tuition:'全程学费上限',max_subject_changes:'最多调整考试单元',study_mode:'学习方式',education:'学历情况',background:'本科或已有专业',ranking_mode:'比较偏好'};
 const value=(key,item)=>key==='subjects'?list(item).map(subject=>[subject.code,subject.name].filter(Boolean).join(' ')).join('、'):key==='provinces'?list(item).join('、'):['max_annual_tuition','max_total_tuition'].includes(key)?`${item} 元`:key==='education'?({bachelor:'本科',master:'硕士',associate:'专科',unknown:'尚未确认'}[item]||text(item)):key==='ranking_mode'?({balanced:'综合考虑',subjects:'备考衔接优先',cost:'费用优先'}[item]||text(item)):String(item);
 const fields=Object.entries(updates).filter(([key])=>Object.hasOwn(labels,key));if(!fields.length)return '';
 return `<section class="ya-profile-proposal"><div class="ya-proposal-heading"><span>${icon('sliders')}</span><div><h2>识别到的备考条件</h2><p>确认后，算法才会按这些条件重新比较。</p></div><small>待你确认</small></div><dl>${fields.map(([key,item])=>`<div><dt>${esc(labels[key])}</dt><dd>${esc(value(key,item))}</dd></div>`).join('')}</dl>${list(proposal.evidence).length?`<details><summary>查看对应的原句</summary>${list(proposal.evidence).slice(0,5).map(item=>`<blockquote>${esc(item.quote||'')}</blockquote>`).join('')}</details>`:''}<button type="button" class="ya-primary" data-agent-action="confirm-profile" ${state.busy?'disabled':''}>${isRunning(task)?'确认条件并更新研究':'确认条件并继续'} ${icon('arrow')}</button></section>`;
}
export function renderAgent(state={}){return state.task?workspace(state):welcome(state);}
function loadStyle(document){if(document.querySelector('link[data-ya-agent-style]'))return;const link=document.createElement('link');link.rel='stylesheet';link.href=new URL('./agent.css',import.meta.url).href;link.dataset.yaAgentStyle='';document.head.append(link);}

function syncAttributes(current,next,keepValue=false){
 for(const attribute of [...current.attributes])if(!(keepValue&&attribute.name==='value')&&!next.hasAttribute(attribute.name))current.removeAttribute(attribute.name);
 for(const attribute of next.attributes)if(!(keepValue&&attribute.name==='value')&&current.getAttribute(attribute.name)!==attribute.value)current.setAttribute(attribute.name,attribute.value);
}
function renderAroundInput(container,html,field){
 const template=container.ownerDocument.createElement('template');template.innerHTML=html;
 const nextField=[...template.content.querySelectorAll('input,textarea,select')].find(node=>node.name===field.name);
 if(!nextField)return false;
 const chain=(node,root)=>{const result=[];for(;node&&node!==root;node=node.parentNode)result.unshift(node);return [root,...result];};
 const current=chain(field,container),next=chain(nextField,template.content);
 if(current.length!==next.length||current.some((node,index)=>index&&node.nodeName!==next[index].nodeName))return false;
 // Removing and reinserting even the same input cancels native IME; keep its entire ancestor chain attached.
 for(let index=0;index<current.length-1;index++){
  const parent=current[index],branch=current[index+1],nextBranch=next[index+1];
  if(index)syncAttributes(parent,next[index]);
  for(const child of [...parent.childNodes])if(child!==branch)child.remove();
  let before=true;for(const sibling of next[index].childNodes){if(sibling===nextBranch){before=false;continue;}const copy=sibling.cloneNode(true);if(before)parent.insertBefore(copy,branch);else parent.append(copy);}
 }
 syncAttributes(field,nextField,true);
 return true;
}

const researchDrafts=new Map();
export function mountAgent(container,options={}){
 const {api,onSettings,onOpenProgram,onCoverage,onDiscover,onKnowledge,initialTaskId,onTaskChange}=options,document=container.ownerDocument;loadStyle(document);
 let disposed=false,generation=0,controller,timer,settingsController,settingsGeneration=0,revisionGeneration=0,revisionController,compositionTarget=null;
 let state={query:'',followup:researchDrafts.get(initialTaskId)||'',task:null,recent:[],settings:{},tab:'overview',busy:false,revising:false,revisionPhase:'',sourcePage:1,sourceFilter:'all',error:'',recentError:'',settingsStatus:'loading',settingsError:'',conditionsOpen:false,conditionDraft:{},showAllSchools:false};
 const remember=()=>{const id=taskId(state.task)||initialTaskId;if(id)researchDrafts.set(id,state.followup);if(researchDrafts.size>30)researchDrafts.delete(researchDrafts.keys().next().value);};
 const render=()=>{
  if(disposed)return;const active=document.activeElement,focus=container.contains(active)?{name:active.name,start:active.selectionStart,end:active.selectionEnd}:null;
  const opened=[...container.querySelectorAll('details[data-persist-key][open]')].map(node=>node.dataset.persistKey);
  const html=renderAgent(state),editing=container.contains(active)&&active.matches('input,textarea,select')&&active.name;
  const preserved=editing&&renderAroundInput(container,html,active);
  if(!preserved)container.innerHTML=html;
  for(const detail of container.querySelectorAll('details[data-persist-key]'))if(opened.includes(detail.dataset.persistKey))detail.open=true;
  if(!preserved&&focus?.name){const field=container.querySelector(`[name="${focus.name}"]`);field?.focus({preventScroll:true});if(field&&typeof focus.start==='number'&&typeof field.setSelectionRange==='function'&&(!field.type||['text','search','textarea'].includes(field.type)))field.setSelectionRange(focus.start,focus.end);}
 };
 const request=async(path,body)=>{controller?.abort();controller=new AbortController();const ticket=++generation;const result=await api(path,{...(body===undefined?{}:{body}),signal:controller.signal});if(disposed||ticket!==generation)throw Object.assign(new Error('stale request'),{name:'AbortError'});return result;};
 const accept=task=>{if(!taskId(task))throw new Error('研究服务未返回有效的任务编号。');state={...state,task:mergeTaskEvents(state.task,task),busy:state.revising,error:''};};
 const fail=error=>{if(disposed||error.name==='AbortError')return;state={...state,busy:false,revising:false,error:error.message||'这一步没有完成，请稍后重试。'};render();};
 const schedule=()=>{clearTimeout(timer);if(!disposed&&!state.revising&&shouldPoll(state.task))timer=setTimeout(poll,state.task.has_more_events?0:1500);};
 async function poll(){const id=taskId(state.task);if(!id||disposed||state.revising)return;try{accept(await request('/api/research?'+new URLSearchParams({research_id:id,after_event:String(state.task.last_event_id||0)})));render();schedule();}catch(error){fail(error);}}
 async function loadTask(id){state={...state,busy:true,error:''};render();try{accept(await request('/api/research?'+new URLSearchParams({research_id:id})));render();schedule();}catch(error){fail(error);}}
 async function create(){if(compositionTarget||state.busy)return;const field=container.querySelector('#ya-query');if(field)state={...state,query:field.value};if(!state.query.trim())return;state={...state,busy:true,error:''};render();try{accept(await request('/api/research',{query:state.query.trim()}));if(onTaskChange){onTaskChange(taskId(state.task));return;}render();schedule();}catch(error){fail(error);}}
 const bodyFor=extra=>({research_id:taskId(state.task),...(extra?.message?{message:extra.message}:state.followup.trim()?{message:state.followup.trim()}:{}),...(extra?.profile?{profile:extra.profile}:{})});
 const consumeDraft=(body,submitted)=>{
  const field=container.querySelector('#ya-followup');if(field&&field.value!==state.followup)state={...state,followup:field.value};
  if(!compositionTarget&&body.message===submitted.trim()&&state.followup===submitted){state={...state,followup:''};if(field)field.value='';}
 };
 async function resume(extra={}){if(compositionTarget||!state.task||state.busy)return;const field=container.querySelector('#ya-followup');if(field)state={...state,followup:field.value};if(isRunning(state.task)){await revise(extra);return;}const submittedDraft=state.followup,body=bodyFor(extra);clearTimeout(timer);state={...state,busy:true,error:''};render();try{accept(await request('/api/research/continue',body));consumeDraft(body,submittedDraft);state={...state,conditionDraft:{},conditionsOpen:false};remember();render();schedule();}catch(error){fail(error);}}
 async function cancel(){if(state.busy)return;clearTimeout(timer);state={...state,busy:true,error:''};render();try{accept(await request('/api/research/cancel',{research_id:taskId(state.task)}));render();schedule();}catch(error){fail(error);}}
 async function revise(extra={}){
  if(state.busy)return;const taskTurn=state.task.turn_id||0,submittedDraft=state.followup,body=bodyFor(extra);if(!body.message&&!body.profile){container.querySelector('#ya-followup')?.focus();return;}
  clearTimeout(timer);controller?.abort();generation++;revisionController?.abort();revisionController=new AbortController();const signal=revisionController.signal,ticket=++revisionGeneration;
  const alive=()=>!disposed&&!signal.aborted&&ticket===revisionGeneration;
  const call=async(path,body)=>{const data=await api(path,{...(body?{body}:{}),signal});if(!alive())throw Object.assign(new Error('cancelled'),{name:'AbortError'});return data;};
  let timedOut=false;const timeout=setTimeout(()=>{if(!disposed&&ticket===revisionGeneration){timedOut=true;revisionController.abort();}},22000);
  state={...state,busy:true,revising:true,revisionPhase:'stopping',error:''};remember();render();
  try{
   accept(await call('/api/research/cancel',{research_id:body.research_id}));render();const deadline=Date.now()+22000;
   while(state.task.can_continue!==true){
    if(Date.now()>=deadline)throw new Error('上轮调用还在结束中。你的草稿已保留，请稍后再点继续研究。');
    await new Promise((resolve,reject)=>{const aborted=()=>{clearTimeout(waitTimer);reject(Object.assign(new Error('cancelled'),{name:'AbortError'}));};const waitTimer=setTimeout(()=>{signal.removeEventListener('abort',aborted);resolve();},350);signal.addEventListener('abort',aborted,{once:true});});
    accept(await call('/api/research?'+new URLSearchParams({research_id:body.research_id,after_event:String(state.task.last_event_id||0)})));render();
   }
   state={...state,revisionPhase:'submitting'};render();
   accept(await call('/api/research/continue',body));consumeDraft(body,submittedDraft);state={...state,busy:false,revising:false,conditionDraft:{},conditionsOpen:false};remember();render();schedule();
  }catch(error){
   if(disposed||ticket!==revisionGeneration)return;
   if(state.revisionPhase==='submitting'){
    clearTimeout(timeout);state={...state,busy:true,revising:false,error:'更新请求已发送，正在核对服务端状态，不会重复提交。'};render();
    const abort=setTimeout(()=>controller?.abort(),8000);
    try{const confirmed=await request('/api/research?'+new URLSearchParams({research_id:body.research_id}));accept(confirmed);state={...state,busy:false,revising:false,error:number(confirmed.turn_id)!==null&&confirmed.turn_id>taskTurn?'':'暂未确认新一轮已启动，草稿已保留。请核对任务后再决定是否继续。'};render();schedule();}
    catch(reconcileError){if(!disposed)fail(new Error('暂时无法确认更新结果。草稿已保留，请刷新任务后再继续，系统不会自动重复提交。'));}
    finally{clearTimeout(abort);}
    return;
   }
   if(timedOut){fail(new Error('等待上轮结束超时，草稿已保留。请稍后再继续研究。'));return;}
   if(!alive()||error.name==='AbortError')return;fail(error);
  }finally{clearTimeout(timeout);}
 }
 async function recent(){try{const value=await api('/api/research?limit=4');if(!disposed){state={...state,recent:list(value.tasks),recentError:''};if(!state.task)render();}}catch(error){if(!disposed){state={...state,recentError:'最近的研究暂时无法读取。'};if(!state.task)render();}}}
 function submitConditions(){
  const updates={};for(const key of editableFields){if(!Object.hasOwn(state.conditionDraft,key))continue;const value=state.conditionDraft[key];updates[key]=key==='provinces'?Array.isArray(value)?value:text(value).split(/[、,，]/).map(v=>v.trim()).filter(Boolean):['target_year','reference_year','max_subject_changes','max_annual_tuition'].includes(key)?value===''?null:Number(value):value;}
  if(!Object.keys(updates).length){state={...state,error:'请先修改一项你希望调整的条件。'};render();return;}
  if(Object.values(updates).some(value=>typeof value==='number'&&!Number.isFinite(value))){state={...state,error:'请填写有效的数字条件。'};render();return;}
  resume({profile:updates,message:'请按我明确填写的条件重新比较'});
 }
 const input=event=>{const name=event.target.name;if(['query','followup'].includes(name)){state={...state,[name]:event.target.value};remember();}else if(name?.startsWith('condition.')&&editableFields.includes(name.slice(10)))state={...state,conditionDraft:{...state.conditionDraft,[name.slice(10)]:event.target.value}};};
 const compositionStart=event=>{if(event.target.matches('input,textarea'))compositionTarget=event.target;};
 const compositionEnd=event=>{if(event.target===compositionTarget){compositionTarget=null;input(event);}};
 const submit=event=>{if(['ya-create-form','ya-continue-form','ya-conditions-form'].includes(event.target.id)){event.preventDefault();if(compositionTarget)return;if(event.target.id==='ya-create-form')create();if(event.target.id==='ya-continue-form')resume();if(event.target.id==='ya-conditions-form')submitConditions();}};
 const keydown=event=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter'&&['query','followup'].includes(event.target.name)){event.preventDefault();if(compositionTarget||event.isComposing||event.keyCode===229)return;event.target.name==='query'?create():resume();}};
 const click=event=>{
  const button=event.target.closest?.('[data-agent-action],[data-agent-tab],[data-agent-task],[data-agent-example],[data-agent-program],[data-agent-knowledge],[data-agent-question],[data-agent-source-filter],[data-agent-source-page],[data-agent-answer]');if(!button||!container.contains(button))return;const data=button.dataset;
  if(data.agentAnswer!==undefined&&!state.busy){const question=list(state.task?.report?.questions)[Number(data.agentAnswer)],option=list(question?.options)[Number(data.agentOption)];if(editableFields.includes(question?.field)&&option&&Object.hasOwn(option,'value')){state={...state,conditionDraft:{...state.conditionDraft,[question.field]:option.value}};render();container.querySelector(`[data-agent-answer="${data.agentAnswer}"][data-agent-option="${data.agentOption}"]`)?.focus({preventScroll:true});}return;}
  if(data.agentSourceFilter){state={...state,sourceFilter:data.agentSourceFilter,sourcePage:1};render();container.querySelector(`[data-agent-source-filter="${data.agentSourceFilter}"]`)?.focus({preventScroll:true});return;}
  if(data.agentSourcePage){state={...state,sourcePage:Number(data.agentSourcePage)};render();const heading=container.querySelector('.ya-sources-intro h2');heading?.setAttribute('tabindex','-1');heading?.scrollIntoView({block:'start',behavior:'auto'});heading?.focus({preventScroll:true});return;}
  if(data.agentTab){state={...state,tab:data.agentTab};render();container.querySelector(`[data-agent-tab="${data.agentTab}"]`)?.focus({preventScroll:true});return;}
  if(data.agentTask){remember();if(onTaskChange)onTaskChange(data.agentTask);else loadTask(data.agentTask);return;}
  if(data.agentProgram){remember();onOpenProgram?.(data.agentProgram);return;}
  if(data.agentKnowledge){remember();onKnowledge?.({entry_id:data.agentKnowledge,...(['accepted','pending'].includes(data.agentKnowledgeQuality)?{quality:data.agentKnowledgeQuality}:{})});return;}
  if(data.agentQuestion!==undefined){const question=list(state.task?.report?.questions)[Number(data.agentQuestion)],item=typeof question==='string'?question:question?.label||question?.message||'';state={...state,followup:state.followup||item+'：'};remember();render();container.querySelector('#ya-followup')?.focus({preventScroll:true});return;}
  if(data.agentExample!==undefined){state={...state,query:examples[Number(data.agentExample)]||''};const input=container.querySelector('[name="query"]');if(input){input.value=state.query;input.focus();}return;}
  const action=data.agentAction;
  if(action==='methods'){state={...state,tab:'methods'};render();container.querySelector('[data-agent-tab="methods"]')?.focus({preventScroll:true});return;}
  if(action==='apply-answers'){submitConditions();return;}
  if(action==='settings'){remember();onSettings?.();}if(action==='knowledge'){remember();onKnowledge?.({});}if(action==='settings-status')readSettings();if(action==='discover')onDiscover?.();if(action==='coverage')onCoverage?.();if(action==='recent')recent();if(action==='continue')resume();
  if(action==='confirm-profile')resume({profile:state.task.report?.profile_proposal?.updates,message:'确认以上备考条件，请重新比较'});
  if(action==='cancel'&&!state.busy)cancel();if(action==='refresh')loadTask(taskId(state.task));if(action==='schools'){state={...state,showAllSchools:!state.showAllSchools};render();}
  if(action==='abort-revise'&&state.revisionPhase==='stopping'){revisionGeneration++;revisionController?.abort();state={...state,busy:false,revising:false,error:'已取消这次更新，草稿仍保留。'};render();}
  if(action==='home'){remember();clearTimeout(timer);controller?.abort();revisionController?.abort();revisionGeneration++;generation++;state={...state,task:null,error:'',busy:false,revising:false,tab:'overview'};if(onTaskChange)onTaskChange('');else{render();recent();}}
 };
 const toggle=event=>{if(event.target.classList?.contains('ya-condition-editor'))state={...state,conditionsOpen:event.target.open};};
 container.addEventListener('input',input);container.addEventListener('change',input);container.addEventListener('compositionstart',compositionStart);container.addEventListener('compositionend',compositionEnd);container.addEventListener('submit',submit);container.addEventListener('click',click);container.addEventListener('keydown',keydown);container.addEventListener('toggle',toggle,true);render();
 async function readSettings(){settingsController?.abort();settingsController=new AbortController();const ticket=++settingsGeneration;state={...state,settingsStatus:'loading',settingsError:''};render();try{const value=await api('/api/settings',{signal:settingsController.signal});if(disposed||ticket!==settingsGeneration)return;state={...state,settings:object(value),settingsStatus:'ready'};render();}catch(error){if(disposed||ticket!==settingsGeneration||error.name==='AbortError')return;state={...state,settingsStatus:'error',settingsError:'暂时无法读取服务状态，连接情况尚不能确认。'};render();}}
 readSettings();if(initialTaskId)loadTask(initialTaskId);else recent();
 return ()=>{remember();disposed=true;compositionTarget=null;generation++;revisionGeneration++;clearTimeout(timer);controller?.abort();revisionController?.abort();settingsGeneration++;settingsController?.abort();container.removeEventListener('input',input);container.removeEventListener('change',input);container.removeEventListener('compositionstart',compositionStart);container.removeEventListener('compositionend',compositionEnd);container.removeEventListener('submit',submit);container.removeEventListener('click',click);container.removeEventListener('keydown',keydown);container.removeEventListener('toggle',toggle,true);};
}
