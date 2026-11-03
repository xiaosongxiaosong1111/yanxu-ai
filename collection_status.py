"""Collection progress is separate from the last recorded catalogue audit."""
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit
from catalog_facts_store import CatalogFactsStore, CatalogFactsError
from json_reader import read_json, SnapshotReadError, STREAM_THRESHOLD, _finite_float, _stream_parser


CATALOG_STATES = {'recorded_verified', 'partial', 'not_collected'}
FETCH_STATES = {'pending', 'running', 'crawling', 'sources_collected', 'collected', 'needs_binding',
                'truncated', 'restricted', 'failed', 'not_found'}
ERRORS = {'timeout': '官方原文读取超时，待重试。', 'restricted': '来源要求登录或限制访问。',
          'wrong_unit': '来源归属未通过核验。', 'unsupported_type': '原文格式尚待适配。',
          'fetch_error': '官方原文暂时无法读取。', 'too_large': '附件超过读取上限，待单独处理。'}


def _skip_bom(stream):
    if stream.read(3) != b'\xef\xbb\xbf':
        stream.seek(0)


def _validate_finite_numbers(value):
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, Decimal):
            _finite_float(current)
        elif isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def _admissions_projection(path):
    parser = _stream_parser()
    try:
        with path.open('rb') as stream:
            _skip_bom(stream)
            events = parser.parse(stream, use_float=False)
            first = next(events, None)
            if first is None or first[:2] != ('', 'start_map'):
                raise SnapshotReadError('采集进度文件格式不正确。')
            metadata_seen = False
            for prefix, event, _ in events:
                if prefix == 'metadata' and not metadata_seen:
                    metadata_seen = True
                    if event != 'start_map':
                        raise SnapshotReadError('采集进度文件格式不正确。')
                if prefix == 'programs':
                    if event != 'start_array':
                        raise SnapshotReadError('采集进度文件格式不正确。')
                    break
                if prefix == '' and event == 'end_map':
                    break
            del events
            stream.seek(0)
            _skip_bom(stream)
            items = parser.items(stream, 'metadata', use_float=False)
            metadata = next(items, {})
            del items
            if not isinstance(metadata, dict):
                raise SnapshotReadError('采集进度文件格式不正确。')
            _validate_finite_numbers(metadata)
            programs = []
            stream.seek(0)
            _skip_bom(stream)
            items = parser.items(stream, 'programs.item', use_float=False)
            for row in items:
                if not isinstance(row, dict):
                    raise SnapshotReadError('采集进度文件格式不正确。')
                _validate_finite_numbers(row)
                unit_id, year = row.get('unit_id'), row.get('year')
                programs.append({'unit_id': _finite_float(unit_id) if isinstance(unit_id, Decimal) else unit_id,
                                 'year': _finite_float(year) if isinstance(year, Decimal) else year})
            del items
        return {'metadata': metadata, 'programs': programs}
    except (parser.JSONError, StopIteration, OverflowError) as error:
        raise SnapshotReadError('Invalid JSON source data.') from error


def _dict_rows(container, key):
    rows = container.get(key, [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise SnapshotReadError('采集进度文件格式不正确。')
    return rows


def _optional_text(container, key):
    if container.get(key) is not None and not isinstance(container[key], str):
        raise SnapshotReadError('采集进度文件格式不正确。')


def _validate_snapshot(filename, value):
    if not isinstance(value, dict) or not isinstance(value.get('metadata', {}), dict):
        raise SnapshotReadError('采集进度文件格式不正确。')
    fields = {'units.json': ('units',), 'admissions.json': ('programs',),
              'national_sources.json': ('units',), 'national_catalog.json': ('units',)}.get(filename, ())
    if filename.startswith('annual_registry_') and filename.endswith('.json'):
        fields = ('units', 'documents')
    for field in fields:
        _dict_rows(value, field)
    if filename == 'units.json' and any(not isinstance(row.get('id'), str) or not row['id']
            or not isinstance(row.get('name'), str) for row in value.get('units', [])):
        raise SnapshotReadError('采集进度文件格式不正确。')
    if filename == 'admissions.json':
        metadata = value.get('metadata', {})
        if type(metadata.get('reference_year', 2027)) is not int:
            raise SnapshotReadError('采集进度文件格式不正确。')
        _dict_rows(metadata, 'unit_catalog_status')
    if filename == 'national_sources.json':
        for row in value.get('units', []):
            _optional_text(row, 'status')
            for field in ('documents', 'pages', 'errors', 'frontier'):
                _dict_rows(row, field)
            for error in row.get('errors', []):
                _optional_text(error, 'code')
    if filename == 'national_catalog.json':
        for row in value.get('units', []):
            for file in _dict_rows(row, 'files'):
                _optional_text(file, 'status')
    if filename.startswith('annual_registry_') and filename.endswith('.json'):
        for document in value.get('documents', []):
            local_path = document.get('local_path', '')
            if not isinstance(local_path, str) or '\0' in local_path:
                raise SnapshotReadError('采集进度文件格式不正确。')
            _optional_text(document, 'sha256')


def _url(value):
    try:
        parts = urlsplit(value)
        return value if (parts.scheme in {'http', 'https'} and parts.hostname and not parts.username
                         and not parts.password and parts.port in {None, 80, 443}) else ''
    except (ValueError, TypeError, AttributeError):
        return ''


def _saved_source(value):
    return (isinstance(value, dict) and value.get('classification') != 'access_gate'
            and value.get('status') in (None, '', 'fetched', 'parsed', 'verified')
            and bool(_url(value.get('url'))))


def _reason(count, checked, source):
    if checked:
        return '上次入库已完成该校目录对账；报考仍需核对目标年度和具体资格。'
    if count:
        return '已有部分专业资料，全校目录及全部考试科目尚未对账完整。'
    state = source.get('status')
    restricted = (state == 'restricted' or any(
        isinstance(item, dict) and (item.get('status') == 'restricted' or item.get('classification') == 'access_gate')
        for item in [*source.get('documents', []), *source.get('pages', [])]) or any(
        isinstance(item, dict) and item.get('code') == 'restricted' for item in source.get('errors', [])))
    if restricted:
        return '来源要求登录或限制访问，尚未完成公开原文采集。'
    if any(_saved_source(item) for item in source.get('documents', [])):
        return '已保存官方原文，专业目录尚待解析或核验，不能据此认定没有招生专业。'
    if state in {'running', 'crawling'}:
        return '正在采集该单位的官方入口与目录。'
    if state == 'needs_binding':
        return '尚未核准该单位的官方招生网站入口。'
    if state in {'failed', 'truncated'}:
        return '目录页面或附件仍有采集缺口，待继续处理。'
    if state in {'not_found', 'sources_collected', 'collected'}:
        return '本轮尚未找到可核验的专业目录原文，不能认定该校未招生。'
    return '尚未完成该单位目录采集，未收录不代表不存在。'


def _parsing(row):
    notes = {'needs_adapter':'原文已保存，年度、表头、学习方式或科目组合尚未通过格式核验。',
             'failure':'原文完整性或解析执行未通过核验，待复查。',
             'restricted':'官方来源要求验证或限制访问。',
             'not_catalog':'该留档原文未解析出本年度统考目录，不能据此认定专业不存在。'}
    files = [file for file in row.get('files', []) if isinstance(file, dict)]
    counts = Counter(file.get('status') for file in files if file.get('status') in {*notes, 'ready'})
    status = ('pending' if not row else 'needs_adapter' if counts['needs_adapter'] else
              'source_check_failed' if counts['failure'] or counts['restricted'] else
              'ready_for_ingestion' if counts['ready'] else 'no_catalog_rows')
    gaps = [{'url':file['url'],'status':file['status'],'parser':str(file.get('parser') or '')[:80],
             'reason':notes[file['status']]}
            for file in files if file.get('status') in notes and _url(file.get('url'))]
    return {'parsing_status':status,'parsing_summary':dict(counts),'parsing_gaps':gaps[:12]}


class CollectionStatus:
    def __init__(self, root, facts_store=None):
        self.root = Path(root)
        self._cache = {}
        self._annual_checks = {}
        self._lock = threading.Lock()
        self._facts = facts_store or CatalogFactsStore(self.root)

    def _read(self, filename):
        path = self.root / filename
        try:
            info = path.stat()
            key = (info.st_ino, info.st_ctime_ns, info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            return {}
        with self._lock:
            cached = self._cache.get(filename)
            if cached and cached[0] == key:
                return cached[1]
            value = (_admissions_projection(path)
                     if filename == 'admissions.json' and info.st_size > STREAM_THRESHOLD
                     else read_json(path))
            _validate_snapshot(filename, value)
            self._cache = {**self._cache, filename: (key, value)}
            return value

    @staticmethod
    def _source_rows(source):
        values = [*source.get('documents', []), *source.get('pages', [])]
        result = {}
        for value in values:
            if not _saved_source(value):
                continue
            url = value['url']
            result.setdefault(url, {key: value.get(key) for key in
                ('id', 'url', 'title', 'kind', 'year', 'year_status', 'sha256', 'checked_at', 'status', 'content_type')})
        return list(result.values())

    def _rows(self):
        registry, admissions = self._read('units.json'), self._read('admissions.json')
        national = self._read('national_sources.json')
        parsing = self._read('national_catalog.json')
        metadata = admissions.get('metadata', {})
        year = metadata.get('reference_year', 2027)
        try: fact_counts = self._facts.counts(year)
        except CatalogFactsError: fact_counts = {}
        annual = self._read(f'annual_registry_{year}.json')
        source_by_id = {str(row.get('unit_id')): row for row in national.get('units', []) if row.get('year') == year}
        parsing_by_id = {str(row.get('unit_id')): row for row in parsing.get('units', []) if row.get('year') == year}
        annual_by_id = {str(row.get('unit_id', row.get('id'))): row for row in annual.get('units', [])}
        counts = Counter(str(row.get('unit_id')) for row in admissions.get('programs', []) if row.get('year') == year)
        checks = {str(row.get('unit_id')): row for row in metadata.get('unit_catalog_status', []) if row.get('year') == year}
        rows = []
        for unit in registry.get('units', []):
            identifier = str(unit['id'])
            source, check = source_by_id.get(identifier, {}), checks.get(identifier, {})
            count = counts[identifier]
            checked = bool(count and check.get('status') == 'verified' and all(
                type(check.get(key)) is int and check[key] == count
                for key in ('expected_records', 'parsed_records', 'verified_records')))
            state = source.get('status', 'pending')
            state = state if state in FETCH_STATES else 'pending'
            sources = self._source_rows(source)
            rows.append({'unit_id': identifier, 'unit_code': unit.get('unit_code'), 'unit_name': unit['name'],
                'province': unit.get('province'), 'year': year, 'program_count': count,
                'fact_count': fact_counts.get(identifier, 0),
                'catalog_status': 'recorded_verified' if checked else 'partial' if count else 'not_collected',
                'collection_status': state, 'annual_status': annual_by_id.get(identifier, {}).get('annual_status', 'unknown'),
                'checked_at': source.get('checked_at') or check.get('checked_at'),
                'source_count': len(sources), 'sources': sources[:40],
                'pending_pages': len(source.get('frontier', [])),
                'errors': [{'code': str(item.get('code', 'unknown'))[:60],
                            'message': ERRORS.get(item.get('code'), '该来源尚未完成采集或核验。')}
                           for item in source.get('errors', [])[:8] if isinstance(item, dict)],
                'official_url': _url(unit.get('official_url')) or _url(unit.get('source_url')),
                'missing_reason': _reason(count, checked, source), **_parsing(parsing_by_id.get(identifier, {}))})
        annual_meta = annual.get('metadata', {})
        if annual_meta.get('annual_scope_status') == 'verified':
            from collectors.annual_registry import verify_annual_registry
            source_state = []
            for doc in annual.get('documents', []):
                try:
                    path = Path(doc.get('local_path', ''))
                    path = path if path.is_absolute() else (self.root.parent / path if path.parts and path.parts[0] == self.root.name else self.root / path)
                    info = path.stat()
                    source_state.append((doc.get('sha256'), info.st_ino, info.st_ctime_ns, info.st_mtime_ns, info.st_size))
                except OSError:
                    source_state.append(None)
            token = (self._cache.get(f'annual_registry_{year}.json', (None,))[0],
                     self._cache.get('units.json', (None,))[0], tuple(source_state))
            with self._lock:
                if token not in self._annual_checks:
                    valid = verify_annual_registry(annual, registry, self.root.parent)
                    self._annual_checks = {token: valid}
                valid = self._annual_checks[token]
            if not valid:
                annual_meta = {**annual_meta, 'annual_scope_status': 'needs_review'}
                rows = [{**row, 'annual_status': 'unknown'} for row in rows]
        return year, rows, national.get('metadata', {}), annual_meta

    def query(self, *, unit_id=None, q='', status='', page=1, page_size=20):
        if not isinstance(q, str) or len(q) > 120 or status not in {'', *CATALOG_STATES, *FETCH_STATES}:
            raise ValueError('采集进度筛选条件不正确。')
        if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError('页码或每页数量不正确。')
        year, all_rows, collection_meta, annual_meta = self._rows()
        if unit_id is not None and (not isinstance(unit_id, str) or unit_id not in {row['unit_id'] for row in all_rows}):
            raise ValueError('招生单位不在当前索引中。')
        query = q.strip().casefold()
        rows = [row for row in all_rows if (unit_id is None or row['unit_id'] == unit_id)
                and (not status or row['catalog_status'] == status or row['collection_status'] == status)
                and query in ' '.join(str(row.get(key) or '') for key in ('unit_name', 'unit_code', 'province')).casefold()]
        start = (page - 1) * page_size
        return {'year': year, 'units': rows[start:start + page_size], 'total': len(rows), 'page': page,
                'page_size': page_size, 'has_more': start + page_size < len(rows),
                'summary': {'indexed_units': len(all_rows),
                    'annual_confirmed_units': sum(row['annual_status'] == 'confirmed' for row in all_rows),
                    'annual_official_total': annual_meta.get('official_total', annual_meta.get('annual_official_total')) if annual_meta.get('annual_scope_status') == 'verified' else None,
                    'annual_scope_status': annual_meta.get('annual_scope_status', 'unknown'),
                    'annual_units_with_programs': sum(row['annual_status'] == 'confirmed' and row['program_count'] > 0 for row in all_rows),
                    'annual_units_without_programs': sum(row['annual_status'] == 'confirmed' and not row['program_count'] for row in all_rows),
                    'not_listed_index_units': sum(row['annual_status'] == 'not_listed' for row in all_rows),
                    'units_with_programs': sum(row['program_count'] > 0 for row in all_rows),
                    'program_count': sum(row['program_count'] for row in all_rows),
                    'recorded_verified_units': sum(row['catalog_status'] == 'recorded_verified' for row in all_rows),
                    'catalog_states': dict(Counter(row['catalog_status'] for row in all_rows)),
                    'collection_states': dict(Counter(row['collection_status'] for row in all_rows)),
                    'national_complete': False, 'checked_at': collection_meta.get('checked_at')},
                'notice': '院校索引数不等于当年硕士招生单位数。采集完成不等于全校目录完整；目录状态为上次入库核验记录，具体字段请查看原文依据。'}
