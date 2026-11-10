"""全国采用数据节点0：只读基线、缺口队列与首批10校选择。

本模块只读取公开包，不联网、不写采用文件、不创建发布索引。口径与
docs/CLAUDE_NATIONAL_DATA_RUNBOOK.md 一致：原始包、既有规则补充后、实际发布是
三种不同口径，不能互相替代。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import ijson

PROTECTED_FILES = (
    'collectors/catalog_pdf_tables.py',
    'collectors/catalog_pdf_variants.py',
    'collectors/catalog_pdf_national.py',
    'collectors/catalog_pdf_national_v2.py',
    'collectors/catalog_hit_public.py',
)
COLLECTIONS = ('documents', 'programs', 'admitted_statistics', 'score_lines')
SELECTION_RULE_VERSION = 'b001-node0-v4'
SUPPORTED_ENTRY_VERIFICATIONS = frozenset({
    'replayed_from_saved_chsi_official_site_statement',
    'saved_visible_original_role_replayed',
    'saved_declaration_and_own_primary_title_replayed',
    'saved_original_exact_role_link',
    'linked_from_saved_unit_profile',
    'replayed_visible_declaration_and_own_title',
    'homepage_name_and_admissions_navigation',
    'saved_original_exact_link',
    'linked_from_saved_annual_charter',
    'saved_original_exact_catalog_link',
})
SUPPORTED_ENTRY_RELATIONS = frozenset({
    'official_home_link',
    'declared_official_site',
    'own_admissions_publication',
    'official_home_statement',
    'official_annual_catalog_link',
})
ENTRY_ROLE_CATEGORIES = frozenset({'home', 'admissions', 'catalog', 'labeled'})
_SELF_REFERENCE = re.compile(
    r'(?:本校|我校|本单位|我单位|本所|我所|本院|我院|学校|招生单位|单位|学院)')
_STRONG_SELF_REFERENCE = re.compile(
    r'(?:本校|我校|本单位|我单位|本所|我所|本院|我院)')
_ROLE_PATTERNS = (
    # A self-referential or institutional site declaration is the strongest
    # visible role evidence.  The label must be attached to the URL/link.
    ('home', re.compile(
        r'(?:本校|我校|本单位|我单位|本所|我所|本院|我院|学校|招生单位|单位|学院)'
        r'(?:官方网站|官网|官方网址|网站|网址|主页)')),
    ('home', re.compile(
        r'(?:研究生院|研究生处|研究生学院|研究生部|研究生教育部|党委研究生工作部|'
        r'研招办|招生办公室)[^。；\n]{0,20}?'
        r'(?:官方网站|官网|官方网址|网站|网址|主页|网)')),
    ('labeled', re.compile(
        r'([一-龥A-Za-z]{2,24})(?:官方网站|官网|官方网址|主页)')),
    ('admissions', re.compile(
        r'(?:招生信息网|招生网站|招生网址|招生网|招生官网|招生资讯网|'
        r'招生信息发布网址|研究生招生信息网址|研招网址|研招网|研招在线|'
        r'研究生招生网|研究生招生信息网|研究生招生专题网站|招生专题网站)')),
    ('catalog', re.compile(r'(?:招生专业目录|专业目录|招生简章|招生章程|附件)')),
    ('home', re.compile(r'(?:网站|网址|主页|官网)')),
)
_PLATFORM_LABEL = re.compile(
    r'(?:中国研究生招生信息网|全国硕士研究生招生信息公开平台|'
    r'中国高等教育学生信息网|学信网(?!址|站|页)|研招网(?!址|站|页))')
_VISIBLE_URL = re.compile(
    r'https?://[^\s<>"\'`()\[\]{}）】。，；、”‘」』,;]+', re.I)
_ENTITY_LABEL = re.compile(
    r'[一-龥A-Za-z]{1,20}(?:大学|学院|学校|研究院|研究所|科学院)')
_GENERIC_ENTITY = frozenset({
    '学校', '学院', '大学', '研究生院', '研究生学院', '研究生部', '本校', '我校',
    '该校', '各学院', '各学校', '详见学校', '所属学院', '校内', '全校',
})
_GENERIC_ROLE_WORD = re.compile(
    r'研究生招生|研招|招生信息|招生办|研究生院|研究生工作')
_ENTITY_TAIL = re.compile(
    r'[一-龥A-Za-z]{1,20}(?:大学|学院|学校|研究院|研究所|科学院|'
    r'研究生院|研究生处|研究生部|党委研究生工作部)')
_NAME_LEAD_NOISE = frozenset(
    '的及或在将请见由可关注意登录考生查阅详获取咨询报名据了为与和等'
    '访问进入点击前往到从向对于是有无未不如若需应须参加选择填写提交'
    '打开看解更多全各本校我该通过')
SHA256_HEX = re.compile(r'^[0-9a-f]{64}$')
NOTICE = ('只读审计：未联网、未调用模型或元宝、未写采用文件、未创建发布索引；'
          '非空字段不等于原文已核验，完整资格仍以逐条条款审校为准。')
PRESENCE_KEYS = ('program_count', 'ids', 'duplicate_ids', 'units', 'tuition', 'tuition_year',
                 'tuition_full', 'tuition_other', 'duration', 'computed_total_cost',
                 'conditions_nonempty', 'requires_review', 'full_qualification')
VERIFIED_KEYS = ('program_count', 'verified_tuition', 'verified_duration',
                 'verified_full_qualification', 'verified_computed_total_cost',
                 'tuition_claim_statuses', 'duration_claim_statuses',
                 'qualification_claim_statuses')
EXPANSION_REQUIRED = ('unit_id', 'program_count', 'official_url', 'collection_status',
                      'missing_reason', 'original_urls', 'gaps', 'entry_verification')
SUMMARY_ROW_KEYS = ('unit_id', 'unit_code', 'unit_name', 'province', 'year',
                    'annual_status', 'program_count', 'coverage_program_count',
                    'raw_tuition', 'raw_duration', 'raw_full_qualification',
                    'effective_tuition', 'effective_duration',
                    'effective_full_qualification', 'verified_tuition',
                    'verified_duration', 'verified_full_qualification',
                    'verified_computed_total_cost', 'remaining_tuition',
                    'remaining_duration', 'remaining_full_qualification',
                    'catalog_status', 'collection_status', 'source_count',
                    'missing_reason', 'official_url', 'entry_trusted', 'gaps',
                    'count_mismatch')


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def original_profile(text):
    urls = []
    for raw in str(text or '').split('\n'):
        value = raw.strip()
        if value and value.lower().startswith(('http://', 'https://')) and value not in urls:
            urls.append(value)
    pdf = [url for url in urls if url.lower().split('?')[0].endswith('.pdf')]
    catalog = [url for url in urls
               if any(word in url.lower() for word in ('zsml', 'zyml', 'catalog', 'zsjz'))
               or url.lower().split('?')[0].endswith(('.pdf', '.htm', '.html'))]
    hosts = sorted({url.split('/')[2] for url in urls if url.count('/') >= 2})
    return {'url_count': len(urls), 'pdf_count': len(pdf),
            'catalog_like_count': len(catalog), 'hosts': hosts}


def _positive_number(value):
    return (type(value) in (int, float) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def _tuition_amount(tuition):
    return isinstance(tuition, dict) and _positive_number(tuition.get('amount'))


def _count(stats, name):
    """Read a verified count when present, else the legacy presence count."""
    value = stats.get('verified_' + name)
    if value is None:
        value = stats.get(name)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _host(value):
    text = str(value or '').strip()
    if not text:
        return ''
    if '://' not in text:
        text = 'http://' + text
    try:
        return (urlsplit(text).hostname or '').lower()
    except ValueError:
        return ''


def _entry_fingerprint(row):
    """Bind the persisted proof to the exact row evidence it verified."""
    evidence = row.get('official_url_evidence')
    proof = row.get('entry_verification')
    evidence = evidence if isinstance(evidence, dict) else {}
    proof = proof if isinstance(proof, dict) else {}
    payload = {
        'unit_id': str(row.get('unit_id') or ''),
        'year': str(row.get('year') or ''),
        'official_url': str(row.get('official_url') or ''),
        'evidence_url': str(evidence.get('url') or ''),
        'evidence_sha256': str(evidence.get('sha256') or ''),
        'evidence_quote': str(evidence.get('quote') or ''),
        'evidence_linked_url': str(evidence.get('linked_url') or ''),
        'evidence_relation': str(evidence.get('relation') or ''),
        'evidence_verification': str(evidence.get('verification') or ''),
        'proof_unit_id': str(proof.get('unit_id') or ''),
        'proof_year': str(proof.get('year') or ''),
        'proof_official_url': str(proof.get('official_url') or ''),
        'proof_document_id': str(proof.get('document_id') or ''),
        'proof_document_sha256': str(proof.get('document_sha256') or ''),
        'proof_file_sha256': str(proof.get('file_sha256') or ''),
        'proof_document_path': str(proof.get('document_path') or ''),
        'proof_quote_match': str(proof.get('quote_match') or ''),
        'proof_role_category': str(proof.get('role_category') or ''),
        'proof_role_evidence': str(proof.get('role_evidence') or ''),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def entry_trusted(row):
    """An official entry is usable only with a replayed saved-original proof.

    The selector consumes the proof record produced by :func:`verify_entry`.
    It does not trust a bare URL, a metadata string, or a manually set flag:
    the proof fingerprint must equal the fingerprint of the evidence actually
    stored on this row.
    """
    gaps = row.get('gaps')
    if isinstance(gaps, list) and 'missing_trusted_entry' in gaps:
        return False
    url = str(row.get('official_url') or '').strip()
    if not url or '尚未核准' in str(row.get('missing_reason') or ''):
        return False
    evidence = row.get('official_url_evidence')
    proof = row.get('entry_verification')
    if not isinstance(evidence, dict) or not isinstance(proof, dict):
        return False
    if proof.get('status') != 'verified':
        return False
    if str(evidence.get('verification') or '') not in SUPPORTED_ENTRY_VERIFICATIONS:
        return False
    if str(evidence.get('relation') or '') not in SUPPORTED_ENTRY_RELATIONS:
        return False
    if str(proof.get('unit_id') or '') != str(row.get('unit_id') or ''):
        return False
    if str(proof.get('year') or '') != str(row.get('year') or ''):
        return False
    if str(proof.get('official_url') or '').strip() != url:
        return False
    if _host(evidence.get('quote')) != _host(url) or not _host(url):
        return False
    if proof.get('role_category') not in ENTRY_ROLE_CATEGORIES:
        return False
    if not str(proof.get('role_evidence') or '').strip():
        return False
    if not SHA256_HEX.match(str(proof.get('proof_sha256') or '')):
        return False
    return _entry_fingerprint(row) == proof.get('proof_sha256')


def _document_matches(record, evidence):
    """Find the annual document entry with the exact URL and SHA declared."""
    for item in record.get('evidence') or []:
        if not isinstance(item, dict):
            continue
        if (item.get('url') == evidence.get('url')
                and str(item.get('sha256') or '').lower()
                == str(evidence.get('sha256') or '').lower()):
            return item
    return None


def _identity_matches(record, document, evidence_url):
    """The saved document must belong to this unit, not just any unit."""
    unit_id = str(record.get('unit_id') or '')
    unit_code = str(record.get('unit_code') or '')
    fields = document.get('structured_fields')
    if isinstance(fields, dict):
        if unit_code and str(fields.get('dwdm') or '') == unit_code:
            return True
        if unit_id and str(fields.get('schId') or '') == unit_id:
            return True
    try:
        query = parse_qs(urlsplit(str(evidence_url or '')).query)
    except ValueError:
        query = {}
    if unit_code and (query.get('schCode') or [''])[0] == unit_code:
        return True
    if unit_id and f'schId-{unit_id}' in str(evidence_url or ''):
        return True
    return False


def _saved_original(data_dir, unit_id, year, sha256, document_id):
    """Locate the saved original and bind its filename to the annual document."""
    folder = Path(data_dir) / 'snapshots' / 'national' / str(year) / str(unit_id)
    if not folder.is_dir():
        return None, 'saved_original_missing'
    prefix = str(sha256 or '').lower()[:16]
    candidates = sorted(path for path in folder.glob('*.html')
                        if prefix and prefix in path.name.lower())
    if not candidates:
        return None, 'saved_original_missing'
    if len(candidates) != 1:
        return None, 'ambiguous_saved_original'
    path = candidates[0]
    parts = str(document_id or '').split('-')
    if len(parts) < 3:
        return None, 'document_id_unbound'
    bound = parts[-2].lower() + '-' + parts[-1].lower()
    if not path.name.lower().startswith(bound):
        return None, 'saved_original_unbound'
    return path, ''


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
        style = str(values.get('style') or '').lower().replace(' ', '')
        # ``display:none !important`` is still a hidden declaration.
        style = style.replace('!important', '')
        return bool(re.search(
            r'(?:^|;)(?:display:none|visibility:hidden)(?:;|$)', style))

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self._skip_tags:
            if tag == self._skip_tags[-1][0]:
                self._skip_tags[-1][1] += 1
            return
        values = dict(attrs)
        marker = ' '.join((str(values.get('id') or ''),
                           str(values.get('class') or '')))
        if (tag in self._SKIP or tag in self._CHROME_TAG
                or self._hidden(attrs)
                or (tag in ('div', 'ul', 'ol') and self._CHROME_MARK.search(marker))):
            self._close_anchor()
            self._skip_tags.append([tag, 1])
            return
        if tag in self._BLOCK:
            self._space()
        if tag == 'a':
            self._close_anchor()
            self._open.append((str(values.get('href') or ''), self._length))

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self._skip_tags:
            return
        if tag in self._BLOCK:
            self._space()

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
    match = re.search(r'<input\b[^>]*\bid\s*=\s*(["\'])hid_content\1[^>]*>',
                      raw, re.I | re.S)
    if match:
        value = re.search(r'\bvalue\s*=\s*"([^"]*)"', match.group(0), re.S)
        if value is None:
            value = re.search(r"\bvalue\s*=\s*'([^']*)'", match.group(0), re.S)
        if value is not None:
            return html.unescape(value.group(1)), 'hid_content'
    match = re.search(
        r'<div\b[^>]*\bclass\s*=\s*(["\'])[^"\']*\byxk-news-contain\b[^"\']*\1[^>]*>',
        raw, re.I | re.S)
    if match:
        start = match.end()
        depth = 1
        for token in re.finditer(r'<(/?)div\b[^>]*>', raw[start:], re.I | re.S):
            depth += -1 if token.group(1) else 1
            if depth == 0:
                return raw[start:start + token.start()], 'yxk_news'
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
        return ('', '', '', '')
    if '://' not in text:
        text = 'http://' + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return ('', '', '', '')
    path = unquote(parts.path or '/').rstrip('/。，；、）】》\'" ') or '/'
    return ((parts.hostname or '').lower(), path,
            parts.query or '', parts.fragment or '')


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


def _visible_quote_spans(text, quote):
    """Find complete canonical URL occurrences in visible text.

    The stored quote and the visible original may differ only by scheme, a
    trailing slash or percent-encoded punctuation.  Literal prefix matches
    are not accepted: only a whole visible URL token (or the canonical URL
    match found by ``_VISIBLE_URL``) may prove the declared target.
    """
    quote = str(quote or '')
    spans = []
    looks_like_url = ('://' in quote or quote.lower().startswith('www.')
                      or bool(re.match(r'^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/:?#]|$)',
                                       quote)))
    position = 0
    while True:
        index = text.find(quote, position)
        if index < 0:
            break
        end = index + len(quote)
        if not looks_like_url or _url_token_boundary(text, index, end):
            spans.append((index, end))
        position = index + 1
    key = _url_key(quote)
    if key[0]:
        for match in _VISIBLE_URL.finditer(text):
            if _url_key(match.group(0)) == key:
                spans.append((match.start(), match.end()))
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
            or not resolved_parts.hostname):
        return None
    return resolved


def _longest_common_substring(left, right):
    left = str(left or '')
    right = str(right or '')
    best = ''
    for start in range(len(left)):
        for end in range(start + len(best) + 1, len(left) + 1):
            piece = left[start:end]
            if piece not in right:
                break
            best = piece
    return best


def _foreign_name_in(span, unit_name, other_names):
    """Find another known unit's exact name in a tight role span.

    School-name patterns over free text produce false positives such as
    "详见学校" or "研究生学院".  The registry already contains every unit
    name, so ownership is checked against those exact names instead.
    """
    unit_name = str(unit_name or '')
    for name in other_names or ():
        name = str(name or '')
        if not name or name == unit_name:
            continue
        if unit_name and (name in unit_name or unit_name in name):
            continue
        if name in span:
            return name
    return ''


def _foreign_label_in(label, unit_name):
    """Entity-like name in a site label that the current unit does not own.

    Applied only to the captured label of a ``labeled`` role, never to a
    free-text window: Chinese word boundaries are unavailable, so scanning
    arbitrary preceding words produces false names such as "考生关注学校".
    """
    unit_name = str(unit_name or '')
    for match in _ENTITY_LABEL.finditer(str(label or '')):
        name = match.group(0)
        if name in _GENERIC_ENTITY or name.startswith('各'):
            continue
        if unit_name and (name in unit_name or unit_name in name):
            continue
        if len(_longest_common_substring(name, unit_name)) >= 3:
            continue
        return name
    return ''


def _entity_owned(name, unit_name):
    """True when an entity-like token is the current unit or a clear variant."""
    name = str(name or '')
    unit_name = str(unit_name or '')
    if not name:
        return False
    if unit_name and (unit_name in name or name in unit_name):
        return True
    return len(_longest_common_substring(name, unit_name)) >= 3


def _foreign_entity_before(candidate, role_start, unit_name):
    """Find an explicit other-unit owner immediately before a role word.

    ``role_start`` is the offset of the role label ("网站", "研究生院网站",
    ...) in ``candidate``.  The shortest entity-like suffix directly before
    the label is considered, then leading prose words are stripped; longer
    pieces are still checked for ownership so a full unit name is not split.
    This rejects "丙大学网站" for 甲大学 while ordinary prose such as
    "考生可登录学校" is not mistaken for a school name.  The current unit
    or a variant of it is accepted.
    """
    prefix = candidate[max(0, role_start - 40):role_start]
    prefix = prefix.rstrip(' 　:：,，、;；。.!！?？-—·')
    pieces = []
    # Shortest entity-like suffix is the word directly before the role.
    for start in range(len(prefix) - 1, -1, -1):
        piece = prefix[start:]
        if _ENTITY_TAIL.fullmatch(piece):
            pieces.append(piece)
    if not pieces:
        return ''
    if any(_entity_owned(piece, unit_name) for piece in pieces):
        return ''
    tail = pieces[0]
    cut = 0
    while cut < len(tail) and tail[cut] in _NAME_LEAD_NOISE:
        cut += 1
    name = tail[cut:]
    if not _ENTITY_TAIL.fullmatch(name) or _entity_owned(name, unit_name):
        return ''
    if name in _GENERIC_ENTITY:
        return ''
    return name


def _role_evidence(text, target_start, target_end, unit_name, other_names=None):
    """Replay a visible role declaration attached to the URL/link.

    Returns ``(role, '')`` when a school-own site role is declared, or
    ``(None, reason)``.  A URL that merely appears in the article, in a
    comment/script, or under another organisation's label is not a role.
    """
    prefix = text[max(0, target_start - 120):target_start]
    cut = max(prefix.rfind('。'), prefix.rfind('\n'))
    if cut >= 0:
        prefix = prefix[cut + 1:]
    target_text = text[target_start:target_end]
    candidate = prefix + target_text
    matches = []
    for priority, (category, pattern) in enumerate(_ROLE_PATTERNS):
        for match in pattern.finditer(candidate):
            matches.append((priority, -match.end(), match.start(),
                            match.end(), match.group(0), category))
    if not matches:
        return None, 'role_declaration_missing'
    target_position = len(prefix)
    distances = [max(0, target_position - item[3]) for item in matches]
    nearest_distance = min(distances)
    nearby = [item for item, distance in zip(matches, distances)
              if distance <= nearest_distance + 2]
    _, _, start, end, role_text, category = min(
        nearby, key=lambda item: (item[0], max(0, target_position - item[3])))
    window = candidate[max(0, start - 24):end]
    tight_window = candidate[max(0, start - 16):end]
    platform = _PLATFORM_LABEL.search(tight_window)
    if platform and not _SELF_REFERENCE.search(tight_window):
        return None, 'platform_not_official_site'
    context = text[max(0, target_start - 220):min(len(text), target_end + 40)]
    # Ownership is decided near the declaration.  A unit name elsewhere in
    # the article does not make an unrelated site belong to this unit.
    own = bool(unit_name and unit_name in context)
    selfref = bool(_SELF_REFERENCE.search(window)
                   or _STRONG_SELF_REFERENCE.search(context))
    overlap = _longest_common_substring(context, unit_name or '')
    label = role_text
    if category == 'labeled':
        label_match = re.match(r'([一-龥A-Za-z]{2,24})', role_text)
        label = label_match.group(1) if label_match else role_text
        owned = (bool(_SELF_REFERENCE.search(label))
                 or bool(unit_name and unit_name in label)
                 or len(_longest_common_substring(label, unit_name or '')) >= 3
                 or bool(_GENERIC_ROLE_WORD.search(label))
                 or bool(unit_name and unit_name in context))
    elif category == 'home':
        institutional = re.match(
            r'(?:研究生院|研究生处|研究生学院|研究生部|研究生教育部|'
            r'党委研究生工作部|研招办|招生办公室)', role_text)
        owned = bool(institutional) or selfref or own or len(overlap) >= 3
    elif category in ('admissions', 'catalog'):
        # Admissions/catalog labels are generic role words of the unit's own
        # annual charter; an explicit other-unit owner is rejected below.
        owned = True
    else:
        owned = selfref or own or len(overlap) >= 3
    foreign = _foreign_name_in(window, unit_name, other_names)
    if not foreign:
        foreign = _foreign_entity_before(candidate, start, unit_name)
    if not foreign and category == 'labeled':
        foreign = _foreign_label_in(label, unit_name)
    if foreign:
        return None, 'foreign_unit_reference'
    if not owned:
        return None, 'role_ownership_missing'
    return {'category': category, 'evidence': role_text}, ''


def _relation_accepts(relation, category):
    if relation == 'own_admissions_publication':
        return category in {'home', 'admissions', 'labeled', 'catalog'}
    if category == 'catalog':
        return relation == 'official_annual_catalog_link'
    return relation in {
        'official_home_link', 'declared_official_site',
        'own_admissions_publication', 'official_home_statement'}


def _enclosing_anchor(anchors, position):
    found = None
    for start, end, href in anchors:
        if start <= position < end:
            if found is None or start >= found[0]:
                found = (start, end, href)
    return found


def _pick_entry_failure(reasons):
    for reason in ('relation_role_mismatch', 'foreign_unit_reference',
                   'platform_not_official_site', 'role_ownership_missing',
                   'unresolved_link_target', 'link_target_mismatch',
                   'role_declaration_missing'):
        if reason in reasons:
            return reason
    return 'role_declaration_missing'


def _replay_entry_role(path, evidence, official_url, unit_id, unit_name,
                       known_hosts=None, known_names=None):
    """Prove that the saved original visibly declares this school's site.

    The check is structural and semantic, not a domain allow/deny list:
    comments, scripts, page chrome and hidden controls are excluded; the
    declared URL must be visible text or the real target of a visible link;
    relative links are resolved against the saved original's URL; the role
    label must belong to this unit; and the role category must match the
    stored relation.
    """
    quote = str(evidence.get('quote') or '').strip()
    linked = str(evidence.get('linked_url') or '').strip()
    relation = str(evidence.get('relation') or '')
    base_url = str(evidence.get('url') or '')
    entry_host = _host(official_url)
    if not quote or not entry_host:
        return None, 'quote_not_in_original'
    if linked and _url_key(linked) != _url_key(quote):
        return None, 'linked_target_mismatch'
    owners = (known_hosts or {}).get(entry_host) or set()
    if owners and unit_id not in owners:
        return None, 'other_unit_host'
    text, anchors = _visible_original(path)
    if not text.strip():
        return None, 'visible_article_missing'
    expected_key = _url_key(linked or quote)
    candidates = []
    for index, end in _visible_quote_spans(text, quote):
        visible_key = _url_key(text[index:end])
        inside = _enclosing_anchor(anchors, index)
        if inside:
            resolved = _resolve_web_target(inside[2], base_url)
            if resolved is None:
                candidates.append((index, end, False, 'literal',
                                   'unresolved_link_target'))
                continue
            target_ok = (_host(resolved) == entry_host
                         and _url_key(resolved) == expected_key)
        else:
            # The complete visible URL token is the target; two metadata
            # strings agreeing with each other cannot substitute for it.
            target_ok = (visible_key == expected_key
                         and visible_key[0] == entry_host)
        candidates.append((index, end, target_ok, 'literal', 'link_target_mismatch'))
    for start, end, href in anchors:
        resolved = _resolve_web_target(href, base_url)
        if resolved is None:
            continue
        if _host(resolved) != entry_host:
            continue
        if _url_key(resolved) != expected_key:
            continue
        candidates.append((start, end, True, 'link_target', ''))
    if not candidates:
        return None, 'quote_not_in_original'
    failures = []
    for start, end, target_ok, quote_match, failure in candidates:
        if not target_ok:
            failures.append(failure)
            continue
        role, reason = _role_evidence(text, start, end, unit_name, known_names)
        if role is None:
            failures.append(reason or 'role_declaration_missing')
            continue
        if not _relation_accepts(relation, role['category']):
            failures.append('relation_role_mismatch')
            continue
        return {**role, 'quote_match': quote_match}, ''
    return None, _pick_entry_failure(failures)


def _quote_match(path, quote):
    """The ownership quote must literally occur in visible article text."""
    quote = str(quote or '').strip()
    if not quote:
        return ''
    text, _ = _visible_original(path)
    return 'literal' if _visible_quote_spans(text, quote) else ''


def verify_entry(unit, record, data_dir, known_hosts=None, known_names=None):
    """Replay one official entry against the real saved original.

    The proof is accepted only when every link in the chain holds: supported
    verification/relation metadata, exact annual document URL+SHA, unit
    identity, a saved file bound to that document, matching file bytes, a
    visible article-level role declaration for this unit, and a real link
    target that matches the claimed evidence. Any missing or mismatched link
    yields a persisted rejection reason instead of a silent pass.
    """
    unit_id = str(record.get('unit_id') or '')
    unit_name = str(unit.get('name') or unit.get('unit_name') or '').strip()
    official_url = str(unit.get('official_url') or '').strip()
    proof = {'status': 'rejected', 'reason': '', 'unit_id': unit_id,
             'year': record.get('year'), 'official_url': official_url,
             'evidence_url': '', 'evidence_sha256': '', 'document_id': '',
             'document_sha256': '', 'document_path': '', 'file_sha256': '',
             'quote_match': '', 'role_category': '', 'role_evidence': ''}
    evidence = unit.get('official_url_evidence')
    if not isinstance(evidence, dict):
        proof['reason'] = 'no_entry_evidence'
        return proof
    proof['evidence_url'] = str(evidence.get('url') or '')
    proof['evidence_sha256'] = str(evidence.get('sha256') or '').lower()
    if str(evidence.get('verification') or '') not in SUPPORTED_ENTRY_VERIFICATIONS:
        proof['reason'] = 'unsupported_verification'
        return proof
    if str(evidence.get('relation') or '') not in SUPPORTED_ENTRY_RELATIONS:
        proof['reason'] = 'unsupported_relation'
        return proof
    entry_host = _host(official_url)
    if not entry_host:
        proof['reason'] = 'missing_official_url'
        return proof
    if _host(evidence.get('quote')) != entry_host:
        proof['reason'] = 'quote_host_mismatch'
        return proof
    linked = str(evidence.get('linked_url') or '').strip()
    if linked and _host(linked) != entry_host:
        proof['reason'] = 'linked_host_mismatch'
        return proof
    if not SHA256_HEX.match(proof['evidence_sha256']):
        proof['reason'] = 'invalid_evidence_sha256'
        return proof
    document = _document_matches(record, evidence)
    if document is None:
        proof['reason'] = 'no_exact_annual_document'
        return proof
    if document.get('kind') != 'annual_master_charter':
        proof['reason'] = 'unsupported_document_kind'
        return proof
    if str(document.get('sha256') or '').lower() != proof['evidence_sha256']:
        proof['reason'] = 'document_sha_mismatch'
        return proof
    proof['document_id'] = str(document.get('document_id') or '')
    proof['document_sha256'] = proof['evidence_sha256']
    if not _identity_matches(record, document, proof['evidence_url']):
        proof['reason'] = 'unit_identity_mismatch'
        return proof
    path, reason = _saved_original(data_dir, unit_id, record.get('year'),
                                   proof['evidence_sha256'], proof['document_id'])
    if path is None:
        proof['reason'] = reason or 'saved_original_missing'
        return proof
    try:
        proof['document_path'] = (path.resolve().relative_to(Path(data_dir).resolve())
                                  .as_posix())
    except ValueError:
        proof['document_path'] = path.as_posix()
    actual = sha256_file(path)
    proof['file_sha256'] = actual
    if actual != proof['evidence_sha256']:
        proof['reason'] = 'saved_original_sha_mismatch'
        return proof
    role, reason = _replay_entry_role(
        path, evidence, official_url, unit_id, unit_name, known_hosts, known_names)
    if role is None:
        proof['reason'] = reason or 'role_declaration_missing'
        return proof
    proof['quote_match'] = role['quote_match']
    proof['role_category'] = role['category']
    proof['role_evidence'] = role['evidence']
    proof['status'] = 'verified'
    proof['reason'] = 'saved_original_replayed'
    return proof


def _official_hosts(registry):
    """Map each known official host to the registry units that claim it."""
    hosts = {}
    for unit in registry.get('units', []):
        if not isinstance(unit, dict):
            continue
        unit_id = unit.get('id')
        host = _host(unit.get('official_url'))
        if isinstance(unit_id, str) and unit_id and host:
            hosts.setdefault(host, set()).add(unit_id)
    return hosts


def verify_entries(registry, annual, data_dir):
    """Build one persisted proof record per confirmed annual unit."""
    units = {unit['id']: unit for unit in registry.get('units', [])
             if isinstance(unit, dict) and isinstance(unit.get('id'), str)}
    known_hosts = _official_hosts(registry)
    known_names = {str(unit.get('name') or '').strip()
                   for unit in registry.get('units', [])
                   if isinstance(unit, dict) and str(unit.get('name') or '').strip()}
    proofs = {}
    for record in annual.get('units', []):
        if record.get('annual_status') != 'confirmed' or type(record.get('year')) is not int:
            continue
        unit_id = record.get('unit_id')
        if not isinstance(unit_id, str) or not unit_id:
            continue
        proofs[unit_id] = verify_entry(
            units.get(unit_id, {}), record, data_dir, known_hosts, known_names)
    return proofs


def classify_gaps(row, stats):
    """Return every real gap code for one unit; a unit may have several."""
    gaps = []
    program_count = int(stats.get('program_count') or 0)
    reason = str(row.get('missing_reason') or '')
    proof = row.get('entry_verification')
    if (not str(row.get('official_url') or '').strip() or '尚未核准' in reason
            or (isinstance(proof, dict) and proof.get('status') != 'verified')):
        gaps.append('missing_trusted_entry')
    if program_count == 0:
        gaps.append('missing_catalog')
    elif row.get('catalog_status') == 'partial':
        gaps.append('partial_catalog')
    if program_count:
        for field in ('full_qualification', 'tuition', 'duration'):
            known = _count(stats, field)
            if known == 0:
                gaps.append('missing_' + field)
            elif known < program_count:
                gaps.append('partial_' + field)
    status = str(row.get('collection_status') or '')
    if status in ('needs_binding', 'truncated') or '登录或限制访问' in reason or '尚未找到可核验' in reason:
        gaps.append('original_pending')
    if status == 'collected' and program_count == 0 and '待解析' in reason:
        gaps.append('parser_pending')
    if row.get('catalog_status') == 'partial' and program_count:
        gaps.append('review_pending')
    return gaps


def _looks_like_expansion(row):
    return (int(row.get('program_count') or 0) == 0
            and str(row.get('collection_status') or '') == 'collected'
            and '待解析' in str(row.get('missing_reason') or ''))


def _remaining(row, field):
    program_count = int(row.get('program_count') or 0)
    return max(0, program_count - _count(row, field))


def _backfill_candidate(row):
    return (int(row.get('program_count') or 0) > 0
            and (_remaining(row, 'tuition') > 0 or _remaining(row, 'duration') > 0))


def _require_fields(row, fields):
    missing = [field for field in fields if field not in row]
    if missing:
        raise ValueError(
            f"queue row {row.get('unit_id')!r} missing required fields for replay: "
            + ', '.join(missing))


def select_batch(rows, thresholds=None, *, expansion=7, backfill=3, queue_sha256=None):
    """Pick a deterministic first batch from the complete persisted queue only."""
    expand, back = [], []
    for row in rows:
        if _looks_like_expansion(row):
            _require_fields(row, EXPANSION_REQUIRED)
            if not str(row.get('original_urls') or '').strip():
                raise ValueError(f"queue row {row.get('unit_id')!r} has empty original_urls")
            if not entry_trusted(row):
                continue
            profile = original_profile(row.get('original_urls') or '')
            if not profile['url_count']:
                continue
            expand.append({**row, 'role': 'expand_catalog', 'profile': profile,
                           'reason': '已保存2027官方原文但尚无采用方向；本轮先解析并核对目录全集。',
                           'next_step': '建立原件清单与目录全集对账，再按格式选择适配器'})
        elif _backfill_candidate(row):
            if not entry_trusted(row):
                continue
            back.append({**row, 'role': 'backfill_fields',
                         'reason': ('已有采用方向但仍有未核验的费用/学制方向；'
                                    '只补剩余缺口，不重复计算既有覆盖，也不改完整资格标志。'),
                         'next_step': '在既有目录校内选真实章程/简章补齐剩余方向的费用与学制逐项证据'})
    expand.sort(key=lambda row: (-row['profile']['catalog_like_count'],
                                 -row['profile']['pdf_count'],
                                 -row['profile']['url_count'], row['unit_id']))
    back.sort(key=lambda row: (_remaining(row, 'tuition') != int(row.get('program_count') or 0)
                               or _remaining(row, 'duration') != int(row.get('program_count') or 0),
                               'original_pending' in (row.get('gaps') or []),
                               -(_remaining(row, 'tuition') + _remaining(row, 'duration')),
                               row['unit_id']))
    return {'expansion': expand[:expansion], 'backfill': back[:backfill],
            'queue_sha256': queue_sha256,
            'selection_rule_version': SELECTION_RULE_VERSION}


def count_programs(programs):
    """Count structurally valid raw facts; no value implies verification."""
    total = {'program_count': 0, 'tuition': 0, 'tuition_year': 0, 'tuition_full': 0,
             'tuition_other': 0, 'duration': 0, 'conditions_nonempty': 0,
             'requires_review': 0, 'full_qualification': 0,
             'computed_total_cost': 0, 'ids': set(), 'duplicate_ids': 0,
             'years': Counter(), 'units': 0}
    per_unit = defaultdict(lambda: {'program_count': 0, 'tuition': 0, 'tuition_year': 0,
                                    'tuition_full': 0, 'duration': 0, 'full_qualification': 0,
                                    'conditions_nonempty': 0})
    seen = set()
    for program in programs:
        identifier = program.get('id')
        if identifier in seen:
            total['duplicate_ids'] += 1
        seen.add(identifier)
        unit_id = program.get('unit_id')
        row = per_unit[unit_id]
        tuition = program.get('tuition')
        duration = program.get('duration_years')
        conditions = program.get('conditions')
        conditions_present = isinstance(conditions, list) and bool(conditions)
        full = program.get('eligibility_evidence_complete') is True and conditions_present
        total['program_count'] += 1
        total['years'][program.get('year')] += 1
        row['program_count'] += 1
        if _tuition_amount(tuition):
            total['tuition'] += 1
            row['tuition'] += 1
            period = str(tuition.get('period') or '')
            if period == 'year':
                total['tuition_year'] += 1
                row['tuition_year'] += 1
            elif period in ('full', 'total'):
                total['tuition_full'] += 1
                row['tuition_full'] += 1
            else:
                total['tuition_other'] += 1
            if period in ('full', 'total') or (period == 'year' and _positive_number(duration)):
                total['computed_total_cost'] += 1
        if _positive_number(duration):
            total['duration'] += 1
            row['duration'] += 1
        if conditions_present:
            total['conditions_nonempty'] += 1
            row['conditions_nonempty'] += 1
        if program.get('requires_review') is True:
            total['requires_review'] += 1
        if full:
            total['full_qualification'] += 1
            row['full_qualification'] += 1
    total['units'] = len(per_unit)
    total['ids'] = len(seen)
    return {'totals': total, 'per_unit': dict(per_unit)}


def verified_counts(index, programs):
    """Replay each present field's evidence claim; only verified claims count."""
    total = {'program_count': 0, 'verified_tuition': 0, 'verified_duration': 0,
             'verified_full_qualification': 0, 'verified_computed_total_cost': 0,
             'tuition_claim_statuses': Counter(), 'duration_claim_statuses': Counter(),
             'qualification_claim_statuses': Counter()}
    per_unit = defaultdict(lambda: {'program_count': 0, 'verified_tuition': 0,
                                    'verified_duration': 0,
                                    'verified_full_qualification': 0,
                                    'verified_computed_total_cost': 0})
    for program in programs:
        unit_id = program.get('unit_id')
        row = per_unit[unit_id]
        row['program_count'] += 1
        total['program_count'] += 1
        tuition_status = ''
        tuition = program.get('tuition')
        if isinstance(tuition, dict):
            if not _tuition_amount(tuition):
                tuition_status = 'invalid'
            else:
                tuition_status = index._claim(
                    'tuition', '学费与计费口径', tuition, tuition.get('evidence') or [],
                    program, verified=True)['status']
                if tuition_status == 'verified' and str(tuition.get('period') or '') not in (
                        'year', 'full', 'total'):
                    tuition_status = 'invalid'
            total['tuition_claim_statuses'][tuition_status] += 1
        tuition_verified = tuition_status == 'verified'
        if tuition_verified:
            total['verified_tuition'] += 1
            row['verified_tuition'] += 1
        duration_status = ''
        duration = program.get('duration_years')
        if duration is not None:
            if not _positive_number(duration):
                duration_status = 'invalid'
            else:
                duration_status = index._claim(
                    'duration', '学制', duration, program.get('duration_evidence') or [],
                    program, verified=True)['status']
            total['duration_claim_statuses'][duration_status] += 1
        duration_verified = duration_status == 'verified'
        if duration_verified:
            total['verified_duration'] += 1
            row['verified_duration'] += 1
        conditions = program.get('conditions')
        if isinstance(conditions, list) and conditions:
            complete = (program.get('eligibility_evidence_complete') is True
                        and not program.get('requires_review'))
            refs = [item for condition in conditions if isinstance(condition, dict)
                    for item in (condition.get('evidence') or [])]
            status = index._claim('eligibility', '完整报考资格条件', {'complete': complete},
                                  refs, program, verified=complete)['status']
            total['qualification_claim_statuses'][status] += 1
            if status == 'verified':
                total['verified_full_qualification'] += 1
                row['verified_full_qualification'] += 1
        if tuition_verified and isinstance(tuition, dict):
            period = str(tuition.get('period') or '')
            if period in ('full', 'total') or (period == 'year' and duration_verified):
                total['verified_computed_total_cost'] += 1
                row['verified_computed_total_cost'] += 1
    return {'totals': total, 'per_unit': dict(per_unit)}


def stream_counts(path):
    """Stream programs.item so the raw JSON is never fully materialized."""
    with open(path, 'rb') as handle:
        programs = ijson.items(handle, 'programs.item', use_float=True)
        return count_programs(programs)


def _load_json(path):
    with open(path, encoding='utf-8-sig') as handle:
        return json.load(handle)


def effective_counts(data_dir):
    """Apply existing enrichment/safety, then separate presence from verification.

    ``tuition``/``duration`` here mean effective-present only. ``verified_*``
    counts replay the field claims through the real EvidenceIndex and are the
    only counts used for queue gaps. This pass materializes admissions.json in
    memory (the raw stream pass does not); peak memory is not bounded here.
    """
    from enrichment import apply_enrichment
    from evidence import EvidenceIndex, apply_evidence_safety
    from service_verification import SourceVerifier
    data_dir = Path(data_dir)
    registry = _load_json(data_dir / 'units.json')
    admissions = _load_json(data_dir / 'admissions.json')
    verifier = SourceVerifier(data_dir)
    enriched = apply_enrichment(registry, admissions, data_dir)
    safe = apply_evidence_safety(registry, enriched, data_dir, verifier=verifier)
    programs = safe.get('programs', [])
    suspended = sum(1 for program in programs
                    if isinstance(program, dict) and program.get('evidence_conflicts'))
    presence = count_programs(programs)
    verified = verified_counts(EvidenceIndex(registry, safe, data_dir, verifier=verifier),
                               programs)
    per_unit = {}
    for unit_id in set(presence['per_unit']) | set(verified['per_unit']):
        per_unit[unit_id] = {**presence['per_unit'].get(unit_id, {}),
                             **verified['per_unit'].get(unit_id, {})}
    totals = {**presence['totals'], **verified['totals']}
    totals['suspended_conflicts'] = suspended
    return {'totals': totals, 'per_unit': per_unit,
            'enrichment_metadata': safe.get('metadata', {}).get('enrichment', {})}


def _units(registry, annual):
    index = {unit['id']: unit for unit in registry.get('units', [])
             if isinstance(unit, dict) and isinstance(unit.get('id'), str)}
    rows = []
    for record in annual.get('units', []):
        if record.get('annual_status') != 'confirmed' or type(record.get('year')) is not int:
            continue
        unit_id = record.get('unit_id')
        unit = index.get(unit_id, {})
        rows.append({
            'unit_id': unit_id,
            'unit_code': record.get('unit_code') or unit.get('unit_code'),
            'unit_name': record.get('unit_name') or unit.get('name'),
            'province': record.get('province') or unit.get('province'),
            'year': record.get('year'),
            'annual_status': record.get('annual_status'),
            'annual_evidence_count': len(record.get('evidence') or []),
            'official_url': unit.get('official_url') or '',
            'official_url_evidence': unit.get('official_url_evidence'),
        })
    return rows


def build_queue(coverage_rows, raw_per_unit, effective_per_unit, annual_rows,
                entry_proofs=None):
    """One replayable row per confirmed annual unit with all three count layers."""
    proofs = entry_proofs or {}
    by_unit = {row['unit_id']: row for row in coverage_rows}
    queue = []
    for base in annual_rows:
        unit_id = base['unit_id']
        coverage = by_unit.get(unit_id, {})
        raw = raw_per_unit.get(unit_id, {})
        effective = effective_per_unit.get(unit_id, {})
        coverage_count = int(coverage.get('program_count') or 0)
        program_count = int(raw.get('program_count') if 'program_count' in raw
                            else coverage_count)
        row = {**base, **{key: coverage.get(key) for key in
                          ('program_count', 'fact_count', 'catalog_status', 'collection_status',
                           'source_count', 'missing_reason', 'original_urls')}}
        row['official_url'] = base.get('official_url') or coverage.get('official_url') or ''
        row['program_count'] = program_count
        row['coverage_program_count'] = coverage_count
        row['raw_tuition'] = int(raw.get('tuition') or 0)
        row['raw_duration'] = int(raw.get('duration') or 0)
        row['raw_full_qualification'] = int(raw.get('full_qualification') or 0)
        row['effective_tuition'] = int(effective.get('tuition') or 0)
        row['effective_duration'] = int(effective.get('duration') or 0)
        row['effective_full_qualification'] = int(effective.get('full_qualification') or 0)
        row['verified_tuition'] = int(effective.get('verified_tuition') or 0)
        row['verified_duration'] = int(effective.get('verified_duration') or 0)
        row['verified_full_qualification'] = int(effective.get('verified_full_qualification') or 0)
        row['verified_computed_total_cost'] = int(
            effective.get('verified_computed_total_cost') or 0)
        row['conditions_nonempty'] = int(raw.get('conditions_nonempty') or 0)
        for field in ('tuition', 'duration', 'full_qualification'):
            row['remaining_' + field] = max(0, program_count - row['verified_' + field])
        proof = dict(proofs.get(unit_id) or {
            'status': 'blocked', 'reason': 'proof_not_computed',
            'unit_id': unit_id, 'year': base.get('year'),
            'official_url': row['official_url'], 'evidence_url': '',
            'evidence_sha256': '', 'document_id': '', 'document_sha256': '',
            'document_path': '', 'file_sha256': '', 'quote_match': ''})
        proof['unit_id'] = str(proof.get('unit_id') or unit_id)
        if proof.get('year') is None:
            proof['year'] = base.get('year')
        proof['official_url'] = str(proof.get('official_url') or row['official_url'])
        row['entry_verification'] = proof
        proof['proof_sha256'] = _entry_fingerprint(row)
        row['gaps'] = classify_gaps(row, {
            'program_count': program_count,
            'verified_tuition': row['verified_tuition'],
            'verified_duration': row['verified_duration'],
            'verified_full_qualification': row['verified_full_qualification']})
        row['count_mismatch'] = program_count != coverage_count
        row['effective_count_mismatch'] = int(effective.get('program_count') or 0) != program_count
        row['entry_trusted'] = entry_trusted(row)
        queue.append(row)
    return queue


def protected_hashes(root):
    return {name: sha256_file(Path(root) / name) for name in PROTECTED_FILES}


def tool_versions(raw_pdf, bbox_pdf):
    result = {}
    for key, path in (('raw_pdftotext', raw_pdf), ('bbox_pdftotext', bbox_pdf)):
        value = {'path': str(path or '')}
        if path:
            try:
                output = subprocess.run([path, '-v'], capture_output=True, text=True, timeout=20)
                value['version'] = (output.stdout or output.stderr).splitlines()[0].strip()
            except (OSError, subprocess.SubprocessError) as error:
                value['error'] = type(error).__name__
        result[key] = value
    return result


def _summary(totals, keys):
    value = {}
    for key in keys:
        item = totals.get(key)
        value[key] = dict(item) if isinstance(item, Counter) else item
    return value


def _summary_row(row):
    return {key: row.get(key) for key in SUMMARY_ROW_KEYS}


def _git(repo_root, *args):
    try:
        output = subprocess.run(['git', *args], cwd=repo_root, capture_output=True,
                                text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ''
    return output.stdout.strip() if output.returncode == 0 else ''


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), 'utf-8', newline='\n')


def audit(data_dir, out_dir, *, raw_pdf='', bbox_pdf='', repo_root='.', expansion=7, backfill=3):
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    registry = _load_json(data_dir / 'units.json')
    annual = _load_json(data_dir / 'annual_registry_2027.json')
    with open(data_dir / 'catalog_coverage_2027.csv', encoding='utf-8-sig', newline='') as handle:
        coverage_rows = list(csv.DictReader(handle))
    raw = stream_counts(data_dir / 'admissions.json')
    effective = effective_counts(data_dir)
    annual_rows = _units(registry, annual)
    proofs = verify_entries(registry, annual, data_dir)
    queue = build_queue(coverage_rows, raw['per_unit'], effective['per_unit'], annual_rows,
                        entry_proofs=proofs)
    expansion_units = {row['unit_id'] for row in queue if _looks_like_expansion(row)}
    _write_json(out_dir / 'queue.json', queue)
    queue_sha = sha256_file(out_dir / 'queue.json')
    picks = select_batch(queue, expansion=expansion, backfill=backfill, queue_sha256=queue_sha)
    batch = {'batch_id': 'b001', 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
             'selection_rule': '完整 queue 确定性排序；不手写学校名；重放必须使用同一 queue SHA',
             'selection_rule_version': SELECTION_RULE_VERSION,
             'queue_sha256': queue_sha,
             'expansion': picks['expansion'], 'backfill': picks['backfill'],
             'notice': NOTICE}
    _write_json(out_dir / 'batch.json', batch)
    summary = {'replayable': False,
               'note': ('摘要仅供阅读；不含 original_urls/official_url_evidence 等选择必需字段，'
                        '不能用来复算批次。重放请使用 queue.json 及其 SHA。'),
               'queue_sha256': queue_sha,
               'rows': [_summary_row(row) for row in queue]}
    _write_json(out_dir / 'queue-summary.json', summary)
    artifacts = {name: sha256_file(out_dir / name)
                 for name in ('queue.json', 'queue-summary.json', 'batch.json')}
    inputs = {
        'admissions_sha256': sha256_file(data_dir / 'admissions.json'),
        'units_sha256': sha256_file(data_dir / 'units.json'),
        'annual_registry_sha256': sha256_file(data_dir / 'annual_registry_2027.json'),
        'coverage_csv_sha256': sha256_file(data_dir / 'catalog_coverage_2027.csv'),
    }
    baseline = {
        'generated_at': batch['generated_at'],
        'git': {'commit': _git(repo_root, 'rev-parse', 'HEAD'),
                'branch': _git(repo_root, 'rev-parse', '--abbrev-ref', 'HEAD'),
                'tracked_dirty': bool(_git(repo_root, 'status', '--porcelain',
                                           '--untracked-files=no'))},
        'module_sha256': sha256_file(Path(__file__)),
        'inputs': inputs,
        'artifacts': artifacts,
        'package': {'catalog_data_zip_sha256': sha256_file(data_dir / 'bundles' / 'catalog-data.zip'),
                    'manifest_sha256': sha256_file(data_dir / 'bundles' / 'manifest.json')},
        'protected_sha256': protected_hashes(Path(repo_root)),
        'tools': tool_versions(raw_pdf, bbox_pdf),
        'annual': {key: annual.get('metadata', {}).get(key) for key in
                   ('year', 'official_total', 'indexed_records', 'confirmed_units', 'not_listed_index_records')},
        'annual_rows': len(annual_rows),
        'coverage_rows': len(coverage_rows),
        'raw_present': _summary(raw['totals'], PRESENCE_KEYS),
        'effective_present': _summary(effective['totals'], PRESENCE_KEYS),
        'verified': _summary(effective['totals'], VERIFIED_KEYS),
        'effective_extra': {'enrichment': effective.get('enrichment_metadata', {}),
                            'suspended_conflicts': effective['totals'].get('suspended_conflicts', 0)},
        'catalog_status': dict(Counter(row.get('catalog_status') for row in coverage_rows)),
        'collection_status': dict(Counter(row.get('collection_status') for row in coverage_rows)),
        'queue': {'count': len(queue), 'sha256': queue_sha, 'replayable': True,
                  'summary_path': 'queue-summary.json', 'summary_replayable': False},
        'queue_gap_counts': dict(Counter(gap for row in queue for gap in row['gaps'])),
        'expansion_pool': {
            'candidates': sum(1 for row in queue if _looks_like_expansion(row)),
            'trusted': sum(1 for row in queue if _looks_like_expansion(row)
                           and row.get('entry_trusted')),
            'untrusted': sum(1 for row in queue if _looks_like_expansion(row)
                             and not row.get('entry_trusted')),
        },
        'entry_proof': {
            'status': dict(Counter(proof.get('status') for proof in proofs.values())),
            'reasons': dict(Counter(proof.get('reason') for proof in proofs.values())),
            'expansion_reasons': dict(Counter(
                proof.get('reason') for unit_id, proof in proofs.items()
                if unit_id in expansion_units)),
        },
        'queue_count': len(queue),
        'queue_count_mismatch': sum(1 for row in queue if row['count_mismatch']),
        'effective_count_mismatch': sum(1 for row in queue if row['effective_count_mismatch']),
        'notice': NOTICE,
    }
    _write_json(out_dir / 'baseline.json', baseline)
    print(json.dumps({'out_dir': str(out_dir), 'raw_present': baseline['raw_present'],
                      'effective_present': baseline['effective_present'],
                      'verified': baseline['verified'],
                      'batch': {'expansion': [row['unit_id'] for row in batch['expansion']],
                                'backfill': [row['unit_id'] for row in batch['backfill']]}},
                     ensure_ascii=False))
    return baseline


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default='data')
    parser.add_argument('--out', default='data/batch_runs/b001')
    parser.add_argument('--raw-pdf', default='')
    parser.add_argument('--bbox-pdf', default='')
    parser.add_argument('--repo-root', default='.')
    args = parser.parse_args(argv)
    audit(args.data_dir, args.out, raw_pdf=args.raw_pdf, bbox_pdf=args.bbox_pdf,
          repo_root=args.repo_root)
    return 0


if __name__ == '__main__':
    sys.exit(main())
