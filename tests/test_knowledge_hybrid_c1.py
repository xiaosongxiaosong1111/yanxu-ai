"""C1: HTTP and Agent share one hybrid retriever with honest, separate accounting."""
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from knowledge_retrieval import HybridRetriever
from knowledge_store import KnowledgeError, KnowledgeStore
from research_store import ResearchError
from research_tools import ResearchTools
from service import AdmissionService, ServiceError
from server import create_server
from tests.test_knowledge_agent_tools import CurrentKnowledge
from tests.test_knowledge_hybrid import Index, Store, record
from tests.test_knowledge_hybrid_http import Vector
from tests.test_research_service import FakeAdmission, FakeSettings


class SharedRetrieverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='n4-c1-'); self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / 'data'; self.row = record(1, '计算机招生科目301')
        self.store = Store([self.row]); self.vector = Vector([self.row])
        self.service = AdmissionService(self.data, knowledge=self.store, search_api_key='')
        self.service._vectors = self.vector

    def test_one_retriever_instance_is_reused_and_service_get_stays_local(self):
        first = self.service._retriever(); second = self.service._retriever()
        self.assertIs(first, second)
        value = self.service.knowledge_retrieve('计算机', unit_id='u', year=2027)
        self.assertEqual(value['returned_count'], 1)
        self.assertFalse(self.vector.calls[-1][1]['allow_remote'])
        self.assertEqual(self.vector.calls[-1][1]['limit'], 40)


class PreparedVector(Vector):
    def __init__(self, rows):
        super().__init__(rows); self.cancelled = 0
    def cancel_build(self):
        self.cancelled += 1
        return {**self.status(), 'cancelled': True}


class Prep:
    def __init__(self): self.calls = 0
    def status(self):
        return {'state': 'ready', 'prepared': True, 'model': 'BAAI/bge-small-zh-v1.5',
                'revision': '46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59',
                'files_verified': 5, 'total_files': 5, 'error_code': ''}
    def start_prepare(self):
        self.calls += 1
        return {'state': 'preparing', 'started': True}


class HybridServiceHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='n4-c1-http-'); self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / 'data'; self.row = record(1, '计算机招生科目301')
        self.store = Store([self.row]); self.vector = PreparedVector([self.row]); self.prep = Prep()
        self.service = AdmissionService(self.data, knowledge=self.store, search_api_key='')
        self.service._vectors = self.vector; self.service._preparer = self.prep
        self.server = create_server(port=0, service=self.service, rate_limit=1000)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def request(self, path, method='GET', body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=4)
        self.addCleanup(connection.close)
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse(); return response.status, json.loads(response.read())

    def test_status_merges_model_state_and_prepare_cancel_are_explicit_async_posts(self):
        status, value = self.request('/api/knowledge/vectors')
        self.assertEqual(status, 200); self.assertEqual(value['data']['embedding_model']['state'], 'ready')
        self.assertEqual(self.prep.calls, 0)
        status, value = self.request('/api/knowledge/embedding/prepare', 'POST', {})
        self.assertEqual(status, 202); self.assertTrue(value['data']['started']); self.assertEqual(self.prep.calls, 1)
        status, value = self.request('/api/knowledge/vectors/cancel', 'POST', {})
        self.assertEqual(status, 202); self.assertEqual(self.vector.cancelled, 1)
        self.assertEqual(self.request('/api/knowledge/embedding/prepare', 'POST', {'model': 'other'})[0], 400)
        self.assertEqual(self.request('/api/knowledge/vectors/cancel')[0], 405)
        self.assertEqual(self.request('/api/knowledge/embedding/prepare')[0], 405)
        self.assertEqual(self.prep.calls, 1)

    def test_closing_the_service_closes_the_one_shared_retriever_and_index(self):
        self.assertIs(self.service._retriever(), self.service._retriever())
        self.service.close()
        self.assertTrue(self.vector.closed)
        with self.assertRaises(ServiceError):
            self.service.knowledge_retrieve('计算机')
        with self.assertRaises(ServiceError):
            self.service.vector_status()


class RealStoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='n4-c1-real-'); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'data'; self.store = KnowledgeStore(self.root)

    def save(self, unit='123', year=2027, suffix='catalog'):
        body = f'{year}年硕士研究生招生专业目录' + chr(10) + '081200 计算机科学与技术 初试科目101、201、301、408。' + suffix
        raw = ('%PDF-1.4' + chr(10) + 'synthetic c1 ' + suffix).encode(); sha = hashlib.sha256(raw).hexdigest()
        source = {'url': f'https://grad.example.edu.cn/{unit}-{year}-{suffix}.pdf',
                  'title': f'{year}年硕士研究生招生专业目录', 'unit_id': unit, 'unit_name': '合成大学',
                  'year': year, 'status': 'official_original_read', 'sha256': sha, 'excerpt': body}
        document = {'url': source['url'], 'raw': raw, 'sha256': sha, 'attribution_status': 'verified',
                    'content_type': 'application/pdf', 'text': body, 'pages': [{'page': 1, 'text': body}]}
        saved = self.store.save_source(source, document=document)
        self.assertEqual(saved['quality']['state'], 'accepted')
        return saved

    def hits(self):
        return self.store.search_segments(query='计算机', page_size=20)['segments']

    def test_real_store_and_shared_index_respect_scope_revocation_and_generation(self):
        primary = self.save(); other_unit = self.save(unit='456', suffix='other')
        old_year = self.save(year=2026, suffix='old')
        retriever = HybridRetriever(self.store, Index(self.hits()))
        scoped = retriever.retrieve('计算机', unit_id='123', year=2027)
        self.assertEqual({row['entry_id'] for row in scoped['segments']}, {primary['id']})
        self.assertNotIn(other_unit['id'], str(scoped)); self.assertNotIn(old_year['id'], str(scoped))
        self.store.change_validity(primary['id'], 'revoked', 'superseded source')
        after = HybridRetriever(self.store, Index(self.hits())).retrieve('计算机', unit_id='123', year=2027)
        self.assertEqual(after['segments'], [])
        replacement = self.save(unit='123', suffix='replacement')
        rows = [row for row in self.hits() if row['entry_id'] == replacement['id']]
        self.assertTrue(rows)
        stale = [{**row, 'generation_id': 'kg_' + 'f' * 32} for row in rows]
        fresh = HybridRetriever(self.store, Index(stale)).retrieve('xyzzy', unit_id='123', year=2027)
        self.assertEqual(fresh['segments'], [])
        self.assertEqual(fresh['candidate_count'], 0)

    def test_agent_search_and_read_use_the_real_store_through_the_shared_retriever(self):
        saved = self.save()
        class KnownIndex(Index):
            def search(self, query, **kwargs):
                value = super().search(query, **kwargs)
                value['usage'] = {'embedding_calls': 1, 'successful_embedding_calls': 1, 'input_tokens': 9}
                value['usage_known'] = True
                return value
        retriever = HybridRetriever(self.store, KnownIndex(self.hits()))
        task = {'id': 'r_' + '2' * 32, 'query': '我想考计算机', 'follow_ups': [], 'profile': {},
                'unit_ids': [], 'reference_year': 2027, 'target_year': 2028}
        tools = ResearchTools(FakeAdmission(), FakeSettings(), task, self.root,
                              knowledge_store=self.store, retriever=retriever)
        found = tools.run('search_knowledge_segments', {'query': '计算机'})
        self.assertTrue(found['segments'])
        self.assertEqual(found['segments'][0]['entry_id'], saved['id'])
        read = tools.run('read_knowledge_segment', {'segment_id': found['segments'][0]['id']})
        self.assertIn('081200', json.dumps(read, ensure_ascii=False))
        self.assertEqual(tools.usage['embedding_calls'], 1)
        self.assertEqual(tools.usage['embedding_input_tokens'], 9)
        self.assertTrue(tools.usage['embedding_usage_known'])


class FakeRetriever:
    def __init__(self, value=None, error=None):
        self.value, self.error, self.calls = value, error, []
    def retrieve(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.error:
            raise self.error
        return self.value


class AgentHybridTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='n4-c1-agent-'); self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / 'data'
        admission = FakeAdmission()
        self.task = {'id': 'r_' + '1' * 32, 'query': '我想考计算机', 'follow_ups': [],
                     'profile': {}, 'unit_ids': [], 'reference_year': 2027, 'target_year': 2028}
        self.store = CurrentKnowledge()
        segment = self.store.search_segments(query='计算机')['segments'][0]
        self.store.searches.clear()
        self.value = {'segments': [segment], 'candidate_count': 1,
                      'usage': {'embedding_calls': 1, 'successful_embedding_calls': 1, 'input_tokens': 12},
                      'usage_known': True}
        self.retriever = FakeRetriever(self.value)
        self.tools = ResearchTools(admission, FakeSettings(), self.task, self.data,
                                   knowledge_store=self.store, retriever=self.retriever)

    def test_agent_search_uses_shared_hybrid_local_only_and_records_usage(self):
        result = self.tools.run('search_knowledge_segments', {'query': '计算机'})
        self.assertEqual(len(result['segments']), 1)
        self.assertEqual(self.store.searches, [])
        query, options = self.retriever.calls[0]
        self.assertEqual(query, '计算机'); self.assertFalse(options['allow_remote'])
        self.assertEqual(self.tools.usage['embedding_calls'], 1)
        self.assertEqual(self.tools.usage['successful_embedding_calls'], 1)
        self.assertEqual(self.tools.usage['embedding_input_tokens'], 12)
        self.assertTrue(self.tools.usage['embedding_usage_known'])
        read = self.tools.run('read_knowledge_segment', {'segment_id': self.store.segment['id']})
        self.assertIn('CURRENT_SOURCE_CANARY', json.dumps(read))

    def test_unknown_usage_stays_unknown_and_never_becomes_zero_tokens(self):
        self.retriever.value = {'segments': self.value['segments'], 'candidate_count': 1,
                                'usage': {'embedding_calls': 1, 'successful_embedding_calls': 0,
                                          'input_tokens': None}, 'usage_known': False}
        self.tools.run('search_knowledge_segments', {'query': '计算机'})
        self.assertFalse(self.tools.usage['embedding_usage_known'])
        self.assertEqual(self.tools.usage['embedding_input_tokens'], 0)

    def test_agent_notice_names_semantic_use_or_honest_keyword_degradation(self):
        self.retriever.value = {**self.value, 'retrieval': {'mode': 'hybrid', 'degraded': False}}
        result = self.tools.run('search_knowledge_segments', {'query': '计算机'})
        self.assertIn('语义候选只用于定位', result['notice'])
        self.retriever.value = {'segments': self.value['segments'], 'candidate_count': 1,
                                'usage': {}, 'usage_known': True,
                                'retrieval': {'mode': 'keyword', 'degraded': True,
                                              'degraded_reason': 'vector_not_ready'}}
        result = self.tools.run('search_knowledge_segments', {'query': '计算机'})
        self.assertIn('退回关键词检索', result['notice'])
        self.assertEqual(result['degraded_reason'], 'vector_not_ready')

    def test_unexpected_vector_failure_degrades_to_keyword_without_fake_usage(self):
        self.retriever.error = KnowledgeError('vector_unavailable', 'private-path-canary')
        result = self.tools.run('search_knowledge_segments', {'query': '计算机'})
        self.assertEqual(len(result['segments']), 1)
        self.assertTrue(self.store.searches)
        self.assertEqual(self.tools.usage['embedding_calls'], 0)
        self.assertTrue(self.tools.usage['embedding_usage_known'])

    def test_failure_after_a_sent_embedding_call_keeps_unknown_usage_in_the_fallback(self):
        error = KnowledgeError('vector_unavailable', 'private-path-canary', 503)
        error.retrieval_usage = {'embedding_calls': 1, 'successful_embedding_calls': 0,
                                 'input_tokens': None, 'usage_known': False}
        self.retriever.error = error
        result = self.tools.run('search_knowledge_segments', {'query': '计算机'})
        self.assertTrue(result['segments'])
        self.assertEqual(self.tools.usage['embedding_calls'], 1)
        self.assertEqual(self.tools.usage['embedding_input_tokens'], 0)
        self.assertFalse(self.tools.usage['embedding_usage_known'])

    def test_cancellation_is_a_research_error_and_not_silently_degraded(self):
        self.retriever.error = KnowledgeError('retrieval_cancelled', '检索已取消。', 409)
        with self.assertRaises(ResearchError) as raised:
            self.tools.run('search_knowledge_segments', {'query': '计算机'})
        self.assertEqual(raised.exception.code, 'cancelled')
        self.assertEqual(self.store.searches, [])


if __name__ == '__main__':
    unittest.main()
