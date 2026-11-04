"""Explicit local model preparation is pinned, verified, atomic and offline for status."""
import hashlib
import io
from pathlib import Path
import tempfile
import threading
import unittest
import unittest.mock

import embedding_prepare
from embedding_catalog import LOCAL_REVISION, MODEL_SIZE
from embedding_prepare import MODEL_SPEC, ModelPreparer, PrepareError


def digest(data, algorithm):
    return hashlib.new(algorithm, data).hexdigest()


class Response:
    def __init__(self, data):
        self._stream = io.BytesIO(data); self.closed = False
    def read(self, size):
        return self._stream.read(size)
    def close(self):
        self.closed = True


class Opener:
    def __init__(self, files, block=None):
        self.files = dict(files); self.block = block; self.urls = []; self.started = threading.Event()
    def __call__(self, url):
        self.urls.append(url)
        if self.block is not None:
            self.started.set(); self.block.wait(5)
        name = url.rsplit('/', 1)[-1]
        if name not in self.files:
            raise OSError('private-network-canary')
        return Response(self.files[name])


class PinnedSpecTests(unittest.TestCase):
    def test_public_revision_and_model_bytes_are_pinned(self):
        self.assertEqual(LOCAL_REVISION, '46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59')
        self.assertEqual(MODEL_SPEC['model_optimized.onnx']['size'], MODEL_SIZE)
        self.assertEqual(MODEL_SPEC['model_optimized.onnx']['sha256'],
                         '1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38')
        self.assertTrue(embedding_prepare.MODEL_BASE_URL.startswith(
            'https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/' + LOCAL_REVISION + '/'))
        self.assertEqual(set(MODEL_SPEC), set(embedding_prepare.MODEL_FILE_ORDER))
        self.assertNotIn('vocab.txt', MODEL_SPEC)


class PreparerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='n4-prepare-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / 'data'
        self.files = {'tiny.onnx': b'MODEL-BYTES', 'config.json': b'{}'}
        self.spec = {
            'tiny.onnx': {'size': len(self.files['tiny.onnx']),
                          'sha1': digest(self.files['tiny.onnx'], 'sha1'), 'sha256': None},
            'config.json': {'size': len(self.files['config.json']),
                            'sha1': digest(self.files['config.json'], 'sha1'), 'sha256': None},
        }
        self._saved = (embedding_prepare.MODEL_SPEC, embedding_prepare.MODEL_FILE_ORDER)
        embedding_prepare.MODEL_SPEC = self.spec
        embedding_prepare.MODEL_FILE_ORDER = ('tiny.onnx', 'config.json')
        self.addCleanup(self.restore)
        embedding_prepare._SIGNATURES.clear()

    def restore(self):
        embedding_prepare.MODEL_SPEC, embedding_prepare.MODEL_FILE_ORDER = self._saved
        embedding_prepare._SIGNATURES.clear()

    def test_status_is_read_only_and_prepare_publishes_once(self):
        opener = Opener(self.files)
        preparer = ModelPreparer(self.data, opener=opener)
        first = preparer.status()
        self.assertEqual(first['state'], 'not_prepared'); self.assertFalse(first['prepared'])
        self.assertEqual(opener.urls, [])
        result = preparer.prepare()
        self.assertEqual(result['state'], 'ready'); self.assertFalse(result['reused'])
        self.assertTrue(preparer.root.is_dir())
        self.assertEqual(sorted(path.name for path in preparer.root.iterdir()),
                         ['config.json', 'tiny.onnx'])
        self.assertEqual(len(opener.urls), 2)
        self.assertTrue(all(url.startswith(embedding_prepare.MODEL_BASE_URL) for url in opener.urls))
        again = preparer.prepare()
        self.assertTrue(again['reused']); self.assertEqual(len(opener.urls), 2)
        self.assertEqual(preparer.status()['files_verified'], 2)

    def test_bad_size_or_digest_never_publishes_partial_model(self):
        opener = Opener({'tiny.onnx': b'SHORT', 'config.json': b'{}'})
        preparer = ModelPreparer(self.data, opener=opener)
        with self.assertRaises(PrepareError) as raised:
            preparer.prepare()
        self.assertEqual(raised.exception.code, 'embedding_model_invalid')
        self.assertFalse(preparer.root.exists())
        self.assertEqual([path for path in preparer.root.parent.iterdir() if path.name.startswith('.tmp-')], [])
        self.assertEqual(preparer.status()['state'], 'failed')
        self.assertEqual(preparer.status()['error_code'], 'embedding_model_invalid')

    def test_failed_download_is_safe_and_existing_files_are_not_destroyed(self):
        preparer = ModelPreparer(self.data, opener=Opener({}))
        preparer.root.mkdir(parents=True)
        (preparer.root / 'tiny.onnx').write_bytes(b'EXISTING-CORRUPT')
        before = sorted((path.name, path.read_bytes()) for path in preparer.root.iterdir())
        with self.assertRaises(PrepareError) as raised:
            preparer.prepare()
        self.assertEqual(raised.exception.code, 'embedding_model_download_failed')
        self.assertNotIn('private-network-canary', str(raised.exception))
        after = sorted((path.name, path.read_bytes()) for path in preparer.root.iterdir())
        self.assertEqual(before, after)

    def test_verified_model_reuses_without_network_even_if_opener_is_broken(self):
        self.assertEqual(ModelPreparer(self.data, opener=Opener(self.files)).prepare()['state'], 'ready')
        broken = ModelPreparer(self.data, opener=Opener({}))
        result = broken.prepare()
        self.assertTrue(result['reused'])
        self.assertEqual(result['state'], 'ready')

    def test_existing_corrupt_directory_is_reported_invalid(self):
        preparer = ModelPreparer(self.data, opener=Opener({}))
        preparer.root.mkdir(parents=True)
        (preparer.root / 'tiny.onnx').write_bytes(b'CORRUPT')
        (preparer.root / 'config.json').write_bytes(b'{}')
        status = preparer.status()
        self.assertEqual(status['state'], 'invalid')
        self.assertEqual(status['error_code'], 'embedding_model_invalid')

    def test_async_prepare_is_single_flight_and_visible_while_running(self):
        block = threading.Event(); opener = Opener(self.files, block=block)
        preparer = ModelPreparer(self.data, opener=opener)
        started = preparer.start_prepare()
        self.assertTrue(started['started'])
        self.assertTrue(opener.started.wait(3))
        self.assertEqual(preparer.status()['state'], 'preparing')
        self.assertFalse(preparer.start_prepare()['started'])
        block.set(); preparer._thread.join(5)
        self.assertEqual(preparer.status()['state'], 'ready')
        self.assertEqual(len(opener.urls), 2)

    def test_repository_disallows_escaping_the_data_directory(self):
        with unittest.mock.patch('embedding_prepare.model_dir', return_value=Path(tempfile.gettempdir()) / 'outside-model'):
            with self.assertRaises(ValueError):
                ModelPreparer(self.data)


if __name__ == '__main__':
    unittest.main()
