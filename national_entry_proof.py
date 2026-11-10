"""Replay official entry ownership from year-bound original bytes."""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from national_entry_html import (
    _resolve_web_target, _url_key, _visible_original, _visible_quote_spans,
)


SUPPORTED_ENTRY_VERIFICATIONS = frozenset({
    'replayed_from_saved_chsi_official_site_statement',
    'saved_visible_original_role_replayed',
    'saved_declaration_and_own_primary_title_replayed',
    'saved_original_exact_role_link',
    'linked_from_saved_unit_profile',
    'replayed_visible_declaration_and_own_title',
    'homepage_name_and_admissions_navigation',
    'saved_original_exact_link',
    'linked_from_saved_annual_charter',
    'saved_original_exact_catalog_link',
})


SUPPORTED_ENTRY_RELATIONS = frozenset({
    'official_home_link',
    'declared_official_site',
    'own_admissions_publication',
    'official_home_statement',
    'official_annual_catalog_link',
})


ENTRY_ROLE_CATEGORIES = frozenset({'home', 'admissions', 'catalog', 'labeled'})


_SELF_REFERENCE = re.compile(
    r'(?:本校|我校|本单位|我单位|本所|我所|本院|我院|学校|招生单位|单位|学院)')


_STRONG_SELF_REFERENCE = re.compile(
    r'(?:本校|我校|本单位|我单位|本所|我所|本院|我院)')


_ROLE_PATTERNS = (
    # A self-referential or institutional site declaration is the strongest
    # visible role evidence.  The label must be attached to the URL/link.
    ('home', re.compile(
        r'(?:本校|我校|本单位|我单位|本所|我所|本院|我院|学校|招生单位|单位|学院)'
        r'(?:官方网站|官网|官方网址|网站|网址|主页)')),
    ('home', re.compile(
        r'(?:研究生院|研究生处|研究生学院|研究生部|研究生教育部|党委研究生工作部|'
        r'研招办|招生办公室)[^。；\n]{0,20}?'
        r'(?:官方网站|官网|官方网址|网站|网址|主页|网)')),
    ('labeled', re.compile(
        r'([一-龥A-Za-z]{2,24})(?:官方网站|官网|官方网址|主页)')),
    ('admissions', re.compile(
        r'(?:招生信息网|招生网站|招生网址|招生网|招生官网|招生资讯网|'
        r'招生信息发布网址|研究生招生信息网址|研招网址|研招网|研招在线|'
        r'研究生招生网|研究生招生信息网|研究生招生专题网站|招生专题网站)')),
    ('catalog', re.compile(r'(?:招生专业目录|专业目录|招生简章|招生章程|附件)')),
    ('home', re.compile(r'(?:网站|网址|主页|官网)')),
)


_PLATFORM_LABEL = re.compile(
    r'(?:中国研究生招生信息网|全国硕士研究生招生信息公开平台|'
    r'中国高等教育学生信息网|学信网(?!址|站|页)|研招网(?!址|站|页))')


_ENTITY_LABEL = re.compile(
    r'[一-龥A-Za-z]{1,20}(?:大学|学院|学校|研究院|研究所|科学院)')


_GENERIC_ENTITY = frozenset({
    '学校', '学院', '大学', '研究生院', '研究生学院', '研究生部', '本校', '我校',
    '该校', '各学院', '各学校', '详见学校', '所属学院', '校内', '全校',
})


_GENERIC_ROLE_WORD = re.compile(
    r'研究生招生|研招|招生信息|招生办|研究生院|研究生工作')


_ENTITY_TAIL = re.compile(
    r'[一-龥A-Za-z]{1,20}(?:大学|学院|学校|研究院|研究所|科学院|'
    r'研究生院|研究生处|研究生部|党委研究生工作部)')


_NAME_LEAD_NOISE = frozenset(
    '的及或在将请见由可关注意登录考生查阅详获取咨询报名据了为与和等'
    '访问进入点击前往到从向对于是有无未不如若需应须参加选择填写提交'
    '打开看解更多全各本校我该通过')


SHA256_HEX = re.compile(r'^[0-9a-f]{64}$')


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _host(value):
    text = str(value or '').strip()
    if not text:
        return ''
    if '://' not in text:
        text = 'http://' + text
    try:
        return (urlsplit(text).hostname or '').lower()
    except ValueError:
        return ''


def _entry_fingerprint(row):
    """Bind the persisted proof to the exact row evidence it verified."""
    evidence = row.get('official_url_evidence')
    proof = row.get('entry_verification')
    evidence = evidence if isinstance(evidence, dict) else {}
    proof = proof if isinstance(proof, dict) else {}
    payload = {
        'unit_id': str(row.get('unit_id') or ''),
        'year': str(row.get('year') or ''),
        'official_url': str(row.get('official_url') or ''),
        'evidence_url': str(evidence.get('url') or ''),
        'evidence_sha256': str(evidence.get('sha256') or ''),
        'evidence_quote': str(evidence.get('quote') or ''),
        'evidence_linked_url': str(evidence.get('linked_url') or ''),
        'evidence_relation': str(evidence.get('relation') or ''),
        'evidence_verification': str(evidence.get('verification') or ''),
        'proof_unit_id': str(proof.get('unit_id') or ''),
        'proof_year': str(proof.get('year') or ''),
        'proof_official_url': str(proof.get('official_url') or ''),
        'proof_document_id': str(proof.get('document_id') or ''),
        'proof_document_sha256': str(proof.get('document_sha256') or ''),
        'proof_file_sha256': str(proof.get('file_sha256') or ''),
        'proof_document_path': str(proof.get('document_path') or ''),
        'proof_quote_match': str(proof.get('quote_match') or ''),
        'proof_role_category': str(proof.get('role_category') or ''),
        'proof_role_evidence': str(proof.get('role_evidence') or ''),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def entry_trusted(row):
    """An official entry is usable only with a replayed saved-original proof.

    The selector consumes the proof record produced by :func:`verify_entry`.
    It does not trust a bare URL, a metadata string, or a manually set flag:
    the proof fingerprint must equal the fingerprint of the evidence actually
    stored on this row.
    """
    gaps = row.get('gaps')
    if isinstance(gaps, list) and 'missing_trusted_entry' in gaps:
        return False
    url = str(row.get('official_url') or '').strip()
    if not url or '尚未核准' in str(row.get('missing_reason') or ''):
        return False
    evidence = row.get('official_url_evidence')
    proof = row.get('entry_verification')
    if not isinstance(evidence, dict) or not isinstance(proof, dict):
        return False
    if proof.get('status') != 'verified':
        return False
    if str(evidence.get('verification') or '') not in SUPPORTED_ENTRY_VERIFICATIONS:
        return False
    if str(evidence.get('relation') or '') not in SUPPORTED_ENTRY_RELATIONS:
        return False
    if str(proof.get('unit_id') or '') != str(row.get('unit_id') or ''):
        return False
    if str(proof.get('year') or '') != str(row.get('year') or ''):
        return False
    if str(proof.get('official_url') or '').strip() != url:
        return False
    if _host(evidence.get('quote')) != _host(url) or not _host(url):
        return False
    if proof.get('role_category') not in ENTRY_ROLE_CATEGORIES:
        return False
    if not str(proof.get('role_evidence') or '').strip():
        return False
    if not SHA256_HEX.match(str(proof.get('proof_sha256') or '')):
        return False
    return _entry_fingerprint(row) == proof.get('proof_sha256')


def _document_matches(record, evidence):
    """Find the annual document entry with the exact URL and SHA declared."""
    for item in record.get('evidence') or []:
        if not isinstance(item, dict):
            continue
        if (item.get('url') == evidence.get('url')
                and str(item.get('sha256') or '').lower()
                == str(evidence.get('sha256') or '').lower()):
            return item
    return None


def _identity_matches(record, document, evidence_url):
    """The saved document must belong to this unit, not just any unit."""
    unit_id = str(record.get('unit_id') or '')
    unit_code = str(record.get('unit_code') or '')
    fields = document.get('structured_fields')
    if isinstance(fields, dict):
        if unit_code and str(fields.get('dwdm') or '') == unit_code:
            return True
        if unit_id and str(fields.get('schId') or '') == unit_id:
            return True
    try:
        query = parse_qs(urlsplit(str(evidence_url or '')).query)
    except ValueError:
        query = {}
    if unit_code and (query.get('schCode') or [''])[0] == unit_code:
        return True
    if unit_id and f'schId-{unit_id}' in str(evidence_url or ''):
        return True
    return False


def _saved_original(data_dir, unit_id, year, sha256, document_id):
    """Locate the saved original and bind its filename to the annual document."""
    folder = Path(data_dir) / 'snapshots' / 'national' / str(year) / str(unit_id)
    if not folder.is_dir():
        return None, 'saved_original_missing'
    prefix = str(sha256 or '').lower()[:16]
    candidates = sorted(path for path in folder.glob('*.html')
                        if prefix and prefix in path.name.lower())
    if not candidates:
        return None, 'saved_original_missing'
    if len(candidates) != 1:
        return None, 'ambiguous_saved_original'
    path = candidates[0]
    parts = str(document_id or '').split('-')
    if len(parts) < 3:
        return None, 'document_id_unbound'
    bound = parts[-2].lower() + '-' + parts[-1].lower()
    if not path.name.lower().startswith(bound):
        return None, 'saved_original_unbound'
    return path, ''


def _longest_common_substring(left, right):
    left = str(left or '')
    right = str(right or '')
    best = ''
    for start in range(len(left)):
        for end in range(start + len(best) + 1, len(left) + 1):
            piece = left[start:end]
            if piece not in right:
                break
            best = piece
    return best


def _foreign_name_in(span, unit_name, other_names):
    """Find another known unit's exact name in a tight role span.

    School-name patterns over free text produce false positives such as
    "详见学校" or "研究生学院".  The registry already contains every unit
    name, so ownership is checked against those exact names instead.
    """
    unit_name = str(unit_name or '')
    for name in other_names or ():
        name = str(name or '')
        if not name or name == unit_name:
            continue
        if unit_name and (name in unit_name or unit_name in name):
            continue
        if name in span:
            return name
    return ''


def _foreign_label_in(label, unit_name):
    """Entity-like name in a site label that the current unit does not own.

    Applied only to the captured label of a ``labeled`` role, never to a
    free-text window: Chinese word boundaries are unavailable, so scanning
    arbitrary preceding words produces false names such as "考生关注学校".
    """
    for match in _ENTITY_LABEL.finditer(str(label or '')):
        name = _nearest_entity(match.group(0), unit_name)
        if not name or name in _GENERIC_ENTITY or name.startswith('各'):
            continue
        if not _entity_owned(name, unit_name):
            return name
    return ''


def _entity_owned(name, unit_name):
    """An explicit institution name needs identity, not shared name fragments."""
    name = re.sub(r'\s+', '', str(name or ''))
    unit_name = re.sub(r'\s+', '', str(unit_name or ''))
    if name and name == unit_name:
        return True
    departments = {'研究生院', '研究生学院', '研究生处', '研究生部', '研招办', '招生办公室'}
    return bool(unit_name and name.startswith(unit_name)
                and name[len(unit_name):] in departments)


def _nearest_entity(text, unit_name=''):
    """Take the institution next to a role after punctuation and prose guides."""
    text = str(text or '').rstrip(' 　:：,，、;；。.!！?？-—·（([的“”‘’"\'')
    parts = re.split(r'[\s，,；;。:：!?！？、（）()\[\]“”‘’"\']+', text)
    candidate = parts[-1] if parts else ''
    if _entity_owned(candidate, unit_name):
        return candidate
    guides = list(re.finditer(
        r'访问|登录|登陆|进入|浏览|查阅|查看|详见|参见|请见|^见|前往|打开|关注|咨询|'
        r'均可在|将在|均在|可在|公布于|发布于|刊登于|'
        r'链接(?:至|到)|跳转(?:至|到)', candidate))
    if guides:
        candidate = candidate[guides[-1].end():]
    if _entity_owned(candidate, unit_name):
        return candidate
    candidate = re.sub(r'^(?:及|和|与)+', '', candidate)
    candidate = re.sub(r'^(?:(?:考生(?:可以|可|应|须|需)?|请|可以|可|应|须|需|由|通过))+',
                       '', candidate)
    return candidate if _ENTITY_TAIL.fullmatch(candidate) else ''


def _foreign_entity_before(candidate, role_start, unit_name):
    """Find an explicit other-unit owner immediately before a role word.

    ``role_start`` is the offset of the role label ("网站", "研究生院网站",
    ...) in ``candidate``. Prose guides and punctuation delimit the nearest
    institution. An earlier occurrence of the current unit cannot override
    that explicit owner; generic guidance such as "考生可登录学校" remains
    distinct from an explicit foreign institution.
    """
    name = _nearest_entity(candidate[max(0, role_start - 80):role_start], unit_name)
    if not name or name in _GENERIC_ENTITY or _entity_owned(name, unit_name):
        return ''
    return name


def _role_evidence(text, target_start, target_end, unit_name, other_names=None):
    """Replay a visible role declaration attached to the URL/link.

    Returns ``(role, '')`` when a school-own site role is declared, or
    ``(None, reason)``.  A URL that merely appears in the article, in a
    comment/script, or under another organisation's label is not a role.
    """
    prefix = text[max(0, target_start - 120):target_start]
    cut = max(prefix.rfind('。'), prefix.rfind('\n'))
    if cut >= 0:
        prefix = prefix[cut + 1:]
    target_text = text[target_start:target_end]
    candidate = prefix + target_text
    matches = []
    for priority, (category, pattern) in enumerate(_ROLE_PATTERNS):
        for match in pattern.finditer(candidate):
            matches.append((priority, -match.end(), match.start(),
                            match.end(), match.group(0), category))
    if not matches:
        return None, 'role_declaration_missing'
    target_position = len(prefix)
    distances = [max(0, target_position - item[3]) for item in matches]
    nearest_distance = min(distances)
    nearby = [item for item, distance in zip(matches, distances)
              if distance <= nearest_distance + 2]
    _, _, start, end, role_text, category = min(
        nearby, key=lambda item: (item[0], max(0, target_position - item[3])))
    window = candidate[max(0, start - 24):end]
    tight_window = candidate[max(0, start - 16):end]
    platform = _PLATFORM_LABEL.search(tight_window)
    if platform and not _SELF_REFERENCE.search(tight_window):
        return None, 'platform_not_official_site'
    context = text[max(0, target_start - 220):min(len(text), target_end + 40)]
    # Ownership is decided near the declaration.  A unit name elsewhere in
    # the article does not make an unrelated site belong to this unit.
    own = bool(unit_name and unit_name in context)
    selfref = bool(_SELF_REFERENCE.search(window)
                   or _STRONG_SELF_REFERENCE.search(context))
    overlap = _longest_common_substring(context, unit_name or '')
    label = role_text
    if category == 'labeled':
        label_match = re.match(r'([一-龥A-Za-z]{2,24})', role_text)
        label = label_match.group(1) if label_match else role_text
        owned = (bool(_SELF_REFERENCE.search(label))
                 or bool(unit_name and unit_name in label)
                 or len(_longest_common_substring(label, unit_name or '')) >= 3
                 or bool(_GENERIC_ROLE_WORD.search(label))
                 or bool(unit_name and unit_name in context))
    elif category == 'home':
        institutional = re.match(
            r'(?:研究生院|研究生处|研究生学院|研究生部|研究生教育部|'
            r'党委研究生工作部|研招办|招生办公室)', role_text)
        owned = bool(institutional) or selfref or own or len(overlap) >= 3
    elif category in ('admissions', 'catalog'):
        # Admissions/catalog labels are generic role words of the unit's own
        # annual charter; an explicit other-unit owner is rejected below.
        owned = True
    else:
        owned = selfref or own or len(overlap) >= 3
    foreign = _foreign_name_in(window, unit_name, other_names)
    if not foreign:
        foreign = _foreign_entity_before(candidate, start, unit_name)
    if not foreign and category == 'labeled':
        foreign = _foreign_label_in(label, unit_name)
    if foreign:
        return None, 'foreign_unit_reference'
    if not owned:
        return None, 'role_ownership_missing'
    return {'category': category, 'evidence': role_text}, ''


def _relation_accepts(relation, category):
    if relation == 'own_admissions_publication':
        return category in {'home', 'admissions', 'labeled', 'catalog'}
    if category == 'catalog':
        return relation == 'official_annual_catalog_link'
    return relation in {
        'official_home_link', 'declared_official_site',
        'own_admissions_publication', 'official_home_statement'}


def _enclosing_anchor(anchors, position):
    found = None
    for start, end, href in anchors:
        if start <= position < end:
            if found is None or start >= found[0]:
                found = (start, end, href)
    return found


def _pick_entry_failure(reasons):
    for reason in ('relation_role_mismatch', 'foreign_unit_reference',
                   'platform_not_official_site', 'role_ownership_missing',
                   'unresolved_link_target', 'link_target_mismatch',
                   'role_declaration_missing'):
        if reason in reasons:
            return reason
    return 'role_declaration_missing'


def _replay_entry_role(path, evidence, official_url, unit_id, unit_name,
                       known_hosts=None, known_names=None):
    """Prove that the saved original visibly declares this school's site.

    The check is structural and semantic, not a domain allow/deny list:
    comments, scripts, page chrome and hidden controls are excluded; the
    declared URL must be visible text or the real target of a visible link;
    relative links are resolved against the saved original's URL; the role
    label must belong to this unit; and the role category must match the
    stored relation.
    """
    quote = str(evidence.get('quote') or '').strip()
    linked = str(evidence.get('linked_url') or '').strip()
    relation = str(evidence.get('relation') or '')
    base_url = str(evidence.get('url') or '')
    entry_host = _host(official_url)
    if not quote or not entry_host:
        return None, 'quote_not_in_original'
    if linked and _url_key(linked) != _url_key(quote):
        return None, 'linked_target_mismatch'
    owners = (known_hosts or {}).get(entry_host) or set()
    if owners and unit_id not in owners:
        return None, 'other_unit_host'
    text, anchors = _visible_original(path)
    if not text.strip():
        return None, 'visible_article_missing'
    expected_key = _url_key(linked or quote)
    candidates = []
    for index, end in _visible_quote_spans(text, quote):
        visible_key = _url_key(text[index:end])
        inside = _enclosing_anchor(anchors, index)
        if inside:
            resolved = _resolve_web_target(inside[2], base_url)
            if resolved is None:
                candidates.append((index, end, False, 'literal',
                                   'unresolved_link_target'))
                continue
            target_ok = (_host(resolved) == entry_host
                         and _url_key(resolved) == expected_key)
        else:
            # The complete visible URL token is the target; two metadata
            # strings agreeing with each other cannot substitute for it.
            target_ok = (visible_key == expected_key
                         and visible_key[0] == entry_host)
        candidates.append((index, end, target_ok, 'literal', 'link_target_mismatch'))
    for start, end, href in anchors:
        resolved = _resolve_web_target(href, base_url)
        if resolved is None:
            continue
        if _host(resolved) != entry_host:
            continue
        if _url_key(resolved) != expected_key:
            continue
        candidates.append((start, end, True, 'link_target', ''))
    if not candidates:
        return None, 'quote_not_in_original'
    failures = []
    for start, end, target_ok, quote_match, failure in candidates:
        if not target_ok:
            failures.append(failure)
            continue
        role, reason = _role_evidence(text, start, end, unit_name, known_names)
        if role is None:
            failures.append(reason or 'role_declaration_missing')
            continue
        if not _relation_accepts(relation, role['category']):
            failures.append('relation_role_mismatch')
            continue
        return {**role, 'quote_match': quote_match}, ''
    return None, _pick_entry_failure(failures)


def _quote_match(path, quote):
    """The ownership quote must literally occur in visible article text."""
    quote = str(quote or '').strip()
    if not quote:
        return ''
    text, _ = _visible_original(path)
    return 'literal' if _visible_quote_spans(text, quote) else ''


def verify_entry(unit, record, data_dir, known_hosts=None, known_names=None):
    """Replay one official entry against the real saved original.

    The proof is accepted only when every link in the chain holds: supported
    verification/relation metadata, exact annual document URL+SHA, unit
    identity, a saved file bound to that document, matching file bytes, a
    visible article-level role declaration for this unit, and a real link
    target that matches the claimed evidence. Any missing or mismatched link
    yields a persisted rejection reason instead of a silent pass.
    """
    unit_id = str(record.get('unit_id') or '')
    unit_name = str(unit.get('name') or unit.get('unit_name') or '').strip()
    official_url = str(unit.get('official_url') or '').strip()
    proof = {'status': 'rejected', 'reason': '', 'unit_id': unit_id,
             'year': record.get('year'), 'official_url': official_url,
             'evidence_url': '', 'evidence_sha256': '', 'document_id': '',
             'document_sha256': '', 'document_path': '', 'file_sha256': '',
             'quote_match': '', 'role_category': '', 'role_evidence': ''}
    evidence = unit.get('official_url_evidence')
    if not isinstance(evidence, dict):
        proof['reason'] = 'no_entry_evidence'
        return proof
    proof['evidence_url'] = str(evidence.get('url') or '')
    proof['evidence_sha256'] = str(evidence.get('sha256') or '').lower()
    if str(evidence.get('verification') or '') not in SUPPORTED_ENTRY_VERIFICATIONS:
        proof['reason'] = 'unsupported_verification'
        return proof
    if str(evidence.get('relation') or '') not in SUPPORTED_ENTRY_RELATIONS:
        proof['reason'] = 'unsupported_relation'
        return proof
    entry_host = _host(official_url)
    if not entry_host:
        proof['reason'] = 'missing_official_url'
        return proof
    if _host(evidence.get('quote')) != entry_host:
        proof['reason'] = 'quote_host_mismatch'
        return proof
    linked = str(evidence.get('linked_url') or '').strip()
    if linked and _host(linked) != entry_host:
        proof['reason'] = 'linked_host_mismatch'
        return proof
    if not SHA256_HEX.match(proof['evidence_sha256']):
        proof['reason'] = 'invalid_evidence_sha256'
        return proof
    document = _document_matches(record, evidence)
    if document is None:
        proof['reason'] = 'no_exact_annual_document'
        return proof
    if document.get('kind') != 'annual_master_charter':
        proof['reason'] = 'unsupported_document_kind'
        return proof
    if str(document.get('sha256') or '').lower() != proof['evidence_sha256']:
        proof['reason'] = 'document_sha_mismatch'
        return proof
    proof['document_id'] = str(document.get('document_id') or '')
    proof['document_sha256'] = proof['evidence_sha256']
    if not _identity_matches(record, document, proof['evidence_url']):
        proof['reason'] = 'unit_identity_mismatch'
        return proof
    path, reason = _saved_original(data_dir, unit_id, record.get('year'),
                                   proof['evidence_sha256'], proof['document_id'])
    if path is None:
        proof['reason'] = reason or 'saved_original_missing'
        return proof
    try:
        proof['document_path'] = (path.resolve().relative_to(Path(data_dir).resolve())
                                  .as_posix())
    except ValueError:
        proof['document_path'] = path.as_posix()
    actual = sha256_file(path)
    proof['file_sha256'] = actual
    if actual != proof['evidence_sha256']:
        proof['reason'] = 'saved_original_sha_mismatch'
        return proof
    role, reason = _replay_entry_role(
        path, evidence, official_url, unit_id, unit_name, known_hosts, known_names)
    if role is None:
        proof['reason'] = reason or 'role_declaration_missing'
        return proof
    proof['quote_match'] = role['quote_match']
    proof['role_category'] = role['category']
    proof['role_evidence'] = role['evidence']
    proof['status'] = 'verified'
    proof['reason'] = 'saved_original_replayed'
    return proof


def _official_hosts(registry):
    """Map each known official host to the registry units that claim it."""
    hosts = {}
    for unit in registry.get('units', []):
        if not isinstance(unit, dict):
            continue
        unit_id = unit.get('id')
        host = _host(unit.get('official_url'))
        if isinstance(unit_id, str) and unit_id and host:
            hosts.setdefault(host, set()).add(unit_id)
    return hosts


def verify_entries(registry, annual, data_dir):
    """Build one persisted proof record per confirmed annual unit."""
    units = {unit['id']: unit for unit in registry.get('units', [])
             if isinstance(unit, dict) and isinstance(unit.get('id'), str)}
    known_hosts = _official_hosts(registry)
    known_names = {str(unit.get('name') or '').strip()
                   for unit in registry.get('units', [])
                   if isinstance(unit, dict) and str(unit.get('name') or '').strip()}
    proofs = {}
    for record in annual.get('units', []):
        if record.get('annual_status') != 'confirmed' or type(record.get('year')) is not int:
            continue
        unit_id = record.get('unit_id')
        if not isinstance(unit_id, str) or not unit_id:
            continue
        proofs[unit_id] = verify_entry(
            units.get(unit_id, {}), record, data_dir, known_hosts, known_names)
    return proofs
