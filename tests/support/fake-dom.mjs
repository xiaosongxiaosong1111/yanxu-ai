// Minimal DOM sufficient for exercising the mounted browser controllers in Node.
// It parses the HTML strings produced by the renderers into lightweight elements
// so tests can dispatch real controller events, inspect attributes, follow focus,
// and drive a fake history stack.

const VOID_ELEMENTS=new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
const camel=name=>name.replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase());

function parseAttributes(source){
 const attributes={};
 const pattern=/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
 let match;
 while((match=pattern.exec(source))){
  const [,name,doubleQuoted,singleQuoted,bare]=match;
  attributes[name]=doubleQuoted??singleQuoted??bare??'';
 }
 return attributes;
}

function parseInto(root,html,ownerDocument,container){
 for(const child of root.children)child.parentNode=null;
 root.children=[];
 const stack=[root];
 const pattern=/<(\/?)([a-zA-Z][\w:-]*)([^<>]*?)(\/?)>/g;
 let match;
 while((match=pattern.exec(html))){
  const [,closing,name,attributeText,selfClosing]=match;
  const upper=name.toUpperCase();
  if(closing){
   for(let index=stack.length-1;index>0;index--){
    if(stack[index].tagName===upper){stack.length=index;break;}
   }
   continue;
  }
  const element=new FakeElement(name,parseAttributes(attributeText||''),ownerDocument,{container});
  const parent=stack[stack.length-1];
  element.parentNode=parent;
  parent.children.push(element);
  if(!selfClosing&&!VOID_ELEMENTS.has(name.toLowerCase()))stack.push(element);
 }
}

function splitDescendants(selector){
 const parts=[];
 let current='',depth=0;
 for(const character of selector){
  if(character==='['||character==='(')depth++;
  if(character===']'||character===')')depth--;
  if(/\s/.test(character)&&depth===0){
   if(current){parts.push(current);current='';}
   continue;
  }
  current+=character;
 }
 if(current)parts.push(current);
 return parts;
}

function parseCompound(part){
 const spec={tag:'',id:'',classes:[],attributes:[],nots:[]};
 const pattern=/([a-zA-Z][\w-]*)|#([\w-]+)|\.([\w-]+)|\[([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]|:not\(([^)]*)\)/g;
 let match;
 while((match=pattern.exec(part))){
  if(match[1])spec.tag=match[1].toUpperCase();
  else if(match[2])spec.id=match[2];
  else if(match[3])spec.classes.push(match[3]);
  else if(match[4])spec.attributes.push([match[4],match[5]??match[6]??match[7]]);
  else if(match[8])spec.nots.push(parseCompound(match[8]));
 }
 return spec;
}

function parseSelector(selector){
 return selector.split(',').map(part=>part.trim()).filter(Boolean).map(part=>splitDescendants(part).map(parseCompound));
}

function matchesCompound(element,spec){
 if(!element)return false;
 if(spec.tag&&element.tagName!==spec.tag)return false;
 if(spec.id&&element.id!==spec.id)return false;
 const classes=String(element.attributes.class||'').split(/\s+/).filter(Boolean);
 if(spec.classes.some(name=>!classes.includes(name)))return false;
 for(const [name,value] of spec.attributes){
  if(!Object.hasOwn(element.attributes,name))return false;
  if(value!==undefined&&String(element.attributes[name])!==value)return false;
 }
 for(const not of spec.nots)if(matchesCompound(element,not))return false;
 return true;
}

function matchesGroup(element,compounds){
 let index=compounds.length-1;
 if(!matchesCompound(element,compounds[index]))return false;
 index--;
 let current=element.parentNode;
 while(index>=0&&current){
  if(matchesCompound(current,compounds[index]))index--;
  current=current.parentNode;
 }
 return index<0;
}

function descendants(root){
 const found=[];
 const visit=node=>{for(const child of node.children){found.push(child);visit(child);}};
 visit(root);
 return found;
}

export class FakeElement{
 constructor(tagName,attributes={},ownerDocument=null,{container=null}={}){
  this.tagName=tagName.toUpperCase();
  this.attributes={...attributes};
  this.ownerDocument=ownerDocument;
  this.children=[];
  this.parentNode=null;
  this.id=attributes.id??'';
  this.name=attributes.name??'';
  this.type=attributes.type??'';
  this.value=attributes.value??'';
  this.selectionStart=0;
  this.selectionEnd=0;
  this.open=Object.hasOwn(attributes,'open');
  this.disabled=Object.hasOwn(attributes,'disabled');
  this.checked=Object.hasOwn(attributes,'checked');
  this.hidden=Object.hasOwn(attributes,'hidden');
  this.style={};
  this.dataset={};
  for(const [key,value] of Object.entries(attributes))if(key.startsWith('data-'))this.dataset[camel(key.slice(5))]=value;
  this._container=container||this;
  this._listeners=new Map();
  this._html=undefined;
 }

 get classList(){
  const element=this;
  const values=()=>String(element.attributes.class||'').split(/\s+/).filter(Boolean);
  return {
   contains:name=>values().includes(name),
   add(name){if(!values().includes(name))element.attributes.class=[...values(),name].join(' ');},
   remove(name){element.attributes.class=values().filter(value=>value!==name).join(' ');},
   toggle(name,force){const present=values().includes(name);const next=force===undefined?!present:!!force;if(next)this.add(name);else this.remove(name);return next;}
  };
 }

 get textContent(){return this._text!==undefined?this._text:this.children.map(child=>child.textContent).join('');}
 set textContent(value){this._text=String(value);for(const child of this.children)child.parentNode=null;this.children=[];}
 get isConnected(){let current=this;while(current.parentNode)current=current.parentNode;return Boolean(current.ownerDocument)&&(current===current.ownerDocument.documentElement||current===current.ownerDocument.body||current===current.ownerDocument.head);}

 get innerHTML(){return this._html??'';}
 set innerHTML(html){
  this._html=String(html);
  this._text=undefined;
  const document=this.ownerDocument;
  if(document&&document.activeElement&&this.contains(document.activeElement))document.activeElement=document.body;
  parseInto(this,this._html,document,this._container===this?this:this._container);
 }

 append(child){if(child.parentNode)child.parentNode.children=child.parentNode.children.filter(node=>node!==child);child.parentNode=this;this.children.push(child);return child;}
 remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(node=>node!==this);this.parentNode=null;}

 setAttribute(name,value){this.attributes[name]=String(value);if(name==='id')this.id=String(value);if(name==='name')this.name=String(value);if(name.startsWith('data-'))this.dataset[camel(name.slice(5))]=String(value);}
 removeAttribute(name){delete this.attributes[name];if(name.startsWith('data-'))delete this.dataset[camel(name.slice(5))];}
 getAttribute(name){return Object.hasOwn(this.attributes,name)?this.attributes[name]:null;}
 hasAttribute(name){return Object.hasOwn(this.attributes,name);}
 focus(){this.ownerDocument.activeElement=this;this._focused=true;}
 blur(){if(this.ownerDocument.activeElement===this)this.ownerDocument.activeElement=this.ownerDocument.body;}
 setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}
 scrollIntoView(){}

 showModal(){this.open=true;}
 close(){const wasOpen=this.open;this.open=false;if(wasOpen)this.dispatch('close',{});}

 contains(node){for(let current=node;current;current=current.parentNode)if(current===this)return true;return false;}
 matches(selector){return parseSelector(selector).some(group=>matchesGroup(this,group));}
 closest(selector){for(let current=this;current;current=current.parentNode)if(current.matches?.(selector))return current;return null;}
 querySelectorAll(selector){const groups=parseSelector(selector);return descendants(this).filter(element=>groups.some(group=>matchesGroup(element,group)));}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}

 addEventListener(type,listener){if(!this._listeners.has(type))this._listeners.set(type,[]);this._listeners.get(type).push(listener);}
 removeEventListener(type,listener){const listeners=this._listeners.get(type)||[];const index=listeners.indexOf(listener);if(index>=0)listeners.splice(index,1);}
 dispatch(type,event={}){for(const listener of [...(this._listeners.get(type)||[])])listener({target:this,preventDefault(){},stopPropagation(){},...event});}
}

class FakeDocument{
 constructor(window){
  this.defaultView=window;
  this._listeners=new Map();
  this.documentElement=new FakeElement('html',{},this,{container:this});
  this.head=this.createElement('head');
  this.body=this.createElement('body');
  this.documentElement.append(this.head);
  this.documentElement.append(this.body);
  this.head.ownerDocument=this;
  this.body.ownerDocument=this;
  this.activeElement=this.body;
 }
 createElement(tagName){return new FakeElement(tagName,{},this,{container:this});}
 getElementById(id){return this.querySelector(`#${id}`);}
 contains(node){return this.documentElement.contains(node);}
 querySelectorAll(selector){return this.documentElement.querySelectorAll(selector);}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 addEventListener(type,listener){if(!this._listeners.has(type))this._listeners.set(type,[]);this._listeners.get(type).push(listener);}
 removeEventListener(type,listener){const listeners=this._listeners.get(type)||[];const index=listeners.indexOf(listener);if(index>=0)listeners.splice(index,1);}
 dispatch(type,event={}){for(const listener of [...(this._listeners.get(type)||[])])listener({target:this,preventDefault(){},stopPropagation(){},...event});}
}

export function createPage(hash='#/'){
 const popstateListeners=new Map();
 const location={hash};
 const entries=[{hash,state:null}];
 let index=0;
 const dispatch=(type,event={})=>{for(const listener of [...(popstateListeners.get(type)||[])])listener(event);};
 const history={
  get state(){return entries[index].state;},
  get length(){return entries.length;},
  get index(){return index;},
  get entries(){return entries.map(entry=>({hash:entry.hash,state:entry.state}));},
  pushState(state,_title,url){entries.length=index+1;entries.push({hash:url,state:state??null});index=entries.length-1;location.hash=url;},
  replaceState(state,_title,url){entries[index]={hash:url,state:state??null};location.hash=url;},
  back(){if(index===0)return false;index--;location.hash=entries[index].hash;dispatch('popstate',{state:entries[index].state});return true;},
  forward(){if(index>=entries.length-1)return false;index++;location.hash=entries[index].hash;dispatch('popstate',{state:entries[index].state});return true;}
 };
 const view={
  location,history,scrollY:0,
  scrollTo(_x,y){view.scrollY=typeof y==='number'?y:0;},
  addEventListener(type,listener){if(!popstateListeners.has(type))popstateListeners.set(type,[]);popstateListeners.get(type).push(listener);},
  removeEventListener(type,listener){const listeners=popstateListeners.get(type)||[];const position=listeners.indexOf(listener);if(position>=0)listeners.splice(position,1);}
 };
 const document=new FakeDocument(view);
 view.document=document;
 const container=document.createElement('div');
 container._container=container;
 container.innerHTML='';
 return {document,window:view,container,history,location};
}

export const settle=async()=>{for(let index=0;index<4;index++)await new Promise(resolve=>setImmediate(resolve));};
