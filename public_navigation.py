"""Bounded public navigation patterns; this module never fetches or executes JS."""
from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit


MAX_CANDIDATES = 200
MAX_INPUT_CHARS = 2 * 1024 * 1024
MAX_SCRIPT_SOURCES = 8
_TOKENS = re.compile(r'''//[^\r\n]*|/\*[\s\S]*?\*/|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`''')
_LITERAL = re.compile(r'''(?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")''')


def _javascript(code):
    """Keep source offsets while removing comments, and mask all literal tokens."""
    clean = _TOKENS.sub(lambda m: ' ' * len(m[0]) if m[0].startswith(('//', '/*')) else m[0], code)
    mask = _TOKENS.sub(lambda m: ' ' * len(m[0]), code)
    return clean, mask


def _literal(value):
    if not _LITERAL.fullmatch(value):
        return None
    escapes = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f', 'v': '\v'}
    def replace(match):
        token = match[0][1:]
        if token.startswith(('u', 'x')):
            return chr(int(token[1:], 16))
        return escapes.get(token, '' if token in {'\n', '\r\n'} else token)
    return re.sub(r'\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r?\n|.)', replace, value[1:-1])


def _target(value, base, authority_scope=None):
    if not isinstance(value, str) or not value.strip() or value.strip().startswith('#') or len(value) > 2048:
        return None
    if any(ord(char) < 32 for char in value):
        return None
    try:
        parts = urlsplit(urljoin(base, value.strip()))
        host = parts.hostname or ''
        permitted_port = parts.port in {None, 80, 443}
        if not permitted_port and isinstance(authority_scope, dict):
            from official_authority import scope_allows
            permitted_port = (authority_scope.get('public_ip_exception') is True
                              and parts.port == 8088 and scope_allows(parts.geturl(), authority_scope))
        if (parts.scheme not in {'http', 'https'} or not host or parts.username or parts.password
                or not permitted_port or host == 'localhost'
                or host.endswith(('.local', '.localhost')) or '%' in host):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return urlunsplit((parts.scheme, parts.netloc, parts.path or '/', parts.query, ''))
    except (TypeError, ValueError):
        return None


def _origin(url):
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)


def _proof(text, source_url, start, end):
    end = min(end, start + 4096)
    return {'source_url': source_url, 'start': start, 'end': end, 'snippet': text[start:end]}


class _NavigationHTML(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.html, self.nodes, self.scripts, self.stack = html, [], [], []
        self.lines = [0] + [match.end() for match in re.finditer('\n', html)]
        self.inert = 0

    def handle_starttag(self, tag, attrs):
        if len(self.nodes) >= 20000 or len(self.stack) >= 256:
            return
        values = dict(attrs)
        line, col = self.getpos()
        start = self.lines[line - 1] + col
        node = {'tag': tag, 'attrs': values, 'start': start, 'end': start + len(self.get_starttag_text()),
                'parts': [], 'inert': bool(self.inert),
                'classes': ' '.join(item['attrs'].get('class', '') for item in self.stack)}
        self.nodes.append(node)
        if tag in {'template', 'noscript'}:
            self.inert += 1
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                if (tag == 'script' and not self.stack[index]['inert']
                        and self.stack[index]['attrs'].get('type', '').lower() in
                        {'', 'text/javascript', 'application/javascript', 'application/x-javascript'}):
                    node = self.stack[index]
                    if not node['attrs'].get('src'):
                        line, col = self.getpos()
                        self.scripts.append((self.html[node['end']:self.lines[line - 1] + col], node['end']))
                self.inert -= sum(item['tag'] in {'template', 'noscript'} for item in self.stack[index:])
                self.stack = self.stack[:index]
                break

    def handle_data(self, value):
        if not self.inert and not any(item['tag'] in {'script', 'style'} for item in self.stack):
            for node in self.stack:
                if node['tag'] == 'a' or node['attrs'].get('onclick'):
                    if sum(map(len, node['parts'])) < 500:
                        node['parts'].append(value)


def _at_top_level(mask, offset):
    prefix = mask[:offset]
    boundary = not prefix.strip() or prefix.rstrip().endswith((';', '}'))
    return boundary and all(prefix.count(left) == prefix.count(right)
                            for left, right in [('(', ')'), ('{', '}'), ('[', ']')])


def _published_level(clean, mask, offset):
    opened = []
    for index, char in enumerate(mask[:offset]):
        if char == '{':
            opened.append(index)
        elif char == '}' and opened:
            opened.pop()
    if not opened:
        return True
    if len(opened) != 1:
        return False
    return bool(re.search(r'\$\(\s*(?:document\s*\)\.ready\(\s*)?function\s*\(\s*\)\s*\{\s*$',
                          clean[max(0, opened[0] - 100):opened[0] + 1]))


def _function_body(mask, start):
    depth = 0
    for index in range(start, len(mask)):
        if mask[index] == '{':
            depth += 1
        elif mask[index] == '}':
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _bisu_routes(code, source_url, offset, source_text, nodes, base, offer):
    clean, mask = _javascript(code)
    pattern = r'''function\s+(\w+)\(\s*(\w+)\s*(?:,\s*\w+\s*)?\)\s*\{\s*(?:let|var|const)\s+(\w+)\s*=\s*\$\(\2\)\.attr\(\s*['"]data-id['"]\s*\)\s*;\s*(?:let|var|const)\s+(\w+)\s*=\s*('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")\s*\+\s*\3\s*\+\s*('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")\s*;\s*window\.location\.href\s*=\s*\4\s*;?\s*\}'''
    for match in re.finditer(pattern, clean):
        if not mask[match.start():].startswith('function') or not _published_level(clean, mask, match.start()):
            continue
        for node in nodes:
            attrs = node['attrs']
            if (re.fullmatch(re.escape(match[1]) + r'\(\s*this\s*\)\s*;?', attrs.get('onclick', ''))
                    and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', attrs.get('data-id', ''))):
                href = _literal(match[5]) + attrs['data-id'] + _literal(match[6])
                offer(href, ''.join(node['parts']), 'onclick',
                      [_proof(base[1], base[0], node['start'], node['end']),
                       _proof(source_text, source_url, offset + match.start(), offset + match.end())])


def _cug_helpers(scripts):
    proofs = {}
    expected = ('functiontoDetailPage(url){if(sessionStorage.getItem("spreadToken")){'
                'if(url.indexOf("?")>-1){url+="&spreadToken="+sessionStorage.getItem("spreadToken");}'
                'else{url+="?spreadToken="+sessionStorage.getItem("spreadToken");}}returnurl;}')
    for code, source_url, offset, source_text in scripts:
        clean, mask = _javascript(code)
        for match in re.finditer(r'function\s+(getAppServer|toDetailPage)\s*\([^)]*\)\s*\{', mask):
            if not _at_top_level(mask, match.start()):
                continue
            end = _function_body(mask, match.end() - 1)
            if end is None:
                continue
            body = re.sub(r'\s+', '', clean[match.start():end])
            valid = body in {'functiongetAppServer(){return"";}', "functiongetAppServer(){return'';}"}
            valid = valid or body in {expected, 'functiontoDetailPage(url){returnurl;}'}
            if valid:
                proofs[match[1]] = _proof(source_text, source_url, offset + match.start(), offset + end)
    return list(proofs.values()) if len(proofs) == 2 else []


def _cug_routes(code, source_url, offset, source_text, nodes, base, helpers, offer):
    clean, mask = _javascript(code)
    header = next((node for node in nodes if 'header_container' in node['attrs'].get('class', '').split()
                   and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', node['attrs'].get('scode', ''))), None)
    if not header or not helpers:
        return
    declaration = re.search(r'\bvar\s+m07\s*=\s*', mask)
    handler = re.search(r'''\$\(\s*['"]\.advice02_content_msg ul li a['"]\s*\)\.click\(\s*function\s*\(\s*\)\s*\{''', clean)
    if not declaration or not handler or mask[handler.start():handler.start()+1] != '$':
        return
    if not all(_published_level(clean, mask, match.start()) for match in (declaration, handler)):
        return
    end = _function_body(mask, handler.end() - 1)
    if not end:
        return
    body = clean[handler.end():end]
    patterns = [r'''var\s+index\s*=\s*\$\(this\)\.attr\(['"]index['"]\)''',
                r'var\s+entity\s*=\s*m07\.data\[0\]\.children\[index\]',
                r'var\s+parentId\s*=\s*m07\.data\[0\]\.catId',
                r'''else\s+if\s*\(entity\.ntype\s*==\s*1\)\s*\{\s*var\s+infoId\s*=\s*entity\.id;\s*var\s+sCode\s*=\s*\$\(['"]\.header_container['"]\)\.attr\(['"]sCode['"]\);\s*var\s+url\s*=\s*getAppServer\(\)\s*\+\s*['"]/page/detail/['"]\s*\+\s*sCode\s*\+\s*['"]/['"]\s*\+\s*parentId\s*\+\s*['"]/['"]\s*\+\s*infoId;\s*window\.open\(toDetailPage\(url\)\)''']
    if not all(re.search(pattern, body) for pattern in patterns):
        return
    try:
        model, consumed = json.JSONDecoder().raw_decode(clean[declaration.end():])
        group = model['data'][0]
        children, parent_id = group['children'], group['catId']
        if type(parent_id) is not int or not 0 < parent_id < 10**12 or not isinstance(children, list):
            return
    except (ValueError, KeyError, IndexError, TypeError):
        return
    for node in nodes:
        attrs = node['attrs']
        if (node['tag'] != 'a' or 'advice02_content_msg' not in node['classes'].split()
                or not re.fullmatch(r'\d{1,6}', attrs.get('index', ''))):
            continue
        try:
            entity = children[int(attrs.get('index', '-1'))]
            info_id = entity['id']
            if (type(info_id) is not int or not 0 < info_id < 10**12 or entity.get('ntype') != 1
                    or str(info_id) != attrs.get('id') or entity.get('catId') != parent_id):
                continue
        except (ValueError, KeyError, IndexError, TypeError):
            continue
        href = '/page/detail/' + header['attrs']['scode'] + '/' + str(parent_id) + '/' + str(info_id)
        offer(href, entity.get('name', ''), 'published_route',
              [_proof(base[1], base[0], node['start'], node['end']),
               _proof(source_text, source_url, offset + declaration.start(), offset + declaration.end() + consumed),
               _proof(source_text, source_url, offset + handler.start(), offset + end),
               _proof(base[1], base[0], header['start'], header['end']), *helpers])


def extract_navigation(raw_html, url, *, script_sources=None, authority_scope=None):
    """Return untrusted candidates with exact source spans, never document facts.

    Only explicitly referenced scripts supplied by the caller participate. Reads,
    authority checks and request budgets remain the caller's responsibility.
    """
    if not isinstance(raw_html, str) or len(raw_html) > MAX_INPUT_CHARS or not _target(url, url, authority_scope):
        return []
    parser = _NavigationHTML(raw_html)
    parser.feed(raw_html)
    parser.close()
    rows, seen = [], set()

    def offer(value, label, role, evidence, *, kind=None, same_origin=False):
        target = _target(value, url, authority_scope)
        if not target or target in seen or len(rows) >= MAX_CANDIDATES:
            return
        if same_origin and _origin(target) != _origin(url):
            return
        seen.add(target)
        kind = kind or ('pdf' if urlsplit(target).path.lower().endswith('.pdf') else 'page')
        rows.append({'url': target, 'label': re.sub(r'\s+', ' ', str(label)).strip()[:160],
                     'kind': kind, 'role': role, 'evidence': evidence})

    for node in parser.nodes:
        if len(rows) >= MAX_CANDIDATES:
            break
        if node['inert']:
            continue
        tag, attrs = node['tag'], node['attrs']
        proof = [_proof(raw_html, url, node['start'], node['end'])]
        if tag in {'a', 'area'}:
            offer(attrs.get('href'), ''.join(node['parts']) or attrs.get('alt', ''), 'anchor', proof)
        elif tag in {'iframe', 'embed', 'object'}:
            offer(attrs.get('data' if tag == 'object' else 'src'), attrs.get('title', ''), 'iframe', proof, same_origin=True)
        elif tag == 'script' and attrs.get('type', '').lower() in {'', 'text/javascript', 'application/javascript', 'application/x-javascript'}:
            offer(attrs.get('src'), '公开导航脚本', 'script', proof, kind='script')
    scripts = [(code, url, offset, raw_html) for code, offset in parser.scripts]
    referenced = {row['url'] for row in rows if row['kind'] == 'script'}
    if isinstance(script_sources, dict):
        for script_url, code in list(script_sources.items())[:MAX_SCRIPT_SOURCES]:
            if script_url in referenced and isinstance(code, str) and len(code) <= MAX_INPUT_CHARS:
                scripts.append((code, script_url, 0, code))
    helpers = _cug_helpers(scripts)
    nodes = [node for node in parser.nodes if not node['inert']]
    for code, source_url, offset, source_text in scripts:
        clean, mask = _javascript(code)
        for match in re.finditer(r'\b(?:document\.write(?:ln)?|showVsbpdfIframe|(?:window\.)?location\.href)\s*(?:\(|=)', mask):
            if len(rows) >= MAX_CANDIDATES:
                break
            name = match[0]
            if not _at_top_level(mask, match.start()):
                continue
            argument = _LITERAL.match(clean, match.end() + len(clean[match.end():]) - len(clean[match.end():].lstrip()))
            if not argument:
                continue
            tail = clean[argument.end():].lstrip()
            proof = [_proof(source_text, source_url, offset + match.start(), offset + argument.end())]
            value = _literal(argument[0])
            if name.startswith('document.') and re.match(r'^\)\s*;', tail):
                written = _NavigationHTML(value)
                written.feed(value)
                for node in written.nodes:
                    if node['tag'] in {'a', 'area', 'iframe'} and not node['inert']:
                        offer(node['attrs'].get('href' if node['tag'] in {'a', 'area'} else 'src'),
                              ''.join(node['parts']) or node['attrs'].get('alt', ''), 'document_write', proof,
                              same_origin=node['tag'] == 'iframe')
            elif name.startswith('showVsbpdfIframe') and tail.startswith(','):
                if urlsplit(value).path.lower().endswith('.pdf'):
                    offer(value, 'PDF原文', 'vsb_pdf', proof, same_origin=True)
            elif 'location.href' in name and re.match(r'^(?:;|$)', tail):
                offer(value, '公开页面跳转', 'location', proof, same_origin=True)
        _bisu_routes(code, source_url, offset, source_text, nodes, (url, raw_html), offer)
        _cug_routes(code, source_url, offset, source_text, nodes, (url, raw_html), helpers, offer)
    return rows
