import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collectors.history import _check_school_url, _SchoolRedirect, collect_sources, main, parse_results_html, score_summary


HEADER = '''<table><tr><th rowspan="2">复试专业</th><th rowspan="2">专业类型</th>
<th rowspan="2">学习方式</th><th rowspan="2">姓名</th><th rowspan="2">考生编号</th>
<th colspan="5">初试各科成绩及总分</th><th rowspan="2">复试成绩</th>
<th rowspan="2">录取成绩</th><th rowspan="2">是否拟录取</th><th rowspan="2">备注</th></tr>
<tr><th>政治</th><th>外语</th><th>业务1</th><th>业务2</th><th>总分</th></tr>'''


def row(name='TESTPERSON', initial=350, retest=80, final=75, admitted='是', notes=''):
    return f'''<tr><td>计算机科学与技术</td><td>学硕</td><td>全日制</td><td>{name}</td>
<td>PRIVATE-CANDIDATE-ID</td><td>60</td><td>70</td><td>110</td><td>110</td>
<td>{initial}</td><td>{retest}</td><td>{final}</td><td>{admitted}</td><td>{notes}</td></tr>'''


def parse(body, year=2026):
    return parse_results_html(HEADER + body + '</table>', unit_id='367949',
                              unit_name='南开大学', department='计算机学院', year=year,
                              url='https://cc.nankai.edu.cn/test/page.htm')


class HistoryTests(unittest.TestCase):
    def test_aggregate_only_explicit_admissions_and_never_exposes_identity(self):
        result = parse(row(initial=350) + row(name='OTHER', initial=370) + row(initial=390, admitted='否'))
        self.assertEqual(result['seen_rows'], 3)
        self.assertEqual(len(result['admitted_statistics']), 1)
        item = result['admitted_statistics'][0]
        self.assertEqual(item['sample_count'], 2)
        self.assertEqual(item['initial'], {'min': 350, 'max': 370, 'mean': 360, 'median': 360})
        self.assertEqual(item['year'], 2026)
        self.assertEqual(item['study_mode'], '全日制')
        self.assertFalse(item['is_full_admission_cohort'])
        self.assertIn('公示样本', item['cohort'])
        self.assertIn('business2', item['subject_scores'])
        payload = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('TESTPERSON', payload)
        self.assertNotIn('PRIVATE-CANDIDATE-ID', payload)

    def test_special_plans_and_unmarked_samples_are_separate(self):
        result = parse(row(initial=350) + row(initial=290, notes='退役大学生士兵计划'))
        self.assertEqual(len(result['admitted_statistics']), 2)
        self.assertEqual(sorted(item['initial']['min'] for item in result['admitted_statistics']), [290, 350])
        self.assertEqual(len({item['plan_type'] for item in result['admitted_statistics']}), 2)

    def test_missing_or_non_numeric_initial_is_not_zero(self):
        result = parse(row(initial='') + row(initial='缺考') + row(initial=350, final='不予加权'))
        self.assertEqual(result['admitted_statistics'][0]['sample_count'], 1)
        self.assertEqual(result['admitted_statistics'][0]['initial']['min'], 350)
        self.assertEqual(result['admitted_statistics'][0]['weighted_final'], None)
        self.assertEqual(result['rejected_rows'], 2)
        self.assertTrue(result['warnings'])

    def test_incomplete_table_is_not_claimed_complete(self):
        result = parse(row() + '<tr><td>计算机科学与技术</td><td>学硕</td></tr>')
        self.assertFalse(result['source_table_complete'])
        self.assertFalse(result['admitted_statistics'][0]['source_table_complete'])

    def test_publication_year_does_not_mix_admission_year(self):
        a, b = parse(row(initial=350), 2025), parse(row(initial=370), 2024)
        self.assertEqual(a['admitted_statistics'][0]['year'], 2025)
        self.assertEqual(b['admitted_statistics'][0]['year'], 2024)
        self.assertNotEqual(a['admitted_statistics'][0]['id'], b['admitted_statistics'][0]['id'])

    def test_duplicate_table_rows_do_not_double_the_sample(self):
        result = parse(row() + row())
        self.assertEqual(result['admitted_statistics'][0]['sample_count'], 1)
        self.assertEqual(result['duplicate_rows'], 1)
        self.assertFalse(result['source_table_complete'])

    def test_repeated_header_keeps_rows_before_and_after_it(self):
        table = HEADER + row(initial=350) + HEADER.replace('<table>', '') + row(initial=390) + '</table>'
        result = parse_results_html(table, unit_id='367949', unit_name='南开大学',
                                    department='计算机学院', year=2026, url='https://cc.nankai.edu.cn/test')
        self.assertEqual(result['seen_rows'], 2)
        self.assertEqual(result['admitted_statistics'][0]['sample_count'], 2)
        self.assertEqual(result['admitted_statistics'][0]['initial']['min'], 350)

    def test_explicit_transfer_publication_stays_a_separate_cohort(self):
        html = HEADER + row() + '</table>'
        base = dict(unit_id='367949', unit_name='南开大学', department='计算机学院',
                    year=2026, url='https://cc.nankai.edu.cn/test/page.htm')
        a = parse_results_html(html, **base)['admitted_statistics'][0]
        b = parse_results_html(html, **base, admission_source='transfer')['admitted_statistics'][0]
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual(b['admission_source'], 'transfer')
        self.assertIn('调剂', b['cohort'])

    def test_no_supported_header_has_no_invented_statistics(self):
        result = parse_results_html('<table><tr><td>未公布录取成绩</td></tr></table>',
                                    unit_id='x', unit_name='x', department='x', year=2026,
                                    url='https://example.edu.cn')
        self.assertEqual(result['admitted_statistics'], [])
        self.assertFalse(result['source_table_complete'])
        self.assertTrue(result['warnings'])

    def test_bad_year_rejected_and_numeric_summary_is_explicit(self):
        with self.assertRaises(ValueError):
            parse(row(), 0)
        self.assertIsNone(score_summary([]))
        self.assertEqual(score_summary([70, 80, 90]), {'min': 70, 'max': 90, 'mean': 80, 'median': 80})
        self.assertIsNone(score_summary(['缺考', '', None, float('nan')]))


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class HistoryCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'history.json'
        self.snapshots = self.root / 'snapshots'
        self.source = {'unit_id': '367949', 'unit_name': '南开大学', 'department': '计算机学院',
                       'year': 2026, 'title': '复试公示', 'url': 'https://cc.nankai.edu.cn/test/page.htm'}

    def collect(self, raw):
        with patch('collectors.history.urllib.request.build_opener') as build, patch('collectors.history.time.sleep'):
            build.return_value.open.return_value = Response(raw)
            result = collect_sources([self.source], self.output, self.snapshots)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, self.source['url'])
        return result

    def test_snapshot_hash_and_anonymous_output_are_saved(self):
        import hashlib
        raw = (HEADER + row() + '</table>').encode()
        result = self.collect(raw)
        document = result['documents'][0]
        self.assertEqual(document['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(Path(document['local_path']).read_bytes(), raw)
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8')), result)
        self.assertNotIn('PRIVATE-CANDIDATE-ID', self.output.read_text(encoding='utf-8'))
        self.assertFalse(self.output.with_suffix('.json.tmp').exists())

    def test_unsupported_page_keeps_document_and_reports_parse_failure(self):
        result = self.collect(b'<html>No admissions table</html>')
        self.assertEqual(result['admitted_statistics'], [])
        self.assertEqual(result['documents'][0]['status'], 'needs_review')
        self.assertTrue(result['documents'][0]['notes'])

    def test_oversized_response_does_not_destroy_existing_history(self):
        self.output.write_text('existing dataset', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.collect(b'x' * (5 * 1024 * 1024 + 1))
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'existing dataset')

    def test_changed_source_schema_does_not_replace_known_history(self):
        self.output.write_text('previous verified history', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.collect(b'<html>No admissions table</html>')
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'previous verified history')

    def test_transfer_sources_use_the_rule_parser_and_keep_admission_statistics_separate(self):
        self.source = {**self.source, 'kind': 'transfer'}
        headers = ['初试专业代码', '初试专业名称', '政治理论名称', '外国语名称', '业务课一名称',
                   '业务课二名称', '总分', '政治理论', '外国语', '业务课一', '业务课二']
        values = ['081200', '计算机科学与技术', '思想政治理论', '英语（二）', '数学（二）',
                  '计算机学科专业基础', '290', '50', '50', '75', '75']
        html = '<p>我院接收调剂专业为：密码（专业代码：145200）。</p><table>'
        html += ''.join('<tr>' + ''.join('<td>' + cell + '</td>' for cell in cells) + '</tr>'
                        for cells in (headers, values)) + '</table>'
        result = self.collect(html.encode())
        self.assertEqual(result['admitted_statistics'], [])
        self.assertEqual(result['documents'][0]['kind'], 'transfer')
        self.assertEqual(result['transfer_rules'][0]['program_code'], '145200')
        self.assertTrue(result['transfer_rules'][0]['requires_review'])

    def test_incomplete_transfer_refresh_also_preserves_existing_history(self):
        self.source = {**self.source, 'kind': 'transfer'}
        self.output.write_text('existing history and transfer rules', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.collect(b'<html>Maintenance</html>')
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'existing history and transfer rules')

    def test_partial_refresh_preserves_other_source_and_history_year(self):
        previous = {'metadata': {'notes': ['已核实的历史来源']},
                    'documents': [{'id': 'other-source', 'url': 'https://cc.nankai.edu.cn/2025/older.htm'}],
                    'admitted_statistics': [{'id': 'other-year', 'year': 2025,
                                             'evidence': {'document_id': 'other-source'}}],
                    'transfer_rules': [], 'retest_rules': [], 'score_lines': [], 'notices': []}
        self.output.write_text(json.dumps(previous), encoding='utf-8')
        result = self.collect((HEADER + row() + '</table>').encode())
        self.assertIn('other-year', [item['id'] for item in result['admitted_statistics']])
        self.assertIn('other-source', [item['id'] for item in result['documents']])

    def test_same_source_refresh_replaces_old_version_without_double_counting(self):
        previous = {'metadata': {}, 'documents': [{'id': 'old-version', 'url': self.source['url'], 'year': 2026}],
                    'admitted_statistics': [{'id': 'old-stat', 'evidence': {'document_id': 'old-version'}}]}
        self.output.write_text(json.dumps(previous), encoding='utf-8')
        result = self.collect((HEADER + row() + '</table>').encode())
        self.assertNotIn('old-stat', [item['id'] for item in result['admitted_statistics']])
        self.assertEqual(len(result['admitted_statistics']), 1)

    def test_official_url_reused_across_years_does_not_erase_previous_year(self):
        previous = {'metadata': {}, 'documents': [{'id': 'older-year', 'url': self.source['url'], 'year': 2025}],
                    'admitted_statistics': [{'id': 'older-stat', 'year': 2025,
                                             'evidence': {'document_id': 'older-year'}}]}
        self.output.write_text(json.dumps(previous), encoding='utf-8')
        result = self.collect((HEADER + row() + '</table>').encode())
        self.assertIn('older-stat', [item['id'] for item in result['admitted_statistics']])
        self.assertEqual(len(result['admitted_statistics']), 2)

    def test_only_configured_school_hosts_and_normal_ports_are_accepted(self):
        for url in ('file:///tmp/x', 'https://nankai.edu.cn.evil.example/test',
                    'http://user:pass@cc.nankai.edu.cn/x', 'https://cc.nankai.edu.cn:8443/x'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _check_school_url(url)
        _check_school_url('https://cc.nankai.edu.cn/page.htm')
        with self.assertRaises(ValueError):
            _SchoolRedirect().redirect_request(None, None, 302, 'found', {}, 'https://127.0.0.1/')

    def test_cli_uses_explicit_source_file(self):
        config = self.root / 'sources.json'
        config.write_text(json.dumps([self.source]), encoding='utf-8')
        with patch('sys.argv', ['history', '--sources', str(config), '--output', str(self.output),
                                '--snapshot-dir', str(self.snapshots)]), patch('collectors.history.collect_sources') as collect:
            main()
        collect.assert_called_once_with([self.source], str(self.output), str(self.snapshots))


if __name__ == '__main__':
    unittest.main()
