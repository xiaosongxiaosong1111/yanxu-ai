"""Year-bound admissions-unit evidence; the general school index is not a census."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .documents import PROJECT_ROOT, decode_text
from .history import _Tables
from public_sources import official_source_url
from service_verification import canonical, plain_text


PROVINCES = dict(zip('11 12 13 14 15 21 22 23 31 32 33 34 35 36 37 41 42 43 44 45 46 50 51 52 53 54 61 62 63 64 65'.split(),
    '北京 天津 河北 山西 内蒙古 辽宁 吉林 黑龙江 上海 江苏 浙江 安徽 福建 江西 山东 河南 湖北 湖南 广东 广西 海南 重庆 四川 贵州 云南 西藏 陕西 甘肃 青海 宁夏 新疆'.split()))
CONTEXT_URL = 'https://yz.chsi.com.cn/zsml/dw.do'
QUERY_URL = 'https://yz.chsi.com.cn/zsml/rs/dws.do'
PROVINCE_LISTS = [('33', 'https://www.zjzs.net/art/2026/10/8/art_155_12662.html')]
MAX_BYTES = 2 * 1024 * 1024


class AnnualRegistryError(ValueError):
    pass


def directory_context(html, year=2027):
    match = re.search(r'\bcurYear\s*:\s*["\'](\d{4})["\']', html)
    if not match or int(match.group(1)) != year or '硕士专业目录' not in plain_text(html):
        raise AnnualRegistryError('公开硕士目录页面没有可核对的目标年度')
    return {'year': year, 'login_limited': bool(re.search(r'登录.{0,8}后.{0,8}查看更多', plain_text(html)))}


def parse_public_units(payload, province, context, year=2027):
    if context.get('year') != year or province not in PROVINCES:
        raise AnnualRegistryError('目录年度或省份上下文不正确')
    msg = payload.get('msg') if isinstance(payload, dict) and payload.get('flag') is True else None
    if not isinstance(msg, dict) or msg.get('curPage') != 1 or not isinstance(msg.get('list'), list):
        raise AnnualRegistryError('仅接收无需登录的普通省份查询首屏，不绕过分页登录限制')
    total, size, page_size = msg.get('totalCount'), msg.get('size'), msg.get('pageCount')
    if (type(total) is not int or type(size) is not int or type(page_size) is not int
            or total < 0 or not 0 < page_size <= 20 or not 0 <= size <= page_size
            or len(msg['list']) != min(total, page_size)):
        raise AnnualRegistryError('公开目录数量声明与返回记录不一致')
    units, seen = [], set()
    for row in msg['list']:
        if (not isinstance(row, dict) or not re.fullmatch(r'\d{5}', str(row.get('dwdm', '')))
                or not re.fullmatch(r'\d+', str(row.get('schId', ''))) or not isinstance(row.get('dwmc'), str)
                or not row['dwmc'].strip() or row.get('szssm') != province or row['dwdm'] in seen):
            raise AnnualRegistryError('公开目录单位身份、所在地或唯一性核验失败')
        seen.add(row['dwdm'])
        units.append({'unit_id': row['schId'], 'unit_code': row['dwdm'], 'unit_name': row['dwmc'].strip(),
            'province': PROVINCES[province], 'year': year, 'annual_status': 'confirmed',
            'evidence_kind': 'current_master_catalog_entry',
            'structured_fields': {field: row[field] for field in ('schId', 'dwdm', 'dwmc', 'szssm')},
            'notes': ['依据公开原始JSON字段；这些字段不是逐字正文引语。']})
    return {'province_code': province, 'province': PROVINCES[province], 'reported_total': total,
            'observed_count': len(units), 'complete': total == len(units), 'units': units,
            'notes': ['接口声明数量不等于已核验全集；仅保存公开查询首屏，未请求登录后分页。']}


def parse_province_units(html, province, year=2027):
    if province not in PROVINCES:
        raise AnnualRegistryError('未知省份')
    titles = re.findall(r'<(?:title|h[1-6])\b[^>]*>(.*?)</(?:title|h[1-6])>', html, re.S | re.I)
    target = PROVINCES[province] + '省' if province not in {'11', '12', '31', '50', '15', '45', '54', '64', '65'} else PROVINCES[province]
    matching = [plain_text(t) for t in titles if re.search(re.escape(target) + r'.*' + str(year) + r'年硕士研究生招生单位', plain_text(t))]
    if not matching:
        raise AnnualRegistryError('省级公告标题未证明目标年度硕士招生单位名单')
    parser = _Tables(); parser.feed(html)
    units, seen = [], set()
    for table in parser.tables:
        columns = None
        for row in table:
            clean = [canonical(cell) for cell in row]
            if '单位代码' in clean and '单位名称' in clean:
                columns = (clean.index('单位代码'), clean.index('单位名称'))
                continue
            if columns is None or not any(value.strip() for value in row):
                continue
            if max(columns) >= len(row):
                raise AnnualRegistryError('省级名单存在列缺失，不能声称已完整解析')
            code, name = (row[index].strip() for index in columns)
            if not re.fullmatch(r'\d{5}', code) or code in seen or not name:
                raise AnnualRegistryError('省级名单单位代码无效、重复或名称缺失')
            seen.add(code)
            units.append({'unit_id': None, 'unit_code': code, 'unit_name': name, 'province': PROVINCES[province],
                'year': year, 'annual_status': 'confirmed', 'evidence_kind': 'provincial_master_unit_list',
                'quote': code + ' ' + name})
    if not units:
        raise AnnualRegistryError('省级名单未解析出明确单位代码列，不能把报考点表当招生单位表')
    return {'province_code': province, 'province': PROVINCES[province], 'reported_total': len(units),
            'observed_count': len(units), 'complete': True, 'units': units, 'title': matching[0],
            'notes': ['仅覆盖该省公告明确列出的硕士招生单位，不能外推全国总数。']}


def parse_exact_query(payload, unit, province, context, year=2027):
    parsed = parse_public_units(payload, province, context, year)
    matches = [row for row in parsed['units'] if row['unit_id'] == str(unit['id'])
               and canonical(row['unit_name']) == canonical(unit['name'])
               and (not re.fullmatch(r'\d{5}', str(unit.get('unit_code') or '')) or row['unit_code'] == unit['unit_code'])]
    status = 'found' if len(matches) == parsed['reported_total'] == 1 else 'not_listed' if parsed['reported_total'] == 0 else 'identity_conflict'
    return {**parsed, 'query_kind': 'exact_unit', 'index_unit_id': str(unit['id']),
            'match_status': status, 'complete': False,
            'notes': ['仅为普通公开招生单位名称或代码搜索首屏；0结果表示当年查询未列入，不推定只招博士。']}


def verify_charter(document, unit, project_root=PROJECT_ROOT, year=2027):
    """Only exact saved bytes and an annual master charter title can confirm a unit."""
    try:
        root = Path(project_root).resolve()
        path = (root / document['local_path']).resolve()
        if not path.is_relative_to(root/'data') or not path.is_file():
            return None
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != document.get('sha256'):
            return None
        url = urlsplit(document['url'])
        if url.scheme != 'https' or not official_source_url(document['url'], unit):
            return None
        html = decode_text(raw, document.get('content_type', ''))
        headings = re.findall(r'<(?:title|h[1-6])\b[^>]*>(.*?)</(?:title|h[1-6])>', html, re.S | re.I)
        headings += re.findall(r'<div\b[^>]*class=["\']article-title["\'][^>]*>(.*?)</div>', html, re.S | re.I)
        pattern = re.escape(canonical(unit['name'])) + str(year) + r'年(?:全日制|非全日制)?硕士研究生招生(?:章程|简章)'
        title = next((plain_text(value) for value in headings if re.search(pattern, canonical(plain_text(value)))), None)
        if not title:
            return None
        return {'unit_id': str(unit['id']), 'unit_code': unit.get('unit_code'), 'unit_name': unit['name'],
                'province': unit.get('province'), 'year': year, 'annual_status': 'confirmed',
                'evidence_kind': 'annual_master_charter', 'quote': title}
    except (OSError, KeyError, ValueError, TypeError, AttributeError):
        return None


def _evidence(row, document):
    value = {'document_id': document.get('id'), 'url': document.get('url'), 'sha256': document.get('sha256'),
             'kind': row['evidence_kind']}
    return {**value, 'quote': row['quote']} if row.get('quote') else {**value, 'structured_fields': row['structured_fields']}


def _identity(row, units, by_code, by_name):
    identity = row.get('unit_id') or by_code.get(row['unit_code']) or by_name.get(canonical(row['unit_name'])) or 'code:' + row['unit_code']
    old = units.get(identity)
    if row.get('unit_id') and by_code.get(row['unit_code']) not in (None, identity):
        return identity, '同一招生单位代码指向不同研招网学校标识'
    if old and canonical(old['unit_name']) != canonical(row['unit_name']):
        return identity, '学校名称不一致，缺少可核验的更名依据'
    if old and re.fullmatch(r'\d{5}', str(old.get('unit_code') or '')) and old['unit_code'] != row['unit_code']:
        return identity, '同一研招网学校标识对应的五位招生单位代码冲突'
    return identity, None


def _exact_outcomes(units, sources):
    result = {**units}
    for source in sources:
        if source.get('query_kind') != 'exact_unit' or source.get('match_status') != 'not_listed':
            continue
        identity = source['index_unit_id']
        if identity not in result:
            continue
        old = result[identity]
        status = 'conflict' if old['annual_status'] == 'confirmed' else 'not_listed'
        evidence = {'document_id': source['document']['id'], 'url': source['document'].get('url'),
            'sha256': source['document'].get('sha256'), 'kind': 'current_master_catalog_exact_query',
            'structured_fields': {'index_unit_id': identity, 'totalCount': 0}}
        result[identity] = {**old, 'annual_status': status, 'evidence': [*old.get('evidence', []), evidence],
            'notes': ['该单位普通公开年度硕士目录精确查询返回0；未据此推定博士only或停招原因。']}
    return result


def _reconciliation(units, provinces, conflicts):
    declarations = {}
    for source in provinces:
        code = source['province_code']
        declarations[code] = {*declarations.get(code, set()), source['reported_total']}
    expected = {code: next(iter(counts)) if len(counts) == 1 else None for code, counts in declarations.items()}
    observed = {province: set() for province in PROVINCES.values()}
    confirmed = {row['unit_code'] for row in units.values() if row['annual_status'] == 'confirmed'}
    for row in units.values():
        if row['annual_status'] == 'confirmed' and any(proof['kind'] in {'current_master_catalog_entry', 'provincial_master_unit_list'} for proof in row['evidence']):
            observed.get(row['province'], set()).add(row['unit_code'])
    province_checks = {code: {'reported_total': expected.get(code), 'observed_unique_units': len(observed[name]),
                            'reconciles': expected.get(code) == len(observed[name])} for code, name in PROVINCES.items()}
    count = sum(expected.values()) if len(expected) == len(PROVINCES) and all(type(value) is int for value in expected.values()) else None
    complete = (not conflicts and count is not None and len(confirmed) == count
        and all(value['reconciles'] for value in province_checks.values())
        and all(row['annual_status'] in {'confirmed', 'not_listed'} for row in units.values()))
    return complete, count if complete else None, province_checks


def build_annual_registry(registry, public_pages, province_lists, *, year=2027, charters=()):
    originals = registry.get('units', [])
    units = {str(row['id']): {'unit_id': str(row['id']), 'unit_name': row['name'],
        'unit_code': row.get('unit_code'), 'province': row.get('province'), 'year': year,
        'annual_status': 'unknown', 'evidence': [],
        'notes': ['通用院校库收录不能证明该年度开展硕士招生。']} for row in originals}
    by_code = {row.get('unit_code'): str(row['id']) for row in originals if row.get('unit_code')}
    by_name = {canonical(row['name']): str(row['id']) for row in originals}
    sources = [*public_pages, *province_lists]
    documents = {source['document']['id']: source['document'] for source in sources if source.get('document')}
    rows = [(row, source['document']) for source in sources for row in source.get('units', []) if source.get('document')]
    rows += [(entry['unit'], entry['document']) for entry in charters]
    conflicts = []
    for row, document in rows:
        identity, conflict = _identity(row, units, by_code, by_name)
        old = units.get(identity, {})
        if conflict:
            conflicts.append({'unit_id': identity, 'unit_name': row['unit_name'], 'unit_code': row['unit_code'],
                              'source_document_id': document['id'], 'reason': conflict})
            continue
        proof = _evidence(row, document)
        evidence = old.get('evidence', [])
        units[identity] = {**old, 'index_unit_code': old.get('index_unit_code', old.get('unit_code')),
            'unit_id': row.get('unit_id') or (identity if not identity.startswith('code:') else None),
            'unit_code': row['unit_code'], 'unit_name': row['unit_name'], 'province': row['province'], 'year': year,
            'annual_status': 'confirmed', 'evidence': evidence if proof in evidence else [*evidence, proof],
            'notes': ['确认目标年度硕士招生单位或目录条目，不代表该校专业目录已完整收录或报考资格已通过。']}
        documents[document['id']] = document
    units = _exact_outcomes(units, sources)
    confirmed = {row['unit_code'] for row in units.values() if row['annual_status'] == 'confirmed'}
    provinces = [{**{key: value for key, value in source.items() if key not in {'units', 'document'}},
                  'source_document_id': source.get('document', {}).get('id')} for source in sources if source.get('query_kind') != 'exact_unit']
    complete, total, checks = _reconciliation(units, provinces, conflicts)
    queries = [{**{key: value for key, value in source.items() if key not in {'units', 'document'}},
                'source_document_id': source['document']['id']} for source in sources if source.get('query_kind') == 'exact_unit']
    return {'metadata': {'year': year, 'checked_at': datetime.now(timezone.utc).isoformat(), 'complete': complete,
        'annual_scope_status': 'verified' if complete else 'provisional', 'official_total': total, 'indexed_records': len(originals),
        'confirmed_units': len(confirmed), 'unconfirmed_index_units': sum(row['annual_status'] in {'unknown', 'conflict'} for row in units.values()),
        'not_listed_index_records': sum(row['annual_status'] == 'not_listed' for row in units.values()),
        'notes': [('全国年度招生单位代码已按31省公开声明数量及普通精确查询对账；专业目录仍需逐校核验。' if complete
                   else '全国年度招生单位全集尚未对账，不把通用院校库条数当官方硕士招生单位总数。'),
                  '国科大培养单位没有独立年度报考单位代码证据时，不升级为已确认独立招生单位。']},
        'province_manifests': provinces, 'documents': list(documents.values()), 'units': list(units.values()),
        'identity_conflicts': conflicts, 'unit_query_manifests': queries, 'province_reconciliation': checks}


def _allowed(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.port in (None, 443) and not parsed.username and not parsed.password
            and ((parsed.hostname == 'yz.chsi.com.cn' and parsed.path in {'/zsml/dw.do', '/zsml/rs/dws.do'})
                 or url in {entry[1] for entry in PROVINCE_LISTS}))


class _Redirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        if not _allowed(newurl):
            raise AnnualRegistryError('年度名单请求跳转到未授权来源')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _download(url, data=None):
    if not _allowed(url):
        raise AnnualRegistryError('只允许已核准的公开年度查询及省考试院名单来源')
    request = Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0 (compatible; GradScopePublicCatalog/1.0)',
        'Referer': CONTEXT_URL, 'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8',
        'X-Requested-With': 'XMLHttpRequest'})
    with build_opener(_Redirect()).open(request, timeout=30) as response:
        if not _allowed(response.geturl()):
            raise AnnualRegistryError('年度名单来源跳转不正确')
        raw, kind = response.read(MAX_BYTES + 1), response.headers.get('Content-Type', '')
    if not raw or len(raw) > MAX_BYTES:
        raise AnnualRegistryError('年度名单原文为空或超过2MB上限')
    return raw, kind


def _cached_original(pointer, directory, root, url, data):
    try:
        document = json.loads(pointer.read_text('utf-8'))
        if document.get('url') != url or document.get('method') != ('POST' if data is not None else 'GET'):
            return None
        if data is not None and document.get('request_sha256') != hashlib.sha256(data).hexdigest():
            return None
        path = (root/document['local_path']).resolve()
        if path.is_relative_to(directory.resolve()) and path.is_file():
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() == document.get('sha256'):
                pending = url == QUERY_URL and json.loads(decode_text(raw)).get('invokeStatus') == 'WAIT'
                return document, raw, pending
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        return None


def _snapshot(url, directory, root, *, data=None, offline=False):
    key = hashlib.sha256(url.encode() + (data or b'')).hexdigest()[:20]
    pointer = directory/f'{key}.metadata.json'
    cached = _cached_original(pointer, directory, root, url, data)
    if cached and (offline or not cached[2]):
        return {**cached[0], 'from_cache': True}, cached[1]
    if offline:
        raise AnnualRegistryError('缺少有效年度原文快照，离线模式未发起请求')
    raw, kind = _download(url, data)
    if url == QUERY_URL and json.loads(decode_text(raw)).get('invokeStatus') == 'WAIT':
        time.sleep(1)
        raw, kind = _download(url, data)
    digest = hashlib.sha256(raw).hexdigest()
    path = directory/f'{key}-{digest[:16]}.txt'
    path.write_bytes(raw)
    document = {'id': f'annual-{key}-{digest[:16]}', 'url': url,
        'local_path': path.relative_to(root).as_posix(), 'sha256': digest, 'content_type': kind,
        'checked_at': datetime.now(timezone.utc).isoformat(), 'method': 'POST' if data is not None else 'GET'}
    if data is not None:
        document = {**document, 'request_body': data.decode('ascii'), 'request_sha256': hashlib.sha256(data).hexdigest()}
    temporary = pointer.with_suffix(pointer.suffix + '.tmp')
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), 'utf-8')
    temporary.replace(pointer)
    return document, raw


def _form(province, unit=None):
    code = (unit or {}).get('unit_code') or ''
    precise_code = bool(re.fullmatch(r'\d{5}', code))
    return {'dwmc': unit['name'] if unit and not precise_code else '', 'dwdm': code if precise_code else '',
            'ssdm': province, 'xxfs': '', 'dwlxs': ['all'], 'tydxs': '', 'jsggjh': '', 'jsxbjh': '',
            'start': 0, 'curPage': 1, 'pageSize': 20, 'totalPage': 0, 'totalCount': 0}


def _exact_queries(registry, known, directory, root, context, *, offline, delay, progress):
    codes = {name: code for code, name in PROVINCES.items()}
    results, errors = [], []
    candidates = [unit for unit in registry['units'] if str(unit['id']) not in known]
    for index, unit in enumerate(candidates):
        province = codes.get(unit.get('province'))
        downloaded = True
        try:
            if province is None:
                raise AnnualRegistryError('索引所在地未知，不推定年度单位范围')
            body = urlencode(_form(province, unit), doseq=True).encode()
            doc, raw = _snapshot(QUERY_URL, directory, root, data=body, offline=offline)
            downloaded = not doc.get('from_cache', False)
            payload = json.loads(decode_text(raw))
            if payload.get('flag') is not True and '登录' in str(payload.get('msg', '')):
                raise AnnualRegistryError('公开查询要求登录，停止剩余查询')
            result = parse_exact_query(payload, unit, province, context)
            results.append({**result, 'document': doc})
        except (OSError, ValueError) as error:
            errors.append({'unit_id': str(unit['id']), 'unit_name': unit['name'], 'source': QUERY_URL, 'error': str(error)})
            if '停止剩余查询' in str(error):
                break
        if progress and (index % 25 == 0 or index + 1 == len(candidates)):
            progress({'exact_attempted': index + 1, 'exact_target': len(candidates), 'last_unit': unit['name'], 'errors': len(errors)})
        if not offline and delay and downloaded:
            time.sleep(delay)
    return results, errors


def _name_cross_checks(registry, sources, directory, root, context, *, offline):
    by_id = {str(row['id']): row for row in registry['units']}
    checks, errors = [], []
    for source in sources:
        if source.get('match_status') != 'not_listed':
            continue
        unit = by_id[source['index_unit_id']]
        query = re.split('[（(]', unit['name'], maxsplit=1)[0]
        target = {**unit, 'name': query, 'unit_code': ''}
        try:
            body = urlencode(_form(source['province_code'], target), doseq=True).encode()
            doc, raw = _snapshot(QUERY_URL, directory, root, data=body, offline=offline)
            parsed = parse_public_units(json.loads(decode_text(raw)), source['province_code'], context)
            checks.append({'index_unit_id': str(unit['id']), 'query_name': query,
                'reported_total': parsed['reported_total'], 'matches': parsed['units'], 'document': doc,
                'notes': ['名称搜索命中其他单位仅作为相关候选，不自动认定更名、合并或隶属关系。']})
        except (OSError, ValueError) as error:
            errors.append({'unit_id': str(unit['id']), 'source': QUERY_URL, 'error': str(error)})
    return checks, errors


def collect_annual_registry(registry, root=PROJECT_ROOT, *, year=2027, offline=False, national_sources=None, delay=.35,
                            exact_queries=False, progress=None):
    root = Path(root).resolve()
    directory = root/f'data/snapshots/annual_registry/{year}'
    directory.mkdir(parents=True, exist_ok=True)
    context_doc, raw = _snapshot(CONTEXT_URL, directory, root, offline=offline)
    context = directory_context(decode_text(raw, context_doc['content_type']), year)
    public, provinces, errors = [], [], []
    for province in PROVINCES:
        form = _form(province)
        downloaded = True
        try:
            doc, raw = _snapshot(QUERY_URL, directory, root, data=urlencode(form, doseq=True).encode(), offline=offline)
            downloaded = not doc.get('from_cache', False)
            parsed = parse_public_units(json.loads(decode_text(raw)), province, context, year)
            public.append({**parsed, 'document': doc, 'context_document_id': context_doc['id']})
        except (OSError, ValueError) as error:
            errors.append({'province_code': province, 'source': QUERY_URL, 'error': str(error)})
        if not offline and delay and downloaded:
            time.sleep(delay)
    for province, url in PROVINCE_LISTS:
        try:
            doc, raw = _snapshot(url, directory, root, offline=offline)
            provinces.append({**parse_province_units(decode_text(raw, doc['content_type']), province, year), 'document': doc})
        except (OSError, ValueError) as error:
            errors.append({'province_code': province, 'source': url, 'error': str(error)})
    if exact_queries:
        known = {str(unit['unit_id']) for source in [*public, *provinces] for unit in source['units'] if unit.get('unit_id')}
        by_code = {row.get('unit_code'): str(row['id']) for row in registry['units']}
        known.update(by_code[row['unit_code']] for source in provinces for row in source['units'] if row['unit_code'] in by_code)
        exact, failures = _exact_queries(registry, known, directory, root, context, offline=offline, delay=delay, progress=progress)
        public.extend(exact)
        errors.extend(failures)
    by_id = {str(unit['id']): unit for unit in registry['units']}
    charters = []
    for row in (national_sources or {}).get('units', []):
        unit = by_id.get(str(row.get('unit_id')))
        if unit is None:
            continue
        for doc in row.get('pages', []):
            verified = verify_charter(doc, unit, root, year)
            if verified:
                charters.append({'unit': verified, 'document': doc})
    result = build_annual_registry(registry, public, provinces, year=year, charters=charters)
    name_checks, name_errors = _name_cross_checks(registry, public, directory, root, context, offline=offline) if exact_queries else ([], [])
    unit_by_id = {check['index_unit_id']: check for check in name_checks}
    annotated = [{**unit, 'related_current_units': unit_by_id[unit['unit_id']]['matches']}
                 if unit['unit_id'] in unit_by_id else unit for unit in result['units']]
    return {**result, 'metadata': {**result['metadata'], 'errors': errors,
            'name_query_errors': name_errors, 'public_query_provinces': sum(source.get('query_kind') != 'exact_unit' for source in public)},
            'documents': [context_doc, *result['documents'], *(check['document'] for check in name_checks)],
            'units': annotated,
            'name_query_manifests': [{**{key: value for key, value in check.items() if key != 'document'},
                                      'source_document_id': check['document']['id']} for check in name_checks]}


def _read_original(document, root):
    path = (root/document['local_path']).resolve()
    if not path.is_relative_to(root/'data') or not path.is_file() or path.stat().st_size > 8 * MAX_BYTES:
        raise AnnualRegistryError('年度依据原文缺失或超出所属数据目录')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != document.get('sha256'):
        raise AnnualRegistryError('年度依据原文摘要不一致')
    return raw


def _replay_annual_source(manifest, documents, originals, root, context):
    doc = documents[manifest['source_document_id']]
    raw = _read_original(doc, root)
    if doc['url'] == QUERY_URL:
        province = manifest['province_code']
        precise = manifest.get('query_kind') == 'exact_unit'
        unit = originals[manifest['index_unit_id']] if precise else None
        body = urlencode(_form(province, unit), doseq=True).encode()
        if doc.get('request_body') != body.decode('ascii') or doc.get('request_sha256') != hashlib.sha256(body).hexdigest():
            raise AnnualRegistryError('年度查询请求与单位、省份或公开首屏参数不一致')
        payload = json.loads(decode_text(raw))
        parsed = parse_exact_query(payload, unit, province, context) if precise else parse_public_units(payload, province, context)
        return {**parsed, 'document': doc}
    province = next((code for code, url in PROVINCE_LISTS if url == doc['url']), None)
    if province is None:
        raise AnnualRegistryError('省级名单来源尚未核准')
    return {**parse_province_units(decode_text(raw, doc.get('content_type', '')), province), 'document': doc}


def verify_annual_registry(annual, registry, project_root=PROJECT_ROOT):
    """Reconstruct annual identities/counts from source bytes before trusting metadata."""
    try:
        root = Path(project_root).resolve()
        documents = {doc['id']: doc for doc in annual['documents']}
        if len(documents) != len(annual['documents']):
            return False
        contexts = [doc for doc in documents.values() if doc['url'] == CONTEXT_URL]
        if len(contexts) != 1:
            return False
        context = directory_context(decode_text(_read_original(contexts[0], root), contexts[0].get('content_type', '')))
        originals = {str(unit['id']): unit for unit in registry['units']}
        manifests = [*annual['province_manifests'], *annual.get('unit_query_manifests', [])]
        sources = [_replay_annual_source(value, documents, originals, root, context) for value in manifests]
        charters = []
        for doc in documents.values():
            _read_original(doc, root)
            unit = originals.get(str(doc.get('unit_id')))
            verified = verify_charter(doc, unit, root) if unit else None
            if verified:
                charters.append({'unit': verified, 'document': doc})
        rebuilt = build_annual_registry(registry, sources, [], charters=charters)
        for check in annual.get('name_query_manifests', []):
            original = originals[check['index_unit_id']]
            query = re.split('[（(]', original['name'], maxsplit=1)[0]
            province = next(code for code, name in PROVINCES.items() if name == original['province'])
            body = urlencode(_form(province, {**original, 'name': query, 'unit_code': ''}), doseq=True).encode()
            doc = documents[check['source_document_id']]
            if doc.get('request_body') != body.decode('ascii') or doc.get('request_sha256') != hashlib.sha256(body).hexdigest():
                return False
            parsed = parse_public_units(json.loads(decode_text(_read_original(doc, root))), province, context)
            if check['query_name'] != query or check['reported_total'] != parsed['reported_total'] or check['matches'] != parsed['units']:
                return False
        keys = ('year', 'complete', 'official_total', 'confirmed_units', 'unconfirmed_index_units', 'not_listed_index_records')
        signature = lambda row: (row['unit_id'], row['unit_code'], row['unit_name'], row['annual_status'],
                                 sorted(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in row.get('evidence', [])))
        return (all(annual['metadata'].get(key) == rebuilt['metadata'].get(key) for key in keys)
                and sorted(map(signature, annual['units']), key=str) == sorted(map(signature, rebuilt['units']), key=str))
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        return False


def main():
    cli = argparse.ArgumentParser(description='核对年度公开硕士单位证据，不绕过登录分页、不推定全国总数')
    cli.add_argument('--units', type=Path, default=PROJECT_ROOT/'data/units.json')
    cli.add_argument('--output', type=Path, default=PROJECT_ROOT/'data/annual_registry_2027.json')
    cli.add_argument('--national-sources', type=Path, default=PROJECT_ROOT/'data/national_sources.json')
    cli.add_argument('--offline', action='store_true')
    cli.add_argument('--exact', action='store_true', help='用页面正常公开代码或名称搜索核对尚未确认的索引单位，仅请求首屏')
    args = cli.parse_args()
    registry = json.loads(args.units.read_text('utf-8-sig'))
    national = json.loads(args.national_sources.read_text('utf-8-sig')) if args.national_sources.exists() else None
    result = collect_annual_registry(registry, offline=args.offline, national_sources=national, exact_queries=args.exact,
                                    progress=lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
    temporary = args.output.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    temporary.replace(args.output)
    print(json.dumps(result['metadata'], ensure_ascii=False))


if __name__ == '__main__':
    main()
