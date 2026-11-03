"""Resumable, unit-scoped official catalog originals; crawling is not verification."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from datetime import datetime, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit

from public_sources import (SourceError, _DeadlineGuard, _DNS_POOL, _transport,
                            official_source_url, document_attribution, rendered_source_html, public_target, _LIMITS)
from .documents import PROJECT_ROOT, decode_text
from .discovery import DiscoveryError


OFFICE = re.compile(r'\.(?:pdf|docx?|xlsx?)(?:[?#]|$)', re.I)
CATALOG = re.compile(r'专业目录|招生目录|招生专业|专业及(?:研究)?方向|研究方向|初试科目|考试科目')
HOME = re.compile(r'研究生招生|研究生院|研究生处|招生信息|硕士招生|招生工作|硕士研究生|研究生教育|学院目录|院系目录|下一页|下页')
EXCLUDE = re.compile(r'调剂|复试|拟录取|录取名单|报考点|考点|推免|夏令营|博士后|本科招生|奖学金|助学金')
MAX_BYTES = 32 * 1024 * 1024
OFFICE_MEDIA = {'application/msword': '.doc', 'application/vnd.ms-excel': '.xls',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx'}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def normalized_url(value, base=''):
    try:
        parts = urlsplit(urljoin(base, unescape(str(value)).strip()))
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            return None
        if parts.port not in {None, 80, 443}:
            return None
        path = quote(unquote(parts.path or '/'), safe='/%:@!$&\'()*+,;=-._~')
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ''))
    except (ValueError, UnicodeError):
        return None


def source_allowed(url, unit):
    if not official_source_url(url, unit):
        return False
    parts = urlsplit(url)
    codes = parse_qs(parts.query).get('schCode', [])
    return not codes or bool(unit.get('unit_code')) and set(codes) == {str(unit['unit_code'])}


class Links(HTMLParser):
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.parts, self.current, self.label, self.stack = [], [], None, [], []
        self.base_href = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        style = re.sub(r'\s+', '', values.get('style', '').lower())
        hidden = (bool(self.stack and self.stack[-1][1]) or tag in {'script','style','noscript','template'}
            or 'hidden' in values or values.get('aria-hidden','').lower() == 'true'
            or bool(re.search(r'display:none|visibility:hidden', style)) or values.get('role') == 'tooltip'
            or bool(re.search(r'(?:^|\s)(?:tooltip(?:-[\w-]+)?|popover(?:-[\w-]+)?|hidden|d-none|hide)(?:$|\s)',values.get('class',''),re.I)))
        if tag not in self.VOID:
            self.stack.append((tag, hidden))
        if tag == 'base' and not hidden and 'href' in values and self.base_href is None:
            self.base_href = values['href'] or ''
        if not hidden and tag == 'a' and values.get('href'):
            self.current, self.label = values, []

    def handle_data(self, data):
        if not (self.stack and self.stack[-1][1]):
            self.parts.append(data)
            if self.current:
                self.label.append(data)

    def handle_endtag(self, tag):
        hidden = bool(self.stack and self.stack[-1][1])
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                hidden = self.stack[index][1]
                self.stack = self.stack[:index]
                break
        if not hidden and tag == 'a' and self.current:
            label = re.sub(r'\s+', ' ', ''.join(self.label)).strip() or self.current.get('title', '')
            self.links.append((self.current['href'], label))
            self.current, self.label = None, []
        if not hidden and tag in {'p', 'div', 'li', 'tr', 'br', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)


def classify_link(label, url, year, *, parent_kind=None, parent_year=None):
    label = re.sub(r'\s+', '', label or '')
    if EXCLUDE.search(label) or ('博士' in label and '硕士' not in label and '硕博' not in label):
        return None
    years = {int(value) for value in re.findall(r'(?<!\d)(20\d{2})(?:年|级|[^\d]|$)', label)}
    if years and year not in years:
        return None
    explicit = year in years
    inherited = parent_year == year and parent_kind in {'catalog', 'charter', 'subjects', 'attachment'}
    attachment = bool(OFFICE.search(url))
    if CATALOG.search(label) or attachment and inherited and '目录' in label:
        kind, priority = ('subjects', 2) if re.search(r'考试科目|初试科目', label) else ('catalog', 0)
    elif re.search(r'招生章程|招生简章', label):
        kind, priority = 'charter', 5
    elif attachment and inherited:
        kind, priority = 'attachment', 3
    elif HOME.search(label):
        kind, priority = 'navigation', 20
    elif attachment:
        return None
    else:
        return None
    return {'kind': kind, 'priority': priority + (0 if explicit else 1 if inherited else 10),
            'year': year if explicit or inherited else None,
            'year_status': 'explicit_link' if explicit else 'linked_publication' if inherited else 'unknown'}


def _html_info(html, unit, year, url=''):
    html = rendered_source_html(html, url)
    headings = re.findall(r'<[^>]+class=["\'][^"\']*(?:sch-sspe-swtit|article-title|yxk-big-title)[^"\']*["\'][^>]*>(.*?)</[^>]+>', html, re.I | re.S)
    headings += re.findall(r'<h[1-3]\b[^>]*>(.*?)</h[1-3]>', html, re.I | re.S)
    headings += re.findall(r'<title\b[^>]*>(.*?)</title>', html, re.I | re.S)
    clean = lambda value: re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', '', value))).strip()
    title = next((clean(value) for value in headings if re.search(r'硕士|招生|目录', clean(value))), '')
    years = set(re.findall(r'(?<!\d)(20\d{2})(?:年|级)', title))
    parser = Links()
    parser.feed(html)
    text = ''.join(parser.parts)
    if not years:
        body = re.sub(r'<(nav|footer|header|a|script|style)\b[^>]*>.*?</\1>', '', html, flags=re.I | re.S)
        visible = Links()
        visible.feed(body)
        match = re.search(re.escape(unit.get('name', '')) + r'\s*' + str(year) + r'年[^。\n]{0,45}(?:硕士|招生)', ''.join(visible.parts))
        if match:
            return title or match[0], year, match[0], parser
    return title, year if years == {str(year)} else None, title if years == {str(year)} else '', parser


def binding_from_html(unit, document, html, *, raw=None):
    original = decode_text(raw, document.get('content_type', '')) if raw is not None else html
    if (urlsplit(document.get('url', '')).hostname != 'yz.chsi.com.cn'
            or not source_allowed(document['url'], unit)
            or hashlib.sha256(raw if raw is not None else html.encode('utf-8')).hexdigest() != document.get('sha256')
            or raw is not None and html not in {original, rendered_source_html(original, document['url'])}):
        return []
    html = rendered_source_html(original, document['url'])
    parser = Links()
    parser.feed(html)
    title, _, _, _ = _html_info(html, unit, document.get('year') or 2027, document['url'])
    if document_attribution(document['url'], unit, ''.join(parser.parts),
                            document_heading=title)['status'] != 'verified':
        return []
    roles = r'官网|官方网站|学校主页|学校官网|院校主页|研究生招生网|研究生院|招生网址|招生网站|研究生处|招生信息网'
    links = [(href, label) for href, label in parser.links
             if re.fullmatch(r'(?:' + re.escape(unit.get('name', '')) + r')?(?:' + roles + r')', re.sub(r'\s+', '', label))]
    own_name = re.split(r'[（(]', unit.get('name', ''))[0]
    links += [(href, label) for href, label in parser.links
              if own_name and own_name in re.sub(r'\s+', '', label) and CATALOG.search(label)]
    text = ''.join(parser.parts)
    statement = r'(?:招生(?:网站|网址)|研究生(?:招生)?(?:网站|网址)|官方(?:网站|网址)|学校(?:网址|官网))\s*[:：]?\s*(https?://[^\s<>"\'，。；：（）\u4e00-\u9fff]+)'
    links += [(match[1].rstrip('，。；）)'), '正文声明招生网址') for match in re.finditer(statement, text)]
    url_pattern = r'https?://[^\s<>"\'，。；：（）\u4e00-\u9fff]+'
    role = r'(?:研究生(?:院|处|部)(?:招生)?(?:信息)?(?:网站|官网|主页|网)?|研究生招生(?:信息)?网|研招网|招生信息网)'
    for match in re.finditer(url_pattern, text):
        prefix = re.split(r'[\n。；;]', text[max(0, match.start()-100):match.start()])[-1].strip()
        if not re.search(role + r'[^\n。；;]{0,30}$', prefix):
            continue
        if not (re.search(r'我校|我院|我所|本校|本院|本所|学校', prefix)
                or own_name and own_name in prefix or re.match(role, prefix)):
            continue
        peers = re.findall(r'[\u4e00-\u9fff（()）]{2,45}(?:大学|学院|研究院|研究所|党校)', prefix)
        if any(own_name not in peer for peer in peers) or re.search(r'其他|借用|参照其他', prefix):
            continue
        links.append((match[0].rstrip('，。；）)'), '本单位研究生招生网站声明'))
    result = {}
    for href, label in links:
        url = normalized_url(href, document['url'])
        if not url:
            continue
        host = urlsplit(url).hostname.lower()
        if not host.endswith(('.edu.cn', '.ac.cn', '.gov.cn', '.org.cn')) or host in {'edu.cn', 'ac.cn', 'gov.cn', 'org.cn'}:
            continue
        # Link text alone is insufficient: retain the literal outgoing target from this saved unit page.
        if href not in unescape(html) and href not in text:
            continue
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, '/', '', ''))
        result[origin] = {'url': origin, 'linked_url': url, 'host': host, 'status': 'linked_official_domain',
            'evidence': {'url': document['url'], 'sha256': document['sha256'],
                'local_path': document.get('local_path'), 'quote': href, 'link_label': label,
                'relation': 'official_home_link'}}
    return list(result.values())


def _restricted_html(text):
    title = re.search(r'<title\b[^>]*>(.*?)</title>', text, re.I | re.S)
    title = re.sub(r'<[^>]+>', '', title[1]) if title else ''
    return bool(re.search(r'登录|验证码|安全验证|访问过于频繁', title)
        or '附件下载' in title and (re.search(r'<form\b', text, re.I)
            or re.search(r'<div\b[^>]*\bclass\s*=\s*["\'][^"\']*\bcode_form\b', text, re.I))
        and re.search(r'<input\b[^>]*\bname\s*=\s*["\']?codeValue\b', text, re.I)
        and re.search(r'createimage\.jsp|请输入验证码', text, re.I))


def _binary_suffix(raw, url, content_type):
    media = content_type.split(';')[0].lower().strip()
    suffix = Path(urlsplit(url).path).suffix.lower()
    suffix = OFFICE_MEDIA.get(media, suffix)
    if raw.startswith(b'%PDF-') and (suffix == '.pdf' or media == 'application/pdf'):
        return '.pdf'
    if raw.startswith(b'PK\x03\x04') and suffix in {'.docx', '.xlsx'}:
        return suffix
    if raw.startswith(b'\xd0\xcf\x11\xe0') and suffix in {'.doc', '.xls'}:
        return suffix
    return None


def fetch_source(url, unit, *, timeout=15, max_bytes=MAX_BYTES, resolver=None, transport=None):
    """Use the existing pinned-IP, ownership and hard-deadline transport for all originals."""
    import socket
    resolver, transport = resolver or socket.getaddrinfo, transport or _transport
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES or not 0 < timeout <= 60:
        raise SourceError('invalid_limit', '下载边界不合法')
    deadline, current = time.monotonic() + timeout, url
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise SourceError('timeout', '官方来源超过总读取期限')
        return value
    for hop in range(4):
        if not source_allowed(current, unit):
            raise SourceError('wrong_unit', '原文或重定向不属于该招生单位')
        lookup = _DNS_POOL.submit(public_target, current, resolver)
        try:
            target = lookup.result(timeout=remaining())
        except FutureTimeout as error:
            lookup.cancel()
            raise SourceError('timeout', '官网DNS解析超时') from error
        semaphore = _LIMITS.for_host(target[0])
        if not semaphore.acquire(timeout=remaining()):
            raise SourceError('timeout', '官网并发等待超时')
        response, guard = None, None
        try:
            guard = _DeadlineGuard(remaining())
            if transport is _transport:
                response = transport(current, target, remaining(), guard)
            else:
                response = transport(current, target, remaining())
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                if not location or hop == 3:
                    raise SourceError('redirect_error', '官方来源重定向不完整')
                current = normalized_url(location, current)
                continue
            if response.status in {401, 403, 429}:
                raise SourceError('restricted', f'官方来源限制访问（HTTP {response.status}）')
            if not 200 <= response.status < 300:
                raise SourceError('http_error', f'官方来源返回HTTP {response.status}')
            content_type = response.getheader('Content-Type', '')
            length = response.getheader('Content-Length', '')
            if length.isdigit() and int(length) > max_bytes:
                raise SourceError('too_large', '原文超过32MB读取上限')
            chunks, total = [], 0
            while True:
                remaining()
                chunk = response.read(min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise SourceError('too_large', '原文超过32MB读取上限')
            raw = b''.join(chunks)
            remaining()
            if guard.expired:
                raise SourceError('timeout', '原文在期限内未完整读取')
            if not raw or length.isdigit() and total != int(length):
                raise SourceError('incomplete_response', '官方原文为空或长度与响应声明不一致')
        finally:
            if guard:
                guard.close()
            if response:
                response.close()
            semaphore.release()
        media = content_type.split(';')[0].lower().strip()
        is_html = media in {'text/html', 'application/xhtml+xml'}
        valid_binary = _binary_suffix(raw, current, content_type)
        if not is_html and not valid_binary:
            raise SourceError('unsupported_type', '原文类型或文件签名无法确认，未采用')
        text = decode_text(raw, content_type) if is_html else ''
        if is_html and _restricted_html(text):
            raise SourceError('restricted', '官方来源要求登录或访问验证')
        return {'url': current, 'raw': raw, 'text': text, 'sha256': hashlib.sha256(raw).hexdigest(),
                'content_type': content_type, 'checked_at': now()}
    raise SourceError('redirect_error', '官方来源重定向超过上限')


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_suffix(path.suffix + '.tmp')
    with stage.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    stage.replace(path)


def _stored(page, item, directory, root, unit, year):
    raw = page['raw']
    suffix = _binary_suffix(raw, page['url'], page.get('content_type', '')) or '.html'
    key = hashlib.sha256(page['url'].encode()).hexdigest()[:16]
    digest = hashlib.sha256(raw).hexdigest()
    path = directory / f'{key}-{digest[:16]}{suffix}'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    local = path.resolve().relative_to(Path(root).resolve()).as_posix()
    return {'id': f'national-{unit["id"]}-{year}-{key}-{digest[:12]}', 'unit_id': str(unit['id']),
        'url': page['url'], 'requested_url': item['url'], 'local_path': local, 'sha256': digest,
        'content_type': page.get('content_type', ''), 'status': 'fetched', 'kind': item['kind'],
        'title': item.get('title', ''), 'year': item.get('year'), 'year_status': item.get('year_status', 'unknown'),
        'year_evidence': item.get('year_evidence'), 'source_url': item.get('source_url'),
        'depth': item.get('depth', 0), 'checked_at': page.get('checked_at') or now()}


def _effective_unit(unit, bindings, url):
    if source_allowed(url, unit):
        return unit
    for binding in bindings:
        bound = {**unit, 'official_url': binding['url']}
        if source_allowed(url, bound):
            return bound
    return None


def _seeds(unit, inventory, year):
    result = [{'url': unit['source_url'], 'title': unit['name'], 'kind': 'profile', 'priority': 50, 'depth': 0}]
    if unit.get('unit_code'):
        result.append({'url': 'https://yz.chsi.com.cn/zsgs/zsjz/detailall.action?schCode=' + str(unit['unit_code']),
                       'title': unit['name'] + '招生章程列表', 'kind': 'navigation', 'priority': -5, 'depth': 0})
    if unit.get('official_url'):
        result.append({'url': unit['official_url'], 'kind': 'navigation', 'title': '已核对官网入口', 'priority': 15, 'depth': 0})
        proof = unit.get('official_url_evidence') or {}
        declared = proof.get('linked_url') or proof.get('quote')
        deep = normalized_url(declared) if isinstance(declared, str) and declared.startswith(('http://','https://')) else None
        if deep and deep != unit['official_url'] and source_allowed(deep, unit):
            result.append({'url': deep, 'kind': 'navigation', 'title': '已留档的官方招生入口',
                           'priority': 0, 'depth': 0, 'source_url': proof.get('url')})
    for candidate in inventory.get('candidates', []):
        classified = classify_link(candidate.get('title', ''), candidate['url'], year)
        if classified:
            result.append({**classified, 'url': candidate['url'], 'title': candidate.get('title', ''),
                           'source_url': candidate.get('source_url'), 'depth': 0})
        elif candidate.get('kind') == 'contact':
            result.append({'url': candidate['url'], 'kind': 'profile', 'title': '官网联系入口', 'priority': 40, 'depth': 0})
    return result


def _cached_sources(inventory, root):
    result = {}
    for doc in inventory.get('pages', []):
        value = doc.get('snapshot_path')
        if doc.get('status') != 'fetched' or not isinstance(value, str):
            continue
        path = Path(value)
        path = path if path.is_absolute() else Path(root) / path
        try:
            if not path.resolve().is_relative_to(Path(root).resolve()):
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() == doc.get('sha256'):
                result[doc['url']] = {'url': doc['url'], 'raw': raw, 'text': decode_text(raw),
                    'sha256': doc['sha256'], 'content_type': 'text/html', 'checked_at': doc.get('checked_at')}
        except (OSError, ValueError):
            continue
    return result


def _owned_chsi(item, unit):
    url = item['url']
    if urlsplit(url).hostname != 'yz.chsi.com.cn':
        return True
    scope = re.search(r'(?:schId[-=]' + re.escape(str(unit['id'])) + r'(?=\D|$))', url)
    code = parse_qs(urlsplit(url).query).get('schCode')
    if scope or code and set(code) == {str(unit.get('unit_code'))}:
        return True
    parent = item.get('source_url')
    return bool(OFFICE.search(url) and parent and _owned_chsi({'url': parent}, unit)
                and urlsplit(parent).hostname == 'yz.chsi.com.cn')


def _page_links(document, parser, year, unit):
    result = []
    base = normalized_url(parser.base_href, document['url']) if parser.base_href is not None else document['url']
    for href, label in parser.links:
        url = normalized_url(href, base or document['url'])
        classified = classify_link(label, url or '', year, parent_kind=document['kind'], parent_year=document.get('year'))
        if url and classified:
            candidate = {**classified, 'url': url, 'title': label, 'depth': document.get('depth', 0) + 1,
                'source_url': document['url'], 'year_evidence': document.get('year_evidence')}
            if _owned_chsi(candidate, unit):
                result.append(candidate)
    return result


def _replayed_links(document, parser, year, unit, pages, previous_links):
    candidates = _page_links(document, parser, year, unit)
    parent = next((page for page in reversed(pages) if page['url'] == document.get('source_url')
                   and page.get('sha256') == document['sha256']), None)
    if parent is None:
        return document, candidates, []
    current, original = urlsplit(document['url']), urlsplit(parent['url'])
    child_dir, parent_dir = current.path.rsplit('/', 1)[0] + '/', original.path.rsplit('/', 1)[0] + '/'
    if ((current.scheme, current.netloc) != (original.scheme, original.netloc)
            or child_dir == parent_dir or not child_dir.startswith(parent_dir)):
        return document, candidates, []
    canonical = parent.get('crawl_alias_of', parent['url'])
    same_links = {item['url'] for item in candidates} == previous_links.get(canonical, set())
    document = {**document, 'crawl_alias_of': canonical,
                'crawl_expansion_status': 'duplicate_same_links' if same_links else 'duplicate_path_growth'}
    # Preserve unfamiliar relative targets for review rather than guessing that their paths are invalid.
    deferred = [] if same_links else [{**item,
        'reason': '同源父子页面原文字节相同且目录路径增长，重复展开已暂停，链接保留待核对。'} for item in candidates]
    return document, [], deferred


def _valid_resume(state, root, unit):
    pages, retry, bindings, replayed, deferred = [], [], [], {}, []
    for document in state.get('pages', []):
        if not _owned_chsi(document, unit):
            continue
        try:
            path = (Path(root)/document['local_path']).resolve()
            raw = path.read_bytes() if path.is_relative_to(Path(root).resolve()) else b''
            if not raw or hashlib.sha256(raw).hexdigest() != document.get('sha256'):
                raise ValueError('原文缺失或摘要不一致')
            if document.get('content_type', '').startswith('text/html'):
                html = decode_text(raw, document.get('content_type', ''))
                if _restricted_html(html):
                    raise ValueError('附件要求验证码，不能作为已获取原文')
                title, source_year, year_quote, parser = _html_info(html, unit, state['year'], document['url'])
                if source_year == state['year']:
                    document = {**document, 'year': source_year, 'year_status': 'explicit_original',
                        'year_evidence': {'url': document['url'], 'sha256': document['sha256'],
                            'local_path': document['local_path'], 'quote': year_quote}}
                elif document.get('year_status') == 'explicit_original':
                    document = {**document, 'year': None, 'year_status': 'unknown', 'year_evidence': None}
                kind = classify_link(title, document['url'], state['year'])
                if kind and kind['kind'] in {'catalog', 'charter', 'subjects'}:
                    document = {**document, 'kind': kind['kind'], 'title': title}
                document = {key: value for key, value in document.items()
                            if key not in {'crawl_alias_of', 'crawl_expansion_status'}}
                document, links, held = _replayed_links(document, parser, state['year'], unit, pages, replayed)
                replayed[document['url']] = {item['url'] for item in links}
                retry.extend(links)
                deferred.extend(held)
                if urlsplit(document['url']).hostname == 'yz.chsi.com.cn':
                    bindings.extend(binding_from_html(unit, document, html, raw=raw))
            pages.append(document)
        except (OSError, ValueError, KeyError):
            retry.append({'url': document.get('requested_url', document.get('url')),
                'title': document.get('title', ''), 'kind': document.get('kind', 'navigation'),
                'priority': 0, 'depth': document.get('depth', 0), 'source_url': document.get('source_url'),
                'year': document.get('year'), 'year_status': document.get('year_status', 'unknown'),
                'year_evidence': document.get('year_evidence')})
    return pages, retry, list({b['url']: b for b in bindings}.values()), replayed, deferred


def _current_link(item, replayed):
    parent = item.get('source_url')
    return parent not in replayed or item.get('url') in replayed[parent]


def _check_host_stop(url, root):
    if urlsplit(url).hostname != 'yz.chsi.com.cn':
        return
    path = Path(root)/'data/runtime/chsi-host-gate.json'
    if not path.exists():
        return
    try:
        gate = json.loads(path.read_text('utf-8'))
        allowed = gate.get('host') == 'yz.chsi.com.cn' and gate.get('status') == 'allowed'
    except (OSError, ValueError, AttributeError):
        allowed = False
    if not allowed:
        raise SourceError('restricted', '研招网主机已暂停新增请求；保留已有原文，等待正常访问恢复后明确续采。')


def collect_unit(unit, inventory, directory, *, project_root=PROJECT_ROOT, year=2027,
                 max_pages=100, max_depth=8, fetcher=fetch_source, previous=None, checkpoint=None):
    directory, root = Path(directory), Path(project_root)
    state_path = directory/'state.json'
    if previous is None and state_path.is_file():
        try:
            previous = json.loads(state_path.read_text('utf-8'))
        except (OSError, ValueError):
            previous = None
    state = previous if isinstance(previous, dict) and previous.get('unit_id') == str(unit['id']) and previous.get('year') == year else {}
    pages, retry, bindings, replayed, deferred = _valid_resume(state, root, unit)
    errors = list(state.get('errors', []))
    attempts = {error['url']: error.get('attempts', 1) for error in errors if error.get('url')}
    retryable = {error['url'] for error in errors if error.get('url') and attempts[error['url']] < 3
        and (error.get('code') in {'timeout','dns_error','fetch_error','incomplete_response'}
             or error.get('code') == 'http_error' and re.search(r'HTTP\s*5\d\d', error.get('message','')))}
    retry += [{**error.get('request', {}), 'url': error['url'], 'priority': 0,
               'kind': error.get('request', {}).get('kind','navigation')}
              for error in errors if error.get('url') in retryable]
    frontier = [item for item in [*retry, *state.get('frontier', []), *_seeds(unit, inventory, year)]
                if item.get('url') and _owned_chsi(item, unit)]
    unresolved = list(state.get('unresolved_links', []))
    frontier += [{'url': b.get('linked_url', b['url']), 'title': '研招网声明官网入口',
        'kind': 'navigation', 'priority': 15, 'depth': 1, 'source_url': b['evidence']['url']} for b in bindings]
    frontier += [item for item in unresolved if _effective_unit(unit, bindings, item['url'])]
    unresolved = [item for item in unresolved if not _effective_unit(unit, bindings, item['url'])]
    suppressed = {item['url'] for item in [*frontier, *unresolved] if not _current_link(item, replayed)}
    frontier = [item for item in frontier if _current_link(item, replayed)]
    unresolved = [item for item in unresolved if _current_link(item, replayed)]
    seen = {p.get('requested_url', p['url']) for p in pages} | {p['url'] for p in pages}
    attempted = {e['url'] for e in errors if e['url'] not in retryable}
    cached = _cached_sources(inventory, root)

    def save(status):
        value = {'unit_id': str(unit['id']), 'unit_name': unit['name'], 'unit_code': unit.get('unit_code'),
            'year': year, 'status': status, 'checked_at': now(), 'catalog_complete': False,
            'limits': {'max_pages': max_pages, 'max_depth': max_depth}, 'pages': pages,
            'documents': [p for p in pages if p.get('year') == year and p['kind'] in {'catalog', 'charter', 'subjects', 'attachment'}],
            'bindings': bindings, 'frontier': frontier, 'deferred_links': deferred,
            'suppressed_obsolete_link_count': len(suppressed),
            'unresolved_links': unresolved, 'errors': errors,
            'notes': ['原文采集与目录解析、全校对账分别验收；未声明全国或本校专业目录完整。']}
        atomic_json(state_path, value)
        if checkpoint:
            checkpoint(value)
        return value

    save('crawling')
    while frontier and len(pages) < max_pages:
        frontier = sorted({item['url']: item for item in reversed(frontier)}.values(), key=lambda item: (item.get('priority', 30), item.get('depth', 0)))
        accessible = [item for item in frontier if item.get('depth', 0) <= max_depth]
        if not accessible:
            break
        item = accessible[0]
        frontier = [item, *[value for value in frontier if value is not item]]
        if item['url'] in seen or item['url'] in attempted:
            frontier = frontier[1:]
            continue
        bound = _effective_unit(unit, bindings, item['url'])
        if bound is None:
            unresolved.append({**item, 'reason': '官网归属尚未由研招网原文或已核验官网证明'})
            frontier = frontier[1:]
            continue
        try:
            page = cached.get(item['url'])
            if page is None:
                _check_host_stop(item['url'], root)
                page = fetcher(item['url'], bound)
            if not source_allowed(page['url'], bound):
                raise SourceError('wrong_unit', '返回原文的单位归属不一致')
            if page.get('text') and _restricted_html(page['text']):
                raise SourceError('restricted', '官方附件要求访问验证，尚未取得原文')
            document = _stored(page, item, directory, root, unit, year)
            seen |= {item['url'], page['url']}
            frontier = frontier[1:]
            html = page.get('text', '')
            if html:
                title, source_year, year_quote, parser = _html_info(html, unit, year, document['url'])
                if source_year == year:
                    document = {**document, 'title': title or document['title'], 'year': year,
                        'year_status': 'explicit_original', 'year_evidence': {'url': document['url'],
                            'sha256': document['sha256'], 'local_path': document['local_path'], 'quote': year_quote}}
                    classified_title = classify_link(title, document['url'], year)
                    if classified_title and classified_title['kind'] in {'catalog', 'charter', 'subjects'}:
                        document = {**document, 'kind': classified_title['kind']}
                elif re.search(r'(?<!\d)20\d{2}(?:年|级)', title):
                    document = {**document, 'title': title, 'year': None, 'year_status': 'source_year_conflict'}
                incoming = binding_from_html(unit, document, html, raw=page['raw'])
                bindings = list({b['url']: b for b in [*bindings, *incoming]}.values())
                if incoming:
                    frontier += [{'url': b.get('linked_url', b['url']), 'title': '研招网声明官网入口', 'kind': 'navigation',
                        'priority': 15, 'depth': item.get('depth', 0) + 1, 'source_url': document['url']} for b in incoming]
                    recovered = [link for link in unresolved if _effective_unit(unit, bindings, link['url'])]
                    frontier += recovered
                    unresolved = [link for link in unresolved if not _effective_unit(unit, bindings, link['url'])]
                document, links, held = _replayed_links(document, parser, year, unit, pages, replayed)
                replayed[document['url']] = {candidate['url'] for candidate in links}
                frontier += links
                deferred += held
            pages.append(document)
            errors = [error for error in errors if error.get('url') != item['url']]
        except (SourceError, DiscoveryError, OSError, ValueError, KeyError) as error:
            attempted.add(item['url'])
            frontier = frontier[1:]
            errors = [value for value in errors if value.get('url') != item['url']]
            errors.append({'url': item['url'], 'code': getattr(error, 'code', 'fetch_error'),
                           'attempts': attempts.get(item['url'],0) + 1, 'request': dict(item),
                           'message': str(error), 'checked_at': now()})
        save('crawling')
    frontier = [item for item in frontier if item['url'] not in seen and item['url'] not in attempted]
    if frontier or deferred:
        status = 'truncated'
    elif not pages and any(e['code'] == 'restricted' for e in errors):
        status = 'restricted'
    elif not pages:
        status = 'failed'
    elif unresolved:
        status = 'needs_binding'
    else:
        status = 'collected'
    return save(status)


class HostPacer:
    def __init__(self, interval=.35):
        self.interval, self.lock, self.next = interval, threading.Lock(), {}

    def __call__(self, url, unit):
        host = urlsplit(url).hostname
        with self.lock:
            delay = max(0, self.next.get(host, 0) - time.monotonic())
            self.next[host] = time.monotonic() + delay + self.interval
        if delay:
            time.sleep(delay)
        return fetch_source(url, unit)


def collect_national(registry, inventory, output, snapshot_dir, *, project_root=PROJECT_ROOT,
                     year=2027, workers=6, max_pages=100, max_depth=8, fetcher=None, refresh=False):
    units = registry.get('units', [])
    leads = {str(row['unit_id']): row for row in inventory.get('units', [])}
    records = {str(unit['id']): {'unit_id': str(unit['id']), 'unit_name': unit['name'], 'year': year,
        'status': 'pending', 'pages': [], 'documents': [], 'bindings': [], 'frontier': [],
        'catalog_complete': False} for unit in units}
    if not refresh and Path(output).is_file():
        try:
            saved = json.loads(Path(output).read_text('utf-8'))
            for record in saved.get('units', []):
                if str(record.get('unit_id')) in records and record.get('year') == year:
                    records[str(record['unit_id'])] = {**record, 'status': 'pending' if record.get('status') == 'crawling' else record.get('status', 'pending')}
        except (OSError, ValueError, TypeError):
            pass
    lock, last_save = threading.Lock(), [0.0]
    def snapshot(force=False):
        with lock:
            if not force and time.monotonic() - last_save[0] < 3:
                return None
            rows = [records[str(unit['id'])] for unit in units]
            original_urls = sum(len(r.get('documents', [])) for r in rows)
            originals = {doc.get('sha256') for row in rows for doc in row.get('documents', []) if doc.get('sha256')}
            metadata = {'schema_version': 1, 'year': year, 'total_units': len(units),
                'attempted_units': sum(r['status'] != 'pending' for r in rows),
                'running_units': sum(r['status'] == 'crawling' for r in rows),
                'units_with_originals': sum(bool(r.get('documents')) for r in rows),
                'original_count': original_urls, 'original_url_count': original_urls,
                'unique_original_count': len(originals), 'duplicate_original_url_count': original_urls - len(originals),
                'crawl_path_alias_count': sum(bool(page.get('crawl_alias_of')) for row in rows for page in row.get('pages', [])),
                'deferred_link_count': sum(len(row.get('deferred_links', [])) for row in rows),
                'fetched_pages': sum(len(r.get('pages', [])) for r in rows),
                'truncated_units': sum(r['status'] == 'truncated' for r in rows),
                'restricted_units': sum(r['status'] == 'restricted' for r in rows),
                'catalog_complete': False, 'checked_at': now(),
                'notes': ['分母为现有院校库索引，不等于已经确认的当年硕士招生单位总数。',
                    'original_count保留来源URL条数口径；unique_original_count按原文SHA去重，同字节别名不算独立原文。']}
            value = {'metadata': metadata, 'units': rows}
            atomic_json(output, value)
            last_save[0] = time.monotonic()
            return value
    def checkpoint(record):
        with lock:
            records[record['unit_id']] = record
        snapshot()
    def work(unit):
        folder = Path(snapshot_dir)/str(year)/str(unit['id'])
        previous = {} if refresh else None
        return collect_unit(unit, leads.get(str(unit['id']), {}), folder, year=year,
            project_root=project_root, max_pages=max_pages, max_depth=max_depth,
            fetcher=fetcher or pacer, previous=previous, checkpoint=checkpoint)
    pacer = HostPacer()
    snapshot(True)
    ordered = sorted(units, key=lambda unit: -sum(c.get('year') == year and c.get('kind') in {'catalog', 'charter'} for c in leads.get(str(unit['id']), {}).get('candidates', [])))
    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 12))) as pool:
        futures = {pool.submit(work, unit): unit for unit in ordered}
        for future in as_completed(futures):
            unit = futures[future]
            try:
                record = future.result()
            except Exception as error:
                record = {**records[str(unit['id'])], 'status': 'failed', 'error': type(error).__name__}
            checkpoint(record)
            print(json.dumps({'unit': unit['name'], 'status': record['status'],
                'pages': len(record.get('pages', [])), 'originals': len(record.get('documents', [])),
                'bindings': len(record.get('bindings', [])), 'remaining': len(record.get('frontier', []))}, ensure_ascii=False), flush=True)
    return snapshot(True)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--units', type=Path, default=PROJECT_ROOT/'data/units.json')
    cli.add_argument('--inventory', type=Path, default=PROJECT_ROOT/'data/source_inventory.json')
    cli.add_argument('--output', type=Path, default=PROJECT_ROOT/'data/national_sources.json')
    cli.add_argument('--snapshot-dir', type=Path, default=PROJECT_ROOT/'data/snapshots/national')
    cli.add_argument('--year', type=int, default=2027)
    cli.add_argument('--workers', type=int, default=6)
    cli.add_argument('--max-pages', type=int, default=100)
    cli.add_argument('--max-depth', type=int, default=8)
    cli.add_argument('--refresh', action='store_true')
    args = cli.parse_args(argv)
    if args.year != 2027 or not 1 <= args.max_pages <= 5000 or not 1 <= args.max_depth <= 20:
        cli.error('当前只采2027参考资料；页面上限1..5000，深度1..20')
    collect_national(json.loads(args.units.read_text('utf-8-sig')),
        json.loads(args.inventory.read_text('utf-8-sig')), args.output, args.snapshot_dir,
        year=args.year, workers=args.workers, max_pages=args.max_pages, max_depth=args.max_depth, refresh=args.refresh)


if __name__ == '__main__':
    main()
