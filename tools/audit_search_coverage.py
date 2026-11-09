"""Sample real Yuanbao searches and record what the official/reference tiers return.

The audit never treats a search summary as a fact. It only records, per sampled
unit, how many official-domain pages were accepted, how many third-party leads
were labelled, and why the rest were rejected. Results are written as counts and
plain URLs so a review can reproduce every call without reading credentials.

Run it against a locally running service:

    python -X utf8 tools/audit_search_coverage.py --sample 20 --scope official

Paid search calls: a unit may use up to three official requests (Site filter,
one no-Site fallback, one wider rewrite); `extended` adds one lead query. Pick
a sample size deliberately and keep the output under data/runtime/ (ignored).
"""
import argparse
import json
import pathlib
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def load_units(index_path):
    connection = sqlite3.connect(index_path)
    try:
        rows = connection.execute('select unit_id, payload from units order by unit_id').fetchall()
    finally:
        connection.close()
    units = []
    for unit_id, payload in rows:
        try:
            value = json.loads(payload)
        except (TypeError, ValueError):
            continue
        name = value.get('name') or value.get('unit_name')
        if isinstance(name, str) and name:
            units.append((unit_id, name))
    return units


def call_search(base_url, unit_id, year, kind, scope, timeout):
    body = json.dumps({'unit_id': unit_id, 'year': year, 'kind': kind, 'scope': scope}).encode()
    request = urllib.request.Request(base_url.rstrip('/') + '/api/search', data=body,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            value = json.loads(error.read())
        except (ValueError, OSError):
            value = {}
        detail = value.get('error')
        if isinstance(detail, dict):
            code, message = detail.get('code'), detail.get('message')
        else:
            code, message = '', detail
        return {'error': str(message or detail or '')[:300], 'code': code or '', 'status': error.code}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        return {'error': f'{type(error).__name__}: {error}'[:300], 'code': '', 'status': 0}
    data = value.get('data')
    return data if isinstance(data, dict) else {'error': 'unexpected response shape', 'code': '', 'status': 0}


def paced_search(base_url, unit_id, year, kind, scope, timeout, interval, retries):
    """Search one unit, spacing paid calls and backing off on upstream limits."""
    if interval > 0:
        time.sleep(interval)
    for attempt in range(retries + 1):
        data = call_search(base_url, unit_id, year, kind, scope, timeout)
        limited = data.get('status') == 429 or data.get('code') == 'rate_limited' or '频繁' in str(data.get('error', ''))
        if not limited or attempt >= retries:
            return data
        time.sleep(30 * (attempt + 1))
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--index', default='data/runtime/catalog-index.sqlite')
    parser.add_argument('--base-url', default='http://127.0.0.1:4180')
    parser.add_argument('--year', type=int, default=2027)
    parser.add_argument('--kind', default='catalog')
    parser.add_argument('--scope', choices=('official', 'extended'), default='official')
    parser.add_argument('--sample', type=int, default=20)
    parser.add_argument('--seed', type=int, default=20261105)
    parser.add_argument('--timeout', type=float, default=120.0)
    parser.add_argument('--interval', type=float, default=4.0, help='seconds to wait before each paid call')
    parser.add_argument('--retries', type=int, default=3, help='backoff retries when the provider rate-limits')
    parser.add_argument('--out', default='')
    args = parser.parse_args(argv)

    units = load_units(args.index)
    if not units:
        print('no units available in the catalog index', file=sys.stderr)
        return 2
    sample = random.Random(args.seed).sample(units, min(args.sample, len(units)))
    rows, counts, errors = [], {'official_results': 0, 'official_domain_candidates': 0,
                                'extended_reference': 0, 'no_official_results': 0}, []
    stages = {name: 0 for name in ('official_found', 'binding_missing', 'attribution_unverified',
                                   'third_party_only', 'domain_mismatch', 'index_no_hit')}
    rate_limited = 0
    for unit_id, name in sample:
        data = paced_search(args.base_url, unit_id, args.year, args.kind, args.scope, args.timeout,
                            args.interval, args.retries)
        if data.get('error'):
            errors.append({'unit_id': unit_id, 'unit_name': name, 'error': data['error'], 'status': data.get('status')})
            if data.get('status') == 429 or '频繁' in str(data.get('error', '')):
                rate_limited += 1
            rows.append({'unit_id': unit_id, 'unit_name': name, 'error': data['error']})
            continue
        outcome = data.get('outcome')
        if outcome in counts:
            counts[outcome] += 1
        stage = data.get('failure_stage')
        if stage in stages:
            stages[stage] += 1
        official = [row for row in data.get('results', []) if row.get('source_tier') == 'official']
        candidates = [row for row in data.get('results', []) if row.get('source_tier') == 'official_domain_candidate']
        third = [row for row in data.get('results', []) if row.get('source_tier') == 'third_party_reference']
        rows.append({'unit_id': unit_id, 'unit_name': name, 'outcome': outcome,
                     'failure_stage': stage, 'binding_status': data.get('binding_status'),
                     'binding_domains': data.get('binding_domains', []),
                     'request_count': data.get('request_count'),
                     'official_count': len(official), 'candidate_count': len(candidates),
                     'third_party_count': len(third),
                     'rejected_count': data.get('rejected_count'), 'malformed_count': data.get('malformed_count'),
                     'site_fallback': data.get('site_fallback'), 'queries_tried': data.get('queries_tried', []),
                     'official_urls': [row.get('url') for row in official][:10],
                     'candidate_hosts': data.get('candidate_hosts', []),
                     'candidate_urls': [row.get('url') for row in candidates][:10],
                     'third_party_urls': [row.get('url') for row in third][:10]})
        print(f"{name:<12} {outcome:<22} official={len(official):>2} candidate={len(candidates):>2} "
              f"third={len(third):>2} rejected={data.get('rejected_count')} fallback={data.get('site_fallback')}")

    report = {'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'index': args.index, 'year': args.year, 'kind': args.kind, 'scope': args.scope,
              'sample_size': len(sample), 'seed': args.seed, 'counts': counts,
              'failure_stages': stages,
              'errors': len(errors), 'rate_limited': rate_limited, 'rows': rows}
    if args.out:
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'wrote {path}')
    print(json.dumps({'counts': counts, 'failure_stages': stages, 'errors': len(errors),
                      'rate_limited': rate_limited}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
