import test from 'node:test';
import assert from 'node:assert/strict';
import {renderAgent} from '../static/agent.mjs';

const entryId='k_'+'a'.repeat(24),segmentId='sg_'+'b'.repeat(32);
const ref={entry_id:entryId,segment_id:segmentId,quote:'本专业学费每年12000元，学制2.5年。',url:'https://example.edu.cn/fees'};
const program={id:'tp_fixture',candidate_origin:'official_original',unit_id:'u_fixture',unit_name:'[夹具] 样本大学',name:'计算机',eligibility_evidence_complete:false,subjects:[{code:'408'}]};
const render=(report,tab='overview')=>renderAgent({task:{id:'r_fixture',status:'completed',query:'[夹具] 报考依据',events:[],report},tab});
const fact=(field,value,state='verified_single_source',extra={})=>({id:'af_'+field,program_id:program.id,field,value,state,evidence_refs:[ref],...extra});

test('official discovery keeps entry, target document and body stages distinct in one collapsed disclosure',()=>{
 const html=render({official_discovery:[
  {unit_name:'[夹具] 入口大学',stage:'entry_verified',entry_verified:true},
  {unit_name:'[夹具] 目录大学',stage:'document_found',entry_verified:true},
  {unit_name:'[夹具] 原文大学',stage:'body_read',entry_verified:true},
  {unit_name:'[夹具] 受限大学',stage:'access_restricted',entry_verified:true},
 ]});
 assert.match(html,/<details class="ya-official-discovery"[^>]*>/);assert.ok(!/<details class="ya-official-discovery"[^>]*\sopen/.test(html));
 assert.match(html,/官网已定位/);assert.match(html,/目标年资料已找到/);assert.match(html,/正文待补/);assert.match(html,/正文已读取/);assert.match(html,/访问受限/);assert.match(html,/继续查找目标年/);
 assert.ok(!html.includes('找不到官网'));assert.ok(!html.includes('已确认报考资格'));
});

test('source roles are explicit and account or unverified links are never presented as a verified website',()=>{
 const html=render({official_discovery:[{unit_name:'[夹具] 学校',stage:'verification_required',entry_sources:[{title:'学校入口',url:'https://example.edu.cn'}],document_sources:[{title:'招生章程',url:'https://example.edu.cn/rules'}],candidate_sources:[{title:'官方账号',url:'https://mp.weixin.qq.com/s/fixture',source_type:'official_account'},{title:'<img src=x>',url:'javascript:alert(1)'}]}]},'sources');
 assert.match(html,/官网入口/);assert.match(html,/招生文档/);assert.match(html,/官方账号线索/);assert.match(html,/归属待核/);assert.ok(!html.includes('href="javascript:'));assert.ok(!html.includes('<img src=x>'));
});

test('an archived official account alone is labelled separately from an independent school website',()=>{
 const html=render({official_discovery:[{unit_name:'[夹具] 学校',stage:'entry_verified',entry_sources:[
  {title:'学校官方发布账号',url:'https://yz.chsi.com.cn/sch/viewZszc--schId-123.dhtml',source_role:'official_account'}
 ]}]});
 assert.match(html,/官方发布账号已定位/);assert.match(html,/继续核对独立官网/);
 assert.ok(!html.includes('官网已定位'));
});

test('verified tuition and fractional duration retain fee period and exact proof without implying complete eligibility',()=>{
 const html=render({candidates:[program],admission_facts:{facts:[fact('tuition',12000,'verified_single_source',{period:'year'}),fact('duration_years',2.5),fact('eligibility','具有国家承认的本科学历','verified_single_source',{conditions:[{field:'education',allowed:['bachelor']}],missing:['专业背景条件尚待核对']})]}});
 assert.match(html,/12000 元 \/ 年/);assert.match(html,/2\.5 年/);assert.match(html,/单一官方原文已核对/);assert.match(html,/专业背景条件尚待核对/);assert.match(html,/资格待核/);
 assert.match(html,new RegExp('data-agent-knowledge="'+entryId+'"'));assert.match(html,new RegExp('data-agent-knowledge-segment="'+segmentId+'"'));
 assert.ok(!html.includes('完全符合报考资格'));assert.ok(!html.includes('[object Object]'));
});

test('conflicted, stale and pending values remain visibly suspended and never appear as usable fee or duration facts',()=>{
 const html=render({candidates:[program],admission_facts:{facts:[fact('tuition',99999,'conflict',{period:'year'}),fact('duration_years',9,'stale'),fact('tuition',77777,'needs_review',{period:'total'})]}},'methods');
 assert.match(html,/来源冲突，暂停使用/);assert.match(html,/依据已失效/);assert.match(html,/待核对/);assert.ok(!html.includes('99999 元'));assert.ok(!html.includes('77777 元'));assert.ok(!html.includes('9 年'));
});

test('missing fields stay unknown and fact quotes, conditions and notices are escaped',()=>{
 const html=render({candidates:[program],admission_facts:{facts:[fact('duration_years',null),fact('eligibility','<script>bad()</script>','needs_review',{notice:'<img src=x>',missing:['<b>专业背景待核</b>'],evidence_refs:[{...ref,quote:'<svg onload=bad()>',url:'https://name:secret@example.edu.cn'}]})]}});
 assert.match(html,/学制待核对/);assert.ok(!html.includes('0 年'));assert.ok(!html.includes('<script>bad()'));assert.ok(!html.includes('<img src=x>'));assert.ok(!html.includes('<svg onload=bad()>'));assert.ok(!html.includes('href="https://name:secret'));
});

test('facts remain scoped to their program and formal candidates also expose admitted fields',()=>{
 const formal={...program,id:'formal_fixture',candidate_origin:undefined};
 const html=render({candidates:[formal],admission_facts:{facts:[fact('tuition',12000,'corroborated',{program_id:formal.id,period:'total'}),fact('tuition',88888,'corroborated',{program_id:'other',period:'year'})]}});
 assert.match(html,/12000 元 \/ 全程/);assert.match(html,/多份官方原文一致/);assert.match(html,/科目与字段/);assert.ok(!html.includes('88888'));
});

test('backend eligibility arrays display only human labels and complex clauses keep their partial scope',()=>{
 const html=render({candidates:[program],admission_facts:{facts:[fact('eligibility',[
  {field:'education',op:'in',value:['bachelor','master'],label:'具有本科学历或硕士学历'},
  {field:'health',op:'eq',value:true,label:'<b>身体健康</b>'},
 ],'verified_single_source',{missing:['工作经验条件待核对'],eligibility_evidence_complete:false})]}});
 assert.match(html,/具有本科学历或硕士学历/);assert.match(html,/&lt;b&gt;身体健康&lt;\/b&gt;/);assert.match(html,/工作经验条件待核对/);assert.ok(!html.includes('[object Object]'));assert.ok(!html.includes('<b>身体健康</b>'));
 const complex=render({candidates:[program],admission_facts:{facts:[fact('eligibility',{all:[{field:'education',op:'in',value:['bachelor']}]})]}});
 assert.match(complex,/已核对部分资格条款，查看原文/);assert.match(complex,/资格仍需结合全部条款与个人条件核对/);
});

test('partial eligibility and object tuition follow the integrated backend contract without promoting coverage',()=>{
 const html=render({candidates:[program],admission_facts:{facts:[fact('tuition',{amount:12000,period:'year'},'verified_single_source',{period:'year'}),fact('eligibility',[{field:'education',op:'in',value:['bachelor'],label:'具有国家承认的本科学历'}],'partial',{missing:['身体健康要求尚不可由现有档案判断']})]}});
 assert.match(html,/12000 元 \/ 年/);assert.match(html,/具有国家承认的本科学历/);assert.match(html,/部分条款已核对，资格仍待确认/);assert.match(html,/身体健康要求尚不可由现有档案判断/);assert.ok(!html.includes('[object Object]'));
});

test('machine missing codes become human explanations while clause text remains escaped',()=>{
 const html=render({candidates:[program],admission_facts:{facts:[fact('eligibility',[],'partial',{missing:['eligibility_scope_incomplete','program_conditions_unresolved','unsupported_condition:<b>健康要求</b>','unexpected_backend_code']})]}});
 assert.match(html,/完整报考资格范围尚待核对/);assert.match(html,/目录仍有未解析的专业条件/);assert.match(html,/仍有暂不能计算的资格条款：&lt;b&gt;健康要求&lt;\/b&gt;/);assert.match(html,/仍有报考条款待核对/);assert.ok(!html.includes('unexpected_backend_code'));assert.ok(!html.includes('<b>健康要求</b>'));
});
