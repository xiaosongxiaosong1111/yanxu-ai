"""Extract anonymous, year-specific summaries from public admissions result tables."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


def _clean(value):
    return re.sub(r'\s+', '', str(value or '')).replace('\u3000', '')


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.stack.append({'rows': [], 'pending': {}, 'row': None, 'cell': None})
        if not self.stack:
            return
        table = self.stack[-1]
        if tag == 'tr':
            table['row'] = {col: value for col, (value, remaining) in table['pending'].items()}
            table['pending'] = {col: (value, remaining - 1) for col, (value, remaining) in table['pending'].items() if remaining > 1}
        if tag in {'td', 'th'}:
            attributes = dict(attrs)
            def span(name):
                try:
                    return min(100, max(1, int(attributes.get(name, 1))))
                except (ValueError, TypeError):
                    return 1
            table['cell'] = {'text': [], 'cols': span('colspan'), 'rows': span('rowspan')}

    def handle_data(self, data):
        if self.stack and self.stack[-1]['cell'] is not None:
            self.stack[-1]['cell']['text'].append(data)

    def handle_endtag(self, tag):
        if not self.stack:
            return
        table = self.stack[-1]
        if tag in {'td', 'th'} and table['cell'] is not None:
            cell = table['cell']
            row = table['row']
            if row is None:
                table['row'] = row = {}
            position = 0
            while position in row:
                position += 1
            value = _clean(''.join(cell['text']))
            for col in range(position, position + cell['cols']):
                row[col] = value
                if cell['rows'] > 1:
                    table['pending'][col] = (value, cell['rows'] - 1)
            table['cell'] = None
        if tag == 'tr' and table['row'] is not None:
            row = table['row']
            if row:
                table['rows'].append([row.get(col, '') for col in range(max(row) + 1)])
            table['row'] = None
        if tag == 'table':
            self.tables.append(self.stack.pop()['rows'])


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def score_summary(values):
    valid = [number for value in values if (number := _number(value)) is not None]
    if not valid:
        return None
    return {'min': min(valid), 'max': max(valid), 'mean': round(statistics.mean(valid), 2),
            'median': statistics.median(valid)}


def _plan_type(notes):
    if not notes:
        return 'unmarked'
    for tokens, label in [(('退役', '退伍', '士兵'), '退役士兵相关计划'),
                          (('骨干', '少数民族'), '少数民族相关计划'),
                          (('免初试', '立功'), '免初试相关计划'),
                          (('专项', '单独考试'), '其他专项')]:
        if any(token in notes for token in tokens):
            return label
    return '其他备注未分类'


def _headers(row):
    aliases = {'初试总分': '总分', '初试成绩': '总分', '业务课一': '业务1', '业务课1': '业务1',
               '业务课二': '业务2', '业务课2': '业务2', '专业名称': '复试专业', '专业': '复试专业',
               '是否录取': '是否拟录取', '综合成绩': '录取成绩', '外国语': '外语'}
    normalized = [aliases.get(_clean(cell), _clean(cell)) for cell in row]
    required = {'复试专业', '学习方式', '总分', '是否拟录取'}
    return {name: normalized.index(name) for name in normalized if name} if required.issubset(normalized) else None


def _summarize_group(key, records, context, complete):
    major, mode, degree, plan = key
    fingerprint = '|'.join([context['unit_id'], context['department'], str(context['year']),
                            context['admission_source'], context['document_id'], *key])
    evidence = {'document_id': context['document_id'], 'url': context['url'], 'page': None,
                'quote': '复试结果公示表：复试专业、学习方式、初试总分、复试成绩、录取成绩、是否拟录取；统计仅纳入标注拟录取且有初试总分的行。'}
    return {
        'id': 'admitted-' + hashlib.sha256(fingerprint.encode()).hexdigest()[:20],
        **{key: context[key] for key in ('unit_id', 'unit_name', 'department', 'year')},
        'program_code': None, 'program_name': major, 'study_mode': mode, 'degree_type': degree,
        'cohort': ('院系专业调剂公示样本（未细分研究方向）' if context['admission_source'] == 'transfer'
                   else '院系专业公示样本（未确认一志愿/调剂类别，未细分研究方向）'),
        'plan_type': plan, 'admission_source': context['admission_source'], 'sample_count': len(records),
        'initial': score_summary(record['initial'] for record in records),
        'retest': score_summary(record['retest'] for record in records),
        'weighted_final': score_summary(record['weighted_final'] for record in records),
        'subject_scores': {field: score_summary(record[field] for record in records)
                           for field in ('politics', 'foreign_language', 'business1', 'business2')},
        'source_table_complete': complete, 'is_full_admission_cohort': False,
        'evidence': evidence, 'notes': ['范围仅限此份官方公示，不代表完整最终录取名单。',
                                       '未标专项不等于已核实普通计划；不同类别和年度分别计算。',
                                       '初试、复试和加权成绩各自统计，不混用分制，不预测录取概率。'],
    }


def parse_results_html(html, *, unit_id, unit_name, department, year, url, document_id=None,
                       admission_source='not_specified'):
    if not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= 2100:
        raise ValueError('招生年度应为2000至2100的整数')
    parser = _Tables()
    parser.feed(html)
    context = {'unit_id': str(unit_id), 'unit_name': unit_name, 'department': department,
               'year': year, 'url': url, 'admission_source': admission_source,
               'document_id': document_id or 'history-' + hashlib.sha256(url.encode()).hexdigest()[:20]}
    groups = defaultdict(list)
    seen = parsed = rejected = 0
    duplicate_rows = 0
    row_fingerprints = set()
    found = False
    for table in parser.tables:
        positions = [(index, mapping) for index, row in enumerate(table) if (mapping := _headers(row))]
        if not positions:
            continue
        found = True
        start, mapping = positions[0]
        for row in table[start + 1:]:
            if not any(row):
                continue
            repeated_header = _headers(row)
            if repeated_header:
                mapping = repeated_header
                continue
            if '复试专业' in row and '是否拟录取' in row:
                continue
            seen += 1
            fingerprint = hashlib.sha256('|'.join(row).encode()).hexdigest()
            if fingerprint in row_fingerprints:
                duplicate_rows += 1
                continue
            row_fingerprints.add(fingerprint)
            if len(row) <= max(mapping.values()):
                rejected += 1
                continue
            get = lambda field: row[mapping[field]] if field in mapping else ''
            status = get('是否拟录取')
            if status in {'否', '不予录取'}:
                parsed += 1
                continue
            total = _number(get('总分'))
            if status not in {'是', '拟录取'} or total is None or not get('复试专业') or not get('学习方式'):
                rejected += 1
                continue
            degree = {'学硕': '学术学位', '专硕': '专业学位'}.get(get('专业类型'), get('专业类型') or '未注明')
            key = (get('复试专业'), get('学习方式'), degree, _plan_type(get('备注')))
            groups[key].append({'initial': total, 'retest': _number(get('复试成绩')),
                                'weighted_final': _number(get('录取成绩')),
                                'politics': _number(get('政治')), 'foreign_language': _number(get('外语')),
                                'business1': _number(get('业务1')), 'business2': _number(get('业务2'))})
            parsed += 1
    complete = found and seen > 0 and rejected == 0 and duplicate_rows == 0
    warnings = ([] if found else ['未发现可识别的录取结果表头，未生成统计。'])
    if rejected:
        warnings.append(f'{rejected}行缺少统计所需字段，未将缺失成绩当零。')
    if duplicate_rows:
        warnings.append(f'发现{duplicate_rows}条完全重复行，统计已去重，原表完整性需核验。')
    return {'admitted_statistics': [_summarize_group(key, records, context, complete) for key, records in groups.items()],
            'seen_rows': seen, 'parsed_rows': parsed, 'rejected_rows': rejected, 'duplicate_rows': duplicate_rows,
            'source_table_complete': complete, 'warnings': warnings}


class _SchoolRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_school_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_school_url(url):
    parsed = urlparse(url)
    host = parsed.hostname or ''
    if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password or not (host == 'nankai.edu.cn' or host.endswith('.nankai.edu.cn')):
        raise ValueError('历史资料采集仅接受已配置的南开大学官方公开来源')
    if parsed.port not in (None, 80 if parsed.scheme == 'http' else 443):
        raise ValueError('历史资料仅允许官方网页默认端口')


def _parse_source(html, source, document_id):
    context = {field: source[field] for field in ('unit_id', 'unit_name', 'department', 'year', 'url')}
    if source.get('kind') == 'transfer':
        from collectors.history_transfer import parse_transfer_html
        return parse_transfer_html(html, **context, document_id=document_id)
    return parse_results_html(html, **context, document_id=document_id,
                              admission_source=source.get('admission_source', 'not_specified'))


def _fetch_source(source, directory):
    url = source['url']
    _check_school_url(url)
    if source.get('kind', 'admission_results') not in {'transfer', 'admission_results'}:
        raise ValueError('不支持的历史资料类型')
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; GradScopeResearch/1.0)'})
    opener = urllib.request.build_opener(_SchoolRedirect())
    with opener.open(request, timeout=25) as response:
        raw = response.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError('历史资料网页超过5MB限制')
    digest = hashlib.sha256(raw).hexdigest()
    filename = directory / f'{source["year"]}-{digest[:16]}.html'
    filename.write_bytes(raw)
    return {'id': 'history-' + digest[:20], 'sha256': digest, 'local_path': filename.as_posix()}, raw.decode('utf-8', errors='replace')


def _merge_history(previous, current):
    replaced_keys = {(document['url'], document.get('year')) for document in current['documents']}
    replaced_ids = {document['id'] for document in previous.get('documents', [])
                    if (document.get('url'), document.get('year')) in replaced_keys}
    result = {'metadata': {**previous.get('metadata', {}), **current['metadata']}}
    result['documents'] = [document for document in previous.get('documents', [])
                           if (document.get('url'), document.get('year')) not in replaced_keys] + current['documents']
    for field in ('admitted_statistics', 'score_lines', 'retest_rules', 'transfer_rules', 'notices'):
        def kept(row):
            evidence = row.get('evidence') or []
            sources = evidence if isinstance(evidence, list) else [evidence]
            return not any(item.get('document_id') in replaced_ids for item in sources if isinstance(item, dict))
        new_ids = {row['id'] for row in current[field] if 'id' in row}
        result[field] = [row for row in previous.get(field, []) if kept(row) and row.get('id') not in new_ids] + current[field]
    return result


def collect_sources(sources, output, snapshot_dir):
    directory = Path(snapshot_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {'metadata': {'checked_at': datetime.now(timezone.utc).isoformat(),
                            'notes': ['仅收录可追溯公示样本，不代表全国历史数据完整。']},
               'documents': [], 'admitted_statistics': [], 'score_lines': [],
               'retest_rules': [], 'transfer_rules': [], 'notices': []}
    for source in sources:
        url = source['url']
        document, html = _fetch_source(source, directory)
        result = _parse_source(html, source, document['id'])
        if not result['source_table_complete'] and Path(output).exists():
            raise ValueError('历史公示的结构或完整性变化，保留既有资料，需核验原始快照后重试')
        payload['documents'].append({**document, 'unit_id': source['unit_id'], 'year': source['year'],
                                     'title': source['title'], 'url': url,
                                     'kind': source.get('kind', 'admission_results'),
                                     'checked_at': payload['metadata']['checked_at'],
                                     'status': 'parsed' if result['source_table_complete'] else 'needs_review',
                                     'notes': result['warnings'], 'table_rows': result['seen_rows']})
        payload['admitted_statistics'].extend(result.get('admitted_statistics', []))
        payload['transfer_rules'].extend(result.get('transfer_rules', []))
        print(json.dumps({'year': source['year'], 'department': source['department'], 'rows': result['seen_rows'],
                          'groups': len(result.get('admitted_statistics', [])),
                          'transfer_rules': len(result.get('transfer_rules', [])),
                          'warnings': result['warnings']}, ensure_ascii=False), flush=True)
        time.sleep(.4)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        previous = json.loads(target.read_text(encoding='utf-8'))
        payload = _merge_history(previous, payload)
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)
    return payload


def main():
    parser = argparse.ArgumentParser(description='收录已核实官方复试结果网页的匿名分年统计')
    parser.add_argument('--sources', required=True)
    parser.add_argument('--output', default='data/history.json')
    parser.add_argument('--snapshot-dir', default='data/snapshots/history')
    args = parser.parse_args()
    sources = json.loads(Path(args.sources).read_text(encoding='utf-8'))
    collect_sources(sources, args.output, args.snapshot_dir)


if __name__ == '__main__':
    main()
