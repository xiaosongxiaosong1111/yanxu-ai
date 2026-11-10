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
import math
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import ijson

from national_entry_html import (
    _URL_TOKEN_CHAR,
    _VISIBLE_URL,
    _VisibleHTMLParser,
    _WEB_SCHEMES,
    _article_fragment,
    _decode_original,
    _is_web_url,
    _resolve_web_target,
    _url_key,
    _url_token_boundary,
    _visible_original,
    _visible_quote_spans,
)
from national_entry_proof import (
    ENTRY_ROLE_CATEGORIES,
    SHA256_HEX,
    SUPPORTED_ENTRY_RELATIONS,
    SUPPORTED_ENTRY_VERIFICATIONS,
    _ENTITY_LABEL,
    _ENTITY_TAIL,
    _GENERIC_ENTITY,
    _GENERIC_ROLE_WORD,
    _NAME_LEAD_NOISE,
    _PLATFORM_LABEL,
    _ROLE_PATTERNS,
    _SELF_REFERENCE,
    _STRONG_SELF_REFERENCE,
    _document_matches,
    _enclosing_anchor,
    _entity_owned,
    _entry_fingerprint,
    _foreign_entity_before,
    _foreign_label_in,
    _foreign_name_in,
    _host,
    _identity_matches,
    _longest_common_substring,
    _official_hosts,
    _pick_entry_failure,
    _quote_match,
    _relation_accepts,
    _replay_entry_role,
    _role_evidence,
    _saved_original,
    entry_trusted,
    sha256_file,
    verify_entries,
    verify_entry,
)


PROTECTED_FILES = (
    'collectors/catalog_pdf_tables.py',
    'collectors/catalog_pdf_variants.py',
    'collectors/catalog_pdf_national.py',
    'collectors/catalog_pdf_national_v2.py',
    'collectors/catalog_hit_public.py',
)


COLLECTIONS = ('documents', 'programs', 'admitted_statistics', 'score_lines')


SELECTION_RULE_VERSION = 'b001-node0-v5'

CODE_FILES = ('national_data_baseline.py', 'national_entry_html.py',
              'national_entry_proof.py')


NOTICE = ('只读审计：未联网、未调用模型或元宝、未写采用文件、未创建发布索引；'
          '非空字段不等于原文已核验，完整资格仍以逐条条款审校为准。')


PRESENCE_KEYS = ('program_count', 'ids', 'duplicate_ids', 'units', 'tuition', 'tuition_year',
                 'tuition_full', 'tuition_other', 'duration', 'computed_total_cost',
                 'conditions_nonempty', 'requires_review', 'full_qualification')


VERIFIED_KEYS = ('program_count', 'verified_tuition', 'verified_duration',
                 'verified_full_qualification', 'verified_computed_total_cost',
                 'tuition_claim_statuses', 'duration_claim_statuses',
                 'qualification_claim_statuses')


EXPANSION_REQUIRED = ('unit_id', 'program_count', 'official_url', 'collection_status',
                      'missing_reason', 'original_urls', 'gaps', 'entry_verification')


SUMMARY_ROW_KEYS = ('unit_id', 'unit_code', 'unit_name', 'province', 'year',
                    'annual_status', 'program_count', 'coverage_program_count',
                    'raw_tuition', 'raw_duration', 'raw_full_qualification',
                    'effective_tuition', 'effective_duration',
                    'effective_full_qualification', 'verified_tuition',
                    'verified_duration', 'verified_full_qualification',
                    'verified_computed_total_cost', 'remaining_tuition',
                    'remaining_duration', 'remaining_full_qualification',
                    'catalog_status', 'collection_status', 'source_count',
                    'missing_reason', 'official_url', 'entry_trusted', 'gaps',
                    'count_mismatch')


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


def _positive_number(value):
    return (type(value) in (int, float) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def _tuition_amount(tuition):
    return isinstance(tuition, dict) and _positive_number(tuition.get('amount'))


def _count(stats, name):
    """Read a verified count when present, else the legacy presence count."""
    value = stats.get('verified_' + name)
    if value is None:
        value = stats.get(name)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def classify_gaps(row, stats):
    """Return every real gap code for one unit; a unit may have several."""
    gaps = []
    program_count = int(stats.get('program_count') or 0)
    reason = str(row.get('missing_reason') or '')
    proof = row.get('entry_verification')
    if (not str(row.get('official_url') or '').strip() or '尚未核准' in reason
            or (isinstance(proof, dict) and proof.get('status') != 'verified')):
        gaps.append('missing_trusted_entry')
    if program_count == 0:
        gaps.append('missing_catalog')
    elif row.get('catalog_status') == 'partial':
        gaps.append('partial_catalog')
    if program_count:
        for field in ('full_qualification', 'tuition', 'duration'):
            known = _count(stats, field)
            if known == 0:
                gaps.append('missing_' + field)
            elif known < program_count:
                gaps.append('partial_' + field)
    status = str(row.get('collection_status') or '')
    if status in ('needs_binding', 'truncated') or '登录或限制访问' in reason or '尚未找到可核验' in reason:
        gaps.append('original_pending')
    if status == 'collected' and program_count == 0 and '待解析' in reason:
        gaps.append('parser_pending')
    if row.get('catalog_status') == 'partial' and program_count:
        gaps.append('review_pending')
    return gaps


def _looks_like_expansion(row):
    return (int(row.get('program_count') or 0) == 0
            and str(row.get('collection_status') or '') == 'collected'
            and '待解析' in str(row.get('missing_reason') or ''))


def _remaining(row, field):
    program_count = int(row.get('program_count') or 0)
    return max(0, program_count - _count(row, field))


def _backfill_candidate(row):
    return (int(row.get('program_count') or 0) > 0
            and (_remaining(row, 'tuition') > 0 or _remaining(row, 'duration') > 0))


def _require_fields(row, fields):
    missing = [field for field in fields if field not in row]
    if missing:
        raise ValueError(
            f"queue row {row.get('unit_id')!r} missing required fields for replay: "
            + ', '.join(missing))


def select_batch(rows, thresholds=None, *, expansion=7, backfill=3, queue_sha256=None):
    """Pick a deterministic first batch from the complete persisted queue only."""
    expand, back = [], []
    for row in rows:
        if _looks_like_expansion(row):
            _require_fields(row, EXPANSION_REQUIRED)
            if not str(row.get('original_urls') or '').strip():
                raise ValueError(f"queue row {row.get('unit_id')!r} has empty original_urls")
            if not entry_trusted(row):
                continue
            profile = original_profile(row.get('original_urls') or '')
            if not profile['url_count']:
                continue
            expand.append({**row, 'role': 'expand_catalog', 'profile': profile,
                           'reason': '已保存2027官方原文但尚无采用方向；本轮先解析并核对目录全集。',
                           'next_step': '建立原件清单与目录全集对账，再按格式选择适配器'})
        elif _backfill_candidate(row):
            if not entry_trusted(row):
                continue
            back.append({**row, 'role': 'backfill_fields',
                         'reason': ('已有采用方向但仍有未核验的费用/学制方向；'
                                    '只补剩余缺口，不重复计算既有覆盖，也不改完整资格标志。'),
                         'next_step': '在既有目录校内选真实章程/简章补齐剩余方向的费用与学制逐项证据'})
    expand.sort(key=lambda row: (-row['profile']['catalog_like_count'],
                                 -row['profile']['pdf_count'],
                                 -row['profile']['url_count'], row['unit_id']))
    back.sort(key=lambda row: (_remaining(row, 'tuition') != int(row.get('program_count') or 0)
                               or _remaining(row, 'duration') != int(row.get('program_count') or 0),
                               'original_pending' in (row.get('gaps') or []),
                               -(_remaining(row, 'tuition') + _remaining(row, 'duration')),
                               row['unit_id']))
    return {'expansion': expand[:expansion], 'backfill': back[:backfill],
            'queue_sha256': queue_sha256,
            'selection_rule_version': SELECTION_RULE_VERSION}


def count_programs(programs):
    """Count structurally valid raw facts; no value implies verification."""
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
        conditions = program.get('conditions')
        conditions_present = isinstance(conditions, list) and bool(conditions)
        full = program.get('eligibility_evidence_complete') is True and conditions_present
        total['program_count'] += 1
        total['years'][program.get('year')] += 1
        row['program_count'] += 1
        if _tuition_amount(tuition):
            total['tuition'] += 1
            row['tuition'] += 1
            period = str(tuition.get('period') or '')
            if period == 'year':
                total['tuition_year'] += 1
                row['tuition_year'] += 1
            elif period in ('full', 'total'):
                total['tuition_full'] += 1
                row['tuition_full'] += 1
            else:
                total['tuition_other'] += 1
            if period in ('full', 'total') or (period == 'year' and _positive_number(duration)):
                total['computed_total_cost'] += 1
        if _positive_number(duration):
            total['duration'] += 1
            row['duration'] += 1
        if conditions_present:
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


def verified_counts(index, programs):
    """Replay each present field's evidence claim; only verified claims count."""
    total = {'program_count': 0, 'verified_tuition': 0, 'verified_duration': 0,
             'verified_full_qualification': 0, 'verified_computed_total_cost': 0,
             'tuition_claim_statuses': Counter(), 'duration_claim_statuses': Counter(),
             'qualification_claim_statuses': Counter()}
    per_unit = defaultdict(lambda: {'program_count': 0, 'verified_tuition': 0,
                                    'verified_duration': 0,
                                    'verified_full_qualification': 0,
                                    'verified_computed_total_cost': 0})
    for program in programs:
        unit_id = program.get('unit_id')
        row = per_unit[unit_id]
        row['program_count'] += 1
        total['program_count'] += 1
        tuition_status = ''
        tuition = program.get('tuition')
        if isinstance(tuition, dict):
            if not _tuition_amount(tuition):
                tuition_status = 'invalid'
            else:
                tuition_status = index._claim(
                    'tuition', '学费与计费口径', tuition, tuition.get('evidence') or [],
                    program, verified=True)['status']
                if tuition_status == 'verified' and str(tuition.get('period') or '') not in (
                        'year', 'full', 'total'):
                    tuition_status = 'invalid'
            total['tuition_claim_statuses'][tuition_status] += 1
        tuition_verified = tuition_status == 'verified'
        if tuition_verified:
            total['verified_tuition'] += 1
            row['verified_tuition'] += 1
        duration_status = ''
        duration = program.get('duration_years')
        if duration is not None:
            if not _positive_number(duration):
                duration_status = 'invalid'
            else:
                duration_status = index._claim(
                    'duration', '学制', duration, program.get('duration_evidence') or [],
                    program, verified=True)['status']
            total['duration_claim_statuses'][duration_status] += 1
        duration_verified = duration_status == 'verified'
        if duration_verified:
            total['verified_duration'] += 1
            row['verified_duration'] += 1
        conditions = program.get('conditions')
        if isinstance(conditions, list) and conditions:
            complete = (program.get('eligibility_evidence_complete') is True
                        and not program.get('requires_review'))
            refs = [item for condition in conditions if isinstance(condition, dict)
                    for item in (condition.get('evidence') or [])]
            status = index._claim('eligibility', '完整报考资格条件', {'complete': complete},
                                  refs, program, verified=complete)['status']
            total['qualification_claim_statuses'][status] += 1
            if status == 'verified':
                total['verified_full_qualification'] += 1
                row['verified_full_qualification'] += 1
        if tuition_verified and isinstance(tuition, dict):
            period = str(tuition.get('period') or '')
            if period in ('full', 'total') or (period == 'year' and duration_verified):
                total['verified_computed_total_cost'] += 1
                row['verified_computed_total_cost'] += 1
    return {'totals': total, 'per_unit': dict(per_unit)}


def stream_counts(path):
    """Stream programs.item so the raw JSON is never fully materialized."""
    with open(path, 'rb') as handle:
        programs = ijson.items(handle, 'programs.item', use_float=True)
        return count_programs(programs)


def _load_json(path):
    with open(path, encoding='utf-8-sig') as handle:
        return json.load(handle)


def effective_counts(data_dir):
    """Apply existing enrichment/safety, then separate presence from verification.

    ``tuition``/``duration`` here mean effective-present only. ``verified_*``
    counts replay the field claims through the real EvidenceIndex and are the
    only counts used for queue gaps. This pass materializes admissions.json in
    memory (the raw stream pass does not); peak memory is not bounded here.
    """
    from enrichment import apply_enrichment
    from evidence import EvidenceIndex, apply_evidence_safety
    from service_verification import SourceVerifier
    data_dir = Path(data_dir)
    registry = _load_json(data_dir / 'units.json')
    admissions = _load_json(data_dir / 'admissions.json')
    verifier = SourceVerifier(data_dir)
    enriched = apply_enrichment(registry, admissions, data_dir)
    safe = apply_evidence_safety(registry, enriched, data_dir, verifier=verifier)
    programs = safe.get('programs', [])
    suspended = sum(1 for program in programs
                    if isinstance(program, dict) and program.get('evidence_conflicts'))
    presence = count_programs(programs)
    verified = verified_counts(EvidenceIndex(registry, safe, data_dir, verifier=verifier),
                               programs)
    per_unit = {}
    for unit_id in set(presence['per_unit']) | set(verified['per_unit']):
        per_unit[unit_id] = {**presence['per_unit'].get(unit_id, {}),
                             **verified['per_unit'].get(unit_id, {})}
    totals = {**presence['totals'], **verified['totals']}
    totals['suspended_conflicts'] = suspended
    return {'totals': totals, 'per_unit': per_unit,
            'enrichment_metadata': safe.get('metadata', {}).get('enrichment', {})}


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


def build_queue(coverage_rows, raw_per_unit, effective_per_unit, annual_rows,
                entry_proofs=None):
    """One replayable row per confirmed annual unit with all three count layers."""
    proofs = entry_proofs or {}
    by_unit = {row['unit_id']: row for row in coverage_rows}
    queue = []
    for base in annual_rows:
        unit_id = base['unit_id']
        coverage = by_unit.get(unit_id, {})
        raw = raw_per_unit.get(unit_id, {})
        effective = effective_per_unit.get(unit_id, {})
        coverage_count = int(coverage.get('program_count') or 0)
        program_count = int(raw.get('program_count') if 'program_count' in raw
                            else coverage_count)
        row = {**base, **{key: coverage.get(key) for key in
                          ('program_count', 'fact_count', 'catalog_status', 'collection_status',
                           'source_count', 'missing_reason', 'original_urls')}}
        row['official_url'] = base.get('official_url') or coverage.get('official_url') or ''
        row['program_count'] = program_count
        row['coverage_program_count'] = coverage_count
        row['raw_tuition'] = int(raw.get('tuition') or 0)
        row['raw_duration'] = int(raw.get('duration') or 0)
        row['raw_full_qualification'] = int(raw.get('full_qualification') or 0)
        row['effective_tuition'] = int(effective.get('tuition') or 0)
        row['effective_duration'] = int(effective.get('duration') or 0)
        row['effective_full_qualification'] = int(effective.get('full_qualification') or 0)
        row['verified_tuition'] = int(effective.get('verified_tuition') or 0)
        row['verified_duration'] = int(effective.get('verified_duration') or 0)
        row['verified_full_qualification'] = int(effective.get('verified_full_qualification') or 0)
        row['verified_computed_total_cost'] = int(
            effective.get('verified_computed_total_cost') or 0)
        row['conditions_nonempty'] = int(raw.get('conditions_nonempty') or 0)
        for field in ('tuition', 'duration', 'full_qualification'):
            row['remaining_' + field] = max(0, program_count - row['verified_' + field])
        proof = dict(proofs.get(unit_id) or {
            'status': 'blocked', 'reason': 'proof_not_computed',
            'unit_id': unit_id, 'year': base.get('year'),
            'official_url': row['official_url'], 'evidence_url': '',
            'evidence_sha256': '', 'document_id': '', 'document_sha256': '',
            'document_path': '', 'file_sha256': '', 'quote_match': ''})
        proof['unit_id'] = str(proof.get('unit_id') or unit_id)
        if proof.get('year') is None:
            proof['year'] = base.get('year')
        proof['official_url'] = str(proof.get('official_url') or row['official_url'])
        row['entry_verification'] = proof
        proof['proof_sha256'] = _entry_fingerprint(row)
        row['gaps'] = classify_gaps(row, {
            'program_count': program_count,
            'verified_tuition': row['verified_tuition'],
            'verified_duration': row['verified_duration'],
            'verified_full_qualification': row['verified_full_qualification']})
        row['count_mismatch'] = program_count != coverage_count
        row['effective_count_mismatch'] = int(effective.get('program_count') or 0) != program_count
        row['entry_trusted'] = entry_trusted(row)
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


def _summary(totals, keys):
    value = {}
    for key in keys:
        item = totals.get(key)
        value[key] = dict(item) if isinstance(item, Counter) else item
    return value


def _summary_row(row):
    return {key: row.get(key) for key in SUMMARY_ROW_KEYS}


def _git(repo_root, *args):
    try:
        output = subprocess.run(['git', *args], cwd=repo_root, capture_output=True,
                                text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ''
    return output.stdout.strip() if output.returncode == 0 else ''


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), 'utf-8', newline='\n')


def _code_hashes(root):
    """Bind the CLI and imported proof logic with checkout-independent LF hashes."""
    return {name: hashlib.sha256(
        (Path(root) / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for name in CODE_FILES}


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
    proofs = verify_entries(registry, annual, data_dir)
    queue = build_queue(coverage_rows, raw['per_unit'], effective['per_unit'], annual_rows,
                        entry_proofs=proofs)
    expansion_units = {row['unit_id'] for row in queue if _looks_like_expansion(row)}
    _write_json(out_dir / 'queue.json', queue)
    queue_sha = sha256_file(out_dir / 'queue.json')
    picks = select_batch(queue, expansion=expansion, backfill=backfill, queue_sha256=queue_sha)
    batch = {'batch_id': 'b001', 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
             'selection_rule': '完整 queue 确定性排序；不手写学校名；重放必须使用同一 queue SHA',
             'selection_rule_version': SELECTION_RULE_VERSION,
             'queue_sha256': queue_sha,
             'expansion': picks['expansion'], 'backfill': picks['backfill'],
             'notice': NOTICE}
    _write_json(out_dir / 'batch.json', batch)
    summary = {'replayable': False,
               'note': ('摘要仅供阅读；不含 original_urls/official_url_evidence 等选择必需字段，'
                        '不能用来复算批次。重放请使用 queue.json 及其 SHA。'),
               'queue_sha256': queue_sha,
               'rows': [_summary_row(row) for row in queue]}
    _write_json(out_dir / 'queue-summary.json', summary)
    artifacts = {name: sha256_file(out_dir / name)
                 for name in ('queue.json', 'queue-summary.json', 'batch.json')}
    inputs = {
        'admissions_sha256': sha256_file(data_dir / 'admissions.json'),
        'units_sha256': sha256_file(data_dir / 'units.json'),
        'annual_registry_sha256': sha256_file(data_dir / 'annual_registry_2027.json'),
        'coverage_csv_sha256': sha256_file(data_dir / 'catalog_coverage_2027.csv'),
    }
    code_hashes = _code_hashes(Path(__file__).resolve().parent)
    baseline = {
        'generated_at': batch['generated_at'],
        'git': {'commit': _git(repo_root, 'rev-parse', 'HEAD'),
                'branch': _git(repo_root, 'rev-parse', '--abbrev-ref', 'HEAD'),
                'tracked_dirty': bool(_git(repo_root, 'status', '--porcelain',
                                           '--untracked-files=no'))},
        'module_sha256': code_hashes['national_data_baseline.py'],
        'code_sha256': code_hashes,
        'code_hash_format': 'UTF-8 source bytes with LF line endings',
        'inputs': inputs,
        'artifacts': artifacts,
        'package': {'catalog_data_zip_sha256': sha256_file(data_dir / 'bundles' / 'catalog-data.zip'),
                    'manifest_sha256': sha256_file(data_dir / 'bundles' / 'manifest.json')},
        'protected_sha256': protected_hashes(Path(repo_root)),
        'tools': tool_versions(raw_pdf, bbox_pdf),
        'annual': {key: annual.get('metadata', {}).get(key) for key in
                   ('year', 'official_total', 'indexed_records', 'confirmed_units', 'not_listed_index_records')},
        'annual_rows': len(annual_rows),
        'coverage_rows': len(coverage_rows),
        'raw_present': _summary(raw['totals'], PRESENCE_KEYS),
        'effective_present': _summary(effective['totals'], PRESENCE_KEYS),
        'verified': _summary(effective['totals'], VERIFIED_KEYS),
        'effective_extra': {'enrichment': effective.get('enrichment_metadata', {}),
                            'suspended_conflicts': effective['totals'].get('suspended_conflicts', 0)},
        'catalog_status': dict(Counter(row.get('catalog_status') for row in coverage_rows)),
        'collection_status': dict(Counter(row.get('collection_status') for row in coverage_rows)),
        'queue': {'count': len(queue), 'sha256': queue_sha, 'replayable': True,
                  'summary_path': 'queue-summary.json', 'summary_replayable': False},
        'queue_gap_counts': dict(Counter(gap for row in queue for gap in row['gaps'])),
        'expansion_pool': {
            'candidates': sum(1 for row in queue if _looks_like_expansion(row)),
            'trusted': sum(1 for row in queue if _looks_like_expansion(row)
                           and row.get('entry_trusted')),
            'untrusted': sum(1 for row in queue if _looks_like_expansion(row)
                             and not row.get('entry_trusted')),
        },
        'entry_proof': {
            'status': dict(Counter(proof.get('status') for proof in proofs.values())),
            'reasons': dict(Counter(proof.get('reason') for proof in proofs.values())),
            'expansion_reasons': dict(Counter(
                proof.get('reason') for unit_id, proof in proofs.items()
                if unit_id in expansion_units)),
        },
        'queue_count': len(queue),
        'queue_count_mismatch': sum(1 for row in queue if row['count_mismatch']),
        'effective_count_mismatch': sum(1 for row in queue if row['effective_count_mismatch']),
        'notice': NOTICE,
    }
    _write_json(out_dir / 'baseline.json', baseline)
    print(json.dumps({'out_dir': str(out_dir), 'raw_present': baseline['raw_present'],
                      'effective_present': baseline['effective_present'],
                      'verified': baseline['verified'],
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
