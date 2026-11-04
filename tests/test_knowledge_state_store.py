"""Current validity never overrides source admission or original-byte integrity."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from knowledge_store import KnowledgeError, KnowledgeStore


class KnowledgeStateStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='n3-state-store-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = KnowledgeStore(self.root)

    def original(self, suffix='one', *, year=2027, body=None, url=None):
        raw = ('%PDF-1.4\nstate fixture ' + suffix).encode()
        text = body or f'{year}年硕士招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。'
        source = {'url': url or 'https://grad.example.edu.cn/catalog.pdf', 'unit_id': 'u1',
                  'unit_name': '合成大学', 'title': f'{year}年硕士招生专业目录', 'year': year,
                  'status': 'official_original_read', 'sha256': hashlib.sha256(raw).hexdigest(), 'excerpt': text}
        document = {'url': source['url'], 'raw': raw, 'text': text, 'pages': [{'page': 1, 'text': text}],
                    'attribution_status': 'verified'}
        return source, document

    def save(self, *args, **kwargs):
        source, document = self.original(*args, **kwargs)
        return self.store.save_source(source, document=document)

    def test_revoke_blocks_existing_segments_without_rewriting_quality(self):
        saved = self.save()
        segment = self.store.search_segments(entry_id=saved['id'])['segments'][0]
        changed = self.store.change_validity(saved['id'], 'revoked', '来源需要重新核实')
        self.assertEqual(changed['entry']['validity'], 'revoked')
        self.assertEqual(changed['entry']['quality']['state'], 'accepted')
        self.assertFalse(changed['entry']['usable'])
        self.assertEqual(self.store.search_segments(entry_id=saved['id'])['segments'], [])
        self.assertEqual(self.store.search()['total'], 0)
        with self.assertRaises(KnowledgeError): self.store.read_segment(segment['id'])
        with self.assertRaises(KnowledgeError): self.store.read_segment(segment['id'],trusted_only=False)

    def test_same_source_save_does_not_reactivate_revoked_knowledge(self):
        saved = self.save()
        self.store.change_validity(saved['id'], 'revoked', '保留撤信状态')
        repeated = self.save()
        self.assertEqual(repeated['validity'], 'revoked')
        self.assertFalse(repeated['usable'])

    def test_restore_requires_original_bytes_and_admission(self):
        saved = self.save()
        self.store.change_validity(saved['id'], 'revoked', '人工复核中')
        (self.root / 'runtime/research-sources' / (saved['sha256'] + '.bin')).unlink()
        with self.assertRaises(KnowledgeError):
            self.store.change_validity(saved['id'], 'active', '尝试恢复')
        self.assertEqual(self.store.get(saved['id'])['validity'], 'revoked')

    def test_pending_cannot_be_selected_as_trusted_current_version(self):
        pending = self.store.save_source({'url': 'https://grad.example.edu.cn/lead', 'unit_id': 'u1',
            'title': '2027年硕士招生专业目录', 'year': 2027, 'status': 'search_lead', 'excerpt': '检索摘要'})
        with self.assertRaises(KnowledgeError): self.store.choose_version(pending['id'], '选择版本')

    def test_different_full_originals_require_explicit_current_version(self):
        first = self.save('one')
        second = self.save('two', body='2027年硕士招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、901。')
        self.assertEqual(self.store.get(first['id'])['validity'], 'conflict')
        self.assertEqual(self.store.get(second['id'])['validity'], 'conflict')
        self.assertEqual(self.store.search_segments()['segments'], [])
        chosen = self.store.choose_version(second['id'], '核对官方更正后采用此版本')
        self.assertTrue(chosen['entry']['usable'])
        self.assertEqual(self.store.get(first['id'])['validity'], 'superseded')
        self.assertTrue(self.store.jobs(second['id'])['jobs'])

    def test_distinct_years_and_identical_text_are_not_automatically_replaced(self):
        first = self.save('one')
        same_text = self.save('two')
        older = self.save('older', year=2026)
        for saved in (first, same_text, older):
            self.assertEqual(self.store.get(saved['id'])['validity'], 'active')
        with self.assertRaises(KnowledgeError):
            self.store.change_validity(older['id'], 'superseded', '错误跨年度替代', first['id'])

    def test_new_version_cannot_bypass_an_unresolved_conflict(self):
        first = self.save('one')
        second = self.save('two', body='2027年硕士招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、901。')
        third = self.save('three', body='2027年硕士招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、902。')
        for saved in (first, second, third):
            self.assertEqual(self.store.get(saved['id'])['validity'], 'conflict')
        self.assertEqual(self.store.search_segments()['segments'], [])

    def test_detail_lists_only_bounded_same_scope_year_version_metadata(self):
        first = self.save('one')
        second = self.save('two')
        self.save('older', year=2026)
        versions = self.store.get(first['id'])['versions']
        self.assertEqual({item['id'] for item in versions}, {first['id'], second['id']})
        self.assertTrue(next(item for item in versions if item['id'] == first['id'])['is_this_entry'])
        self.assertTrue(all(not {'text','excerpt','pages','search_text'} & item.keys() for item in versions))

    def test_bounded_version_list_always_includes_the_currently_open_entry(self):
        first=self.save('oldest')
        for index in range(21):self.save('newer-'+str(index))
        versions=self.store.get(first['id'])['versions']
        self.assertEqual(len(versions),20)
        self.assertEqual(versions[0]['id'],first['id'])

    def test_reason_validation_and_unknown_id_do_not_leak_paths(self):
        saved = self.save()
        for reason in ['', 'secret_key=should-never-be-an-audit-reason']:
            with self.subTest(reason=reason), self.assertRaises(KnowledgeError):
                self.store.change_validity(saved['id'], 'revoked', reason)
        with self.assertRaises(KnowledgeError) as error:
            self.store.jobs('k_' + '0' * 24)
        self.assertNotIn(str(self.root), str(error.exception))


if __name__ == '__main__':
    unittest.main()
