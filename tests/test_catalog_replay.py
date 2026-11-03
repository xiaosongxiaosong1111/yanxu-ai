from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from catalog_replay import refresh_saved_catalog
from collectors.catalog_html import parse_catalog_html
from service_verification import SourceVerifier


class CatalogReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.unit = {'id': 'u', 'name': '测试大学', 'official_url': 'https://example.edu.cn'}
        self.registry = {'units': [self.unit]}
        self.html = '<h1>测试大学2027年硕士研究生招生专业目录</h1><table><tr><th>学院</th><th>专业</th><th>学位类型</th><th>研究方向</th><th>学习方式</th><th>初试科目</th></tr><tr><td>001计算机学院</td><td>081200计算机科学与技术</td><td>学术学位</td><td>01人工智能02软件工程</td><td>全日制</td><td>①101统考思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr></table>'
        self.path = self.root / 'catalog.html'
        self.path.write_text(self.html, 'utf-8')
        self.doc = {'id': 'd', 'unit_id': 'u', 'year': 2027, 'kind': 'catalog', 'status': 'parsed',
            'url': 'https://example.edu.cn/catalog', 'local_path': self.path.name,
            'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest(), 'catalog_parser': 'html_table_v1'}
        parsed = parse_catalog_html(self.html, self.unit, 2027, self.doc)
        self.assertEqual(parsed['issues'], [])
        self.admissions = {'metadata': {'catalog_complete': False, 'unit_catalog_status': [
            {'unit_id': 'u', 'year': 2027, 'status': 'partial', 'verified_records': 0}]},
            'programs': parsed['programs'], 'documents': [self.doc], 'score_lines': [{'id': 'historic'}]}

    def test_current_citations_are_refreshed_without_changing_core_or_independent_fields(self):
        old = deepcopy(self.admissions)
        for row in old['programs']:
            row['evidence'] = row['evidence'][:-1]
            row['exam_evidence'] = [{**row['exam_evidence'][0], 'quote': '201英语（一）'}]
            row['tuition'] = {'amount': 8000, 'period': 'year', 'evidence': {'document_id': 'independent-fee'}}
            row['duration_years'] = 3
            row['duration_evidence'] = {'document_id': 'independent-duration'}
        before = deepcopy(old)
        self.assertFalse(SourceVerifier(self.root).program(old['programs'][0], {'d': self.doc}, self.unit))
        updated, report = refresh_saved_catalog(self.registry, old, self.root)
        self.assertEqual(old, before)
        self.assertEqual(report['refreshed_count'], 2)
        self.assertEqual(report['needs_review_count'], 0)
        self.assertEqual(updated['score_lines'], old['score_lines'])
        for index, row in enumerate(updated['programs']):
            self.assertTrue(SourceVerifier(self.root).program(row, {'d': self.doc}, self.unit))
            self.assertEqual(row['evidence'], self.admissions['programs'][index]['evidence'])
            self.assertEqual(row['tuition'], old['programs'][index]['tuition'])
            self.assertEqual(row['duration_evidence'], old['programs'][index]['duration_evidence'])

    def test_only_supported_exact_national_alias_scopes_can_change(self):
        old = deepcopy(self.admissions)
        old['programs'][0]['subjects'][0]['scope'] = 'u'
        updated, report = refresh_saved_catalog(self.registry, old, self.root)
        self.assertEqual(updated['programs'][0]['subjects'][0]['scope'], 'national')
        self.assertEqual(updated['programs'][0]['subjects'][0]['name'], '统考思想政治理论')
        self.assertEqual(report['alias_changed_count'], 1)
        old['programs'][0]['subjects'][-1]['scope'] = 'u'
        rejected, report = refresh_saved_catalog(self.registry, old, self.root)
        self.assertEqual(report['needs_review_count'], 1)
        self.assertEqual(rejected['programs'][0]['subjects'][-1]['scope'], 'u')
        self.assertTrue(rejected['programs'][0]['stale'])

    def test_contact_suffix_is_removed_only_when_all_remaining_original_facts_match(self):
        old=deepcopy(self.admissions)
        old['programs'][0]['department']+='联系人：董老师'
        updated,report=refresh_saved_catalog(self.registry,old,self.root)
        self.assertEqual(report['needs_review_count'],0)
        self.assertEqual(updated['programs'][0]['department'],'计算机学院')
        other=deepcopy(old);other['programs'][0]['department']='历史学院联系人：董老师'
        rejected,report=refresh_saved_catalog(self.registry,other,self.root)
        self.assertEqual(report['needs_review_count'],1)
        self.assertTrue(rejected['programs'][0]['stale'])

    def test_source_safety_flags_are_refreshed_without_correcting_the_original_subject_or_business_fields(self):
        for original, actual in [('301数学（一）', '301数学（二）'),
                                 ('408计算机学科专业基础', '498法侓硕士综合')]:
            with self.subTest(subject=actual):
                html = self.html.replace(original, actual)
                self.path.write_text(html, 'utf-8')
                document = {**self.doc, 'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}
                parsed = parse_catalog_html(html, self.unit, 2027, document)
                old = {**deepcopy(self.admissions), 'documents': [document],
                    'programs': [{key: deepcopy(value) for key, value in row.items()
                        if key not in {'exam_matching_supported', 'subject_identity_issues'}} for row in parsed['programs']]}
                old['programs'][0].update({'tuition': {'amount': 8000, 'period': 'year'},
                    'duration_years': 3, 'stale': True, 'stale_reason': '独立业务待核', 'conditions': [{'business': 'preserve'}]})
                before = deepcopy(old)
                updated, report = refresh_saved_catalog(self.registry, old, self.root)
                self.assertEqual(report['needs_review_count'], 0)
                self.assertEqual(old, before)
                self.assertTrue(all(row['exam_matching_supported'] is False for row in updated['programs']))
                self.assertEqual(updated['programs'][0]['subject_identity_issues'], parsed['programs'][0]['subject_identity_issues'])
                self.assertEqual(updated['programs'][0]['subjects'], parsed['programs'][0]['subjects'])
                self.assertTrue(updated['programs'][0]['stale'])
                self.assertEqual(updated['programs'][0]['stale_reason'], '独立业务待核')
                self.assertEqual(updated['programs'][0]['conditions'], old['programs'][0]['conditions'])
                self.assertEqual(updated['programs'][0]['tuition'], old['programs'][0]['tuition'])
                self.assertEqual(updated['programs'][0]['duration_years'], 3)

    def test_absent_current_parser_safety_flags_clear_only_old_parser_markers(self):
        old = deepcopy(self.admissions)
        for row in old['programs']:
            row.update({'exam_matching_supported': False, 'subject_identity_issues': [{'reason': 'old parser marker'}],
                        'stale': True, 'stale_reason': '独立业务审核'})
        updated, report = refresh_saved_catalog(self.registry, old, self.root)
        self.assertEqual(report['needs_review_count'], 0)
        for row in updated['programs']:
            self.assertNotIn('exam_matching_supported', row)
            self.assertNotIn('subject_identity_issues', row)
            self.assertTrue(row['stale'])
            self.assertEqual(row['stale_reason'], '独立业务审核')

    def test_precise_prefixed_english_and_math_aliases_can_refresh_scope_without_renaming(self):
        from catalog_replay import _alias_scopes
        html = self.html.replace('201英语（一）', '201统考英语一').replace('301数学（一）', '301统考数学一')
        self.path.write_text(html, 'utf-8')
        document = {**self.doc, 'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}
        parsed = parse_catalog_html(html, self.unit, 2027, document)
        old = {**deepcopy(self.admissions), 'documents': [document], 'programs': deepcopy(parsed['programs'])}
        for row in old['programs']:
            for subject in row['subjects']:
                if subject['code'] in {'201', '301'}: subject['scope'] = self.unit['id']
        converted, changed = _alias_scopes(old['programs'][0])
        self.assertTrue(changed)
        self.assertTrue(all(s['scope'] == 'national' for s in converted['subjects'] if s['code'] in {'201', '301'}))
        updated, report = refresh_saved_catalog(self.registry, old, self.root)
        self.assertEqual(report['needs_review_count'], 0)
        self.assertEqual(report['alias_changed_count'], 2)
        self.assertEqual(updated['programs'][0]['subjects'], parsed['programs'][0]['subjects'])
        bad = deepcopy(old['programs'][0])
        for subject in bad['subjects']:
            if subject['code'] == '201': subject['name'] = '统考英语二'
            if subject['code'] == '301': subject['name'] = '统考数学二'
        converted, _ = _alias_scopes(bad)
        self.assertTrue(all(s['scope'] == self.unit['id'] for s in converted['subjects'] if s['code'] in {'201', '301'}))

    def test_changed_or_missing_core_rows_remain_present_and_need_review(self):
        for field, value in [('code', '083500'), ('id', 'not-in-source')]:
            with self.subTest(field=field):
                old = deepcopy(self.admissions)
                old['programs'][0][field] = value
                updated, report = refresh_saved_catalog(self.registry, old, self.root)
                self.assertEqual(len(updated['programs']), 2)
                self.assertEqual(updated['programs'][0][field], value)
                self.assertEqual(updated['programs'][0]['catalog_replay_status'], 'needs_review')
                self.assertEqual(report['needs_review_count'], 1)
                self.assertEqual(updated['metadata']['unit_catalog_status'][0]['status'], 'needs_review')

    def test_failed_original_bytes_cannot_be_repaired_by_just_replacing_quotes(self):
        self.path.write_text(self.html.replace('408计算机', '409计算机'), 'utf-8')
        updated, report = refresh_saved_catalog(self.registry, self.admissions, self.root)
        self.assertEqual(report['needs_review_count'], 2)
        self.assertTrue(all(row['catalog_replay_status'] == 'needs_review' for row in updated['programs']))
        self.assertEqual(updated['programs'][0]['evidence'], self.admissions['programs'][0]['evidence'])

    def test_each_source_and_inline_policy_is_replayed_once_for_all_its_directions(self):
        from collectors.catalog_enrichment import apply_inline_policies
        with patch('collectors.catalog_enrichment.apply_inline_policies', wraps=apply_inline_policies) as policies:
            updated, report = refresh_saved_catalog(self.registry, self.admissions, self.root)
        self.assertEqual(policies.call_count, 1)
        self.assertEqual(report['documents_replayed'], 1)
        self.assertEqual(report['needs_review_count'], 0)
        self.assertEqual(len(updated['programs']), 2)

    def test_a_restored_original_clears_only_the_stale_flag_created_by_this_migration(self):
        old = deepcopy(self.admissions)
        original_bytes = self.path.read_bytes()
        self.path.write_text('broken', 'utf-8')
        failed, report = refresh_saved_catalog(self.registry, old, self.root)
        self.assertEqual(report['needs_review_count'], 2)
        self.path.write_bytes(original_bytes)
        recovered, report = refresh_saved_catalog(self.registry, failed, self.root)
        self.assertEqual(report['needs_review_count'], 0)
        self.assertFalse(recovered['programs'][0].get('stale', False))
        self.assertEqual(recovered['metadata']['unit_catalog_status'][0]['status'], 'partial')
        old['programs'][0] = {**old['programs'][0], 'stale': True, 'stale_reason': '尚未完成新公告采集'}
        unrelated, report = refresh_saved_catalog(self.registry, old, self.root)
        self.assertTrue(unrelated['programs'][0]['stale'])

    def test_same_original_with_a_new_document_identity_does_not_reuse_old_citation_ids(self):
        verifier = SourceVerifier(self.root)
        before = verifier._source_programs(self.doc, self.unit, 2027, {})
        other = {**self.doc, 'id': 'other'}
        after = verifier._source_programs(other, self.unit, 2027, {})
        self.assertTrue(all(ref['document_id'] == 'd' for ref in before['programs'][0]['evidence']))
        self.assertTrue(all(ref['document_id'] == 'other' for ref in after['programs'][0]['evidence']))


if __name__ == '__main__': unittest.main()
