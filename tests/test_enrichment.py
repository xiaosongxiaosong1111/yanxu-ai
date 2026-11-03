import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from json_reader import read_json
from unittest.mock import patch

from enrichment import apply_enrichment


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = {'units': [{'id': 'u', 'name': '测试大学', 'official_url': 'https://www.a.edu.cn'}]}
        self.program = {'id': 'p', 'unit_id': 'u', 'year': 2027, 'department_code': '001',
                        'code': '081200', 'name': '计算机科学与技术', 'degree_type': '学术学位',
                        'study_mode': '全日制', 'tuition': None, 'duration_years': None,
                        'conditions': [], 'requires_review': True, 'eligibility_evidence_complete': False}
        self.admissions = {'metadata': {'reference_year': 2027}, 'documents': [], 'programs': [self.program]}
        self.source = self.root / 'fees.html'
        self.source.write_text('<h1>测试大学2027级研究生学费学制</h1><p>学硕专业 8000</p>'
                               '<p>收费标准（元/生.学年）</p><p>学术学位学习方式均为全日制，学制3年。</p>', 'utf-8')
        self.document = {'id': 'd', 'unit_id': 'u', 'year': 2027, 'kind': 'fees', 'status': 'verified',
                         'url': 'https://grad.a.edu.cn/fees', 'local_path': 'fees.html',
                         'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}
        self.document['cohort_evidence'] = self.evidence('测试大学2027级研究生学费学制')
        self.rule = {'id': 'r', 'unit_id': 'u', 'year': 2027, 'program_ids': ['p'],
                     'selector': {'degree_type': '学术学位', 'study_mode': '全日制'},
                     'fields': {'tuition': {'value': {'amount': 8000, 'period': 'year', 'currency': 'CNY'},
                                            'evidence': [self.evidence('学硕专业 8000'), self.evidence('收费标准（元/生.学年）')]},
                                'duration_years': {'value': 3, 'evidence': [self.evidence('学术学位学习方式均为全日制，学制3年。')]}}}
        self.overlay = {'schema_version': 1, 'documents': [self.document], 'rules': [self.rule]}

    def evidence(self, quote, **extra):
        return {'document_id': 'd', 'url': 'https://grad.a.edu.cn/fees', 'page': 1, 'quote': quote, **extra}

    def apply(self):
        (self.root / 'program_enrichment.json').write_text(json.dumps(self.overlay, ensure_ascii=False), 'utf-8')
        return apply_enrichment(self.registry, self.admissions, self.root)

    def test_missing_overlay_returns_original(self):
        self.assertIs(apply_enrichment(self.registry, self.admissions, self.root), self.admissions)

    def test_verified_fields_and_provenance_without_eligibility_changes(self):
        before = copy.deepcopy(self.admissions)
        result = self.apply()
        row = result['programs'][0]
        self.assertEqual(row['tuition']['amount'], 8000)
        self.assertEqual(row['tuition']['period'], 'year')
        self.assertNotIn('total', row['tuition'])
        self.assertEqual(row['duration_years'], 3)
        self.assertEqual(row['tuition']['evidence'], self.rule['fields']['tuition']['evidence'])
        self.assertTrue(row['duration_evidence'])
        self.assertTrue(row['enrichment_evidence'])
        self.assertTrue(row['requires_review'])
        self.assertFalse(row['eligibility_evidence_complete'])
        self.assertEqual(self.admissions, before)
        self.assertEqual(result['metadata']['enrichment']['applied_count'], 1)
        self.assertEqual(result['metadata']['enrichment']['invalid_rule_ids'], [])

    def test_existing_nonempty_fields_are_never_overwritten(self):
        self.admissions['programs'] = [{**self.program, 'tuition': {'amount': 123, 'period': 'year'}, 'duration_years': 2}]
        result = self.apply()
        self.assertEqual(result['programs'], self.admissions['programs'])
        self.assertEqual(result['metadata']['enrichment']['applied_count'], 0)

    def test_whitelist_year_school_mode_and_selector_are_all_required(self):
        variants = [{'id': 'not-whitelisted'}, {'year': 2028}, {'unit_id': 'other'},
                    {'study_mode': '非全日制'}, {'degree_type': '专业学位'}]
        for variant in variants:
            with self.subTest(variant=variant):
                self.admissions['programs'] = [{**self.program, **variant}]
                result = self.apply()
                self.assertIsNone(result['programs'][0]['tuition'])
                self.assertIn('r', result['metadata']['enrichment']['invalid_rule_ids'])
        self.admissions['programs'] = [self.program, {**self.program, 'id': 'new-row'}]
        result = self.apply()
        self.assertIsNone(result['programs'][1]['tuition'])
        self.assertEqual(result['metadata']['enrichment']['applied_count'], 1)

    def test_tampering_and_missing_source_reject_values_but_keep_dependencies(self):
        original = self.source.read_bytes()
        for raw in [b'changed', None]:
            with self.subTest(raw=raw):
                if raw is None:
                    self.source.unlink(missing_ok=True)
                else:
                    self.source.write_bytes(raw)
                result = self.apply()
                self.assertIsNone(result['programs'][0]['tuition'])
                self.assertIsNone(result['programs'][0]['duration_years'])
                self.assertEqual(result['documents'], [self.document])
                self.assertIn('r', result['metadata']['enrichment']['invalid_rule_ids'])
        self.source.write_bytes(original)
        self.assertEqual(self.apply()['programs'][0]['tuition']['amount'], 8000)

    def test_bad_field_does_not_suppress_other_verified_field(self):
        for change in [{'value': {'amount': 9000, 'period': 'year', 'currency': 'CNY'}},
                       {'evidence': [self.evidence('不连续拼接的引文')]},
                       {'value': {'amount': 8000, 'period': 'total', 'currency': 'CNY'}},
                       {'value': {'amount': True, 'period': 'year', 'currency': 'CNY'}}]:
            with self.subTest(change=change):
                saved = copy.deepcopy(self.rule['fields']['tuition'])
                self.rule['fields']['tuition'] = {**saved, **change}
                result = self.apply()
                self.assertIsNone(result['programs'][0]['tuition'])
                self.assertEqual(result['programs'][0]['duration_years'], 3)
                self.assertIn('r', result['metadata']['enrichment']['invalid_rule_ids'])
                self.rule['fields']['tuition'] = saved

    def test_wrong_kind_year_school_host_and_source_path_fail_closed(self):
        for change in [{'kind': 'catalog'}, {'year': 2028}, {'unit_id': 'other'}, {'status': []}, {'content_type': []},
                       {'url': 'https://evil.example/fees'}, {'local_path': '../fees.html'}]:
            with self.subTest(change=change):
                self.overlay['documents'] = [{**self.document, **change}]
                result = self.apply()
                self.assertIsNone(result['programs'][0]['tuition'])
                self.assertIn('r', result['metadata']['enrichment']['invalid_rule_ids'])

    def test_quote_must_be_on_the_exact_page_not_adjacent_page(self):
        pages = ['测试大学2027级研究生学费学制 收费标准（元/生.学年）',
                 '学硕专业 8000 学术学位学习方式均为全日制，学制3年。']
        with patch('service_verification.SourceVerifier.pages', return_value=pages):
            result = self.apply()
        self.assertIsNone(result['programs'][0]['tuition'])
        self.assertIsNone(result['programs'][0]['duration_years'])

    def test_unknown_or_broad_selectors_and_duplicate_ids_are_invalid(self):
        for selector in [{}, {'study_mode': '全日制'}, {'study_mode': '全日制', 'anything': 'yes'},
                         {'study_mode': '全日制', 'department_code': []}]:
            with self.subTest(selector=selector):
                self.rule['selector'] = selector
                self.assertIsNone(self.apply()['programs'][0]['tuition'])
        self.rule['selector'] = {'study_mode': '全日制', 'degree_type': '学术学位'}
        self.rule['program_ids'] = ['p', 'p']
        self.assertIsNone(self.apply()['programs'][0]['tuition'])

    def test_absent_cohort_proof_and_conflicting_documents_are_invalid(self):
        self.overlay['documents'] = [{k: v for k, v in self.document.items() if k != 'cohort_evidence'}]
        self.assertIsNone(self.apply()['programs'][0]['tuition'])
        self.overlay['documents'] = [self.document, {**self.document, 'sha256': '0' * 64}]
        result = self.apply()
        self.assertIsNone(result['programs'][0]['tuition'])
        self.assertEqual(len(result['documents']), 2)

    def test_previous_cohort_with_incidental_target_year_is_not_2027_policy(self):
        title = '测试大学2026级研究生学费学制（附2027年报名提醒）'
        self.source.write_text(title + ' 学硕专业 8000 收费标准（元/生.学年）'
                               ' 学术学位学习方式均为全日制，学制3年。', 'utf-8')
        self.document['sha256'] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.document['cohort_evidence']['quote'] = title
        self.assertIsNone(self.apply()['programs'][0]['tuition'])

    def test_malformed_overlay_reports_error_without_changing_input(self):
        for raw in ['{', '[]', '{"schema_version":2,"documents":[],"rules":[]}']:
            with self.subTest(raw=raw):
                (self.root / 'program_enrichment.json').write_text(raw, 'utf-8')
                result = apply_enrichment(self.registry, self.admissions, self.root)
                self.assertEqual(result['programs'], self.admissions['programs'])
                self.assertTrue(result['metadata']['enrichment']['invalid_rule_ids'])

    def test_wrong_scalar_types_fail_closed_instead_of_crashing(self):
        changes = [lambda: self.rule.update(unit_id=[]),
                   lambda: self.rule['selector'].update(study_mode=['全日制']),
                   lambda: self.rule['fields']['tuition']['evidence'][0].update(document_id=[])]
        original = copy.deepcopy(self.overlay)
        for change in changes:
            with self.subTest(change=change):
                self.overlay = copy.deepcopy(original)
                self.rule = self.overlay['rules'][0]
                change()
                result = self.apply()
                self.assertIsNone(result['programs'][0]['tuition'])
                self.assertTrue(result['metadata']['enrichment']['invalid_rule_ids'])

    def test_unbounded_integer_values_are_rejected_without_float_overflow(self):
        for field in ['tuition', 'duration_years']:
            with self.subTest(field=field):
                if field == 'tuition':
                    self.rule['fields'][field]['value']['amount'] = 10 ** 400
                else:
                    self.rule['fields'][field]['value'] = 10 ** 400
                result = self.apply()
                self.assertIsNone(result['programs'][0][field])
                self.assertIn('r', result['metadata']['enrichment']['invalid_rule_ids'])

    def test_linked_current_fee_requires_verified_2027_charter_and_publication(self):
        charter = self.root / 'charter.html'
        charter.write_text('测试大学2027年硕士招生章程 费用见 https://grad.a.edu.cn/fee-list', 'utf-8')
        doc = {'id': 'charter', 'unit_id': 'u', 'year': 2027, 'kind': 'charter', 'status': 'verified',
               'url': 'https://grad.a.edu.cn/charter', 'local_path': 'charter.html',
               'sha256': hashlib.sha256(charter.read_bytes()).hexdigest()}
        doc['cohort_evidence'] = {'document_id': 'charter', 'url': doc['url'], 'page': 1,
                                  'quote': '测试大学2027年硕士招生章程'}
        publication = self.root / 'publication.html'
        publication.write_text('<a href="/fees">收费附件</a>', 'utf-8')
        self.document.pop('cohort_evidence')
        self.document['applicability_evidence'] = {**doc['cohort_evidence'], 'quote': '费用见 https://grad.a.edu.cn/fee-list'}
        self.document['publication_page'] = {'url': 'https://grad.a.edu.cn/fee-list',
                                            'local_path': 'publication.html',
                                            'sha256': hashlib.sha256(publication.read_bytes()).hexdigest()}
        self.overlay['documents'].append(doc)
        self.assertEqual(self.apply()['programs'][0]['tuition']['amount'], 8000)
        publication.write_text('<a href="/other">错误附件</a>', 'utf-8')
        self.document['publication_page']['sha256'] = hashlib.sha256(publication.read_bytes()).hexdigest()
        result = self.apply()
        self.assertIsNone(result['programs'][0]['tuition'])
        self.assertEqual(result['documents'], self.overlay['documents'])
        self.document['applicability_evidence']['document_id'] = []
        self.assertIsNone(self.apply()['programs'][0]['tuition'])


class RealEnrichmentTests(unittest.TestCase):
    def test_official_262_records_and_precise_annual_amounts(self):
        root = Path(__file__).resolve().parents[1] / 'data'
        registry = json.loads((root / 'units.json').read_text('utf-8'))
        admissions = read_json(root / 'admissions.json')
        before = copy.deepcopy(admissions)
        result = apply_enrichment(registry, admissions, root)
        report = result['metadata']['enrichment']
        self.assertEqual(report['invalid_rule_ids'], [])
        self.assertEqual(report['applied_count'], 262)
        filled = [p for p in result['programs'] if p.get('tuition') is not None
                  and p['unit_id'] in {'367949', '368317'}]
        self.assertEqual(len(filled), 262)
        self.assertEqual(sum(p['tuition']['amount'] == 8000 for p in filled), 252)
        self.assertEqual(sum(p['tuition']['amount'] == 14000 for p in filled), 9)
        self.assertEqual(sum(p['tuition']['amount'] == 18000 for p in filled), 1)
        self.assertTrue(all(p['duration_years'] == 3 and p['tuition']['period'] == 'year' for p in filled))
        self.assertEqual(admissions, before)
        for old, new in zip(before['programs'], result['programs']):
            for field in ['requires_review', 'eligibility_evidence_complete', 'conditions']:
                self.assertEqual(old[field], new[field])


if __name__ == '__main__':
    unittest.main()
