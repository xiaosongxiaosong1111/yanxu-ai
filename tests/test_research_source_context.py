"""Source follow-up requests carry identifiers, never browser-supplied evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from research_service import ResearchService, ResearchError


ENTRY = 'k_' + 'a' * 24
UNIT = {'id': '367889', 'name': '北京服装学院',
        'official_url': 'https://yjs.bift.edu.cn', 'province': '北京'}


class SourceContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        admission = Mock()
        admission.catalog_bootstrap.return_value = {'units': [UNIT, {**UNIT, 'id': 'other'}]}
        self.service = ResearchService(Path(temporary.name), admission)
        self.addCleanup(self.service.close)
        self.entry = {'id': ENTRY, 'unit_id': UNIT['id'], 'year': 2027,
                      'title': '北京服装学院2027年招生专业目录',
                      'url': 'https://yjs.bift.edu.cn/zsgz/zsjz/catalog.htm',
                      'status': 'search_lead', 'validity': 'active',
                      'quality': {'state': 'pending', 'reusable': False,
                                  'reasons': [{'code': 'original_missing', 'message': '尚未读取原文'}]},
                      'text': '浏览器和模型均不应直接收到的完整原文', 'secret': 'not-a-real-key'}
        self.knowledge = Mock()
        self.knowledge.get.side_effect = lambda identifier: deepcopy(self.entry)
        self.service._knowledge_store = self.knowledge

    def request(self, **changes):
        return self.service.create({'query': '补证这份目录并比较科目',
                                    'knowledge_entry_id': ENTRY, **changes})

    def test_pending_source_seeds_scope_and_persists_minimal_server_context(self):
        result = self.request()
        self.assertEqual(result['unit_ids'], [UNIT['id']])
        self.assertEqual(result['knowledge_entry_id'], ENTRY)
        context = result['source_context']
        self.assertEqual(context['year'], 2027)
        self.assertEqual(context['quality'], 'pending')
        self.assertEqual(context['reason_codes'], ['original_missing'])
        self.assertNotIn('text', context)
        self.assertNotIn('secret', context)
        self.assertEqual(result['status'], 'waiting_configuration')
        self.assertEqual(self.service.store.get(result['id'])['source_context'], context)

    def test_unknown_year_is_not_filled_from_requested_or_target_year(self):
        self.entry.update(year=None, requested_year=2027, target_year=2028)
        result = self.request()
        self.assertIsNone(result['source_context']['year'])
        self.assertEqual(result['reference_year'], 2027)

    def test_actual_historical_year_sets_reference_only_when_unspecified(self):
        self.entry['year'] = 2026
        result = self.request()
        self.assertEqual(result['reference_year'], 2026)
        self.assertEqual(result['source_context']['year'], 2026)
        self.assertNotIn('reference_year', result['confirmed_fields'])

    def test_explicit_scope_or_year_conflict_fails_without_task_creation(self):
        for change in ({'unit_ids': ['other']}, {'profile': {'reference_year': 2026}}):
            with self.subTest(change=change), self.assertRaises(ResearchError):
                self.request(**change)
        self.assertEqual(self.service.store.list()['total'], 0)

    def test_browser_cannot_supply_context_metadata_or_invalid_id(self):
        for change in ({'source_context': self.entry}, {'knowledge_entry_id': 'https://example.org'},
                       {'knowledge_entry_id': 3}):
            with self.subTest(change=change), self.assertRaises(ResearchError):
                self.request(**change)

    def test_unavailable_excluded_private_or_nonofficial_sources_fail_closed(self):
        cases = ({'validity': 'revoked'}, {'quality': {'state': 'excluded'}},
                 {'content_notice': '材料可能包含逐人录取信息，仅保留来源和版本，不索引个人名单。'},
                 {'url': 'https://unrelated.example.org/catalog'}, {'unit_id': 'missing'})
        for change in cases:
            with self.subTest(change=change):
                original = deepcopy(self.entry)
                self.entry.update(change)
                with self.assertRaises(ResearchError):
                    self.request()
                self.entry = original

    def test_missing_source_returns_safe_domain_error(self):
        from knowledge_store import KnowledgeError
        self.knowledge.get.side_effect = KnowledgeError('unknown_knowledge', 'missing', 404)
        with self.assertRaises(ResearchError) as raised:
            self.request()
        self.assertEqual(raised.exception.code, 'source_context_unavailable')

    def test_reviewed_alternate_official_domains_share_the_agent_gate(self):
        self.service.admission.catalog_bootstrap.return_value = {'units': [
            {'id': 'ucas', 'name': '中国科学院大学', 'official_url': 'https://www.ucas.edu.cn/'}]}
        self.entry.update(unit_id='ucas', url='https://admission.ucas.ac.cn/catalog.htm')
        self.assertEqual(self.request()['unit_ids'], ['ucas'])
        self.entry['url'] = 'https://unreviewed.example.org/catalog.htm'
        with self.assertRaises(ResearchError):
            self.request()

    def test_continue_reloads_context_and_can_explicitly_clear_it(self):
        task = self.request()
        self.entry['title'] = '更新后的原文标题'
        continued = self.service.continue_research({'research_id': task['id'], 'message': '继续补证'})
        self.assertEqual(continued['source_context']['title'], self.entry['title'])
        self.entry['validity'] = 'revoked'
        with self.assertRaises(ResearchError):
            self.service.continue_research({'research_id': task['id']})
        cleared = self.service.continue_research({'research_id': task['id'], 'knowledge_entry_id': None})
        self.assertIsNone(cleared['source_context'])
        self.assertIsNone(cleared['knowledge_entry_id'])


if __name__ == '__main__':
    unittest.main()
