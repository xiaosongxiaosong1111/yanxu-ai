import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, call, patch

from server import AdmissionHandler, AdmissionHTTPServer, MAX_BODY_BYTES, RateLimiter, create_server
from service import AdmissionService, ServiceError


class FakeService:
    def bootstrap(self): return {'programs': [], 'service': {'search_configured': False}}
    def health(self): return {'status': 'ok', 'release_ready': False}
    def programs(self, **query): return query
    def subjects(self, **query): return query
    def match(self, payload):
        if payload.get('fail') == 'validation': raise ServiceError('invalid_profile', '条件格式不正确', 400)
        if payload.get('fail') == 'private': raise RuntimeError('private-api-key-path')
        return {'received': payload}
    def history_match(self, payload): return {'received': payload}
    def sensitivity(self, payload): return {'received': payload}
    def route_plan(self, payload): return {'received': payload}
    def adjustments(self, payload): return {'received': payload}
    def radar_status(self, **query): return query
    def radar_watch(self, payload): return {'received': payload}
    def radar_check(self, payload, **kwargs): return {'received': payload}
    def source_tasks(self, **query): return query
    def evidence(self, **query): return query
    def sources(self, **query): return query
    def collection_status(self, **query): return query
    def catalog_bootstrap(self): return {'revision': 'published-1', 'units': [], 'verification_mode': 'published_snapshot'}
    def catalog_programs(self, **query): return query
    def catalog_program(self, **query): return query
    def catalog_subjects(self, **query): return query
    def catalog_sources(self, **query): return query
    def source_proof(self, payload): return {'received': payload}
    def search(self, payload): raise ServiceError('not_configured', '平台尚未配置检索服务', 503)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.static = Path(self.temp.name) / 'static'
        self.static.mkdir()
        (self.static / 'index.html').write_text('<!doctype html><h1>研序</h1>', encoding='utf-8')
        (self.static / 'application.mjs').write_text('export const ready = true;', encoding='utf-8')
        (self.static / 'styles.css').write_text('body{color:black}', encoding='utf-8')
        (self.static / '.env').write_text('SECRET=private', encoding='utf-8')
        (self.static / 'sub').mkdir()
        self.server = create_server(port=0, service=FakeService(), static_dir=self.static, rate_limit=500)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method='GET', path='/', body=None, headers=None, timeout=3):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        self.addCleanup(connection.close)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def post(self, path, value, headers=None):
        return self.request('POST', path, json.dumps(value), {'Content-Type': 'application/json', **(headers or {})})

    def test_static_index_mime_and_security_headers(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertIn('研序', body.decode())
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("object-src 'none'", headers['Content-Security-Policy'])
        self.assertIn('text/html', headers['Content-Type'])
        status, headers, body = self.request(path='/application.mjs')
        self.assertEqual(status, 200)
        self.assertIn('javascript', headers['Content-Type'])
        self.assertNotIn('Python', headers.get('Server', ''))

    def test_head_has_length_but_no_body(self):
        status, headers, body = self.request('HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')
        self.assertGreater(int(headers['Content-Length']), 0)

    def test_disconnected_browser_does_not_trigger_a_second_error_response(self):
        for phase in ('end_headers', 'write'):
            handler = AdmissionHandler.__new__(AdmissionHandler)
            handler.command = 'GET'
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock(side_effect=ConnectionAbortedError('closed') if phase == 'end_headers' else None)
            handler.wfile = Mock()
            if phase == 'write':
                handler.wfile.write.side_effect = ConnectionAbortedError('closed')
            handler._send(200, b'{}')
            self.assertTrue(handler.close_connection)

    def test_directories_dotfiles_and_traversals_are_never_served(self):
        for path in ['/sub', '/.env', '/../README.md', '/%2e%2e/server.py', '/%5c..%5cserver.py', '/missing.txt', '/C:/Windows/win.ini']:
            with self.subTest(path=path):
                status, headers, body = self.request(path=path)
                self.assertIn(status, [400, 403, 404])
                self.assertNotIn(b'SECRET', body)
                self.assertNotIn(b'Directory listing', body)

    def test_all_get_api_routes_return_envelope_and_query(self):
        for path in ['/api/bootstrap', '/api/health', '/api/programs?unit_id=a&year=2027&q=408', '/api/subjects?unit_id=a&year=2027&q=408']:
            with self.subTest(path=path):
                status, headers, body = self.request(path=path)
                value = json.loads(body)
                self.assertEqual(status, 200)
                self.assertTrue(value['ok'])
                self.assertIsNone(value['error'])
                if 'year=' in path: self.assertEqual(value['data']['year'], 2027)
                self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_national_collection_progress_is_read_only_paginated_and_validates_query(self):
        status, _, raw = self.request(path='/api/collection-status?unit_id=a&status=partial&page=2&page_size=10')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['data'], {'unit_id': 'a', 'status': 'partial', 'page': '2', 'page_size': '10'})
        for path in ['/api/collection-status?private_path=x', '/api/collection-status?q=a&q=b']:
            self.assertEqual(self.request(path=path)[0], 400)

    def test_published_catalog_routes_are_paged_read_only_and_keep_full_bootstrap(self):
        for endpoint in ['catalog-programs', 'catalog-subjects']:
            status, _, body = self.request(path=f'/api/{endpoint}?unit_id=a&year=2027&offset=20&limit=10&revision=r1')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['data'], {'unit_id':'a','year':2027,'offset':20,'limit':10,'revision':'r1'})
        status, _, body = self.request(path='/api/catalog-bootstrap')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['data']['verification_mode'], 'published_snapshot')
        status, _, body = self.request(path='/api/catalog-program?program_id=p1&revision=r1')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['data']['program_id'], 'p1')
        self.assertEqual(self.request(path='/api/bootstrap')[0], 200)
        for path in ['/api/catalog-programs?offset=bad', '/api/catalog-programs?limit=1&limit=2',
                     '/api/catalog-program?private_path=x', '/api/catalog-bootstrap?unknown=1']:
            self.assertEqual(self.request(path=path)[0], 400)
        self.assertEqual(self.post('/api/catalog-programs', {})[0], 405)
        status,_,body=self.request(path='/api/catalog-sources?kind=catalog&offset=12&limit=12&revision=r1')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['data']['offset'],12)
        self.assertEqual(self.post('/api/catalog-sources',{})[0],405)

    def test_real_saved_history_flows_through_api_without_implying_admission(self):
        from urllib.parse import urlencode
        from catalog_index import CatalogIndex
        from service import COLLECTIONS
        service = AdmissionService(search_api_key='')
        registry = service._load('units.json', ('units',))
        admissions = service._load('admissions.json', COLLECTIONS)
        history = service._load('history.json', COLLECTIONS)
        sample = next(row for row in history['admitted_statistics']
                      if row['unit_id'] == '367949' and row.get('subject_scores'))
        program = next(row for row in admissions['programs']
                       if row['unit_id'] == sample['unit_id'] and row['department'] == sample['department']
                       and row['name'] == sample['program_name'] and row['study_mode'] == sample['study_mode'])
        selected_registry = {**registry, 'units': [row for row in registry['units'] if row['id'] == sample['unit_id']]}
        selected = {'metadata': admissions['metadata'], **{key: list({row['id']: row
                    for row in [*admissions[key], *history[key]]
                    if row.get('unit_id') == sample['unit_id']}.values()) for key in COLLECTIONS}}
        # Exercise the current page/detail HTTP contract without a prebuilt runtime index or national replay.
        service._catalog.index = CatalogIndex(Path(self.temp.name) / 'history-catalog.sqlite')
        service._catalog.index.sync(selected_registry, selected, {
            'release_ready': False, 'catalog_complete': False, 'registry_complete': False,
            'index_total': 1, 'program_units': 1, 'program_count': len(selected['programs']),
            'verified_unit_ids': [], 'unit_catalog_status': [{'unit_id': sample['unit_id'],
                'year': 2027, 'status': 'partial', 'verified': False}]}, 'history-http-fixture')
        self.server.service = service
        status, _, body = self.request(path='/api/catalog-bootstrap')
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)['data']['coverage']['release_ready'])
        status, _, body = self.request(path='/api/catalog-program?' + urlencode({'program_id': program['id']}))
        value = json.loads(body)
        self.assertEqual(status, 200)
        data = value['data']
        self.assertEqual(data['meta']['last_completed_admission_year'], 2026)
        self.assertFalse(data['coverage']['release_ready'])
        self.assertGreater(len(data['admitted_statistics']), 0)
        self.assertIn('transfer', {row['admission_source'] for row in data['admitted_statistics']})
        self.assertTrue(any(row.get('subject_scores') for row in data['admitted_statistics']))
        for field in ('local_path', 'text_path', 'text_pages'):
            self.assertNotIn(field, json.dumps(data['documents']))
        status, _, body = self.post('/api/history/match', {'kind': 'transfer', 'year': 2026, 'facts': {}})
        value = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(value['ok'])
        self.assertEqual(value['data']['data_status'], 'available')
        self.assertTrue(value['data']['historical_reference'])
        self.assertEqual(value['data']['matched'], [])
        self.assertEqual(len(value['data']['needs_confirmation']), 1)

    def test_invalid_query_keys_duplicate_keys_and_years_are_rejected(self):
        for path in ['/api/programs?year=bad', '/api/subjects?api_key=private', '/api/subjects?q=a&q=b']:
            with self.subTest(path=path):
                status, _, body = self.request(path=path)
                self.assertEqual(status, 400)
                self.assertFalse(json.loads(body)['ok'])

    def test_post_routes_and_validation_errors(self):
        for path in ['/api/match', '/api/history/match', '/api/sensitivity']:
            status, _, body = self.post(path, {'profile': {}})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)['ok'])
        status, _, body = self.post('/api/match', {'fail': 'validation'})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)['error']['code'], 'invalid_profile')
        status, _, body = self.post('/api/search', {})
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)['error']['code'], 'not_configured')

    def test_foreign_origins_and_dns_rebinding_hosts_are_rejected(self):
        for origin in ['https://evil.example', 'null', f'http://127.0.0.1:{self.port}.evil.example']:
            with self.subTest(origin=origin):
                status, _, _ = self.post('/api/match', {}, {'Origin': origin})
                self.assertEqual(status, 403)
        status, _, _ = self.post('/api/match', {}, {'Origin': f'http://127.0.0.1:{self.port}'})
        self.assertEqual(status, 200)
        status, _, _ = self.request(path='/api/bootstrap', headers={'Host': 'evil.example'})
        self.assertEqual(status, 403)
        status, _, _ = self.post('/api/match', {}, {'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(status, 403)

    def test_json_content_type_length_and_shape_are_required(self):
        for body, headers, expected in [('{', {'Content-Type': 'application/json'}, 400),
                                        ('[]', {'Content-Type': 'application/json'}, 400),
                                        ('{}', {'Content-Type': 'text/plain'}, 415),
                                        ('', {'Content-Type': 'application/json', 'Content-Length': '-1'}, 400),
                                        ('x' * 66000, {'Content-Type': 'application/json'}, 413)]:
            with self.subTest(headers=headers, expected=expected):
                status, _, value = self.request('POST', '/api/match', body, headers)
                self.assertEqual(status, expected)
                self.assertFalse(json.loads(value)['ok'])

    def test_unknown_route_method_and_unconfigured_chat_are_explicit(self):
        for method, path, expected in [('GET', '/api/unknown', 404), ('GET', '/api/match', 405),
                                        ('POST', '/api/chat', 503), ('PUT', '/api/match', 405),
                                        ('OPTIONS', '/api/match', 405)]:
            with self.subTest(method=method, path=path):
                status, _, body = self.request(method, path, '{}', {'Content-Type': 'application/json'})
                self.assertEqual(status, expected)
                self.assertFalse(json.loads(body)['ok'])

    def test_early_rejections_with_bodies_keep_delivering_json_without_retries(self):
        cases = [('GET', '/api/unknown', {}, 404), ('GET', '/api/match', {}, 405),
                 ('POST', '/api/chat', {}, 503), ('PUT', '/api/match', {}, 405),
                 ('OPTIONS', '/api/match', {}, 405),
                 ('POST', '/api/match', {'Origin': 'https://evil.example'}, 403),
                 ('POST', '/api/match', {'Host': 'evil.example'}, 403)]
        for index in range(12):
            for method, path, headers, expected in cases:
                with self.subTest(index=index, method=method, path=path, headers=headers):
                    status, _, body = self.request(method, path, '{"unconsumed":"body"}',
                                                   {'Content-Type': 'application/json', **headers})
                    self.assertEqual(status, expected)
                    self.assertFalse(json.loads(body)['ok'])

    def test_early_rejection_does_not_wait_for_a_declared_unsent_body(self):
        status, _, body = self.request('POST', '/api/chat', b'',
                                      {'Content-Type': 'application/json', 'Content-Length': str(MAX_BODY_BYTES)},
                                      timeout=0.5)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)['error']['code'], 'not_configured')

    def test_unhandled_errors_do_not_disclose_private_values(self):
        status, _, body = self.post('/api/match', {'fail': 'private'})
        self.assertEqual(status, 500)
        self.assertNotIn(b'private-api-key-path', body)
        self.assertEqual(json.loads(body)['error']['code'], 'internal_error')

    def test_rate_limit_applies_to_health_as_well_as_business_routes(self):
        self.server.limiter = RateLimiter(limit=2, window=60)
        self.assertEqual(self.request(path='/api/health')[0], 200)
        self.assertEqual(self.request(path='/api/health')[0], 200)
        status, headers, body = self.request(path='/api/health')
        self.assertEqual(status, 429)
        self.assertIn('Retry-After', headers)
        self.assertEqual(json.loads(body)['error']['code'], 'rate_limited')

    def test_static_and_api_budgets_are_independent_and_both_enforced(self):
        self.server.limiter = RateLimiter(limit=1)
        self.server.static_limiter = RateLimiter(limit=2)
        self.assertEqual(self.request(path='/application.mjs')[0], 200)
        self.assertEqual(self.request(path='/api/health')[0], 200)
        self.assertEqual(self.request(path='/api/health')[0], 429)
        self.assertEqual(self.request(path='/styles.css')[0], 200)
        status, headers, body = self.request(path='/')
        self.assertEqual(status, 429)
        self.assertEqual(headers['Retry-After'], '60')
        self.assertEqual(json.loads(body)['error']['code'], 'rate_limited')

    def test_static_flood_cannot_consume_api_request_budget(self):
        self.server.limiter = RateLimiter(limit=1)
        self.server.static_limiter = RateLimiter(limit=1)
        self.assertEqual(self.request(path='/')[0], 200)
        self.assertEqual(self.request(path='/styles.css')[0], 429)
        self.assertEqual(self.request(path='/favicon.ico')[0], 429)
        self.assertEqual(self.request(path='/api/radar')[0], 200)

    def test_rate_limiter_expires_without_mutating_other_clients(self):
        now = [1.0]
        limiter = RateLimiter(limit=1, window=10, clock=lambda: now[0])
        self.assertTrue(limiter.allow('a'))
        self.assertFalse(limiter.allow('a'))
        self.assertTrue(limiter.allow('b'))
        now[0] = 12
        self.assertTrue(limiter.allow('a'))

    def test_new_api_routes_envelopes_and_separate_query_schemas(self):
        for path in ['/api/route-plan', '/api/adjustments', '/api/radar/watch', '/api/radar/check', '/api/source-proof']:
            status, _, body = self.post(path, {})
            self.assertEqual(status, 200, path)
            self.assertTrue(json.loads(body)['ok'])
            self.assertEqual(self.request(path=path)[0], 405)
            self.assertEqual(self.post(path, {}, {'Origin': 'https://evil.example'})[0], 403)
        for path in ['/api/radar?program_id=a&year=2027', '/api/source-tasks?program_id=a']:
            status, _, body = self.request(path=path)
            self.assertEqual(status, 200, path)
            self.assertEqual(json.loads(body)['data']['program_id'], 'a')
        for path in ['/api/radar?q=private', '/api/radar?program_id=a&program_id=b',
                     '/api/source-tasks?year=2027', '/api/source-tasks?program_id=a&q=private']:
            self.assertEqual(self.request(path=path)[0], 400, path)

    def test_evidence_and_source_routes_have_strict_read_only_queries(self):
        for path in ['/api/evidence?program_id=a', '/api/sources?unit_id=a&year=2027&kind=fees&q=fees']:
            status, _, body = self.request(path=path)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)['ok'])
            self.assertEqual(self.post(path.split('?')[0], {})[0], 405)
        for path in ['/api/evidence', '/api/evidence?program_id=a&program_id=b', '/api/evidence?year=2027',
                     '/api/sources?api_key=x', '/api/sources?year=bad']:
            self.assertEqual(self.request(path=path)[0], 400, path)

    def test_proof_and_search_share_search_rate_limit(self):
        self.server.search_limiter = RateLimiter(limit=1)
        self.assertEqual(self.post('/api/source-proof', {})[0], 200)
        status, _, body = self.post('/api/search', {})
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(body)['error']['code'], 'rate_limited')

    def test_content_length_must_be_an_unsigned_decimal(self):
        for length in ('+2', '2 ', '-2', '0x2'):
            status, _, body = self.request('POST', '/api/match', '{}', {'Content-Type': 'application/json', 'Content-Length': length})
            self.assertEqual(status, 400, length)
            self.assertEqual(json.loads(body)['error']['code'], 'invalid_body')

    def test_radar_scheduler_is_opt_in_single_and_stops_cleanly(self):
        self.assertIsNone(getattr(self.server, '_radar_thread', None))
        checked = threading.Event()
        self.server.service.radar_status = Mock(return_value={'watch_count': 1})
        self.server.service.radar_check = Mock(side_effect=lambda _, **kwargs: checked.set())
        self.server.start_radar(interval=0.01)
        first = self.server._radar_thread
        self.server.start_radar(interval=0.01)
        self.assertIs(first, self.server._radar_thread)
        self.assertTrue(checked.wait(1))
        self.server.shutdown()
        self.server.server_close()
        self.assertFalse(first.is_alive())

    def test_radar_scheduler_keeps_idle_watches_off_the_network(self):
        self.server.service.radar_status = Mock(return_value={'watch_count': 0})
        self.server.service.radar_check = Mock()
        self.server.start_radar(interval=0.01)
        self.assertFalse(threading.Event().wait(0.035))
        self.server.shutdown()
        self.server.server_close()
        self.server.service.radar_status.assert_called()
        self.server.service.radar_check.assert_not_called()


class ShutdownTests(unittest.TestCase):
    def server_and_socket(self):
        server = object.__new__(AdmissionHTTPServer)
        server.close_request = Mock()
        connection = Mock()
        return server, connection

    def test_response_is_half_closed_then_residual_body_drained_before_close(self):
        server, connection = self.server_and_socket()
        order = Mock()
        order.attach_mock(connection.shutdown, 'half_close')
        order.attach_mock(connection.recv, 'read')
        order.attach_mock(server.close_request, 'close')
        connection.recv.side_effect = [b'{}', b'']
        server.shutdown_request(connection)
        self.assertEqual(order.mock_calls, [call.half_close(socket.SHUT_WR),
                                           call.read(8192), call.read(8192), call.close(connection)])

    def test_continuous_body_has_a_fixed_byte_budget(self):
        server, connection = self.server_and_socket()
        connection.recv.return_value = b'x' * 8192
        with patch('server.time.monotonic', return_value=10):
            server.shutdown_request(connection)
        self.assertEqual(sum(item.args[0] for item in connection.recv.call_args_list), MAX_BODY_BYTES * 2)
        self.assertTrue(all(0 < item.args[0] <= 0.025 for item in connection.settimeout.call_args_list))
        server.close_request.assert_called_once_with(connection)

    def test_drain_uses_one_total_deadline_and_stops_on_timeout(self):
        for timed_out in (False, True):
            with self.subTest(timed_out=timed_out):
                server, connection = self.server_and_socket()
                connection.recv.side_effect = socket.timeout() if timed_out else [b'body']
                with patch('server.time.monotonic', side_effect=[10, 10.001, 10.030]):
                    server.shutdown_request(connection)
                self.assertEqual(connection.recv.call_count, 1)
                server.close_request.assert_called_once_with(connection)

    def test_socket_errors_still_close_the_connection(self):
        server, connection = self.server_and_socket()
        connection.shutdown.side_effect = OSError('already disconnected')
        connection.recv.side_effect = OSError('connection closed')
        server.shutdown_request(connection)
        server.close_request.assert_called_once_with(connection)


if __name__ == '__main__':
    unittest.main()
