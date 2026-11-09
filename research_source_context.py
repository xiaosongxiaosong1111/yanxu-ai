"""Resolve an optional knowledge follow-up without trusting browser metadata."""
import re
import unit_bindings

from grad_engine import validate_profile
from public_sources import official_source_url
from research_store import ResearchError


def source_context(identifier, profile, explicit_profile, unit_ids, units, lookup):
    if identifier is None:
        return {'knowledge_entry_id': None, 'source_context': None,
                'profile': profile, 'unit_ids': unit_ids}
    if not isinstance(identifier, str) or not re.fullmatch(r'k_[0-9a-f]{24}', identifier):
        raise ResearchError('invalid_knowledge', '补证资料编号不正确，请从知识库重新选择。')
    try:
        entry = lookup(identifier)
    except Exception as error:
        raise ResearchError('source_context_unavailable', '补证资料暂不可用，请从知识库重新选择。', 409) from error
    quality = entry.get('quality') or {}
    unit_id = entry.get('unit_id')
    unit = unit_bindings.enrich(next((item for item in units if item['id'] == unit_id), None))
    if (not unit or entry.get('validity', 'active') != 'active' or quality.get('state') == 'excluded'
            or entry.get('content_notice')
            or not official_source_url(str(entry.get('url') or ''), unit)):
        raise ResearchError('source_context_unavailable', '这份资料不属于当前可补证的官方学校来源。', 409)
    if unit_ids and unit_id not in unit_ids:
        raise ResearchError('source_context_scope', '补证资料的学校与本轮所选范围不一致，请重新选择。')
    year = entry.get('year')
    year = year if type(year) is int and 2000 <= year <= 2100 else None
    if year is not None:
        if 'reference_year' in explicit_profile and profile['reference_year'] != year:
            raise ResearchError('source_context_year', '补证资料的实际年度与本轮参考年度不一致，请分别研究。')
        try:
            profile = validate_profile({**profile, 'reference_year': year})
        except ValueError as error:
            raise ResearchError('source_context_year', '这份资料的年度不适用于当前入学规划。') from error
    context = {'entry_id': identifier, 'unit_id': unit_id, 'year': year,
               'title': str(entry.get('title') or '')[:500], 'url': entry['url'],
               'status': entry.get('status'), 'quality': quality.get('state', 'pending'),
               'reason_codes': [reason['code'] for reason in quality.get('reasons', [])[:12]
                                if isinstance(reason, dict) and isinstance(reason.get('code'), str)]}
    return {'knowledge_entry_id': identifier, 'source_context': context,
            'profile': profile, 'unit_ids': unit_ids or [unit_id]}
