"""Reviewed pointers to public admission catalogs, without program facts."""
from collections import defaultdict
from functools import lru_cache
import json
from pathlib import Path
from urllib.parse import urlsplit

from official_authority import registered_scopes, scope_allows
from official_search import http_url, target_matches
from public_sources import official_source_url
from unit_bindings import consortium_admissions_scope


SOURCE_TYPE = 'official_catalog_index'


@lru_cache(maxsize=1)
def reviewed_entries():
    path = Path(__file__).with_name('collectors') / 'official_catalog_entries.json'
    return tuple(json.loads(path.read_text(encoding='utf-8'))) if path.exists() else ()


def register(tools, year, *, unit_ids=(), provinces=(), hinted=()):
    """Attach exact-cohort navigation leads; source reading still verifies each original."""
    scope = set(unit_ids)
    if not (scope or provinces or hinted):
        return []
    rows_by_unit = defaultdict(list)
    for row in reviewed_entries():
        identifier = row['unit_id']
        if row['year'] != year or identifier not in tools.units:
            continue
        if scope and identifier not in scope:
            continue
        if not scope and provinces and tools.units[identifier].get('province') not in provinces:
            continue
        if not scope and not provinces and identifier not in hinted:
            continue
        rows_by_unit[identifier].append(row)
    ordered = dict.fromkeys((*hinted, *rows_by_unit))
    sources = []
    covered = 0
    previous = {(source.get('unit_id'),source.get('url')):source for source in tools.sources.values()
                if source.get('requested_year') == year and source.get('source_type') in (SOURCE_TYPE,'yuanbao_search')
                and source.get('source_tier') == 'official'}
    for identifier in ordered:
        if identifier not in rows_by_unit:
            continue
        unit = tools._unit(identifier)
        registered = registered_scopes(unit)
        owned = []
        for row in rows_by_unit[identifier]:
            if not (target_matches(row,year,'catalog') or target_matches(row,year,'charter')):
                continue
            urls = (row['url'],row['publisher_url'])
            if not all(
                    http_url(url) and not urlsplit(url).fragment
                    and any(scope_allows(url, entry) for entry in registered)
                    and official_source_url(url,unit)
                    and consortium_admissions_scope(url,unit) is None for url in urls
            ):
                continue
            for url in dict.fromkeys(urls):
                owned.append({**row,'url':url})
        if not owned:
            continue
        added = []
        for row in list({row['url']:row for row in owned}.values())[:3]:
            key = (identifier,row['url'])
            if key in previous:
                if previous[key].get('source_type') == SOURCE_TYPE:
                    added.append(previous[key])
                continue
            sid = tools._source({'unit_id':identifier,'url':row['url'],'title':row['title'],
                'year':None,'requested_year':year,'requested_kind':'catalog','status':'search_lead',
                'source_type':SOURCE_TYPE,'search_channel':SOURCE_TYPE,'source_tier':'official',
                'source_role':'catalog_entry','original_available':False,'excerpt':'',
                'notice':'已登记当年官方目录入口，正文与初试科目等待读取核验。'})
            if sid:
                added.append(tools.sources[sid])
        if not added:
            continue
        sources.extend(added)
        covered += 1
        if covered >= 4:
            break
    return sources
