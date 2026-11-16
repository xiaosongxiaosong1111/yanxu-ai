"""Evidence-backed official domains and short names for catalog units.

The registry stores one ``official_url`` per unit. That is not enough when a
school publishes its admissions site on a second registered domain: the
school homepage and its graduate admissions site can live on different
suffixes (``ucas.edu.cn`` and ``ucas.ac.cn`` for 中国科学院大学). Widening the
official filter by guesswork would let any ``.edu.cn`` page count as official,
so every extra domain here must name the official page that proves the
relation, the quote or link it relies on, and the date it was reviewed.

``enrich`` only adds domains backed by such evidence. It never invents a
binding, and a unit without a reviewed row keeps ``official_url`` empty so the
rest of the product can report "binding missing" instead of pretending the
official site has no material.

Deployments can append reviewed rows in ``data/official_bindings.json`` with the
same shape; rows that fail validation are ignored, never guessed.
"""
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent
EXTERNAL_PATH = PROJECT_ROOT / 'data' / 'official_bindings.json'

# Each domain records where the claim comes from. `relation` is a short
# machine-readable reason a reviewer can replay; `evidence` must be an
# official page that was actually read. `checked_at` is the review date.
CURATED = (
    {
        'name': '燕山大学', 'aliases': (), 'unit_code': '10216',
        'domains': (
            {'url': 'https://zsjyc.ysu.edu.cn/yjsxwz/', 'role': 'admissions_site',
             'relation': 'linked_from_institution_homepage', 'evidence': 'https://www.ysu.edu.cn/',
             'evidence_quote': '研究生招生 → http://zsjyc.ysu.edu.cn/yjsxwz/yzsy.htm',
             'checked_at': '2026-11-16'},
            {'url': 'https://zsjyc.ysu.edu.cn/info/1255/', 'role': 'catalog_portal',
             'relation': 'institution_links_to_master_catalog_portal',
             'evidence': 'https://zsjyc.ysu.edu.cn/yjsxwz/sszs/zsml.htm',
             'evidence_quote': '004 信息科学与工程学院2027年硕士研究生招生目录 → /info/1255/4496.htm',
             'checked_at': '2026-11-16'},
        ),
    },
    {
        'name': '湖南农业大学', 'aliases': (), 'unit_code': '10537',
        'domains': (
            {'url': 'https://yjsy.hunau.edu.cn/', 'role': 'admissions_site',
             'relation': 'linked_from_institution_homepage', 'evidence': 'https://www.hunau.edu.cn/',
             'evidence_quote': '研究生教育 → https://yjsy.hunau.edu.cn/',
             'checked_at': '2026-11-16'},
            {'url': 'https://nxy.hunau.edu.cn/gksx/ssszs/', 'role': 'department_site',
             'relation': 'linked_from_institution_organization_directory',
             'evidence': 'https://www.hunau.edu.cn/jgsz/xysz/',
             'evidence_quote': '农学院 → http://nxy.hunau.edu.cn/',
             'checked_at': '2026-11-16'},
        ),
    },
    {
        'name': '广州大学', 'aliases': (), 'unit_code': '11078',
        'domains': (
            {'url': 'https://yjsy.gzhu.edu.cn/', 'role': 'admissions_site',
             'relation': 'linked_from_institution_homepage', 'evidence': 'https://www.gzhu.edu.cn/zzjg/xy_b_.htm',
             'evidence_quote': '研究生招生 → http://yjsy.gzhu.edu.cn/', 'checked_at': '2026-11-16'},
            {'url': 'https://environ.gzhu.edu.cn/', 'role': 'department_site',
             'relation': 'linked_from_institution_organization_directory',
             'evidence': 'https://www.gzhu.edu.cn/zzjg/xy_b_.htm',
             'evidence_quote': '环境科学与工程学院 → http://environ.gzhu.edu.cn/', 'checked_at': '2026-11-16',
             'publisher_label': '环境科学与工程学院、大湾区环境研究院',
             'publisher_evidence': 'https://www.gzhu.edu.cn/xxgk/xrld/dw.htm'},
        ),
    },
    {
        'name': '福建医科大学', 'aliases': (), 'unit_code': '10392',
        'domains': ({'url': 'https://yjsy.fjmu.edu.cn/', 'role': 'admissions_site',
                     'relation': 'linked_from_institution_organization_directory',
                     'evidence': 'https://www.fjmu.edu.cn/91/listm.htm',
                     'evidence_quote': '研究生处 → https://yjsy.fjmu.edu.cn/', 'checked_at': '2026-11-16'},),
    },
    {
        'name': '西南财经大学', 'aliases': (), 'unit_code': '10651',
        'domains': (
            {'url': 'https://yz.swufe.edu.cn/', 'role': 'admissions_site',
             'relation': 'linked_from_institution_homepage', 'evidence': 'https://www.swufe.edu.cn/zsjy.htm',
             'evidence_quote': '研究生招生 → https://yz.swufe.edu.cn/', 'checked_at': '2026-11-16'},
            {'url': 'https://yzcx.swufe.edu.cn/chaxun/yzb/ss/', 'role': 'catalog_portal',
             'relation': 'institution_links_to_master_catalog_portal',
             'evidence': 'https://yz.swufe.edu.cn/info/1101/13351.htm',
             'evidence_quote': '2027年硕士研究生招生目录 → https://yzcx.swufe.edu.cn/chaxun/yzb/ss/2027/01zszc&ml/2027ssml.html',
             'checked_at': '2026-11-16'},
        ),
    },
    {
        'name': '四川外国语大学', 'aliases': (), 'unit_code': '10650',
        'domains': ({'url': 'https://graduate.sisu.edu.cn/', 'role': 'admissions_site',
                     'relation': 'linked_from_institution_homepage', 'evidence': 'https://www.sisu.edu.cn/zsjy/index.htm',
                     'evidence_quote': '研究生招生 → http://graduate.sisu.edu.cn/', 'checked_at': '2026-11-16'},),
    },
    {
        'name': '西安科技大学', 'aliases': (), 'unit_code': '10704',
        'domains': ({'url': 'https://yjs.xust.edu.cn/', 'role': 'admissions_site',
                     'relation': 'linked_from_institution_organization_directory',
                     'evidence': 'https://www.xust.edu.cn/zzjg.htm',
                     'evidence_quote': '研究生院 → https://yjs.xust.edu.cn/', 'checked_at': '2026-11-16'},),
    },
    {
        'name': '中南财经政法大学', 'aliases': (), 'unit_code': '10520',
        'domains': ({'url': 'https://yzb.zuel.edu.cn/', 'role': 'admissions_site',
                     'relation': 'institution_college_names_graduate_admissions_site',
                     'evidence': 'https://iprcn.zuel.edu.cn/zgzscqyjw-zgzscqyjw_tzgg/zgzscqyjwcn_cont_news/details-39613.html',
                     'evidence_quote': '学校“研究生招生信息网”：http://yzb.zuel.edu.cn。',
                     'checked_at': '2026-11-16'},),
    },
    {
        'name': '辽宁大学', 'aliases': (), 'unit_code': '10140',
        'domains': ({'url': 'https://grs.lnu.edu.cn/', 'role': 'admissions_site',
                     'relation': 'linked_from_institution_homepage',
                     'evidence': 'https://www.lnu.edu.cn/',
                     'evidence_quote': '研究生招生 → https://grs.lnu.edu.cn/zsgz.htm', 'checked_at': '2026-11-16'},),
    },
    {
        'name': '内江师范学院', 'aliases': (), 'unit_code': '10640',
        'domains': ({'url': 'https://gc.njtc.edu.cn/', 'role': 'department_site',
                     'relation': 'linked_from_institution_organization_directory',
                     'evidence': 'https://www.njtc.edu.cn/jgsz/jxkyjg.htm',
                     'evidence_quote': '物理与电子信息工程学院 → http://gc.njtc.edu.cn',
                     'checked_at': '2026-11-16'},),
    },
    {
        'name': '昆明理工大学', 'aliases': (), 'unit_code': '10674',
        'domains': ({'url': 'https://yjs.kust.edu.cn/', 'role': 'admissions_site',
            'relation': 'institution_publishes_master_catalog',
            'evidence': 'https://yjs.kust.edu.cn/info/1254/4413.htm',
            'evidence_quote': '昆明理工大学2027年硕士研究生招生专业目录', 'checked_at': '2026-11-16'},
            {'url': 'https://www.swugs.ynu.edu.cn/', 'role': 'special_admissions_site',
             'relation': 'charter_limits_consortium_site_to_special_admissions',
             'evidence': 'https://www.kust.edu.cn/info/1166/57688.htm',
             'evidence_quote': '西南联合研究生院专项计划', 'checked_at': '2026-11-16'},),
    },
    {
        'name': '太原科技大学', 'aliases': (), 'unit_code': '10109',
        'domains': ({'url': 'https://yjsxy.tyust.edu.cn/', 'role': 'admissions_site',
            'relation': 'institution_publishes_master_catalog',
            'evidence': 'https://yjsxy.tyust.edu.cn/info/1203/3712.htm',
            'evidence_quote': '太原科技大学2027年硕士研究生招生目录', 'checked_at': '2026-11-11'},),
    },
    # Reviewed raw originals and their SHA256 live in fixtures/official_discovery/reviewed_roots.
    # These are entry bindings, never proof of a requested year's admissions facts.
    {
        'name': '复旦大学', 'aliases': (), 'unit_code': '10246',
        'domains': ({'url': 'https://gsao.fudan.edu.cn/', 'role': 'admissions_site',
            'relation': 'institution_publication_names_admissions_site',
            'evidence': 'https://www.fudan.edu.cn/2025/1009/c24a146881/page.htm',
            'evidence_quote': '招生网址：https://gsao.fudan.edu.cn', 'checked_at': '2026-11-09'},),
    },
    {
        'name': '青海师范大学', 'aliases': (), 'unit_code': '10746',
        'domains': ({'url': 'https://yjsb.qhnu.edu.cn/', 'role': 'admissions_site',
            'relation': 'linked_from_institution_homepage', 'evidence': 'https://www.qhnu.edu.cn/',
            'evidence_quote': '研究生招生 → https://yjsb.qhnu.edu.cn/', 'checked_at': '2026-11-09'},),
    },
    {
        'name': '山西中医药大学', 'aliases': (), 'unit_code': '10809',
        'domains': ({'url': 'https://yjsb.sxtcm.edu.cn/', 'role': 'admissions_site',
            'relation': 'linked_from_institution_homepage', 'evidence': 'https://www.sxtcm.edu.cn/',
            'evidence_quote': '研究生招生 → https://yjsb.sxtcm.edu.cn/', 'checked_at': '2026-11-09'},),
    },
    {
        'name': '中国科学院大学',
        'aliases': ('国科大',),
        'domains': (
            {
                'url': 'https://www.ucas.edu.cn/',
                'role': 'institution_homepage',
                'relation': 'homepage_links_to_admissions_site',
                'evidence': 'https://www.ucas.edu.cn/',
                'evidence_quote': '首页导航链接到 https://admission.ucas.ac.cn/',
                'checked_at': '2026-11-05',
            },
            {
                # The admissions site lives on a different registrable domain
                # (ucas.ac.cn vs ucas.edu.cn). The homepage link is the proof;
                # without this row the exact-host official filter rejects every
                # admissions page the school actually publishes.
                'url': 'https://admission.ucas.ac.cn/',
                'role': 'admissions_site',
                'relation': 'linked_from_institution_homepage',
                'evidence': 'https://www.ucas.edu.cn/',
                'evidence_quote': '首页导航链接到 https://admission.ucas.ac.cn/',
                'checked_at': '2026-11-05',
            },
        ),
    },
    {
        'name': '中国社会科学院大学',
        'aliases': ('社科大',),
        'unit_code': '14596',
        'domains': (
            {
                'url': 'https://skdzs.ucass.edu.cn/',
                'role': 'admissions_site',
                'relation': 'admissions_charter_names_the_site',
                'evidence': 'https://skdzs.ucass.edu.cn/info/1166/2740.htm',
                'evidence_quote': '中国社会科学院大学招生网：skdzs.ucass.edu.cn',
                'checked_at': '2026-11-05',
            },
        ),
    },
)

_cache = {'path': None, 'mtime': None, 'rows': ()}


def normalize_name(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))


def _http_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        return None
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username or parts.password:
            return None
        if parts.port not in {None, 80, 443}:
            return None
        return parts
    except ValueError:
        return None


def _checked_domain(row):
    """Keep only a domain row that carries its own official evidence."""
    if not isinstance(row, dict):
        return None
    url, evidence = row.get('url'), row.get('evidence')
    if not _http_url(url) or not _http_url(evidence):
        return None
    host = (urlsplit(url).hostname or '').lower().rstrip('.')
    if host == 'chsi.com.cn' or host.endswith('.chsi.com.cn'):
        return None
    relation = row.get('relation') if isinstance(row.get('relation'), str) else 'declared_official_site'
    role = row.get('role') if isinstance(row.get('role'), str) else 'official_site'
    quote = row.get('evidence_quote') if isinstance(row.get('evidence_quote'), str) else ''
    checked_at = row.get('checked_at') if isinstance(row.get('checked_at'), str) else ''
    return {'url': str(url).strip(), 'role': role[:60], 'relation': relation[:80],
            'evidence': str(evidence).strip(), 'evidence_quote': quote[:300],
            'checked_at': checked_at[:40]}


def _checked_row(row):
    if not isinstance(row, dict) or not isinstance(row.get('name'), str) or not row['name'].strip():
        return None
    domain_rows, alias_rows = row.get('domains'), row.get('aliases', ())
    if not isinstance(domain_rows, (list, tuple)) or not isinstance(alias_rows, (list, tuple)):
        return None
    domains = tuple(value for value in (_checked_domain(item) for item in domain_rows) if value)
    if not domains:
        return None
    aliases = tuple(value.strip() for value in alias_rows
                    if isinstance(value, str) and 1 < len(value.strip()) <= 20)
    unit_code = row.get('unit_code')
    return {'name': row['name'].strip()[:80], 'aliases': aliases[:8],
            'unit_code': str(unit_code).strip()[:40] if unit_code not in (None, '') else None,
            'domains': domains[:8]}


def _external_rows(path):
    path = Path(path) if path is not None else EXTERNAL_PATH
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return ()
    if _cache['path'] == str(path) and _cache['mtime'] == stamp:
        return _cache['rows']
    try:
        value = json.loads(path.read_text('utf-8'))
        source = value.get('units') if isinstance(value, dict) else value
        rows = tuple(row for row in (_checked_row(item) for item in source or []) if row) if isinstance(source, list) else ()
    except (OSError, ValueError, TypeError):
        rows = ()
    _cache.update({'path': str(path), 'mtime': stamp, 'rows': rows})
    return rows


def rows(path=None):
    """Curated rows plus any reviewed deployment rows (deduplicated by name)."""
    combined, seen = [], set()
    for row in (*CURATED, *_external_rows(path)):
        key = normalize_name(row['name'])
        if key in seen:
            continue
        seen.add(key)
        combined.append(row)
    return tuple(combined)


def find_row(unit, path=None):
    if not isinstance(unit, dict):
        return None
    name = normalize_name(unit.get('name'))
    code = str(unit.get('unit_code') or '').strip()
    for row in rows(path):
        if code and row.get('unit_code') and code == row['unit_code']:
            return row
        if name and name == normalize_name(row['name']):
            return row
    return None


def resolve_query(query, path=None):
    """Resolve a short school name such as 国科大 to its catalog name."""
    value = normalize_name(query)
    if not value:
        return None
    for row in rows(path):
        canonical = normalize_name(row['name'])
        if value == canonical:
            return {'alias': None, 'canonical': row['name']}
        for alias in row['aliases']:
            if value == normalize_name(alias):
                return {'alias': alias, 'canonical': row['name']}
    return None


def expand_query(query, path=None):
    """Expand short-name tokens inside a catalog search query.

    ``search_text`` stores the registry name, not the nickname, so a query
    such as ``社科大 计算机`` matched nothing even though ``中国社会科学院大学
    计算机`` works. Only a token that resolves *exactly* to a known alias is
    rewritten; partial names and arbitrary words are left untouched so this
    can never widen a search into a different school.
    """
    if not isinstance(query, str) or not query.strip():
        return query
    terms, changed = [], False
    for term in query.split():
        resolved = resolve_query(term, path)
        if resolved and resolved['alias']:
            terms.append(resolved['canonical'])
            changed = True
        else:
            terms.append(term)
    return ' '.join(terms) if changed else query


def aliases_for(unit, path=None):
    row = find_row(unit, path)
    return tuple(row['aliases']) if row else tuple(unit.get('aliases') or ())


def consortium_admissions_scope(url, unit):
    """Identify the exact special-program host or an unproven host on its shared domain."""
    parts = _http_url(url)
    host = parts.hostname.lower().rstrip('.') if parts else ''
    if host != 'ynu.edu.cn' and not host.endswith('.ynu.edu.cn'):
        return None
    row = find_row(unit)
    if row and row['unit_code'] == '10674':
        return host == 'www.swugs.ynu.edu.cn'
    return None


def reviewed_publisher_title(url, unit, heading):
    """Accept a reviewed department's short admissions heading only within its own scope."""
    row = find_row(unit)
    if row is None:
        return False
    from official_authority import _scope, scope_allows
    for domain in row['domains']:
        publisher = normalize_name(domain.get('publisher_label'))
        if (domain.get('role') != 'department_site' or not publisher or not _http_url(domain.get('publisher_evidence'))
                or not scope_allows(url, _scope(domain['url']))):
            continue
        section = re.escape(publisher.split('、', 1)[0])
        title = (re.escape(publisher) + r'20\d{2}年(?:攻读)?硕士(?:学位)?研究生招生(?:简章|章程|专业目录|目录|公告|指南)'
                 + r'(?:[-—|]' + section + r')?')
        if re.fullmatch(title, normalize_name(heading)):
            return True
    return False


def enrich(unit, path=None):
    """Return the unit with evidence-backed extra domains and aliases merged."""
    if not isinstance(unit, dict):
        return unit
    row = find_row(unit, path)
    if row is None:
        return unit
    existing = []
    single = unit.get('official_url')
    if isinstance(single, str) and single.strip():
        existing.append({'url': single.strip(), 'role': 'registered', 'relation': 'catalog_registry',
                         'evidence': '', 'evidence_quote': '', 'checked_at': ''})
    for item in unit.get('official_urls') or []:
        value = _checked_domain(item if isinstance(item, dict) else {'url': item})
        if value:
            existing.append(value)
    seen = {normalize_name(item['url']).rstrip('/') for item in existing}
    for domain in row['domains']:
        key = normalize_name(domain['url']).rstrip('/')
        if key in seen:
            continue
        seen.add(key)
        existing.append(domain)
    primary = unit.get('official_url') if isinstance(unit.get('official_url'), str) and unit.get('official_url').strip() else existing[0]['url']
    return {**unit, 'official_url': primary, 'official_urls': existing,
            'aliases': list(dict.fromkeys([*aliases_for(unit, path), *(unit.get('aliases') or [])])),
            'official_binding': {'source': 'curated_evidence', 'name': row['name'],
                                 'unit_code': row.get('unit_code'), 'domains': list(row['domains'])}}
