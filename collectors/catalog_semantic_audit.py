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


PARSER_FILES = {'pdf_table_v1': 'catalog_pdf_tables.py', 'pdf_table_v2': 'catalog_pdf_variants.py',
                'pdf_table_national_v1':'catalog_pdf_national.py','pdf_table_national_v2':'catalog_pdf_national_v2.py',
                'pdf_table_national_v3':'catalog_pdf_national_v3.py',
                'pdf_table_hit_v1':'catalog_hit_public.py'}
V3_PARSER_DEPENDENCIES = ('collectors/catalog_pdf_national_v3.py', 'collectors/catalog_pdf_national_v2.py',
    'collectors/catalog_pdf_national.py', 'collectors/catalog_pdf_tables.py',
    'collectors/catalog_html.py', 'collectors/catalog_publication.py', 'collectors/embedded_documents.py',
    'collectors/catalogs.py', 'collectors/documents.py', 'service_verification.py',
    'public_sources.py', 'public_navigation.py')
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
    root = Path(project_root).resolve()
    if document.get('catalog_parser') == 'pdf_table_national_v3':
        dependencies = {relative: hashlib.sha256(_read_file(relative, root, 2 * 1024 * 1024)).hexdigest()
                        for relative in V3_PARSER_DEPENDENCIES}
        return hashlib.sha256(_encoded({'schema': 'national-v3-dependencies-v1', 'files': dependencies})).hexdigest()
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
        source_sha = _hash(document.get('sha256'))
        if source_sha is None:
            raise ValueError('来源摘要格式无效')
        raw = _read_file(document.get('local_path'), root, MAX_SOURCE_BYTES)
        if not raw.startswith(b'%PDF-') or hashlib.sha256(raw).hexdigest() != source_sha:
            raise ValueError('实际PDF原文与来源摘要不一致')
        parser_sha = semantic_parser_sha256(document, root)
        programs_sha = semantic_programs_sha256(parsed_programs)
        audits = _approved_list(root, approved_audits)
        matches = [row for row in audits if _hash(row.get('source_sha256')) == source_sha
                   and _hash(row.get('parser_sha256')) == parser_sha
                   and _hash(row.get('programs_sha256')) == programs_sha]
        if len(matches) != 1 or not _review_valid(matches[0]):
            return {'approved': False, 'reason': '当前原文、适配器及完整记录集没有唯一有效的根语义批准',
                    'applicable': True, 'source_sha256': source_sha, 'parser_sha256': parser_sha,
                    'programs_sha256': programs_sha}
        return {'approved': True, 'reason': '原文、适配器及完整记录集与根独立语义审校批准一致',
                'applicable': True, 'source_sha256': source_sha, 'parser_sha256': parser_sha,
                'programs_sha256': programs_sha, 'reviewer': matches[0]['reviewer'],
                'reviewed_at': matches[0]['reviewed_at']}
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError) as error:
        return {'approved': False, 'reason': str(error), 'applicable': True}


def check_semantic_audit(document, parsed_programs, project_root, approved_audits=None):
    return explain_semantic_audit(document, parsed_programs, project_root, approved_audits)['approved']
