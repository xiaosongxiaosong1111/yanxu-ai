"""Bounded, pinned-IP reads of one unit's public HTML and PDF evidence."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import hashlib
from html.parser import HTMLParser
import http.client
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unicodedata
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from collectors.discovery import DiscoveryError, USER_AGENT, _LIMITS, _PinnedConnection, _Response, _url_parts, public_target
from collectors.documents import decode_text


MAX_BYTES = 8 * 1024 * 1024
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='official-source-dns')


class SourceError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class _PublishedCharter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.bodies = []
        self.scripts, self.script_parts = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('id') == 'hid_content' and attrs.get('type', '').lower() == 'hidden':
            self.bodies.append(attrs.get('value', ''))
        if tag == 'script' and not attrs.get('src') and attrs.get('type', '').lower() in {'', 'text/javascript', 'application/javascript'}:
            self.script_parts = []

    def handle_data(self, value):
        if self.script_parts is not None:
            self.script_parts.append(value)

    def handle_endtag(self, tag):
        if tag == 'script' and self.script_parts is not None:
            self.scripts.append(''.join(self.script_parts))
            self.script_parts = None


def _publishes_charter(code, statement):
    tokens = r'''//[^\r\n]*|/\*[\s\S]*?\*/|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`'''
    code = re.sub(tokens, lambda match: ' ' * len(match[0]) if match[0].startswith(('//', '/*')) else match[0], code)
    direct = r'^\s*' + statement + r'\s*;?\s*$'
    ready = r'^\s*\$\(\s*document\s*\)\.ready\(\s*function\(\s*\)\s*\{\s*' + statement
    return bool(re.search(direct, code) or re.search(ready, code))


def rendered_source_html(html, url):
    """Recover the exact body CHSI's published page inserts from its encoded input."""
    try:
        target = urlsplit(url)
    except (TypeError, ValueError):
        return html
    if (not isinstance(html, str) or target.hostname != 'yz.chsi.com.cn'
            or target.path != '/zsgs/zsjz/detail.action'):
        return html
    script = r'''\$\(\s*["']\#cnt_body_wrapper["']\s*\)\.html\(\s*\$\(\s*["']\#hid_content["']\s*\)\.val\(\s*\)\s*\)'''
    wrapper = r'''(<div\b[^>]*\bid=["']cnt_body_wrapper["'][^>]*>)\s*(</div\s*>)'''
    if len(list(re.finditer(wrapper, html, re.I))) != 1:
        return html
    parser = _PublishedCharter()
    parser.feed(html)
    if (len(parser.bodies) != 1 or not parser.bodies[0].strip()
            or not any(_publishes_charter(code, script) for code in parser.scripts)):
        return html
    return re.sub(wrapper, lambda match: match[1] + parser.bodies[0] + match[2], html, count=1, flags=re.I)


class _DeadlineGuard:
    def __init__(self, timeout):
        self.connection, self.sock, self.expired = None, None, False
        self.lock = threading.Lock()
        self.timer = threading.Timer(timeout, self._expire)
        self.timer.daemon = True
        self.timer.start()

    def bind(self, connection, response=None):
        sock = getattr(connection, 'sock', None)
        if sock is None and response is not None:
            stream = getattr(getattr(response, 'response', response), 'fp', None)
            sock = getattr(getattr(stream, 'raw', None), '_sock', None)
        with self.lock:
            self.connection = connection
            self.sock = sock or self.sock
            expired = self.expired
        if expired:
            self._shutdown(connection, self.sock)
            raise SourceError('timeout', '官方原文读取超过总期限。')

    @staticmethod
    def _shutdown(connection, sock=None):
        sock = sock or getattr(connection, 'sock', None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _expire(self):
        with self.lock:
            self.expired = True
            connection, sock = self.connection, self.sock
        self._shutdown(connection, sock)

    def close(self):
        self.timer.cancel()
        self.timer.join()


def _transport(url, target, timeout, guard):
    """Expose the shared pinned connection before waiting for response headers."""
    parts = urlsplit(url)
    host, port, addresses = target
    connection = _PinnedConnection(host, port, addresses[0], timeout, parts.scheme == 'https')
    guard.bind(connection)
    try:
        path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
        connection.request('GET', path, headers={'User-Agent': USER_AGENT,
                            'Accept': 'text/html,application/xhtml+xml,application/pdf'})
        guard.bind(connection)
        return _Response(connection.getresponse(), connection)
    except Exception:
        connection.close()
        raise


def _official_root(unit):
    try:
        _, host, _ = _url_parts(unit.get('official_url'))
    except (DiscoveryError, AttributeError):
        return None
    parts = host.split('.')
    if host in {'edu.cn', 'ac.cn', 'gov.cn', 'org.cn'} or host.endswith('.chsi.com.cn') or host == 'chsi.com.cn':
        return None
    if len(parts) >= 3 and parts[-2:] in [['edu', 'cn'], ['ac', 'cn'], ['gov', 'cn'], ['org', 'cn']]:
        return '.'.join(parts[-3:])
    return host.removeprefix('www.')


def _chsi_school_ids(url):
    parts = urlsplit(url)
    return set(re.findall(r'(?:^|[/?&,;\-])schId[-=](\d+)(?=\D|$)',
                          unquote(parts.path + '?' + parts.query), re.I))


def _chsi_unit_codes(url):
    parts = urlsplit(url)
    return set(re.findall(r'(?:^|[/?&,;\-])schCode[-=]([^&/?;,#]*)',
                          unquote(parts.path + '?' + parts.query), re.I))


def official_source_url(url, unit):
    """Check allowed endpoints; unscoped CHSI content still needs attribution."""
    try:
        _, host, _ = _url_parts(url)
    except DiscoveryError:
        return False
    if host == 'yz.chsi.com.cn':
        school_ids = _chsi_school_ids(url)
        unit_codes = _chsi_unit_codes(url)
        return ((not school_ids or school_ids == {str(unit.get('id'))})
                and (not unit_codes or bool(unit.get('unit_code')) and unit_codes == {str(unit['unit_code'])}))
    root = _official_root(unit)
    return bool(root and root not in {'chsi.com.cn', 'yz.chsi.com.cn'}
        and (host == root or host.endswith('.' + root)))


def document_attribution(url, unit, text='', document_title='', document_heading=''):
    """A shared CHSI host alone cannot establish which school's document this is."""
    if not official_source_url(url, unit):
        return {'status': 'wrong_unit', 'reason': '来源标识不属于所选招生单位。'}
    normalized = lambda value: re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value)))
    name = normalized(unit.get('name') or '')
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), '')
    headings = [normalized(value) for value in (document_heading, document_title, first_line) if value]
    if name and any(name not in heading and re.search(r'大学|研究院|研究所|党校', heading) for heading in headings):
        return {'status': 'wrong_unit', 'reason': '原文主标题明确指向其他招生单位。'}
    if urlsplit(url).hostname.lower().rstrip('.') != 'yz.chsi.com.cn':
        return {'status': 'verified', 'reason': '来源属于所选招生单位的官方域名。'}
    if _chsi_school_ids(url) == {str(unit.get('id'))}:
        return {'status': 'verified', 'reason': '研招网URL的招生单位标识一致。'}
    if unit.get('unit_code') and _chsi_unit_codes(url) == {str(unit['unit_code'])}:
        return {'status': 'verified', 'reason': '研招网URL的招生单位代码一致。'}
    named = name and (any(name in heading for heading in headings)
                     or re.search(r'(?:招生单位|学校名称|院校名称)\s*[:：]\s*' + re.escape(name), text))
    if named:
        return {'status': 'verified', 'reason': '研招网原文主标题或招生单位字段明确标识本校。'}
    return {'status': 'needs_review', 'reason': '研招网页面缺少本校schId及明确学校归属，仅保留未核验来源。'}


class _VisibleHTML(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
    BLOCK = {'p', 'div', 'section', 'article', 'main', 'li', 'tr', 'h1', 'h2', 'h3', 'br', 'table'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.parts, self.main, self.title, self.heading = [], [], [], [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        style = re.sub(r'\s+', '', values.get('style', '').lower())
        hidden = tag in {'script', 'style', 'nav', 'aside', 'footer', 'header', 'noscript', 'template'}
        hidden = hidden or 'hidden' in values or values.get('aria-hidden', '').lower() == 'true'
        hidden = hidden or bool(re.search(r'display:none|visibility:hidden', style))
        hidden = hidden or values.get('role') == 'tooltip'
        hidden = hidden or bool(re.search(r'(?:^|\s)(?:tooltip(?:-[\w-]+)?|popover(?:-[\w-]+)?|hidden|d-none|hide)(?:$|\s)', values.get('class', ''), re.I))
        inherited = bool(self.stack and self.stack[-1][1])
        main = tag in {'main', 'article'} or values.get('role') == 'main' or bool(self.stack and self.stack[-1][2])
        if tag in self.BLOCK:
            self._append('\n', inherited or hidden, main)
        elif tag in {'td', 'th'}:
            self._append('\t', inherited or hidden, main)
        if tag not in self.VOID:
            self.stack.append((tag, inherited or hidden, main))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                _, hidden, main = self.stack[index]
                if tag in self.BLOCK:
                    self._append('\n', hidden, main)
                self.stack = self.stack[:index]
                break

    def _append(self, data, hidden, main):
        if not hidden:
            self.parts.append(data)
            if main:
                self.main.append(data)

    def handle_data(self, data):
        if self.stack and self.stack[-1][0] == 'title':
            self.title.append(data)
        elif not any(item[0] in {'head', 'title'} for item in self.stack):
            self._append(data, bool(self.stack and self.stack[-1][1]), bool(self.stack and self.stack[-1][2]))
            if any(item[0] == 'h1' for item in self.stack) and not (self.stack and self.stack[-1][1]):
                self.heading.append(data)

    def result(self):
        text = ''.join(self.main if self.main else self.parts)
        lines = [re.sub(r'[ \t\r\v]+', ' ', line).strip() for line in text.splitlines()]
        return '\n'.join(line for line in lines if line), re.sub(r'\s+', ' ', ''.join(self.title)).strip()


def _pdf_text(path, timeout):
    executable = shutil.which('pdftotext')
    if not executable:
        raise SourceError('extraction_unavailable', 'PDF文字提取需要pdftotext，原文尚未核验。')
    result = subprocess.run([executable, '-raw', '-enc', 'UTF-8', str(path), '-'],
                            capture_output=True, check=True, timeout=timeout)
    return result.stdout.decode('utf-8')


def _extract(raw, content_type, cache_dir, pdf_extractor, remaining, url=''):
    media_type = content_type.split(';', 1)[0].strip().lower()
    if media_type == 'application/pdf':
        if not raw.startswith(b'%PDF-'):
            raise SourceError('invalid_pdf', '来源声明为PDF，但原始文件不是PDF。')
        if cache_dir is not None:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='source-pdf-', dir=cache_dir) as folder:
            path = Path(folder) / 'source.pdf'
            path.write_bytes(raw)
            text = pdf_extractor(path) if pdf_extractor else _pdf_text(path, remaining())
        pieces = text.split('\f')
        if pieces and not pieces[-1].strip():
            pieces = pieces[:-1]
        pages = [{'page': index, 'text': piece} for index, piece in enumerate(pieces, 1)]
        return text, pages, '', ''
    if media_type not in {'text/html', 'application/xhtml+xml'}:
        raise SourceError('unsupported_type', '只读取官方HTML或PDF原文。')
    parser = _VisibleHTML()
    parser.feed(rendered_source_html(decode_text(raw, content_type), url))
    parser.close()
    text, title = parser.result()
    return text, [{'page': 1, 'text': text}], title, re.sub(r'\s+', ' ', ''.join(parser.heading)).strip()


def fetch_official_document(url, unit, *, timeout=10, max_bytes=MAX_BYTES,
                            resolver=socket.getaddrinfo, transport=_transport,
                            cache_dir=None, pdf_extractor=None):
    """Validate ownership before every hop and connect only to validated IPs."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
        raise SourceError('invalid_limit', '读取超时必须在0至60秒之间。')
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= MAX_BYTES:
        raise SourceError('invalid_limit', '读取上限不得超过8MB。')
    deadline = time.monotonic() + timeout

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise SourceError('timeout', '官方原文读取超时。')
        return value

    current = url
    try:
        for hop in range(4):
            if not official_source_url(current, unit):
                raise SourceError('wrong_unit', '来源或重定向不属于所选招生单位。')
            lookup = _DNS_POOL.submit(public_target, current, resolver)
            try:
                target = lookup.result(timeout=remaining())
            except FutureTimeout as error:
                lookup.cancel()
                raise SourceError('timeout', '官方来源DNS解析超时。') from error
            semaphore = _LIMITS.for_host(target[0])
            if not semaphore.acquire(timeout=remaining()):
                raise SourceError('timeout', '来源站点并发读取等待超时。')
            response, guard = None, None
            try:
                guard = _DeadlineGuard(remaining())
                if transport is _transport:
                    response = transport(current, target, remaining(), guard)
                else:
                    response = transport(current, target, remaining())
                    guard.bind(getattr(response, 'connection', None), response)
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader('Location')
                    if not location or hop == 3:
                        raise SourceError('redirect_error', '原文重定向缺少目标或超过3次。')
                    current = urljoin(current, location)
                    continue
                if response.status in {401, 403, 429}:
                    raise SourceError('restricted', f'官方来源限制访问（HTTP {response.status}）。')
                if response.status < 200 or response.status >= 300:
                    raise SourceError('http_error', f'官方来源读取失败（HTTP {response.status}）。')
                content_type = response.getheader('Content-Type', '')
                length = response.getheader('Content-Length', '')
                if length.isdigit() and int(length) > max_bytes:
                    raise SourceError('too_large', '官方原文超过下载上限。')
                chunks, total = [], 0
                while True:
                    limit = remaining()
                    connection = getattr(response, 'connection', None)
                    if connection is not None and getattr(connection, 'sock', None) is not None:
                        connection.sock.settimeout(limit)
                    chunk = response.read(min(65536, max_bytes + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        raise SourceError('too_large', '官方原文超过下载上限。')
                raw = b''.join(chunks)
            finally:
                try:
                    if guard is not None:
                        guard.close()
                    if response is not None:
                        response.close()
                finally:
                    semaphore.release()
            try:
                text, pages, title, heading = _extract(raw, content_type, cache_dir, pdf_extractor, remaining, current)
            except SourceError:
                raise
            except subprocess.TimeoutExpired as error:
                raise SourceError('timeout', 'PDF文字提取超时。') from error
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                raise SourceError('extraction_error', '原文文字提取失败，不能作为补证依据。') from error
            remaining()
            attribution = document_attribution(current, unit, text, title, heading)
            if attribution['status'] == 'wrong_unit':
                raise SourceError('wrong_unit', attribution['reason'])
            return {'url': current, 'text': text, 'pages': pages, 'raw': raw,
                    'sha256': hashlib.sha256(raw).hexdigest(), 'content_type': content_type,
                    'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    'document_title': title, 'document_heading': heading,
                    'attribution_status': attribution['status'], 'attribution_reason': attribution['reason'],
                    'extraction_status': 'extracted' if text.strip() else 'empty_text'}
    except DiscoveryError as error:
        raise SourceError(error.code, str(error)) from error
    except (TimeoutError, socket.timeout) as error:
        raise SourceError('timeout', '官方原文读取超时。') from error
    except (OSError, http.client.HTTPException) as error:
        if time.monotonic() >= deadline:
            raise SourceError('timeout', '官方原文读取超过总期限。') from error
        raise SourceError('fetch_error', '官方原文暂时无法读取。') from error
