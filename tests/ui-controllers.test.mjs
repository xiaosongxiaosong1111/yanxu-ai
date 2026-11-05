import test from 'node:test';
import assert from 'node:assert/strict';
import {createPage,settle} from './support/fake-dom.mjs';
import {mountKnowledge} from '../static/knowledge.mjs';
import {mountAgent} from '../static/agent.mjs';
import {openDialog,closeDialog} from '../static/dialog-focus.mjs';

// These harnesses mount the real controllers against a fake document. They
// dispatch events through the same listeners a browser would use, drive a real
// (in-memory) history stack, and read document.activeElement after each render.

function dispatch(container,selector){
 const target=container.querySelector(selector);
 assert.ok(target,`missing element ${selector}`);
 target.focus?.();
 container.dispatch('click',{target});
 return target;
}

const entry=(id,title)=>({id,title,unit_name:'甲大学',year:2027,status:'published_snapshot',url:'https://example.edu.cn/'+id,excerpt:title+' 摘录',updated_at:'2026-09-01T00:00:00Z',quality:{version:1,state:'accepted',reusable:true,material_type:'catalog'},provenance:['https://example.edu.cn/source']});

function knowledgeHarness({hash='#/knowledge',initialEntryId='',entries=[entry('e1','甲大学目录'),entry('e2','乙大学目录')]}={}){
 const page=createPage(hash);
 const requests=[];
 const api=async path=>{
  requests.push(path);
  const query=new URLSearchParams(path.split('?')[1]||'');
  const id=query.get('entry_id');
  if(id)return {entry:entries.find(item=>item.id===id)||entry(id,'资料 '+id)};
  return {entries,total:entries.length,page:Number(query.get('page'))||1,facets:{units:[],years:[]},summary:{total:entries.length,quality_counts:{accepted:entries.length,pending:0,excluded:0}}};
 };
 const cleanup=mountKnowledge(page.container,{api,initialEntryId,initialQuality:new URLSearchParams(hash.split('?')[1]||'').get('quality')||'accepted'});
 return {...page,container:page.container,requests,cleanup};
}

test('knowledge detail history: back closes, forward restores, close never duplicates list entries',async()=>{
 const harness=knowledgeHarness({hash:'#/knowledge?quality=pending'});
 await settle();
 assert.equal(harness.history.length,1);
 const listUrl=harness.location.hash;
 const listIndex=harness.history.index;
 dispatch(harness.container,'[data-knowledge-open="e1"]');
 await settle();
 assert.match(harness.location.hash,/quality=pending/);
 assert.match(harness.location.hash,/entry_id=e1/);
 assert.equal(harness.history.length,2);
 assert.ok(harness.container.querySelector('.yk-reader'),'reader should be mounted');
 const detailUrl=harness.location.hash;
 const detailIndex=harness.history.index;

 harness.history.back();
 await settle();
 assert.equal(harness.container.querySelector('.yk-reader'),null,'browser back must close the reader');
 assert.equal(harness.location.hash,'#/knowledge?quality=pending');
 assert.match(harness.container.innerHTML,/甲大学目录/);

 harness.history.forward();
 await settle();
 assert.ok(harness.container.querySelector('.yk-reader'),'browser forward must restore the reader');
 assert.equal(harness.location.hash,'#/knowledge?quality=pending&entry_id=e1');
 assert.ok(harness.container.querySelector('#yk-reader-title'),'reader title is present after forward');

 dispatch(harness.container,'[data-knowledge-action="close-detail"]');
 await settle();
 assert.equal(harness.history.length,2,'closing an opened detail must not add another list entry');
 assert.equal(harness.location.hash,listUrl);
 assert.equal(harness.container.querySelector('.yk-reader'),null);
 assert.equal(harness.document.activeElement?.dataset?.knowledgeOpen,'e1','focus returns to the entry that opened the detail');
 assert.equal(harness.history.index,listIndex,'closing must rewind the cursor to the original list entry, not replace the detail entry in place');
 assert.equal(harness.history.entries[detailIndex].hash,detailUrl,'the detail entry must still sit ahead of the cursor after closing');
 assert.equal(harness.history.entries.filter(item=>item.hash===listUrl).length,1,'closing must not leave a second list entry');
 harness.history.forward();
 await settle();
 assert.equal(harness.history.index,detailIndex,'forward after closing must land on the same detail entry');
 assert.equal(harness.location.hash,detailUrl);
 assert.ok(harness.container.querySelector('.yk-reader'),'forward after closing restores the same detail');
 assert.ok(harness.container.querySelector('#yk-reader-title'),'the restored detail has its reader title');
 assert.match(harness.location.hash,/entry_id=e1/);
 harness.history.back();
 await settle();
 assert.equal(harness.history.index,listIndex,'back from the restored detail returns to the original list entry');
 assert.equal(harness.location.hash,listUrl);
 assert.equal(harness.container.querySelector('.yk-reader'),null);
 assert.equal(harness.history.entries.filter(item=>item.hash===listUrl).length,1,'no duplicate list entry appears after close/forward/back');
 assert.equal(harness.history.entries.length,2);
 assert.equal(harness.document.activeElement?.dataset?.knowledgeOpen,'e1','back to the list restores the entry focus');
 harness.cleanup();
});

test('knowledge deep link closes in place without rewinding unrelated history',async()=>{
 const harness=knowledgeHarness({hash:'#/knowledge?entry_id=e1',initialEntryId:'e1'});
 await settle();
 assert.ok(harness.container.querySelector('.yk-reader'));
 assert.equal(harness.history.length,1);
 dispatch(harness.container,'[data-knowledge-action="close-detail"]');
 await settle();
 assert.equal(harness.history.length,1,'a deep-linked detail has no list entry to rewind to');
 assert.equal(harness.location.hash,'#/knowledge');
 assert.equal(harness.container.querySelector('.yk-reader'),null);
 harness.cleanup();
});

function researchTask(id,query){return {research_id:id,query,status:'completed',created_at:'2026-09-01T00:00:00Z',updated_at:'2026-09-02T00:00:00Z'};}

function agentHarness(){
 const page=createPage('#/');
 const calls=[];
 const api=(path,options={})=>new Promise((resolve,reject)=>calls.push({path,options,resolve,reject}));
 const cleanup=mountAgent(page.container,{api});
 return {...page,container:page.container,calls,cleanup};
}

async function settleStartup(harness,tasks,total=tasks.length){
 const settings=harness.calls.find(call=>call.path==='/api/settings');
 const recent=harness.calls.find(call=>call.path.startsWith('/api/research?limit='));
 settings?.resolve({});
 recent?.resolve({tasks,total});
 await settle();
}

test('an in-flight list response predating a delete cannot resurrect the deleted research',async()=>{
 const harness=agentHarness();
 await settleStartup(harness,[researchTask('a','旧甲'),researchTask('b','旧乙')],5);
 dispatch(harness.container,'[data-agent-action="recent-more"]');
 const stale=harness.calls.find(call=>call.path==='/api/research?limit=100');
 assert.ok(stale,'expanded list request should be pending');

 dispatch(harness.container,'[data-agent-delete="a"]');
 dispatch(harness.container,'[data-agent-delete="a"]');
 const deletion=harness.calls.find(call=>call.path==='/api/research/delete');
 assert.ok(deletion,'confirming should start the delete request');
 assert.equal(deletion.options.body.research_id,'a');
 assert.equal(stale.options.signal.aborted,true,'starting a delete invalidates the older list request');

 const otherDelete=harness.container.querySelector('[data-agent-delete="b"]');
 assert.equal(otherDelete.disabled,true,'other deletes stay disabled while one delete is in flight');
 dispatch(harness.container,'[data-agent-delete="b"]');
 assert.equal(harness.calls.filter(call=>call.path==='/api/research/delete').length,1,'a second delete cannot start mid-flight');
 assert.equal(harness.container.querySelector('.ya-recent-confirm')?.querySelector('[data-agent-delete]')?.dataset.agentDelete,'a','pendingDelete cannot drift to another task');

 deletion.resolve({ok:true});
 await settle();
 const fresh=harness.calls.filter(call=>call.path==='/api/research?limit=100').at(-1);
 assert.notEqual(fresh,stale);
 fresh.resolve({tasks:[researchTask('b','旧乙')],total:4});
 await settle();
 stale.resolve({tasks:[researchTask('a','旧甲'),researchTask('b','旧乙')],total:5});
 await settle();
 assert.equal(harness.container.querySelector('[data-agent-task="a"]'),null,'late old list response must be discarded');
 assert.ok(harness.container.querySelector('[data-agent-task="b"]'));
 harness.cleanup();
});

test('deleting the last research falls back to the research input instead of body',async()=>{
 const harness=agentHarness();
 await settleStartup(harness,[researchTask('a','唯一研究')],1);
 dispatch(harness.container,'[data-agent-delete="a"]');
 dispatch(harness.container,'[data-agent-delete="a"]');
 const deletion=harness.calls.find(call=>call.path==='/api/research/delete');
 deletion.resolve({ok:true});
 await settle();
 const empty=harness.calls.filter(call=>call.path.startsWith('/api/research?limit=')).at(-1);
 empty.resolve({tasks:[],total:0});
 await settle();
 assert.equal(harness.container.querySelector('[data-agent-task]'),null);
 assert.equal(harness.document.activeElement?.id,'ya-query','successful last delete focuses the stable research input');
 assert.equal(harness.container.querySelector('[data-agent-action="recent"]'),null);
 harness.cleanup();
});

test('delete failure keeps the confirmation retryable and never focuses a disabled control',async()=>{
 const harness=agentHarness();
 await settleStartup(harness,[researchTask('a','研究甲')],1);
 dispatch(harness.container,'[data-agent-delete="a"]');
 dispatch(harness.container,'[data-agent-delete="a"]');
 const deletion=harness.calls.find(call=>call.path==='/api/research/delete');
 deletion.reject(new Error('本机资料服务连接中断'));
 await settle();
 const confirm=harness.container.querySelector(`[data-agent-delete="a"]`);
 assert.ok(confirm,'failed delete must keep the same task in confirmation state');
 assert.match(harness.container.innerHTML,/本机资料服务连接中断/);
 assert.equal(harness.document.activeElement,confirm,'failure focus lands on the retryable confirmation');
 assert.equal(confirm.disabled,false);
 harness.cleanup();
});

test('cancelling a delete returns focus to that row delete, not another control',async()=>{
 const harness=agentHarness();
 await settleStartup(harness,[researchTask('a','研究甲'),researchTask('b','研究乙')],2);
 const first=dispatch(harness.container,'[data-agent-delete="a"]');
 assert.equal(harness.document.activeElement?.dataset?.agentDelete,'a','entering confirmation focuses the confirm control');
 dispatch(harness.container,'[data-agent-action="cancel-delete"]');
 const restored=harness.container.querySelector(`[data-agent-delete="a"]`);
 assert.equal(harness.document.activeElement,restored);
 assert.equal(restored.disabled,false);
 assert.ok(!harness.calls.some(call=>call.path==='/api/research/delete'),'cancel never issues a delete');
 harness.cleanup();
});

test('info dialog keeps internal focus through content swaps and restores the first external trigger',async()=>{
 const page=createPage('#/');
 const dialog=page.document.createElement('dialog');
 dialog.setAttribute('id','info-dialog');
 page.document.body.append(dialog);
 const trigger=page.document.createElement('button');
 trigger.setAttribute('id','about-trigger');
 page.document.body.append(trigger);
 trigger.focus();

 openDialog(page.document,'info-dialog','<div class="info-heading"><button type="button" data-action="close-info">关闭</button></div><div class="info-content"><button type="button" data-action="reset-profile">清除本机条件与收藏</button></div>','#about-trigger');
 assert.equal(dialog.open,true);
 assert.equal(dialog.contains(page.document.activeElement),true,'opening replaces BODY focus with a control inside the dialog');

 openDialog(page.document,'info-dialog','<div class="info-heading"><button type="button" data-action="close-info">关闭</button></div><div class="info-danger"><button type="button" data-action="reset-profile-confirm">确认清除</button><button type="button" data-action="reset-profile-cancel">取消</button></div>','#about-trigger','[data-action="reset-profile-confirm"]');
 assert.equal(page.document.activeElement?.dataset?.action,'reset-profile-confirm','entering the clear confirmation moves focus onto the confirm control');

 openDialog(page.document,'info-dialog','<div class="info-heading"><button type="button" data-action="close-info">关闭</button></div><div class="info-content"><button type="button" data-action="reset-profile">清除本机条件与收藏</button></div>','#about-trigger','[data-action="reset-profile"]');
 assert.equal(page.document.activeElement?.dataset?.action,'reset-profile','cancel returns focus to the internal entry');

 closeDialog(page.document,'info-dialog');
 assert.equal(dialog.open,false);
 assert.equal(page.document.activeElement,trigger,'final close restores the external trigger captured on first open');
});
