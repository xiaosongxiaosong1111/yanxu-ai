"""Real-source admission queries and transparent catalogue release checks."""

from collections import Counter
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import official_search
import unit_bindings
from official_search import http_url as _http_url
from public_sources import official_roots, official_source_url

from service_verification import SourceVerifier
from snapshot_cache import SnapshotCache
from radar import RadarService
from source_proof import KINDS as PROOF_KINDS, ProofError, SourceProofService
from enrichment import apply_enrichment
from evidence import EvidenceIndex, KINDS as SOURCE_KINDS, apply_evidence_safety, exam_unresolved_reason
from collection_status import CollectionStatus
from published_catalog import PublishedCatalog, CatalogQueryError
from catalog_facts_store import CatalogFactsStore, CatalogFactsError
from json_reader import read_json, SnapshotReadError
from knowledge_api import checked_request


SEARCH_ENDPOINT = 'https://api.wsa.cloud.tencent.com/SearchPro'
# The search vocabulary and tiering live in official_search so the page API and
# the research agent cannot drift apart again.
SEARCH_KINDS = official_search.SEARCH_KINDS
SEARCH_SCOPES = official_search.SEARCH_SCOPES
COLLECTIONS = ('documents', 'programs', 'score_lines', 'retest_rules', 'transfer_rules', 'notices', 'admitted_statistics')


class ServiceError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _nonfinite(value):
    raise ValueError('Non-finite numbers are not valid source data.')


def _text(value, limit=120):
    if not isinstance(value, str) or len(value) > limit:
        raise ServiceError('invalid_query', '查询字段格式或长度不正确。')
    return value.strip()


def _merge_rows(first, second):
    seen = {row.get('id') or json.dumps(row, sort_keys=True, ensure_ascii=False) for row in first}
    result = list(first)
    for row in second:
        key = row.get('id') or json.dumps(row, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def _public_document(value):
    if isinstance(value, dict):
        return {key: _public_document(item) for key, item in value.items()
                if key != 'text_pages' and (not key.endswith('_path') or key=='field_path')}
    if isinstance(value, list):
        return [_public_document(item) for item in value]
    return value


class AdmissionService:
    def __init__(self, data_dir=None, engine=None, search_opener=None, search_api_key=None, radar=None, source_proof=None,
                 settings=None, research=None, knowledge=None, vectors=None, retriever=None, preparer=None):
        self.data_dir = Path(data_dir) if data_dir is not None else Path(__file__).parent / 'data'
        self._provided_engine = engine
        self._provided_key = search_api_key
        self._settings = settings
        self._research = research
        self._knowledge = knowledge
        self._vectors = vectors
        self._retriever_instance = retriever
        self._preparer = preparer
        self._extension_lock = threading.RLock()
        self._closed = False
        self._opener = search_opener or build_opener(NoRedirect()).open
        self.verifier = SourceVerifier(self.data_dir)
        self._cache = SnapshotCache(self.data_dir)
        self._catalog_facts = CatalogFactsStore(self.data_dir)
        self._collection = CollectionStatus(self.data_dir, facts_store=self._catalog_facts)
        self._catalog = PublishedCatalog(self.data_dir)
        self.radar = radar if radar is not None else RadarService(self.data_dir)
        self.proof = source_proof if source_proof is not None else SourceProofService(self.data_dir, search_callback=self.search)

    def _engine(self):
        return self._provided_engine or importlib.import_module('grad_engine')

    def _load(self, filename, keys):
        path = self.data_dir / filename
        if not path.exists():
            return {'metadata': {}, **{key: [] for key in keys}}
        try:
            value = read_json(path)
            if not isinstance(value, dict) or not isinstance(value.get('metadata', {}), dict):
                raise ValueError('invalid root')
            if any(not isinstance(value.get(key, []), list) or any(not isinstance(row, dict) for row in value.get(key, [])) for key in keys):
                raise ValueError('invalid collection')
            if any(row.get('id') is not None and not isinstance(row['id'], str) for key in keys for row in value.get(key, [])):
                raise ValueError('invalid identity')
            if filename == 'units.json' and any(not isinstance(unit.get('id'), str) or not unit['id'] or not isinstance(unit.get('name'), str) for unit in value.get('units', [])):
                raise ValueError('invalid unit')
            return {'metadata': value.get('metadata', {}), **{key: value.get(key, []) for key in keys}}
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            raise ServiceError('data_unavailable', '资料库正在更新或需要校验，请稍后重试。', 503) from error

    def _load_snapshot(self):
        self.verifier = SourceVerifier(self.data_dir)
        registry = self._load('units.json', ('units',))
        admissions = self._load('admissions.json', COLLECTIONS)
        history = self._load('history.json', COLLECTIONS)
        merged = {'metadata': admissions['metadata'],
                  **{key: _merge_rows(admissions[key], history[key]) for key in COLLECTIONS}}
        enriched = apply_enrichment(registry, merged, self.data_dir)
        return registry, apply_evidence_safety(registry, enriched, self.data_dir, verifier=self.verifier)

    def _state(self):
        def verify(registry, admissions):
            return self._coverage(registry, admissions)
        try:
            return self._cache.get(self._load_snapshot, verify)
        except OSError as error:
            raise ServiceError('data_unavailable', '资料库正在更新或需要校验，请稍后重试。', 503) from error

    def _snapshot(self):
        registry, admissions, _, _ = self._state()
        return registry, admissions

    def _key(self):
        value = self._provided_key
        if value is None:
            value = ''
            if self._settings is not None or (self.data_dir / 'runtime/settings.sqlite').is_file():
                try: value = self._settings_call('yuanbao_key')
                except ServiceError: value = ''
            value = value or os.environ.get('GRAD_SEARCH_API_KEY', '')
        if not isinstance(value, str) or len(value) > 512 or any(char in value for char in '\r\n'):
            return ''
        return value.strip()

    def _search_config(self):
        # Explicit injection also supports an empty key as a hard network opt-out in tests.
        if self._provided_key is not None:
            key = self._key()
            return {'provider':'yuanbao','auth_mode':'api_key','api_key':key} if key else None
        if self._settings is not None or (self.data_dir / 'runtime/settings.sqlite').is_file():
            target = self._settings_service()
            if callable(getattr(target, 'runtime_yuanbao', None)):
                try: config = self._settings_call('runtime_yuanbao')
                except ServiceError: return None
                if config:
                    return config
        key = self._key()
        return {'provider':'yuanbao','auth_mode':'api_key','api_key':key} if key else None

    def _settings_service(self):
        with self._extension_lock:
            if self._settings is None:
                self._settings = importlib.import_module('settings_store').SettingsService(self.data_dir)
            return self._settings

    def _research_service(self):
        with self._extension_lock:
            if self._closed:
                raise ServiceError('research_unavailable', '研究服务正在关闭，请稍后重试。', 503)
            if self._research is None:
                self._research = importlib.import_module('research_service').ResearchService(
                    self.data_dir, self, settings=self._settings_service())
            return self._research

    def _knowledge_service(self):
        with self._extension_lock:
            if self._closed: raise ServiceError('knowledge_unavailable', '资料服务已关闭。', 503)
            if self._knowledge is None:
                self._knowledge = importlib.import_module('knowledge_store').KnowledgeStore(self.data_dir)
            return self._knowledge

    def _vector_index(self):
        with self._extension_lock:
            if self._closed:
                raise ServiceError('vector_unavailable', '向量索引服务已关闭。', 503)
            if self._vectors is None:
                try:
                    module = importlib.import_module('knowledge_vectors')
                    self._vectors = module.VectorIndex(self._knowledge_service(), self._settings_service())
                except ServiceError:
                    raise
                except Exception as error:
                    self._raise_extension('knowledge', error, 'vector_unavailable', '向量索引暂时不可用，可继续使用关键词检索。')
            return self._vectors

    def _retriever(self):
        with self._extension_lock:
            if self._closed:
                raise ServiceError('retrieval_unavailable', '检索服务已关闭。', 503)
            if self._retriever_instance is None:
                try:
                    module = importlib.import_module('knowledge_retrieval')
                    self._retriever_instance = module.HybridRetriever(self._knowledge_service(), self._vector_index())
                except ServiceError:
                    raise
                except Exception as error:
                    self._raise_extension('knowledge', error, 'retrieval_unavailable', '检索服务暂时不可用，请稍后重试。')
            return self._retriever_instance

    def _embedding_preparer(self):
        with self._extension_lock:
            if self._closed:
                raise ServiceError('vector_unavailable', '向量索引服务已关闭。', 503)
            if self._preparer is None:
                try:
                    module = importlib.import_module('embedding_prepare')
                    self._preparer = module.ModelPreparer(self.data_dir)
                except Exception as error:
                    self._raise_extension('knowledge', error, 'embedding_model_not_ready', '本机向量模型状态暂时无法读取。')
            return self._preparer

    def _raise_extension(self, kind, error, code, message):
        module_name, name = {'settings':('settings_store','SettingsError'),'research':('research_service','ResearchError'),
                             'knowledge':('knowledge_store','KnowledgeError')}[kind]
        try:
            error_type = getattr(importlib.import_module(module_name), name)
        except Exception:
            error_type = ()
        if error_type and isinstance(error, error_type):
            raise ServiceError(error.code, error.message, error.status) from None
        if kind == 'knowledge':
            try:
                prepare = importlib.import_module('embedding_prepare').PrepareError
            except Exception:
                prepare = ()
            if prepare and isinstance(error, prepare):
                raise ServiceError(error.code, error.message, error.status) from None
        if kind == 'knowledge':
            raise ServiceError(code, message, 503) from None
        raise error

    def knowledge_retrieve(self, q, unit_id='', year=None, entry_id=None, limit=8, allow_remote=False):
        try:
            return self._retriever().retrieve(q, unit_id=unit_id, year=year, entry_id=entry_id,
                                              limit=limit, allow_remote=allow_remote)
        except ServiceError:
            raise
        except Exception as error:
            self._raise_extension('knowledge', error, 'retrieval_unavailable', '检索服务暂时不可用，请稍后重试。')

    def vector_status(self):
        try:
            status = self._vector_index().status()
        except ServiceError:
            raise
        except Exception as error:
            self._raise_extension('knowledge', error, 'vector_unavailable', '向量索引状态暂时无法读取。')
        try:
            model = self._embedding_preparer().status()
        except ServiceError as error:
            model = {'state': 'failed', 'prepared': False, 'error_code': error.code}
        except Exception:
            model = {'state': 'failed', 'prepared': False, 'error_code': 'embedding_model_status_unavailable'}
        return {**status, 'embedding_model': model}

    def vector_build(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'profile_id'}:
            raise ServiceError('invalid_query', '向量构建请求包含不支持的字段。')
        profile_id = payload.get('profile_id')
        if profile_id is not None and (not isinstance(profile_id, str) or not 1 <= len(profile_id) <= 100):
            raise ServiceError('invalid_query', '向量配置编号不正确。')
        try:
            return self._vector_index().start_build(profile_id)
        except ServiceError:
            raise
        except Exception as error:
            self._raise_extension('knowledge', error, 'vector_unavailable', '向量索引构建暂时无法启动。')

    def vector_cancel(self, payload):
        if not isinstance(payload, dict) or payload:
            raise ServiceError('invalid_query', '取消请求不需要额外字段。')
        try:
            return self._vector_index().cancel_build()
        except ServiceError:
            raise
        except Exception as error:
            self._raise_extension('knowledge', error, 'vector_unavailable', '向量索引任务暂时无法取消。')

    def embedding_prepare(self, payload):
        if not isinstance(payload, dict) or payload:
            raise ServiceError('invalid_query', '模型准备请求不需要额外字段。')
        try:
            return self._embedding_preparer().start_prepare()
        except ServiceError:
            raise
        except Exception as error:
            self._raise_extension('knowledge', error, 'embedding_model_not_ready', '本机向量模型暂时无法准备。')

    def _extension_call(self, kind, method, *args, **kwargs):
        try:
            target = {'settings':self._settings_service,'research':self._research_service,'knowledge':self._knowledge_service}[kind]()
            return getattr(target, method)(*args, **kwargs)
        except ServiceError:
            raise
        except Exception as error:
            self._raise_extension(kind, error, 'knowledge_unavailable', '知识库暂时无法读取，请稍后重试。')

    def _settings_call(self, method, *args, **kwargs):
        return self._extension_call('settings', method, *args, **kwargs)

    def settings_public(self):
        return self._settings_call('public_settings')

    def settings_providers(self):
        return self._settings_call('providers')

    def settings_models(self, payload):
        return self._settings_call('models', payload)

    def settings_test(self, payload):
        return self._settings_call('test', payload)

    def settings_save(self, payload):
        return self._settings_call('save', payload)

    def settings_delete(self, payload):
        return self._settings_call('delete', payload)

    def research_create(self, payload):
        return self._extension_call('research', 'create', payload)

    def research_get(self, research_id=None, after_event=0, limit=20):
        if research_id is None:
            return self._extension_call('research', 'list', limit=limit)
        return self._extension_call('research', 'get', research_id, after_event=after_event)

    def research_cancel(self, payload):
        return self._extension_call('research', 'cancel', payload)

    def research_continue(self, payload):
        return self._extension_call('research', 'continue_research', payload)

    def research_delete(self, payload):
        return self._extension_call('research', 'delete', payload)

    def knowledge_get(self, entry_id=None, q='', status='', year=None, unit_id='', page=1, page_size=12, quality='accepted'):
        if entry_id is not None:
            return self._extension_call('knowledge', 'get', _text(entry_id, 100))
        if year == '': year=None
        elif isinstance(year,str) and re.fullmatch(r'[0-9]{4}',year): year=int(year)
        if (not isinstance(q,str) or len(q)>240 or not isinstance(unit_id,str) or len(unit_id)>100
                or not isinstance(status,str) or status not in {'','search_lead','official_original_read','published_snapshot'}
                or not isinstance(quality,str) or quality not in {'accepted','pending','excluded','all'}
                or year is not None and (type(year) is not int or not 2000<=year<=2100)):
            raise ServiceError('invalid_query', '知识库筛选条件格式不正确。')
        pagination = {}
        for name, value, upper in (('page',page,100000),('page_size',page_size,50)):
            if isinstance(value,str) and re.fullmatch(r'[0-9]{1,6}',value): value=int(value)
            if type(value) is not int or not 1<=value<=upper:
                raise ServiceError('invalid_query', '知识库分页参数格式不正确。')
            pagination[name]=value
        result = self._extension_call('knowledge', 'search', query=q, status=status, year=year,
                                      unit_id=unit_id, quality=quality, **pagination)
        # The registry stores full official names, so a short name such as
        # 国科大 would find nothing even when this school's materials are
        # already saved. Only retry with an evidence-backed alias when the
        # original query was empty; this never widens to a different school.
        expanded = unit_bindings.expand_query(q)
        if not result.get('total') and expanded != q and len(expanded) <= 240:
            retried = self._extension_call('knowledge', 'search', query=expanded, status=status, year=year,
                                           unit_id=unit_id, quality=quality, **pagination)
            if retried.get('total'):
                return {**retried, 'query_expansion': {
                    'requested': q, 'effective': expanded,
                    'notice': '已把学校简称展开为目录中的规范校名；这只是检索词变化，不是新的资料或事实。'}}
        return result

    def knowledge_request(self, action, payload):
        method, values = checked_request(action, payload, ServiceError)
        return self._extension_call('knowledge', method, **values)

    def close(self):
        with self._extension_lock:
            if self._closed:
                return
            self._closed = True
            research = self._research
            retriever = self._retriever_instance
            vectors = self._vectors
        if research is not None:
            research.close()
        if retriever is not None:
            retriever.close()
        if vectors is not None:
            try:
                vectors.close()
            except Exception:
                pass

    def _connection_status(self):
        configured = bool(self._search_config())
        ai, yuanbao = {}, {}
        if self._settings is not None or (self.data_dir / 'runtime/settings.sqlite').is_file():
            try:
                settings = self.settings_public()
                ai, yuanbao = settings.get('ai', {}), settings.get('yuanbao', {})
            except ServiceError:
                return {'search_configured': configured, 'search_status': 'unavailable',
                        'search_provider': 'yuanbao_search', 'ai_status': 'unavailable', 'ai_configured': False,
                        'proof_status': 'configured' if configured else 'not_configured'}
        search_status = yuanbao.get('status', 'not_configured')
        if search_status == 'not_configured' and configured:
            search_status = 'configured'
        return {'search_configured': configured, 'search_status': search_status,
                'search_provider': 'yuanbao_search', 'ai_status': ai.get('status', 'not_configured'),
                'ai_configured': bool(ai.get('provider')), 'ai_provider': ai.get('provider', ''),
                'ai_model': ai.get('model', ''), 'proof_status': 'configured' if configured else 'not_configured'}

    def _coverage(self, registry, admissions):
        metadata, units = registry['metadata'], registry['units']
        index_ids = [unit.get('id') for unit in units]
        registry_complete = self.verifier.registry(registry)
        year = admissions['metadata'].get('reference_year', 2027)
        rows = [program for program in admissions['programs'] if program.get('year') == year]
        statuses = admissions['metadata'].get('unit_catalog_status', [])
        statuses = statuses if isinstance(statuses, list) else []
        status_counts = Counter(item.get('unit_id') for item in statuses if isinstance(item, dict) and item.get('year') == year)
        by_unit = {item.get('unit_id'): item for item in statuses if isinstance(item, dict) and item.get('year') == year}
        documents = {document.get('id'): document for document in admissions['documents']}
        complete_units, excluded_units, details = [], [], []
        for unit in units:
            unit_id = unit.get('id')
            status = by_unit.get(unit_id, {})
            actual = [program for program in rows if program.get('unit_id') == unit_id]
            counts = [status.get(key) for key in ('expected_records', 'parsed_records', 'verified_records')]
            count_ok = all(_integer(count) for count in counts) and len(set(counts)) == 1 and counts[0] == len(actual)
            verified = status_counts[unit_id] == 1 and status.get('status') == 'verified' and count_ok and bool(actual) and self.verifier.unit_reconciles(unit, year, actual, documents) and all(self.verifier.program(program, documents, unit) for program in actual)
            not_applicable = status_counts[unit_id] == 1 and status.get('status') == 'not_applicable' and count_ok and not actual and self.verifier.not_applicable(status, documents, unit, year)
            if verified: complete_units.append(unit_id)
            if not_applicable: excluded_units.append(unit_id)
            effective_status = status.get('status', 'not_collected')
            if effective_status in {'verified', 'not_applicable'} and not (verified or not_applicable): effective_status = 'needs_review'
            details.append({'unit_id': unit_id, 'unit_name': unit.get('name'), 'status': effective_status, 'declared_status': status.get('status', 'not_collected'),
                            'parsed_records': len(actual), 'verified': verified, 'not_applicable': not_applicable})
        unique_rows = len({program.get('id') for program in rows}) == len(rows)
        known_rows = all(program.get('unit_id') in index_ids for program in rows)
        complete = bool(registry_complete and admissions['metadata'].get('catalog_complete') is True and complete_units and len(complete_units) + len(excluded_units) == len(units) and unique_rows and known_rows)
        missing = [unit_id for unit_id in index_ids if unit_id not in complete_units and unit_id not in excluded_units]
        return {'index_total': len(units), 'program_units': len({row.get('unit_id') for row in rows}), 'program_count': len(rows),
                'qualification_complete_count': sum(row.get('eligibility_evidence_complete') is True and not row.get('requires_review') for row in rows),
                'document_count': len(admissions['documents']), 'registry_complete': registry_complete,
                'catalog_complete': complete, 'release_ready': complete, 'missing_units_count': len(missing),
                'missing_unit_ids': missing, 'verified_unit_ids': complete_units, 'unit_catalog_status': details,
                'document_kinds': dict(Counter(document.get('kind', 'unknown') for document in admissions['documents'])),
                'reference_year': year, 'checked_at': metadata.get('checked_at'),
                'notes': '单位索引、目录记录和字段完整度分别核验。未核验完整时仅提供研究预览。'}

    def _years(self, admissions):
        metadata = admissions['metadata']
        reference, target = metadata.get('reference_year', 2027), metadata.get('target_year', 2028)
        years = {reference, target, reference - 1, reference - 2, reference - 3}
        years.update(row.get('year') for key in COLLECTIONS for row in admissions[key] if _integer(row.get('year')))
        return {year for year in years if _integer(year) and 2000 <= year <= 2100}

    def _query(self, registry, admissions, unit_id=None, year=None, q=''):
        if unit_id is not None:
            unit_id = _text(unit_id, 100)
            if unit_id not in {unit.get('id') for unit in registry['units']}:
                raise ServiceError('unknown_unit', '该招生单位尚未收录到当前索引。')
        year = admissions['metadata'].get('reference_year', 2027) if year is None else year
        if not _integer(year) or year not in self._years(admissions):
            raise ServiceError('unsupported_year', '该年度不在当前资料查询范围内。')
        return unit_id, year, _text(q).casefold()

    @staticmethod
    def _statistics(rows):
        fields = {'id', 'unit_id', 'unit_name', 'department', 'program_name', 'program_code', 'year', 'study_mode',
                  'cohort', 'plan_type', 'degree_type', 'admission_source', 'sample_count', 'source_table_complete', 'is_full_admission_cohort', 'evidence', 'evidence_status', 'evidence_note', 'notes'}
        result = []
        for row in rows:
            clean = {key: value for key, value in row.items() if key in fields}
            for key in ('initial', 'retest', 'weighted_final'):
                if isinstance(row.get(key), dict):
                    clean[key] = {field: row[key][field] for field in ('min', 'max', 'mean', 'median') if field in row[key]}
            if isinstance(row.get('subject_scores'), dict):
                clean['subject_scores'] = {key: {field: value[field] for field in ('min', 'max', 'mean', 'median') if field in value}
                                           for key, value in row['subject_scores'].items()
                                           if key in {'politics', 'foreign_language', 'business1', 'business2', '政治', '外语', '业务课一', '业务课二'} and isinstance(value, dict)}
            result.append(clean)
        return result

    def bootstrap(self):
        registry, admissions, coverage, revision = self._state()
        metadata = admissions['metadata']
        return {'meta': {'target_year': metadata.get('target_year', 2028), 'reference_year': metadata.get('reference_year', 2027),
                         'last_completed_admission_year': metadata.get('last_completed_admission_year', metadata.get('reference_year', 2027) - 1),
                         'score_years': sorted({row['year'] for row in admissions['score_lines'] if _integer(row.get('year'))}, reverse=True),
                         'historical_reference': True, 'stage': 'local_research',
                         'notice': '2028规划使用已收录年度的历史资料，不表示满足目标年度报考条件。'},
                'coverage': coverage, 'units': registry['units'],
                **{key: admissions[key] for key in ('programs', 'score_lines', 'retest_rules', 'transfer_rules', 'notices')},
                'documents': _public_document(admissions['documents']),
                'admitted_statistics': self._statistics(admissions['admitted_statistics']),
                'service': {**self._connection_status(), 'data_revision': revision,
                            'radar': self.radar_status()}}

    def health(self):
        if self._catalog.available():
            return {'status':'ok','mode':'local_research',**self._published('health'),
                    'search_configured':bool(self._search_config())}
        return {'status': 'ok', 'mode': 'local_research', **self._cache.status(),
                'search_configured': bool(self._search_config())}

    def _published(self, action, *args, **kwargs):
        try:
            return getattr(self._catalog, action)(*args, **kwargs)
        except CatalogQueryError as error:
            raise ServiceError(error.code, error.message, error.status) from error

    def catalog_bootstrap(self):
        result = self._published('bootstrap')
        # Merge evidence-backed extra domains and short names (国科大/社科大)
        # into the same unit rows the page already uses. Only units with a
        # reviewed binding change; the rest keep an empty official_url and are
        # still reported as binding-missing by the search pipeline.
        return {**result, 'units': [unit_bindings.enrich(unit) for unit in result['units']],
                'service': {**self._connection_status(),
                'data_revision': result['revision'], 'verification_mode': 'published_snapshot',
                'radar': self.radar_status()}}

    def catalog_programs(self, unit_id=None, year=None, q='', offset=0, limit=20, revision=None):
        return self._published('page', 'programs', unit_id=unit_id, year=year,
                               q=unit_bindings.expand_query(q),
                               offset=offset, limit=limit, revision=revision)

    def catalog_subjects(self, unit_id=None, year=None, q='', offset=0, limit=50, revision=None,
                         code=None, scope=None, exam_unit=None):
        return self._published('page', 'subjects', unit_id=unit_id, year=year,
                               q=unit_bindings.expand_query(q),
                               offset=offset, limit=limit, revision=revision, code=code, scope=scope, exam_unit=exam_unit)

    def catalog_program(self, program_id, revision=None):
        return self._published('program', program_id, revision=revision)

    def catalog_sources(self,unit_id=None,year=None,q='',kind=None,offset=0,limit=12,revision=None):
        return self._published('sources',unit_id=unit_id,year=year,q=q,kind=kind,
                               offset=offset,limit=limit,revision=revision)

    def catalog_facts(self,unit_id=None,year=None,q='',offset=0,limit=10):
        try:
            return self._catalog_facts.query(unit_id=unit_id,year=year,q=q,offset=offset,limit=limit)
        except CatalogFactsError as error:
            raise ServiceError(error.code,error.message,error.status) from error

    def collection_status(self, unit_id=None, q='', status='', page=1, page_size=20):
        try:
            if isinstance(page, bool) or isinstance(page_size, bool):
                raise ValueError('页码格式不正确。')
            return self._collection.query(unit_id=unit_id, q=q, status=status,
                                          page=int(page), page_size=int(page_size))
        except (json.JSONDecodeError, SnapshotReadError, OSError) as error:
            raise ServiceError('data_unavailable', '采集进度暂时无法读取，请稍后刷新。', 503) from error
        except (ValueError, TypeError) as error:
            raise ServiceError('invalid_query', '采集进度的学校、状态或页码不正确。') from error

    def programs(self, unit_id=None, year=None, q=''):
        registry, admissions, coverage, _ = self._state()
        unit_id, year, needle = self._query(registry, admissions, unit_id, year, q)
        scope = [program for program in admissions['programs'] if program.get('year') == year and (not unit_id or program.get('unit_id') == unit_id)]
        rows = [program for program in scope if needle in ' '.join(str(program.get(key, '')) for key in ('unit_name', 'department', 'code', 'name', 'direction', 'province', 'campus')).casefold()
                or any(needle in f"{subject.get('code', '')} {subject.get('name', '')}".casefold() for subject in program.get('subjects', []) if isinstance(subject, dict))]
        verified = year == coverage['reference_year'] and (unit_id in coverage['verified_unit_ids'] if unit_id else coverage['catalog_complete'])
        status = 'available' if rows else 'not_collected' if not scope else 'no_match_in_verified_catalog' if verified else 'no_match_in_partial_catalog'
        return {'programs': rows, 'total': len(rows), 'coverage': coverage, 'status': status, 'year': year, 'unit_id': unit_id}

    def subjects(self, unit_id=None, year=None, q=''):
        result = self.programs(unit_id=unit_id, year=year)
        needle = _text(q).casefold()
        subjects = {}
        for program in result['programs']:
            if exam_unresolved_reason(program):
                continue
            for subject in program.get('subjects', []):
                if not isinstance(subject, dict) or not all(isinstance(subject.get(key), str) for key in ('code', 'name', 'scope')):
                    continue
                if needle not in f"{subject['code']} {subject['name']} {program.get('unit_name', '')}".casefold():
                    continue
                key = (subject['code'], subject['name'], subject['scope'], program['year'])
                previous = subjects.get(key, {**{field: subject[field] for field in ('code', 'name', 'scope')},
                                              'unit_name': '全国统一命题' if subject['scope'] == 'national' else program.get('unit_name'), 'year': program['year'], 'program_ids': []})
                subjects[key] = {**previous, 'program_ids': list(dict.fromkeys([*previous['program_ids'], program['id']]))}
        coverage = result['coverage']
        verified = result['year'] == coverage['reference_year'] and (unit_id in coverage['verified_unit_ids'] if unit_id else coverage['catalog_complete'])
        status = 'available' if subjects else result['status'] if not result['programs'] else 'no_match_in_verified_catalog' if verified else 'no_match_in_partial_catalog'
        return {'subjects': list(subjects.values()), 'total': len(subjects), 'coverage': coverage,
                'status': status,
                'year': result['year'], 'unit_id': unit_id}

    @staticmethod
    def _payload(payload, required, allowed):
        if not isinstance(payload, dict) or set(payload) - set(allowed) or not set(required) <= set(payload):
            raise ServiceError('invalid_payload', '请求字段缺失或包含不支持的字段。')

    @staticmethod
    def _unresolved_exams(rows, registry, admissions, verifier):
        documents={row['id']:row for row in admissions['documents']}
        units={row['id']:row for row in registry['units']}
        result=[]
        for row in rows:
            reason=exam_unresolved_reason(row)
            if not reason: continue
            refs=row.get('evidence')
            refs=[refs] if isinstance(refs,dict) else refs if isinstance(refs,list) else []
            ids=[row.get('catalog_document_id'),*[ref.get('document_id') for ref in refs if isinstance(ref,dict)]]
            unit=units.get(row.get('unit_id'),{})
            source=next((documents[identity] for identity in ids if isinstance(identity,str) and identity in documents
                and documents[identity].get('unit_id')==row.get('unit_id') and documents[identity].get('year')==row.get('year')
                and verifier.document_allowed(documents[identity],unit,SOURCE_KINDS) and verifier.read(documents[identity])),{})
            result=[*result,{'program_id':row['id'],**{field:row.get(field) for field in
                ('unit_id','unit_name','department','name','code','direction','year','study_mode')},
                'reason':reason,'url':source.get('url','')}]
        return result

    def _matching_input(self, payload):
        try:
            profile = self._engine().validate_profile(payload['profile'])
        except (ValueError,TypeError) as error:
            raise ServiceError('invalid_profile','个人条件格式不正确，请检查字段与取值。') from error
        selected = payload.get('unit_ids')
        if selected is not None and (not isinstance(selected,list) or not selected or len(selected)>2000
                                     or any(not isinstance(item,str) or not item for item in selected)):
            raise ServiceError('unknown_unit','筛选的招生单位不存在或格式不正确。')
        state = self._published('scope', selected) if self._catalog.available() else None
        if state:
            registry, admissions = state['registry'], state['admissions']
            coverage = {**state['coverage'], 'revision':state['revision'], 'originals_revision':state['originals_revision']}
        else:
            registry, admissions, coverage, _ = self._state()
        if selected is not None and (not isinstance(selected, list) or not selected or len(selected) > 2000 or any(not isinstance(item, str) or item not in {unit.get('id') for unit in registry['units']} for item in selected)):
            raise ServiceError('unknown_unit', '筛选的招生单位不存在或格式不正确。')
        try:
            _, year, _ = self._query(registry, admissions, year=profile.get('reference_year'))
            unresolved=self._unresolved_exams([row for row in admissions['programs'] if row.get('year')==year
                        and (selected is None or row.get('unit_id') in selected)],registry,admissions,
                        state['verifier'] if state else self.verifier)
            coverage={**coverage,'exam_unresolved_programs':unresolved,'exam_unresolved_program_count':len(unresolved)}
            programs = (self._catalog.matching_programs(state,year) if state else
                        [program for program in admissions['programs'] if program.get('year') == year
                         and (selected is None or program.get('unit_id') in selected) and not exam_unresolved_reason(program)])
        except (ValueError, TypeError) as error:
            raise ServiceError('invalid_profile', '个人条件格式不正确，请检查字段与取值。') from error
        return programs, profile, selected, coverage

    @staticmethod
    def _matching_metadata(programs, selected, coverage):
        ids = list(dict.fromkeys(selected if selected is not None else [program['unit_id'] for program in programs]))
        return {'coverage': coverage, 'release_ready': coverage['release_ready'], 'historical_reference': True,
                'exam_unresolved_programs':coverage.get('exam_unresolved_programs',[]),
                **{key:coverage[key] for key in ('revision','originals_revision') if key in coverage},
                'scope': {'mode': 'national' if coverage['release_ready'] else 'research_preview', 'unit_ids': ids,
                          'requested_scope': 'explicit_units' if selected else 'national',
                          'complete': bool(ids) and all(unit_id in coverage['verified_unit_ids'] for unit_id in ids)},
                'warnings': ([] if coverage['release_ready'] else ['全国目录尚未完成完整性核验；结果仅适用于当前已收录项目的研究预览。'])
                    + ([f"有{coverage['exam_unresolved_program_count']}个方向的原文科目套组或代码名称仍待核实，未参与本次算法比较。"] if coverage.get('exam_unresolved_program_count') else [])}

    def match(self, payload):
        self._payload(payload, ('profile',), ('profile', 'unit_ids'))
        programs, profile, selected, coverage = self._matching_input(payload)
        try:
            result = self._engine().match_programs(programs, profile)
        except (ValueError, TypeError) as error:
            raise ServiceError('invalid_profile', '个人条件格式不正确，请检查字段与取值。') from error
        return {**result, **self._matching_metadata(programs, selected, coverage)}

    def sensitivity(self, payload):
        self._payload(payload, ('profile', 'scenarios'), ('profile', 'unit_ids', 'scenarios'))
        scenarios = payload['scenarios']
        if not isinstance(scenarios, list) or len(scenarios) > 12 or any(not isinstance(item, dict) for item in scenarios):
            raise ServiceError('invalid_scenarios', '条件试算最多支持十二个有效场景。')
        programs, profile, selected, coverage = self._matching_input(payload)
        try:
            result = self._engine().sensitivity_analysis(programs, profile, scenarios)
        except (ValueError, TypeError) as error:
            raise ServiceError('invalid_scenarios', '试算场景包含不支持的条件或取值。') from error
        return {**result, **self._matching_metadata(programs, selected, coverage)}

    def route_plan(self, payload):
        self._payload(payload, ('profile',), ('profile', 'unit_ids'))
        programs, profile, selected, coverage = self._matching_input(payload)
        try:
            result = importlib.import_module('planning_engine').plan_routes(programs, profile)
        except (ValueError, TypeError) as error:
            raise ServiceError('invalid_profile', '路线条件格式不正确，请检查个人条件。') from error
        return {**result, **self._matching_metadata(programs, selected, coverage)}

    def adjustments(self, payload):
        self._payload(payload, ('profile', 'allowed_changes'), ('profile', 'unit_ids', 'allowed_changes', 'goal_min_candidates'))
        programs, profile, selected, coverage = self._matching_input(payload)
        try:
            result = importlib.import_module('planning_engine').suggest_adjustments(programs, profile, payload['allowed_changes'],
                                                                                  goal_min_candidates=payload.get('goal_min_candidates', 1))
        except (ValueError, TypeError) as error:
            raise ServiceError('invalid_adjustments', '允许调整项或候选目标格式不正确。') from error
        return {**result, **self._matching_metadata(programs, selected, coverage)}

    def _program(self, registry, admissions, identity):
        identity = _text(identity, 300)
        program = next((row for row in admissions['programs'] if row.get('id') == identity), None)
        unit = next((row for row in registry['units'] if program and row.get('id') == program.get('unit_id')), None)
        if not program or not unit:
            raise ServiceError('unknown_program', '该专业方向尚未收录到当前资料库。', 404)
        return program, unit

    def source_tasks(self, program_id):
        if self._catalog.available():
            state=self._published('scope_for_program',_text(program_id,300))
            registry,admissions,coverage=state['registry'],state['admissions'],state['coverage']
        else:
            registry, admissions, coverage, _ = self._state()
        program, _ = self._program(registry, admissions, program_id)
        try:
            return _public_document(self.proof.tasks(program, admissions['documents'], coverage))
        except ProofError as error:
            raise ServiceError(error.code, error.message, error.status) from error

    def evidence(self, program_id,revision=None):
        if self._catalog.available() or revision is not None:
            program_id = _text(program_id,300)
            state = self._published('evidence_scope',program_id,revision=revision)
            result=EvidenceIndex(state['registry'],state['admissions'],self.data_dir,state['verifier']).program(program_id)
            return {**result,'revision':state['revision'],'originals_revision':state['originals_revision']}
        registry, admissions = self._snapshot()
        program, _ = self._program(registry, admissions, program_id)
        return EvidenceIndex(registry, admissions, self.data_dir, self.verifier).program(program['id'])

    def sources(self, unit_id=None, year=None, kind=None, q=''):
        state=self._published('scope',[unit_id]) if self._catalog.available() and unit_id is not None else None
        registry,admissions=(state['registry'],state['admissions']) if state else self._snapshot()
        unit_id, checked_year, query = self._query(registry, admissions, unit_id, year, q)
        if kind is not None:
            kind = _text(kind, 40)
            if kind not in SOURCE_KINDS:
                raise ServiceError('invalid_kind', '该资料类型不在已收录来源范围内。')
        return EvidenceIndex(registry, admissions, self.data_dir, state['verifier'] if state else self.verifier).sources(
            unit_id=unit_id, year=checked_year if year is not None else None, kind=kind, q=query)

    def source_proof(self, payload):
        self._payload(payload, ('program_id', 'kind'), ('program_id', 'kind'))
        if not isinstance(payload['kind'], str) or payload['kind'] not in PROOF_KINDS:
            raise ServiceError('invalid_kind', '不支持的补证类型。')
        if self._catalog.available():
            state=self._published('scope_for_program',_text(payload['program_id'],300))
            registry,admissions=state['registry'],state['admissions']
        else:
            registry, admissions = self._snapshot()
        program, unit = self._program(registry, admissions, payload['program_id'])
        try:
            return _public_document(self.proof.investigate(program, unit, payload['kind']))
        except ProofError as error:
            raise ServiceError(error.code, error.message, error.status) from error

    def radar_status(self, program_id=None, unit_id=None, year=None):
        try:
            if program_id is None and unit_id is None and year is None:
                return _public_document(self.radar.status())
            if self._catalog.available():
                ids=[program_id] if program_id is not None else self.radar.status()['program_ids']
                state=(self._published('scope',[unit_id]) if unit_id is not None else
                       self._published('scope_for_programs',ids,allow_missing=True))
                registry,admissions=state['registry'],state['admissions']
            else:
                registry, admissions = self._snapshot()
            if program_id is not None: self._program(registry, admissions, program_id)
            unit_id, checked_year, _ = self._query(registry, admissions, unit_id, year)
            watched = set(self.radar.status()['program_ids'])
            ids = [row['id'] for row in admissions['programs'] if (program_id is None or row['id'] == program_id)
                   and row['id'] in watched and (unit_id is None or row.get('unit_id') == unit_id)
                   and (year is None or row.get('year') == checked_year)]
            return _public_document(self.radar.status(program_ids=ids))
        except (ValueError, TypeError, OSError) as error:
            raise ServiceError('radar_unavailable', '招生雷达状态需要重新校验，已保留原记录。', 503) from error

    def radar_watch(self, payload):
        self._payload(payload, ('program_ids',), ('program_ids',))
        if self._catalog.available():
            state=self._published('scope_for_programs',payload['program_ids'])
            registry,admissions=state['registry'],state['admissions']
        else:
            registry, admissions = self._snapshot()
        try:
            return _public_document(self.radar.watch(payload['program_ids'], programs=admissions['programs'], documents=admissions['documents'], units=registry['units']))
        except (ValueError, TypeError) as error:
            raise ServiceError('invalid_watch', '请选择至多100个已有官方目录来源的专业方向。') from error
        except OSError as error:
            raise ServiceError('radar_unavailable', '招生雷达记录暂不可写入，已保留原记录。', 503) from error

    def radar_check(self, payload, stop_event=None):
        self._payload(payload, (), ())
        if not self.radar_status().get('watch_count'):
            return self.radar_status()
        if self._catalog.available():
            state=self._published('scope_for_programs',self.radar_status()['program_ids'],allow_missing=True)
            registry,admissions=state['registry'],state['admissions']
        else:
            registry, admissions = self._snapshot()
        try:
            return _public_document(self.radar.check(programs=admissions['programs'], documents=admissions['documents'], units=registry['units'], stop_event=stop_event))
        except (ValueError, TypeError, OSError) as error:
            raise ServiceError('radar_unavailable', '招生雷达检查暂不可完成，已保留原记录。', 503) from error

    def history_match(self, payload):
        self._payload(payload, ('kind', 'year', 'facts'), ('kind', 'year', 'facts'))
        if not isinstance(payload['kind'], str) or payload['kind'] not in {'retest', 'transfer'} or not isinstance(payload['facts'], dict):
            raise ServiceError('invalid_payload', '历史匹配类型或条件格式不正确。')
        if self._catalog.available():
            history=self._published('historical_rules',payload['kind'],payload['year'])
            year,rules=payload['year'],history['rules']
        else:
            history=None
            registry, admissions = self._snapshot()
            _, year, _ = self._query(registry, admissions, year=payload['year'])
            rules = [rule for rule in admissions[f"{payload['kind']}_rules"] if rule.get('year') == year]
        try:
            result = self._engine().match_historical_rules(rules, payload['facts'], year)
        except (ValueError, TypeError) as error:
            raise ServiceError('invalid_profile', '历史匹配条件格式不正确。') from error
        return {**result, 'data_status': 'available' if rules else 'needs_review' if history and history['stored_count'] else 'not_collected', 'historical_reference': True,
                **({key:history[key] for key in ('revision','verification_mode','unverified_count')} if history else {}),
                'notice': '历史规则不能代替目标年度的复试或调剂公告。'}

    def _search_response(self, request):
        from yuanbao_auth import checked_response
        from provider_catalog import TransportError
        try:
            with self._opener(request, timeout=15) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ServiceError('search_invalid_response', '检索响应过大，请缩小查询范围。', 502)
            value = json.loads(raw)
            return checked_response(value)
        except TransportError as error:
            codes = {'provider_auth_failed':'search_auth_failed', 'provider_permission_denied':'search_auth_failed',
                     'signature_expired':'search_auth_failed', 'provider_rate_limited':'search_rate_limited',
                     'search_not_enabled':'search_unavailable', 'search_unavailable':'search_unavailable',
                     'provider_request_failed':'search_unavailable'}
            code = codes.get(error.code, 'search_invalid_response')
            raise ServiceError(code, error.message, 429 if code == 'search_rate_limited' else error.status) from None
        except HTTPError as error:
            code = 'search_rate_limited' if error.code == 429 else 'search_auth_failed' if error.code in {401, 403} else 'search_unavailable'
            error.close()
            raise ServiceError(code, '元宝检索暂不可用，请检查服务配置、额度或稍后重试。', 429 if error.code == 429 else 503) from error
        except (URLError, TimeoutError, OSError) as error:
            raise ServiceError('search_unavailable', '元宝检索连接失败，请稍后重试。', 503) from error
        except (ValueError, TypeError, UnicodeError) as error:
            raise ServiceError('search_invalid_response', '元宝检索返回的数据需要重新核验。', 502) from error

    def search(self, payload):
        self._payload(payload, ('unit_id', 'year', 'kind'), ('unit_id', 'year', 'kind', 'scope'))
        scope = payload.get('scope', 'official')
        if scope not in SEARCH_SCOPES:
            raise ServiceError('invalid_scope', '检索范围只支持 official 或 extended。')
        if self._catalog.available():
            state = self._published('scope', [_text(payload['unit_id'], 100)])
            registry, admissions = state['registry'], state['admissions']
        else:
            registry, admissions = self._snapshot()
        unit_id, year, _ = self._query(registry, admissions, payload['unit_id'], payload['year'])
        if not isinstance(payload['kind'], str) or payload['kind'] not in SEARCH_KINDS:
            raise ServiceError('invalid_kind', '不支持的检索资料类型。')
        config = self._search_config()
        if not config:
            raise ServiceError('not_configured', '平台尚未配置元宝检索服务，现有资料与规则筛选仍可使用。', 503)
        # Extra official domains exist only when a reviewed binding proves them
        # (see unit_bindings); a missing binding is reported as such instead of
        # being turned into "the official site has no material".
        unit = unit_bindings.enrich(next(unit for unit in registry['units'] if unit['id'] == unit_id))
        roots = official_roots(unit)
        site = roots[0] if roots else None
        variants = official_search.search_variants(unit['name'], year, payload['kind'])
        from yuanbao_auth import build_request
        from provider_catalog import TransportError

        def call(request_body):
            try:
                url, raw, headers = build_request(config, request_body)
            except TransportError as error:
                raise ServiceError(error.code, error.message, error.status) from None
            return self._search_response(Request(url, raw, headers=headers, method='POST'))

        def is_official(url):
            return official_source_url(url, unit)

        binding_missing = not roots
        result = official_search.run_ladder(
            call, queries=variants, is_official=is_official, unit_name=unit['name'], roots=roots,
            candidate_enabled=True, site=site, include_third=scope == 'extended',
            target_match=lambda row:official_search.target_matches(row,year,payload['kind']),
            third_query=f"{unit['name']} 考研 经验 报录比 参考书" if scope == 'extended' else None)
        official_results = result['official_results']
        candidates = result['candidate_results']
        third_results = result['third_results']
        outcome = ('official_results' if official_results else
                   'official_domain_candidates' if candidates else
                   'extended_reference' if third_results else 'no_official_results')
        stage = official_search.failure_stage(official_results, candidates, third_results,
                                              result['rejected_count'], binding_missing)
        return {'provider': 'yuanbao_search', 'query': variants[0], 'queries_tried': result['queries_tried'],
                'scope': scope, 'outcome': outcome, 'third_party_count': len(third_results),
                'candidate_count': result['candidate_count'], 'candidate_hosts': result['candidate_hosts'],
                'checked_at': _now(), 'request_id': result['request_id'],
                'results': result['results'], 'rejected_count': result['rejected_count'],
                'malformed_count': result['malformed_count'], 'site_fallback': result['site_fallback'],
                'request_count': result['request_count'], 'failure_stage': stage,
                'binding_status': 'missing' if binding_missing else 'bound',
                'binding_domains': list(roots),
                'target_status':result['target_status'],'target_results':result['target_results'],
                'navigation_results':result['navigation_results'],
                'notice': official_search.notice(official_results, candidates, third_results,
                                                 len(result['queries_tried']), binding_missing=binding_missing)}
