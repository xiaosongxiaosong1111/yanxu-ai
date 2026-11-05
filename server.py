"""Local-only static and JSON HTTP boundary for the admission research app."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import socket
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from service import AdmissionService, ServiceError
from knowledge_api import READ_ACTIONS, WRITE_ACTIONS, checked_retrieve, checked_empty


MAX_BODY_BYTES = 65536
CLOSE_DRAIN_SECONDS = 0.025
MAX_CLOSE_DRAIN_BYTES = MAX_BODY_BYTES * 2
STATIC_TYPES = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
                '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
                '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml',
                '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
                '.ico': 'image/x-icon', '.woff2': 'font/woff2'}
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


class RateLimiter:
    def __init__(self, limit=300, window=60, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, client):
        now = self.clock()
        with self._lock:
            start, count = self._hits.get(client, (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            if count >= self.limit:
                return False
            kept = self._hits if len(self._hits) < 2048 else {key: row for key, row in self._hits.items() if now - row[0] < self.window}
            self._hits = {**kept, client: (start, count + 1)}
            return True


class AdmissionHTTPServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True
    allow_reuse_address = True

    def start_radar(self, interval=3600):
        if getattr(self, '_radar_thread', None) is not None:
            return
        self._radar_stop = threading.Event()
        self._radar_error = None
        def poll():
            while not self._radar_stop.wait(interval):
                try:
                    if self.service.radar_status().get('watch_count'):
                        self.service.radar_check({}, stop_event=self._radar_stop)
                    self._radar_error = None
                except Exception:
                    self._radar_error = 'radar_unavailable'
        self._radar_thread = threading.Thread(target=poll, name='admission-radar', daemon=True)
        self._radar_thread.start()

    def server_close(self):
        if getattr(self, '_radar_thread', None) is not None:
            self._radar_stop.set()
            self._radar_thread.join(timeout=25)
        close = getattr(getattr(self, 'service', None), 'close', None)
        try:
            if callable(close):
                close()
        finally:
            super().server_close()

    def shutdown_request(self, request):
        # The handler has already flushed its response. Closing with an unread
        # request body can send a TCP reset on Windows and discard that response.
        try:
            request.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        deadline = time.monotonic() + CLOSE_DRAIN_SECONDS
        remaining = MAX_CLOSE_DRAIN_BYTES
        try:
            while remaining > 0:
                timeout = min(CLOSE_DRAIN_SECONDS, deadline - time.monotonic())
                if timeout <= 0:
                    break
                request.settimeout(timeout)
                chunk = request.recv(min(8192, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            self.close_request(request)


class AdmissionHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'Yanshu'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        # The development API must not write personal queries or credentials to access logs.
        return

    def _send(self, status, body, content_type='application/json; charset=utf-8', extra=None, include_length=True):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        if include_length:
            self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store' if content_type.startswith('application/json') else 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        if status >= 400:
            self.send_header('Connection', 'close')
            self.close_connection = True
        for key, value in (extra or {}).items():
            self.send_header(key, str(value))
        try:
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)
        except ConnectionError:
            self.close_connection = True

    def _json(self, data=None, error=None, status=200):
        status = error.status if error else status
        payload = {'ok': error is None, 'data': None if error else data,
                   'error': {'code': error.code, 'message': error.message} if error else None}
        headers = {'Retry-After': 60} if status == 429 else None
        self._send(status, json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8'), extra=headers)

    def _request_scope(self):
        try:
            host = urlsplit('//' + self.headers.get('Host', ''))
            allowed = {'127.0.0.1', 'localhost', self.server.server_address[0]}
            if host.hostname not in allowed or host.port != self.server.server_address[1] or host.username or host.password or host.path:
                raise ValueError('invalid host')
        except ValueError as error:
            raise ServiceError('invalid_host', '请求主机不在本地服务范围内。', 403) from error
        parsed = urlsplit(self.path)
        limiter = self.server.limiter if parsed.path.startswith('/api/') else self.server.static_limiter
        if not limiter.allow(self.client_address[0]):
            raise ServiceError('rate_limited', '请求较频繁，请稍后再试。', 429)
        if parsed.scheme or parsed.netloc or '\x00' in self.path:
            raise ServiceError('invalid_path', '请求地址不正确。')
        return parsed

    def _origin(self):
        origin = self.headers.get('Origin')
        expected = 'http://' + self.headers.get('Host', '')
        if (origin is not None and origin != expected) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise ServiceError('origin_rejected', '此接口只接受当前站点的请求。', 403)

    def _read_json(self):
        if self.headers.get('Transfer-Encoding'):
            raise ServiceError('invalid_body', '不支持此请求传输方式。')
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise ServiceError('unsupported_media_type', '请使用 JSON 请求。', 415)
        try:
            raw_length = self.headers.get('Content-Length')
            if raw_length is None or len(raw_length) > 20 or not re.fullmatch(r'[0-9]+', raw_length) or len(self.headers.get_all('Content-Length', [])) != 1:
                raise ValueError('missing length')
            length = int(raw_length)
            if length < 0:
                raise ValueError('negative length')
        except ValueError as error:
            raise ServiceError('invalid_body', '请求长度不正确。') from error
        if length > MAX_BODY_BYTES:
            raise ServiceError('body_too_large', '请求内容过大。', 413)
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('incomplete body')
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
            if not isinstance(value, dict):
                raise ValueError('not object')
            return value
        except (ValueError, UnicodeError, socket.timeout) as error:
            raise ServiceError('invalid_json', 'JSON 内容格式不正确。') from error

    @staticmethod
    def _query(parsed, allowed=('q', 'unit_id', 'year'), required=(), allow_empty_year=False):
        values = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=12)
        if set(values) - set(allowed) or not set(required) <= set(values) or any(len(value) != 1 for value in values.values()):
            raise ServiceError('invalid_query', '查询参数重复或不受支持。')
        result = {key: value[0] for key, value in values.items()}
        if allow_empty_year and result.get('year') == '': result.pop('year')
        if 'year' in result:
            try: result['year'] = int(result['year'])
            except ValueError as error: raise ServiceError('unsupported_year', '查询年度格式不正确。') from error
        for key in ('offset', 'limit', 'exam_unit', 'after_event'):
            if key in result:
                if not re.fullmatch(r'[0-9]{1,8}', result[key]):
                    raise ServiceError('invalid_query', '分页参数格式不正确。')
                result[key] = int(result[key])
        if not result.get('unit_id'): result.pop('unit_id', None)
        return result

    def _static(self, parsed):
        decoded = unquote(parsed.path).replace('\\', '/')
        if '\x00' in decoded or ':' in decoded or any(part.startswith('.') for part in decoded.split('/') if part):
            raise ServiceError('invalid_path', '该资源不可访问。', 404)
        root = self.server.static_dir
        path = (root / ('index.html' if decoded == '/' else decoded.lstrip('/'))).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in STATIC_TYPES:
            raise ServiceError('not_found', '未找到请求的页面或资源。', 404)
        try:
            stat = path.stat()
            raw = path.read_bytes()
        except OSError as error: raise ServiceError('not_found', '资源暂不可读取。', 404) from error
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        if self._matches_etag(etag):
            self._send(304, b'', STATIC_TYPES[path.suffix.lower()], extra={'ETag': etag}, include_length=False)
            return
        self._send(200, raw, STATIC_TYPES[path.suffix.lower()], extra={'ETag': etag})

    def _matches_etag(self, etag):
        header = self.headers.get('If-None-Match')
        if not header:
            return False
        candidates = [part.strip() for part in header.split(',')]
        return '*' in candidates or etag in candidates or f'W/{etag}' in candidates

    def _knowledge_get(self, parsed):
        if parsed.path in READ_ACTIONS:
            action, allowed = READ_ACTIONS[parsed.path]
            query = self._query(parsed, allowed, allow_empty_year=True)
            self._json(self.server.service.knowledge_request(action, query))
            return True
        if parsed.path in WRITE_ACTIONS:
            self._query(parsed, ())
            raise ServiceError('method_not_allowed', '此接口需要 POST 请求。', 405)
        return False

    def _get(self, parsed):
        if self._knowledge_get(parsed): return
        path = parsed.path
        if path == '/api/knowledge/retrieve':
            if not self.server.retrieval_limiter.allow(self.client_address[0]):
                raise ServiceError('rate_limited', '知识检索较频繁，请稍后再试。', 429)
            query = self._query(parsed, ('q', 'unit_id', 'year', 'entry_id', 'limit'), ('q',), allow_empty_year=True)
            self._json(self.server.service.knowledge_retrieve(**query, allow_remote=False))
        elif path == '/api/knowledge/vectors':
            self._query(parsed, ())
            self._json(self.server.service.vector_status())
        elif path in {'/api/knowledge/vectors/build', '/api/knowledge/vectors/cancel',
                      '/api/knowledge/embedding/prepare'}:
            self._query(parsed, ())
            raise ServiceError('method_not_allowed', '此接口需要 POST 请求。', 405)
        elif path in {'/api/settings', '/api/settings/providers'}:
            self._query(parsed, ())
            method = self.server.service.settings_public if path == '/api/settings' else self.server.service.settings_providers
            self._json(method())
        elif path == '/api/knowledge':
            query = self._query(parsed, ('entry_id','q','status','year','unit_id','page','page_size','quality'), allow_empty_year=True)
            if 'entry_id' in query and (not query['entry_id'] or len(query)>1):
                raise ServiceError('invalid_query', '请选择一份资料，或使用列表筛选。')
            self._json(self.server.service.knowledge_get(**query))
        elif path == '/api/research':
            query = self._query(parsed, ('research_id', 'after_event', 'limit'))
            if ('research_id' in query and (not query['research_id'] or 'limit' in query)
                    or 'research_id' not in query and 'after_event' in query):
                raise ServiceError('invalid_query', '请选择研究任务，或仅指定列表条数。')
            self._json(self.server.service.research_get(**query))
        elif path.startswith('/api/settings/') or path in {'/api/research/cancel', '/api/research/continue', '/api/research/delete'}:
            self._query(parsed, ())
            known = {'/api/settings/models', '/api/settings/test', '/api/settings/save', '/api/settings/delete',
                     '/api/research/cancel', '/api/research/continue', '/api/research/delete'}
            if path not in known:
                raise ServiceError('not_found', '未找到此接口。', 404)
            raise ServiceError('method_not_allowed', '此接口需要 POST 请求。', 405)
        elif path == '/api/bootstrap': self._json(self.server.service.bootstrap())
        elif path == '/api/catalog-bootstrap':
            self._query(parsed, ())
            self._json(self.server.service.catalog_bootstrap())
        elif path == '/api/catalog-programs': self._json(self.server.service.catalog_programs(**self._query(parsed, ('unit_id','year','q','offset','limit','revision'))))
        elif path == '/api/catalog-subjects': self._json(self.server.service.catalog_subjects(**self._query(parsed, ('unit_id','year','q','offset','limit','revision','code','scope','exam_unit'))))
        elif path == '/api/catalog-program': self._json(self.server.service.catalog_program(**self._query(parsed, ('program_id','revision'), ('program_id',))))
        elif path == '/api/catalog-sources': self._json(self.server.service.catalog_sources(**self._query(parsed, ('unit_id','year','q','kind','offset','limit','revision'))))
        elif path == '/api/catalog-facts': self._json(self.server.service.catalog_facts(**self._query(parsed, ('unit_id','year','q','offset','limit'), ('unit_id',))))
        elif path == '/api/health': self._json(self.server.service.health())
        elif path == '/api/programs': self._json(self.server.service.programs(**self._query(parsed)))
        elif path == '/api/subjects': self._json(self.server.service.subjects(**self._query(parsed)))
        elif path == '/api/collection-status': self._json(self.server.service.collection_status(**self._query(parsed, ('unit_id', 'q', 'status', 'page', 'page_size'))))
        elif path == '/api/radar': self._json(self.server.service.radar_status(**self._query(parsed, ('program_id', 'unit_id', 'year'))))
        elif path == '/api/source-tasks': self._json(self.server.service.source_tasks(**self._query(parsed, ('program_id',), ('program_id',))))
        elif path == '/api/evidence': self._json(self.server.service.evidence(**self._query(parsed, ('program_id','revision'), ('program_id',))))
        elif path == '/api/sources': self._json(self.server.service.sources(**self._query(parsed, ('unit_id', 'year', 'kind', 'q'))))
        elif path in {'/api/match', '/api/history/match', '/api/sensitivity', '/api/search', '/api/chat', '/api/route-plan', '/api/adjustments', '/api/radar/watch', '/api/radar/check', '/api/source-proof'}:
            raise ServiceError('method_not_allowed', '此接口需要 POST 请求。', 405)
        elif path.startswith('/api/'):
            raise ServiceError('not_found', '未找到此接口。', 404)
        else: self._static(parsed)

    def _post(self, parsed):
        self._origin()
        if parsed.path == '/api/knowledge/retrieve':
            self._query(parsed, ())
            if not self.server.retrieval_limiter.allow(self.client_address[0]):
                raise ServiceError('rate_limited', '知识检索较频繁，请稍后再试。', 429)
            values = checked_retrieve(self._read_json(), ServiceError)
            self._json(self.server.service.knowledge_retrieve(**values, allow_remote=True))
            return
        if parsed.path in {'/api/knowledge/vectors/build', '/api/knowledge/vectors/cancel',
                           '/api/knowledge/embedding/prepare'}:
            self._query(parsed, ())
            if not self.server.knowledge_limiter.allow(self.client_address[0]):
                raise ServiceError('rate_limited', '向量索引维护较频繁，请稍后再试。', 429)
            payload = self._read_json()
            if parsed.path == '/api/knowledge/vectors/build':
                value = self.server.service.vector_build(payload)
            elif parsed.path == '/api/knowledge/vectors/cancel':
                checked_empty(payload, ServiceError)
                value = self.server.service.vector_cancel(payload)
            else:
                checked_empty(payload, ServiceError)
                value = self.server.service.embedding_prepare(payload)
            self._json(value, status=202)
            return
        if parsed.path == '/api/knowledge/vectors':
            raise ServiceError('method_not_allowed', '向量状态接口只接受 GET 请求。', 405)
        if parsed.path in WRITE_ACTIONS:
            self._query(parsed, ())
            if not self.server.knowledge_limiter.allow(self.client_address[0]):
                raise ServiceError('rate_limited', '知识维护操作较频繁，请稍后再试。', 429)
            self._json(self.server.service.knowledge_request(WRITE_ACTIONS[parsed.path], self._read_json()))
            return
        if parsed.path in READ_ACTIONS:
            raise ServiceError('method_not_allowed', '知识读取接口只接受读取请求。', 405)
        extension_routes = {'/api/settings/models': 'settings_models', '/api/settings/test': 'settings_test',
                            '/api/settings/save': 'settings_save', '/api/settings/delete': 'settings_delete',
                            '/api/research': 'research_create', '/api/research/cancel': 'research_cancel',
                            '/api/research/continue': 'research_continue',
                            '/api/research/delete': 'research_delete'}
        if parsed.path in extension_routes:
            self._query(parsed, ())
            if (parsed.path in {'/api/settings/models', '/api/settings/test'}
                    and not self.server.settings_limiter.allow(self.client_address[0])):
                raise ServiceError('rate_limited', '连接测试或模型读取较频繁，请稍后再试。', 429)
            if (parsed.path in {'/api/research', '/api/research/continue'}
                    and not self.server.research_limiter.allow(self.client_address[0])):
                raise ServiceError('rate_limited', '研究任务提交较频繁，请稍后再试。', 429)
            value = getattr(self.server.service, extension_routes[parsed.path])(self._read_json())
            self._json(value, status=202 if parsed.path in {'/api/research', '/api/research/continue'} else 200)
            return
        if parsed.path in {'/api/settings', '/api/settings/providers'}:
            self._query(parsed, ())
            raise ServiceError('method_not_allowed', '配置状态接口只接受读取请求。', 405)
        if parsed.path in {'/api/knowledge','/api/evidence', '/api/sources', '/api/catalog-bootstrap', '/api/catalog-programs', '/api/catalog-program', '/api/catalog-subjects','/api/catalog-sources','/api/catalog-facts'}:
            raise ServiceError('method_not_allowed', '来源核验接口只接受读取请求。', 405)
        routes = {'/api/match': self.server.service.match,
                  '/api/history/match': self.server.service.history_match,
                  '/api/sensitivity': self.server.service.sensitivity,
                  '/api/search': self.server.service.search,
                  '/api/route-plan': self.server.service.route_plan,
                  '/api/adjustments': self.server.service.adjustments,
                  '/api/radar/watch': self.server.service.radar_watch,
                  '/api/radar/check': self.server.service.radar_check,
                  '/api/source-proof': self.server.service.source_proof}
        if parsed.path == '/api/chat':
            raise ServiceError('not_configured', '额外 AI 问答尚未接通，规则计算与已有资料仍可使用。', 503)
        if parsed.path not in routes:
            raise ServiceError('not_found', '未找到此接口。', 404)
        if parsed.path in {'/api/search', '/api/source-proof'} and not self.server.search_limiter.allow(self.client_address[0]):
            raise ServiceError('rate_limited', '元宝检索请求较频繁，请稍后再试。', 429)
        self._json(routes[parsed.path](self._read_json()))

    def _dispatch(self):
        try:
            parsed = self._request_scope()
            if self.command in {'GET', 'HEAD'}: self._get(parsed)
            elif self.command == 'POST': self._post(parsed)
            else: raise ServiceError('method_not_allowed', '不支持此请求方法。', 405)
        except ServiceError as error:
            self._json(error=error)
        except (ValueError, UnicodeError):
            self._json(error=ServiceError('invalid_request', '请求格式不正确。'))
        except Exception:
            self._json(error=ServiceError('internal_error', '服务暂时无法完成请求，请稍后重试。', 500))

    do_GET = _dispatch
    do_HEAD = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_PATCH = _dispatch
    do_DELETE = _dispatch
    do_OPTIONS = _dispatch
    do_TRACE = _dispatch


def create_server(host='127.0.0.1', port=4180, service=None, static_dir=None, rate_limit=300):
    if host != 'localhost' and not ipaddress.ip_address(host).is_loopback:
        raise ValueError('This research server only binds to loopback addresses.')
    base = Path(__file__).parent
    server = AdmissionHTTPServer((host, port), AdmissionHandler)
    server.service = service if service is not None else AdmissionService(base / 'data')
    server.static_dir = (Path(static_dir) if static_dir is not None else base / 'static').resolve()
    server.limiter = RateLimiter(limit=rate_limit)
    server.static_limiter = RateLimiter(limit=1200)
    server.search_limiter = RateLimiter(limit=10)
    server.settings_limiter = RateLimiter(limit=12)
    server.research_limiter = RateLimiter(limit=6)
    server.knowledge_limiter = RateLimiter(limit=12)
    server.retrieval_limiter = RateLimiter(limit=20)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description='研序本地研究开发服务')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=4180, type=int)
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).parent / 'data')
    parser.add_argument('--static-dir', type=Path, default=Path(__file__).parent / 'static')
    args = parser.parse_args(argv)
    server = create_server(args.host, args.port, AdmissionService(args.data_dir), args.static_dir)
    server.start_radar()
    print(f'研序本地研究服务：http://{args.host}:{server.server_address[1]}/', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__':
    main()
