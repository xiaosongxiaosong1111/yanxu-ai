"""The official 2026 Nankai transfer table, with presentation markup removed."""
import copy
import unittest

from collectors.history_transfer import parse_transfer_html
from grad_engine import match_historical_rules


URL = 'https://cc.nankai.edu.cn/2026/0407/c13297a597352/page.htm'
CONTEXT = dict(unit_id='10055', unit_name='南开大学', department='密码与网络空间安全学院',
               year=2026, url=URL, document_id='official-transfer-2026')
# Text and merged header structure checked against the actual public HTML on 2026-10-28.
ANNOUNCEMENT = '''<p>一、接收调剂专业</p>
<p>我院接收调剂专业为：密码（专业代码：145200）。</p>
<p>二、申请调剂的基本条件</p>
<p>调剂考生须符合《南开大学2026年硕士研究生招生简章》规定的报考条件，
同时调剂考生一志愿学习方式须为全日制，一志愿报考专业和考生科目须满足以下条件：</p>
<table><tr><td rowspan="2">初试专业代码</td><td rowspan="2">初试专业名称</td>
<td colspan="4">初试科目要求</td><td colspan="5">初试成绩要求</td></tr>
<tr><td>政治理论名称</td><td>外国语名称</td><td>业务课一名称</td><td>业务课二名称</td>
<td>总分</td><td>政治理论</td><td>外国语</td><td>业务课一</td><td>业务课二</td></tr>
<tr><td>081200</td><td>计算机科学与技术</td><td>思想政治理论</td>
<td>英语（一）或英语（二）</td><td>数学（一）或数学（二）</td><td>计算机学科专业基础</td>
<td>290</td><td>50</td><td>50</td><td>75</td><td>75</td></tr>
<tr><td>083900</td><td>网络空间安全</td><td>思想政治理论</td>
<td>英语（一）或英语（二）</td><td>数学（一）或数学（二）</td><td>计算机学科专业基础</td>
<td>290</td><td>50</td><td>50</td><td>75</td><td>75</td></tr>
<tr><td>070100</td><td>数学</td><td>思想政治理论</td><td>英语（一）</td>
<td>数学分析</td><td>高等代数</td><td>360</td><td>50</td><td>50</td><td>90</td><td>90</td></tr>
<tr><td>085400</td><td>电子信息</td><td>思想政治理论</td>
<td>英语（一）或英语（二）</td><td>数学（一）或数学（二）</td><td>计算机学科专业基础</td>
<td>290</td><td>50</td><td>50</td><td>75</td><td>75</td></tr>
<tr><td>085400</td><td>电子信息</td><td>思想政治理论</td><td>英语（二）</td>
<td>数学（二）</td><td>自动控制原理</td><td>360</td><td>50</td><td>50</td><td>90</td><td>90</td></tr>
<tr><td>085400</td><td>电子信息</td><td>思想政治理论</td><td>英语（二）</td>
<td>数学（二）</td><td>电子综合基础</td><td>360</td><td>50</td><td>50</td><td>90</td><td>90</td></tr>
</table><p>三、调剂流程</p>'''


def parse(html=ANNOUNCEMENT, **changes):
    return parse_transfer_html(html, **{**CONTEXT, **changes})


def routes(rule):
    return next(condition['any'] for condition in rule['requirements'] if 'any' in condition)


def conditions(route):
    return {item['field']: item for item in route['all']}


def facts(code='081200', scores=(290, 50, 50, 75, 75), subjects=('101', '204', '302', '408')):
    return {'study_mode': '全日制', 'first_choice_program_code': code,
            **dict(zip(('total', 'politics', 'foreign_language', 'business1', 'business2'), scores)),
            'subjects': [{'code': code, 'scope': 'national', 'exam_unit': index}
                         for index, code in enumerate(subjects, 1)]}


class TransferParserTests(unittest.TestCase):
    def test_actual_merged_headers_and_six_alternative_routes(self):
        result = parse()
        self.assertEqual((result['seen_rows'], result['parsed_rows']), (6, 6))
        self.assertTrue(result['source_table_complete'])
        self.assertEqual(len(result['transfer_rules']), 1)
        rule = result['transfer_rules'][0]
        self.assertEqual((rule['program_code'], rule['program_name']), ('145200', '密码'))
        self.assertTrue(rule['requires_review'])
        self.assertTrue(rule['historical_reference'])
        options = [conditions(route) for route in routes(rule)]
        self.assertEqual([row['first_choice_program_code']['value'] for row in options],
                         ['081200', '083900', '070100', '085400', '085400', '085400'])
        self.assertEqual([row['total']['value'] for row in options], [290, 290, 360, 290, 360, 360])
        self.assertEqual([row['business1']['value'] for row in options], [75, 75, 90, 75, 90, 90])

    def test_each_field_has_original_evidence_and_scope(self):
        rule = parse()['transfer_rules'][0]
        for route in routes(rule):
            self.assertEqual(route['evidence']['document_id'], CONTEXT['document_id'])
            self.assertEqual(route['evidence']['url'], URL)
            self.assertIn(conditions(route)['first_choice_program_code']['value'], route['evidence']['quote'])
            for item in route['all']:
                self.assertTrue(item['evidence']['quote'])
                self.assertEqual(item['evidence']['url'], URL)
        self.assertIn('我院接收调剂专业为', rule['evidence']['quote'])

    def test_or_options_are_in_same_exam_unit_and_all_verified_national_codes(self):
        subjects = conditions(routes(parse()['transfer_rules'][0])[0])['subjects']['value']
        self.assertEqual([subject['code'] for subject in subjects], ['101', '201', '204', '301', '302', '408'])
        self.assertTrue(all(subject['scope'] == 'national' for subject in subjects))
        self.assertEqual({subject['exam_unit'] for subject in subjects}, {1, 2, 3, 4})
        for unit in (2, 3):
            options = [subject for subject in subjects if subject['exam_unit'] == unit]
            self.assertEqual(len({subject['choice_group'] for subject in options}), 1)

    def test_self_set_names_without_codes_are_unknown_not_fabricated(self):
        options = routes(parse()['transfer_rules'][0])
        for index, names in [(2, ['数学分析', '高等代数']), (4, ['自动控制原理']), (5, ['电子综合基础'])]:
            unknown = [subject for subject in conditions(options[index])['subjects']['value']
                       if subject['scope'] == 'unknown']
            self.assertEqual([subject['name'] for subject in unknown], names)
            self.assertTrue(all(subject['code'] is None for subject in unknown))

    def test_study_mode_requires_explicit_restrictive_original_wording(self):
        variants = [ANNOUNCEMENT.replace('一志愿学习方式须为全日制', '一志愿学习方式另行核实'),
                    ANNOUNCEMENT.replace('一志愿学习方式须为全日制', '一志愿学习方式不限全日制'),
                    ANNOUNCEMENT.replace('一志愿学习方式须为全日制', '一志愿学习方式可为全日制')]
        for html in variants:
            with self.subTest(html=html[:30]):
                requirements = parse(html)['transfer_rules'][0]['requirements']
                self.assertFalse(any(item.get('field') == 'study_mode' for item in requirements))
        self.assertEqual(parse()['transfer_rules'][0]['requirements'][0]['value'], '全日制')

    def test_missing_target_or_table_never_invents_a_rule(self):
        for html in ['', '<p>普通通知</p>', ANNOUNCEMENT.replace('我院接收调剂专业为：密码（专业代码：145200）。', '')]:
            result = parse(html)
            self.assertEqual(result['transfer_rules'], [])
            self.assertFalse(result['source_table_complete'])
            self.assertTrue(result['warnings'])
        no_table = ANNOUNCEMENT.split('<table>')[0]
        self.assertEqual(parse(no_table)['transfer_rules'], [])

    def test_malformed_or_missing_scores_remain_unknown_constraints(self):
        for value in ('待定', '', 'nan', 'inf', '-1', '501'):
            result = parse(ANNOUNCEMENT.replace('<td>290</td>', '<td>' + value + '</td>', 1))
            self.assertEqual(result['seen_rows'], 6)
            self.assertEqual(len(routes(result['transfer_rules'][0])), 6)
            self.assertIsNone(conditions(routes(result['transfer_rules'][0])[0])['total']['value'])
            self.assertFalse(result['source_table_complete'])
            self.assertTrue(result['warnings'])

    def test_no_hardcoded_target_or_row_count_and_input_remains_unchanged(self):
        html = ANNOUNCEMENT.replace('密码（专业代码：145200）', '测试专业（专业代码：123456）')
        html = html[:html.index('<tr><td>083900')] + '</table>'
        original = str(html)
        result = parse(html, document_id=None)
        self.assertEqual(html, original)
        self.assertEqual(result['seen_rows'], 1)
        self.assertEqual(result['transfer_rules'][0]['program_code'], '123456')
        self.assertTrue(result['transfer_rules'][0]['evidence']['document_id'])

    def test_context_validation(self):
        for changes in ({'year': True}, {'year': 1999}, {'year': '2026'}, {'url': ''}, {'unit_id': None},
                        {'document_id': ''}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                parse(**changes)
        with self.assertRaises((ValueError, TypeError)):
            parse(None)


class TransferEngineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.rules = parse()['transfer_rules']

    def test_qualified_historical_route_keeps_unreviewed_eligibility_unknown(self):
        original = copy.deepcopy(self.rules)
        result = match_historical_rules(self.rules, facts(), 2026)
        self.assertEqual((len(result['matched']), len(result['excluded']), len(result['needs_confirmation'])), (0, 0, 1))
        self.assertEqual(self.rules, original)
        self.assertEqual(result['needs_confirmation'][0]['reasons'][1]['status'], 'matched')

    def test_either_english_and_either_math_are_accepted(self):
        for english in ('201', '204'):
            for math in ('301', '302'):
                result = match_historical_rules(self.rules, facts(subjects=('101', english, math, '408')), 2026)
                self.assertEqual(len(result['needs_confirmation']), 1)
                self.assertEqual(result['needs_confirmation'][0]['reasons'][1]['status'], 'matched')

    def test_cannot_mix_professional_route_360_scores_with_290_408_route(self):
        result = match_historical_rules(self.rules, facts('085400', scores=(360, 50, 50, 90, 90),
                                                         subjects=('101', '204', '302', '999')), 2026)
        self.assertEqual(len(result['needs_confirmation']), 1)
        # Self-set course identity is unknown, so name alone cannot falsely exclude all alternatives.
        self.assertEqual(result['needs_confirmation'][0]['reasons'][1]['status'], 'unknown')
        result = match_historical_rules(self.rules, facts('085400', scores=(359, 50, 50, 90, 90),
                                                         subjects=('101', '204', '302', '999')), 2026)
        self.assertEqual(len(result['excluded']), 1)

    def test_total_single_subject_mode_and_major_conflicts_exclude(self):
        variants = [facts(scores=(289, 50, 50, 75, 75)), facts(scores=(290, 49, 50, 75, 75)),
                    {**facts(), 'study_mode': '非全日制'}, facts('999999')]
        for data in variants:
            self.assertEqual(len(match_historical_rules(self.rules, data, 2026)['excluded']), 1)

    def test_missing_score_and_different_year_never_imply_opportunity(self):
        result = match_historical_rules(self.rules, {**facts(), 'total': None}, 2026)
        self.assertEqual(len(result['needs_confirmation']), 1)
        self.assertTrue(result['historical_reference'])
        self.assertEqual(match_historical_rules(self.rules, facts(), 2028)['needs_confirmation'], [])


if __name__ == '__main__':
    unittest.main()
