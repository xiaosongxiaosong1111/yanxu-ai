"""全国采用数据节点0：只读基线、缺口队列与首批10校选择。

本模块只读取公开包，不联网、不写采用文件、不创建发布索引。口径与
docs/CLAUDE_NATIONAL_DATA_RUNBOOK.md 一致：原始包、既有规则补充后、实际发布是
三种不同口径，不能互相替代。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import ijson

PROTECTED_FILES = (
    'collectors/catalog_pdf_tables.py',
    'collectors/catalog_pdf_variants.py',
    'collectors/catalog_pdf_national.py',
    'collectors/catalog_pdf_national_v2.py',
    'collectors/catalog_hit_public.py',
)
COLLECTIONS = ('documents', 'programs', 'admitted_statistics', 'score_lines')
NOTICE = ('只读审计：未联网、未调用模型或元宝、未写采用文件、未创建发布索引；'
          '非空字段不等于原文已核验，完整资格仍以逐条条款审校为准。')


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def original_profile(text):
    urls = []
    for raw in str(text or '').split('\n'):
        value = raw.strip()
        if value and value.lower().startswith(('http://', 'https://')) and value not in urls:
            urls.append(value)
    pdf = [url for url in urls if url.lower().split('?')[0].endswith('.pdf')]
    catalog = [url for url in urls
               if any(word in url.lower() for word in ('zsml', 'zyml', 'catalog', 'zsjz'))
               or url.lower().split('?')[0].endswith(('.pdf', '.htm', '.html'))]
    hosts = sorted({url.split('/')[2] for url in urls if url.count('/') >= 2})
    return {'url_count': len(urls), 'pdf_count': len(pdf),
            'catalog_like_count': len(catalog), 'hosts': hosts}


def classify_gaps(row, stats):
    """Return every real gap code for one unit; a unit may have several."""
    gaps = []
    program_count = int(stats.get('program_count') or 0)
    reason = str(row.get('missing_reason') or '')
    if not str(row.get('official_url') or '').strip() or '尚未核准' in reason:
        gaps.append('missing_trusted_entry')
    if program_count == 0:
        gaps.append('missing_catalog')
    elif row.get('catalog_status') == 'partial':
        gaps.append('partial_catalog')
    if program_count and not stats.get('full_qualification'):
        gaps.append('missing_full_qualification')
    if program_count and not stats.get('tuition'):
        gaps.append('missing_tuition')
    if program_count and not stats.get('duration'):
        gaps.append('missing_duration')
    status = str(row.get('collection_status') or '')
    if status in ('needs_binding', 'truncated') or '登录或限制访问' in reason or '尚未找到可核验' in reason:
        gaps.append('original_pending')
    if status == 'collected' and program_count == 0 and '待解析' in reason:
        gaps.append('parser_pending')
    if row.get('catalog_status') == 'partial' and program_count:
        gaps.append('review_pending')
    return gaps


def _expansion_candidate(row):
    return (int(row.get('program_count') or 0) == 0
            and str(row.get('collection_status') or '') == 'collected'
            and '待解析' in str(row.get('missing_reason') or '')
            and str(row.get('official_url') or '').strip())


def _backfill_candidate(row):
    return (int(row.get('program_count') or 0) > 0
            and not int(row.get('tuition') or 0)
            and not int(row.get('duration') or 0))


def select_batch(rows, thresholds=None, *, expansion=7, backfill=3):
    """Pick a deterministic first batch from real coverage rows only."""
    expand, back = [], []
    for row in rows:
        if _expansion_candidate(row):
            profile = original_profile(row.get('original_urls') or '')
            if not profile['url_count']:
                continue
            expand.append({**row, 'role': 'expand_catalog', 'profile': profile,
                           'reason': '已保存2027官方原文但尚无采用方向；本轮先解析并核对目录全集。',
                           'next_step': '建立原件清单与目录全集对账，再按格式选择适配器'})
        elif _backfill_candidate(row):
            back.append({**row, 'role': 'backfill_fields',
                         'reason': '已有采用方向，但费用与学制均为空；本轮补原文依据而不是改标志。',
                         'next_step': '在既有目录校内选真实章程/简章补齐费用与学制逐项证据'})
    expand.sort(key=lambda row: (-row['profile']['catalog_like_count'],
                                 -row['profile']['pdf_count'],
                                 -row['profile']['url_count'], row['unit_id']))
    back.sort(key=lambda row: ('original_pending' in (row.get('gaps') or []),
                               -int(row.get('program_count') or 0), row['unit_id']))
    return {'expansion': expand[:expansion], 'backfill': back[:backfill]}


def count_programs(programs):
    """Count raw program facts exactly as stored; no value implies verification."""
    total = {'program_count': 0, 'tuition': 0, 'tuition_year': 0, 'tuition_full': 0,
             'tuition_other': 0, 'duration': 0, 'conditions_nonempty': 0,
             'requires_review': 0, 'full_qualification': 0,
             'computed_total_cost': 0, 'ids': set(), 'duplicate_ids': 0,
             'years': Counter(), 'units': 0}
    per_unit = defaultdict(lambda: {'program_count': 0, 'tuition': 0, 'tuition_year': 0,
                                    'tuition_full': 0, 'duration': 0, 'full_qualification': 0,
                                    'conditions_nonempty': 0})
    seen = set()
    for program in programs:
        identifier = program.get('id')
        if identifier in seen:
            total['duplicate_ids'] += 1
        seen.add(identifier)
        unit_id = program.get('unit_id')
        row = per_unit[unit_id]
        tuition = program.get('tuition')
        duration = program.get('duration_years')
        full = program.get('eligibility_evidence_complete') is True
        total['program_count'] += 1
        total['years'][program.get('year')] += 1
        row['program_count'] += 1
        if tuition is not None:
            total['tuition'] += 1
            row['tuition'] += 1
            period = str((tuition or {}).get('period') or '') if isinstance(tuition, dict) else ''
            if period == 'year':
                total['tuition_year'] += 1
                row['tuition_year'] += 1
            elif period in ('full', 'total'):
                total['tuition_full'] += 1
                row['tuition_full'] += 1
            else:
                total['tuition_other'] += 1
            if period == 'year' and duration is not None:
                total['computed_total_cost'] += 1
        elif isinstance(tuition, dict) and duration is not None:
            pass
        if isinstance(tuition, dict) and tuition.get('period') in ('full', 'total'):
            total['computed_total_cost'] += 1
        if duration is not None:
            total['duration'] += 1
            row['duration'] += 1
        conditions = program.get('conditions')
        if isinstance(conditions, list) and conditions:
            total['conditions_nonempty'] += 1
            row['conditions_nonempty'] += 1
        if program.get('requires_review') is True:
            total['requires_review'] += 1
        if full:
            total['full_qualification'] += 1
            row['full_qualification'] += 1
    total['units'] = len(per_unit)
    total['ids'] = len(seen)
    return {'totals': total, 'per_unit': dict(per_unit)}


def stream_counts(path):
    """Stream programs.item so the 160MB JSON is never fully materialized."""
    programs = ijson.items(open(path, 'rb'), 'programs.item')
    return count_programs(programs)


def _load_json(path):
    with open(path, encoding='utf-8-sig') as handle:
        return json.load(handle)


def effective_counts(data_dir):
    """Apply the existing enrichment and evidence-safety layers, then recount."""
    from enrichment import apply_enrichment
    from evidence import apply_evidence_safety
    from service_verification import SourceVerifier
    data_dir = Path(data_dir)
    registry = _load_json(data_dir / 'units.json')
    admissions = _load_json(data_dir / 'admissions.json')
    enriched = apply_enrichment(registry, admissions, data_dir)
    safe = apply_evidence_safety(registry, enriched, data_dir,
                                 verifier=SourceVerifier(data_dir))
    suspended = sum(1 for program in safe.get('programs', [])
                    if isinstance(program, dict) and program.get('evidence_conflicts'))
    result = count_programs(safe.get('programs', []))
    result['totals']['suspended_conflicts'] = suspended
    result['enrichment_metadata'] = safe.get('metadata', {}).get('enrichment', {})
    return result


def _units(registry, annual):
    index = {unit['id']: unit for unit in registry.get('units', [])
             if isinstance(unit, dict) and isinstance(unit.get('id'), str)}
    rows = []
    for record in annual.get('units', []):
        if record.get('annual_status') != 'confirmed' or type(record.get('year')) is not int:
            continue
        unit_id = record.get('unit_id')
        unit = index.get(unit_id, {})
        rows.append({
            'unit_id': unit_id,
            'unit_code': record.get('unit_code') or unit.get('unit_code'),
            'unit_name': record.get('unit_name') or unit.get('name'),
            'province': record.get('province') or unit.get('province'),
            'year': record.get('year'),
            'annual_status': record.get('annual_status'),
            'annual_evidence_count': len(record.get('evidence') or []),
            'official_url': unit.get('official_url') or '',
            'official_url_evidence': unit.get('official_url_evidence'),
        })
    return rows


def build_queue(coverage_rows, per_unit, annual_rows):
    by_unit = {row['unit_id']: row for row in coverage_rows}
    queue = []
    for base in annual_rows:
        unit_id = base['unit_id']
        coverage = by_unit.get(unit_id, {})
        stats = per_unit.get(unit_id, {'program_count': 0, 'tuition': 0, 'duration': 0,
                                       'full_qualification': 0, 'conditions_nonempty': 0})
        row = {**base, **{key: coverage.get(key) for key in
                          ('program_count', 'fact_count', 'catalog_status', 'collection_status',
                           'source_count', 'missing_reason', 'original_urls')}}
        row['official_url'] = base['official_url'] or coverage.get('official_url') or ''
        row['program_count'] = int(stats.get('program_count') or 0)
        row['coverage_program_count'] = int(coverage.get('program_count') or 0)
        row['tuition'] = int(stats.get('tuition') or 0)
        row['duration'] = int(stats.get('duration') or 0)
        row['full_qualification'] = int(stats.get('full_qualification') or 0)
        row['conditions_nonempty'] = int(stats.get('conditions_nonempty') or 0)
        row['gaps'] = classify_gaps(row, stats)
        row['count_mismatch'] = row['program_count'] != row['coverage_program_count']
        queue.append(row)
    return queue


def protected_hashes(root):
    return {name: sha256_file(Path(root) / name) for name in PROTECTED_FILES}


def tool_versions(raw_pdf, bbox_pdf):
    result = {}
    for key, path in (('raw_pdftotext', raw_pdf), ('bbox_pdftotext', bbox_pdf)):
        value = {'path': str(path or '')}
        if path:
            try:
                output = subprocess.run([path, '-v'], capture_output=True, text=True, timeout=20)
                value['version'] = (output.stdout or output.stderr).splitlines()[0].strip()
            except (OSError, subprocess.SubprocessError) as error:
                value['error'] = type(error).__name__
        result[key] = value
    return result


def _summary(totals):
    keys = ('program_count', 'ids', 'duplicate_ids', 'units', 'tuition', 'tuition_year',
            'tuition_full', 'tuition_other', 'duration', 'computed_total_cost',
            'conditions_nonempty', 'requires_review', 'full_qualification')
    return {key: totals.get(key) for key in keys}


def audit(data_dir, out_dir, *, raw_pdf='', bbox_pdf='', repo_root='.', expansion=7, backfill=3):
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    registry = _load_json(data_dir / 'units.json')
    annual = _load_json(data_dir / 'annual_registry_2027.json')
    with open(data_dir / 'catalog_coverage_2027.csv', encoding='utf-8-sig', newline='') as handle:
        coverage_rows = list(csv.DictReader(handle))
    raw = stream_counts(data_dir / 'admissions.json')
    effective = effective_counts(data_dir)
    annual_rows = _units(registry, annual)
    queue = build_queue(coverage_rows, raw['per_unit'], annual_rows)
    picks = select_batch(queue, expansion=expansion, backfill=backfill)
    batch = {'batch_id': 'b001', 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
             'selection_rule': '数据队列确定性排序；不手写学校名',
             'expansion': picks['expansion'], 'backfill': picks['backfill'],
             'notice': NOTICE}
    baseline = {
        'generated_at': batch['generated_at'],
        'git': {'commit': subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True,
                                         text=True).stdout.strip(),
                'branch': subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                                         capture_output=True, text=True).stdout.strip()},
        'package': {'catalog_data_zip_sha256': sha256_file(data_dir / 'bundles' / 'catalog-data.zip'),
                    'manifest_sha256': sha256_file(data_dir / 'bundles' / 'manifest.json')},
        'protected_sha256': protected_hashes(Path(repo_root)),
        'tools': tool_versions(raw_pdf, bbox_pdf),
        'annual': {key: annual.get('metadata', {}).get(key) for key in
                   ('year', 'official_total', 'indexed_records', 'confirmed_units', 'not_listed_index_records')},
        'annual_rows': len(annual_rows),
        'coverage_rows': len(coverage_rows),
        'raw': _summary(raw['totals']),
        'effective_after_enrichment_and_evidence_safety': _summary(effective['totals']),
        'effective_extra': {'enrichment': effective.get('enrichment_metadata', {}),
                            'suspended_conflicts': effective['totals'].get('suspended_conflicts', 0)},
        'catalog_status': dict(Counter(row.get('catalog_status') for row in coverage_rows)),
        'collection_status': dict(Counter(row.get('collection_status') for row in coverage_rows)),
        'queue_gap_counts': dict(Counter(gap for row in queue for gap in row['gaps'])),
        'queue_count': len(queue),
        'queue_count_mismatch': sum(1 for row in queue if row['count_mismatch']),
        'notice': NOTICE,
    }
    for name, payload in (('baseline.json', baseline), ('queue.json', queue),
                          ('batch.json', batch)):
        (out_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({'out_dir': str(out_dir), 'raw': baseline['raw'],
                      'effective': baseline['effective_after_enrichment_and_evidence_safety'],
                      'batch': {'expansion': [row['unit_id'] for row in batch['expansion']],
                                'backfill': [row['unit_id'] for row in batch['backfill']]}},
                     ensure_ascii=False))
    return baseline


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default='data')
    parser.add_argument('--out', default='data/batch_runs/b001')
    parser.add_argument('--raw-pdf', default='')
    parser.add_argument('--bbox-pdf', default='')
    parser.add_argument('--repo-root', default='.')
    args = parser.parse_args(argv)
    audit(args.data_dir, args.out, raw_pdf=args.raw_pdf, bbox_pdf=args.bbox_pdf,
          repo_root=args.repo_root)
    return 0


if __name__ == '__main__':
    sys.exit(main())
