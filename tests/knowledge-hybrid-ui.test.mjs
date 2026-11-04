import test from 'node:test';
import assert from 'node:assert/strict';
import {renderKnowledge} from '../static/knowledge.mjs';

const state={loadStatus:'ready',entries:[],total:0,page:1,pageSize:12,summary:{total:0,quality_counts:{accepted:0,pending:0,excluded:0}},filters:{q:'计算机',status:'',year:'',unit_id:'',quality:'accepted'},facets:{units:[],years:[]},hybrid:{status:'idle'}};
const segment={id:'sg_'+'a'.repeat(32),entry_id:'k_'+'b'.repeat(24),text:'081200 计算机科学与技术 初试科目101、201、301、408。',page_start:2,retrieval_methods:['keyword','semantic'],rerank:{version:'rule_v1'},source:{unit_id:'u',unit_name:'样本大学',year:2027,title:'2027 招生目录'}};
const result={segments:[segment],returned_count:1,candidate_count:2,usage:{embedding_calls:1,successful_embedding_calls:1,input_tokens:12},usage_known:true,retrieval:{mode:'hybrid',degraded:false,reranker:'rule_v1',notice:'语义相近不等于报考条件相同。',latency:{total_ms:42}}};

test('the library keeps keyword paging and offers an explicit separate hybrid submission',()=>{
 const html=renderKnowledge(state);
 assert.match(html,/id="yk-search-form"/);assert.match(html,/data-knowledge-action="hybrid-search"/);assert.match(html,/混合检索/);
 assert.match(html,/“搜索”只读关键词列表/);assert.match(html,/主动提交/);
 assert.ok(!html.includes('yk-hybrid-results'),'idle state renders no result panel and performs no implicit work');
});

test('hybrid results report finite counts, rule rerank, usage and an N3 reader link',()=>{
 const html=renderKnowledge({...state,hybrid:{status:'ready',result}});
 assert.match(html,/返回 1 条/);assert.match(html,/有限候选 2 条/);assert.match(html,/关键词与语义同时命中/);
 assert.match(html,/向量调用 1 次（成功 1）/);assert.match(html,/输入 12 token/);assert.match(html,/总耗时 42 ms/);
 assert.match(html,/data-knowledge-open="k_b{24}"/);assert.match(html,/data-knowledge-hybrid-segment="sg_a{32}"/);
 assert.match(html,/打开 N3 精读原文/);assert.match(html,/规则重排 rule_v1/);assert.match(html,/不代表全国/);
});

test('keyword degradation is named honestly and unknown usage never becomes zero',()=>{
 const html=renderKnowledge({...state,hybrid:{status:'ready',result:{...result,usage:{embedding_calls:1,successful_embedding_calls:0,input_tokens:null},usage_known:false,retrieval:{...result.retrieval,mode:'keyword',degraded:true,degraded_reason:'vector_not_ready'}}}});
 assert.match(html,/关键词降级/);assert.match(html,/语义通道未参与：语义索引尚未建立/);assert.match(html,/输入 token 未知/);assert.match(html,/向量调用 1 次（成功 0）/);
 assert.ok(!html.includes('输入 0 token'));
});

test('empty and error states preserve the finite-scope boundary instead of claiming nothing exists',()=>{
 const empty=renderKnowledge({...state,hybrid:{status:'ready',result:{...result,segments:[],returned_count:0,candidate_count:0}}});
 assert.match(empty,/当前范围内没有可核验的匹配片段/);assert.match(empty,/不代表全国所有学校都没有相关资料/);
 const failed=renderKnowledge({...state,hybrid:{status:'error',error:'<script>bad()</script>'}});
 assert.match(failed,/检索未完成/);assert.match(failed,/data-knowledge-action="hybrid-retry"/);assert.ok(!failed.includes('<script>bad()</script>'));
});

test('untrusted result titles, names and text are escaped in the hybrid list',()=>{
 const hostile={...segment,text:'<img src=x onerror=alert(1)>',source:{...segment.source,unit_name:'<b>bad</b>',title:'<script>bad()</script>'}};
 const html=renderKnowledge({...state,hybrid:{status:'ready',result:{...result,segments:[hostile]}}});
 assert.ok(!html.includes('<img src=x'));assert.ok(!html.includes('<script>bad()</script>'));assert.match(html,/&lt;img src=x/);
});
