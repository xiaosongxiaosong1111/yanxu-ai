import hashlib
from copy import deepcopy
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from collectors.catalog_batch import collect_school, collect_manifest, save_progress, unit_scope_supported, ingest_verified_batch
from collectors.catalog_html import parse_catalog_html
from service_verification import SourceVerifier


class CatalogBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'data' / 'snapshots'
        self.unit = {'id': '100', 'name': '测试大学', 'official_url': 'https://www.a.edu.cn'}
        self.raw = '<h1>测试大学2027年硕士研究生招生专业目录</h1><p>原始目录正文</p>'.encode()
        self.path = self.root / 'original.html'
        self.path.write_bytes(self.raw)
        self.source = {'url': 'https://yz.a.edu.cn/catalog', 'title': '测试大学2027年硕士招生专业目录',
                       'parser': 'html_table_v1', 'source_path': str(self.path),
                       'sha256': hashlib.sha256(self.raw).hexdigest()}
        self.spec = {'unit_id': '100', 'year': 2027, 'sources': [self.source]}
        self.program = {'id': 'p', 'unit_id': '100', 'year': 2027, 'code': '081200', 'name': '计算机科学与技术',
                        'department': '计算机学院', 'direction': '00 不区分研究方向', 'study_mode': '全日制',
                        'subjects': [{'code': '408', 'name': '计算机学科专业基础', 'scope': 'national', 'exam_unit': 1}]}
        self.parse = Mock(return_value={'programs': [self.program], 'expected_records': 1, 'issues': [], 'retest_rules': []})

    def collect(self, **kwargs):
        return collect_school(self.spec, self.unit, self.directory, project_root=self.root, parser=self.parse, **kwargs)

    def test_offline_copies_exact_originals_and_returns_existing_schema(self):
        entry = self.collect(offline=True)
        self.assertEqual(entry['programs'], [self.program])
        self.assertEqual(entry['status']['status'], 'verified')
        document = entry['documents'][0]
        self.assertEqual(document['catalog_parser'], 'html_table_v1')
        self.assertEqual((self.root / document['local_path']).read_bytes(), self.raw)
        self.assertEqual(document['sha256'], self.source['sha256'])
        self.assertEqual(self.source['source_path'], str(self.path))

    def test_local_hash_and_official_ownership_are_mandatory(self):
        for mutation in [{'sha256': '0' * 64}, {'url': 'https://other.edu.cn/catalog'}]:
            with self.subTest(mutation=mutation):
                self.spec['sources'] = [{**self.source, **mutation}]
                entry = self.collect(offline=True)
                self.assertEqual(entry['programs'], [])
                self.assertEqual(entry['status']['status'], 'failed')

    def test_incomplete_and_scope_limited_sources_remain_partial(self):
        self.parse.return_value = {**self.parse.return_value, 'issues': ['1行未解析'], 'expected_records': 2}
        self.assertEqual(self.collect(offline=True)['status']['status'], 'partial')
        self.parse.return_value = {**self.parse.return_value, 'issues': [], 'expected_records': 1}
        self.path.write_text('<h1>测试大学计算机学院2027年硕士招生专业目录</h1>', 'utf-8')
        self.spec['sources'][0]['sha256'] = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(self.collect(offline=True)['status']['status'], 'partial')

    def test_failed_source_does_not_stop_other_schools(self):
        bad = {**self.spec, 'sources': [{**self.source, 'sha256': '0' * 64}]}
        entries = collect_manifest({'units': [self.unit]}, {'schools': [bad, self.spec]}, self.directory,
                                   project_root=self.root, offline=True, parser=self.parse)
        self.assertEqual([entry['status']['status'] for entry in entries], ['failed', 'verified'])

    def test_duplicate_sources_cannot_inflate_records(self):
        self.spec['sources'] = [self.source, dict(self.source)]
        entry = self.collect(offline=True)
        self.assertEqual(len(entry['documents']), 1)
        self.assertEqual(len(entry['programs']), 1)

    def test_progress_merge_preserves_unrelated_history_and_fees(self):
        entry = self.collect(offline=True)
        output = self.root / 'batch.json'
        previous = {'metadata': {}, 'programs': [], 'documents': [], 'score_lines': [{'id': 'line'}],
                    'admitted_statistics': [{'id': 'sample'}], 'notices': [{'id': 'notice'}]}
        merged = save_progress(output, [entry], previous)
        self.assertEqual(merged['score_lines'], previous['score_lines'])
        self.assertEqual(merged['admitted_statistics'], previous['admitted_statistics'])
        self.assertEqual(json.loads(output.read_text('utf-8')), merged)
        self.assertFalse(merged['metadata']['catalog_complete'])

    def test_scope_requires_original_school_year_and_unqualified_catalog_title(self):
        self.assertTrue(unit_scope_supported(self.unit, 2027, [('html_table_v1', self.raw.decode())]))
        self.assertFalse(unit_scope_supported(self.unit, 2027, [('html_table_v1', '<h1>测试大学2026年硕士招生专业目录</h1>')]))
        self.assertFalse(unit_scope_supported(self.unit, 2027, [('html_table_v1', '<h1>其他大学2027年硕士招生专业目录</h1>')]))
        self.assertFalse(unit_scope_supported(self.unit, 2027, [('html_table_v1', '<h1>测试大学2027年全日制专业学位招生目录</h1>')]))

    def test_separate_exam_columns_replay_without_fabricated_circled_quotes(self):
        html = '<h1>测试大学2027年硕士研究生招生专业目录</h1><table><tr><th>学院</th><th>专业</th><th>研究方向</th><th>学习方式</th>'
        html += ''.join(f'<th>考试科目{slot}</th>' for slot in range(1, 5))
        html += '</tr><tr><td>001计算机学院</td><td>081200计算机科学与技术</td><td>00不区分方向</td><td>全日制</td>'
        html += '<td>101思想政治理论</td><td>201英语（一）</td><td>301数学（一）</td><td>408计算机学科专业基础</td></tr></table>'
        self.path.write_text(html, 'utf-8')
        document = {'id': 'd', 'url': self.source['url'], 'unit_id': '100', 'year': 2027,
                    'kind': 'catalog', 'status': 'parsed', 'local_path': self.path.name,
                    'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest(), 'catalog_parser': 'html_table_v1'}
        parsed = parse_catalog_html(html, self.unit, 2027, document)
        self.assertEqual(parsed['issues'], [])
        self.assertEqual(len(parsed['programs']), 1)
        verifier = SourceVerifier(self.root)
        self.assertTrue(verifier.program(parsed['programs'][0], {'d': document}, self.unit))
        self.assertTrue(verifier.unit_reconciles(self.unit, 2027, parsed['programs'], {'d': document}))
        wrong = deepcopy(parsed['programs'])
        wrong[0]['subjects'][2]['code'] = '999'
        self.assertFalse(verifier.program(wrong[0], {'d': document}, self.unit))
        self.assertFalse(verifier.unit_reconciles(self.unit, 2027, wrong, {'d': document}))

    def test_real_national_code_name_contradiction_is_documented_but_never_reconciles_complete(self):
        html = '<h1>测试大学2027年硕士研究生招生专业目录</h1><table><tr><th>学院</th><th>专业</th><th>研究方向</th><th>学习方式</th><th>初试科目</th></tr>'
        html += '<tr><td>010环境学院</td><td>083000环境科学与工程</td><td>01环境工程</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（二）④801环境工程</td></tr></table>'
        self.path.write_text(html, 'utf-8')
        document = {'id': 'd', 'url': self.source['url'], 'unit_id': '100', 'year': 2027,
            'kind': 'catalog', 'status': 'parsed', 'local_path': self.path.name,
            'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest(), 'catalog_parser': 'html_table_v1'}
        parsed = parse_catalog_html(html, self.unit, 2027, document)
        self.assertEqual(len(parsed['programs']), 1)
        row = parsed['programs'][0]
        verifier = SourceVerifier(self.root)
        self.assertTrue(verifier.program(row, {'d': document}, self.unit))
        self.assertFalse(verifier.unit_reconciles(self.unit, 2027, [row], {'d': document}))
        without_issues = {key: value for key, value in row.items() if key != 'subject_identity_issues'}
        self.assertFalse(verifier.unit_reconciles(self.unit, 2027, [without_issues], {'d': document}))

    def _zsml_sources(self):
        from collectors.catalog_zsml import parse_zsml_html
        from tests.test_catalog_zsml import HTML, TREE, UNIT
        documents = {}
        for identifier, raw, url, parser in [
            ('tree', json.dumps(TREE, ensure_ascii=False), 'https://gsas.ncu.edu.cn/zsml/getTreeData', 'zsml_tree'),
            ('page', HTML, 'https://gsas.ncu.edu.cn/zsml/sszsml/zsmlYjfx/2027/001/030100?ksfs=21', 'zsml_table')]:
            path = self.root / (identifier + ('.json' if identifier == 'tree' else '.html'))
            path.write_text(raw, 'utf-8')
            documents[identifier] = {'id': identifier, 'url': url, 'unit_id': UNIT['id'], 'year': 2027,
                'kind': 'catalog', 'status': 'parsed', 'local_path': path.name,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'catalog_parser': parser}
        parsed = parse_zsml_html(HTML, UNIT, '001 法学院', 2027, documents['page'])
        return UNIT, parsed['programs'], documents

    def test_zsml_replay_uses_the_saved_tree_and_initial_exam_headers(self):
        unit, programs, documents = self._zsml_sources()
        verifier = SourceVerifier(self.root)
        self.assertTrue(all(verifier.program(program, documents, unit) for program in programs))
        self.assertTrue(verifier.unit_reconciles(unit, 2027, programs, documents))
        for field, value in [('department', '假的学院'), ('code', '030200')]:
            wrong = deepcopy(programs)
            wrong[0][field] = value
            self.assertFalse(verifier.program(wrong[0], documents, unit))
            self.assertFalse(verifier.unit_reconciles(unit, 2027, wrong, documents))
        wrong = deepcopy(programs)
        wrong[0]['subjects'] = [subject for subject in wrong[0]['subjects'] if subject['code'] != '203']
        self.assertFalse(verifier.program(wrong[0], documents, unit))

    def test_tree_replay_reuses_the_verified_bytes_and_rechecks_changed_context(self):
        from collectors.catalog_zsml import parse_zsml_tree
        unit, programs, documents = self._zsml_sources()
        verifier = SourceVerifier(self.root)
        with patch('collectors.catalog_zsml.parse_zsml_tree', wraps=parse_zsml_tree) as parse:
            self.assertTrue(verifier.unit_reconciles(unit, 2027, programs, documents))
            self.assertTrue(all(verifier.program(program, documents, unit) for program in programs))
            self.assertEqual(parse.call_count, 1)
            changed = deepcopy(documents)
            tree_path = self.root / changed['tree']['local_path']
            tree_path.write_text(tree_path.read_text('utf-8').replace('法学院', '其他学院'), 'utf-8')
            changed['tree']['sha256'] = hashlib.sha256(tree_path.read_bytes()).hexdigest()
            self.assertFalse(verifier.program(programs[0], changed, unit))
            self.assertEqual(parse.call_count, 2)
            changed['tree']['url'] = 'https://other.edu.cn/zsml/getTreeData'
            self.assertFalse(verifier.program(programs[0], changed, unit))

    def test_same_document_quotes_share_one_original_read_per_validation(self):
        unit, programs, documents = self._zsml_sources()
        verifier = SourceVerifier(self.root)
        with patch.object(verifier, 'read', wraps=verifier.read) as read:
            quotes = verifier.evidence_quotes(programs[0]['evidence'], programs[0], documents, unit)
            self.assertEqual(len(quotes), len(programs[0]['evidence']))
            self.assertEqual(read.call_count, 1)

    def test_zsml_missing_tree_missing_page_duplicate_source_and_wrong_endpoint_fail_closed(self):
        unit, programs, documents = self._zsml_sources()
        for missing in ['tree', 'page']:
            selected = {key: value for key, value in documents.items() if key != missing}
            self.assertFalse(SourceVerifier(self.root).unit_reconciles(unit, 2027, programs, selected))
            self.assertFalse(SourceVerifier(self.root).program(programs[0], selected, unit))
        duplicate = {**documents, 'duplicate': {**documents['page'], 'id': 'duplicate'}}
        self.assertFalse(SourceVerifier(self.root).unit_reconciles(unit, 2027, programs, duplicate))
        wrong = deepcopy(documents)
        wrong['page']['url'] = wrong['page']['url'].replace('/030100?', '/030200?')
        for program in programs:
            program['evidence'] = [{**item, 'url': wrong['page']['url']} for item in program['evidence']]
            program['exam_evidence']['url'] = wrong['page']['url']
        self.assertFalse(SourceVerifier(self.root).program(programs[0], wrong, unit))
        self.assertFalse(SourceVerifier(self.root).unit_reconciles(unit, 2027, programs, wrong))

    def test_html_partial_department_is_not_a_whole_university_catalog(self):
        html = '<h1>测试大学计算机学院2027年硕士招生专业目录</h1><table><tr><th>学院</th><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr><tr><td>001计算机学院</td><td>081200计算机科学与技术</td><td>00不区分方向</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr></table>'
        self.path.write_text(html, 'utf-8')
        document = {'id': 'd', 'url': self.source['url'], 'unit_id': '100', 'year': 2027,
            'kind': 'catalog', 'status': 'parsed', 'local_path': self.path.name,
            'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest(), 'catalog_parser': 'html_table_v1'}
        parsed = parse_catalog_html(html, self.unit, 2027, document)
        verifier = SourceVerifier(self.root)
        self.assertTrue(verifier.program(parsed['programs'][0], {'d': document}, self.unit))
        self.assertFalse(verifier.unit_reconciles(self.unit, 2027, parsed['programs'], {'d': document}))

    def test_explicit_html_replay_rejects_wrong_chsi_school_even_with_real_quotes(self):
        self.test_separate_exam_columns_replay_without_fabricated_circled_quotes()
        html = self.path.read_text('utf-8')
        url = 'https://yz.chsi.com.cn/sch/viewZszc--schId-999.dhtml'
        document = {'id': 'd', 'url': url, 'unit_id': '100', 'year': 2027,
            'kind': 'catalog', 'status': 'parsed', 'local_path': self.path.name,
            'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest(), 'catalog_parser': 'html_table_v1'}
        parsed = parse_catalog_html(html, self.unit, 2027, document)
        verifier = SourceVerifier(self.root)
        self.assertFalse(verifier.program(parsed['programs'][0], {'d': document}, self.unit))
        self.assertFalse(verifier.unit_reconciles(self.unit, 2027, parsed['programs'], {'d': document}))

    def test_explicit_program_level_scopes_follow_the_original_columns(self):
        html = '<h1>测试大学2027年硕士招生专业目录</h1><table><tr><th>专业</th><th>学习方式</th><th>考试科目</th></tr><tr><td>0812计算机科学与技术</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr></table>'
        self.path.write_text(html, 'utf-8')
        document = {'id': 'd', 'url': self.source['url'], 'unit_id': '100', 'year': 2027,
            'kind': 'catalog', 'status': 'parsed', 'local_path': self.path.name,
            'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest(), 'catalog_parser': 'html_table_v1'}
        parsed = parse_catalog_html(html, self.unit, 2027, document)
        self.assertEqual(parsed['issues'], [])
        program = parsed['programs'][0]
        self.assertEqual(program['code_level'], 'discipline')
        self.assertEqual(program['direction'], '')
        self.assertEqual(program['direction_scope'], 'program')
        self.assertEqual(program['department'], '')
        self.assertEqual(program['department_scope'], 'unit')
        verifier = SourceVerifier(self.root)
        self.assertTrue(verifier.program(program, {'d': document}, self.unit))
        self.assertTrue(verifier.unit_reconciles(self.unit, 2027, [program], {'d': document}))
        for field, value in [('code_level', ''), ('department', '计算机学院'), ('direction', '00 不区分方向'), ('direction_scope', ''), ('department_scope', '')]:
            wrong = {**program, field: value}
            self.assertFalse(verifier.program(wrong, {'d': document}, self.unit), field)
            self.assertFalse(verifier.unit_reconciles(self.unit, 2027, [wrong], {'d': document}), field)
        self.assertFalse(verifier.program(program, {'d': {**document, 'catalog_parser': None}}, self.unit))

    def test_html_metadata_cannot_change_the_source_year(self):
        self.test_explicit_program_level_scopes_follow_the_original_columns()
        self.path.write_text(self.path.read_text('utf-8').replace('2027', '2026'), 'utf-8')
        document = {'id': 'd', 'url': self.source['url'], 'unit_id': '100', 'year': 2027,
            'kind': 'catalog', 'status': 'parsed', 'local_path': self.path.name,
            'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest(), 'catalog_parser': 'html_table_v1'}
        parsed = parse_catalog_html(self.path.read_text('utf-8'), self.unit, 2027, document)
        verifier = SourceVerifier(self.root)
        self.assertFalse(verifier.program(parsed['programs'][0], {'d': document}, self.unit))
        self.assertFalse(verifier.unit_reconciles(self.unit, 2027, parsed['programs'], {'d': document}))
        self.path.write_text(self.path.read_text('utf-8') + '<footer>2027年版权所有</footer>', 'utf-8')
        document = {**document, 'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}
        parsed = parse_catalog_html(self.path.read_text('utf-8'), self.unit, 2027, document)
        self.assertFalse(SourceVerifier(self.root).program(parsed['programs'][0], {'d': document}, self.unit))

    def test_explicit_replay_keeps_identity_and_complete_exam_quotes_on_the_same_source_row(self):
        html = '<h1>测试大学2027年硕士招生专业目录</h1><table><tr><th>学院</th><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr>'
        html += '<tr><td>001计算机学院</td><td>081200计算机科学与技术</td><td>00不区分方向</td><td>全日制</td><td>①101思想政治理论②201英语（一）或203日语③301数学（一）④408计算机学科专业基础</td></tr>'
        html += '<tr><td>002软件学院</td><td>085400电子信息</td><td>01软件工程</td><td>非全日制</td><td>①101思想政治理论②204英语（二）③302数学（二）④801软件工程综合</td></tr></table>'
        self.path.write_text(html, 'utf-8')
        document = {'id': 'd', 'url': self.source['url'], 'unit_id': '100', 'year': 2027,
            'kind': 'catalog', 'status': 'parsed', 'local_path': self.path.name,
            'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest(), 'catalog_parser': 'html_table_v1'}
        parsed = parse_catalog_html(html, self.unit, 2027, document)
        self.assertEqual(parsed['issues'], [])
        first, other = parsed['programs']
        verifier = SourceVerifier(self.root)
        self.assertTrue(verifier.program(first, {'d': document}, self.unit))
        self.assertTrue(verifier.program(other, {'d': document}, self.unit))
        for field in ['evidence', 'exam_evidence']:
            wrong = {**first, field: deepcopy(other[field])}
            with self.subTest(field=field):
                self.assertFalse(verifier.program(wrong, {'d': document}, self.unit))
        wrong = {**first, 'exam_evidence': [{**first['exam_evidence'][0], 'quote': '201英语（一）'}]}
        self.assertFalse(verifier.program(wrong, {'d': document}, self.unit))
        wrong = {**first, 'evidence': [*first['evidence'], other['evidence'][1]]}
        self.assertFalse(verifier.program(wrong, {'d': document}, self.unit))
        reordered = {**first, 'evidence': list(reversed(first['evidence']))}
        self.assertTrue(verifier.program(reordered, {'d': document}, self.unit))

    def _incremental_entry(self, identifier, *, code='081200', subject='408计算机学科专业基础'):
        html = '<h1>测试大学计算机学院2027年硕士招生专业目录</h1><table><tr><th>学院</th><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr><tr><td>001计算机学院</td><td>' + code + '计算机科学与技术</td><td>00不区分方向</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④' + subject + '</td></tr></table>'
        path = self.root / (identifier + '.html')
        path.write_text(html, 'utf-8')
        document = {'id': identifier, 'url': self.source['url'] + '/' + identifier, 'unit_id': '100', 'year': 2027,
            'kind': 'catalog', 'status': 'parsed', 'local_path': path.name,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'catalog_parser': 'html_table_v1'}
        parsed = parse_catalog_html(html, self.unit, 2027, document)
        return {**parsed, 'documents': [document], 'status': {'unit_id': '100', 'unit_name': self.unit['name'], 'year': 2027,
            'status': 'partial', 'expected_records': 1, 'parsed_records': 1, 'verified_records': 0, 'notes': []}}

    def test_partial_ingest_appends_independently_verified_rows_without_losing_previous_data(self):
        old, fresh = self._incremental_entry('old'), self._incremental_entry('new', code='085400')
        previous = {'metadata': {}, 'programs': old['programs'], 'documents': old['documents'],
            'score_lines': [{'id': 'history'}], 'notices': [{'id': 'notice'}]}
        before = deepcopy(previous)
        merged = ingest_verified_batch({'units': [self.unit]}, [fresh], previous, project_root=self.root)
        self.assertEqual({row['id'] for row in merged['programs']}, {old['programs'][0]['id'], fresh['programs'][0]['id']})
        self.assertEqual(merged['score_lines'], previous['score_lines'])
        self.assertEqual(merged['notices'], previous['notices'])
        self.assertEqual(previous, before)
        status = merged['metadata']['unit_catalog_status'][0]
        self.assertEqual(status['status'], 'partial')
        self.assertEqual(status['accepted_records'], 1)
        self.assertEqual(status['retained_records'], 1)
        self.assertEqual(status['parsed_records'], 2)
        self.assertFalse(merged['metadata']['catalog_complete'])
        self.assertTrue(all(row.get('catalog_active', True) for row in merged['documents']))

    def test_partial_ingest_rejects_forged_rows_and_does_not_claim_complete(self):
        old, fresh = self._incremental_entry('old'), self._incremental_entry('new', code='085400')
        fresh['programs'][0]['subjects'][-1]['code'] = '999'
        fresh['status']['status'] = 'verified'
        previous = {'metadata': {}, 'programs': old['programs'], 'documents': old['documents']}
        merged = ingest_verified_batch({'units': [self.unit]}, [fresh], previous, project_root=self.root)
        self.assertEqual(merged['programs'], old['programs'])
        status = merged['metadata']['unit_catalog_status'][0]
        self.assertEqual(status['rejected_records'], 1)
        self.assertEqual(status['accepted_records'], 0)
        self.assertEqual(status['status'], 'needs_review')
        self.assertEqual(status['rejections'][0]['program_id'], fresh['programs'][0]['id'])

    def test_partial_ingest_preserves_conflicting_versions_instead_of_overwriting_identity(self):
        old, changed = self._incremental_entry('old'), self._incremental_entry('changed', subject='801软件工程综合')
        previous = {'metadata': {}, 'programs': old['programs'], 'documents': old['documents']}
        merged = ingest_verified_batch({'units': [self.unit]}, [changed], previous, project_root=self.root)
        self.assertEqual(len(merged['programs']), 1)
        self.assertEqual(merged['programs'][0]['subjects'][-1]['code'], '408')
        self.assertTrue(merged['programs'][0]['catalog_conflict'])
        self.assertTrue(merged['programs'][0]['stale'])
        self.assertEqual(merged['catalog_conflicts'][0]['incoming']['subjects'][-1]['code'], '801')
        self.assertEqual(merged['metadata']['unit_catalog_status'][0]['status'], 'needs_review')
        self.assertEqual(merged['metadata']['unit_catalog_status'][0]['conflicting_records'], 1)

    def test_same_catalog_identity_refresh_retains_independent_fees_and_is_atomic(self):
        old, refresh = self._incremental_entry('old'), self._incremental_entry('refresh')
        old['programs'][0] = {**old['programs'][0], 'tuition': {'amount': 8000, 'period': 'year'},
            'duration_years': 3, 'duration_evidence': {'document_id': 'fee-source'}}
        previous = {'metadata': {}, 'programs': old['programs'], 'documents': old['documents']}
        output = self.root / 'ingested.json'
        merged = ingest_verified_batch({'units': [self.unit]}, [refresh], previous, project_root=self.root, output=output)
        self.assertEqual(merged['programs'][0]['tuition']['amount'], 8000)
        self.assertEqual(merged['programs'][0]['duration_years'], 3)
        self.assertEqual(merged['programs'][0]['duration_evidence'], {'document_id': 'fee-source'})
        self.assertEqual(merged['programs'][0]['evidence'][0]['document_id'], 'refresh')
        self.assertEqual(json.loads(output.read_text('utf-8')), merged)
        self.assertFalse(output.with_suffix('.json.tmp').exists())

    def test_pdf_table_adapter_replays_raw_page_quotes_and_keeps_or_choices(self):
        from collectors.catalog_pdf_tables import parse_catalog_pdf_tables
        from tests.test_catalog_pdf_tables import layout
        source = layout()
        path = self.root / 'table.pdf'
        raw = b'%PDF-test-original'
        path.write_bytes(raw)
        document = {'id': 'pdf', 'unit_id': '100', 'year': 2027, 'kind': 'catalog', 'status': 'parsed',
            'url': 'https://yz.a.edu.cn/2027.pdf', 'local_path': path.name,
            'sha256': hashlib.sha256(raw).hexdigest(), 'catalog_parser': 'pdf_table_v1'}
        parsed = parse_catalog_pdf_tables(source, self.unit, 2027, document)
        self.assertEqual(parsed['issues'], [])
        with patch('collectors.catalog_pdf_tables.extract_pdf_layout', return_value=source), patch('collectors.documents.extract_pdf_text', return_value=source['raw_text']):
            verifier = SourceVerifier(self.root)
            self.assertTrue(all(verifier.program(row, {'pdf': document}, self.unit) for row in parsed['programs']))
            wrong = deepcopy(parsed['programs'][0])
            wrong['subjects'] = [row for row in wrong['subjects'] if row['code'] != '202']
            self.assertFalse(verifier.program(wrong, {'pdf': document}, self.unit))
            wrong = deepcopy(parsed['programs'][0])
            for subject in wrong['subjects']:
                if subject.get('choice_group'): subject['choice_group'] = 'changed-choice'
            self.assertFalse(verifier.program(wrong, {'pdf': document}, self.unit))

    def test_pdf_table_ingest_requires_external_semantic_approval_and_rechecks_revocation(self):
        import os
        from collectors.catalog_semantic_audit import semantic_parser_sha256, semantic_programs_sha256
        from collectors.catalog_pdf_tables import parse_catalog_pdf_tables
        from collectors.catalog_batch import _verified_row
        from tests.test_catalog_pdf_tables import layout
        source = layout()
        path = self.root / 'table.pdf'; path.write_bytes(b'%PDF-semantic-fixture')
        doc = {'id': 'pdf', 'unit_id': '100', 'year': 2027, 'kind': 'catalog', 'status': 'parsed',
            'url': 'https://yz.a.edu.cn/2027.pdf', 'local_path': path.name,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'catalog_parser': 'pdf_table_v1',
            'semantic_audit': {'status': 'approved'}}
        parsed = parse_catalog_pdf_tables(source, self.unit, 2027, doc)
        entry = {**parsed, 'documents': [doc], 'status': {'unit_id': '100', 'year': 2027, 'status': 'partial'}}
        (self.root / 'collectors').mkdir()
        (self.root / 'collectors/catalog_pdf_tables.py').write_bytes(Path('collectors/catalog_pdf_tables.py').read_bytes())
        (self.root / 'data').mkdir()
        policy = self.root / 'data/catalog_semantic_audits.json'
        approval = {'status': 'approved', 'source_sha256': doc['sha256'],
            'parser_sha256': semantic_parser_sha256(doc, self.root),
            'programs_sha256': semantic_programs_sha256(parsed['programs']),
            'reviewer': 'independent original grid review', 'reviewed_at': '2026-10-30T00:00:00+00:00'}
        with patch('collectors.catalog_pdf_tables.extract_pdf_layout', return_value=source), \
                patch('collectors.documents.extract_pdf_text', return_value=source['raw_text']):
            rejected = ingest_verified_batch({'units': [self.unit]}, [entry], project_root=self.root)
            self.assertEqual(rejected['programs'], [])
            self.assertIn('语义', rejected['metadata']['unit_catalog_status'][0]['rejections'][0]['reason'])
            policy.write_text(json.dumps({'audits': [approval]}), 'utf-8')
            accepted = ingest_verified_batch({'units': [self.unit]}, [entry], project_root=self.root)
            self.assertEqual(len(accepted['programs']), len(parsed['programs']))
            verifier = SourceVerifier(self.root)
            self.assertIsNotNone(_verified_row(parsed['programs'][0], {'pdf': doc}, self.unit, verifier))
            stamp = policy.stat()
            policy.write_text(json.dumps({'audits': []}), 'utf-8')
            os.utime(policy, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertIsNone(_verified_row(parsed['programs'][0], {'pdf': doc}, self.unit, verifier))
            retained = ingest_verified_batch({'units': [self.unit]}, [entry], accepted, project_root=self.root)
            self.assertEqual(retained['programs'], accepted['programs'])

    def test_pdf_v2_dispatch_replays_actual_fixture_and_uses_a_distinct_document_identity(self):
        from tests.test_catalog_pdf_variants import source
        from collectors.catalog_pdf_variants import parse_catalog_pdf_variants
        layout = source('btbu')
        unit = {**self.unit, 'id': '367888', 'name': '北京工商大学'}
        path = self.root / 'v2.pdf'; path.write_bytes(b'%PDF-v2-original-fixture')
        spec = {'sources': [{'parser': 'pdf_table_v2', 'url': 'https://yz.a.edu.cn/2027.pdf',
            'source_path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}
        with patch('collectors.catalog_pdf_variants.extract_pdf_variant_layout', return_value=layout), \
                patch('collectors.documents.extract_pdf_text', return_value=layout['raw_text']), \
                patch('collectors.catalog_batch.extract_pdf_text', return_value=layout['raw_text']), \
                patch('collectors.catalog_pdf_variants.parse_catalog_pdf_variants', wraps=parse_catalog_pdf_variants) as parser:
            entry = collect_school(spec, unit, self.directory, project_root=self.root, offline=True)
            self.assertGreater(len(entry['programs']), 0, entry.get('issues'))
            document = next(row for row in entry['documents'] if row['kind'] == 'catalog')
            self.assertTrue(document['id'].endswith('-v2'))
            self.assertEqual(parser.call_args.kwargs['project_root'], self.root)
            verifier = SourceVerifier(self.root)
            self.assertTrue(all(verifier.program(row, {document['id']: document}, unit) for row in entry['programs']))
            self.assertEqual(parser.call_args.kwargs['project_root'], self.root.resolve())
            self.assertEqual(ingest_verified_batch({'units': [unit]}, [entry], project_root=self.root)['programs'], [])

    def test_office_originals_are_copied_and_replayed_without_fabricated_pdf_pages(self):
        from tests.test_catalog_office import docx
        source = docx([['专业', '研究方向', '学习方式', '初试科目'], ['081200计算机科学与技术', '00不区分研究方向', '全日制', '①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础']])
        path = self.root / 'original.docx'
        path.write_bytes(source)
        spec = {'unit_id': '100', 'sources': [{'url': 'https://yz.a.edu.cn/catalog.docx', 'parser': 'office_table_v1',
            'source_path': str(path), 'sha256': hashlib.sha256(source).hexdigest()}]}
        entry = collect_school(spec, self.unit, self.directory, project_root=self.root, offline=True)
        self.assertEqual(len(entry['programs']), 1)
        self.assertEqual(entry['status']['status'], 'verified')
        documents = {row['id']: row for row in entry['documents']}
        verifier = SourceVerifier(self.root)
        self.assertTrue(verifier.program(entry['programs'][0], documents, self.unit))
        self.assertTrue(verifier.unit_reconciles(self.unit, 2027, entry['programs'], documents))
        self.assertTrue(all(ref.get('page') is None for ref in entry['programs'][0]['evidence']))

    def test_chsi_published_hidden_html_is_replayed_from_the_original_bytes(self):
        from html import escape
        entry = self._incremental_entry('body')
        body = (self.root / 'body.html').read_text('utf-8')
        raw = ('<html><title>招生章程</title><div id="cnt_body_wrapper"></div><input id="hid_content" type="hidden" value="'
               + escape(body, quote=True) + '"><script>$("#cnt_body_wrapper").html($("#hid_content").val());</script></html>').encode()
        path = self.root / 'published.html'
        path.write_bytes(raw)
        spec = {'unit_id': '100', 'sources': [{'url': 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?id=100',
            'parser': 'html_table_v1', 'source_path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}]}
        collected = collect_school(spec, self.unit, self.directory, project_root=self.root, offline=True)
        self.assertEqual(len(collected['programs']), 1)
        document = collected['documents'][0]
        self.assertEqual(document['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual((self.root / document['local_path']).read_bytes(), raw)
        verifier = SourceVerifier(self.root)
        self.assertTrue(verifier.program(collected['programs'][0], {document['id']: document}, self.unit))
        changed_route = {**document, 'url': 'https://yz.chsi.com.cn/sch/viewZszc--schId-100.dhtml'}
        self.assertNotIn('计算机科学与技术', ' '.join(verifier.pages(changed_route, self.unit)))

    def test_ingest_adopts_replayed_values_instead_of_unverified_optional_metadata(self):
        entry = self._incremental_entry('new')
        entry['programs'][0]['tuition'] = {'amount': 999999, 'period': 'year'}
        entry['programs'][0]['eligibility_evidence_complete'] = True
        entry['programs'][0]['planned_enrollment'] = {'count': 999999}
        merged = ingest_verified_batch({'units': [self.unit]}, [entry], project_root=self.root)
        self.assertIsNone(merged['programs'][0]['tuition'])
        self.assertFalse(merged['programs'][0]['eligibility_evidence_complete'])
        self.assertNotIn('planned_enrollment', merged['programs'][0])

    def test_conflicts_require_review_until_resolved_and_bad_batches_do_not_replace_files(self):
        from collectors.documents import DocumentError
        old, changed = self._incremental_entry('old'), self._incremental_entry('changed', subject='801软件工程综合')
        previous = {'metadata': {}, 'programs': old['programs'], 'documents': old['documents']}
        merged = ingest_verified_batch({'units': [self.unit]}, [changed], previous, project_root=self.root)
        refreshed = ingest_verified_batch({'units': [self.unit]}, [self._incremental_entry('refresh')], merged, project_root=self.root)
        self.assertTrue(refreshed['programs'][0]['catalog_conflict'])
        self.assertEqual(refreshed['metadata']['unit_catalog_status'][0]['status'], 'needs_review')
        output = self.root / 'current.json'
        output.write_text('existing', 'utf-8')
        bad = deepcopy(changed)
        bad['documents'][0]['id'] = 'old'
        for batch in [[None], [{**changed, 'status': {**changed['status'], 'unit_id': 'unknown'}}], [bad]]:
            with self.subTest(batch=batch), self.assertRaises(DocumentError):
                ingest_verified_batch({'units': [self.unit]}, batch, previous, project_root=self.root, output=output)
            self.assertEqual(output.read_text('utf-8'), 'existing')

    def test_only_an_independently_complete_new_catalog_can_archive_a_removed_direction(self):
        old, current = self._incremental_entry('old'), self._incremental_entry('current', code='085400')
        previous = {'metadata': {}, 'programs': old['programs'], 'documents': old['documents']}
        partial = ingest_verified_batch({'units': [self.unit]}, [current], previous, project_root=self.root)
        self.assertEqual(len(partial['programs']), 2)
        path = self.root / 'current.html'
        raw = path.read_text('utf-8').replace('测试大学计算机学院2027年', '测试大学2027年')
        path.write_text(raw, 'utf-8')
        current['documents'][0]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        current['status']['status'] = 'verified'
        complete = ingest_verified_batch({'units': [self.unit]}, [current], previous, project_root=self.root)
        self.assertEqual(len(complete['programs']), 1)
        self.assertEqual(complete['catalog_superseded'][0]['id'], old['programs'][0]['id'])
        self.assertTrue(complete['catalog_superseded'][0]['catalog_removed'])
        self.assertEqual(complete['metadata']['unit_catalog_status'][0]['status'], 'verified')
        self.assertFalse(next(row for row in complete['documents'] if row['id'] == 'old')['catalog_active'])

    def test_collector_cli_routes_partial_progress_through_verified_incremental_ingest(self):
        import contextlib
        import io
        import sys
        from collectors.catalog_batch import main
        old, fresh = self._incremental_entry('old'), self._incremental_entry('fresh', code='085400')
        previous = {'metadata': {}, 'programs': old['programs'], 'documents': old['documents']}
        units, manifest, output = (self.root / name for name in ['units.json', 'manifest.json', 'admissions.json'])
        units.write_text(json.dumps({'units': [self.unit]}), 'utf-8')
        manifest.write_text(json.dumps({'schools': [{'unit_id': '100'}]}), 'utf-8')
        output.write_text(json.dumps(previous), 'utf-8')
        args = ['catalog_batch', '--manifest', str(manifest), '--units', str(units), '--output', str(output), '--offline']
        original = save_progress
        def progress(path, collected, previous, *, registry):
            return original(path, collected, previous, registry=registry, project_root=self.root)
        with patch.object(sys, 'argv', args), patch('collectors.catalog_batch.collect_manifest', return_value=[fresh]), \
                patch('collectors.catalog_batch.save_progress', side_effect=progress), contextlib.redirect_stdout(io.StringIO()):
            main()
        self.assertEqual(len(json.loads(output.read_text('utf-8'))['programs']), 2)

    def test_online_source_download_and_missing_original_failures_are_explicit(self):
        spec = {**self.spec, 'sources': [{key: value for key, value in self.source.items() if key not in {'source_path', 'sha256'}}]}
        fetched = Mock(return_value={'raw': self.raw, 'content_type': 'text/html'})
        entry = collect_school(spec, self.unit, self.directory, project_root=self.root, fetcher=fetched, parser=self.parse)
        self.assertEqual(len(entry['programs']), 1)
        fetched.assert_called_once()
        failed = collect_school(spec, self.unit, self.directory, project_root=self.root, offline=True, parser=self.parse)
        self.assertEqual(failed['status']['status'], 'failed')
        for invalid in [{'year': 2028, 'sources': [self.source]}, {'year': 2027, 'sources': []}, {'year': 2027, 'sources': ['bad']}]:
            self.assertEqual(collect_school(invalid, self.unit, self.directory, project_root=self.root, offline=True)['status']['status'], 'failed')
        with patch('collectors.catalog_batch.MAX_BYTES', 10):
            oversized = collect_school(self.spec, self.unit, self.directory, project_root=self.root, offline=True)
            self.assertEqual(oversized['status']['status'], 'failed')

    def test_inline_policy_replay_is_cached_once_for_the_whole_source_not_once_per_direction(self):
        from collectors.catalog_enrichment import apply_inline_policies
        entry = self._incremental_entry('shared')
        path = self.root / 'shared.html'
        html = path.read_text('utf-8').replace('00不区分方向', '01人工智能02软件工程')
        path.write_text(html, 'utf-8')
        document = {**entry['documents'][0], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        entry = {**entry, **parse_catalog_html(html, self.unit, 2027, document), 'documents': [document]}
        self.assertEqual(len(entry['programs']), 2)
        with patch('collectors.catalog_enrichment.apply_inline_policies', wraps=apply_inline_policies) as policies:
            merged = ingest_verified_batch({'units': [self.unit]}, [entry], project_root=self.root)
            self.assertEqual(len(merged['programs']), 2)
            self.assertEqual(policies.call_count, 1)

    def test_exam_group_display_separator_does_not_change_internal_course_hyphens(self):
        from service_verification import canonical, exam_groups
        groups = exam_groups('①101-思想政治理论②201－英语（一）或203—日语③301数学（一）④801-C-C++专业综合')
        self.assertEqual(groups[1], {('101', canonical('思想政治理论'))})
        self.assertEqual(groups[2], {('201', canonical('英语（一）')), ('203', canonical('日语'))})
        self.assertEqual(groups[4], {('801', canonical('C-C++专业综合'))})
        self.assertEqual(exam_groups('①101--思想政治理论')[1], {('101', canonical('-思想政治理论'))})

    def test_pdf_replay_cache_includes_the_parent_bytes_even_when_mtime_is_restored(self):
        import os
        from collectors.catalog_pdf_tables import parse_catalog_pdf_tables
        from tests.test_catalog_pdf_tables import layout
        source = layout()
        path = self.root / 'table.pdf'
        path.write_bytes(b'%PDF-original')
        parent = self.root / 'parent.html'
        parent.write_text('<h1>测试大学2027年硕士招生专业目录</h1><a href="/table.pdf">目录</a>', 'utf-8')
        document = {'id': 'pdf', 'unit_id': '100', 'year': 2027, 'kind': 'catalog', 'status': 'parsed',
            'url': 'https://yz.a.edu.cn/table.pdf', 'local_path': path.name,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'catalog_parser': 'pdf_table_v1',
            'publication_page': {'id': 'pub', 'unit_id': '100', 'year': 2027, 'url': 'https://yz.a.edu.cn/publish',
                'local_path': parent.name, 'sha256': hashlib.sha256(parent.read_bytes()).hexdigest()}}
        parsed = parse_catalog_pdf_tables(source, self.unit, 2027, document)
        with patch('collectors.catalog_pdf_tables.extract_pdf_layout', return_value=source), \
                patch('collectors.documents.extract_pdf_text', return_value=source['raw_text']), \
                patch('collectors.catalog_pdf_tables.parse_catalog_pdf_tables', return_value=parsed) as parser:
            verifier = SourceVerifier(self.root)
            self.assertIsNotNone(verifier._source_programs(document, self.unit, 2027, {}))
            before = parent.stat()
            parent.write_text(parent.read_text('utf-8').replace('2027', '2026'), 'utf-8')
            os.utime(parent, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertIsNone(verifier._source_programs(document, self.unit, 2027, {}))
            self.assertEqual(parser.call_count, 1)
            self.assertEqual(parser.call_args.kwargs['project_root'], self.root.resolve())

    def test_zsml_explicit_no_unified_exam_page_is_required_but_not_an_empty_parse_failure(self):
        from collectors.catalog_zsml import parse_zsml_html
        unit, programs, documents = self._zsml_sources()
        tree_path = self.root / documents['tree']['local_path']
        tree = json.loads(tree_path.read_text('utf-8'))
        tree[0]['children'].append({'id': '035100', 'name': '035100 教育', 'scode': '21'})
        tree_path.write_text(json.dumps(tree, ensure_ascii=False), 'utf-8')
        documents['tree']['sha256'] = hashlib.sha256(tree_path.read_bytes()).hexdigest()
        html = '<table id="zsml-table"><tr><th>学科及研究方向</th><th>初试科目</th><th>统考招生</th></tr><tr class="zsml-talbe-yx-zy"><td>035100（专业学位）教育</td></tr><tr class="zsml-talbe-yx-zy-yjfx"><td>01（全日制）仅推免方向</td><td></td><td>否</td></tr></table>'
        path = self.root / 'excluded.html'
        path.write_text(html, 'utf-8')
        doc = {**documents['page'], 'id': 'excluded', 'url': documents['page']['url'].replace('/030100?', '/035100?'),
            'local_path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        documents['excluded'] = doc
        parsed = parse_zsml_html(html, unit, '001 法学院', 2027, doc)
        self.assertEqual(parsed['issues'], [])
        self.assertEqual(parsed['excluded_recommendation_only'], 1)
        self.assertTrue(SourceVerifier(self.root).unit_reconciles(unit, 2027, programs, documents))
        self.assertFalse(SourceVerifier(self.root).unit_reconciles(unit, 2027, programs, {key: value for key, value in documents.items() if key != 'excluded'}))
        path.write_text(html.replace('>否<', '>待公布<'), 'utf-8')
        documents['excluded'] = {**doc, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'excluded_recommendation_only': 100}
        self.assertFalse(SourceVerifier(self.root).unit_reconciles(unit, 2027, programs, documents))

    def test_office_actual_single_exam_title_cannot_be_hidden_by_ordinary_metadata(self):
        import io
        import zipfile
        from collectors.catalog_office import parse_catalog_office
        from tests.test_catalog_office import docx, package
        rows = [['专业', '研究方向', '学习方式', '初试科目'], ['081200计算机科学与技术', '00不区分研究方向', '全日制', '①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础']]
        original = docx(rows)
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            xml = archive.read('word/document.xml').decode()
        for title, accepted in [('测试大学2027年单独考试硕士生招生专业目录', False),
                                 ('测试大学2027年硕士研究生招生专业目录', True)]:
            content = xml.replace('测试大学2027年硕士研究生招生专业目录', title)
            content = content.replace('<w:tbl>', '<w:p><w:r><w:t>有关单独考试招生简章与推荐免试的说明另见公告，本文不含推免。</w:t></w:r></w:p><w:tbl>', 1)
            raw = package({'word/document.xml': content})
            path = self.root / 'scope.docx'
            path.write_bytes(raw)
            document = {'id': 'office', 'unit_id': '100', 'year': 2027, 'kind': 'catalog', 'status': 'parsed',
                'url': 'https://yz.a.edu.cn/catalog.docx', 'local_path': path.name,
                'title': '测试大学2027年普通统考招生目录', 'sha256': hashlib.sha256(raw).hexdigest(), 'catalog_parser': 'office_table_v1'}
            parsed = parse_catalog_office(raw, self.unit, 2027, document)
            self.assertTrue(parsed['programs'])
            self.assertEqual(SourceVerifier(self.root).program(parsed['programs'][0], {'office': document}, self.unit), accepted)

    def test_original_main_title_scope_guard_covers_special_routes_without_reading_body_notes(self):
        from service_verification import ordinary_catalog_scope
        for title in ['2027年博士研究生“申请-考核”制招生简章', '2027年接收推免攻读硕士研究生专业目录',
                      '2027年港澳台硕士研究生招生专业目录', '2027年单独考试硕士生招生专业目录']:
            with self.subTest(title=title):
                self.assertFalse(ordinary_catalog_scope('<h1>' + title + '</h1>', html=True))
                self.assertFalse(ordinary_catalog_scope(title + '\n专业代码 初试科目'))
        ordinary = '<h1>测试大学2027年硕士招生专业目录（不含推免和单独考试）</h1><p>博士申请考核制及推荐免试招生简章另见文件。</p>'
        self.assertTrue(ordinary_catalog_scope(ordinary, html=True))

    def test_actual_fjnu_and_zzuli_portal_headers_do_not_merge_navigation_into_main_titles(self):
        from service_verification import ordinary_catalog_scope
        # Exact raw prefixes of the saved official pages; layout tables precede the article title.
        fixtures = Path(__file__).parent / 'fixtures/catalog_scope'
        for filename in ('fjnu-2027-header.html', 'zzuli-2027-header.html'):
            with self.subTest(source=filename):
                html = (fixtures / filename).read_text('utf-8')
                self.assertTrue(ordinary_catalog_scope(html, html=True))
                # A genuine article title still wins over an ordinary browser/metadata title.
                special = re.sub(r"(<span class='Article_Title'>).*?(</span>)",
                    r'\g<1>2027年单独考试硕士生招生专业目录\2', html, count=1, flags=re.S)
                self.assertFalse(ordinary_catalog_scope(special, html=True))
        # Exact first paragraph extracted from the original BJTU single-exam DOCX.
        special = (fixtures / 'bjtu-2027-single-exam-title.html').read_text('utf-8')
        self.assertFalse(ordinary_catalog_scope('<title>普通硕士招生目录</title>' + special, html=True))

    def test_zsml_opaque_route_is_verified_against_the_tree_without_guessing_the_study_mode(self):
        from collectors.catalog_zsml import parse_zsml_html
        from tests.test_catalog_zsml import HTML
        unit, _, documents = self._zsml_sources()
        tree_path = self.root / documents['tree']['local_path']
        tree = json.loads(tree_path.read_text('utf-8'))
        tree[0]['children'][0] = {**tree[0]['children'][0], 'id': '030100_1', 'name': '030100 法学'}
        tree_path.write_text(json.dumps(tree, ensure_ascii=False), 'utf-8')
        documents['tree']['sha256'] = hashlib.sha256(tree_path.read_bytes()).hexdigest()
        documents['page']['url'] = documents['page']['url'].replace('/030100?', '/030100_1?') + '&xxfs=undefined'
        programs = parse_zsml_html(HTML, unit, '001 法学院', 2027, documents['page'])['programs']
        verifier = SourceVerifier(self.root)
        self.assertTrue(all(verifier.program(program, documents, unit) for program in programs))
        self.assertEqual({program['study_mode'] for program in programs}, {'全日制', '非全日制'})
        self.assertTrue(verifier.unit_reconciles(unit, 2027, programs, documents))
        wrong = deepcopy(documents)
        wrong['page']['url'] = wrong['page']['url'].replace('/030100_1?', '/030100_2?')
        forged = parse_zsml_html(HTML, unit, '001 法学院', 2027, wrong['page'])['programs']
        self.assertFalse(SourceVerifier(self.root).program(forged[0], wrong, unit))
        self.assertFalse(SourceVerifier(self.root).unit_reconciles(unit, 2027, forged, wrong))
        wrong['page']['url'] = wrong['page']['url'].replace('/030100_2?', '/030100?')
        forged = parse_zsml_html(HTML, unit, '001 法学院', 2027, wrong['page'])['programs']
        self.assertFalse(SourceVerifier(self.root).program(forged[0], wrong, unit))


if __name__ == '__main__':
    unittest.main()
