"""Manually reviewed entries keep their real originals, separate from discovery scores."""
import hashlib
import json
from pathlib import Path
import unittest

from collectors.documents import decode_text
from public_sources import official_source_url
import unit_bindings


class ReviewedOfficialRootsTests(unittest.TestCase):
    def test_each_reviewed_root_has_an_unchanged_official_original_and_named_pointer(self):
        base = Path(__file__).parent / 'fixtures/official_discovery/reviewed_roots'
        rows = json.loads((base/'manifest.json').read_text('utf-8'))['documents']
        self.assertEqual(len(rows), 3)
        for row in rows:
            with self.subTest(unit=row['unit_name']):
                raw = (base/row['raw_path']).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), row['sha256'])
                html = decode_text(raw, row['content_type'])
                self.assertIn(row['unit_name'], html)
                self.assertIn(row['official_url'].rstrip('/'), html)
                unit = unit_bindings.enrich({'id':row['unit_code'], 'name':row['unit_name'],
                                             'unit_code':row['unit_code'], 'official_url':None})
                self.assertEqual(unit['official_url'], row['official_url'])
                self.assertTrue(official_source_url(row['official_url'], unit))
                self.assertFalse(official_source_url('https://grad.unrelated.edu.cn/', unit))
                proof = unit['official_binding']['domains'][0]
                self.assertEqual(proof['evidence'], row['url'])
                self.assertEqual(proof['checked_at'], '2026-11-09')


if __name__ == '__main__':
    unittest.main()
