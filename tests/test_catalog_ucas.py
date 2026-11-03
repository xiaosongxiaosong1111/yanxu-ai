import hashlib
import tempfile
import unittest
import weakref
from pathlib import Path

from collectors.catalog_ucas import parse_ucas_public,ucas_public_scope


class UcasPublicTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.docs={}
        self.unit={'id':'367929','unit_code':'14430','name':'中国科学院大学','province':'北京','official_url':'https://admission.ucas.ac.cn/'}
        self.base='https://admission.ucas.ac.cn';self.route='/info/ZhaoshengDanwei/9e780c52-baf5-4020-b453-bc4510579559'
        self.leaf_route=self.route.replace('/ZhaoshengDanwei/','/ZhaoshengDanweiDetail/')+'/8000212027'
        self.index_html='<h1>中国科学院大学招生信息网</h1><li class="erji_list">招生单位 - 2027</li><table><tr><td>单位代码</td><td>单位名称</td><td>单位地址</td><td>邮政编码</td><td>联系电话</td><td>联系人</td></tr><tr><td>80002</td><td><a href="'+self.leaf_route+'">数学与系统科学研究院</a></td><td>中关村东路55号</td><td>100190</td><td>010-00000000</td><td>招生办</td></tr></table><div class="pager"><span class="current">1</span></div>'
        self.index=self.save('index',self.index_html,self.base+self.route,kind='catalog_context')
        self.exams='①101思想政治理论②201英语（一）或203日语③301数学（一）④801高等代数'
        self.html='<h1>中国科学院大学招生信息网</h1><h2>数学与系统科学研究院</h2><table><tr><td>单位代码</td><td>80002</td><td>目录类别</td><td>硕士</td></tr></table><table id="detail"><tr><td>学科、专业名称（代码）研究方向</td><td>指导教师</td><td>预计招生</td><td>考试科目</td><td>备注</td></tr><tr><td>070101 基础数学</td><td></td><td>5</td><td></td><td></td></tr><tr><td>01 (全日制)代数理论1</td><td>张老师</td><td></td><td>'+self.exams+'</td><td>只招硕博连读</td></tr><tr><td>01 (全日制)代数理论1</td><td>李老师</td><td></td><td>同上</td><td>同等学力考生须加试</td></tr><tr><td>02 (非全日制)应用数学</td><td>王老师</td><td></td><td>同上</td><td>只招专业学位硕士研究生</td></tr></table>'
        self.leaf=self.save('leaf',self.html,self.base+self.leaf_route)
        self.leaf={**self.leaf,'catalog_parser':'ucas_public_v1','public_form_profile':'ucas','public_form_context':{
            'profile':'ucas','entry_document_id':'index','listing_document_id':'index','index_document_ids':['index']}}
        self.docs['leaf']=self.leaf

    def save(self,identity,text,url,kind='catalog'):
        path=self.root/'data/snapshots'/f'{identity}.html';path.parent.mkdir(parents=True,exist_ok=True);raw=text.encode();path.write_bytes(raw)
        doc={'id':identity,'unit_id':self.unit['id'],'year':2027,'kind':kind,'url':url,'local_path':path.relative_to(self.root).as_posix(),
             'sha256':hashlib.sha256(raw).hexdigest(),'request_method':'GET','status':'parsed'}
        self.docs[identity]=doc;return doc

    def parse(self,html=None,document=None):return parse_ucas_public(html or self.html,self.unit,2027,document or self.leaf,project_root=self.root,documents=self.docs)

    def replace(self,html):
        doc=self.save('leaf',html,self.leaf['url']);self.leaf={**doc,**{k:self.leaf[k] for k in ['catalog_parser','public_form_profile','public_form_context']}}
        self.docs['leaf']=self.leaf;self.html=html

    def test_five_digit_training_unit_and_deduplicated_advisors_keep_all_real_notes(self):
        result=self.parse();self.assertEqual(result['expected_records'],2);self.assertEqual(result['issues'],[])
        first=result['programs'][0]
        self.assertEqual(first['id'],'367929:2027:80002:070101:01:全日制');self.assertEqual(first['department_code'],'80002')
        self.assertEqual(first['department'],'数学与系统科学研究院');self.assertEqual(first['direction'],'01 代数理论1')
        self.assertIsNone(first['province']);self.assertIsNone(first['degree_type'])
        self.assertEqual(first['advisors'],['张老师','李老师']);self.assertEqual(len(first['catalog_variants']),2)
        self.assertIn('同等学力考生须加试',first['notes']);self.assertIsNone(first.get('planned_enrollment'))
        self.assertFalse(first['eligibility_evidence_complete']);self.assertTrue(first['requires_review'])

    def test_inherited_exam_quotes_point_to_both_same_up_and_exact_anchor_in_this_major(self):
        result=self.parse();second=result['programs'][1]
        self.assertEqual(second['study_mode'],'非全日制');self.assertEqual(second['degree_type'],'专业学位')
        self.assertEqual([s['code'] for s in second['subjects']],['101','201','203','301','801'])
        self.assertTrue(any(ref['quote']=='同上' for ref in second['exam_evidence']))
        self.assertTrue(any(ref['quote']==self.exams for ref in second['exam_evidence']))
        self.assertTrue(second['exam_matching_supported'])

    def test_same_up_does_not_cross_a_new_major(self):
        marker='<tr><td>02 (非全日制)应用数学</td>'
        self.replace(self.html.replace(marker,'<tr><td>070104 应用数学</td><td></td><td>3</td><td></td><td></td></tr>'+marker))
        with self.assertRaises(ValueError):self.parse()

    def test_only_recommendation_is_excluded_but_joint_master_doctor_training_remains(self):
        self.replace(self.html.replace('只招硕博连读','只招免试推荐的硕博连读生').replace('同等学力考生须加试','只招免试推荐的硕博连读生'))
        result=self.parse();self.assertEqual(len(result['programs']),1);self.assertEqual(len(result['excluded_records']),1)
        self.assertTrue(any('只招免试推荐的硕博连读生' in ref['quote'] for ref in result['excluded_records'][0]['evidence']))

    def test_exam_variants_for_same_admission_direction_are_conditional(self):
        self.replace(self.html.replace('>同上</td><td>同等学力','>'+self.exams.replace('301数学（一）','302数学（二）')+'</td><td>同等学力'))
        first=self.parse()['programs'][0];self.assertFalse(first['exam_matching_supported']);self.assertEqual(len(first['exam_combinations']),4)

    def test_unknown_mode_missing_slot_hidden_exam_and_wrong_leaf_identity_fail(self):
        for old,new in [('(非全日制)','(未知)'),('③301数学（一）',''),('>80002<','>80003<'),
                        ('<td>'+self.exams,'<td hidden>'+self.exams),('>硕士<','>博士<')]:
            text=self.html.replace(old,new);doc=self.save('bad',text,self.leaf['url']);doc={**doc,'public_form_context':self.leaf['public_form_context']}
            with self.assertRaises(ValueError):self.parse(text,doc)

    def test_scope_requires_all_original_index_pages_and_every_visible_leaf(self):
        self.assertTrue(ucas_public_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])
        old=self.index_html;self.index_html=old.replace('</div>','<a href="'+self.route+'?page=2">2</a></div>')
        self.docs['index']=self.save('index',self.index_html,self.index['url'],kind='catalog_context')
        self.assertFalse(ucas_public_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])

    def test_source_sha_public_link_and_original_response_are_mandatory(self):
        for doc in [{**self.leaf,'sha256':'0'*64},{**self.leaf,'url':self.leaf['url'].replace('80002','80003')},
                    {**self.leaf,'request_method':'POST'}]:
            with self.assertRaises(ValueError):self.parse(document=doc)
        with self.assertRaises(ValueError):self.parse(self.html+' transformed')

    def test_public_metadata_website_spans_do_not_change_exact_label_value_pairs(self):
        self.replace(self.html.replace('<td>硕士</td></tr>','<td>硕士</td></tr><tr><td>网址</td><td colspan="3">https://www.amss.cas.cn/</td></tr>'))
        self.assertEqual(len(self.parse()['programs']),2)

    def test_degree_is_only_assigned_from_explicit_literal_notes(self):
        original=self.html
        for note,expected in [('学术学位','学术学位'),('专业学位','专业学位'),('全日制专业学位硕士生专业','专业学位')]:
            self.replace(original.replace('只招硕博连读',note).replace('同等学力考生须加试',note))
            self.assertEqual(self.parse()['programs'][0]['degree_type'],expected)

    def test_every_declared_context_is_a_real_same_year_index_page(self):
        for identity,extra in [('foreign',{'url':'https://example.org/index'}),('bad-sha',{'sha256':'0'*64}),
                               ('wrong-page',{'url':self.base+self.route+'?page=2'})]:
            doc={**self.index,'id':identity,**extra};self.docs[identity]=doc
            leaf={**self.leaf,'public_form_context':{**self.leaf['public_form_context'],'index_document_ids':['index',identity]}}
            with self.assertRaises(ValueError):self.parse(document=leaf)

    def test_index_annual_heading_must_agree_with_actual_leaf_year(self):
        self.docs['index']=self.save('index',self.index_html.replace('招生单位 - 2027','招生单位 - 2026'),self.index['url'],kind='catalog_context')
        with self.assertRaises(ValueError):self.parse()

    def test_training_address_has_exact_original_listing_evidence(self):
        first=self.parse()['programs'][0]
        self.assertEqual(first['training_unit_address'],'中关村东路55号')
        self.assertTrue(any(ref['document_id']=='index' and ref['quote']=='中关村东路55号' for ref in first['evidence']))

    def test_all_visible_index_pages_and_training_units_reconcile_once_each(self):
        index1=self.index_html.replace('</div>','<a href="'+self.route+'?page=2">2</a></div>')
        self.docs['index']=self.save('index',index1,self.index['url'],kind='catalog_context')
        index2=self.index_html.replace('80002','80003').replace('数学与系统科学研究院','物理研究所').replace('class="current">1','class="current">2').replace('</div>','<a href="'+self.route+'?page=1">1</a></div>')
        self.save('index2',index2,self.base+self.route+'?page=2',kind='catalog_context')
        context={**self.leaf['public_form_context'],'index_document_ids':['index','index2']}
        self.leaf={**self.leaf,'public_form_context':context};self.docs['leaf']=self.leaf
        html=self.html.replace('80002','80003').replace('数学与系统科学研究院','物理研究所')
        second=self.save('leaf2',html,self.leaf['url'].replace('80002','80003'))
        self.docs['leaf2']={**second,'catalog_parser':'ucas_public_v1','public_form_context':{**context,'listing_document_id':'index2'}}
        result=ucas_public_scope(self.unit,2027,self.docs,project_root=self.root)
        self.assertTrue(result['complete']);self.assertEqual(result['training_units'],2);self.assertEqual(result['expected_records'],4)
        del self.docs['leaf2'];self.assertFalse(ucas_public_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])

    def test_negative_recommendation_wording_does_not_erase_ordinary_candidates(self):
        original=self.html
        for note in ['不只招免试推荐的硕博连读生','只招统考生，不招免试推荐的硕博连读生','本方向并非只接收推免生']:
            self.replace(original.replace('只招硕博连读',note).replace('同等学力考生须加试',note))
            self.assertEqual(len(self.parse()['programs']),2)

    def test_conflicting_same_direction_names_are_not_silently_overwritten(self):
        self.replace(self.html.replace('01 (全日制)代数理论1</td><td>李老师','01 (全日制)另一个名称</td><td>李老师'))
        with self.assertRaises(ValueError):self.parse()

    def test_source_path_bounds_and_unit_year_are_checked_before_interpreting_html(self):
        with self.assertRaises(ValueError):self.parse(document={**self.leaf,'local_path':'../../unrelated.html'})
        with self.assertRaises(ValueError):parse_ucas_public(self.html,self.unit,2028,self.leaf,project_root=self.root,documents=self.docs)

    def test_many_different_sources_do_not_keep_every_html_tree_resident(self):
        from collectors.catalog_ucas import _tree
        _tree.cache_clear()
        refs=[weakref.ref(_tree('<p>source-'+str(index)+'</p>')) for index in range(20)]
        self.assertLessEqual(sum(ref() is not None for ref in refs),8)


if __name__=='__main__':unittest.main()
