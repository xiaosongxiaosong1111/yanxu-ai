import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import national_data_baseline as baseline


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), 'utf-8')


def entry_evidence(url='https://a.edu.cn/'):
    return {'url': 'https://yz.chsi.com.cn/sch/schoolInfo--schId-1.dhtml',
            'verification': 'saved_original_exact_link',
            'quote': url, 'linked_url': url}


class ProfileTests(unittest.TestCase):
    def test_original_profile_counts_real_urls_without_double_counting(self):
        text = ('https://a.edu.cn/zsml/2027.htm\n'
                'https://a.edu.cn/zsml/2027.htm\n'
                'https://a.edu.cn/files/2027-zyml.pdf\n')
        profile = baseline.original_profile(text)
        self.assertEqual(profile['url_count'], 2)
        self.assertEqual(profile['pdf_count'], 1)
        self.assertEqual(profile['catalog_like_count'], 2)
        self.assertEqual(profile['hosts'], ['a.edu.cn'])

    def test_original_profile_ignores_blank_and_non_http_rows(self):
        profile = baseline.original_profile('\n  \nftp://a.edu.cn/x.pdf\n')
        self.assertEqual(profile, {'url_count': 0, 'pdf_count': 0,
                                   'catalog_like_count': 0, 'hosts': []})


class GapTests(unittest.TestCase):
    def row(self, **overrides):
        value = {'unit_id': '1', 'official_url': 'https://a.edu.cn/',
                 'official_url_evidence': entry_evidence(),
                 'catalog_status': 'not_collected', 'collection_status': 'collected',
                 'missing_reason': '已保存官方原文，专业目录尚待解析或核验，不能据此认定没有招生专业。'}
        value.update(overrides)
        return value

    def stats(self, **overrides):
        value = {'program_count': 0, 'full_qualification': 0, 'tuition': 0, 'duration': 0}
        value.update(overrides)
        return value

    def test_saved_originals_without_adopted_programs_are_parser_pending(self):
        gaps = baseline.classify_gaps(self.row(), self.stats())
        self.assertIn('missing_catalog', gaps)
        self.assertIn('parser_pending', gaps)
        self.assertNotIn('original_pending', gaps)

    def test_login_restricted_source_is_original_pending_not_no_data(self):
        gaps = baseline.classify_gaps(
            self.row(collection_status='needs_binding', official_url='',
                     missing_reason='来源要求登录或限制访问，尚未完成公开原文采集。'),
            self.stats())
        self.assertIn('missing_trusted_entry', gaps)
        self.assertIn('original_pending', gaps)

    def test_adopted_programs_report_field_gaps_and_review(self):
        gaps = baseline.classify_gaps(
            self.row(catalog_status='partial', collection_status='collected',
                     missing_reason='已有部分专业资料，全校目录及全部考试科目尚未对账完整。'),
            self.stats(program_count=12))
        self.assertIn('partial_catalog', gaps)
        self.assertIn('review_pending', gaps)
        self.assertIn('missing_full_qualification', gaps)
        self.assertIn('missing_tuition', gaps)
        self.assertIn('missing_duration', gaps)
        self.assertNotIn('missing_catalog', gaps)

    def test_complete_fields_do_not_report_missing_gaps(self):
        gaps = baseline.classify_gaps(
            self.row(catalog_status='recorded_verified'),
            self.stats(program_count=5, full_qualification=5, tuition=5, duration=5))
        self.assertEqual(gaps, [])

    def test_partial_field_coverage_keeps_the_remaining_direction_gaps(self):
        gaps = baseline.classify_gaps(
            self.row(catalog_status='recorded_verified'),
            {'program_count': 5, 'verified_tuition': 1, 'verified_duration': 1,
             'verified_full_qualification': 1})
        self.assertIn('partial_tuition', gaps)
        self.assertIn('partial_duration', gaps)
        self.assertIn('partial_full_qualification', gaps)
        self.assertNotIn('missing_tuition', gaps)
        self.assertNotIn('missing_duration', gaps)
        self.assertNotIn('missing_full_qualification', gaps)

    def test_only_tuition_covered_reports_duration_and_qualification_only(self):
        gaps = baseline.classify_gaps(
            self.row(catalog_status='recorded_verified'),
            {'program_count': 5, 'verified_tuition': 5, 'verified_duration': 0,
             'verified_full_qualification': 0})
        self.assertNotIn('partial_tuition', gaps)
        self.assertNotIn('missing_tuition', gaps)
        self.assertIn('missing_duration', gaps)
        self.assertIn('missing_full_qualification', gaps)

    def test_partial_qualification_is_not_reported_as_complete(self):
        gaps = baseline.classify_gaps(
            self.row(catalog_status='recorded_verified'),
            {'program_count': 5, 'verified_tuition': 5, 'verified_duration': 5,
             'verified_full_qualification': 4})
        self.assertEqual(gaps, ['partial_full_qualification'])

    def test_unapproved_entry_is_missing_even_when_a_url_string_exists(self):
        gaps = baseline.classify_gaps(
            self.row(official_url='https://unreviewed.example.edu.cn/',
                     missing_reason='尚未核准该单位的官方招生网站入口。'),
            self.stats())
        self.assertIn('missing_trusted_entry', gaps)

    def test_queue_without_programs_does_not_claim_field_gaps(self):
        gaps = baseline.classify_gaps(self.row(), self.stats())
        self.assertNotIn('missing_tuition', gaps)
        self.assertNotIn('missing_full_qualification', gaps)


class EntryTests(unittest.TestCase):
    def test_entry_requires_replayable_ownership_evidence(self):
        row = {'official_url': 'https://a.edu.cn/', 'gaps': [],
               'official_url_evidence': entry_evidence()}
        self.assertTrue(baseline.entry_trusted(row))

    def test_entry_evidence_may_link_a_deeper_page_on_the_same_host(self):
        evidence = entry_evidence('https://a.edu.cn/yjszs/index.htm')
        row = {'official_url': 'https://a.edu.cn/', 'gaps': [],
               'official_url_evidence': evidence}
        self.assertTrue(baseline.entry_trusted(row))

    def test_entry_rejects_unapproved_or_missing_evidence(self):
        base = {'official_url': 'https://a.edu.cn/', 'gaps': []}
        self.assertFalse(baseline.entry_trusted(base))
        self.assertFalse(baseline.entry_trusted({**base, 'gaps': ['missing_trusted_entry'],
                                                 'official_url_evidence': entry_evidence()}))
        self.assertFalse(baseline.entry_trusted(
            {**base, 'missing_reason': '尚未核准该单位的官方招生网站入口。',
             'official_url_evidence': entry_evidence()}))
        self.assertFalse(baseline.entry_trusted({**base, 'official_url': ''}))
        self.assertFalse(baseline.entry_trusted(
            {**base, 'official_url_evidence': {'verification': '', 'quote': 'https://a.edu.cn/'}}))

    def test_entry_rejects_evidence_for_another_host(self):
        row = {'official_url': 'https://a.edu.cn/', 'gaps': [],
               'official_url_evidence': entry_evidence('https://b.edu.cn/')}
        self.assertFalse(baseline.entry_trusted(row))

    def test_entry_handles_malformed_evidence(self):
        row = {'official_url': 'https://a.edu.cn/', 'gaps': [],
               'official_url_evidence': {'verification': 'x', 'quote': 'not a url'}}
        self.assertFalse(baseline.entry_trusted(row))
        self.assertEqual(baseline._host('http://[::1'), '')


class CountTests(unittest.TestCase):
    def test_count_programs_ignores_invalid_values_and_flags(self):
        invalid = {'id': 'p1', 'unit_id': 'u1', 'year': 2027,
                   'tuition': {'amount': None, 'period': 'year'},
                   'duration_years': 0, 'conditions': [],
                   'eligibility_evidence_complete': True, 'requires_review': False}
        totals = baseline.count_programs([invalid])['totals']
        self.assertEqual(totals['program_count'], 1)
        self.assertEqual(totals['tuition'], 0)
        self.assertEqual(totals['duration'], 0)
        self.assertEqual(totals['computed_total_cost'], 0)
        self.assertEqual(totals['full_qualification'], 0)

    def test_count_programs_counts_structurally_valid_raw_values(self):
        program = {'id': 'p1', 'unit_id': 'u1', 'year': 2027,
                   'tuition': {'amount': 12000, 'period': 'year'},
                   'duration_years': 3, 'conditions': [{'evidence': []}],
                   'eligibility_evidence_complete': True, 'requires_review': False}
        result = baseline.count_programs([program])
        totals = result['totals']
        self.assertEqual((totals['tuition'], totals['tuition_year'], totals['duration'],
                          totals['computed_total_cost'], totals['full_qualification']),
                         (1, 1, 1, 1, 1))
        self.assertEqual(result['per_unit']['u1']['tuition'], 1)

    def test_count_programs_handles_full_period_other_period_and_duplicates(self):
        programs = [
            {'id': 'p1', 'unit_id': 'u1', 'year': 2027,
             'tuition': {'amount': 30000, 'period': 'full'}, 'duration_years': None},
            {'id': 'p1', 'unit_id': 'u1', 'year': 2028,
             'tuition': {'amount': 1000, 'period': 'month'}, 'duration_years': 2},
            {'id': 'p2', 'unit_id': 'u1', 'year': 2027, 'tuition': 5000,
             'duration_years': 2},
        ]
        totals = baseline.count_programs(programs)['totals']
        self.assertEqual(totals['duplicate_ids'], 1)
        self.assertEqual(totals['tuition'], 2)
        self.assertEqual(totals['tuition_full'], 1)
        self.assertEqual(totals['tuition_other'], 1)
        self.assertEqual(totals['computed_total_cost'], 1)
        self.assertEqual(totals['duration'], 2)
        self.assertEqual(totals['years'][2027], 2)

    def test_stream_counts_reads_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'admissions.json'
            write_json(path, {'programs': [{'id': 'p1', 'unit_id': 'u1', 'year': 2027}]})
            self.assertEqual(baseline.stream_counts(path)['totals']['program_count'], 1)


class FakeIndex:
    def __init__(self, status='verified'):
        self.status = status
        self.calls = []

    def _claim(self, identifier, label, value, refs, context, *, verified=False, note=''):
        self.calls.append((identifier, verified))
        return {'status': self.status if verified else 'needs_review'}


class VerifiedCountTests(unittest.TestCase):
    def program(self, **overrides):
        value = {'id': 'p1', 'unit_id': 'u1', 'year': 2027,
                 'tuition': {'amount': 12000, 'period': 'year', 'evidence': [{}]},
                 'duration_years': 3, 'duration_evidence': [{}],
                 'conditions': [{'evidence': [{}]}],
                 'eligibility_evidence_complete': True, 'requires_review': False}
        value.update(overrides)
        return value

    def test_verified_counts_require_a_verified_claim(self):
        program = self.program()
        result = baseline.verified_counts(FakeIndex(), [program])
        totals = result['totals']
        self.assertEqual((totals['verified_tuition'], totals['verified_duration'],
                          totals['verified_full_qualification'],
                          totals['verified_computed_total_cost']), (1, 1, 1, 1))
        self.assertEqual(result['per_unit']['u1']['verified_tuition'], 1)

    def test_needs_review_claims_are_not_counted_as_verified(self):
        totals = baseline.verified_counts(FakeIndex('needs_review'), [self.program()])['totals']
        self.assertEqual((totals['verified_tuition'], totals['verified_duration'],
                          totals['verified_full_qualification'],
                          totals['verified_computed_total_cost']), (0, 0, 0, 0))
        self.assertEqual(totals['tuition_claim_statuses'], {'needs_review': 1})
        self.assertEqual(totals['duration_claim_statuses'], {'needs_review': 1})
        self.assertEqual(totals['qualification_claim_statuses'], {'needs_review': 1})

    def test_invalid_values_are_not_verified_even_with_a_verified_claim(self):
        program = self.program(tuition={'amount': None, 'period': 'year'},
                               duration_years=0)
        totals = baseline.verified_counts(FakeIndex(), [program])['totals']
        self.assertEqual((totals['verified_tuition'], totals['verified_duration'],
                          totals['verified_computed_total_cost']), (0, 0, 0))

    def test_full_qualification_needs_conditions_and_no_review_flag(self):
        no_conditions = self.program(conditions=[])
        flagged = self.program(requires_review=True)
        totals = baseline.verified_counts(FakeIndex(), [no_conditions, flagged])['totals']
        self.assertEqual(totals['verified_full_qualification'], 0)

    def test_verified_computed_total_covers_full_period_and_year_times_duration(self):
        full = self.program(tuition={'amount': 30000, 'period': 'full', 'evidence': [{}]},
                            duration_years=None)
        year = self.program(id='p2')
        no_duration = self.program(id='p3', duration_years=None)
        totals = baseline.verified_counts(FakeIndex(), [full, year, no_duration])['totals']
        self.assertEqual(totals['verified_tuition'], 3)
        self.assertEqual(totals['verified_computed_total_cost'], 2)

    def test_verified_counts_handles_non_dict_tuition(self):
        program = self.program(tuition=5000)
        totals = baseline.verified_counts(FakeIndex(), [program])['totals']
        self.assertEqual(totals['verified_tuition'], 0)

    def test_verified_claim_with_unsupported_period_is_not_verified(self):
        program = self.program(tuition={'amount': 1000, 'period': 'month',
                                        'evidence': [{}]})
        totals = baseline.verified_counts(FakeIndex(), [program])['totals']
        self.assertEqual(totals['verified_tuition'], 0)
        self.assertEqual(totals['tuition_claim_statuses'], {'invalid': 1})


class EffectiveCountTests(unittest.TestCase):
    def test_effective_counts_separates_presence_from_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            write_json(data / 'units.json', {'units': [
                {'id': 'u1', 'name': '甲大学', 'official_url': 'https://a.edu.cn/',
                 'official_url_evidence': entry_evidence()}]})
            write_json(data / 'admissions.json', {
                'documents': [], 'admitted_statistics': [], 'score_lines': [], 'metadata': {},
                'programs': [{'id': 'p1', 'unit_id': 'u1', 'year': 2027,
                              'tuition': {'amount': 12000, 'period': 'year', 'evidence': []},
                              'duration_years': 3, 'duration_evidence': [],
                              'conditions': [], 'eligibility_evidence_complete': False,
                              'requires_review': True}]})
            result = baseline.effective_counts(data)
            self.assertEqual(result['totals']['tuition'], 1)
            self.assertEqual(result['totals']['duration'], 1)
            self.assertEqual(result['totals']['verified_tuition'], 0)
            self.assertEqual(result['totals']['verified_duration'], 0)
            self.assertEqual(result['totals']['verified_computed_total_cost'], 0)
            self.assertEqual(result['totals']['suspended_conflicts'], 0)
            self.assertEqual(result['per_unit']['u1']['verified_tuition'], 0)

    def test_effective_counts_without_overlay_or_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            write_json(data / 'units.json', {'units': []})
            write_json(data / 'admissions.json', {
                'documents': [], 'admitted_statistics': [], 'score_lines': [],
                'metadata': {}, 'programs': []})
            result = baseline.effective_counts(data)
            self.assertEqual(result['totals']['program_count'], 0)
            self.assertEqual(result['totals']['suspended_conflicts'], 0)


class UnitsTests(unittest.TestCase):
    def test_units_only_keeps_confirmed_integer_year_records(self):
        registry = {'units': [{'id': 'u1', 'name': '甲', 'unit_code': '10001',
                               'province': '甲省', 'official_url': 'https://a.edu.cn/',
                               'official_url_evidence': entry_evidence()},
                              {'id': 'u2', 'name': '乙'}]}
        annual = {'units': [
            {'unit_id': 'u1', 'year': 2027, 'annual_status': 'confirmed',
             'evidence': [{'id': 'e1'}]},
            {'unit_id': 'u2', 'year': 2027, 'annual_status': 'not_listed'},
            {'unit_id': 'u2', 'year': '2027', 'annual_status': 'confirmed'},
        ]}
        rows = baseline._units(registry, annual)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['unit_code'], '10001')
        self.assertEqual(rows[0]['annual_evidence_count'], 1)
        self.assertEqual(rows[0]['official_url'], 'https://a.edu.cn/')


class QueueTests(unittest.TestCase):
    def test_build_queue_keeps_raw_effective_verified_and_remaining_counts(self):
        annual = [{'unit_id': 'u1', 'year': 2027, 'annual_status': 'confirmed',
                   'official_url': 'https://a.edu.cn/', 'official_url_evidence': entry_evidence()}]
        coverage = [{'unit_id': 'u1', 'program_count': '10', 'catalog_status': 'recorded_verified',
                     'collection_status': 'collected', 'missing_reason': '',
                     'source_count': '2', 'original_urls': 'https://a.edu.cn/zsml.htm',
                     'official_url': 'https://a.edu.cn/'}]
        raw = {'u1': {'program_count': 10, 'tuition': 2, 'duration': 4,
                      'full_qualification': 0, 'conditions_nonempty': 0}}
        effective = {'u1': {'program_count': 10, 'tuition': 4, 'duration': 6,
                            'full_qualification': 0, 'verified_tuition': 3,
                            'verified_duration': 5, 'verified_full_qualification': 1,
                            'verified_computed_total_cost': 3}}
        queue = baseline.build_queue(coverage, raw, effective, annual)
        row = queue[0]
        self.assertEqual(row['raw_tuition'], 2)
        self.assertEqual(row['effective_tuition'], 4)
        self.assertEqual(row['verified_tuition'], 3)
        self.assertEqual(row['remaining_tuition'], 7)
        self.assertEqual(row['remaining_duration'], 5)
        self.assertEqual(row['remaining_full_qualification'], 9)
        self.assertIn('partial_tuition', row['gaps'])
        self.assertIn('partial_duration', row['gaps'])
        self.assertFalse(row['count_mismatch'])
        self.assertEqual(row['original_urls'], 'https://a.edu.cn/zsml.htm')

    def test_build_queue_flags_coverage_count_mismatch_and_missing_units(self):
        annual = [{'unit_id': 'u2', 'year': 2027, 'annual_status': 'confirmed'}]
        coverage = [{'unit_id': 'u2', 'program_count': '0'}]
        queue = baseline.build_queue(coverage, {}, {}, annual)
        row = queue[0]
        self.assertEqual(row['verified_tuition'], 0)
        self.assertEqual(row['remaining_tuition'], 0)
        self.assertNotIn('partial_tuition', row['gaps'])


class SelectTests(unittest.TestCase):
    def expansion_row(self, unit_id='1', url='https://a.edu.cn/', gaps=None, evidence=True):
        row = {'unit_id': unit_id, 'unit_name': '甲', 'official_url': url,
               'catalog_status': 'not_collected', 'collection_status': 'collected',
               'missing_reason': '已保存官方原文，专业目录尚待解析或核验，不能据此认定没有招生专业。',
               'original_urls': 'https://a.edu.cn/zsml/2027.htm', 'program_count': 0,
               'gaps': ['missing_catalog', 'parser_pending'] if gaps is None else gaps}
        if evidence:
            row['official_url_evidence'] = entry_evidence(url)
        return row

    def test_expansion_pool_requires_a_trusted_entry(self):
        picks = baseline.select_batch([self.expansion_row(evidence=False)], expansion=1, backfill=0)
        self.assertEqual(picks['expansion'], [])

    def test_expansion_pool_rejects_rows_already_marked_untrusted(self):
        row = self.expansion_row(gaps=['missing_trusted_entry', 'missing_catalog'])
        picks = baseline.select_batch([row], expansion=1, backfill=0)
        self.assertEqual(picks['expansion'], [])

    def test_expansion_pool_prefers_catalog_like_originals_and_never_invents_units(self):
        rows = [self.expansion_row('3', 'https://c.edu.cn/'),
                self.expansion_row('2', 'https://b.edu.cn/'),
                {**self.expansion_row('1', 'https://a.edu.cn/', gaps=['partial_catalog']),
                 'missing_reason': '已有部分专业资料，全校目录及全部考试科目尚未对账完整。'}]
        rows[1]['original_urls'] = ('https://b.edu.cn/zsml/2027.htm\n'
                                    'https://b.edu.cn/2027-zyml.pdf')
        picks = baseline.select_batch(rows, expansion=2, backfill=0)
        self.assertEqual([row['unit_id'] for row in picks['expansion']], ['2', '3'])
        self.assertTrue(all(pick['role'] == 'expand_catalog' for pick in picks['expansion']))

    def test_expansion_requires_original_urls_as_a_hard_error(self):
        row = self.expansion_row()
        del row['original_urls']
        with self.assertRaisesRegex(ValueError, 'original_urls'):
            baseline.select_batch([row], expansion=1, backfill=0)

    def test_expansion_rejects_empty_or_non_url_originals(self):
        row = self.expansion_row()
        row['original_urls'] = ''
        with self.assertRaisesRegex(ValueError, 'empty original_urls'):
            baseline.select_batch([row], expansion=1, backfill=0)
        row['original_urls'] = 'not-a-url'
        self.assertEqual(baseline.select_batch([row], expansion=1, backfill=0)['expansion'], [])

    def test_expansion_requires_persisted_gaps_as_a_hard_error(self):
        row = self.expansion_row()
        del row['gaps']
        with self.assertRaisesRegex(ValueError, 'gaps'):
            baseline.select_batch([row], expansion=1, backfill=0)

    def test_backfill_pool_prefers_units_with_programs_and_no_fields(self):
        rows = [
            {'unit_id': '9', 'program_count': 40, 'tuition': 0, 'duration': 0, 'gaps': []},
            {'unit_id': '8', 'program_count': 12, 'tuition': 0, 'duration': 0, 'gaps': []},
            {'unit_id': '7', 'program_count': 80, 'tuition': 3, 'duration': 0, 'gaps': []},
        ]
        picks = baseline.select_batch(rows, expansion=0, backfill=2)
        self.assertEqual([row['unit_id'] for row in picks['backfill']], ['9', '8'])

    def test_backfill_pool_retains_partially_covered_units_by_remaining_count(self):
        rows = [
            {'unit_id': '5', 'program_count': 100, 'verified_tuition': 90,
             'verified_duration': 95, 'gaps': []},
            {'unit_id': '4', 'program_count': 20, 'verified_tuition': 0,
             'verified_duration': 0, 'gaps': []},
            {'unit_id': '3', 'program_count': 100, 'verified_tuition': 100,
             'verified_duration': 100, 'gaps': []},
        ]
        picks = baseline.select_batch(rows, expansion=0, backfill=2)
        self.assertEqual([row['unit_id'] for row in picks['backfill']], ['4', '5'])

    def test_backfill_pool_prefers_units_whose_originals_are_already_collected(self):
        rows = [
            {'unit_id': '5', 'program_count': 200, 'tuition': 0, 'duration': 0,
             'gaps': ['missing_tuition', 'original_pending']},
            {'unit_id': '4', 'program_count': 30, 'tuition': 0, 'duration': 0,
             'gaps': ['missing_tuition']},
        ]
        picks = baseline.select_batch(rows, expansion=0, backfill=2)
        self.assertEqual([row['unit_id'] for row in picks['backfill']], ['4', '5'])

    def test_select_batch_records_queue_binding(self):
        picks = baseline.select_batch([self.expansion_row()], expansion=1, backfill=0,
                                      queue_sha256='a' * 64)
        self.assertEqual(picks['queue_sha256'], 'a' * 64)
        self.assertEqual(picks['selection_rule_version'], baseline.SELECTION_RULE_VERSION)


class AuditTests(unittest.TestCase):
    def make_fixture(self, root):
        data = root / 'data'
        data.mkdir(parents=True)
        (data / 'bundles').mkdir()
        (data / 'bundles' / 'catalog-data.zip').write_bytes(b'zip-bytes')
        write_json(data / 'bundles' / 'manifest.json', {'files': []})
        write_json(data / 'units.json', {'units': [
            {'id': 'u1', 'name': '甲大学', 'unit_code': '10001', 'province': '甲省',
             'official_url': 'https://a.edu.cn/', 'official_url_evidence': entry_evidence()},
            {'id': 'u2', 'name': '乙大学', 'unit_code': '10002', 'province': '乙省',
             'official_url': 'https://b.edu.cn/',
             'official_url_evidence': entry_evidence('https://b.edu.cn/')}]})
        write_json(data / 'admissions.json', {
            'documents': [], 'admitted_statistics': [], 'score_lines': [],
            'metadata': {},
            'programs': [{'id': 'p1', 'unit_id': 'u2', 'year': 2027, 'code': '081200',
                          'name': '计算机', 'tuition': {'amount': 12000, 'period': 'year',
                          'evidence': []}, 'duration_years': 3, 'duration_evidence': [],
                          'conditions': [], 'eligibility_evidence_complete': False,
                          'requires_review': True}]})
        write_json(data / 'annual_registry_2027.json', {
            'metadata': {'year': 2027, 'official_total': 2, 'indexed_records': 3,
                         'confirmed_units': 2, 'not_listed_index_records': 1},
            'units': [
                {'unit_id': 'u1', 'unit_code': '10001', 'unit_name': '甲大学',
                 'province': '甲省', 'year': 2027, 'annual_status': 'confirmed',
                 'evidence': [{'id': 'e1'}]},
                {'unit_id': 'u2', 'unit_code': '10002', 'unit_name': '乙大学',
                 'province': '乙省', 'year': 2027, 'annual_status': 'confirmed',
                 'evidence': []},
                {'unit_id': 'u3', 'year': 2027, 'annual_status': 'not_listed'},
                {'unit_id': 'u4', 'year': '2027', 'annual_status': 'confirmed'}]})
        with open(data / 'catalog_coverage_2027.csv', 'w', encoding='utf-8-sig',
                  newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                'unit_id', 'unit_code', 'unit_name', 'province', 'annual_status',
                'program_count', 'fact_count', 'catalog_status', 'collection_status',
                'source_count', 'missing_reason', 'official_url', 'original_urls'])
            writer.writeheader()
            writer.writerow({'unit_id': 'u1', 'unit_code': '10001', 'unit_name': '甲大学',
                             'province': '甲省', 'annual_status': 'confirmed',
                             'program_count': '0', 'fact_count': '0',
                             'catalog_status': 'not_collected',
                             'collection_status': 'collected', 'source_count': '2',
                             'missing_reason': '已保存官方原文，专业目录尚待解析或核验，不能据此认定没有招生专业。',
                             'official_url': 'https://a.edu.cn/',
                             'original_urls': 'https://a.edu.cn/zsml/2027.htm'})
            writer.writerow({'unit_id': 'u2', 'unit_code': '10002', 'unit_name': '乙大学',
                             'province': '乙省', 'annual_status': 'confirmed',
                             'program_count': '1', 'fact_count': '0',
                             'catalog_status': 'recorded_verified',
                             'collection_status': 'collected', 'source_count': '1',
                             'missing_reason': '', 'official_url': 'https://b.edu.cn/',
                             'original_urls': 'https://b.edu.cn/zsml/2027.htm'})
        repo = root / 'repo'
        for name in baseline.PROTECTED_FILES:
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(name.encode())
        return data, repo

    def test_audit_writes_replayable_queue_and_bound_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, repo = self.make_fixture(root)
            out = root / 'out'
            baseline.audit(data, out, repo_root=repo, expansion=1, backfill=1)
            queue_text = (out / 'queue.json').read_text('utf-8')
            queue = json.loads(queue_text)
            batch = json.loads((out / 'batch.json').read_text('utf-8'))
            report = json.loads((out / 'baseline.json').read_text('utf-8'))
            summary = json.loads((out / 'queue-summary.json').read_text('utf-8'))
            self.assertEqual(len(queue), 2)
            self.assertEqual(batch['queue_sha256'], baseline.sha256_file(out / 'queue.json'))
            self.assertEqual(batch['selection_rule_version'], baseline.SELECTION_RULE_VERSION)
            self.assertEqual([row['unit_id'] for row in batch['expansion']], ['u1'])
            self.assertEqual([row['unit_id'] for row in batch['backfill']], ['u2'])
            replay = baseline.select_batch(queue, expansion=1, backfill=1,
                                           queue_sha256=batch['queue_sha256'])
            self.assertEqual([row['unit_id'] for row in replay['expansion']],
                             [row['unit_id'] for row in batch['expansion']])
            self.assertEqual([row['unit_id'] for row in replay['backfill']],
                             [row['unit_id'] for row in batch['backfill']])
            self.assertEqual([row['reason'] for row in replay['expansion']],
                             [row['reason'] for row in batch['expansion']])
            self.assertEqual([row['reason'] for row in replay['backfill']],
                             [row['reason'] for row in batch['backfill']])
            self.assertFalse(summary['replayable'])
            with self.assertRaisesRegex(ValueError, 'original_urls'):
                baseline.select_batch(summary['rows'], expansion=1, backfill=0)
            self.assertEqual(report['raw_present']['tuition'], 1)
            self.assertEqual(report['effective_present']['tuition'], 1)
            self.assertEqual(report['verified']['verified_tuition'], 0)
            self.assertEqual(report['queue']['sha256'], batch['queue_sha256'])
            self.assertEqual(report['expansion_pool'],
                             {'candidates': 1, 'trusted': 1, 'untrusted': 0})
            self.assertTrue(queue[0]['entry_trusted'])
            self.assertEqual(report['protected_sha256'], baseline.protected_hashes(repo))
            self.assertIn('inputs', report)
            self.assertIn('admissions_sha256', report['inputs'])
            self.assertTrue(report['queue']['replayable'])
            self.assertEqual(report['annual_rows'], 2)

    def test_main_runs_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, repo = self.make_fixture(root)
            out = root / 'out'
            code = baseline.main(['--data-dir', str(data), '--out', str(out),
                                  '--repo-root', str(repo)])
            self.assertEqual(code, 0)
            self.assertTrue((out / 'baseline.json').exists())


class ToolTests(unittest.TestCase):
    def test_tool_versions_records_success_and_oserror(self):
        missing = str(Path(tempfile.gettempdir()) / 'missing-pdftotext-xyz')
        versions = baseline.tool_versions(shutil.which('git'), missing)
        self.assertIn('version', versions['raw_pdftotext'])
        self.assertEqual(versions['bbox_pdftotext'].get('error'), 'FileNotFoundError')

    def test_tool_versions_without_paths(self):
        versions = baseline.tool_versions('', '')
        self.assertEqual(versions['raw_pdftotext'], {'path': ''})

    def test_sha256_file_matches_hashlib(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.bin'
            path.write_bytes(b'abc')
            self.assertEqual(baseline.sha256_file(path),
                             'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_git_helper_returns_empty_for_missing_working_tree(self):
        self.assertEqual(baseline._git(str(Path(tempfile.gettempdir()) / 'no-such-repo-xyz'),
                                       'rev-parse', 'HEAD'), '')

    def test_count_reader_tolerates_non_numeric_values(self):
        self.assertEqual(baseline._count({'verified_tuition': 'oops'}, 'tuition'), 0)


if __name__ == '__main__':
    unittest.main()
