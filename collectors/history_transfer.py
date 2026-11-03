"""Extract evidence-backed historical transfer routes; never infer eligibility."""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser

from collectors.history import _Tables, _clean, _number


# Exact official national-exam identities; other names do not establish a code.
NATIONAL_SUBJECTS = {'思想政治理论': '101', '英语(一)': '201', '英语(二)': '204',
                     '数学(一)': '301', '数学(二)': '302', '计算机学科专业基础': '408'}
HEADER = ('初试专业代码', '初试专业名称', '政治理论名称', '外国语名称', '业务课一名称',
          '业务课二名称', '总分', '政治理论', '外国语', '业务课一', '业务课二')
SCORES = ('total', 'politics', 'foreign_language', 'business1', 'business2')


class _Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs, self.current, self.in_paragraph = [], [], False

    def handle_starttag(self, tag, attrs):
        if tag == 'p':
            self.current, self.in_paragraph = [], True

    def handle_data(self, data):
        if self.in_paragraph:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == 'p' and self.in_paragraph:
            self.paragraphs.append(_clean(''.join(self.current)))
            self.current, self.in_paragraph = [], False


def _evidence(context, quote):
    return {'document_id': context['document_id'], 'url': context['url'], 'quote': quote, 'page': None}


def _condition(field, op, value, label, evidence):
    return {'field': field, 'op': op, 'value': value, 'label': label, 'evidence': {**evidence}}


def _subjects(names):
    result = []
    for unit, name in enumerate(names, 1):
        alternatives = name.split('或') if name else ['']
        for alternative in alternatives:
            normalized = alternative.replace('（', '(').replace('）', ')')
            code = NATIONAL_SUBJECTS.get(normalized)
            subject = {'name': alternative, 'code': code, 'scope': 'national' if code else 'unknown',
                       'exam_unit': unit}
            if len(alternatives) > 1:
                subject['choice_group'] = f'transfer-unit-{unit}'
            result.append(subject)
    return result


def _route(row, mapping, context):
    get = lambda name: row[mapping[name]] if mapping[name] < len(row) else ''
    evidence = _evidence(context, '；'.join(f'{name}：{get(name)}' for name in HEADER))
    code = get(HEADER[0])
    code = code if re.fullmatch(r'\d{6}', code) else None
    subjects = _subjects([get(name) for name in HEADER[2:6]])
    requirements = [_condition('first_choice_program_code', 'eq', code, '一志愿专业代码', evidence),
                    _condition('subjects', 'subjects_match', subjects, '原初试科目整组要求', evidence)]
    complete = code is not None and all(get(name) for name in HEADER[1:6])
    for field, name, ceiling in zip(SCORES, HEADER[6:], (500, 100, 100, 150, 150)):
        value = _number(get(name))
        value = value if value is not None and value <= ceiling else None
        complete = complete and value is not None
        requirements.append(_condition(field, 'gte', value, name + '最低要求', evidence))
    return {'all': requirements, 'label': get(HEADER[1]) + '原初试路线', 'evidence': evidence}, bool(complete)


def _table_routes(tables, context):
    routes, seen, unresolved, found = [], 0, 0, False
    for table in tables:
        mapping = None
        for row in table:
            if set(HEADER).issubset(row):
                mapping = {name: row.index(name) for name in HEADER}
                found = True
                continue
            if mapping is None or not any(row) or HEADER[0] in row:
                continue
            route, complete = _route(row, mapping, context)
            routes.append(route)
            seen += 1
            unresolved += not complete
    return routes, seen, unresolved, found


def _target(paragraphs):
    pattern = r'(?:我院|本院)?接收调剂专业为[：:]([^。；;]{1,80}?)（专业代码[：:](\d{6})）'
    for paragraph in paragraphs:
        match = re.search(pattern, paragraph)
        if match:
            return match.group(2), match.group(1), paragraph
    return None


def _build_rule(target, routes, paragraphs, context):
    code, name, quote = target
    requirements = []
    for paragraph in paragraphs:
        if re.search(r'一志愿学习方式须为全日制', paragraph):
            requirements.append(_condition('study_mode', 'eq', '全日制', '一志愿学习方式',
                                           _evidence(context, paragraph)))
            break
    requirements.append({'any': routes, 'label': '满足任一完整原初试路线',
                         'evidence': _evidence(context, quote)})
    fingerprint = '|'.join(str(context[key]) for key in ('unit_id', 'department', 'year', 'document_id')) + '|' + code
    return {'id': 'transfer-' + hashlib.sha256(fingerprint.encode()).hexdigest()[:20],
            **{key: context[key] for key in ('unit_id', 'unit_name', 'department', 'year')},
            'program_code': code, 'program_name': name, 'requirements': requirements,
            'requires_review': True, 'historical_reference': True,
            'evidence': _evidence(context, quote),
            'notes': ['须同时符合当年学校招生简章及学院资格审查要求，完整资格条件尚未核验。',
                      '仅为历史公告条件，不代表目标年度开放调剂或仍有名额。',
                      '自命题科目未给可核验代码时保留未知身份，不按名称认定相同考试。']}


def _context(unit_id, unit_name, department, year, url, document_id):
    if isinstance(year, bool) or not isinstance(year, int) or not 2000 <= year <= 2100:
        raise ValueError('招生年度应为2000至2100的整数')
    fields = {'unit_id': unit_id, 'unit_name': unit_name, 'department': department, 'url': url}
    if any(not isinstance(value, str) or not value.strip() for value in fields.values()):
        raise ValueError('调剂来源的院校、院系及网址不得缺失')
    if document_id is not None and (not isinstance(document_id, str) or not document_id.strip()):
        raise ValueError('来源文档标识应为非空字符串')
    return {**fields, 'year': year,
            'document_id': document_id or 'history-' + hashlib.sha256(url.encode()).hexdigest()[:20]}


def parse_transfer_html(html, *, unit_id, unit_name, department, year, url, document_id=None):
    if not isinstance(html, str):
        raise TypeError('调剂网页应为HTML字符串')
    context = _context(unit_id, unit_name, department, year, url, document_id)
    tables, paragraphs = _Tables(), _Paragraphs()
    tables.feed(html)
    paragraphs.feed(html)
    target = _target(paragraphs.paragraphs)
    routes, seen, unresolved, found = _table_routes(tables.tables, context)
    warnings = []
    if target is None:
        warnings.append('未发现明确接收调剂专业及代码的原文段落，未生成调剂规则。')
    if not found or not routes:
        warnings.append('未发现可识别且含申请路线的调剂条件表格，未生成调剂规则。')
    if unresolved:
        warnings.append(f'{unresolved}行存在缺失或非法专业、科目、分数；约束保留未知，未以零或删除条件替代。')
    rules = [_build_rule(target, routes, paragraphs.paragraphs, context)] if target and routes else []
    return {'transfer_rules': rules, 'warnings': warnings, 'seen_rows': seen,
            'parsed_rows': seen - unresolved, 'unresolved_rows': unresolved,
            'source_table_complete': bool(target and routes and found and not unresolved)}
