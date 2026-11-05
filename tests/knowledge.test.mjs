import test from 'node:test';
import assert from 'node:assert/strict';
import {renderKnowledge,knowledgeLocation} from '../static/knowledge.mjs';

const entry={id:'k_one',title:'2027 年计算机招生目录',unit_id:'u',unit_name:'样本大学',year:2027,status:'official_original_read',url:'https://grad.example.edu.cn/catalog.pdf',excerpt:'这是学校原文中的摘录。',text:'这是学校原文中的完整提取内容。',original_available:true,field_verification:'not_structured',sha256:'a'.repeat(64),provenance:['r_one','r_two'],version_count:2,first_seen_at:'2026-11-02T00:00:00Z',last_seen_at:'2026-11-02T01:00:00Z'};
const state={loadStatus:'ready',entries:[entry],total:1,page:1,pageSize:12,summary:{total:1,search_lead:0,official_original_read:1,published_snapshot:0},filters:{q:'',status:'',year:'',unit_id:''},facets:{units:[{unit_id:'u',unit_name:'样本大学'}],years:[2027]}};

test('library distinguishes saved originals, search leads and published material without claiming verified fields',()=>{
 const html=renderKnowledge({...state,entries:[entry,{...entry,id:'k_two',status:'search_lead',year:null,requested_year:2028},{...entry,id:'k_three',status:'published_snapshot',original_available:false}]});
 assert.match(html,/研究知识库/);assert.match(html,/原文留档/);assert.match(html,/搜索线索/);assert.match(html,/已收录资料/);
 assert.match(html,/年度待核验/);assert.match(html,/检索目标 2028/);assert.match(html,/不等于字段已核验/);
 assert.ok(!html.includes('全国知识已齐备'));assert.ok(!html.includes('已确认可报考'));
});

test('search, school, year and source status filters preserve the current query and selections',()=>{
 const html=renderKnowledge({...state,filters:{q:'计算机',unit_id:'u',year:'2027',status:'official_original_read'}});
 assert.match(html,/id="yk-query"[^>]*value="计算机"/);
 assert.match(html,/<option value="u" selected>样本大学<\/option>/);
 assert.match(html,/<option value="2027" selected>2027 年<\/option>/);
 assert.match(html,/<option value="official_original_read" selected>/);
});

test('detail shows exact source text and provenance while preserving original dates and field limits',()=>{
 const html=renderKnowledge({...state,detailStatus:'ready',detail:entry,detailId:entry.id});
 assert.match(html,/这是学校原文中的完整提取内容/);assert.match(html,/href="https:\/\/grad.example.edu.cn\/catalog.pdf"/);
 assert.match(html,/2 次研究/);assert.match(html,/2 个版本/);assert.match(html,/SHA-256/);assert.match(html,/2027 年/);
 assert.match(html,/字段仍需逐项核验/);assert.match(html,/返回资料列表/);
});

test('missing or changed snapshots never display their stale body as dependable original text',()=>{
 const html=renderKnowledge({...state,detailStatus:'ready',detail:{...entry,original_available:false,snapshot_status:'missing_or_changed'},detailId:entry.id});
 assert.match(html,/原文留档需要重新核对/);
 assert.ok(!html.includes(entry.text));assert.ok(!html.includes(entry.excerpt));
});

test('lead summaries and published excerpts are never labelled as freshly read official originals',()=>{
 const lead=renderKnowledge({...state,detailStatus:'ready',detail:{...entry,status:'search_lead',original_available:false},detailId:entry.id});
 assert.match(lead,/搜索摘要/);assert.ok(!lead.includes('class="yk-reader-text"'));
 const published=renderKnowledge({...state,detailStatus:'ready',detail:{...entry,status:'published_snapshot',original_available:false},detailId:entry.id});
 assert.match(published,/已收录资料摘录/);assert.ok(!published.includes('本次联网核验成功'));
});

test('external titles, text, query and URLs cannot create executable content',()=>{
 const unsafe={...entry,title:'<img src=x onerror=alert(1)>',url:'javascript:alert(1)',text:'<script>bad()</script>',unit_name:'<svg onload=bad()>',provenance:['<r>']};
 const html=renderKnowledge({...state,entries:[unsafe],filters:{q:'"><img src=x>',status:'',year:'',unit_id:''},detailStatus:'ready',detail:unsafe,detailId:entry.id});
 assert.match(html,/&lt;img/);assert.match(html,/&lt;script&gt;/);assert.ok(!html.includes('<script>'));assert.ok(!html.includes('href="javascript:'));assert.ok(!html.includes('<svg onload'));
});

test('retained page excerpts keep exact page numbers and never promote lead text to official originals',()=>{
 const html=renderKnowledge({...state,detailStatus:'ready',detail:{...entry,pages:[{page:3,text:'第3页的精确原文 <script>test</script>'}]},detailId:entry.id});
 assert.match(html,/第 3 页/);assert.match(html,/第3页的精确原文 &lt;script&gt;test&lt;\/script&gt;/);
 const lead=renderKnowledge({...state,detailStatus:'ready',detail:{...entry,status:'search_lead',original_available:false},detailId:entry.id});
 assert.match(lead,/打开来源网页/);assert.ok(!lead.includes('>查看官方来源 '));
});

test('metadata-only notices are explicit and suppress personal bodies or page excerpts even if mistakenly returned',()=>{
 const notice='材料可能包含逐人录取信息，仅保留来源和版本，不索引个人名单。';
 const html=renderKnowledge({...state,entries:[{...entry,text_origin:'metadata_only',excerpt:'不应公开的个人名单摘录'}],detailStatus:'ready',detail:{...entry,text_origin:'metadata_only',content_notice:notice,text:'不应公开的个人名单正文',pages:[{page:1,text:'不应公开的姓名考号'}]},detailId:entry.id});
 assert.match(html,/仅保留来源信息/);assert.match(html,/不索引个人名单/);
 for(const secret of ['不应公开的个人名单摘录','不应公开的个人名单正文','不应公开的姓名考号'])assert.ok(!html.includes(secret));
 assert.ok(!html.includes('查看页内摘录'));assert.ok(!html.includes('尚未保存可展示的正文'));
});

test('default original rows stay compact while search results and the reader keep the saved text',()=>{
 const original={...entry,excerpt:'官网导航 新闻中心 招生信息 计算机相关资料',text:'完整原文保持不变，仍可从详情阅读。'};
 const browsing=renderKnowledge({...state,entries:[original]});
 assert.match(browsing,/原文已留档，打开详情查看摘录与来源/);assert.ok(!browsing.includes(original.excerpt));
 const searching=renderKnowledge({...state,entries:[original],filters:{...state.filters,q:'计算机'}});
 assert.match(searching,/官网导航 新闻中心 招生信息 计算机相关资料/);
 const reading=renderKnowledge({...state,entries:[original],detailStatus:'ready',detail:original,detailId:original.id});
 assert.match(reading,/完整原文保持不变，仍可从详情阅读/);
});

test('loading, recoverable failures, empty results and pagination have explicit states',()=>{
 assert.match(renderKnowledge({loadStatus:'loading'}),/正在读取知识库/);
 assert.match(renderKnowledge({...state,loadStatus:'error',error:'本地服务暂不可用'}),/重新读取/);
 assert.match(renderKnowledge({...state,entries:[],total:0}),/还没有可复用资料/);
 const pages=renderKnowledge({...state,total:25,page:2});
 assert.match(pages,/第 2 \/ 3 页/);assert.match(pages,/data-knowledge-action="previous"/);assert.match(pages,/data-knowledge-action="next"/);
 const unknown=renderKnowledge({...state,summary:{}}).match(/<div class="yk-quality-tabs"[\s\S]*?<\/div>/)?.[0]||'';
 assert.match(unknown,/<strong>—<\/strong>/);assert.ok(!unknown.includes('<strong>0</strong>'));
});

test('quality admission is an independent default section with honest counts and an explicit pending section',()=>{
 const html=renderKnowledge({...state,summary:{...state.summary,quality_counts:{accepted:1,pending:3,excluded:2}}});
 assert.match(html,/data-knowledge-quality="accepted"[^>]*aria-selected="true"/);
 assert.match(html,/data-knowledge-quality="pending"/);assert.match(html,/data-knowledge-quality="excluded"/);
 assert.match(html,/可复用资料/);assert.match(html,/待核线索/);assert.match(html,/已拦截/);assert.match(html,/全库统计/);
 assert.match(html,/name="status"/);
 const accepted=renderKnowledge({...state,entries:[{...entry,quality:{version:1,state:'accepted',reusable:true,material_type:'catalog',reasons:[]}}]});
 assert.match(accepted,/yk-quality-badge is-accepted/);
});

test('unclassified or internally inconsistent quality never displays an accepted entry badge',()=>{
 for(const quality of [undefined,{version:9,state:'accepted',reusable:true},{version:1,state:'accepted',reusable:false}]){
  const html=renderKnowledge({...state,entries:[{...entry,quality}],filters:{...state.filters,quality:'all'}});
  assert.match(html,/data-quality-state="pending"/);assert.ok(!html.includes('yk-quality-badge is-accepted'));
 }
});

test('an empty accepted section never claims the whole library is empty',()=>{
 const html=renderKnowledge({...state,entries:[],total:0,filters:{...state.filters,quality:'accepted'},summary:{...state.summary,total:5,quality_counts:{accepted:0,pending:4,excluded:1}}});
 assert.match(html,/还没有可复用资料/);assert.match(html,/查看待核线索/);assert.ok(!html.includes('还没有保存相关资料'));
});

test('quality reasons are expandable in both rows and reader and escape untrusted messages',()=>{
 const quality={version:1,state:'pending',reusable:false,material_type:'catalog',reasons:[{code:'unknown_year',message:'来源年度仍待核验 <img src=x>'}]};
 const html=renderKnowledge({...state,entries:[{...entry,quality}],detailStatus:'ready',detail:{...entry,quality},detailId:entry.id,filters:{...state.filters,quality:'pending'}});
 assert.match(html,/yk-entry-quality/);assert.match(html,/yk-admission-details/);assert.match(html,/来源年度仍待核验 &lt;img/);
 assert.match(html,/暂不供研究自动复用/);assert.ok(!html.includes('<img src=x>'));
});

test('excluded quality hides all garbage or personal text while preserving source metadata and reasons',()=>{
 const quality={version:1,state:'excluded',reusable:false,material_type:'navigation',reasons:[{code:'navigation_only',message:'正文主要是导航片段，已拦截。'}]};
 const blocked={...entry,quality,excerpt:'GARBAGE_EXCERPT_CANARY',text:'PERSONAL_OR_GARBAGE_BODY_CANARY',pages:[{page:1,text:'PRIVATE_PAGE_CANARY'}]};
 const html=renderKnowledge({...state,entries:[blocked],detailStatus:'ready',detail:blocked,detailId:entry.id,filters:{...state.filters,quality:'excluded'}});
 for(const value of [blocked.excerpt,blocked.text,blocked.pages[0].text])assert.ok(!html.includes(value));
 assert.match(html,/正文主要是导航片段，已拦截/);assert.match(html,/href="https:\/\/grad.example.edu.cn\/catalog.pdf"/);assert.match(html,/仅保留来源信息/);
 assert.ok(!html.includes('查看页内摘录'));
});

test('knowledge location keeps filters, page and reader in the shareable hash while omitting defaults',()=>{
 assert.equal(knowledgeLocation({q:'',status:'',year:'',unit_id:'',quality:'accepted'},1,''),'#/knowledge');
 assert.equal(knowledgeLocation({q:' 408 ',status:'official_original_read',year:'2027',unit_id:'u',quality:'pending'},3,'k_one'),'#/knowledge?q=408&status=official_original_read&year=2027&unit_id=u&quality=pending&page=3&entry_id=k_one');
 assert.equal(knowledgeLocation({q:'计算机',quality:'all'},1,''),'#/knowledge?q=%E8%AE%A1%E7%AE%97%E6%9C%BA&quality=all');
});
