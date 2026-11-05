import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from service import AdmissionService, ServiceError
from source_proof import ProofError


class FakeEngine:
    def __init__(self):
        self.calls = []

    def validate_profile(self, profile):
        if not isinstance(profile, dict) or profile.get('bad'):
            raise ValueError('private detail')
        return {**profile, 'reference_year': profile.get('reference_year', 2027)}

    def match_programs(self, programs, profile):
        self.calls.append(('match', programs, profile))
        return {'matches': [{'program': p} for p in programs], 'excluded': [],
                'needs_confirmation': [], 'questions': [], 'summary': {}}

    def match_historical_rules(self, rules, facts, year):
        self.calls.append(('history', rules, facts, year))
        return {'matched': rules, 'excluded': [], 'needs_confirmation': [],
                'year': year, 'historical_reference': True}

    def sensitivity_analysis(self, programs, profile, scenarios):
        self.calls.append(('sensitivity', programs, profile, scenarios))
        return {'baseline': {'candidate_ids': [p['id'] for p in programs]}, 'scenarios': scenarios}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.data.mkdir()
        self.engine = FakeEngine()
        self.service = AdmissionService(self.data, engine=self.engine, search_api_key='')
        raw = self.data / 'snapshots' / 'catalog.html'
        raw.parent.mkdir()
        self.identity_quote = '001学院 085404计算机技术 00不区分方向 (1)全日制'
        self.exam_quote = '①101思想政治理论②204英语(二)③302数学(二)④408计算机学科专业基础'
        self.source_quote = '21全国统考 ' + self.identity_quote + ' ' + self.exam_quote
        raw.write_text('<html><body>' + self.source_quote + '</body></html>', encoding='utf-8')
        self.document = {'id': 'doc-a', 'unit_id': '123', 'year': 2027, 'kind': 'catalog',
                         'url': 'https://grad.a.edu.cn/catalog', 'status': 'parsed',
                         'sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
                         'local_path': 'snapshots/catalog.html'}
        evidence = {'document_id': 'doc-a', 'url': self.document['url'], 'quote': self.source_quote, 'page': 1}
        charter = self.data / 'snapshots' / 'charter.html'
        charter.write_text('测试乙研究所2027年不招收硕士研究生。', encoding='utf-8')
        self.charter = {'id': 'doc-b', 'unit_id': '456', 'year': 2027, 'kind': 'charter',
                        'url': 'https://www.b.ac.cn/charter', 'status': 'parsed',
                        'sha256': hashlib.sha256(charter.read_bytes()).hexdigest(),
                        'local_path': 'snapshots/charter.html'}
        self.program = {'id': 'a-1', 'unit_id': '123', 'unit_name': '测试甲大学', 'year': 2027,
                        'department': '学院', 'department_code': '001', 'code': '085404', 'name': '计算机技术',
                        'direction': '00不区分方向', 'study_mode': '全日制', 'degree_type': '专业学位',
                        'subjects': [{'code': code, 'name': name, 'scope': scope, 'exam_unit': index + 1} for index, (code, name, scope) in enumerate(
                                     [('101', '思想政治理论', 'national'), ('204', '英语(二)', 'national'),
                                      ('302', '数学(二)', 'national'), ('408', '计算机学科专业基础', 'national')])],
                        'evidence': evidence, 'exam_evidence': {**evidence, 'quote': self.exam_quote},
                        'conditions': [], 'requires_review': False}
        self.registry = {'metadata': {'complete': True, 'expected_pages': 1, 'fetched_pages': [1],
                                      'failed_pages': [], 'checked_at': '2026-10-28T00:00:00Z'},
                         'units': [{'id': '123', 'name': '测试甲大学', 'province': '天津',
                                    'source_url': 'https://yz.chsi.com.cn/sch/schoolInfo--schId-123.dhtml',
                                    'official_url': 'https://www.a.edu.cn'},
                                   {'id': '456', 'name': '测试乙研究所', 'province': '北京',
                                    'source_url': 'https://yz.chsi.com.cn/sch/schoolInfo--schId-456.dhtml',
                                    'official_url': 'https://www.b.ac.cn'}]}
        index = self.data / 'snapshots' / 'registry.html'
        index.write_text('<div class="sch-item"><a class="name" href="/sch/schoolInfo--schId-123.dhtml">测试甲大学</a><div class="sch-department">天津</div></div>'
                         '<div class="sch-item"><a class="name" href="/sch/schoolInfo--schId-456.dhtml">测试乙研究所</a><div class="sch-department">北京</div></div>', encoding='utf-8')
        self.registry['metadata'] = {**self.registry['metadata'], 'unit_count': 2,
                                     'pages': [{'page': 1, 'url': 'https://yz.chsi.com.cn/sch/',
                                                'snapshot_path': str(index), 'sha256': hashlib.sha256(index.read_bytes()).hexdigest(),
                                                'record_count': 2}]}
        self.admissions = {'metadata': {'reference_year': 2027, 'target_year': 2028, 'catalog_complete': False,
                                       'unit_catalog_status': []}, 'documents': [self.document, self.charter],
                           'programs': [self.program], 'score_lines': [], 'retest_rules': [],
                           'transfer_rules': [], 'notices': []}
        self.write()

    def write(self):
        (self.data / 'units.json').write_text(json.dumps(self.registry, ensure_ascii=False), encoding='utf-8')
        (self.data / 'admissions.json').write_text(json.dumps(self.admissions, ensure_ascii=False), encoding='utf-8')

    def verified(self):
        self.admissions['metadata'] = {**self.admissions['metadata'], 'catalog_complete': True,
            'unit_catalog_status': [{'unit_id': '123', 'year': 2027, 'status': 'verified',
                                     'expected_records': 1, 'parsed_records': 1, 'verified_records': 1,
                                     'notes': '已对账'},
                                    {'unit_id': '456', 'year': 2027, 'status': 'not_applicable',
                                     'expected_records': 0, 'parsed_records': 0, 'verified_records': 0,
                                     'notes': '测试夹具明确不适用',
                                     'evidence': {'document_id': 'doc-b', 'url': self.charter['url'],
                                                  'quote': '测试乙研究所2027年不招收硕士研究生。'}}]}
        self.write()

    def test_bootstrap_keeps_true_data_and_separates_registry_from_catalog(self):
        result = self.service.bootstrap()
        self.assertEqual(result['programs'][0]['name'], '计算机技术')
        self.assertTrue(result['coverage']['registry_complete'])
        self.assertFalse(result['coverage']['catalog_complete'])
        self.assertFalse(result['coverage']['release_ready'])
        self.assertEqual(result['coverage']['index_total'], 2)
        self.assertEqual(result['coverage']['program_count'], 1)
        self.assertFalse(result['service']['search_configured'])
        self.assertEqual(result['meta']['target_year'], 2028)
        self.assertEqual(result['meta']['score_years'], [])

    def test_one_snapshot_shares_original_replay_but_new_revision_rebuilds_it(self):
        import service
        seen = []
        original = service.apply_evidence_safety

        def track(*args, **kwargs):
            seen.append(kwargs.get('verifier'))
            return original(*args, **kwargs)

        with patch('service.apply_evidence_safety', side_effect=track):
            self.service.bootstrap()
            self.assertIsNotNone(seen[0])
            self.assertIs(self.service.verifier, seen[0])
            self.service.bootstrap()
            self.assertEqual(len(seen), 1)
            self.admissions['metadata']['target_year'] = 2029
            self.write()
            self.service.bootstrap()
            self.assertEqual(len(seen), 2)
            self.assertIs(self.service.verifier, seen[1])
            self.assertIsNot(seen[0], seen[1])

    def test_field_evidence_has_original_quote_scope_and_safe_metadata(self):
        result = self.service.evidence('a-1')
        self.assertEqual(result['program']['id'], 'a-1')
        subjects = next(row for row in result['claims'] if row['id'] == 'subjects')
        self.assertEqual(subjects['status'], 'verified')
        self.assertEqual(subjects['evidence'][0]['quote'], self.exam_quote)
        self.assertEqual(subjects['evidence'][0]['url'], self.document['url'])
        self.assertNotIn('local_path', json.dumps(result))
        with self.assertRaises(ServiceError): self.service.evidence('missing')

    def test_source_catalog_filters_and_rejects_invalid_queries(self):
        result = self.service.sources(unit_id='123', year=2027, kind='catalog', q='')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['sources'][0]['related_program_ids'], ['a-1'])
        for query in [{'kind': 'bad'}, {'q': []}, {'unit_id': 'absent'}, {'year': '2027'}]:
            with self.subTest(query=query), self.assertRaises(ServiceError): self.service.sources(**query)

    def test_public_document_projection_removes_nested_storage_paths_and_keeps_evidence(self):
        public = {'url': 'https://grad.a.edu.cn/fees', 'sha256': 'a' * 64, 'quote': '官方学费原文'}
        self.admissions['documents'][0] = {**self.document, 'publication_page': {
            **public, 'local_path': 'private/publication.html', 'text_path': 'private/text.txt',
            'text_pages': [{'page': 1, 'local_path': 'private/page.txt'}],
            'proof': [{'snapshot_path': 'private/snapshot.html', 'cache_path': 'private/cache', **public}]}}
        self.write()
        result = self.service.bootstrap()
        serialized = json.dumps(result['documents'], ensure_ascii=False)
        for field in ('local_path', 'text_path', 'text_pages', 'snapshot_path', 'cache_path'):
            self.assertNotIn(field, serialized)
        self.assertNotIn('private/', serialized)
        self.assertEqual(result['documents'][0]['publication_page']['url'], public['url'])
        self.assertEqual(result['documents'][0]['publication_page']['proof'][0], public)
        _, internal = self.service._snapshot()
        self.assertEqual(internal['documents'][0]['publication_page']['local_path'], 'private/publication.html')

    def test_radar_and_proof_source_responses_share_public_projection(self):
        document = {'url': self.document['url'], 'sha256': self.document['sha256'], 'local_path': 'private/source.html',
                    'publication_page': {'quote': '官方引文', 'snapshot_path': 'private/publication.html'}}
        self.service.radar = Mock()
        self.service.radar.status.return_value = {'events': [{'evidence': {'before': document}}]}
        self.service.proof = Mock()
        self.service.proof.tasks.return_value = {'tasks': [{'last_check': {'checks': [document]}}]}
        self.service.proof.investigate.return_value = {'checks': [document]}
        for response in (self.service.radar_status(), self.service.source_tasks('a-1'),
                         self.service.source_proof({'program_id': 'a-1', 'kind': 'fees'})):
            serialized = json.dumps(response, ensure_ascii=False)
            self.assertNotIn('private/', serialized)
            self.assertIn(self.document['sha256'], serialized)
            self.assertIn('官方引文', serialized)

    def test_missing_files_yield_explicit_empty_coverage(self):
        service = AdmissionService(self.data / 'missing', engine=self.engine)
        result = service.bootstrap()
        self.assertEqual(result['programs'], [])
        self.assertFalse(result['coverage']['release_ready'])
        self.assertEqual(result['coverage']['index_total'], 0)

    def test_invalid_storage_reports_unavailable_without_disclosing_path(self):
        (self.data / 'admissions.json').write_text('invalid json', encoding='utf-8')
        with self.assertRaises(ServiceError) as caught:
            self.service.bootstrap()
        self.assertEqual(caught.exception.code, 'data_unavailable')
        self.assertNotIn(str(self.data), caught.exception.message)

    def test_array_or_wrong_collection_storage_is_rejected(self):
        for value in [[], {'programs': 'bad'}, {'programs': [None]}]:
            with self.subTest(value=value):
                (self.data / 'admissions.json').write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(ServiceError):
                    self.service.bootstrap()

    def test_complete_requires_all_units_and_valid_evidence(self):
        self.verified()
        self.assertTrue(self.service.bootstrap()['coverage']['release_ready'])
        for field, value in [('verified_records', 0), ('expected_records', 2), ('status', 'needs_review')]:
            self.verified()
            self.admissions['metadata']['unit_catalog_status'][0][field] = value
            self.write()
            self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])

    def test_missing_source_wrong_hash_and_review_flags_block_release(self):
        for mutation in ['missing', 'hash', 'no-evidence', 'bad-subject', 'unknown-unit']:
            with self.subTest(mutation=mutation):
                self.verified()
                if mutation == 'missing':
                    self.admissions['documents'][0] = {**self.document, 'local_path': 'missing.pdf'}
                elif mutation == 'hash':
                    self.admissions['documents'][0] = {**self.document, 'sha256': 'f' * 64}
                elif mutation == 'no-evidence':
                    self.admissions['programs'][0] = {**self.program, 'evidence': None}
                elif mutation == 'bad-subject':
                    self.admissions['programs'][0] = {**self.program, 'subjects': [{'code': '408'}]}
                else:
                    self.admissions['programs'][0] = {**self.program, 'unit_id': 'unknown'}
                self.write()
                self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])
                self.admissions['documents'] = [self.document, self.charter]
                self.admissions['programs'] = [self.program]

    def test_catalogue_completeness_is_separate_from_eligibility_verification(self):
        self.verified()
        self.admissions['programs'][0] = {**self.program, 'requires_review': True,
                                         'eligibility_evidence_complete': False}
        self.write()
        coverage = self.service.bootstrap()['coverage']
        self.assertTrue(coverage['catalog_complete'])
        self.assertEqual(coverage['qualification_complete_count'], 0)
        self.admissions['programs'][0] = {**self.program, 'requires_review': False,
                                         'eligibility_evidence_complete': True}
        self.write()
        self.assertEqual(self.service.bootstrap()['coverage']['qualification_complete_count'], 1)

    def test_subject_evidence_and_complete_exam_units_are_checked(self):
        self.verified()
        subjects = [{**subject, 'exam_unit': index + 1, 'evidence': self.program['evidence']}
                    for index, subject in enumerate(self.program['subjects'])]
        self.admissions['programs'][0] = {**self.program, 'evidence': {**self.program['evidence'], 'quote': self.identity_quote}, 'subjects': subjects}
        self.write()
        self.assertTrue(self.service.bootstrap()['coverage']['catalog_complete'])
        self.admissions['programs'][0]['subjects'] = [subjects[0], subjects[1], subjects[3]]
        self.write()
        self.assertFalse(self.service.bootstrap()['coverage']['catalog_complete'])

    def test_release_checks_raw_registry_and_does_not_exclude_units_by_notes(self):
        self.verified()
        self.registry['units'] = self.registry['units'][:1]
        self.registry['metadata']['unit_count'] = 1
        self.write()
        self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])
        self.registry['units'].append({'id': '456', 'name': '测试乙研究所', 'official_url': 'https://www.b.ac.cn'})
        self.registry['metadata']['unit_count'] = 2
        self.admissions['metadata']['unit_catalog_status'][1].pop('evidence')
        self.write()
        self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])

    def test_forged_quotes_missing_subjects_and_third_party_sources_block_release(self):
        for mutation in ['forged', 'two-subjects', 'duplicate-subject', 'third-party', 'wrong-name']:
            with self.subTest(mutation=mutation):
                self.verified()
                changed = {**self.program}
                if mutation == 'forged':
                    changed = {**changed, 'subjects': [{'code': '999', 'name': '编造', 'scope': '123', 'exam_unit': 1},
                                                       {'code': '998', 'name': '编造二', 'scope': '123', 'exam_unit': 2}],
                               'exam_evidence': {**changed['evidence'], 'quote': '①999编造②998编造二'}}
                elif mutation == 'two-subjects': changed['subjects'] = changed['subjects'][:2]
                elif mutation == 'duplicate-subject': changed['subjects'] = [dict(changed['subjects'][0])] * 2
                elif mutation == 'wrong-name': changed['name'] = '原文没有的专业'
                else:
                    self.admissions['documents'][0] = {**self.document, 'url': 'https://evil.example/catalog'}
                    changed['evidence'] = {**changed['evidence'], 'url': 'https://evil.example/catalog'}
                    changed['exam_evidence'] = {**changed['exam_evidence'], 'url': 'https://evil.example/catalog'}
                self.admissions['programs'] = [changed]
                self.write()
                self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])
                self.admissions['programs'] = [self.program]
                self.admissions['documents'] = [self.document, self.charter]

    def test_raw_direction_count_cannot_be_replaced_by_self_reported_numbers(self):
        self.verified()
        raw = self.data / self.document['local_path']
        raw.write_text(self.source_quote + '\n21全国统考 学院 085404 计算机技术 01人工智能 全日制 ' + self.exam_quote, encoding='utf-8')
        self.document = {**self.document, 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}
        self.admissions['documents'][0] = self.document
        self.write()
        self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])

    def test_registry_failure_duplicate_rows_and_ambiguous_status_block_release(self):
        self.verified()
        self.registry['metadata']['failed_pages'] = [1]
        self.write()
        self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])

        self.registry['metadata']['failed_pages'] = []
        self.admissions['programs'].append(dict(self.program))
        self.write()
        self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])
        self.admissions['programs'] = [self.program]
        status = self.admissions['metadata']['unit_catalog_status'][0]
        self.admissions['metadata']['unit_catalog_status'].append(dict(status))
        self.write()
        self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])

    def test_same_count_cannot_replace_a_source_direction_with_a_duplicate(self):
        self.verified()
        second_quote = self.source_quote.replace('00不区分方向', '01人工智能')
        raw = self.data / self.document['local_path']
        raw.write_text(self.source_quote + '\n' + second_quote, encoding='utf-8')
        self.admissions['documents'][0] = {**self.document, 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}
        second = {**self.program, 'id': 'a-2', 'direction': '01人工智能',
                  'evidence': {**self.program['evidence'], 'quote': second_quote}}
        self.admissions['programs'] = [self.program, second]
        self.admissions['metadata']['unit_catalog_status'][0] = {
            **self.admissions['metadata']['unit_catalog_status'][0],
            'expected_records': 2, 'parsed_records': 2, 'verified_records': 2}
        self.write()
        self.assertTrue(self.service.bootstrap()['coverage']['release_ready'])
        self.admissions['programs'][1] = {**self.program, 'id': 'a-2'}
        self.write()
        self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])

    def test_scope_and_department_must_match_the_saved_source(self):
        self.verified()
        for field in ('scope', 'department'):
            changed = {**self.program}
            if field == 'scope':
                changed['subjects'] = [{**subject, 'scope': '123'} for subject in self.program['subjects']]
            else:
                changed['department'] = '原文没有的学院'
            self.admissions['programs'] = [changed]
            self.write()
            self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])

    def test_a_department_stopping_admissions_does_not_exclude_the_institution(self):
        self.verified()
        quote = '测试乙研究所2027年计算机学院不招收硕士研究生，其他院系仍招收硕士研究生。'
        raw = self.data / self.charter['local_path']
        raw.write_text(quote, encoding='utf-8')
        self.admissions['documents'][1] = {**self.charter, 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}
        self.admissions['metadata']['unit_catalog_status'][1]['evidence']['quote'] = quote
        self.write()
        self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])
        self.assertEqual(self.service.bootstrap()['coverage']['missing_units_count'], 1)

    def test_repeated_pdf_column_headers_do_not_become_exam_names(self):
        self.verified()
        header = '\n考试方式 院系所 专业 研究方向 学习方式 专业研究方向备注 考试科目'
        raw = self.data / self.document['local_path']
        raw.write_text(self.source_quote + header, encoding='utf-8')
        self.admissions['documents'][0] = {**self.document, 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}
        self.admissions['programs'] = [{**self.program, 'evidence': {**self.program['evidence'], 'quote': self.source_quote + header}, 'exam_evidence': None}]
        self.write()
        self.assertTrue(self.service.bootstrap()['coverage']['release_ready'])

    def test_three_exam_units_keep_the_explicit_no_fourth_exam_marker(self):
        self.verified()
        quote = self.source_quote.replace('④408计算机学科专业基础', '④--无')
        raw = self.data / self.document['local_path']
        raw.write_text(quote, encoding='utf-8')
        self.admissions['documents'][0] = {**self.document, 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}
        self.admissions['programs'] = [{**self.program, 'subjects': self.program['subjects'][:3],
                                        'evidence': {**self.program['evidence'], 'quote': quote}, 'exam_evidence': None}]
        self.write()
        self.assertTrue(self.service.bootstrap()['coverage']['release_ready'])

    def test_alternative_subjects_require_one_choice_group_within_the_exam_unit(self):
        self.verified()
        quote = self.source_quote.replace('②204英语(二)', '②204英语(二)或201英语(一)')
        raw = self.data / self.document['local_path']
        raw.write_text(quote, encoding='utf-8')
        self.admissions['documents'][0] = {**self.document, 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}
        subjects = [{**subject, **({'choice_group': 'language'} if subject['exam_unit'] == 2 else {})} for subject in self.program['subjects']]
        subjects.append({'code': '201', 'name': '英语(一)', 'scope': 'national', 'exam_unit': 2, 'choice_group': 'language'})
        self.admissions['programs'] = [{**self.program, 'subjects': subjects,
                                      'evidence': {**self.program['evidence'], 'quote': quote}, 'exam_evidence': None}]
        self.write()
        self.assertTrue(self.service.bootstrap()['coverage']['release_ready'])
        for mutation in ('missing', 'different', 'empty'):
            changed = [{key: value for key, value in subject.items() if key != 'choice_group'} for subject in subjects]
            if mutation != 'missing':
                changed = [{**subject, **({'choice_group': str(index) if mutation == 'different' else ''} if subject['exam_unit'] == 2 else {})} for index, subject in enumerate(changed)]
            self.admissions['programs'][0]['subjects'] = changed
            self.write()
            self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])

    def test_program_query_uses_real_rows_and_rejects_invalid_scope(self):
        result = self.service.programs(q='408', unit_id='123', year=2027)
        self.assertEqual(result['total'], 1)
        self.assertEqual(self.service.programs(q='不存在')['status'], 'no_match_in_partial_catalog')
        self.assertEqual(self.service.programs(unit_id='456')['status'], 'not_collected')
        for kwargs in [{'unit_id': 'bad'}, {'year': 1900}, {'year': True}, {'q': ['bad']}, {'q': 'x' * 121}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ServiceError):
                self.service.programs(**kwargs)

    def test_program_search_matches_saved_location_and_campus(self):
        self.admissions['programs'] = [{**self.program, 'province': '天津', 'campus': '津南校区'}]
        self.write()
        self.assertEqual(self.service.programs(q='天津')['total'], 1)
        self.assertEqual(self.service.programs(q='津南')['total'], 1)
        self.assertEqual(self.service.programs(q='不存在的校区')['total'], 0)

    def test_verified_empty_query_is_distinguished(self):
        self.verified()
        self.assertEqual(self.service.programs(q='不存在', unit_id='123')['status'], 'no_match_in_verified_catalog')
        self.assertEqual(self.service.subjects(q='不存在', unit_id='123')['status'], 'no_match_in_verified_catalog')

    def test_history_merges_by_id_and_only_exposes_anonymous_aggregates(self):
        self.admissions['score_lines'] = [{'id': 'line-1', 'unit_id': '123', 'year': 2026, 'total': 310}]
        self.write()
        history = {'metadata': {}, 'documents': [{**self.document, 'id': 'history-doc', 'kind': 'admitted', 'year': 2026}],
                   'score_lines': [{'id': 'line-1', 'year': 2026, 'total': 999},
                                   {'id': 'line-2', 'unit_id': '123', 'year': 2025, 'total': 300}],
                   'admitted_statistics': [{'id': 'stat-1', 'unit_id': '123', 'year': 2026,
                                            'sample_count': 3, 'is_full_admission_cohort': False,
                                            'candidate_name': '不应公开的姓名', 'candidate_number': 'private-123',
                                            'initial': {'min': 310, 'max': 350, 'median': 330, 'raw_names': ['秘密']}}]}
        (self.data / 'history.json').write_text(json.dumps(history, ensure_ascii=False), encoding='utf-8')
        result = self.service.bootstrap()
        self.assertEqual(result['score_lines'][0]['total'], 310)
        self.assertEqual(len(result['score_lines']), 2)
        self.assertEqual(result['meta']['score_years'], [2026, 2025])
        self.assertEqual(result['admitted_statistics'][0]['initial']['median'], 330)
        self.assertFalse(result['admitted_statistics'][0]['is_full_admission_cohort'])
        self.assertNotIn('不应公开', json.dumps(result, ensure_ascii=False))
        self.assertNotIn('private-123', json.dumps(result))
        self.assertNotIn('raw_names', json.dumps(result))
        self.assertNotIn('local_path', json.dumps(result['documents']))

    def test_invalid_key_format_is_not_reported_as_configured(self):
        for value in ['bad\nkey', 'x' * 513, 7]:
            with self.subTest(value_type=type(value)):
                service = AdmissionService(self.data, search_api_key=value)
                self.assertFalse(service.bootstrap()['service']['search_configured'])

    def test_source_file_must_stay_inside_data_directory(self):
        outside = Path(self.temp.name) / 'private.txt'
        outside.write_text('not a source', encoding='utf-8')
        self.verified()
        self.admissions['documents'][0] = {**self.document, 'local_path': str(outside),
                                          'sha256': hashlib.sha256(outside.read_bytes()).hexdigest()}
        self.write()
        self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])

    def test_nonfinite_json_and_invalid_registry_ids_fail_safely(self):
        (self.data / 'admissions.json').write_text('{"metadata":{"bad":NaN}}', encoding='utf-8')
        with self.assertRaises(ServiceError):
            self.service.bootstrap()
        self.write()
        self.registry['units'][0]['id'] = ['invalid']
        self.write()
        with self.assertRaises(ServiceError):
            self.service.bootstrap()

    def test_subject_identity_keeps_same_code_different_unit_scope(self):
        other = {**self.program, 'id': 'b-1', 'unit_id': '456', 'unit_name': '测试乙研究所',
                 'subjects': [{'code': '901', 'name': '数据结构', 'scope': '456'}]}
        first = {**self.program, 'subjects': [{'code': '901', 'name': '数据结构', 'scope': '123'}]}
        self.admissions['programs'] = [first, other]
        self.write()
        result = self.service.subjects(q='901')
        self.assertEqual(result['total'], 2)
        self.assertEqual({item['scope'] for item in result['subjects']}, {'123', '456'})
        self.assertEqual(result['subjects'][0]['program_ids'], ['a-1'])

    def test_match_delegates_to_engine_and_marks_preview_and_reference(self):
        result = self.service.match({'profile': {'reference_year': 2027}, 'unit_ids': ['123']})
        self.assertEqual(len(result['matches']), 1)
        self.assertFalse(result['release_ready'])
        self.assertEqual(result['scope']['mode'], 'research_preview')
        self.assertEqual(result['scope']['unit_ids'], ['123'])
        self.assertTrue(result['historical_reference'])
        self.assertEqual(self.engine.calls[-1][0], 'match')

    def test_match_payload_validation_and_engine_errors_are_safe(self):
        for payload in [None, [], {}, {'profile': [], 'unit_ids': []}, {'profile': {}, 'secret': 'value'},
                        {'profile': {}, 'unit_ids': ['unknown']}, {'profile': {'bad': True}}]:
            with self.subTest(payload=payload), self.assertRaises(ServiceError) as caught:
                self.service.match(payload)
            self.assertNotIn('private detail', caught.exception.message)

    def test_sensitivity_delegates_with_scope_and_limits_scenarios(self):
        scenarios = [{'name': '扩大地区', 'changes': {'provinces': ['天津', '北京']}}]
        result = self.service.sensitivity({'profile': {}, 'unit_ids': ['123'], 'scenarios': scenarios})
        self.assertEqual(result['baseline']['candidate_ids'], ['a-1'])
        self.assertEqual(result['scenarios'], scenarios)
        self.assertFalse(result['release_ready'])
        self.assertEqual(result['scope']['mode'], 'research_preview')
        self.assertEqual(self.engine.calls[-1][0], 'sensitivity')
        for value in [None, {}, [None], scenarios * 13]:
            with self.subTest(value=value), self.assertRaises(ServiceError):
                self.service.sensitivity({'profile': {}, 'scenarios': value})

    def test_history_is_year_specific_and_has_explicit_empty_state(self):
        rule = {'id': 'r1', 'year': 2026, 'unit_id': '123', 'title': '复试规则'}
        self.admissions['retest_rules'] = [rule, {**rule, 'id': 'r2', 'year': 2025}]
        self.write()
        result = self.service.history_match({'kind': 'retest', 'year': 2026, 'facts': {}})
        self.assertEqual(result['matched'], [rule])
        self.assertEqual(self.service.history_match({'kind': 'transfer', 'year': 2026, 'facts': {}})['data_status'], 'not_collected')
        for payload in [{'kind': 'bad', 'year': 2026, 'facts': {}}, {'kind': 'retest', 'year': 1900, 'facts': {}},
                        {'kind': 'retest', 'year': 2026, 'facts': []}, {'kind': 'retest', 'year': 2026, 'facts': {}, 'api_key': 'secret'}]:
            with self.subTest(payload=payload), self.assertRaises(ServiceError):
                self.service.history_match(payload)

    def test_search_missing_key_and_payload_validation(self):
        with self.assertRaises(ServiceError) as caught:
            self.service.search({'unit_id': '123', 'year': 2027, 'kind': 'catalog'})
        self.assertEqual(caught.exception.code, 'not_configured')
        for payload in [{'unit_id': 'bad', 'year': 2027, 'kind': 'catalog'},
                        {'unit_id': '123', 'year': 1900, 'kind': 'catalog'},
                        {'unit_id': '123', 'year': 2027, 'kind': 'bad'},
                        {'unit_id': '123', 'year': 2027, 'kind': 'catalog', 'query': 'personal secret'}]:
            with self.subTest(payload=payload), self.assertRaises(ServiceError):
                self.service.search(payload)

    def test_search_uses_fixed_endpoint_public_terms_and_filters_vr_domains(self):
        observed = []
        pages = [json.dumps({'title': '目录', 'url': 'https://grad.a.edu.cn/catalog', 'content': '公开资料'}),
                 json.dumps({'title': '非官方', 'url': 'https://unrelated.example/info'}),
                 json.dumps({'title': '伪域名', 'url': 'https://a.edu.cn.evil.example/info'}),
                 'not-json', {'title': '中心公告', 'url': 'https://yz.chsi.com.cn/notice'}]
        def opener(request, timeout):
            observed.append((request, timeout))
            return Response(json.dumps({'Response': {'Pages': pages, 'RequestId': 'req-1'}}).encode())
        service = AdmissionService(self.data, engine=self.engine, search_api_key='test-key', search_opener=opener)
        result = service.search({'unit_id': '123', 'year': 2027, 'kind': 'catalog'})
        request, timeout = observed[0]
        self.assertEqual(request.full_url, 'https://api.wsa.cloud.tencent.com/SearchPro')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-key')
        body = json.loads(request.data)
        self.assertEqual(set(body), {'Query', 'Site'})
        self.assertIn('测试甲大学', body['Query'])
        self.assertEqual(body['Site'], 'a.edu.cn')
        self.assertLessEqual(timeout, 20)
        self.assertEqual(len(result['results']), 2)
        self.assertEqual(result['rejected_count'], 2)
        self.assertEqual(result['malformed_count'], 1)
        self.assertEqual(result['request_id'], 'req-1')
        self.assertNotIn('test-key', json.dumps(result))
        self.assertEqual(result['results'][0]['source_status'], 'official_domain_unverified_content')

    def test_search_retries_without_site_when_the_site_filter_is_empty(self):
        bodies, responses = [], [
            {'Response': {'RequestId': 'req-empty', 'Query': '公开查询', 'Version': 'standard'}},
            {'Response': {'Pages': [json.dumps({'title': '目录', 'url': 'https://grad.a.edu.cn/catalog', 'content': '公开资料'})],
                          'RequestId': 'req-wide'}},
        ]
        def opener(request, timeout):
            bodies.append(json.loads(request.data))
            return Response(json.dumps(responses[len(bodies) - 1]).encode())
        service = AdmissionService(self.data, engine=self.engine, search_api_key='test-key', search_opener=opener)
        result = service.search({'unit_id': '123', 'year': 2027, 'kind': 'catalog'})
        self.assertEqual(len(bodies), 2)
        self.assertIn('Site', bodies[0])
        self.assertNotIn('Site', bodies[1])
        self.assertTrue(result['site_fallback'])
        self.assertEqual(result['request_id'], 'req-wide')
        self.assertEqual([row['url'] for row in result['results']], ['https://grad.a.edu.cn/catalog'])

    def test_search_retries_without_site_when_every_site_result_is_rejected(self):
        bodies, responses = [], [
            {'Response': {'Pages': [json.dumps({'title': '转载', 'url': 'https://unrelated.example/info'}),
                                    json.dumps({'title': '伪域名', 'url': 'https://a.edu.cn.evil.example/info'}),
                                    'not-json'], 'RequestId': 'req-site'}},
            {'Response': {'Pages': [json.dumps({'title': '目录', 'url': 'https://grad.a.edu.cn/catalog', 'content': '公开资料'})],
                          'RequestId': 'req-wide'}},
        ]
        def opener(request, timeout):
            bodies.append(json.loads(request.data))
            return Response(json.dumps(responses[len(bodies) - 1]).encode())
        service = AdmissionService(self.data, engine=self.engine, search_api_key='test-key', search_opener=opener)
        result = service.search({'unit_id': '123', 'year': 2027, 'kind': 'catalog'})
        self.assertEqual(len(bodies), 2)
        self.assertIn('Site', bodies[0])
        self.assertNotIn('Site', bodies[1])
        self.assertTrue(result['site_fallback'])
        self.assertEqual(result['request_id'], 'req-wide')
        self.assertEqual([row['url'] for row in result['results']], ['https://grad.a.edu.cn/catalog'])
        self.assertEqual((result['rejected_count'], result['malformed_count']), (0, 0))

    def test_search_keeps_the_site_result_when_the_retry_finds_nothing_official(self):
        bodies, responses = [], [
            {'Response': {'Pages': [json.dumps({'title': '转载', 'url': 'https://unrelated.example/info'})],
                          'RequestId': 'req-site'}},
            {'Response': {'Pages': [json.dumps({'title': '同样非官方', 'url': 'https://another.example/info'})],
                          'RequestId': 'req-wide'}},
        ]
        def opener(request, timeout):
            bodies.append(json.loads(request.data))
            return Response(json.dumps(responses[len(bodies) - 1]).encode())
        service = AdmissionService(self.data, engine=self.engine, search_api_key='test-key', search_opener=opener)
        result = service.search({'unit_id': '123', 'year': 2027, 'kind': 'catalog'})
        self.assertEqual(len(bodies), 2)
        self.assertFalse(result['site_fallback'])
        self.assertEqual(result['request_id'], 'req-site')
        self.assertEqual(result['results'], [])
        self.assertEqual(result['rejected_count'], 1)

    def test_search_errors_do_not_echo_response_or_key(self):
        for error in [HTTPError('https://x', 429, 'sensitive key', {}, None),
                      HTTPError('https://x', 401, 'sensitive key', {}, None),
                      URLError('sensitive key'), TimeoutError('sensitive key')]:
            def opener(*args, _error=error, **kwargs):
                raise _error
            service = AdmissionService(self.data, engine=self.engine, search_api_key='sensitive key', search_opener=opener)
            with self.subTest(error=error), self.assertRaises(ServiceError) as caught:
                service.search({'unit_id': '123', 'year': 2027, 'kind': 'catalog'})
            self.assertNotIn('sensitive key', caught.exception.message)

    def test_search_rejects_invalid_or_oversize_upstream_json(self):
        for response in [b'not json', b'[]', json.dumps({'Response': {'Error': {'Code': 'UnauthorizedOperation', 'Message': 'private'}}}).encode(), b'x' * (4 * 1024 * 1024 + 1)]:
            service = AdmissionService(self.data, engine=self.engine, search_api_key='test-key',
                                       search_opener=lambda *a, _response=response, **k: Response(_response))
            with self.subTest(size=len(response)), self.assertRaises(ServiceError) as caught:
                service.search({'unit_id': '123', 'year': 2027, 'kind': 'catalog'})
            self.assertNotIn('private', caught.exception.message)

    def test_only_current_platform_environment_key_is_used(self):
        with patch.dict(os.environ, {'GRAD_SEARCH_API_KEY': '', 'TENCENT_API_KEY': 'old-secret'}, clear=True):
            self.assertFalse(AdmissionService(self.data).bootstrap()['service']['search_configured'])
        with patch.dict(os.environ, {'GRAD_SEARCH_API_KEY': 'new-key'}, clear=True):
            result = AdmissionService(self.data).bootstrap()
            self.assertTrue(result['service']['search_configured'])
            self.assertNotIn('new-key', json.dumps(result))

    def test_cached_coverage_computes_once_and_returns_independent_copies(self):
        with patch.object(self.service, '_coverage', wraps=self.service._coverage) as coverage:
            with ThreadPoolExecutor(max_workers=6) as executor:
                results = list(executor.map(lambda _: self.service.bootstrap(), range(12)))
            self.assertEqual(coverage.call_count, 1)
        results[0]['programs'][0]['name'] = '污染'
        results[0]['coverage']['missing_unit_ids'].clear()
        again = self.service.bootstrap()
        self.assertEqual(again['programs'][0]['name'], '计算机技术')
        self.assertTrue(again['coverage']['missing_unit_ids'])
        self.assertEqual(again['service']['data_revision'], results[1]['service']['data_revision'])

    def test_same_length_source_tamper_with_restored_mtime_invalidates_release(self):
        self.verified()
        first = self.service.bootstrap()
        self.assertTrue(first['coverage']['release_ready'])
        path = self.data / self.document['local_path']
        before = path.stat()
        raw = path.read_bytes()
        changed = raw.replace(b'408', b'999')
        self.assertNotEqual(raw, changed)
        self.assertEqual(len(raw), len(changed))
        with path.open('r+b') as stream:
            stream.write(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(path.stat().st_size, before.st_size)
        self.assertFalse(self.service.health()['release_ready'])
        after = self.service.bootstrap()
        self.assertFalse(after['coverage']['release_ready'])
        self.assertNotEqual(first['service']['data_revision'], after['service']['data_revision'])
        self.assertFalse(AdmissionService(self.data, search_api_key='').bootstrap()['coverage']['release_ready'])

    def test_cache_invalidates_semantic_json_and_all_source_mutations(self):
        self.verified()
        first = self.service.bootstrap()
        self.assertTrue(first['coverage']['release_ready'])
        self.admissions['programs'][0] = {**self.program, 'eligibility_evidence_complete': True}
        self.write()
        changed = self.service.bootstrap()
        self.assertEqual(changed['coverage']['qualification_complete_count'], 1)
        self.assertNotEqual(changed['service']['data_revision'], first['service']['data_revision'])
        for source in (self.data / self.document['local_path'], self.data / 'snapshots/registry.html', self.data / self.charter['local_path']):
            original = source.read_bytes()
            source.write_bytes(b'tampered')
            self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])
            source.unlink()
            self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])
            source.write_bytes(original)
            self.assertTrue(self.service.bootstrap()['coverage']['release_ready'])

    def test_health_does_not_bootstrap_and_never_reports_stale_release(self):
        with patch.object(self.service, 'bootstrap', side_effect=AssertionError('heavy bootstrap')):
            self.assertFalse(self.service.health()['release_ready'])
        self.verified()
        self.assertTrue(self.service.bootstrap()['coverage']['release_ready'])
        self.assertTrue(self.service.health()['release_ready'])
        (self.data / self.document['local_path']).unlink()
        self.assertFalse(self.service.health()['release_ready'])

    def test_cache_keeps_tracking_all_base_files_after_first_build(self):
        self.service.bootstrap()
        self.registry['units'][0] = {**self.registry['units'][0], 'name': '更新的单位名'}
        (self.data / 'units.json').write_text(json.dumps(self.registry, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(self.service.bootstrap()['units'][0]['name'], '更新的单位名')
        history = self.data / 'history.json'
        history.write_text(json.dumps({'score_lines': [{'id': 'new-line', 'year': 2026, 'total': 300}]}), encoding='utf-8')
        self.assertEqual(self.service.bootstrap()['score_lines'][0]['id'], 'new-line')
        history.unlink()
        self.assertEqual(self.service.bootstrap()['score_lines'], [])

    def test_enrichment_hook_tracks_overlay_json_and_rejected_source_dependencies(self):
        overlay = self.data / 'program_enrichment.json'
        overlay.write_text('{"fee":8000}', encoding='utf8')
        source = self.data / 'snapshots/fees.html'
        raw = b'2027 official annual tuition'
        source.write_bytes(raw)
        document = {**self.document, 'id': 'overlay-fees', 'kind': 'fees',
                    'local_path': 'snapshots/fees.html', 'sha256': hashlib.sha256(raw).hexdigest()}
        def enrich(registry, admissions, data_dir):
            fee = json.loads((data_dir / 'program_enrichment.json').read_text(encoding='utf8'))['fee']
            valid = source.exists() and source.read_bytes() == raw
            return {**admissions, 'documents': [*admissions['documents'], document],
                    'programs': [{**program, 'tuition': fee if valid else None} for program in admissions['programs']]}
        with patch('service.apply_enrichment', side_effect=enrich, create=True) as apply:
            first = self.service.bootstrap()
            self.assertEqual(first['programs'][0]['tuition'], 8000)
            self.service.bootstrap()
            self.assertEqual(apply.call_count, 1)
            before = overlay.stat()
            with overlay.open('r+b') as stream: stream.write(b'{"fee":9000}')
            os.utime(overlay, ns=(before.st_atime_ns, before.st_mtime_ns))
            second = self.service.bootstrap()
            self.assertEqual(second['programs'][0]['tuition'], 9000)
            self.assertNotEqual(first['service']['data_revision'], second['service']['data_revision'])
            source.unlink()
            self.assertIsNone(self.service.bootstrap()['programs'][0]['tuition'])
            source.write_bytes(raw)
            self.assertEqual(self.service.bootstrap()['programs'][0]['tuition'], 9000)

    def test_planning_routes_apply_scope_and_keep_release_gate(self):
        payload = {'profile': {'reference_year': 2027}, 'unit_ids': ['123']}
        with patch('planning_engine.plan_routes', return_value={'routes': []}) as plan:
            result = self.service.route_plan(payload)
        self.assertFalse(result['release_ready'])
        self.assertEqual(result['scope']['unit_ids'], ['123'])
        self.assertEqual(plan.call_args.args[0][0]['id'], 'a-1')
        with patch('planning_engine.suggest_adjustments', return_value={'suggestions': []}) as suggest:
            result = self.service.adjustments({**payload, 'allowed_changes': {'provinces': [['天津']]}, 'goal_min_candidates': 2})
        self.assertEqual(suggest.call_args.kwargs['goal_min_candidates'], 2)
        self.assertEqual(result['suggestions'], [])
        for action, bad in [(self.service.route_plan, {**payload, 'extra': True}),
                            (self.service.adjustments, payload)]:
            with self.assertRaises(ServiceError): action(bad)
        with patch('planning_engine.plan_routes', side_effect=ValueError('private')):
            with self.assertRaises(ServiceError) as error: self.service.route_plan(payload)
        self.assertEqual(error.exception.code, 'invalid_profile')

    def test_source_tasks_without_key_and_investigation_derive_only_public_facts(self):
        tasks = self.service.source_tasks('a-1')
        self.assertEqual(tasks['program_id'], 'a-1')
        self.assertIn('enrollment', {item['kind'] for item in tasks['tasks']})
        with patch.object(self.service.proof, 'fetcher', side_effect=AssertionError('must not fetch without key')):
            result = self.service.source_proof({'program_id': 'a-1', 'kind': 'fees'})
        self.assertEqual(result['status'], 'not_configured')
        self.assertEqual(result['unit_id'], '123')
        self.assertFalse(self.service.bootstrap()['coverage']['release_ready'])
        for payload in [{'program_id': 'missing', 'kind': 'fees'}, {'program_id': 'a-1', 'kind': []},
                        {'program_id': 'a-1', 'kind': 'fees', 'profile': {'background': 'private'}}]:
            with self.subTest(payload=payload), self.assertRaises(ServiceError): self.service.source_proof(payload)
        self.service.proof = Mock()
        self.service.proof.tasks.side_effect = ProofError('storage_error', '记录不可读', 500)
        with self.assertRaises(ServiceError) as error: self.service.source_tasks('a-1')
        self.assertEqual(error.exception.code, 'storage_error')
        self.service.proof.investigate.side_effect = ProofError('storage_error', '记录不可写', 500)
        with self.assertRaises(ServiceError): self.service.source_proof({'program_id': 'a-1', 'kind': 'fees'})

    def test_radar_watches_check_and_filters_are_explicit_and_do_not_change_catalogue(self):
        self.service.radar.fetcher = Mock(return_value={'raw': (self.data / self.document['local_path']).read_bytes(),
                                                        'url': self.document['url'], 'content_type': 'text/html'})
        before = (self.data / 'admissions.json').read_bytes()
        self.assertEqual(self.service.radar_check({})['watch_count'], 0)
        self.service.radar.fetcher.assert_not_called()
        self.assertEqual(self.service.radar_watch({'program_ids': ['a-1']})['watch_count'], 1)
        self.service.radar.fetcher.assert_not_called()
        self.assertEqual(self.service.radar_status(unit_id='123')['program_ids'], ['a-1'])
        self.assertEqual(self.service.radar_status(program_id='a-1', year=2027)['program_ids'], ['a-1'])
        self.assertEqual(self.service.radar_status(year=2026)['watch_count'], 0)
        checked = self.service.radar_check({})
        self.assertEqual(checked['check_state'], 'complete')
        self.service.radar.fetcher.assert_called_once()
        self.assertEqual(before, (self.data / 'admissions.json').read_bytes())
        for payload in [{'program_ids': ['missing']}, {'program_ids': ['a-1'], 'year': 2027}]:
            with self.assertRaises(ServiceError): self.service.radar_watch(payload)
        with self.assertRaises(ServiceError): self.service.radar_check({'program_ids': []})

    def test_radar_wrapper_handles_broken_runtime_without_exposing_paths(self):
        self.service.radar = Mock()
        self.service.radar.status.side_effect = ValueError(str(self.data))
        with self.assertRaises(ServiceError) as error: self.service.radar_status()
        self.assertNotIn(str(self.data), error.exception.message)
        self.service.radar.status.side_effect = None
        self.service.radar.status.return_value = {'watch_count': 1}
        self.service.radar.watch.side_effect = OSError(str(self.data))
        with self.assertRaises(ServiceError): self.service.radar_watch({'program_ids': ['a-1']})
        self.service.radar.check.side_effect = OSError(str(self.data))
        with self.assertRaises(ServiceError): self.service.radar_check({})



if __name__ == '__main__':
    unittest.main()
