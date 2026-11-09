"""One shared search pipeline for the page API and the research agent.

Before this module existed the page endpoint and ``ResearchTools.search_sources``
each parsed Yuanbao responses, decided what counted as an official host and
built their own fallback ladder. A fix in one place never reached the other,
which is why the agent could report "no official material" while the page
endpoint found the same page. Everything that turns a provider response into
tiered rows now lives here:

* response parsing and malformed counting,
* official / candidate / third-party classification,
* the Site-filter fallback and query ladder,
* the honest outcome, notice and failure-stage vocabulary.

Callers keep their own transport, budget accounting and source storage; they
pass a ``call`` callback that performs exactly one external request and returns
an already-normalized response body.
"""
import json
import re
import unicodedata
from urllib.parse import urlsplit

SEARCH_KINDS = {'catalog': '硕士招生专业目录 考试科目', 'charter': '硕士研究生招生章程',
                'fees': '研究生学费 学制', 'score': '硕士复试分数线',
                'retest': '硕士复试录取办法', 'transfer': '硕士调剂办法',
                'change': '硕士招生考试科目调整', 'enrollment': '硕士招生人数 招生计划 推免人数'}
SEARCH_SCOPES = ('official', 'extended')
# Third-party postgraduate sites are discovery leads only. They never become
# facts: the UI labels them and the research tools still require an official
# original before any field or recommendation may rely on them.
THIRD_PARTY_SOURCES = {'offcn.com': '中公教育', 'kaoyan.com': '考研帮', 'eol.cn': '中国教育在线',
                       'koolearn.com': '新东方在线', 'wendu.com': '文都考研', 'yantu.com': '研途考研'}
# Some units have no verified official_url in the registry. SearchPro still finds
# their admissions pages, so refusing every host silently hides real sources.
# These rows stay a separate, explicitly unconfirmed tier: they are never counted
# as official results, never read as originals and never become evidence.
CANDIDATE_TITLE_MARKERS = ('招生简章', '招生章程', '招生专业目录', '专业目录', '招生信息',
                           '硕士研究生招生', '研究生招生', '复试', '录取', '推免', '考试科目')
CANDIDATE_COMMERCIAL_MARKERS = ('考研', '报考攻略', '难度', '培训', '辅导', '课程', '备考',
                                '经验', '报录比', '分数线', '参考书', '汇总', '真题', '网课', '咨询')
CANDIDATE_INSTITUTIONAL_SUFFIXES = ('.edu.cn', '.ac.cn', '.gov.cn')
# Repeated pages on a plain commercial host are not evidence that the host is
# the school: search portals and postgraduate aggregators often republish the
# same notice under many paths. Keep those platforms out of the candidate tier
# even when two or more of their pages name the unit.
CANDIDATE_BLOCKED_DOMAINS = (
    'qq.com', 'baidu.com', 'sohu.com', 'sina.com.cn', '163.com', 'zhihu.com',
    'weibo.com', 'bilibili.com', 'douyin.com', 'xiaohongshu.com',
    'yanzhaowang.com.cn', 'chinakaoyan.com', 'kaoyan365.cn', 'kuakao.com',
    'xdf.cn', 'chsi.com.cn', 'yzw.org.cn',
    *THIRD_PARTY_SOURCES,
)

FAILURE_STAGES = ('official_found', 'binding_missing', 'attribution_unverified',
                  'third_party_only', 'domain_mismatch', 'index_no_hit')


def http_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.port not in {None, 80, 443}:
            return None
        return parsed
    except ValueError:
        return None


def root_domain(url):
    parsed = http_url(url)
    if not parsed:
        return None
    host = parsed.hostname.lower().rstrip('.')
    parts = host.split('.')
    if len(parts) >= 3 and parts[-2:] in [['edu', 'cn'], ['ac', 'cn'], ['gov', 'cn'], ['org', 'cn']]:
        return '.'.join(parts[-3:])
    return host.removeprefix('www.')


def host_matches(host, roots):
    return any(host == root or host.endswith('.' + root) for root in roots)


def parse_pages(body):
    """Return accepted provider items plus the count of unparseable entries."""
    items, malformed = [], 0
    pages = body.get('Pages') if isinstance(body, dict) else None
    for page in pages[:50] if isinstance(pages, list) else []:
        try:
            item = json.loads(page) if isinstance(page, str) else page
            parsed = http_url(item.get('url')) if isinstance(item, dict) else None
            if not parsed:
                malformed += 1
                continue
            items.append({'title': str(item.get('title', ''))[:300], 'url': item['url'],
                          'published_at': str(item.get('date', ''))[:100],
                          'summary': str(item.get('content') or item.get('passage') or '')[:2400],
                          'host': parsed.hostname.lower().rstrip('.')})
        except (ValueError, TypeError):
            malformed += 1
    return items, malformed


def _row(item, tier, status, name=None):
    row = {'title': item['title'], 'url': item['url'], 'published_at': item['published_at'],
           'summary': item['summary'], 'source_tier': tier, 'source_status': status}
    if name:
        row['source_name'] = name
    return row


def official_rows(items, is_official):
    """Keep only rows the caller's official predicate accepts; count the rest."""
    rows, rejected = [], 0
    for item in items:
        if not is_official(item['url']):
            rejected += 1
            continue
        rows.append(_row(item, 'official', 'official_domain_unverified_content'))
    return rows, rejected


def third_party_rows(items):
    rows, rejected = [], 0
    for item in items:
        name = next((label for root, label in THIRD_PARTY_SOURCES.items() if host_matches(item['host'], (root,))), None)
        if not name:
            rejected += 1
            continue
        rows.append(_row(item, 'third_party_reference', 'third_party_unverified_reference', name))
    return rows, rejected


def candidate_rows(items, unit_name, roots=()):
    """Label likely official pages when the unit has no verified official_url.

    The filter is deliberately conservative: the page title must name the
    unit, read like an admissions document and avoid commercial wording.
    Institutional suffixes score highest; two independent pages on the same
    host are the fallback for institutes that use a plain .com domain.
    Known search portals and third-party aggregators are never candidates,
    even when they republish several matching pages. Nothing here is official
    or evidence until a human or replay verifies the domain, so the tier stays
    `official_domain_candidate`.
    """
    name = re.sub(r'\s+', '', unicodedata.normalize('NFKC', unit_name or ''))
    if not name:
        return []
    grouped = {}
    for item in items:
        host = item['host']
        if host_matches(host, roots):
            continue
        if host_matches(host, CANDIDATE_BLOCKED_DOMAINS):
            continue
        if host_matches(host, tuple(THIRD_PARTY_SOURCES)):
            continue
        title = item['title']
        flat = re.sub(r'\s+', '', unicodedata.normalize('NFKC', title))
        if name not in flat or not any(word in flat for word in CANDIDATE_TITLE_MARKERS):
            continue
        if any(word in flat for word in CANDIDATE_COMMERCIAL_MARKERS):
            continue
        grouped.setdefault(host, []).append((_row(item, 'official_domain_candidate',
                                                  'official_domain_unverified_attribution', host),
                                             flat.startswith(name)))
    accepted = []
    for host, entries in grouped.items():
        score = 2 if host.endswith(CANDIDATE_INSTITUTIONAL_SUFFIXES) else 0
        score += 1 if len(entries) >= 2 else 0
        score += 1 if any(starts for _, starts in entries) else 0
        if score >= 2:
            accepted.append((score, len(entries), host, [row for row, _ in entries[:4]]))
    accepted.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [row for *_, rows in accepted[:4] for row in rows][:10]


def dedupe(rows):
    """Collapse repeated links; SearchPro sometimes returns one page twice."""
    seen, unique = set(), []
    for row in rows:
        key = row['url'].split('#', 1)[0]
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def search_variants(unit_name, year, kind):
    """Primary query plus one wider rewrite, tried only after a dead end.

    SearchPro is paid per call, so the ladder stops at the first query that
    yields an official page. Official sites often title a standing catalog
    by school and graduate school rather than by admission year, so the
    rewrite moves the year and names the graduate school explicitly.
    """
    primary = f"{year}年 {unit_name} {SEARCH_KINDS[kind]}"
    wider = f"{unit_name} 研究生院 {year} {SEARCH_KINDS[kind]}"
    return [primary] if wider == primary else [primary, wider]


def failure_stage(official, candidates, third, rejected, binding_missing):
    """Which stage actually failed, so a zero result is never over-claimed."""
    if official:
        return 'official_found'
    if candidates:
        return 'binding_missing' if binding_missing else 'attribution_unverified'
    if third:
        return 'third_party_only'
    if binding_missing:
        return 'binding_missing'
    if rejected:
        return 'domain_mismatch'
    return 'index_no_hit'


def notice(official, candidates, third, queries_count, *, binding_missing=False, domain_label='官方域名'):
    if official:
        text = '检索用于发现来源；官方域名结果仍需核对原文与适用年度，返回结果不是完整目录。'
        if third:
            text += ' 第三方结果只是线索参考，不能作为报考依据。'
        return text
    if candidates:
        hosts = ', '.join(sorted({row.get('source_name') for row in candidates if row.get('source_name')}))
        prefix = ('该校尚未登记可信官网域名，这不代表官网没有资料；'
                  if binding_missing else '未在已登记官方域名内找到结果；')
        text = (f'{prefix}发现 {len(candidates)} 条疑似官方页面'
                f'（{hosts}），域名归属尚未确认，仅供核对，不能作为报考依据。')
        if third:
            text += ' 另有第三方线索，见下方标注。'
        return text
    if third:
        text = '检索用于发现来源；第三方结果只是线索参考，不能作为报考依据。官方域名本次没有可展示来源。'
        if binding_missing:
            text = ('该校尚未登记可信官网域名，本次检索无法按官网归属过滤；这不代表官网没有资料。' + text)
        return text
    if binding_missing:
        return ('该校尚未登记可信官网域名，本次检索无法按官网归属过滤；这不代表官网没有资料。'
                '可提供招生单位官网地址，或稍后重试。')
    return (f'已在{domain_label}内尝试 {queries_count} 个检索词，本次没有可展示来源；'
            '这不代表资料不存在。可核对招生单位研究生院官网，或换用更具体的年份、专业后重试。')


def run_ladder(call, *, queries, is_official, unit_name='', roots=(), candidate_enabled=False,
               site='', include_third=False, collect_third=False, third_query=None,
               third_only_without_official=False, can_call=None):
    """Run the bounded query ladder and return tiered, deduplicated rows.

    ``call(body)`` performs one external request and returns a normalized
    response body. ``can_call()`` lets a caller stop before a paid request when
    its own budget is exhausted; ``budget_exhausted`` then stays visible in the
    result instead of being silently swallowed.

    ``collect_third`` keeps third-party rows already present in the responses
    the ladder paid for; it never issues an extra request. A caller that wants
    a dedicated lead query (the page's extended scope) sets ``include_third``
    and ``third_query`` instead.
    """
    def allowed():
        return can_call is None or can_call()

    queries_tried, candidate_pages, third_pages = [], [], []
    official, body = [], {'Pages': []}
    rejected = malformed = request_count = 0
    site_fallback = budget_exhausted = False
    results = []
    for index, query in enumerate(queries):
        if not allowed():
            budget_exhausted = True
            break
        queries_tried.append(query)
        request_body = {'Query': query}
        if site and index == 0:
            request_body['Site'] = site
        body = call(request_body)
        request_count += 1
        items, bad = parse_pages(body)
        results, request_rejected = official_rows(items, is_official)
        rejected += request_rejected
        malformed += bad
        if collect_third:
            third_pages.extend(third_party_rows(items)[0])
        if site and index == 0 and (not body.get('Pages') or not results):
            # SearchPro's Site filter matches exact indexed hosts, so a school
            # site can miss its own graduate subdomain, and a non-empty page
            # set can still contain only non-official hosts. Retry once without
            # the filter; official classification still applies below.
            if not allowed():
                budget_exhausted = True
                break
            fallback = call({'Query': query})
            request_count += 1
            fallback_items, fallback_bad = parse_pages(fallback)
            if candidate_enabled:
                candidate_pages.extend(candidate_rows(fallback_items, unit_name, roots))
            if collect_third:
                third_pages.extend(third_party_rows(fallback_items)[0])
            fallback_rows, fallback_rejected = official_rows(fallback_items, is_official)
            rejected += fallback_rejected
            malformed += fallback_bad
            if fallback_rows or (not body.get('Pages') and fallback.get('Pages')):
                body, site_fallback = fallback, True
                results = fallback_rows
        if candidate_enabled:
            candidate_pages.extend(candidate_rows(parse_pages(body)[0], unit_name, roots))
        results = dedupe(results)
        if results:
            break
    official_results = list(results)
    third = list(third_pages)
    wants_third = include_third and (not third_only_without_official or not official_results)
    if wants_third and third_query:
        if allowed():
            queries_tried.append(third_query)
            body = call({'Query': third_query})
            request_count += 1
            third_items, third_malformed = parse_pages(body)
            third, third_rejected = third_party_rows(third_items)
            rejected += third_rejected
            malformed += third_malformed
        else:
            budget_exhausted = True
    known = {row['url'].split('#', 1)[0] for row in official_results}
    candidates = []
    if candidate_enabled and not official_results:
        for row in dedupe(candidate_pages):
            key = row['url'].split('#', 1)[0]
            if key in known:
                continue
            known.add(key)
            candidates.append(row)
    third_results = []
    for row in dedupe(third):
        key = row['url'].split('#', 1)[0]
        if key in known:
            continue
        known.add(key)
        third_results.append(row)
    return {'queries_tried': queries_tried, 'official_results': official_results,
            'candidate_results': candidates, 'third_results': third_results,
            'results': [*official_results, *candidates, *third_results],
            'rejected_count': rejected, 'malformed_count': malformed,
            'site_fallback': site_fallback, 'request_count': request_count,
            'request_id': str(body.get('RequestId', ''))[:120],
            'budget_exhausted': budget_exhausted,
            'candidate_count': len(candidates),
            'candidate_hosts': sorted({row.get('source_name') for row in candidates if row.get('source_name')})}
