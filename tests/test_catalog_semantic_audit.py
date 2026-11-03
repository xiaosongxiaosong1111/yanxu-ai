from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from collectors.catalog_semantic_audit import (
    check_semantic_audit, explain_semantic_audit, semantic_programs_sha256,
    semantic_parser_sha256,
)


class CatalogSemanticAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'collectors').mkdir()
        (self.root / 'data').mkdir()
        self.source = self.root / 'catalog.pdf'
        self.source.write_bytes(b'%PDF-1.7 actual saved original')
        (self.root / 'collectors/catalog_pdf_tables.py').write_text('# adapter v1\n', 'utf-8')
        (self.root / 'collectors/catalog_pdf_variants.py').write_text('# adapter v2\n', 'utf-8')
        self.document = {'id': 'd', 'unit_id': 'u', 'year': 2027, 'catalog_parser': 'pdf_table_v1',
                         'local_path': 'catalog.pdf', 'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}
        self.rows = [{'id': 'u:2027:001:081200:01:全日制', 'unit_id': 'u', 'year': 2027,
                      'department_code': '001', 'department': '计算机学院', 'code': '081200',
                      'name': '计算机科学与技术', 'direction': '01人工智能', 'study_mode': '全日制',
                      'subjects': [{'code': '101', 'name': '思想政治理论', 'scope': 'national', 'exam_unit': 1},
                                   {'code': '201', 'name': '英语（一）', 'scope': 'national', 'exam_unit': 2,
                                    'choice_group': 'language'}],
                      'exam_combinations': [{'subjects': [{'code': '201', 'exam_unit': 2}]}],
                      'source_locator': {'page': 1, 'top': 120.5, 'cell': [10, 100, 80, 150]},
                      'evidence': [{'document_id': 'd', 'quote': '原文', 'page': 1}]}]

    def approval(self, document=None, rows=None):
        document = self.document if document is None else document
        rows = self.rows if rows is None else rows
        return {'status': 'approved', 'source_sha256': document['sha256'],
                'parser_sha256': semantic_parser_sha256(document, self.root),
                'programs_sha256': semantic_programs_sha256(rows), 'reviewer': 'root independent review',
                'reviewed_at': datetime.now(timezone.utc).isoformat()}

    def check(self, approvals=None, document=None, rows=None):
        return check_semantic_audit(self.document if document is None else document,
            self.rows if rows is None else rows, self.root, approvals)

    def test_pdf_requires_a_separate_explicit_approval_not_document_self_assertion(self):
        self.assertFalse(self.check())
        self.assertFalse(self.check([], {**self.document, 'semantic_audit': self.approval(), 'status': 'approved'}))
        self.assertFalse(self.check([{'status': 'approved'}]))
        self.assertTrue(self.check([self.approval()]))

    def test_supported_v2_is_bound_to_its_own_adapter(self):
        document = {**self.document, 'catalog_parser': 'pdf_table_v2'}
        self.assertFalse(self.check([self.approval()], document))
        self.assertTrue(self.check([self.approval(document)], document))

    def test_actual_source_tamper_invalidates_approval_even_if_metadata_and_mtime_stay(self):
        approvals = [self.approval()]
        original_time = self.source.stat().st_mtime_ns
        self.source.write_bytes(b'%PDF-1.7 different original!!')
        os.utime(self.source, ns=(original_time, original_time))
        self.assertFalse(self.check(approvals))
        document = {**self.document, 'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}
        self.assertFalse(self.check(approvals, document))

    def test_actual_parser_tamper_invalidates_approval_without_mtime_shortcut(self):
        approvals = [self.approval()]
        path = self.root / 'collectors/catalog_pdf_tables.py'
        original_time = path.stat().st_mtime_ns
        path.write_text('# adapter x1\n', 'utf-8')
        os.utime(path, ns=(original_time, original_time))
        self.assertFalse(self.check(approvals))

    def test_all_core_facts_complete_choices_combinations_and_locations_are_bound(self):
        approvals = [self.approval()]
        for key, value in [('department', '另一学院'), ('department_code', '002'), ('code', '083500'),
                           ('direction', '02另一方向'), ('study_mode', '非全日制'),
                           ('source_locator', {'page': 2}), ('future_condition_field', '新增条件')]:
            with self.subTest(field=key):
                self.assertFalse(self.check(approvals, rows=[{**self.rows[0], key: value}]))
        for field, value in [('code', '203'), ('choice_group', 'another-group'), ('exam_unit', 3), ('scope', 'u')]:
            rows = deepcopy(self.rows); rows[0]['subjects'][1][field] = value
            with self.subTest(subject_field=field): self.assertFalse(self.check(approvals, rows=rows))
        rows = deepcopy(self.rows); rows[0]['exam_combinations'][0]['subjects'][0]['code'] = '203'
        self.assertFalse(self.check(approvals, rows=rows))
        self.assertFalse(self.check(approvals, rows=[]))
        self.assertFalse(self.check(approvals, rows=[*self.rows, {**self.rows[0], 'id': 'another'}]))

    def test_fingerprint_sorts_record_and_exam_sets_but_keeps_coordinate_order(self):
        rows = [*deepcopy(self.rows), {**deepcopy(self.rows[0]), 'id': 'another'}]
        reordered = deepcopy(rows[::-1])
        reordered[0]['subjects'] = reordered[0]['subjects'][::-1]
        self.assertEqual(semantic_programs_sha256(rows), semantic_programs_sha256(reordered))
        changed = deepcopy(rows); changed[0]['source_locator']['cell'].reverse()
        self.assertNotEqual(semantic_programs_sha256(rows), semantic_programs_sha256(changed))

    def test_evidence_and_fetch_timestamps_are_not_semantic_facts(self):
        changed = deepcopy(self.rows)
        changed[0]['evidence'] = [{'document_id': 'new-id', 'quote': '刷新引文'}]
        changed[0]['checked_at'] = '2027-01-01T00:00:00Z'
        changed[0]['subjects'][0]['evidence'] = {'quote': '新引用'}
        self.assertEqual(semantic_programs_sha256(self.rows), semantic_programs_sha256(changed))
        changed[0]['conditions'] = ['须相关本科专业']
        self.assertNotEqual(semantic_programs_sha256(self.rows), semantic_programs_sha256(changed))

    def test_root_file_is_reloaded_and_revocation_is_immediate(self):
        path = self.root / 'data/catalog_semantic_audits.json'
        approval = self.approval()
        path.write_text(json.dumps({'audits': [approval]}), 'utf-8')
        self.assertTrue(self.check())
        original_time = path.stat().st_mtime_ns
        path.write_text(json.dumps({'audits': [{**approval, 'status': 'revoked!'}]}), 'utf-8')
        os.utime(path, ns=(original_time, original_time))
        self.assertFalse(self.check())
        path.write_text('{broken json', 'utf-8')
        self.assertFalse(self.check())

    def test_approval_hashes_reviewer_timestamp_status_and_uniqueness_are_validated(self):
        approval = self.approval()
        for changes in [{'status': 'pending'}, {'reviewer': ''}, {'reviewed_at': 'yesterday'},
                        {'reviewed_at': '2026-10-30T10:00:00'}, {'source_sha256': 'not-a-hash'},
                        {'parser_sha256': '0' * 64}, {'programs_sha256': '0' * 64}]:
            with self.subTest(changes=changes): self.assertFalse(self.check([{**approval, **changes}]))
        self.assertFalse(self.check([approval, approval]))
        self.assertFalse(self.check([approval, {**approval, 'status': 'revoked'}]))

    def test_missing_parser_source_and_path_escape_fail_closed(self):
        approvals = [self.approval()]
        (self.root / 'collectors/catalog_pdf_tables.py').unlink()
        self.assertFalse(self.check(approvals))
        for local in ['../outside.pdf', str(self.root.parent / 'outside.pdf'), None]:
            with self.subTest(path=local): self.assertFalse(self.check(approvals, {**self.document, 'local_path': local}))
        self.assertFalse(self.check(approvals, {**self.document, 'local_path': 'data'}))

    def test_unknown_pdf_table_adapter_cannot_fall_through_or_choose_its_own_code_path(self):
        for adapter in ['pdf_table_v3', 'PDF_TABLE_V1', 'pdf_table_custom']:
            with self.subTest(adapter=adapter):
                self.assertFalse(self.check([self.approval()], {**self.document, 'catalog_parser': adapter,
                    'parser_path': 'collectors/catalog_pdf_tables.py'}))
        self.assertTrue(self.check([], {**self.document, 'catalog_parser': 'html_table_v1'}))
        self.assertTrue(self.check([], {**self.document, 'catalog_parser': 'pdf_row_v1'}))

    def test_malformed_or_duplicate_recordsets_are_rejected(self):
        for rows in [{}, [None], [{'id': ''}], [self.rows[0], self.rows[0]], [{**self.rows[0], 'bad': float('nan')}]]:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError): semantic_programs_sha256(rows)
        self.assertFalse(self.check([self.approval()], rows=[None]))

    def test_explanation_and_read_only_contract(self):
        before_document, before_rows = deepcopy(self.document), deepcopy(self.rows)
        approval = self.approval(); before_approval = deepcopy(approval)
        result = explain_semantic_audit(self.document, self.rows, self.root, [approval])
        self.assertTrue(result['approved'])
        self.assertEqual(self.document, before_document)
        self.assertEqual(self.rows, before_rows)
        self.assertEqual(approval, before_approval)
        self.assertFalse((self.root / 'data/catalog_semantic_audits.json').exists())
        self.assertTrue(explain_semantic_audit({**self.document, 'catalog_parser': 'html_table_v1'}, [], self.root)['approved'])


if __name__ == '__main__': unittest.main()
