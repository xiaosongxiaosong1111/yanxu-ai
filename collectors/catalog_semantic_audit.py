"""Read-only approval gate for PDF table semantics, separate from parser replay.

The root reviewer owns data/catalog_semantic_audits.json. Downloaders must never
write approvals. An explicitly supplied approved_audits value is trusted caller
configuration, never a field taken from a document or a downloaded manifest.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import stat


JXAU_PARSER = 'pdf_table_academic_professional_v1'
YCIT_PARSER = 'pdf_table_ycit_exact_v1'
QAU_PARSER = 'pdf_table_qau_chapter_v1'
REPLAY_BINDINGS = Path(__file__).with_name('catalog_review_bindings.json')
_PORT_MODULES = {
    JXAU_PARSER: ('catalog_pdf_department_blocks.py',),
    YCIT_PARSER: ('catalog_ycit_pdf.py',),
    QAU_PARSER: ('catalog_qau_pdf.py','catalog_qau_public_source.py'),
}
PORTABLE_ADAPTERS = frozenset(_PORT_MODULES)
_SHARED_PORT_DEPENDENCIES = (
    'collectors/catalog_strict_syntax.py', 'collectors/catalog_publication.py',
    'collectors/catalog_pdf_sectioned.py', 'collectors/catalog_ycit_vsb.py', 'collectors/catalog_html.py',
    'collectors/catalog_pdf_national.py', 'collectors/catalog_pdf_national_v2.py',
    'collectors/catalog_pdf_tables.py', 'collectors/catalog_pdf_variants.py',
    'collectors/catalogs.py', 'collectors/documents.py', 'collectors/embedded_documents.py',
    'collectors/catalog_semantic_audit.py', 'collectors/catalog_batch.py', 'document_extraction.py',
    'collectors/national_viewer_links.py', 'official_authority.py', 'public_sources.py', 'public_navigation.py', 'service_verification.py',
    'unit_bindings.py',
)
PARSER_FILES = {'pdf_table_v1': 'catalog_pdf_tables.py', 'pdf_table_v2': 'catalog_pdf_variants.py',
                'pdf_table_national_v1':'catalog_pdf_national.py','pdf_table_national_v2':'catalog_pdf_national_v2.py',
                **{adapter: files[0] for adapter,files in _PORT_MODULES.items()},
                'pdf_table_hit_v1':'catalog_hit_public.py'}
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_AUDIT_BYTES = 8 * 1024 * 1024
_METADATA = {'catalog_document_id', 'checked_at', 'published_at', 'stale', 'catalog_conflict',
             'version_state', 'catalog_removed', 'removal_reason'}
_SET_FIELDS = {'subjects', 'exam_combinations', 'kskmz', 'kskm'}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def _facts(value, field=''):
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('目录事实字段必须为字符串')
        return {key: _facts(item, key) for key, item in value.items()
                if key not in _METADATA and key != 'evidence' and not key.endswith('_evidence')}
    if isinstance(value, list):
        items = [_facts(item) for item in value]
        return sorted(items, key=_encoded) if field in _SET_FIELDS else items
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError('目录事实不是可核验的JSON值')


def semantic_programs_sha256(programs):
    """Hash every current row and its complete facts; only evidence/transport is omitted."""
    if not isinstance(programs, list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str)
                                           or not row['id'].strip() for row in programs):
        raise ValueError('语义审计必须提供本来源完整目录记录列表')
    if len({row['id'] for row in programs}) != len(programs):
        raise ValueError('完整目录记录身份重复，不能批准语义审校')
    rows = sorted((_facts(row) for row in programs), key=_encoded)
    return hashlib.sha256(_encoded(rows)).hexdigest()


def _read_file(value, root, limit):
    if not isinstance(value, (str, Path)):
        raise ValueError('原文路径缺失')
    path = Path(value)
    path = (root / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('原文或审核文件路径超出项目范围')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise ValueError('原文不是可读取的普通文件或超过大小限制')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if not raw or len(raw) > limit:
        raise ValueError('原文超过读取限制')
    return raw


def semantic_parser_sha256(document, project_root):
    """Only the internal adapter map chooses executable source; metadata paths are ignored."""
    name = PARSER_FILES.get(document.get('catalog_parser'))
    if name is None:
        raise ValueError('PDF表格适配器未列入语义审核白名单')
    adapter = document['catalog_parser']
    if adapter in PORTABLE_ADAPTERS:
        # The evidence store can be mounted from another checkout with different code.
        code_root = Path(__file__).resolve().parents[1]
        paths = (*_SHARED_PORT_DEPENDENCIES,*(f'collectors/{name}' for name in _PORT_MODULES[adapter]))
        files = {relative: hashlib.sha256(_read_file(relative,code_root,2*1024*1024)
                                          .replace(b'\r\n',b'\n')).hexdigest()
                 for relative in paths}
        return hashlib.sha256(_encoded(files)).hexdigest()
    root = Path(project_root).resolve()
    return hashlib.sha256(_read_file(Path('collectors') / name, root, 2 * 1024 * 1024)).hexdigest()


def _hash(value):
    return value.lower() if isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{64}', value) else None


def _review_valid(audit):
    if (audit.get('status') != 'approved' or not isinstance(audit.get('reviewer'), str)
            or not audit['reviewer'].strip() or not isinstance(audit.get('reviewed_at'), str)):
        return False
    try:
        reviewed = datetime.fromisoformat(audit['reviewed_at'])
        return reviewed.tzinfo is not None and reviewed.utcoffset() is not None
    except ValueError:
        return False


def _approved_list(root, approved_audits):
    if approved_audits is None:
        approved_audits = json.loads(_read_file(Path('data/catalog_semantic_audits.json'), root, MAX_AUDIT_BYTES))
    if isinstance(approved_audits, dict):
        approved_audits = approved_audits.get('audits')
    if (not isinstance(approved_audits, list) or len(approved_audits) > 10000
            or any(not isinstance(row, dict) for row in approved_audits)):
        raise ValueError('根审核清单格式无效')
    return approved_audits


def _ported_approvals(document, audits, parser_sha, source_sha, programs_sha, programs, root):
    index = json.loads(_read_file(REPLAY_BINDINGS.name,REPLAY_BINDINGS.parent.resolve(),16*1024))
    binding = index.get(document['catalog_parser']) if isinstance(index,dict) else None
    publication = document.get('publication_page') or {}
    if (not isinstance(binding,dict) or not isinstance(publication,dict)
            or parser_sha != _hash(binding.get('parser_sha256'))):
        return []
    publication_sha = _hash(binding.get('publication_sha256'))
    if not publication_sha or _hash(publication.get('sha256')) != publication_sha:
        return []
    raw = _read_file(publication.get('local_path'),root,MAX_SOURCE_BYTES)
    if hashlib.sha256(raw).hexdigest() != publication_sha:
        return []
    full_replay = hashlib.sha256(_encoded(sorted(programs,key=lambda row:row['id']))).hexdigest()
    if full_replay != _hash(binding.get('full_replay_sha256')):
        return []
    expected = _hash(binding.get('reviewed_approval_sha256'))
    matches = [row for row in audits if row.get('unit_id') == document.get('unit_id')
               and _hash(row.get('source_sha256')) == source_sha
               and _hash(row.get('programs_sha256')) == programs_sha
               and hashlib.sha256(_encoded(row)).hexdigest() == expected]
    if len(matches) != 1:
        return []
    proof = matches[0]['review_evidence']
    raw = _read_file(proof['local_path'],root,MAX_AUDIT_BYTES)
    if hashlib.sha256(raw).hexdigest() != _hash(proof['sha256']):
        return []
    return matches


def _approved_subset(approval, programs):
    ids = approval.get('approved_program_ids')
    if not isinstance(ids,list) or not ids or any(not isinstance(item,str) or not item for item in ids):
        raise ValueError('已审校目录方向清单无效')
    allowed = set(ids)
    rows = [row for row in programs if row['id'] in allowed]
    if (len(allowed) != len(ids) or len(rows) != len(ids)
            or semantic_programs_sha256(rows) != _hash(approval.get('approved_programs_sha256'))):
        raise ValueError('当前目录方向与已审校子集不一致')
    return ids


def explain_semantic_audit(document, parsed_programs, project_root, approved_audits=None):
    """No cache: source bytes, adapter bytes, and root approvals are reread each call."""
    if not isinstance(document, dict):
        return {'approved': False, 'reason': '来源记录无效'}
    adapter = document.get('catalog_parser')
    if not isinstance(adapter, str) or not adapter.lower().startswith('pdf_table'):
        return {'approved': True, 'reason': '此适配器不属于本次PDF表格语义审计范围', 'applicable': False}
    if adapter not in PARSER_FILES:
        return {'approved': False, 'reason': '未知PDF表格适配器，不能绕过独立语义审核', 'applicable': True}
    try:
        root = Path(project_root).resolve()
        if adapter in PORTABLE_ADAPTERS and root.name == 'data':
            root = root.parent
        source_sha = _hash(document.get('sha256'))
        if source_sha is None:
            raise ValueError('来源摘要格式无效')
        raw = _read_file(document.get('local_path'), root, MAX_SOURCE_BYTES)
        if not raw.startswith(b'%PDF-') or hashlib.sha256(raw).hexdigest() != source_sha:
            raise ValueError('实际PDF原文与来源摘要不一致')
        parser_sha = semantic_parser_sha256(document, root)
        programs_sha = semantic_programs_sha256(parsed_programs)
        audits = _approved_list(root, approved_audits)
        ported = adapter in PORTABLE_ADAPTERS
        matches = (_ported_approvals(document,audits,parser_sha,source_sha,programs_sha,parsed_programs,root) if ported else
                   [row for row in audits if _hash(row.get('source_sha256')) == source_sha
                    and _hash(row.get('parser_sha256')) == parser_sha
                    and _hash(row.get('programs_sha256')) == programs_sha])
        if len(matches) != 1 or not _review_valid(matches[0]):
            return {'approved': False, 'reason': '当前原文、适配器及完整记录集没有唯一有效的根语义批准',
                    'applicable': True, 'source_sha256': source_sha, 'parser_sha256': parser_sha,
                    'programs_sha256': programs_sha}
        subset = {'approved_program_ids':_approved_subset(matches[0],parsed_programs)} if ported else {}
        return {'approved': True, 'reason': '原文、适配器及完整记录集与根独立语义审校批准一致',
                'applicable': True, 'source_sha256': source_sha, 'parser_sha256': parser_sha,
                'programs_sha256': programs_sha, 'reviewer': matches[0]['reviewer'],
                'reviewed_at': matches[0]['reviewed_at'], **subset}
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError) as error:
        return {'approved': False, 'reason': str(error), 'applicable': True}


def check_semantic_audit(document, parsed_programs, project_root, approved_audits=None):
    return explain_semantic_audit(document, parsed_programs, project_root, approved_audits)['approved']
