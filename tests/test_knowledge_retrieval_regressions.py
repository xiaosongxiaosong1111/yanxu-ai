"""Independent N1 checks. All sources, services and databases are isolated fixtures."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from grad_engine import validate_profile
from knowledge_quality import assess_quality
from knowledge_store import KnowledgeStore
from research_store import ResearchError
from research_tools import ResearchTools
from tests.test_research_service import FakeAdmission, FakeSettings


class KnowledgeRetrievalRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='yanxu-n1-check-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / 'data'
        admission = FakeAdmission()
        admission.units += [
            {'id': '456', 'name': '夹具乙校', 'official_url': 'https://u456.example.edu.cn'},
            {'id': '999', 'name': '夹具他校', 'official_url': 'https://u999.example.edu.cn'},
        ]
        self.store = KnowledgeStore(self.data)
        self.task = {'id': 'r_' + '1' * 32, 'query': '计算机', 'follow_ups': [],
                     'profile': validate_profile({}), 'unit_ids': [], 'reference_year': 2027, 'target_year': 2028}
        self.tools = ResearchTools(admission, FakeSettings(), self.task, self.data, knowledge_store=self.store)

    def published(self, suffix, year=2027, unit='123', older=False):
        source = {'url': f'https://grad.example.edu.cn/fixture/{unit}/{suffix}',
                  'title': f'{year}年计算机招生专业目录', 'unit_id': unit, 'unit_name': '夹具学校' + unit,
                  'year': year, 'requested_year': 2028, 'status': 'published_snapshot',
                  'excerpt': '081200 计算机科学与技术，初试科目101、201、301、408。', 'checked_at': '2026-11-03'}
        saved = self.store.save_source(source)
        self.assertEqual(saved['quality']['state'], 'accepted', 'The test corpus must contain reusable fixtures.')
        if older:
            with closing(sqlite3.connect(self.store.path)) as connection:
                with connection:
                    connection.execute('UPDATE knowledge_entries SET last_seen_at=? WHERE id=?', ('2000-01-01T00:00:00Z', saved['id']))
        return saved

    def test_target_year_is_filtered_before_the_first_48_entries(self):
        expected = self.published('target-year', older=True)
        for index in range(48):
            self.published('other-year-' + str(index), year=2026)
        result = self.tools.search_knowledge({'query': '计算机', 'year': 2027})
        self.assertIn(expected['id'], {item['knowledge_id'] for item in result['results']})

    def test_selected_school_is_filtered_before_the_first_eight_results(self):
        self.task['unit_ids'] = ['123']
        expected = self.published('selected-school', older=True)
        for index in range(8):
            self.published('other-school-' + str(index), unit='999')
        result = self.tools.search_knowledge({'query': '计算机'})
        self.assertIn(expected['id'], {item['knowledge_id'] for item in result['results']})
        self.assertTrue(all(item['unit_id'] == '123' for item in result['results']))

    def test_multiple_selected_schools_are_filtered_before_the_limit(self):
        self.task['unit_ids'] = ['123', '456']
        first = self.published('selected-a', unit='123', older=True)
        second = self.published('selected-b', unit='456', older=True)
        for index in range(8):
            self.published('outside-scope-' + str(index), unit='999')
        result = self.tools.search_knowledge({'query': '计算机'})
        identifiers = {item['knowledge_id'] for item in result['results']}
        self.assertTrue({first['id'], second['id']} <= identifiers)
        self.assertLessEqual(len(result['results']), 8)
        self.assertTrue(all(item['unit_id'] in {'123', '456'} for item in result['results']))

    def test_explicit_school_cannot_expand_the_task_scope(self):
        self.task['unit_ids'] = ['123']
        with self.assertRaises(ResearchError):
            self.tools.search_knowledge({'query': '计算机', 'unit_id': '999'})

    def test_reference_year_miss_reports_stored_years_without_returning_history(self):
        saved = self.published('history-only', year=2026)
        result = self.tools.search_knowledge({'query': '计算机', 'year': 2027})
        self.assertEqual(result['results'], [])
        self.assertEqual(result['stored_years'], [2026])
        self.assertIn('2026', result['notice'])
        self.assertIn('不能把历史年度当作目标年度', result['notice'])
        self.assertNotIn(saved['id'], str(result))

    def test_short_school_name_is_expanded_only_after_the_first_query_misses(self):
        saved = self.store.save_source({
            'url': 'https://grad.example.edu.cn/fixture/ucas/alias',
            'title': '2026年中国科学院大学计算机招生专业目录', 'unit_id': '123',
            'unit_name': '中国科学院大学', 'year': 2026, 'requested_year': 2028,
            'status': 'published_snapshot',
            'excerpt': '081200 计算机科学与技术，初试科目101、201、301、408。',
            'checked_at': '2026-11-03'})
        self.assertEqual(saved['quality']['state'], 'accepted')
        result = self.tools.search_knowledge({'query': '国科大 计算机', 'year': 2026})
        self.assertEqual(result['requested_query'], '国科大 计算机')
        self.assertEqual(result['effective_query'], '中国科学院大学 计算机')
        self.assertTrue(result['alias_expanded'])
        self.assertIn(saved['id'], {item['knowledge_id'] for item in result['results']})
        self.assertIn('只是检索词变化', result['notice'])
        direct = self.tools.search_knowledge({'query': '计算机', 'year': 2026})
        self.assertNotIn('alias_expanded', direct)

    def read_pdf(self, text, search_title='2028年计算机招生目录', metadata=None):
        raw = b'%PDF-1.4\nN1 isolated fetcher fixture'
        url = 'https://grad.example.edu.cn/catalog.pdf'
        document = {'url': url, 'raw': raw, 'sha256': hashlib.sha256(raw).hexdigest(),
                    'document_title': '', 'document_heading': '', 'attribution_status': 'verified',
                    'checked_at': '2026-11-03', 'text': text, 'pages': [{'page': 1, 'text': text}], **(metadata or {})}
        self.tools.source_fetcher = Mock(return_value=document)
        identifier = self.tools._source({'url': url, 'title': search_title, 'status': 'search_lead',
                                         'unit_id': '123', 'year': None, 'requested_year': 2028, 'excerpt': '搜索线索'})
        return self.tools.read_source({'source_id': identifier, 'unit_id': '123'})

    def test_pdf_year_comes_from_actual_admissions_heading(self):
        result = self.read_pdf('2027年硕士研究生招生专业目录\n081200 计算机科学与技术\n初试科目101、201、301、408。')
        self.assertEqual(result['year'], 2027)
        self.assertEqual(result['requested_year'], 2028)

    def test_pdf_original_heading_replaces_conflicting_search_title(self):
        result = self.read_pdf('2027年硕士研究生招生专业目录\n081200 计算机科学与技术\n初试科目101、201、301、408。')
        self.assertEqual(result['knowledge_quality']['state'], 'accepted')
        saved = self.store.get(result['knowledge_id'])
        self.assertEqual(saved['year'], 2027)
        self.assertIn('2027', saved['title'])

    def test_pdf_publication_date_and_historical_reference_do_not_supply_year(self):
        result = self.read_pdf('发布时间：2026-09-01\n计算机考试科目调整说明\n参考2025年相关通知，初试科目为101、201、301、408。')
        self.assertIsNone(result['year'])
        self.assertNotEqual(result['knowledge_quality']['state'], 'accepted')

    def test_conflicting_pdf_admissions_headings_remain_unknown(self):
        result = self.read_pdf('2027年硕士研究生招生专业目录\n2026年硕士研究生招生专业目录\n081200 计算机科学与技术\n初试科目101、201、301、408。')
        self.assertIsNone(result['year'])

    def test_selected_schools_share_the_result_limit(self):
        self.task['unit_ids'] = ['123', '456']
        for index in range(12):
            self.published('many-a-' + str(index), unit='123')
        expected = self.published('only-b', unit='456', older=True)
        result = self.tools.search_knowledge({'query': '计算机'})
        self.assertEqual(len(result['results']), 8)
        self.assertEqual(result['query_total'], 13)
        self.assertIn(expected['id'], {item['knowledge_id'] for item in result['results']})

    def test_explicit_school_narrows_multiselect_scope(self):
        self.task['unit_ids'] = ['123', '456']
        self.published('a', unit='123')
        self.published('b', unit='456')
        result = self.tools.search_knowledge({'query': '计算机', 'unit_id': '456'})
        self.assertEqual(result['query_total'], 1)
        self.assertEqual([item['unit_id'] for item in result['results']], ['456'])

    def test_pdf_wrapped_heading_retains_actual_year(self):
        result = self.read_pdf('2027 年\n硕士研究生招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。')
        self.assertEqual(result['year'], 2027)
        self.assertEqual(result['knowledge_quality']['state'], 'accepted')

    def test_repeated_same_year_pdf_heading_is_unambiguous(self):
        result = self.read_pdf('2027年硕士招生专业目录\n2027年硕士招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。')
        self.assertEqual(result['year'], 2027)
        self.assertEqual(result['knowledge_quality']['state'], 'accepted')

    def test_pdf_historical_comparison_does_not_supply_actual_year(self):
        for heading in ['参考2025年硕士研究生招生专业目录', '根据2025年硕士研究生招生专业目录调整说明',
                        '关于对比2025年和2026年硕士招生目录的说明',
                        '2025年硕士招生管理规定，是制定本办法的历史依据']:
            with self.subTest(heading=heading):
                result = self.read_pdf(heading + '\n081200 计算机科学与技术 初试科目101、201、301、408。')
                self.assertIsNone(result['year'])
                self.assertNotEqual(result['knowledge_quality']['state'], 'accepted')
                self.tools.sources = {}

    def test_pdf_cited_regulations_do_not_supply_document_year(self):
        for heading in ['按照2025年硕士研究生招生管理规定，报考者须具有本科学历',
                        '根据教育部关于全国硕士研究生招生的2025年招生管理规定',
                        '依照2025年硕士研究生招生管理规定',
                        '遵照2025年硕士研究生招生管理规定']:
            with self.subTest(heading=heading):
                result = self.read_pdf(heading + '\n报考资格：考生须具有国家承认的本科学历。')
                self.assertIsNone(result['year'])
                self.assertNotEqual(result['knowledge_quality']['state'], 'accepted')
                self.tools.sources = {}

    def test_original_metadata_and_body_year_conflict_remains_pending(self):
        result = self.read_pdf('2026年硕士研究生招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。',
                               metadata={'document_title': '2027年硕士研究生招生专业目录'})
        self.assertIsNone(result['year'])
        self.assertEqual(result['knowledge_quality']['state'], 'pending')

    def test_year_in_long_body_paragraph_is_not_a_document_heading(self):
        result = self.read_pdf('2025年硕士招生管理规定' + '报考者应具有本科学历' * 25)
        self.assertIsNone(result['year'])
        self.assertNotEqual(result['knowledge_quality']['state'], 'accepted')

    @staticmethod
    def score_source():
        return {'title': '2027年硕士研究生复试基本分数线', 'unit_id': '123', 'year': 2027,
                'status': 'official_original_read', 'text_origin': 'full_extraction'}

    def score_quality(self, body):
        return assess_quality(self.score_source(), body, original_available=True, authority_verified=True)

    def test_score_header_and_numeric_row_are_substantive_without_unit_suffix(self):
        quality = self.score_quality('专业 总分 政治 英语 业务课一 业务课二\n计算机科学与技术 350 50 50 75 75')
        self.assertEqual(quality['state'], 'accepted')

    def test_score_header_without_data_does_not_pass(self):
        self.assertNotEqual(self.score_quality('专业 总分 政治 英语 业务课一 业务课二')['state'], 'accepted')

    def test_postal_numbers_do_not_become_score_facts(self):
        self.assertNotEqual(self.score_quality('首页 招生通知\n邮编300071 联系电话02212345678\n附件：点击下载')['state'], 'accepted')


if __name__ == '__main__':
    unittest.main(verbosity=2)
