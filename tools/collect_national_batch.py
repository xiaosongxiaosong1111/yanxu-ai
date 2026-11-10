"""Replay a committed national batch; new school requests require --network."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collectors.documents import decode_text
from collectors.national_sources import (HostPacer, MAX_BYTES, _binary_suffix,
                                         atomic_json, collect_national, fetch_source)
from public_sources import SourceError


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _inside(path, base, *, descendant=False):
    resolved, base = Path(path).resolve(), Path(base).resolve()
    if not resolved.is_relative_to(base) or descendant and resolved == base:
        raise ValueError('路径必须位于指定的隔离工作树目录内: ' + str(base))
    return resolved


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _committed_batch(root, path):
    relative = path.relative_to(root).as_posix()
    result = subprocess.run(['git', '-C', str(root), 'show', 'HEAD:' + relative],
                            capture_output=True, timeout=15, check=False)
    if result.returncode or result.stdout != path.read_bytes():
        raise ValueError('批次必须与当前 HEAD 已提交文件字节一致')
    return result.stdout


def _scope(registry, batch):
    if not isinstance(registry, dict) or not isinstance(registry.get('units'), list):
        raise ValueError('单位索引必须提供 units 列表')
    if not isinstance(batch, dict) or not isinstance(batch.get('batch_id'), str):
        raise ValueError('已提交批次缺少 batch_id')
    rows = []
    for key in ('expansion', 'backfill'):
        if not isinstance(batch.get(key), list):
            raise ValueError('批次必须提供 expansion/backfill 列表')
        rows.extend(batch[key])
    units = {str(unit['id']): unit for unit in registry['units']}
    selected, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('批次单位记录不合法')
        identity = str(row.get('unit_id', ''))
        unit = units.get(identity)
        if identity in seen or unit is None or row.get('year') != 2027:
            raise ValueError('批次单位须来自真实索引，年份为2027且不能重复')
        if str(row.get('unit_code')) != str(unit.get('unit_code')):
            raise ValueError('批次单位代码与索引不一致')
        selected.append(dict(unit))
        seen.add(identity)
    if not selected:
        raise ValueError('批次不能为空')
    return {**registry, 'units': selected}


def _cached_pages(root, inventory, identities):
    if not isinstance(inventory, dict) or not isinstance(inventory.get('units'), list):
        raise ValueError('线索清单必须提供 units 列表')
    rows, cached, seen = [], {}, set()
    for row in inventory['units']:
        if not isinstance(row, dict):
            raise ValueError('线索单位记录不合法')
        identity = str(row.get('unit_id', ''))
        if identity not in identities or identity in seen:
            raise ValueError('线索只能包含本批单位，且每个单位只能出现一次')
        seen.add(identity)
        if not isinstance(row.get('candidates', []), list) or not isinstance(row.get('pages', []), list):
            raise ValueError('线索 candidates/pages 必须是列表')
        html = []
        for page in row.get('pages', []):
            if not isinstance(page, dict):
                raise ValueError('缓存页面记录不合法')
            if page.get('status') != 'fetched':
                continue
            location = page.get('snapshot_path') or page.get('local_path')
            if not isinstance(location, str) or not isinstance(page.get('url'), str):
                raise ValueError('缓存页面缺少原件路径或URL')
            path = _inside(root / location, root / 'data/snapshots', descendant=True)
            if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
                raise ValueError('缓存原件缺失或超过32MB')
            if _sha(path) != page.get('sha256'):
                raise ValueError('缓存原件 SHA 与清单不一致')
            mime = page.get('content_type', 'text/html')
            if not isinstance(mime, str):
                raise ValueError('缓存MIME不合法')
            is_html = mime.split(';')[0].strip().lower() in {'text/html', 'application/xhtml+xml'}
            raw = path.read_bytes()
            if not is_html and not _binary_suffix(raw, page['url'], mime):
                raise ValueError('缓存原件MIME或文件签名不合法')
            if is_html and _binary_suffix(raw, page['url'], mime):
                raise ValueError('二进制原件不能伪装成HTML缓存')
            record = {**page, 'path': path, 'content_type': mime, 'is_html': is_html}
            for url in {page['url'], page.get('requested_url', page['url'])}:
                if not isinstance(url, str) or urlsplit(url).scheme not in {'http', 'https'}:
                    raise ValueError('缓存URL必须为HTTP(S)')
                key = (identity, url)
                if key in cached and cached[key]['sha256'] != page['sha256']:
                    raise ValueError('同一缓存URL存在冲突原件')
                cached[key] = record
            if is_html:
                html.append({**page, 'snapshot_path': path.relative_to(root).as_posix()})
        rows.append({**row, 'pages': html})
    return {**inventory, 'units': rows}, cached


def _fetcher(cached, *, network, pacer, counts):
    lock = threading.Lock()

    def record(name):
        with lock:
            counts[name] += 1

    def fetch(url, unit):
        page = cached.get((str(unit['id']), url))
        if page:
            raw = page['path'].read_bytes()
            if hashlib.sha256(raw).hexdigest() != page['sha256']:
                raise SourceError('invalid_cache', '缓存原件 SHA 已改变')
            record('fetcher_cache_hits')
            original_headers = {name: page[name] for name in ('response_content_type',
                'response_content_disposition', 'type_detection') if name in page}
            return {'url': page['url'], 'raw': raw, 'sha256': page['sha256'],
                    'text': decode_text(raw, page['content_type']) if page['is_html'] else '',
                    'content_type': page['content_type'], 'checked_at': page.get('checked_at'),
                    **original_headers}
        host = (urlsplit(url).hostname or '').lower().rstrip('.')
        if not network or host == 'chsi.com.cn' or host.endswith('.chsi.com.cn'):
            record('network_disabled')
            raise SourceError('network_disabled', '本批未启用此原件的新请求；只复用缓存，不代表远端限制访问')
        record('network_attempts')
        return pacer(url, unit)

    return fetch


def _validate_limits(network, workers, interval, max_pages, max_depth):
    if type(network) is not bool or type(workers) is not int or not 1 <= workers <= 2:
        raise ValueError('network须为布尔值，workers只能为1或2')
    if type(interval) not in {int, float} or not math.isfinite(interval) or interval < 2:
        raise ValueError('同主机间隔至少2秒')
    if type(max_pages) is not int or max_pages < 1 or type(max_depth) is not int or max_depth < 0:
        raise ValueError('max_pages须为正整数，max_depth须为非负整数')


def run_batch(root, batch_path, inventory_path, output, snapshot_dir, *, network=False,
              workers=2, interval=2, max_pages=80, max_depth=6, collector=None, pacer_factory=None):
    _validate_limits(network, workers, interval, max_pages, max_depth)
    root = Path(root).resolve()
    batch_path = _inside(batch_path, root, descendant=True)
    units_path = root / 'data/units.json'
    inventory_path = _inside(inventory_path, root / 'data/batch_runs', descendant=True)
    output = _inside(output, root / 'data/batch_runs', descendant=True)
    output_stage = _inside(output.with_suffix(output.suffix + '.tmp'), root / 'data/batch_runs', descendant=True)
    snapshot_dir = _inside(snapshot_dir, root / 'data/snapshots', descendant=True)
    inputs = {batch_path, units_path.resolve(), inventory_path}
    if inputs.intersection({output, output_stage}) or output.suffix != '.json':
        raise ValueError('输出只能是独立JSON报告，不能覆盖输入或目录库')
    batch = json.loads(_committed_batch(root, batch_path).decode('utf-8-sig'))
    registry = json.loads(units_path.read_text('utf-8-sig'))
    scoped = _scope(registry, batch)
    identities = [str(row['id']) for row in scoped['units']]
    inventory, cached = _cached_pages(root, json.loads(inventory_path.read_text('utf-8-sig')), identities)
    counts = {'fetcher_cache_hits': 0, 'network_attempts': 0, 'network_disabled': 0}
    pacer = (pacer_factory or HostPacer)(interval=interval,
        fetcher=lambda url, unit: fetch_source(url, unit, blocked_hosts=('chsi.com.cn',)))
    fetch = _fetcher(cached, network=network, pacer=pacer, counts=counts)
    provenance = {'schema_version': 1, 'batch_id': batch['batch_id'], 'started_at': _now(),
        'unit_ids': identities, 'cache_url_count': len(cached),
        'source_files': {name: {'path': path.relative_to(root).as_posix(), 'sha256': _sha(path)}
                         for name, path in (('batch', batch_path), ('units', units_path), ('inventory', inventory_path))},
        'parameters': {'network': network, 'new_chsi_requests': False, 'year': 2027, 'workers': workers,
            'interval': interval, 'max_pages': max_pages, 'max_depth': max_depth,
            'output': output.relative_to(root).as_posix(), 'snapshot_dir': snapshot_dir.relative_to(root).as_posix()}}
    result, failure = {'metadata': {}, 'units': []}, None
    try:
        result = (collector or collect_national)(scoped, inventory, output, snapshot_dir,
            project_root=root, year=2027, workers=workers, max_pages=max_pages,
            max_depth=max_depth, fetcher=fetch, refresh=False)
    except (Exception, KeyboardInterrupt) as error:
        failure = error
        if output.is_file():
            try:
                result = json.loads(output.read_text('utf-8-sig'))
            except (OSError, ValueError):
                pass
    runner = {**provenance, 'finished_at': _now(), 'run_status': 'failed' if failure else 'completed',
        **counts, 'final_status_counts': dict(Counter(row.get('status', 'unknown') for row in result.get('units', [])))}
    if failure:
        runner = {**runner, 'error_type': type(failure).__name__}
    result = {**result, 'metadata': {**result.get('metadata', {}), 'batch_runner': runner}}
    atomic_json(output, result)
    if failure:
        raise failure
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--snapshot-dir', type=Path, required=True)
    parser.add_argument('--network', action='store_true', help='允许新增学校官网请求，仍禁止新增研招网请求')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--max-pages', type=int, default=80)
    parser.add_argument('--max-depth', type=int, default=6)
    args = parser.parse_args(argv)
    try:
        result = run_batch(args.root, args.batch, args.inventory, args.output, args.snapshot_dir,
            network=args.network, workers=args.workers, interval=args.interval,
            max_pages=args.max_pages, max_depth=args.max_depth)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    print(json.dumps(result['metadata'], ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
