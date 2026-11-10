"""Incremental catalog imports from official originals; discovery is not coverage."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from public_sources import SourceError, fetch_official_document, official_source_url, rendered_source_html
from service_verification import SourceVerifier, canonical, plain_text, PUBLIC_CATALOG_ADAPTERS, CATALOG_ADAPTERS
from .catalogs import catalog_status, merge_admissions
from .documents import PROJECT_ROOT, DocumentError, decode_text, extract_pdf_text, save_pdf_text


PARSERS = frozenset(CATALOG_ADAPTERS)
MAX_BYTES = 32 * 1024 * 1024


def _context_documents(document):
    value=document.get('context_documents',{})
    rows=list(value.values()) if isinstance(value,dict) else value
    if (not isinstance(rows,list) or len(rows)>4096 or any(not isinstance(row,dict)
            or not isinstance(row.get('id'),str) or not row['id'] for row in rows)):
        raise DocumentError('公开目录上下文来源集合格式不正确')
    result={row['id']:row for row in rows}
    if len(result)!=len(rows):raise DocumentError('公开目录上下文来源身份重复')
    if isinstance(value,dict) and set(value)!=set(result):raise DocumentError('公开目录上下文来源身份与索引不一致')
    return result


def parse_document(document, unit, year, root=PROJECT_ROOT, raw=None):
    path = Path(root) / document['local_path']
    raw = path.read_bytes() if raw is None else raw
    kind = document.get('catalog_parser')
    if kind=='pdf_table_hit_v1':
        from .catalog_hit_public import parse_hit_public,extract_hit_public_layout
        return parse_hit_public(extract_hit_public_layout(path),unit,year,document,project_root=root)
    if kind in {'html_structured_v1','office_structured_v1'}:
        from .catalog_structured_tables import parse_html_catalog_structured,parse_office_catalog_structured
        parser=parse_html_catalog_structured if kind=='html_structured_v1' else parse_office_catalog_structured
        return parser(raw,unit,year,document,project_root=root)
    if kind == 'office_national_v1':
        from .catalog_office_national import parse_catalog_office_national
        return parse_catalog_office_national(raw,unit,year,document,project_root=root)
    if kind in {'html_national_v1','html_south_v1'}:
        from .catalog_html_national import parse_catalog_html_national
        from .catalog_html_south import parse_catalog_html_south
        parser = parse_catalog_html_national if kind=='html_national_v1' else parse_catalog_html_south
        return parser(rendered_source_html(decode_text(raw,document.get('content_type','')),document['url']),unit,year,document,project_root=root)
    if kind == 'pdf_table_national_v3':
        from .catalog_pdf_national_v3 import parse_catalog_pdf_national_v3,extract_pdf_national_layout_v3
        return parse_catalog_pdf_national_v3(extract_pdf_national_layout_v3(path),unit,year,document,project_root=root)
    if kind == 'pdf_table_national_v2':
        from .catalog_pdf_national_v2 import parse_catalog_pdf_national_v2,extract_pdf_national_layout_v2
        return parse_catalog_pdf_national_v2(extract_pdf_national_layout_v2(path),unit,year,document,project_root=root)
    if kind == 'pdf_table_national_v1':
        from .catalog_pdf_national import parse_catalog_pdf_national,extract_pdf_national_layout
        return parse_catalog_pdf_national(extract_pdf_national_layout(path),unit,year,document,project_root=root)
    if kind in PUBLIC_CATALOG_ADAPTERS:
        parsed=SourceVerifier(root)._source_programs(document,unit,year,{},_context_documents(document))
        if parsed is None: raise DocumentError('公开目录表单的原始查询链尚未通过核验')
        return parsed
    if kind == 'chsi_detail_v1':
        parsed = SourceVerifier(root)._source_programs(document, unit, year, {})
        if parsed is None: raise DocumentError('研招网详情缺少真实公开首方向及原文上下文证明')
        return parsed
    if kind == 'html_table_v1':
        from .catalog_html import parse_catalog_html
        from .catalog_enrichment import apply_inline_policies
        html = rendered_source_html(decode_text(raw, document.get('content_type', '')), document['url'])
        parsed = parse_catalog_html(html, unit, year, document)
        return {**parsed, 'programs': apply_inline_policies(parsed['programs'], html, unit, year, document)}
    if kind == 'pdf_row_v1':
        from .catalog_pdf import parse_catalog_pdf
        return parse_catalog_pdf(extract_pdf_text(path), unit, year, document, format='row-v1')
    if kind == 'pdf_table_v1':
        from .catalog_pdf_tables import extract_pdf_layout, parse_catalog_pdf_tables
        return parse_catalog_pdf_tables(extract_pdf_layout(path), unit, year, document, project_root=root)
    if kind == 'pdf_table_v2':
        from .catalog_pdf_variants import extract_pdf_variant_layout, parse_catalog_pdf_variants
        return parse_catalog_pdf_variants(extract_pdf_variant_layout(path), unit, year, document, project_root=root)
    if kind == 'office_table_v1':
        from .catalog_office import parse_catalog_office
        return parse_catalog_office(raw, unit, year, document)
    if kind in {'catalog_index_v1', 'zsml_tree'}:
        return {'programs': [], 'expected_records': 0, 'issues': [], 'retest_rules': []}
    if kind == 'zsml_table':
        from .catalog_zsml import parse_zsml_html
        return parse_zsml_html(decode_text(raw, document.get('content_type', '')), unit,
                               document.get('department', ''), year, document)
    raise DocumentError('该目录格式尚未提供可重放的适配器')


def _headings(parser, text):
    if parser in {'pdf_row_v1', 'pdf_table_v1', 'pdf_table_v2', 'pdf_table_national_v1','pdf_table_national_v2','pdf_table_national_v3'}:
        title = []
        for line in text.splitlines()[:8]:
            if re.search(r'院系所|考试科目|学习方式|全国统考|拟招生人数', line):
                break
            if line.strip():
                title.append(line.strip())
        return [''.join(title)]
    headings = re.findall(r'<h[1-6]\b[^>]*>(.*?)</h[1-6]>', text, re.I | re.S)
    headings += re.findall(r'<title\b[^>]*>(.*?)</title>', text, re.I | re.S)
    # CHSI articles use a styled div rather than a heading on some schools.
    headings += re.findall(r'<[^>]+class=["\'][^"\']*(?:ch-title|content-title|news-title|art-title|sch-sspe-swtit)[^"\']*["\'][^>]*>(.*?)</[^>]+>', text, re.I | re.S)
    return [plain_text(value) for value in headings]


def unit_scope_supported(unit, year, originals):
    """A general school catalogue title can substantiate scope; limited titles cannot."""
    name = canonical(unit.get('name', ''))
    for parser, text in originals:
        for heading in _headings(parser, text):
            normalized = canonical(heading)
            if not name or name not in normalized or str(year) not in normalized:
                continue
            rest = normalized.replace(name, '')
            if ('硕士' not in rest or not re.search(r'招生.*(?:专业)?目录|(?:招生)?专业目录', rest)
                    or re.search(r'学院|学部|部分|全日制|非全日制|学术学位|专业学位', rest)):
                continue
            prefix=re.sub(r'20\d{2}年?|全国统一考试|全国统考|全国|统一考试|统考|招收|攻读|招生|附件\d*|年','',rest.split('硕士',1)[0])
            if re.sub(r'[\W\d_]+','',prefix):continue
            return True
    return False


def _source_bytes(source, unit, *, offline, fetcher, project_root=PROJECT_ROOT, year=2027):
    url = source.get('url')
    typed_detail = (source.get('parser') or source.get('catalog_parser')) == 'chsi_detail_v1'
    if typed_detail:
        from .chsi_public_proof import verify_public_detail
        candidate = {**deepcopy(source), 'local_path': source.get('source_path') or source.get('local_path'),
                     'unit_id': str(unit['id']), 'year': year, 'status': 'parsed'}
        if not verify_public_detail(candidate, unit, year, project_root=project_root)['verified']:
            raise DocumentError('研招网详情缺少真实GET原文、学校上下文或公开首方向证明')
    elif not official_source_url(url, unit):
        raise DocumentError('目录来源不属于该招生单位官方域名或正确的研招网学校标识')
    local = source.get('source_path') or source.get('local_path')
    if local:
        path = Path(local)
        raw = path.read_bytes()
        if not isinstance(source.get('sha256'), str) or hashlib.sha256(raw).hexdigest() != source['sha256']:
            raise DocumentError('提供的原文与摘要不一致')
        return raw, source.get('content_type', 'application/pdf' if raw.startswith(b'%PDF-') else 'text/html')
    if offline:
        raise DocumentError('离线导入缺少带摘要的原始文件')
    fetched = fetcher(url, unit, max_bytes=min(MAX_BYTES, 8 * 1024 * 1024))
    return fetched['raw'], fetched['content_type']


def _snapshot(source, unit, year, directory, root, *, offline, fetcher):
    parser = source.get('parser') or source.get('catalog_parser')
    if parser not in PARSERS:
        raise DocumentError('未知目录格式，必须先提供解析与重放核验')
    raw, content_type = _source_bytes(source, unit, offline=offline, fetcher=fetcher, project_root=root, year=year)
    if not raw or len(raw) > MAX_BYTES:
        raise DocumentError('目录原文为空或超过32MB')
    digest = hashlib.sha256(raw).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    extension = '.pdf' if raw.startswith(b'%PDF-') else '.json' if parser == 'zsml_tree' else '.zip' if parser in {'office_table_v1','office_national_v1','office_structured_v1'} else '.html'
    key = hashlib.sha256(source['url'].encode()).hexdigest()[:16]
    target = directory / f'{key}-{digest[:16]}{extension}'
    target.write_bytes(raw)
    document = {'id': f'catalog-{unit["id"]}-{year}-{key}-{digest[:12]}',
                'unit_id': str(unit['id']), 'year': year, 'kind': 'catalog',
                'url': source['url'], 'title': source.get('title', f'{unit["name"]}{year}年硕士招生目录'),
                'published_at': source.get('published_at'),
                'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'status': 'parsed', 'sha256': digest, 'content_type': content_type,
                'local_path': target.resolve().relative_to(Path(root).resolve()).as_posix(),
                'catalog_parser': parser, 'notes': []}
    if parser == 'pdf_table_v2': document = {**document, 'id': document['id'] + '-v2'}
    if source.get('department'):
        document['department'] = source['department']
    if source.get('catalog_previous_id'):
        document['catalog_previous_id'] = source['catalog_previous_id']
    if isinstance(source.get('publication_page'), dict):
        document['publication_page'] = deepcopy(source['publication_page'])
    for field in ('learning_mode_document','public_form_context','public_form_profile','public_aspx_page',
                  'context_documents','request_method','request_body','request_sha256','request_document'):
        if field in source: document = {**document,field:deepcopy(source[field])}
    if parser == 'chsi_detail_v1':
        for field in ('method', 'request_body', 'requested_url', 'context_document', 'public_gate_proof'):
            if field in source: document[field] = deepcopy(source[field])
    if parser in {'office_table_v1','office_national_v1','office_structured_v1'}:
        from .catalog_office import extract_office_html
        text = extract_office_html(raw)
    else:
        text = (extract_pdf_text(target) if raw.startswith(b'%PDF-')
                else rendered_source_html(decode_text(raw, content_type), document['url']))
    if raw.startswith(b'%PDF-'):
        document = save_pdf_text(document, text, root)
    return document, raw, text


def _failure(unit, year, error):
    code = getattr(error, 'code', '')
    restricted = code in {'restricted', 'redirect_error'}
    return {'programs': [], 'documents': [], 'retest_rules': [], 'issues': [str(error)],
            'expected_records': 0, 'status': {'unit_id': str(unit['id']), 'unit_name': unit['name'],
                'year': year, 'status': 'restricted' if restricted else 'failed',
                'expected_records': None, 'parsed_records': 0, 'verified_records': 0,
                'notes': [str(error)]}}


def collect_school(spec, unit, snapshot_dir, *, project_root=PROJECT_ROOT, offline=False,
                   fetcher=fetch_official_document, parser=parse_document):
    year = spec.get('year', 2027)
    if type(year) is not int or year != 2027:
        return _failure(unit, year, DocumentError('当前适配仅核验2027参考目录，不推定其他年度规则'))
    sources = spec.get('sources')
    if not isinstance(sources, list) or not sources:
        return _failure(unit, year, DocumentError('学校没有提供完整原始目录集合'))
    result = {'programs': [], 'documents': [], 'retest_rules': [], 'expected_records': 0, 'issues': []}
    originals, seen = [], set()
    directory = Path(snapshot_dir) / f'{unit["id"]}-{year}'
    for source in sources:
        try:
            if not isinstance(source, dict):
                raise DocumentError('目录来源描述格式不正确')
            document, raw, text = _snapshot(source, unit, year, directory, project_root, offline=offline, fetcher=fetcher)
            if source.get('continue_from_previous'):
                if not result['documents']: raise DocumentError('分页目录缺少前页来源')
                document = {**document, 'catalog_previous_id': result['documents'][-1]['id']}
            identity = (document['url'], document['sha256'])
            if identity in seen:
                continue
            seen.add(identity)
            if document.get('catalog_previous_id') and parser is parse_document:
                verifier = SourceVerifier(project_root)
                docs = {row['id']: row for row in [*result['documents'], document]}
                parsed = verifier._source_programs(document, unit, year, {}, docs)
                if parsed is None: raise DocumentError('分页目录前序来源链缺失或无法独立重放')
            else:
                parsed = parser(document, unit, year, project_root, raw)
            originals.append((document['catalog_parser'], text))
            result['documents'].append(document)
            contexts = _context_documents(document) if document['catalog_parser'] in PUBLIC_CATALOG_ADAPTERS else {}
            related_documents = [*parsed.get('documents', []), *contexts.values()] if isinstance(contexts,dict) else parsed.get('documents', [])
            for related in related_documents:
                if related.get('kind') in {'publication', 'catalog_context'} and not any(row['id'] == related['id'] for row in result['documents']):
                    result['documents'].append(related)
            for field in ('programs', 'retest_rules', 'issues'):
                result[field].extend(parsed.get(field, []))
            result['expected_records'] += parsed.get('expected_records', 0)
        except (DocumentError, SourceError, OSError, ValueError, TypeError) as error:
            result['issues'].append(str(error))
    if not result['documents']:
        return _failure(unit, year, DocumentError('；'.join(result['issues'])))
    status = catalog_status(result, unit, year)
    complete_scope = unit_scope_supported(unit, year, originals)
    catalog_documents = [row for row in result['documents'] if row.get('kind') == 'catalog']
    typed_public = catalog_documents and all(row.get('catalog_parser') in PUBLIC_CATALOG_ADAPTERS|{'pdf_table_hit_v1'} for row in catalog_documents)
    if typed_public:
        verifier=SourceVerifier(project_root)
        complete_scope=verifier.unit_reconciles(unit,year,result['programs'],{row['id']:row for row in result['documents']})
        result['source_set_reconciliation']={'complete':complete_scope,'method':'replayed_public_query_options'}
    elif len(catalog_documents) > 1:
        from .catalog_source_set import source_set_reconciliation
        verifier = SourceVerifier(project_root)
        proof = source_set_reconciliation(unit, year, catalog_documents, verifier)
        result['source_set_reconciliation'] = proof
        complete_scope = proof['complete'] or (any(row.get('catalog_parser') == 'zsml_tree' for row in result['documents'])
            and verifier.unit_reconciles(unit, year, result['programs'], {row['id']: row for row in result['documents']}))
    if not complete_scope:
        result['issues'].append('原文未能证明所导入集合覆盖该校全部统考目录，保留部分数据')
    if status['status'] != 'verified' or not complete_scope:
        status = {**status, 'status': 'partial', 'verified_records': 0,
                  'notes': [*status['notes'], *result['issues']]}
    return {**result, 'status': status}


def collect_manifest(registry, manifest, snapshot_dir, *, project_root=PROJECT_ROOT, offline=False,
                     fetcher=fetch_official_document, parser=parse_document):
    schools = manifest.get('schools', manifest.get('units', []))
    if not isinstance(schools, list):
        raise DocumentError('批处理清单必须提供schools数组')
    units = {str(unit['id']): unit for unit in registry.get('units', [])}
    collected = []
    for spec in schools:
        if not isinstance(spec, dict) or str(spec.get('unit_id')) not in units:
            raise DocumentError('批处理学校必须对应现有研招网单位身份')
        collected.append(collect_school(spec, units[str(spec['unit_id'])], snapshot_dir,
            project_root=project_root, offline=offline, fetcher=fetcher, parser=parser))
    return collected


def save_progress(output, collected, previous=None, *, registry=None, project_root=PROJECT_ROOT):
    merged = (ingest_verified_batch(registry, collected, previous, project_root=project_root)
              if registry is not None else merge_admissions(collected, previous=previous))
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    temporary.replace(path)
    return merged


def _semantic_decisions(documents, unit, year, verifier, approved_audits=None):
    from .catalog_semantic_audit import explain_semantic_audit
    decisions = {}
    for identity, document in documents.items():
        if (not str(document.get('catalog_parser', '')).lower().startswith('pdf_table')
                or document.get('unit_id') != unit['id'] or document.get('year') != year): continue
        parsed = verifier._source_programs(document, unit, year, {}, documents)
        decisions = {**decisions, identity: explain_semantic_audit(document,
            (parsed or {}).get('programs', []), verifier.root, approved_audits)}
    return decisions


def _verified_row(row, documents, unit, verifier, semantic_decisions=None):
    if not verifier.program(row, documents, unit):
        return None
    refs = row.get('evidence')
    refs = [refs] if isinstance(refs, dict) else refs
    sources = {ref.get('document_id') for ref in refs or [] if isinstance(ref, dict)}
    if row.get('catalog_document_id') in sources:
        sources = [row['catalog_document_id']]
    tree = verifier._tree_scope(unit, row.get('year'), [value for value in documents.values()
        if value.get('unit_id') == unit['id'] and value.get('year') == row.get('year')
        and value.get('kind') == 'catalog' and value.get('catalog_active', True)])
    if tree is None: return None
    for identity in sources:
        document = documents.get(identity)
        if not document: continue
        if str(document.get('catalog_parser', '')).lower().startswith('pdf_table'):
            decisions = (semantic_decisions if semantic_decisions is not None
                         else _semantic_decisions({identity: document}, unit, row.get('year'), verifier))
            if not decisions.get(identity, {}).get('approved'): continue
        parsed = verifier._source_programs(document, unit, row.get('year'), tree[0], documents)
        matches = [value for value in (parsed or {}).get('programs', [])
                   if value.get('id') == row.get('id') and verifier._signature(value) == verifier._signature(row)]
        if len(matches) != 1: continue
        return deepcopy(matches[0])
    return None


def _refresh_record(previous, current):
    retained = {key: previous[key] for key in ('tuition', 'tuition_evidence', 'duration_years', 'duration_evidence')
                if previous.get(key) is not None and current.get(key) is None}
    conflict = {'catalog_conflict': True, 'stale': True, 'requires_review': True} if previous.get('catalog_conflict') else {}
    return {**current, **retained, **conflict}


def _ingest_rows(previous, incoming, documents, unit, year, verifier, approved_audits=None):
    rows = {row['id']: row for row in previous}
    if len(rows) != len(previous): raise DocumentError('旧目录包含重复记录身份，拒绝静默覆盖')
    counters = {'accepted_records': 0, 'added_records': 0, 'updated_records': 0, 'rejected_records': 0,
                'conflicting_records': 0, 'retained_records': len(previous), 'rejections': []}
    conflicts = []
    decisions = _semantic_decisions(documents, unit, year, verifier, approved_audits)
    for candidate in incoming:
        verified = (_verified_row(candidate, documents, unit, verifier, decisions)
                    if candidate.get('unit_id') == unit['id'] and candidate.get('year') == year else None)
        if verified is None:
            counters['rejected_records'] += 1
            reason = '无法从该校该年度原文独立重放本行及完整初试组合'
            decision = decisions.get(candidate.get('catalog_document_id'))
            if decision is None:
                refs = candidate.get('evidence'); refs = [refs] if isinstance(refs, dict) else refs
                decision = next((decisions[ref['document_id']] for ref in refs or []
                    if isinstance(ref, dict) and ref.get('document_id') in decisions), None)
            if decision and not decision['approved']: reason = 'PDF表格缺少独立语义审校批准：' + decision['reason']
            counters['rejections'].append({'program_id': candidate.get('id'), 'reason': reason})
            continue
        identity = verified['id']
        old = rows.get(identity)
        if old and verifier._signature(old) != verifier._signature(verified):
            counters['conflicting_records'] += 1
            conflicts.append({'unit_id': unit['id'], 'year': year, 'program_id': identity,
                'status': 'needs_review', 'previous': deepcopy(old), 'incoming': verified})
            rows[identity] = {**old, 'catalog_conflict': True, 'stale': True, 'requires_review': True}
            continue
        rows[identity] = _refresh_record(old, verified) if old else verified
        counters['accepted_records'] += 1
        counters['updated_records' if old else 'added_records'] += 1
    return list(rows.values()), counters, conflicts


def _ingest_documents(previous, incoming, records, conflicts, unit_id, year, empty_pages=()):
    documents = {row['id']: row for row in previous}
    referenced = set()
    for row in records:
        refs = row.get('evidence')
        refs = [refs] if isinstance(refs, dict) else refs
        referenced.update(ref.get('document_id') for ref in refs or [] if isinstance(ref, dict))
    pending = {ref.get('document_id') for conflict in conflicts for ref in
               (conflict['incoming'].get('evidence') if isinstance(conflict['incoming'].get('evidence'), list)
                else [conflict['incoming'].get('evidence')]) if isinstance(ref, dict)}
    for identity, row in list(documents.items()):
        if (row.get('kind') == 'catalog' and row.get('unit_id') == unit_id and row.get('year') == year
                and row.get('catalog_parser') not in {'zsml_tree', 'catalog_index_v1'} and identity not in referenced):
            documents[identity] = {**row, 'catalog_active': False, 'version_state': 'superseded'}
        structural = row.get('catalog_parser') in {'zsml_tree', 'catalog_index_v1'} or str(row.get('local_path', '')).endswith('.json')
        if (row.get('unit_id') == unit_id and row.get('year') == year and structural
                and any(value.get('catalog_parser') in {'zsml_tree', 'catalog_index_v1'}
                        and value.get('url') == row.get('url') and value['id'] != identity for value in incoming)):
            documents[identity] = {**row, 'catalog_active': False, 'version_state': 'superseded'}
    for row in incoming:
        active = (row.get('kind') != 'catalog' or row['id'] in referenced or row['id'] in empty_pages
                  or row.get('catalog_parser') in {'zsml_tree', 'catalog_index_v1'})
        documents[row['id']] = {**row, 'catalog_active': active,
            'version_state': 'current' if active else 'conflicting' if row['id'] in pending else 'pending_review'}
    return list(documents.values())


def _ingest_school(previous, entries, unit, year, root, approved_audits=None):
    incoming_docs = [{**row, 'catalog_active': True} for entry in entries for row in entry.get('documents', [])]
    old_documents = {row['id']: row for row in previous.get('documents', [])}
    if any(row['id'] in old_documents and any(old_documents[row['id']].get(key) != row.get(key)
            for key in ('sha256', 'url', 'unit_id', 'year')) for row in incoming_docs):
        raise DocumentError('来源身份已绑定其他原文版本，拒绝覆盖旧证据')
    docmap = {row['id']: row for row in [*previous.get('documents', []), *incoming_docs]}
    tree_ids = {row['id'] for row in incoming_docs if row.get('catalog_parser') == 'zsml_tree'}
    if tree_ids:
        docmap = {identity: {**row, 'catalog_active': False}
                  if identity not in tree_ids and row.get('unit_id') == unit['id'] and row.get('year') == year
                  and (row.get('catalog_parser') == 'zsml_tree' or str(row.get('local_path', '')).endswith('.json'))
                  else row for identity, row in docmap.items()}
    relevant = [row for row in previous.get('programs', []) if row.get('unit_id') == unit['id'] and row.get('year') == year]
    candidates = [row for entry in entries for row in entry.get('programs', [])]
    verifier = SourceVerifier(root)
    records, counters, conflicts = _ingest_rows(relevant, candidates, docmap, unit, year, verifier, approved_audits)
    incoming_ids = {row.get('id') for row in candidates}
    current = [row for row in records if row['id'] in incoming_ids]
    incoming_complete = (all(entry['status'].get('status') == 'verified' for entry in entries)
        and not counters['rejected_records'] and not counters['conflicting_records'] and bool(current)
        and verifier.unit_reconciles(unit, year, current, {row['id']: row for row in incoming_docs}))
    superseded = []
    if incoming_complete:
        superseded = [{**row, 'stale': True, 'catalog_removed': True, 'version_state': 'superseded',
            'removal_reason': '新发布目录全集已独立核验，当前原文未再列出该方向'}
            for row in records if row['id'] not in incoming_ids]
        records = current
    counters['superseded_records'] = len(superseded)
    empty_pages = set()
    tree = verifier._tree_scope(unit, year, [row for row in docmap.values() if row.get('unit_id') == unit['id']
        and row.get('year') == year and row.get('kind') == 'catalog' and row.get('catalog_active', True)])
    for document in incoming_docs:
        if incoming_complete and document.get('catalog_parser') in PUBLIC_CATALOG_ADAPTERS:
            parsed = verifier._source_programs(document, unit, year, {}, docmap)
            if parsed and not parsed['issues'] and not parsed['programs'] and parsed['expected_records']==0:
                empty_pages.add(document['id'])
            continue
        if document.get('catalog_parser') not in {'html_table_v1', 'zsml_table'} or tree is None: continue
        parsed = verifier._source_programs(document, unit, year, tree[0], docmap)
        if (parsed and not parsed['issues'] and not parsed['programs'] and parsed['expected_records'] == 0
                and parsed.get('excluded_recommendation_only', 0) > 0): empty_pages.add(document['id'])
    documents = _ingest_documents(previous.get('documents', []), incoming_docs, records, conflicts, unit['id'], year, empty_pages)
    scope_docs = {row['id']: row for row in documents}
    complete = (all(entry['status'].get('status') == 'verified' for entry in entries)
        and not counters['rejected_records'] and not counters['conflicting_records'] and not any(row.get('catalog_conflict') for row in records)
        and bool(records) and verifier.unit_reconciles(unit, year, records, scope_docs))
    review = counters['rejected_records'] or counters['conflicting_records'] or any(row.get('catalog_conflict') for row in records)
    state = 'verified' if complete else 'needs_review' if review else 'partial'
    declared = {entry['status'].get('status') for entry in entries}
    if not candidates and declared <= {'failed', 'restricted'}:
        state = 'restricted' if declared == {'restricted'} else 'failed'
    notes = list(dict.fromkeys(str(note) for entry in entries for note in entry['status'].get('notes', [])))
    status = {'unit_id': unit['id'], 'unit_name': unit['name'], 'year': year, 'status': state,
        'expected_records': len(records) if complete else None, 'parsed_records': len(records),
        'verified_records': len(records) if complete else 0, **counters,
        'notes': [*notes, '逐条核验后增量收录；部分来源不代表该校目录全集。']}
    old_statuses = previous.get('metadata', {}).get('unit_catalog_status', [])
    metadata = {**previous.get('metadata', {}), 'target_year': 2028, 'reference_year': 2027,
        'catalog_complete': False, 'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'unit_catalog_status': [row for row in old_statuses if (row.get('unit_id'), row.get('year')) != (unit['id'], year)] + [status]}
    unrelated = [row for row in previous.get('programs', []) if (row.get('unit_id'), row.get('year')) != (unit['id'], year)]
    return {**previous, 'metadata': metadata, 'programs': [*unrelated, *records], 'documents': documents,
        'catalog_conflicts': [*previous.get('catalog_conflicts', []), *conflicts],
        'catalog_superseded': [*previous.get('catalog_superseded', []), *superseded]}


def ingest_verified_batch(registry, collected, previous=None, *, project_root=PROJECT_ROOT, output=None, approved_audits=None):
    """Incrementally adopt only independently replayed rows; preserve unresolved versions."""
    units = {str(row['id']): row for row in registry.get('units', [])}
    grouped = {}
    for entry in collected:
        if (not isinstance(entry, dict) or not isinstance(entry.get('status'), dict)
                or any(not isinstance(entry.get(key, []), list) or any(not isinstance(row, dict) for row in entry.get(key, []))
                       for key in ('programs', 'documents'))):
            raise DocumentError('批次必须提供学校状态、目录记录和原始文件列表')
        status = entry.get('status', {})
        identity, year = str(status.get('unit_id')), status.get('year')
        if identity not in units or type(year) is not int or year != 2027:
            raise DocumentError('增量导入必须绑定已收录招生单位和2027参考年度')
        grouped.setdefault((identity, year), []).append(entry)
    merged = deepcopy(previous or {'metadata': {}, 'programs': [], 'documents': []})
    for (identity, year), entries in grouped.items():
        merged = _ingest_school(merged, deepcopy(entries), units[identity], year, project_root, approved_audits)
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + '\n', 'utf-8')
        temporary.replace(path)
    return merged


def main():
    cli = argparse.ArgumentParser(description='按清单增量采集可重放的学校目录；有限来源保留partial')
    cli.add_argument('--manifest', type=Path, required=True)
    cli.add_argument('--units', type=Path, default=PROJECT_ROOT / 'data/units.json')
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--previous', type=Path)
    cli.add_argument('--snapshot-dir', type=Path, default=PROJECT_ROOT / 'data/snapshots/catalogs/batch')
    cli.add_argument('--offline', action='store_true')
    args = cli.parse_args()
    registry = json.loads(args.units.read_text('utf-8-sig'))
    manifest = json.loads(args.manifest.read_text('utf-8-sig'))
    previous_path = args.previous or args.output
    previous = json.loads(previous_path.read_text('utf-8-sig')) if previous_path.exists() else None
    collected = []
    for spec in manifest.get('schools', manifest.get('units', [])):
        entry = collect_manifest(registry, {'schools': [spec]}, args.snapshot_dir, offline=args.offline)[0]
        collected.append(entry)
        save_progress(args.output, collected, previous, registry=registry)
        print(json.dumps({'unit': entry['status']['unit_name'], 'status': entry['status']['status'],
                          'programs': len(entry['programs']), 'notes': entry['status']['notes']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
