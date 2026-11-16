"""Reproduce temporary exam facts from a verified official directory itself."""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unicodedata

from collectors.documents import decode_text
from grad_engine import _exclusion_filter, _subject_units
from research_store import ResearchError

_MAJOR = re.compile(r'(?<![0-9A-Z])(?:0[1-9]|1[0-4])\d{2}(?:\d{2}|[A-Z]\d)(?![0-9A-Z])')
_VERSION = 'catalog_structure_v1'
_MAX_ORIGINAL_BYTES = 8 * 1024 * 1024


def major_code(query):
    codes = set(_MAJOR.findall(unicodedata.normalize('NFKC',query).upper()))
    return next(iter(codes)) if len(codes) == 1 else ''


def exam_codes(query):
    return set(re.findall(r'(?<![0-9A-Za-z])\d{3}(?![0-9A-Za-z]|\s*(?:分|人|元|名|万|天|年|位|校|所|次))',query))


def _locators(row,source):
    fields = [('row',row.get('evidence')),('exams',row.get('exam_evidence'))]
    return [{**ref,'field':field,'source_id':source['id'],'original_url':source['url'],
             'sha256':source['sha256']} for field,refs in fields for ref in refs or ()
            if isinstance(ref,dict) and ref.get('quote') and ref.get('url') == source['url']]


def _raw_original(path, sha):
    with path.open('rb') as stream:
        raw = stream.read(_MAX_ORIGINAL_BYTES + 1)
    return raw if len(raw) <= _MAX_ORIGINAL_BYTES and hashlib.sha256(raw).hexdigest() == sha else b''


def _parse_rows(tools, source, unit, code):
    key = (source['id'],source['url'],source['sha256'],unit['id'],source['year'],code)
    cache = getattr(tools,'_live_catalog_cache',{})
    path = tools._knowledge()._blob_path(source['sha256'])
    if key in cache:
        return deepcopy(cache[key]) if tools._knowledge()._blob_available(source['sha256']) else []
    raw = _raw_original(path,source['sha256'])
    document = {'id':source['id'],'url':source['url'],'sha256':source['sha256'],
                'unit_id':unit['id'],'year':source['year']}
    if raw.startswith(b'%PDF-'):
        from research_catalog_pdf import parse_live_catalog_pdf
        with TemporaryDirectory(prefix='yanxu-pdf-') as folder:
            original = Path(folder) / 'original.pdf'
            original.write_bytes(raw)
            parsed = parse_live_catalog_pdf(original,unit,source['year'],document,major_code=code)
    elif raw:
        from research_catalog_html import parse_catalog_html_blocks
        parsed = parse_catalog_html_blocks(decode_text(raw,''),unit,source['year'],document)
    else:
        return []
    rows = [row for row in parsed.get('programs',()) if row.get('code') == code
            and row.get('unit_id') == unit['id'] and row.get('year') == source['year']]
    tools._live_catalog_cache = {**cache,key:deepcopy(rows)}
    return rows


def verified_rows(tools, source_id, code):
    from research_candidate_tools import _public_program, _trusted
    source = tools.sources.get(source_id) or {}
    if not code or not _trusted(tools,source) or tools._source_unavailable(source):
        return []
    unit = tools._unit(source['unit_id'])
    try:
        rows = _parse_rows(tools,source,unit,code)
    except (ValueError,OSError,ImportError):
        return []
    candidates = []
    for row in rows:
        groups, unknown = _subject_units(row.get('subjects'))
        refs = _locators(row,source)
        if (unknown or sorted(groups) not in ([1,2],[1,2,3],[1,2,3,4])
                or {ref['field'] for ref in refs} != {'row','exams'} or not row.get('id')):
            continue
        identity = hashlib.sha256((row['id']+'|'+source['sha256']).encode()).hexdigest()[:28]
        proof = {'mode':_VERSION,'source_id':source_id,'sha256':source['sha256'],'code':code}
        value = {**row,'id':'tp_'+identity,'candidate_origin':'official_original',
                 'verification_mode':'official_original','evidence_refs':refs,'candidate_proof':proof,
                 'tuition':None,'conditions':[],'requires_review':True,'eligibility_evidence_complete':False}
        candidates.append(_public_program(tools,value))
    return candidates


def adopt(tools,source_id):
    from research_candidate_tools import MAX_CANDIDATES, _sync_pool
    user_query = tools.task.get('query','')
    tool_query = getattr(tools,'_live_catalog_query','')
    code = major_code(user_query) or major_code(tool_query)
    accepted = verified_rows(tools,source_id,code)
    if accepted:
        from official_search import broad_subject_exclusions
        query = user_query if exam_codes(user_query) or major_code(tool_query)!=code else tool_query
        excluded = broad_subject_exclusions(query) | {
            row['code'] for row in tools.task.get('profile',{}).get('excluded_subjects',())}
        required = exam_codes(query) - excluded
        compatible = []
        for row in accepted:
            subjects,status = _exclusion_filter(row['subjects'],[{'code':code} for code in excluded])
            groups,_ = _subject_units(subjects)
            slots = [{subject['code'] for subject in slot} for slot in groups.values()]
            if (status['status']=='matched' and required <= set().union(*slots)
                    and all(len(required & slot) <= 1 for slot in slots)):
                compatible.append(row)
        accepted = compatible
    if not accepted:
        return
    existing = tools._source_candidate_records
    available = min(6,max(0,MAX_CANDIDATES-len(existing)))
    new = [row for row in accepted if row['id'] not in existing][:available]
    tools._source_candidate_records = {**existing,**{row['id']:row for row in new}}
    _sync_pool(tools)


def replay(tools,record):
    proof = record.get('candidate_proof') or {}
    try:
        return next((row for row in verified_rows(tools,proof['source_id'],proof['code'])
                     if row['id']==record['id'] and row['candidate_proof']['sha256']==proof['sha256']),None)
    except (KeyError,ResearchError):
        return None
