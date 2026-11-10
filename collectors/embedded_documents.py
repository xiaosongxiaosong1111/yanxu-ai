"""Find published document-viewer references; candidates carry no trust or year."""
from html.parser import HTMLParser
import re
from urllib.parse import urljoin

from public_navigation import MAX_CANDIDATES, MAX_INPUT_CHARS, _literal, _origin, _target, extract_navigation


_VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
         'param', 'source', 'track', 'wbr'}
_HIDDEN_CLASS = re.compile(r'(?:^|\s)(?:tooltip(?:-[\w-]+)?|popover(?:-[\w-]+)?|hidden|d-none|hide)(?:$|\s)', re.I)
_VSB_LITERAL = re.compile(r'''\bshowVsbpdfIframe\s*\(\s*('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")''')


def _hidden(tag, attrs):
    style = re.sub(r'\s+', '', attrs.get('style', '').lower())
    return (tag in {'template', 'noscript', 'style'} or 'hidden' in attrs or 'inert' in attrs
            or attrs.get('aria-hidden', '').lower() == 'true' or attrs.get('role', '').lower() == 'tooltip'
            or bool(re.search(r'(?:^|;)(?:display:none|visibility:hidden)(?:!important)?(?:;|$)', style))
            or bool(_HIDDEN_CLASS.search(attrs.get('class', ''))))


class _PublishedHTML(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.html, self.nodes, self.stack, self.ranges = html, [], [], []
        self.lines = [0] + [match.end() for match in re.finditer('\n', html)]
        self.base = None

    def char_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if len(self.nodes) >= 20000 or len(self.stack) >= 256:
            raise ValueError('HTML structure limit exceeded')
        values = dict(attrs)
        # Missing attribute values must remain usable for boolean-attribute tests.
        normalized = {key: value or '' for key, value in values.items()}
        inherited = bool(self.stack and self.stack[-1]['hidden'])
        start = self.char_offset()
        node = {'tag': tag, 'attrs': normalized, 'start': start,
                'end': start + len(self.get_starttag_text()),
                'hidden': inherited or _hidden(tag, normalized), 'root_hidden': False}
        node['root_hidden'] = node['hidden'] and not inherited
        self.nodes.append(node)
        if tag == 'base' and not node['hidden'] and self.base is None and 'href' in normalized:
            self.base = node
        if tag in _VOID:
            if node['root_hidden']:
                self.ranges.append((node['start'], node['end']))
        else:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                end = self.html.find('>', self.char_offset()) + 1
                for node in self.stack[index:]:
                    if node['root_hidden']:
                        self.ranges.append((node['start'], end or len(self.html)))
                self.stack = self.stack[:index]
                break

    def masked(self):
        ranges = self.ranges + [(node['start'], len(self.html)) for node in self.stack if node['root_hidden']]
        # Anchors are handled elsewhere and must not consume the viewer candidate budget.
        ranges += [(node['start'], node['end']) for node in self.nodes if node['tag'] in {'a', 'area'}]
        chars = list(self.html)
        for start, end in ranges:
            chars[start:end] = ['\n' if char == '\n' else ' ' for char in chars[start:end]]
        return ''.join(chars)


def _proof(html, page_url, node):
    start, end = node['start'], min(node['end'], node['start'] + 4096)
    return {'source_url': page_url, 'start': start, 'end': end, 'snippet': html[start:end]}


def _sudy_label(attrs):
    # The viewer's metadata is data, never executable JS or a Python expression.
    title = re.search(r'''['"]title['"]\s*:\s*(['"])(.{0,500}?)\1''', attrs.get('sudyfile-attr', ''))
    return title[2] if title else attrs.get('title', '') or 'PDF原文'


def extract_embedded_documents(html, page_url):
    """Return static same-origin viewer links with exact original HTML evidence.

    Parsing does not fetch files, execute JavaScript, infer their contents, approve
    official ownership, or inherit an admissions year from a filename.
    """
    if not isinstance(html, str) or len(html) > MAX_INPUT_CHARS or not _target(page_url, page_url):
        return []
    parser = _PublishedHTML(html)
    try:
        parser.feed(html)
        parser.close()
        base = urljoin(page_url, parser.base['attrs']['href']) if parser.base else page_url
    except (ValueError, RecursionError):
        return []
    base_proofs = [_proof(html, page_url, parser.base)] if parser.base else []
    rows, seen = [], set()

    def offer(href, label, recipe, evidence):
        target = _target(href, base)
        if not target or _origin(target) != _origin(page_url) or target in seen or len(rows) >= MAX_CANDIDATES:
            return
        seen.add(target)
        rows.append({'url': target, 'href': href, 'label': re.sub(r'\s+', ' ', label).strip()[:160],
                     'kind': 'attachment', 'recipe': recipe, 'evidence': evidence + base_proofs})

    nodes_by_start = {node['start']: node for node in parser.nodes if not node['hidden']}
    for candidate in extract_navigation(parser.masked(), base):
        role = candidate['role']
        if role not in {'vsb_pdf', 'iframe'}:
            continue
        evidence = [{**proof, 'source_url': page_url,
                     'snippet': html[proof['start']:proof['end']]} for proof in candidate['evidence']]
        first = evidence[0]
        if role == 'vsb_pdf':
            match = _VSB_LITERAL.search(first['snippet'])
            href = _literal(match[1]) if match else None
        else:
            node = nodes_by_start.get(first['start'])
            href = node['attrs'].get('data' if node['tag'] == 'object' else 'src') if node else None
        if href:
            offer(href, candidate['label'] or 'PDF原文', role, evidence)
    for node in parser.nodes:
        attrs = node['attrs']
        explicit = ('wp_pdf_player' in attrs.get('class', '').split() or 'sudyplayer' in attrs)
        if node['tag'] == 'div' and not node['hidden'] and explicit and attrs.get('pdfsrc'):
            offer(attrs['pdfsrc'], _sudy_label(attrs), 'sudy_pdfsrc', [_proof(html, page_url, node)])
    return rows
