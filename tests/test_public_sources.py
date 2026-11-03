import hashlib
import io
from pathlib import Path
import tempfile
import threading
import time
import socket
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import Mock, patch

from public_sources import SourceError, fetch_official_document, official_source_url, rendered_source_html


UNIT = {'id': '123', 'name': '样本大学', 'official_url': 'https://www.example.edu.cn/'}


def resolver(host, port, **kwargs):
    return [(2, 1, 6, '', ('93.184.216.34', port))]


class Response(io.BytesIO):
    def __init__(self, body=b'', status=200, **headers):
        super().__init__(body)
        self.status, self.headers = status, {'Content-Type': 'text/html; charset=utf-8', **headers}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class PublicSourceTests(unittest.TestCase):
    def test_official_charter_hidden_publication_is_rendered_without_other_hidden_fields(self):
        url = 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?schCode=10001'
        html = '''<h1>样本大学2027年硕士研究生招生章程</h1><div id="cnt_body_wrapper"></div>
        <input type="hidden" id="hid_content" value="&lt;p&gt;专业目录原文&lt;/p&gt;&lt;a href='https://example.edu.cn/catalog.pdf'&gt;目录附件&lt;/a&gt;">
        <input type="hidden" id="other" value="PRIVATE_UNPUBLISHED">
        <script>$("#cnt_body_wrapper").html($("#hid_content").val());</script>'''
        rendered = rendered_source_html(html, url)
        self.assertIn('<p>专业目录原文</p>', rendered)
        self.assertEqual(rendered_source_html(rendered, url), rendered)
        for other in ['https://other.edu.cn/zsgs/zsjz/detail.action', 'https://yz.chsi.com.cn/other']:
            self.assertEqual(rendered_source_html(html, other), html)
        page = fetch_official_document(url, {**UNIT, 'unit_code': '10001'}, resolver=resolver,
            transport=lambda *args: Response(html.encode()))
        self.assertIn('专业目录原文', page['text'])
        self.assertNotIn('PRIVATE_UNPUBLISHED', page['text'])
        self.assertEqual(page['raw'], html.encode())
        self.assertEqual(page['sha256'], hashlib.sha256(html.encode()).hexdigest())

    def test_charter_renderer_does_not_treat_comments_or_strings_as_published_code(self):
        url = 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?schCode=10001'
        body = '<div id="cnt_body_wrapper"></div><input type="hidden" id="hid_content" value="&lt;p&gt;UNPUBLISHED&lt;/p&gt;">'
        statement = '$("#cnt_body_wrapper").html($("#hid_content").val());'
        for code in ['/* '+statement+' */', '// '+statement, '`\n'+statement+'\n`',
                     'if(false){\n'+statement+'\n}']:
            html = body + '<script>' + code + '</script>'
            self.assertEqual(rendered_source_html(html, url), html)
        actual = body+'<script>$(document).ready(function(){\n'+statement+'\ninitSchRep();\n});</script>'
        self.assertIn('<p>UNPUBLISHED</p>', rendered_source_html(actual, url))

    def fetch(self, response, **kwargs):
        return fetch_official_document('https://grad.example.edu.cn/a', UNIT,
                                       resolver=resolver, transport=lambda *args: response, **kwargs)

    def test_html_preserves_source_hash_and_visible_main_text(self):
        raw = '<title>2027年招生</title><nav>旧资料</nav><main><h1>2027年招生</h1><p>学费8000元/年</p><script>恶意()</script><p hidden>90000元</p><span style="display: none">10000元</span><span class="tooltip">提示文字</span></main><footer>版权</footer>'.encode()
        page = self.fetch(Response(raw))
        self.assertEqual(page['raw'], raw)
        self.assertEqual(page['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(page['document_title'], '2027年招生')
        self.assertIn('学费8000元/年', page['pages'][0]['text'])
        for hidden in ['旧资料', '恶意', '90000', '10000', '提示文字', '版权']:
            self.assertNotIn(hidden, page['text'])
        self.assertEqual(page['extraction_status'], 'extracted')

    def test_domain_boundary_is_specific_to_unit_and_exact_chsi_host(self):
        for url in ['https://grad.example.edu.cn/a', 'https://yz.chsi.com.cn/a']:
            self.assertTrue(official_source_url(url, UNIT))
        for url in ['https://other.edu.cn/a', 'https://example.edu.cn.evil.com/',
                    'https://evil.chsi.com.cn/', 'https://yz.chsi.com.cn.evil.com/',
                    'http://127.0.0.1/a', 'https://user@example.edu.cn/',
                    'https://grad.example.edu.cn:8443/', 'file:///etc/passwd']:
            self.assertFalse(official_source_url(url, UNIT), url)

    def test_cross_school_redirect_is_rejected_before_resolving_or_requesting(self):
        transport = Mock(return_value=Response(status=302, Location='https://other.edu.cn/private'))
        dns = Mock(side_effect=resolver)
        with self.assertRaises(SourceError) as error:
            fetch_official_document('https://example.edu.cn/a', UNIT, resolver=dns, transport=transport)
        self.assertEqual(error.exception.code, 'wrong_unit')
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(dns.call_count, 1)

    def test_chsi_school_id_is_checked_before_network_and_on_redirect(self):
        for url in ['https://yz.chsi.com.cn/sch/view--schId-999.dhtml',
                    'https://yz.chsi.com.cn/page?schId=999',
                    'https://yz.chsi.com.cn/page?schId=123&schId=999',
                    'https://yz.chsi.com.cn/sch/view--schId-1234.dhtml']:
            with self.subTest(url=url):
                self.assertFalse(official_source_url(url, UNIT))
                transport = Mock()
                with self.assertRaises(SourceError) as error:
                    fetch_official_document(url, UNIT, resolver=resolver, transport=transport)
                self.assertEqual(error.exception.code, 'wrong_unit')
                transport.assert_not_called()
        transport = Mock(return_value=Response(status=302, Location='https://yz.chsi.com.cn/page?schId=999'))
        with self.assertRaises(SourceError):
            fetch_official_document('https://yz.chsi.com.cn/page?schId=123', UNIT,
                                    resolver=resolver, transport=transport)
        self.assertEqual(transport.call_count, 1)

    def test_chsi_charter_unit_code_must_match_even_when_school_id_matches(self):
        unit = {**UNIT, 'unit_code': '10001'}
        correct = 'https://yz.chsi.com.cn/zsgs/zsjz/detailall.action?schCode=10001'
        self.assertTrue(official_source_url(correct, unit))
        page = fetch_official_document(correct, unit, resolver=resolver,
            transport=lambda *args: Response('<title>招生章程</title>'.encode()))
        self.assertEqual(page['attribution_status'], 'verified')
        for suffix in ('schCode=10002', 'schCode=10001&schCode=10002',
                       'schCode=10002&schId=123', 'schCode=', 'schCode=bad'):
            with self.subTest(suffix=suffix):
                self.assertFalse(official_source_url('https://yz.chsi.com.cn/zsgs/zsjz/detailall.action?' + suffix, unit))
        self.assertFalse(official_source_url(correct, UNIT))

    def test_official_school_names_normalize_parenthesis_width_but_keep_campus_identity(self):
        unit = {**UNIT, 'name': '样本大学(华东)', 'unit_code': '10001'}
        url = 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?schCode=10001'
        page = fetch_official_document(url, unit, resolver=resolver,
            transport=lambda *args: Response('<h1>样本大学（华东）2027年硕士招生章程</h1>'.encode()))
        self.assertEqual(page['attribution_status'], 'verified')
        with self.assertRaises(SourceError):
            fetch_official_document(url, unit, resolver=resolver,
                transport=lambda *args: Response('<h1>样本大学（北京）2027年硕士招生章程</h1>'.encode()))

    def test_chsi_unscoped_page_remains_readable_but_attribution_requires_school(self):
        def fetch(html, url='https://yz.chsi.com.cn/page'):
            return fetch_official_document(url, UNIT, resolver=resolver,
                transport=lambda *args: Response(html.encode()))
        unknown = fetch('<title>2027年招生</title><h1>学费说明</h1><p>学费8000元/年</p>')
        self.assertEqual(unknown['attribution_status'], 'needs_review')
        named = fetch('<title>中国研究生招生信息网</title><h1>样本大学2027年硕士招生</h1>')
        self.assertEqual(named['attribution_status'], 'verified')
        self.assertEqual(named['document_heading'], '样本大学2027年硕士招生')
        identified = fetch('<title>2027年学费</title>', 'https://yz.chsi.com.cn/page?schId=123')
        self.assertEqual(identified['attribution_status'], 'verified')
        for url in ['https://yz.chsi.com.cn/page', 'https://yz.chsi.com.cn/page?schId=123']:
            with self.subTest(url=url), self.assertRaises(SourceError) as error:
                fetch('<title>2027年学费</title><h1>另一大学2027年招生</h1><p>相关新闻提到样本大学。</p>', url)
            self.assertEqual(error.exception.code, 'wrong_unit')

    def test_mixed_private_dns_and_rebinding_redirect_are_rejected(self):
        answers = [[(2, 1, 6, '', ('93.184.216.34', 443))], [(2, 1, 6, '', ('10.0.0.2', 443))]]
        transport = Mock(return_value=Response(status=302, Location='/b'))
        with self.assertRaises(SourceError) as error:
            fetch_official_document('https://example.edu.cn/a', UNIT,
                                    resolver=Mock(side_effect=answers), transport=transport)
        self.assertEqual(error.exception.code, 'unsafe_url')
        self.assertEqual(transport.call_count, 1)
        with self.assertRaises(SourceError):
            fetch_official_document('https://example.edu.cn/a', UNIT,
                resolver=lambda *a, **k: answers[0] + answers[1], transport=transport)

    def test_transport_receives_only_prevalidated_pinned_addresses(self):
        transport = Mock(return_value=Response('<p>正文</p>'.encode()))
        fetch_official_document('https://example.edu.cn/a', UNIT, resolver=resolver, transport=transport)
        self.assertEqual(transport.call_args.args[1], ('example.edu.cn', 443, ('93.184.216.34',)))

    def test_redirect_limit_and_http_failures_are_explicit(self):
        transport = Mock(side_effect=lambda *a: Response(status=302, Location='/next'))
        with self.assertRaises(SourceError) as error:
            fetch_official_document('https://example.edu.cn/a', UNIT, resolver=resolver, transport=transport)
        self.assertEqual(error.exception.code, 'redirect_error')
        self.assertEqual(transport.call_count, 4)
        for status, code in [(429, 'restricted'), (403, 'restricted'), (500, 'http_error')]:
            with self.subTest(status=status), self.assertRaises(SourceError) as error:
                self.fetch(Response(status=status))
            self.assertEqual(error.exception.code, code)

    def test_limits_unknown_type_timeout_and_empty_text(self):
        cases = [(Response(b'x' * 33), {'max_bytes': 32}, 'too_large'),
                 (Response(b'x', **{'Content-Length': '100'}), {'max_bytes': 32}, 'too_large'),
                 (Response(b'{}', **{'Content-Type': 'application/json'}), {}, 'unsupported_type')]
        for response, kwargs, code in cases:
            with self.subTest(code=code), self.assertRaises(SourceError) as error:
                self.fetch(response, **kwargs)
            self.assertEqual(error.exception.code, code)
        with self.assertRaises(SourceError) as error:
            fetch_official_document('https://example.edu.cn/', UNIT, resolver=resolver,
                                    transport=Mock(side_effect=TimeoutError('private detail')))
        self.assertEqual(error.exception.code, 'timeout')
        self.assertEqual(self.fetch(Response(b'<script>bad()</script>'))['extraction_status'], 'empty_text')

    def test_pdf_has_exact_pages_and_uses_runtime_temp_directory(self):
        raw = b'%PDF-1.7 fake fixture'
        paths = []
        def extract(path):
            paths.append(Path(path))
            self.assertEqual(Path(path).read_bytes(), raw)
            return '2027年正文\n第一页\f第二页\f'
        with tempfile.TemporaryDirectory() as folder:
            result = self.fetch(Response(raw, **{'Content-Type': 'application/pdf'}),
                                cache_dir=folder, pdf_extractor=extract)
            self.assertEqual([p['page'] for p in result['pages']], [1, 2])
            self.assertEqual(result['text'], '2027年正文\n第一页\f第二页\f')
            self.assertTrue(paths[0].is_relative_to(Path(folder)))
            self.assertFalse(paths[0].exists())
        self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())

    def test_invalid_pdf_and_extractor_failure_are_explicit(self):
        with self.assertRaises(SourceError) as error:
            self.fetch(Response(b'not pdf', **{'Content-Type': 'application/pdf'}))
        self.assertEqual(error.exception.code, 'invalid_pdf')
        with self.assertRaises(SourceError) as error:
            self.fetch(Response(b'%PDF-1.7', **{'Content-Type': 'application/pdf'}),
                       pdf_extractor=Mock(side_effect=ValueError('bad pdf')))
        self.assertEqual(error.exception.code, 'extraction_error')

    def test_bare_suffix_cannot_expand_unit_ownership_and_tooltip_roles_are_hidden(self):
        self.assertFalse(official_source_url('https://other.edu.cn/a', {'official_url': 'https://edu.cn/'}))
        result = self.fetch(Response('<p>正文</p><div role="tooltip">假的8000元</div><div class="d-none">假的9000元</div>'.encode()))
        self.assertEqual(result['text'], '正文')

    def test_slow_dns_obeys_whole_request_deadline(self):
        release = threading.Event()
        def slow(*args, **kwargs):
            release.wait(1)
            return resolver(*args, **kwargs)
        transport = Mock()
        started = time.monotonic()
        try:
            with self.assertRaises(SourceError) as error:
                fetch_official_document('https://example.edu.cn/', UNIT, timeout=.01, resolver=slow, transport=transport)
        finally:
            release.set()
        self.assertEqual(error.exception.code, 'timeout')
        self.assertLess(time.monotonic() - started, .3)
        transport.assert_not_called()

    def test_slow_headers_and_body_obey_hard_deadline_and_close_socket(self):
        for slow_headers, close_connection in [(True, False), (False, False), (False, True)]:
            with self.subTest(slow_headers=slow_headers, close_connection=close_connection):
                client, server = socket.socketpair()
                stop = threading.Event()
                def send_slowly():
                    try:
                        prefix = (b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nX-Slow: ' if slow_headers
                                  else b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 12\r\n'
                                       + (b'Connection: close\r\n' if close_connection else b'') + b'\r\n')
                        server.sendall(prefix)
                        for _ in range(12):
                            if stop.wait(.04):
                                return
                            server.sendall(b'x')
                        if slow_headers:
                            server.sendall(b'\r\nContent-Length: 2\r\n\r\nok')
                    except OSError:
                        pass
                    finally:
                        server.close()
                sender = threading.Thread(target=send_slowly, daemon=True)
                sender.start()
                started = time.monotonic()
                try:
                    with patch('collectors.discovery.socket.create_connection', return_value=client):
                        with self.assertRaises(SourceError) as error:
                            fetch_official_document('http://grad.example.edu.cn/a', UNIT, timeout=.1, resolver=resolver)
                    self.assertEqual(error.exception.code, 'timeout')
                    self.assertLess(time.monotonic() - started, .3)
                    self.assertEqual(client.fileno(), -1)
                finally:
                    stop.set()
                    client.close()
                    sender.join(1)
                self.assertFalse(sender.is_alive())

    def test_deadline_timer_is_joined_after_success_and_timeout(self):
        timer_type, timers = threading.Timer, []
        def timer_factory(*args, **kwargs):
            timer = timer_type(*args, **kwargs)
            timers.append(timer)
            return timer
        with patch('public_sources.threading.Timer', side_effect=timer_factory):
            self.fetch(Response(b'<p>ok</p>'))
            with self.assertRaises(SourceError):
                fetch_official_document('https://example.edu.cn/', UNIT, resolver=resolver,
                    transport=Mock(side_effect=TimeoutError('timeout')))
        self.assertEqual(len(timers), 2)
        self.assertTrue(all(not timer.is_alive() for timer in timers))

    def test_deadline_guard_setup_failure_releases_host_limit(self):
        semaphore = Mock()
        semaphore.acquire.return_value = True
        with patch('public_sources._LIMITS.for_host', return_value=semaphore), \
             patch('public_sources._DeadlineGuard', side_effect=SourceError('timeout', 'deadline expired')):
            with self.assertRaises(SourceError):
                self.fetch(Response(b'<p>ok</p>'))
        semaphore.release.assert_called_once_with()
        semaphore.reset_mock()
        response = Response(b'<p>ok</p>')
        with patch('public_sources._LIMITS.for_host', return_value=semaphore), \
             patch.object(response, 'close', side_effect=OSError('closed socket')):
            with self.assertRaises(SourceError):
                self.fetch(response)
        semaphore.release.assert_called_once_with()

    def test_same_host_reads_are_serialized(self):
        first = threading.Event()
        release = threading.Event()
        active, peak = [], []
        lock = threading.Lock()
        class HeldResponse(Response):
            def close(self):
                with lock:
                    if active:
                        active.pop()
                super().close()
        def transport(*args):
            with lock:
                active.append(1)
                peak.append(len(active))
            first.set()
            release.wait(1)
            return HeldResponse(b'<p>ok</p>')
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(fetch_official_document, 'https://example.edu.cn/a', UNIT, resolver=resolver, transport=transport)
            first.wait(1)
            two = pool.submit(fetch_official_document, 'https://example.edu.cn/b', UNIT, resolver=resolver, transport=transport)
            release.set()
            one.result()
            two.result()
        self.assertEqual(max(peak), 1)

    def test_default_pdf_extractor_uses_bounded_pdftotext(self):
        completed = Mock(stdout='页一\f页二\f'.encode())
        with patch('public_sources.shutil.which', return_value='pdftotext'), patch('public_sources.subprocess.run', return_value=completed) as run:
            result = self.fetch(Response(b'%PDF-1.7 fixture', **{'Content-Type': 'application/pdf'}))
        self.assertEqual(len(result['pages']), 2)
        self.assertEqual(run.call_args.args[0][1:4], ['-raw', '-enc', 'UTF-8'])
        self.assertLessEqual(run.call_args.kwargs['timeout'], 10)
        with patch('public_sources.shutil.which', return_value=None), self.assertRaises(SourceError) as error:
            self.fetch(Response(b'%PDF-1.7', **{'Content-Type': 'application/pdf'}))
        self.assertEqual(error.exception.code, 'extraction_unavailable')


if __name__ == '__main__':
    unittest.main()
