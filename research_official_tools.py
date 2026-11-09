"""Bounded official-entry discovery from replayable roots, never search guesses."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from collectors.documents import decode_text
from official_authority import OfficialAuthority, registered_scopes, scope_allows
import official_search
from public_navigation import extract_navigation
from public_sources import (SourceError, document_attribution, extract_saved_document,
                            fetch_navigation_resource, official_source_url, _VisibleHTML)
from research_store import ResearchError
from source_privacy import contains_personal_roster


MAX_NAVIGATION_READS = 8
MAX_UNIT_SECONDS = 45
AUTHORITY_NOTICE = '部分官网归属证据已失效；相关旧正文停止复用，原始留档与审计记录仍保留。'


class _AccountHeading(_VisibleHTML):
    """The saved CHSI article template uses one visible h2.yxk-big-title."""
    def __init__(self):
        super().__init__()
        self.article_headings, self.collecting = [], None

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if (tag == 'h2' and 'yxk-big-title' in dict(attrs).get('class', '').split()
                and self.stack and not self.stack[-1][1]):
            self.collecting = []
            self.article_headings.append(self.collecting)

    def handle_data(self, value):
        super().handle_data(value)
        if self.collecting is not None and self.stack and not self.stack[-1][1]:
            self.collecting.append(value)

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag == 'h2':self.collecting = None


def _account_heading(raw, doc):
    if (doc.get('document_heading') or not re.match(
            r'^/sch/(?:viewZszc|viewBulletin|tjzc--method-viewPub)', urlsplit(raw['url']).path)):
        return ''
    parser = _AccountHeading()
    parser.feed(decode_text(raw['raw'], raw.get('content_type', 'text/html')))
    parser.close()
    return re.sub(r'\s+', ' ', ''.join(parser.article_headings[0])).strip() if len(parser.article_headings) == 1 else ''


def initialize(tools, navigation_fetcher=None, saved_declarations=None):
    tools._official_authority = OfficialAuthority(tools.data_dir)
    tools.navigation_fetcher = navigation_fetcher or fetch_navigation_resource
    tools._official_discovery = {}
    tools._official_base_units = {key: {name: deepcopy(value) for name, value in unit.items()
                                      if name != 'official_authorities'} for key, unit in tools.units.items()}
    tools._official_unit_requests = {}
    tools._saved_declarations = saved_declarations


def current_unit(tools, identifier):
    base = tools._official_base_units[identifier]
    unit = tools._official_authority.enrich(base) if tools._official_authority.path.exists() else base
    tools.units[identifier] = unit
    return unit


def _inside(data, name):
    path = Path(name)
    choices = [path] if path.is_absolute() else [data / path, data.parent / path]
    return next((candidate.resolve() for candidate in choices
                 if candidate.resolve().is_relative_to(data)), None)


def declarations(tools, unit):
    """Only saved public originals; the authority verifier replays their identity."""
    values = tools._saved_declarations
    if values is None:
        values = []
        for name in ('runtime/official_discovery_declarations.json', 'official_discovery_declarations.json'):
            path = tools.data_dir / name
            if path.is_file() and path.stat().st_size <= 4 * 1024 * 1024:
                try:
                    values.extend(json.loads(path.read_text('utf-8')).get('documents', [])[:200])
                except (ValueError, OSError):
                    continue
        path = tools.data_dir / ('annual_registry_' + str(tools.task['reference_year']) + '.json')
        if path.is_file() and path.stat().st_size <= 8 * 1024 * 1024:
            try:
                registry = json.loads(path.read_text('utf-8'))
                item = next((row for row in registry.get('units', [])
                             if str(row.get('unit_id')) == unit['id']), {})
                ids = {row.get('document_id') for row in item.get('evidence', [])}
                values.extend({**row, 'unit_id': unit['id']} for row in registry.get('documents', [])
                              if row.get('id') in ids)
            except (ValueError, OSError):
                pass
    result = []
    for row in values or []:
        if not isinstance(row, dict) or row.get('unit_id', unit['id']) != unit['id']:
            continue
        path = _inside(tools.data_dir, str(row.get('local_path') or ''))
        if not isinstance(row.get('raw'), bytes) and (not path or not path.is_file()):
            continue
        result.append({**row, **({'local_path': str(path)} if path else {})})
        if len(result) >= 8:break
    return result


def _public(row):
    return {key: deepcopy(value) for key, value in row.items() if key in {
        'id', 'url', 'title', 'unit_id', 'unit_name', 'year', 'status', 'source_type',
        'source_role', 'knowledge_id', 'knowledge_quality', 'original_available',
        'sha256', 'checked_at', 'excerpt', 'authority_id', 'extraction', 'content_notice',
        'knowledge_current', 'usable', 'authority_current', 'notice'}}


def _account_source(row, unit):
    from public_sources import _chsi_school_ids, _chsi_unit_codes
    url = str(row.get('url') or '')
    return (urlsplit(url).hostname == 'yz.chsi.com.cn'
            and row.get('unit_id') == unit.get('id')
            and (_chsi_school_ids(url) == {str(unit.get('id'))}
                 or bool(unit.get('unit_code')) and _chsi_unit_codes(url) == {str(unit['unit_code'])}))


def _authority_view(report, units, authority):
    """Project current authority without rewriting a saved source or prior audit."""
    scoped = {identifier: authority.enrich(unit) for identifier, unit in units.items()}
    invalid = set()
    def allowed(row):
        unit = scoped.get(row.get('unit_id'))
        if not unit:
            return False
        try:
            return _account_source(row, unit) or official_source_url(str(row.get('url') or ''), unit)
        except (TypeError, ValueError):
            return False
    for discovery in report.get('official_discovery', []):
        for group in ('entry_sources', 'navigation_sources', 'document_sources', 'candidate_sources'):
            for row in discovery.get(group, []):
                if group == 'candidate_sources' and row.get('authority_current') is not False:continue
                if not allowed(row):invalid.add((row.get('unit_id'), row.get('url')))
    def source_view(row):
        if (row.get('unit_id'), row.get('url')) not in invalid:
            return deepcopy(row)
        return {**row, 'excerpt': '', 'knowledge_current': False, 'usable': False,
                'authority_current': False, 'cache_hit': False, 'notice': AUTHORITY_NOTICE}
    evidence = [source_view(row) for row in report.get('evidence', [])]
    current = {row.get('id'): row for row in evidence if row.get('id')}
    current_url = {(row.get('unit_id'), row.get('url')): row for row in evidence}
    discoveries = []
    for original in report.get('official_discovery', []):
        value = deepcopy(original)
        pending = list(value.get('candidate_sources', []))
        for group in ('entry_sources', 'navigation_sources', 'document_sources'):
            rows = []
            for old in value.get(group, []):
                row = source_view(current.get(old.get('id')) or
                    current_url.get((old.get('unit_id'), old.get('url'))) or old)
                if (row.get('unit_id'), row.get('url')) in invalid:
                    if not any(item.get('id') == row.get('id') for item in pending):
                        pending.append({**_public(row), 'status': 'ownership_needs_review'})
                else:rows.append(_public(row))
            value[group] = rows
        value['candidate_sources'] = pending
        value['entry_verified'] = bool(value['entry_sources'])
        readable = any(row.get('knowledge_quality', {}).get('state') == 'accepted'
                       and row.get('knowledge_current') is not False and row.get('usable') is not False
                       for row in value['document_sources'])
        value['stage'] = ('body_read' if readable else 'document_found' if value['document_sources'] else
            'entry_verified' if value['entry_verified'] else 'verification_required' if pending else
            'access_restricted' if any(row.get('code') in {'restricted', 'source_restricted'}
                for row in value.get('errors', [])) else 'no_lead')
        if value['stage'] != original.get('stage') or invalid:
            value['notice'] = AUTHORITY_NOTICE
        discoveries.append(value)
    projected = {**report, 'evidence': evidence, 'official_discovery': discoveries}
    return projected, projected != report, invalid


def refresh(tools):
    report = {'official_discovery': list(tools._official_discovery.values()),
              'evidence': list(tools.sources.values())}
    if not report['official_discovery']:
        return False
    current, changed, invalid = _authority_view(report, tools._official_base_units, tools._official_authority)
    if changed:
        tools.sources = {row['id']: row for row in current['evidence']}
        tools._official_discovery = {row['unit_id']: row for row in current['official_discovery']}
    if invalid:
        identifiers = {row.get('knowledge_id') for row in current['evidence']
                       if row.get('authority_current') is False and row.get('knowledge_id')}
        tools._knowledge_invalidated.update(identifiers)
        tools.algorithm, tools._algorithm_signature = {}, None
        if AUTHORITY_NOTICE not in tools.gaps:tools.gaps.append(AUTHORITY_NOTICE)
    return changed


def inherit(tools, previous):
    """Restore discovery audit records, then project today's authority immediately."""
    selected = tools.task.get('unit_ids') or []
    tools._official_discovery = {row['unit_id']: deepcopy(row)
        for row in previous.get('official_discovery', []) if isinstance(row, dict)
        and row.get('unit_id') in tools._official_base_units
        and (not selected or row['unit_id'] in selected)}
    return refresh(tools)


def refresh_saved_discovery(task, admission, data_dir):
    result = deepcopy(task)
    if not any(isinstance(result.get(key), dict) and result[key].get('official_discovery')
               for key in ('report', 'previous_report')):
        return result
    import unit_bindings
    units = {row['id']: unit_bindings.enrich(row) for row in admission.catalog_bootstrap().get('units', [])}
    authority = OfficialAuthority(data_dir)
    for key in ('report', 'previous_report'):
        report = result.get(key)
        if not isinstance(report, dict) or not report.get('official_discovery'):continue
        current, changed, invalid = _authority_view(report, units, authority)
        if invalid:
            current = {**current, 'assistant_summary': AUTHORITY_NOTICE, 'algorithm': {}, 'decisions': [],
                       'decision_intelligence': None, 'evidence_ledger': {},
                       'gaps': list(dict.fromkeys([*current.get('gaps', []), AUTHORITY_NOTICE]))}
        result[key] = current
        downgraded = any(old.get('stage') == 'body_read' and new.get('stage') != 'body_read'
                         for old, new in zip(report.get('official_discovery', []), current['official_discovery']))
        if (invalid or downgraded) and key == 'report' and result.get('status') == 'completed':result['status'] = 'partial'
    return result


def _script_scope(url):
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    if parts.scheme not in {'http', 'https'} or port not in {80, 443} or parts.username or parts.password:
        return None
    return {'scheme': parts.scheme, 'host': parts.hostname, 'port': port,
            'path': parts.path or '/', 'path_mode': 'exact', 'account': None,
            'public_ip_exception': False}


def _rank(rows, year, kind):
    unique = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get('url'):
            continue
        label = str(row.get('label') or row.get('title') or '')
        if contains_personal_roster(label):continue
        lowered = (label + ' ' + row['url']).lower()
        if re.search(r'登录|统一身份|报名系统|就业|财务|jquery|bootstrap|echarts|polyfill|vue(?:\.min)?\.js', lowered):
            continue
        score = 100 if official_search.target_matches({'title': label}, year, kind) else 0
        score += 25 if re.search(r'研究生招生|研招|招生简章|招生章程|专业目录|硕士招生', label) else 0
        score += 12 if re.search(r'研究生院|研究生教育|招生信息|硕士|招生工作', label) else 0
        score += 8 if row.get('role') in {'location', 'onclick', 'vsb_pdf', 'iframe'} else 0
        score += 6 if re.search(r'(?:action|common|header|nav|site|main|config|getAppServer|utils|index)\.(?:min\.)?js', lowered) else 0
        score += 3 if re.search(r'/yjs|/sszs|/zsxx|/zsjz|/zsgz|/yz|/info/', lowered) else 0
        score -= 20 if re.search(r'本科|博士|留学生|国际招生', label) else 0
        if row['url'] not in unique or score > unique[row['url']][0]:
            unique[row['url']] = (score, row)
    return [row for _, row in sorted(unique.values(), key=lambda pair: (-pair[0], pair[1]['url']))]


def discover(tools, args):
    if not isinstance(args, dict) or set(args) - {'unit_id', 'year', 'kind'}:
        raise ResearchError('invalid_tool_arguments', '官网发现参数不正确。')
    unit = tools._unit(args.get('unit_id'))
    year, kind = args.get('year', tools.task['reference_year']), args.get('kind', 'catalog')
    if type(year) is not int or not 2020 <= year <= tools.task['target_year'] or kind not in official_search.SEARCH_KINDS:
        raise ResearchError('invalid_scope', '请使用有效的招生年度和资料类型。')
    runner = _Discovery(tools, unit, year, kind)
    return runner.run()


class _Discovery:
    def __init__(self, tools, unit, year, kind):
        self.tools, self.unit, self.year, self.kind = tools, unit, year, kind
        self.authority = tools._official_authority
        self.saved = declarations(tools, unit)
        self.deadline = min(tools.deadline, time.monotonic() + MAX_UNIT_SECONDS)
        self.reads, self.cache, self.errors, self.visited = 0, {}, [], set()
        self.entries, self.documents, self.pending, self.navigation = {}, {}, {}, {}
        self.scripts = {}
        self.foreign = [row['name'] for row in tools.units.values() if row['id'] != unit['id']]

    def _guard(self):
        self._control()
        if (self.reads >= MAX_NAVIGATION_READS or time.monotonic() >= self.deadline
                or self.tools.usage['navigation_fetches'] >= 12):
            raise ResearchError('navigation_budget_exceeded', '本轮官网导航预算已用完；已定位入口和待补步骤已保留。', 409)

    def _control(self):
        self.tools._check_control()
        if time.monotonic() >= self.deadline:
            raise ResearchError('navigation_budget_exceeded', '本轮官网导航时间已用完。', 409)

    def _fetch(self, url, scope):
        self._control()
        if url in self.cache:return self.cache[url]
        self._guard()
        # Source identity verification receives public bytes, never a fake unit.
        from research_tools import gated_resolver
        self.reads += 1
        self.tools.usage['navigation_fetches'] += 1
        raw = self.tools.navigation_fetcher(url, None, authority_scope=scope,
            timeout=min(10, max(.1, self.deadline-time.monotonic())), max_bytes=2*1024*1024,
            resolver=gated_resolver)
        self._control()
        if (not isinstance(raw.get('raw'), bytes) or hashlib.sha256(raw['raw']).hexdigest() != raw.get('sha256')
                or not scope_allows(raw.get('url'), scope)):
            raise SourceError('invalid_original', '公开导航原件的范围或完整性未通过检查。')
        self.cache[url] = raw
        self.cache[raw['url']] = raw
        return raw

    def _error(self, url, error):
        if getattr(error, 'code', '') in {'cancelled', 'research_cancelled'}:raise error
        self.errors.append({'url': url, 'code': getattr(error, 'code', 'navigation_unavailable'),
                            'message': str(error)[:180]})

    def _verify(self, url):
        scoped = self.authority.enrich(self.unit)
        if official_source_url(url, scoped):return scoped
        result = self.authority.verify_candidate(self.unit, url, fetcher=self._fetch,
            roots=list(self.cache.values()), saved_declarations=self.saved, foreign_names=self.foreign)
        self._control()
        if result['status'] != 'verified':
            sid = self.tools._source({'url': url, 'title': '官方入口归属待核', 'unit_id': self.unit['id'],
                'unit_name': self.unit['name'], 'year': None, 'status': 'search_lead',
                'source_type': 'ownership_candidate', 'source_tier': 'official_domain_candidate', 'excerpt': ''})
            if sid:self.pending[sid] = _public(self.tools.sources[sid])
            self.errors.append({'url': url, 'code': result.get('code'), 'message': result.get('message')})
            return None
        scoped = self.authority.enrich(self.unit)
        self.tools.units[self.unit['id']] = scoped
        return scoped

    def _adopt(self, raw, unit, *, cached_account=False):
        self._control()
        from research_tools import _document_scope
        doc = extract_saved_document(raw['raw'], raw.get('content_type', 'text/html'), raw['url'])
        owner = unit
        if cached_account:
            # An archived school-account original is local proof, not authority
            # to make new requests against the suspended shared CHSI host.
            owner = {key: unit[key] for key in ('id','name','unit_code') if key in unit}
            from public_sources import _chsi_school_ids, _chsi_unit_codes
            if (urlsplit(raw['url']).hostname != 'yz.chsi.com.cn' or not (
                    _chsi_school_ids(raw['url']) == {unit['id']} or
                    bool(unit.get('unit_code')) and _chsi_unit_codes(raw['url']) == {str(unit['unit_code'])})):
                raise SourceError('wrong_unit', '留档账号的招生单位标识不一致。')
            heading = _account_heading(raw, doc)
            if heading:doc = {**doc, 'document_heading': heading}
        attribution = document_attribution(raw['url'], owner, doc['text'], doc['document_title'], doc['document_heading'])
        if attribution['status'] != 'verified':
            raise SourceError('wrong_unit', '已读取页面尚不能确认属于当前学校。')
        doc = {**doc, 'attribution_status': 'verified', 'checked_at': raw.get('checked_at')}
        scoped = doc
        if doc.get('format') != 'pdf':
            # A list of 2027 notices is not itself the 2027 charter.
            digits = str.maketrans('〇零一二三四五六七八九', '00123456789')
            scoped = {**doc, 'text': '', **{key: re.sub(r'[〇零一二三四五六七八九]{4}(?=\s*年)',
                      lambda match:match[0].translate(digits),str(doc.get(key) or ''))
                      for key in ('document_title','document_heading')}}
        title, actual = _document_scope(scoped)
        if not title:title=doc.get('document_title') or doc.get('document_heading') or self.unit['name']
        role = 'admissions_document' if actual else 'official_entry'
        self._control()
        sid = self.tools._source({'url': doc['url'], 'title': title or doc['document_title'] or self.unit['name'],
            'unit_id': self.unit['id'], 'unit_name': self.unit['name'], 'year': actual,
            'status': 'official_original_read', 'source_type': 'official_platform_cache' if cached_account else 'official_original',
            'source_role': 'official_account' if cached_account else role,
            'authority_id':next((row['id'] for row in self.authority.bindings(self.unit)
                                 if scope_allows(doc['url'],row['scope'])),None),
            'checked_at': raw.get('checked_at'), 'sha256': raw['sha256'], 'excerpt': doc['text'][:12000],
            'page_count': len(doc['pages']), 'field_verification': 'not_structured',
            'links': self.tools._link_sources(raw.get('navigation') or doc.get('links', []), unit),
            'navigation': deepcopy(raw.get('navigation', []))})
        self._control()
        self.tools._save_source(sid, document=doc)
        self._control()
        source = _public(self.tools.sources[sid])
        self.entries.setdefault(sid, source)
        if actual == self.year and official_search.target_matches({'title': title}, self.year, self.kind):
            self.documents[sid] = source
        else:
            self.navigation[sid] = source
        return sid

    def _cached_accounts(self):
        for source in self.saved:
            self._control()
            try:
                raw=source.get('raw')
                if raw is None:
                    path=_inside(self.tools.data_dir,str(source.get('local_path') or ''))
                    if not path or not path.is_file() or path.stat().st_size>2*1024*1024:continue
                    raw=path.read_bytes()
                if not isinstance(raw,bytes) or hashlib.sha256(raw).hexdigest()!=source.get('sha256'):continue
                self._adopt({**source,'raw':raw},self.unit,cached_account=True)
            except Exception as error:
                self._error(str(source.get('url') or ''),error)

    def _links(self, raw):
        html = decode_text(raw['raw'], raw.get('content_type', 'text/html'))
        links = extract_navigation(html, raw['url'], script_sources=self.scripts,
            authority_scope=next((row['scope'] for row in self.authority.bindings(self.unit)
                                  if scope_allows(raw['url'], row['scope'])), None))
        scripts = _rank([row for row in links if row.get('kind') == 'script'], self.year, self.kind)
        for row in scripts[:2]:
            if row['url'] in self.scripts:continue
            # This exact script endpoint is referenced by the current verified page.
            scope = _script_scope(row['url'])
            if scope is None:continue
            try:
                script = self._fetch(row['url'], scope)
                self.scripts[row['url']] = decode_text(script['raw'], script.get('content_type', 'text/javascript'))
            except Exception as error:self._error(row['url'], error)
        return _rank(extract_navigation(html, raw['url'], script_sources=self.scripts,
            authority_scope=next((row['scope'] for row in self.authority.bindings(self.unit)
                                  if scope_allows(raw['url'], row['scope'])), None)), self.year, self.kind)

    def _pdf(self, row, unit):
        self._guard()
        from research_tools import LIMITS, gated_resolver
        if self.tools.usage['documents'] >= LIMITS['max_documents']:
            raise ResearchError('research_budget_exceeded', '已达到本轮原文读取上限。', 409)
        self.reads += 1
        self.tools.usage['navigation_fetches'] += 1
        self.tools.usage['documents'] += 1
        doc = self.tools.source_fetcher(row['url'], unit,
            timeout=min(12, max(.1, self.deadline-time.monotonic())), max_bytes=8*1024*1024,
            cache_dir=self.tools.data_dir/'runtime/public-fetch', resolver=gated_resolver)
        self._control()
        return self._adopt(doc, unit)

    def run(self):
        self._control()
        self._cached_accounts()
        candidates = self.authority.declaration_candidates(self.unit, self.saved, self.foreign)
        queue = [{'url': url, 'label': '研究生招生官网入口', 'kind': 'page', 'depth': 0}
                 for url in self.authority.seed_urls(self.unit)]
        for row in queue:
            sid = self.tools._source({'url': row['url'], 'title': self.unit['name']+'官方入口',
                'unit_id': self.unit['id'], 'unit_name': self.unit['name'], 'year': None,
                'status': 'official_entry', 'source_type': 'official_entry', 'source_role': 'official_entry',
                'excerpt': '', 'notice': '官方归属已有登记；正文和本轮可访问状态仍须分别核验。'})
            if sid:self.entries[sid] = _public(self.tools.sources[sid])
        queue.extend({'url': row['url'], 'label': '受信声明指向的招生入口', 'kind': 'page', 'depth': 0}
                     for row in candidates)
        queue.extend({'url': row['url'], 'label': row.get('title', ''), 'kind': 'page', 'depth': 0}
                     for row in list(self.tools.sources.values()) if row.get('unit_id') == self.unit['id']
                     and row.get('source_tier') == 'official_domain_candidate')
        while queue and self.reads < MAX_NAVIGATION_READS and time.monotonic() < self.deadline:
            row = queue.pop(0)
            url = row['url'].split('#', 1)[0]
            if url in self.visited or row.get('kind') == 'script':continue
            self.visited.add(url)
            if contains_personal_roster(row.get('label', '')):continue
            try:
                unit = self._verify(url)
                if unit is None:continue
                if row.get('kind') == 'pdf' or urlsplit(url).path.lower().endswith('.pdf'):
                    self._pdf({**row, 'url': url}, unit)
                    continue
                scope = next(scope for scope in [*registered_scopes(self.unit),
                    *(entry['scope'] for entry in self.authority.bindings(self.unit))] if scope_allows(url, scope))
                raw = self._fetch(url, scope)
                if not any(kind in raw.get('content_type', '').lower() for kind in ('html', 'xhtml')):
                    continue
                self._adopt(raw, unit)
                links = self._links(raw)
                if row.get('depth', 0) < 3:
                    children = [{**child, 'depth': row.get('depth', 0)+1} for child in links
                                if child.get('kind') != 'script']
                    queue = children[:20] + queue
                if self.documents:
                    # A PDF-only publication still needs its actual attached original.
                    selected = self.documents[next(reversed(self.documents))]
                    if selected.get('knowledge_quality', {}).get('state') == 'accepted':break
            except Exception as error:
                self._error(url, error)
                if getattr(error, 'code', '') in {'cancelled', 'research_cancelled'}:raise
        entry_verified = bool(self.entries or self.authority.seed_urls(self.unit))
        readable = any(row.get('knowledge_quality', {}).get('state') == 'accepted' for row in self.documents.values())
        restricted = any(row.get('code') in {'restricted', 'source_restricted'} for row in self.errors)
        stage = ('body_read' if readable else 'document_found' if self.documents else
                 'entry_verified' if entry_verified else 'access_restricted' if restricted else
                 'verification_required' if self.pending else 'no_lead')
        result = {'unit_id': self.unit['id'], 'unit_name': self.unit['name'], 'year': self.year, 'kind': self.kind,
            'stage': stage, 'entry_verified': entry_verified, 'entry_sources': list(self.entries.values()),
            'navigation_sources': list(self.navigation.values()), 'document_sources': list(self.documents.values()),
            'candidate_sources': list(self.pending.values()), 'errors': self.errors[:12],
            'usage': {'public_reads': self.reads, 'paid_searches': 0},
            'notice': '已按官方归属、目标年度、公告和正文分阶段核对；待核、受限或历史材料不代表该校没有官网。'}
        self._control()
        self.tools._official_discovery[self.unit['id']] = deepcopy(result)
        return result
