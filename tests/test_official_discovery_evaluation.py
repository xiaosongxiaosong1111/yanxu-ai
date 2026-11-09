"""Acceptance scoring must never turn a known URL or a title into read evidence."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tools.evaluate_official_discovery import (
    FIXTURES, RecordingTransport, ReplayTransport, assess_result, evaluate_case, load_cases, main, select_heldout,
)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.case = {'unit': {'id': 'u', 'name': '示例大学'}, 'requested_year': 2027,
                     'kind': 'charter', 'expected': {'article': 'https://grad.example.edu.cn/2027.htm'}}
        self.raw = ('示例大学2027年硕士研究生招生章程\n' +
                    '报考条件和报名办法由学校公布。考试与复试要求按招生计划执行。' * 25).encode()
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.source = {'url': self.case['expected']['article'], 'title': '示例大学2027年硕士研究生招生章程',
                       'year': 2027, 'status': 'official_original_read', 'excerpt': self.raw.decode(),
                       'checked_at': '2026-11-09T10:00:00Z', 'sha256': self.digest,
                       'knowledge_id': 'k_' + '1' * 24, 'original_available': True,
                       'knowledge_quality': {'state': 'accepted'}}
        self.recordings = [{'url': self.source['url'], 'status': 200, 'sha256': self.digest,
                            'checked_at': self.source['checked_at'], 'raw': self.raw}]

    def result(self, source=None):
        return {'entry_verified': True, 'entry_sources': [{'url': 'https://grad.example.edu.cn/'}],
                'document_sources': [source or self.source], 'navigation_sources': [], 'errors': []}

    def test_requested_document_body_and_hash_are_independent_measures(self):
        value = assess_result(self.case, self.result(), self.recordings)
        self.assertTrue(value['entry_verified'])
        self.assertTrue(value['requested_document_located'])
        self.assertTrue(value['requested_body_read'])
        self.assertTrue(value['source_evidence_complete'])
        self.assertTrue(value['expected_article_discovered'])

    def test_title_and_footer_do_not_count_as_charter_body(self):
        source = {**self.source, 'excerpt': self.source['title'] + '\n版权所有 示例大学'}
        value = assess_result(self.case, self.result(source), self.recordings)
        self.assertTrue(value['requested_document_located'])
        self.assertFalse(value['requested_body_read'])

    def test_homepage_listing_headlines_cannot_be_counted_as_article_body(self):
        raw = ('<title>示例大学研究生院</title><h1>研究生院</h1>'
               '<a href="/2027.htm">示例大学2027年硕士研究生招生章程</a>' + self.raw.decode()).encode()
        source = {**self.source, 'sha256': hashlib.sha256(raw).hexdigest()}
        record = {**self.recordings[0], 'sha256': source['sha256'], 'raw': raw,
                  'headers': {'Content-Type': 'text/html;charset=utf-8'}}
        value = assess_result(self.case, self.result(source), [record])
        self.assertTrue(value['requested_document_located'])
        self.assertFalse(value['requested_body_read'])

    def test_long_site_menu_plus_short_attachment_notice_is_not_substantive_body(self):
        excerpt = ('栏目导航：报名 考试 复试\n' * 50 + self.source['title'] +
                   '\n现将我校招生简章及专业目录公布，请按要求报名和填写复试科目。\n附件【2027年招生简章.pdf】')
        self.assertFalse(assess_result(self.case, self.result({**self.source, 'excerpt': excerpt}),
                                      self.recordings)['requested_body_read'])

    def test_historical_charter_never_becomes_requested_year_success(self):
        source = {**self.source, 'year': 2026, 'title': '示例大学2026年硕士研究生招生章程',
                  'excerpt': self.raw.decode().replace('2027', '2026')}
        value = assess_result(self.case, self.result(source), self.recordings)
        self.assertFalse(value['requested_document_located'])
        self.assertFalse(value['requested_body_read'])
        self.assertTrue(value['historical_document_observed'])

    def test_missing_year_is_not_filled_from_the_request(self):
        source = {**self.source, 'year': None, 'title': '示例大学硕士招生章程',
                  'excerpt': self.raw.decode().replace('2027', '')}
        self.assertFalse(assess_result(self.case, self.result(source), self.recordings)['requested_body_read'])

    def test_placeholder_and_undergraduate_notice_do_not_match(self):
        for title in ('示例大学2027年硕士招生简章预计发布', '示例大学2027年本科招生章程',
                      '示例大学2027年硕士研究生招生简章（未发布）',
                      '示例大学2027年接收推荐免试攻读硕士学位研究生招生简章',
                      '示例大学2027年推免生招生章程'):
            with self.subTest(title=title):
                source = {**self.source, 'title': title}
                self.assertFalse(assess_result(self.case, self.result(source), self.recordings)['requested_document_located'])

    def test_http_and_https_default_port_versions_match_the_same_expected_document(self):
        source = {**self.source, 'url': self.source['url'].replace('https://', 'http://')}
        records = [{**self.recordings[0], 'url': source['url']}]
        self.assertTrue(assess_result(self.case, self.result(source), records)['expected_article_discovered'])

    def test_actual_chinese_year_and_master_charter_title_variants_match(self):
        for title in ('重庆医科大学二〇二七年硕士研究生招生章程及专业目录',
                      '复旦大学2027年招收攻读硕士学位研究生章程',
                      '青海师范大学2027年招收攻读硕士学位研究生简章'):
            with self.subTest(title=title):
                value = assess_result(self.case, self.result({**self.source, 'title': title}), self.recordings)
                self.assertTrue(value['requested_document_located'])
        for title in ('示例大学二〇二六年硕士招生简章', '示例大学2027年博士研究生章程',
                      '示例大学2027年章程'):
            with self.subTest(title=title):
                self.assertFalse(assess_result(self.case, self.result({**self.source, 'title': title}),
                                               self.recordings)['requested_document_located'])

    def test_metadata_without_matching_original_hash_is_not_complete_evidence(self):
        value = assess_result(self.case, self.result({**self.source, 'sha256': '0' * 64}), self.recordings)
        self.assertFalse(value['source_evidence_complete'])

    def test_read_body_without_saved_original_is_not_complete_source_evidence(self):
        source = {**self.source, 'knowledge_id': None, 'original_available': False}
        value = assess_result(self.case, self.result(source), self.recordings)
        self.assertTrue(value['requested_body_read'])
        self.assertFalse(value['source_evidence_complete'])

    def test_year_without_qualified_body_does_not_pass_evidence(self):
        source = {**self.source, 'status': 'official_link', 'excerpt': ''}
        value = assess_result(self.case, self.result(source), self.recordings)
        self.assertTrue(value['requested_document_located'])
        self.assertFalse(value['requested_body_read'])
        self.assertFalse(value['source_evidence_complete'])

    def test_http_metadata_without_replayed_raw_is_not_complete_evidence(self):
        records = [{key: value for key, value in self.recordings[0].items() if key != 'raw'}]
        self.assertFalse(assess_result(self.case, self.result(), records)['source_evidence_complete'])

    def test_expected_final_url_is_only_a_scoring_answer(self):
        value = assess_result(self.case, {'entry_verified': False, 'document_sources': [],
                                         'navigation_sources': [], 'errors': []}, self.recordings)
        self.assertFalse(value['expected_article_discovered'])
        self.assertFalse(value['requested_body_read'])

    def test_fetcher_restriction_stays_distinct_from_discovery_failure(self):
        result = {'entry_verified': True, 'entry_sources': [{'url': 'https://grad.example.edu.cn/'}],
                  'document_sources': [], 'navigation_sources': [self.source],
                  'errors': [{'code': 'restricted', 'url': self.source['url'], 'message': 'HTTP 403'}]}
        value = assess_result(self.case, result, [])
        self.assertTrue(value['requested_document_located'])
        self.assertFalse(value['requested_body_read'])
        self.assertIn('restricted', value['error_codes'])

    def test_heldout_selection_is_deterministic_excludes_regressions_and_spans_provinces(self):
        units = [{'id': str(i), 'name': f'学校{i}', 'province': str(i % 31),
                  'official_url': f'https://u{i}.example.edu.cn/'} for i in range(110)]
        first = select_heldout(units, {'0', '1', '2'}, 40)
        second = select_heldout(list(reversed(units)), {'0', '1', '2'}, 40)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 40)
        self.assertTrue({'0', '1', '2'}.isdisjoint(row['id'] for row in first))
        self.assertEqual(len({row['province'] for row in first}), 31)

    def test_replay_rejects_missing_and_tampered_recordings_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_path = Path(directory) / 'page.html'
            raw_path.write_bytes(self.raw)
            record = {'url': self.source['url'], 'status': 200, 'sha256': self.digest,
                      'raw_path': 'page.html', 'headers': {'Content-Type': 'text/html'}}
            replay = ReplayTransport([record], directory)
            self.assertFalse(replay.records[record['url']]['integrity_verified'])
            response = replay(self.source['url'], ('example.edu.cn', 443, ['93.184.216.34']), 1)
            self.assertEqual(response.read(), self.raw)
            with self.assertRaisesRegex(ValueError, 'not recorded'):
                replay('https://example.edu.cn/other', (), 1)
            raw_path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                replay(self.source['url'], (), 1)

    def test_recording_keeps_response_bytes_hash_headers_and_request_time(self):
        from tools.evaluate_official_discovery import ReplayResponse
        with tempfile.TemporaryDirectory() as directory:
            transport = RecordingTransport(directory, interval=2)
            with patch('public_sources._transport', return_value=ReplayResponse(
                    self.raw, 200, {'Content-Type': 'text/html', 'Set-Cookie': 'excluded'})):
                response = transport(self.source['url'], ('example.edu.cn', 443, ['93.184.216.34']), 5)
                self.assertEqual(response.read(10) + response.read(), self.raw)
                response.close()
                response.close()
            self.assertEqual(len(transport.records), 1)
            record = transport.records[0]
            self.assertEqual(record['sha256'], self.digest)
            self.assertEqual((Path(directory) / record['raw_path']).read_bytes(), self.raw)
            self.assertTrue(record['checked_at'])
            self.assertTrue(record['pid'])
            self.assertNotIn('Set-Cookie', record['headers'])
            self.assertEqual(len((Path(directory) / 'requests.jsonl').read_text('utf-8').splitlines()), 1)

    def test_replay_cannot_read_a_response_outside_the_fixture_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            replay = ReplayTransport([{'url': self.source['url'], 'raw_path': '../outside.html',
                                      'sha256': self.digest, 'status': 200}], directory)
            with self.assertRaisesRegex(ValueError, 'outside fixture'):
                replay(self.source['url'], (), 1)

    def test_partial_http200_body_timeout_cannot_become_a_replay_success(self):
        from public_sources import SourceError
        from tools.evaluate_official_discovery import ReplayResponse
        response = ReplayResponse(self.raw)
        response.read = unittest.mock.Mock(side_effect=[self.raw[:10], TimeoutError('fixture timeout')])
        with tempfile.TemporaryDirectory() as directory, patch('public_sources._transport', return_value=response):
            recorder = RecordingTransport(directory)
            wrapped = recorder(self.source['url'], ('example.edu.cn', 443, ['93.184.216.34']), 5)
            self.assertEqual(wrapped.read(10), self.raw[:10])
            with self.assertRaises(TimeoutError):
                wrapped.read(10)
            wrapped.close()
            record = recorder.records[0]
            self.assertFalse(record['complete'])
            self.assertEqual(record['status'], 200)
            with self.assertRaises(SourceError) as failure:
                ReplayTransport([record], directory)(self.source['url'], (), 1)
            self.assertEqual(failure.exception.code, 'timeout')

    def test_legacy_replay_keeps_actual_production_fetch_failure_blocked(self):
        from tools.evaluate_official_discovery import _legacy_completion
        record = {'url': self.source['url'], 'status': 200}
        report = {'rows': [{'result': {'errors': [{'url': self.source['url'], 'code': 'timeout'}]}}]}
        self.assertFalse(_legacy_completion([record], report)[0]['complete'])
        report['rows'][0]['result']['errors'][0]['code'] = 'too_large'
        self.assertFalse(_legacy_completion([record], report)[0]['complete'])
        report['rows'][0]['result']['errors'][0]['code'] = 'wrong_unit'
        self.assertTrue(_legacy_completion([record], report)[0]['complete'])

    def test_regression_fixture_keeps_32_real_cases_and_human_reading_limits(self):
        cases = load_cases()
        self.assertEqual(len(cases), 32)
        self.assertEqual(len({case['unit']['id'] for case in cases}), 32)
        self.assertEqual(sum(case['expected']['classification'] == 'historical_or_department_only' for case in cases), 5)
        self.assertEqual(sum(case['expected']['classification'] == 'target_original_unread' for case in cases), 2)
        for case in cases:
            with self.subTest(unit=case['unit']['id']):
                expected_url = case['expected']['article']
                self.assertNotEqual(case['unit'].get('official_url'), expected_url)
                self.assertNotIn(expected_url, case.get('unconfirmed_candidates', []))
                self.assertNotIn(expected_url, [row['url'] for row in case.get('saved_declarations', [])])
                self.assertIn('human_read_status', case['expected'])

    def test_production_tool_discovers_article_from_homepage_over_recorded_http(self):
        homepage = 'https://grad.example.edu.cn/'
        case = {**self.case, 'unit': {**self.case['unit'], 'official_url': homepage}}
        html = ('<html><title>示例大学研究生院</title><h1>示例大学研究生招生</h1>'
                '<a href="/2027.htm">2027年硕士研究生招生章程</a></html>').encode()
        article = ('<html><title>示例大学2027年硕士研究生招生章程</title><h1>示例大学2027年硕士研究生招生章程</h1>'
                   '<article>' + self.raw.decode() + '</article></html>').encode()
        with tempfile.TemporaryDirectory() as directory:
            records = []
            for index, (url, raw) in enumerate(((homepage, html), (self.source['url'], article))):
                name = str(index) + '.html'
                (Path(directory) / name).write_bytes(raw)
                records.append({'url': url, 'status': 200, 'sha256': hashlib.sha256(raw).hexdigest(),
                                'checked_at': '2026-11-09T10:00:00Z', 'raw_path': name,
                                'headers': {'Content-Type': 'text/html; charset=utf-8'}})
            transport = ReplayTransport(records, directory)
            row = evaluate_case(case, transport, replay=True)
            self.assertTrue(row['metrics']['requested_body_read'], row)
            self.assertTrue(row['metrics']['source_evidence_complete'], row)
            document = row['result']['document_sources'][0]
            self.assertTrue(document.get('knowledge_id'), document)
            self.assertTrue(document.get('original_available'), document)
            self.assertIn('state', document.get('knowledge_quality', {}))
            self.assertEqual(transport.calls[0], homepage)
            self.assertIn(self.source['url'], transport.calls[1:])

    def test_cli_replay_writes_scope_and_integrity_report_without_a_service(self):
        with tempfile.TemporaryDirectory() as directory:
            replay = Path(directory) / 'replay'
            (replay / 'recordings').mkdir(parents=True)
            (replay / 'recordings.json').write_text('[]', 'utf-8')
            output = Path(directory) / 'report'
            result = {'stage': 'needs_review', 'entry_verified': False,
                      'errors': [{'code': 'no_authority_seed'}]}
            case = {**self.case, 'case_id': 'probe', 'cohort': 'regression32',
                    'starting_condition': 'unconfirmed_search_candidate'}
            with patch('tools.evaluate_official_discovery.load_cases', return_value=[case]), patch(
                    'research_tools.ResearchTools.run', return_value=result), redirect_stdout(io.StringIO()):
                code = main(['--out', str(output), '--replay', str(replay), '--heldout', '0'])
            report = json.loads((output / 'report.json').read_text('utf-8'))
            self.assertEqual(code, 0)
            self.assertEqual(report['mode'], 'replay')
            self.assertEqual(report['summary']['regression32']['total'], 1)
            self.assertEqual(report['summary']['regression32']['metrics']['requested_body_read'], 0)
            self.assertFalse(report['final_article_seeded'])
            self.assertEqual(report['paid_calls'], 0)
            self.assertEqual(report['user_databases_opened'], 0)
            self.assertIn('tools/evaluate_official_discovery.py', report['source_sha256'])

    def test_reviewed_binding_is_reported_separately_from_autonomous_recovery(self):
        case = next(row for row in load_cases() if row['unit']['id'] == '368127')
        with tempfile.TemporaryDirectory() as directory, patch('research_tools.ResearchTools.run', return_value={
                'entry_verified': True, 'entry_sources': [], 'errors': []}):
            row = evaluate_case(case, ReplayTransport([], directory), replay=True)
        self.assertEqual(row['original_starting_condition'], 'unconfirmed_search_candidate')
        self.assertEqual(row['starting_condition'], 'reviewed_curated_root')
        self.assertEqual(row['reviewed_root_binding']['source'], 'curated_evidence')
        self.assertFalse(row['metrics']['requested_body_read'])

    def test_real_bisu_homepage_action_script_and_article_replay_through_production(self):
        case = next(row for row in load_cases() if row['unit']['id'] == '367863')
        records = []
        for name in ('bisu-entry-record.json', 'bisu-article-record.json', 'bisu-recommendation-record.json'):
            saved = json.loads((FIXTURES / 'http' / name).read_text('utf-8'))['records']
            records.extend({**row, 'raw_path': 'http/' + row['raw_path']} for row in saved)
        script_path = FIXTURES / 'navigation' / 'bisu-action.js'
        script = json.loads(script_path.with_suffix('.js.record.json').read_text('utf-8'))
        records.append({**script, 'status': 200, 'raw_path': 'navigation/bisu-action.js',
                        'headers': {'Content-Type': script['content_type']}})
        transport = ReplayTransport(records, FIXTURES)
        row = evaluate_case(case, transport, replay=True)
        self.assertTrue(row['metrics']['expected_article_discovered'], row)
        self.assertTrue(row['metrics']['requested_body_read'], row)
        self.assertTrue(row['metrics']['source_evidence_complete'], row)
        actual = next(source for source in row['result']['document_sources']
                      if source['url'] == case['expected']['article'])
        self.assertTrue(actual.get('knowledge_id'), actual)
        self.assertTrue(actual.get('original_available'), actual)
        self.assertEqual(actual.get('knowledge_quality', {}).get('state'), 'accepted', actual)
        self.assertEqual(transport.calls[0], case['unit']['official_url'])
        self.assertIn(script['url'], transport.calls)
        self.assertLess(transport.calls.index(script['url']), transport.calls.index(case['expected']['article']))

    def test_unbound_cug_replays_chsi_pointer_then_actual_homepage_routing(self):
        case = next(row for row in load_cases() if row['unit']['id'] == '368369')
        self.assertFalse(case['unit'].get('official_url'))
        article = json.loads((FIXTURES / 'http/cug-2027.html.record.json').read_text('utf-8'))
        index = next(row for row in json.loads((FIXTURES / 'navigation/manifest.json').read_text('utf-8'))
                     if row['file'] == 'cug-index.html')
        records = [{**article, 'status': 200, 'raw_path': 'http/cug-2027.html',
                    'headers': {'Content-Type': article['content_type']}},
                   {**index, 'status': 200, 'raw_path': 'navigation/cug-index.html',
                    'checked_at': '2026-11-09T06:24:18+00:00',
                    'headers': {'Content-Type': 'text/html;charset=UTF-8'}}]
        transport = ReplayTransport(records, FIXTURES)
        row = evaluate_case(case, transport, replay=True)
        self.assertTrue(row['metrics']['entry_verified'], row)
        self.assertTrue(row['metrics']['expected_article_discovered'], row)
        self.assertEqual(transport.calls[0], case['expected']['entry'])
        self.assertIn(case['expected']['article'], transport.calls[1:])
        self.assertFalse(any('yz.chsi.com.cn' in url for url in transport.calls))


if __name__ == '__main__':
    unittest.main()
