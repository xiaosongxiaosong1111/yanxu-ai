"""C2 settings/status contracts: real progress counts, desired/serving state, no implicit work."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from knowledge_store import KnowledgeStore
import knowledge_vector_store as ledger
from knowledge_vectors import VectorIndex
from tests.test_knowledge_vectors import FakeEmbedding, FakeSettings, MemoryBackend


class VectorStatusProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='n4-c2-status-'); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.store = KnowledgeStore(self.root)
        self.settings = FakeSettings(); self.embedding = FakeEmbedding(); self.backend = MemoryBackend()
        self.index = VectorIndex(self.store, self.settings, self.embedding, lambda: self.backend)
        self.addCleanup(self.index.close)

    def save(self, name='one', unit='u1', year=2027):
        text = f'{year}年硕士招生专业目录\n081200 计算机科学与技术\n初试科目101、201、301、408。\n\n跨专业考生须加试。'
        raw = ('%PDF-1.4\nc2 status fixture ' + name).encode()
        source = {'url': 'https://grad.example.edu.cn/' + name + '.pdf', 'title': f'{year}年硕士招生专业目录',
                  'unit_id': unit, 'unit_name': '合成大学', 'year': year,
                  'status': 'official_original_read', 'sha256': hashlib.sha256(raw).hexdigest()}
        return self.store.save_source(source, document={'raw': raw, 'text': text,
                    'pages': [{'page': 1, 'text': text}], 'url': source['url'],
                    'attribution_status': 'verified'})

    def test_status_read_is_side_effect_free_and_reports_current_scope_before_any_build(self):
        self.save()
        self.assertFalse((self.root / 'vector-index' / 'milvus.db').exists())
        status = self.index.status()
        self.assertEqual(status['status'], 'not_ready'); self.assertFalse(status['building'])
        self.assertEqual(status['spaces'], []); self.assertEqual(status['jobs']['succeeded'], 0)
        self.assertEqual(status['last_build'], {})
        self.assertEqual(status['current']['sources'], 1)
        self.assertGreaterEqual(status['current']['segments'], 1)
        self.assertIsNone(status['current']['windows'])
        self.assertEqual(self.embedding.calls, []); self.assertEqual(self.backend.upserts, 0)
        self.assertFalse((self.root / 'vector-index' / 'milvus.db').exists())

    def test_built_space_reports_actual_source_segment_window_counts_not_client_inference(self):
        source = self.save(); self.index.build(); status = self.index.status()
        eligible = ledger.eligible(self.store)
        self.assertEqual(status['status'], 'ready'); self.assertEqual(len(status['spaces']), 1)
        space = status['spaces'][0]
        self.assertEqual(space['sources'], 1)
        self.assertEqual(space['segments'], len({row['id'] for row in eligible}))
        self.assertEqual(space['windows'], len(eligible))
        self.assertEqual((space['completed_count'], space['expected_count']), (len(eligible), len(eligible)))
        self.assertEqual(status['current']['sources'], 1)
        self.assertEqual(status['current']['segments'], len({row['id'] for row in eligible}))
        self.assertIsNone(status['current']['windows'])
        self.assertEqual(status['active_profile_id'], 'ep_fixture')
        self.assertEqual(status['active_provider'], 'fixture')
        self.assertTrue(status['last_build']['usage_known'])
        self.assertGreater(status['last_build']['embedded_inputs'], 0)
        self.assertEqual(source['quality']['state'], 'accepted')

    def test_revocation_changes_desired_scope_but_keeps_actual_serving_counts_visible(self):
        source = self.save(); self.index.build(); space = self.index.status()['spaces'][0]
        self.store.change_validity(source['id'], 'revoked', '来源已失效')
        status = self.index.status()
        self.assertEqual(status['current']['sources'], 0)
        self.assertEqual(status['current']['segments'], 0)
        self.assertEqual(status['spaces'][0]['sources'], space['sources'])
        self.assertEqual(status['spaces'][0]['segments'], space['segments'])
        self.assertTrue(status['dirty'])


if __name__ == '__main__':
    unittest.main()
