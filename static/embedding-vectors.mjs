import {esc} from './workbench-model.mjs';

const array=value=>Array.isArray(value)?value:[];
const object=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
const integer=value=>Number.isInteger(value)&&value>=0?value:null;
const amount=value=>integer(value)===null?'待统计':String(value);
const text=value=>typeof value==='string'?value:'';

function activeSpace(value){return array(value.spaces).find(space=>space.id===value.active_space_id)||null;}
function stagingSpace(value){return array(value.spaces).find(space=>space.state==='staging')||null;}
function spaceLabel(space){return space?`${space.model||'模型身份待记录'}${integer(space.dimension)!==null?` · ${space.dimension} 维`:''}`:'尚未记录';}
function profileDiffers(space,saved){return !!space&&!!saved?.profile_id&&space.profile_id!==saved.profile_id;}

export function vectorStatus(value={},saved={}){
 const active=activeSpace(value),staging=stagingSpace(value),building=value.building===true;
 const connected=saved?.status==='connected';
 const outdated=!!active&&connected&&(active.model!==saved.model||integer(saved.dimension)!==null&&active.dimension!==saved.dimension||profileDiffers(active,saved)||value.dirty===true);
 return {active,staging,building,connected,outdated,state:building?'building':outdated?'stale':active?'ready':value.status==='failed'?'failed':'not_ready'};
}

export function vectorStateLabel(value={},saved={}){
 const info=vectorStatus(value,saved);
 if(info.building){const job=object(value.jobs);if(integer(job.running)>0)return '正在建立新索引';if(integer(job.failed)>0)return '新索引未完成，保留上一代';return '正在准备新索引';}
 if(info.state==='stale')return '模型或资料已变化，待更新索引';
 if(!info.connected&&array(value.spaces).length)return '连接已移除，现有索引保留';
 if(info.state==='ready')return value.dirty?'已有索引，资料有更新':'已有可用语义索引';
 if(info.state==='failed')return info.active?'新索引未完成，保留上一代':'语义索引未完成';
 return '尚未建立语义索引';
}

function modelLabel(model){
 const state=text(model?.state);
 if(state==='ready')return `已校验 ${amount(model.files_verified)} / ${amount(model.total_files)} 个模型文件`;
 if(state==='preparing')return '正在准备本机模型，首次约 95 MB';
 if(state==='invalid')return '本机模型文件校验未通过';
 if(state==='failed')return `本机模型准备失败${model.error_code?` · ${model.error_code}`:''}`;
 return '本机模型尚未准备，首次约 95 MB';
}

function usageText(last){
 const usage=object(last),known=usage.usage_known!==false;
 const calls=integer(usage.provider_calls),hits=integer(usage.cache_hits),inputs=integer(usage.embedded_inputs);
 const tokens=integer(usage.input_tokens);
 const parts=[];
 if(calls!==null)parts.push(`实际调用 ${calls} 次`);
 if(hits!==null)parts.push(`缓存复用 ${hits} 个窗口`);
 if(inputs!==null)parts.push(`生成 ${inputs} 个输入向量`);
 parts.push(known&&tokens!==null?`输入 ${tokens} token`:'输入 token 未知');
 return parts.join(' · ');
}

function identityRow(title,space,{desired=false}={}){
 return `<div class="ye-serving-profile ${desired?'is-desired':''}"><span>${esc(title)}</span><strong>${esc(spaceLabel(space))}</strong>${space?.state?`<small>${esc(space.state==='staging'?'staging · 尚未切换服务':space.state==='ready'?'serving':'状态 '+space.state)}</small>`:''}</div>`;
}

function counts(space,current){
 if(!space)return `<p class="ye-index-progress">当前范围候选：${esc(amount(current?.sources))} 份资料 · ${esc(amount(current?.segments))} 个片段；窗口${integer(current?.windows)===null?'待构建后统计':` ${amount(current.windows)} 个`}</p>`;
 return `<p class="ye-index-progress">已登记 ${amount(space.sources)} 份资料 · ${amount(space.segments)} 个片段 · ${amount(space.windows)} 个窗口；完成 ${amount(space.completed_count)} / ${amount(space.expected_count)} 个窗口</p>`;
}

function modelPreparation(state,value){
 const model=object(value.embedding_model),local=state.provider?.id==='local_bge'||state.saved?.provider==='local_bge';
 if(!local)return '';
 const busy=state.vectorBusy==='prepare',preparing=model.state==='preparing',ready=model.state==='ready';
 return `<section class="ye-model-preparation"><div><span class="ye-quiet-label">本机中文向量模型</span><strong>${esc(modelLabel(model))}</strong><p>固定公开模型，在研究服务所在设备上运行；打开设置、读取状态和后台轮询都不会下载或推理。</p></div>${preparing?'<span class="ye-muted" role="status">准备中，请保持联网…</span>':ready?'':'<button type="button" data-embedding-action="vectors-prepare" '+(busy?'disabled':'')+'>'+(busy?'正在提交准备…':'准备本机模型')+'</button>'}</section>`;
}

export function renderVectorSettings(state={}){
 const value=object(state.vectors),saved=object(state.saved),info=vectorStatus(value,saved),model=object(value.embedding_model),job=object(value.jobs),last=object(value.last_build);
 const local=state.provider?.id==='local_bge'||saved.provider==='local_bge',modelHint=local&&model.state!=='ready'?'本机模型未准备':'';
 const error=state.vectorError,building=info.building,configured=info.connected,modelReady=!local||model.state==='ready',canBuild=configured&&modelReady&&!building&&!state.vectorBusy;
 const shown=info.staging||info.active,current=object(value.current);
 const desire=saved.status==='connected'?{model:saved.model,dimension:saved.dimension,state:'desired'}:null;
 const draft=state.draft,savedChanged=!!desire&&(text(draft?.model)!==text(desire.model)||text(draft?.provider)!==text(saved.provider));
 return `<details class="ye-vector-settings" ${state.vectorOpen?'open':''}><summary><span>知识语义索引</span><small>${error?'状态暂时无法读取':esc(modelHint||vectorStateLabel(value,saved))}</small></summary><div class="ye-vector-content"><p class="ye-vector-purpose">原文结构索引继续保留。语义索引只补充不同说法的条款，是否适用仍以来源、年份和原文为准。</p>${state.vectorLoading?'<p class="ye-muted" role="status">正在读取索引状态…</p>':''}${error?`<div class="ye-vector-error" role="alert">${esc(error)}<button type="button" data-embedding-action="vectors-retry">重新读取状态</button></div>`:`<div class="ye-vector-identity">${desire?identityRow('目标模型（desired）',desire,{desired:true}):'<div class="ye-serving-profile is-desired"><span>目标模型（desired）</span><strong>尚未保存向量连接</strong></div>'}${info.active?identityRow('当前服务索引（serving）',info.active):'<div class="ye-serving-profile"><span>当前服务索引（serving）</span><strong>尚未建立</strong></div>'}${info.staging&&info.staging.id!==info.active?.id?identityRow('正在建立（staging）',info.staging):''}${info.outdated?'<p class="ye-vector-warning">目标模型或资料范围已变化。更新成功前，继续使用当前服务索引；新模型失败不会把目标标签贴到旧索引上。</p>':''}${savedChanged?'<p class="ye-muted">上方草稿尚未保存，不会改变当前索引。</p>':''}</div>${modelPreparation(state,value)}${counts(shown,current)}<div class="ye-job-counts"><span>待处理 <strong>${amount(job.pending)}</strong></span><span>运行中 <strong>${amount(job.running)}</strong></span><span>已完成 <strong>${amount(job.succeeded)}</strong></span><span>失败 <strong>${amount(job.failed)}</strong></span><span>已取消 <strong>${amount(job.cancelled)}</strong></span>${integer(job.uncertain)>0?'<span>待确认 <strong>'+amount(job.uncertain)+'</strong></span>':''}</div>${last.provider_calls!==undefined?`<p class="ye-muted">上次构建：${esc(usageText(last))}${last.usage_known===false?'；部分调用用量未知，不推算费用':''}</p>`:''}${integer(job.uncertain)>0?'<p class="ye-muted">有调用状态待确认，不会在打开页面时自动重复执行或计费。</p>':''}${value.error_code?`<p class="ye-vector-error">索引任务未完成 · ${esc(value.error_code)}</p>`:''}`}${!configured&&!info.active?'<p class="ye-muted">先测试并保存上方的向量模型连接，再建立语义索引。关键词检索始终保留。</p>':!building?`<div class="ye-vector-actions"><button type="button" data-embedding-action="vectors-build" ${canBuild?'':'disabled'}>${state.vectorBusy==='build'?'正在提交…':info.outdated?'更新索引':info.active?'同步当前资料':'建立语义索引'} →</button></div><p class="ye-muted">只处理当前通过准入且仍有效的原文片段，已缓存内容不会重复生成向量。${state.provider?.requires_key!==false?'云端模型可能消耗服务商额度。':'本机模型在研究服务所在设备上运行。'}</p>`:`<div class="ye-vector-actions"><button type="button" data-embedding-action="vectors-cancel" ${state.vectorBusy?'disabled':''}>${state.vectorBusy==='cancel'?'正在请求停止…':'停止构建'}</button></div><p class="ye-muted" role="status">构建在后台进行；停止会在下一次调用前生效，已完成片段保留。离开页面不会清除已完成内容。</p>`}${state.vectorConfirm?'<section class="ye-build-confirm"><strong>开始建立语义索引？</strong><p>将采用已保存的向量模型处理当前合格资料。实际数量以任务统计为准，不会替换尚未验证的新索引。</p><div><button type="button" data-embedding-action="vectors-confirm">开始构建</button><button type="button" data-embedding-action="vectors-dismiss">暂不构建</button></div></section>':''}${state.vectorNotice?`<p class="ye-vector-notice" role="status">${esc(state.vectorNotice)}</p>`:''}<details class="ye-storage-details"><summary>索引与存储细节</summary><p>存储后端：${esc(text(value.backend)||'尚未读取')}。此处显示索引任务记录，不等于刚刚完成存储连接测试。</p><p>更换模型会建立独立索引代次；检查通过后再切换。构建失败时保留原有可用资料与索引。</p></details></div></details>`;
}

export function createVectorSettings({api,onChange,getProfileId,isLocal}={}){
 let state={vectors:{},vectorOpen:false},disposed=false,generation=0,controller,timer,closedByUser=false;
 const update=patch=>{state={...state,...patch};if(!disposed)onChange?.(state);};
 function schedule(ms){clearTimeout(timer);if(disposed)return;timer=setTimeout(()=>load({quiet:true}),ms);}
 async function load({quiet=false}={}){
  if(disposed)return;controller?.abort();controller=new AbortController();const ticket=++generation;
  clearTimeout(timer);if(!quiet)update({vectorLoading:true,vectorError:''});
  try{
   const vectors=await api('/api/knowledge/vectors',{signal:controller.signal});
   if(disposed||ticket!==generation)return;
   if(!vectors||typeof vectors!=='object'||Array.isArray(vectors))throw new Error('索引状态返回格式需要重新检查。');
   const hint=isLocal?.()&&object(vectors.embedding_model).state!=='ready'&&!closedByUser;
   update({vectors,vectorLoading:false,vectorError:'',...(hint?{vectorOpen:true}:{})});
   if(vectors.building===true)schedule(1800);
   else if(object(vectors.embedding_model).state==='preparing')schedule(1200);
  }catch(error){if(disposed||ticket!==generation||error.name==='AbortError')return;update({vectorLoading:false,vectorError:error.message||'索引状态暂时无法读取。'});}
 }
 async function build(){
  if(disposed||state.vectorBusy)return;controller?.abort();controller=new AbortController();const ticket=++generation;
  clearTimeout(timer);update({vectorBusy:'build',vectorConfirm:false,vectorError:'',vectorNotice:''});
  try{
   const profile=getProfileId?.();
   const result=await api('/api/knowledge/vectors/build',{body:profile?{profile_id:profile}:{},signal:controller.signal});
   if(disposed||ticket!==generation)return;
   const started=result?.started===true;
   update({vectorBusy:'',vectors:result&&typeof result==='object'?result:state.vectors,
    vectorNotice:started?'已提交索引构建，正在读取实际进度。':result?.building===true?'已有构建在进行，正在读取进度。':'任务没有启动，请核对连接与索引状态。'});
   load({quiet:true});
  }catch(error){if(disposed||ticket!==generation||error.name==='AbortError')return;update({vectorBusy:'',vectorError:error.message||'索引任务尚未启动，请稍后重试。'});}
 }
 async function cancel(){
  if(disposed||state.vectorBusy)return;controller?.abort();controller=new AbortController();const ticket=++generation;
  update({vectorBusy:'cancel',vectorError:'',vectorNotice:''});
  try{
   const result=await api('/api/knowledge/vectors/cancel',{body:{},signal:controller.signal});
   if(disposed||ticket!==generation)return;
   update({vectorBusy:'',vectorNotice:result?.started===true?'已请求服务端停止构建，等待当前调用结束。':'当前没有正在执行的构建。'});
   load({quiet:true});
  }catch(error){if(disposed||ticket!==generation||error.name==='AbortError')return;update({vectorBusy:'',vectorError:error.message||'暂时无法请求取消构建。'});}
 }
 async function prepare(){
  if(disposed||state.vectorBusy)return;controller?.abort();controller=new AbortController();const ticket=++generation;
  clearTimeout(timer);update({vectorBusy:'prepare',vectorError:'',vectorNotice:''});
  try{
   const result=await api('/api/knowledge/embedding/prepare',{body:{},signal:controller.signal});
   if(disposed||ticket!==generation)return;
   const model=result&&typeof result==='object'?result:{state:'preparing'};
   const vectors={...state.vectors,embedding_model:model};
   update({vectorBusy:'',vectors,vectorNotice:model.state==='ready'?'本机模型已就绪，校验通过。':model.started===true?'已开始准备本机模型，首次约 95 MB。':'模型准备已在进行，请等待状态更新。'});
   load({quiet:true});
  }catch(error){if(disposed||ticket!==generation||error.name==='AbortError')return;update({vectorBusy:'',vectorError:error.message||'本机模型暂时无法准备，请检查网络后重试。'});}
 }
 return {load,open(value){if(state.vectorOpen!==value){closedByUser=value===false;update({vectorOpen:value});}},click(action){
  if(action==='vectors-retry'){load();return true;}
  if(action==='vectors-build'){update({vectorOpen:true,vectorConfirm:true});return true;}
  if(action==='vectors-dismiss'){update({vectorConfirm:false});return true;}
  if(action==='vectors-confirm'){build();return true;}
  if(action==='vectors-cancel'){cancel();return true;}
  if(action==='vectors-prepare'){prepare();return true;}
  return false;
 },snapshot:()=>state,dispose(){disposed=true;generation++;clearTimeout(timer);controller?.abort();}};
}
