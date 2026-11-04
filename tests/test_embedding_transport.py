"""No external API calls: embedding capability, security and identity contracts."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from embedding_catalog import checked_embedding_config, profile, public_providers, LOCAL_MODEL, LOCAL_REVISION
from embedding_transport import EmbeddingTransport, TransportError


def response(vectors, model='embed-model'):
    return {'model': model, 'data': [{'index': i, 'embedding': vector} for i, vector in enumerate(vectors)]}


class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='n4-embed-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = {'provider': 'custom', 'base_url': 'https://embedding.example.com/v1',
                       'api_key': 'fixture-embedding-key', 'model': 'embed-model'}

    def transport(self, value, captured=None):
        def request(config, method, path, body):
            if captured is not None: captured.append((config, method, path, body))
            return deepcopy(value)
        return EmbeddingTransport(self.root, transport=request)

    def test_independent_embedding_presets_and_pinned_local_defaults(self):
        presets = {value['id'] for value in public_providers()}
        self.assertEqual(presets, {'local_bge', 'siliconflow', 'dashscope', 'openai', 'ollama', 'custom'})
        value = checked_embedding_config({'provider': 'local_bge'})
        self.assertEqual((value['model'], value['model_revision'], value['dimension']), (LOCAL_MODEL, LOCAL_REVISION, 512))
        self.assertFalse(value.get('api_key'))
        for provider in ('deepseek', 'anthropic', 'yuanbao'):
            with self.subTest(provider=provider), self.assertRaises(TransportError):
                checked_embedding_config({'provider': provider, 'api_key': 'key'})

    def test_identity_excludes_keys_but_binds_model_endpoint_and_input_policy(self):
        first = profile({**self.config, 'dimension': 2})
        self.assertEqual(first, profile({**self.config, 'api_key': 'changed-key', 'dimension': 2}))
        self.assertNotIn('fixture-embedding-key', json.dumps(first))
        for changed in ({'model': 'another'}, {'base_url': 'https://other.example.com/v1'},
                        {'dimension': 3}, {'model_revision': 'r2'}, {'document_prefix': '背景：'}):
            with self.subTest(changed=changed):
                self.assertNotEqual(first['fingerprint'], profile({**self.config, 'dimension': 2, **changed})['fingerprint'])

    def test_config_rejects_unsafe_endpoints_credentials_and_invalid_limits(self):
        changes = [{'base_url': 'http://127.0.0.1/v1'}, {'base_url': 'https://169.254.169.254/v1'},
                   {'base_url': 'https://user:secret@example.com/v1'}, {'base_url': 'https://x.example/v1?key=a'},
                   {'base_url': 'https://x.example/v1#fragment'}, {'api_key': 'bad\nkey'},
                   {'dimension': True}, {'dimension': 0}, {'dimension': 9000}, {'input_limit': False},
                   {'input_limit': 9000}, {'normalization': 'none'}, {'model': '../unsafe'}]
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises(TransportError):
                checked_embedding_config({**self.config, **changed})

    def test_real_order_is_restored_by_explicit_indices_and_vectors_are_normalized(self):
        value = {'model': 'embed-model', 'data': [{'index': 1, 'embedding': [0, 5]}, {'index': 0, 'embedding': [3, 4]}],
                 'usage': {'prompt_tokens': 7, 'total_tokens': 7}}
        captured = []
        result = self.transport(value, captured).embed(self.config, ['甲', '乙'])
        self.assertEqual(result['vectors'], [[0.6, 0.8], [0.0, 1.0]])
        self.assertEqual((result['dimension'], result['input_count'], result['model']), (2, 2, 'embed-model'))
        self.assertTrue(result['usage_known'])
        self.assertEqual(captured[0][2], '/embeddings')
        self.assertEqual(captured[0][3]['encoding_format'], 'float')

    def test_missing_duplicate_out_of_range_or_boolean_indices_fail(self):
        values = [{}, {'data': []}, {'model': 'embed-model', 'data': [{'embedding': [1, 2]}]},
                  {'model': 'embed-model', 'data': [{'index': True, 'embedding': [1, 2]}]},
                  {'model': 'embed-model', 'data': [{'index': 1, 'embedding': [1, 2]}]},
                  {'model': 'embed-model', 'data': [{'index': 0, 'embedding': [1, 2]}, {'index': 0, 'embedding': [1, 2]}]}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(TransportError):
                self.transport(value).embed(self.config, ['甲'])

    def test_bad_numeric_vectors_and_model_identity_fail_closed(self):
        vectors = [[True, 1], [float('nan'), 1], [float('inf'), 1], ['1', 2], [0, 0], [], [1] * 8193]
        for vector in vectors:
            with self.subTest(vector=str(vector)[:40]), self.assertRaises(TransportError):
                self.transport(response([vector])).embed(self.config, ['甲'])
        with self.assertRaises(TransportError): self.transport(response([[1, 0]], 'other-model')).embed(self.config, ['甲'])

    def test_batch_and_observed_dimension_drift_are_rejected(self):
        with self.assertRaises(TransportError): self.transport(response([[1, 0], [1, 0, 0]])).embed(self.config, ['甲', '乙'])
        with self.assertRaises(TransportError): self.transport(response([[1, 0]])).embed({**self.config, 'dimension': 3}, ['甲'])
        transport = self.transport(response([[1, 0]]))
        transport.embed(self.config, ['甲'])
        transport.http._request = lambda *args: response([[1, 0, 0]])
        with self.assertRaises(TransportError): transport.embed(self.config, ['乙'])

    def test_usage_unknown_is_not_presented_as_zero_billed_tokens(self):
        result = self.transport(response([[1, 0]])).embed(self.config, ['甲'])
        self.assertFalse(result['usage_known'])
        self.assertEqual(result['usage'], {})

    def test_prefix_and_cancellation_preserve_input_and_do_not_make_extra_calls(self):
        captured = []
        config = {**self.config, 'query_prefix': '检索：', 'document_prefix': '正文：'}
        before = deepcopy(config)
        transport = self.transport(response([[1, 0]]), captured)
        transport.embed(config, ['原文'], purpose='query')
        self.assertEqual(captured[0][3]['input'], ['检索：原文'])
        self.assertEqual(config, before)
        stop = threading.Event(); stop.set()
        with self.assertRaises(TransportError) as caught: transport.embed(config, ['原文'], stop_event=stop)
        self.assertEqual(caught.exception.code, 'cancelled')
        self.assertEqual(len(captured), 1)

    def test_input_validation_and_external_error_redaction(self):
        for texts in ([], [''], ['x'] * 33, [123], ['\ud800'], ['\x00']):
            with self.subTest(texts=repr(texts)), self.assertRaises(TransportError):
                self.transport(response([[1, 0]])).embed(self.config, texts)
        def broken(*args): raise OSError('fixture-embedding-key raw provider secret')
        with self.assertRaises(TransportError) as caught:
            EmbeddingTransport(self.root, transport=broken).embed(self.config, ['甲'])
        self.assertNotIn('fixture-embedding-key', str(caught.exception))

    def test_stop_event_raised_during_provider_call_is_rechecked_before_publishing(self):
        stop = threading.Event()
        def request(config, method, path, body):
            stop.set()
            return deepcopy(response([[1, 0]]))
        with self.assertRaises(TransportError) as caught:
            EmbeddingTransport(self.root, transport=request).embed(self.config, ['甲'], stop_event=stop)
        self.assertEqual(caught.exception.code, 'cancelled')

    def test_local_missing_model_never_downloads_implicitly(self):
        with self.assertRaises(TransportError) as caught:
            EmbeddingTransport(self.root).embed({'provider': 'local_bge'}, ['短文本'])
        self.assertEqual(caught.exception.code, 'embedding_model_not_ready')

    def test_model_catalog_uses_embedding_scope_and_safe_paths(self):
        captured = []
        value = {'data': [{'id': 'BAAI/bge-m3'}, {'id': 'chat-only'}, {'id': 'bad id'}, 'junk']}
        siliconflow = {'provider': 'siliconflow', 'api_key': 'fixture-catalog-key'}
        result = self.transport(value, captured).models(siliconflow)
        self.assertEqual([row['id'] for row in result['models']], ['BAAI/bge-m3', 'chat-only'])
        self.assertEqual((captured[0][1], captured[0][2]), ('GET', '/models?sub_type=embedding'))
        self.assertIn('实际向量测试', result['notice'])
        local = EmbeddingTransport(self.root).models({'provider': 'local_bge'})
        self.assertEqual([row['id'] for row in local['models']], [LOCAL_MODEL])
        with self.assertRaises(TransportError):
            self.transport({'data': 'not-a-list'}).models({**self.config})

    def test_siliconflow_embedding_catalog_reaches_the_wire_through_safe_http(self):
        from ai_transport import AITransport
        captured = []
        def wire(config, method, path, body=None, stop_event=None):
            captured.append((method, path))
            return {'data': [{'id': 'BAAI/bge-m3'}]}
        transport = EmbeddingTransport(self.root, transport=AITransport(request=wire).request_json)
        result = transport.models({'provider': 'siliconflow', 'api_key': 'fixture-catalog-key'})
        self.assertEqual(captured, [('GET', '/models?sub_type=embedding')])
        self.assertEqual([row['id'] for row in result['models']], ['BAAI/bge-m3'])

    def test_huge_integer_component_is_a_controlled_transport_error(self):
        def request(config, method, path, body):
            return {'model': 'embed-model', 'data': [{'index': 0, 'embedding': [10 ** 1000, 1]}]}
        with self.assertRaises(TransportError) as caught:
            EmbeddingTransport(self.root, transport=request).embed(self.config, ['甲'])
        self.assertEqual(caught.exception.code, 'invalid_model_response')
        # Extreme-but-finite floats stay acceptable; only unrepresentable values are refused.
        result = self.transport(response([[1e308, 1e-308], [3, 4]])).embed(self.config, ['甲', '乙'])
        self.assertEqual(result['dimension'], 2)
        self.assertTrue(all(math.isfinite(value) for vector in result['vectors'] for value in vector))

    def test_presets_without_a_dynamic_catalog_return_an_honest_manual_contract(self):
        captured = []
        def request(config, method, path, body):
            captured.append((method, path, body))
            return {'data': [{'id': 'should-not-be-read'}]}
        transport = EmbeddingTransport(self.root, transport=request)
        for config in ({'provider': 'dashscope', 'api_key': 'fixture-key'},
                       {'provider': 'dashscope', 'api_key': 'fixture-key', 'model': 'text-embedding-v4'}):
            with self.subTest(model=config.get('model')):
                result = transport.models(config)
                self.assertEqual(result['discovery'], 'manual')
                self.assertEqual(result['models'], [])
                self.assertFalse(result['authentication_verified'])
                self.assertIn('手动填写', result['notice'])
        self.assertEqual(captured, [])

    def test_ollama_catalog_uses_the_root_tags_route_and_official_shape(self):
        from tests.test_ai_transport import Connection, Response
        payload = json.dumps({'models': [
            {'name': 'embeddinggemma:latest', 'model': 'embeddinggemma:latest', 'digest': 'sha256:abc'},
            {'name': 'bge-m3', 'model': 'bge-m3'},
            {'name': 'embeddinggemma:latest'},
            {'name': 'not a model!'},
            {'name': 7},
        ]}).encode()
        Connection.response = Response(payload)
        with patch('ai_transport._PinnedConnection', Connection):
            result = EmbeddingTransport(self.root).models({'provider': 'ollama'})
        self.assertEqual(Connection.last.request_args[0][:2], ('GET', '/api/tags'))
        self.assertEqual([row['id'] for row in result['models']], ['embeddinggemma:latest', 'bge-m3'])
        self.assertEqual(result['discovery'], 'remote')
        self.assertFalse(result['authentication_verified'])
        self.assertTrue(Connection.last.sock.closed.is_set())
        Connection.response = Response(b'{"models":"not-a-list"}')
        with patch('ai_transport._PinnedConnection', Connection), self.assertRaises(TransportError):
            EmbeddingTransport(self.root).models({'provider': 'ollama'})


if __name__ == '__main__': unittest.main()
