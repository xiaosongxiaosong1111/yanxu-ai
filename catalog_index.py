"""Published catalog revisions with bounded, read-only SQLite queries."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote


class CatalogIndexError(RuntimeError):
    pass


class CatalogRevisionError(CatalogIndexError):
    code = 'revision_changed'


HISTORY_KINDS = ('score_lines', 'retest_rules', 'transfer_rules', 'notices', 'admitted_statistics')
SCHEMA_VERSION = 5


def encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError) as error:
        raise CatalogIndexError('目录发布数据不是有效JSON。') from error


def bounded_text(value, maximum, label, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise CatalogIndexError(label + '格式不正确。')
    return value


def page_arguments(unit_id, year, q, offset, limit):
    bounded_text(unit_id, 100, '招生单位', optional=True)
    bounded_text(q, 200, '检索词')
    if year is not None and (type(year) is not int or not 2000 <= year <= 2100):
        raise CatalogIndexError('目录年度不正确。')
    if type(offset) is not int or not 0 <= offset <= 10000000:
        raise CatalogIndexError('分页起点不正确。')
    if type(limit) is not int or not 1 <= limit <= 200:
        raise CatalogIndexError('每页条数应为1至200。')


def _validate_rows(value, label):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise CatalogIndexError(label + '列表不正确。')
    ids = [row.get('id') for row in value]
    if any(not isinstance(identifier, str) or not identifier for identifier in ids) or len(ids) != len(set(ids)):
        raise CatalogIndexError(label + '身份缺失或重复。')
    for identifier in ids: bounded_text(identifier, 512, label + '身份')


def _year(value, label, *, unknown=False):
    if unknown and value is None: return
    if type(value) is not int or not 2000 <= value <= 2100:
        raise CatalogIndexError(label + '必须为有效整数年度。')


def _metadata(value, label):
    if not isinstance(value, dict): raise CatalogIndexError(label + '格式不正确。')
    for field in ('reference_year', 'target_year'):
        if field in value: _year(value[field], label + field)
    for field in ('complete', 'catalog_complete', 'release_ready', 'registry_complete'):
        if field in value and type(value[field]) is not bool:
            raise CatalogIndexError(label + field + '必须为布尔值。')
    encoded(value)
    return value


def _validate_coverage(value, unit_ids):
    states = value.get('unit_catalog_status', [])
    if not isinstance(states, list) or any(not isinstance(row, dict) for row in states):
        raise CatalogIndexError('单位覆盖状态必须为对象列表。')
    ids = [row.get('unit_id') for row in states]
    if any(not isinstance(identity, str) or identity not in unit_ids for identity in ids) or len(ids) != len(set(ids)):
        raise CatalogIndexError('单位覆盖状态身份缺失、重复或不属于单位索引。')
    for row in states:
        if 'year' in row: _year(row['year'], '单位覆盖状态年度')
        if 'status' in row: bounded_text(row['status'], 100, '单位覆盖状态')
        if 'verified' in row and type(row['verified']) is not bool:
            raise CatalogIndexError('目录核验标记必须为布尔值。')


def _scope_arguments(unit_ids):
    if not isinstance(unit_ids, (list, tuple)) or not 1 <= len(unit_ids) <= 2000:
        raise CatalogIndexError('请明确选择1至2000个招生单位。')
    for identifier in unit_ids:
        bounded_text(identifier, 100, '招生单位')
        if not identifier: raise CatalogIndexError('招生单位不能为空。')
    return list(dict.fromkeys(unit_ids))


def _like(value):
    return '%' + value.strip().casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


SEARCH_SYNONYMS = {'专硕': '专业学位', '学硕': '学术学位', '非全': '非全日制'}


def _search_clause(value):
    """Require every whitespace-separated term to match the same row.

    A single substring test made the common query `南开大学 计算机` fail: no
    row's search text contains that exact string even though it contains both
    parts. AND-ing the terms keeps the parameterized, escaped LIKE semantics
    (terms are still literals, never SQL) while matching how users actually
    search. A short colloquial term also matches its catalog wording, but the
    literal term is kept so the synonym can never hide an exact hit. Empty
    input already short-circuits before this helper is called.
    """
    clauses, parameters = [], []
    for term in value.strip().split():
        variants = [term]
        synonym = SEARCH_SYNONYMS.get(term.casefold())
        if synonym:
            variants.append(synonym)
        clauses.append('(' + ' OR '.join("search_text LIKE ? ESCAPE '\\'" for _ in variants) + ')')
        parameters.extend(_like(variant) for variant in variants)
    return clauses, parameters


def _validate_subjects(program):
    subjects = program.get('subjects', [])
    if not isinstance(subjects, list) or any(not isinstance(row, dict) for row in subjects):
        raise CatalogIndexError('初试科目必须为对象列表。')
    seen = set()
    for subject in subjects:
        for field, maximum in (('code', 32), ('name', 500), ('scope', 100)):
            bounded_text(subject.get(field), maximum, '科目' + field)
            if not subject[field].strip(): raise CatalogIndexError('初试科目字段不能为空。')
        if subject['scope'] not in {'national', program['unit_id']}:
            raise CatalogIndexError('自命题科目作用域不属于本校。')
        if type(subject.get('exam_unit')) is not int or subject['exam_unit'] not in {1, 2, 3, 4}:
            raise CatalogIndexError('考试单元应为1至4整数。')
        identity = subject['code'], subject['scope'], subject['exam_unit']
        if identity in seen: raise CatalogIndexError('同一专业的考试单元科目身份重复。')
        seen.add(identity)


def _subject_publication(programs):
    edges, buckets = [], {}
    for program in programs:
        if program.get('exam_matching_supported') is False or program.get('subject_identity_issues'): continue
        counted = set()
        for subject in program.get('subjects', []):
            code, scope, slot, name = (subject[field] for field in ('code', 'scope', 'exam_unit', 'name'))
            edges.append((program['id'], program['unit_id'], program['year'], code, scope, slot, name, len(edges)))
            for unit in ('', program['unit_id']):
                for year in (0, program['year']):
                    for exam in (0, slot):
                        key = unit, year, exam, code, scope
                        value = buckets.setdefault(key, {'code': code, 'scope': scope, 'name': name,
                            'names': [], 'exam_units': set(), 'program_count': 0})
                        value['exam_units'].add(slot)
                        if name not in value['names']: value['names'].append(name)
                        if key not in counted:
                            value['program_count'] += 1
                            counted.add(key)
    rows = []
    for ordinal, (key, value) in enumerate(buckets.items()):
        payload = {**value, 'exam_units': sorted(value['exam_units'])}
        search = ' '.join([value['code'], *value['names']]).casefold()
        rows.append((*key, search, ordinal, encoded(payload)))
    return edges, rows


def _program_source_fields(program):
    conflicts = program.get('evidence_conflicts')
    conflicts = conflicts if isinstance(conflicts, dict) else {}
    conditions = program.get('conditions')
    conditions = conditions if isinstance(conditions, list) else []
    tuition = conflicts.get('tuition', program.get('tuition'))
    tuition = tuition if isinstance(tuition, dict) else {}
    enrollment = program.get('planned_enrollment')
    enrollment = enrollment if isinstance(enrollment, dict) else {}
    return [
        (program.get('evidence'), 'catalog', '专业与研究方向'),
        (program.get('exam_evidence') or program.get('evidence'), 'subjects', '初试科目'),
        (tuition.get('evidence'), 'tuition', '学费与计费口径'),
        (program.get('duration_evidence'), 'duration', '学制'),
        (enrollment.get('evidence'), 'enrollment', '招生计划'),
        *((condition.get('evidence'), 'eligibility', '完整报考资格')
          for condition in conditions if isinstance(condition, dict))]


def _source_documents(admissions, registry=None, verifier=None):
    # These are navigation edges from the verified publication input, not fresh source attestations.
    from evidence import _group, _historical_match, _items, _scope_matches, _score_applicability
    documents = {row['id']: row for row in admissions.get('documents', [])}
    from evidence import PUBLIC_FORM_PARSERS, PUBLIC_FORM_PROFILES, public_form_source_links
    form_links = public_form_source_links(registry or {}, admissions, verifier)
    relations = {}

    def attach(values, program, claim_id, label, context):
        for item in _items(values):
            identifier = item.get('document_id')
            document = documents.get(identifier, {}) if isinstance(identifier, str) else {}
            if (document.get('unit_id') != context.get('unit_id')
                    or document.get('year') != context.get('year')
                    or not _scope_matches(item.get('scope'), context)):
                continue
            link = {'program_id': program['id'], 'claim_id': claim_id, 'label': label}
            relations.setdefault(identifier, {})[(program['id'], claim_id)] = link

    for program in admissions.get('programs', []):
        for values, claim_id, label in _program_source_fields(program):
            attach(values, program, claim_id, label, program)
        for row in admissions.get('admitted_statistics', []):
            if _historical_match(program, row):
                attach(row.get('evidence'), program, 'history:' + row['id'], f"{row['year']}年公示统计", row)
        for row in admissions.get('score_lines', []):
            if _score_applicability(program, row) != 'excluded':
                attach(row.get('evidence'), program, 'score:' + row['id'], f"{row['year']}年学校参考线", row)
    for identifier, document in documents.items():
        dependencies = [item.get('document_id') for item in _items(document.get('applicability_evidence'))]
        publication = document.get('publication_page')
        if isinstance(publication, dict): dependencies.append('publication:' + _group(publication))
        for dependency in dependencies:
            target = documents.get(dependency) if isinstance(dependency, str) else None
            if target and (target.get('unit_id'), target.get('year')) == (document.get('unit_id'), document.get('year')):
                relations.setdefault(dependency, {}).update(relations.get(identifier, {}))
    rows = []
    for identifier, document in documents.items():
        clean = {key: value for key, value in document.items()
                 if key not in {'related_program_ids', 'related_claims', 'related_count', 'related_claim_count',
                                'detail_url', 'detail_url_kind', 'detail_document_id', 'detail_sha256', 'original_format'}}
        if (document.get('kind') == 'catalog_context' or document.get('catalog_parser') in PUBLIC_FORM_PARSERS
                or document.get('public_form_profile') in PUBLIC_FORM_PROFILES or identifier in form_links):
            clean = {**clean, **form_links.get(identifier, {}),
                     'detail_url': form_links.get(identifier, {}).get('detail_url', ''),
                     'request_url': document.get('url', '')}
            source = verifier.read(document) if verifier is not None else None
            if source and (source[1].lstrip().startswith((b'[', b'{')) or document.get('catalog_parser')=='nuaa_public_v1'):
                clean = {**clean, 'original_format': 'structured_fields'}
        links = list(relations.get(identifier, {}).values())
        if links:
            ids = sorted({link['program_id'] for link in links})
            clean = {**clean, 'related_program_ids': ids, 'related_claims': links[:20],
                     'related_count': len(ids), 'related_claim_count': len(links)}
        rows.append(clean)
    return rows


SCHEMA = '''
CREATE TABLE IF NOT EXISTS publication (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS units (unit_id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS programs (program_id TEXT PRIMARY KEY, unit_id TEXT NOT NULL, year INTEGER, ordinal INTEGER NOT NULL, search_text TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS programs_scope ON programs(unit_id,year,ordinal);
CREATE INDEX IF NOT EXISTS programs_year ON programs(year,ordinal);
CREATE TABLE IF NOT EXISTS documents (document_id TEXT PRIMARY KEY, unit_id TEXT, year INTEGER, ordinal INTEGER NOT NULL, payload TEXT NOT NULL, kind TEXT, search_text TEXT);
CREATE INDEX IF NOT EXISTS documents_scope ON documents(unit_id,year,ordinal);
CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL,record_id TEXT NOT NULL,unit_id TEXT NOT NULL,year INTEGER NOT NULL,ordinal INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(kind,record_id));
CREATE INDEX IF NOT EXISTS records_scope ON records(unit_id,kind,year,ordinal);
CREATE TABLE IF NOT EXISTS program_subjects (program_id TEXT NOT NULL,unit_id TEXT NOT NULL,year INTEGER NOT NULL,code TEXT NOT NULL,scope TEXT NOT NULL,exam_unit INTEGER NOT NULL,name TEXT NOT NULL,ordinal INTEGER NOT NULL,PRIMARY KEY(program_id,code,scope,exam_unit));
CREATE INDEX IF NOT EXISTS program_subjects_scope ON program_subjects(unit_id,year,code,scope,exam_unit);
CREATE TABLE IF NOT EXISTS subject_catalog (unit_filter TEXT NOT NULL,year_filter INTEGER NOT NULL,exam_filter INTEGER NOT NULL,code TEXT NOT NULL,scope TEXT NOT NULL,search_text TEXT NOT NULL,ordinal INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(unit_filter,year_filter,exam_filter,code,scope));
CREATE INDEX IF NOT EXISTS subject_catalog_scope ON subject_catalog(unit_filter,year_filter,exam_filter,ordinal);
'''


class CatalogIndex:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def read_connection(self):
        """Missing indexes fail explicitly; a read never creates a database or table."""
        connection = None
        try:
            uri = 'file:' + quote(self.path.resolve().as_posix(), safe='/:') + '?mode=ro'
            connection = sqlite3.connect(uri, uri=True, timeout=5)
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            yield connection
        except sqlite3.Error as error:
            raise CatalogIndexError('已发布目录索引尚不可用，请等待资料发布。') from error
        finally:
            if connection is not None:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()

    def metadata(self):
        with self.read_connection() as connection:
            return self._publication(connection)

    @staticmethod
    def _publication(connection, revision=None):
        bounded_text(revision, 128, '资料版本', optional=True)
        row = connection.execute('SELECT revision,payload FROM publication WHERE singleton=1').fetchone()
        if row is None: raise CatalogIndexError('目录索引还没有已发布版本。')
        if revision is not None and row[0] != revision:
            raise CatalogRevisionError('资料已发布新版本，请刷新后重试。')
        try:
            result = json.loads(row[1])
        except (ValueError, TypeError) as error:
            raise CatalogIndexError('已发布版本元数据损坏，请重新发布。') from error
        if not isinstance(result, dict) or result.get('revision') != row[0]:
            raise CatalogIndexError('已发布版本声明不一致，请重新发布。')
        return result

    def bootstrap(self):
        with self.read_connection() as connection:
            publication = self._publication(connection)
            units = connection.execute('SELECT payload FROM units ORDER BY ordinal').fetchall()
        return {**publication, 'units': [json.loads(row[0]) for row in units]}

    def sync(self, registry, admissions, coverage, revision, *, verifier=None):
        bounded_text(revision, 128, '资料版本')
        if not revision:
            raise CatalogIndexError('资料版本不能为空。')
        if not isinstance(registry, dict) or not isinstance(admissions, dict) or not isinstance(coverage, dict):
            raise CatalogIndexError('发布数据格式不正确。')
        meta = _metadata(admissions.get('metadata', {}), '目录元数据')
        registry_meta = _metadata(registry.get('metadata', {}), '单位元数据')
        _metadata(coverage, '覆盖状态')
        units, programs, documents = registry.get('units', []), admissions.get('programs', []), admissions.get('documents', [])
        for rows, label in ((units, '招生单位'), (programs, '专业'), (documents, '文档')):
            _validate_rows(rows, label)
        unit_ids = {row['id'] for row in units}
        _validate_coverage(coverage, unit_ids)
        for unit in units:
            bounded_text(unit['id'], 100, '招生单位身份')
            bounded_text(unit.get('name'), 500, '招生单位名称')
        if any(row.get('unit_id') not in unit_ids for row in [*programs, *documents]):
            raise CatalogIndexError('专业或文档不属于现有招生单位。')
        if any(type(row.get('year')) is not int or not 2000 <= row['year'] <= 2100 for row in programs):
            raise CatalogIndexError('专业年度必须为有效整数。')
        if any(row.get('year') is not None and (type(row['year']) is not int or not 2000 <= row['year'] <= 2100) for row in documents):
            raise CatalogIndexError('文档年度必须为有效整数或未知。')
        for document in documents:
            bounded_text(document.get('kind'), 64, '文档种类')
            if not document['kind']: raise CatalogIndexError('文档种类不能为空。')
        for program in programs:
            _validate_subjects(program)
            for field in ('unit_name', 'department', 'code', 'name', 'direction', 'province', 'campus',
                          'study_mode', 'degree_type'):
                if field in program and program[field] is not None:
                    bounded_text(program[field], 2000, '专业' + field)
        record_rows = []
        for kind in HISTORY_KINDS:
            values = admissions.get(kind, [])
            _validate_rows(values, kind)
            for ordinal, row in enumerate(values):
                if row.get('unit_id') not in unit_ids: raise CatalogIndexError('历史记录不属于现有招生单位。')
                _year(row.get('year'), '历史记录年度')
                if 'kind' in row: bounded_text(row['kind'], 100, '历史记录种类')
                record_rows.append((kind, row['id'], row['unit_id'], row['year'], ordinal, encoded(row)))
        if self.path.is_file():
            try:
                current = self.metadata()
            except CatalogIndexError:
                current = None
            if current and current['revision'] == revision and current.get('schema_version') == SCHEMA_VERSION:
                return {'revision': revision, 'unit_count': len(units), 'program_count': len(programs), 'updated': False}
        reference_year = meta.get('reference_year')
        counts = Counter(row['unit_id'] for row in programs if reference_year is None or row.get('year') == reference_year)
        states = {row['unit_id']: row for row in coverage.get('unit_catalog_status', []) if isinstance(row, dict) and 'unit_id' in row}
        unit_rows = [(row['id'], ordinal, encoded({**row, 'unit_id': row['id'], 'unit_name': row.get('name', ''),
            'program_count': counts[row['id']], 'catalog_status': states.get(row['id'], {}).get('status', 'not_collected'),
            'verified': states.get(row['id'], {}).get('verified') is True, 'year': reference_year})) for ordinal, row in enumerate(units)]
        program_rows = []
        for ordinal, row in enumerate(programs):
            search = [str(row.get(field) or '') for field in ('unit_name', 'department', 'code', 'name', 'direction',
                                                               'province', 'campus', 'study_mode', 'degree_type')]
            search += [str(subject.get('code', '')) + ' ' + str(subject.get('name', ''))
                       for subject in row.get('subjects', []) if isinstance(subject, dict)]
            program_rows.append((row['id'], row['unit_id'], row.get('year'), ordinal, ' '.join(search).casefold(), encoded(row)))
        unit_names = {row['id']: row['name'] for row in units}
        document_rows = [(row['id'], row.get('unit_id'), row.get('year'), ordinal,
                          encoded({**row, 'unit_name': unit_names[row['unit_id']]}), row['kind'],
                          ' '.join(str(row.get(field) or '') for field in ('kind', 'title', 'url')).casefold())
                         for ordinal, row in enumerate(_source_documents(admissions, registry, verifier))]
        subject_edges, subject_rows = _subject_publication(programs)
        publication = encoded({'meta': meta, 'coverage': coverage, 'revision': revision,
            'registry_metadata': registry_meta, 'schema_version': SCHEMA_VERSION,
            'published_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'verification_mode': 'published_snapshot',
            'notice': '查询使用最后已发布核验版本；索引命中不表示本次已重新访问或核验原网页。'})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.executescript(SCHEMA)
            connection.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in connection.execute('PRAGMA table_info(documents)')}
            for column in ('kind', 'search_text'):
                if column not in columns: connection.execute('ALTER TABLE documents ADD COLUMN ' + column + ' TEXT')
            connection.execute('CREATE INDEX IF NOT EXISTS documents_kind ON documents(kind,unit_id,year,ordinal)')
            for table in ('program_subjects', 'subject_catalog', 'records', 'programs', 'documents', 'units', 'publication'):
                connection.execute('DELETE FROM ' + table)
            connection.executemany('INSERT INTO units VALUES(?,?,?)', unit_rows)
            connection.executemany('INSERT INTO programs VALUES(?,?,?,?,?,?)', program_rows)
            connection.executemany('INSERT INTO documents(document_id,unit_id,year,ordinal,payload,kind,search_text) VALUES(?,?,?,?,?,?,?)', document_rows)
            connection.executemany('INSERT INTO records VALUES(?,?,?,?,?,?)', record_rows)
            connection.executemany('INSERT INTO program_subjects VALUES(?,?,?,?,?,?,?,?)', subject_edges)
            connection.executemany('INSERT INTO subject_catalog VALUES(?,?,?,?,?,?,?,?)', subject_rows)
            connection.execute('INSERT INTO publication VALUES(1,?,?)', (revision, publication))
            connection.execute('COMMIT')
        except sqlite3.Error as error:
            if connection is not None and connection.in_transaction:
                connection.execute('ROLLBACK')
            raise CatalogIndexError('目录索引发布失败，保留上一已发布版本。') from error
        finally:
            if connection is not None:
                connection.close()
        return {'revision': revision, 'unit_count': len(units), 'program_count': len(programs), 'updated': True}

    def _query(self, table, *, unit_id=None, year=None, q='', offset=0, limit=20, revision=None):
        page_arguments(unit_id, year, q, offset, limit)
        conditions, parameters = [], []
        if unit_id is not None:
            conditions.append('unit_id=?')
            parameters.append(unit_id)
        if year is not None:
            conditions.append('year=?')
            parameters.append(year)
        if q:
            clauses, values = _search_clause(q)
            conditions.extend(clauses)
            parameters.extend(values)
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        with self.read_connection() as connection:
            publication = self._publication(connection, revision)
            total = connection.execute('SELECT COUNT(*) FROM ' + table + where, parameters).fetchone()[0]
            rows = connection.execute('SELECT payload FROM ' + table + where + ' ORDER BY ordinal LIMIT ? OFFSET ?',
                                      [*parameters, limit, offset]).fetchall()
        return {'programs' if table == 'programs' else 'documents': [json.loads(row[0]) for row in rows],
                'total': total, 'offset': offset, 'limit': limit, 'revision': publication['revision']}

    def query_programs(self, unit_id=None, year=None, q='', offset=0, limit=20, *, revision=None):
        return self._query('programs', unit_id=unit_id, year=year, q=q, offset=offset, limit=limit, revision=revision)

    def query_documents(self, unit_id=None, year=None, offset=0, limit=50, *, revision=None):
        return self._query('documents', unit_id=unit_id, year=year, offset=offset, limit=limit, revision=revision)

    def query_subjects(self, unit_id=None, year=None, q='', offset=0, limit=50, *,
                       code=None, scope=None, exam_unit=None, revision=None):
        page_arguments(unit_id, year, q, offset, limit)
        if unit_id is not None and not unit_id.strip(): raise CatalogIndexError('招生单位不能为空。')
        bounded_text(code, 32, '科目代码', optional=True)
        bounded_text(scope, 100, '科目作用域', optional=True)
        if exam_unit is not None and (type(exam_unit) is not int or exam_unit not in {1, 2, 3, 4}):
            raise CatalogIndexError('考试单元应为1至4整数。')
        conditions = ['unit_filter=?', 'year_filter=?', 'exam_filter=?']
        parameters = [unit_id or '', year or 0, exam_unit or 0]
        for column, value in (('code', code), ('scope', scope)):
            if value is not None:
                conditions.append(column + '=?')
                parameters.append(value)
        if q:
            clauses, values = _search_clause(q)
            conditions.extend(clauses)
            parameters.extend(values)
        where = ' WHERE ' + ' AND '.join(conditions)
        with self.read_connection() as connection:
            publication = self._publication(connection, revision)
            total = connection.execute('SELECT COUNT(*) FROM subject_catalog' + where, parameters).fetchone()[0]
            rows = connection.execute('SELECT payload FROM subject_catalog' + where + ' ORDER BY ordinal LIMIT ? OFFSET ?',
                                      [*parameters, limit, offset]).fetchall()
        return {'subjects': [json.loads(row[0]) for row in rows], 'total': total, 'offset': offset,
                'limit': limit, 'revision': publication['revision']}

    def query_sources(self, unit_id=None, year=None, q='', kind=None, offset=0, limit=12, *, revision=None):
        page_arguments(unit_id, year, q, offset, limit)
        bounded_text(kind, 64, '文档种类', optional=True)
        scope_conditions, scope_parameters = [], []
        for column, value in (('unit_id', unit_id), ('year', year)):
            if value is not None:
                scope_conditions.append(column + '=?')
                scope_parameters.append(value)
        conditions, parameters = list(scope_conditions), list(scope_parameters)
        if kind is not None:
            conditions.append('kind=?')
            parameters.append(kind)
        if q:
            clauses, values = _search_clause(q)
            conditions.extend(clauses)
            parameters.extend(values)
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        scope_where = ' WHERE ' + ' AND '.join(scope_conditions) if scope_conditions else ''
        with self.read_connection() as connection:
            publication = self._publication(connection, revision)
            count = connection.execute('SELECT COUNT(*) FROM documents' + scope_where, scope_parameters).fetchone()[0]
            total = connection.execute('SELECT COUNT(*) FROM documents' + where, parameters).fetchone()[0]
            rows = connection.execute('SELECT payload FROM documents' + where + ' ORDER BY ordinal LIMIT ? OFFSET ?',
                                      [*parameters, limit, offset]).fetchall()
        return {'documents': [json.loads(row[0]) for row in rows], 'total': total, 'offset': offset,
                'limit': limit, 'revision': publication['revision'],
                'summary': {'document_count': count, 'published_count': count, 'filtered_count': total}}

    @staticmethod
    def _scope_data(connection, unit_ids, publication, program=None):
        result = {**publication, 'units': [], 'programs': [], 'documents': [], **{kind: [] for kind in HISTORY_KINDS}}
        if not unit_ids: return result
        marks = ','.join('?' for _ in unit_ids)
        result['units'] = [json.loads(row[0]) for row in connection.execute(
            'SELECT payload FROM units WHERE unit_id IN (' + marks + ') ORDER BY ordinal', unit_ids)]
        if program is None:
            result['programs'] = [json.loads(row[0]) for row in connection.execute(
                'SELECT payload FROM programs WHERE unit_id IN (' + marks + ') ORDER BY ordinal', unit_ids)]
        else:
            result['programs'] = [program]
        result['documents'] = [json.loads(row[0]) for row in connection.execute(
            'SELECT payload FROM documents WHERE unit_id IN (' + marks + ') ORDER BY ordinal', unit_ids)]
        for kind, payload in connection.execute(
                'SELECT kind,payload FROM records WHERE unit_id IN (' + marks + ') ORDER BY kind,ordinal', unit_ids):
            result[kind].append(json.loads(payload))
        return result

    def scope_snapshot(self, unit_ids, *, revision=None):
        selected = _scope_arguments(unit_ids)
        with self.read_connection() as connection:
            publication = self._publication(connection, revision)
            return self._scope_data(connection, selected, publication)

    def program_bundle(self, identifier, *, revision=None):
        bounded_text(identifier, 512, '专业身份')
        with self.read_connection() as connection:
            publication = self._publication(connection, revision)
            row = connection.execute('SELECT payload FROM programs WHERE program_id=?', (identifier,)).fetchone()
            program = json.loads(row[0]) if row else None
            bundle = self._scope_data(connection, [program['unit_id']] if program else [], publication, program)
        return {**bundle, 'program': program}

    def _get(self, table, column, identifier):
        bounded_text(identifier, 512, '资料身份')
        with self.read_connection() as connection:
            row = connection.execute('SELECT payload FROM ' + table + ' WHERE ' + column + '=?', (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def get_program(self, identifier):
        return self._get('programs', 'program_id', identifier)

    def get_document(self, identifier):
        return self._get('documents', 'document_id', identifier)

    def unit_summaries(self):
        with self.read_connection() as connection:
            rows = connection.execute('SELECT payload FROM units ORDER BY ordinal').fetchall()
        return [json.loads(row[0]) for row in rows]
