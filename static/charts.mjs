import {safeHttpUrl} from './data.mjs';
import {esc} from './workbench-model.mjs';

const number=value=>Number.isFinite(value)?Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2}):'未知';
const finite=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0;
const evidenceReady=row=>['verified','derived'].includes(row?.evidence_status);
const normalize=value=>String(value??'').replace(/\s+/g,'').replace(/^\d+(?=[\u4e00-\u9fff])/,'');
const sourceOf=row=>(Array.isArray(row.evidence)?row.evidence:[row.evidence]).find(item=>safeHttpUrl(item?.url)&&typeof item?.quote==='string'&&item.quote.trim());
const sourceButton=(programId,claimId,label)=>`<button type="button" class="ych-source" data-action="evidence" data-program-id="${esc(programId)}" data-claim-id="${esc(claimId)}">${esc(label)} <span aria-hidden="true">↗</span></button>`;
const absent=message=>`<div class="ych-empty"><p>${message}</p><span>资料缺失保留空位，不推算分数，也不把缺失值记为零。</span></div>`;
const modes={first_choice:'一志愿公示样本',transfer:'调剂公示样本',not_specified:'一志愿 / 调剂类别未单列'};
const kinds={national:'国家线',university:'学校复试线',department:'学院复试线',program:'专业复试线'};

function windowYears(program) {
 const years=(program.historyWindow||[]).filter(year=>Number.isInteger(year)&&year>=2000&&year<=2100);
 const latest=years.length?Math.max(...years):null;
 return latest===null?[]:[latest-2,latest-1,latest];
}

function explicitMaximum(record) {
 const values=['full_score','full_marks','score_max'].filter(field=>record?.[field]!==undefined&&record[field]!==null).map(field=>record[field]);
 if(!values.length)return null;
 return values.every(value=>finite(value)&&value>0&&value===values[0])?values[0]:NaN;
}

function sameProgram(program,row,broad=false) {
 const raw=program.raw||{},unit=program.unit_id||raw.unit_id,code=program.code||raw.code;
 const department=normalize(program.department||raw.department),mode=program.mode||raw.study_mode;
 if(!unit||row.unit_id!==unit)return false;
 if(row.study_mode&&row.study_mode!==mode)return false;
 if(broad)return !row.program_code||row.program_code===code;
 if(!department||!normalize(row.department)||normalize(row.department)!==department)return false;
 if(!row.study_mode||row.study_mode!==mode)return false;
 if(row.program_code?row.program_code!==code:normalize(row.program_name)!==normalize(raw.name))return false;
 return !row.direction||String(row.direction).replace(/\s+/g,'')===String(raw.direction??'').replace(/\s+/g,'');
}

function pointOf(row,kind) {
 if(!evidenceReady(row))return null;
 const scores=kind==='admitted'?row.initial:{min:row.total,max:row.total,...Object.fromEntries(['full_score','full_marks','score_max'].filter(key=>row[key]!==undefined).map(key=>[key,row[key]]))};
 if(!scores||!finite(scores.min)||!finite(scores.max)||scores.min>scores.max||!sourceOf(row)||typeof row.id!=='string'||!row.id)return null;
 const fullScore=explicitMaximum(scores);
 if(fullScore!==null&&(!Number.isFinite(fullScore)||scores.max>fullScore))return null;
 const median=finite(scores.median)&&scores.median>=scores.min&&scores.median<=scores.max?scores.median:null;
 return {id:row.id,year:row.year,min:scores.min,max:scores.max,median,
  count:Number.isInteger(row.sample_count)&&row.sample_count>=0?row.sample_count:null,
  fullScore,claimId:`${kind==='admitted'?'history':'score'}:${row.id}`};
}

function seriesOf(program,rows,years,kind) {
 const grouped=new Map();
 for(const row of rows){
  const broad=kind==='reference_line'||kind==='score_line'&&['national','university'].includes(row.kind);
  if(!years.includes(row.year)||!sameProgram(program,row,broad))continue;
  const point=pointOf(row,kind);if(!point)continue;
  const scope=kind==='admitted'?(row.direction?`方向：${row.direction}`:'院系专业范围 · 未细分方向'):[kinds[row.kind]||'复试线范围未明确',row.department,row.discipline_name||row.program_name].filter(Boolean).join(' · ');
  const category=kind==='admitted'?modes[row.admission_source]||row.admission_source||'一志愿 / 调剂类别未单列':kinds[row.kind]||'复试线范围未单列';
  const plan=['unmarked','not_specified',undefined,null,''].includes(row.plan_type)?'计划类别未单列':row.plan_type==='ordinary_base_line'?'学校公布的基本线':row.plan_type;
  const prefixes=Array.isArray(row.code_prefixes)?row.code_prefixes.filter(value=>typeof value==='string').toSorted():[];
  const key=JSON.stringify([row.unit_id,normalize(row.department),row.program_code||normalize(row.program_name),row.study_mode||'',row.degree_type||'',row.discipline_code||'',prefixes,scope,row.cohort||'',plan,category,point.fullScore]);
  const referenceScope=[row.degree_type,row.discipline_name||row.program_name,prefixes.length?`代码范围 ${prefixes.join(' / ')}`:row.discipline_code?`门类代码 ${row.discipline_code}`:''].filter(Boolean).join(' · ');
  const entry=grouped.get(key)||{key,kind,scope,category,plan,cohort:row.cohort||'公示样本',referenceScope,scopeNote:row.scope_note||row.applicability_note||'',fullScore:point.fullScore,points:[]};
  grouped.set(key,{...entry,points:[...entry.points,point]});
 }
 return [...grouped.values()].flatMap(group=>{
  const repeated=new Set(group.points.map(point=>point.year)).size!==group.points.length;
  return repeated?group.points.map(point=>({...group,key:`${group.key}:${point.id}`,points:[point],separatePublications:true})):group;
 });
}

export function buildHistorySeries(program={}) {
 const years=windowYears(program),rows=(program.history||[]).map(row=>row?.statistic).filter(row=>row&&typeof row==='object');
 const lines=[...(program.scoreLines||[]),...(program.referenceScoreLines||[]),...(program.unverifiedScoreLines||[])];
 const pendingRows=[...rows.filter(row=>sameProgram(program,row)),...lines.filter(row=>sameProgram(program,row,['national','university'].includes(row.kind)))];
 const pendingEvidenceCount=new Set(pendingRows.filter(row=>years.includes(row.year)&&!evidenceReady(row)).map(row=>row.id)).size;
 return {years,pendingEvidenceCount,admitted:seriesOf(program,rows,years,'admitted'),scoreLines:seriesOf(program,program.scoreLines||[],years,'score_line'),referenceLines:seriesOf(program,program.referenceScoreLines||[],years,'reference_line')};
}

function domainFor(group) {
 const minimum=Math.min(...group.points.map(point=>point.min)),maximum=Math.max(...group.points.map(point=>point.max));
 const margin=Math.max(10,(maximum-minimum)*.15);
 const low=Math.max(0,Math.floor((minimum-margin)/10)*10);
 const high=Math.min(group.fullScore??Infinity,Math.ceil((maximum+margin)/10)*10);
 return {low,high:high>low?high:low+10};
}

function scoreGraphic(group,years,programId,index) {
 const {low,high}=domainFor(group),x=index=>104+index*242,y=value=>174-(value-low)/(high-low)*142;
 const byYear=new Map(group.points.map(point=>[point.year,point]));
 const grid=Array.from({length:5},(_,index)=>{
  const value=low+(high-low)*index/4,position=y(value);
  return `<g class="ych-grid"><line x1="64" y1="${position}" x2="656" y2="${position}"/><text x="48" y="${position+5}" text-anchor="end">${number(value)}</text></g>`;
 }).join('');
 const trend=group.kind==='admitted'&&group.fullScore!==null?years.slice(1).map((year,index)=>{
  const before=byYear.get(years[index]),after=byYear.get(year);
  return before?.median!==null&&before?.median!==undefined&&after?.median!==null&&after?.median!==undefined?`<path class="ych-trend" d="M ${x(index)} ${y(before.median)} L ${x(index+1)} ${y(after.median)}"/>`:'';
 }).join(''):'';
 const points=years.map((year,index)=>{
  const point=byYear.get(year),position=x(index);
  if(!point)return `<g class="ych-missing"><line x1="${position}" y1="94" x2="${position}" y2="116"/><text x="${position}" y="137" text-anchor="middle">未收录</text><text x="${position}" y="210" text-anchor="middle">${year}</text></g>`;
  const attrs=`data-action="evidence" data-program-id="${esc(programId)}" data-claim-id="${esc(point.claimId)}"`;
  const median=point.median!==null?`<circle class="ych-median" cx="${position}" cy="${y(point.median)}" r="6"/><text class="ych-value" x="${position+16}" y="${y(point.median)+5}">${number(point.median)}</text>`:'';
  return `<g class="ych-point" ${attrs}><title>${year}：${number(point.min)}—${number(point.max)}${point.median!==null?`，中位数 ${number(point.median)}`:''}，点击查看原文依据</title><line class="ych-range" x1="${position}" y1="${y(point.max)}" x2="${position}" y2="${y(point.min)}"/><line class="ych-cap" x1="${position-15}" y1="${y(point.max)}" x2="${position+15}" y2="${y(point.max)}"/><line class="ych-cap" x1="${position-15}" y1="${y(point.min)}" x2="${position+15}" y2="${y(point.min)}"/>${median}<text class="ych-bound" x="${position-24}" y="${y(point.max)-10}" text-anchor="end">${number(point.max)}</text>${point.min!==point.max?`<text class="ych-bound" x="${position-24}" y="${y(point.min)+18}" text-anchor="end">${number(point.min)}</text>`:''}<text x="${position}" y="210" text-anchor="middle">${year}</text></g>`;
 }).join('');
 const titleId=`ych-${group.kind}-${index}`;
 return `<div class="ych-scroll" role="region" aria-label="${group.kind==='admitted'?'录取公示初试区间':'复试分数线'}图表，可横向滑动" tabindex="0"><svg class="ych-score" viewBox="0 0 720 230" role="img" aria-labelledby="${titleId}"><title id="${titleId}">${group.kind==='admitted'?'公示样本初试最低、最高及中位数':'复试总分线'}；缺失年度不补值，原始数据及依据见下方</title>${grid}${trend}${points}</svg></div>`;
}

function scoreTable(group,years,programId) {
 return `<details class="ych-table-details"><summary>查看数据表与原文依据</summary><div class="ych-table-scroll"><table><caption>${group.kind==='admitted'?'公示样本初试原始分数':'官方复试总分线'} · ${esc(group.category)} · ${esc(group.plan)}</caption><thead><tr><th>年份</th><th>${group.kind==='admitted'?'初试区间':'总分线'}</th>${group.kind==='admitted'?'<th>中位数</th><th>公示样本</th>':''}<th>原文</th></tr></thead><tbody>${years.map(year=>{
  const point=group.points.find(point=>point.year===year);
  return `<tr><th>${year}</th><td>${point?(group.kind==='admitted'?`${number(point.min)}—${number(point.max)}`:number(point.min)):'未收录'}</td>${group.kind==='admitted'?`<td>${point?number(point.median):'未收录'}</td><td>${point&&point.count!==null?`${number(point.count)} 人`:'未注明'}</td>`:''}<td>${point?sourceButton(programId,point.claimId,`${year} 原文依据`):'—'}</td></tr>`;
 }).join('')}</tbody></table></div></details>`;
}

function mobileScoreGraphic(group,years,programId) {
 const {low,high}=domainFor(group),fraction=value=>(high-value)/(high-low)*100;
 const columns=years.map(year=>{
  const point=group.points.find(point=>point.year===year);
  if(!point)return `<div class="ych-mini-column is-missing"><span class="ych-mini-year">${year}</span><span class="ych-mini-axis"><i></i></span><span class="ych-mini-values">未收录</span></div>`;
  const top=fraction(point.max),height=(point.max-point.min)/(high-low)*100;
  const marker=point.median!==null?`<i class="ych-mini-median" style="top:${fraction(point.median).toFixed(2)}%"></i>`:'';
  const values=group.kind==='admitted'?`${number(point.min)}—${number(point.max)}`:number(point.min);
  return `<button type="button" class="ych-mini-column" data-action="evidence" data-program-id="${esc(programId)}" data-claim-id="${esc(point.claimId)}" aria-label="${year} ${esc(values)}，查看原文依据"><span class="ych-mini-year">${year}</span><span class="ych-mini-axis"><i class="ych-mini-range" style="top:${top.toFixed(2)}%;height:${height.toFixed(2)}%"></i>${marker}</span><span class="ych-mini-values">${values}</span>${point.median!==null?`<span class="ych-mini-median-value">中位 ${number(point.median)}</span>`:''}</button>`;
 }).join('');
 return `<div class="ych-mobile-plot"><div class="ych-mini-grid">${columns}</div><p class="ych-mini-scale">原始分数范围 ${number(low)}—${number(high)}；点击数据查看依据。</p></div>`;
}

function scoreCard(group,years,programId,index) {
 const reference=group.kind==='reference_line';
 const label=group.kind==='admitted'?'录取公示样本 · 初试分数':reference?'学校门类参考线 · 适用关系待核':'复试分数线 · 总分';
 const dates=years.map(year=>{const point=group.points.find(point=>point.year===year);return `<div><span>${year}</span>${point?sourceButton(programId,point.claimId,'查看依据'):'<span class="ych-unavailable">资料未收录</span>'}</div>`;}).join('');
 const disclaimer=group.kind==='admitted'?'样本区间仅来自对应公示，不代表全体最终录取考生。':reference?'学校门类基本线的适用关系待核，各学院可能提高要求，不能直接认定为本专业复试线。':'复试线表示进入复试的明示门槛，不能填作录取最低分。';
 return `<section class="ych-card" data-chart="${group.kind==='admitted'?'admitted-range':reference?'school-reference-line':'retest-line'}"><div class="ych-heading"><div><span class="ych-eyebrow">${label}</span><h3>${esc(group.category)}</h3></div><span class="ych-badge">${group.fullScore!==null?`满分 ${number(group.fullScore)}`:'满分口径未注明'}</span></div><p class="ych-scope">${reference?esc(group.referenceScope||'学校原始范围待核'):esc(group.scope)} · ${esc(group.plan)}${group.kind==='admitted'?` · ${esc(group.cohort)}`:''}</p>${scoreGraphic(group,years,programId,index)}${mobileScoreGraphic(group,years,programId)}<div class="ych-year-sources">${dates}</div><div class="ych-legend"><span><i></i>${group.kind==='admitted'?'公示样本最低—最高':reference?'学校门类基本线（适用关系待核）':'复试总分线'}</span>${group.kind==='admitted'?'<span><i class="ych-legend-dot"></i>初试中位数（原文可计算时）</span>':''}</div><p class="ych-note">${disclaimer} ${esc(group.scopeNote)} ${group.fullScore===null?'满分未注明，按各年度原始分数分开保留，不连接跨年趋势。':'仅同范围、同类别、同满分且连续年份的中位数连接；空缺处断开。'}${group.separatePublications?' 同年多份公示分开保留，样本未合并。':''}</p>${scoreTable(group,years,programId)}</section>`;
}

export function renderHistoryCharts(program={}) {
 const model=buildHistorySeries(program);
 if(!model.years.length)return absent('历史年度尚未明确，暂不绘制年度图。');
 const admitted=model.admitted.map((group,index)=>scoreCard(group,model.years,program.id,index)).join('');
 const lines=model.scoreLines.map((group,index)=>scoreCard(group,model.years,program.id,index)).join('');
 const references=model.referenceLines.map((group,index)=>scoreCard(group,model.years,program.id,index)).join('');
 const pending=model.pendingEvidenceCount?`<p class="ych-pending" role="status">${model.pendingEvidenceCount} 条历史记录的依据待核，暂不绘制为确定分数；请核对对应原文与统计口径。</p>`:'';
 return `<div class="ych-history"><div class="ych-section-heading"><div><span class="ych-eyebrow">把年度与口径一起看清</span><h2>历史分数，有据可查。</h2></div><span class="ych-muted">${model.years[0]}—${model.years.at(-1)}</span></div>${pending}${admitted||absent('尚未收录可绘制的录取公示样本。')}${lines||`<p class="ych-note">明确适用于本专业的复试线资料尚未收录。</p>`}${references}</div>`;
}

function candidateLabel(program) {
 return [program.name,program.department,program.code,program.raw?.direction||program.major,program.mode].filter(Boolean).join(' · ');
}

function metricRows(programs,kind) {
 return programs.map(program=>{
  const change=program.evaluation?.subject_changes;
  const value=kind==='annual_fee'?program.annualFee:kind==='total_fee'?program.totalFee:kind==='duration'?program.duration:change&&!change.uncertain?change.count:null;
  return {programId:program.id,label:candidateLabel(program),value:finite(value)?value:null,
   claimId:kind.includes('fee')?'tuition':kind==='duration'?'duration':'subjects'};
 });
}

export function buildComparisonMetrics(programs=[]) {
 const selected=programs.slice(0,3),years=[...new Set(selected.map(program=>program.year??null))];
 return years.flatMap(year=>{
  const scoped=selected.filter(program=>(program.year??null)===year),hasAnnual=scoped.some(program=>finite(program.annualFee)),hasTotal=scoped.some(program=>finite(program.totalFee));
  const kinds=[...(hasAnnual||!hasTotal?['annual_fee']:[]),...(hasTotal?['total_fee']:[]),'duration',...(scoped.some(program=>program.evaluation)?['subject_changes']:[])];
  return kinds.map(kind=>{
   const rows=metricRows(scoped,kind),values=rows.map(row=>row.value).filter(value=>value!==null);
   return {kind,year,rows,max:values.length?Math.max(...values):null,knownCount:values.length};
  });
 });
}

function metricCard(metric) {
 const labels={annual_fee:['年学费','元 / 年','元'],total_fee:['全程学费','元 / 全程','元'],duration:['培养学制','年','年'],subject_changes:['备考调整','考试单元','个考试单元']};
 const [label,unit,suffix]=labels[metric.kind];
 const known=metric.knownCount;
 const rows=metric.rows.map(row=>{
  const width=row.value===null?null:metric.max?Math.min(100,row.value/metric.max*100):0;
  return `<div class="ych-metric-row"><div class="ych-metric-name">${esc(row.label)}</div><div class="ych-bar-track ${width===null?'is-unknown':''}">${width===null?'<span>未知</span>':`<span class="ych-bar-fill" style="width:${width.toFixed(2)}%"></span>`}</div><div class="ych-metric-value">${row.value===null?'未知':`${number(row.value)} ${suffix}`} ${sourceButton(row.programId,row.claimId,`查看${metric.kind==='subject_changes'?'科目':''}依据`)}</div></div>`;
 }).join('');
 const note=metric.kind==='annual_fee'||metric.kind==='total_fee'?'仅比较所示收费口径；住宿、生活费及未公布项目未计入，不从学制推填全程费用。':metric.kind==='duration'?'学制按原文记录；特殊培养项目和延期安排仍需单独核对。':'由当前备考组合与目录科目计算；未知科目不判为零调整，数量不表示录取概率。';
 return `<section class="ych-card ych-metric-card" data-chart="comparison-metric" data-metric="${metric.kind}"><div class="ych-heading"><h3>${label}</h3><span class="ych-badge">${unit}</span></div><p class="ych-scope">参考年度 ${esc(metric.year??'未注明')} · ${known}/${metric.rows.length} 项有明确数值</p>${!known&&metric.kind.includes('fee')?'<p class="ych-note">费用尚未收录，暂不能比较。</p>':''}${rows}<p class="ych-note">${note}</p></section>`;
}

export function renderComparisonCharts(programs=[]) {
 const metrics=buildComparisonMetrics(programs);
 return metrics.length?`<section class="ych-comparison" aria-label="候选费用与备考差异图"><div class="ych-section-heading"><div><span class="ych-eyebrow">把取舍看得具体</span><h2>看清费用，也看清准备成本。</h2></div></div><div class="ych-comparison-grid">${metrics.map(metricCard).join('')}</div></section>`:'';
}

export function renderRouteCoverage(result) {
 const routes=(Array.isArray(result?.routes)?result.routes:[]).filter(route=>finite(route.coverage?.program_count));
 if(!routes.length)return '';
 const maximum=Math.max(0,...routes.map(route=>route.coverage.program_count));
 const integer=value=>Number.isInteger(value)&&value>=0?number(value):'未知';
 const rows=routes.map(route=>{
  const count=route.coverage.program_count,width=maximum?count/maximum*100:0;
  const label=route.max_subject_changes===0?'沿用现有备考':`最多调整 ${integer(route.max_subject_changes)} 个单元`;
  return `<div class="ych-route-row"><div class="ych-route-heading"><strong>${label}</strong><span><b>${number(count)}</b> 个方向</span></div><div class="ych-route-track"><span style="width:${width.toFixed(2)}%"></span></div><div class="ych-route-meta"><span>${integer(route.coverage.department_program_count)} 个院系专业 · ${integer(route.coverage.unit_count)} 所学校</span><span>${integer(route.matched_count)} 个已知条件相容 · ${integer(route.needs_confirmation_count)} 个待核验</span></div></div>`;
 }).join('');
 return `<section class="ych-card ych-route-coverage" data-chart="route-coverage" aria-label="备考路线覆盖方向数"><div class="ych-heading"><div><span class="ych-eyebrow">调整带来的选择空间</span><h3>多准备一步，增加哪些选择？</h3></div><span class="ych-badge">已收录参考目录</span></div>${rows}<p class="ych-note">主柱按方向计数，院系专业和学校分别去重。覆盖包含资格待核验方向，不等于可报考数量；科目身份未完整的方向未计入任一路线。</p></section>`;
}
