"""Replayable, unit-scoped ownership evidence; candidates never become flat roots."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit

from collectors.documents import decode_text
from unit_bindings import find_row, normalize_name


MAX_BYTES = 2 * 1024 * 1024
MAX_PAGES = 6
MAX_DEPTH = 2
ACCOUNT_HOSTS = {'mp.weixin.qq.com': '__biz'}
OWN_ROLE = re.compile(r'研究生|研招|招生|学校主页|官方网站|官网|学院|院系|学部|公众号')
REFERENCE = re.compile(r'友情链接|其他|其它|参考|参照|合作单位|协作单位')
INSTITUTION = re.compile(r'大学|学院|研究院|研究所|党校')


def _now():
    return datetime.now(timezone.utc)


def _date(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
    except (ValueError, TypeError):
        return None


def _url(value, *, exception=False):
    if not isinstance(value, str) or not value or len(value) > 2048 or re.search(r'[\x00-\x20\\]', value):
        return None
    try:
        parts = urlsplit(value)
        host = (parts.hostname or '').lower().rstrip('.')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        if (parts.scheme not in {'http', 'https'} or not host or parts.username or parts.password
                or host in {'localhost', 'edu.cn', 'ac.cn', 'gov.cn', 'org.cn'}
                or host.endswith(('.localhost', '.local', '.internal'))):
            return None
        try:
            address = ipaddress.ip_address(host)
            if not exception or not address.is_global:
                return None
        except ValueError:
            if '.' not in host or not re.fullmatch(r'[a-z0-9.-]+', host):
                return None
        if port not in {80, 443} and not (exception and port == 8088):
            return None
        path = parts.path or '/'
        decoded = unquote(path)
        if '\\' in decoded or any(part in {'.', '..'} for part in decoded.split('/')):
            return None
        netloc = host + (':' + str(port) if port != (443 if parts.scheme == 'https' else 80) else '')
        return urlunsplit((parts.scheme, netloc, path, parts.query, ''))
    except (ValueError, UnicodeError):
        return None


def _scope(url, *, exception=False):
    normalized = _url(url, exception=exception)
    if not normalized:
        return None
    parts = urlsplit(normalized)
    account_key = ACCOUNT_HOSTS.get(parts.hostname)
    values = parse_qs(parts.query).get(account_key, []) if account_key else []
    account = {'key': account_key, 'value': values[0]} if len(values) == 1 and values[0] else None
    try:
        ipaddress.ip_address(parts.hostname)
        literal_ip = True
    except ValueError:
        literal_ip = False
    return {'scheme': parts.scheme, 'host': parts.hostname,
            'port': parts.port or (443 if parts.scheme == 'https' else 80),
            'path': parts.path, 'path_mode': 'prefix' if parts.path.endswith('/') else 'exact',
            'account': account, 'query': parse_qs(parts.query, keep_blank_values=True) if parts.query and not account else None,
            'public_ip_exception': bool(exception and (literal_ip or parts.port == 8088)),
            'https_upgrade': bool(parts.scheme == 'http' and parts.port in {None, 80} and not literal_ip)}


def scope_allows(url, scope):
    """Exact origin, bounded path and optional publisher account; no parent suffixes."""
    if not isinstance(scope, dict):
        return False
    normalized = _url(url, exception=scope.get('public_ip_exception') is True)
    if not normalized:
        return False
    parts = urlsplit(normalized)
    origin = (parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80))
    upgrade = (scope.get('https_upgrade') is True and scope.get('scheme') == 'http'
               and scope.get('port') == 80 and not scope.get('public_ip_exception')
               and origin == ('https', scope.get('host'), 443))
    if origin != (scope.get('scheme'), scope.get('host'), scope.get('port')) and not upgrade:
        return False
    path = scope.get('path')
    if not isinstance(path, str) or not path.startswith('/'):
        return False
    if scope.get('path_mode') == 'exact':
        if parts.path != path:
            return False
    elif scope.get('path_mode') == 'prefix':
        if parts.path != path.rstrip('/') and not parts.path.startswith(path.rstrip('/') + '/'):
            return False
    else:
        return False
    account = scope.get('account')
    if parts.hostname in ACCOUNT_HOSTS and not account:
        return False
    if account and (not isinstance(account, dict) or not account.get('value')
                    or parse_qs(parts.query).get(account.get('key')) != [account.get('value')]):
        return False
    if scope.get('query') is not None and parse_qs(parts.query, keep_blank_values=True) != scope['query']:
        return False
    return True


def _scope_url(scope):
    port = scope['port']
    netloc = scope['host'] + (':' + str(port) if port != (443 if scope['scheme'] == 'https' else 80) else '')
    query = scope.get('query') or {}
    if scope.get('account'):
        query = {scope['account']['key']: [scope['account']['value']]}
    return urlunsplit((scope['scheme'], netloc, scope['path'], urlencode(query, doseq=True), ''))


def registered_scopes(unit):
    """Current registered entries and reviewed declarations retain exact host/path."""
    if not isinstance(unit, dict):
        return ()
    values = [unit.get('official_url')]
    reviewed = find_row(unit)
    if reviewed:
        values.extend(row['url'] for row in reviewed['domains'])
    for row in unit.get('official_urls') or []:
        if isinstance(row, dict) and row.get('evidence') and row.get('checked_at'):
            values.append(row.get('url'))
    result = []
    for value in values:
        scope = _scope(value)
        if scope and scope['host'] != 'yz.chsi.com.cn' and scope not in result:
            result.append(scope)
            if scope['scheme'] == 'http' and scope['port'] == 80:
                secure = {**scope, 'scheme': 'https', 'port': 443, 'https_upgrade': False}
                if secure not in result:
                    result.append(secure)
    return tuple(result)


def _script_html(code):
    # Only literal HTML published by document.write is a navigation edge.
    from public_navigation import _at_top_level, _javascript
    _, mask = _javascript(code)
    literal = r'''(?P<quote>['"`])(?P<html>(?:\\.|(?!(?P=quote))[\s\S])*?)(?P=quote)'''
    token = r'''/\*[\s\S]*?\*/|//[^\r\n]*|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`'''
    pattern = r'(?P<publish>(?<![\w$.])document\.write(?:ln)?\(\s*' + literal + r'\s*\))|' + token
    return '\n'.join(match['html'].replace('\\"', '"').replace("\\'", "'").replace('\\/', '/')
                     for match in re.finditer(pattern, code)
                     if match['publish'] and '${' not in match['html'] and _at_top_level(mask, match.start()))


class _Page(HTMLParser):
    VOID = {'area', 'base', 'br', 'hr', 'img', 'input', 'link', 'meta', 'source', 'wbr'}

    def __init__(self, raw, media='text/html'):
        super().__init__(convert_charrefs=True)
        self.links, self.scripts, self.primary, self.text = [], [], [], []
        self.stack, self.anchor, self.label, self.heading, self.code = [], None, [], [], []
        self.active_script = False
        self.logos, self.logo_depths = [], set()
        html = decode_text(raw, media)
        self.feed(_script_html(html) if 'javascript' in media else html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = bool(self.stack and self.stack[-1][1]) or tag in {'style', 'template', 'noscript'}
        hidden = hidden or 'hidden' in attrs or attrs.get('aria-hidden') == 'true'
        hidden = hidden or bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', attrs.get('style', ''), re.I))
        if tag not in self.VOID:
            self.stack.append((tag, hidden))
        if hidden:
            return
        if tag not in self.VOID and (tag == 'header' or re.search(
                r'(?:^|[-_\s])logo(?:$|[-_\s])', attrs.get('class', '') + ' ' + attrs.get('id', ''), re.I)):
            self.logo_depths.add(len(self.stack)-1)
        if tag == 'img' and self.logo_depths:
            self.logos.append(attrs.get('alt', '').strip())
        if tag in {'a', 'area'} and attrs.get('href'):
            self.anchor, self.label = attrs['href'], []
            if tag == 'area':
                self.links.append((self.anchor, attrs.get('alt', '')))
                self.anchor = None
        if tag == 'script':
            self.active_script = attrs.get('type', '').lower() in {'', 'text/javascript', 'application/javascript', 'application/x-javascript'}
            if attrs.get('src') and self.active_script:
                self.scripts.append(attrs['src'])
            self.code = []
        if tag in {'title', 'h1'}:
            self.heading = []

    def handle_data(self, value):
        if self.stack and self.stack[-1][1]:
            return
        if any(tag == 'script' for tag, _ in self.stack):
            self.code.append(value)
            return
        self.text.append(value)
        if self.anchor is not None:
            self.label.append(value)
        if any(tag in {'title', 'h1'} for tag, _ in self.stack):
            self.heading.append(value)

    def handle_endtag(self, tag):
        hidden = bool(self.stack and self.stack[-1][1])
        if not hidden:
            if tag == 'a' and self.anchor is not None:
                self.links.append((self.anchor, ''.join(self.label).strip()))
                self.anchor = None
            if tag in {'title', 'h1'} and self.heading:
                self.primary.append(''.join(self.heading).strip())
                self.heading = []
            if tag == 'script' and self.code and self.active_script:
                nested = _Page(_script_html(''.join(self.code)).encode('utf-8'))
                self.links.extend(nested.links)
                self.code = []
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                self.logo_depths = {depth for depth in self.logo_depths if depth < len(self.stack)}
                break


def qualified_title_matches(unit, heading, text):
    """Match an omitted campus qualifier only with full visible identity or unit code."""
    name = normalize_name(unit.get('name'))
    qualified = re.fullmatch(r'(.+)\(([^()]+)\)', name)
    value, visible = normalize_name(heading), normalize_name(text)
    code = str(unit.get('unit_code') or '')
    unit_field = bool(code and re.search(r'(?:招生单位|学校|院校)代码[:：]' + re.escape(code) + r'(?!\d)', visible))
    return bool(qualified and qualified[1] in value and '(' not in value
                and ')' not in value and (name in visible or unit_field))


def _identity(unit, document):
    page = _Page(document['raw'], document['content_type'])
    name = normalize_name(unit.get('name'))
    headings = [normalize_name(value) for value in page.primary]
    # A shortened campus title needs the complete visible name or unit code after ownership proof.
    def own_heading(value):
        return name in value or qualified_title_matches(unit, value, ''.join(page.text))
    if not name or any(INSTITUTION.search(value) and not own_heading(value) for value in headings):
        return 'target_unit_mismatch', None
    own = next((value for value in headings if own_heading(value)), None)
    if not own:
        own = next((normalize_name(value) for value in page.logos if own_heading(normalize_name(value))), None)
    if not own:
        return 'target_identity_missing', None
    role = 'department' if re.search(r'学院|学部|院系', own.replace(name, '').replace('研究生学院', '研究生院')) else 'admissions' if OWN_ROLE.search(own.replace(name, '')) else 'institution'
    return None, role


def _literal_declarations(unit, text, foreign_names):
    from collectors.official_bindings import URL, BARE_URL
    name = normalize_name(unit.get('name'))
    found = []
    for pattern in (URL, BARE_URL):
        for match in pattern.finditer(text):
            if match.start() and text[match.start()-1] == '@':
                continue
            href = match[0].rstrip('.,;:!?)]}')
            start = text.rfind('\n', 0, match.start()) + 1
            prefix = normalize_name(text[start:match.start()])
            previous = next((normalize_name(line) for line in reversed(text[:start].splitlines()) if line.strip()), '')
            clause = prefix or previous
            if re.search(r'其他|其它|参照|参考(?!书目)|友情链接|借用|合作单位|协作单位', clause) or any(normalize_name(other) != name and normalize_name(other) in clause
                                               for other in foreign_names if other):
                continue
            role = bool(re.search(r'(?:学校|本校|我校|本院|我院|本所|我所|研究生|研招|官网).*?(?:网址|网站|网|主页)[:：]?$', clause))
            nearby = normalize_name(text[max(0, match.start()-600):match.start()])
            contact = bool(re.fullmatch(r'(?:网站|网址)[:：]', clause) and
                           re.search(r'联系方式|联系办法|通讯信息|招生咨询', nearby) and
                           (name in nearby or unit.get('unit_code') and re.search(
                               r'单位代码[:：]' + re.escape(str(unit['unit_code'])) + r'(?!\d)', nearby)))
            publication = bool(name in clause and re.search(r'(?:硕士|研究生).*(?:招生章程|招生简章|专业目录)', clause))
            if not (role or contact or publication):
                continue
            implicit = href.lower().startswith('www.') or BARE_URL.fullmatch(href)
            target = _url('https://' + href if implicit else href, exception=True)
            if target and not (urlsplit(target).hostname == 'chsi.com.cn' or urlsplit(target).hostname.endswith('.chsi.com.cn')):
                found.append((target, href))
    return found


class OfficialAuthority:
    def __init__(self, data_dir, *, clock=None):
        self.data_dir = Path(data_dir).resolve()
        self.path = self.data_dir / 'runtime/official-authority.sqlite'
        self.clock = clock or _now

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                connection.execute('CREATE TABLE IF NOT EXISTS authority (id TEXT PRIMARY KEY, unit_id TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL)')
                yield connection
        finally:
            connection.close()

    def seed_urls(self, unit):
        registered = tuple(_scope_url(row) for row in registered_scopes(unit))
        return tuple(dict.fromkeys([*registered, *(row['candidate_url'] for row in self.bindings(unit))]))

    @staticmethod
    def _failure(code, message='归属证据尚未完成核验。'):
        return {'status': 'needs_review', 'code': code, 'message': message}

    def _checked(self, document, requested, scope, *, saved=False):
        if not isinstance(document, dict) or not isinstance(document.get('raw'), bytes):
            raise ValueError('invalid_document')
        raw = document['raw']
        if not raw or len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != document.get('sha256'):
            raise ValueError('invalid_original')
        if not saved and document.get('network_verified') is not True:
            raise ValueError('network_unverified')
        actual = _url(document.get('url'), exception=scope.get('public_ip_exception') is True)
        if not actual or not scope_allows(actual, scope):
            raise ValueError('redirect_scope_mismatch')
        media = document.get('content_type', '')
        if not isinstance(media, str) or not any(kind in media.lower() for kind in ('html', 'javascript')):
            raise ValueError('unsupported_type')
        if 'javascript' in media.lower() and len(raw) > 256 * 1024:
            raise ValueError('script_too_large')
        return {**document, 'url': actual, 'requested_url': requested, 'content_type': media,
                'fetch_scope': scope}

    def _snapshot(self, document):
        path = self.data_dir / 'runtime/official-authority-proofs' / (document['sha256'] + '.raw')
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(document['raw'])
        return {key: value for key, value in {**document, 'local_path': path.relative_to(self.data_dir).as_posix()}.items()
                if key in {'url', 'requested_url', 'sha256', 'content_type', 'local_path', 'fetch_scope', 'kind', 'href', 'label', 'next_url'}}

    def _load_proof(self, proof):
        local = proof.get('local_path')
        if not isinstance(local, str):
            raise ValueError('invalid_original')
        path = (self.data_dir / local).resolve()
        if not path.is_relative_to(self.data_dir / 'runtime/official-authority-proofs'):
            raise ValueError('invalid_original')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != proof.get('sha256'):
            raise ValueError('proof_changed')
        return {**proof, 'raw': raw}

    def _saved(self, unit, declarations, foreign_names):
        from collectors.official_bindings import binding_candidates, _OriginalHTML, _context
        from public_sources import document_attribution, rendered_source_html
        result = []
        for source in list(declarations)[:8]:
            try:
                if not isinstance(source, dict):
                    continue
                raw = source.get('raw')
                if raw is None and source.get('local_path'):
                    path = (self.data_dir / source['local_path']).resolve()
                    if not path.is_relative_to(self.data_dir):
                        continue
                    raw = path.read_bytes()
                if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES or hashlib.sha256(raw).hexdigest() != source.get('sha256'):
                    continue
                url = source.get('url', '')
                if urlsplit(url).hostname != 'yz.chsi.com.cn':
                    continue
                doc = {**source, 'raw': raw, 'content_type': source.get('content_type', 'text/html'), 'kind': 'saved_chsi'}
                for candidate in binding_candidates(unit, doc, raw, foreign_names=foreign_names):
                    if candidate['status'] == 'declared_official_origin':
                        result.append((candidate['linked_url'], candidate['evidence']['quote'], doc))
                page = _OriginalHTML()
                page.feed(rendered_source_html(decode_text(raw, doc['content_type']), url))
                text = ''.join(page.parts)
                if document_attribution(url, unit, text, document_heading=' '.join(page.heading))['status'] != 'verified':
                    continue
                for anchor in page.anchors:
                    href, label = anchor['href'], anchor['label']
                    context = _context(text, anchor['start'], anchor['end'])
                    target = _url(urljoin(url, href), exception=True)
                    if target and self._own_link(unit, context + label, foreign_names):
                        result.append((target, href, doc))
                result.extend((target, href, doc) for target, href in _literal_declarations(unit, text, foreign_names))
            except (ValueError, TypeError, OSError):
                continue
        return result

    @staticmethod
    def _own_link(unit, label, foreign_names):
        return bool(OWN_ROLE.search(label) and not REFERENCE.search(label)
                    and not any(name and name != unit.get('name') and name in label for name in foreign_names))

    def declaration_candidates(self, unit, saved_declarations, foreign_names=()):
        """Return proven declaration pointers, awaiting separate target verification."""
        if not isinstance(unit, dict):
            return []
        candidates = {}
        for linked, href, document in self._saved(unit, saved_declarations, foreign_names):
            if urlsplit(linked).hostname == 'yz.chsi.com.cn':
                continue
            candidates.setdefault(linked, {'url': linked, 'unit_id': str(unit.get('id')),
                'proof_url': document['url'], 'sha256': document['sha256'], 'quoted_href': href,
                'status': 'ownership_candidate', 'relation': 'saved_chsi_explicit_declaration'})
        return list(candidates.values())

    def verify_candidate(self, unit, candidate_url, *, fetcher, roots=(), saved_declarations=(), foreign_names=()):
        """Resolve at most six public reads from current registered/saved proof roots."""
        target = _url(candidate_url, exception=True)
        if not isinstance(unit, dict) or not unit.get('id') or not target or urlsplit(target).hostname == 'yz.chsi.com.cn':
            return self._failure('invalid_candidate')
        if urlsplit(target).hostname in ACCOUNT_HOSTS and not _scope(target).get('account'):
            return self._failure('account_scope_missing')
        try:
            return self._verify(unit, target, fetcher, roots, saved_declarations, foreign_names)
        except (ValueError, OSError, KeyError, TypeError) as error:
            return self._failure(str(error)[:100] or 'verification_failed')
        except Exception as error:
            # Fetchers expose existing transport errors without importing a second network stack.
            return self._failure(getattr(error, 'code', 'fetch_failed'))

    def _verify(self, unit, target, fetcher, roots, saved_declarations, foreign_names):
        queue, seen, reads = [], set(), 0
        proof_chain, grant_scope, seed = None, None, None
        for linked, href, doc in self._saved(unit, saved_declarations, foreign_names):
            scope = _scope(linked, exception=True)
            if scope_allows(target, scope):
                proof_chain = [{**doc, 'href': href, 'next_url': linked}]
                grant_scope, seed = scope, {'kind': 'saved_chsi'}
                break
        seeds = registered_scopes(unit)
        starts = [(_scope_url(scope), scope, {'kind': 'registered', 'scope': scope})
                  for scope in sorted(seeds, key=lambda row: row['scheme'] != 'https')]
        for grant in self.bindings(unit):
            starts.append((grant['candidate_url'], grant['scope'],
                           {'kind': 'derived', 'authority_id': grant['id'], 'scope': grant['scope']}))
        for url, scope, root in starts:
            if scope_allows(target, scope):
                grant_scope, proof_chain, seed = scope, [], root
                break
        for url, scope, root in starts:
            queue.append((url, scope, [], 0, root))
        supplied = {doc.get('url'): doc for doc in list(roots)[:MAX_PAGES] if isinstance(doc, dict)}
        while proof_chain is None and queue and reads < MAX_PAGES - 1:
            url, scope, route, depth, root = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            reads += 1
            try:
                doc = self._checked(supplied[url] if url in supplied else fetcher(url, scope), url, scope)
            except Exception:
                continue
            page = _Page(doc['raw'], doc['content_type'])
            if _identity(unit, doc)[0] == 'target_unit_mismatch':
                return self._failure('proof_unit_mismatch')
            for href, label in page.links[:200]:
                linked = _url(urljoin(doc['url'], href))
                if not linked or not self._own_link(unit, label, foreign_names):
                    continue
                linked_scope = _scope(linked)
                edge = {**doc, 'kind': 'raw_link', 'href': href, 'label': label, 'next_url': linked}
                if scope_allows(target, linked_scope):
                    proof_chain, grant_scope, seed = [*route, edge], linked_scope, root
                    break
                if depth < MAX_DEPTH and scope_allows(linked, scope):
                    queue.append((linked, scope, [*route, edge], depth + 1, root))
            if proof_chain is not None:
                break
            for src in page.scripts[:2]:
                script = _url(urljoin(doc['url'], src))
                if script and depth < MAX_DEPTH and scope_allows(script, scope):
                    queue.append((script, scope, [*route, {**doc, 'kind': 'script_src', 'href': src,
                                                        'next_url': script}], depth + 1, root))
        if proof_chain is None:
            return self._failure('ownership_unproven')
        if grant_scope['host'] in ACCOUNT_HOSTS and not grant_scope.get('account'):
            return self._failure('account_scope_missing')
        target_doc = self._checked(fetcher(target, grant_scope), target, grant_scope)
        problem, role = _identity(unit, target_doc)
        if problem:
            return self._failure(problem)
        account = grant_scope.get('account')
        if account:
            html = decode_text(target_doc['raw'], target_doc['content_type'])
            identifiers = re.findall(r'''(?:\b(?:biz|__biz)\s*[:=]\s*["'])([^"']+)''', html)
            if not identifiers or set(identifiers) != {account['value']}:
                return self._failure('target_account_mismatch')
        checked = self.clock()
        payload = {'unit_id': str(unit['id']), 'unit_name': unit.get('name'), 'unit_code': str(unit.get('unit_code') or ''),
                   'candidate_url': target, 'scope': grant_scope, 'publisher_role': role, 'seed': seed,
                   'proofs': [self._snapshot(doc) for doc in proof_chain], 'target': self._snapshot(target_doc),
                   'checked_at': checked.isoformat(), 'expires_at': (checked + timedelta(days=30)).isoformat(),
                   'state': 'verified', 'replay_verified': True}
        identifier = 'oa_' + hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        payload = {**payload, 'id': identifier}
        with self._connect() as connection:
            connection.execute('INSERT OR REPLACE INTO authority VALUES (?, ?, ?, ?)',
                               (identifier, str(unit['id']), 'verified', json.dumps(payload, ensure_ascii=False)))
        return {'status': 'verified', 'code': 'ownership_verified', 'authority': payload}

    def revoke(self, authority_id, reason='revoked'):
        if not self.path.exists():
            return False
        with self._connect() as connection:
            row = connection.execute('SELECT payload FROM authority WHERE id=?', (authority_id,)).fetchone()
            if not row:
                return False
            payload = {**json.loads(row[0]), 'state': 'revoked', 'replay_verified': False, 'reason': str(reason)[:200]}
            connection.execute('UPDATE authority SET state=?,payload=? WHERE id=?',
                               ('revoked', json.dumps(payload, ensure_ascii=False), authority_id))
        return True

    def replay(self, unit, authority_id, *, fetcher=None, _seen=()):
        if authority_id in _seen or len(_seen) >= 6:
            return self._failure('proof_cycle_or_depth')
        if not self.path.exists():
            return self._failure('authority_missing')
        try:
            with self._connect() as connection:
                row = connection.execute('SELECT state,payload FROM authority WHERE id=?', (authority_id,)).fetchone()
        except sqlite3.Error:
            return self._failure('authority_store_unreadable')
        if not row or row[0] != 'verified':
            return self._failure('authority_revoked' if row else 'authority_missing')
        try:
            payload = json.loads(row[1])
            if (payload['unit_id'] != str(unit.get('id')) or normalize_name(payload['unit_name']) != normalize_name(unit.get('name'))
                    or payload['unit_code'] != str(unit.get('unit_code') or '')):
                return self._failure('unit_identity_changed')
            expiry, checked = _date(payload.get('expires_at')), _date(payload.get('checked_at'))
            if (not expiry or not checked or expiry <= self.clock() or checked > self.clock()
                    or expiry > checked + timedelta(days=30) or payload.get('state') != 'verified'):
                return self._failure('proof_expired')
            seed = payload['seed']
            if seed['kind'] == 'registered' and seed.get('scope') not in registered_scopes(unit):
                return self._failure('registered_seed_changed')
            if seed['kind'] == 'derived':
                parent = self.replay(unit, seed.get('authority_id'), fetcher=fetcher, _seen=(*_seen, authority_id))
                if parent['status'] != 'verified' or parent['authority']['scope'] != seed.get('scope'):
                    return self._failure('parent_authority_invalid')
            elif seed['kind'] not in {'registered', 'saved_chsi'}:
                return self._failure('invalid_proof_root')
            proofs = [self._load_proof(proof) for proof in payload['proofs']]
            target = self._load_proof(payload['target'])
            expected_scope = seed.get('scope')
            expected_url = None
            for index, proof in enumerate(proofs):
                if proof['kind'] == 'saved_chsi':
                    if index or seed['kind'] != 'saved_chsi':
                        raise ValueError('proof_chain_mismatch')
                    declarations = self._saved(unit, [proof], ())
                    if not any(href == proof['href'] and linked == proof['next_url'] for linked, href, _ in declarations):
                        raise ValueError('proof_replay_failed')
                    expected_scope = _scope(proof['next_url'], exception=True)
                    continue
                if (not expected_scope or not scope_allows(proof['requested_url'], expected_scope)
                        or expected_url and proof['requested_url'] != expected_url):
                    raise ValueError('proof_chain_mismatch')
                proof = self._checked(proof, proof['requested_url'], expected_scope, saved=True)
                proofs[index] = proof
                page = _Page(proof['raw'], proof['content_type'])
                links = [(src, '') for src in page.scripts] if proof['kind'] == 'script_src' else page.links
                if not any(href == proof['href'] and _url(urljoin(proof['url'], href)) == proof['next_url']
                           and (proof['kind'] == 'script_src' or label == proof.get('label') and self._own_link(unit, label, ()))
                           for href, label in links):
                    raise ValueError('proof_replay_failed')
                expected_url = proof['next_url']
                if index == len(proofs) - 1:
                    if proof['kind'] != 'raw_link':
                        raise ValueError('proof_chain_mismatch')
                    expected_scope = _scope(proof['next_url'])
            if expected_scope != payload['scope']:
                raise ValueError('proof_scope_changed')
            if not scope_allows(target['requested_url'], expected_scope):
                raise ValueError('target_replay_failed')
            target = self._checked(target, target['requested_url'], expected_scope, saved=True)
            if _identity(unit, target)[0] or not scope_allows(target['url'], payload['scope']):
                raise ValueError('target_replay_failed')
            account = payload['scope'].get('account')
            if account:
                html = decode_text(target['raw'], target['content_type'])
                values = re.findall(r'''(?:\b(?:biz|__biz)\s*[:=]\s*["'])([^"']+)''', html)
                if not values or set(values) != {account['value']}:
                    raise ValueError('target_account_mismatch')
            if fetcher:
                for proof in [*proofs, target]:
                    if proof.get('kind') == 'saved_chsi':
                        continue
                    live = self._checked(fetcher(proof['requested_url'], proof['fetch_scope']),
                                         proof['requested_url'], proof['fetch_scope'])
                    if live['sha256'] != proof['sha256'] or live['url'] != proof['url']:
                        raise ValueError('proof_changed')
            return {'status': 'verified', 'code': 'proof_replayed', 'authority': payload}
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.revoke(authority_id, str(error))
            return self._failure(str(error)[:100] or 'proof_replay_failed')
        except Exception as error:
            self.revoke(authority_id, getattr(error, 'code', 'proof_fetch_failed'))
            return self._failure(getattr(error, 'code', 'proof_fetch_failed'))

    def bindings(self, unit):
        if not isinstance(unit, dict) or not self.path.exists():
            return ()
        try:
            with self._connect() as connection:
                identifiers = [row[0] for row in connection.execute(
                    'SELECT id FROM authority WHERE unit_id=? AND state=?', (str(unit.get('id')), 'verified'))]
        except sqlite3.Error:
            return ()
        results = (self.replay(unit, identifier) for identifier in identifiers)
        return tuple(result['authority'] for result in results if result['status'] == 'verified')

    def allows(self, unit, url):
        if not isinstance(unit, dict):
            return False
        return any(scope_allows(url, scope) for scope in registered_scopes(unit)) or any(
            scope_allows(url, row['scope']) for row in self.bindings(unit))

    def enrich(self, unit):
        if not isinstance(unit, dict):
            return unit
        registered = [{'id': 'registered_' + str(index), 'unit_id': str(unit.get('id')),
                       'state': 'verified', 'replay_verified': True, 'scope': scope,
                       'publisher_role': 'registered'} for index, scope in enumerate(registered_scopes(unit))]
        return {**unit, 'official_authorities': [*registered, *self.bindings(unit)]}
