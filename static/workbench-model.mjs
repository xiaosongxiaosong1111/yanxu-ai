import {safeHttpUrl} from './data.mjs';

export const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const count=value=>Number.isFinite(value)&&value>=0?String(value):'未知';
export const programLabel=program=>[program?.unit_name||program?.raw?.unit_name||program?.name,program?.raw?.department||program?.department,program?.major||[program?.raw?.name||program?.name,program?.raw?.direction||program?.direction].filter(Boolean).join(' · ')].filter(Boolean).join(' · ');
export function sourceLink(source,label='查看官方原文') {
 const href=safeHttpUrl(typeof source==='string'?source:source?.url||source?.source_url||source?.final_url);
 return href?`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`:'<span class="wb-muted">原文链接待核验</span>';
}
export function displayValue(value,field='',period='') {
 if(value===null||value===undefined||value==='')return '未知';
 if(field==='tuition'&&typeof value==='object')return Number.isFinite(value.amount)?displayValue(value.amount,'tuition',value.period):Number.isFinite(value.total)?displayValue(value.total,'tuition','total'):'未知';
 if(field==='tuition'&&typeof value==='number')return `${value.toLocaleString('zh-CN')} 元${period==='year'?'/年':period==='total'?'/全程':'（计费口径待核）'}`;
 if(field==='subjects'&&Array.isArray(value))return value.length?value.map(subject=>`${Number.isInteger(subject.exam_unit)?`考试单元 ${subject.exam_unit}：`:''}${[subject.code,subject.name].filter(Boolean).join(' ')}`).join('；'):'原记录未列出';
 if(['duration','duration_years'].includes(field)&&typeof value==='number')return `${value} 年`;
 if(Array.isArray(value)&&value.every(item=>typeof item==='string'))return value.join('；')||'原记录未列出';
 if(typeof value==='object')return JSON.stringify(value);
 return String(value);
}
export function buildRouteRequest(context,scopeUnit='') {
 const realIds=[...new Set((context.units||[]).filter(unit=>unit.annual_status!=='not_listed').map(unit=>unit.id).filter(Boolean))];
 const unitIds=scopeUnit?[scopeUnit]:realIds;
 if(!unitIds.length||unitIds.some(id=>!realIds.includes(id)))throw new Error('请选择真实的已收录学校作为分析范围。');
 const profile=structuredClone(context.profile||{});
 if(Number.isInteger(context.referenceYear))profile.reference_year=context.referenceYear;
 return {profile,unit_ids:unitIds};
}
export const scopeKey=(context,scopeUnit='')=>JSON.stringify(buildRouteRequest(context,scopeUnit));

export function buildAdjustmentRequest(context,draft) {
 const request=buildRouteRequest(context,draft.scopeUnit||''),profile=request.profile,allowed={};
 if(draft.allowSubjects) {
  if(!Number.isInteger(profile.max_subject_changes))throw new Error('请先在个人条件中设置原换科上限，再试算增加。');
  const baseline=profile.max_subject_changes;
  const choices=[1,2].filter(value=>value>baseline);
  if(!choices.length)throw new Error('当前换科上限已是2个考试单元，没有可增加的选项。');
  allowed.max_subject_changes=choices;
 }
 if(draft.allowRegions)allowed.provinces=expandedRegions(profile,draft.regionInput);
 if(draft.allowBudget)allowed.max_annual_tuition=increasedBudget(profile,draft.budgetInput);
 if(!Object.keys(allowed).length)throw new Error('请先明确勾选允许调整的条件。');
 return {...request,allowed_changes:allowed,goal_min_candidates:1};
}
function expandedRegions(profile,input) {
 const current=Array.isArray(profile.provinces)?profile.provinces:[];
 if(!current.length)throw new Error('当前未限制地区，无需扩大地区范围。');
 const added=[...new Set(String(input||'').split(/[，,、\s]+/).filter(Boolean))];
 if(!added.length||added.some(value=>value.length>30)||added.length>10)throw new Error('请填写至多10个真实地区名称，以顿号或逗号分隔。');
 const expanded=[...new Set([...current,...added])];
 if(expanded.length===current.length)throw new Error('请至少填写一个新增地区。');
 return [expanded];
}
function increasedBudget(profile,input) {
 if(!Number.isFinite(profile.max_annual_tuition))throw new Error('请先在个人条件中填写原预算，再试算提高预算。');
 if(String(input??'').trim()==='')throw new Error('请填写允许提高到的年学费预算。');
 const value=Number(input);
 if(!Number.isFinite(value)||value<0||value>10000000||value<=profile.max_annual_tuition)throw new Error('新预算应高于原预算，且为有效的年学费金额。');
 return [value];
}
export function createRequestGate() {
 let requests=new Map(),closed=false;
 const abort=slot=>{requests.get(slot)?.abort();requests.delete(slot);};
 return {
  begin(slot){abort(slot);const controller=new AbortController();if(closed)controller.abort();else requests.set(slot,controller);return {signal:controller.signal,current:()=>!closed&&!controller.signal.aborted&&requests.get(slot)===controller};},
  abort,
  close(){closed=true;for(const controller of requests.values())controller.abort();requests=new Map();}
 };
}
export function createDraft(context) {
 return {scopeUnit:context.selectedUnitId||'',allowSubjects:false,allowRegions:false,allowBudget:false,regionInput:'',budgetInput:'',watchIds:[],watchOpen:false,watchDirty:false,proofProgram:'',proofKind:'charter'};
}
export function initialState(context) {
 return {draft:createDraft(context),selectedUnitId:context.selectedUnitId||'',profileKey:JSON.stringify([context.profile,context.referenceYear,(context.units||[]).map(unit=>unit.id)]),routeResult:null,adjustmentResult:null,selectedRoute:'subjects-0',radar:null,proofTasks:null,proofResults:{},busy:{},errors:{}};
}
