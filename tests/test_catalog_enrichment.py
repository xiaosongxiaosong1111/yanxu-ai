import copy
import unittest

from collectors.catalog_enrichment import apply_inline_policies, extract_inline_policies
from service_verification import canonical, plain_text


TITLE = '<h1>测试大学2027年硕士研究生招生章程及招生专业目录</h1>'
HEADER = '<tr><th>学位类型</th><th>授位类别</th><th>专业</th><th>学费（每生每年）</th><th>住宿费（每生每年）</th><th>学制</th><th>学习形式</th></tr>'


def row(name, fee, mode='全日制', years='3年'):
    return f'<tr><td>专业学位</td><td>工程硕士</td><td>{name}</td><td>{fee}元</td><td>1200元</td><td>{years}</td><td>{mode}</td></tr>'


class CatalogEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.unit = {'id': '1', 'name': '测试大学', 'official_url': None}
        self.doc = {'id': 'd', 'unit_id': '1', 'year': 2027, 'status': 'parsed',
                    'url': 'https://yz.chsi.com.cn/sch/viewZszc--infoId-1,schId-1.dhtml', 'sha256': 'a' * 64}

    def program(self, name='计算机技术', mode='全日制', **extra):
        return {'id': name + str(mode), 'unit_id': '1', 'year': 2027, 'name': name,
                'code': '085404', 'direction': '00 不区分研究方向', 'study_mode': mode,
                'tuition': None, 'duration_years': None, 'requires_review': True,
                'eligibility_evidence_complete': False, **extra}

    def test_explicit_table_matches_name_mode_and_keeps_original_quotes(self):
        html = TITLE + '<table>' + HEADER + row('<p>计算机技术</p><p>人工智能</p>', 10000) + row('公共管理', 20000, '非全日制') + row('公共管理', 13000) + '</table>'
        original = [self.program(), self.program('公共管理', '非全日制'), self.program('公共管理')]
        before = copy.deepcopy(original)
        actual = apply_inline_policies(original, html, self.unit, 2027, self.doc)
        self.assertEqual([p['tuition']['amount'] for p in actual], [10000, 20000, 13000])
        self.assertTrue(all(p['duration_years'] == 3 for p in actual))
        self.assertEqual(original, before)
        for program in actual:
            self.assertEqual(program['tuition']['period'], 'year')
            self.assertEqual(program['tuition']['currency'], 'CNY')
            self.assertFalse(program['eligibility_evidence_complete'])
            self.assertTrue(program['requires_review'])
            for evidence in program['tuition']['evidence'] + program['duration_evidence']:
                self.assertEqual(evidence['url'], self.doc['url'])
                self.assertEqual(evidence['page'], 1)
                self.assertIn(canonical(evidence['quote']), canonical(plain_text(html)))

    def test_joint_project_rates_never_apply_to_ordinary_programs(self):
        html = TITLE + '<table>' + HEADER + row('材料与化工（中外合作办学）', 33000) + row('材料工程', 10000) + '</table>'
        programs = [self.program('材料与化工'), self.program('材料与化工', notes=['中外合作办学项目']), self.program('材料工程')]
        actual = apply_inline_policies(programs, html, self.unit, 2027, self.doc)
        self.assertIsNone(actual[0]['tuition'])
        self.assertEqual(actual[1]['tuition']['amount'], 33000)
        self.assertEqual(actual[2]['tuition']['amount'], 10000)

    def test_ambiguous_values_or_unknown_mode_are_not_filled(self):
        html = TITLE + '<table>' + HEADER + row('计算机技术', 10000) + row('计算机技术', 12000) + '</table>'
        actual = apply_inline_policies([self.program(), self.program(mode=None)], html, self.unit, 2027, self.doc)
        self.assertIsNone(actual[0]['tuition'])
        self.assertEqual(actual[0]['duration_years'], 3)
        self.assertIsNone(actual[1]['tuition'])

    def test_already_known_values_are_not_overwritten(self):
        html = TITLE + '<table>' + HEADER + row('计算机技术', 10000) + '</table>'
        program = self.program(tuition={'amount': 9000, 'period': 'year'}, duration_years=2)
        self.assertEqual(apply_inline_policies([program], html, self.unit, 2027, self.doc), [program])

    def test_wrong_school_year_or_access_state_fail_closed(self):
        html = TITLE + '<table>' + HEADER + row('计算机技术', 10000) + '</table>'
        for document, body in [({**self.doc, 'unit_id': '2'}, html), ({**self.doc, 'year': 2026}, html),
                               ({**self.doc, 'url': 'https://yz.chsi.com.cn/sch/viewZszc--schId-2.dhtml'}, html),
                               ({**self.doc, 'status': 'needs_review'}, html), (self.doc, html.replace('2027', '2026'))]:
            with self.subTest(document=document, body=body[:50]):
                self.assertIsNone(apply_inline_policies([self.program()], body, self.unit, 2027, document)[0]['tuition'])

    def test_three_column_table_uses_explicit_equal_mode_statement_and_not_scholarships(self):
        html = TITLE + '<p>学校招收的硕士研究生为专业学位硕士研究生，学制为3年，最长学习年限为5年。</p>'
        html += '<table><tr><th>学位点（专业类别及代码）</th><th>招生专业</th><th>收费标准</th></tr><tr><td>教育（0451）</td><td>小学教育</td><td>8000元/生·年</td></tr></table>'
        html += '<p>全日制学生与非全日制学生收费标准一致。</p>'
        html += '<table><tr><th>类型</th><th>标准</th><th>备注</th></tr><tr><td>奖学金</td><td>12000元/生·年</td><td>评选</td></tr></table>'
        actual = apply_inline_policies([self.program('小学教育'), self.program('小学教育', '非全日制'), self.program('数学')], html, self.unit, 2027, self.doc)
        self.assertEqual([p['tuition']['amount'] for p in actual[:2]], [8000, 8000])
        self.assertEqual(actual[0]['duration_years'], 3)
        self.assertIsNone(actual[2]['tuition'])
        self.assertEqual(len(extract_inline_policies(html, self.unit, 2027, self.doc)['candidates']), 1)

    def test_missing_fee_period_or_invalid_money_does_not_become_annual_fee(self):
        for header, fee in [(HEADER.replace('每生每年', '全程'), '30000'), (HEADER, '10000—12000'), (HEADER, '待定')]:
            html = TITLE + '<table>' + header + row('计算机技术', fee) + '</table>'
            self.assertIsNone(apply_inline_policies([self.program()], html, self.unit, 2027, self.doc)[0]['tuition'])

    def test_general_policy_is_same_school_year_and_does_not_use_longest_study_years(self):
        html = TITLE + '<p>所有学科专业学制均为3年。我校各招生专业学费均为8000元/人/学年，住宿费为1000~1200元/人/学年。最长学习年限为5年。</p>'
        programs = [self.program(), self.program(year=2026), self.program(unit_id='2'), self.program(mode=None)]
        actual = apply_inline_policies(programs, html, self.unit, 2027, self.doc)
        self.assertEqual(actual[0]['tuition']['amount'], 8000)
        self.assertEqual(actual[0]['duration_years'], 3)
        self.assertTrue(all(p['tuition'] is None and p['duration_years'] is None for p in actual[1:]))

    def test_explicit_full_time_duration_exceptions_keep_accounting_and_translation_separate(self):
        html = TITLE + '<p>全日制：会计硕士学制2年；翻译硕士学制2.5年；其他各专业（领域）硕士学制均为3年。学费按国家批准执行。</p>'
        actual = apply_inline_policies([self.program('会计', code='125300'), self.program('英语笔译', code='055101'), self.program(), self.program(mode='非全日制')], html, self.unit, 2027, self.doc)
        self.assertEqual([p['duration_years'] for p in actual], [2, 2.5, 3, None])
        self.assertTrue(all(p['tuition'] is None for p in actual))
        self.assertEqual(len(actual[2]['duration_evidence']), 1)

    def test_indefinite_fee_does_not_hide_a_real_uniform_duration(self):
        html = TITLE + '<p>我所硕士研究生学制为3年，学习方式为全日制。学费按国家批准执行。</p>'
        actual = apply_inline_policies([self.program(), self.program(mode='非全日制')], html, self.unit, 2027, self.doc)
        self.assertEqual(actual[0]['duration_years'], 3)
        self.assertIsNone(actual[0]['tuition'])
        self.assertIsNone(actual[1]['duration_years'])

    def test_identical_names_in_different_degree_types_do_not_conflict(self):
        html = TITLE + '<table>' + HEADER + row('石油与天然气工程', 10000) + row('石油与天然气工程', 7000).replace('专业学位', '学术学位') + '</table>'
        actual = apply_inline_policies([self.program('石油与天然气工程', degree_type='专业学位'), self.program('石油与天然气工程', degree_type='学术学位')], html, self.unit, 2027, self.doc)
        self.assertEqual([p['tuition']['amount'] for p in actual], [10000, 7000])

    def test_short_name_cannot_borrow_fee_of_a_different_longer_name(self):
        html = TITLE + '<table>' + HEADER + row('材料科学与工程', 10000) + '</table>'
        actual = apply_inline_policies([self.program('材料科学'), self.program('材料科学与工程')], html, self.unit, 2027, self.doc)
        self.assertIsNone(actual[0]['tuition'])
        self.assertIsNone(actual[0]['duration_years'])
        self.assertEqual(actual[1]['tuition']['amount'], 10000)

    def test_reviewed_school_rules_require_exact_scope_sentence_and_code(self):
        cases = [
            ('367988', '我院2027年面向全国招收全日制学术型硕士和专业学位硕士研究生，学制3年。学费标准:学术型、专业型硕士生8000元/年。', 8000, 3),
            ('367986', '学费为每年8000元（生活困难学生可按我院奖助政策申请学费相应标准的减免）。', 8000, None),
            ('367782', '我院招收的硕士研究生培养方式是全日制，学制为3年，修业年限最长为4年。', None, 3),
            ('367898', '中监所全日制硕士研究生基本学制为3年，因特殊情况经批准最长可延期2年毕业。中监所硕士研究生学费为每生每年8000元。', 8000, 3),
            ('3416382019', '2027年我校“食品与营养”“资源与环境”2个专业学位硕士点计划招收全日制硕士研究生（详见《安康学院2027年硕士研究生招生专业目录》），学制三年。各招生专业学费为8000元/人&middot;学年。', 8000, 3),
        ]
        for unit_id, text, amount, duration in cases:
            unit = {**self.unit, 'id': unit_id}
            doc = {**self.doc, 'unit_id': unit_id, 'url': f'https://yz.chsi.com.cn/sch/viewZszc--schId-{unit_id}.dhtml'}
            actual = apply_inline_policies([self.program(unit_id=unit_id), self.program(unit_id=unit_id, mode='非全日制')], TITLE + '<p>' + text + '</p>', unit, 2027, doc)
            with self.subTest(unit_id=unit_id):
                self.assertEqual((actual[0]['tuition'] or {}).get('amount'), amount)
                self.assertEqual(actual[0]['duration_years'], duration)
                self.assertIsNone(actual[1]['tuition'])
                self.assertIsNone(actual[1]['duration_years'])

    def test_liaoning_part_time_specific_degree_prefixes_do_not_fill_other_programs(self):
        html = TITLE + '<p>非全日制（一次性缴纳）：会计硕士、工商管理硕士学制2.5年；工学专业学位学制均为3.5年。学费按国家批准执行。</p>'
        actual = apply_inline_policies([self.program('会计', mode='非全日制', code='125300'), self.program(mode='非全日制'), self.program('金融', mode='非全日制', code='025100')], html, self.unit, 2027, self.doc)
        self.assertEqual([p['duration_years'] for p in actual], [2.5, 3.5, None])

    def test_reviewed_official_parenthetical_labels_require_unit_code_and_full_name(self):
        unit = {**self.unit, 'id': '302975907'}
        doc = {**self.doc, 'unit_id': unit['id'], 'url': 'https://yz.chsi.com.cn/sch/viewZszc--schId-302975907.dhtml'}
        html = TITLE + '<table>' + HEADER + row('新一代电子信息技术', 10000) + '</table>'
        programs = [self.program('新一代电子信息技术（含量子技术等）', unit_id=unit['id'], code='085401'), self.program('新一代电子信息技术（含量子技术等）', unit_id=unit['id'], code='085402')]
        actual = apply_inline_policies(programs, html, unit, 2027, doc)
        self.assertEqual(actual[0]['tuition']['amount'], 10000)
        self.assertIsNone(actual[1]['tuition'])


if __name__ == '__main__':
    unittest.main()
