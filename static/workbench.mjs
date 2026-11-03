import {buildRouteRequest,buildAdjustmentRequest,createRequestGate,initialState,scopeKey} from './workbench-model.mjs';
import {renderRoutes,renderRadar,renderProof} from './workbench-views.mjs';

const sessions=new WeakMap();
function loadStyle(document) {
 if(document.querySelector('link[data-wb-style]'))return;
 const link=document.createElement('link');link.rel='stylesheet';link.href=new URL('./workbench.css',import.meta.url).href;link.dataset.wbStyle='';document.head.append(link);
}
function restoreState(context,stored) {
 const initial=initialState(context);
 if(!stored)return initial;
 const sameProfile=stored.profileKey===initial.profileKey,sameUnit=stored.selectedUnitId===initial.selectedUnitId;
 const validScope=!stored.draft.scopeUnit||(context.units||[]).some(unit=>unit.id===stored.draft.scopeUnit);
 const same=sameProfile&&sameUnit&&validScope;
 return {...stored,profileKey:initial.profileKey,selectedUnitId:initial.selectedUnitId,busy:{},errors:{},
  ...(!same?{routeResult:null,adjustmentResult:null,selectedRoute:'subjects-0'}:{}),
  draft:{...stored.draft,...(!sameUnit||!validScope?{scopeUnit:initial.draft.scopeUnit}:{}),...(!sameProfile?{allowSubjects:false,allowRegions:false,allowBudget:false}:{} )}};
}

export function mountWorkbench(container,options) {
 const {api,getContext,onOpenProgram,onSaveProgram,onEditProfile,onMessage}=options;
 const document=container.ownerDocument;loadStyle(document);
 let disposed=false,state=restoreState(getContext(),sessions.get(document));
 const gate=createRequestGate(),tool=getContext().tool;
 const setState=patch=>{state={...state,...patch};sessions.set(document,state);};
 const setDraft=patch=>setState({draft:{...state.draft,...patch}});
 const render=()=>{
  if(disposed)return;
  const focused=document.activeElement,focus=container.contains(focused)?{name:focused.name,value:focused.value,action:focused.dataset?.wbAction,id:focused.dataset?.wbId,route:focused.dataset?.wbRoute}:null;
  const context=getContext();container.innerHTML=`<div data-wb-root>${({routes:renderRoutes,radar:renderRadar,proof:renderProof}[tool]||renderRoutes)(context,state)}</div>`;
  if(focus)Array.from(container.querySelectorAll('input,select,button')).find(node=>focus.name?node.name===focus.name&&(node.type!=='checkbox'||node.value===focus.value):focus.action&&node.dataset.wbAction===focus.action&&node.dataset.wbId===focus.id&&node.dataset.wbRoute===focus.route)?.focus({preventScroll:true});
 };
 const fail=(slot,error)=>{setState({errors:{...state.errors,[slot]:error.message||'请求未完成，请稍后重试。'}});render();};
 async function request(slot,path,body,commit) {
  const ticket=gate.begin(slot);setState({busy:{...state.busy,[slot]:true},errors:{...state.errors,[slot]:''}});render();
  try {const data=await api(path,{...(body===undefined?{}:{body}),signal:ticket.signal});if(ticket.current()&&!disposed){commit(data);setState({busy:{...state.busy,[slot]:false}});render();}}
  catch(error){if(ticket.current()&&!disposed){setState({busy:{...state.busy,[slot]:false}});if(error.name!=='AbortError')fail(slot,error);else render();}}
 }
 function cancel(slot) {
  gate.abort(slot);setState({busy:{...state.busy,[slot]:false},errors:{...state.errors,[slot]:'已取消等待本次结果。服务端已开始的来源读取可能仍会完成。'}});render();
 }
 function runRoutes() {
  try {
   const context=getContext();if(!context.profile?.subjects?.length){onEditProfile?.();return;}
   const body=buildRouteRequest(context,state.draft.scopeUnit),key=scopeKey(context,state.draft.scopeUnit);
   request('routes','/api/route-plan',body,data=>{if(key===scopeKey(getContext(),state.draft.scopeUnit))setState({routeResult:data,selectedRoute:data.routes?.some(route=>route.id===state.selectedRoute)?state.selectedRoute:data.routes?.[0]?.id});});
  }catch(error){fail('routes',error);}
 }
 function runAdjustments() {
  try {const body=buildAdjustmentRequest(getContext(),state.draft);request('adjustments','/api/adjustments',body,data=>setState({adjustmentResult:data}));}
  catch(error){fail('adjustments',error);}
 }
 function readRadar() {
  request('radar','/api/radar',undefined,data=>{setState({radar:data});if(!state.draft.watchDirty)setDraft({watchIds:[...(data.program_ids||[])]});});
 }
 function saveWatches() {
  if(!state.radar||state.busy.radar||state.busy.watch)return;
  const known=new Set((getContext().programs||[]).map(program=>program.id));
  const ids=[...new Set(state.draft.watchIds||[])];
  if(ids.length>100||ids.some(id=>!known.has(id))){fail('watch',new Error('请选择至多100个当前已收录的方向。'));return;}
  request('watch','/api/radar/watch',{program_ids:ids},data=>{setState({radar:data});setDraft({watchIds:[...(data.program_ids||ids)],watchDirty:false});onMessage?.(ids.length?'关注已保存，首次只建立来源基线。':'已关闭全部方向关注。');});
 }
 function checkRadar() {
  if(state.busy.watch||state.busy.radar||!state.radar?.watch_count)return;
  request('radar','/api/radar/check',{},data=>setState({radar:data}));
 }
 function loadTasks() {
  const id=state.draft.proofProgram;if(!id)return;
  request('tasks','/api/source-tasks?program_id='+encodeURIComponent(id),undefined,data=>{if(id===state.draft.proofProgram)setState({proofTasks:data});});
 }
 function runProof() {
  if(!getContext().searchConfigured){fail('proof',new Error('元宝官方检索尚未配置，仍可查看资料缺口。'));return;}
  const id=state.draft.proofProgram,kind=state.draft.proofKind;
  if(!id||!(state.proofTasks?.tasks||[]).some(task=>task.kind===kind)){fail('proof',new Error('请先选择方向并载入对应的补证任务。'));return;}
  request('proof','/api/source-proof',{program_id:id,kind},data=>{if(id===state.draft.proofProgram)setState({proofResults:{...state.proofResults,[kind]:data}});});
 }
 function scopeChanged(value) {
  gate.abort('routes');gate.abort('adjustments');
  setDraft({scopeUnit:value});setState({routeResult:null,adjustmentResult:null,busy:{...state.busy,routes:false,adjustments:false},errors:{}});render();
 }
 function proofProgramChanged(value) {
  gate.abort('tasks');gate.abort('proof');setDraft({proofProgram:value});
  setState({proofTasks:null,proofResults:{},busy:{...state.busy,tasks:false,proof:false},errors:{}});render();loadTasks();
 }
 function invalidateAdjustments() {
  gate.abort('adjustments');setState({adjustmentResult:null,busy:{...state.busy,adjustments:false},errors:{...state.errors,adjustments:''}});
 }
 function onChange(event) {
  const input=event.target,name=input.name;if(!name)return;
  if(name==='scopeUnit'){scopeChanged(input.value);return;}
  if(name==='proofProgram'){proofProgramChanged(input.value);return;}
  if(name==='wb-watch'){const ids=state.draft.watchIds||[];setDraft({watchIds:input.checked?[...new Set([...ids,input.value])]:ids.filter(id=>id!==input.value),watchDirty:true});render();return;}
  if(['allowSubjects','allowRegions','allowBudget'].includes(name)){setDraft({[name]:input.checked});invalidateAdjustments();render();return;}
  if(name==='proofKind'){setDraft({proofKind:input.value});render();}
 }
 function onInput(event) {
  if(['regionInput','budgetInput'].includes(event.target.name)) {
   const needsRender=state.adjustmentResult||state.busy.adjustments||state.errors.adjustments;
   setDraft({[event.target.name]:event.target.value});invalidateAdjustments();if(needsRender)render();
  }
 }
 function onToggle(event) {
  if(!container.contains(event.target))return;
  if(event.target.matches?.('[data-wb-watch-details]'))setDraft({watchOpen:event.target.open});
  if(event.target.matches?.('[data-wb-adjust-details]'))setDraft({adjustmentOpen:event.target.open});
 }
 function onClick(event) {
  const button=event.target.closest?.('[data-wb-action]');if(!button||!container.contains(button)||button.disabled)return;
  event.preventDefault();event.stopPropagation();const action=button.dataset.wbAction;
  const handlers={'run-routes':runRoutes,'run-adjustments':runAdjustments,'edit-profile':()=>onEditProfile?.(),'check-radar':checkRadar,'save-watches':saveWatches,'load-tasks':loadTasks,'run-proof':runProof,'retry-radar':readRadar,
   'select-all-watches':()=>{setDraft({watchIds:[...new Set([...(state.draft.watchIds||[]),...(getContext().savedIds||[])])].slice(0,100),watchDirty:true});render();},'clear-watches':()=>{setDraft({watchIds:[],watchDirty:true});render();},
   'open-program':()=>onOpenProgram?.(button.dataset.wbId),'save-program':()=>{onSaveProgram?.(button.dataset.wbId);render();},
   'select-route':()=>{setState({selectedRoute:button.dataset.wbRoute});render();}};
  if(action.startsWith('cancel-')){cancel(action.slice(7));return;}
  handlers[action]?.();
 }
 function initialize() {
  if(tool==='radar')readRadar();
  if(tool==='proof') {
   const context=getContext(),known=new Set((context.programs||[]).map(program=>program.id));
   if(!known.has(state.draft.proofProgram)){setDraft({proofProgram:(context.savedIds||[]).find(id=>known.has(id))||context.programs?.[0]?.id||''});setState({proofTasks:null,proofResults:{}});}
   if(state.draft.proofProgram&&!state.proofTasks)loadTasks();
  }
 }
 container.addEventListener('click',onClick);container.addEventListener('change',onChange);container.addEventListener('input',onInput);container.addEventListener('toggle',onToggle,true);
 render();initialize();
 return ()=>{disposed=true;gate.close();setState({busy:{}});container.removeEventListener('click',onClick);container.removeEventListener('change',onChange);container.removeEventListener('input',onInput);container.removeEventListener('toggle',onToggle,true);};
}
