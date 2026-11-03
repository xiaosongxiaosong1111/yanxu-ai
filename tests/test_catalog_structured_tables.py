"""Exact source-cell catalogues whose field labels differ from frozen adapters."""
import copy
import hashlib
import unittest

from tests.test_catalog_facts import source, table, UNIT, EXAM
from tests.test_catalog_office_national import workbook
from collectors.catalog_structured_tables import parse_html_catalog_structured, parse_office_catalog_structured


HEADERS=('招生学院','学科专业代码及名称','研究方向代码及名称','学习方式','学位类型','考试科目')


class StructuredCatalogTests(unittest.TestCase):
    def html(self,rows,headers=HEADERS,extra=''):
        raw,doc=source(extra+table(rows,headers))
        return raw,doc,parse_html_catalog_structured(raw,UNIT,2027,doc)

    def test_explicit_faculty_zero_direction_mode_and_degree_all_survive(self):
        raw,doc,result=self.html([('001基础医学院','071003生理学','00不区分研究方向','(1)全日制','学术型',EXAM)])
        self.assertEqual(result['issues'],[])
        self.assertEqual(len(result['programs']),1)
        row=result['programs'][0]
        self.assertEqual((row['department_code'],row['department'],row['direction'],row['degree_type']),('001','基础医学院','00不区分研究方向','学术学位'))
        self.assertEqual(row['id'],'123:2027:001:071003:00:全日制')
        self.assertTrue(all(ref['quote'] in raw.decode() for ref in row['evidence']+row['exam_evidence']))

    def test_uncoded_faculty_identity_does_not_collapse_two_colleges(self):
        _,_,result=self.html([('新材料学院','080500材料科学与工程','01材料物理','全日制','学硕',EXAM),
                              ('智能制造学院','080500材料科学与工程','01材料物理','全日制','学硕',EXAM)])
        self.assertEqual(len(result['programs']),2,result['issues'])
        self.assertEqual(len({p['id'] for p in result['programs']}),2)
        self.assertTrue(all(p['department_code'] is None for p in result['programs']))
        self.assertEqual({p['department'] for p in result['programs']},{'新材料学院','智能制造学院'})

    def test_physical_rowspan_keeps_its_origin_and_a_blank_cell_never_borrows(self):
        html=('<table><tr>'+''.join('<th>'+v+'</th>' for v in HEADERS)+'</tr>'
              '<tr><td rowspan="2">001信息学院</td><td rowspan="2">081200计算机科学与技术</td>'
              '<td>01人工智能</td><td rowspan="2">全日制</td><td rowspan="2">学术型</td><td rowspan="2">'+EXAM+'</td></tr>'
              '<tr><td>02网络安全</td></tr></table>')
        raw,doc=source(html);result=parse_html_catalog_structured(raw,UNIT,2027,doc)
        self.assertEqual(len(result['programs']),2,result['issues'])
        second=result['programs'][1]
        self.assertEqual(second['source_locator']['row'],3)
        self.assertEqual(second['exam_evidence'][0]['source_locator']['origin_row'],2)
        raw,doc=source(html.replace('rowspan="2"','rowspan="1"'))
        result=parse_html_catalog_structured(raw,UNIT,2027,doc)
        self.assertEqual(len(result['programs']),1)
        self.assertTrue(result['issues'])

    def test_unknown_mode_and_full_time_undergraduate_condition_are_not_inferred(self):
        for mode,major in [('', '081200计算机科学与技术'),('', '081200计算机科学与技术（仅招全日制本科毕业生）')]:
            _,_,result=self.html([('001信息学院',major,'01人工智能',mode,'学术型',EXAM)])
            self.assertEqual(result['programs'],[])
            self.assertTrue(result['issues'])

    def test_unconditional_whole_school_mode_is_cited_but_partial_mode_is_not(self):
        headers=('学院名称','招生专业','研究方向','初试考试科目')
        for claim,expected in [('我校硕士研究生招生专业学习方式为全日制。',1),('我校部分硕士研究生招生专业学习方式为全日制。',0)]:
            _,_,result=self.html([('信息学院','081200计算机科学与技术','01人工智能',EXAM)],headers,'<p>'+claim+'</p>')
            self.assertEqual(len(result['programs']),expected,result['issues'])
            if expected:self.assertTrue(any(claim in ref['quote'] for ref in result['programs'][0]['evidence']))

    def test_cross_slot_linked_choices_and_recommendation_only_rows_are_excluded(self):
        linked='①101思想政治理论②201英语一③301数学一或302数学二④801程序设计或802数据结构（须成套选择）'
        _,_,result=self.html([('001信息学院','081200计算机科学与技术','01人工智能','全日制','学术型',linked)])
        self.assertEqual(result['programs'],[])
        _,_,result=self.html([('001信息学院','081200计算机科学与技术','01人工智能','全日制','学术型',EXAM,'仅接收推免生')],(*HEADERS,'备注'))
        self.assertEqual(result['programs'],[])
        self.assertEqual(len(result['excluded_records']),1)

    def test_parent_with_multiple_field_codes_is_not_rewritten_to_a_guessed_field(self):
        _,_,result=self.html([('新材料学院','0856材料与化工（材料工程085601、化学工程085602）','01材料物理','全日制','专硕',EXAM)])
        self.assertEqual(result['programs'],[])
        self.assertTrue(result['issues'])

    def test_ordinary_or_choices_keep_every_option_within_the_right_slot(self):
        _,_,result=self.html([('001信息学院','081200计算机科学与技术','01人工智能','全日制','学术型',EXAM)])
        row=result['programs'][0]
        self.assertEqual([s['code'] for s in row['subjects']],['101','201','203','301','801'])
        self.assertTrue(all(s['choice_group']=='exam-unit-2' for s in row['subjects'] if s['exam_unit']==2))

    def test_wrong_source_hash_year_school_and_hidden_rows_are_rejected(self):
        raw,doc,_=self.html([('001信息学院','081200计算机科学与技术','01人工智能','全日制','学术型',EXAM)])
        for changed in [{'sha256':'0'*64},{'year':2026},{'unit_id':'999'}]:
            result=parse_html_catalog_structured(raw,UNIT,2027,{**doc,**changed})
            self.assertFalse(result['programs'])
        for title in ['其他大学2027年硕士研究生招生专业目录','样本大学2026年硕士研究生招生专业目录','样本大学2027年推荐免试硕士研究生招生目录']:
            changed,meta=source(table([('001信息学院','081200计算机科学与技术','01人工智能','全日制','学术型',EXAM)],HEADERS),title)
            self.assertFalse(parse_html_catalog_structured(changed,UNIT,2027,meta)['programs'])
        hidden,meta=source(table([('001信息学院','081200计算机科学与技术','01人工智能','全日制','学术型',EXAM)],HEADERS).replace('<tr><td>','<tr hidden><td>'))
        self.assertFalse(parse_html_catalog_structured(hidden,UNIT,2027,meta)['programs'])

    def test_real_office_cell_geometry_supplies_faculty_without_quota_text(self):
        data={'A1':'样本大学2027年硕士研究生全国统考招生专业目录','A2':'培养单位','B2':'招生专业',
              'C2':'研究方向','D2':'学习方式','E2':'考试科目',
              'A3':'001 作科所 拟招生人数：73（不含推免生）','B3':'071010生物化学与分子生物学',
              'C3':'01植物分子生物学','D3':'全日制','E3':EXAM,'C4':'02基因工程'}
        raw=workbook(data,['A3:A4','B3:B4','D3:D4','E3:E4'])
        doc={'id':'office','unit_id':'123','year':2027,'url':'https://grad.sample.edu.cn/catalog.xlsx','sha256':hashlib.sha256(raw).hexdigest()}
        original=copy.deepcopy(doc)
        result=parse_office_catalog_structured(raw,UNIT,2027,doc)
        self.assertEqual(len(result['programs']),2,result['issues'])
        self.assertEqual(result['programs'][0]['department'],'作科所')
        self.assertEqual(result['programs'][1]['exam_evidence'][0]['source_locator']['origin_row'],3)
        self.assertEqual(result['programs'][1]['source_locator']['sheet'],'目录')
        self.assertEqual(doc,original)
        self.assertFalse(parse_office_catalog_structured(raw,UNIT,2027,{**doc,'sha256':'0'*64})['programs'])

    def test_navigation_heading_without_a_year_does_not_replace_dated_article_h1(self):
        raw,doc,_=self.html([('001信息学院','081200计算机科学与技术','01人工智能','全日制','学术型',EXAM)])
        raw=raw.replace(b'<h1>', '<h2>全日制招生</h2><h1>'.encode(),1)
        doc={**doc,'sha256':hashlib.sha256(raw).hexdigest()}
        result=parse_html_catalog_structured(raw,UNIT,2027,doc)
        self.assertEqual(len(result['programs']),1,result['issues'])

    def test_literal_admissions_major_and_professional_direction_aliases_are_read(self):
        _,_,result=self.html([('035101法律（非法学）','不区分研究方向','全日制',EXAM)],
            ('招生专业代码及名称','专业研究方向','学习形式','考试科目'))
        self.assertEqual(len(result['programs']),1,result['issues'])
        self.assertEqual(result['programs'][0]['direction'],'不区分研究方向')

    def test_malformed_office_context_fails_without_dereferencing_untrusted_types(self):
        for unit,doc in [(None,{}),({},None)]:
            self.assertFalse(parse_office_catalog_structured(b'not office',unit,2027,doc)['programs'])

    def test_two_named_national_languages_under_one_explicit_unit_are_both_kept(self):
        exam='①101思想政治理论②201英语一202俄语③611体育基本理论'
        raw,_,result=self.html([('体育学院','040301体育人文社会学','01体育社会','全日制','学术型',exam)])
        self.assertEqual([s['code'] for s in result['programs'][0]['subjects']],['101','201','202','611'])
        self.assertTrue(all(s['scope']=='national' for s in result['programs'][0]['subjects'] if s['exam_unit']==2))
        self.assertTrue(all(ref['quote']==exam for ref in result['programs'][0]['exam_evidence']))
        self.assertNotIn('或',raw.decode())

    def test_an_unparsed_course_code_inside_a_name_is_a_gap_not_a_single_course(self):
        exam='①101思想政治理论②201英语一③611综合（参考612另一门综合）'
        _,_,result=self.html([('体育学院','040301体育人文社会学','01体育社会','全日制','学术型',exam)])
        self.assertFalse(result['programs'])
        self.assertTrue(result['issues'])

    def test_original_cell_evidence_retains_nonbreaking_spaces_at_both_edges(self):
        _,_,result=self.html([('001信息学院','\u00a0\u00a0081200计算机科学与技术\u00a0','01人工智能','全日制','学术型',EXAM)])
        quote=next(ref['quote'] for ref in result['programs'][0]['evidence'] if '081200' in ref['quote'])
        self.assertEqual(quote,'\u00a0\u00a0081200计算机科学与技术\u00a0')

    def test_duplicate_rows_merge_only_when_all_facts_match(self):
        row=('001信息学院','081200计算机科学与技术','01人工智能','全日制','学术型',EXAM)
        _,_,result=self.html([row,row])
        self.assertEqual(len(result['programs']),1)
        self.assertEqual(result['expected_records'],1)
        self.assertEqual(len(result['programs'][0]['duplicate_source_locators']),2)
        _,_,conflict=self.html([row,(*row[:-1],EXAM.replace('801专业基础','802程序设计'))])
        self.assertFalse(conflict['programs'])
        self.assertTrue(conflict['issues'])

    def test_unknown_header_does_not_hide_initial_exam_rows(self):
        _,_,result=self.html([('081200计算机科学与技术',EXAM)],('其他名称','其他内容'))
        self.assertFalse(result['programs'])
        self.assertTrue(result['issues'])

    def test_office_other_year_special_scope_and_unbound_publication_are_rejected(self):
        for title in ['样本大学2026年硕士研究生招生专业目录','样本大学2027年推荐免试硕士研究生招生目录',
                      '2027年硕士研究生招生专业目录']:
            raw=workbook({'A1':title,'A2':'招生专业','B2':'学习方式','C2':'考试科目',
                          'A3':'081200计算机科学与技术','B3':'全日制','C3':EXAM})
            doc={'id':'office','unit_id':'123','year':2027,'url':'https://grad.sample.edu.cn/catalog.xlsx','sha256':hashlib.sha256(raw).hexdigest()}
            self.assertFalse(parse_office_catalog_structured(raw,UNIT,2027,doc)['programs'])

    def test_conflicting_or_excepted_whole_school_claim_is_not_a_default_mode(self):
        rows=[('信息学院','081200计算机科学与技术','01人工智能',EXAM)]
        headers=('学院名称','招生专业','研究方向','考试科目')
        for claim in ['我校硕士研究生招生专业学习方式为全日制。我校硕士研究生招生专业学习方式为非全日制。',
                      '除法律硕士外，我校硕士研究生招生专业学习方式为全日制。']:
            _,_,result=self.html(rows,headers,'<p>'+claim+'</p>')
            self.assertFalse(result['programs'])
            self.assertTrue(result['issues'])

    def test_explicit_ambiguous_or_qualified_mode_cell_never_falls_back_to_body(self):
        for mode in ['全日制、非全日制','非全日制（定向）']:
            _,_,result=self.html([('信息学院','081200计算机科学与技术','01人工智能',mode,'学术型',EXAM)],
                extra='<p>我校硕士研究生招生专业学习方式为全日制。</p>')
            self.assertFalse(result['programs'])
            self.assertTrue(result['issues'])

    def test_unrecognised_table_with_unmarked_initial_codes_keeps_a_coverage_gap(self):
        known=table([('001信息学院','081200计算机科学与技术','01人工智能','全日制','学术型',EXAM)],HEADERS)
        unknown=table([('081300建筑学','101思想政治理论201英语（一）301数学（一）801建筑基础')],('未知字段','未知内容'))
        raw,doc=source(known+unknown);result=parse_html_catalog_structured(raw,UNIT,2027,doc)
        self.assertEqual(len(result['programs']),1)
        self.assertTrue(result['issues'])

    def test_negated_recommendation_only_notes_do_not_remove_unified_exam_rows(self):
        for note in ['并非仅接收推免生','本方向并非只接收推免生','不只接收推免生']:
            _,_,result=self.html([('001信息学院','081200计算机科学与技术','01人工智能','全日制','学术型',EXAM,note)],(*HEADERS,'备注'))
            self.assertEqual(len(result['programs']),1,result['issues'])
            self.assertFalse(result['excluded_records'])

    def test_explicit_numeric_pairing_notes_do_not_create_four_free_combinations(self):
        exam='①101思想政治理论②201英语（一）③301数学（一）或302数学（二）④801专业基础或802程序设计'
        for note in ['③301与④801搭配，③302与④802搭配。','第三单元301对应第四单元801，302对应802。']:
            _,_,result=self.html([('001信息学院','081200计算机科学与技术','01人工智能','全日制','学术型',exam,note)],(*HEADERS,'备注'))
            self.assertFalse(result['programs'])
            self.assertTrue(result['issues'])


if __name__=='__main__':unittest.main()
