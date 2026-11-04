import {esc} from './workbench-model.mjs';

const array=value=>Array.isArray(value)?value:[];
const integer=value=>Number.isInteger(value)&&value>=0?value:null;
const amount=value=>integer(value)===null?'待统计':String(value);

export function vectorStateLabel(value={}){
 if(value.building===true)return '正在建立新索引';
 if(value.status==='failed')return value.active_space_id?'新索引未完成，保留上一代':'语义索引未完成';
 if(value.status==='ready'&&value.active_space_id)return value.dirty?'已有索引，资料有更新':'已有可用语义索引';
 return '尚未建立语义索引';
}

function spaceLabel(space){
 return `${space.model||'模型身份待记录'}${integer(space.dimension)!==null?` · ${space.dimension} 维`:''}`;
}

export function renderVectorSettings(state={}){
 const value=state.vectors||{},active=array(value.spaces).find(space=>space.id===value.active_space_id),working=array(value.spaces).find(space=>space.state==='staging'),job=value.jobs||{};
 const error=state.vectorError,ready=value.status==='ready'&&!!value.active_space_id,building=value.building===true;
 const configured=state.saved?.status==='connected',canBuild=configured&&!building&&!state.vectorBusy;
 const count=working||active;
 return `<details class="ye-vector-settings" ${state.vectorOpen?'open':''}><summary><span>知识语义索引</span><small>${error?'状态暂时无法读取':esc(vectorStateLabel(value))}</small></summary><div class="ye-vector-content"><p class="ye-vector-purpose">原文结构索引继续保留。语义索引用于补充不同说法的条款，是否适用仍以来源、年份和原文为准。</p>${state.vectorLoading?'<p class="ye-muted" role="status">正在读取索引状态…</p>':''}${error?`<div class="ye-vector-error" role="alert">${esc(error)}<button type="button" data-embedding-action="vectors-retry">重新读取状态</button></div>`:`${active?`<div class="ye-serving-profile"><span>当前索引</span><strong>${esc(spaceLabel(active))}</strong></div>`:''}${working?`<div class="ye-serving-profile"><span>正在构建</span><strong>${esc(spaceLabel(working))}</strong></div>`:''}${count?`<p class="ye-index-progress">已写入 ${amount(count.completed_count)} / ${amount(count.expected_count)} 段</p>`:''}<div class="ye-job-counts"><span>待处理 <strong>${amount(job.pending)}</strong></span><span>已完成 <strong>${amount(job.succeeded)}</strong></span><span>失败 <strong>${amount(job.failed)}</strong></span></div>${integer(job.uncertain)>0?'<p class="ye-muted">有调用状态待确认，不会在打开页面时自动重复执行。</p>':''}${value.error_code?`<p class="ye-vector-error">索引任务未完成 · ${esc(value.error_code)}</p>`:''}`}${!configured?'<p class="ye-muted">先测试并保存上方的向量模型连接，再建立语义索引。关键词检索始终保留。</p>':!building?`<div class="ye-vector-actions"><button type="button" data-embedding-action="vectors-build" ${canBuild?'':'disabled'}>${state.vectorBusy?'正在提交…':value.status==='failed'?'重试索引任务':ready?'同步当前资料':'建立语义索引'} →</button></div><p class="ye-muted">只处理当前通过准入且仍有效的原文片段，已缓存内容不会重复生成向量。${state.provider?.requires_key!==false?'云端模型可能消耗服务商额度。':'本机模型在研究服务所在设备上运行。'}</p>`:'<p class="ye-muted" role="status">构建在后台进行；离开页面不会清除已完成的片段。</p>'}${state.vectorConfirm?'<section class="ye-build-confirm"><strong>开始建立语义索引？</strong><p>将采用已保存的向量模型处理当前合格资料。实际数量以任务统计为准，不会替换尚未验证的新索引。</p><div><button type="button" data-embedding-action="vectors-confirm">开始构建</button><button type="button" data-embedding-action="vectors-dismiss">暂不构建</button></div></section>':''}${state.vectorNotice?`<p class="ye-vector-notice" role="status">${esc(state.vectorNotice)}</p>`:''}<details class="ye-storage-details"><summary>索引与存储细节</summary><p>存储后端：${esc(value.backend||'尚未读取')}。此处显示索引任务记录，不等于刚刚完成存储连接测试。</p><p>更换模型会建立独立索引代次；检查通过后再切换。构建失败时保留原有可用资料与索引。</p></details></div></details>`;
}

export function createVectorSettings({api,onChange,getProfileId}={}){
 let state={vectors:{},vectorOpen:false},disposed=false,generation=0,controller,timer;
 const update=patch=>{state={...state,...patch};if(!disposed)onChange?.(state);};
 async function load({quiet=false}={}){
  if(disposed)return;controller?.abort();controller=new AbortController();const ticket=++generation;
  if(!quiet)update({vectorLoading:true,vectorError:''});
  try{const vectors=await api('/api/knowledge/vectors',{signal:controller.signal});if(disposed||ticket!==generation)return;if(!vectors||typeof vectors!=='object'||Array.isArray(vectors))throw new Error('索引状态返回格式需要重新检查。');update({vectors,vectorLoading:false,vectorError:''});clearTimeout(timer);if(vectors.building===true)timer=setTimeout(()=>load({quiet:true}),1800);}
  catch(error){if(disposed||ticket!==generation||error.name==='AbortError')return;update({vectorLoading:false,vectorError:error.message||'索引状态暂时无法读取。'});}
 }
 async function build(){
  if(disposed||state.vectorBusy)return;controller?.abort();controller=new AbortController();const ticket=++generation;
  clearTimeout(timer);update({vectorBusy:true,vectorConfirm:false,vectorError:'',vectorNotice:''});
  try{const profile=getProfileId?.(),result=await api('/api/knowledge/vectors/build',{body:profile?{profile_id:profile}:{},signal:controller.signal});if(disposed||ticket!==generation)return;update({vectorBusy:false,vectors:result,vectorNotice:result.started===true?'已提交索引构建，正在读取实际进度。':result.building===true?'已有构建在进行，正在读取进度。':'任务没有启动，请核对连接与索引状态。'});load({quiet:true});}
  catch(error){if(disposed||ticket!==generation||error.name==='AbortError')return;update({vectorBusy:false,vectorError:error.message||'索引任务尚未启动，请稍后重试。'});}
 }
 return {load,open(value){if(state.vectorOpen!==value)update({vectorOpen:value});},click(action){if(action==='vectors-retry'){load();return true;}if(action==='vectors-build'){update({vectorOpen:true,vectorConfirm:true});return true;}if(action==='vectors-dismiss'){update({vectorConfirm:false});return true;}if(action==='vectors-confirm'){build();return true;}return false;},snapshot:()=>state,dispose(){disposed=true;generation++;clearTimeout(timer);controller?.abort();}};
}
