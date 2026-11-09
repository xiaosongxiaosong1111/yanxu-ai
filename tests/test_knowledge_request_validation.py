"""Request-only SHA reuse must retain fresh source/generation checks at return."""
from contextlib import contextmanager
import asyncio
import hashlib
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from knowledge_retrieval import HybridRetriever, rule_rerank
from knowledge_store import KnowledgeError, KnowledgeStore
from tests.test_knowledge_hybrid import Store, record


class RequestValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='knowledge-request-')
        self.addCleanup(temporary.cleanup)
        self.store = KnowledgeStore(Path(temporary.name)/'data')
        self.raw = b'%PDF-1.4\nSYNTHETIC_REQUEST_FIXTURE\n'+b'x'*(256*1024)
        self.sha = hashlib.sha256(self.raw).hexdigest()
        self.pages = [{'page': n, 'text': ('2027年硕士研究生招生专业目录\n' if n == 1 else '')
                       + '081200计算机科学与技术 初试科目101、201、301、408。\n'
                       + '计算机招生说明。'*230+str(n)} for n in range(1, 33)]
        self.source = {'url': 'https://grad.example.edu.cn/catalog.pdf',
                       'title': '2027年硕士研究生招生专业目录', 'unit_id': 'u1',
                       'unit_name': '测试大学', 'year': 2027, 'requested_year': 2028,
                       'status': 'official_original_read', 'sha256': self.sha}
        self.document = {'url': self.source['url'], 'raw': self.raw, 'sha256': self.sha,
                         'attribution_status': 'verified', 'pages': self.pages,
                         'text': '\n'.join(page['text'] for page in self.pages)}
        self.saved = self.store.save_source(self.source, document=self.document)
        self.blob = self.store._blob_path(self.sha)
        self.retriever = HybridRetriever(self.store)

    def retrieve(self, **options):
        return self.retriever.retrieve('计算机', unit_id='u1', year=2027, limit=8, **options)

    @contextmanager
    def opened_blobs(self, paths=None):
        original = Path.open
        calls = []
        targets = paths or {self.blob}
        def tracked(path, *args, **kwargs):
            if path in targets and (args[0] if args else kwargs.get('mode', 'r')) == 'rb':
                calls.append((threading.get_ident(), path))
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', tracked):
            yield calls

    def mutate_at_rerank(self, mutate):
        def rerank(*args):
            result = rule_rerank(*args)
            mutate()
            return result
        with patch('knowledge_retrieval.rule_rerank', side_effect=rerank):
            return self.retrieve()

    def test_one_original_is_hashed_once_for_retrieval_and_once_before_return(self):
        with self.opened_blobs() as calls:
            result = self.retrieve()
        self.assertEqual(result['candidate_count'], 40)
        self.assertEqual(result['returned_count'], 8)
        self.assertEqual(len(calls), 2)

    def test_each_request_revalidates_the_original(self):
        with self.opened_blobs() as calls:
            self.retrieve()
            self.retrieve()
        self.assertEqual(len(calls), 4)
        self.blob.write_bytes(b'X'*len(self.raw))
        self.assertEqual(self.retrieve()['segments'], [])

    def test_each_original_and_only_the_returned_originals_are_rechecked(self):
        raw = self.raw+b'second-original'
        sha = hashlib.sha256(raw).hexdigest()
        url = 'https://grad.example.edu.cn/second-catalog.pdf'
        source = {**self.source, 'url': url, 'sha256': sha}
        self.store.save_source(source, document={**self.document, 'url': url, 'raw': raw, 'sha256': sha})
        second_blob = self.store._blob_path(sha)
        with self.opened_blobs({self.blob, second_blob}) as calls:
            result = self.retrieve()
        selected = {row['source']['sha256'] for row in result['segments']}
        self.assertEqual(len(calls), 2+len(selected))
        for digest, blob in ((self.sha, self.blob), (sha, second_blob)):
            self.assertEqual(sum(path == blob for _, path in calls), 2 if digest in selected else 1)

    def test_original_tampered_after_rerank_cannot_be_returned(self):
        result = self.mutate_at_rerank(lambda: self.blob.write_bytes(b'X'*len(self.raw)))
        self.assertEqual(result['segments'], [])

    def test_revoked_after_rerank_cannot_be_returned(self):
        result = self.mutate_at_rerank(lambda: self.store.change_validity(
            self.saved['id'], 'revoked', '隔离回归撤信，不能复用旧引文。'))
        self.assertEqual(result['segments'], [])

    def test_generation_switched_after_rerank_cannot_return_old_segments(self):
        old_generation = self.saved['extraction']['generation_id']
        pages = [{'page': 1, 'text': '2027年硕士研究生招生专业目录\n'
                  '081200计算机科学与技术 初试科目101、201、301、408。\n新的规范提取版本。'}]
        document = {**self.document, 'pages': pages, 'text': pages[0]['text']}
        result = self.mutate_at_rerank(lambda: self.store.save_source(self.source, document=document))
        current = self.store.get(self.saved['id'])
        self.assertNotEqual(current['extraction']['generation_id'], old_generation)
        self.assertEqual(result['segments'], [])

    def test_nested_scopes_restore_outer_cache_and_discard_on_exception(self):
        with self.opened_blobs() as calls:
            with self.store.validation_scope():
                self.assertTrue(self.store._blob_available(self.sha))
                with self.assertRaisesRegex(RuntimeError, 'scope fixture'):
                    with self.store.validation_scope():
                        self.assertTrue(self.store._blob_available(self.sha))
                        raise RuntimeError('scope fixture')
                self.assertTrue(self.store._blob_available(self.sha))
            self.assertTrue(self.store._blob_available(self.sha))
        self.assertEqual(len(calls), 3)

    def test_concurrent_scopes_do_not_share_validation_results(self):
        barrier = threading.Barrier(2)
        errors = []
        def worker():
            try:
                with self.store.validation_scope():
                    self.assertTrue(self.store._blob_available(self.sha))
                    barrier.wait(timeout=5)
                    self.assertTrue(self.store._blob_available(self.sha))
            except Exception as error:
                errors.append(error)
        with self.opened_blobs() as calls:
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(calls), 2)

    def test_async_task_scopes_do_not_share_validation_results(self):
        async def worker():
            with self.store.validation_scope():
                self.assertTrue(self.store._blob_available(self.sha))
                await asyncio.sleep(0)
                self.assertTrue(self.store._blob_available(self.sha))
        async def run():
            await asyncio.gather(worker(), worker())
        with self.opened_blobs() as calls:
            asyncio.run(run())
        self.assertEqual(len(calls), 2)

    def test_other_store_instances_cannot_reuse_this_request_validation(self):
        other = KnowledgeStore(self.store.data_dir)
        with self.store.validation_scope():
            self.assertTrue(self.store._blob_available(self.sha))
            self.blob.write_bytes(b'X'*len(self.raw))
            self.assertFalse(other._blob_available(self.sha))
        self.assertFalse(self.store._blob_available(self.sha))

    def test_digest_is_read_in_bounded_chunks_and_oversized_files_fail_closed(self):
        sizes = []
        class Stream(io.BytesIO):
            def read(self, size=-1):
                sizes.append(size)
                if not 0 < size <= 64*1024:
                    raise AssertionError('Original read allocation must stay within 64 KiB.')
                return super().read(size)
        original = Path.open
        def opened(path, *args, **kwargs):
            return Stream(self.raw) if path == self.blob else original(path, *args, **kwargs)
        with patch.object(Path, 'open', opened):
            self.assertTrue(self.store._blob_available(self.sha))
        self.assertGreater(len(sizes), 1)
        with self.blob.open('wb') as stream:stream.truncate(8*1024*1024+1)
        self.assertFalse(self.store._blob_available(self.sha))

    def test_file_growing_after_initial_stat_still_fails_the_read_limit(self):
        class GrowingStream:
            remaining = 8*1024*1024+1
            def __enter__(self):return self
            def __exit__(self, *args):return False
            def read(self, size=-1):
                self_size = min(size, self.remaining)
                self.remaining -= self_size
                return b'x'*self_size
        original = Path.open
        def opened(path, *args, **kwargs):
            return GrowingStream() if path == self.blob else original(path, *args, **kwargs)
        with patch.object(Path, 'open', opened):
            self.assertFalse(self.store._blob_available(self.sha))

    def test_fake_store_without_validation_context_remains_supported(self):
        row = record(1, '计算机招生')
        result = HybridRetriever(Store([row])).retrieve('计算机')
        self.assertEqual(result['returned_count'], 1)

    def test_fake_store_source_changed_during_rerank_is_rechecked(self):
        row = record(1, '计算机招生')
        store = Store([row])
        def rerank(*args):
            result = rule_rerank(*args)
            store.rows[row['id']]['source']['validity'] = 'revoked'
            return result
        with patch('knowledge_retrieval.rule_rerank', side_effect=rerank):
            result = HybridRetriever(store).retrieve('计算机')
        self.assertEqual(result['segments'], [])

    def test_cancellation_during_final_validation_is_not_returned(self):
        stop = threading.Event()
        original_get = self.store.get
        calls = []
        def get(identifier):
            calls.append(identifier)
            value = original_get(identifier)
            if len(calls) > 40:stop.set()
            return value
        with patch.object(self.store, 'get', side_effect=get), self.assertRaises(KnowledgeError):
            self.retrieve(stop_event=stop)
        self.assertTrue(stop.is_set())

    def test_final_metadata_read_failure_drops_previously_hydrated_results(self):
        original_get = self.store.get
        count = 0
        def get(identifier):
            nonlocal count
            count += 1
            if count > 40:raise OSError('private-path-canary')
            return original_get(identifier)
        with patch.object(self.store, 'get', side_effect=get):
            result = self.retrieve()
        self.assertEqual(result['segments'], [])
        self.assertNotIn('private-path-canary', str(result))


if __name__ == '__main__':
    unittest.main()
