/* Keep an editable node and its ancestors attached, preserving native IME and undo. */
function attributes(current,next,keepValue=false){
 for(const attribute of [...current.attributes])if(!(keepValue&&attribute.name==='value')&&!next.hasAttribute(attribute.name))current.removeAttribute(attribute.name);
 for(const attribute of next.attributes)if(!(keepValue&&attribute.name==='value')&&current.getAttribute(attribute.name)!==attribute.value)current.setAttribute(attribute.name,attribute.value);
}

function ancestors(node,root){
 const result=[];
 for(;node&&node!==root;node=node.parentNode)result.unshift(node);
 return [root,...result];
}

export function preserveSettingsInput(container,html,field){
 if(!field?.name||!container.contains(field))return false;
 const template=container.ownerDocument.createElement('template');template.innerHTML=html;
 const nextField=[...template.content.querySelectorAll('input,textarea,select')].find(node=>node.name===field.name);
 if(!nextField)return false;
 const current=ancestors(field,container),next=ancestors(nextField,template.content);
 if(current.length!==next.length||current.some((node,index)=>index&&node.nodeName!==next[index].nodeName))return false;
 for(let index=0;index<current.length-1;index++){
  const parent=current[index],branch=current[index+1],nextBranch=next[index+1];
  if(index)attributes(parent,next[index]);
  for(const child of [...parent.childNodes])if(child!==branch)child.remove();
  let before=true;
  for(const sibling of next[index].childNodes){if(sibling===nextBranch){before=false;continue;}const copy=sibling.cloneNode(true);if(before)parent.insertBefore(copy,branch);else parent.append(copy);}
 }
 attributes(field,nextField,true);
 return true;
}
