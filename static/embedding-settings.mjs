import {esc} from './workbench-model.mjs';
import {icon} from './icons.mjs';
import {preserveSettingsInput} from './settings-focus.mjs';
import {createVectorSettings,renderVectorSettings} from './embedding-vectors.mjs';

const object=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
const text=value=>typeof value==='string'?value:'';
const list=value=>Array.isArray(value)?value:[];
const dimension=value=>Number.isInteger(value)&&value>0?value:null;
const endpoint=value=>text(value).replace(/\/+$/,'');

export function normalizeEmbedding(value){
 const row=object(value);
 return {kind:'embedding',provider:text(row.provider),model:text(row.model),base_url:text(row.base_url),status:text(row.status)||'not_configured',has_key:row.has_key===true,key_source:text(row.key_source),tested_at:text(row.tested_at),saved_at:text(row.saved_at),dimension:dimension(row.dimension),response_model:text(row.response_model),model_revision:text(row.model_revision),normalization:text(row.normalization),profile_id:text(row.profile_id)};
}

export function embeddingPayload(draft,secret,saved){
 const current=normalizeEmbedding(saved),value=object(draft),key=text(secret);
 const same=current.provider===value.provider&&endpoint(current.base_url)===endpoint(value.base_url);
 return {kind:'embedding',provider:text(value.provider),model:text(value.model).trim(),base_url:text(value.base_url).trim(),...(key?{api_key:key}:{}),use_saved_key:!key&&current.has_key&&same};
}

function fields(state,provider){
 const draft=state.draft||{},fixed=provider.id==='local_bge',stored=state.saved?.has_key===true&&state.saved.provider===provider.id&&endpoint(state.saved.base_url)===endpoint(draft.base_url);
 const models=list(state.models),manual=state.manualModel===true||!models.length;
 const model=fixed?`<div class="ye-local-model"><strong>${esc(draft.model||provider.default_model||'本机模型')}</strong><span>中文 · 本机运行 · 无需 API Key</span></div>`:manual?`<input id="ye-model" name="embedding_model" value="${esc(draft.model||'')}" autocomplete="off" placeholder="填写可用向量模型 ID">`:`<select id="ye-model" name="embedding_model">${draft.model&&!models.some(row=>row.id===draft.model)?`<option value="${esc(draft.model)}" selected>${esc(draft.model)}（当前填写）</option>`:''}${models.map(row=>`<option value="${esc(row.id)}" ${row.id===draft.model?'selected':''}>${esc(row.label||row.id)}</option>`).join('')}</select>`;
 return `<label class="ys-field" for="ye-provider"><span>向量模型服务商</span><select id="ye-provider" name="embedding_provider">${list(state.providers).map(row=>`<option value="${esc(row.id)}" ${row.id===draft.provider?'selected':''}>${esc(row.label||row.id)}</option>`).join('')}</select></label>${provider.requires_key!==false?`<label class="ys-field" for="ye-api-key"><span>API Key${stored?'<small>已安全保存</small>':''}</span><span class="ys-key-input"><input id="ye-api-key" name="embedding_api_key" type="password" autocomplete="new-password" autocapitalize="off" spellcheck="false" placeholder="${stored?'留空继续使用这项连接已保存的 Key':'填写已开通向量模型的 API Key'}"><button type="button" class="ys-reveal-key" data-embedding-action="reveal" aria-label="显示 API Key" aria-pressed="false">显示</button></span></label>`:''}<div class="ys-model-heading"><label for="ye-model">使用的向量模型</label>${provider.models_path?`<button type="button" class="ys-text-button" data-embedding-action="models" ${state.busy?'disabled':''}>${state.busy==='models'?'正在读取…':'读取模型'} ${icon('arrow')}</button>`:''}</div><div class="ys-model-input ye-model-field">${model}</div>${!fixed&&models.length?`<div class="ys-model-picker-tools"><button type="button" class="ys-text-button" data-embedding-action="manual">${manual?'返回模型列表':'手动填写模型 ID'}</button></div>`:''}${fixed?'<p class="ye-local-note">首次部署需准备约 95 MB 模型文件；是否已就绪，以实际测试为准。模型在研究服务所在设备上运行，不上传待检索文本。</p>':`<p class="ys-field-help">${esc(state.modelNotice||'仅选择已开通的向量模型。读取目录不代表已验证向量生成能力。')}</p>`}${!fixed?`<details class="ys-advanced ye-advanced" ${state.advanced?'open':''}><summary>接口与高级设置<span>${provider.id==='custom'?'必填':'可选'}</span></summary><div><label class="ys-field" for="ye-base-url"><span>接口地址</span><input id="ye-base-url" name="embedding_base_url" type="url" value="${esc(draft.base_url||'')}" spellcheck="false" placeholder="使用服务商默认接口"></label>${provider.note?`<p class="ys-field-help">${esc(provider.note)}</p>`:''}</div></details>`:''}`;
}

export function renderEmbeddingSettings(state={}){
 const provider=list(state.providers).find(row=>row.id===state.draft?.provider)||{},saved=normalizeEmbedding(state.saved),busy=!!state.busy;
 if(!list(state.providers).length)return '<div class="ye-empty" role="status"><h2>知识检索连接尚未准备好</h2><p>服务尚未返回支持的向量模型列表，请重新读取服务配置。</p></div>';
 const feedback=state.error?`<div class="ys-feedback is-error" role="alert">${icon('alert')}<div><strong>${esc(state.errorTitle||'向量连接尚未完成')}</strong><p>${esc(state.error)}</p></div></div>`:state.notice?`<div class="ys-feedback ${state.success?'is-success':'is-information'}" role="status">${icon(state.success?'check':'info')}<div><strong>${state.success?'向量连接已验证并保存':state.busy==='test'?'正在验证向量生成':state.busy==='save'?'正在安全保存':'连接提示'}</strong><p>${esc(state.notice)}</p></div></div>`:'';
 return `<section class="ys-form-card ye-form-card" aria-busy="${busy}"><div class="ys-form-intro"><span class="ys-service-mark">知</span><div><h2>让知识按含义被找到</h2><p>关键词精确检索继续保留，语义检索补充不同说法的官方条款。</p></div></div><form id="ye-embedding-form" autocomplete="off">${fields(state,provider)}${saved.status==='connected'?`<div class="ye-verified-identity"><span>已保存连接</span><strong>${esc(saved.model)}${saved.dimension?` · ${saved.dimension} 维`:''}</strong>${saved.model_revision?`<details><summary>模型身份</summary><code>${esc(saved.model_revision)}</code><p>维度来自通过测试的实际向量响应，配置成功不表示知识库索引已完成。</p></details>`:''}</div>`:''}<div class="ye-feedback">${feedback}</div><div class="ys-form-actions"><button type="submit" class="ys-primary-button" ${busy?'disabled':''}>${state.busy==='test'?'正在测试向量…':state.busy==='save'?'正在保存…':state.retrySave?'重试保存':'测试并保存'} ${busy?'<span class="ys-small-spinner" aria-hidden="true"></span>':icon('arrow')}</button>${saved.has_key||saved.status!=='not_configured'?`<button type="button" class="ys-delete-button" data-embedding-action="delete" ${busy?'disabled':''}>移除连接</button>`:''}</div><p class="ys-submit-note">${provider.local?'测试会在研究服务所在设备上实际生成两段短文本的向量。':'测试会真实调用所选向量模型，可能消耗少量服务商额度。'}聊天与元宝连接独立保存。</p></form></section>${renderVectorSettings({...state,provider})}`;
}

function initialState(providers,saved){
 const rows=list(providers).filter(row=>row&&typeof row.id==='string'),current=normalizeEmbedding(saved),provider=rows.find(row=>row.id===current.provider)||rows.find(row=>row.id==='local_bge')||rows[0]||{};
 return {providers:rows,saved:current,draft:{provider:provider.id||'',model:current.provider===provider.id?current.model:provider.default_model||'',base_url:current.provider===provider.id?current.base_url:provider.default_base_url||''},models:[],manualModel:false,busy:'',error:'',errorTitle:'',notice:'',success:false,retrySave:false,advanced:provider.id==='custom'};
}

export function createEmbeddingConnection({api,providers,saved,onChange,onSaved}={}){
 let state=initialState(providers,saved),secret='',receipt=null,disposed=false,generation=0,controller;
 const update=(patch,render=true)=>{state={...state,...patch};if(!disposed)onChange?.(state,render);};
 const cancel=()=>{generation++;controller?.abort();};
 const provider=()=>state.providers.find(row=>row.id===state.draft.provider)||{};
 const payload=()=>embeddingPayload(state.draft,secret,state.saved);
 async function request(path,body){cancel();controller=new AbortController();const ticket=generation;const result=await api(path,{body,signal:controller.signal});if(disposed||ticket!==generation)throw Object.assign(new Error('cancelled'),{name:'AbortError'});return result;}
 function failure(error,phase){if(disposed||error.name==='AbortError')return;if(['invalid_validation','validation_required','validation_expired'].includes(error.code))receipt=null;update({busy:'',error:error.message||'连接暂时未完成，请稍后重试。',errorTitle:error.code==='embedding_model_not_ready'?'本机模型尚未准备':phase==='save'?'测试通过，保存尚未完成':phase==='models'?'向量模型目录尚未读取':'向量调用未通过',retrySave:phase==='save'&&!!receipt,success:false});}
 function validate(){const body=payload(),row=provider();const issue=!row.id?'请选择支持的向量服务商。':!body.model?'请选择或填写向量模型 ID。':row.requires_key!==false&&!body.api_key&&!body.use_saved_key?'请填写这项向量连接的 API Key。':row.id==='custom'&&!body.base_url?'请填写自定义向量接口地址。':'';if(issue){update({error:issue,errorTitle:'还差一项连接信息',advanced:row.id==='custom'||state.advanced});return false;}return true;}
 async function models(){if(state.busy||!validate())return;update({busy:'models',error:'',notice:'',success:false});try{const result=await request('/api/settings/models',payload());const rows=list(result?.models).filter(row=>typeof row?.id==='string');update({busy:'',models:rows,manualModel:false,modelNotice:text(result?.notice),notice:rows.length?`已读取 ${rows.length} 个候选模型，仍需实际向量测试。`:'服务未返回候选模型，可手动填写已开通的模型 ID。'});}catch(error){failure(error,'models');}}
 async function save(){
  if(state.busy||!receipt&&!validate())return;let phase=receipt?'save':'test';
  try{if(!receipt){update({busy:'test',error:'',notice:'正在用两段固定短文本验证向量生成，尚未写入索引。',success:false});const body=payload(),result=await request('/api/settings/test',body);if(!text(result?.validation_token)||!dimension(result?.dimension))throw new Error('服务未返回有效的向量维度与验证收据，请重新测试。');receipt={body,token:result.validation_token,dimension:result.dimension};}
   phase='save';update({busy:'save',error:'',notice:'实际向量测试通过，正在保存这项独立连接。'});const result=await request('/api/settings/save',{...receipt.body,validation_token:receipt.token});const value=normalizeEmbedding(result?.embedding);if(value.status!=='connected'||!value.dimension)throw new Error('配置尚未成为已验证的向量连接，请重新读取后重试。');receipt=null;secret='';update({saved:value,busy:'',retrySave:false,success:true,notice:`已验证 ${value.model} · 实际 ${value.dimension} 维。知识索引状态在下方单独记录。`});onSaved?.(result);
  }catch(error){failure(error,phase);}
 }
 async function remove(){if(state.busy)return;update({busy:'delete',error:'',notice:'',success:false});try{const result=await request('/api/settings/delete',{kind:'embedding'});receipt=null;secret='';update({saved:normalizeEmbedding(result?.embedding),busy:'',notice:'向量连接已移除；聊天、元宝、原文和结构索引保持独立。',retrySave:false});onSaved?.(result);}catch(error){failure(error,'delete');}}
 return {models,save,remove,snapshot:()=>state,secret:()=>secret,
  choose(id){const row=state.providers.find(item=>item.id===id);if(!row)return;cancel();secret='';receipt=null;update({...initialState(state.providers,state.saved),draft:{provider:id,model:state.saved.provider===id?state.saved.model:row.default_model||'',base_url:state.saved.provider===id?state.saved.base_url:row.default_base_url||''},advanced:id==='custom'});},
  input(name,value){if(['test','save','delete'].includes(state.busy))return;const key={embedding_model:'model',embedding_base_url:'base_url'}[name];if(!key&&name!=='embedding_api_key')return;const loading=state.busy==='models';if(loading)cancel();receipt=null;if(name==='embedding_api_key')secret=text(value);update({...(key?{draft:{...state.draft,[key]:text(value)}}:{}),busy:'',retrySave:false,success:false,error:'',notice:''},loading);},
  manual(){update({manualModel:!state.manualModel});},advanced(open){update({advanced:open},false);},dispose(){disposed=true;cancel();secret='';receipt=null;},
 };
}

function loadStyle(document){if(document.querySelector('link[data-ye-style]'))return;const node=document.createElement('link');node.rel='stylesheet';node.href=new URL('./embedding-settings.css',import.meta.url).href;node.dataset.yeStyle='';document.head.append(node);}

export function mountEmbeddingSettings(container,{api,providers,saved,onSaved}={}){
 const document=container.ownerDocument;loadStyle(document);let disposed=false,composing=null,queued=false,state={},vectorState={};
 const render=()=>{if(disposed)return;if(composing){queued=true;return;}const active=document.activeElement,editing=container.contains(active)&&active.matches('input,textarea,select'),html=renderEmbeddingSettings({...state,...vectorState});if(!editing||!preserveSettingsInput(container,html,active))container.innerHTML=html;const key=container.querySelector('[name="embedding_api_key"]');if(key&&key.value!==connection.secret())key.value=connection.secret();if(['test','save','delete'].includes(state.busy))container.querySelectorAll('form input,form select').forEach(node=>node.disabled=true);};
 const vectors=createVectorSettings({api,onChange:value=>{vectorState=value;render();},getProfileId:()=>state.saved?.profile_id});
 const connection=createEmbeddingConnection({api,providers,saved,onChange:(value,shouldRender)=>{state=value;if(shouldRender)render();else{container.querySelector('.ye-feedback')?.replaceChildren();const submit=container.querySelector('form [type="submit"]');if(submit)submit.innerHTML=`测试并保存 ${icon('arrow')}`;}},onSaved:value=>{onSaved?.(value);vectors.load();}});state=connection.snapshot();
 const input=event=>connection.input(event.target.name,event.target.value);
 const change=event=>{if(event.target.name==='embedding_provider'&&!composing&&!state.busy)connection.choose(event.target.value);else input(event);};
 const submit=event=>{if(event.target.id==='ye-embedding-form'){event.preventDefault();if(!composing&&!state.busy)connection.save();}};
 const click=event=>{const button=event.target.closest?.('[data-embedding-action]');if(!button||!container.contains(button)||button.disabled||composing)return;const action=button.dataset.embeddingAction;if(vectors.click(action))return;if(action==='models')connection.models();if(action==='manual')connection.manual();if(action==='delete')connection.remove();if(action==='reveal'){const key=container.querySelector('#ye-api-key');if(key){const show=key.type==='password';key.type=show?'text':'password';button.textContent=show?'隐藏':'显示';button.setAttribute('aria-pressed',String(show));}}};
 const start=event=>{if(event.target.matches('input,textarea'))composing=event.target;};
 const end=event=>{if(event.target===composing){composing=null;input(event);if(queued){queued=false;render();}}};
 const toggle=event=>{if(event.target.matches?.('.ye-vector-settings'))vectors.open(event.target.open);if(event.target.matches?.('.ye-advanced'))connection.advanced(event.target.open);};
 for(const [type,handler] of [['input',input],['change',change],['submit',submit],['click',click],['compositionstart',start],['compositionend',end]])container.addEventListener(type,handler);container.addEventListener('toggle',toggle,true);render();vectors.load();
 return {isBusy:()=>!!state.busy,dispose(){disposed=true;connection.dispose();vectors.dispose();for(const input of container.querySelectorAll('[name="embedding_api_key"]'))input.value='';for(const [type,handler] of [['input',input],['change',change],['submit',submit],['click',click],['compositionstart',start],['compositionend',end]])container.removeEventListener(type,handler);container.removeEventListener('toggle',toggle,true);}};
}
