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
    domains = tuple(value for value in (_checked_domain(item) for item in row.get('domains') or []) if value)
    if not domains:
        return None
    aliases = tuple(value.strip() for value in row.get('aliases') or []
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
