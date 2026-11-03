import unittest
from copy import deepcopy
from collectors.catalog_html_south import parse_catalog_html_south

UNIT={'id':'1','name':'测试大学','official_url':'https://test.edu.cn/','province':'测试省'}
DOC={'id':'d','unit_id':'1','year':2027,'url':'https://test.edu.cn/catalog','kind':'catalog'}
EXAM='①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础'

class SouthernCatalogTests(unittest.TestCase):
    def parse(self,body,document=None):
        return parse_catalog_html_south('<h1>测试大学2027年硕士研究生招生专业目录</h1>'+body,UNIT,2027,document or DOC)

    def test_published_southern_headers_and_training_mode_are_exact(self):
        result=self.parse('<table><tr><th>学院代码及学院名称</th><th>专业代码及专业名称</th><th>研究方向</th><th>培养方式</th><th>初试考试科目</th></tr>'
            f'<tr><td>001商务学院</td><td>081200计算机科学与技术</td><td>不区分研究方向</td><td>全日制</td><td>{EXAM}</td></tr></table>')
        self.assertEqual(result['issues'],[])
        row=result['programs'][0]
        self.assertEqual((row['department_code'],row['name'],row['direction']),('001','计算机科学与技术','不区分研究方向'))
        self.assertEqual(row['study_mode'],'全日制')
        self.assertEqual(result['expected_records'],1)

    def test_only_decorations_between_exam_ordinal_and_code_are_removed_from_fields(self):
        original='①▲101思想政治理论②▲201英语（一）③▲302数学（二）④★802化工原理'
        body='<table><tr><th>专业代码及名称</th><th>学习方式</th><th>研究方向</th><th>初试科目</th></tr>'
        body+=f'<tr><td>0817化学工程与技术</td><td>全日制</td><td>1.绿色化工</td><td>{original}</td></tr></table>'
        before=deepcopy(body);result=self.parse(body)
        self.assertEqual(result['issues'],[])
        row=result['programs'][0]
        self.assertEqual([s['code'] for s in row['subjects']],['101','201','302','802'])
        self.assertEqual(row['subjects'][-1]['name'],'化工原理')
        self.assertTrue(all(s['evidence']['quote']==original for s in row['subjects']))
        self.assertEqual(row['raw_source']['initial_exam'],original)
        self.assertEqual(body,before)

    def test_exact_note_mode_is_used_but_applicant_full_time_qualification_is_not(self):
        head='<table><tr><th>招生专业</th><th>考试科目</th><th>备注</th></tr>'
        good=self.parse(head+f'<tr><td>081200计算机科学与技术</td><td>{EXAM}</td><td>全日制</td></tr></table>')
        self.assertEqual(good['issues'],[])
        self.assertEqual(good['programs'][0]['study_mode'],'全日制')
        bad=self.parse(head+f'<tr><td>081200计算机科学与技术</td><td>{EXAM}</td><td>限全日制本科生报考</td></tr></table>')
        self.assertFalse(bad['programs'])
        self.assertTrue(bad['issues'])

    def test_embedded_mode_and_department_contact_do_not_change_literal_subjects(self):
        result=self.parse('<table><tr><th>学院代码学院名称联系电话</th><th>专业代码专业名称学习形式</th><th>研究方向</th><th>初试科目</th></tr>'
            f'<tr><td>030金融学院0991-7842101</td><td>020204金融学（全日制）</td><td>01货币政策</td><td>{EXAM}</td></tr></table>')
        self.assertEqual(result['issues'],[])
        row=result['programs'][0]
        self.assertEqual((row['department_code'],row['department'],row['name']),('030','金融学院','金融学'))
        self.assertEqual(row['study_mode'],'全日制')
        self.assertIn('020204金融学（全日制）',[ref['quote'] for ref in row['evidence']])

    def test_hierarchical_parent_mode_and_dash_direction_keep_original_quotes(self):
        result=self.parse('<table><tr><th>院系所、专业、研究方向</th><th>招生人数</th><th>考试科目</th><th>备注</th></tr>'
            '<tr><td>000不区分院系所</td><td>8</td><td></td><td></td></tr>'
            '<tr><td>081903安全技术及工程(全日制)</td><td>4</td><td></td><td></td></tr>'
            f'<tr><td>--01粉尘防爆技术</td><td></td><td>{EXAM}</td><td></td></tr></table>')
        self.assertEqual(result['issues'],[])
        row=result['programs'][0]
        self.assertEqual((row['department_code'],row['code'],row['direction'],row['study_mode']),('000','081903','01粉尘防爆技术','全日制'))
        self.assertIn('--01粉尘防爆技术',[ref['quote'] for ref in row['evidence']])
        self.assertIn('081903安全技术及工程(全日制)',[ref['quote'] for ref in row['evidence']])

    def test_missing_extra_table_header_remains_a_gap(self):
        body=f'<table><tr><th>专业</th><th>学习方式</th><th>考试科目</th></tr><tr><td>081200计算机科学与技术</td><td>全日制</td><td>{EXAM}</td></tr></table>'
        body+=f'<table><tr><td>083500软件工程</td><td>全日制</td><td>{EXAM}</td></tr></table>'
        result=self.parse(body)
        self.assertEqual(len(result['programs']),1)
        self.assertTrue(result['issues'])

    def test_foreign_unit_wrong_year_and_wrong_origin_cannot_be_authorized_by_metadata(self):
        body=f'<table><tr><th>专业</th><th>学习方式</th><th>考试科目</th></tr><tr><td>081200计算机科学与技术</td><td>全日制</td><td>{EXAM}</td></tr></table>'
        for doc in [{**DOC,'unit_id':'other'},{**DOC,'year':2026},{**DOC,'url':'https://other.edu.cn/catalog'}]:
            with self.subTest(doc=doc):self.assertFalse(self.parse(body,doc)['programs'])
        result=parse_catalog_html_south('<h1>测试大学2026年硕士研究生招生专业目录</h1>'+body,UNIT,2027,DOC)
        self.assertFalse(result['programs'])


    def test_leaf_major_in_direction_column_keeps_its_six_digit_identity(self):
        head='<table><tr><th>专业代码及名称</th><th>学习方式</th><th>研究方向</th><th>初试科目</th><th>招生单位</th></tr>'
        result=self.parse(head+f'<tr><td>0451教育</td><td>全日制</td><td>045103学科教学（语文）</td><td>{EXAM}</td><td>人文学院</td></tr>'
            f'<tr><td>0451教育</td><td>全日制</td><td>045109学科教学（历史）</td><td>{EXAM}</td><td>人文学院</td></tr></table>')
        self.assertEqual(result['issues'],[])
        self.assertEqual({p['code'] for p in result['programs']},{'045103','045109'})
        self.assertTrue(all(p['direction']=='' and p['direction_scope']=='program' for p in result['programs']))
        self.assertEqual(len({p['id'] for p in result['programs']}),2)

    def test_separate_code_name_columns_keep_explicit_faculty_namespace(self):
        result=self.parse('<table><tr><th>院系所</th><th>专业代码</th><th>专业（领域）</th><th>研究方向</th><th>学习方式</th><th>初试科目</th></tr>'
            f'<tr><td>001体育学院</td><td>040301</td><td>体育人文社会学</td><td>01社会体育</td><td>全日制</td><td>{EXAM}</td></tr>'
            f'<tr><td>002新闻学院</td><td>040301</td><td>体育人文社会学</td><td>01体育新闻</td><td>全日制</td><td>{EXAM}</td></tr></table>')
        self.assertEqual(result['issues'],[])
        self.assertEqual({p['department_code'] for p in result['programs']},{'001','002'})
        self.assertEqual(len({p['id'] for p in result['programs']}),2)

    def test_catalog_row_with_unrecognized_identity_is_not_silently_consumed(self):
        result=self.parse('<table><tr><th>专业</th><th>其他专业代码</th><th>学习方式</th><th>考试科目</th></tr>'
            f'<tr><td>081200计算机科学与技术</td><td></td><td>全日制</td><td>{EXAM}</td></tr>'
            f'<tr><td>名称待核</td><td>083500</td><td>全日制</td><td>{EXAM}</td></tr></table>')
        self.assertEqual(len(result['programs']),1)
        self.assertTrue(result['issues'])



    def test_both_embedded_modes_and_degree_enum_are_not_single_mode_defaults(self):
        result=self.parse('<table><tr><th>学院代码学院名称联系电话</th><th>专业代码专业名称学习形式</th><th>研究方向</th><th>初试科目</th></tr>'
            '<tr><td>041公共管理学院</td><td>125200公共管理（专业学位）（全日制、非全日制）</td><td>01政府治理</td><td>①199管理类综合能力②204英语（二）</td></tr></table>')
        self.assertEqual(result['issues'],[])
        self.assertEqual({p['study_mode'] for p in result['programs']},{'全日制','非全日制'})
        self.assertTrue(all(p['name']=='公共管理' for p in result['programs']))
        result=self.parse('<table><tr><th>学位类别</th><th>专业及研究方向</th><th>考试科目</th></tr>'
            f'<tr><td>全日制学术学位</td><td>081900矿业工程01安全工程</td><td>{EXAM}</td></tr></table>')
        self.assertEqual(result['issues'],[])
        self.assertEqual(result['programs'][0]['study_mode'],'全日制')
        self.assertEqual(result['programs'][0]['degree_type'],'学术学位')

    def test_mixed_width_numeric_direction_ordinals_preserve_all_published_numbers(self):
        directions=''.join(str(i)+'.医学方向'+str(i)+'；' for i in range(1,22))
        result=self.parse('<table><tr><th>专业代码及名称</th><th>学习方式</th><th>研究方向</th><th>初试科目</th></tr>'
            f'<tr><td>1051临床医学</td><td>全日制</td><td>{directions}</td><td>①101思想政治理论②201英语（一）③306临床医学综合能力（西医）</td></tr></table>')
        self.assertEqual(result['issues'],[])
        self.assertEqual(result['expected_records'],21)
        self.assertEqual(len(result['programs']),21)
        self.assertEqual(result['programs'][0]['direction'],'1.医学方向1')
        self.assertEqual(result['programs'][-1]['direction'],'21.医学方向21')
        self.assertFalse(any(p['direction'].startswith('01') for p in result['programs']))

    def test_exact_current_year_whole_school_mode_statement_is_not_undergraduate_mode(self):
        body=f'<table><tr><th>专业</th><th>考试科目</th></tr><tr><td>081200计算机科学与技术</td><td>{EXAM}</td></tr></table>'
        for clause in ['2027年仅招收全日制硕士研究生，不招收非全日制硕士研究生。','我校仅招收参加全国统一考试的全日制硕士研究生。','培养的硕士研究生类型为全日制硕士，学制3年。']:
            result=self.parse('<p>'+clause+'</p>'+body)
            self.assertEqual(result['issues'],[],clause)
            self.assertEqual({p['study_mode'] for p in result['programs']},{'全日制'})
            self.assertEqual(len(result['programs']),1)
        result=self.parse('<p>报考人员应为全日制重点高校本科应届、往届毕业生。</p>'+body)
        self.assertFalse(result['programs'])
        self.assertTrue(result['issues'])



    def test_recommendation_only_and_single_exam_rows_never_enter_unified_candidates(self):
        head='<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试方式</th><th>初试科目</th><th>备注</th></tr>'
        result=self.parse(head+f'<tr><td>081200计算机科学与技术</td><td>01方向甲</td><td>全日制</td><td>21全国统考</td><td>{EXAM}</td><td>仅接收推免生</td></tr>'
            f'<tr><td>083500软件工程</td><td>01方向乙</td><td>全日制</td><td>23单独考试</td><td>{EXAM}</td><td></td></tr></table>')
        self.assertFalse(result['programs'])
        self.assertEqual(result['expected_records'],0)
        self.assertEqual(result['excluded_recommendation_only'],1)
        self.assertEqual(result['excluded_non_unified_exam'],1)
        self.assertEqual(result['issues'],[])

    def test_incomplete_or_duplicate_candidate_rows_stay_explicit_gaps(self):
        result=self.parse('<table><tr><th>专业</th><th>学习方式</th><th>考试科目</th></tr>'
            '<tr><td>081200计算机科学与技术</td><td>全日制</td><td>①101思想政治理论②201英语（一）④408计算机学科专业基础</td></tr></table>')
        self.assertFalse(result['programs']);self.assertEqual(result['expected_records'],1);self.assertTrue(result['issues'])
        row=f'<tr><td>081200计算机科学与技术</td><td>全日制</td><td>{EXAM}</td></tr>'
        result=self.parse('<table><tr><th>专业</th><th>学习方式</th><th>考试科目</th></tr>'+row+row+'</table>')
        self.assertEqual(len(result['programs']),2)
        self.assertTrue(any('重复' in issue for issue in result['issues']))

    def test_reference_section_and_later_true_catalog_header_are_separate(self):
        head='<tr><th>专业</th><th>学习方式</th><th>考试科目</th></tr>'
        result=self.parse('<table>'+head+f'<tr><td>081200计算机科学与技术</td><td>全日制</td><td>{EXAM}</td></tr>'
            '<tr><td colspan="3">参考书目及考试大纲</td></tr><tr><td>040101</td><td>参考资料</td><td>书目说明</td></tr>'+head+
            f'<tr><td>083500软件工程</td><td>全日制</td><td>{EXAM}</td></tr></table>')
        self.assertEqual(result['issues'],[])
        self.assertEqual({p['code'] for p in result['programs']},{'081200','083500'})



    def test_faculty_contact_column_and_hierarchical_contact_row_keep_real_faculties(self):
        result=self.parse('<table><tr><th>专业代码</th><th>专业名称</th><th>招生学院及联系方式</th><th>学习方式</th><th>初试科目</th></tr>'
            f'<tr><td>050101</td><td>文艺学</td><td>001文学院联系人：甲老师联系方式：023-58105613</td><td>全日制</td><td>{EXAM}</td></tr></table>')
        self.assertEqual(result['issues'],[])
        self.assertEqual(result['programs'][0]['department_code'],'001')
        self.assertEqual(result['programs'][0]['department'],'文学院')
        self.assertIn('001文学院联系人：甲老师联系方式：023-58105613',[ref['quote'] for ref in result['programs'][0]['evidence']])
        result=self.parse('<table><tr><th>院系所、专业</th><th>学习方式</th><th>研究方向</th><th>考试科目</th></tr>'
            '<tr><td>001教育科学学院</td><td colspan="3">联系人：甲老师联系电话：18212661122</td></tr>'
            f'<tr><td>045101教育管理</td><td>全日制</td><td>不区分研究方向</td><td>{EXAM}</td></tr></table>')
        self.assertEqual(result['issues'],[])
        self.assertEqual((result['programs'][0]['department_code'],result['programs'][0]['department']),('001','教育科学学院'))


if __name__=='__main__':unittest.main()
