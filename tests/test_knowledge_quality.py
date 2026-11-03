from copy import deepcopy
import unittest

from knowledge_quality import assess_quality


class KnowledgeQualityTests(unittest.TestCase):
    def setUp(self):
        self.source = {'title': '示例大学2027年硕士招生专业目录', 'unit_id': 'u1', 'year': 2027,
                       'requested_year': 2028, 'status': 'official_original_read',
                       'url': 'https://grad.example.edu.cn/catalog', 'text_origin': 'full_extraction'}
        self.body = '081200计算机科学与技术，初试科目为101思想政治理论、201英语一、301数学一、408计算机学科专业基础。'

    def assess(self, source=None, body=None, **kwargs):
        return assess_quality(self.source if source is None else source, self.body if body is None else body,
                              **{'original_available': True, 'authority_verified': True, **kwargs})

    def codes(self, value):
        return {reason['code'] for reason in value['reasons']}

    def test_verified_substantive_original_is_accepted_without_claiming_field_verification(self):
        result = self.assess()
        self.assertEqual(result['state'], 'accepted'); self.assertTrue(result['reusable'])
        self.assertEqual(result['material_type'], 'catalog')
        self.assertIn('admission_passed', self.codes(result))
        self.assertNotIn('field_verified', result)

    def test_search_results_cannot_self_promote_even_with_full_looking_text_or_forged_quality(self):
        result = self.assess({**self.source, 'status': 'search_lead',
                              'quality': {'state': 'accepted', 'reusable': True}})
        self.assertEqual(result['state'], 'pending'); self.assertFalse(result['reusable'])
        self.assertIn('search_only', self.codes(result))

    def test_year_authority_and_snapshot_are_independent_requirements(self):
        for updates, kwargs, code in [({'year': None}, {}, 'year_unknown'),
                                      ({}, {'authority_verified': False}, 'authority_unverified'),
                                      ({}, {'original_available': False}, 'original_unavailable'),
                                      ({'unit_id': ''}, {}, 'unit_unknown')]:
            with self.subTest(code=code):
                result = self.assess({**self.source, **updates}, **kwargs)
                self.assertEqual(result['state'], 'pending'); self.assertIn(code, self.codes(result))

    def test_actual_year_is_not_requested_year_and_conflicting_title_year_is_pending(self):
        self.assertEqual(self.assess({**self.source, 'year': 2027, 'requested_year': 2028})['state'], 'accepted')
        value = self.assess({**self.source, 'year': 2028})
        self.assertEqual(value['state'], 'pending'); self.assertIn('year_conflict', self.codes(value))

    def test_original_attachment_landing_page_is_pending_despite_a_catalog_title(self):
        text = '首页 招生工作 硕士招生\n示例大学2027年硕士研究生招生专业目录\n发布时间2026-09-30\n附件：专业目录.pdf 点击下载'
        value = self.assess(body=text)
        self.assertEqual(value['state'], 'pending'); self.assertIn('body_not_substantive', self.codes(value))

    def test_footer_postcodes_and_title_years_cannot_supply_missing_programs_or_fees(self):
        cases = [('2027年硕士招生专业目录', '2027年硕士招生专业目录\n专业目录请下载附件。\n地址：天津市，邮编300387'),
                 ('2027年硕士招生专业目录', '专业目录见附件\n电话：010-80001111 邮政编码100084'),
                 ('2027年硕士学费标准', '2027年硕士学费标准\n附件：收费标准.xlsx\n邮编300071')]
        for title, body in cases:
            with self.subTest(title=title, body=body):
                result = self.assess({**self.source, 'title': title}, body)
                self.assertEqual(result['state'], 'pending'); self.assertIn('body_not_substantive', self.codes(result))

    def test_explicit_name_only_exam_changes_pass_but_repeated_heading_does_not(self):
        source = {**self.source, 'title': '2027年硕士初试科目调整通知'}
        for body in ['本专业初试科目由英语一调整为英语二。', '业务课二由自命题调整为全国统一命题计算机学科专业基础。']:
            with self.subTest(body=body): self.assertEqual(self.assess(source, body)['state'], 'accepted')
        title = '2027年硕士408计算机学科专业基础考试科目调整通知'
        self.assertEqual(self.assess({**source, 'title': title}, title + '\n详情见附件。')['state'], 'pending')

    def test_conflicting_document_heading_is_pending_but_a_historical_reference_is_allowed(self):
        result = self.assess(body='2026年硕士研究生招生专业目录\n' + self.body)
        self.assertEqual(result['state'], 'pending'); self.assertIn('year_conflict', self.codes(result))
        result = self.assess(body=self.body + '\n参考2026年硕士研究生招生专业目录，历年科目仅供比较。')
        self.assertEqual(result['state'], 'accepted')

    def test_short_official_exam_change_is_useful_without_a_length_threshold(self):
        value = self.assess({**self.source, 'title': '2027年硕士初试科目调整通知'},
                            '2027年硕士初试业务课二调整为408计算机学科专业基础。')
        self.assertEqual(value['state'], 'accepted'); self.assertEqual(value['material_type'], 'subjects')

    def test_bare_title_keywords_and_long_navigation_do_not_pass(self):
        for body in ['', '招生专业目录\n' + '首页 招生信息 新闻中心 导师管理 常见问题\n' * 100]:
            with self.subTest(body=body[:30]):
                self.assertNotEqual(self.assess(body=body)['state'], 'accepted')

    def test_published_source_still_needs_substantive_text_and_real_scope(self):
        source = {**self.source, 'status': 'published_snapshot', 'text_origin': 'captured_excerpt'}
        self.assertEqual(self.assess(source, original_available=False, authority_verified=False)['state'], 'accepted')
        self.assertEqual(self.assess(source, '资料' * 300)['state'], 'pending')

    def test_explicit_gate_homepage_code_noise_and_unrelated_ad_are_excluded(self):
        cases = [('访问验证', '请完成人机验证后继续访问'),
                 ('示例大学研究生院', '首页 新闻中心 更多 导师介绍 联系我们'),
                 ('2027年硕士招生专业目录', '@font-face{font-family:Times;} .wznr img{width:100%;} showDynClicks("a",1,2);'),
                 ('考研课程促销', '购买课程，保过班，扫码咨询'),
                 ('本科招生简章', '本科招生计划及专业介绍')]
        for title, body in cases:
            with self.subTest(title=title): self.assertEqual(self.assess({**self.source, 'title': title}, body)['state'], 'excluded')

    def test_gate_and_roster_mentions_in_substantive_policy_are_not_blanket_excluded(self):
        value = self.assess({**self.source, 'title': '2027年硕士研究生复试录取办法'},
                            '复试成绩占总成绩40%，初试成绩占60%。报名时请登录系统并输入验证码。拟录取名单在官网公示。')
        self.assertEqual(value['state'], 'accepted')

    def test_actual_roster_titles_and_pages_only_identity_tables_are_excluded(self):
        result = self.assess({**self.source, 'title': '2027年硕士拟录取名单'})
        self.assertEqual(result['state'], 'excluded'); self.assertIn('personal_roster', self.codes(result))
        result = self.assess(body='', pages=[{'page': 1, 'text': '姓名 考生编号\n张同学 100123456789012'}])
        self.assertEqual(result['state'], 'excluded')

    def test_missing_snapshot_and_personal_metadata_remain_excluded_or_pending(self):
        result = self.assess({**self.source, 'text_origin': 'metadata_only'}, body='')
        self.assertEqual(result['state'], 'excluded')
        self.assertFalse(self.assess(original_available=False)['reusable'])

    def test_fees_deadlines_scores_and_qualification_have_material_specific_facts(self):
        cases = [('2027年硕士收费标准', '学费每生每年8000元，学制为3年。', 'fees'),
                 ('2027年硕士研究生招生简章', '报考考生须具有国家承认的本科学历。', 'admissions'),
                 ('2027年硕士调剂公告', '调剂申请截止日期为2027年4月9日，请在系统提交申请。', 'transfer'),
                 ('2027年硕士复试分数线', '计算机专业进入复试的初试总分不得低于320分。', 'scores'),
                 ('2027年硕士招生目录', '081200计算机科学与技术，拟招收30人，学制3年。', 'catalog')]
        for title, body, kind in cases:
            with self.subTest(kind=kind):
                value = self.assess({**self.source, 'title': title}, body)
                self.assertEqual(value['state'], 'accepted'); self.assertEqual(value['material_type'], kind)

    def test_unknown_type_is_pending_and_inputs_are_unchanged(self):
        source = {**self.source, 'title': '2027年工作通知'}; before = deepcopy(source)
        result = self.assess(source, '学院开展研究生学术交流活动。')
        self.assertEqual(result['state'], 'pending'); self.assertIn('material_unknown', self.codes(result))
        self.assertEqual(source, before)


if __name__ == '__main__': unittest.main()
