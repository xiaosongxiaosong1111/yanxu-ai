import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from collectors.discovery import (
    DiscoveryError, classify_title, collect_inventory, discover_unit,
    extract_links, fetch_public_page, public_target, summarize_inventory,
    _PinnedConnection, _transport, main,
)


PUBLIC = '203.0.113.8'


def resolver(host, port, **kwargs):
    return [(2, 1, 6, '', ('93.184.216.34', port))]


class DiscoveryTests(unittest.TestCase):
    def test_public_target_validates_authority_and_all_resolved_addresses(self):
        self.assertEqual(public_target('https://example.edu.cn/catalog', resolver)[0], 'example.edu.cn')
        for url in ['file:///tmp/a', 'http://127.0.0.1/', 'http://[::1]/',
                    'https://user:pass@example.edu.cn/', 'https://example.edu.cn:8080/',
                    'https://example.edu.cn/\nsecret', 'http://169.254.169.254/latest',
                    'http://10.0.0.2/', 'http://localhost/']:
            with self.subTest(url=url), self.assertRaises(DiscoveryError):
                public_target(url, resolver)
        for url in ['https://yzw.***/', 'https://example.edu.cn/' + 'x' * 5000]:
            with self.subTest(url=url), self.assertRaises(DiscoveryError):
                public_target(url, resolver)
        def mixed(host, port, **kwargs):
            return [(2, 1, 6, '', ('93.184.216.34', port)), (2, 1, 6, '', ('192.168.1.5', port))]
        with self.assertRaises(DiscoveryError):
            public_target('https://example.edu.cn/', mixed)
        def fail(*args, **kwargs):
            raise OSError('dns failed')
        with self.assertRaises(DiscoveryError):
            public_target('https://example.edu.cn/', fail)

    def test_classification_preserves_year_and_does_not_accept_generic_navigation(self):
        self.assertEqual(classify_title('2027年硕士招生专业目录', 2027), ('catalog', 2027))
        self.assertEqual(classify_title('2026年硕士复试调剂办法', 2027), ('transfer', 2026))
        self.assertEqual(classify_title('研究生招生网', 2027), ('admissions_home', None))
        self.assertIsNone(classify_title('2020年招生简章', 2027))
        self.assertIsNone(classify_title('2027年博士研究生招生简章', 2027))
        self.assertEqual(classify_title('2027年硕博士研究生招生章程', 2027), ('charter', 2027))
        self.assertIsNone(classify_title('校友新闻', 2027))

    def test_links_normalize_nested_titles_and_track_attribution_without_verifying(self):
        html = '''<h1>样本大学</h1><a href="/sch/schoolInfo--schId-123,categoryId-4.dhtml">联系办法</a>
          <a href="https://grad.example.edu.cn/2027.htm"><b>2027年</b>硕士招生目录</a>
          <a href="/zsml/">硕士目录</a><a href="javascript:alert(1)">招生简章</a>
          <a href="https://other.edu.cn/news">其他网站</a>'''
        links = extract_links(html, 'https://yz.chsi.com.cn/sch/schoolInfo--schId-123.dhtml', '123', 2027)
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0]['kind'], 'contact')
        self.assertEqual(links[1]['title'], '2027年硕士招生目录')
        self.assertEqual(links[1]['status'], 'needs_review')
        self.assertEqual(links[1]['attribution'], 'linked_from_unit_profile')
        self.assertFalse(any(link.get('verified') for link in links))

    def test_plaintext_website_is_only_a_lead_and_private_hosts_are_not_kept(self):
        html = '<h1>样本大学</h1><p>招生网址：https://grad.example.edu.cn/</p><a href="http://127.0.0.1/2027">2027硕士目录</a>'
        links = extract_links(html, 'https://yz.chsi.com.cn/sch/schoolInfo--schId-123.dhtml', '123', 2027)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['kind'], 'admissions_home')
        self.assertEqual(links[0]['status'], 'needs_review')

    def test_fragmented_website_labels_and_anchor_text_reconstruct_public_leads(self):
        html = '<p><span>招生网址：</span><a href="https://yjs.example.edu.cn/"><span>https://yjs.</span><span>example.edu.cn/</span></a></p>'
        links = extract_links(html, 'https://yz.chsi.com.cn/sch/schoolInfo--schId-123.dhtml', '123', 2027)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['url'], 'https://yjs.example.edu.cn/')
        self.assertEqual(links[0]['kind'], 'admissions_home')

    def test_safe_fetch_validates_each_redirect_and_stops_oversized_content(self):
        class Response:
            def __init__(self, status, headers, body=b''):
                self.status, self.headers, self.body = status, headers, body
            def getheader(self, name, default=None):
                return self.headers.get(name, default)
            def read(self, limit):
                return self.body[:limit]
        calls = []
        def transport(url, *args):
            calls.append(url)
            return Response(302, {'Location': 'http://127.0.0.1/private'})
        with self.assertRaises(DiscoveryError):
            fetch_public_page('https://example.edu.cn/', resolver=resolver, transport=transport, retries=0)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(DiscoveryError):
            fetch_public_page('https://example.edu.cn/', resolver=resolver,
                              transport=lambda *args: Response(200, {'Content-Type': 'text/html'}, b'x' * 100),
                              max_bytes=50, retries=0)
        with self.assertRaises(DiscoveryError):
            fetch_public_page('https://example.edu.cn/', resolver=resolver,
                              transport=lambda *args: Response(200, {'Content-Type': 'application/pdf'}), retries=0)
        result = fetch_public_page('https://example.edu.cn/', resolver=resolver,
                                   transport=lambda *args: Response(200, {'Content-Type': 'text/html; charset=utf-8'}, '<h1>招生</h1>'.encode()), retries=0)
        self.assertIn('招生', result['text'])
        self.assertEqual(len(result['sha256']), 64)

    def test_safe_fetch_retries_bounded_errors_and_marks_restriction(self):
        class Response:
            status = 403
            def getheader(self, name, default=None): return default
            def read(self, limit): return b''
        with self.assertRaises(DiscoveryError) as ctx:
            fetch_public_page('https://example.edu.cn/', resolver=resolver, transport=lambda *args: Response(), retries=0)
        self.assertEqual(ctx.exception.code, 'restricted')
        calls = []
        def bad(*args):
            calls.append(1)
            raise TimeoutError('timeout')
        with patch('collectors.discovery.time.sleep'), self.assertRaises(DiscoveryError):
            fetch_public_page('https://example.edu.cn/', resolver=resolver, transport=bad, retries=1)
        self.assertEqual(len(calls), 2)

    def test_discover_saves_snapshots_and_keeps_partial_failure_honest(self):
        unit = {'id': '123', 'name': '样本大学', 'source_url': 'https://yz.chsi.com.cn/sch/schoolInfo--schId-123.dhtml', 'official_url': None}
        def fetch(url):
            if 'categoryId' in url:
                raise DiscoveryError('restricted', 'HTTP 403')
            text = '<a href="/sch/schoolInfo--schId-123,categoryId-4.dhtml">联系办法</a><a href="https://grad.example.edu.cn/2027.htm">2027硕士招生目录</a>'
            return {'url': url, 'text': text, 'sha256': 'a' * 64, 'content_type': 'text/html'}
        with tempfile.TemporaryDirectory() as folder:
            result = discover_unit(unit, 2027, Path(folder), fetcher=fetch)
            self.assertEqual(result['unit_id'], '123')
            self.assertEqual(result['status'], 'discovered')
            self.assertEqual(result['candidates'][1]['kind'], 'catalog')
            self.assertTrue(any(page['status'] == 'restricted' for page in result['pages']))
            self.assertTrue(any(Path(page['snapshot_path']).is_file() for page in result['pages'] if page['status'] == 'fetched'))
            self.assertFalse(result['catalog_complete'])

    def test_inventory_includes_pending_units_and_resumes_only_matching_year(self):
        units = [{'id': '1', 'name': '一', 'source_url': 'https://example.edu.cn/'}, {'id': '2', 'name': '二', 'source_url': 'https://example.edu.cn/'}]
        def discover(unit, year, folder):
            return {'unit_id': unit['id'], 'unit_name': unit['name'], 'year': year,
                    'status': 'not_found', 'checked_at': 'now', 'pages': [], 'candidates': [], 'catalog_complete': False}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'inventory.json'
            result = collect_inventory(units, 2027, output, Path(folder) / 'snapshots', limit=1, workers=1, discoverer=discover)
            self.assertEqual(result['metadata']['attempted_units'], 1)
            self.assertEqual(result['metadata']['pending_units'], 1)
            self.assertFalse(result['metadata']['attempt_complete'])
            result = collect_inventory(units, 2027, output, Path(folder) / 'snapshots', workers=1, discoverer=discover)
            self.assertEqual(result['metadata']['attempted_units'], 2)
            self.assertTrue(result['metadata']['attempt_complete'])
            self.assertFalse(result['metadata']['catalog_complete'])
            self.assertEqual(len(json.loads(output.read_text(encoding='utf-8'))['units']), 2)
            result = collect_inventory(units, 2026, output, Path(folder), limit=0, discoverer=discover)
            self.assertEqual(result['metadata']['attempted_units'], 0)

    def test_summary_counts_distinct_candidates_and_preserves_failures(self):
        rows = [{'status': 'discovered', 'pages': [{'status': 'fetched'}, {'status': 'dns_error'}],
                 'candidates': [{'url': 'https://x.edu.cn/', 'host': 'x.edu.cn', 'kind': 'catalog', 'year': 2027}]},
                {'status': 'failed', 'candidates': []}, {'status': 'restricted', 'candidates': []},
                {'status': 'pending', 'candidates': []}]
        result = summarize_inventory(rows, 2027)
        self.assertEqual(result['attempted_units'], 3)
        self.assertEqual(result['failed_units'], 1)
        self.assertEqual(result['restricted_units'], 1)
        self.assertEqual(result['candidate_count'], 1)
        self.assertEqual(result['current_year_document_candidates'], 1)
        self.assertEqual(result['fetched_pages'], 1)
        self.assertEqual(result['failed_pages'], 1)
        self.assertFalse(result['catalog_complete'])

    def test_captcha_or_login_destination_is_restricted_not_a_missing_source(self):
        unit = {'id': '123', 'name': '样本大学', 'source_url': 'https://yz.chsi.com.cn/sch/schoolInfo--schId-123.dhtml'}
        for url, html in [('https://account.chsi.com.cn/login', '<title>登录</title>'),
                          (unit['source_url'], '<title>安全验证</title>')]:
            with self.subTest(url=url), tempfile.TemporaryDirectory() as folder:
                result = discover_unit(unit, 2027, Path(folder), fetcher=lambda _: {
                    'url': url, 'text': html, 'sha256': 'b' * 64, 'content_type': 'text/html'})
                self.assertEqual(result['status'], 'restricted')
                self.assertEqual(result['pages'][0]['status'], 'restricted')

    def test_external_source_cannot_claim_registry_attribution_by_substring(self):
        links = extract_links('<a href="https://grad.example.edu.cn/2027">2027硕士目录</a>',
                              'https://yz.chsi.com.cn.evil.example/page', '123', 2027)
        self.assertEqual(links[0]['attribution'], 'linked_from_source_lead')

    def test_pinned_connection_uses_verified_ip_and_tls_original_hostname(self):
        sock, secure = Mock(), Mock()
        context = Mock()
        context.wrap_socket.return_value = secure
        with patch('collectors.discovery.socket.create_connection', return_value=sock) as connect:
            connection = _PinnedConnection('example.edu.cn', 80, '93.184.216.34', 3, False)
            connection.connect()
            self.assertIs(connection.sock, sock)
            connect.assert_called_once_with(('93.184.216.34', 80), 3)
        with patch('collectors.discovery.socket.create_connection', return_value=sock), patch('collectors.discovery.ssl.create_default_context', return_value=context):
            connection = _PinnedConnection('example.edu.cn', 443, '93.184.216.34', 3, True)
            connection.connect()
            context.wrap_socket.assert_called_once_with(sock, server_hostname='example.edu.cn')
            self.assertIs(connection.sock, secure)

    def test_transport_sends_only_get_and_closes_response_and_connection(self):
        connection, response = Mock(), Mock()
        response.status = 200
        response.getheader.return_value = 'text/html'
        response.read.return_value = b'<html></html>'
        connection.getresponse.return_value = response
        with patch('collectors.discovery._PinnedConnection', return_value=connection):
            result = _transport('https://example.edu.cn/a?year=2027#unused', ('example.edu.cn', 443, ('93.184.216.34',)), 3)
            self.assertEqual(connection.request.call_args.args, ('GET', '/a?year=2027'))
            self.assertEqual(result.status, 200)
            self.assertEqual(result.getheader('Content-Type'), 'text/html')
            self.assertEqual(result.read(100), b'<html></html>')
            result.close()
            response.close.assert_called_once()
            connection.close.assert_called_once()
        connection.request.side_effect = OSError('network')
        with patch('collectors.discovery._PinnedConnection', return_value=connection), self.assertRaises(OSError):
            _transport('https://example.edu.cn/', ('example.edu.cn', 443, ('93.184.216.34',)), 3)
        self.assertEqual(connection.close.call_count, 2)

    def test_corrupt_checkpoint_and_worker_failure_remain_explicit(self):
        units = [{'id': '1', 'name': '一', 'source_url': 'https://example.edu.cn/'}]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'inventory.json'
            output.write_text('{broken', encoding='utf-8')
            def fail(*args): raise RuntimeError('private diagnostic text')
            with redirect_stdout(io.StringIO()):
                result = collect_inventory(units, 2027, output, Path(folder), workers=1, discoverer=fail)
            self.assertEqual(result['units'][0]['status'], 'failed')
            self.assertEqual(result['units'][0]['error'], 'RuntimeError')
            self.assertNotIn('private diagnostic', output.read_text(encoding='utf-8'))

    def test_cli_zero_limit_creates_a_pending_inventory_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            units = Path(folder) / 'units.json'
            output = Path(folder) / 'inventory.json'
            units.write_text(json.dumps({'units': [{'id': '1', 'name': '一', 'source_url': 'https://example.edu.cn/'}]}), encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                main(['--units', str(units), '--output', str(output), '--snapshot-dir', folder, '--limit', '0'])
            self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['metadata']['attempted_units'], 0)


if __name__ == '__main__':
    unittest.main()
