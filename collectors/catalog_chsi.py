"""Typed public CHSI catalogs; original exam combinations remain authoritative."""
from copy import deepcopy
from itertools import product
import json
import re
from urllib.parse import parse_qs, urlsplit

from .catalogs import NATIONAL, _program, compact


def _literal(text, name):
    matches = list(re.finditer(r'(?m)^\s*' + re.escape(name) + r'\s*:\s*', text))
    if len(matches) != 1:
        raise ValueError(f'官方页面字段{name}未唯一声明')
    value, end = json.JSONDecoder().raw_decode(text[matches[0].end():])
    tail = text[matches[0].end() + end:].lstrip()
    if not tail.startswith((',', '}')):
        raise ValueError('页面字段不是独立JSON字面量')
    return value


def _year(html, year):
    if not isinstance(html, str) or type(year) is not int or not 2000 <= year <= 2100:
        raise ValueError('招生年度无效')
    years = set(re.findall(r'\bcurYear\s*:\s*["\'](20\d{2})["\']', html))
    if years != {str(year)} or not re.search(r'<h1\b[^>]*>\s*{{\s*curYear\s*}}年硕士专业目录\s*</h1>', html):
        raise ValueError('实际官方页面未明确对应年度硕士目录')


def _context(html, unit, year):
    if (not isinstance(unit, dict) or not isinstance(unit.get('id'), str) or not unit['id'].isdigit()
            or not isinstance(unit.get('unit_code'), str) or not re.fullmatch(r'\d{5}', unit['unit_code'])
            or not isinstance(unit.get('name'), str) or not unit['name'].strip()):
        raise ValueError('招生单位代码、名称或研招网身份未核实')
    _year(html, year)
    identity = _literal(html, 'cxvo')
    if not isinstance(identity, dict) or any(str(identity.get(field)) != str(unit.get(key))
            for field, key in [('schId', 'id'), ('dwdm', 'unit_code'), ('dwmc', 'name')]):
        raise ValueError('实际官方页面招生单位身份不一致')


def _evidence(document, fields, path):
    return {'document_id': document['id'], 'url': document['url'],
            'structured_fields': deepcopy(fields), 'field_path': path, 'kind': 'structured_fields'}


def _slot(value, slot, unit, document, path):
    if not isinstance(value, dict):
        raise ValueError('初试科目单元缺失')
    code, name = value.get('kskmdm'), value.get('kskmmc')
    if name in {'无', '不考'} and isinstance(code, str) and re.fullmatch(r'[-—－]*', code.strip()):
        return None
    if not isinstance(code, str) or not re.fullmatch(r'[1-9]\d{2}', code) or not isinstance(name, str) or not name.strip():
        raise ValueError('初试科目代码或名称无法核对')
    alias = re.sub(r'^(英语|数学)([一二三])$', r'\1(\2)', compact(name))
    national = code in NATIONAL and alias in NATIONAL[code]
    if code in NATIONAL and not national:
        raise ValueError('全国统考代码与科目名称不一致')
    return {'code': code, 'name': name.strip(), 'exam_unit': slot, 'scope': 'national' if national else str(unit['id']),
            'reference': value.get('cksm'), 'evidence': _evidence(document, value, path)}


def _exams(raw, unit, document, path):
    if not isinstance(raw, list) or not raw or len(raw) > 200:
        return [], [], False, ['实际科目套组缺失或数量异常']
    combinations, issues = [], []
    for index, group in enumerate(raw):
        try:
            if not isinstance(group, dict):
                raise ValueError('实际科目套组格式无效')
            slots = [_slot(group.get(f'km{slot}Vo'), slot, unit, document, f'{path}[{index}].km{slot}Vo') for slot in range(1, 5)]
            subjects = [subject for subject in slots if subject is not None]
            if [s['exam_unit'] for s in subjects] != list(range(1, len(subjects) + 1)) or len(subjects) not in {2, 3, 4}:
                raise ValueError('初试单元缺失或不连续')
            combinations.append({'subjects': subjects, 'evidence': _evidence(document, group, f'{path}[{index}]')})
        except ValueError as error:
            issues.append(str(error))
    if issues or not combinations:
        return [], deepcopy(raw), False, issues
    alternatives = {slot: {} for slot in range(1, 5)}
    signatures = set()
    for group in combinations:
        signature = []
        for subject in group['subjects']:
            identity = (subject['code'], subject['name'], subject['scope'])
            alternatives[subject['exam_unit']] = {**alternatives[subject['exam_unit']], identity: subject}
            signature.append(identity)
        signatures.add(tuple(signature))
    lengths = {len(group['subjects']) for group in combinations}
    if len(lengths) != 1:
        return [], deepcopy(raw), False, ['初试套组跨单元关联，暂不支持条件匹配']
    count = next(iter(lengths))
    expected = 1
    for slot in range(1, count + 1):
        expected *= len(alternatives[slot])
    if expected != len(signatures) or signatures != set(product(*(alternatives[slot] for slot in range(1, count + 1)))):
        return [], deepcopy(raw), False, ['初试套组跨单元关联，暂不支持条件匹配']
    subjects = [{**subject, **({'choice_group': f'exam-unit-{slot}'} if len(alternatives[slot]) > 1 else {})}
                for slot in range(1, count + 1) for subject in alternatives[slot].values()]
    return subjects, deepcopy(raw), True, []


def _quota(raw, document, path):
    if not isinstance(raw, str):
        return None
    match = re.fullmatch(r'(专业|院系所|研究方向|方向)\s*[:：]\s*(\d+)\s*[（(](不含|含)推免[）)]', raw.strip())
    if not match:
        return None
    return {'count': int(match[2]), 'scope': {'专业': 'department_program', '院系所': 'department',
            '研究方向': 'direction', '方向': 'direction'}[match[1]],
            'includes_recommendation_exemption': match[3] == '含', 'estimated': True,
            'evidence': _evidence(document, {'nzsrsstr': raw}, path),
            'notes': ['该数值为原文明确范围内拟招生人数，不能重复累加到每个研究方向。']}


def _row(raw, unit, year, document, path, *, html_fields=None):
    fields = [('dwdm', 'unit_code'), ('dwmc', 'name'), ('schId', 'id')]
    if not isinstance(raw, dict) or any(str(raw.get(k)) != str(unit.get(v)) for k, v in fields):
        raise ValueError('方向条目招生单位身份不一致')
    for key, pattern in [('yxsdm', r'[0-9A-Z]{3}'), ('zydm', r'[0-9A-Z]{6}'), ('yjfxdm', r'[0-9A-Z]{2}')]:
        if not isinstance(raw.get(key), str) or not re.fullmatch(pattern, raw[key]):
            raise ValueError('院系、专业或方向代码未明确')
    if any(not isinstance(raw.get(k), str) or not raw[k].strip() for k in ['id', 'yxsmc', 'zymc', 'yjfxmc']):
        raise ValueError('院系、专业或方向名称未明确')
    if raw.get('xxfs') not in {'1', '2'}:
        raise ValueError('方向学习方式未明确')
    exam_types = {'21': {'统考', '全国统考'}, '22': {'推免', '推荐免试'}, '23': {'单考', '单独考试'}}
    if raw.get('ksfsdm') not in exam_types or raw.get('ksfsmc') not in exam_types.get(raw.get('ksfsdm'), set()):
        raise ValueError('方向考试方式未明确')
    if raw['ksfsdm'] != '21':
        return None, {'source_direction_id': raw['id'], 'reason': f'原文考试方式{raw["ksfsdm"]}不属于统考21',
                      'evidence': [_evidence(document, raw, path)]}, []
    exam_path = 'mixin.data.kskm' if html_fields is not None else path + '.kskmz'
    subjects, combinations, supported, issues = _exams(raw.get('kskmz'), unit, document, exam_path)
    row = _program(unit, year, raw['yxsdm'] + ' ' + raw['yxsmc'], raw['zydm'], raw['zymc'],
                   raw['yjfxdm'] + ' ' + raw['yjfxmc'], {'1': '全日制', '2': '非全日制'}[raw['xxfs']],
                   subjects, '', document, degree={'xs': '学术学位', 'zy': '专业学位'}.get(raw.get('xwlx')))
    proof = _evidence(document, html_fields if html_fields is not None else {k: v for k, v in raw.items() if k != 'kskmz'},
                      'detail.labels' if html_fields is not None else path)
    quota = _quota(raw.get('nzsrsstr'), document, 'detail.labels.拟招生人数' if html_fields is not None else path + '.nzsrsstr')
    if quota and html_fields is not None:
        quota = {**quota, 'evidence': _evidence(document, {'拟招生人数': raw['nzsrsstr']}, 'detail.labels.拟招生人数')}
    return {**row, 'chsi_direction_id': raw['id'], 'catalog_document_id': document['id'],
            'exam_combinations': combinations, 'exam_matching_supported': supported,
            'evidence': [proof], 'exam_evidence': _evidence(document, {'kskm' if html_fields is not None else 'kskmz': raw.get('kskmz')}, exam_path),
            'planned_enrollment': quota,
            'notes': [raw['zybz']] if raw.get('zybz') else []}, None, issues


def parse_chsi_directions_json(payload, unit, year, document, *, context_html):
    _context(context_html, unit, year)
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    if not isinstance(payload, dict) or not isinstance(payload.get('msg'), dict):
        raise ValueError('公开方向响应无有效列表，可能需要登录或稍后重试')
    model, page = payload.get('zsmlcxModel'), payload['msg']
    if not isinstance(model, dict) or str(model.get('dwdm')) != str(unit.get('unit_code')):
        raise ValueError('方向查询单位与真实页面不一致')
    address = urlsplit(document['url'])
    request = parse_qs(document.get('request_body', ''))
    if (address.scheme != 'https' or address.hostname != 'yz.chsi.com.cn' or address.username or address.password
            or address.port not in {None, 443} or address.path != '/zsml/rs/yjfxs.do' or address.query
            or document.get('method') != 'POST' or request.get('dwdm') != [str(unit['unit_code'])]
            or request.get('zydm') != [model.get('zydm')]):
        raise ValueError('方向JSON必须保留真实POST地址和对应查询参数')
    rows = page.get('list')
    if (not isinstance(rows, list) or type(page.get('totalCount')) is not int or page['totalCount'] < len(rows)
            or type(page.get('curPage')) is not int or page['curPage'] < 1):
        raise ValueError('方向列表分页范围无效')
    result = {'programs': [], 'expected_records': len(rows), 'issues': [], 'excluded_records': [],
              'catalog_scope_complete': False, 'page_scope': {'official_total': page['totalCount'],
              'observed_rows': len(rows), 'page': page['curPage'], 'complete': len(rows) == page['totalCount']}}
    for index, raw in enumerate(rows):
        try:
            if not isinstance(raw, dict) or raw.get('zydm') != model.get('zydm'):
                raise ValueError('方向专业与原始专业查询不一致')
            row, excluded, issues = _row(raw, unit, year, document, f'msg.list[{index}]')
            result = {**result, 'programs': [*result['programs'], *([row] if row else [])],
                      'excluded_records': [*result['excluded_records'], *([excluded] if excluded else [])],
                      'expected_records': result['expected_records'] - int(excluded is not None),
                      'issues': [*result['issues'], *issues]}
        except (ValueError, AttributeError) as error:
            result = {**result, 'issues': [*result['issues'], str(error)]}
    identities = [row['id'] for row in result['programs']]
    if len(identities) != len(set(identities)):
        result = {**result, 'issues': [*result['issues'], '方向身份重复，不能认定目录完整']}
    return {**result, 'not_publishable_without_detail': True, 'requires_public_gate_proof': True,
            'publication_status': 'test_or_discovery_only',
            'programs': [{**row, 'catalog_publishable': False, 'requires_public_gate_proof': True,
                          'not_publishable_without_detail': True} for row in result['programs']]}


def _detail_values(html):
    pairs = re.findall(r'<div\b[^>]*class=["\']name["\'][^>]*>(.*?)</div>\s*'
                       r'<div\b[^>]*class=["\']value["\'][^>]*>(.*?)</div>', html, re.S)
    values = {}
    for label, value in pairs:
        label = label.strip().rstrip('：:')
        if label in values:
            raise ValueError('详情原文字段重复')
        values = {**values, label: value.strip()}
    return values


def _coded(value, digits):
    match = re.fullmatch(r'\(([0-9A-Z]{' + str(digits) + r'})\)\s*(.+)', value)
    if not match or '{{' in value or '<' in value:
        raise ValueError('详情身份代码或名称不明确')
    return match[1], match[2].strip()


def parse_chsi_detail_html(html, unit, year, document, *, context_html):
    _context(context_html, unit, year)
    _year(html, year)
    address = urlsplit(document['url'])
    identifier = _literal(html, 'id')
    if (address.scheme != 'https' or address.hostname != 'yz.chsi.com.cn'
            or address.path != '/zsml/yjfxdetail' or address.username or address.password
            or address.port not in {None, 443} or document.get('method', 'GET') != 'GET'
            or parse_qs(address.query).get('id') != [identifier]):
        raise ValueError('详情原文地址与页面方向身份不一致')
    values = _detail_values(html)
    school, name = _coded(values.get('招生单位', ''), 5)
    if school != str(unit.get('unit_code')) or name != unit.get('name'):
        raise ValueError('详情招生单位身份与官方上下文不一致')
    department, department_name = _coded(values.get('院系所', ''), 3)
    identity = re.sub(r"\(\{\{\s*xwlx\s*===\s*'xs'\s*\?\s*'学术学位'\s*:\s*'专业学位'\s*}}\)", '', values.get('专业', ''))
    code, program_name = _coded(identity, 6)
    direction, direction_name = _coded(values.get('研究方向', ''), 2)
    mode = values.get('学习方式', '')
    modes = {'全日制': '1', '非全日制': '2'}
    if mode not in modes:
        expression = re.fullmatch(r"\{\{\s*([12])\s*==\s*1\s*\?\s*'全日制'\s*:\s*'非全日制'\s*}}", mode)
        if not expression:
            raise ValueError('详情学习方式没有明确的原文映射')
        mode_code = expression[1]
    else:
        mode_code = modes[mode]
    exam = values.get('考试方式')
    exam_code = {'统考': '21', '全国统考': '21', '单考': '23', '单独考试': '23', '推免': '22', '推荐免试': '22'}.get(exam)
    if exam_code is None:
        raise ValueError('详情考试方式未明确')
    if (not re.search(r'v-for=["\']ite,ind in kskm["\']', html)
            or not all('ite.km' + str(slot) + 'Vo.kskmdm' in html for slot in range(1, 5))):
        raise ValueError('原始JSON未由真实考试科目模板绑定')
    raw = {'id': identifier, 'dwdm': school, 'dwmc': name, 'schId': str(unit['id']),
           'ksfsdm': exam_code, 'ksfsmc': exam, 'yxsdm': department, 'yxsmc': department_name,
           'zydm': code, 'zymc': program_name, 'yjfxdm': direction, 'yjfxmc': direction_name,
           'xxfs': mode_code, 'xwlx': _literal(html, 'xwlx'), 'zybz': _literal(html, 'bz'),
           'kskmz': _literal(html, 'kskm'), 'nzsrsstr': values.get('拟招生人数')}
    row, excluded, issues = _row(raw, unit, year, document, 'mixin.data', html_fields=values)
    return {'programs': [row] if row else [], 'expected_records': int(row is not None), 'issues': issues,
            'excluded_records': [excluded] if excluded else [], 'catalog_scope_complete': False,
            'page_scope': {'official_total': 1, 'observed_rows': 1, 'complete': True}}
