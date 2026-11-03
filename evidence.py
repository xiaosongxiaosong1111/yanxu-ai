"""Field-level original evidence, explicit provenance chains and safe conflicts."""
from copy import deepcopy
import hashlib
import re
from urllib.parse import urlsplit
from urllib.parse import parse_qs

from collectors.documents import decode_text
from public_sources import _VisibleHTML, document_attribution, official_source_url, rendered_source_html
from source_proof import _direction_scope
from service_verification import CATALOG_ADAPTERS, PUBLIC_CATALOG_ADAPTERS, SourceVerifier, canonical, plain_text


KINDS = ('catalog', 'fees', 'duration', 'charter', 'notice', 'score', 'retest', 'transfer',
         'admission_results', 'publication', 'enrollment', 'change', 'catalog_context')
IDENTITY = ('id', 'unit_id', 'unit_name', 'year', 'department_code', 'department', 'code',
            'name', 'direction', 'study_mode')
SCOPE = ('unit_id', 'year', 'department', 'code', 'direction', 'study_mode')
STAT_FIELDS = ('sample_count', 'initial', 'retest', 'weighted_final', 'subject_scores',
               'plan_type', 'admission_source', 'study_mode', 'degree_type', 'source_table_complete')
NOTICE = '核对的是已保存官方原文，不代表刚刚访问成功；参考年度、适用范围与缺失字段分别显示。'
REFERENCE_TYPES = {'exact_quote', 'reference_link', 'structured_fields'}
PUBLIC_FORM_PARSERS = PUBLIC_CATALOG_ADAPTERS
PUBLIC_FORM_PROFILES = {'buaa', 'ezui', 'zju', 'scu', 'szu', 'aspx_stu', 'aspx_scut', 'ucas', 'nuaa'}


def _verified_get(document, unit, year, verifier):
    if (not isinstance(document, dict) or document.get('request_method') != 'GET'
            or document.get('unit_id') != unit['id'] or document.get('year') != year
            or not official_source_url(document.get('url'), unit)):
        return None
    source = verifier.read(document)
    if not source:
        return None
    try:
        raw = source[0].read_bytes()
    except OSError:
        return None
    if hashlib.sha256(raw).hexdigest() != document.get('sha256') or raw.lstrip().startswith((b'{', b'[')):
        return None
    return {'detail_url': document['url'], 'detail_url_kind': 'query_page',
            'detail_document_id': document['id'], 'detail_sha256': document['sha256']}


def _form_context_ids(context, documents):
    profile = context.get('profile')
    keys = {'buaa': ('entry_document_id', 'department_options_id', 'major_options_id', 'direction_options_id'),
            'ezui': ('entry_document_id', 'listing_document_id', 'exam_page_id'),
            'zju': ('entry_document_id', 'annual_check_id', 'interface_script_id', 'faculty_options_id'),
            'ucas': ('entry_document_id', 'listing_document_id'),
            'nuaa': ('entry_document_id', 'controller_document_id', 'publication_document_id'),
            'scu': ('entry_document_id',), 'szu': ('entry_document_id', 'index_document_id'),
            'aspx_stu': ('entry_document_id', 'listing_document_id'),
            'aspx_scut': ('entry_document_id', 'listing_document_id')}.get(profile, ())
    identities = {context.get(key) for key in keys if isinstance(context.get(key), str)}
    if profile == 'ucas':
        identities |= {identity for identity in context.get('index_document_ids', []) if isinstance(identity,str)}
    if profile == 'ezui':
        identities |= {identity for identity in context.get('dictionary_document_ids', [])
                       if isinstance(identity, str) and parse_qs(documents.get(identity, {}).get('request_body', ''))
                       == {'key': ['select_dict_XXFS']}}
    if profile in {'aspx_stu', 'aspx_scut'}:
        previous = context.get('listing_document_id')
        seen = set()
        while isinstance(previous, str) and previous in documents and previous not in seen and len(seen) < 4096:
            seen = seen | {previous}
            previous = (documents[previous].get('public_aspx_page') or {}).get('previous_document_id')
        identities = identities | seen
    return identities


def public_form_source_links(registry, admissions, verifier=None):
    """Derive GET reproductions only from a replayed public request/context chain."""
    if verifier is None:
        return {}
    units = {row['id']: row for row in registry.get('units', [])}
    documents = {row['id']: row for row in admissions.get('documents', [])}
    links, checked = {}, set()
    for program in admissions.get('programs', []):
        primary = documents.get(program.get('catalog_document_id'), {})
        identity = primary.get('id')
        if primary.get('catalog_parser') not in PUBLIC_FORM_PARSERS or identity in checked:
            continue
        unit = units.get(program.get('unit_id'))
        context = primary.get('public_form_context') or {}
        if not unit or not verifier.program(program, documents, unit):
            continue
        checked.add(identity)
        entry = _verified_get(documents.get(context.get('entry_document_id')), unit, program['year'], verifier)
        if entry is None:
            continue
        for source_id in _form_context_ids(context, documents):
            links.setdefault(source_id, {**entry, 'public_form_profile': context['profile']})
        profile = context.get('profile')
        target = (primary if profile in {'scu', 'aspx_stu', 'aspx_scut', 'ucas'}
                  else documents.get(context.get('exam_page_id')) if profile == 'ezui'
                  else documents.get(context.get('entry_document_id')))
        detail = _verified_get(target, unit, program['year'], verifier)
        if detail:
            links[identity] = {**detail, 'public_form_profile': context['profile']}
    for primary in documents.values():
        related = primary.get('learning_mode_document')
        if primary.get('catalog_parser') not in {'html_national_v1', 'html_south_v1'} or not isinstance(related, dict):
            continue
        target = documents.get(related.get('id'), {})
        fields = ('id', 'unit_id', 'year', 'url', 'sha256', 'local_path')
        if target.get('kind') != 'catalog_context' or any(target.get(key) != related.get(key) for key in fields):
            continue
        unit = units.get(primary.get('unit_id'))
        candidates = [program for program in admissions.get('programs', [])
                      if program.get('unit_id') == primary.get('unit_id') and program.get('year') == primary.get('year')
                      and any(ref.get('document_id') == primary['id'] for ref in _items(program.get('evidence')))]
        if unit and any(verifier.program(program, documents, unit) for program in candidates):
            detail = _verified_get(target, unit, primary['year'], verifier) or {}
            links.setdefault(target['id'], {**detail, 'public_form_profile': 'catalog_mode_context'})
    return links


def exam_unresolved_reason(program):
    issues=program.get('subject_identity_issues')
    if isinstance(issues,list) and issues:
        reasons=[item['reason'].rstrip('。；; ') for item in issues if isinstance(item,dict) and isinstance(item.get('reason'),str)]
        return '原文科目代码与名称存在冲突：'+('；'.join(dict.fromkeys(reasons)) or '对应关系尚未核准')+'。保留原值，核实前不参与换科、路线与最小调整计算。'
    if program.get('exam_matching_supported') is False:
        return '原文科目套组或科目身份尚不能完整核对；保留原始组合，暂不参与换科、路线与最小调整计算。'
    return None


def _items(value):
    return [row for row in (value if isinstance(value, list) else [value]) if isinstance(row, dict)]


def _group(document):
    digest = document.get('sha256')
    return digest if isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest) else str(document.get('id', 'unknown'))


def _scope_matches(scope, context):
    return not scope or isinstance(scope, dict) and all(
        key not in SCOPE or value == context.get(key) for key, value in scope.items())


def _department(value):
    return re.sub(r'^\d+(?=[\u4e00-\u9fff])', '', canonical(value))


def _historical_match(program, row):
    return (row.get('unit_id') == program.get('unit_id')
            and _department(row.get('department')) == _department(program.get('department'))
            and row.get('study_mode') == program.get('study_mode')
            and (row.get('program_code') == program.get('code') if row.get('program_code')
                 else canonical(row.get('program_name')) == canonical(program.get('name'))))


def _scope_contradiction(quote, context):
    mode = '非全日制' if '非全日制' in quote else '全日制' if '全日制' in quote else None
    if mode and context.get('study_mode') != mode:
        return True
    compact = quote.replace(canonical(context.get('unit_name') or ''), '') if context.get('unit_name') else quote
    compact = compact.replace('非全日制', '').replace('全日制', '')
    departments = re.findall(r'[\u4e00-\u9fff]{2,40}?学院', compact)
    if any(_department(context.get('department')) not in department for department in departments):
        return True
    direction_text = quote
    for key in ('code', 'department', 'department_code', 'name'):
        if context.get(key):
            direction_text = direction_text.replace(canonical(context[key]), '')
    direction_text = re.sub(r'[〔【\[(]\d{4}[〕】\])]\d+号', '', direction_text)
    if _direction_scope(context, direction_text) is None:
        return True
    return False


def _semantic_scope(quote, context, combined):
    if _scope_contradiction(quote, context):
        return False
    if re.search(r'学硕|学术学位', quote):
        return context.get('degree_type') == '学术学位'
    names = [context.get('name'), context.get('program_name'), context.get('code')]
    direction = re.sub(r'^\d+\s*', '', str(context.get('direction') or ''))
    return any(value and canonical(value) in quote for value in [*names, direction])


def _score_applicability(program, row):
    if row.get('unit_id') != program.get('unit_id'):
        return 'excluded'
    code = str(program.get('code') or '')
    if row.get('program_code') and row['program_code'] != code:
        return 'excluded'
    if row.get('discipline_code') and not code.startswith(str(row['discipline_code'])):
        return 'excluded'
    prefixes = row.get('code_prefixes')
    if isinstance(prefixes, list) and not any(isinstance(prefix, str) and code.startswith(prefix) for prefix in prefixes):
        return 'excluded'
    if row.get('department') and _department(row['department']) != _department(program.get('department')):
        return 'excluded'
    if row.get('direction') and canonical(row['direction']) != canonical(program.get('direction')):
        return 'excluded'
    unknown = False
    for key in ('degree_type', 'study_mode', 'plan_type'):
        expected, actual = row.get(key), program.get(key)
        if expected in (None, '', 'all', 'not_specified'):
            continue
        if actual in (None, '', 'unknown', 'unmarked', 'not_specified'):
            unknown = True
        elif expected != actual:
            return 'excluded'
    scoped = row.get('program_code') or row.get('discipline_code') or prefixes or row.get('kind') in {'national', 'university'}
    return 'unknown' if unknown or not scoped else 'applicable'


def _score_numbers_match(row, refs):
    quotes = [plain_text(ref['quote']) for ref in refs if ref['verification'] == 'exact_quote' and ref['relation'] == 'supports']
    order = row.get('column_order')
    if order is not None:
        fields = {'total', 'politics', 'foreign_language', 'business1', 'business2'}
        headers = canonical(' '.join(ref['quote'] for ref in refs if ref['verification'] == 'exact_quote' and ref.get('role') == 'column_header'))
        if not isinstance(order, list) or len(order) != 5 or set(order) != fields or not all(re.search(label, headers) for label in ('政治', '外语', '业务[课科][一1]', '业务[课科][二2]', '总分')):
            return False
    else:
        order = ('total', 'politics', 'foreign_language', 'business1', 'business2')
    values = [row.get(field) for field in order]
    values = [value for value in values if type(value) in (int, float)]
    if not values:
        return False
    expected = [str(int(value)) if float(value).is_integer() else str(value) for value in values]
    for quote in quotes:
        if row.get('source_scope_name') and not canonical(quote).startswith(canonical(row['source_scope_name'])):
            continue
        numbers = re.findall(r'(?<![\d.])\d+(?:\.\d+)?(?![\d.])', quote)
        if numbers[-len(expected):] == expected:
            return True
    return False


def _quoted_scope(item, context, quote):
    explicit = item.get('scope') if isinstance(item.get('scope'), dict) else {}
    specific = bool(explicit.get('direction') and explicit.get('direction') == context.get('direction'))
    name = context.get('name') or context.get('program_name')
    program_specific = bool(name and canonical(name) in canonical(quote))
    scope = {'unit_id': context.get('unit_id'), 'year': context.get('year'),
             'level': 'direction' if specific else 'program' if program_specific else 'unit',
             'direction_specific': specific}
    if specific:
        scope['direction'] = explicit['direction']
    return scope


class EvidenceIndex:
    def __init__(self, registry, admissions, root, verifier=None):
        self.admissions = admissions
        self.units = {row['id']: row for row in registry.get('units', []) if isinstance(row.get('id'), str)}
        self.documents = {row['id']: row for row in admissions.get('documents', []) if isinstance(row.get('id'), str)}
        for row in list(self.documents.values()):
            publication = row.get('publication_page')
            if isinstance(publication, dict):
                identifier = 'publication:' + _group(publication)
                self.documents.setdefault(identifier, {**publication, 'id': identifier, 'title': row.get('title', '') + ' · 发布页面',
                    'unit_id': row.get('unit_id'), 'year': row.get('year'), 'kind': 'publication', 'status': 'parsed',
                    'checked_at': row.get('checked_at'), 'published_at': row.get('published_at')})
        self.verifier = verifier or SourceVerifier(root)
        self._sources = {}
        self._catalog_rows = {}
        self._catalog_verifications = {}
        self._public_form_links = None

    def _source(self, identifier):
        if identifier in self._sources:
            return self._sources[identifier]
        document = self.documents.get(identifier, {})
        unit = self.units.get(document.get('unit_id'), {}) if isinstance(document.get('unit_id'), str) else {}
        chsi = document.get('catalog_parser') == 'chsi_detail_v1'
        declared_form = (document.get('catalog_parser') in PUBLIC_FORM_PARSERS
                         or document.get('public_form_profile') in PUBLIC_FORM_PROFILES)
        if (declared_form or document.get('kind') == 'catalog_context') and self._public_form_links is None:
            self._public_form_links = public_form_source_links({'units': list(self.units.values())},
                {**self.admissions, 'documents': list(self.documents.values())}, self.verifier)
        form = declared_form or identifier in (self._public_form_links or {})
        allowed = self.verifier.document_allowed(document, unit, KINDS) if chsi or form else official_source_url(document.get('url'), unit)
        if document.get('kind') == 'catalog_context':
            allowed = identifier in (self._public_form_links or {}) and official_source_url(document.get('url'), unit)
        safe_url = document.get('url') if allowed else ''
        valid_types = all(isinstance(document.get(key, ''), str) for key in ('kind', 'status', 'content_type'))
        source = self.verifier.read(document) if valid_types and safe_url else None
        valid = bool(source)
        structured = chsi or bool(form and source and (source[1].lstrip().startswith((b'[', b'{'))
                                  or document.get('catalog_parser')=='nuaa_public_v1'))
        heading, text = '', ''
        if valid and not structured:
            if source[1].startswith(b'%PDF-'):
                pages = self.verifier.pages(document, unit, KINDS)
                heading = next((line for line in (pages[0].splitlines() if pages else []) if line.strip()), '')
            else:
                try:
                    if document.get('catalog_parser') in {'office_table_v1', 'office_national_v1', 'office_structured_v1'}:
                        from collectors.catalog_office import extract_office_html
                        raw = extract_office_html(source[1])
                    else:
                        raw = rendered_source_html(decode_text(source[1], document.get('content_type', '')), document.get('url'))
                except (ValueError, OSError, ImportError):
                    valid, raw = False, ''
                parser = _VisibleHTML()
                parser.feed(raw)
                text, title = parser.result()
                heading = ''.join(parser.heading).strip() or title or next((line for line in text.splitlines() if line.strip()), '')
            years = set(re.findall(r'(?<!\d)(20\d{2})(?:年|级)', heading))
            if years and years != {str(document.get('year'))}:
                valid = False
        if valid and not structured and urlsplit(safe_url).hostname == 'yz.chsi.com.cn':
            valid = document_attribution(safe_url, unit, text, document_heading=heading)['status'] == 'verified'
        value = {key: document.get(key) for key in ('id', 'title', 'unit_id', 'year', 'kind', 'published_at', 'checked_at', 'sha256')}
        value = {**value, 'url': safe_url, 'publisher': unit.get('name', '发布单位待核验'),
                 'unit_name': unit.get('name', ''), 'verification': 'snapshot_verified' if valid else 'invalid_source',
                 'source_group': _group(document), 'version_state': document.get('version_state', 'current'),
                'catalog_active': document.get('catalog_active', True), 'superseded_by': document.get('superseded_by'),
                'aliases': [], 'related_program_ids': []}
        if chsi:
            value = {**value, 'original_format':'structured_fields', 'detail_url':safe_url}
        elif form:
            detail = self._public_form_links.get(identifier, {})
            method = document.get('request_method', '')
            value = {**value, **detail, 'detail_url': detail.get('detail_url', ''),
                     'request_method': method if method in {'GET', 'POST'} else '', 'request_url': safe_url,
                     **({'original_format': 'structured_fields'} if structured else {})}
        self._sources[identifier] = value
        return value

    def _ref(self, item, context, relation='supports'):
        if relation == 'supports' and item.get('relation') in {'supports', 'applicability', 'context'}:
            relation = item['relation']
        identifier = item.get('document_id')
        document = self.documents.get(identifier, {}) if isinstance(identifier, str) else {}
        source = self._source(identifier) if isinstance(identifier, str) else {}
        correct = (source.get('verification') == 'snapshot_verified'
                   and document.get('unit_id') == context.get('unit_id') and document.get('year') == context.get('year')
                   and document.get('url') == item.get('url') and _scope_matches(item.get('scope'), context))
        if item.get('kind') == 'structured_fields':
            return self._structured_ref(item, context, document, source, relation, correct)
        quote, page = item.get('quote'), item.get('page')
        pages = self.verifier.pages(document, self.units[context['unit_id']], KINDS) if correct else []
        if (correct and document.get('kind') == 'catalog_context' and document.get('status') == 'fetched'
                and identifier in (self._public_form_links or {})):
            # The primary parser already replayed this binding; download status alone is no proof.
            raw = self.verifier.read(document)
            pages = self.verifier._source_pages(document, raw) if raw else []
        candidate = pages
        if page is not None:
            candidate = pages[page - 1:page] if type(page) is int and 1 <= page <= len(pages) else []
        exact = isinstance(quote, str) and bool(canonical(quote)) and any(canonical(plain_text(quote)) in canonical(text) for text in candidate)
        verification = 'exact_quote' if correct and exact else 'unavailable' if correct else 'invalid_source'
        token = repr((source.get('source_group'), page, quote if exact else '', relation))
        return {**source, 'id': 'evidence-' + hashlib.sha256(token.encode()).hexdigest()[:20],
                'document_id': identifier if isinstance(identifier, str) else None, 'relation': relation,
                'page': page if type(page) is int and page > 0 else None,
                'quote': quote if correct and exact else '', 'verification': verification,
                'role': item.get('role') if isinstance(item.get('role'), str) else None,
                'scope': _quoted_scope(item, context, quote if exact else ''),
                'note': '已保存原文逐字定位。' if correct and exact else '对应原文、年度、范围或页码未通过核对。'}

    def _structured_ref(self, item, context, document, source, relation, correct):
        verified = bool(correct and self.verifier.structured_evidence(item, context, self.documents,
                                                                     self.units[context['unit_id']]))
        path = item.get('field_path') if verified else None
        fields = deepcopy(item['structured_fields']) if verified else None
        token = repr((source.get('source_group'), path, fields, relation))
        direction = bool(context.get('direction'))
        if path == 'detail.labels.拟招生人数':
            direction = (context.get('planned_enrollment') or {}).get('scope') == 'direction'
        scope = {'unit_id':context.get('unit_id'), 'year':context.get('year'),
                 'level':'direction' if direction else 'program', 'direction_specific':direction}
        return {**source, 'id':'evidence-'+hashlib.sha256(token.encode()).hexdigest()[:20],
                'document_id':document.get('id'), 'relation':relation, 'kind':'structured_fields',
                'page':None, 'quote':'', 'field_path':path, 'structured_fields':fields,
                'detail_url':source.get('detail_url', '') if verified else '', 'scope':scope,
                'verification':'structured_fields' if verified else 'unavailable' if correct else 'invalid_source',
                'note':'已核对官方页面的原始结构化字段；字段路径与原始值可复核，不作为正文逐字引语。' if verified else
                       '结构化字段的原文、公开访问链、所属方向或字段路径未通过核对。'}

    def _references(self, values, context):
        result = []
        for item in _items(values):
            result.append(self._ref(item, context))
            document = self.documents.get(item.get('document_id'), {}) if isinstance(item.get('document_id'), str) else {}
            applicability = document.get('applicability_evidence')
            if isinstance(applicability, dict):
                result.append(self._ref(applicability, context, 'applicability'))
                publication = document.get('publication_page')
                if isinstance(publication, dict):
                    identifier = 'publication:' + _group(publication)
                    source = self._source(identifier)
                    original = self.verifier.read(publication)
                    linked = source.get('verification') == 'snapshot_verified' and original and isinstance(document.get('url'), str) and urlsplit(document['url']).path in decode_text(original[1], publication.get('content_type', ''))
                    result.append({**source, 'id': identifier, 'document_id': identifier, 'relation': 'context',
                                   'page': None, 'quote': '', 'verification': 'reference_link' if linked else 'unavailable',
                                   'note': '适用章程引用该发布页，已保存页面包含收费附件链接。' if linked else '发布页的引用关系未完成核对。'})
        return list({(row.get('source_group'), row['page'], row['quote'], row['relation'],
                      row.get('field_path'), repr(row.get('structured_fields'))): row for row in result}.values())

    def _catalog_replay(self, document, unit):
        identifier = document['id']
        if identifier in self._catalog_rows:
            return self._catalog_rows[identifier]
        source = self.verifier.read(document)
        rows = []
        if (source and document.get('catalog_active', True)
                and self._source(identifier).get('verification') == 'snapshot_verified'):
            relevant = [row for row in self.documents.values() if row.get('unit_id') == unit['id']
                        and row.get('year') == document['year'] and row.get('kind') == 'catalog'
                        and row.get('catalog_active', True)]
            tree = self.verifier._tree_scope(unit, document['year'], relevant) if document.get('catalog_parser') == 'zsml_table' else ({}, set())
            parsed = self.verifier._source_programs(document, unit, document['year'], tree[0], self.documents) if tree is not None else None
            if parsed is not None:
                rows = parsed['programs']
        self._catalog_rows[identifier] = rows
        return rows

    def _verified_catalog(self, context):
        fields = (*IDENTITY, 'department_scope', 'direction_scope', 'code_level', 'catalog_document_id',
                  'subjects', 'evidence', 'exam_evidence', 'planned_enrollment', 'exam_combinations',
                  'exam_matching_supported', 'chsi_direction_id', 'degree_type', 'notes',
                  'catalog_publishable', 'not_publishable_without_detail')
        key = repr(tuple(context.get(field) for field in fields))
        if key not in self._catalog_verifications:
            unit = self.units.get(context.get('unit_id'))
            verified = bool(unit and self.verifier.program(context, self.documents, unit))
            self._catalog_verifications[key] = verified
        return self._catalog_verifications[key]

    def _catalog_field(self, field, refs, period, context):
        values, accepted = set(), set()
        unit = self.units.get(context.get('unit_id'))
        if not unit:
            return values, accepted
        for identifier in dict.fromkeys(ref.get('document_id') for ref in refs):
            document = self.documents.get(identifier, {})
            if document.get('catalog_parser') not in CATALOG_ADAPTERS or document.get('catalog_parser') in {'zsml_tree', 'catalog_index_v1'}:
                continue
            if not self._verified_catalog(context):
                continue
            rows = [row for row in self._catalog_replay(document, unit)
                    if row.get('id') == context.get('id')
                    and SourceVerifier._signature(row) == SourceVerifier._signature(context)]
            if len(rows) != 1:
                continue
            expected = rows[0].get('tuition' if field == 'tuition' else 'duration_years')
            proof = expected.get('evidence') if isinstance(expected, dict) else rows[0].get('duration_evidence')
            required = {(item.get('document_id'), item.get('page'), canonical(item.get('quote', '')))
                        for item in _items(proof)}
            actual = {(item.get('document_id'), item.get('page'), canonical(item.get('quote', '')))
                      for item in refs if item.get('document_id') == identifier
                      and item['verification'] == 'exact_quote' and item['relation'] == 'supports'}
            if expected is None or not required or required != actual:
                continue
            if field == 'tuition':
                value = (context.get('evidence_conflicts') or {}).get('tuition', context.get('tuition')) or {}
                if expected.get('period') != period or expected.get('currency') != value.get('currency'):
                    continue
                expected = expected['amount']
            values.add(float(expected))
            accepted.update(actual)
        return values, accepted

    def _numbers(self, field, refs, period, context, *, catalog_values=None):
        if catalog_values is None:
            catalog_values, _ = self._catalog_field(field, refs, period, context)
        values = list(catalog_values)
        for item in refs:
            if item['verification'] != 'exact_quote' or item['relation'] != 'supports':
                continue
            if self.documents.get(item.get('document_id'), {}).get('catalog_parser') is not None:
                continue
            quote = canonical(item['quote'])
            combined = canonical(' '.join(row['quote'] for row in refs if row['verification'] == 'exact_quote' and row['relation'] == 'supports' and row['source_group'] == item['source_group']))
            if field == 'tuition' and period == 'year' and not re.search(r'元/(?:生[·./])?(?:学年|年)|每年|年学费|按学年', combined):
                continue
            if not _semantic_scope(quote, context, combined):
                continue
            if field == 'tuition':
                actual_period = 'total' if re.search(r'全程|全学程|学费总额|合计|共\d', quote) else 'year' if re.search(r'元/(?:生[·./])?(?:学年|年)|每年|年学费|按学年', quote) else period
                if actual_period != period:
                    continue
                match = re.search(r'(?<![\d.])(\d+(?:\.\d+)?)(?:元(?:/生)?(?:[·./]?学?年)?(?:[。.]?)?)?$', quote)
            else:
                match = re.search(r'(?:学制(?:为)?|修业年限(?:为)?)(\d+(?:\.\d+)?)年', quote)
                match = match or re.search(r'(?<![\d.])(\d+(?:\.\d+)?)(?:年[。.]?)?$', quote)
            if match:
                values.append(float(match.group(1)))
        return set(values)

    def _claim(self, identifier, label, value, refs, context, *, verified=False, note=''):
        evidence = self._references(refs, context)
        catalog_values = None
        if identifier in {'tuition', 'duration'}:
            period = value.get('period') if isinstance(value, dict) else None
            catalog_values, replayed = self._catalog_field(identifier, evidence, period, context)
            evidence = [{**item, 'relation': 'out_of_scope', 'note': '原文可定位，但明示学院、方向或学习方式不适用于当前字段，不计为采用依据。'}
                        if item['verification'] == 'exact_quote' and item['relation'] == 'supports'
                        and (item.get('document_id'), item.get('page'), canonical(item.get('quote', ''))) not in replayed
                        and _scope_contradiction(canonical(item['quote']), context)
                        else item for item in evidence]
        valid = [item for item in evidence if item['verification'] in REFERENCE_TYPES and item['relation'] != 'out_of_scope']
        status = 'missing' if value is None else 'verified' if verified and valid and all(item['verification'] in REFERENCE_TYPES for item in evidence) else 'needs_review'
        if identifier in {'tuition', 'duration'} and value is not None:
            amount, period = (value.get('amount'), value.get('period')) if isinstance(value, dict) else (value, None)
            numbers = self._numbers(identifier, evidence, period, context, catalog_values=catalog_values)
            if len(numbers) > 1:
                status = 'conflict'
                note = '同年度、相同作用范围和计费口径的字段支持材料出现不同数值，暂不采用确定值。'
            elif not numbers or amount not in numbers:
                status = 'needs_review'
                note = '原文已定位，但字段数值或计费口径尚未完整核对。'
        if identifier == 'enrollment' and isinstance(value, dict):
            counts = [int(match.group(1)) for item in valid if item['relation'] == 'supports'
                      for match in re.finditer(r'(?:拟招(?:统考|收)?人数|招生人数|计划招生)\s*[:：为]?\s*(\d+)', item['quote'])]
            structured_quota = any(item['verification'] == 'structured_fields' and item.get('field_path') == 'detail.labels.拟招生人数' for item in valid)
            if type(value.get('count')) is not int or not structured_quota and value['count'] not in counts:
                status = 'needs_review'
        return {'id': identifier, 'field': identifier, 'label': label, 'value': deepcopy(value), 'status': status,
                'scope': {key: context.get(key) for key in (*SCOPE, 'cohort', 'plan_type', 'admission_source') if key in context},
                'evidence': evidence, 'source_count': len({item['source_group'] for item in valid}), 'note': note}

    def _history(self, program, row):
        evidence = _items(row.get('evidence'))
        verified, refs = False, []
        for item in evidence:
            identifier = item.get('document_id')
            document = self.documents.get(identifier, {}) if isinstance(identifier, str) else {}
            source = self._source(identifier) if isinstance(identifier, str) else {}
            if source.get('verification') != 'snapshot_verified' or document.get('unit_id') != row.get('unit_id') or document.get('year') != row.get('year') or document.get('url') != item.get('url'):
                continue
            original = self.verifier.read(document)
            from collectors.history import _Tables, parse_results_html
            html = decode_text(original[1], document.get('content_type', ''))
            parsed = parse_results_html(html, unit_id=row['unit_id'], unit_name=row.get('unit_name', ''),
                department=row.get('department', ''), year=row['year'], url=document['url'], document_id=identifier,
                admission_source=row.get('admission_source', 'not_specified'))
            matched = next((sample for sample in parsed['admitted_statistics'] if sample['id'] == row.get('id')), None)
            verified = bool(matched and all(matched.get(field) == row.get(field) for field in STAT_FIELDS))
            tables = _Tables()
            tables.feed(html)
            headers = [cell for table in tables.tables for cells in table[:2] for cell in cells
                       if canonical(cell) in {'复试专业', '学习方式', '总分', '初试总分', '初试各科成绩及总分', '是否拟录取', '复试成绩', '录取成绩'}]
            for quote in dict.fromkeys(headers):
                ref = self._ref({**item, 'quote': quote, 'page': None}, row)
                if ref['verification'] == 'exact_quote':
                    refs.append({**ref, 'verification': 'derived_statistics' if verified else 'exact_quote'})
        value = {key: deepcopy(row.get(key)) for key in STAT_FIELDS}
        return {'id': 'history:' + str(row.get('id')), 'field': 'history', 'label': f"{row.get('year')}年公示样本成绩",
                'value': value, 'status': 'derived' if verified else 'needs_review',
                'scope': {key: row.get(key) for key in ('unit_id', 'year', 'department', 'program_code', 'program_name', 'study_mode', 'cohort', 'plan_type', 'admission_source')},
                'evidence': refs, 'source_count': len({item['source_group'] for item in refs}),
                'note': '从原始公示中明确拟录取且有成绩的行重放计算；仅为该公示样本，不代表完整录取群体。' if verified else '原始公示尚不足以复核全部统计值和分组，不能称为已验证。',
                'calculation': {'method': '对同校、学院、专业、学习方式、计划和招生类别分别计算最小值、最大值与中位数。', 'sample_count': row.get('sample_count')}}

    def program(self, identifier):
        program = next(row for row in self.admissions.get('programs', []) if row.get('id') == identifier)
        unit = self.units[program['unit_id']]
        documents = self.documents
        catalog = self._verified_catalog(program)
        conflicts = program.get('evidence_conflicts') or {}
        tuition = conflicts.get('tuition', program.get('tuition'))
        duration = conflicts.get('duration_years', program.get('duration_years'))
        enrollment = program.get('planned_enrollment')
        subjects = program.get('subjects') or []
        exam_note = exam_unresolved_reason(program)
        subject_value = subjects or ({'exam_combinations':program['exam_combinations']} if exam_note and program.get('exam_combinations') else None)
        eligibility = program.get('eligibility_evidence_complete') is True and not program.get('requires_review')
        conditions = [item.get('evidence') for item in program.get('conditions', []) if isinstance(item, dict)]
        claims = [self._claim('catalog', '专业与研究方向', {key: program.get(key) for key in ('code', 'name', 'direction', 'study_mode')}, program.get('evidence'), program, verified=catalog),
                  self._claim('subjects', '初试科目与备选组合', subject_value, program.get('exam_evidence') or [item.get('evidence') for item in subjects] or program.get('evidence'), program, verified=catalog and not exam_note, note=exam_note or ''),
                  self._claim('tuition', '学费与计费口径', tuition, (tuition or {}).get('evidence') if isinstance(tuition, dict) else [], program, verified=True),
                  self._claim('duration', '学制', duration, program.get('duration_evidence'), program, verified=True),
                  self._claim('enrollment', '招生计划及统计范围', enrollment, (enrollment or {}).get('evidence') if isinstance(enrollment, dict) else [], program, verified=catalog,
                              note='院系专业合计不能直接当作每个方向的独立名额。'),
                  self._claim('eligibility', '完整报考资格条件', {'complete': eligibility}, [item for value in conditions for item in _items(value)], program, verified=eligibility,
                              note='仅有招生目录、章程或费用资料不足以证明完整报考资格，需核验学院与专业条件。')]
        if exam_note:
            claims=[{**claim,'status':'needs_review'} if claim['id']=='subjects' else claim for claim in claims]
        last_year = self.admissions.get('metadata', {}).get('last_completed_admission_year', program['year'] - 1)
        years = {last_year, last_year - 1, last_year - 2}
        for row in self.admissions.get('admitted_statistics', []):
            if row.get('year') in years and _historical_match(program, row):
                claims.append(self._history(program, row))
        for row in self.admissions.get('score_lines', []):
            applicability = _score_applicability(program, row)
            if row.get('year') in years and applicability != 'excluded':
                claim = self._claim('score:' + str(row.get('id')), f"{row['year']}年复试线", {key: row.get(key) for key in ('total', 'politics', 'foreign_language', 'business1', 'business2', 'kind')}, row.get('evidence'), row, verified=True)
                checked = _score_numbers_match(row, claim['evidence'])
                claim = {**claim, 'status': claim['status'] if checked and applicability == 'applicable' else 'needs_review',
                         'applicability': applicability,
                         'scope': {**claim['scope'], **{key: row.get(key) for key in ('degree_type', 'discipline_code', 'code_prefixes', 'plan_type', 'study_mode')}, 'level': row.get('kind')},
                         'note': '学校门类基本线；学院与专业可提出更高要求，不等同专业线。' if row.get('kind') in {'national', 'university'} else '仅适用于原文明确的专业范围。'}
                if applicability == 'unknown':
                    claim['note'] += '目标项目学位类型或其他适用条件未确认，仅作门类参考。'
                if not checked:
                    claim['note'] += '数值尚未在原文相同行中完成核对。'
                claims.append(claim)
        return {'program': {key: program.get(key) for key in IDENTITY}, 'claims': claims,
                'summary': {'claim_count': len(claims), 'verified_count': sum(c['status'] in {'verified', 'derived'} for c in claims),
                            'pending_count': sum(c['status'] in {'missing', 'needs_review'} for c in claims),
                            'conflict_count': sum(c['status'] == 'conflict' for c in claims),
                            'source_count': len({e['source_group'] for c in claims for e in c['evidence'] if e['verification'] in REFERENCE_TYPES | {'derived_statistics'} and e['relation'] != 'out_of_scope'})}, 'notice': NOTICE}

    def sources(self, unit_id=None, year=None, kind=None, q=''):
        related = {identifier: set() for identifier in self.documents}
        related_claims = {identifier: {} for identifier in self.documents}
        def attach(values, program, claim_id, label, context=None):
            context = context or program
            for item in _items(values):
                identifier = item.get('document_id')
                document = self.documents.get(identifier, {}) if isinstance(identifier, str) else {}
                if document.get('unit_id') != context.get('unit_id') or document.get('year') != context.get('year'):
                    continue
                related.setdefault(identifier, set()).add(program['id'])
                related_claims.setdefault(identifier, {})[(program['id'], claim_id)] = {'program_id': program['id'], 'claim_id': claim_id, 'label': label}
        for program in self.admissions.get('programs', []):
            conflicts = program.get('evidence_conflicts') or {}
            tuition = conflicts.get('tuition', program.get('tuition')) or {}
            attach(program.get('evidence'), program, 'catalog', '专业与研究方向')
            attach(program.get('exam_evidence') or program.get('evidence'), program, 'subjects', '初试科目')
            attach(tuition.get('evidence'), program, 'tuition', '学费与计费口径')
            attach(program.get('duration_evidence'), program, 'duration', '学制')
            attach((program.get('planned_enrollment') or {}).get('evidence'), program, 'enrollment', '招生计划')
            for condition in program.get('conditions', []):
                if isinstance(condition, dict):
                    attach(condition.get('evidence'), program, 'eligibility', '完整报考资格')
            for row in self.admissions.get('admitted_statistics', []):
                if _historical_match(program, row):
                    attach(row.get('evidence'), program, 'history:' + str(row.get('id')), f"{row.get('year')}年公示统计", row)
            for row in self.admissions.get('score_lines', []):
                if _score_applicability(program, row) != 'excluded':
                    attach(row.get('evidence'), program, 'score:' + str(row.get('id')), f"{row.get('year')}年学校参考线", row)
        for identifier, document in self.documents.items():
            for item in _items(document.get('applicability_evidence')):
                dependency = item.get('document_id')
                if isinstance(dependency, str):
                    related.setdefault(dependency, set()).update(related.get(identifier, set()))
                    related_claims.setdefault(dependency, {}).update(related_claims.get(identifier, {}))
            publication = document.get('publication_page')
            if isinstance(publication, dict):
                dependency = 'publication:' + _group(publication)
                related.setdefault(dependency, set()).update(related.get(identifier, set()))
                related_claims.setdefault(dependency, {}).update(related_claims.get(identifier, {}))
        grouped = {}
        for identifier, document in self.documents.items():
            row = self._source(identifier)
            if unit_id is not None and row['unit_id'] != unit_id or year is not None and row['year'] != year or kind is not None and row['kind'] != kind:
                continue
            if q and canonical(q) not in canonical(' '.join(str(row.get(key) or '') for key in ('title', 'publisher', 'kind', 'year'))):
                continue
            group = grouped.setdefault(row['source_group'], {**row, 'related_program_ids': [], 'aliases': [], '_claims': {}})
            group['related_program_ids'] = sorted(set(group['related_program_ids']) | related.get(identifier, set()))
            group['_claims'].update(related_claims.get(identifier, {}))
            if row['url'] and row['url'] != group['url'] and row['url'] not in group['aliases']:
                group['aliases'].append(row['url'])
        rows = [{**{key: value for key, value in row.items() if key != '_claims'},
                 'related_count': len(row['related_program_ids']), 'related_claim_count': len(row['_claims']),
                 'related_claims': list(row['_claims'].values())[:20]} for row in grouped.values()]
        return {'sources': rows, 'total': len(rows), 'summary': {'unique_document_count': len(rows),
                'verified_count': sum(row['verification'] == 'snapshot_verified' for row in rows),
                'pending_count': sum(row['verification'] != 'snapshot_verified' for row in rows)}, 'notice': NOTICE}


def apply_evidence_safety(registry, admissions, root, *, verifier=None):
    """Suspend only provable conflicts; saved catalogue data is never modified."""
    index = EvidenceIndex(registry, admissions, root, verifier=verifier)
    programs = []
    for program in admissions.get('programs', []):
        updates, conflicts = {}, {}
        for field, identifier in (('tuition', 'tuition'), ('duration_years', 'duration')):
            value = program.get(field)
            if value is None:
                continue
            refs = value.get('evidence') if isinstance(value, dict) else program.get('duration_evidence')
            claim = index._claim(identifier, field, value, refs, program, verified=True)
            if claim['status'] == 'conflict':
                updates[field], conflicts[field] = None, deepcopy(value)
        programs.append({**program, **updates, 'evidence_conflicts': conflicts} if conflicts else program)
    statistics = []
    for row in admissions.get('admitted_statistics', []):
        claim = index._history({}, row)
        statistics.append({**row, 'evidence_status': claim['status'], 'evidence_note': claim['note']})
    lines = []
    for row in admissions.get('score_lines', []):
        refs = index._references(row.get('evidence'), row)
        checked = bool(refs) and all(ref['verification'] in {'exact_quote', 'reference_link'} for ref in refs) and _score_numbers_match(row, refs)
        lines.append({**row, 'evidence_status': 'verified' if checked else 'needs_review',
                      'evidence_note': '已保存原文中逐项核对数值及声明的表格列序；专业适用性需单独核验。' if checked else '原文、数值或列序尚未完成核验，图表暂不作为确定值绘制。'})
    changed = any(p is not old for p, old in zip(programs, admissions.get('programs', []))) or bool(statistics or lines)
    return {**admissions, 'programs': programs, 'admitted_statistics': statistics, 'score_lines': lines} if changed else admissions
