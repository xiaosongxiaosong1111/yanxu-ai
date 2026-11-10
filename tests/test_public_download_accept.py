"""Exercise default pinned downloads against a socket-level MIME-negotiating server."""
from contextlib import contextmanager
import socket
import ssl
import threading
import unittest
from unittest.mock import Mock, patch

from collectors.discovery import DiscoveryError, USER_AGENT
from collectors.national_sources import fetch_source
from public_sources import SourceError, fetch_official_document


UNIT = {'id': '123', 'name': '样本大学', 'official_url': 'https://www.example.edu.cn/'}
BODY = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1fixture spreadsheet container'


def resolver(host, port, **kwargs):
    return [(2, 1, 6, '', ('93.184.216.34', port))]


def response(status=200, body=BODY, media='application/vnd.ms-excel', extra=''):
    return (f'HTTP/1.1 {status} Test\r\nContent-Type: {media}\r\n'
            f'Content-Length: {len(body)}\r\nConnection: close\r\n{extra}\r\n').encode() + body


def mime_accepting_iis(headers):
    accepted = {}
    for part in headers.get('accept', '').split(','):
        media, *params = part.strip().split(';')
        accepted[media] = float(next((p[2:] for p in params if p.startswith('q=')), '1'))
    if any(accepted.get(kind, 0) > 0 for kind in ('application/vnd.ms-excel', 'application/*', '*/*')):
        return response()
    return response(406, b'Client does not accept the MIME type', 'text/html')


@contextmanager
def pinned_server(factory, *, certificate_error=False):
    """Replace the socket only; both public entry points retain their default transport."""
    client, server = socket.socketpair()
    server.settimeout(2)
    state = {'wire': b'', 'headers': {}}
    def serve():
        try:
            while b'\r\n\r\n' not in state['wire']:
                chunk = server.recv(4096)
                if not chunk:
                    return
                state['wire'] += chunk
            lines = state['wire'].decode('ascii').split('\r\n')
            state['headers'] = {key.lower(): value.strip() for line in lines[1:] if ': ' in line
                                for key, value in [line.split(': ', 1)]}
            server.sendall(factory(state['headers']))
        except OSError:
            pass
        finally:
            server.close()
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    context = Mock()
    context.wrap_socket.return_value = client
    if certificate_error:
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError('untrusted certificate')
    try:
        with patch('collectors.discovery.socket.create_connection', return_value=client) as connect, \
             patch('collectors.discovery.ssl.create_default_context', return_value=context) as verified:
            yield state, connect, context, verified
    finally:
        client.close()
        worker.join(3)
        if worker.is_alive():
            raise AssertionError('diagnostic server did not terminate')


class PublicDownloadAcceptTests(unittest.TestCase):
    def test_default_http_and_https_downloads_accept_excel_without_spoofing_identity(self):
        for scheme, port in (('http', 80), ('https', 443)):
            with self.subTest(scheme=scheme):
                with pinned_server(mime_accepting_iis) as (state, connect, context, verified):
                    result = fetch_source(f'{scheme}://grad.example.edu.cn/catalog.xls', UNIT,
                                          resolver=resolver)
                self.assertEqual(result['raw'], BODY)
                self.assertEqual(result['content_type'], 'application/vnd.ms-excel')
                self.assertEqual(connect.call_args.args[0], ('93.184.216.34', port))
                self.assertEqual(state['headers']['host'], 'grad.example.edu.cn')
                self.assertEqual(state['headers']['user-agent'], USER_AGENT)
                self.assertNotIn('referer', state['headers'])
                self.assertNotIn('authorization', state['headers'])
                for kind in ('text/html', 'application/xhtml+xml', 'application/pdf'):
                    self.assertIn(kind, state['headers']['accept'])
                self.assertIn('*/*;q=0.8', state['headers']['accept'])
                if scheme == 'https':
                    verified.assert_called_once_with()
                    context.wrap_socket.assert_called_once_with(context.wrap_socket.return_value,
                                                               server_hostname='grad.example.edu.cn')
                else:
                    verified.assert_not_called()

    def test_public_document_entry_still_reads_html_through_the_same_pinned_transport(self):
        with pinned_server(lambda headers: response(body=b'<main>official source</main>', media='text/html')) as (state, connect, _, _):
            result = fetch_official_document('http://grad.example.edu.cn/info', UNIT, resolver=resolver)
        self.assertIn('official source', result['text'])
        self.assertEqual(connect.call_args.args[0], ('93.184.216.34', 80))
        self.assertIn('text/html', state['headers']['accept'])

    def test_private_dns_target_still_fails_before_opening_a_socket(self):
        private = lambda *args, **kwargs: [(2, 1, 6, '', ('127.0.0.1', 80))]
        with patch('collectors.discovery.socket.create_connection') as connect:
            with self.assertRaises(DiscoveryError) as caught:
                fetch_source('http://grad.example.edu.cn/catalog.xls', UNIT, resolver=private)
        self.assertEqual(caught.exception.code, 'unsafe_url')
        connect.assert_not_called()

    def test_cross_school_redirect_is_not_followed_under_the_wider_accept(self):
        dns = Mock(side_effect=resolver)
        with pinned_server(lambda headers: response(302, b'', 'text/html',
            'Location: http://other.edu.cn/catalog.xls\r\n')) as (_, connect, _, _):
            with self.assertRaises(SourceError) as caught:
                fetch_source('http://grad.example.edu.cn/catalog.xls', UNIT, resolver=dns)
        self.assertEqual(caught.exception.code, 'wrong_unit')
        self.assertEqual(dns.call_count, 1)
        connect.assert_called_once()

    def test_declared_size_limit_remains_enforced(self):
        with pinned_server(lambda headers: response()) as (_, connect, _, _):
            with self.assertRaises(SourceError) as caught:
                fetch_source('http://grad.example.edu.cn/catalog.xls', UNIT,
                             resolver=resolver, max_bytes=8)
        self.assertEqual(caught.exception.code, 'too_large')
        connect.assert_called_once()

    def test_accept_does_not_promote_a_fake_office_payload(self):
        with pinned_server(lambda headers: response(body=b'not an OLE container')):
            with self.assertRaises(SourceError) as caught:
                fetch_source('http://grad.example.edu.cn/catalog.xls', UNIT, resolver=resolver)
        self.assertEqual(caught.exception.code, 'unsupported_type')

    def test_https_certificate_failure_is_not_downgraded_or_retried(self):
        with pinned_server(mime_accepting_iis, certificate_error=True) as (_, connect, context, verified):
            with self.assertRaises(ssl.SSLCertVerificationError):
                fetch_source('https://grad.example.edu.cn/catalog.xls', UNIT, resolver=resolver)
        connect.assert_called_once()
        verified.assert_called_once_with()
        self.assertEqual(context.wrap_socket.call_args.kwargs['server_hostname'], 'grad.example.edu.cn')


if __name__ == '__main__':
    unittest.main()
