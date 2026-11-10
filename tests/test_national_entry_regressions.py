"""Entry boundaries replay through real originals, queue proofs and selection."""
import tempfile
import unittest
from pathlib import Path

import national_data_baseline as baseline
import tests.test_national_data_baseline as baseline_cases


class EntryCodeProvenanceTests(unittest.TestCase):
    def test_imported_entry_logic_is_bound_even_when_cli_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('national_data_baseline.py', 'national_entry_html.py',
                         'national_entry_proof.py'):
                (root / name).write_bytes(b'original\r\n')
            before = baseline._code_hashes(root)
            (root / 'national_entry_html.py').write_bytes(b'changed\n')
            after = baseline._code_hashes(root)
            self.assertEqual(before['national_data_baseline.py'],
                             after['national_data_baseline.py'])
            self.assertNotEqual(before['national_entry_html.py'],
                                after['national_entry_html.py'])
            (root / 'national_entry_proof.py').write_bytes(b'original\n')
            self.assertEqual(before['national_entry_proof.py'],
                             baseline._code_hashes(root)['national_entry_proof.py'])

    def test_audit_report_binds_all_entry_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, repo = baseline_cases.AuditTests().make_fixture(root)
            report = baseline.audit(data, root / 'out', repo_root=repo)
            self.assertEqual(set(report['code_sha256']), {
                'national_data_baseline.py', 'national_entry_html.py',
                'national_entry_proof.py'})
            self.assertEqual(report['module_sha256'],
                             report['code_sha256']['national_data_baseline.py'])


class EntryBoundaryRegressions(unittest.TestCase):
    def setUp(self):
        self.fixture = baseline_cases.EntryRoleReplayTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def decision(self, article, **kwargs):
        _, unit, record = self.fixture.make(article, return_context=True, **kwargs)
        registry, annual = {'units': [unit]}, {'units': [record]}
        proofs = baseline.verify_entries(registry, annual, self.fixture.data)
        coverage = [{
            'unit_id': 'u1', 'program_count': '0', 'fact_count': '0',
            'catalog_status': 'not_collected', 'collection_status': 'collected',
            'source_count': '1', 'missing_reason': '官方专业目录待解析。',
            'original_urls': 'https://a.edu.cn/zsml/2027.htm'}]
        queue = baseline.build_queue(coverage, {}, {}, baseline._units(registry, annual),
                                     entry_proofs=proofs)
        picks = baseline.select_batch(queue, expansion=1, backfill=0)
        return proofs['u1'], queue[0], picks['expansion']

    def assert_allowed(self, article, **kwargs):
        proof, row, picks = self.decision(article, **kwargs)
        self.assertEqual(proof['status'], 'verified', proof['reason'])
        self.assertTrue(row['entry_trusted'])
        self.assertEqual([item['unit_id'] for item in picks], ['u1'])

    def assert_rejected(self, article, **kwargs):
        proof, row, picks = self.decision(article, **kwargs)
        self.assertEqual(proof['status'], 'rejected')
        self.assertFalse(row['entry_trusted'])
        self.assertEqual(picks, [])

    @staticmethod
    def anchor(target, label='https://a.edu.cn/admissions'):
        return f'<p>本校官网：<a href="{target}">{label}</a></p>'

    def test_non_default_port_in_visible_url_is_a_different_target(self):
        self.assert_rejected('本校官网：https://a.edu.cn:8443/admissions',
                             official_url='https://a.edu.cn/admissions')

    def test_non_default_port_in_anchor_is_a_different_target(self):
        self.assert_rejected(self.anchor('https://a.edu.cn:8443/admissions'),
                             official_url='https://a.edu.cn/admissions')

    def test_default_https_port_is_equivalent(self):
        self.assert_allowed(self.anchor('https://a.edu.cn:443/admissions'),
                            official_url='https://a.edu.cn/admissions')

    def test_same_explicit_non_default_port_remains_valid(self):
        url = 'https://a.edu.cn:8443/admissions'
        self.assert_allowed(self.anchor(url, url), official_url=url)

    def test_encoded_path_separator_does_not_prove_another_path(self):
        self.assert_rejected(self.anchor('https://a.edu.cn/admissions%2Fother',
                                         'https://a.edu.cn/admissions/other'),
                             official_url='https://a.edu.cn/admissions/other')

    def test_same_encoded_reserved_path_remains_valid(self):
        url = 'https://a.edu.cn/admissions%2Fother'
        self.assert_allowed(self.anchor(url, url), official_url=url)

    def test_percent_encoded_unreserved_letter_is_equivalent(self):
        self.assert_allowed(self.anchor('https://a.edu.cn/%61dmissions'),
                            official_url='https://a.edu.cn/admissions')

    def test_url_embedded_in_another_url_query_is_not_an_entry(self):
        self.assert_rejected(
            '本校官网：https://evil.example/path?next=(https://a.edu.cn/)')

    def test_query_subdelimiters_do_not_start_an_independent_target(self):
        for delimiter in (',', ';', "'"):
            with self.subTest(delimiter=delimiter):
                self.assert_rejected(
                    f'本校官网：https://evil.example/path?next={delimiter}https://a.edu.cn/')

    def test_chinese_query_text_does_not_expose_a_nested_url(self):
        self.assert_rejected('本校官网：https://evil.example/?next=中文(https://a.edu.cn/)')

    def test_fullwidth_query_parentheses_do_not_expose_a_nested_url(self):
        self.assert_rejected('本校官网：https://evil.example/?next=中文（https://a.edu.cn/）')

    def test_parenthesized_explicit_iri_remains_valid(self):
        self.assert_allowed('本校官网：（https://a.edu.cn/招生）',
                            official_url='https://a.edu.cn/招生')

    def test_ascii_entry_before_chinese_publication_prose_remains_valid(self):
        cases = [
            ('本校官网：https://a.edu.cn（查询途径：招生导览）。', 'https://a.edu.cn'),
            ('我校建立了奖助体系，请访问网站https://a.edu.cn/jxj/进行查询。',
             'https://a.edu.cn/jxj/'),
            ('学校官方网站https://a.edu.cn公布的信息为准。', 'https://a.edu.cn'),
            ('学校研究生院官网(网址：https://a.edu.cn)公示。', 'https://a.edu.cn'),
        ]
        for article, target in cases:
            with self.subTest(article=article):
                self.assert_allowed(article, official_url=target)

    def test_explicit_iri_is_not_a_prefix_of_a_different_iri(self):
        self.assert_rejected('本校官网：https://a.edu.cn/招生目录',
                             official_url='https://a.edu.cn/招生')

    def test_exact_explicit_iri_remains_valid(self):
        self.assert_allowed('本校官网：https://a.edu.cn/招生',
                            official_url='https://a.edu.cn/招生')

    def test_parenthesized_independent_url_remains_valid(self):
        self.assert_allowed('本校官网：（https://a.edu.cn/）')

    def test_ascii_parenthesized_independent_url_remains_valid(self):
        self.assert_allowed('本校官网：(https://a.edu.cn/)')

    def test_registered_prefix_in_other_host_stays_rejected(self):
        self.assert_rejected('本校官网：https://a.edu.cn.attacker.example/')

    def test_nearest_foreign_owner_wins_for_all_role_categories(self):
        roles = [
            ('招生信息网', 'official_home_link'),
            ('研究生院网站', 'official_home_link'),
            ('招生专业目录', 'official_annual_catalog_link'),
            ('官方网站', 'official_home_link'),
        ]
        for role, relation in roles:
            with self.subTest(role=role):
                self.assert_rejected(
                    f'甲大学提醒考生访问丙大学{role}：https://a.edu.cn/',
                    relation=relation)

    def test_nearest_current_owner_remains_valid(self):
        self.assert_allowed('考生可登录甲大学研究生院网站：https://a.edu.cn/')

    def test_shared_university_suffix_is_not_an_ownership_alias(self):
        self.assert_rejected(
            '华东师范大学提醒考生访问东北师范大学研究生院网站：https://a.edu.cn/',
            unit_name='华东师范大学')

    def test_complete_current_university_name_remains_valid(self):
        self.assert_allowed('考生可登录华东师范大学研究生院网站：https://a.edu.cn/',
                            unit_name='华东师范大学')

    def test_generic_admissions_guidance_remains_valid(self):
        self.assert_allowed('甲大学招生安排如下，考生可登录学校网站：https://a.edu.cn/')

    def test_real_publication_guides_do_not_become_foreign_names(self):
        cases = [
            ('拟录取名单等均可在北京师范大学研究生招生网：https://a.edu.cn/', '北京师范大学'),
            ('复试参考书目将在北京市市政工程研究院网站：https://a.edu.cn/', '北京市市政工程研究院'),
            ('均在大连民族大学研究生招生网站：https://a.edu.cn/', '大连民族大学'),
            ('见上海交通大学研究生招生网：https://a.edu.cn/', '上海交通大学'),
            ('相关考试大纲及参考书目将在学校研究生招生网公布：https://a.edu.cn/', '衢州学院'),
            ('及学校研究生招生信息网：https://a.edu.cn/', '西安交通大学'),
        ]
        for article, name in cases:
            with self.subTest(name=name):
                self.assert_allowed(article, unit_name=name)

    def test_prose_guides_inside_exact_institution_names_are_not_removed(self):
        self.assert_allowed('请见和田学院研究生院网站：https://a.edu.cn/',
                            unit_name='和田学院')

    def test_unknown_entity_ending_with_current_name_is_not_an_alias(self):
        self.assert_rejected('东北京师范大学研究生院网站：https://a.edu.cn/',
                             unit_name='北京师范大学')

    def test_current_unit_named_article_keeps_generic_admissions_role(self):
        self.assert_allowed('<h1>甲大学2027年招生章程</h1>'
                            '<p>招生信息网：https://a.edu.cn/</p>')

    def test_hidden_article_container_cannot_be_extracted_as_visible(self):
        self.assert_rejected('<div class="yxk-news-contain" hidden>'
                             '<p>本校官网：https://a.edu.cn/</p></div>')

    def test_nonvoid_slash_does_not_close_hidden_html_container(self):
        self.assert_rejected('<div hidden/><p>本校官网：https://a.edu.cn/</p></div>')

    def test_nonvoid_slash_does_not_remove_hidden_article_ancestor(self):
        self.assert_rejected('<span hidden/><div class="yxk-news-contain">'
                             '<p>本校官网：https://a.edu.cn/</p></div></span>')

    def test_hidden_ancestor_of_article_container_stays_hidden(self):
        self.assert_rejected('<div hidden><div class="yxk-news-contain">'
                             '<p>本校官网：https://a.edu.cn/</p></div></div>')

    def test_visible_article_still_excludes_unrelated_page_chrome(self):
        self.assert_rejected('<p>本校官网：https://a.edu.cn/</p>'
                             '<div class="yxk-news-contain"><p>请查阅附件。</p></div>')

    def test_visible_article_container_remains_valid(self):
        self.assert_allowed('<div class="yxk-news-contain">'
                            '<p>本校官网：https://a.edu.cn/</p></div>')

    def test_display_none_with_tab_and_important_stays_hidden(self):
        self.assert_rejected('<div style="display:\tnone !important">'
                             '本校官网：https://a.edu.cn/</div>')

    def test_visibility_hidden_with_newline_and_important_stays_hidden(self):
        self.assert_rejected('<div style="visibility:\nhidden!important">'
                             '本校官网：https://a.edu.cn/</div>')

    def test_css_comment_before_hidden_value_stays_hidden(self):
        self.assert_rejected('<div style="display:/**/none !important">'
                             '本校官网：https://a.edu.cn/</div>')

    def test_css_comment_alone_does_not_hide_visible_text(self):
        self.assert_allowed('<p style="/* display:none */ color:blue">'
                            '本校官网：https://a.edu.cn/</p>')

    def test_css_comment_cannot_join_an_invalid_hidden_keyword(self):
        self.assert_allowed('<p style="display:no/**/ne">'
                            '本校官网：https://a.edu.cn/</p>')

    def test_hidden_css_whitespace_normalization_handles_form_feed(self):
        self.assert_rejected('<div style="display:\fnone !important">'
                             '本校官网：https://a.edu.cn/</div>')

    def test_similar_visible_css_property_is_not_hidden(self):
        self.assert_allowed('<p style="backface-visibility: hidden">'
                            '本校官网：https://a.edu.cn/</p>')

    def test_hidden_void_input_does_not_swallow_visible_following_text(self):
        self.assert_allowed('<input hidden name="example">'
                            '<p>本校官网：https://a.edu.cn/</p>')

    def test_hidden_void_img_does_not_swallow_visible_following_text(self):
        self.assert_allowed('<img hidden src="logo.png">'
                            '<p>本校官网：https://a.edu.cn/</p>')

    def test_self_closing_hidden_void_does_not_swallow_visible_text(self):
        self.assert_allowed('<input hidden name="example" />'
                            '<p>本校官网：https://a.edu.cn/</p>')

    def test_void_inside_hidden_parent_does_not_end_its_hidden_subtree(self):
        self.assert_rejected('<div hidden><input hidden><br>'
                             '<p>本校官网：https://a.edu.cn/</p></div>')

    def test_nested_hidden_parent_does_not_hide_later_visible_sibling(self):
        self.assert_allowed('<div hidden><input hidden><div>隐藏文字</div></div>'
                            '<p>本校官网：https://a.edu.cn/</p>')

    def test_hidden_rendered_chsi_article_rule_remains_supported(self):
        self.assert_allowed('学校网址：https://a.edu.cn/', hidden=True)


if __name__ == '__main__':
    unittest.main()
