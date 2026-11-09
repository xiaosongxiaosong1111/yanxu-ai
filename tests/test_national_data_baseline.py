import csv
import hashlib
import html
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import national_data_baseline as baseline


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), 'utf-8')


STATEMENT_URL = 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?zsgsId=3552467248&schCode=10001'


def entry_evidence(url='https://a.edu.cn/'):
    return {'url': STATEMENT_URL,
            'sha256': 'a' * 64,
            'verification': 'replayed_from_saved_chsi_official_site_statement',
            'quote': url, 'linked_url': url, 'relation': 'official_home_link'}


def attach_proof(row, status='verified', reason='saved_original_replayed', **overrides):
    proof = {'status': status, 'reason': reason, 'unit_id': row.get('unit_id'),
             'year': row.get('year'), 'official_url': row.get('official_url'),
             'evidence_url': (row.get('official_url_evidence') or {}).get('url', ''),
             'evidence_sha256': (row.get('official_url_evidence') or {}).get('sha256', ''),
             'document_id': 'national-u1-2027-aaaabbbbccccdddd-aaaaaaaaaaaa',
             'document_sha256': 'a' * 64, 'document_path':
             'snapshots/national/2027/u1/aaaabbbbccccdddd-aaaaaaaaaaaaaaaa.html',
             'file_sha256': 'a' * 64, 'quote_match': 'literal',
             'role_category': 'home', 'role_evidence': '研究生院网站主页'}
    proof.update(overrides)
    row['entry_verification'] = proof
    proof['proof_sha256'] = baseline._entry_fingerprint(row)
    return row


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
    def test_entry_without_a_replayed_proof_is_untrusted(self):
        row = {'unit_id': 'u1', 'year': 2027, 'official_url': 'https://a.edu.cn/',
               'gaps': [], 'official_url_evidence': entry_evidence()}
        self.assertFalse(baseline.entry_trusted(row))

    def test_metadata_claim_is_not_enough_even_when_hosts_match(self):
        row = attach_proof({'unit_id': 'u1', 'year': 2027,
                            'official_url': 'https://a.edu.cn/', 'gaps': [],
                            'official_url_evidence': entry_evidence()},
                           status='rejected', reason='no_exact_annual_document')
        self.assertFalse(baseline.entry_trusted(row))

    def test_entry_rejects_unapproved_or_missing_evidence(self):
        base = {'unit_id': 'u1', 'year': 2027, 'official_url': 'https://a.edu.cn/',
                'gaps': []}
        self.assertFalse(baseline.entry_trusted(base))
        self.assertFalse(baseline.entry_trusted(
            {**attach_proof({**base, 'official_url_evidence': entry_evidence()}),
             'gaps': ['missing_trusted_entry']}))
        self.assertFalse(baseline.entry_trusted(
            {**base, 'missing_reason': '尚未核准该单位的官方招生网站入口。',
             'official_url_evidence': entry_evidence()}))
        self.assertFalse(baseline.entry_trusted({**base, 'official_url': ''}))
        self.assertFalse(baseline.entry_trusted(
            {**base, 'official_url_evidence': {'verification': '', 'quote': 'https://a.edu.cn/'}}))

    def test_entry_handles_malformed_evidence(self):
        row = attach_proof({'unit_id': 'u1', 'year': 2027,
                            'official_url': 'https://a.edu.cn/', 'gaps': [],
                            'official_url_evidence': {'verification': 'x',
                                                      'quote': 'not a url'}})
        self.assertFalse(baseline.entry_trusted(row))
        self.assertEqual(baseline._host('http://[::1'), '')


class EntryProofTests(unittest.TestCase):
    """R2: the entry gate must replay a real saved original, not metadata."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.saved_bytes = ('<html><p>研究生院网站主页：'
                            '<a href="http://a.edu.cn/">http://a.edu.cn/</a></p></html>'
                            ).encode('utf-8')
        self.sha = hashlib.sha256(self.saved_bytes).hexdigest()
        self.doc_id = f'national-u1-2027-aaaabbbbccccdddd-{self.sha[:12]}'
        folder = self.data / 'snapshots' / 'national' / '2027' / 'u1'
        folder.mkdir(parents=True)
        (folder / f'aaaabbbbccccdddd-{self.sha[:16]}.html').write_bytes(self.saved_bytes)
        self.record = {
            'unit_id': 'u1', 'unit_code': '10001', 'year': 2027,
            'annual_status': 'confirmed',
            'evidence': [{'document_id': self.doc_id, 'url': STATEMENT_URL,
                          'sha256': self.sha, 'kind': 'annual_master_charter',
                          'quote': '甲大学2027年硕士研究生招生章程'}]}
        self.unit = {
            'id': 'u1', 'official_url': 'https://a.edu.cn/',
            'official_url_evidence': {
                'url': STATEMENT_URL, 'sha256': self.sha, 'quote': 'http://a.edu.cn/',
                'link_label': '招生网站', 'relation': 'official_home_link',
                'linked_url': 'http://a.edu.cn/',
                'verification': 'replayed_from_saved_chsi_official_site_statement'}}

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self):
        return baseline.verify_entry(self.unit, self.record, self.data)

    def rewritten(self, **changes):
        unit = {**self.unit, 'official_url_evidence': dict(self.unit['official_url_evidence'])}
        unit['official_url_evidence'].update(changes)
        return baseline.verify_entry(unit, self.record, self.data)

    def test_correct_saved_original_replays_to_verified(self):
        proof = self.verify()
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['reason'], 'saved_original_replayed')
        self.assertEqual(proof['file_sha256'], self.sha)
        self.assertEqual(proof['quote_match'], 'literal')
        self.assertIn('snapshots/national/2027/u1/', proof['document_path'])

    def test_zeroed_evidence_sha_is_rejected(self):
        proof = self.rewritten(sha256='0' * 64)
        self.assertEqual(proof['status'], 'rejected')
        self.assertEqual(proof['reason'], 'no_exact_annual_document')

    def test_rejected_or_failed_verification_status_is_rejected(self):
        for value in ('rejected', 'failed'):
            proof = self.rewritten(verification=value)
            self.assertEqual(proof['status'], 'rejected')
            self.assertEqual(proof['reason'], 'unsupported_verification')

    def test_removed_sha_or_original_url_is_rejected(self):
        unit = {**self.unit, 'official_url_evidence': dict(self.unit['official_url_evidence'])}
        del unit['official_url_evidence']['sha256']
        self.assertEqual(baseline.verify_entry(unit, self.record, self.data)['reason'],
                         'invalid_evidence_sha256')
        unit = {**self.unit, 'official_url_evidence': dict(self.unit['official_url_evidence'])}
        del unit['official_url_evidence']['url']
        self.assertEqual(baseline.verify_entry(unit, self.record, self.data)['reason'],
                         'no_exact_annual_document')

    def test_replaced_original_bytes_are_rejected(self):
        folder = self.data / 'snapshots' / 'national' / '2027' / 'u1'
        (folder / f'aaaabbbbccccdddd-{self.sha[:16]}.html').write_bytes(b'<html>changed</html>')
        self.assertEqual(self.verify()['reason'], 'saved_original_sha_mismatch')

    def test_wrong_unit_identity_is_rejected(self):
        self.record['unit_code'] = '19999'
        self.assertEqual(self.verify()['reason'], 'unit_identity_mismatch')

    def test_source_host_only_evidence_is_rejected(self):
        proof = self.rewritten(quote='https://yz.chsi.com.cn/')
        self.assertEqual(proof['reason'], 'quote_host_mismatch')

    def test_quote_absent_from_the_original_is_rejected(self):
        proof = self.rewritten(quote='http://b.edu.cn/')
        self.assertEqual(proof['reason'], 'quote_host_mismatch')
        proof = self.rewritten(quote='http://a.edu.cn/other/',
                               linked_url='http://a.edu.cn/other/')
        self.assertEqual(proof['reason'], 'quote_not_in_original')

    def test_unsupported_relation_is_rejected(self):
        proof = self.rewritten(relation='generic_contact_url')
        self.assertEqual(proof['reason'], 'unsupported_relation')

    def test_missing_saved_original_is_rejected(self):
        shutil.rmtree(self.data / 'snapshots')
        self.assertEqual(self.verify()['reason'], 'saved_original_missing')

    def test_ambiguous_saved_original_is_rejected(self):
        folder = self.data / 'snapshots' / 'national' / '2027' / 'u1'
        (folder / f'ffffffffffffffff-{self.sha[:16]}.html').write_bytes(self.saved_bytes)
        self.assertEqual(self.verify()['reason'], 'ambiguous_saved_original')

    def test_missing_entry_payloads_are_rejected(self):
        proof = baseline.verify_entry({'official_url': 'https://a.edu.cn/'},
                                      self.record, self.data)
        self.assertEqual(proof['reason'], 'no_entry_evidence')
        unit = {**self.unit, 'official_url': ''}
        self.assertEqual(baseline.verify_entry(unit, self.record, self.data)['reason'],
                         'missing_official_url')

    def test_linked_url_on_another_host_is_rejected(self):
        self.assertEqual(self.rewritten(linked_url='https://b.edu.cn/')['reason'],
                         'linked_host_mismatch')

    def test_unsupported_annual_document_kind_is_rejected(self):
        self.record['evidence'][0]['kind'] = 'current_master_catalog_entry'
        self.assertEqual(self.verify()['reason'], 'unsupported_document_kind')

    def test_document_index_and_file_binding_reject_unbound_names(self):
        self.assertEqual(
            baseline._document_matches({'evidence': ['skip', {'url': 'x', 'sha256': '0'}]},
                                       {'url': 'x', 'sha256': '0'})['url'], 'x')
        self.assertEqual(baseline._saved_original(self.data, 'u1', 2027, self.sha, 'x')[1],
                         'document_id_unbound')
        self.assertEqual(baseline._saved_original(
            self.data, 'u1', 2027, self.sha,
            f'national-u1-2027-ffffffffffffffff-{self.sha[:12]}')[1],
            'saved_original_unbound')
        self.assertEqual(baseline._saved_original(
            self.data, 'missing', 2027, self.sha, self.doc_id)[1],
            'saved_original_missing')
        folder = self.data / 'snapshots' / 'national' / '2027' / 'u1'
        self.assertEqual(baseline._saved_original(
            self.data, 'u1', 2027, 'f' * 64, self.doc_id)[1],
            'saved_original_missing')
        self.assertEqual(baseline._quote_match(
            folder / f'aaaabbbbccccdddd-{self.sha[:16]}.html', ''), '')

    def test_identity_can_come_from_structured_fields_or_schid_url(self):
        record = {'unit_id': 'u1', 'unit_code': '10001'}
        self.assertTrue(baseline._identity_matches(
            record, {'structured_fields': {'dwdm': '10001'}}, 'https://x/'))
        self.assertTrue(baseline._identity_matches(
            record, {'structured_fields': {'schId': 'u1'}}, 'https://x/'))
        self.assertTrue(baseline._identity_matches(
            {'unit_id': 'u1', 'unit_code': ''}, {}, 'https://x/schId-u1.html'))
        self.assertFalse(baseline._identity_matches(
            record, {}, 'http://[::1'))

    def test_verify_entries_skips_non_string_unit_ids(self):
        proofs = baseline.verify_entries(
            {'units': [self.unit]},
            {'units': [{'unit_id': 123, 'year': 2027, 'annual_status': 'confirmed'},
                       self.record]}, self.data)
        self.assertEqual(list(proofs), ['u1'])

    def test_entry_trusted_rejects_each_tampered_link(self):
        proofs = baseline.verify_entries({'units': [self.unit]},
                                         {'units': [self.record]}, self.data)
        row = baseline.build_queue([], {}, {}, [{
            'unit_id': 'u1', 'year': 2027, 'annual_status': 'confirmed',
            'official_url': 'https://a.edu.cn/',
            'official_url_evidence': self.unit['official_url_evidence']}],
            entry_proofs=proofs)[0]
        self.assertTrue(baseline.entry_trusted(row))
        cases = [
            {'official_url_evidence': {**row['official_url_evidence'],
                                       'verification': 'unknown'}},
            {'official_url_evidence': {**row['official_url_evidence'],
                                       'relation': 'unknown'}},
            {'entry_verification': {**row['entry_verification'], 'unit_id': 'u2'}},
            {'entry_verification': {**row['entry_verification'], 'year': 2028}},
            {'entry_verification': {**row['entry_verification'],
                                    'official_url': 'https://b.edu.cn/'}},
            {'official_url_evidence': {**row['official_url_evidence'], 'quote': ''}},
            {'entry_verification': {**row['entry_verification'], 'proof_sha256': 'x'}},
        ]
        for change in cases:
            self.assertFalse(baseline.entry_trusted({**row, **change}))

    def test_build_queue_fills_a_missing_proof_year(self):
        row = baseline.build_queue([], {}, {}, [{
            'unit_id': 'u1', 'year': 2027, 'annual_status': 'confirmed',
            'official_url': 'https://a.edu.cn/',
            'official_url_evidence': self.unit['official_url_evidence']}],
            entry_proofs={'u1': {'status': 'rejected', 'reason': 'x',
                                 'unit_id': 'u1'}})[0]
        self.assertEqual(row['entry_verification']['year'], 2027)
        self.assertFalse(row['entry_trusted'])

    def test_host_helpers_cover_empty_and_scheme_less_values(self):
        self.assertEqual(baseline._host(''), '')
        self.assertEqual(baseline._host('a.edu.cn/'), 'a.edu.cn')

    def test_build_queue_persists_and_binds_the_proof(self):
        proofs = baseline.verify_entries({'units': [self.unit]},
                                         {'units': [self.record]}, self.data)
        row = baseline.build_queue([], {}, {}, [{
            'unit_id': 'u1', 'unit_code': '10001', 'unit_name': '甲大学',
            'province': '甲省', 'year': 2027, 'annual_status': 'confirmed',
            'annual_evidence_count': 1, 'official_url': 'https://a.edu.cn/',
            'official_url_evidence': self.unit['official_url_evidence']}],
            entry_proofs=proofs)[0]
        self.assertEqual(row['entry_verification']['status'], 'verified')
        self.assertTrue(row['entry_trusted'])
        self.assertNotIn('missing_trusted_entry', row['gaps'])
        tampered = {**row, 'official_url_evidence': {
            **row['official_url_evidence'], 'sha256': '0' * 64}}
        self.assertFalse(baseline.entry_trusted(tampered))
        forged = {**row, 'entry_verification': {
            **row['entry_verification'], 'proof_sha256': 'b' * 64}}
        self.assertFalse(baseline.entry_trusted(forged))


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


class EntryRoleReplayTests(unittest.TestCase):
    """R2: the saved original must visibly declare this unit's site role."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, article='', *, unit_name='甲大学', unit_id='u1',
             official_url='https://a.edu.cn/', full_html=None, hidden=False,
             quote=None, linked=None, known_hosts=None, known_names=None,
             relation='official_home_link', return_context=False):
        if full_html is None:
            if hidden:
                payload = ('<html><body><div class="cnt_body_wrapper"></div>'
                           f'<input type="hidden" id="hid_content" value="'
                           f'{html.escape(article, quote=True)}"></body></html>')
            else:
                payload = f'<html><body>{article}</body></html>'
        else:
            payload = full_html
        saved = payload.encode('utf-8')
        sha = hashlib.sha256(saved).hexdigest()
        document_id = f'national-{unit_id}-2027-aaaabbbbccccdddd-{sha[:12]}'
        folder = self.data / 'snapshots' / 'national' / '2027' / unit_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'aaaabbbbccccdddd-{sha[:16]}.html').write_bytes(saved)
        record = {
            'unit_id': unit_id, 'unit_code': '10001', 'year': 2027,
            'annual_status': 'confirmed',
            'evidence': [{'document_id': document_id, 'url': STATEMENT_URL,
                          'sha256': sha, 'kind': 'annual_master_charter',
                          'quote': '甲大学2027年硕士研究生招生章程'}]}
        unit = {
            'id': unit_id, 'name': unit_name, 'official_url': official_url,
            'official_url_evidence': {
                'url': STATEMENT_URL, 'sha256': sha,
                'quote': quote if quote is not None else official_url,
                'linked_url': linked if linked is not None else official_url,
                'relation': relation,
                'verification': 'replayed_from_saved_chsi_official_site_statement'}}
        proof = baseline.verify_entry(unit, record, self.data, known_hosts,
                                     known_names)
        if return_context:
            return proof, unit, record
        return proof

    def test_chsi_counterexample_is_not_trusted_or_selected(self):
        proof, unit, record = self.make(
            '我校学历（学籍）信息可登录“中国高等教育学生信息网”'
            '（网址：https://www.chsi.com.cn）查询。'
            '学校网址：http://www.sdau.edu.cn/',
            unit_name='山东农业大学', official_url='https://www.chsi.com.cn/',
            hidden=True, return_context=True)
        self.assertEqual(proof['reason'], 'platform_not_official_site')
        row = {'unit_id': 'u1', 'year': 2027, 'annual_status': 'confirmed',
               'official_url': unit['official_url'],
               'official_url_evidence': unit['official_url_evidence'],
               'catalog_status': 'not_collected', 'collection_status': 'collected',
               'missing_reason': '已保存官方原文，专业目录尚待解析或核验，不能据此认定没有招生专业。',
               'original_urls': 'https://www.chsi.com.cn/zsml/2027.htm',
               'gaps': ['missing_catalog', 'parser_pending']}
        queue = baseline.build_queue([], {}, {}, [row],
                                     entry_proofs={'u1': proof})
        self.assertFalse(queue[0]['entry_trusted'])
        picks = baseline.select_batch(queue, expansion=1, backfill=1)
        self.assertEqual(picks['expansion'], [])
        self.assertEqual(picks['backfill'], [])

    def test_hidden_chsi_article_replays_a_visible_school_role(self):
        proof = self.make(
            '学校网址：<a href="http://www.sdau.edu.cn/">http://www.sdau.edu.cn/</a>'
            '；研究生处网址：http://yjsc.sdau.edu.cn/',
            unit_name='山东农业大学', official_url='http://www.sdau.edu.cn/',
            hidden=True)
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['role_category'], 'home')
        self.assertIn('网址', proof['role_evidence'])
        self.assertEqual(proof['quote_match'], 'literal')

    def test_chsi_student_status_url_is_not_a_school_site_role(self):
        proof = self.make(
            '我校学历（学籍）信息可登录“中国高等教育学生信息网”'
            '（网址：https://www.chsi.com.cn）查询。'
            '学校网址：http://www.sdau.edu.cn/',
            unit_name='山东农业大学', official_url='https://www.chsi.com.cn/',
            hidden=True)
        self.assertEqual(proof['status'], 'rejected')
        self.assertEqual(proof['reason'], 'platform_not_official_site')
        self.assertEqual(proof['role_category'], '')

    def test_admissions_page_platform_url_is_not_its_own_site(self):
        proof = self.make(
            '我校研究生招生信息均由我校研究生学院官方网站'
            '（http://yjs.dep.dlpu.edu.cn/）统一公布。'
            '考生可登录中国高等教育学生信息网'
            '（网址：https://www.chsi.com.cn）查询。',
            unit_name='大连工业大学', official_url='https://www.chsi.com.cn/',
            hidden=True)
        self.assertEqual(proof['status'], 'rejected')
        self.assertEqual(proof['reason'], 'platform_not_official_site')

    def test_url_only_in_a_comment_or_script_is_not_visible_evidence(self):
        proof = self.make(full_html=(
            '<html><body><p>请查阅招生章程。</p>'
            '<!-- 研究生院网站主页：https://a.edu.cn/ --></body></html>'))
        self.assertEqual(proof['reason'], 'quote_not_in_original')
        proof = self.make(full_html=(
            '<html><body><p>请查阅招生章程。</p>'
            '<script>var u = "https://a.edu.cn/";</script></body></html>'))
        self.assertEqual(proof['reason'], 'quote_not_in_original')

    def test_an_unrelated_link_target_is_not_a_site_role(self):
        proof = self.make('请点击<a href="https://a.edu.cn/">这里</a>查看。')
        self.assertEqual(proof['reason'], 'role_declaration_missing')

    def test_another_units_site_declaration_is_rejected(self):
        proof = self.make(
            '乙大学官方网站：<a href="https://a.edu.cn/">https://a.edu.cn/</a>',
            unit_name='甲大学', known_names={'甲大学', '乙大学'})
        self.assertEqual(proof['reason'], 'foreign_unit_reference')

    def test_unregistered_foreign_site_label_is_rejected(self):
        proof = self.make(
            '甲大学2027年硕士研究生招生章程。'
            '乙大学官方网站：<a href="https://b.edu.cn/">https://b.edu.cn/</a>。',
            unit_name='甲大学', official_url='https://b.edu.cn/')
        self.assertEqual(proof['reason'], 'foreign_unit_reference')

    def test_labeled_own_site_role_is_admitted_and_trusted(self):
        proof, unit, record = self.make(
            '山西大同大学官方网站：'
            '<a href="https://a.edu.cn/">https://a.edu.cn/</a>。',
            unit_name='山西大同大学', official_url='https://a.edu.cn/',
            return_context=True)
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['role_category'], 'labeled')
        row = {'unit_id': 'u1', 'year': 2027, 'annual_status': 'confirmed',
               'official_url': unit['official_url'],
               'official_url_evidence': unit['official_url_evidence'],
               'catalog_status': 'not_collected', 'collection_status': 'collected',
               'missing_reason': '已保存官方原文，专业目录尚待解析或核验，不能据此认定没有招生专业。',
               'original_urls': 'https://a.edu.cn/zsml/2027.htm',
               'gaps': ['missing_catalog', 'parser_pending']}
        queue = baseline.build_queue([row], {}, {}, [row],
                                     entry_proofs={'u1': proof})
        self.assertTrue(queue[0]['entry_trusted'])
        picks = baseline.select_batch(queue, expansion=1, backfill=1)
        self.assertEqual([item['unit_id'] for item in picks['expansion']], ['u1'])

    def test_generic_admissions_label_is_owned_by_the_article(self):
        proof = self.make(
            '东北林业大学2027年硕士研究生招生章程。'
            '研究生招生信息网主页：https://a.edu.cn/',
            unit_name='东北林业大学', official_url='https://a.edu.cn/')
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['role_category'], 'labeled')

    def test_anchor_target_must_match_the_declared_url(self):
        proof = self.make(
            '我校官方网站：<a href="https://www.chsi.com.cn/">https://a.edu.cn/</a>')
        self.assertEqual(proof['reason'], 'link_target_mismatch')

    def test_plain_visible_url_with_a_role_can_pass(self):
        proof = self.make('我校官方网站：https://a.edu.cn/')
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['quote_match'], 'literal')

    def test_linked_url_is_compared_ignoring_scheme_and_trailing_slash(self):
        proof = self.make('我校官方网站：https://a.edu.cn/',
                          linked='http://a.edu.cn')
        self.assertEqual(proof['status'], 'verified')

    def test_linked_target_with_a_different_path_is_rejected(self):
        proof = self.make('我校官方网站：https://a.edu.cn/',
                          linked='https://a.edu.cn/zsml/')
        self.assertEqual(proof['reason'], 'linked_target_mismatch')

    def test_nearby_platform_mention_does_not_hide_a_school_site_role(self):
        proof = self.make(
            '请登录中国研究生招生信息网了解政策，或访问我校官方网站：'
            '<a href="https://a.edu.cn/">https://a.edu.cn/</a>。')
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['role_category'], 'home')

    def test_earlier_catalog_words_do_not_override_the_site_label(self):
        proof = self.make(
            '招生专业目录等相关内容可登录我校网站'
            '（https://a.edu.cn/）查询。')
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['role_category'], 'home')

    def test_yanzhao_url_label_is_not_the_platform(self):
        proof = self.make('我校研招网址：https://a.edu.cn/')
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['role_category'], 'admissions')

    def test_full_article_can_own_an_abbreviated_label(self):
        proof = self.make(
            '甲大学2027年硕士研究生招生章程。' + '其他说明。' * 120
            + '考生可通过招生信息网（https://a.edu.cn/）查询。')
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['role_category'], 'admissions')

    def test_own_admissions_publication_accepts_a_catalog_title(self):
        proof = self.make(
            '<a href="https://a.edu.cn/zsml.htm">'
            '甲大学2027年硕士研究生招生专业目录</a>',
            official_url='https://a.edu.cn/zsml.htm',
            relation='own_admissions_publication')
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['role_category'], 'catalog')
        self.assertEqual(proof['quote_match'], 'link_target')

    def test_home_relation_still_rejects_a_catalog_title(self):
        proof = self.make(
            '<a href="https://a.edu.cn/zsml.htm">'
            '甲大学2027年硕士研究生招生专业目录</a>',
            official_url='https://a.edu.cn/zsml.htm',
            relation='official_home_link')
        self.assertEqual(proof['reason'], 'relation_role_mismatch')

    def test_percent_encoded_punctuation_is_normalised(self):
        proof = self.make(
            '我院官网地址：http://a.edu.cn/。',
            official_url='http://a.edu.cn/%E3%80%82',
            quote='http://a.edu.cn/%E3%80%82',
            linked='http://a.edu.cn/%E3%80%82',
            relation='declared_official_site')
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['quote_match'], 'literal')

    def test_backface_visibility_style_does_not_hide_article_text(self):
        proof = self.make(full_html=(
            '<html><body><p style="backface-visibility: hidden">'
            '我校官方网站：https://a.edu.cn/</p></body></html>'))
        self.assertEqual(proof['status'], 'verified')

    def test_linked_target_with_a_different_fragment_is_rejected(self):
        proof = self.make(
            '我校官方网站：https://a.edu.cn/',
            quote='https://a.edu.cn/#/admission/article-3506',
            linked='https://a.edu.cn/')
        self.assertEqual(proof['reason'], 'linked_target_mismatch')

    def test_registry_host_owned_by_another_unit_is_rejected(self):
        proof = self.make('我校官方网站：https://a.edu.cn/',
                          known_hosts={'a.edu.cn': {'u2'}})
        self.assertEqual(proof['reason'], 'other_unit_host')


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
    def expansion_row(self, unit_id='1', url='https://a.edu.cn/', gaps=None, evidence=True,
                      proof_status='verified'):
        row = {'unit_id': unit_id, 'unit_name': '甲', 'official_url': url, 'year': 2027,
               'catalog_status': 'not_collected', 'collection_status': 'collected',
               'missing_reason': '已保存官方原文，专业目录尚待解析或核验，不能据此认定没有招生专业。',
               'original_urls': 'https://a.edu.cn/zsml/2027.htm', 'program_count': 0,
               'gaps': ['missing_catalog', 'parser_pending'] if gaps is None else gaps}
        if evidence:
            row['official_url_evidence'] = entry_evidence(url)
        return attach_proof(row, status=proof_status,
                            reason=('saved_original_replayed' if proof_status == 'verified'
                                    else 'no_exact_annual_document'))

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

    def backfill_row(self, unit_id, program_count, verified_tuition=0,
                     verified_duration=0, gaps=None):
        row = {'unit_id': unit_id, 'year': 2027,
               'official_url': 'https://a.edu.cn/',
               'official_url_evidence': entry_evidence(),
               'program_count': program_count,
               'verified_tuition': verified_tuition,
               'verified_duration': verified_duration,
               'verified_full_qualification': 0,
               'gaps': [] if gaps is None else gaps}
        return attach_proof(row)

    def test_backfill_pool_prefers_units_with_programs_and_no_fields(self):
        rows = [self.backfill_row('9', 40), self.backfill_row('8', 12),
                self.backfill_row('7', 80, verified_tuition=3)]
        picks = baseline.select_batch(rows, expansion=0, backfill=2)
        self.assertEqual([row['unit_id'] for row in picks['backfill']], ['9', '8'])

    def test_backfill_pool_retains_partially_covered_units_by_remaining_count(self):
        rows = [self.backfill_row('5', 100, verified_tuition=90, verified_duration=95),
                self.backfill_row('4', 20),
                self.backfill_row('3', 100, verified_tuition=100, verified_duration=100)]
        picks = baseline.select_batch(rows, expansion=0, backfill=2)
        self.assertEqual([row['unit_id'] for row in picks['backfill']], ['4', '5'])

    def test_backfill_pool_prefers_units_whose_originals_are_already_collected(self):
        rows = [self.backfill_row('5', 200,
                                  gaps=['missing_tuition', 'original_pending']),
                self.backfill_row('4', 30, gaps=['missing_tuition'])]
        picks = baseline.select_batch(rows, expansion=0, backfill=2)
        self.assertEqual([row['unit_id'] for row in picks['backfill']], ['4', '5'])

    def test_backfill_pool_requires_a_trusted_entry(self):
        good = self.backfill_row('3', 10)
        bad = self.backfill_row('2', 50)
        bad['entry_verification'].update(status='rejected',
                                         reason='no_exact_annual_document')
        bad['entry_verification']['proof_sha256'] = baseline._entry_fingerprint(bad)
        self.assertFalse(baseline.entry_trusted(bad))
        picks = baseline.select_batch([bad, good], expansion=0, backfill=1)
        self.assertEqual([row['unit_id'] for row in picks['backfill']], ['3'])

    def test_expansion_pool_rejects_a_proof_without_a_replayed_role(self):
        row = self.expansion_row()
        row['entry_verification']['role_category'] = ''
        row['entry_verification']['proof_sha256'] = baseline._entry_fingerprint(row)
        self.assertFalse(baseline.entry_trusted(row))
        picks = baseline.select_batch([row], expansion=1, backfill=0)
        self.assertEqual(picks['expansion'], [])

    def test_backfill_pool_rejects_a_proof_without_role_evidence(self):
        row = self.backfill_row('3', 10)
        row['entry_verification']['role_evidence'] = '   '
        row['entry_verification']['proof_sha256'] = baseline._entry_fingerprint(row)
        self.assertFalse(baseline.entry_trusted(row))
        picks = baseline.select_batch([row], expansion=0, backfill=1)
        self.assertEqual(picks['backfill'], [])

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
        saved = ('<html><p>研究生院网站主页：'
                 '<a href="https://a.edu.cn/">https://a.edu.cn/</a></p></html>').encode('utf-8')
        saved_sha = hashlib.sha256(saved).hexdigest()
        document_id = f'national-u1-2027-aaaabbbbccccdddd-{saved_sha[:12]}'
        snapshot = data / 'snapshots' / 'national' / '2027' / 'u1'
        snapshot.mkdir(parents=True)
        (snapshot / f'aaaabbbbccccdddd-{saved_sha[:16]}.html').write_bytes(saved)
        saved2 = ('<html><p>研究生院网站主页：'
                  '<a href="https://b.edu.cn/">https://b.edu.cn/</a></p></html>').encode('utf-8')
        saved2_sha = hashlib.sha256(saved2).hexdigest()
        document2_id = f'national-u2-2027-eeeeffff00001111-{saved2_sha[:12]}'
        snapshot2 = data / 'snapshots' / 'national' / '2027' / 'u2'
        snapshot2.mkdir(parents=True)
        (snapshot2 / f'eeeeffff00001111-{saved2_sha[:16]}.html').write_bytes(saved2)
        u1_evidence = {'url': STATEMENT_URL, 'sha256': saved_sha,
                       'quote': 'https://a.edu.cn/', 'link_label': '研究生院网站主页',
                       'relation': 'official_home_link', 'linked_url': 'https://a.edu.cn/',
                       'verification': 'replayed_from_saved_chsi_official_site_statement'}
        u2_statement = ('https://yz.chsi.com.cn/zsgs/zsjz/detail.action?'
                        'zsgsId=3552467249&schCode=10002')
        u2_evidence = {'url': u2_statement, 'sha256': saved2_sha,
                       'quote': 'https://b.edu.cn/', 'link_label': '研究生院网站主页',
                       'relation': 'official_home_link', 'linked_url': 'https://b.edu.cn/',
                       'verification': 'replayed_from_saved_chsi_official_site_statement'}
        write_json(data / 'units.json', {'units': [
            {'id': 'u1', 'name': '甲大学', 'unit_code': '10001', 'province': '甲省',
             'official_url': 'https://a.edu.cn/', 'official_url_evidence': u1_evidence},
            {'id': 'u2', 'name': '乙大学', 'unit_code': '10002', 'province': '乙省',
             'official_url': 'https://b.edu.cn/',
             'official_url_evidence': u2_evidence}]})
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
                 'evidence': [{'document_id': document_id, 'url': STATEMENT_URL,
                               'sha256': saved_sha, 'kind': 'annual_master_charter',
                               'quote': '甲大学2027年硕士研究生招生章程'}]},
                {'unit_id': 'u2', 'unit_code': '10002', 'unit_name': '乙大学',
                 'province': '乙省', 'year': 2027, 'annual_status': 'confirmed',
                 'evidence': [{'document_id': document2_id, 'url': u2_statement,
                               'sha256': saved2_sha, 'kind': 'annual_master_charter',
                               'structured_fields': {'schId': 'u2'},
                               'quote': '乙大学2027年硕士研究生招生章程'}]},
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
            self.assertEqual(report['entry_proof']['status'], {'verified': 2})
            self.assertEqual(queue[0]['entry_verification']['status'], 'verified')
            self.assertEqual(len(queue[0]['entry_verification']['proof_sha256']), 64)
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

    def test_write_json_uses_lf_bytes_for_canonical_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.json'
            baseline._write_json(path, {'a': 1})
            data = path.read_bytes()
            self.assertEqual(data, b'{\n  "a": 1\n}')

    def test_git_helper_returns_empty_for_missing_working_tree(self):
        self.assertEqual(baseline._git(str(Path(tempfile.gettempdir()) / 'no-such-repo-xyz'),
                                       'rev-parse', 'HEAD'), '')

    def test_count_reader_tolerates_non_numeric_values(self):
        self.assertEqual(baseline._count({'verified_tuition': 'oops'}, 'tuition'), 0)


if __name__ == '__main__':
    unittest.main()
