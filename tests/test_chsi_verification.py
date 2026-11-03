from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collectors.catalog_chsi import parse_chsi_detail_html
from service_verification import SourceVerifier, citation_signature
from tests.test_catalog_chsi import CONTEXT, UNIT, ROW, combination, detail_html


class ChsiVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'detail.html'
        self.path.write_text(detail_html(), 'utf-8')
        self.document = {'id': 'detail', 'unit_id': UNIT['id'], 'year': 2027,
            'kind': 'catalog', 'status': 'parsed', 'catalog_parser': 'chsi_detail_v1',
            'url': 'https://yz.chsi.com.cn/zsml/yjfxdetail?id=' + ROW['id'], 'method': 'GET',
            'local_path': self.path.name, 'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}
        self.documents = {'detail': self.document}
        self.row = parse_chsi_detail_html(detail_html(), UNIT, 2027, self.document, context_html=CONTEXT)['programs'][0]
        self.proof = {'verified': True, 'context_html': CONTEXT, 'signature': 'context-and-gate-sha', 'documents': []}

    def test_exact_structured_fields_replay_without_fabricated_quote(self):
        with patch.object(SourceVerifier, '_chsi_proof', return_value=self.proof):
            verifier = SourceVerifier(self.root)
            self.assertTrue(verifier.program(self.row, self.documents, UNIT))
            self.assertTrue(verifier.structured_evidence(self.row['evidence'][0], self.row, self.documents, UNIT))
            self.assertFalse(verifier.unit_reconciles(UNIT, 2027, [self.row], self.documents))
            self.assertNotIn('quote', self.row['evidence'][0])
            wrong = deepcopy(self.row)
            wrong['evidence'][0]['structured_fields']['研究方向'] = '(02)其他方向'
            self.assertFalse(verifier.program(wrong, self.documents, UNIT))
            self.assertFalse(verifier.structured_evidence(wrong['evidence'][0], self.row, self.documents, UNIT))
            wrong = deepcopy(self.row); wrong['subjects'][1]['code'] = '203'
            self.assertFalse(verifier.program(wrong, self.documents, UNIT))

    def test_missing_gate_wrong_url_year_or_context_cannot_replay(self):
        with patch.object(SourceVerifier, '_chsi_proof', return_value={'verified': False}):
            self.assertFalse(SourceVerifier(self.root).program(self.row, self.documents, UNIT))
        for changed in [{'url': self.document['url'].replace(ROW['id'], 'other')}, {'method': 'POST'}, {'year': 2026}]:
            with self.subTest(changed=changed), patch.object(SourceVerifier, '_chsi_proof', return_value=self.proof):
                docs = {'detail': {**self.document, **changed}}
                self.assertFalse(SourceVerifier(self.root).program(self.row, docs, UNIT))
        with patch.object(SourceVerifier, '_chsi_proof', return_value={**self.proof, 'context_html': CONTEXT.replace('2027', '2026')}):
            self.assertFalse(SourceVerifier(self.root).program(self.row, self.documents, UNIT))

    def test_coupled_exam_sets_are_preserved_but_cannot_be_flattened_or_changed(self):
        raw = {**ROW, 'kskmz': [combination(), combination('203', '日语', third='612')]}
        html = detail_html(raw)
        self.path.write_text(html, 'utf-8')
        doc = {**self.document, 'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}
        row = parse_chsi_detail_html(html, UNIT, 2027, doc, context_html=CONTEXT)['programs'][0]
        with patch.object(SourceVerifier, '_chsi_proof', return_value=self.proof):
            verifier = SourceVerifier(self.root)
            self.assertTrue(verifier.program(row, {'detail': doc}, UNIT))
            self.assertEqual(row['subjects'], [])
            self.assertFalse(row['exam_matching_supported'])
            for changes in [{'exam_matching_supported': True}, {'exam_combinations': []}, {'subjects': self.row['subjects']}]:
                self.assertFalse(verifier.program({**row, **changes}, {'detail': doc}, UNIT))

    def test_structured_signature_retains_field_path_and_values(self):
        original = self.row['evidence']
        self.assertIsNotNone(citation_signature(original))
        changed = deepcopy(original); changed[0]['field_path'] = 'another.row'
        self.assertNotEqual(citation_signature(original), citation_signature(changed))
        changed = deepcopy(original); changed[0]['quote'] = 'invented concatenation'
        self.assertIsNone(citation_signature(changed))


if __name__ == '__main__':
    unittest.main()
