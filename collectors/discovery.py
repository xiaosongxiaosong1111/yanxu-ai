"""Find public admission-source leads; discovery never proves catalog completeness."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_BYTES = 2 * 1024 * 1024
USER_AGENT = 'GradScopeSourceDiscovery/0.1 (public admission source research)'


class DiscoveryError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def checked_at():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _url_parts(url):
    if not isinstance(url, str) or len(url) > 4096 or re.search(r'[\x00-\x20\x7f]', url):
        raise DiscoveryError('unsafe_url', 'URL contains invalid characters')
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').rstrip('.').lower().encode('idna').decode('ascii')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except (ValueError, UnicodeError) as error:
        raise DiscoveryError('unsafe_url', 'Invalid URL authority') from error
    if parts.scheme not in {'http', 'https'} or not host or parts.username is not None or parts.password is not None:
        raise DiscoveryError('unsafe_url', 'Only public HTTP(S) without credentials is accepted')
    if port not in {80, 443} or host == 'localhost' or host.endswith(('.localhost', '.local')) or '%' in host:
        raise DiscoveryError('unsafe_url', 'Host or port is not permitted')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None and not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*', host):
        raise DiscoveryError('unsafe_url', 'Invalid public hostname')
    if address is not None and not address.is_global:
        raise DiscoveryError('unsafe_url', 'Non-public network address is not permitted')
    return parts, host, port


def public_target(url, resolver=socket.getaddrinfo):
    _, host, port = _url_parts(url)
    try:
        addresses = tuple(dict.fromkeys(item[4][0] for item in resolver(host, port, type=socket.SOCK_STREAM)))
    except OSError as error:
        raise DiscoveryError('dns_error', 'Public hostname could not be resolved') from error
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise DiscoveryError('unsafe_url', 'Resolved addresses are not all public')
    return host, port, tuple(sorted(addresses, key=lambda address: ':' in address))


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, timeout, tls):
        super().__init__(host, port=port, timeout=timeout)
        self._address, self._tls = address, tls

    def connect(self):
        self.sock = socket.create_connection((self._address, self.port), self.timeout)
        if self._tls:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


class _Response:
    def __init__(self, response, connection):
        self.response, self.connection = response, connection
        self.status = response.status

    def getheader(self, name, default=None):
        return self.response.getheader(name, default)

    def read(self, limit):
        return self.response.read(limit)

    def close(self):
        self.response.close()
        self.connection.close()


def _transport(url, target, timeout):
    parts = urlsplit(url)
    host, port, addresses = target
    connection = _PinnedConnection(host, port, addresses[0], timeout, parts.scheme == 'https')
    try:
        path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
        connection.request('GET', path, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,application/xhtml+xml'})
        return _Response(connection.getresponse(), connection)
    except Exception:
        connection.close()
        raise


class _HostLimits:
    def __init__(self):
        self.lock = threading.Lock()
        self.semaphores = {}

    def for_host(self, host):
        with self.lock:
            if host not in self.semaphores:
                self.semaphores[host] = threading.BoundedSemaphore(3 if host == 'yz.chsi.com.cn' else 1)
            return self.semaphores[host]


_LIMITS = _HostLimits()


def fetch_public_page(url, *, resolver=socket.getaddrinfo, transport=_transport,
                      timeout=15, retries=2, max_bytes=MAX_BYTES):
    for attempt in range(retries + 1):
        current = url
        try:
            for redirect in range(4):
                target = public_target(current, resolver)
                with _LIMITS.for_host(target[0]):
                    response = transport(current, target, timeout)
                    try:
                        status = response.status
                        if status in {301, 302, 303, 307, 308}:
                            location = response.getheader('Location')
                            if not location or redirect == 3:
                                raise DiscoveryError('redirect_error', 'Redirect limit or missing location')
                            current = urljoin(current, location)
                            continue
                        if status in {401, 403, 429}:
                            raise DiscoveryError('restricted', f'HTTP {status}')
                        if status >= 400:
                            raise DiscoveryError('http_error', f'HTTP {status}')
                        content_type = response.getheader('Content-Type', '')
                        if not any(kind in content_type.lower() for kind in ('text/html', 'application/xhtml+xml')):
                            raise DiscoveryError('not_html', 'Non-HTML document recorded as a link only')
                        length = response.getheader('Content-Length')
                        if length and length.isdigit() and int(length) > max_bytes:
                            raise DiscoveryError('too_large', 'HTML exceeds byte limit')
                        body = response.read(max_bytes + 1)
                        if len(body) > max_bytes:
                            raise DiscoveryError('too_large', 'HTML exceeds byte limit')
                        charset = re.search(r'charset\s*=\s*["\']?([\w-]+)', content_type, re.I)
                        if charset is None:
                            charset = re.search(r'charset\s*=\s*["\']?([\w-]+)', body[:4096].decode('ascii', errors='ignore'), re.I)
                        encoding = charset.group(1) if charset else 'utf-8'
                        try:
                            text = body.decode(encoding, errors='replace')
                        except LookupError:
                            text = body.decode('utf-8', errors='replace')
                        return {'url': current, 'text': text, 'sha256': hashlib.sha256(body).hexdigest(),
                                'content_type': content_type, 'raw': body}
                    finally:
                        if hasattr(response, 'close'):
                            response.close()
                        time.sleep(0.12)
        except DiscoveryError as error:
            if error.code not in {'dns_error', 'http_error'} or attempt == retries:
                raise
        except (OSError, http.client.HTTPException) as error:
            if attempt == retries:
                raise DiscoveryError('fetch_error', type(error).__name__) from error
        time.sleep(0.3 * (attempt + 1))
    raise DiscoveryError('fetch_error', 'Page could not be fetched')


def classify_title(title, year):
    label = re.sub(r'\s+', '', title)
    years = re.findall(r'20\d{2}', label)
    document_year = int(years[0]) if years else None
    if document_year is not None and not year - 3 <= document_year <= year + 1:
        return None
    if '本科招生' in label and '硕士' not in label:
        return None
    if '博士' in label and '硕士' not in label and '硕博' not in label:
        return None
    for kind, words in [
        ('contact', ('联系办法', '联系方式', '招生部门联系')),
        ('intro', ('院校简介', '学校简介')),
        ('transfer', ('调剂',)), ('retest', ('复试',)),
        ('subjects', ('初试科目', '考试科目', '自命题', '考试大纲', '参考书')),
        ('catalog', ('招生专业目录', '招生目录', '专业目录', '硕士目录')),
        ('charter', ('招生简章', '招生章程')),
        ('admissions_home', ('研究生招生网', '研究生院', '研招网', '招生网址', '招生网站', '招生信息')),
    ]:
        if any(word in label for word in words):
            return kind, document_year
    return None


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.current, self.parts, self.text_parts = [], None, [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'a' and values.get('href'):
            self.current, self.parts = values, []

    def handle_data(self, data):
        self.text_parts.append(data)
        if self.current is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in {'p', 'div', 'li', 'br'}:
            self.text_parts.append('\n')
        if tag == 'a' and self.current is not None:
            label = re.sub(r'\s+', ' ', ''.join(self.parts)).strip() or self.current.get('title', '')
            self.links.append((self.current['href'], label))
            self.current, self.parts = None, []


def extract_links(html, source_url, unit_id, year):
    parser = _Links()
    parser.feed(html)
    raw_links = list(parser.links)
    website = r'(?:招生(?:网站|网址)|研究生(?:招生)?(?:网站|网址)|官方(?:网站|网址)|学校(?:网址|官网))\s*[:：]?\s*(https?://[^\s<>"\']+)'
    raw_links += [(match.group(1).rstrip('，。；）)'), '招生网址') for match in re.finditer(website, ''.join(parser.text_parts))]
    found = {}
    for href, label in raw_links:
        category = classify_title(label, year)
        if not category:
            continue
        url = urljoin(source_url, href)
        try:
            parts, host, _ = _url_parts(url)
        except DiscoveryError:
            continue
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))
        same_unit = host == 'yz.chsi.com.cn' and re.search(rf'schId[-=]{re.escape(str(unit_id))}(?:[^0-9]|$)', url)
        external = host not in {'yz.chsi.com.cn', 'www.chsi.com.cn', 'account.chsi.com.cn'} and not host.endswith('.chei.com.cn')
        if not same_unit and not external:
            continue
        kind, document_year = category
        found.setdefault(url, {'url': url, 'title': label[:500], 'kind': kind, 'year': document_year,
                               'host': host, 'source_url': source_url, 'status': 'needs_review',
                               'attribution': 'linked_from_unit_profile' if urlsplit(source_url).hostname == 'yz.chsi.com.cn' else 'linked_from_source_lead',
                               'verified': False})
    return list(found.values())


def discover_unit(unit, year, snapshot_dir, fetcher=fetch_public_page):
    unit_id = str(unit['id'])
    if not re.fullmatch(r'[0-9]{1,15}', unit_id):
        raise DiscoveryError('invalid_unit', 'Expected numeric CHSI school ID')
    queue = [unit['source_url']]
    if unit.get('official_url'):
        queue.append(unit['official_url'])
    visited, pages, candidates = set(), [], {}
    while queue and len(visited) < 4:
        url, *queue = queue
        if url in visited:
            continue
        visited = visited | {url}
        try:
            page = fetcher(url)
            title = re.search(r'<title[^>]*>(.*?)</title>', page['text'], re.I | re.S)
            if (urlsplit(page['url']).hostname == 'account.chsi.com.cn'
                    or title and re.search(r'登录|验证码|安全验证|访问过于频繁', title.group(1))):
                raise DiscoveryError('restricted', 'Login or access verification is required')
            folder = Path(snapshot_dir)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f'{unit_id}-{page["sha256"][:18]}.html'
            path.write_bytes(page.get('raw') or page['text'].encode('utf-8'))
            pages = [*pages, {'url': page['url'], 'status': 'fetched', 'sha256': page['sha256'],
                              'snapshot_path': str(path), 'checked_at': checked_at()}]
            links = extract_links(page['text'], page['url'], unit_id, year)
            candidates = {**candidates, **{link['url']: link for link in links}}
            child_pages = [link['url'] for link in links if link['host'] == 'yz.chsi.com.cn'
                           and link['kind'] in {'contact', 'intro', 'catalog', 'charter'}
                           and not re.search(r'\.(?:pdf|docx?|xlsx?)(?:\?|$)', link['url'], re.I)]
            declared_homes = [link['url'] for link in links if link['kind'] == 'admissions_home'
                              and link['host'] != 'yz.chsi.com.cn']
            queue = list(dict.fromkeys([*queue, *child_pages[:3], *declared_homes[:1]]))
        except DiscoveryError as error:
            pages = [*pages, {'url': url, 'status': error.code, 'error': str(error), 'checked_at': checked_at()}]
    useful = [link for link in candidates.values() if link['kind'] not in {'contact', 'intro'}]
    fetched = any(page['status'] == 'fetched' for page in pages)
    status = 'discovered' if useful else 'not_found' if fetched else 'restricted' if any(page['status'] == 'restricted' for page in pages) else 'failed'
    return {'unit_id': unit_id, 'unit_name': unit['name'], 'year': year, 'status': status,
            'checked_at': checked_at(), 'pages': pages, 'candidates': list(candidates.values()),
            'catalog_complete': False,
            'notes': ['来源线索尚需校方归属、适用年度和目录内容核验；发现完成不代表专业目录完整。']}


def summarize_inventory(rows, year):
    attempted = sum(row['status'] != 'pending' for row in rows)
    pages = [page for row in rows for page in row.get('pages', [])]
    candidates = [candidate for row in rows for candidate in row.get('candidates', [])
                  if candidate.get('kind') not in {'contact', 'intro'}]
    return {'year': year, 'total_units': len(rows), 'attempted_units': attempted,
            'pending_units': len(rows) - attempted,
            'failed_units': sum(row['status'] == 'failed' for row in rows),
            'restricted_units': sum(row['status'] == 'restricted' for row in rows),
            'units_with_leads': sum(row['status'] == 'discovered' for row in rows),
            'candidate_count': len({candidate['url'] for candidate in candidates}),
            'external_candidate_count': len({candidate['url'] for candidate in candidates if candidate.get('host') != 'yz.chsi.com.cn'}),
            'current_year_document_candidates': len({candidate['url'] for candidate in candidates
                if candidate.get('year') == year and candidate.get('kind') in {'catalog', 'charter', 'subjects', 'transfer', 'retest'}}),
            'fetched_pages': sum(page.get('status') == 'fetched' for page in pages),
            'restricted_pages': sum(page.get('status') == 'restricted' for page in pages),
            'failed_pages': sum(page.get('status') not in {'fetched', 'restricted'} for page in pages),
            'attempt_complete': attempted == len(rows), 'catalog_complete': False,
            'checked_at': checked_at(),
            'notes': ['仅统计已尝试的公开来源发现。未发现不等于不存在；所有线索仍需人工或专用解析器核验。']}


def _save_inventory(output, rows, year):
    data = {'metadata': summarize_inventory(rows, year), 'units': rows}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, output)
    return data


def collect_inventory(units, year, output, snapshot_dir, *, limit=None, workers=4,
                      refresh=False, retry_failed=False, discoverer=discover_unit):
    existing = {}
    if Path(output).is_file() and not refresh:
        try:
            saved = json.loads(Path(output).read_text(encoding='utf-8'))
            existing = {row['unit_id']: row for row in saved.get('units', []) if row.get('year') == year}
        except (OSError, ValueError, TypeError, KeyError):
            existing = {}
    rows = [existing.get(str(unit['id']), {'unit_id': str(unit['id']), 'unit_name': unit['name'],
                                         'year': year, 'status': 'pending', 'pages': [], 'candidates': [], 'catalog_complete': False}) for unit in units]
    pending = [unit for unit, row in zip(units, rows) if row['status'] == 'pending'
               or retry_failed and row['status'] in {'failed', 'restricted'}]
    pending = pending if limit is None else pending[:max(0, limit)]
    _save_inventory(output, rows, year)
    with ThreadPoolExecutor(max_workers=max(1, min(4, workers))) as executor:
        futures = {executor.submit(discoverer, unit, year, snapshot_dir): unit for unit in pending}
        for future in as_completed(futures):
            unit = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {'unit_id': str(unit['id']), 'unit_name': unit['name'], 'year': year,
                          'status': 'failed', 'checked_at': checked_at(), 'pages': [], 'candidates': [],
                          'catalog_complete': False, 'error': type(error).__name__}
            rows = [result if row['unit_id'] == result['unit_id'] else row for row in rows]
            count = sum(row['status'] != 'pending' for row in rows)
            if count % 10 == 0 or count % 25 == 0 or count == len(units):
                _save_inventory(output, rows, year)
            if count % 25 == 0 or count == len(units):
                print(json.dumps(summarize_inventory(rows, year), ensure_ascii=True), flush=True)
    return _save_inventory(output, rows, year)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--units', default='data/units.json')
    parser.add_argument('--year', type=int, default=2027)
    parser.add_argument('--output', default='data/source_inventory.json')
    parser.add_argument('--snapshot-dir', default='data/snapshots/discovery')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args(argv)
    if not 2000 <= args.year <= 2100:
        parser.error('year must be between 2000 and 2100')
    source = json.loads(Path(args.units).read_text(encoding='utf-8'))
    result = collect_inventory(source['units'], args.year, Path(args.output), Path(args.snapshot_dir),
                               limit=args.limit, workers=args.workers, refresh=args.refresh, retry_failed=args.retry_failed)
    print(json.dumps(result['metadata'], ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
