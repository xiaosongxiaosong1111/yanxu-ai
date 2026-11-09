import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from server import create_server
from service import AdmissionService, ServiceError


class FakeKnowledge:
    def __init__(self):
        self.calls = []

    def search(self, **query):
        self.calls.append(('search', query))
        return {'entries': [], 'total': 0, 'page': query['page'], 'page_size': query['page_size'],
                'summary': {'total': 0, 'search_lead': 0, 'official_original_read': 0, 'published_snapshot': 0}}

    def get(self, entry_id):
        self.calls.append(('get', entry_id))
        if entry_id == 'missing':
            raise ServiceError('unknown_knowledge', '资料未找到。', 404)
        return {'id': entry_id, 'status': 'search_lead', 'title': '官方目录线索',
                'url': 'https://example.edu.cn/catalog', 'excerpt': '搜索摘要，尚未读取原文。'}


class KnowledgeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.knowledge = FakeKnowledge()
        self.service = AdmissionService(self.data, knowledge=self.knowledge, search_api_key='')
        self.service._state = Mock(side_effect=AssertionError('knowledge reads must not load national catalogue'))
        self.server = create_server(port=0, service=self.service, rate_limit=1000)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, method='GET'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=4)
        self.addCleanup(connection.close)
        connection.request(method, path, body='{}' if method == 'POST' else None,
                           headers={'Content-Type': 'application/json'} if method == 'POST' else {})
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    def test_list_and_detail_use_only_local_knowledge_service(self):
        self.assertFalse((self.data / 'runtime').exists())
        status, value = self.request('/api/knowledge?q=408&status=search_lead&year=2027&unit_id=123&page=2&page_size=6')
        self.assertEqual(status, 200)
        self.assertTrue(value['ok'])
        self.assertEqual(self.knowledge.calls[-1], ('search', {'query': '408', 'status': 'search_lead',
                         'year': 2027, 'unit_id': '123', 'quality': 'accepted', 'page': 2, 'page_size': 6}))
        status, value = self.request('/api/knowledge?entry_id=entry-a')
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['id'], 'entry-a')
        self.assertEqual(value['data']['status'], 'search_lead')
        self.service._state.assert_not_called()
        self.assertFalse((self.data / 'runtime').exists())

    def test_invalid_queries_never_reach_the_store(self):
        queries = ['q=a&q=b', 'entry_id=', 'entry_id=a&q=b', 'unknown=x', 'page=0', 'page=-1',
                   'page=1.5', 'page_size=0', 'page_size=51', 'year=x', 'year=9999', 'status=verified',
                   'q=' + 'a' * 241]
        for query in queries:
            with self.subTest(query=query):
                status, value = self.request('/api/knowledge?' + query)
                self.assertEqual(status, 400)
                self.assertFalse(value['ok'])
        self.assertEqual(self.knowledge.calls, [])

    def test_actual_frontend_empty_filter_url_is_a_valid_list_request(self):
        status, value = self.request('/api/knowledge?q=&status=&year=&unit_id=&page=1&page_size=12')
        self.assertEqual(status, 200)
        self.assertTrue(value['ok'])
        self.assertIsNone(self.knowledge.calls[-1][1]['year'])
        status, _ = self.request('/api/knowledge?q=' + 'a' * 240 + '&year=2027')
        self.assertEqual(status, 200)
        self.assertEqual(self.knowledge.calls[-1][1]['year'], 2027)

    def test_quality_defaults_to_reusable_and_each_explicit_partition_is_forwarded(self):
        status, _ = self.request('/api/knowledge')
        self.assertEqual(status, 200)
        self.assertEqual(self.knowledge.calls[-1][1]['quality'], 'accepted')
        for quality in ('accepted', 'pending', 'excluded', 'all'):
            with self.subTest(quality=quality):
                status, _ = self.request('/api/knowledge?quality=' + quality)
                self.assertEqual(status, 200)
                self.assertEqual(self.knowledge.calls[-1][1]['quality'], quality)

    def test_invalid_quality_cannot_reach_the_store_or_mix_with_detail(self):
        for query in ('quality=', 'quality=trusted', 'quality=accepted&quality=all', 'entry_id=x&quality=all'):
            with self.subTest(query=query): self.assertEqual(self.request('/api/knowledge?' + query)[0], 400)
        self.assertEqual(self.knowledge.calls, [])
        for quality in (None, True, [], 'trusted'):
            with self.subTest(quality=quality), self.assertRaises(ServiceError):
                self.service.knowledge_get(quality=quality)

    def test_short_school_name_retries_once_with_the_reviewed_canonical_name(self):
        def search(**query):
            self.knowledge.calls.append(('search', query))
            hit = query['query'] == '中国科学院大学'
            return {'entries': [{'id': 'k_ucas', 'status': 'published_snapshot'}] if hit else [],
                    'total': 1 if hit else 0, 'page': query['page'], 'page_size': query['page_size'],
                    'summary': {'total': 1 if hit else 0}}
        self.knowledge.search = search
        status, value = self.request('/api/knowledge?q=%E5%9B%BD%E7%A7%91%E5%A4%A7')
        self.assertEqual(status, 200)
        self.assertEqual([call[1]['query'] for call in self.knowledge.calls], ['国科大', '中国科学院大学'])
        expansion = value['data']['query_expansion']
        self.assertEqual((expansion['requested'], expansion['effective']), ('国科大', '中国科学院大学'))
        self.assertIn('只是检索词变化', expansion['notice'])
        self.knowledge.calls.clear()
        self.request('/api/knowledge?q=%E5%9B%BD%E7%A7%91%E5%A4%A7&page=2')
        self.assertEqual([call[1]['query'] for call in self.knowledge.calls], ['国科大'])
        self.knowledge.calls.clear()
        self.knowledge.search = search
        self.request('/api/knowledge?q=408')
        self.assertEqual([call[1]['query'] for call in self.knowledge.calls], ['408'])
        self.assertEqual(self.knowledge.calls[0][1]['quality'], 'accepted')

    def test_missing_entries_and_read_only_method_are_explicit(self):
        self.assertEqual(self.request('/api/knowledge?entry_id=missing')[0], 404)
        status, value = self.request('/api/knowledge', 'POST')
        self.assertEqual(status, 405)
        self.assertEqual(value['error']['code'], 'method_not_allowed')

    def test_private_storage_errors_are_not_exposed(self):
        self.knowledge.search = Mock(side_effect=OSError('private-key-and-local-path'))
        status, value = self.request('/api/knowledge')
        self.assertEqual(status, 503)
        self.assertNotIn('private-key-and-local-path', json.dumps(value))

    def test_actual_sqlite_knowledge_round_trip_retains_source_status_and_error_boundary(self):
        from knowledge_store import KnowledgeStore
        self.service._knowledge = KnowledgeStore(self.data)
        saved = self.service._knowledge.save_source({'title': '2027年公开目录线索', 'url': 'https://example.edu.cn/catalog',
                    'status': 'search_lead', 'requested_year': 2027, 'excerpt': '检索摘要，尚待核对。'})
        status, value = self.request('/api/knowledge?entry_id=' + saved['id'])
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['status'], 'search_lead')
        self.assertIsNone(value['data']['year'])
        self.assertEqual(value['data']['requested_year'], 2027)
        self.assertNotIn(str(self.data), json.dumps(value, ensure_ascii=False))
        status, value = self.request('/api/knowledge?entry_id=missing')
        self.assertIn(status, {400, 404})
        self.assertFalse(value['ok'])

    def test_constructor_remains_lazy_and_closed_service_does_not_create_store(self):
        untouched = AdmissionService(self.data / 'fresh', search_api_key='')
        untouched.close()
        self.assertFalse((self.data / 'fresh').exists())
        with self.assertRaises(ServiceError):
            untouched.knowledge_get()
        self.assertFalse((self.data / 'fresh').exists())


if __name__ == '__main__':
    unittest.main()
