"""Real school cutoffs keep their original scope, pages and independent scores."""
import hashlib
import json
from pathlib import Path
import unittest

from service_verification import SourceVerifier, canonical


class SchoolCutoffSourceTests(unittest.TestCase):
    def test_historical_citations_are_original_text_including_the_legacy_detail_view(self):
        root = Path(__file__).resolve().parents[1] / 'data'
        history = json.loads((root / 'history.json').read_text('utf-8'))
        documents = {row['id']: row for row in history['documents']}
        units = {row['id']: row for row in json.loads((root / 'units.json').read_text('utf-8'))['units']}
        verifier = SourceVerifier(root)
        for sample in history['admitted_statistics']:
            references = sample['evidence'] if isinstance(sample['evidence'], list) else [sample['evidence']]
            self.assertTrue(references)
            for item in references:
                with self.subTest(sample=sample['id'], quote=item.get('quote')):
                    document = documents[item['document_id']]
                    pages = verifier.pages(document, units[sample['unit_id']], ('admission_results',))
                    selected = pages[item['page'] - 1:item['page']] if item.get('page') else pages
                    self.assertTrue(any(canonical(item['quote']) in canonical(page) for page in selected))

    def test_three_year_school_references_are_exact_originals_not_admission_minima(self):
        root = Path(__file__).resolve().parents[1] / 'data'
        history = json.loads((root / 'history.json').read_text('utf-8'))
        documents = {row['id']: row for row in history['documents']}
        units = json.loads((root / 'units.json').read_text('utf-8'))['units']
        unit = next(row for row in units if row['id'] == '367949')
        rows = [row for row in history['score_lines'] if row['id'].startswith('score-nankai-')]
        self.assertEqual(len(rows), 6)
        expected = {'学术学位': {2024: 300, 2025: 290, 2026: 290},
                    '专业学位': {2024: 310, 2025: 290, 2026: 290}}
        verifier = SourceVerifier(root)
        for row in rows:
            with self.subTest(row=row['id']):
                self.assertEqual(row['total'], expected[row['degree_type']][row['year']])
                self.assertEqual(row['kind'], 'university')
                self.assertEqual(row['discipline_code'], '08')
                self.assertEqual(row['applicability_status'], 'needs_review')
                self.assertIsNone(row['program_code'])
                self.assertIsNone(row['study_mode'])
                self.assertIsNone(row['full_score'])
                self.assertTrue(row['code_prefixes'])
                self.assertEqual(row['column_order'], ['politics', 'foreign_language', 'business1', 'business2', 'total'])
                self.assertTrue(any(item.get('role') == 'column_header' for item in row['evidence']))
                for item in row['evidence']:
                    document = documents[item['document_id']]
                    self.assertEqual(document['year'], row['year'])
                    self.assertEqual(document['url'], item['url'])
                    self.assertEqual(document['unit_id'], row['unit_id'])
                    path, raw = verifier.read(document)
                    self.assertTrue(path.is_relative_to(root.resolve()))
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), document['sha256'])
                    pages = verifier.pages(document, unit, ('score',))
                    self.assertIn(canonical(item['quote']), canonical(pages[item['page'] - 1]))
                self.assertTrue(any('各学院' in item['quote'] for item in row['evidence']))


if __name__ == '__main__':
    unittest.main()
