"""Real pinned local model inference; skipped automatically if the model is absent."""
from pathlib import Path
import math
import tempfile
import threading
import unittest

from embedding_transport import EmbeddingTransport, TransportError
from embedding_catalog import LOCAL_MODEL, LOCAL_REVISION

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / 'data/runtime/embedding-models/bge-small-zh-v1.5' / LOCAL_REVISION
CONFIG = {'provider': 'local_bge'}


@unittest.skipUnless(MODEL_DIR.is_dir(), 'pinned local model not prepared in this workspace')
class LocalEmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.transport = EmbeddingTransport(ROOT / 'data')

    def test_real_vectors_are_normalized_deterministic_and_identity_bound(self):
        first = self.transport.embed(CONFIG, ['跨专业考生须加试两门本科主干课程。', '本专业不含定向就业考生。'])
        self.assertEqual(first['dimension'], 512)
        self.assertEqual(first['model'], LOCAL_MODEL)
        self.assertEqual(first['input_count'], 2)
        for vector in first['vectors']:
            self.assertEqual(len(vector), 512)
            self.assertTrue(all(type(value) is float and math.isfinite(value) for value in vector))
            self.assertAlmostEqual(math.sqrt(sum(value*value for value in vector)), 1.0, places=6)
        second = self.transport.embed(CONFIG, ['跨专业考生须加试两门本科主干课程。'])
        self.assertEqual(second['vectors'][0][:8], first['vectors'][0][:8])
        other = self.transport.embed(CONFIG, ['背景：跨专业考生须加试两门本科主干课程。'])
        self.assertNotEqual(other['vectors'][0][:8], first['vectors'][0][:8])

    def test_query_and_document_purposes_both_work_with_prefixes(self):
        config = {**CONFIG, 'document_prefix': '正文：', 'query_prefix': '检索：'}
        document = self.transport.embed(config, ['初试科目为101、201、301、408。'], purpose='document')
        query = self.transport.embed(config, ['408 考什么科目'], purpose='query')
        self.assertEqual((document['dimension'], query['dimension']), (512, 512))
        self.assertTrue(document['usage_known'] and document['usage']['input_tokens'] > 0)

    def test_missing_model_never_downloads_and_reports_not_ready(self):
        with tempfile.TemporaryDirectory(prefix='n4-empty-model-') as root:
            transport = EmbeddingTransport(root)
            with self.assertRaises(TransportError) as caught:
                transport.embed(CONFIG, ['短文本'])
            self.assertEqual(caught.exception.code, 'embedding_model_not_ready')

    def test_corrupt_or_truncated_model_files_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='n4-bad-model-') as root:
            target = Path(root)/'runtime/embedding-models/bge-small-zh-v1.5'/LOCAL_REVISION
            target.mkdir(parents=True)
            (target/'model_optimized.onnx').write_bytes(b'not-an-onnx-model')
            for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json'):
                (target/name).write_text('{}', encoding='utf-8')
            with self.assertRaises(TransportError) as caught:
                EmbeddingTransport(root).embed(CONFIG, ['短文本'])
            self.assertIn(caught.exception.code, {'embedding_model_invalid', 'embedding_model_not_ready'})

    def test_oversized_single_input_is_rejected_not_silently_truncated(self):
        with self.assertRaises(TransportError) as caught:
            self.transport.embed(CONFIG, ['初试科目设定。'*600])
        self.assertEqual(caught.exception.code, 'embedding_input_too_long')

    def test_stop_event_cancels_without_calling_the_model(self):
        stop = threading.Event(); stop.set()
        with self.assertRaises(TransportError) as caught:
            self.transport.embed(CONFIG, ['短文本'], stop_event=stop)
        self.assertEqual(caught.exception.code, 'cancelled')

    def test_auxiliary_files_tampered_after_first_use_are_rejected(self):
        """The verification cache must notice changes to tokenizer/config files, not only
        to the ONNX weights."""
        import shutil
        with tempfile.TemporaryDirectory(prefix='n4-tamper-model-') as root:
            target = Path(root)/'runtime/embedding-models/bge-small-zh-v1.5'/LOCAL_REVISION
            target.mkdir(parents=True)
            for item in MODEL_DIR.iterdir():
                shutil.copy2(item, target/item.name)
            transport = EmbeddingTransport(root)
            first = transport.embed(CONFIG, ['短文本'])
            self.assertEqual(first['dimension'], 512)
            tokenizer = target/'tokenizer.json'
            tokenizer.write_text(tokenizer.read_text(encoding='utf-8')+'\n', encoding='utf-8')
            with self.assertRaises(TransportError) as caught:
                transport.embed(CONFIG, ['短文本'])
            self.assertEqual(caught.exception.code, 'embedding_model_invalid')


if __name__ == '__main__':
    unittest.main()
