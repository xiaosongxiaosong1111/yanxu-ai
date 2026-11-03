"""Refresh catalog citations against current parsers without changing admissions facts."""
from collections import defaultdict
from copy import deepcopy
import re

from service_verification import SourceVerifier, canonical, citation_signature


ALIASES = {'101': {'统考思想政治理论','政治','思想政治理论(全国统一命题科目)'}, '201': {'统考英语一'}, '301': {'统考数学一'},
           '408': {'计算机学科专业基础综合'}, '204': {'英语(二)(全国统一命题科目)'}}
PROOF_FIELDS = ('evidence', 'exam_evidence', 'catalog_document_id', 'catalog_parser', 'source_locator',
                'exam_matching_supported', 'subject_identity_issues')
REVIEW_NOTE = '解析器升级后的原文重放存在未解决记录，不能视为完整已核验。'


def _alias_scopes(program):
    value, changed = deepcopy(program), False
    if not isinstance(value.get('subjects'), list) or any(not isinstance(row, dict) for row in value['subjects']):
        return value, False
    for subject in value.get('subjects', []):
        aliases = ALIASES.get(subject.get('code'), set())
        if (subject.get('scope') == value.get('unit_id')
                and canonical(subject.get('name')) in {canonical(name) for name in aliases}):
            subject['scope'] = 'national'
            changed = True
    return value, changed


def _replay_scope(unit, year, documents, verifier, report):
    selected = [row for row in documents.values() if row.get('unit_id') == unit['id']
                and row.get('year') == year and row.get('kind') == 'catalog' and row.get('catalog_active', True)]
    tree = verifier._tree_scope(unit, year, selected)
    if tree is None: return {}, '原始院系树缺失、重复或无法独立核验'
    rows = defaultdict(list)
    for document in selected:
        try:
            parsed = verifier._source_programs(document, unit, year, tree[0], documents)
        except (ValueError, OSError, ImportError, TypeError) as error:
            report['source_failures'].append({'document_id': document['id'], 'reason': type(error).__name__})
            continue
        if parsed is None: continue
        report['documents_replayed'] += 1
        for row in parsed.get('programs', []):
            rows[row.get('id')].append((row, document['id']))
    return rows, '当前原文重放未找到同一记录身份'


def _choose_row(program, choices, verifier):
    if not choices: return None, '当前原文重放未找到同一记录身份'
    signatures = {verifier._signature(row) for row, _ in choices}
    if None in signatures or len(signatures) != 1:
        return None, '同一记录身份在原始来源中存在核心内容冲突'
    if verifier._signature(program) not in signatures:
        return None, '原文核心字段与保存记录不同，不能通过刷新引文覆盖'
    preferred = {program.get('catalog_document_id')}
    refs = program.get('evidence')
    refs = [refs] if isinstance(refs, dict) else refs
    preferred.update(ref.get('document_id') for ref in refs or [] if isinstance(ref, dict))
    return next((row for row, identity in choices if identity in preferred), choices[0][0]), None


def _department_label(program, choices, verifier):
    value=program.get('department')
    if not isinstance(value,str): return program,False
    clean=re.split(r'(?:咨询电话|联系电话|联系人)\s*[:：]',value,maxsplit=1)[0].strip()
    if not clean or clean==value: return program,False
    candidate={**program,'department':clean}
    signature=verifier._signature(candidate)
    if signature is not None and any(signature==verifier._signature(row) for row,_ in choices):
        return candidate,True
    return program,False


def _refresh_proofs(original, parsed):
    # Tuition, duration, qualifications and historical fields retain their independent evidence.
    result = {key: deepcopy(value) for key, value in original.items() if key not in PROOF_FIELDS}
    for field in PROOF_FIELDS:
        if field in parsed: result[field] = deepcopy(parsed[field])
    result['subjects'] = deepcopy(parsed['subjects'])
    return result


def _mark_review(program, reason):
    return {**program, 'stale': True, 'requires_review': True, 'catalog_replay_status': 'needs_review',
            'catalog_replay_note': reason,
            'catalog_replay_stale': program.get('catalog_replay_stale') is True or program.get('stale') is not True}


def _restore_migration_stale(candidate, original, documents):
    if original.get('catalog_replay_status') != 'needs_review' or not original.get('catalog_replay_stale'):
        return candidate
    refs = candidate.get('evidence')
    refs = [refs] if isinstance(refs, dict) else refs
    source_stale = any(documents.get(ref.get('document_id'), {}).get('stale') for ref in refs or [] if isinstance(ref, dict))
    if not (original.get('catalog_conflict') or original.get('stale_reason') or original.get('stale_reasons') or source_stale):
        candidate = {key: value for key, value in candidate.items() if key not in {'stale', 'catalog_replay_stale'}}
    return candidate


def _scope_statuses(meta, failed_scopes, units, programs, documents, verifier):
    statuses = []
    for row in meta.get('unit_catalog_status', []):
        scope = row.get('unit_id'), row.get('year')
        if scope in failed_scopes:
            row = {**row, 'status': 'needs_review', 'verified_records': 0,
                'catalog_replay_previous_status': row.get('catalog_replay_previous_status', row.get('status')),
                'catalog_replay_previous_verified_records': row.get('catalog_replay_previous_verified_records', row.get('verified_records', 0)),
                'notes': list(dict.fromkeys([*row.get('notes', []), REVIEW_NOTE]))}
        elif row.get('catalog_replay_previous_status'):
            previous = row['catalog_replay_previous_status']
            relevant = [program for program in programs if (program.get('unit_id'), program.get('year')) == scope]
            unit = units.get(scope[0])
            if previous != 'verified' or unit and verifier.unit_reconciles(unit, scope[1], relevant, documents):
                row = {**row, 'status': previous, 'verified_records': row.get('catalog_replay_previous_verified_records', 0),
                    'notes': [note for note in row.get('notes', []) if note != REVIEW_NOTE]}
                row = {key: value for key, value in row.items() if not key.startswith('catalog_replay_previous_')}
        statuses.append(row)
    return statuses


def refresh_saved_catalog(registry, admissions, root):
    """Return a copy and an audit report; do not write, add, delete, or resolve conflicts."""
    if (not isinstance(registry, dict) or not isinstance(admissions, dict)
            or not isinstance(admissions.get('programs'), list) or not isinstance(admissions.get('documents'), list)):
        raise ValueError('需要已保存的招生单位、专业记录和原始文档列表。')
    result = deepcopy(admissions)
    units = {row['id']: row for row in registry.get('units', [])}
    documents = {row['id']: row for row in result['documents']}
    verifier, scopes = SourceVerifier(root), {}
    report = {'program_count': len(result['programs']), 'refreshed_count': 0, 'citation_changed_count': 0,
        'alias_changed_count': 0, 'label_changed_count':0, 'needs_review_count': 0, 'documents_replayed': 0, 'changes': [], 'needs_review': [], 'source_failures': []}
    updated, failed_scopes = [], set()
    for original in result['programs']:
        scope = original.get('unit_id'), original.get('year')
        unit = units.get(scope[0])
        program, alias_changed = _alias_scopes(original)
        label_changed=False
        if not unit or type(scope[1]) is not int:
            parsed, reason = None, '招生单位或年度无法绑定已保存原文'
        else:
            if scope not in scopes: scopes[scope] = _replay_scope(unit, scope[1], documents, verifier, report)
            rows, missing = scopes[scope]
            choices=rows.get(program.get('id'), [])
            program,label_changed=_department_label(program,choices,verifier)
            parsed, reason = _choose_row(program, choices, verifier)
            if parsed is None and not rows: reason = missing
        candidate = _refresh_proofs(program, parsed) if parsed is not None else None
        if candidate is not None and not verifier.program(candidate, documents, unit):
            candidate, reason = None, '当前引文与完整初试组合仍未通过独立原文核验'
        if candidate is None:
            updated.append(_mark_review(original, reason))
            report['needs_review_count'] += 1
            report['needs_review'].append({'program_id': original.get('id'), 'reason': reason})
            failed_scopes.add(scope)
            continue
        proof_changed = any(citation_signature(original.get(field)) != citation_signature(candidate.get(field))
                            for field in ('evidence', 'exam_evidence'))
        candidate = {**_restore_migration_stale(candidate, original, documents), 'catalog_replay_status': 'verified'}
        candidate.pop('catalog_replay_note', None)
        updated.append(candidate)
        report['refreshed_count'] += 1
        report['citation_changed_count'] += int(proof_changed)
        report['alias_changed_count'] += int(alias_changed)
        report['label_changed_count'] += int(label_changed)
        if proof_changed or alias_changed or label_changed:
            report['changes'].append({'program_id': original['id'], 'citations_refreshed': proof_changed,
                                      'national_alias_scope_refreshed': alias_changed,'department_label_normalized':label_changed})
    result['programs'] = updated
    meta = result.get('metadata', {})
    statuses = _scope_statuses(meta, failed_scopes, units, updated, documents, verifier)
    result['metadata'] = {**meta, **({'catalog_complete': False} if failed_scopes else {}), 'unit_catalog_status': statuses}
    return result, report
