import test from 'node:test';
import assert from 'node:assert/strict';
import {createPage,settle} from './support/fake-dom.mjs';

// Boots the real application module against a fake document so the dialog
// actions and the knowledge surface are exercised through their actual event
// listeners, history entries and focus handling rather than rendered strings.

const entry={id:'e1',title:'甲大学目录',unit_name:'甲大学',year:2027,status:'published_snapshot',url:'https://example.edu.cn/e1',excerpt:'甲大学目录摘录',updated_at:'2026-09-01T00:00:00Z',quality:{version:1,state:'accepted',reusable:true,material_type:'catalog'},provenance:['https://example.edu.cn/source']};
const payloads={
 '/api/catalog-bootstrap':{revision:'rev-1',meta:{target_year:2028,reference_year:2027},units:[],coverage:{release_ready:false},service:{search_configured:false}},
 '/api/radar':{program_ids:[],unread_count:0},
 '/api/settings':{},
 '/api/research':{tasks:[],total:0},
 '/api/knowledge':{entries:[entry],total:1,page:1,facets:{units:[],years:[]},summary:{total:1,quality_counts:{accepted:1,pending:0,excluded:0}}}
};
function respond(path){
 if(path.startsWith('/api/knowledge?')){
  const query=new URLSearchParams(path.split('?')[1]||''),id=query.get('entry_id');
  if(id)return {entry};
  return payloads['/api/knowledge'];
 }
 if(path.startsWith('/api/research?'))return payloads['/api/research'];
 return payloads[path]||{};
}

test('application dialogs keep focus and the knowledge surface keeps its history contract',async()=>{
 const page=createPage('#/');
 const document=page.document;
 for(const id of ['app','site-header','toast']){const node=document.createElement(id==='app'?'main':'div');node.setAttribute('id',id);document.body.append(node);}
 for(const id of ['info-dialog','preferences-dialog','evidence-dialog']){const dialog=document.createElement('dialog');dialog.setAttribute('id',id);document.body.append(dialog);}
 document.hidden=false;

 globalThis.document=document;
 globalThis.window=page.window;
 globalThis.location=page.location;
 globalThis.history=page.history;
 globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}};
 globalThis.CSS={escape:value=>String(value)};
 globalThis.setInterval=()=>0;
 globalThis.requestAnimationFrame=callback=>{callback();return 0;};
 globalThis.fetch=async path=>({ok:true,json:async()=>({ok:true,data:respond(String(path))})});

 await import('../static/application.mjs');
 for(let index=0;index<30;index++)await settle();

 const about=document.querySelector('[data-action="about"]');
 assert.ok(about,'header about control should render');
 about.focus();
 document.dispatch('click',{target:about});
 const info=document.getElementById('info-dialog');
 assert.equal(info.open,true);
 assert.equal(info.contains(document.activeElement),true,'opening replaces BODY focus with a dialog control');

 document.querySelector('[data-action="reset-profile"]').focus();
 document.dispatch('click',{target:document.querySelector('[data-action="reset-profile"]')});
 assert.equal(document.activeElement?.dataset?.action,'reset-profile-confirm','clear confirmation receives focus');
 document.querySelector('[data-action="reset-profile-cancel"]').focus();
 document.dispatch('click',{target:document.querySelector('[data-action="reset-profile-cancel"]')});
 assert.equal(document.activeElement?.dataset?.action,'reset-profile','cancel returns to the internal entry');
 document.querySelector('[data-action="close-info"]').focus();
 document.dispatch('click',{target:document.querySelector('[data-action="close-info"]')});
 assert.equal(info.open,false);
 assert.equal(document.activeElement,about,'closing restores the external trigger captured on first open');

 about.focus();
 document.dispatch('click',{target:about});
 const input=document.getElementById('plan-import-input');
 assert.ok(input,'about dialog exposes the plan import input');
 const backup={format:'yanshu-plan',version:1,profile:{target_year:2028,reference_year:2027,education:'unknown',background:'',subjects:[],ranking_mode:'balanced',preferences:{}},preferences:{query:'',unit_id:'',priority:'balanced',examCount:4},saved:[]};
 document.dispatch('change',{target:{id:'plan-import-input',files:[{size:200,text:async()=>JSON.stringify(backup)}],value:''}});
 for(let index=0;index<10;index++)await settle();
 assert.equal(document.activeElement?.dataset?.action,'confirm-import','reading a backup focuses the import confirmation');
 document.querySelector('[data-action="cancel-import"]').focus();
 document.dispatch('click',{target:document.querySelector('[data-action="cancel-import"]')});
 assert.equal(document.activeElement?.dataset?.action,'import-plan','cancelling the import returns to the import entry');
 document.querySelector('[data-action="close-info"]').focus();
 document.dispatch('click',{target:document.querySelector('[data-action="close-info"]')});
 assert.equal(info.open,false);

 const agentRoot=document.getElementById('agent-root');
 const knowledgeLink=agentRoot?.querySelector('[data-agent-action="knowledge"]');
 assert.ok(knowledgeLink,'agent surface should render the knowledge entry point');
 knowledgeLink.focus();
 agentRoot.dispatch('click',{target:knowledgeLink});
 for(let index=0;index<25;index++)await settle();

 let knowledgeRoot=document.getElementById('knowledge-root');
 assert.ok(knowledgeRoot,'knowledge surface should mount after navigation');
 const openButton=knowledgeRoot.querySelector('[data-knowledge-open="e1"]');
 assert.ok(openButton,'knowledge list should render saved entries');
 const listLength=page.history.length;
 const listUrl=page.location.hash;
 const listIndex=page.history.index;
 openButton.focus();
 knowledgeRoot.dispatch('click',{target:openButton});
 for(let index=0;index<10;index++)await settle();
 assert.match(page.location.hash,/entry_id=e1/);
 assert.equal(page.history.length,listLength+1);
 assert.ok(knowledgeRoot.querySelector('.yk-reader'));
 const detailUrl=page.location.hash;
 const detailIndex=page.history.index;

 page.history.back();
 for(let index=0;index<10;index++)await settle();
 assert.equal(knowledgeRoot.querySelector('.yk-reader'),null,'browser back closes the reader');
 assert.equal(page.location.hash,'#/knowledge');

 page.history.forward();
 for(let index=0;index<10;index++)await settle();
 assert.equal(document.getElementById('knowledge-root'),knowledgeRoot,'forward must not tear down the knowledge surface');
 assert.ok(knowledgeRoot.querySelector('.yk-reader'),'forward restores the reader');
 assert.equal(page.history.index,detailIndex);

 const beforeClose=page.history.length;
 const closeButton=knowledgeRoot.querySelector('[data-knowledge-action="close-detail"]');
 closeButton.focus();
 knowledgeRoot.dispatch('click',{target:closeButton});
 for(let index=0;index<10;index++)await settle();
 assert.equal(page.history.length,beforeClose,'closing the reader must not add a duplicate list entry');
 assert.equal(page.location.hash,'#/knowledge');
 assert.equal(knowledgeRoot.querySelector('.yk-reader'),null);
 assert.equal(page.history.index,listIndex,'closing must move the cursor back to the original list entry');
 assert.equal(page.history.entries[detailIndex].hash,detailUrl,'the detail entry must remain ahead of the cursor after closing');
 assert.equal(page.history.entries.filter(item=>item.hash===listUrl).length,1,'closing must not replace the detail entry with a second list entry');

 page.history.forward();
 for(let index=0;index<10;index++)await settle();
 assert.equal(document.getElementById('knowledge-root'),knowledgeRoot,'forward after closing must reuse the knowledge surface');
 assert.ok(knowledgeRoot.querySelector('.yk-reader'),'forward after closing restores the same detail');
 assert.equal(page.location.hash,detailUrl);
 assert.equal(page.history.index,detailIndex);

 page.history.back();
 for(let index=0;index<10;index++)await settle();
 assert.equal(page.history.index,listIndex,'back from the restored detail returns to the original list entry');
 assert.equal(page.location.hash,listUrl);
 assert.equal(knowledgeRoot.querySelector('.yk-reader'),null);
 assert.equal(page.history.entries.filter(item=>item.hash===listUrl).length,1,'close/forward/back leaves only one list entry');
 assert.equal(page.history.length,beforeClose);
});
