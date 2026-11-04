"""Independent embedding connection lifecycle: test receipts, profiles, honest degradation."""
import json
from pathlib import Path
import tempfile
import unittest

from settings_store import SettingsService, SettingsError
from embedding_catalog import LOCAL_MODEL, LOCAL_REVISION


class FakeTransport:
    def __init__(self):
        self.chat_calls = []

    def models(self, config):
        return {'models': [{'id': 'chat-model', 'label': 'chat-model'}], 'discovery': 'remote'}

    def chat(self, config, messages, tools=None, stop_event=None):
        self.chat_calls.append(config['model'])
        return {'message': {'role': 'assistant', 'content': 'OK', 'tool_calls': []}, 'usage': {}}

    def test_yuanbao(self, key):
        raise AssertionError('embedding settings must never go through the yuanbao path')


class FakeEmbedding:
    def __init__(self):
        self.calls = []
        self.dimension = None

    def embed(self, config, texts, purpose='document', stop_event=None):
        self.calls.append((config['provider'], tuple(texts), purpose))
        dimension = self.dimension or (512 if config['provider'] == 'local_bge' else 7)
        return {'vectors': [[1.0]+[0.0]*(dimension-1) for _ in texts], 'dimension': dimension,
                'model': config['model'], 'model_revision': config['model_revision'],
                'usage': {}, 'usage_known': False, 'input_count': len(texts)}

    def models(self, config):
        self.calls.append(('models', config['provider']))
        return {'models': [{'id': 'embed-model', 'label': 'embed-model'}], 'discovery': 'remote',
                'notice': '目录仅为候选'}


REMOTE = {'kind': 'embedding', 'provider': 'custom', 'base_url': 'https://embedding.example.com/v1',
          'api_key': 'embedding-secret-fixture', 'model': 'embed-model'}


class EmbeddingSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n4-settings-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.embedding = FakeEmbedding()
        self.store = SettingsService(self.root, transport=FakeTransport(), embedding=self.embedding)

    def save_embedding(self, payload=REMOTE, with_receipt=True):
        token = self.store.test(dict(payload))['validation_token'] if with_receipt else None
        body = {**payload, **({'validation_token': token} if token else {})}
        return self.store.save(body)

    def test_local_default_needs_no_key_and_binds_real_vector_receipt(self):
        before = len(self.store.transport.chat_calls)
        proof = self.store.test({'kind': 'embedding', 'provider': 'local_bge'})
        self.assertEqual(len(self.store.transport.chat_calls), before)
        self.assertEqual(proof['dimension'], 512)
        self.assertEqual(proof['response_model'], LOCAL_MODEL)
        saved = self.store.save({'kind': 'embedding', 'provider': 'local_bge',
                                 'validation_token': proof['validation_token']})
        self.assertEqual(saved['embedding']['status'], 'connected')
        self.assertFalse(saved['embedding']['has_key'])
        self.assertEqual(saved['embedding']['dimension'], 512)
        runtime = self.store.runtime_embedding()
        self.assertEqual((runtime['model'], runtime['model_revision'], runtime['dimension']),
                         (LOCAL_MODEL, LOCAL_REVISION, 512))
        by_profile = self.store.runtime_embedding(saved['embedding']['profile_id'])
        self.assertEqual(by_profile['profile_id'], saved['embedding']['profile_id'])

    def test_remote_test_saves_observed_dimension_and_model_without_key_leak(self):
        proof = self.store.test(dict(REMOTE))
        self.assertEqual((proof['dimension'], proof['response_model']), (7, 'embed-model'))
        public = self.save_embedding()
        embedding = public['embedding']
        self.assertEqual((embedding['status'], embedding['dimension'], embedding['response_model']),
                         ('connected', 7, 'embed-model'))
        self.assertNotIn(REMOTE['api_key'], json.dumps(public))
        runtime = self.store.runtime_embedding()
        self.assertEqual(runtime['api_key'], REMOTE['api_key'])
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(REMOTE['api_key'].encode(), path.read_bytes())

    def test_changed_key_gets_new_profile_and_old_snapshot_stays_usable(self):
        first = self.save_embedding()
        old_id = first['embedding']['profile_id']
        changed = self.save_embedding({**REMOTE, 'api_key': 'rotated-embedding-key'})
        new_id = changed['embedding']['profile_id']
        self.assertNotEqual(old_id, new_id)
        self.assertEqual(self.store.runtime_embedding()['api_key'], 'rotated-embedding-key')
        self.assertEqual(self.store.runtime_embedding(old_id)['api_key'], REMOTE['api_key'])

    def test_new_observed_dimension_creates_new_identity_and_never_overwrites_old_snapshot(self):
        self.embedding.dimension = 7
        first = self.save_embedding()
        old_id = first['embedding']['profile_id']
        self.assertEqual(self.store.runtime_embedding(old_id)['dimension'], 7)
        self.embedding.dimension = 8
        changed = self.save_embedding()
        new_id = changed['embedding']['profile_id']
        self.assertNotEqual(old_id, new_id)
        self.assertEqual(self.store.runtime_embedding()['dimension'], 8)
        self.assertEqual(self.store.runtime_embedding(old_id)['dimension'], 7)

    def test_delete_invalidates_old_profiles_and_never_borrows_the_chat_key(self):
        proof = self.store.test({'kind': 'ai', 'provider': 'deepseek', 'api_key': 'chat-secret-fixture',
                                 'model': 'chat-model'})
        self.store.save({'kind': 'ai', 'provider': 'deepseek', 'api_key': 'chat-secret-fixture',
                         'model': 'chat-model', 'validation_token': proof['validation_token']})
        saved = self.save_embedding()
        profile_id = saved['embedding']['profile_id']
        self.store.delete({'kind': 'embedding'})
        for call in (lambda: self.store.runtime_embedding(), lambda: self.store.runtime_embedding(profile_id)):
            with self.assertRaises(SettingsError) as caught:
                call()
            self.assertEqual(caught.exception.status, 503)
        self.assertEqual(self.store.runtime_ai()['api_key'], 'chat-secret-fixture')
        if self.store.box.persistent and (self.root/'runtime/settings.sqlite').is_file():
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(self.root/'runtime/settings.sqlite')) as connection:
                rows = connection.execute("SELECT count(*) FROM settings WHERE kind LIKE 'embedding%'").fetchone()[0]
            self.assertEqual(rows, 0)

    def test_save_without_real_vector_receipt_is_not_tested_and_runtime_refuses(self):
        public = self.save_embedding(with_receipt=False)
        self.assertEqual(public['embedding']['status'], 'not_tested')
        with self.assertRaises(SettingsError) as caught:
            self.store.runtime_embedding()
        self.assertEqual(caught.exception.code, 'model_not_verified')

    def test_receipt_is_bound_to_the_exact_embedding_config(self):
        proof = self.store.test(dict(REMOTE))
        public = self.store.save({**REMOTE, 'model': 'other-embed',
                                  'validation_token': proof['validation_token']})
        self.assertEqual(public['embedding']['status'], 'not_tested')

    def test_local_bge_rejects_credentials_and_unsupported_providers(self):
        for payload in ({'kind': 'embedding', 'provider': 'local_bge', 'api_key': 'x'},
                        {'kind': 'embedding', 'provider': 'deepseek', 'api_key': 'k', 'model': 'm'},
                        {'kind': 'embedding', 'provider': 'local_bge', 'dimension': 1024}):
            with self.subTest(payload=payload), self.assertRaises(SettingsError):
                self.store.test(payload)

    def test_providers_listing_is_embedding_specific(self):
        rows = self.store.providers()
        identifiers = {row['id'] for row in rows['embedding_providers']}
        self.assertIn('local_bge', identifiers)
        self.assertNotIn('deepseek', identifiers)
        self.assertNotIn('yuanbao', identifiers)
        local = next(row for row in rows['embedding_providers'] if row['id'] == 'local_bge')
        self.assertFalse(local['requires_key'])

    def test_models_discovery_uses_embedding_contract(self):
        result = self.store.models(dict(REMOTE))
        self.assertEqual([row['id'] for row in result['models']], ['embed-model'])
        self.assertIn(('models', 'custom'), self.embedding.calls)

    def test_public_state_for_unconfigured_embedding_is_honest(self):
        public = self.store.public_settings()
        self.assertEqual(public['embedding']['status'], 'not_configured')
        self.assertFalse(public['embedding']['has_key'])
        self.assertEqual(public['embedding']['provider'], 'local_bge')

    def test_profile_snapshot_survives_restart_through_encrypted_storage(self):
        if not self.store.box.persistent:
            self.skipTest('Windows DPAPI persistence only')
        saved = self.save_embedding()
        profile_id = saved['embedding']['profile_id']
        restarted = SettingsService(self.root, transport=FakeTransport(), embedding=self.embedding)
        runtime = restarted.runtime_embedding(profile_id)
        self.assertEqual(runtime['api_key'], REMOTE['api_key'])
        self.assertEqual((runtime['model'], runtime['dimension']), ('embed-model', 7))
        self.assertNotIn(REMOTE['api_key'], json.dumps(restarted.public_settings()))
        restarted.delete({'kind': 'embedding'})
        with self.assertRaises(SettingsError):
            restarted.runtime_embedding(profile_id)


if __name__ == '__main__':
    unittest.main()
