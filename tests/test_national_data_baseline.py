import unittest

import national_data_baseline as baseline


class ProfileTests(unittest.TestCase):
    def test_original_profile_counts_real_urls_without_double_counting(self):
        text = ('https://a.edu.cn/zsml/2027.htm\n'
                'https://a.edu.cn/zsml/2027.htm\n'
                'https://a.edu.cn/files/2027-zyml.pdf\n')
        profile = baseline.original_profile(text)
        self.assertEqual(profile['url_count'], 2)
        self.assertEqual(profile['pdf_count'], 1)
        self.assertEqual(profile['catalog_like_count'], 2)

    def test_original_profile_ignores_blank_and_non_http_rows(self):
        profile = baseline.original_profile('\n  \nftp://a.edu.cn/x.pdf\n')
        self.assertEqual(profile, {'url_count': 0, 'pdf_count': 0, 'catalog_like_count': 0, 'hosts': []})


class GapTests(unittest.TestCase):
    def row(self, **overrides):
        value = {'unit_id': '1', 'official_url': 'https://a.edu.cn/',
                 'catalog_status': 'not_collected', 'collection_status': 'collected',
                 'missing_reason': '已保存官方原文，专业目录尚待解析或核验，不能据此认定没有招生专业。'}
        value.update(overrides)
        return value

    def stats(self, **overrides):
        value = {'program_count': 0, 'full_qualification': 0, 'tuition': 0, 'duration': 0}
        value.update(overrides)
        return value

    def test_saved_originals_without_adopted_programs_are_parser_pending(self):
        gaps = baseline.classify_gaps(self.row(), self.stats())
        self.assertIn('missing_catalog', gaps)
        self.assertIn('parser_pending', gaps)
        self.assertNotIn('original_pending', gaps)

    def test_login_restricted_source_is_original_pending_not_no_data(self):
        gaps = baseline.classify_gaps(
            self.row(collection_status='needs_binding', official_url='',
                     missing_reason='来源要求登录或限制访问，尚未完成公开原文采集。'),
            self.stats())
        self.assertIn('missing_trusted_entry', gaps)
        self.assertIn('original_pending', gaps)

    def test_adopted_programs_report_field_gaps_and_review(self):
        gaps = baseline.classify_gaps(
            self.row(catalog_status='partial', collection_status='collected',
                     missing_reason='已有部分专业资料，全校目录及全部考试科目尚未对账完整。'),
            self.stats(program_count=12))
        self.assertIn('partial_catalog', gaps)
        self.assertIn('review_pending', gaps)
        self.assertIn('missing_full_qualification', gaps)
        self.assertIn('missing_tuition', gaps)
        self.assertIn('missing_duration', gaps)
        self.assertNotIn('missing_catalog', gaps)

    def test_complete_fields_do_not_report_missing_gaps(self):
        gaps = baseline.classify_gaps(
            self.row(catalog_status='recorded_verified'),
            self.stats(program_count=5, full_qualification=5, tuition=5, duration=5))
        self.assertEqual(gaps, [])

    def test_queue_without_programs_does_not_claim_field_gaps(self):
        gaps = baseline.classify_gaps(self.row(), self.stats())
        self.assertNotIn('missing_tuition', gaps)
        self.assertNotIn('missing_full_qualification', gaps)


class BatchTests(unittest.TestCase):
    def test_expansion_pool_prefers_catalog_like_originals_and_never_invents_units(self):
        rows = [
            {'unit_id': '3', 'unit_name': '丙', 'official_url': 'https://c.edu.cn/',
             'catalog_status': 'not_collected', 'collection_status': 'collected',
             'missing_reason': '已保存官方原文，专业目录尚待解析或核验，不能据此认定没有招生专业。',
             'original_urls': 'https://c.edu.cn/zsml/2027.htm', 'program_count': 0},
            {'unit_id': '2', 'unit_name': '乙', 'official_url': 'https://b.edu.cn/',
             'catalog_status': 'not_collected', 'collection_status': 'collected',
             'missing_reason': '已保存官方原文，专业目录尚待解析或核验，不能据此认定没有招生专业。',
             'original_urls': 'https://b.edu.cn/zsml/2027.htm\nhttps://b.edu.cn/2027-zyml.pdf', 'program_count': 0},
            {'unit_id': '1', 'unit_name': '甲', 'official_url': 'https://a.edu.cn/',
             'catalog_status': 'partial', 'collection_status': 'collected',
             'missing_reason': '已有部分专业资料，全校目录及全部考试科目尚未对账完整。',
             'original_urls': 'https://a.edu.cn/zsml/2027.htm', 'program_count': 0},
        ]
        picks = baseline.select_batch(rows, {'program_count': 0, 'tuition': 0, 'duration': 0},
                                      expansion=2, backfill=1)
        self.assertEqual([row['unit_id'] for row in picks['expansion']], ['2', '3'])
        self.assertTrue(all(pick['role'] == 'expand_catalog' for pick in picks['expansion']))

    def test_backfill_pool_prefers_units_with_programs_and_no_fields(self):
        rows = [
            {'unit_id': '9', 'program_count': 40, 'tuition': 0, 'duration': 0, 'gaps': []},
            {'unit_id': '8', 'program_count': 12, 'tuition': 0, 'duration': 0, 'gaps': []},
            {'unit_id': '7', 'program_count': 80, 'tuition': 3, 'duration': 0, 'gaps': []},
        ]
        picks = baseline.select_batch(rows, {}, expansion=0, backfill=2)
        self.assertEqual([row['unit_id'] for row in picks['backfill']], ['9', '8'])

    def test_backfill_pool_prefers_units_whose_originals_are_already_collected(self):
        rows = [
            {'unit_id': '5', 'program_count': 200, 'tuition': 0, 'duration': 0,
             'gaps': ['missing_tuition', 'original_pending']},
            {'unit_id': '4', 'program_count': 30, 'tuition': 0, 'duration': 0,
             'gaps': ['missing_tuition']},
        ]
        picks = baseline.select_batch(rows, {}, expansion=0, backfill=2)
        self.assertEqual([row['unit_id'] for row in picks['backfill']], ['4', '5'])


if __name__ == '__main__':
    unittest.main()
