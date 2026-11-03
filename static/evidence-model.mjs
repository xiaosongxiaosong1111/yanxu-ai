import {safeHttpUrl} from './data.mjs';

export const sourceKinds={catalog:'招生目录',catalog_context:'目录查询依据',charter:'招生章程',fees:'收费标准',duration:'学制',score:'复试分数线',retest:'复试办法',transfer:'调剂办法',change:'科目调整',notice:'招生通知',admission_results:'录取公示',publication:'原文发布页面',enrollment:'招生计划'};
export const claimStatuses={verified:'原文已核对',missing:'依据未收录',needs_review:'仍需核验',conflict:'存在冲突',derived:'按公示数据计算'};
export const verificationLabels={exact_quote:'原文已定位',structured_fields:'原文结构化字段已核对',derived_statistics:'原始公示与统计口径',snapshot_verified:'保存原文已核对',published_snapshot:'上次发布留档',reference_link:'原文引用关系已核对',out_of_scope:'不适用于当前结论',unavailable:'留档暂不可核对',invalid_source:'来源校验未通过'};
export const SOURCE_PAGE_SIZE=12;
const string=value=>typeof value==='string'?value:'';
const number=value=>Number.isFinite(value)?Number(value).toLocaleString('zh-CN'):'未知';

export function officialEvidenceUrl(value){
 const safe=safeHttpUrl(value);if(!safe)return '';
 const parsed=new URL(safe);return parsed.username||parsed.password?'':safe;
}

export function originalSourceUrl(source={}){
 return officialEvidenceUrl(Object.hasOwn(source,'detail_url')?source.detail_url:source.url);
}

export function isStructuredEvidence(item){
 return item?.kind==='structured_fields'&&item.verification==='structured_fields'
  &&typeof item.field_path==='string'&&Boolean(item.field_path.trim())
  &&item.structured_fields&&typeof item.structured_fields==='object'&&!Array.isArray(item.structured_fields)
  &&Object.keys(item.structured_fields).length>0;
}

const ordered=value=>Array.isArray(value)?value.map(ordered):value&&typeof value==='object'
 ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,ordered(value[key])])):value;
const referenceIdentity=item=>JSON.stringify([item.document_id,item.page,item.quote,item.relation,
 item.verification,item.field_path,ordered(item.structured_fields)]);

export function normalizeSourceFilters(value={}){
 return {q:string(value?.q).trim().slice(0,120),unit_id:string(value?.unit_id).trim().slice(0,120),
  year:/^20\d{2}$/.test(String(value?.year))?String(value.year):'',kind:typeof value?.kind==='string'&&Object.hasOwn(sourceKinds,value.kind)?value.kind:''};
}

export function buildSourceQuery(value,{offset=0,limit=SOURCE_PAGE_SIZE,revision=''}={}){
 const filters=normalizeSourceFilters(value),query=new URLSearchParams();
 for(const [key,part] of Object.entries(filters))if(part)query.set(key,part);
 query.set('offset',Number.isInteger(offset)&&offset>=0&&offset<=10000000?offset:0);
 query.set('limit',Number.isInteger(limit)&&limit>=1&&limit<=200?limit:SOURCE_PAGE_SIZE);
 if(typeof revision==='string'&&revision&&revision.length<=128)query.set('revision',revision);
 return '/api/catalog-sources?'+query;
}

export function groupEvidence(evidence){
 const groups=new Map();
 for(const item of Array.isArray(evidence)?evidence:[]){
  if(!item||typeof item!=='object')continue;
  const key=String(item.source_group||item.document_id||officialEvidenceUrl(item.url)||item.id||'unidentified');
  const group=groups.get(key)||{id:key,primary:item,items:[],urls:[]};
  const identity=referenceIdentity(item);
  if(!group.items.some(quote=>referenceIdentity(quote)===identity))group.items=[...group.items,item];
  const url=originalSourceUrl(item);if(url&&!group.urls.includes(url))group.urls=[...group.urls,url];
  groups.set(key,group);
 }
 return [...groups.values()];
}

export function scopeText(scope={}){
 const admission={first_choice:'一志愿公示样本',transfer:'调剂公示样本',not_specified:'一志愿或调剂未单列'}[scope?.admission_source];
 const plan={unmarked:'计划类别未单列',regular:'普通计划',special:'专项计划',ordinary_base_line:'学校公布的基本线'}[scope?.plan_type]||scope?.plan_type;
 const degree={academic:'学术学位',professional:'专业学位'}[scope?.degree_type]||scope?.degree_type;
 const prefixes=Array.isArray(scope?.code_prefixes)?scope.code_prefixes.filter(value=>typeof value==='string').map(value=>value+'*').join('、'):'';
 const fields=[Number.isInteger(scope?.year)?scope.year+' 年':null,scope?.unit_name,scope?.department,scope?.program_name||scope?.name,
  scope?.program_code||scope?.code,scope?.direction,scope?.study_mode,degree,scope?.source_scope_name,scope?.discipline_name,
  scope?.discipline_code?'学科代码 '+scope.discipline_code:null,prefixes?'专业代码范围 '+prefixes:null,scope?.cohort,plan,admission,scope?.scope_note];
 return [...new Set(fields.filter(value=>typeof value==='string'&&value.trim()))].join(' · ')||'适用范围仍需核验';
}

export function formatClaimValue(claim={}){
 const value=claim.value,field=claim.field||claim.id;
 if(value===null||value===undefined)return '尚未收录';
 if(field==='tuition'&&typeof value==='object')return Number.isFinite(value.amount)?`${number(value.amount)} 元 / ${value.period==='year'?'年':value.period==='total'?'全程':'计费周期待核验'}`:'费用尚待核验';
 if(['duration','duration_years'].includes(field)&&Number.isFinite(value))return number(value)+' 年';
 if(field==='subjects'&&Array.isArray(value))return value.map(subject=>[subject.code,subject.name].filter(Boolean).join(' ')).join(' / ')||'科目待核验';
 if(field==='subjects'&&Array.isArray(value?.exam_combinations))return `原文保留 ${value.exam_combinations.length} 组科目组合 · 换科计算待核`;
 if(['enrollment','planned_enrollment'].includes(field)&&typeof value==='object')return Number.isFinite(value.count)?number(value.count)+' 人'+(value.scope==='direction'?' · 方向计划':value.scope?' · 共享计划口径':' · 范围待核验'):'计划人数待核验';
 if(typeof value==='string')return value;
 if(typeof value==='number')return number(value);
 if(typeof value==='boolean')return value?'已记录相应标记，仍需核对适用范围':'资格完整依据尚待核验';
 if(Array.isArray(value))return value.length?`共 ${value.length} 项，见对应原文`:'尚无可核对条目';
 if(typeof value==='object'){
  if(value.initial){const initial=value.initial;return `初试 ${number(initial.min)}—${number(initial.max)} 分 · ${number(value.sample_count)} 人公示样本`;}
  if(Number.isFinite(value.total))return number(value.total)+' 分';
  return [value.code,value.name,value.direction,value.study_mode].filter(part=>typeof part==='string').join(' · ')||'请结合原文与适用范围核对';
 }
 return '尚待核验';
}

export function programSourceLabel(program={}){
 const raw=program.raw||program;
 return [raw.unit_name||program.name,raw.department,raw.name||program.major,raw.direction,raw.study_mode].filter(value=>typeof value==='string'&&value).join(' · ');
}

export function sourcePage(sources,page=1,size=12){
 if(sources&&typeof sources==='object'&&!Array.isArray(sources)&&Number.isInteger(sources.offset)&&Number.isInteger(sources.limit)&&sources.limit>0&&Number.isInteger(sources.total)){
  return {items:Array.isArray(sources.sources)?sources.sources:[],page:Math.floor(sources.offset/sources.limit)+1,
   pages:Math.max(1,Math.ceil(sources.total/sources.limit)),total:sources.total};
 }
 if(sources&&typeof sources==='object'&&!Array.isArray(sources))sources=sources.sources;
 const values=Array.isArray(sources)?sources:[],pages=Math.max(1,Math.ceil(values.length/size)),current=Math.min(pages,Math.max(1,Number.isInteger(page)?page:1));
 return {items:values.slice((current-1)*size,current*size),page:current,pages,total:values.length};
}

export function validateSourcePage(data,offset,size=SOURCE_PAGE_SIZE){
 if(!data||!Array.isArray(data.sources)||!Number.isSafeInteger(data.total)||data.total<0||data.offset!==offset||data.limit!==size
  ||typeof data.revision!=='string'||!data.revision||data.revision.length>128||data.verification_mode!=='published_snapshot'
  ||data.sources.length!==Math.min(size,Math.max(0,data.total-offset))||data.sources.some(source=>!source||typeof source!=='object'||Array.isArray(source))){
  throw new Error('来源分页响应不完整，请重新读取资料索引。');
 }
 return {...data,sources:data.sources.map(source=>({...source,verification:'published_snapshot'}))};
}
