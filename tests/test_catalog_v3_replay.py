"""The v3 adapter must replay independently and retain the root PDF approval gate."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collectors.catalog_batch import PARSERS, ingest_verified_batch, parse_document
from collectors.catalog_semantic_audit import explain_semantic_audit, semantic_parser_sha256, semantic_programs_sha256
from collectors.catalog_pdf_national_v3 import PARSER
from service_verification import CATALOG_ADAPTERS, STATIC_CATALOG_ADAPTERS, SourceVerifier
from tests.test_catalog_pdf_national_v3 import UNIT, source


DEPENDENCIES = ('collectors/catalog_pdf_national_v3.py', 'collectors/catalog_pdf_national_v2.py',
    'collectors/catalog_pdf_national.py', 'collectors/catalog_pdf_tables.py',
    'collectors/catalog_html.py', 'collectors/catalog_publication.py', 'collectors/embedded_documents.py',
    'collectors/catalogs.py', 'collectors/documents.py', 'service_verification.py',
    'public_sources.py', 'public_navigation.py')


class CatalogV3ReplayTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        project = Path(__file__).resolve().parents[1]
        for relative in DEPENDENCIES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((project / relative).read_bytes())
        (self.root / 'data').mkdir()
        self.unit = {**UNIT, 'official_url': 'https://sample.edu.cn/'}
        raw = b'%PDF-original-fixture'
        (self.root / 'catalog.pdf').write_bytes(raw)
        self.doc = {'id': 'd', 'unit_id': 'u', 'year': 2027, 'kind': 'catalog', 'status': 'parsed',
            'url': 'https://sample.edu.cn/catalog.pdf', 'local_path': 'catalog.pdf',
            'sha256': hashlib.sha256(raw).hexdigest(), 'catalog_parser': PARSER}
        self.layout = source()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('collectors.catalog_pdf_national_v3.extract_pdf_national_layout_v3', return_value=self.layout))
        stack.enter_context(patch('collectors.documents.extract_pdf_text', return_value=self.layout['raw_text']))
        stack.enter_context(patch('collectors.catalog_pdf_national.verify_catalog_publication', return_value={
            'verified': False, 'documents': [], 'evidence': [], 'mode_evidence': [], 'mode': None, 'mode_conflict': False}))

    def parsed(self):
        return parse_document(self.doc, self.unit, 2027, root=self.root)

    def entry(self):
        return {**self.parsed(), 'documents': [self.doc],
            'status': {'unit_id': 'u', 'year': 2027, 'status': 'partial'}}

    def approval(self, rows):
        return {'status': 'approved', 'source_sha256': self.doc['sha256'],
            'parser_sha256': semantic_parser_sha256(self.doc, self.root),
            'programs_sha256': semantic_programs_sha256(rows),
            'reviewer': 'synthetic fixture independent reviewer', 'reviewed_at': '2026-11-10T10:00:00+08:00'}

    def test_v3_is_registered_for_batch_and_static_source_replay(self):
        for registered in (PARSERS, CATALOG_ADAPTERS, STATIC_CATALOG_ADAPTERS):
            self.assertIn(PARSER, registered)
        parsed = self.parsed()
        self.assertEqual(len(parsed['programs']), 2, parsed['issues'])
        self.assertEqual(parsed['catalog_format'], PARSER)
        verifier = SourceVerifier(self.root)
        self.assertTrue(all(verifier.program(row, {'d': self.doc}, self.unit) for row in parsed['programs']))

    def test_source_verifier_rejects_changed_department_mode_subjects_and_source_identity(self):
        parsed = self.parsed()
        verifier = SourceVerifier(self.root)
        row = parsed['programs'][0]
        self.assertTrue(verifier.program(row, {'d': self.doc}, self.unit))
        for key, value in [('department', '其他学院'), ('department_code', '002'),
                           ('study_mode', '非全日制'), ('id', 'forged'), ('unit_id', 'other')]:
            with self.subTest(key=key):
                self.assertFalse(verifier.program({**row, key: value}, {'d': self.doc}, self.unit))
        changed = deepcopy(row)
        changed['subjects'][0]['code'] = '199'
        self.assertFalse(verifier.program(changed, {'d': self.doc}, self.unit))

    def test_v3_has_no_pdf_approval_bypass_and_replays_after_synthetic_approval(self):
        entry = self.entry()
        rejected = ingest_verified_batch({'units': [self.unit]}, [entry], project_root=self.root)
        self.assertEqual(rejected['programs'], [])
        self.assertTrue(any('语义' in item['reason'] for status in rejected['metadata']['unit_catalog_status']
            for item in status.get('rejections', [])))
        audits = {'audits': [self.approval(entry['programs'])]}
        (self.root / 'data/catalog_semantic_audits.json').write_text(json.dumps(audits), 'utf8')
        accepted = ingest_verified_batch({'units': [self.unit]}, [entry], project_root=self.root)
        self.assertEqual(len(accepted['programs']), 2)

    def test_v3_approval_binds_actual_profile_and_frozen_dependencies(self):
        programs = self.parsed()['programs']
        approval = self.approval(programs)
        self.assertTrue(explain_semantic_audit(self.doc, programs, self.root, [approval])['approved'])
        for relative in DEPENDENCIES:
            with self.subTest(dependency=relative):
                path = self.root / relative
                raw = path.read_bytes()
                path.write_bytes(raw + b'\n# altered fixture dependency\n')
                self.assertFalse(explain_semantic_audit(self.doc, programs, self.root, [approval])['approved'])
                path.write_bytes(raw)
        self.assertTrue(explain_semantic_audit(self.doc, programs, self.root, [approval])['approved'])

    def test_v2_approval_and_document_override_paths_do_not_authorize_v3(self):
        programs = self.parsed()['programs']
        v2 = {**self.doc, 'catalog_parser': 'pdf_table_national_v2'}
        approval = {**self.approval(programs), 'parser_sha256': semantic_parser_sha256(v2, self.root)}
        forged = {**self.doc, 'parser_path': 'collectors/catalog_pdf_national_v2.py', 'semantic_audit': approval}
        self.assertFalse(explain_semantic_audit(forged, programs, self.root, [approval])['approved'])
        expected_v2 = hashlib.sha256((self.root / 'collectors/catalog_pdf_national_v2.py').read_bytes()).hexdigest()
        self.assertEqual(semantic_parser_sha256(v2, self.root), expected_v2)

    def test_changed_full_recordset_or_missing_dependency_invalidates_v3_approval(self):
        programs = self.parsed()['programs']
        approval = self.approval(programs)
        changed = [{**programs[0], 'degree_type': '伪造学位'}, *programs[1:]]
        self.assertFalse(explain_semantic_audit(self.doc, changed, self.root, [approval])['approved'])
        self.assertFalse(explain_semantic_audit(self.doc, programs[:1], self.root, [approval])['approved'])
        (self.root / 'collectors/catalog_pdf_national.py').unlink()
        self.assertFalse(explain_semantic_audit(self.doc, programs, self.root, [approval])['approved'])


if __name__ == '__main__':
    unittest.main()
