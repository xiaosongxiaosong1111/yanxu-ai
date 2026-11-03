import {esc} from './workbench-model.mjs';
import {icon} from './icons.mjs';

const object=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
const text=value=>typeof value==='string'?value:'';
const list=value=>Array.isArray(value)?value:[];
const safeUrl=value=>{try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.href:'';}catch{return '';}};
const statusNames={connected:'连接已验证',not_configured:'尚未配置',not_tested:'尚未验证'};
export function connectionLabel(value){const item=object(value);return item.status==='connected'?'连接已验证':item.has_key||item.status==='not_tested'?'尚未验证':'尚未配置';}
export function normalizeSettings(value){
 const source=object(value),connection=kind=>{const row=object(source[kind]);return {kind,provider:text(row.provider),model:text(row.model),base_url:text(row.base_url),status:text(row.status)||'not_configured',has_key:row.has_key===true,key_source:text(row.key_source),tested_at:text(row.tested_at),saved_at:text(row.saved_at),...(kind==='yuanbao'?{auth_mode:row.auth_mode==='standard'?'standard':'api_key'}:{})};};
 return {ai:connection('ai'),yuanbao:connection('yuanbao'),storage:{mode:text(source.storage?.mode),persistent:source.storage?.persistent===true}};
}
export function settingsPayload(kind,draft,credentials,saved){
 const current=object(saved?.[kind]),provider=kind==='yuanbao'?'yuanbao':text(draft?.provider);
 const secret=typeof credentials==='string'?{api_key:credentials}:object(credentials),apiKey=text(secret.api_key);
 if(kind==='yuanbao'){
  const mode=draft?.auth_mode==='standard'?'standard':'api_key',sameMode=(current.auth_mode||'api_key')===mode;
  if(mode==='standard'){
   const id=text(secret.secret_id),key=text(secret.secret_key);
   return {kind,provider,auth_mode:mode,...(id?{secret_id:id}:{}),...(key?{secret_key:key}:{}),use_saved_key:!id&&!key&&sameMode&&current.has_key===true};
  }
  return {kind,provider,auth_mode:mode,...(apiKey?{api_key:apiKey}:{}),use_saved_key:!apiKey&&sameMode&&current.has_key===true};
 }
 return {kind,provider,...(kind==='ai'?{model:text(draft?.model).trim(),base_url:text(draft?.base_url).trim()}:{}),
  ...(apiKey?{api_key:apiKey}:{}),use_saved_key:!apiKey&&current.has_key===true&&(kind==='yuanbao'||current.provider===provider)};
}
const authMode=(state,saved)=>['standard','api_key'].includes(state.yuanbaoAuthMode)?state.yuanbaoAuthMode:saved.yuanbao.has_key?saved.yuanbao.auth_mode:'standard';
function secretField(name,label,stored,placeholder,note=''){return `<label class="ys-field" for="ys-${name.replace('_','-')}"><span>${esc(label)}${note?`<small>${esc(note)}</small>`:''}${stored?'<small>已安全保存</small>':''}</span><span class="ys-key-input"><input id="ys-${name.replace('_','-')}" name="${name}" type="password" autocomplete="new-password" autocapitalize="off" spellcheck="false" placeholder="${stored?'留空继续使用已保存的凭据':esc(placeholder)}"><button type="button" class="ys-reveal-key" data-settings-action="reveal" data-secret-field="${name}" aria-label="显示 ${esc(label)}" aria-pressed="false">显示</button></span></label>`;}
function yuanbaoCredentials(mode,stored,busy){return `<fieldset class="ys-auth-modes"><legend>接入方式</legend><div>${[['standard','腾讯云标准方式','SecretId + SecretKey'],['api_key','服务 API Key','一个元宝搜索服务密钥']].map(([value,title,description])=>`<label class="ys-auth-option ${mode===value?'is-selected':''}"><input type="radio" name="auth_mode" value="${value}" ${mode===value?'checked ':''}${busy?'disabled':''}><span><strong>${title}</strong><small>${description}</small></span></label>`).join('')}</div></fieldset>${mode==='standard'?secretField('secret_id','SecretId',stored,'填写腾讯云 SecretId')+secretField('secret_key','SecretKey',stored,'填写与 SecretId 配对的 SecretKey'):secretField('api_key','服务 API Key',stored,'填写元宝搜索服务 API Key')}${stored&&mode==='standard'?'<p class="ys-pair-note">两项都留空可使用已保存凭据；更换时请填写完整的一对。</p>':''}`;}
function yuanbaoExplanation(mode){return `<div class="ys-yuanbao-explainer"><span class="ys-quiet-label">${mode==='standard'?'服务端自动签名':'独立的元宝搜索服务密钥'}</span><p>${mode==='standard'?'使用 SecretId 和 SecretKey 完成腾讯云签名鉴权，无需填写地域。':'使用元宝搜索专用的服务 API Key，与腾讯云 SecretKey 不同。'}测试会发起一次真实检索，结果仍需核对来源、年份和适用范围。</p><a class="ys-doc-link" href="https://cloud.tencent.com/document/product/1806/${mode==='standard'?'121802':'130615'}" target="_blank" rel="noopener noreferrer">查看官方接入说明 ${icon('arrowUp')}</a></div>`;}
const statusClass=item=>item?.status==='connected'?'is-connected':item?.has_key?'is-pending':'is-empty';
function connectionRow(title,item){return `<div class="ys-connection-row"><span class="ys-status-dot ${statusClass(item)}"></span><div><strong>${esc(title)}</strong><span>${esc(connectionLabel(item))}</span></div>${item?.status==='connected'?icon('check'):''}</div>`;}
function summary(saved){return `<aside class="ys-settings-aside"><div class="ys-summary-card"><span class="ys-kicker">连接概览</span><h2>为每一次研究，<br>准备好工具。</h2>${connectionRow('AI 模型',saved.ai)}${connectionRow('元宝搜索',saved.yuanbao)}<p class="ys-aside-note">AI 负责理解与规划，元宝负责发现资料。算法始终保留可复算的比较依据。</p></div><div class="ys-security-note">${icon('shield')}<div><strong>密钥由你掌握</strong><p>Key 仅提交到当前研究服务，不保存到浏览器。${saved.storage.persistent?'当前使用加密存储，页面不会回显已保存的 Key。':'当前为会话存储，重启服务后需要重新填写。'}</p></div></div></aside>`;}

function modelPicker(draft,models,manual,busy){
 const current=text(draft.model),useInput=!models.length||manual;
 const extra=current&&!models.some(model=>model.id===current)?`<option value="${esc(current)}" selected>${esc(current)}（当前填写，列表未返回）</option>`:!current?'<option value="" selected disabled>请选择模型</option>':'';
 const field=useInput?`<input id="ys-model" name="model" type="text" autocomplete="off" value="${esc(current)}" placeholder="${models.length?'填写模型 ID':'读取模型后选择，也可手动输入模型 ID'}">`:`<select id="ys-model" name="model">${extra}${models.map(model=>`<option value="${esc(model.id)}"${model.id===current?' selected':''}>${esc(model.label||model.id)}</option>`).join('')}</select>`;
 return field+(models.length?`<div class="ys-model-picker-tools"><button type="button" class="ys-text-button" data-settings-action="model-mode" aria-controls="ys-model" aria-pressed="${!!manual}" ${busy?'disabled':''}>${manual?'返回模型列表':'手动填写模型 ID'}</button></div>`:'');
}

export function renderSettings(state={}){
 if(['loading','error'].includes(state.loadStatus))return `<section class="ys-settings-page"><button type="button" class="ys-back" data-settings-action="back">${icon('arrow')} 返回研究</button><header class="ys-settings-heading"><span class="ys-kicker">服务连接</span><h1>连接 AI，开始研究。</h1><p>服务状态会从当前研究服务读取。</p></header><div class="ys-load-state" role="status" aria-live="polite"><h2>${state.loadStatus==='loading'?'正在读取服务配置':'暂时无法读取服务配置'}</h2><p>${esc(state.loadStatus==='loading'?'请稍候，读取完成后即可选择模型服务商。':state.error||'服务列表尚未读取，不能判断连接状态。')}</p>${state.loadStatus==='error'?'<button type="button" class="ys-primary-button" data-settings-action="reload">重新读取配置</button>':''}</div></section>`;
 const saved=normalizeSettings(state.saved),tab=state.tab==='yuanbao'?'yuanbao':'ai',draft=object(state.draft),providers=list(state.providers),provider=providers.find(item=>item.id===draft.provider)||{},busy=!!state.busy;
 const mode=authMode(state,saved),same=tab==='yuanbao'?mode===saved.yuanbao.auth_mode:draft.provider===saved.ai.provider,stored=saved[tab].has_key&&same;
 const official=safeUrl(provider.docs_url),models=list(state.models),modelInput=modelPicker(draft,models,state.manualModel===true,busy);
 return `<section class="ys-settings-page"><button type="button" class="ys-back" data-settings-action="back">${icon('arrow')} 返回研究</button><header class="ys-settings-heading"><span class="ys-kicker">服务连接</span><h1>连接 AI，开始研究。</h1><p>选择接入方式，填写你的服务凭据。连接验证通过后，即可开始研究。</p></header><div class="ys-settings-layout"><div class="ys-settings-main"><div class="ys-settings-tabs" role="tablist" aria-label="配置类型"><button type="button" role="tab" id="ys-tab-ai" aria-controls="ys-settings-panel" aria-selected="${tab==='ai'}" data-settings-tab="ai">AI 模型</button><button type="button" role="tab" id="ys-tab-yuanbao" aria-controls="ys-settings-panel" aria-selected="${tab==='yuanbao'}" data-settings-tab="yuanbao">元宝搜索</button></div><section class="ys-form-card" id="ys-settings-panel" role="tabpanel" aria-labelledby="ys-tab-${tab}" aria-busy="${busy}"><div class="ys-form-intro"><span class="ys-service-mark">${tab==='ai'?'AI':'搜'}</span><div><h2>${tab==='ai'?'选择你的模型服务':'连接元宝搜索'}</h2><p>${tab==='ai'?'让 Agent 理解需求、安排研究，并清楚解释结果。':'从官方目录与公告中，为每一条建议找到出处。'}</p></div></div><form id="ys-settings-form" autocomplete="off">${tab==='ai'?`<label class="ys-field" for="ys-provider"><span>模型服务商</span><select id="ys-provider" name="provider">${providers.map(item=>`<option value="${esc(item.id)}" ${item.id===draft.provider?'selected':''}>${esc(item.label||item.id)}</option>`).join('')}</select></label>`:''}${tab==='yuanbao'?yuanbaoCredentials(mode,stored,busy):secretField('api_key','API Key',stored,provider.requires_key===false?'本地模型无需填写':'粘贴你的 API Key',provider.requires_key===false?'本地模型无需填写':'')}${tab==='ai'?`<div class="ys-model-heading"><label for="ys-model">使用的模型</label><button type="button" class="ys-text-button" data-settings-action="models" ${busy?'disabled':''}>${state.busy==='models'?'正在读取…':'读取模型'} ${icon('arrow')}</button></div><div class="ys-model-input">${modelInput}</div><p class="ys-field-help">${esc(state.modelNotice||(models.length?`已读取 ${models.length} 个模型。模型是否可调用，以连接测试为准。`:'先读取模型列表；服务商不支持时，可以填写可用模型 ID。'))}</p><details class="ys-advanced" ${state.advanced?'open':''}><summary>${provider.id==='custom'?'接口地址':'接口与高级设置'}<span>${provider.id==='custom'?'必填':'可选'}</span></summary><div><label class="ys-field" for="ys-base-url"><span>接口地址</span><input id="ys-base-url" name="base_url" type="url" value="${esc(draft.base_url||provider.default_base_url||provider.base_url||'')}" placeholder="使用默认官方接口" spellcheck="false"></label>${provider.note?`<p class="ys-field-help">${esc(provider.note)}</p>`:''}${official?`<a href="${esc(official)}" class="ys-doc-link" target="_blank" rel="noopener noreferrer">查看官方接入说明 ${icon('arrowUp')}</a>`:''}</div></details>`:yuanbaoExplanation(mode)}<div class="ys-settings-feedback" role="status" aria-live="polite">${state.error?`<div class="ys-feedback is-error">${icon('alert')}<div><strong>${esc(state.errorTitle||'连接尚未完成')}</strong><p>${esc(state.error)}</p></div></div>`:state.notice?`<div class="ys-feedback ${state.success?'is-success':'is-information'}">${icon(state.success?'check':'info')}<div><strong>${state.success?'连接已验证并保存':state.busy?'正在验证连接':'连接提示'}</strong><p>${esc(state.notice)}</p></div></div>`:''}</div><div class="ys-form-actions"><button type="submit" class="ys-primary-button" ${busy?'disabled':''}>${state.busy==='test'?'正在测试连接…':state.busy==='save'?'正在安全保存…':state.retrySave?'重试保存':'测试并保存'}${busy?'<span class="ys-small-spinner" aria-hidden="true"></span>':icon('arrow')}</button>${saved[tab].has_key||['connected','not_tested'].includes(saved[tab].status)?`<button type="button" class="ys-delete-button" data-settings-action="delete" ${busy?'disabled':''}>移除连接</button>`:''}</div><p class="ys-submit-note">${tab==='ai'?'测试会真实调用所选模型，可能产生少量服务商用量。':'测试会真实调用元宝搜索，使用你的搜索服务额度。'}</p></form></section></div>${summary(saved)}</div></section>`;
}

function loadStyle(document){if(document.querySelector('link[data-ys-settings-style]'))return;const link=document.createElement('link');link.rel='stylesheet';link.href=new URL('./settings.css',import.meta.url).href;link.dataset.ysSettingsStyle='';document.head.append(link);}
export function mountSettings(container,{api,onBack,onSaved}={}){
 const document=container.ownerDocument;loadStyle(document);
 let disposed=false,generation=0,controller,initController,initGeneration=0;
 let secretDrafts={ai:{api_key:''},yuanbao:{api_key:{api_key:''},standard:{secret_id:'',secret_key:''}}},validationTickets={ai:null,yuanbao:null};
 let state={saved:{},providers:[],tab:'ai',yuanbaoAuthMode:'standard',draft:{provider:'',model:'',base_url:''},models:[],manualModel:false,busy:'load',loadStatus:'loading',error:'',errorTitle:'',notice:'',success:false,advanced:false,retrySave:false};
 const currentSecrets=()=>state.tab==='ai'?secretDrafts.ai:secretDrafts.yuanbao[state.yuanbaoAuthMode];
 const changeSecrets=changes=>{secretDrafts=state.tab==='ai'?{...secretDrafts,ai:{...secretDrafts.ai,...changes}}:{...secretDrafts,yuanbao:{...secretDrafts.yuanbao,[state.yuanbaoAuthMode]:{...currentSecrets(),...changes}}};};
 const clearSecrets=kind=>{secretDrafts=kind==='ai'?{...secretDrafts,ai:{api_key:''}}:{...secretDrafts,yuanbao:{api_key:{api_key:''},standard:{secret_id:'',secret_key:''}}};};
 const discardTicket=kind=>{validationTickets={...validationTickets,[kind]:null};};
 const render=()=>{
  if(disposed)return;const active=document.activeElement,focus=container.contains(active)?{name:active.name,start:active.selectionStart,end:active.selectionEnd}:null;
  container.innerHTML=renderSettings(state);for(const [name,value] of Object.entries(currentSecrets())){const secret=container.querySelector(`[name="${name}"]`);if(secret)secret.value=value;}
  if(['test','save','delete'].includes(state.busy))container.querySelectorAll('#ys-settings-form input,#ys-settings-form select').forEach(input=>input.disabled=true);
  if(focus?.name){const input=container.querySelector(`[name="${focus.name}"]`);input?.focus({preventScroll:true});if(input&&typeof focus.start==='number'&&['text','password','url'].includes(input.type))input.setSelectionRange(focus.start,focus.end);}
 };
 const request=async(path,body)=>{controller?.abort();controller=new AbortController();const ticket=++generation;const data=await api(path,{...(body===undefined?{}:{body}),signal:controller.signal});if(disposed||ticket!==generation)throw Object.assign(new Error('cancelled'),{name:'AbortError'});return data;};
 const applySaved=value=>{state={...state,saved:normalizeSettings(value)};};
 const chooseProvider=id=>{const provider=state.providers.find(item=>item.id===id)||{},saved=state.saved.ai||{};clearSecrets('ai');discardTicket('ai');state={...state,draft:{provider:id,model:saved.provider===id?saved.model:'',base_url:saved.provider===id?saved.base_url:provider.default_base_url||provider.base_url||''},models:[],manualModel:false,error:'',errorTitle:'',notice:'',success:false,retrySave:false,modelNotice:'',advanced:!provider.default_base_url&&!provider.base_url};};
 const payload=()=>settingsPayload(state.tab,{...state.draft,auth_mode:state.yuanbaoAuthMode},currentSecrets(),state.saved);
 const start=busy=>{state={...state,busy,error:'',errorTitle:'',notice:'',success:false};render();};
 const fail=(error,phase=state.busy)=>{
  if(disposed||error.name==='AbortError')return;
  const titles={models:'模型列表尚未读取',test:state.tab==='ai'?'模型调用未通过':'真实检索未通过',save:'连接已验证，保存尚未完成',delete:'连接尚未移除'};
  if(phase==='save'&&['validation_required','validation_expired','invalid_validation'].includes(error.code))discardTicket(state.tab);
  state={...state,busy:'',error:error.message||'连接未完成，请检查配置后重试。',errorTitle:titles[phase]||'连接尚未完成',success:false,retrySave:phase==='save'&&!!validationTickets[state.tab]};render();
 };
 async function initialize(){
  initController?.abort();initController=new AbortController();const ticket=++initGeneration;state={...state,busy:'load',loadStatus:'loading',error:''};render();
  try{const [providers,saved]=await Promise.all([api('/api/settings/providers',{signal:initController.signal}),api('/api/settings',{signal:initController.signal})]);if(disposed||ticket!==initGeneration)return;
   const rows=list(providers?.providers??providers).filter(item=>item&&typeof item.id==='string');if(!rows.length)throw new Error('服务商列表为空，请检查服务后重新读取。');
   state={...state,providers:rows,busy:'',loadStatus:'ready'};applySaved(saved);state={...state,yuanbaoAuthMode:state.saved.yuanbao.has_key?state.saved.yuanbao.auth_mode:'standard'};chooseProvider(state.saved.ai.provider||rows[0].id);render();
  }catch(error){if(disposed||ticket!==initGeneration||error.name==='AbortError')return;state={...state,busy:'',loadStatus:'error',error:error.message||'暂时无法读取服务配置。'};render();}
 }
 function validate(body,modelRequired){
  const provider=state.providers.find(item=>item.id===body.provider);
  const pair=state.tab==='yuanbao'&&body.auth_mode==='standard';
  const issue=pair&&!body.use_saved_key&&!body.secret_id?['secret_id','请填写 SecretId；使用已保存凭据时，请将两项都留空。']:pair&&!body.use_saved_key&&!body.secret_key?['secret_key','请填写与 SecretId 配对的 SecretKey，不能只更换一项。']:state.tab==='ai'&&!provider?['provider','请先选择模型服务商。']:state.tab==='ai'&&modelRequired&&!body.model?['model','请选择或填写要实际调用的模型 ID。']:!pair&&!body.api_key&&!body.use_saved_key&&(state.tab==='yuanbao'||provider?.requires_key!==false)?['api_key','请填写 API Key，或使用当前服务已保存的 Key。']:state.tab==='ai'&&provider?.id==='custom'&&!body.base_url?['base_url','请填写自定义兼容接口地址。']:null;
  if(!issue)return true;state={...state,busy:'',error:issue[1],errorTitle:'还差一项连接信息',success:false,...(issue[0]==='base_url'?{advanced:true}:{})};render();container.querySelector(`[name="${issue[0]}"]`)?.focus();return false;
 }
 async function models(){
  const body=payload();if(!validate(body,false))return;start('models');
  try{const result=await request('/api/settings/models',body);const rows=list(result?.models).filter(item=>item&&typeof item.id==='string');state={...state,busy:'',models:rows,manualModel:false,modelNotice:text(result?.notice),notice:result.discovery==='manual'?'该服务需手动填写模型 ID，尚未验证实际调用。':rows.length?`已读取 ${rows.length} 个模型，接下来测试所选模型。`:'服务没有返回可用模型，请检查账号权限或手动填写模型 ID。',draft:{...state.draft,model:state.draft.model||rows[0]?.id||''}};render();}catch(error){fail(error,'models');}
 }
 async function save(){
  if(state.loadStatus!=='ready')return;const kind=state.tab;let pending=validationTickets[kind];
  const body=pending?.body||payload();if(!pending&&!validate(body,true))return;
  try{
   if(!pending){start('test');const result=await request('/api/settings/test',body);if(typeof result?.validation_token!=='string'||!result.validation_token)throw new Error('服务没有返回有效的连接验证结果，请重试。');pending={body,token:result.validation_token};validationTickets={...validationTickets,[kind]:pending};}
   state={...state,busy:'save',notice:'连接测试通过，正在安全保存配置。',error:'',errorTitle:'',success:false};render();
   const saved=await request('/api/settings/save',{...pending.body,validation_token:pending.token});applySaved(saved);discardTicket(kind);clearSecrets(kind);
   state={...state,busy:'',success:true,retrySave:false,notice:kind==='ai'?'所选模型已完成真实调用验证，研究任务将使用这项连接。':'元宝搜索已完成真实检索验证，研究任务可以查找公开资料。'};render();onSaved?.(state.saved);
  }catch(error){fail(error,state.busy);}
 }
 async function remove(){start('delete');try{applySaved(await request('/api/settings/delete',{kind:state.tab}));clearSecrets(state.tab);discardTicket(state.tab);state={...state,busy:'',notice:'连接已移除，你可以重新选择服务并配置。',models:[],success:false,retrySave:false};render();onSaved?.(state.saved);}catch(error){fail(error,'delete');}}
 const input=event=>{
  const name=event.target.name,value=event.target.value;if(!['api_key','secret_id','secret_key','model','base_url'].includes(name)||['test','save','delete'].includes(state.busy))return;
  const interrupted=state.busy==='models';if(interrupted){controller?.abort();generation++;}discardTicket(state.tab);
  const credential=['api_key','secret_id','secret_key'].includes(name);if(credential)changeSecrets({[name]:value});
  state={...state,...(!credential?{draft:{...state.draft,[name]:value}}:{}),...(interrupted?{busy:''}:{}),success:false,notice:'',error:'',retrySave:false};
  if(interrupted)render();else{container.querySelector('.ys-settings-feedback')?.replaceChildren();const submit=container.querySelector('#ys-settings-form [type="submit"]');if(submit)submit.innerHTML=`测试并保存 ${icon('arrow')}`;}
 };
 const change=event=>{
  if(event.target.name==='auth_mode'&&state.tab==='yuanbao'&&!state.busy&&['standard','api_key'].includes(event.target.value)){discardTicket('yuanbao');state={...state,yuanbaoAuthMode:event.target.value,error:'',errorTitle:'',notice:'',success:false,retrySave:false};render();container.querySelector('[name="auth_mode"]:checked')?.focus({preventScroll:true});}
  if(event.target.name==='provider'&&!['test','save','delete'].includes(state.busy)){controller?.abort();generation++;chooseProvider(event.target.value);state={...state,busy:''};render();}
 };
 const submit=event=>{if(event.target.id==='ys-settings-form'){event.preventDefault();if(!state.busy)save();}};
 const click=event=>{
  const button=event.target.closest?.('[data-settings-action],[data-settings-tab]');if(!button||!container.contains(button))return;
  if(button.dataset.settingsTab){if(state.busy)return;const tab=button.dataset.settingsTab;state={...state,tab,error:'',errorTitle:'',notice:validationTickets[tab]?'连接测试已通过，尚待完成保存。':'',success:false,retrySave:!!validationTickets[tab]};render();return;}
  const action=button.dataset.settingsAction;if(action==='back')onBack?.();if(action==='reload')initialize();if(action==='models'&&!state.busy)models();if(action==='delete'&&!state.busy)remove();
  if(action==='model-mode'&&!state.busy&&state.models.length){state={...state,manualModel:!state.manualModel};render();container.querySelector('#ys-model')?.focus({preventScroll:true});}
  if(action==='reveal'){const name=button.dataset.secretField||'api_key',field=container.querySelector(`[name="${name}"]`);if(field){const show=field.type==='password',label=name==='secret_id'?'SecretId':name==='secret_key'?'SecretKey':'API Key';field.type=show?'text':'password';button.textContent=show?'隐藏':'显示';button.setAttribute('aria-pressed',String(show));button.setAttribute('aria-label',(show?'隐藏 ':'显示 ')+label);}}
 };
 const toggle=event=>{if(event.target.classList?.contains('ys-advanced'))state={...state,advanced:event.target.open};};
 container.addEventListener('input',input);container.addEventListener('change',change);container.addEventListener('submit',submit);container.addEventListener('click',click);container.addEventListener('toggle',toggle,true);render();initialize();
 return ()=>{disposed=true;generation++;initGeneration++;controller?.abort();initController?.abort();clearSecrets('ai');clearSecrets('yuanbao');validationTickets={ai:null,yuanbao:null};for(const field of container.querySelectorAll('[name="api_key"],[name="secret_id"],[name="secret_key"]'))field.value='';container.removeEventListener('input',input);container.removeEventListener('change',change);container.removeEventListener('submit',submit);container.removeEventListener('click',click);container.removeEventListener('toggle',toggle,true);};
}
