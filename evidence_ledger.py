"""Deterministic evidence coverage and source-independence audit.

The audit answers two narrow questions about the evidence a research report
already carries:

* Does this claim actually have a public URL and a verbatim quote?
* Do two cited documents share the same quote text (copying) so they should
  not be counted as two independent confirmations?

It does not decide whether an admission condition is satisfied, and it never
turns an unknown into a match. Independence is a heuristic over document ids
and exact normalized quotes, not a claim of editorial independence.
"""
import re
from urllib.parse import urlsplit


VERSION = 'evidence_ledger_v1'
MAX_CLAIMS = 96
MAX_CLUSTERS = 20
_QUOTE = re.compile(r'[\W_]+')


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _url(value):
    if not isinstance(value, str):
        return ''
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ''
    return value if parsed.scheme in {'http', 'https'} and parsed.hostname else ''


def _domain(url):
    try:
        return urlsplit(url).hostname.lower().rstrip('.') if url else ''
    except ValueError:
        return ''


def _quote_fingerprint(quote):
    return _QUOTE.sub('', quote.lower())


def _locator(ref):
    page = ref.get('page')
    locator = ref.get('source_locator')
    if isinstance(page, (int, str)) and str(page).strip():
        return True
    return isinstance(locator, dict) and bool(locator)


def _reference(ref):
    if not isinstance(ref, dict):
        return None
    url = _url(ref.get('url'))
    quote = _text(ref.get('quote'), 2000)
    if not url or not quote:
        return None
    document = _text(ref.get('document_id'), 240) or url
    return {'document': document, 'url': url, 'domain': _domain(url),
            'quote': quote, 'fingerprint': _quote_fingerprint(quote),
            'locator': _locator(ref)}


def _components(refs):
    """Count document groups not joined by an identical quote."""
    parent = {}

    def find(item):
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left, right):
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    documents = {ref['document'] for ref in refs}
    by_quote = {}
    for ref in refs:
        if ref['fingerprint']:
            by_quote.setdefault(ref['fingerprint'], set()).add(ref['document'])
    for group in by_quote.values():
        group = sorted(group)
        for item in group[1:]:
            union(group[0], item)
    return len({find(document) for document in documents})


def _claim(row, reason):
    refs = [ref for ref in (_reference(item) for item in reason.get('evidence') or []) if ref]
    documents = sorted({ref['document'] for ref in refs})
    domains = sorted({ref['domain'] for ref in refs if ref['domain']})
    independent = _components(refs) if refs else 0
    if independent >= 2:
        assessment = 'corroborated'
    elif independent == 1:
        assessment = 'single_source'
    else:
        assessment = 'unsourced'
    return {'candidate_id': row.get('candidate_id'), 'unit_name': _text(row.get('unit_name'), 120),
            'candidate_status': row.get('status'), 'field': _text(reason.get('field'), 60),
            'label': _text(reason.get('label'), 80), 'status': reason.get('status'),
            'evidence_refs': len(refs), 'independent_sources': independent,
            'documents': documents, 'domains': domains,
            'quoted_refs': len(refs), 'locator_refs': sum(ref['locator'] for ref in refs),
            'assessment': assessment}


def _clusters(refs):
    by_quote = {}
    for ref in refs:
        if ref['fingerprint']:
            by_quote.setdefault(ref['fingerprint'], []).append(ref)
    clusters = []
    for group in by_quote.values():
        documents = sorted({ref['document'] for ref in group})
        if len(documents) < 2:
            continue
        urls = sorted({ref['url'] for ref in group})
        clusters.append({'quote_preview': group[0]['quote'][:120], 'documents': documents,
                         'urls': urls[:8], 'document_count': len(documents)})
    clusters.sort(key=lambda row: (-row['document_count'], row['quote_preview']))
    return clusters


def audit(decisions):
    """Return claim-level coverage and exact-quote duplicate clusters."""
    if not isinstance(decisions, list):
        decisions = []
    claims, all_refs = [], []
    for row in decisions:
        if not isinstance(row, dict):
            continue
        for reason in row.get('reasons') or []:
            if not isinstance(reason, dict):
                continue
            claim = _claim(row, reason)
            claims.append(claim)
            all_refs.extend(ref for ref in (_reference(item) for item in reason.get('evidence') or []) if ref)
    total = len(claims)
    counts = {name: sum(claim['assessment'] == name for claim in claims)
              for name in ('corroborated', 'single_source', 'unsourced')}
    sourced = counts['corroborated'] + counts['single_source']
    clusters = _clusters(all_refs)
    return {'version': VERSION, 'claims': claims[:MAX_CLAIMS],
            'summary': {'claim_count': total, 'sourced_claims': sourced,
                        'corroborated_claims': counts['corroborated'],
                        'single_source_claims': counts['single_source'],
                        'unsourced_claims': counts['unsourced'],
                        'coverage_ratio': round(sourced / total, 4) if total else 0.0,
                        'corroboration_ratio': round(counts['corroborated'] / total, 4) if total else 0.0,
                        'evidence_refs': len(all_refs),
                        'duplicate_quote_clusters': len(clusters)},
            'duplicate_clusters': clusters[:MAX_CLUSTERS],
            'note': ('覆盖率只说明理由是否带有可回到原文的链接与逐字引文；'
                     '重复引文检测只识别完全相同文本，不证明来源彼此独立或结论为真。'
                     '待确认理由不会因为带有引文就升级为明确匹配。')}
