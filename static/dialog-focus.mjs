// Focus maintenance for the application's native <dialog> surfaces.
// The dictionary is module-scoped so a dialog that swaps its innerHTML while
// staying open keeps the original external trigger as its eventual return point.
const returnFocus=new Map();
const bound=new WeakSet();
const focusable='button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])';

export function openDialog(document,id,html,returnSelector,focusSelector){
 const dialog=document.getElementById(id);
 if(!dialog)return null;
 if(!dialog.open){
  returnFocus.set(id,{element:document.activeElement,selector:returnSelector,hash:document.defaultView?.location?.hash??''});
  dialog.showModal();
 }
 if(!bound.has(dialog)){bound.add(dialog);dialog.addEventListener('close',()=>restoreDialogFocus(document,id,document.defaultView?.location?.hash??''));}
 dialog.innerHTML=html;
 if(document.body?.style)document.body.style.overflow='hidden';
 const target=(focusSelector?dialog.querySelector(focusSelector):null)||dialog.querySelector(focusable)||dialog;
 target?.focus?.({preventScroll:true});
 return dialog;
}

export function closeDialog(document,id){document.getElementById(id)?.close?.();}

export function restoreDialogFocus(document,id,hash){
 const previous=returnFocus.get(id);
 if(!previous||previous.hash!==hash)return false;
 const target=previous.element?.isConnected?previous.element:document.querySelector(previous.selector);
 if(!target?.focus)return false;
 target.focus({preventScroll:true});
 return true;
}

export function clearDialogReturnPoint(id){returnFocus.delete(id);}
