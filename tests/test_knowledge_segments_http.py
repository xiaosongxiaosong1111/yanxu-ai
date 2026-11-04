"""N3 knowledge endpoints use isolated stores and ephemeral HTTP servers."""
import http.client
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
from urllib.parse import urlencode

from server import create_server, RateLimiter
from service import AdmissionService, ServiceError


ENTRY = 'k_' + 'a' * 24
OTHER = 'k_' + 'b' * 24
SEGMENT = 'sg_' + 'c' * 32


class FakeSegmentStore:
    def __init__(self):
        self.calls = []

    def record(self, method, values):
        self.calls.append((method, values))
        return {'method': method, **values}

    def search_segments(self, **values):
        return self.record('search_segments', values)

    def read_segment(self, **values):
        return self.record('read_segment', values)

    def change_validity(self, **values):
        return self.record('change_validity', values)

    def choose_version(self, **values):
        return self.record('choose_version', values)

    def rebuild_segments(self, **values):
        return self.record('rebuild_segments', values)

    def jobs(self, **values):
        return self.record('jobs', values)


class KnowledgeSegmentsHTTPTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='n3-http-')
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name) / 'data'
        self.store = FakeSegmentStore()
        self.service = AdmissionService(self.data, knowledge=self.store, search_api_key='')
        self.service._state = Mock(side_effect=AssertionError('No catalog reads'))
        self.server = create_server(port=0, service=self.service, rate_limit=1000)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, method='GET', payload=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=4)
        self.addCleanup(connection.close)
        body = json.dumps(payload) if payload is not None else None
        values = {'Content-Type': 'application/json'} if payload is not None else {}
        connection.request(method, path, body=body, headers={**values, **(headers or {})})
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    def actual_original(self):
        from knowledge_store import KnowledgeStore
        self.service._knowledge = KnowledgeStore(self.data)
        raw = b'%PDF-1.4\nHTTP lifecycle fixture'
        text = '2027年硕士招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。'
        source = {'url': 'https://grad.example.edu.cn/lifecycle.pdf', 'unit_id': 'u-http',
                  'title': '2027年硕士招生专业目录', 'year': 2027,
                  'status': 'official_original_read', 'sha256': hashlib.sha256(raw).hexdigest(), 'excerpt': text}
        document = {'url': source['url'], 'raw': raw, 'text': text,
                    'pages': [{'page': 1, 'text': text}], 'attribution_status': 'verified'}
        return self.service._knowledge.save_source(source, document=document)

    def test_search_and_read_map_only_typed_bounded_parameters(self):
        path = '/api/knowledge/segments?entry_id=' + ENTRY + '&q=408&year=2027&page=2&page_size=6'
        status, value = self.request(path)
        self.assertEqual(status, 200)
        self.assertEqual(self.store.calls[-1], ('search_segments', {
            'entry_id': ENTRY, 'query': '408', 'unit_id': '', 'year': 2027,
            'page': 2, 'page_size': 6, 'quality': 'accepted'}))
        status, value = self.request('/api/knowledge/segment?segment_id=' + SEGMENT + '&before=1&after=2&max_chars=4000')
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['trusted_only'], True)
        self.assertEqual(self.store.calls[-1], ('read_segment', {
            'segment_id': SEGMENT, 'before': 1, 'after': 2, 'max_chars': 4000, 'trusted_only': True}))
        self.service._state.assert_not_called()
        self.assertFalse((self.data / 'runtime').exists())

    def test_defaults_and_blank_filters_are_compatible_with_the_browser(self):
        status, _ = self.request('/api/knowledge/segments?q=&year=&unit_id=&page=1&page_size=8')
        self.assertEqual(status, 200)
        self.assertIsNone(self.store.calls[-1][1]['year'])
        self.assertEqual(self.store.calls[-1][1]['quality'], 'accepted')
        status, _ = self.request('/api/knowledge/segment?segment_id=' + SEGMENT)
        self.assertEqual(status, 200)
        self.assertEqual(self.store.calls[-1][1]['max_chars'], 6000)

    def test_mutations_are_explicit_and_use_only_knowledge_methods(self):
        payload = {'entry_id': ENTRY, 'validity': 'superseded', 'reason': '采用经过核对的同年更正版', 'replacement_id': OTHER}
        self.assertEqual(self.request('/api/knowledge/state', 'POST', payload)[0], 200)
        self.assertEqual(self.store.calls[-1], ('change_validity', payload))
        self.assertEqual(self.request('/api/knowledge/select-version', 'POST', {'entry_id': ENTRY, 'reason': '人工核对后采用'})[0], 200)
        self.assertEqual(self.store.calls[-1][0], 'choose_version')
        self.assertEqual(self.request('/api/knowledge/reindex', 'POST', {'entry_id': ENTRY})[0], 200)
        self.assertEqual(self.store.calls[-1], ('rebuild_segments', {'entry_id': ENTRY}))
        self.assertEqual(self.request('/api/knowledge/jobs?entry_id=' + ENTRY)[0], 200)
        self.assertEqual(self.store.calls[-1], ('jobs', {'entry_id': ENTRY}))

    def test_invalid_queries_do_not_reach_the_store(self):
        paths = ['/api/knowledge/segments?page=0', '/api/knowledge/segments?page_size=51',
                 '/api/knowledge/segments?year=9999', '/api/knowledge/segments?q=a&q=b',
                 '/api/knowledge/segments?entry_id=../private', '/api/knowledge/segments?url=https://example.edu.cn',
                 '/api/knowledge/segment?segment_id=' + SEGMENT + '&before=-1',
                 '/api/knowledge/segment?segment_id=' + SEGMENT + '&after=4',
                 '/api/knowledge/segment?segment_id=' + SEGMENT + '&max_chars=999999',
                 '/api/knowledge/segment?segment_id=' + SEGMENT + '&trusted_only=false',
                 '/api/knowledge/segment?segment_id=x', '/api/knowledge/jobs',
                 '/api/knowledge/jobs?entry_id=' + ENTRY + '&page=1']
        for path in paths:
            with self.subTest(path=path): self.assertEqual(self.request(path)[0], 400)
        self.assertEqual(self.store.calls, [])

    def test_invalid_mutations_do_not_reach_the_store(self):
        cases = [('/api/knowledge/state', {'entry_id': ENTRY, 'validity': 'accepted', 'reason': 'x'}),
                 ('/api/knowledge/state', {'entry_id': ENTRY, 'validity': 'revoked', 'reason': ''}),
                 ('/api/knowledge/state', {'entry_id': ENTRY, 'validity': 'superseded', 'reason': 'x'}),
                 ('/api/knowledge/select-version', {'entry_id': ENTRY, 'reason': 'x', 'quality': 'accepted'}),
                 ('/api/knowledge/reindex', {'entry_id': ENTRY, 'api_key': 'private'}),
                 ('/api/knowledge/reindex', {'entry_id': True})]
        for path, payload in cases:
            with self.subTest(path=path, payload=payload): self.assertEqual(self.request(path, 'POST', payload)[0], 400)
        self.assertEqual(self.store.calls, [])

    def test_direct_service_boolean_numbers_and_credential_reasons_are_rejected(self):
        requests = [('segments', {'page': True}), ('segment', {'segment_id': SEGMENT, 'before': False}),
                    ('state', {'entry_id': ENTRY, 'validity': 'revoked', 'reason': 'secret_key=private-canary'})]
        for action, payload in requests:
            with self.subTest(action=action), self.assertRaises(ServiceError):
                self.service.knowledge_request(action, payload)
        self.assertEqual(self.store.calls, [])

    def test_direct_service_rejects_unknown_actions_types_and_inapplicable_replacement(self):
        requests = [('__dict__', {}), ('segments', {'year': True}), ('segments', {'quality': []}),
                    ('segments', {'quality': 'trusted'}), ('segments', {'q': False}),
                    ('state', {'entry_id': ENTRY, 'validity': 'revoked', 'reason': 'x', 'replacement_id': OTHER})]
        for action, payload in requests:
            with self.subTest(action=action, payload=payload), self.assertRaises(ServiceError):
                self.service.knowledge_request(action, payload)
        self.assertEqual(self.store.calls, [])

    def test_methods_and_origins_are_rejected_before_actions(self):
        for path in ['/api/knowledge/state', '/api/knowledge/select-version', '/api/knowledge/reindex']:
            with self.subTest(path=path): self.assertEqual(self.request(path)[0], 405)
        for path in ['/api/knowledge/segments', '/api/knowledge/segment', '/api/knowledge/jobs']:
            with self.subTest(path=path): self.assertEqual(self.request(path, 'POST', {})[0], 405)
        self.assertEqual(self.request('/api/knowledge/reindex', 'POST', {'entry_id': ENTRY}, {'Origin': 'https://outside.example'})[0], 403)
        self.assertEqual(self.store.calls, [])

    def test_mutation_rate_limit_is_separate_from_read_and_other_quotas(self):
        self.server.knowledge_limiter = RateLimiter(limit=1)
        self.assertEqual(self.request('/api/knowledge/reindex', 'POST', {'entry_id': ENTRY})[0], 200)
        status, value = self.request('/api/knowledge/reindex', 'POST', {'entry_id': ENTRY})
        self.assertEqual(status, 429)
        self.assertEqual(self.request('/api/knowledge/segment?segment_id=' + SEGMENT)[0], 200)
        self.assertTrue(self.server.settings_limiter.allow('127.0.0.1'))
        self.assertTrue(self.server.research_limiter.allow('127.0.0.1'))

    def test_storage_exception_does_not_leak_private_details(self):
        self.store.search_segments = Mock(side_effect=OSError('SECRET-LOCAL-PATH-CANARY'))
        status, value = self.request('/api/knowledge/segments')
        self.assertEqual(status, 503)
        self.assertNotIn('SECRET-LOCAL-PATH-CANARY', json.dumps(value))

    def test_actual_store_revocation_gates_read_but_preserves_audit_reason(self):
        saved = self.actual_original()
        status, result = self.request('/api/knowledge/segments?entry_id=' + saved['id'])
        self.assertEqual(status, 200)
        found = result['data']['segments'][0]
        segment_id = found['id']
        self.assertIs(found['source']['usable'], True)
        self.assertFalse({'text', 'excerpt', 'full_text', 'pages', 'search_text'} & set(found['source']))
        payload = {'entry_id': saved['id'], 'validity': 'revoked', 'reason': '官方更正待人工核对'}
        status, result = self.request('/api/knowledge/state', 'POST', payload)
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['entry']['validity'], 'revoked')
        self.assertEqual(result['data']['entry']['quality']['state'], 'accepted')
        self.assertEqual(self.request('/api/knowledge/segment?segment_id=' + segment_id)[0], 409)
        status, result = self.request('/api/knowledge/segments?entry_id=' + saved['id'] + '&quality=all')
        self.assertEqual(result['data']['segments'], [])
        status, result = self.request('/api/knowledge?entry_id=' + saved['id'])
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['validity_reason'], payload['reason'])
        self.assertEqual(result['data']['text'], '')
        self.assertEqual(self.request('/api/knowledge/reindex', 'POST', {'entry_id': saved['id']})[0], 409)

    def test_actual_store_pending_cannot_be_promoted_and_reindex_is_local(self):
        saved = self.actual_original()
        pending = self.service._knowledge.save_source({'url': 'https://grad.example.edu.cn/lead', 'unit_id': 'u-http',
            'title': '2027年硕士招生专业目录', 'year': 2027, 'status': 'search_lead', 'excerpt': '尚未读取原文'})
        self.assertEqual(pending['quality']['state'], 'pending')
        for path, payload in [('/api/knowledge/state', {'entry_id': pending['id'], 'validity': 'active', 'reason': '尝试采用'}),
                              ('/api/knowledge/select-version', {'entry_id': pending['id'], 'reason': '尝试采用'})]:
            with self.subTest(path=path): self.assertEqual(self.request(path, 'POST', payload)[0], 409)
        status, result = self.request('/api/knowledge/reindex', 'POST', {'entry_id': saved['id']})
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['state'], 'succeeded')
        self.assertNotIn('vector_ready', result['data'])
        status, result = self.request('/api/knowledge/jobs?entry_id=' + saved['id'])
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['backend'], 'local_segments')
        self.assertTrue(any(job['state'] == 'succeeded' for job in result['data']['jobs']))

    def test_actual_store_late_page_read_and_snapshot_tamper_gate(self):
        from knowledge_store import KnowledgeStore
        self.service._knowledge = KnowledgeStore(self.data)
        raw = b'%PDF-1.4\nsynthetic HTTP fixture'
        digest = hashlib.sha256(raw).hexdigest()
        pages = [{'page': page, 'text': '目录正文。' if page != 21 else '第21页条款：跨专业考生须参加加试课程。'}
                 for page in range(1, 23)]
        pages[0]['text'] = '2027年硕士招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。'
        source = {'url': 'https://grad.example.edu.cn/http.pdf', 'title': '2027年硕士招生专业目录',
                  'unit_id': 'u-http', 'year': 2027, 'status': 'official_original_read',
                  'sha256': digest, 'excerpt': pages[0]['text']}
        document = {'url': source['url'], 'raw': raw, 'sha256': digest, 'attribution_status': 'verified',
                    'text': '\n'.join(page['text'] for page in pages), 'pages': pages}
        saved = self.service._knowledge.save_source(source, document=document)
        status, result = self.request('/api/knowledge/segments?' + urlencode({'entry_id': saved['id'], 'q': '跨专业', 'year': 2027}))
        self.assertEqual(status, 200)
        found = result['data']['segments'][0]
        self.assertEqual(found['page_start'], 21)
        status, result = self.request('/api/knowledge/segment?' + urlencode({'segment_id': found['id'], 'after': 1}))
        self.assertEqual(status, 200)
        self.assertIn('跨专业考生', result['data']['segment']['text'])
        self.assertLessEqual(result['data']['returned_chars'], 6000)
        blob = self.data / 'runtime/research-sources' / (digest + '.bin')
        blob.write_bytes(b'X' * len(raw))
        status, result = self.request('/api/knowledge/segment?segment_id=' + found['id'])
        self.assertEqual(status, 409)
        self.assertNotIn('跨专业考生', json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
