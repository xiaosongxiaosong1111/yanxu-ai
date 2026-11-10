"""Visible saved HTML and complete HTTP entry targets."""
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit


_URI_START = r'(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.)'
_VISIBLE_URL = re.compile(
    _URI_START + r"[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%\-]+", re.I)
_IRI_ENVELOPE = re.compile(
    _URI_START + r'[^\s<>"`{}。，；、]+', re.I)
_WRAPPERS = {'(': ')', '[': ']', '（': '）', '【': '】', '《': '》',
             '“': '”', '‘': '’', '「': '」', '『': '』'}
_VOID_TAGS = frozenset({
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'param', 'source', 'track', 'wbr'})


class _VisibleHTMLParser(HTMLParser):
    """Collect visible article text and real anchor targets.

    Comments, scripts, styles, navigation/head/footer chrome and hidden
    elements are not visible article content.  Text is whitespace-normalised
    as it is appended so anchor offsets stay valid for later role checks.
    """

    _BLOCK = frozenset({
        'p', 'div', 'br', 'li', 'tr', 'td', 'th', 'h1', 'h2', 'h3', 'h4',
        'h5', 'h6', 'section', 'article', 'ul', 'ol', 'table', 'tbody',
        'thead', 'hr', 'blockquote', 'pre'})
    _SKIP = frozenset({'script', 'style', 'noscript', 'template', 'svg'})
    _CHROME_TAG = frozenset({'head', 'header', 'nav', 'footer', 'aside'})
    _CHROME_MARK = re.compile(
        r'(?:^|[-_])(?:nav|navbar|menu|footer|header|breadcrumb|sidebar|banner)'
        r'(?:$|[-_])', re.I)

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts = []
        self._length = 0
        self._open = []
        # Each suppressed subtree is [tag, depth].  Depth counts nested tags
        # with the same name so an inner closing tag cannot end an outer
        # hidden/chrome container early.
        self._skip_tags = []
        self.anchors = []

    def _space(self):
        if self._length and not self._parts[-1].endswith(' '):
            self._parts.append(' ')
            self._length += 1

    def _add(self, value):
        value = re.sub(r'\s+', ' ', value or '')
        if not value:
            return
        if value.startswith(' ') and (not self._length or self._parts[-1].endswith(' ')):
            value = value[1:]
        if value:
            self._parts.append(value)
            self._length += len(value)

    def _close_anchor(self):
        if self._open:
            href, start = self._open.pop()
            self.anchors.append((start, self._length, href))

    @staticmethod
    def _hidden(attrs):
        values = dict(attrs)
        if 'hidden' in values:
            return True
        style = re.sub(r'/\*.*?\*/', ' ', str(values.get('style') or ''), flags=re.S)
        space = r'[ \t\r\n\f]*'
        return bool(re.search(
            rf'(?:^|;){space}(?:display{space}:{space}none|'
            rf'visibility{space}:{space}hidden){space}'
            rf'(?:!{space}important{space})?(?:;|$)', style, re.I))

    @classmethod
    def _suppressed(cls, tag, attrs):
        values = dict(attrs)
        marker = ' '.join((str(values.get('id') or ''),
                           str(values.get('class') or '')))
        return (tag in cls._SKIP or tag in cls._CHROME_TAG or cls._hidden(attrs)
                or (tag in ('div', 'ul', 'ol') and cls._CHROME_MARK.search(marker)))

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self._skip_tags:
            if tag == self._skip_tags[-1][0] and tag not in _VOID_TAGS:
                self._skip_tags[-1][1] += 1
            return
        values = dict(attrs)
        if self._suppressed(tag, attrs):
            if tag not in _VOID_TAGS:
                self._close_anchor()
                self._skip_tags.append([tag, 1])
            return
        if tag in self._BLOCK:
            self._space()
        if tag == 'a':
            self._close_anchor()
            self._open.append((str(values.get('href') or ''), self._length))

    def handle_startendtag(self, tag, attrs):
        # HTML only self-closes void elements; a slash does not close a div.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._skip_tags:
            if tag == self._skip_tags[-1][0]:
                self._skip_tags[-1][1] -= 1
                if self._skip_tags[-1][1] <= 0:
                    self._skip_tags.pop()
            return
        if tag == 'a':
            self._close_anchor()
        if tag in self._BLOCK:
            self._space()

    def handle_data(self, data):
        if not self._skip_tags:
            self._add(data)

    def text(self):
        return ''.join(self._parts)


class _ArticleSelector(HTMLParser):
    """Select the saved article without discarding its visibility ancestry."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._stack = []
        self._capture_depth = None
        self._parts = []
        self.found_article = False
        self.hidden_content = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        values = dict(attrs)
        ancestor_hidden = bool(self._stack and self._stack[-1][1])
        suppressed = ancestor_hidden or bool(_VisibleHTMLParser._suppressed(tag, attrs))
        if (tag == 'input' and values.get('id') == 'hid_content'
                and not ancestor_hidden and self.hidden_content is None
                and 'value' in values):
            # This field is the known CHSI rendered article, not any hidden input.
            self.hidden_content = str(values['value'] or '')
        is_article = tag == 'div' and 'yxk-news-contain' in str(
            values.get('class') or '').split()
        if is_article and not self.found_article:
            self.found_article = True
            if not suppressed:
                self._capture_depth = len(self._stack) + 1
        if self._capture_depth is not None:
            self._parts.append(self.get_starttag_text())
        if tag not in _VOID_TAGS:
            self._stack.append((tag, suppressed))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._capture_depth is not None:
            self._parts.append(f'</{tag}>')
        matching = next((i for i in range(len(self._stack) - 1, -1, -1)
                         if self._stack[i][0] == tag), None)
        if matching is None:
            return
        if self._capture_depth is not None and matching < self._capture_depth:
            self._capture_depth = None
        self._stack = self._stack[:matching]

    def _append(self, value):
        if self._capture_depth is not None:
            self._parts.append(value)

    def handle_data(self, data):
        self._append(data)

    def handle_entityref(self, name):
        self._append(f'&{name};')

    def handle_charref(self, name):
        self._append(f'&#{name};')

    def handle_comment(self, data):
        self._append(f'<!--{data}-->')

    def article(self):
        return ''.join(self._parts)


def _decode_original(path):
    raw = Path(path).read_bytes()
    for encoding in ('utf-8', 'gb18030'):
        try:
            return raw.decode(encoding).replace('\\/', '/')
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace').replace('\\/', '/')


def _article_fragment(raw):
    """Return the rendered article body, not page chrome or hidden controls.

    Chsi annual-charter pages keep the article HTML in the ``hid_content``
    input value and render it into the visible container.  Other saved pages
    expose the article directly in ``yxk-news-contain``.  The article is the
    only place where a school's own site-role declaration is meaningful;
    platform navigation/header/footer links must not become entry evidence.
    """
    selector = _ArticleSelector()
    selector.feed(raw)
    selector.close()
    if selector.hidden_content is not None:
        return selector.hidden_content, 'hid_content'
    if selector.found_article:
        return selector.article(), 'yxk_news'
    return raw, 'full_page'


def _visible_original(path):
    raw = _decode_original(path)
    fragment, _ = _article_fragment(raw)
    parser = _VisibleHTMLParser()
    parser.feed(fragment)
    parser.close()
    return parser.text(), parser.anchors


def _is_web_url(value):
    try:
        return urlsplit(str(value or '')).scheme.lower() in ('http', 'https')
    except ValueError:
        return False


def _url_key(value):
    text = str(value or '').strip().rstrip('。，；、）】》\'"')
    if not text:
        return ('', '', '', '', '')
    if '://' not in text:
        text = 'http://' + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return ('', '', '', '', '')
    if (parts.scheme.lower() not in _WEB_SCHEMES or not parts.hostname or port == 0
            or parts.username is not None or parts.password is not None):
        return ('', '', '', '', '')
    default_port = 443 if parts.scheme.lower() == 'https' else 80
    port = None if port in (None, default_port) else port
    path = _normalise_percent(parts.path or '/').rstrip('/。，；、）】》\'" ') or '/'
    return ((parts.hostname or '').lower(), port, path,
            parts.query or '', parts.fragment or '')


def _normalise_percent(value):
    """Decode unreserved bytes only; encoded path delimiters remain encoded."""
    def replace(match):
        char = chr(int(match.group(1), 16))
        return char if re.fullmatch(r'[A-Za-z0-9._~-]', char) else '%' + match.group(1).upper()
    return re.sub(r'%([0-9A-Fa-f]{2})', replace, value)


_URL_TOKEN_CHAR = re.compile(r'[A-Za-z0-9._~%/?&=#:@+!$*\-]')


_WEB_SCHEMES = frozenset({'http', 'https'})


def _url_token_boundary(text, start, end):
    """True when ``text[start:end]`` is a complete visible URL token.

    A prefix such as ``https://a.edu.cn`` inside
    ``https://a.edu.cn.attacker.example/`` or
    ``https://a.edu.cn/admissions-other`` is not a complete target: the next
    character continues the same URL token.
    """
    if start > 0 and _URL_TOKEN_CHAR.match(text[start - 1]):
        return False
    return not (end < len(text) and _URL_TOKEN_CHAR.match(text[end]))


def _url_ranges(pattern, text):
    """Trim unmatched wrapping brackets without splitting nested URL values."""
    for match in pattern.finditer(text):
        token = match.group(0)
        for opening, closing in (('(', ')'), ('[', ']')):
            while token.endswith(closing) and token.count(closing) > token.count(opening):
                token = token[:-1]
        if token:
            yield match.start(), match.start() + len(token), token


def _visible_url_tokens(text):
    """ASCII URI tokens stop before adjacent Chinese publication prose."""
    yield from _url_ranges(_VISIBLE_URL, text)


def _first_unmatched_wrapper(token):
    stack = []
    closing = frozenset(_WRAPPERS.values())
    for index, char in enumerate(token):
        if char in _WRAPPERS:
            stack.append(_WRAPPERS[char])
        elif char in closing:
            if not stack or stack[-1] != char:
                return index
            stack.pop()
    return len(token)


def _iri_envelopes(text):
    """Keep paired query wrappers but stop at an outside closing wrapper."""
    position = 0
    while True:
        match = _IRI_ENVELOPE.search(text, position)
        if match is None:
            return
        token = match.group(0)
        length = _first_unmatched_wrapper(token)
        if length:
            yield match.start(), match.start() + length, token[:length]
        # Resume after the accepted range so a later, independently wrapped
        # school IRI is not lost inside a previous platform link's prose.
        position = match.start() + max(1, length)


def _inside_other_url(position, envelopes):
    return any(start < position < end for start, end, _ in envelopes)


def _literal_span_ok(text, quote, start, end, tokens, envelopes):
    if not _url_token_boundary(text, start, end) or _inside_other_url(start, envelopes):
        return False
    if not quote.isascii():
        # An explicit IRI is accepted only whole; no guess at Unicode prose boundaries.
        return any(left == start and right == end for left, right, _ in envelopes)
    return not any(left < end < right for left, right, _ in tokens)


def _visible_quote_spans(text, quote):
    """Find complete canonical URL occurrences in visible text.

    The stored quote and the visible original may differ only by the accepted
    scheme/default-port policy, a trailing slash or unreserved percent bytes.
    Encoded delimiters stay distinct. Literal prefix matches
    are not accepted: only a whole visible URL token (or the canonical URL
    match found by ``_VISIBLE_URL``) may prove the declared target.
    """
    quote = str(quote or '')
    spans = []
    looks_like_url = ('://' in quote or quote.lower().startswith('www.')
                      or bool(re.match(r'^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/:?#]|$)',
                                       quote)))
    position = 0
    tokens = list(_visible_url_tokens(text))
    envelopes = list(_iri_envelopes(text))
    while True:
        index = text.find(quote, position)
        if index < 0:
            break
        end = index + len(quote)
        if not looks_like_url or _literal_span_ok(
                text, quote, index, end, tokens, envelopes):
            spans.append((index, end))
        position = index + 1
    key = _url_key(quote)
    if key[0] and quote.isascii():
        for start, end, token in tokens:
            if not _inside_other_url(start, envelopes) and _url_key(token) == key:
                spans.append((start, end))
    return sorted(set(spans))


def _resolve_web_target(href, base_url):
    """Resolve a visible anchor target to an absolute HTTP(S) URL.

    Relative hrefs are resolved against the saved original's URL, never
    against the declared entry.  Non-HTTP schemes (``javascript:``,
    ``mailto:``, ``data:``) and fragment-only links cannot prove a target and
    return ``None`` instead of falling back to metadata.
    """
    href = str(href or '').strip()
    if not href or href.startswith('#'):
        return None
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme and scheme not in _WEB_SCHEMES:
        return None
    base = str(base_url or '').strip()
    if not scheme and not href.startswith('//') and not _is_web_url(base):
        return None
    try:
        resolved = urljoin(base, href)
        resolved_parts = urlsplit(resolved)
    except ValueError:
        return None
    if (resolved_parts.scheme.lower() not in _WEB_SCHEMES
            or not _url_key(resolved)[0]):
        return None
    return resolved
