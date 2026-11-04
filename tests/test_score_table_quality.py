"""Score-table admission uses actual rows, never headings or unrelated numbers."""
import unittest

from knowledge_quality import assess_quality


class ScoreTableQualityTests(unittest.TestCase):
    HEADER = '专业 总分 政治 英语 业务课一 业务课二'
    ROW = '计算机科学与技术 350 50 50 75 75'
    SOURCE = {'title': '2027年硕士研究生复试基本分数线', 'unit_id': '123', 'year': 2027,
              'status': 'official_original_read', 'text_origin': 'full_extraction'}

    def assess(self, body, **updates):
        return assess_quality({**self.SOURCE, **updates}, body,
                              original_available=True, authority_verified=True)

    def test_score_table_does_not_require_a_unit_on_every_numeric_cell(self):
        result = self.assess(self.HEADER + '\n' + self.ROW)
        self.assertEqual(result['state'], 'accepted')
        self.assertEqual(result['material_type'], 'scores')

    def test_tab_separated_program_code_and_decimal_scores_are_supported(self):
        body = self.HEADER.replace(' ', '\t') + '\n085404 计算机技术\t350.5\t50\t50\t75\t75'
        self.assertEqual(self.assess(body)['state'], 'accepted')

    def test_header_without_complete_score_row_remains_pending(self):
        for tail in ['', '\n计算机科学与技术 待公布', '\n计算机科学与技术 350 50',
                     '\n计算机科学与技术 350 50 50 75', '\n350 50 50 75 75']:
            with self.subTest(tail=tail):
                self.assertNotEqual(self.assess(self.HEADER + tail)['state'], 'accepted')

    def test_numeric_footer_and_download_links_are_not_score_rows(self):
        for row in ['邮编300071 联系电话02212345678', '附件：分数线 350 50 50 75 75',
                    '点击下载 350 50 50 75 75', '浏览次数 350 50 50 75 75',
                    '发布日期 2026 09 01', '计算机科学与技术 350 50 50 750 75']:
            with self.subTest(row=row):
                self.assertNotEqual(self.assess(self.HEADER + '\n' + row)['state'], 'accepted')

    def test_numbers_without_a_score_header_do_not_pass(self):
        for body in [self.ROW, '专业 总分\n' + self.ROW, '总分 总分 总分 总分 总分\n' + self.ROW,
                     '专业 日期 编号 次数\n' + self.ROW]:
            with self.subTest(body=body):
                self.assertNotEqual(self.assess(body)['state'], 'accepted')

    def test_extra_numeric_cells_and_update_dates_cannot_become_part_of_the_label(self):
        for row in ['最新更新 2026 11 03 12', '更新时间 2026 11 03 12',
                    '计算机科学与技术 2026 11 03 12', '计算机科学与技术 1 350 50 50',
                    '更新时间 12 30 45']:
            with self.subTest(row=row):
                self.assertNotEqual(self.assess('专业 总分 政治 英语\n' + row)['state'], 'accepted')

    def test_real_score_rows_do_not_promote_search_snippets_or_unknown_years(self):
        for updates, code in [({'status': 'search_lead'}, 'search_only'),
                              ({'year': None}, 'year_unknown')]:
            with self.subTest(updates=updates):
                result = self.assess(self.HEADER + '\n' + self.ROW, **updates)
                self.assertEqual(result['state'], 'pending')
                self.assertIn(code, {reason['code'] for reason in result['reasons']})

    def test_real_score_rows_still_need_authority_and_original(self):
        for flags, code in [({'original_available': False, 'authority_verified': True}, 'original_unavailable'),
                            ({'original_available': True, 'authority_verified': False}, 'authority_unverified')]:
            with self.subTest(flags=flags):
                result = assess_quality(self.SOURCE, self.HEADER + '\n' + self.ROW, **flags)
                self.assertEqual(result['state'], 'pending')
                self.assertIn(code, {reason['code'] for reason in result['reasons']})

    def test_personal_score_rosters_remain_excluded(self):
        body = '姓名 考生编号 总分 政治 英语 业务课一 业务课二\n张同学 100123456789012 350 50 50 75 75'
        self.assertEqual(self.assess(body)['state'], 'excluded')


if __name__ == '__main__':
    unittest.main()
