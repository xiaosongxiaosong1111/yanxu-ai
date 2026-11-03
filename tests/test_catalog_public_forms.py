import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlencode

from collectors.catalog_public_forms import parse_public_form_html, public_form_scope


class PublicFormTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.unit={'id':'367883','unit_code':'10006','name':'北京航空航天大学','province':'北京',
                   'official_url':'https://yzb.buaa.edu.cn/'}
        self.docs={}
        self.entry=self.save('entry','''<title>北京航空航天大学研究生招生网</title>
        <h2>招收2027年学历硕士专业目录及初试考试科目查询</h2>
        <form action="../ssmlcxjg.jsp?wbtreeid=1018" method="post"><select name="xydm"></select>
        <select name="zydm"></select><select name="yjfxdm"></select></form>
        <script>$.ajax({type:'post',url:'/system/resource/importdata/getData.jsp',data:{siteName:'1403782683',templateid:'2'}});</script>''',
            'https://yzb.buaa.edu.cn/xlss/zymlcx.htm')
        self.departments=self.option('deps',{'code_root_mc':'xymc','code_root_dm':'xydm'},'<option value="001">001材料科学与工程学院</option>')
        self.majors=self.option('majors',{'parentCode':'xydm','parentValue':'001','code_root_mc':'zymc','code_root_dm':'zydm'},'<option value="080500">080500材料科学与工程（学术学位）</option>')
        self.directions=self.option('directions',{'parentCode':'xydm,zydm','parentValue':'001,080500','code_root_mc':'yjfxmc','code_root_dm':'yjfxdm'},'<option value="01">高温结构材料</option>')
        self.html='''<title>北京航空航天大学研究生招生网</title>
        <h2>招收2027年学历硕士专业目录及初试考试科目查询</h2>
        <ul class="zyinfo"><li><b>学院代码及名称：</b>001材料科学与工程学院</li>
        <li><b>专业代码及名称：</b>080500材料科学与工程（学术学位）</li>
        <li><b>专业拟招收人数：</b>49（含专项）</li><li><b>研究方向名称：</b>高温结构材料</li>
        <li><b>专业备注：</b>学制3年，全日制学习方式。</li></ul>
        <table class="ksinfo"><tr><th>考试科目单元</th><th>考试科目代码</th><th>考试科目名称</th></tr>
        <tr><td>第一门考试科目</td><td>101</td><td>思想政治理论</td></tr>
        <tr><td>第二门考试科目</td><td>201</td><td>英语（一）</td></tr>
        <tr><td>第三门考试科目</td><td>302</td><td>数学（二）</td></tr>
        <tr><td>第四门考试科目</td><td>801</td><td>材料综合</td></tr></table>'''
        self.leaf=self.save('leaf',self.html,'https://yzb.buaa.edu.cn/ssmlcxjg.jsp?wbtreeid=1018',
                            {'xydm':'001','zydm':'080500','yjfxdm':'01'})
        self.leaf={**self.leaf,'catalog_parser':'public_form_v1','public_form_context':{'profile':'buaa',
            'entry_document_id':'entry','department_options_id':'deps','major_options_id':'majors','direction_options_id':'directions'}}
        self.docs={**self.docs,'leaf':self.leaf}

    def save(self,identity,html,url,params=None):
        path=self.root/'data/snapshots'/f'{identity}.html';path.parent.mkdir(parents=True,exist_ok=True)
        raw=html.encode();path.write_bytes(raw)
        document={'id':identity,'unit_id':self.unit['id'],'year':2027,'kind':'catalog','url':url,
                  'local_path':path.relative_to(self.root).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),
                  'request_method':'POST' if params is not None else 'GET'}
        if params is not None:
            body=urlencode(params);document={**document,'request_body':body,'request_sha256':hashlib.sha256(body.encode()).hexdigest()}
        self.docs={**self.docs,identity:document};return document

    def option(self,identity,params,html):
        return self.save(identity,html,'https://yzb.buaa.edu.cn/system/resource/importdata/getData.jsp',
                         {'siteName':'1403782683','templateid':'2',**params})

    def parse(self,html=None,document=None,unit=None,year=2027):
        return parse_public_form_html(self.html if html is None else html,unit or self.unit,year,
            document or self.leaf,documents=self.docs,project_root=self.root)

    def replace_leaf(self,html):
        fresh=self.save('leaf',html,self.leaf['url'],{'xydm':'001','zydm':'080500','yjfxdm':'01'})
        self.leaf={**fresh,'catalog_parser':'public_form_v1','public_form_context':self.leaf['public_form_context']}
        self.docs={**self.docs,'leaf':self.leaf};self.html=html

    def test_public_form_complete_identity_exams_and_exact_quotes(self):
        parsed=self.parse();self.assertEqual(parsed['issues'],[]);self.assertEqual(parsed['expected_records'],1)
        row=parsed['programs'][0]
        self.assertEqual(row['id'],'367883:2027:001:080500:01:全日制')
        self.assertEqual(row['name'],'材料科学与工程');self.assertEqual(row['degree_type'],'学术学位')
        self.assertEqual([s['code'] for s in row['subjects']],['101','201','302','801'])
        self.assertEqual([s['scope'] for s in row['subjects']],['national']*3+['367883'])
        self.assertEqual(row['duration_years'],3)
        self.assertIsNone(row.get('planned_enrollment'))  # a major-wide total is not a direction quota
        self.assertIn('49（含专项）',' '.join(row['notes']))
        self.assertTrue(all(ref['quote'] for ref in row['evidence']))
        self.assertIn('801',row['exam_evidence']['quote'])

    def test_complete_scope_independently_enumerates_options_and_leaves(self):
        scope=public_form_scope(self.unit,2027,self.docs,project_root=self.root)
        self.assertTrue(scope['complete']);self.assertEqual(scope['expected_records'],1)
        self.assertEqual(scope['expected_document_ids'],['leaf'])

    def test_scope_with_missing_direction_result_is_not_complete(self):
        self.option('directions',{'parentCode':'xydm,zydm','parentValue':'001,080500','code_root_mc':'yjfxmc','code_root_dm':'yjfxdm'},
            '<option value="01">高温结构材料</option><option value="02">其他方向</option>')
        scope=public_form_scope(self.unit,2027,self.docs,project_root=self.root)
        self.assertFalse(scope['complete']);self.assertEqual(scope['expected_records'],2)

    def test_identity_or_mode_cannot_be_borrowed_from_neighbor(self):
        for old,new in [('001材料科学与工程学院','002电子学院'),('080500材料科学与工程','080600其他专业'),
                        ('高温结构材料</li>','邻居方向</li>'),('学制3年，全日制学习方式。','未公布学习方式')]:
            with self.subTest(value=new):
                original=self.html;self.replace_leaf(original.replace(old,new))
                with self.assertRaises(ValueError):self.parse()
                self.replace_leaf(original)

    def test_hidden_exam_rows_do_not_fill_missing_visible_slots(self):
        self.replace_leaf(self.html.replace('<tr><td>第四门','<tr hidden><td>第四门'))
        with self.assertRaises(ValueError):self.parse()

    def test_multiple_alternatives_share_only_the_actual_exam_unit(self):
        self.replace_leaf(self.html.replace('<td>201</td><td>英语（一）</td>',
            '<td>201</td><td>英语（一）</td></tr><tr><td>第二门考试科目</td><td>203</td><td>日语</td>'))
        row=self.parse()['programs'][0]
        self.assertEqual([s['code'] for s in row['subjects']],['101','201','203','302','801'])
        self.assertTrue(all(s.get('choice_group')=='exam-unit-2' for s in row['subjects'] if s['exam_unit']==2))

    def test_explicit_unused_slots_are_not_missing_course_data(self):
        self.replace_leaf(self.html.replace('<td>801</td><td>材料综合</td>','<td>--</td><td>无</td>'))
        row=self.parse()['programs'][0]
        self.assertEqual([s['exam_unit'] for s in row['subjects']],[1,2,3])

    def test_paired_alternative_columns_keep_each_code_with_its_own_name(self):
        self.replace_leaf(self.html.replace('<td>201</td><td>英语（一）</td>','<td>202 或 203</td><td>俄语 或 日语</td>'))
        row=self.parse()['programs'][0]
        self.assertEqual([(s['code'],s['name']) for s in row['subjects'] if s['exam_unit']==2],[('202','俄语'),('203','日语')])

    def test_explicit_or_row_inherits_only_the_preceding_exam_unit(self):
        self.replace_leaf(self.html.replace('<td>201</td><td>英语（一）</td>',
            '<td>201</td><td>英语（一）</td></tr><tr><td>或</td><td>203</td><td>日语</td>'))
        row=self.parse()['programs'][0]
        self.assertEqual([(s['code'],s['exam_unit']) for s in row['subjects']],[('101',1),('201',2),('203',2),('302',3),('801',4)])

    def test_shared_direction_with_two_explicit_modes_keeps_independent_mode_durations(self):
        self.replace_leaf(self.html.replace('学制3年，全日制学习方式。','全日制学习方式，学制3年。非全日制学习方式，学制2年。'))
        rows=self.parse()['programs']
        self.assertEqual({r['study_mode']:r['duration_years'] for r in rows},{'全日制':3,'非全日制':2})

    def test_bad_year_unit_endpoint_and_duplicate_parameters_are_rejected(self):
        with self.assertRaises(ValueError):self.parse(year=2026)
        with self.assertRaises(ValueError):self.parse(unit={**self.unit,'id':'foreign'})
        with self.assertRaises(ValueError):self.parse(document={**self.leaf,'url':'https://evil.edu.cn/ssmlcxjg.jsp?wbtreeid=1018'})
        body=self.leaf['request_body']+'&xydm=999'
        with self.assertRaises(ValueError):self.parse(document={**self.leaf,'request_body':body,'request_sha256':hashlib.sha256(body.encode()).hexdigest()})

    def test_source_and_request_tampering_revoke_proof(self):
        with self.assertRaises(ValueError):self.parse(document={**self.leaf,'request_sha256':'0'*64})
        self.docs={**self.docs,'majors':{**self.majors,'sha256':'0'*64}}
        with self.assertRaises(ValueError):self.parse()

    def test_option_duplicates_inert_options_and_unlisted_choices_are_rejected(self):
        for options in ['<option value="080500">080500材料科学与工程（学术学位）</option>'*2,
                        '<option hidden value="080500">080500材料科学与工程（学术学位）</option>',
                        '<option value="080600">080600其他学科</option>']:
            with self.subTest(options=options):
                self.option('majors',{'parentCode':'xydm','parentValue':'001','code_root_mc':'zymc','code_root_dm':'zydm'},options)
                with self.assertRaises(ValueError):self.parse()

    def test_scope_rejects_duplicates_and_missing_context(self):
        self.assertFalse(public_form_scope(self.unit,2027,{},project_root=self.root)['complete'])
        docs={**self.docs,'other':{**self.leaf,'id':'other'}}
        self.assertFalse(public_form_scope(self.unit,2027,docs,project_root=self.root)['complete'])


class JsonPublicFormTests(unittest.TestCase):
    save=PublicFormTests.save
    option=PublicFormTests.option
    parse=PublicFormTests.parse
    def setUp(self):
        PublicFormTests.setUp(self)
        self.docs={};self.unit={'id':'368278','unit_code':'10427','name':'济南大学','province':'山东','official_url':'https://yanzhao.ujn.edu.cn/'}
        self.entry=self.save('entry','''<h1>济南大学2027年硕士研究生招生专业目录</h1>
        <div id="nd" data-value="2027"></div><table data-data-url="zsZsmlJbxxMng/queryViewZsmlList"></table>
        <script>function beforeLoad(param){param.conditions=get("zsmlForm").getData();}</script>''',
        'https://yanzhao.ujn.edu.cn/zsml/enterViewZsml?nd=2027')
        self.row={'zsmlbh':'catalog-001','organCode':'101','organName':'卓越工程师学院','xkdm':'085404',
                  'xkmc':'计算机技术','yjfxdm':'00','yjfxmc':'不区分研究方向','yjfx':'00不区分研究方向','xk':'085404计算机技术','xxfs':'1','xz':3}
        self.listing=self.save('listing',json.dumps([self.row],ensure_ascii=False),'https://yanzhao.ujn.edu.cn/zsZsmlJbxxMng/queryViewZsmlList',
            {'conditions':json.dumps({'nd':'2027','yxsbh':None,'xkbh':None,'xxfs':None},separators=(',',':')),'_gridInfo':'{}'})
        self.dictionary=self.save('dict',json.dumps([{'value':'1','text':'全日制'},{'value':'2','text':'非全日制'}],ensure_ascii=False),
            'https://yanzhao.ujn.edu.cn/sysCommon/getContextData',{'key':'select_dict_XXFS'})
        self.page=self.save('page','''<h3>专业方向：085404计算机技术_00不区分研究方向</h3>
        <table data-data-url="zsZsmlJbxxMng/queryZsmlKskmList"></table>
        <script>conditions.zsmlbh = "catalog-001";</script>''',
            'https://yanzhao.ujn.edu.cn/zsZsmlJbxxMng/enterViewZsmlKskm?'+urlencode({key:self.row[key] for key in ['zsmlbh','yjfx','xk']}))
        self.exams=[{'kmzbh':'001','kscc':str(slot),'kskm':course} for slot,course in enumerate(
            ['101思想政治理论','204英语（二）','302数学（二）','408计算机学科专业基础'],1)]
        self.html=json.dumps(self.exams,ensure_ascii=False)
        self.leaf=self.save('leaf',self.html,'https://yanzhao.ujn.edu.cn/zsZsmlJbxxMng/queryZsmlKskmList',
            {'conditions':json.dumps({'zsmlbh':'catalog-001'},separators=(',',':')),'_gridInfo':'{}'})
        self.leaf={**self.leaf,'catalog_parser':'public_form_v1','public_form_context':{'profile':'ezui',
            'entry_document_id':'entry','listing_document_id':'listing','exam_page_id':'page','row_identity':'catalog-001','dictionary_document_ids':['dict']}}
        self.docs={**self.docs,'leaf':self.leaf}

    # The inherited HTML-format tests are intentionally not used for a distinct JSON profile.
    def test_ezui_identity_exams_use_actual_structured_fields(self):
        parsed=self.parse();row=parsed['programs'][0]
        self.assertEqual(row['id'],'368278:2027:101:085404:00:全日制')
        self.assertEqual([s['code'] for s in row['subjects']],['101','204','302','408'])
        self.assertEqual(row['evidence'][0]['kind'],'structured_fields')
        self.assertEqual(row['evidence'][0]['structured_fields']['organCode'],'101')
        self.assertTrue(all(ref.get('quote') in (None,'') for ref in row['exam_evidence']))
        from service_verification import citation_signature
        self.assertTrue(all(citation_signature(ref) is not None for ref in row['evidence']))
        self.assertTrue(row['exam_matching_supported'])

    def test_ezui_wrong_row_context_and_final_exam_stages_rejected(self):
        document={**self.leaf,'public_form_context':{**self.leaf['public_form_context'],'row_identity':'other'}}
        with self.assertRaises(ValueError):self.parse(document=document)
        extra=[*self.exams,{'kmzbh':'001','kscc':'1','kskm':'F001面试','kskmjd':'2'}]
        html=json.dumps(extra,ensure_ascii=False)
        fresh=self.save('leaf',html,self.leaf['url'],{'conditions':json.dumps({'zsmlbh':'catalog-001'},separators=(',',':')),'_gridInfo':'{}'})
        leaf={**fresh,'catalog_parser':'public_form_v1','public_form_context':self.leaf['public_form_context']}
        row=self.parse(html=html,document=leaf)['programs'][0]
        self.assertEqual(len(row['subjects']),4)

    def test_ezui_conditional_combinations_are_preserved_and_not_flattened_as_independent_choices(self):
        extra=[*self.exams,*[{**x,'kmzbh':'002','kskm':{'204英语（二）':'201英语（一）','302数学（二）':'301数学（一）'}.get(x['kskm'],x['kskm'])} for x in self.exams]]
        html=json.dumps(extra,ensure_ascii=False)
        fresh=self.save('leaf',html,self.leaf['url'],{'conditions':json.dumps({'zsmlbh':'catalog-001'},separators=(',',':')),'_gridInfo':'{}'})
        leaf={**fresh,'catalog_parser':'public_form_v1','public_form_context':self.leaf['public_form_context']}
        row=self.parse(html=html,document=leaf)['programs'][0]
        self.assertFalse(row['exam_matching_supported']);self.assertEqual(len(row['exam_combinations']),2)
        self.assertEqual(len(row['subjects']),6)

    def test_ezui_explicit_unused_slots_and_same_group_language_options(self):
        examples=[self.exams[:3]+[{'kmzbh':'001','kscc':'4','kskm':'--无'}],
                  self.exams+[{'kmzbh':'001','kscc':'2','kskm':'203日语'}]]
        for raw,count in zip(examples,[3,5]):
            html=json.dumps(raw,ensure_ascii=False)
            fresh=self.save('leaf',html,self.leaf['url'],{'conditions':json.dumps({'zsmlbh':'catalog-001'},separators=(',',':')),'_gridInfo':'{}'})
            leaf={**fresh,'catalog_parser':'public_form_v1','public_form_context':self.leaf['public_form_context']}
            row=self.parse(html=html,document=leaf)['programs'][0]
            self.assertEqual(len(row['subjects']),count);self.assertTrue(row['exam_matching_supported'])

    def test_ezui_scope_replays_real_listing_and_rejects_missing_or_duplicate_responses(self):
        scope=public_form_scope(self.unit,2027,self.docs,project_root=self.root)
        self.assertTrue(scope['complete']);self.assertEqual(scope['expected_records'],1)
        with_more={**self.docs,'copy':{**self.leaf,'id':'copy'}}
        self.assertFalse(public_form_scope(self.unit,2027,with_more,project_root=self.root)['complete'])
        self.save('listing',json.dumps([self.row,{**self.row,'zsmlbh':'unfetched'}],ensure_ascii=False),self.listing['url'],
            {'conditions':json.dumps({'nd':'2027','yxsbh':None,'xkbh':None,'xxfs':None},separators=(',',':')),'_gridInfo':'{}'})
        self.assertFalse(public_form_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])

    def test_ezui_proof_chain_rejects_foreign_host_tampered_year_and_unknown_mode(self):
        for key,change in [('entry',{'url':'https://evil.edu.cn/zsml/enterViewZsml?nd=2027'}),
                           ('listing',{'year':2026}),('dict',{'request_body':'key=private'})]:
            old=self.docs[key];self.docs={**self.docs,key:{**old,**change}}
            with self.assertRaises(ValueError):self.parse()
            self.docs={**self.docs,key:old}
        self.docs={**self.docs,'dict':{**self.dictionary,'sha256':'0'*64}}
        with self.assertRaises(ValueError):self.parse()

    def test_ezui_refuses_unproven_exam_stage_or_missing_primary_exam_slot(self):
        for raw in [[{**x,'kskmjd':'hidden'} for x in self.exams],self.exams[:1]+self.exams[2:]]:
            html=json.dumps(raw,ensure_ascii=False)
            fresh=self.save('leaf',html,self.leaf['url'],{'conditions':json.dumps({'zsmlbh':'catalog-001'},separators=(',',':')),'_gridInfo':'{}'})
            leaf={**fresh,'catalog_parser':'public_form_v1','public_form_context':self.leaf['public_form_context']}
            with self.assertRaises(ValueError):self.parse(html=html,document=leaf)


class ZjuPublicFormTests(unittest.TestCase):
    save=PublicFormTests.save
    parse=PublicFormTests.parse
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.docs={}
        self.unit={'id':'368213','unit_code':'10335','name':'浙江大学','official_url':'https://yjsy.zju.edu.cn/'}
        self.entry=self.save('entry','<title>浙江大学研究生教育管理信息系统</title><script src="/js/app.test.js"></script>',
            'https://yjsy.zju.edu.cn/open/zsss/ZsssTksList?zsnf=2027')
        self.script=self.save('script','name:"ZsssTksList",title:"年硕士研究生招生专业目录查询（统考生）",url:{list:"/open/zsss/zsml?mllx=0"},check:"/open/zsss/checkZsmlNf",value:this.$route.query.zsnf',
            'https://yjsy.zju.edu.cn/js/app.test.js')
        self.annual=self.save('annual',json.dumps({'success':True,'result':'1'}),
            'https://yjsy.zju.edu.cn/dataapi/open/zsss/checkZsmlNf?zsnf=2027')
        self.faculty={'id':'faculty-01','departName':'教育学院','zsdm':'030'}
        self.options=self.save('options',json.dumps({'success':True,'result':[self.faculty]},ensure_ascii=False),
            'https://yjsy.zju.edu.cn/dataapi/open/zsss/queryXyList?mllx=0&zsnf=2027')
        self.row={'id':'direction-01','zsnf':'2027','sysOrgCode':'faculty-01','sysOrgCode_dictText':'教育学院',
            'ejxkdm':'040100','ejxkmc':'教育学','zyId_dictText':'教育学','yjfxXh':'02','yjfx':'课程与教学论',
            'xxfs':'1','xxfs_dictText':'全日制','xwlb':'0','xwlb_dictText':'学术学位','ksfs':'1','ksfs_dictText':'统考','fbzt':'1','spzt':'1',
            'kmxx':[{'kskm1dm':'101','kskm1mc':'思想政治理论','kskm2dm':'201','kskm2mc':'英语（一）','ywk1dm':'311','ywk1mc':'教育学专业基础','ywk2dm':'--','ywk2mc':'无'}]}
        self.set_rows([self.row])

    def set_rows(self,rows):
        self.html=json.dumps({'success':True,'code':200,'result':rows},ensure_ascii=False)
        self.leaf=self.save('leaf',self.html,'https://yjsy.zju.edu.cn/dataapi/open/zsss/zsml?'+urlencode(
            {'mllx':'0','zsnf':'2027','sysOrgCode':'faculty-01','pageNo':'1','pageSize':'30','column':'createTime','order':'desc','field':'id,'}))
        self.leaf={**self.leaf,'catalog_parser':'public_form_v1','public_form_context':{'profile':'zju',
            'entry_document_id':'entry','faculty_options_id':'options','annual_check_id':'annual','interface_script_id':'script'}}
        self.docs={**self.docs,'leaf':self.leaf}

    def test_zju_row_preserves_admission_major_direction_and_original_exam_fields(self):
        parsed=self.parse();self.assertEqual(parsed['issues'],[])
        row=parsed['programs'][0];self.assertEqual(row['id'],'368213:2027:030:040100:02:全日制')
        self.assertEqual(row['direction'],'02 课程与教学论');self.assertEqual(len(row['subjects']),3)
        self.assertEqual(row['evidence'][0]['field_path'],'result[0]');self.assertTrue(row['exam_matching_supported'])
        from service_verification import citation_signature
        self.assertTrue(all(citation_signature(ref) is not None for ref in row['evidence']))

    def test_zju_aggregate_and_single_exam_are_explicitly_excluded(self):
        self.set_rows([self.row,{**self.row,'id':'aggregate','yjfxXh':'JH','yjfx':'该专业以上方向合计招生数','kmxx':[]},
                       {**self.row,'id':'single','yjfxXh':'03','ksfs':'3','ksfs_dictText':'单独考试'}])
        parsed=self.parse();self.assertEqual(len(parsed['programs']),1);self.assertEqual(len(parsed['excluded_records']),2)
        self.assertEqual(parsed['expected_records'],1)

    def test_zju_wrong_year_private_flag_or_foreign_faculty_cannot_be_adopted(self):
        for change in [{'zsnf':'2026'},{'sysOrgCode':'other'},{'fbzt':'0'},{'xxfs_dictText':'非全日制'}]:
            self.set_rows([{**self.row,**change}]);parsed=self.parse();self.assertFalse(parsed['programs']);self.assertTrue(parsed['issues'])

    def test_zju_actual_options_cover_all_faculties_and_changed_scope_is_incomplete(self):
        scope=public_form_scope(self.unit,2027,self.docs,project_root=self.root);self.assertTrue(scope['complete'])
        self.options=self.save('options',json.dumps({'success':True,'result':[self.faculty,{'id':'other','departName':'其他学院','zsdm':'040'}]},ensure_ascii=False),
            'https://yjsy.zju.edu.cn/dataapi/open/zsss/queryXyList?mllx=0&zsnf=2027')
        self.assertFalse(public_form_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])

    def test_zju_requires_actual_linked_script_and_published_annual_check(self):
        for key,change in [('script',{'url':'https://evil.edu.cn/js/app.test.js'}),
                           ('annual',{'url':'https://yjsy.zju.edu.cn/dataapi/open/zsss/checkZsmlNf?zsnf=2026'}),
                           ('options',{'sha256':'0'*64})]:
            old=self.docs[key];self.docs={**self.docs,key:{**old,**change}}
            with self.assertRaises(ValueError):self.parse()
            self.docs={**self.docs,key:old}

    def test_zju_unexplained_course_omission_blocks_full_scope_without_dropping_other_rows(self):
        self.set_rows([self.row,{**self.row,'id':'missing-exams','yjfxXh':'03','kmxx':[]}])
        result=self.parse();self.assertEqual(len(result['programs']),1);self.assertTrue(result['issues'])
        self.assertFalse(public_form_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])


if __name__=='__main__':unittest.main()
