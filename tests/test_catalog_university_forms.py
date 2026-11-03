import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit

from collectors.catalog_university_forms import parse_university_form, university_form_scope


class UniversityFormsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.docs={}
        self.unit={'id':'368479','unit_code':'10610','name':'四川大学','province':'四川'}
        entry='''<title>四川大学研究生招生信息网</title>
        <meta name="Keywords" content="2027年四川大学硕士专业目录">
        <select id="yxslist"><option value="0">不限学院</option><option value="101">101经济学院</option></select>
        <select id="Yearlist"><option value="2027">2027</option></select>
        <script>$("#Yearlist").val("2027"); url: '/sszyml/simpleiq/'+id;</script>'''
        self.entry=self.save('entry',entry,'https://yz.scu.edu.cn/sszyml',context=True)
        self.html='''<table id="datatabel"><thead><tr><th>学科专业代码、专业名称及研究方向</th>
        <th>导师姓名</th><th>招生人数</th><th>考试科目</th><th>备注</th></tr></thead>
        <tr><td><h3>101 经济学院</h3></td><td></td><td>30</td><td></td><td></td></tr>
        <tr><td colspan="5">招生人数含推免，各专业限制详见备注。</td></tr>
        <tr><td><h4>020100 理论经济学</h4></td><td></td><td>30(10)</td><td></td><td>学术学位</td></tr>
        <tr><td>01 (全日制)政治经济学</td><td>某老师</td><td></td><td>① 101 思想政治理论 ② 201 英语（一） ③ 303 数学（三） ④ 801 经济学原理</td><td>学制：3年；学费：7200元/生.年</td></tr>
        <tr><td>02 (全日制)西方经济学</td><td>其他老师</td><td></td><td>同上</td><td>限经济学本科报考。</td></tr></table>'''
        self.leaf=self.save('leaf',self.html,'https://yz.scu.edu.cn/sszyml/simpleiq/101;0;0;2027;;;')
        self.bind('scu')

    def save(self,identity,text,url,context=False):
        raw=text.encode();path=self.root/'data/snapshots'/f'{identity}.raw'
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        doc={'id':identity,'unit_id':self.unit['id'],'year':2027,'url':url,'kind':'catalog_context' if context else 'catalog',
             'local_path':path.relative_to(self.root).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),
             'request_method':'GET','content_type':'application/json' if text.startswith('{') else 'text/html'}
        self.docs={**self.docs,identity:doc};return doc

    def bind(self,profile,**extra):
        self.leaf={**self.leaf,'catalog_parser':'university_form_v1',
                   'public_form_context':{'profile':profile,'entry_document_id':'entry',**extra}}
        self.docs={**self.docs,'leaf':self.leaf}

    def replace(self,text):
        context=self.leaf['public_form_context'];self.html=text
        self.leaf={**self.save('leaf',text,self.leaf['url']),'catalog_parser':'university_form_v1','public_form_context':context}
        self.docs={**self.docs,'leaf':self.leaf}

    def parse(self,**changes):
        return parse_university_form(changes.get('text',self.html),changes.get('unit',self.unit),changes.get('year',2027),
             changes.get('document',self.leaf),project_root=self.root,documents=changes.get('documents',self.docs))

    def test_scu_exact_identity_subjects_and_same_as_above_proof(self):
        result=self.parse();self.assertEqual(result['issues'],[]);self.assertEqual(result['expected_records'],2)
        first,second=result['programs'];self.assertEqual(first['id'],'368479:2027:101:020100:01:全日制')
        self.assertEqual(first['degree_type'],'学术学位')
        self.assertEqual([s['code'] for s in second['subjects']],['101','201','303','801'])
        refs=second['exam_evidence'];self.assertIn('同上',[r['quote'] for r in refs])
        self.assertTrue(any('801' in r['quote'] for r in refs));self.assertTrue(second['unparsed_condition_notes'])
        self.assertIsNone(first.get('planned_enrollment'))

    def test_scu_same_as_above_never_crosses_major_or_source(self):
        fresh=self.html.replace('<tr><td>02 (全日制)',
            '<tr><td>020200 应用经济学</td><td></td><td>10</td><td></td><td>学术学位</td></tr><tr><td>02 (全日制)')
        self.replace(fresh);result=self.parse();self.assertTrue(result['issues']);self.assertEqual(len(result['programs']),1)

    def test_scu_recommendation_only_excluded_with_original(self):
        self.replace(self.html.replace('限经济学本科报考。','本专业在本学院只招收推免生。'))
        result=self.parse();self.assertEqual(len(result['programs']),1);self.assertEqual(result['expected_records'],1)
        self.assertEqual(len(result['excluded_records']),1);self.assertIn('只招收推免生',str(result['excluded_records']))

    def test_scu_remark_same_as_above_preserves_restriction_and_both_quotes(self):
        self.replace(self.html.replace('学制：3年；学费：7200元/生.年','本专业只招收推免生。').replace('限经济学本科报考。','同上'))
        result=self.parse();self.assertEqual(result['issues'],[]);self.assertEqual(result['programs'],[])
        self.assertEqual(len(result['excluded_records']),2)
        self.assertIn('同上',[r['quote'] for r in result['excluded_records'][1]['evidence']])
        self.assertIn('本专业只招收推免生。',[r['quote'] for r in result['excluded_records'][1]['evidence']])

    def test_scu_negative_recommendation_wording_does_not_exclude_ordinary_direction(self):
        for note in ('不只招收推免生。','并非仅接收推免生。','只招收统考生，不招收推免生。'):
            original=self.html;self.replace(original.replace('限经济学本科报考。',note))
            self.assertEqual(len(self.parse()['programs']),2,note);self.replace(original)

    def test_scu_unused_slots_and_multiple_languages(self):
        self.replace(self.html.replace('② 201 英语（一）','② 201 英语（一）或203日语').replace('④ 801 经济学原理','④ -- 无'))
        result=self.parse();self.assertEqual(result['issues'],[])
        self.assertEqual([(s['exam_unit'],s['code']) for s in result['programs'][0]['subjects']],[(1,'101'),(2,'201'),(2,'203'),(3,'303')])

    def test_scu_explicit_degree_with_non_full_time_quota_is_retained(self):
        self.replace(self.html.replace('学术学位</td>','专业学位<br>含非全日制人数：160</td>'))
        self.assertEqual(self.parse()['programs'][0]['degree_type'],'专业学位')

    def test_scu_empty_real_response_is_valid_but_not_ignored_for_scope(self):
        self.replace(self.html[:self.html.index('</thead>')+8]+'</table>')
        self.assertEqual(self.parse()['expected_records'],0)
        self.assertTrue(university_form_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])

    def test_scu_missing_or_duplicate_faculty_results_prevent_complete(self):
        self.assertTrue(university_form_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])
        duplicate={**self.docs,'other':{**self.leaf,'id':'other'}}
        self.assertFalse(university_form_scope(self.unit,2027,duplicate,project_root=self.root)['complete'])
        original=(self.root/self.entry['local_path']).read_text('utf-8')
        self.entry=self.save('entry',original.replace('</select>','<option value="102">102法学院</option></select>',1),self.entry['url'],context=True)
        self.assertFalse(university_form_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])

    def test_scu_wrong_faculty_hidden_rows_and_unknown_table_rejected(self):
        for old,new in [('101 经济学院','102 法学院'),('<tr><td>01 (全日制)','<tr hidden><td>01 (全日制)'),
                        ('考试科目</th>','参考书</th>')]:
            with self.subTest(new=new):
                original=self.html;self.replace(original.replace(old,new))
                result=self.parse();self.assertTrue(result['issues']);self.replace(original)

    def test_proof_rejects_wrong_year_unit_url_text_or_sha(self):
        for changes in [{'year':2026},{'unit':{**self.unit,'id':'wrong'}},{'text':self.html+'x'},
                        {'document':{**self.leaf,'sha256':'0'*64}},
                        {'document':{**self.leaf,'url':'https://evil.edu.cn'+urlsplit(self.leaf['url']).path}},
                        {'documents':{**self.docs,'entry':{**self.entry,'sha256':'0'*64}}}]:
            with self.subTest(changes=list(changes)):
                with self.assertRaises((ValueError,KeyError)):self.parse(**changes)


class SzuFormsTests(UniversityFormsTests):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.docs={}
        self.unit={'id':'368417','unit_code':'10590','name':'深圳大学','province':'广东'}
        self.entry=self.save('entry','''<h1>深圳大学2027年硕士研究生招生专业目录</h1>
        <a href="https://ehall.szu.edu.cn/gsapp/sys/zsjzapp/index.do#/2027">专业目录</a>''',
        'https://yz.szu.edu.cn/info/1006/15196.htm',context=True)
        self.index_data={'success':True,'ZSND':'2027','PCWID':'batch','zsjzDatas':[
            {'XBYXDM':'110','YXMC':'计算机与软件学院','ZYDM':'081200','ZYMC':'081200 计算机科学与技术(全日制)',
             'ZYWID':'major-wid','PC':'batch','XWLX_DISPLAY':'学术学位'}]}
        self.index=self.save('index',json.dumps(self.index_data,ensure_ascii=False),
            'https://ehall.szu.edu.cn/gsapp/sys/zsjzapp/temphtml/2027/data/1/indexdata.js',context=True)
        self.payload={'zszy':{'ND':'2027','PC_DISPLAY':'2027','PC':'batch','XBYXDM':'110','YXMC':'110 计算机与软件学院',
            'ZYDM':'081200','ZYMC':'计算机科学与技术','WID':'major-wid','XWLX_DISPLAY':'学术学位','BKYQ':'仅招收计算机类本科毕业生。'},
            'fxds':[{'ZYFXDM':'01','ZYFXMC':'人工智能','ZYML':'major-wid','PC':'batch'},
                    {'ZYFXDM':'02','ZYFXMC':'计算机系统','ZYML':'major-wid','PC':'batch'}],
            'zskm':[{'KMLB':'1','KMDM':c,'KMMC':n,'ZYML':'major-wid','PC':'batch'} for c,n in
                [('101','思想政治理论'),('201','英语一'),('301','数学一'),('408','计算机学科专业基础综合')]]}
        self.html=json.dumps(self.payload,ensure_ascii=False)
        self.leaf=self.save('leaf',self.html,'https://ehall.szu.edu.cn/gsapp/sys/zsjzapp/temphtml/2027/data/4/110/081200/zszy.js')
        self.bind('szu',index_document_id='index')

    # The shared trust-boundary test remains applicable; SCU-specific tests do not.
    test_scu_exact_identity_subjects_and_same_as_above_proof=None
    test_scu_same_as_above_never_crosses_major_or_source=None
    test_scu_recommendation_only_excluded_with_original=None
    test_scu_remark_same_as_above_preserves_restriction_and_both_quotes=None
    test_scu_negative_recommendation_wording_does_not_exclude_ordinary_direction=None
    test_scu_unused_slots_and_multiple_languages=None
    test_scu_explicit_degree_with_non_full_time_quota_is_retained=None
    test_scu_empty_real_response_is_valid_but_not_ignored_for_scope=None
    test_scu_missing_or_duplicate_faculty_results_prevent_complete=None
    test_scu_wrong_faculty_hidden_rows_and_unknown_table_rejected=None

    def replace_payload(self,payload):
        self.payload=payload;self.replace(json.dumps(payload,ensure_ascii=False))

    def test_szu_all_direction_fields_and_structured_originals(self):
        result=self.parse();self.assertEqual(result['issues'],[]);self.assertEqual(len(result['programs']),2)
        row=result['programs'][0];self.assertEqual(row['study_mode'],'全日制');self.assertEqual(row['department_code'],'110')
        self.assertEqual([s['code'] for s in row['subjects']],['101','201','301','408'])
        self.assertTrue(all(s['scope']=='national' for s in row['subjects']))
        self.assertTrue(all(ref['kind']=='structured_fields' and 'quote' not in ref for ref in row['evidence']))
        self.assertTrue(row['unparsed_condition_notes'])
        self.assertTrue(university_form_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])

    def test_szu_retest_and_additional_subjects_never_become_initial(self):
        self.replace_payload({**self.payload,'zskm':[*self.payload['zskm'],
            {'KMLB':'2','KMDM':'F110','KMMC':'复试','ZYML':'major-wid','PC':'batch'},
            {'KMLB':'3','KMDM':'J110','KMMC':'加试','ZYML':'major-wid','PC':'batch'}]})
        self.assertEqual(len(self.parse()['programs'][0]['subjects']),4)

    def test_szu_actual_official_http_link_is_bound_to_https_original(self):
        html=(self.root/self.entry['local_path']).read_text('utf-8')
        self.entry=self.save('entry',html.replace('https://ehall','http://ehall'),self.entry['url'],context=True)
        self.assertEqual(len(self.parse()['programs']),2)

    def test_szu_explicit_recommendation_only_keeps_excluded_original(self):
        self.replace_payload({**self.payload,'zszy':{**self.payload['zszy'],'BKYQ':'本专业仅接收推荐免试生。'},'zskm':[]})
        result=self.parse();self.assertEqual(result['programs'],[]);self.assertEqual(len(result['excluded_records']),2)
        self.assertEqual(result['expected_records'],0)

    def test_szu_sentence_whitespace_keeps_literal_recommendation_restriction(self):
        self.replace_payload({**self.payload,'zszy':{**self.payload['zszy'],'BKYQ':'科研兴趣。 仅接推免。'},'zskm':None})
        result=self.parse();self.assertEqual(result['issues'],[]);self.assertEqual(len(result['excluded_records']),2)

    def test_szu_direction_restriction_never_excludes_neighbor_direction(self):
        payload={**self.payload,'zszy':{**self.payload['zszy'],'BKYQ':'02计算机系统方向仅招推免。'},
            'fxds':[self.payload['fxds'][0],{**self.payload['fxds'][1],'BZ':'仅推免招生'}]}
        self.replace_payload(payload);result=self.parse()
        self.assertEqual(result['issues'],[]);self.assertEqual(result['expected_records'],1)
        self.assertEqual([r['direction'] for r in result['programs']],['01 人工智能'])
        self.assertEqual(len(result['excluded_records']),1)
        self.assertIn('BZ',str(result['excluded_records'][0]['evidence']))

    def test_szu_missing_mode_and_wrong_identity_never_inferred(self):
        for field,value in [('ND','2026'),('XBYXDM','111'),('ZYDM','085404'),('WID','other')]:
            payload={**self.payload,'zszy':{**self.payload['zszy'],field:value}}
            with self.subTest(field=field):
                original=self.payload;self.replace_payload(payload)
                with self.assertRaises(ValueError):self.parse()
                self.replace_payload(original)
        index={**self.index_data,'zsjzDatas':[{**self.index_data['zsjzDatas'][0],'ZYMC':'081200 计算机科学与技术'}]}
        self.index=self.save('index',json.dumps(index,ensure_ascii=False),self.index['url'],context=True)
        with self.assertRaises(ValueError):self.parse()

    def test_szu_duplicate_direction_and_incomplete_initial_are_issues(self):
        for changes in [{'fxds':self.payload['fxds']*2},{'zskm':self.payload['zskm'][:1]}]:
            original=self.payload;self.replace_payload({**self.payload,**changes})
            result=self.parse();self.assertTrue(result['issues']);self.replace_payload(original)

    def test_szu_scope_detects_missing_or_duplicate_real_major(self):
        duplicate={**self.docs,'copy':{**self.leaf,'id':'copy'}}
        self.assertFalse(university_form_scope(self.unit,2027,duplicate,project_root=self.root)['complete'])
        index={**self.index_data,'zsjzDatas':[*self.index_data['zsjzDatas'],
            {**self.index_data['zsjzDatas'][0],'ZYDM':'085404','ZYMC':'085404 计算机技术(全日制)','ZYWID':'other'}]}
        self.index=self.save('index',json.dumps(index,ensure_ascii=False),self.index['url'],context=True)
        self.assertFalse(university_form_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])


if __name__=='__main__':unittest.main()
