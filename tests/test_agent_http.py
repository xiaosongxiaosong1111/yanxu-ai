import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from server import create_server
from service import AdmissionService, ServiceError


class FakeSettings:
    def __init__(self):
        self.saved_key = ''
        self.calls = []

    def providers(self):
        return {'providers': [{'id': 'deepseek', 'label': 'DeepSeek'}]}

    def public_settings(self):
        return {'ai': {'status': 'not_configured', 'has_key': False},
                'yuanbao': {'status': 'connected' if self.saved_key else 'not_configured',
                             'has_key': bool(self.saved_key)}, 'storage': {'persistent': True}}

    def models(self, payload):
        self.calls.append(('models', payload))
        return {'models': [{'id': 'model-a'}], 'discovery': 'remote'}

    def test(self, payload):
        self.calls.append(('test', payload))
        return {'status': 'connected', 'validation_token': 'test-receipt'}

    def save(self, payload):
        self.calls.append(('save', payload))
        return self.public_settings()

    def delete(self, payload):
        self.calls.append(('delete', payload))
        return self.public_settings()

    def yuanbao_key(self):
        return self.saved_key


class FakeResearch:
    def __init__(self):
        self.calls = []
        self.closed = False

    def create(self, payload):
        self.calls.append(('create', payload))
        return {'research_id': 'task-1', 'status': 'waiting_configuration', 'query': payload['query']}

    def get(self, research_id, after_event=0):
        self.calls.append(('get', research_id, after_event))
        return {'research_id': research_id, 'status': 'running', 'events': [], 'last_event_id': after_event}

    def list(self, limit=20):
        self.calls.append(('list', limit))
        return {'tasks': [], 'total': 0}

    def cancel(self, payload):
        self.calls.append(('cancel', payload))
        return {'research_id': payload['research_id'], 'status': 'cancelled'}

    def continue_research(self, payload):
        self.calls.append(('continue', payload))
        return {'research_id': payload['research_id'], 'status': 'queued'}

    def close(self):
        self.closed = True


class AgentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.settings = FakeSettings()
        self.research = FakeResearch()
        self.service = AdmissionService(self.data, settings=self.settings, research=self.research)
        self.server = create_server(port=0, service=self.service, rate_limit=1000)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, payload=None, method=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=4)
        self.addCleanup(connection.close)
        body = None if payload is None else json.dumps(payload)
        outgoing = {'Content-Type': 'application/json'} if payload is not None else {}
        connection.request(method or ('GET' if payload is None else 'POST'), path, body=body,
                           headers={**outgoing, **(headers or {})})
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    def test_settings_discovery_testing_and_save_are_distinct_and_no_key_returns(self):
        for path in ('/api/settings', '/api/settings/providers'):
            status, value = self.request(path)
            self.assertEqual(status, 200)
            self.assertTrue(value['ok'])
            self.assertNotIn('api_key', json.dumps(value))
        draft = {'kind': 'ai', 'provider': 'deepseek', 'api_key': 'private-test-key', 'model': 'model-a'}
        status, value = self.request('/api/settings/models', draft)
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['models'][0]['id'], 'model-a')
        status, value = self.request('/api/settings/test', draft)
        self.assertEqual(status, 200)
        receipt = value['data']['validation_token']
        self.request('/api/settings/save', {**draft, 'validation_token': receipt})
        self.request('/api/settings/delete', {'kind': 'ai'})
        self.assertEqual([row[0] for row in self.settings.calls], ['models', 'test', 'save', 'delete'])

    def test_research_create_poll_cancel_continue_and_list_use_only_task_service(self):
        self.service._state = Mock(side_effect=AssertionError('full catalog forbidden'))
        status, value = self.request('/api/research', {'query': '我想考计算机'})
        self.assertEqual(status, 202)
        self.assertEqual(value['data']['status'], 'waiting_configuration')
        status, value = self.request('/api/research?research_id=task-1&after_event=3')
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['last_event_id'], 3)
        self.request('/api/research?limit=4')
        status, _ = self.request('/api/research/cancel', {'research_id': 'task-1'})
        self.assertEqual(status, 200)
        status, _ = self.request('/api/research/continue', {'research_id': 'task-1', 'message': '考虑天津'})
        self.assertEqual(status, 202)
        self.assertEqual(self.research.calls, [('create', {'query': '我想考计算机'}), ('get', 'task-1', 3),
                                              ('list', 4), ('cancel', {'research_id': 'task-1'}),
                                              ('continue', {'research_id': 'task-1', 'message': '考虑天津'})])

    def test_unsafe_methods_queries_origins_and_bodies_never_reach_credentials(self):
        for path in ('/api/settings?api_key=secret', '/api/settings/providers?a=1',
                     '/api/settings/test?api_key=secret', '/api/research?query=secret',
                     '/api/research?research_id=a&research_id=b', '/api/research?after_event=1',
                     '/api/research?research_id=a&after_event=-1', '/api/research?research_id=a&limit=1',
                     '/api/research?research_id='):
            with self.subTest(path=path):
                status, _ = self.request(path)
                self.assertEqual(status, 400)
        for path in ('/api/settings/test', '/api/settings/save', '/api/settings/delete', '/api/settings/models',
                     '/api/research/cancel', '/api/research/continue'):
            status, _ = self.request(path)
            self.assertEqual(status, 405)
        status, _ = self.request('/api/settings/test', {}, headers={'Origin': 'https://outside.example'})
        self.assertEqual(status, 403)
        status, _ = self.request('/api/settings/test', {}, headers={'Content-Type': 'text/plain'})
        self.assertEqual(status, 415)
        status, _ = self.request('/api/settings/test?api_key=secret', {})
        self.assertEqual(status, 400)
        self.assertEqual(self.settings.calls, [])

    def test_remote_actions_rate_limit_but_cancel_and_poll_remain_available(self):
        self.server.settings_limiter = Mock(allow=Mock(return_value=False))
        status, value = self.request('/api/settings/test', {})
        self.assertEqual(status, 429)
        self.assertEqual(value['error']['code'], 'rate_limited')
        self.server.research_limiter = Mock(allow=Mock(return_value=False))
        status, _ = self.request('/api/research', {'query': '计算机'})
        self.assertEqual(status, 429)
        self.assertEqual(self.request('/api/research?research_id=a')[0], 200)
        self.assertEqual(self.request('/api/research/cancel', {'research_id': 'a'})[0], 200)

    def test_server_close_closes_research_and_is_idempotent(self):
        self.service.close()
        self.service.close()
        self.assertTrue(self.research.closed)

    def test_settings_errors_are_mapped_and_unexpected_errors_never_echo_secrets(self):
        from settings_store import SettingsError
        self.settings.test = Mock(side_effect=SettingsError('model_unavailable', '该模型暂不可用。', 503))
        status, value = self.request('/api/settings/test', {'kind': 'ai'})
        self.assertEqual(status, 503)
        self.assertEqual(value['error']['code'], 'model_unavailable')
        self.settings.test = Mock(side_effect=RuntimeError('private-key-and-path'))
        status, value = self.request('/api/settings/test', {'kind': 'ai'})
        self.assertEqual(status, 500)
        self.assertNotIn('private-key-and-path', json.dumps(value))

    def test_research_errors_are_mapped_without_loading_catalog(self):
        class ResearchError(Exception):
            code, message, status = 'queue_full', '研究队列已满。', 429
        self.research.create = Mock(side_effect=ResearchError())
        with patch('service.importlib.import_module', return_value=SimpleNamespace(ResearchError=ResearchError)):
            status, value = self.request('/api/research', {'query': '计算机'})
        self.assertEqual(status, 429)
        self.assertEqual(value['error']['code'], 'queue_full')


class AgentLazyServiceTests(unittest.TestCase):
    def test_concurrent_settings_reads_initialize_one_store(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AdmissionService(Path(directory) / 'absent')
            settings = FakeSettings()
            with patch('settings_store.SettingsService', return_value=settings) as factory:
                with ThreadPoolExecutor(max_workers=6) as executor:
                    values = list(executor.map(lambda _: service.settings_public(), range(12)))
            factory.assert_called_once()
            self.assertEqual(len(values), 12)
            self.assertFalse((Path(directory) / 'absent').exists())

    def test_construction_and_close_do_not_initialize_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'new-data'
            with patch('service.importlib.import_module', side_effect=AssertionError('unexpected import')):
                service = AdmissionService(data)
                service.close()
                self.assertFalse(data.exists())

    def test_saved_search_key_precedes_environment_and_is_not_exposed(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = FakeSettings()
            settings.saved_key = 'project-saved-private'
            service = AdmissionService(Path(directory), settings=settings)
            with patch.dict(os.environ, {'GRAD_SEARCH_API_KEY': 'project-env-private', 'WSA_API_KEY': 'other-project'}):
                self.assertEqual(service._key(), 'project-saved-private')
                settings.saved_key = ''
                self.assertEqual(service._key(), 'project-env-private')

    def test_connected_status_is_based_on_test_receipts_not_key_presence(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = FakeSettings()
            settings.public_settings = lambda: {'ai': {'status': 'saved_unverified', 'has_key': True},
                                                'yuanbao': {'status': 'saved_unverified', 'has_key': True}}
            settings.saved_key = 'private'
            service = AdmissionService(Path(directory), settings=settings)
            service._published = Mock(return_value={'revision': 'r', 'units': []})
            service.radar_status = Mock(return_value={})
            status = service.catalog_bootstrap()['service']
            self.assertTrue(status['search_configured'])
            self.assertEqual(status['search_status'], 'saved_unverified')
            self.assertEqual(status['ai_status'], 'saved_unverified')
            self.assertNotIn('private', json.dumps(status))


if __name__ == '__main__':
    unittest.main()
