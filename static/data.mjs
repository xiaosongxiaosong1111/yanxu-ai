export const defaultProfile = { target_year:2028, reference_year:2027, education:'unknown', background:'', subjects:[], ranking_mode:'balanced', preferences:{cost:2,subjects:3,location:2} };
export const defaultPreferences = { query:'', unit_id:'', priority:'balanced', examCount:4, sourceKind:'catalog' };
export const sourceKinds = [['catalog','招生目录'],['charter','招生章程'],['score','复试分数线'],['retest','复试办法'],['transfer','调剂办法'],['fees','学费与学制'],['enrollment','招生计划'],['change','科目调整']];
export const sourceKindLabel = value => (sourceKinds.find(([key])=>key===value)||sourceKinds[0])[1];
export function sourceResearchSeed(unit,kind='catalog',year=null) {
  if(typeof unit?.name!=='string'||!unit.name.trim())return '';
  const selected=sourceKinds.some(([value])=>value===kind)?kind:'catalog';
  const annual=Number.isInteger(year)&&year>=2000&&year<=2100?`${year}年`:'';
  return `${unit.name.trim()} ${annual}${sourceKindLabel(selected)}${selected==='catalog'?' 考试科目':''}`;
}
const record = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
const text = value => typeof value === 'string' ? value : '';
export const subjectIdentity = subject => [subject.scope || '',subject.code || '',subject.exam_unit || ''].join('|');
export function safeHttpUrl(value) { try { const url=new URL(value); return ['http:','https:'].includes(url.protocol) ? url.href : ''; } catch { return ''; } }

export function createApi(fetcher = (...args) => fetch(...args)) {
  return async (path, {body,signal} = {}) => {
    let response;
    try { response=await fetcher(path,{method:body === undefined ? 'GET':'POST',credentials:'same-origin',headers:body === undefined ? {}:{'Content-Type':'application/json'},...(body === undefined ? {}:{body:JSON.stringify(body)}),signal}); }
    catch(error) { if(error.name==='AbortError') throw error; const failure=new Error('暂时无法连接资料服务，请稍后重试。'); failure.code='network_error'; throw failure; }
    let envelope;
    try { envelope=await response.json(); } catch { const error=new Error('资料服务返回了无法识别的内容。'); error.code='invalid_response'; throw error; }
    if(!envelope || typeof envelope!=='object' || Array.isArray(envelope)) { const error=new Error('资料服务返回了无法识别的内容。'); error.code='invalid_response'; throw error; }
    if(!response.ok || envelope.ok !== true) { const error=new Error(envelope.error?.message || '请求未完成，请稍后重试。'); error.code=envelope.error?.code || 'request_failed'; throw error; }
    return envelope.data;
  };
}
export const api = createApi();

export function annualUnitOptionLabel(unit) {
  return unit.name+(unit.annual_status==='not_listed'?`（${unit.annual_year||'当年'}未列独立招生单位）`:Number(unit.program_count)>0?'':'（目录待入库）');
}
export function annualUnitCount(bootstrap) {
  const total=bootstrap?.coverage?.annual_official_total??bootstrap?.coverage?.annual_listed_units??bootstrap?.meta?.annual_scope?.official_total;
  if(Number.isInteger(total)&&total>=0)return total;
  const units=bootstrap?.units||[],confirmed=units.filter(unit=>unit.annual_status==='confirmed').length;
  return confirmed?confirmed:null;
}

export function catalogPageWindow(items,total,page=1,size=6) {
  const count=Number.isInteger(total)&&total>=0?total:0,totalPages=Math.max(1,Math.ceil(count/size));
  return {items:Array.isArray(items)?items:[],total:count,totalPages,page:Number.isInteger(page)&&page>0?Math.min(page,totalPages):1};
}
export const declaredExamUnits=subject=>Array.isArray(subject?.exam_units)?subject.exam_units.filter(value=>Number.isInteger(value)&&value>=1&&value<=4):Number.isInteger(subject?.exam_unit)?[subject.exam_unit]:[];
export function catalogAliasMap(units=[]) {
  const map=new Map();
  for(const unit of Array.isArray(units)?units:[]) {
    if(!unit||typeof unit.name!=='string'||!unit.name.trim())continue;
    for(const alias of Array.isArray(unit.aliases)?unit.aliases:[]) {
      const key=String(alias||'').trim().toLowerCase();
      if(key)map.set(key,unit.name);
    }
  }
  return map;
}
export function expandCatalogQuery(query,aliases=new Map()) {
  return String(query||'').trim().split(/\s+/).filter(Boolean).map(token=>aliases.get(token.toLowerCase())||token).join(' ');
}
export function matchesCatalogQuery(program,query='',aliases=new Map()) {
  const needles=expandCatalogQuery(query,aliases).toLowerCase().split(/\s+/).filter(Boolean);if(!needles.length)return true;
  // Keep fields and literal-or-synonym terms aligned with CatalogIndex search.
  const synonyms={'专硕':'专业学位','学硕':'学术学位','非全':'非全日制'};
  const haystack=['unit_name','department','code','name','direction','province','campus','study_mode','degree_type'].map(field=>String(program?.[field]||'')).join(' ').toLowerCase()+' '+(Array.isArray(program?.subjects)?program.subjects:[]).map(subject=>[subject?.code,subject?.name].join(' ')).join(' ').toLowerCase();
  return needles.every(needle=>haystack.includes(needle)||(Object.hasOwn(synonyms,needle)&&haystack.includes(synonyms[needle])));
}
// Pick a school only when the query clearly names one. An exact token wins over
// a name prefix; within the same kind the longest name wins; a tie leaves the
// selection alone rather than guessing.
export function inferCatalogUnit(query,units=[]) {
  const tokens=String(query||'').trim().split(/\s+/).filter(Boolean);
  if(!tokens.length)return '';
  const flat=value=>String(value||'').replace(/\s+/g,'').toLowerCase(),needle=flat(query),exact=[],prefix=[];
  for(const unit of Array.isArray(units)?units:[]) {
    if(!unit||typeof unit.name!=='string'||!unit.id||unit.annual_status==='not_listed')continue;
    for(const name of [unit.name,...(Array.isArray(unit.aliases)?unit.aliases:[])]) {
      const key=flat(name);
      if(!key)continue;
      // An exact token outranks a prefix: `中国人民大学 计算机` must not pick
      // `中国人民大学苏州校区` merely because its name is longer.
      if(tokens.some(token=>flat(token)===key))exact.push({id:unit.id,length:key.length});
      else if(needle.startsWith(key))prefix.push({id:unit.id,length:key.length});
    }
  }
  const matched=exact.length?exact:prefix;
  if(!matched.length)return '';
  const longest=Math.max(...matched.map(item=>item.length)),best=[...new Set(matched.filter(item=>item.length===longest).map(item=>item.id))];
  return best.length===1?best[0]:'';
}

export function createCatalogClient(request) {
  let revision='',raw=new Map(),bundles=new Map(),pending=new Map();
  const failure=(code,message)=>Object.assign(new Error(message),{code});
  const accept=(data,expected=revision)=>{if(!data||typeof data!=='object')throw failure('invalid_response','资料服务返回内容不完整。');if(expected&&data.revision!==expected)throw failure('stale_revision','已发布目录已更新，请刷新资料后重试。');return data;};
  const remember=programs=>{for(const program of programs||[])if(program&&typeof program.id==='string')raw.set(program.id,program);};
  const query=(values)=>{const params=new URLSearchParams();for(const [key,value] of Object.entries({...values,...(revision?{revision}:{})}))if(value!==undefined&&value!==null&&value!=='')params.set(key,String(value));return params;};
  return {
    revision:()=>revision,programs:()=>[...raw.values()],bundle:id=>bundles.get(id)||null,remember,
    async bootstrap(){const data=await request('/api/catalog-bootstrap');const next=data?.revision||data?.service?.data_revision;if(typeof next!=='string'||!next)throw failure('invalid_response','目录尚无可读取的发布版本。');if(next!==revision){raw=new Map();bundles=new Map();pending=new Map();}revision=next;return data;},
    async page({unit_id,year,q='',page=1,signal}={}){const expected=revision;const data=accept(await request('/api/catalog-programs?'+query({unit_id,year,q,offset:((Number.isInteger(page)&&page>0?page:1)-1)*6,limit:6}),{signal}),expected);if(expected!==revision)throw failure('stale_revision','目录版本已变化，请重新读取。');remember(data.programs);return data;},
    async program(id){
      if(typeof id!=='string'||!id||id.length>512)throw failure('invalid_query','专业身份不正确。');
      if(bundles.has(id))return bundles.get(id);const expected=revision,key=expected+'|'+id;
      if(pending.has(key))return pending.get(key);
      const promise=(async()=>{const data=accept(await request('/api/catalog-program?'+query({program_id:id})),expected);if(expected!==revision)throw failure('stale_revision','目录版本已变化，请重新读取。');if(data.program?.id!==id)throw failure('invalid_response','返回的专业身份与请求不一致。');remember([data.program]);bundles.set(id,data);return data;})().finally(()=>pending.delete(key));
      pending.set(key,promise);return promise;
    },
    async many(ids){const unique=[...new Set(ids||[])],loaded=[],failed=[];for(let offset=0;offset<unique.length;offset+=3){const part=unique.slice(offset,offset+3),results=await Promise.allSettled(part.map(id=>this.program(id)));results.forEach((result,index)=>result.status==='fulfilled'?loaded.push(result.value):failed.push({id:part[index],error:result.reason}));}return {loaded,failed};},
    async subjects({unit_id,year,q='',signal}={}){
      const expected=revision,abort=()=>{if(signal?.aborted)throw Object.assign(new Error('科目检索已取消'),{name:'AbortError'});};
      const read=async offset=>{abort();return accept(await request('/api/catalog-subjects?'+query({unit_id,year,q,offset,limit:200}),{signal}),expected);};
      const first=await read(0),subjects=[...(first.subjects||[])];
      if(!Number.isInteger(first.total)||first.total<subjects.length||first.total>1000000)throw failure('invalid_response','科目目录分页统计不完整。');
      for(let offset=200;offset<first.total;offset+=600){const starts=[offset,offset+200,offset+400].filter(value=>value<first.total),parts=await Promise.all(starts.map(read));for(const part of parts){if(!Array.isArray(part.subjects)||!part.subjects.length)throw failure('invalid_response','科目目录有页面未能读取完整，请重试。');subjects.push(...part.subjects);}}
      abort();if(expected!==revision||subjects.length!==first.total)throw failure('stale_revision','科目目录版本或总数已变化，请重新读取。');return {...first,subjects};
    }
  };
}

export function matchingStatistics(raw, statistics = []) {
  const normalize=value=>String(value || '').replace(/\s+/g,'').replace(/^\d+(?=[\u4e00-\u9fff])/,'');
  if(!raw.unit_id || !normalize(raw.department) || !['全日制','非全日制'].includes(raw.study_mode) || (!raw.code && !normalize(raw.name))) return [];
  return statistics.filter(item=>item.unit_id===raw.unit_id && normalize(item.department)===normalize(raw.department) && item.study_mode===raw.study_mode && (item.program_code ? item.program_code===raw.code : normalize(item.program_name)===normalize(raw.name)) && (!item.direction || String(item.direction).replace(/\s+/g,'')===String(raw.direction||'').replace(/\s+/g,'')));
}
export function matchingRules(raw, rules = []) {
  const normalize=value=>String(value || '').replace(/\s+/g,'').replace(/^\d+(?=[\u4e00-\u9fff])/,'');
  if(!raw.unit_id || !normalize(raw.department) || (!raw.code && !normalize(raw.name))) return [];
  return rules.filter(rule=>rule.unit_id===raw.unit_id && normalize(rule.department)===normalize(raw.department) && ((raw.code && (rule.program_code===raw.code || rule.program_codes?.includes(raw.code))) || (normalize(raw.name) && normalize(rule.program_name)===normalize(raw.name))));
}
export function historicalFacts(profile, fields, program) {
  const initial={education:profile.education || 'unknown',background:text(profile.background),subjects:Array.isArray(profile.subjects)?profile.subjects.map(subject=>({...subject})):[],...(['全日制','非全日制'].includes(fields.first_choice_study_mode)?{study_mode:fields.first_choice_study_mode}:{}),...(Number.isFinite(profile.work_years)?{work_years:profile.work_years}:{}),...(program.code?{program_code:program.code}:{})};
  const facts=[['total',500],['politics',100],['foreign_language',100],['business1',300],['business2',300]].reduce((result,[name,max])=>{
    if(fields[name]===undefined || fields[name]==='')return result;
    const value=Number(fields[name]);
    if(!Number.isFinite(value)||value<0||value>max)throw new Error('请按原始成绩填写有效分数。');
    return {...result,[name]:value};
  },initial);
  if(fields.first_choice_program_code) {
    if(!/^\d{6}$/.test(fields.first_choice_program_code))throw new Error('一志愿专业代码应为六位数字。');
    return {...facts,first_choice_program_code:fields.first_choice_program_code};
  }
  return facts;
}
export function scoreLineApplicability(raw,line) {
  const normalize=value=>String(value||'').replace(/\s+/g,'').replace(/^\d+(?=[\u4e00-\u9fff])/,'');
  if(line.unit_id!==raw.unit_id || (line.program_code ? line.program_code!==raw.code : !['university','national'].includes(line.kind))) return 'excluded';
  if(line.discipline_code && !String(raw.code||'').startsWith(String(line.discipline_code))) return 'excluded';
  if(Array.isArray(line.code_prefixes) && !line.code_prefixes.some(prefix=>typeof prefix==='string' && prefix && String(raw.code||'').startsWith(prefix))) return 'excluded';
  if(line.department && normalize(line.department)!==normalize(raw.department)) return 'excluded';
  if(line.direction && String(line.direction).replace(/\s+/g,'')!==String(raw.direction||'').replace(/\s+/g,'')) return 'excluded';
  let unknown=false;
  for(const field of ['study_mode','degree_type','plan_type']) {
    if(!line[field]) continue;
    if(!raw[field]) unknown=true;
    else if(line[field]!==raw[field]) return 'excluded';
  }
  return unknown?'unknown':'matched';
}
export function adaptProgram(raw, lines = [], evaluation = null, statistics = [], lastCompletedAdmissionYear = null) {
  const tuition=record(raw.tuition);
  const evidence=Array.isArray(raw.evidence) ? raw.evidence : raw.evidence ? [raw.evidence] : [];
  const historyWindow=Number.isInteger(lastCompletedAdmissionYear)?[lastCompletedAdmissionYear,lastCompletedAdmissionYear-1,lastCompletedAdmissionYear-2]:[];
  const scopedLines=lines.filter(line=>historyWindow.includes(line.year)&&scoreLineApplicability(raw,line)!=='excluded');
  const verified=row=>['verified','derived'].includes(row.evidence_status);
  const unverifiedScoreLines=scopedLines.filter(line=>!verified(line));
  const scoreLines=scopedLines.filter(line=>verified(line)&&scoreLineApplicability(raw,line)==='matched');
  const referenceScoreLines=scopedLines.filter(line=>verified(line)&&scoreLineApplicability(raw,line)==='unknown').map(line=>({...line,applicability:'unknown',applicability_note:'该材料的学位类别、学习形式或计划类型与本方向的适用关系仍待核验；仅作学校原始分数线参考。'}));
  const samples=matchingStatistics(raw,statistics).filter(item=>historyWindow.includes(item.year));
  return {
    id:raw.id,name:raw.unit_name || '',abbr:(raw.unit_name || '校').slice(0,1),unit_id:raw.unit_id,year:raw.year,
    department:String(raw.department||'').split(/(?:咨询电话|联系电话|联系人|学院网站)\s*[:：]/)[0].trim()||'院系资料未收录',major:[raw.name,raw.direction].filter(Boolean).join(' · '),code:raw.code || '',
    city:raw.campus || raw.province || '校区待收录',region:raw.province || '',degree:raw.degree_type || '学位类型待核验',mode:raw.study_mode || '学习形式待核验',
    subjectObjects:Array.isArray(raw.subjects) ? raw.subjects : [],subjects:(raw.subjects || []).map(subject => [subject.code,subject.name].filter(Boolean).join(' ')),
    annualFee:tuition.period === 'year' && Number.isFinite(tuition.amount) ? tuition.amount : null,
    totalFee:Number.isFinite(tuition.total) ? tuition.total : tuition.period === 'total' && Number.isFinite(tuition.amount) ? tuition.amount : null,
    duration:Number.isFinite(raw.duration_years) ? raw.duration_years : null,seats:Number.isFinite(raw.unified_exam_seats) ? raw.unified_exam_seats : raw.planned_enrollment?.scope==='direction'&&Number.isFinite(raw.planned_enrollment.count)?raw.planned_enrollment.count:null,
    plannedEnrollment:Number.isFinite(raw.planned_enrollment?.count)?{...raw.planned_enrollment}:null,
    history:samples.map(item=>({year:item.year,min:verified(item)?item.initial?.min??null:null,max:verified(item)?item.initial?.max??null:null,median:verified(item)?item.initial?.median??null:null,count:verified(item)?item.sample_count??null:null,evidenceStatus:verified(item)?item.evidence_status:'needs_review',evidenceNote:item.evidence_note||'保存原文与统计依据尚未通过核对。',statistic:item})).toSorted((a,b)=>b.year-a.year),historyWindow,scoreLines,referenceScoreLines,unverifiedScoreLines,evidence,notes:Array.isArray(raw.notes) ? raw.notes : raw.notes ? [raw.notes] : [],
    status:evaluation?.status || 'not_assessed',evaluation,raw,
  };
}

export function restoreClientState(cached) {
  const source=record(cached), input=record(source.profile), preferences=record(source.preferences);
  const profile={...defaultProfile,preferences:{...defaultProfile.preferences},subjects:[]};
  const priority=['balanced','subjects','cost'].includes(input.ranking_mode)?input.ranking_mode:['balanced','subjects','cost'].includes(preferences.priority)?preferences.priority:'balanced';
  profile.ranking_mode=priority;
  if(['unknown','bachelor','master','associate'].includes(input.education)) profile.education=input.education;
  profile.background=text(input.background).slice(0,120);
  if(['全日制','非全日制'].includes(input.study_mode)) profile.study_mode=input.study_mode;
  if(Number.isFinite(input.work_years) && input.work_years>=0 && input.work_years<=80) profile.work_years=input.work_years;
  if(Number.isFinite(input.max_annual_tuition) && input.max_annual_tuition>=0 && input.max_annual_tuition<=1000000) profile.max_annual_tuition=input.max_annual_tuition;
  if(Number.isInteger(input.max_subject_changes) && input.max_subject_changes>=0 && input.max_subject_changes<=4) profile.max_subject_changes=input.max_subject_changes;
  if(Array.isArray(input.provinces)) profile.provinces=input.provinces.filter(value=>typeof value==='string').slice(0,35);
  if(Array.isArray(input.subjects)) profile.subjects=input.subjects.filter(subject=>subject && typeof subject.code==='string' && typeof subject.scope==='string' && subject.scope && Number.isInteger(subject.exam_unit) && subject.exam_unit>=1 && subject.exam_unit<=4).slice(0,4).map(subject=>({code:subject.code.slice(0,20),name:text(subject.name).slice(0,120),scope:subject.scope.slice(0,120),exam_unit:subject.exam_unit}));
  for(const field of ['cost','subjects','location']) if(Number.isFinite(input.preferences?.[field]) && input.preferences[field]>=0 && input.preferences[field]<=5) profile.preferences={...profile.preferences,[field]:input.preferences[field]};
  return {profile,preferences:{query:text(preferences.query).slice(0,80),unit_id:text(preferences.unit_id).slice(0,120),priority,examCount:[2,3,4].includes(preferences.examCount)?preferences.examCount:4,sourceKind:sourceKinds.some(([key])=>key===preferences.sourceKind)?preferences.sourceKind:'catalog'},saved:Array.isArray(source.saved)?[...new Set(source.saved.filter(value=>typeof value==='string').map(value=>value.slice(0,300)))].slice(0,100):[]};
}
