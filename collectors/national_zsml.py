"""Discover and replay public ZSML catalog systems from nationwide official originals."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from public_sources import SourceError, official_source_url, document_attribution, rendered_source_html
from service_verification import SourceVerifier, canonical, plain_text
from .catalog_zsml import collect_zsml
from .catalogs import catalog_status
from .documents import PROJECT_ROOT, DocumentError, decode_text, extract_pdf_text
from .national_sources import Links, binding_from_html, collect_unit, HostPacer


ALREADY_COLLECTED = {'368259', '368317', '368033'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def catalog_base(url, year=2027):
    try:
        parts = urlsplit(unescape(url))
        if (parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username or parts.password
                or parts.port not in (None, 80, 443) or not parts.hostname.endswith(('.edu.cn', '.ac.cn', '.gov.cn'))):
            return None
        match = re.match(r'^(.*?)/zsml/sszsml/(?:index/' + str(year) + r'(?!\d)|zsmlYjfx/' + str(year) + r'/)', parts.path)
        if not match:
            return None
        return urlunsplit((parts.scheme, parts.netloc, match[1], '', ''))
    except (ValueError, TypeError):
        return None


def _read(document, root):
    try:
        path = (root/document['local_path']).resolve()
        if not path.is_relative_to(root/'data') or not path.is_file() or path.stat().st_size > 32*1024*1024:
            return None
        raw = path.read_bytes()
        return raw if hashlib.sha256(raw).hexdigest() == document.get('sha256') else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _entries(text, source_url, year):
    parser = Links(); parser.feed(text)
    urls = [(urljoin(source_url, href), label) for href, label in parser.links]
    urls += [(unescape(match[0]), '原文目录网址') for match in re.finditer(r'https?://[^\s<>"\'，。；：（）]+', text)]
    if catalog_base(source_url, year):
        urls.append((source_url, '已读取官方目录入口'))
    found = {}
    for url, label in urls:
        base = catalog_base(url, year)
        if base:
            entry = base + f'/zsml/sszsml/index/{year}'
            found[base] = {'base_url': base, 'entry_url': entry, 'quote': entry, 'link_label': label}
    return list(found.values())


def _bound_unit(unit, row, root):
    if unit.get('official_url'):
        return unit
    for binding in row.get('bindings', []):
        proof = binding.get('evidence') or {}
        raw = _read(proof, root)
        if raw is None:
            continue
        try:
            html = decode_text(raw)
            verified = binding_from_html(unit, proof, html, raw=raw)
        except (ValueError, OSError):
            continue
        if any(item['url'] == binding.get('url') for item in verified):
            return {**unit, 'official_url': binding['url'], '_binding_evidence': proof}
    for document in row.get('pages', []):
        if urlsplit(document.get('url', '')).hostname != 'yz.chsi.com.cn':
            continue
        raw = _read(document, root)
        if raw is None:
            continue
        try:
            verified = binding_from_html(unit, document, decode_text(raw, document.get('content_type', '')), raw=raw)
        except (ValueError, OSError):
            continue
        if verified:
            binding = verified[0]
            return {**unit, 'official_url': binding['url'], '_binding_evidence': binding['evidence']}
    return unit


def _candidate(unit, original_unit, document, text, entry):
    title_parts = re.findall(r'<(?:h[1-3]|title)\b[^>]*>(.*?)</(?:h[1-3]|title)>', text, re.I | re.S)
    title_parts += re.findall(r'<div\b[^>]*class=["\']article-title["\'][^>]*>(.*?)</div>', text, re.I | re.S)
    title = next((plain_text(value) for value in title_parts if canonical(unit['name']) in canonical(plain_text(value))), '')
    attributed = document_attribution(document['url'], unit, plain_text(text), document_heading=title)
    if attributed['status'] != 'verified':
        return None
    known = official_source_url(entry['entry_url'], unit)
    if not known and (unit.get('official_url') or urlsplit(document['url']).hostname != 'yz.chsi.com.cn' or not title):
        return None
    proof = {'document_id': document['id'], 'url': document['url'], 'local_path': document['local_path'],
             'sha256': document['sha256'], 'quote': entry['quote'], 'link_label': entry['link_label'],
             'relation': 'official_annual_catalog_link'}
    official = unit.get('official_url') or entry['base_url'] + '/'
    bound = {**{key: value for key, value in unit.items() if key != '_binding_evidence'}, 'official_url': official}
    return {'unit_id': str(unit['id']), 'unit_name': unit['name'], 'year': 2027,
            'base_url': entry['base_url'], 'entry_url': entry['entry_url'], 'unit': bound,
            'evidence': [proof], 'binding_required': not bool(original_unit.get('official_url')),
            'binding_evidence': unit.get('_binding_evidence', proof) if not original_unit.get('official_url') else None}


def _source_groups(rows):
    groups = {}
    for row in rows:
        identifier = str(row.get('unit_id'))
        old = groups.get(identifier, {})
        groups[identifier] = {**old, **row, 'pages': [*old.get('pages', []), *row.get('pages', [])],
                              'bindings': [*old.get('bindings', []), *row.get('bindings', [])]}
    return list(groups.values())


def _pdf_text(document, root):
    directory = root/'data/runtime/catalog_jobs/zsml/discovery_text'
    path, metadata = directory/f'{document["sha256"]}.txt', directory/f'{document["sha256"]}.json'
    try:
        record = json.loads(metadata.read_text('utf-8'))
        text = path.read_bytes()
        if record.get('source_sha256') == document['sha256'] and hashlib.sha256(text).hexdigest() == record.get('text_sha256'):
            return text.decode('utf-8')
    except (OSError, ValueError, TypeError):
        pass
    text = extract_pdf_text(root/document['local_path'])
    directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.txt.tmp'); temporary.write_bytes(text.encode('utf-8')); temporary.replace(path)
    _write(metadata, {'source_sha256': document['sha256'], 'text_sha256': hashlib.sha256(text.encode()).hexdigest()})
    return text


def _inspect(original, row, document, root, year):
    raw = _read(document, root)
    if raw is None:
        return [], {'unit_id': str(original['id']), 'url': document.get('url'), 'error': '原文缺失或摘要不一致'}
    pdf = raw.startswith(b'%PDF-')
    if not pdf and not any(token in raw for token in (b'sszsml', b'getTreeData', b'zsmlYjfx')):
        return [], None
    try:
        text = (_pdf_text(document, root) if pdf else
                rendered_source_html(decode_text(raw, document.get('content_type', '')), document['url']))
        entries = _entries(text, document['url'], year)
        unit = _bound_unit(original, row, root) if entries else original
        return [value for entry in entries if (value := _candidate(unit, original, document, text, entry)) is not None], None
    except (ValueError, OSError) as error:
        return [], {'unit_id': str(original['id']), 'url': document.get('url'), 'error': str(error)}


def discover_candidates(registry, national, root=PROJECT_ROOT, *, year=2027, progress=None):
    root = Path(root).resolve()
    units = {str(unit['id']): unit for unit in registry['units']}
    candidates, errors, seen_sources, inputs = {}, [], set(), []
    for row in _source_groups(national.get('units', [])):
        original = units.get(str(row.get('unit_id')))
        if original is None:
            continue
        for document in row.get('pages', []):
            identity = (document.get('url'), document.get('sha256'), str(original['id']))
            if identity in seen_sources or document.get('unit_id') not in (None, str(original['id'])):
                continue
            seen_sources.add(identity)
            inputs.append((original, row, document))
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(_inspect, original, row, doc, root, year) for original, row, doc in inputs]
        for completed, job in enumerate(as_completed(jobs), 1):
            values, error = job.result()
            if error:
                errors.append(error)
            for candidate in values:
                key = (candidate['unit_id'], candidate['base_url'])
                old = candidates.get(key)
                candidates[key] = ({**old, 'evidence': [*old['evidence'], *candidate['evidence']]} if old else candidate)
            if progress and (completed % 500 == 0 or completed == len(jobs)):
                progress({'inspected_sources': completed, 'source_total': len(jobs), 'candidate_units': len({key[0] for key in candidates})})
    return {'metadata': {'year': year, 'scanned_units': len({str(row.get('unit_id')) for row in national.get('units', [])}),
             'source_groups': len(national.get('units', [])),
             'inspected_sources': len(seen_sources), 'candidate_units': len({key[0] for key in candidates}),
             'checked_at': _now(), 'catalog_complete': False},
            'candidates': sorted(candidates.values(), key=lambda value: value['unit_id']), 'errors': errors}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    temporary.replace(path)


def _verified(result, unit, root):
    verifier = SourceVerifier(root)
    documents = {doc['id']: doc for doc in result.get('documents', [])}
    invalid = [program['id'] for program in result.get('programs', []) if not verifier.program(program, documents, unit)]
    scope = verifier.unit_reconciles(unit, 2027, result.get('programs', []), documents)
    return {'invalid_programs': invalid, 'valid_programs': len(result.get('programs', []))-len(invalid),
            'unit_reconciles': scope}


def _collect(candidate, root, refresh):
    path = root/f'data/runtime/catalog_jobs/zsml/{candidate["unit_id"]}.json'
    try:
        if path.is_file() and not refresh:
            try:
                old = json.loads(path.read_text('utf-8'))
                old = old if isinstance(old, dict) else {}
            except (ValueError, OSError):
                old = {}
            if old.get('base_url') == candidate['base_url'] and old.get('status', {}).get('status') == 'verified':
                proof = _verified(old, candidate['unit'], root)
                if proof['unit_reconciles'] and not proof['invalid_programs']:
                    return {'unit_id': candidate['unit_id'], 'unit_name': candidate['unit_name'], 'state': 'verified',
                            'programs': len(old['programs']), 'result_path': path.relative_to(root).as_posix(), 'resumed': True}
        parts = urlsplit(candidate['base_url'])
        base = urlunsplit(('https', parts.netloc, parts.path, '', ''))
        result = collect_zsml(candidate['unit'], root/'data/snapshots/catalogs', base_url=base,
                              project_root=root, refresh=refresh)
        proof = _verified(result, candidate['unit'], root)
        status = catalog_status(result, candidate['unit'], 2027)
        if not proof['unit_reconciles'] or proof['invalid_programs']:
            status = {**status, 'status': 'partial', 'verified_records': proof['valid_programs'],
                      'notes': [*status['notes'], '独立原文重放或全校专业页对账尚未全部通过。']}
        result = {**result, 'status': status, 'verification': proof, 'unit': candidate['unit'],
                  'base_url': candidate['base_url'], 'discovery_evidence': candidate['evidence'],
                  'binding_evidence': candidate.get('binding_evidence')}
        _write(path, result)
        return {'unit_id': candidate['unit_id'], 'unit_name': candidate['unit_name'], 'state': status['status'],
                'programs': len(result['programs']), 'expected_nodes': result.get('expected_nodes'),
                'fetched_nodes': result.get('fetched_nodes'), 'issues': result.get('issues', []),
                'result_path': path.relative_to(root).as_posix(), 'binding_required': candidate['binding_required']}
    except (ValueError, OSError, DocumentError) as error:
        return {'unit_id': candidate['unit_id'], 'unit_name': candidate['unit_name'], 'state': 'failed', 'error': str(error)}


def collect_candidates(candidates, root=PROJECT_ROOT, *, workers=3, skip=(), refresh=False, progress=None):
    root = Path(root).resolve()
    rows = [{'unit_id': value['unit_id'], 'unit_name': value['unit_name'], 'state': 'already_collected'}
            for value in candidates if value['unit_id'] in skip]
    pending = [value for value in candidates if value['unit_id'] not in skip]
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as pool:
        jobs = {pool.submit(_collect, value, root, refresh): value for value in pending}
        for job in as_completed(jobs):
            rows.append(job.result())
            if progress:
                progress(rows)
    return {'metadata': {'year': 2027, 'catalog_complete': False, 'checked_at': _now(),
                        'discovered_units': len(candidates), 'finished_units': len(rows)}, 'units': rows}


def _homepage_unit(unit, source, root, fetcher, max_pages):
    directory = root/f'data/snapshots/catalogs/zsml-discovery/homepages/{unit["id"]}'
    previous = {}
    try:
        previous = json.loads((directory/'state.json').read_text('utf-8'))
    except (OSError, ValueError):
        pass
    previous = {**previous, 'unit_id': str(unit['id']), 'year': 2027,
        'frontier': [{'url': unit['official_url'], 'kind': 'navigation', 'title': '已核准官网首页',
                      'priority': -100, 'depth': 0}, *previous.get('frontier', [])]}
    cached = {**source, 'pages': [{**doc, 'snapshot_path': doc.get('local_path'), 'status': 'fetched'}
                                for doc in source.get('pages', [])]}
    try:
        return collect_unit(unit, cached, directory, project_root=root, year=2027,
                            max_pages=len(previous.get('pages', []))+max_pages, max_depth=3,
                            fetcher=fetcher, previous=previous)
    except (SourceError, OSError, ValueError, KeyError) as error:
        return {'unit_id': str(unit['id']), 'unit_name': unit['name'], 'pages': [], 'status': 'failed',
                'errors': [{'url': unit['official_url'], 'message': str(error)}], 'catalog_complete': False}


def crawl_homepages(registry, national, root=PROJECT_ROOT, *, workers=8, max_pages=6, progress=None):
    """Visit every bound official entry first, reusing the shared bounded/resumable crawler."""
    if type(max_pages) is not int or not 1 <= max_pages <= 20:
        raise ValueError('每单位首页发现增量必须在1至20页之间')
    root = Path(root).resolve()
    units = [unit for unit in registry['units'] if unit.get('official_url')]
    if any(not re.fullmatch(r'\d+', str(unit.get('id', ''))) for unit in units):
        raise ValueError('招生单位标识格式不正确')
    sources = {str(row['unit_id']): row for row in _source_groups(national.get('units', []))}
    fetcher, rows = HostPacer(), []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        jobs = [pool.submit(_homepage_unit, unit, sources.get(str(unit['id']), {}), root, fetcher, max_pages) for unit in units]
        for job in as_completed(jobs):
            rows.append(job.result())
            if progress and (len(rows) % 10 == 0 or len(rows) == len(jobs)):
                progress(rows)
    originals = {(row['unit_id'], doc['url'], doc['sha256']) for row in rows for doc in row.get('pages', [])}
    return {'metadata': {'year': 2027, 'catalog_complete': False, 'checked_at': _now(),
        'indexed_units': len(registry['units']), 'official_entry_units': len(units), 'visited_units': len(rows),
        'originals': len(originals), 'notes': ['官方首页发现仅覆盖已证明归属的官网入口；未把有限爬取当作专业目录完整。']}, 'units': rows}


def main():
    cli = argparse.ArgumentParser(description='扫描全国官方原文中的同构公开目录并增量采集，不推定其他目录格式已覆盖')
    cli.add_argument('--national-sources', type=Path, default=PROJECT_ROOT/'data/national_sources.json')
    cli.add_argument('--units', type=Path, default=PROJECT_ROOT/'data/units.json')
    cli.add_argument('--output', type=Path, default=PROJECT_ROOT/'data/national_zsml.json')
    cli.add_argument('--additional-sources', type=Path)
    cli.add_argument('--discover-only', action='store_true')
    cli.add_argument('--refresh', action='store_true')
    cli.add_argument('--crawl-homepages', action='store_true')
    args = cli.parse_args()
    registry = json.loads(args.units.read_text('utf-8-sig'))
    national = json.loads(args.national_sources.read_text('utf-8-sig'))
    if args.additional_sources:
        extra = json.loads(args.additional_sources.read_text('utf-8-sig'))
        national = {**national, 'units': [*national['units'], *extra['units']]}
    if args.crawl_homepages:
        homepage_output = PROJECT_ROOT/'data/runtime/catalog_jobs/zsml/homepages.json'
        def home_progress(rows):
            _write(homepage_output, {'units': rows, 'catalog_complete': False, 'checked_at': _now()})
            print(json.dumps({'homepage_units_visited': len(rows)}, ensure_ascii=False), flush=True)
        homes = crawl_homepages(registry, national, progress=home_progress)
        _write(homepage_output, homes)
        national = {**national, 'units': [*national['units'], *homes['units']]}
    discovery = discover_candidates(registry, national,
        progress=lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
    _write(args.output, discovery)
    print(json.dumps(discovery['metadata'], ensure_ascii=False), flush=True)
    if args.discover_only:
        return
    def progress(rows):
        _write(args.output, {**discovery, 'units': rows})
        summary = {**rows[-1]}
        if summary.get('issues'):
            summary = {**summary, 'issue_count': len(summary['issues']), 'issues': summary['issues'][:3]}
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    result = collect_candidates(discovery['candidates'], skip=ALREADY_COLLECTED, refresh=args.refresh, progress=progress)
    _write(args.output, {**discovery, **result, 'discovery_metadata': discovery['metadata']})


if __name__ == '__main__':
    main()
