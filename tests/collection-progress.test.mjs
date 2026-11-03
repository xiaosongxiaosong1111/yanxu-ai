import test from 'node:test';
import assert from 'node:assert/strict';
import {renderCollectionUnits,renderCollectionSummary} from '../static/collection-progress.mjs';

test('national progress separates index, annual confirmation and catalog completeness',()=>{
 const html=renderCollectionSummary({indexed_units:936,annual_confirmed_units:300,annual_official_total:null,units_with_programs:23,program_count:1822,recorded_verified_units:5,catalog_states:{not_collected:913}});
 assert.match(html,/936/);assert.match(html,/300/);assert.match(html,/年度招生单位全集尚未核准/);assert.match(html,/913/);
 assert.doesNotMatch(html,/全国目录已完整/);
});

test('missing schools retain reason, original links and remaining pages without inventing eligibility',()=>{
 const html=renderCollectionUnits([{unit_id:'u',unit_name:'<甲大学>',province:'天津',catalog_status:'not_collected',collection_status:'truncated',program_count:0,pending_pages:7,missing_reason:'已保存官方原文，尚待解析',official_url:'https://a.edu.cn/',sources:[{url:'https://a.edu.cn/catalog.pdf',title:'2027专业目录',kind:'catalog',year:2027}]}]);
 assert.match(html,/&lt;甲大学&gt;/);assert.match(html,/尚待解析/);assert.match(html,/7/);assert.match(html,/href="https:\/\/a.edu.cn\/catalog.pdf"/);assert.doesNotMatch(html,/不可报考/);
 assert.doesNotMatch(renderCollectionUnits([{unit_id:'x',unit_name:'乙',sources:[{url:'javascript:alert(1)',title:'bad'}]}]),/href="javascript/);
});

test('unit rows distinguish parsing readiness, format gaps and source verification failures from publication',()=>{
 for(const [status,label] of [['pending','尚待解析'],['ready_for_ingestion','解析候选待核验'],['needs_adapter','原文格式待适配'],['source_check_failed','源校验失败'],['no_catalog_rows','本批原文未提取目录条目']]){
  const html=renderCollectionUnits([{unit_id:'u',unit_name:'样本大学',catalog_status:'not_collected',parsing_status:status,program_count:0}]);
  assert.ok(html.includes(label),status);assert.match(html,/解析候选须通过原文与语义核验后入库/);assert.match(html,/目录尚未入库/);assert.doesNotMatch(html,/查看已收录专业|目录已完整|官方未公开/);
 }
});

test('mixed original processing counts keep ready files separate from failures and non-catalog documents',()=>{
 const html=renderCollectionUnits([{unit_id:'u',unit_name:'样本大学',parsing_status:'needs_adapter',parsing_summary:{ready:2,needs_adapter:3,failure:1,not_catalog:4,restricted:1}}]);
 assert.match(html,/解析候选待核验 2 份/);assert.match(html,/待适配 3 份/);assert.match(html,/校验或解析失败 1 份/);assert.match(html,/未提取目录条目 4 份/);assert.match(html,/访问受限 1 份/);
});

test('expandable parser gaps show the exact original links and escaped reasons without asserting missing official publication',()=>{
 const html=renderCollectionUnits([{unit_id:'u',unit_name:'样本大学',parsing_status:'needs_adapter',parsing_gaps:[
  {url:'https://sample.edu.cn/catalog.pdf',status:'needs_adapter',parser:'pdf_catalog',reason:'跨页表格的院系单元格需要适配'},
  {url:'https://sample.edu.cn/charter.html',status:'not_catalog',parser:'html',reason:'原文是招生章程，没有专业表格'},
  {url:'javascript:alert(1)',status:'failure',reason:'<img src=x onerror=alert(1)>'}
 ]}]);
 assert.match(html,/查看原文处理缺口（3）/);assert.match(html,/href="https:\/\/sample.edu.cn\/catalog.pdf"/);assert.match(html,/跨页表格的院系单元格需要适配/);assert.match(html,/原文是招生章程，没有专业表格/);assert.match(html,/未提取条目不代表没有招生目录/);
 assert.match(html,/&lt;img/);assert.doesNotMatch(html,/href="javascript:|<img/);assert.match(html,/原文链接待核验/);
});

test('per-unit parser detail remains bounded and keeps the verified annual denominator and not-listed explanation',()=>{
 const summary=renderCollectionSummary({indexed_units:936,annual_confirmed_units:931,annual_official_total:931,annual_scope_status:'verified',annual_units_without_programs:900,catalog_states:{not_collected:905}});
 assert.match(summary,/900/);assert.doesNotMatch(summary,/905/);
 const html=renderCollectionUnits([{unit_id:'u',unit_name:'保留索引学校',year:2027,annual_status:'not_listed',parsing_status:'pending',parsing_gaps:Array.from({length:15},(_,index)=>({url:`https://sample.edu.cn/${index}.pdf`,status:'not_catalog',reason:'原文不是专业目录'}))}]);
 assert.match(html,/2027 年公开目录未列为独立招生单位/);assert.match(html,/不据此推定该校停止招生/);assert.match(html,/查看原文处理缺口（12）/);assert.doesNotMatch(html,/href="https:\/\/sample.edu.cn\/12.pdf"/);
});
