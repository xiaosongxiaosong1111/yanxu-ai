"""Broad catalog retrieval keeps search coverage separate from evidence verification."""
import unittest

import official_search


class BroadCatalogQueryTests(unittest.TestCase):
    def test_removes_negative_subject_and_filler_but_keeps_scope_and_major(self):
        queries = official_search.broad_catalog_queries(
            '帮我找一下2027年京津冀计算机技术０８５４０４，不考４０８、３０１，硕士招生专业目录',
            2027, ('北京', '天津', '河北'), ('408', '301'))
        self.assertEqual(len(queries), 2)
        self.assertNotEqual(*queries)
        for query in queries:
            self.assertIn('京津冀', query)
            self.assertIn('085404', query)
            self.assertIn('计算机技术', query)
            self.assertEqual(query.count('2027'), 1)
            self.assertNotIn('408', query)
            self.assertNotIn('301', query)
            self.assertNotIn('帮我', query)
        self.assertIn('招生专业目录', queries[0])
        self.assertIn('自命题', queries[1])

    def test_adds_only_known_province_scope_and_keeps_positive_exam_codes(self):
        queries = official_search.broad_catalog_queries(
            '083500 软件工程 初试408', 2027, ('广东', '个人信息不外发'))
        self.assertEqual(len(queries), 2)
        for query in queries:
            self.assertIn('广东', query)
            self.assertIn('083500', query)
            self.assertIn('408', query)
            self.assertNotIn('个人信息不外发', query)
            self.assertNotIn('自命题', query)

    def test_generic_catalog_needs_no_second_query(self):
        self.assertEqual(official_search.broad_catalog_queries('2027年 硕士招生专业目录', 2027),
                         ['2027年 硕士研究生招生专业目录'])


class BroadCatalogLadderTests(unittest.TestCase):
    def capture(self, **options):
        calls = []
        def call(payload):
            calls.append(payload)
            return {'Pages': [{'title': '2027年硕士招生专业目录',
                               'url': 'https://example.edu.cn/' + str(len(calls))}],
                    'RequestId': str(len(calls))}
        result = official_search.run_ladder(
            call, queries=['primary', 'alternate'], is_official=lambda url: True,
            target_match=lambda row: official_search.target_matches(row, 2027, 'catalog'),
            **options)
        return result, calls

    def test_cross_school_search_can_continue_after_first_hit_and_union_results(self):
        result, calls = self.capture(stop_on_target=False)
        self.assertEqual(calls, [{'Query': 'primary'}, {'Query': 'alternate'}])
        self.assertEqual(result['request_count'], 2)
        self.assertEqual([row['url'] for row in result['target_results']],
                         ['https://example.edu.cn/1', 'https://example.edu.cn/2'])

    def test_named_defaults_preserve_early_stop_and_request_shape(self):
        result, calls = self.capture()
        self.assertEqual(calls, [{'Query': 'primary'}])
        self.assertEqual(result['request_count'], 1)

    def test_cross_school_search_still_obeys_request_budget(self):
        budget = iter([True, False])
        result, calls = self.capture(stop_on_target=False, can_call=lambda: next(budget))
        self.assertEqual(calls, [{'Query': 'primary'}])
        self.assertTrue(result['budget_exhausted'])
        self.assertEqual(result['target_status'], 'found')


if __name__ == '__main__':
    unittest.main()
