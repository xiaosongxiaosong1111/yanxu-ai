"""An official homepage is an entry, not the requested year's document."""
import unittest

import official_search


def page(title, path, host='grad.example.edu.cn'):
    return {'title': title, 'url': 'https://' + host + path}


class OfficialTargetLadderTests(unittest.TestCase):
    def ladder(self, responses, **changes):
        calls = []
        def call(body):
            calls.append(body)
            return {'Pages': responses[len(calls)-1]}
        result = official_search.run_ladder(call, queries=['precise', 'wider'], site='example.edu.cn',
            is_official=lambda url: url.startswith('https://grad.example.edu.cn/'),
            unit_name='样本大学', roots=('example.edu.cn',), candidate_enabled=True,
            target_match=lambda row: official_search.target_matches(row, 2027, 'catalog'), **changes)
        return result, calls

    def test_homepage_and_old_catalog_do_not_end_requested_document_search(self):
        result, calls = self.ladder([[page('样本大学首页', '/')],
                                    [page('样本大学2026年硕士专业目录', '/old')],
                                    [page('样本大学2027年硕士招生专业目录', '/current')]])
        self.assertEqual(len(calls), 3)
        self.assertEqual(result['target_status'], 'found')
        self.assertEqual([row['url'] for row in result['target_results']],
                         ['https://grad.example.edu.cn/current'])
        self.assertEqual(len(result['navigation_results']), 2)

    def test_no_target_keeps_verified_entry_but_does_not_claim_document_found(self):
        result, calls = self.ladder([[page('样本大学首页', '/')], [], []])
        self.assertEqual(result['target_status'], 'navigation_only')
        self.assertEqual(len(result['official_results']), 1)
        self.assertEqual(result['target_results'], [])

    def test_bound_unit_new_domain_candidate_survives_an_existing_entry_result(self):
        candidate = page('样本大学2027年硕士招生专业目录', '/catalog', 'admission.example.ac.cn')
        result, _ = self.ladder([[page('样本大学首页', '/'), candidate], [], []])
        self.assertEqual(len(result['candidate_results']), 1)
        self.assertEqual(result['candidate_results'][0]['source_tier'], 'official_domain_candidate')
        self.assertEqual(result['target_results'], [])

    def test_target_match_uses_real_title_year_and_material_not_url_or_summary(self):
        cases = [({'title': '2026招生专业目录', 'url': 'https://example.edu.cn/2027'}, False),
                 ({'title': '2027年度招生通知', 'summary': '专业目录2027'}, False),
                 ({'title': '二〇二七年硕士研究生招生专业目录'}, True),
                 ({'title': '20270年硕士研究生招生专业目录'}, False),
                 ({'title': '2027年硕士研究生招生简章'}, False),
                 ({'title': '2027年硕士招生专业目录（未发布）'}, False),
                 ({'title': '2027年硕士招生专业目录（未公布）'}, False),
                 ({'title': '预计2027年硕士专业目录'}, False)]
        for row, expected in cases:
            with self.subTest(row=row):
                self.assertEqual(official_search.target_matches(row, 2027, 'catalog'), expected)

    def test_real_charter_titles_and_pdf_advertisements_do_not_hide_the_target(self):
        for title in ('复旦大学2027年招收攻读硕士学位研究生章程',
                      '青海师范大学2027年硕士学位研究生简章',
                      '川北医学院二〇二七年硕士研究生招生简章与专业目录'):
            with self.subTest(title=title):
                self.assertTrue(official_search.target_matches({'title': title}, 2027, 'charter'))

    def test_regular_exam_target_does_not_stop_on_recommendation_only_charter(self):
        for title in ('2027年接收推荐免试攻读硕士学位研究生招生简章',
                      '2027年推免生招生专业目录', '2027年优秀大学生夏令营招生简章'):
            with self.subTest(title=title):
                self.assertFalse(official_search.target_matches({'title': title}, 2027, 'charter'))


if __name__ == '__main__':
    unittest.main()
