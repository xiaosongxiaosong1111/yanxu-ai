import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collectors.catalog_nuaa_public import parse_nuaa_public,nuaa_public_scope,decode_nuaa_literals


class NuaaPublicTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.docs={}
        self.unit={'id':'368181','unit_code':'10287','name':'南京航空航天大学','province':'江苏','official_url':'https://www.graduate.nuaa.edu.cn/'}
        self.base='https://yzsbm.nuaa.edu.cn/sszsml/'
        self.entry_html='''<h1>招生目录</h1><script>document.write("<script src='js/data.js?v="+timestamp+"'><\\/script>");document.write("<script src='js/zsml.js?v="+timestamp+"'><\\/script>");</script>'''
        self.entry=self.save('entry',self.entry_html,self.base+'index.html',kind='catalog_context')
        self.controller=self.save('controller','known-public-ui-controller',self.base+'js/zsml.js?v=1793587444000',kind='catalog_context')
        self.publication=self.save('publication','''<h1>南京航空航天大学研究生院</h1><h3>招生工作</h3><div portletmode="simpleArticleAttri"><h3 class="title">2027年硕士研究生招生专业目录</h3><div class="wp_articlecontent"><a href="http://yzsbm.nuaa.edu.cn/sszsml/index.html">2027年目录查询</a></div></div>''','https://www.graduate.nuaa.edu.cn/2026/0928/c13487a411935/page.htm',kind='catalog_context')
        self.tree={'text':self.unit['name'],'value':'0','items':[{'text':'001 航空学院','value':'001','YXSH':'001','feature':'dict'}]}
        self.zsml={'name':self.unit['name'],'nd':'2027','zslb':'1','mllb':'12'}
        self.exam='01方向：①101思想政治理论②201英语（一）③301数学（一）④810工程力学<br>F1方向：①101思想政治理论②204英语（二）③302数学（二）④811电子技术<br>'
        self.faculty={'yxsh':'001','yxmc':'航空学院','zslb':'1','yxbz':'不接收同等学力考生','exportZsmlYxZys':[{
            'yxsh':'001','zydm':'085503','zymc':'航空工程','xwlb':'2','zybz':'报考前须核对条件','kskm':self.exam,'exportZsmlYxZyYjfxs':[
                {'yxsh':'001','zydm':'085503','fxm':'01','fxmc':'飞行器力学','xxfs':'1'},
                {'yxsh':'001','zydm':'085503','fxm':'F1','fxmc':'非全方向','xxfs':'2'}]}]}
        self.data={'mllb_12':[self.faculty]};self.rebuild()
        self.addCleanup(patch.stopall)
        patch('collectors.catalog_nuaa_public.ENTRY_SHA',self.entry['sha256']).start()
        patch('collectors.catalog_nuaa_public.CONTROLLER_SHA',self.controller['sha256']).start()

    def save(self,identity,text,url,kind='catalog'):
        path=self.root/'data/snapshots'/f'{identity}.response';path.parent.mkdir(parents=True,exist_ok=True);raw=text.encode();path.write_bytes(raw)
        document={'id':identity,'unit_id':self.unit['id'],'year':2027,'kind':kind,'url':url,'local_path':path.relative_to(self.root).as_posix(),
                  'sha256':hashlib.sha256(raw).hexdigest(),'request_method':'GET','status':'parsed'}
        self.docs[identity]=document;return document

    def rebuild(self):
        self.text='var treeData = '+json.dumps(self.tree,ensure_ascii=False)+';\nvar mainData = '+json.dumps(self.data,ensure_ascii=False)+';\nvar zsml = '+json.dumps(self.zsml,ensure_ascii=False)+';'
        doc=self.save('data',self.text,self.base+'js/data.js?v=1793587444000')
        self.document={**doc,'catalog_parser':'nuaa_public_v1','public_form_profile':'nuaa','public_form_context':{
            'profile':'nuaa','entry_document_id':'entry','controller_document_id':'controller','publication_document_id':'publication'}}
        self.docs['data']=self.document

    def parse(self,document=None,text=None):return parse_nuaa_public(self.text if text is None else text,self.unit,2027,document or self.document,project_root=self.root,documents=self.docs)

    def test_direction_specific_exams_are_exact_and_do_not_cross_between_full_and_nonfull_modes(self):
        result=self.parse();self.assertEqual(result['expected_records'],2);first,second=result['programs']
        self.assertEqual(first['id'],'368181:2027:001:085503:01:全日制');self.assertEqual(first['degree_type'],'专业学位')
        self.assertEqual([s['code'] for s in first['subjects']],['101','201','301','810'])
        self.assertEqual([s['code'] for s in second['subjects']],['101','204','302','811'])
        self.assertTrue(first['exam_matching_supported']);self.assertFalse(first['eligibility_evidence_complete'])
        self.assertEqual(first['notes'],['不接收同等学力考生','报考前须核对条件'])

    def test_structured_evidence_keeps_exact_original_values_and_named_literal_paths_without_prose_quote(self):
        from service_verification import citation_signature
        first=self.parse()['programs'][0]
        refs=[ref for ref in first['evidence'] if ref.get('kind')=='structured_fields']
        self.assertTrue(any(ref['field_path'].startswith('mainData.mllb_12[0].exportZsmlYxZys[0]') for ref in refs))
        self.assertTrue(any(ref['structured_fields'].get('kskm')==self.exam for ref in refs))
        self.assertTrue(all('quote' not in ref and citation_signature(ref) is not None for ref in refs))

    def test_rejects_executable_or_unrecognized_javascript_instead_of_evaluating_it(self):
        for suffix in [';fetch("https://example.org/")',';var password="x"',';throw Error()']:
            with self.assertRaises(ValueError):decode_nuaa_literals(self.text+suffix)
        with self.assertRaises(ValueError):decode_nuaa_literals(self.text.replace('var treeData =','var treeData = eval('))

    def test_direction_groups_must_cover_each_direction_once_and_never_borrow_another_major(self):
        for exam in [self.exam.replace('F1方向：','02方向：'),self.exam+self.exam,
                     self.exam.replace('01方向：','01、F1方向：')]:
            self.faculty['exportZsmlYxZys'][0]['kskm']=exam;self.rebuild()
            with self.assertRaises(ValueError):self.parse()

    def test_unlabeled_one_exam_group_can_apply_to_all_explicit_directions_with_real_alternatives(self):
        self.faculty['exportZsmlYxZys'][0]['kskm']='①101思想政治理论②201英语（一）或203日语③301数学（一）④810工程力学<br>'
        self.rebuild();rows=self.parse()['programs']
        self.assertTrue(all([s['code'] for s in row['subjects']]==['101','201','203','301','810'] for row in rows))

    def test_tree_faculty_set_and_complete_data_are_reconciled(self):
        self.assertTrue(nuaa_public_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])
        self.tree['items']=[*self.tree['items'],{'text':'002 动力学院','value':'002','YXSH':'002','feature':'dict'}];self.rebuild()
        with self.assertRaises(ValueError):self.parse()

    def test_annual_name_mode_and_department_major_direction_identity_conflicts_are_rejected(self):
        for key,value in [('nd','2026'),('name','其他学校'),('zslb','2')]:
            old=self.zsml[key];self.zsml[key]=value;self.rebuild()
            with self.assertRaises(ValueError):self.parse()
            self.zsml[key]=old
        self.faculty['exportZsmlYxZys'][0]['exportZsmlYxZyYjfxs'][0]['yxsh']='999';self.rebuild()
        with self.assertRaises(ValueError):self.parse()

    def test_original_ui_controller_and_entry_are_version_checked_and_sources_are_bounded(self):
        with self.assertRaises(ValueError):self.parse(document={**self.document,'local_path':'../../other.response'})
        with self.assertRaises(ValueError):self.parse(document={**self.document,'sha256':'0'*64})
        altered=self.save('controller','changed UI code',self.controller['url'],kind='catalog_context');self.docs['controller']=altered
        with self.assertRaises(ValueError):self.parse()

    def test_placeholder_recommendation_exams_are_not_real_initial_courses(self):
        self.faculty['exportZsmlYxZys'][0]['kskm']='①188只招收推免生②266只招收推免生③799只招收推免生④899只招收推免生'
        self.rebuild();parsed=self.parse();self.assertEqual(parsed['programs'],[]);self.assertEqual(len(parsed['excluded_records']),2)

    def test_trailing_whitespace_in_real_direction_code_is_normalized_without_altering_original_field(self):
        self.faculty['exportZsmlYxZys'][0]['exportZsmlYxZyYjfxs'][0]['fxm']='01 '
        self.rebuild();first=self.parse()['programs'][0]
        self.assertEqual(first['direction'],'01 飞行器力学')
        self.assertTrue(any(ref.get('structured_fields',{}).get('fxm')=='01 ' for ref in first['evidence']))

    def test_untrusted_literal_container_types_fail_cleanly(self):
        self.data['mllb_12']=[42];self.rebuild()
        with self.assertRaises(ValueError):self.parse()

        self.data['mllb_12']=[self.faculty];self.tree['items']=[None];self.rebuild()
        with self.assertRaises(ValueError):self.parse()
        self.tree['items']=[{'text':'001 航空学院','value':'001','YXSH':'001','feature':'dict'}]
        self.faculty['exportZsmlYxZys'][0]['exportZsmlYxZyYjfxs']=[None];self.rebuild()
        with self.assertRaises(ValueError):self.parse()

    def test_json_literal_rejects_non_json_constants_and_duplicate_keys(self):
        for replacement in ['"nd":"2026","nd":"2027"','"nd":NaN','"nd":Infinity']:
            text=self.text.replace('"nd": "2027"',replacement)
            with self.assertRaises(ValueError):decode_nuaa_literals(text)

    def test_deeply_nested_untrusted_literal_is_reported_as_invalid_input(self):
        text='var treeData = {"x":'+'['*1200+'0'+']'*1200+'};'
        with self.assertRaises(ValueError):decode_nuaa_literals(text)

    def test_extra_or_non_object_context_is_not_bound_as_a_verified_source(self):
        for context in [list(self.document['public_form_context']),{**self.document['public_form_context'],'extra_document_id':'unvalidated'}]:
            with self.assertRaises(ValueError):self.parse(document={**self.document,'public_form_context':context})


if __name__=='__main__':unittest.main()
