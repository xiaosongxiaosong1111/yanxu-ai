"""Read the explicitly published catalog without loading the national JSON corpus."""
import hashlib
import json
import threading
import re
from urllib.parse import urlsplit

from catalog_index import CatalogIndex, CatalogIndexError, page_arguments
from evidence import EvidenceIndex, KINDS, apply_evidence_safety, exam_unresolved_reason
from service_verification import SourceVerifier
from snapshot_cache import SnapshotCache


NOTICE = '使用最后已发布的核验快照；本次查询未重新访问或核验全部官方原文。'
COLLECTIONS = ('documents', 'programs', 'score_lines', 'retest_rules', 'transfer_rules', 'notices', 'admitted_statistics')


class CatalogQueryError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def public_value(value):
    if isinstance(value, dict):
        return {key: public_value(item) for key, item in value.items()
                if key != 'text_pages' and (not key.endswith('_path') or key == 'field_path'
                    and isinstance(item, str) and 0 < len(item) <= 512 and not re.search(r'[:/\\]', item))}
    if isinstance(value, list):
        return [public_value(item) for item in value]
    return value


def checked_revision(value):
    if value is not None and (not isinstance(value, str) or not value or len(value) > 128 or '\x00' in value):
        raise CatalogQueryError('invalid_query', '资料版本格式不正确。')
    return value


class PublishedCatalog:
    def __init__(self, root):
        self.root = root
        self.index = CatalogIndex(root / 'runtime/catalog-index.sqlite')
        self._scopes = {}
        self._lock = threading.Lock()

    def available(self):
        return self.index.path.is_file()

    def _read(self, method, *args, **kwargs):
        try:
            return getattr(self.index, method)(*args, **kwargs)
        except CatalogIndexError as error:
            stale = getattr(error, 'code', '') == 'revision_changed'
            raise CatalogQueryError('revision_changed' if stale else 'catalog_unpublished',
                                    '资料版本已更新，请刷新后重试。' if stale else '尚无可用的已发布目录，请等待资料核验发布。',
                                    409 if stale else 503) from error
        except (ValueError,TypeError,KeyError) as error:
            raise CatalogQueryError('catalog_unpublished','已发布目录格式需要重新核验，请等待重新发布。',503) from error

    def bootstrap(self):
        value = self._read('bootstrap')
        meta = {**value.get('meta', {}), 'historical_reference': True, 'stage': 'local_research'}
        return public_value({'meta': meta, 'coverage': value.get('coverage', {}), 'units': value.get('units', []),
                             'revision': value['revision'], 'published_at': value.get('published_at'),
                             'verification_mode': 'published_snapshot', 'notice': NOTICE})

    def health(self):
        value=self._read('metadata')
        return {'release_ready':False,'published_release_ready':bool(value.get('coverage',{}).get('release_ready')),
                'data_revision':value['revision'],'verification_mode':'published_snapshot',
                'coverage_status':'published_snapshot','notice':NOTICE}

    def page(self, kind, *, unit_id=None, year=None, q='', offset=0, limit=20, revision=None,
             code=None, scope=None, exam_unit=None):
        checked_revision(revision)
        try:
            page_arguments(unit_id, year, q, offset, limit)
        except CatalogIndexError as error:
            raise CatalogQueryError('invalid_query', '分页、年度或检索条件格式不正确。') from error
        if (code is not None and (not isinstance(code, str) or len(code) > 32)
                or scope is not None and (not isinstance(scope, str) or len(scope) > 100)
                or exam_unit is not None and (type(exam_unit) is not int or exam_unit not in {1, 2, 3, 4})):
            raise CatalogQueryError('invalid_query', '科目代码、作用域或考试单元格式不正确。')
        filters = {'code': code, 'scope': scope, 'exam_unit': exam_unit} if kind == 'subjects' else {}
        result = self._read('query_' + kind, unit_id=unit_id, year=year, q=q,
                            offset=offset, limit=limit, revision=revision, **filters)
        return public_value({**result, 'has_more': offset + limit < result['total'],
                             'verification_mode': 'published_snapshot', 'notice': NOTICE})

    def program(self, program_id, *, revision=None):
        checked_revision(revision)
        if not isinstance(program_id, str) or not program_id.strip() or len(program_id) > 300 or '\x00' in program_id:
            raise CatalogQueryError('invalid_query', '专业方向标识格式不正确。')
        value = self._read('program_bundle', program_id, revision=revision)
        if value.get('program') is None:
            raise CatalogQueryError('unknown_program', '该专业方向尚未收录到已发布目录。', 404)
        keys = ('program', 'meta', 'coverage', 'revision', 'documents', 'score_lines', 'admitted_statistics',
                'retest_rules', 'transfer_rules', 'notices')
        return public_value({**{key: value[key] for key in keys if key in value},
                             'verification_mode': 'published_snapshot', 'notice': NOTICE})

    def sources(self, *,unit_id=None,year=None,q='',kind=None,offset=0,limit=12,revision=None):
        checked_revision(revision)
        try:
            page_arguments(unit_id,year,q,offset,limit)
        except CatalogIndexError as error:
            raise CatalogQueryError('invalid_query','来源分页或筛选格式不正确。') from error
        if kind is not None and (not isinstance(kind,str) or kind not in KINDS):
            raise CatalogQueryError('invalid_kind','该资料类型不在已发布来源范围内。')
        value=self._read('query_sources',unit_id=unit_id,year=year,q=q,kind=kind,
                         offset=offset,limit=limit,revision=revision)
        sources=[]
        for document in value.pop('documents'):
            keys=('id','url','title','unit_id','unit_name','year','kind','status','sha256',
                  'checked_at','published_at','version_state','catalog_active','aliases',
                  'related_program_ids','related_claims','original_format','detail_url','detail_url_kind',
                  'detail_document_id','detail_sha256','request_method','request_url')
            row={key:document[key] for key in keys if key in document}
            parsed=urlsplit(row.get('url',''))
            if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
                row['url']=''
            if document.get('catalog_parser') == 'chsi_detail_v1':
                row={**row,'original_format':'structured_fields','detail_url':row.get('url','')}
            sources.append({**row,'publisher':document.get('unit_name') or '发布单位待核验',
                'source_group':document.get('sha256') or document['id'],'verification':'published_snapshot',
                'aliases':row.get('aliases',[]),'related_program_ids':row.get('related_program_ids',[]),
                'related_claims':row.get('related_claims',[])})
        summary=value.get('summary',{})
        return public_value({**value,'sources':sources,'has_more':offset+limit<value['total'],
            'verification_mode':'published_snapshot','notice':NOTICE,
            'summary':{**summary,'unique_document_count':summary.get('document_count',0),
                       'verified_count':0,'pending_count':0}})

    @staticmethod
    def _registry_units(registry, verifier):
        from collectors.registry import decode_registry_html, parse_registry_page
        fields = ('id', 'name', 'province', 'unit_code', 'source_url')
        expected = {row['id']: tuple(row.get(key) for key in fields) for row in registry['units']}
        found = set()
        for page in registry['metadata'].get('pages', []):
            if urlsplit(page.get('url', '')).hostname != 'yz.chsi.com.cn':
                continue
            source = verifier.read(page, 'snapshot_path')
            if not source:
                continue
            records, pagination = parse_registry_page(decode_registry_html(source[1]), page['url'])
            if pagination.get('invalid_items'):
                continue
            found.update(row['id'] for row in records if row['id'] in expected
                         and tuple(row.get(key) for key in fields) == expected[row['id']])
            if found == set(expected):
                break
        return found

    @staticmethod
    def _verify_scope(registry, admissions, bundle, verifier, *, complete_catalog=True):
        documents = {row['id']: row for row in admissions['documents']}
        known_units = PublishedCatalog._registry_units(registry, verifier)
        invalid, states, units, details, annual_excluded = [], {}, [], [], []
        index = EvidenceIndex(registry, admissions, verifier.root, verifier)
        reference_year = admissions['metadata'].get('reference_year', 2027)
        for unit in registry['units']:
            rows = [row for row in admissions['programs'] if row.get('unit_id') == unit['id']]
            not_listed = unit.get('annual_status') == 'not_listed' and unit.get('annual_year') == reference_year
            if not_listed:
                annual_excluded.append(unit['id'])
            valid = [row for row in rows if not (not_listed and row.get('year') == reference_year)
                     and unit['id'] in known_units and verifier.program(row, documents, unit)]
            valid_ids = {row['id'] for row in valid}
            invalid.extend(row['id'] for row in rows if row['id'] not in valid_ids)
            complete = (complete_catalog and unit.get('verified') is True and len(valid) == len(rows) and bool(rows)
                        and verifier.unit_reconciles(unit, admissions['metadata'].get('reference_year', 2027),
                                                     [r for r in rows if r.get('year') == admissions['metadata'].get('reference_year', 2027)], documents))
            if complete:
                units.append(unit['id'])
            details.append({'unit_id':unit['id'], 'unit_name':unit.get('name'), 'verified':complete,
                            'status':'not_listed' if not_listed else 'verified' if complete else 'needs_review' if len(valid)<len(rows) else 'partial',
                            'parsed_records':len(rows), 'verified_records':len(valid),
                            **({'notes':f'未列入{reference_year}年度独立硕士招生单位名单，不作为该年度独立报考候选；不据此推断停招。'} if not_listed else {})})
            for row in rows:
                states[row['id']] = {}
                for key, field in (('tuition','tuition'), ('duration_years','duration')):
                    value = row.get(key)
                    if value is not None:
                        refs = value.get('evidence') if isinstance(value,dict) else row.get('duration_evidence')
                        states[row['id']][key] = index._claim(field, field, value, refs, row, verified=True)['status']
        return {**bundle.get('coverage', {}), 'release_ready':False, 'catalog_complete':False,
                'published_release_ready':bool(bundle.get('coverage',{}).get('release_ready')),
                'verified_unit_ids':units, 'unit_catalog_status':details, 'validation_mode':'scoped_originals',
                'annual_not_listed_unit_ids':annual_excluded,
                'validated_unit_ids':sorted(known_units), 'invalid_program_count':len(invalid),
                '_invalid_program_ids':invalid, '_field_states':states}

    def scope(self, unit_ids=None, *, revision=None):
        checked_revision(revision)
        if unit_ids is None:
            summary = self._read('bootstrap')
            unit_ids = [u['id'] for u in summary['units'] if u.get('program_count')]
            revision = revision or summary['revision']
        if (not isinstance(unit_ids,list) or len(unit_ids)>2000
                or any(not isinstance(value,str) or not value or len(value)>100 for value in unit_ids)):
            raise CatalogQueryError('unknown_unit', '筛选的招生单位格式不正确。')
        unit_ids = list(dict.fromkeys(unit_ids))
        bundle = self._read('scope_snapshot', unit_ids, revision=revision)
        if {u['id'] for u in bundle['units']} != set(unit_ids):
            raise CatalogQueryError('unknown_unit', '筛选的招生单位尚未收录到已发布索引。')
        return self._checked_bundle(bundle)

    def _checked_bundle(self, bundle, *, complete_catalog=True):
        fingerprint = hashlib.sha256(json.dumps({'bundle':bundle,'complete_catalog':complete_catalog},
            ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
        with self._lock:
            entry = self._scopes.get(fingerprint)
            if entry is None:
                entry = {'cache':SnapshotCache(self.root,base_paths=()), 'verifier':None}
                self._scopes = {**dict(list(self._scopes.items())[-7:]), fingerprint:entry}
        registry = {'metadata':bundle.get('registry_metadata',{}), 'units':bundle['units']}
        admissions = {'metadata':bundle['meta'], **{key:bundle.get(key,[]) for key in COLLECTIONS}}
        def load():
            entry['verifier'] = SourceVerifier(self.root)
            return registry, apply_evidence_safety(registry, admissions, self.root, verifier=entry['verifier'])
        try:
            data = entry['cache'].get(load, lambda reg,adm:self._verify_scope(reg,adm,bundle,entry['verifier'],
                complete_catalog=complete_catalog))
        except (OSError,ValueError,TypeError) as error:
            raise CatalogQueryError('data_unavailable', '所选学校原文正在变化或无法核验，请稍后重试。',503) from error
        reg, adm, coverage, source_revision = data
        validation = {key:coverage.pop(key) for key in ('_invalid_program_ids','_field_states')}
        return {'registry':reg, 'admissions':adm, 'coverage':coverage, 'validation':validation,
                'revision':bundle['revision'], 'originals_revision':source_revision, 'verifier':entry['verifier']}

    def evidence_scope(self, program_id, *, revision=None):
        checked_revision(revision)
        bundle = self._read('program_bundle', program_id, revision=revision)
        if bundle.get('program') is None:
            raise CatalogQueryError('unknown_program', '该专业方向尚未收录到已发布目录。', 404)
        # Keep the school's tree and source context, but a field drawer does not audit every direction.
        return self._checked_bundle(bundle, complete_catalog=False)

    def scope_for_program(self, program_id, *,revision=None):
        checked_revision(revision)
        value = self._read('program_bundle', program_id,revision=revision)
        if value.get('program') is None:
            raise CatalogQueryError('unknown_program', '该专业方向尚未收录到已发布目录。',404)
        return self.scope([value['program']['unit_id']], revision=value['revision'])

    def scope_for_programs(self, program_ids, *,allow_missing=False):
        if (not isinstance(program_ids,list) or len(program_ids)>100
                or any(not isinstance(value,str) or not value or len(value)>300 for value in program_ids)):
            raise CatalogQueryError('invalid_watch','请选择至多100个真实专业方向。')
        metadata=self._read('metadata')
        programs=[self._read('get_program',identity) for identity in dict.fromkeys(program_ids)]
        if not allow_missing and any(value is None for value in programs):
            raise CatalogQueryError('unknown_program','部分关注方向已不在当前已发布目录，请重新核对。',404)
        units=list(dict.fromkeys(row['unit_id'] for row in programs if row))
        if not units:
            return {'registry':{'metadata':{},'units':[]},
                    'admissions':{'metadata':metadata['meta'],**{key:[] for key in COLLECTIONS}},
                    'coverage':{**metadata.get('coverage',{}),'release_ready':False},
                    'revision':metadata['revision'],'verifier':SourceVerifier(self.root)}
        return self.scope(units,revision=metadata['revision'])

    def historical_rules(self,kind,year):
        if kind not in {'retest','transfer'} or type(year) is not int:
            raise CatalogQueryError('invalid_query','历史规则类型或年度不正确。')
        try:
            with self.index.read_connection() as connection:
                publication=self.index._publication(connection)
                last=publication['meta'].get('last_completed_admission_year',publication['meta'].get('reference_year',2027)-1)
                if not last-2<=year<=last:
                    raise CatalogQueryError('unsupported_year','仅支持最近三个已完成招生年度的历史核对。')
                rules=[json.loads(row[0]) for row in connection.execute(
                    'SELECT payload FROM records WHERE kind=? AND year=? ORDER BY ordinal',(kind+'_rules',year))]
                ids=list(dict.fromkeys(row['unit_id'] for row in rules))
                marks=','.join('?' for _ in ids)
                units=[json.loads(row[0]) for row in connection.execute('SELECT payload FROM units WHERE unit_id IN ('+marks+')',ids)] if ids else []
                documents=[json.loads(row[0]) for row in connection.execute('SELECT payload FROM documents WHERE unit_id IN ('+marks+') AND year=?',[*ids,year])] if ids else []
        except CatalogIndexError as error:
            raise CatalogQueryError('catalog_unpublished','历史规则发布快照暂不可用。',503) from error
        registry={'units':units,'metadata':publication.get('registry_metadata',{})}
        admissions={'metadata':publication['meta'],**{key:[] for key in COLLECTIONS},'documents':documents}
        index=EvidenceIndex(registry,admissions,self.root)
        verified=[]
        for rule in rules:
            refs=index._references(rule.get('evidence'),rule)
            if refs and all(ref['verification']=='exact_quote' for ref in refs):
                verified.append(rule)
        return {'rules':verified,'stored_count':len(rules),'revision':publication['revision'],
                'unverified_count':len(rules)-len(verified),'verification_mode':'scoped_originals'}

    @staticmethod
    def matching_programs(state, year):
        invalid = set(state['validation']['_invalid_program_ids'])
        states = state['validation']['_field_states']
        return [{**row, **{field:None for field,status in states.get(row['id'],{}).items() if status!='verified'}}
                for row in state['admissions']['programs'] if row.get('year')==year and row['id'] not in invalid
                and not exam_unresolved_reason(row)]
