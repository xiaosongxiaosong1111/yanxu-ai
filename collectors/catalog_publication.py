"""Replay an official HTML publication and its exact attachment link, offline."""
import hashlib
import json
from pathlib import Path
import re
import stat
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from public_sources import _VisibleHTML, document_attribution, official_source_url, rendered_source_html
from .catalogs import compact, evidence
from .documents import PROJECT_ROOT, decode_text


MAX_HTML = 8 * 1024 * 1024
MAX_ORIGINAL = 32 * 1024 * 1024
_TOPIC = re.compile(r'硕士|研究生|招生目录|专业目录|招生章程|招生简章')
_EXCLUDED = re.compile(r'留学生|国际学生|港澳台|推免(?:生)?(?:招生)?(?:专业)?目录|推荐免试.*(?:目录|简章)|单独考试|博士(?:研究生)?招生')


class _PublicationHTML(_VisibleHTML):
    def __init__(self):
        super().__init__()
        self.headings, self.links, self.active, self.anchor = [], [], [], None

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        values = dict(attrs)
        hidden = bool(self.stack and self.stack[-1][1])
        if hidden:
            return
        title_class = re.search(r'(?:^|\s)(?:sch-sspe-swtit|article-title|content-title|news-title|art-title|yxk-big-title)(?:\s|$)', values.get('class', ''), re.I)
        if tag in {'h1', 'h2', 'h3', 'h4'} or title_class:
            self.active.append({'tag': tag, 'depth': len(self.stack), 'parts': []})
        if tag == 'a' and values.get('href'):
            self.anchor = {'href': values['href'], 'parts': [], 'main': bool(self.stack and self.stack[-1][2])}

    def handle_data(self, data):
        super().handle_data(data)
        if self.stack and not self.stack[-1][1]:
            for heading in self.active:
                heading['parts'].append(data)
            if self.anchor:
                self.anchor['parts'].append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor:
            self.links.append({**self.anchor, 'label': re.sub(r'\s+', ' ', ''.join(self.anchor['parts'])).strip()})
            self.anchor = None
        completed = [value for value in self.active if value['tag'] == tag and value['depth'] == len(self.stack)]
        self.headings.extend(re.sub(r'\s+', ' ', ''.join(value['parts'])).strip() for value in completed)
        self.active = [value for value in self.active if value not in completed]
        super().handle_endtag(tag)


def _url(value, base=''):
    try:
        parsed = urlsplit(urljoin(base, value))
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            return None
        path = quote(unquote(parsed.path or '/'), safe='/%:@!$&\'()*+,;=-._~')
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ''))
    except (ValueError, TypeError, UnicodeError):
        return None


def _read(record, root, limit):
    if not isinstance(record, dict) or not isinstance(record.get('local_path'), str):
        return None, 'missing_path'
    try:
        path = Path(record['local_path'])
        base = Path(root).parent if not path.is_absolute() and path.parts and path.parts[0] == Path(root).name else Path(root)
        path = (base / path).resolve()
        if not path.is_relative_to(Path(root).resolve()):
            return None, 'outside_workspace'
        if not stat.S_ISREG(path.stat().st_mode):
            return None, 'not_regular_file'
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
        digest = hashlib.sha256(raw).hexdigest()
        if not raw or len(raw) > limit:
            return None, 'source_size:' + digest
        if digest != record.get('sha256'):
            return None, 'hash_mismatch:' + digest
        return raw, digest
    except OSError:
        return None, 'unreadable_source'


def publication_context_signature(document, unit, year, *, project_root=PROJECT_ROOT):
    """Uncached parent bytes also invalidate parsed results after restored-mtime edits."""
    parent = document.get('publication_page')
    _, fingerprint = _read(parent, project_root, MAX_HTML)
    value = {'parent': parent, 'fingerprint': fingerprint, 'attachment_id': document.get('id'), 'attachment_url': document.get('url'),
             'attachment_sha256': document.get('sha256'), 'unit': unit, 'year': year}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def whole_document_mode(text, unit, year, *, allow_project=False):
    """Only complete, unconditional sentences assert a whole-document learning mode."""
    subjects = ['我校', '本校', '学校', re.escape(unit.get('name', '')), '本目录(?:所列(?:的)?)?(?:各)?专业', '本目录', '本文件']
    if re.search(r'学院|研究院|研究所$', unit.get('name', '')):
        subjects.extend(['我院', '本院', '我所', '本所'])
    if allow_project:
        subjects.extend(['本项目', '本专业'])
    subject = '(?:' + '|'.join(value for value in subjects if value) + ')'
    mode = '(非全日制|全日制)'
    suffix = '(?:(?:非定向|定向)(?:就业)?)?(?:硕士(?:研究生|生))?'
    patterns = [subject + '(?:' + str(year) + '年)?(?:仅|只)招收' + mode + suffix,
                subject + '(?:' + str(year) + '年)?(?:招收的|录取的)?(?:硕士(?:研究生|生))?(?:招生)?(?:的)?(?:学习(?:方式|形式))?(?:均为|全部为|仅为)' + mode + suffix]
    matches = []
    for sentence in re.split(r'[。；;\n]', text):
        value = compact(sentence).strip('：: ')
        value = re.sub(r'^(?:[（(]?[一二三四五六七八九十\d]+[）).、]|第[一二三四五六七八九十\d]+条)', '', value)
        for pattern in patterns:
            match = re.fullmatch(pattern, value)
            if match:
                matches.append({'mode': match[1], 'quote': sentence.strip()})
    values = {row['mode'] for row in matches}
    return {'mode': next(iter(values)) if len(values) == 1 else None, 'claims': matches,
            'conflict': len(values) > 1}


def _label_mode(value):
    modes = set(re.findall(r'非全日制|(?<!非)全日制', value))
    return next(iter(modes)) if len(modes) == 1 and '目录' in value and not re.search(r'部分|除外|仅.*方向', value) else None


def _failed(reason):
    return {'verified': False, 'reason': reason, 'evidence': [], 'documents': [],
            'mode': None, 'mode_evidence': [], 'mode_conflict': False}


def verify_catalog_publication(document, unit, year, *, project_root=PROJECT_ROOT):
    """Metadata is a locator; actual parent heading and href establish context."""
    parent = document.get('publication_page')
    if (type(year) is not int or year != 2027 or str(document.get('unit_id')) != str(unit.get('id'))
            or document.get('year') != year or not isinstance(parent, dict)
            or str(parent.get('unit_id')) != str(unit.get('id')) or parent.get('year') not in {None, year}
            or not official_source_url(document.get('url'), unit) or not official_source_url(parent.get('url'), unit)):
        return _failed('发布页或附件的招生单位、官方归属、年度未通过核对')
    original, why = _read(document, project_root, MAX_ORIGINAL)
    if original is None:
        return _failed('附件原始文件未通过路径、大小或摘要核对：' + why)
    raw, why = _read(parent, project_root, MAX_HTML)
    if raw is None:
        return _failed('发布页原始文件未通过路径、大小或摘要核对：' + why)
    try:
        html = rendered_source_html(decode_text(raw, parent.get('content_type', '')), parent['url'])
        parser = _PublicationHTML()
        parser.feed(html)
        parser.close()
        text, title = parser.result()
    except (ValueError, TypeError, UnicodeError):
        return _failed('发布页原文不能可靠解码')
    headings = list(dict.fromkeys([*parser.headings, title]))
    dated = [heading for heading in headings if _TOPIC.search(heading) and re.search(r'(?<!\d)20\d{2}(?!\d)', heading)]
    if not dated or any(set(re.findall(r'(?<!\d)(20\d{2})(?!\d)', heading)) != {str(year)} for heading in dated):
        return _failed('发布页自身标题没有唯一明确的2027招生年度，正文链接年份不能替代标题')
    heading = dated[0]
    if _EXCLUDED.search(heading):
        return _failed('发布页标题属于其他招生范围，不能当普通统考目录')
    if document_attribution(parent['url'], unit, text, title, heading)['status'] != 'verified':
        return _failed('发布页原文标题或单位字段与所选招生单位矛盾')
    links = [link for link in parser.links if _url(link['href'], parent['url']) == _url(document['url'])]
    if parser.main:
        links = [link for link in links if link['main']]
    if not links:
        return _failed('发布页可见正文没有精确指向该附件的实际href')
    if any((years := set(re.findall(r'(?<!\d)(20\d{2})(?!\d)', link['label']))) and years != {str(year)} for link in links):
        return _failed('附件链接文字明示其他年度，不能继承2027父页年度')
    if any(_EXCLUDED.search(link['label']) for link in links):
        return _failed('附件链接明示留学生、推免或单考等不同招生范围')
    publication = {**parent, 'id': parent.get('id') or 'publication-' + parent['sha256'][:20],
                   'unit_id': str(unit['id']), 'year': year, 'kind': 'publication', 'status': 'parsed', 'title': heading}
    publication = {key: value for key, value in publication.items() if key != 'catalog_parser'}
    refs = [evidence(publication, heading, 1), *[evidence(publication, link['label'], 1) for link in links if link['label']]]
    refs = list({row['quote']: row for row in refs}.values())
    mode_claims = [{'mode': mode, 'quote': value} for value in [heading, *[link['label'] for link in links]]
                   if (mode := _label_mode(value))]
    uniform = whole_document_mode(text, unit, year)
    mode_claims.extend(uniform['claims'])
    modes = {claim['mode'] for claim in mode_claims}
    return {'verified': True, 'reason': '已重放官方发布页自身年度、单位归属与实际附件href',
            'evidence': refs, 'documents': [publication], 'mode': next(iter(modes)) if len(modes) == 1 else None,
            'mode_evidence': [evidence(publication, claim['quote'], 1) for claim in mode_claims] if len(modes) == 1 else [],
            'mode_conflict': len(modes) > 1}
