"""Public ZSML trees and header-selected exam columns, never retest columns."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from decimal import Decimal
import json
import re
import threading
import time
from urllib.parse import urlencode, urlsplit

from .catalogs import _Table, _expanded_rows, _program, evidence, parse_subjects
from .documents import PROJECT_ROOT, DocumentError, decode_text, fetch_snapshot


def parse_zsml_tree(tree):
    if not isinstance(tree, list) or not tree:
        raise ValueError('官方院系树为空或格式不正确')
    nodes, seen_departments, seen = [], set(), set()
    for department in tree:
        if not isinstance(department, dict):
            raise ValueError('官方院系节点不是对象')
        identifier, name, children = department.get('id'), department.get('name'), department.get('children')
        if (not isinstance(identifier, str) or not re.fullmatch(r'[0-9A-Z]{3}', identifier)
                or identifier in seen_departments or not isinstance(name, str) or not name.strip()
                or not isinstance(children, list)):
            raise ValueError('院系身份、专业列表缺失或重复')
        seen_departments.add(identifier)
        for child in children:
            if not isinstance(child, dict):
                raise ValueError('专业节点不是对象')
            if not re.fullmatch(r'\d{2}', str(child.get('scode', ''))):
                raise ValueError('专业节点考试方式缺失，不能把未识别节点当作非统考排除')
            if str(child.get('scode')) != '21':
                continue
            route, title = child.get('id'), child.get('name')
            if (not isinstance(route, str) or not re.fullmatch(r'[0-9A-Z]{6}(?:_\d+)?', route)
                    or (identifier, route) in seen or not isinstance(title, str) or not title.strip()):
                raise ValueError('统考专业代码、名称缺失或重复')
            declared = re.match(r'^([0-9A-Z]{6})(?:\s|[（(]|$)', title.strip())
            code = declared[1] if declared else route
            if not re.fullmatch(re.escape(code) + r'(?:_\d+)?', route) or len(code) != 6:
                raise ValueError('原始目录路径与专业名称声明的代码不一致')
            seen.add((identifier, route))
            nodes.append({'department_id': identifier, 'department': name, 'code': code,
                          'route_id': route, 'name': title, 'exam_type': '21'})
    if not nodes:
        raise ValueError('官方树未列出可核对的统考专业')
    return nodes


def _columns(rows):
    for row in rows:
        labels = {index: re.sub(r'\s+', '', cell['text']) for index, cell in row['expanded'].items()}
        initial = [index for index, text in labels.items() if text in {'初试科目', '考试科目'}]
        identity = [index for index, text in labels.items() if text in {'专业及方向', '专业及研究方向', '专业、方向及学习方式', '学科及研究方向', '研究方向'}]
        if initial and identity and len(initial) == len(identity) == 1:
            quota = next((index for index, text in labels.items() if text in {'人数', '总计划', '拟接收统考数'}
                          or '人数' in text and ('预计' in text or '拟招' in text)), None)
            retest = next((index for index, text in labels.items() if '复试' in text), None)
            return {'identity': identity[0], 'initial': initial[0], 'quota': quota, 'retest': retest, 'labels': labels,
                    'duration': next((index for index, text in labels.items() if text == '学制'), None),
                    'tuition': next((index for index, text in labels.items() if re.fullmatch(r'(?:总|年)?学费(?:[（(].*[）)])?', text)), None),
                    'campus': next((index for index, text in labels.items() if text in {'培养校区', '校区'}), None),
                    'unified_exam': next((index for index, text in labels.items() if text == '统考招生'), None)}
    return None


def _direction_exams(text, code, mode):
    label = r'[0-9A-Z]{2}(?:\s*[（(](?:全日制|非全日制)[）)])?'
    scopes = list(re.finditer(label + r'(?:\s*[、，,]\s*' + label + r')*\s*方向\s*[:：]', text))
    if not scopes:
        return text, []
    selected = []
    for index, scope in enumerate(scopes):
        directions = re.findall(r'([0-9A-Z]{2})(?:\s*[（(](全日制|非全日制)[）)])?', scope.group())
        if any(number == code and (not kind or kind == mode) for number, kind in directions):
            selected.append(text[scope.end():scopes[index + 1].start() if index + 1 < len(scopes) else len(text)].strip())
    if len(selected) != 1:
        return '', ['初试分组未唯一覆盖该研究方向，不能采用其他方向的科目']
    return selected[0], []


def _initial_subjects(text, unit_id, document):
    normalized = re.sub(r'([①②③④]|或者|或)\s*(\d{3})\s*[-－—]\s*', r'\1\2', text)
    subjects, issues = parse_subjects(normalized, unit_id, document)
    if normalized != text:
        subjects = [{**subject, 'evidence': evidence(document, text)} for subject in subjects]
    return subjects, issues


def _table_fields(cells, columns, document):
    result = {}
    duration = cells.get(columns['duration'], {}).get('text', '')
    years = re.fullmatch(r'(\d+(?:\.\d+)?)\s*年', duration)
    if years:
        result = {**result, 'duration_years': float(years[1]), 'duration_evidence': evidence(document, duration)}
    campus = cells.get(columns['campus'], {}).get('text', '')
    if campus:
        result = {**result, 'campus': campus, 'campus_evidence': evidence(document, campus)}
    fee = cells.get(columns['tuition'], {}).get('text', '')
    amount = re.fullmatch(r'(\d+(?:\.\d+)?)(万)?元(?:/(学年|年))?', re.sub(r'\s+', '', fee))
    header = columns['labels'].get(columns['tuition'], '')
    if amount:
        period = 'year' if amount[3] or header.startswith('年') else 'total' if header.startswith('总') else None
        if period:
            value = float(Decimal(amount[1]) * (10000 if amount[2] else 1))
            result = {**result, 'tuition': {'amount': value, 'currency': 'CNY', 'period': period,
                       'evidence': [evidence(document, header), evidence(document, fee)]}}
    return result


def _exclude_named_recommendation_directions(result, group, note, document):
    clauses = re.finditer(r'(?:^|[。；;])\s*(?:\d+[.．]\s*)?([^。；;]+?)方向\s*仅招收推免生(?=[。；;]|$)', note)
    active = {row['id'] for row in result['programs']}
    rows = [row for row in group if row['id'] in active]
    names = {re.sub(r'\s+', '', row['direction']) for row in rows}
    excluded = {}
    for clause in clauses:
        labels = [re.sub(r'\s+', '', value) for value in re.split(r'[、，,]', clause[1])]
        if labels and all(label in names for label in labels):
            excluded = {**excluded, **{row['id']: row for row in rows if re.sub(r'\s+', '', row['direction']) in labels}}
    if not excluded:
        return result
    records = [{**{key: row[key] for key in ('id', 'unit_id', 'year', 'department_code', 'code', 'direction', 'study_mode')},
                'reason': '原表备注明确该方向仅招收推免生',
                'evidence': [row['evidence'][1], evidence(document, note)]} for row in excluded.values()]
    return {**result, 'programs': [row for row in result['programs'] if row['id'] not in excluded],
            'expected_records': result['expected_records'] - len(excluded),
            'excluded_recommendation_only': result['excluded_recommendation_only'] + len(excluded),
            'excluded_records': [*result['excluded_records'], *records],
            'issues': [issue for issue in result['issues'] if not any(issue.startswith(key + '：') for key in excluded)],
            'retest_rules': [row for row in result['retest_rules'] if row['id'].removeprefix('retest:') not in excluded]}


def parse_zsml_html(html, unit, department, year, document):
    parser = _Table()
    parser.feed(html)
    rows = list(_expanded_rows(parser.rows))
    expected = sum('zsml-talbe-yx-zy-yjfx' in row['class'].split() for row in rows)
    result = {'programs': [], 'expected_records': expected, 'issues': [], 'retest_rules': [],
              'excluded_recommendation_only': 0, 'excluded_records': []}
    columns = _columns(rows)
    if columns is None:
        result['issues'].append('初试与专业方向表头未唯一识别，不尝试从复试列猜测科目')
        return result
    header, group = None, []
    for row in rows:
        cells, classes = row['expanded'], row['class'].split()
        identity = cells.get(columns['identity'], {}).get('text', '')
        if 'zsml-talbe-yx-zy' in classes:
            header, group = re.match(r'([0-9A-Z]{6})\s*(.*)', identity), []
        elif 'zsml-talbe-yx-zy-yjfx' in classes:
            accepts = cells.get(columns['unified_exam'], {}).get('text', '')
            if columns['unified_exam'] is not None and accepts == '否':
                result['excluded_recommendation_only'] += 1
                result['expected_records'] -= 1
                result['excluded_records'].append({'reason': '原表明确统考招生为否',
                    'evidence': [evidence(document, identity), evidence(document, accepts)]})
                continue
            if columns['unified_exam'] is not None and accepts != '是':
                result['issues'].append(f'方向行{row["index"]}统考招生标记未知，不能当作不招生排除')
            direction = re.fullmatch(r'([0-9A-Z]{2})\s*[（(](全日制|非全日制)[）)]\s*(.+)', identity)
            exam_cell = cells.get(columns['initial'])
            if not header or not direction or not exam_cell:
                result['issues'].append(f'方向行{row["index"]}缺少专业、学习方式或初试单元')
                continue
            code, raw_name = header.groups()
            degree = '学术学位' if '学术学位' in raw_name else '专业学位' if '专业学位' in raw_name else None
            name = re.sub(r'[（(](?:专业学位|学术学位|全国统考)[）)]', '', raw_name).strip()
            direction_code, mode, direction_name = direction.groups()
            exam_text, scope_issues = _direction_exams(exam_cell['text'], direction_code, mode)
            subjects, subject_issues = _initial_subjects(exam_text, unit['id'], document)
            issues = [*scope_issues, *subject_issues]
            program = _program(unit, year, department, code, name, f'{direction_code} {direction_name}',
                               mode, subjects, '', document, degree=degree)
            program['evidence'] = [evidence(document, header.group(0)), evidence(document, identity)]
            program['exam_evidence'] = evidence(document, exam_text)
            program = {**program, **_table_fields(cells, columns, document)}
            quota = cells.get(columns['quota'])
            if quota and re.fullmatch(r'\d+', quota['text']):
                program['planned_enrollment'] = {
                    'count': int(quota['text']), 'scope': 'direction' if quota['origin'] == row['index'] else 'department_program',
                    'includes_recommendation_exemption': (True if columns['labels'][columns['quota']] == '总计划'
                        else False if columns['labels'][columns['quota']] == '拟接收统考数' else None), 'estimated': True,
                    'evidence': evidence(document, quota['text']),
                    'notes': ['原表为预计招生人数，是否含推免或专项未据此推定。']}
            retest = cells.get(columns['retest'], {}).get('text', '')
            if retest:
                result['retest_rules'].append({
                    'id': 'retest:' + program['id'], 'unit_id': str(unit['id']), 'unit_name': unit['name'],
                    'department': program['department'], 'year': year, 'title': f'{department} {code}复试与加试科目',
                    'program_codes': [code], 'requirements': [], 'requires_review': True,
                    'evidence': evidence(document, retest), 'notes': [retest]})
            result['issues'].extend(f'{program["id"]}：{issue}' for issue in issues)
            result['programs'].append(program)
            group.append(program)
        elif row['cells'] and row['cells'][0]['text'] == '学制及考试特殊用具说明':
            note = ' '.join(cell['text'] for cell in row['cells'][1:]).strip()
            duration = re.match(r'学制([二三四五六])年(?:\s|[，,。；;：:]|$)', note)
            if duration:
                years = {'二': 2, '三': 3, '四': 4, '五': 5, '六': 6}[duration.group(1)]
                group_ids = {program['id'] for program in group}
                result['programs'] = [
                    {**program, 'duration_years': years, 'duration_evidence': evidence(document, note)}
                    if program['id'] in group_ids and program.get('duration_years') is None else program for program in result['programs']]
        elif row['cells'] and row['cells'][0]['text'] == '备注':
            note = ' '.join(cell['text'] for cell in row['cells'][1:]).strip()
            result = _exclude_named_recommendation_directions(result, group, note, document)
            group_ids = {program['id'] for program in group}
            result['programs'] = [
                {**program, 'notes': [note], 'evidence': [*program['evidence'], evidence(document, note)]}
                if note and program['id'] in group_ids else program for program in result['programs']]
    if not expected:
        result['issues'].append('没有公开的统考方向行，不能认定该页完整')
    identities = [program['id'] for program in result['programs']]
    if len(identities) != len(set(identities)):
        result['issues'].append('方向身份重复')
    return result


def collect_zsml(unit, snapshot_dir, *, base_url, year=2027, workers=3, refresh=False,
                 offline=False, project_root=PROJECT_ROOT):
    base = urlsplit(base_url)
    official = urlsplit(unit.get('official_url') or '')
    root = '.'.join((official.hostname or '').split('.')[-3:])
    if (base.scheme != 'https' or not base.hostname or base.username or base.password
            or base.port not in (None, 443) or not root or not (base.hostname == root or base.hostname.endswith('.' + root))):
        raise DocumentError('目录系统不属于该校已经核对的官方域名')
    if type(year) is not int or not 2000 <= year <= 2100:
        raise ValueError('目录年度格式不正确')
    base_url = base_url.rstrip('/')
    directory = Path(snapshot_dir) / f'zsml-{unit["id"]}-{year}'
    options = {'project_root': project_root, 'refresh': refresh, 'offline': offline}
    tree_doc, raw = fetch_snapshot(base_url + '/zsml/getTreeData', directory, unit, year, 'catalog',
        f'{unit["name"]}{year}年官方院系专业树', data=urlencode({'nd': str(year), 'zslb': '1'}).encode(), **options)
    tree_doc = {**tree_doc, 'catalog_parser': 'zsml_tree'}
    nodes = parse_zsml_tree(json.loads(decode_text(raw)))
    result = {'programs': [], 'documents': [tree_doc], 'expected_records': 0, 'issues': [], 'retest_rules': [],
              'expected_nodes': len(nodes), 'fetched_nodes': 0, 'failed_nodes': [],
              'excluded_recommendation_only': 0, 'excluded_records': []}
    throttle, next_request = threading.Lock(), [0.0]

    def collect(node):
        if not offline:
            with throttle:
                delay = next_request[0] - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                next_request[0] = time.monotonic() + .3
        url = f'{base_url}/zsml/sszsml/zsmlYjfx/{year}/{node["department_id"]}/{node["route_id"]}?ksfs=21'
        doc, content = fetch_snapshot(url, directory, unit, year, 'catalog',
            f'{unit["name"]}{year}年{node["department"]} {node["code"]}专业目录', **options)
        doc = {**doc, 'catalog_parser': 'zsml_table', 'department': node['department']}
        parsed = parse_zsml_html(decode_text(content), unit, node['department'], year, doc)
        if any(row['code'] != node['code'] for row in parsed['programs']):
            parsed['issues'].append('方向页专业代码与原始院系树不一致')
        return doc, parsed

    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 3))) as pool:
        pending = {pool.submit(collect, node): node for node in nodes}
        for task in as_completed(pending):
            node = pending[task]
            try:
                doc, parsed = task.result()
            except (DocumentError, ValueError, OSError) as error:
                result['failed_nodes'].append({'department_id': node['department_id'], 'code': node['code'], 'error': str(error)})
                result['issues'].append(f'{node["department_id"]}/{node["code"]}未能完整采集')
                continue
            result['fetched_nodes'] += 1
            result['documents'].append(doc)
            for key in ('programs', 'issues', 'retest_rules', 'excluded_records'):
                result[key].extend(parsed[key])
            result['expected_records'] += parsed['expected_records']
            result['excluded_recommendation_only'] += parsed['excluded_recommendation_only']
    return result
