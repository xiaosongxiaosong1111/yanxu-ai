"""Exercise production official discovery with replayable public HTTP evidence.

Expected article URLs are used only after discovery for scoring. No service,
provider credentials, user database, or adopted admissions facts are involved.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html import unescape
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / 'tests' / 'fixtures' / 'official_discovery'
METRICS = ('entry_verified', 'requested_document_located', 'requested_body_read',
           'source_evidence_complete', 'expected_article_discovered')
BODY_TERMS = ('报考条件', '报名', '考试', '复试', '招生计划', '招生专业', '学制', '学习方式', '收费')


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def load_cases(path=None):
    value = json.loads(Path(path or FIXTURES / 'cases32.json').read_text('utf-8'))
    return value['cases']


def select_heldout(units, excluded, count=40):
    """One seeded hash-order unit per province, then remaining hash-order units."""
    rank = lambda row: hashlib.sha256(('official-discovery-20261109:' + str(row['id'])).encode()).hexdigest()
    eligible = sorted((deepcopy(row) for row in units if str(row['id']) not in excluded
                       and row.get('official_url')), key=rank)
    first, rest, provinces = [], [], set()
    for row in eligible:
        province = row.get('province', '')
        if province in provinces:
            rest.append(row)
        else:
            first.append(row)
            provinces.add(province)
    return (first + rest)[:count]


def _matches(source, case):
    title = str(source.get('title') or '').translate(str.maketrans('〇○零一二三四五六七八九', '000123456789'))
    if re.search(r'预计|尚未|未发布|待发布|占位|本科|博士|高职|专升本|推免|推荐免试|夏令营', title):
        return False
    if not re.search(r'(?<!\d)' + str(case['requested_year']) + r'(?!\d)', title):
        return False
    pattern = (r'专业目录|招生目录' if case.get('kind') == 'catalog' else
               r'招生章程|招生简章|硕士(?:学位)?研究生(?:招生)?(?:章程|简章)')
    return bool(re.search(pattern, title))


def _primary_publication(source, case, recordings):
    from collectors.documents import decode_text
    for record in recordings:
        if record.get('url') != source.get('url') or not isinstance(record.get('raw'), bytes):
            continue
        media = str(record.get('headers', {}).get('Content-Type', record.get('content_type', '')))
        if 'html' not in media.lower():
            return True
        html = decode_text(record['raw'], media)
        headings = re.findall(r'<(?:title|h1)\b[^>]*>(.*?)</(?:title|h1)>', html, re.I | re.S)
        parts = urlsplit(record['url'])
        if parts.hostname == 'yz.chsi.com.cn' and parts.path == '/zsgs/zsjz/detail.action':
            headings += re.findall(r'<div\b[^>]*class=[\"\'][^\"\']*\barticle-title\b[^\"\']*[\"\'][^>]*>(.*?)</div>',
                                   html, re.I | re.S)
        return any(_matches({'title': unescape(re.sub(r'<[^>]+>', '', heading))}, case) for heading in headings)
    return True


def _body_read(source, case, recordings):
    body = str(source.get('excerpt') or source.get('text') or '')
    lines = body.splitlines()
    start = next((index + 1 for index, line in enumerate(lines) if _matches({'title': line}, case)), 0)
    end = next((index for index in range(start, len(lines)) if re.match(
        r'\s*(?:附件[【\[：:]|上一篇|下一篇|Copyright|版权所有)', lines[index], re.I)), len(lines))
    body = '\n'.join(lines[start:end])
    return (source.get('status') == 'official_original_read'
            and source.get('year') == case['requested_year'] and _matches(source, case)
            and len(body) >= 500 and sum(term in body for term in BODY_TERMS) >= 2
            and _primary_publication(source, case, recordings))


def _recorded_proof(source, recordings):
    digest = source.get('sha256')
    if not re.fullmatch(r'[0-9a-f]{64}', str(digest or '')) or not source.get('checked_at'):
        return False
    for record in recordings:
        if (record.get('url') == source.get('url') and record.get('status') == 200
                and record.get('sha256') == digest and record.get('checked_at')):
            raw = record.get('raw')
            return (hashlib.sha256(raw).hexdigest() == digest if isinstance(raw, bytes)
                    else record.get('integrity_verified') is True)
    return False


def _same_document_url(first, second):
    def identity(value):
        parts = urlsplit(str(value or ''))
        if parts.scheme not in {'http', 'https'}:
            return None
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        default = port == (443 if parts.scheme == 'https' else 80)
        return (None if default else parts.scheme, parts.hostname, None if default else port,
                parts.path or '/', parts.query, parts.fragment)
    try:
        return identity(first) is not None and identity(first) == identity(second)
    except ValueError:
        return False


def assess_result(case, result, recordings):
    documents = result.get('document_sources') or []
    navigation = result.get('navigation_sources') or []
    sources = documents + navigation
    matching = [row for row in sources if _matches(row, case)]
    read = [row for row in documents if _body_read(row, case, recordings)]
    expected = (case.get('expected') or {}).get('article')
    error_codes = sorted({str(row.get('code') or 'unknown') for row in result.get('errors') or []})
    return {
        'entry_verified': result.get('entry_verified') is True and bool(result.get('entry_sources')),
        'requested_document_located': bool(matching),
        'requested_body_read': bool(read),
        'source_evidence_complete': any(_recorded_proof(row, recordings) and row.get('knowledge_id')
                                       and row.get('original_available') is True
                                       and row.get('knowledge_quality', {}).get('state') == 'accepted' for row in read),
        'expected_article_applicable': bool(expected),
        'expected_article_discovered': bool(expected and any(_same_document_url(row.get('url'), expected)
                                                            for row in sources)),
        'historical_document_observed': any(type(row.get('year')) is int
                                            and row['year'] < case['requested_year'] for row in sources),
        'stage': result.get('stage', 'unknown'), 'error_codes': error_codes,
        'matching_urls': [row.get('url') for row in matching],
        'read_urls': [row.get('url') for row in read],
    }


class ReplayResponse:
    def __init__(self, raw, status=200, headers=None):
        self.status, self.headers = status, headers or {}
        self.stream = io.BytesIO(raw)

    def getheader(self, name, default=None):
        return next((value for key, value in self.headers.items() if key.lower() == name.lower()), default)

    def read(self, size=-1):
        return self.stream.read(size)

    def close(self):
        self.stream.close()


class ReplayError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class ReplayTransport:
    """No network fallback: absent or tampered responses fail visibly."""
    def __init__(self, records, directory):
        self.records = {record['url']: {**record, 'integrity_verified': False} for record in records}
        self.directory = Path(directory).resolve()
        self.calls = []

    def __call__(self, url, target, timeout):
        self.calls.append(url)
        record = self.records.get(url)
        if record is None:
            raise ReplayError('URL not recorded: ' + url, 'recording_missing')
        if record.get('error_code') or record.get('complete') is False:
            from public_sources import SourceError
            raise SourceError(record.get('error_code', 'recorded_incomplete'),
                              record.get('error_message', 'Recorded response did not complete.'))
        raw_path = (self.directory / record['raw_path']).resolve()
        if not raw_path.is_relative_to(self.directory):
            raise ReplayError('recording outside fixture directory', 'recording_path_invalid')
        raw = raw_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ReplayError('recording SHA256 mismatch: ' + url, 'recording_corrupt')
        record['integrity_verified'] = True
        return ReplayResponse(raw, record['status'], record.get('headers'))


class _RecordingResponse:
    def __init__(self, response, guard, owner, record):
        self.response, self.guard, self.owner, self.record = response, guard, owner, record
        self.status, self.connection = response.status, getattr(response, 'connection', None)
        self.chunks, self.closed, self.complete, self.read_error = [], False, False, None

    def getheader(self, name, default=None):
        return self.response.getheader(name, default)

    def read(self, size=-1):
        try:
            chunk = self.response.read(size)
        except Exception as error:
            self.read_error = {'error_code': 'timeout' if isinstance(error, TimeoutError) else 'fetch_error',
                               'error_message': 'Recorded body read failed: ' + type(error).__name__}
            raise
        self.chunks.append(chunk)
        if not chunk or size < 0:
            self.complete = True
        return chunk

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.response.close()
            self.owner.save({**self.record, 'status': self.status,
                             'complete': self.complete or not 200 <= self.status < 300,
                             **(self.read_error or {}),
                             'headers': {key: self.getheader(key, '') for key in ('Content-Type', 'Location')}},
                            b''.join(self.chunks))
        finally:
            self.guard.close()


class RecordingTransport:
    """Keep pinned production GET behavior; pace requests separately per host."""
    def __init__(self, directory, interval=2):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.interval, self.records = interval, []
        self.lock, self.next_host_request = threading.Lock(), {}

    def save(self, record, raw):
        digest = hashlib.sha256(raw).hexdigest()
        path = self.directory / (digest + '.bin')
        path.write_bytes(raw)
        value = {**record, 'sha256': digest, 'response_bytes': len(raw), 'raw_path': path.name,
                 'integrity_verified': True}
        with self.lock:
            self.records.append(value)
            with (self.directory / 'requests.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(value, ensure_ascii=False) + '\n')
        return value

    def __call__(self, url, target, timeout):
        import public_sources
        host = urlsplit(url).hostname
        with self.lock:
            pause = max(0, self.next_host_request.get(host, 0) - time.monotonic())
            self.next_host_request[host] = time.monotonic() + pause + self.interval
        if pause:
            time.sleep(min(pause, timeout))
        guard = public_sources._DeadlineGuard(max(.1, timeout - pause))
        try:
            response = public_sources._transport(url, target, max(.1, timeout - pause), guard)
            return _RecordingResponse(response, guard, self, {'url': url, 'checked_at': stamp(),
                                      'pid': os.getpid(), 'pinned_addresses': list(target[2])})
        except Exception as error:
            guard.close()
            self.save({'url': url, 'checked_at': stamp(), 'pid': os.getpid(), 'status': 0,
                       'complete': False, 'error_code': 'timeout' if isinstance(error, TimeoutError) else 'fetch_error',
                       'error_message': 'Recorded connection failed: ' + type(error).__name__, 'headers': {}}, b'')
            raise


class _Admission:
    def __init__(self, unit):
        self.unit = unit

    def catalog_bootstrap(self):
        return {'units': [deepcopy(self.unit)], 'revision': 'public-acceptance-only',
                'meta': {'reference_year': 2027, 'target_year': 2028}, 'coverage': {}}

    def catalog_programs(self, **query):
        return {'programs': [], 'total': 0}


class _NoProviderSettings:
    def yuanbao_key(self):
        return ''

    def runtime_ai(self):
        raise ValueError('acceptance evaluator never calls a model')


def evaluate_case(case, transport, *, replay=False):
    """Run the same production tool used by research, in isolated scratch data."""
    import public_sources
    from grad_engine import validate_profile
    from research_store import ResearchError
    from research_tools import ResearchTools
    def resolver(host, *args, **kwargs):
        if str(host).lower().rstrip('.') == 'yz.chsi.com.cn':
            raise public_sources.SourceError('restricted', 'Replay preserves the production CHSI request gate.')
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
    def fetch(url, unit, **kwargs):
        options = {**kwargs, 'transport': transport}
        if replay:
            options['resolver'] = resolver
        return public_sources.fetch_official_document(url, unit, **options)
    def navigate(url, unit, **kwargs):
        options = {**kwargs, 'transport': transport}
        if replay:
            options['resolver'] = resolver
        return public_sources.fetch_navigation_resource(url, unit, **options)
    with tempfile.TemporaryDirectory(prefix='official-discovery-evaluation-') as scratch:
        data = Path(scratch)
        _seed_declarations(case, data)
        research_id = 'r_' + hashlib.sha256(str(case.get('case_id', case['unit']['id'])).encode()).hexdigest()[:32]
        task = {'id': research_id, 'query': case['unit']['name'], 'follow_ups': [],
                'profile': validate_profile({}), 'unit_ids': [case['unit']['id']],
                'reference_year': case['requested_year'], 'target_year': case['requested_year'] + 1}
        tools = None
        condition = case.get('starting_condition', 'registered_homepage')
        reviewed = None
        try:
            tools = ResearchTools(_Admission(case['unit']), _NoProviderSettings(), task, data,
                                  source_fetcher=fetch, navigation_fetcher=navigate)
            enriched = tools.units[case['unit']['id']]
            if not case['unit'].get('official_url') and enriched.get('official_binding'):
                condition, reviewed = 'reviewed_curated_root', enriched['official_binding']
            for candidate in case.get('unconfirmed_candidates', []):
                tools._source({'url': candidate, 'unit_id': case['unit']['id'],
                               'title': case['unit']['name'] + '招生入口候选（归属待核）',
                               'status': 'official_domain_candidate',
                               'source_tier': 'official_domain_candidate', 'year': None})
            result = tools.run('discover_official_entry', {'unit_id': case['unit']['id'],
                               'year': case['requested_year'], 'kind': case.get('kind', 'charter')})
        except ResearchError as error:
            result = {'stage': 'tool_error', 'entry_verified': False, 'errors': [
                      {'code': error.code, 'message': error.message}]}
        except Exception as error:
            result = {'stage': 'evaluator_or_fetcher_error', 'entry_verified': False, 'errors': [
                      {'code': type(error).__name__, 'message': str(error)[:300]}]}
        records = list(transport.records.values()) if replay else transport.records
        document_urls = {row.get('url') for row in result.get('document_sources', [])}
        proof_records = [{**record, **({'raw': (transport.directory / record['raw_path']).read_bytes()}
                         if record['url'] in document_urls else {})} for record in records]
        proof_records += [{**record, 'status': 200, 'complete': True,
                           'raw': (FIXTURES / record['raw_path']).read_bytes(),
                           'headers': {'Content-Type': record.get('content_type', 'text/html')},
                           'evidence_origin': 'frozen_public_CHSI_original'}
                          for record in case.get('saved_declarations', []) if record['url'] in document_urls]
        return {'case_id': case.get('case_id', case['unit']['id']), 'unit_id': case['unit']['id'],
                'name': case['unit']['name'], 'cohort': case.get('cohort', 'regression32'),
                'starting_condition': condition,
                'original_starting_condition': case.get('starting_condition'), 'reviewed_root_binding': reviewed,
                'requested_year': case['requested_year'], 'kind': case.get('kind', 'charter'),
                'reference_classification': (case.get('expected') or {}).get('classification'),
                'reference_read_limit': (case.get('expected') or {}).get('human_read_status'),
                'metrics': assess_result(case, result, proof_records),
                'result': result, 'usage': dict(tools.usage) if tools else {}, 'checked_at': stamp()}


def _seed_declarations(case, data):
    """Copy frozen public authority pages, never expected target articles, into scratch."""
    declarations = []
    for record in case.get('saved_declarations', []):
        raw = (FIXTURES / record['raw_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError('declaration fixture SHA256 mismatch')
        target = data / 'snapshots' / (record['sha256'] + '.html')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        declarations.append({**record, 'local_path': target.relative_to(data).as_posix()})
    if declarations:
        # Production authority accepts saved same-unit CHSI originals in this public manifest.
        (data / 'official_discovery_declarations.json').write_text(
            json.dumps({'documents': declarations}, ensure_ascii=False), 'utf-8')


def summarize(rows):
    return {cohort: {'total': len(group), 'metrics': {key: sum(row['metrics'][key] for row in group)
            for key in METRICS}, 'scope': 'registered public URL coverage; not autonomous unbound proof finding'
            if cohort == 'heldout_registered' else '32 researched failures; annual/body/evidence outcomes separate',
            'denominators': {key: sum(row['metrics']['expected_article_applicable'] for row in group)
                             if key == 'expected_article_discovered' else len(group) for key in METRICS},
            'starting_conditions': {condition: {'total': len(selected),
                'metrics': {key: sum(row['metrics'][key] for row in selected) for key in METRICS}}
                for condition in sorted({row['starting_condition'] for row in group})
                for selected in [[row for row in group if row['starting_condition'] == condition]]}}
            for cohort in sorted({row['cohort'] for row in rows})
            for group in [[row for row in rows if row['cohort'] == cohort]]}


def _source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
        'research_tools.py', 'research_official_tools.py', 'official_search.py', 'official_authority.py', 'unit_bindings.py',
        'public_navigation.py', 'public_sources.py', 'unit_bindings.py', 'tools/evaluate_official_discovery.py')
        if (ROOT / name).is_file()}


def _legacy_completion(records, report):
    """Old baseline recordings get completion from the production error ledger."""
    def identity(url):
        parts = urlsplit(str(url or ''))
        return (parts.hostname, parts.port if parts.port not in {80, 443} else None,
                parts.path or '/', parts.query)
    errors = [error for row in report.get('rows', []) for error in row.get('result', {}).get('errors', [])]
    truncated = any(len(row.get('result', {}).get('errors', [])) >= 12 for row in report.get('rows', []))
    fetch_failures = {identity(error.get('url')) for error in errors if error.get('code') in {
        'timeout', 'fetch_error', 'too_large', 'navigation_unavailable', 'navigation_budget_exceeded', 'OSError', 'TimeoutError'}}
    return [{**record, **({} if 'complete' in record else {
        'complete': not truncated and identity(record['url']) not in fetch_failures,
        'completion_basis': 'production_error_ledger_full_response_inference'})} for record in records]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--heldout', type=int, default=40)
    parser.add_argument('--workers', type=int, choices=range(1, 7), default=4)
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--cohort', choices=('all', 'regression32', 'heldout_registered'), default='all')
    parser.add_argument('--replay', type=Path, help='recorded report directory; no network fallback')
    args = parser.parse_args(argv)
    if not 0 <= args.heldout <= 100 or not 2 <= args.interval <= 60:
        parser.error('heldout must be 0..100; live host interval must be 2..60 seconds')
    cases = load_cases()
    units = json.loads((ROOT / 'data' / 'units.json').read_text('utf-8'))['units']
    heldout = select_heldout(units, {case['unit']['id'] for case in cases}, args.heldout)
    cases += [{'case_id': 'heldout-' + unit['id'], 'unit': unit, 'requested_year': 2027, 'kind': 'charter',
               'cohort': 'heldout_registered', 'starting_condition': 'registered_homepage'} for unit in heldout]
    cases = [case for case in cases if args.cohort == 'all' or case.get('cohort', 'regression32') == args.cohort]
    args.out.mkdir(parents=True, exist_ok=True)
    transport = (ReplayTransport(_legacy_completion(json.loads((args.replay / 'recordings.json').read_text('utf-8')),
                                 json.loads((args.replay / 'report.json').read_text('utf-8'))
                                 if (args.replay / 'report.json').is_file() else {}),
                                 args.replay / 'recordings') if args.replay else
                 RecordingTransport(args.out / 'recordings', args.interval))
    (args.out / 'sample.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2), 'utf-8')
    rows = []
    started = stamp()
    source_hashes = _source_hashes()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(evaluate_case, case, transport, replay=bool(args.replay)): case for case in cases}
        for future in as_completed(pending):
            row = future.result()
            rows.append(row)
            print(json.dumps({'unit_id': row['unit_id'], 'metrics': row['metrics']}, ensure_ascii=False), flush=True)
    rows.sort(key=lambda row: row['case_id'])
    report = {'started_at': started, 'finished_at': stamp(), 'pid': os.getpid(), 'mode': 'replay' if args.replay else 'live',
              'workers': args.workers, 'host_interval_seconds': args.interval, 'paid_calls': 0,
              'user_databases_opened': 0, 'final_article_seeded': False, 'summary': summarize(rows), 'rows': rows}
    report['code_head'] = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                         capture_output=True, text=True, check=True).stdout.strip()
    report['source_sha256'] = source_hashes
    report['finished_source_sha256'] = _source_hashes()
    report['source_changed_during_run'] = report['source_sha256'] != report['finished_source_sha256']
    (args.out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    records = list(transport.records.values()) if args.replay else transport.records
    (args.out / 'recordings.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
