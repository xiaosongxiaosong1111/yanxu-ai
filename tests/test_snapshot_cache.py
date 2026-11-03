import os
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch

from snapshot_cache import SnapshotCache


class SnapshotCacheTests(unittest.TestCase):
    def test_source_fingerprint_streams_bytes_without_allocating_the_complete_file(self):
        with patch.object(Path,'read_bytes',side_effect=MemoryError('complete source allocation')):
            fingerprint=self.cache._fingerprint((self.source,))
        self.assertEqual(fingerprint[0][-1],hashlib.sha256(b'source').hexdigest())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'data'
        self.root.mkdir()
        self.source = self.root / 'snapshots' / 'catalog.html'
        self.source.parent.mkdir()
        self.source.write_text('source', encoding='utf8')
        self.cache = SnapshotCache(self.root)
        self.load = Mock(return_value=({'units': []}, {'documents': [{'local_path': 'snapshots/catalog.html'}]}))
        self.verify = Mock(return_value={'release_ready': True})

    def test_directory_junction_chain_cannot_keep_a_trusted_revision(self):
        if os.name != 'nt': self.skipTest('Windows junction regression')
        target = self.root / 'target'
        self.source.parent.rename(target)
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(self.source.parent), str(target)], capture_output=True)
        if result.returncode: self.skipTest('Junction creation unavailable')
        self.addCleanup(lambda: self.source.parent.rmdir() if self.source.parent.is_junction() else None)
        self.cache.get(self.load, self.verify)
        old = self.cache._signature
        outside = Path(self.temp.name) / 'outside'
        target.rename(outside)
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(target), str(outside)], capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.addCleanup(lambda: target.rmdir() if target.is_junction() else None)
        self.assertNotEqual(old, self.cache._fingerprint(self.cache._paths))
        self.assertFalse(self.cache.status()['release_ready'])

    def test_dot_dependency_and_runtime_writes_do_not_crash_or_invalidate(self):
        self.load.return_value = ({'units': []}, {'documents': [{'local_path': '.'}]})
        self.cache.get(self.load, self.verify)
        runtime = self.root / 'runtime'
        runtime.mkdir()
        (runtime / 'radar.json').write_text('{}', encoding='utf8')
        self.assertTrue(self.cache.status()['release_ready'])
        self.cache.get(self.load, self.verify)
        self.assertEqual(self.verify.call_count, 1)

    def test_changed_while_verifying_retries_and_never_publishes_unstable_state(self):
        def unstable(*_):
            self.source.write_text(self.source.read_text(encoding='utf8') + 'x', encoding='utf8')
            return {'release_ready': True}
        with self.assertRaises(OSError): self.cache.get(self.load, unstable)
        self.assertFalse(self.cache.status()['release_ready'])
        value = self.cache.get(self.load, self.verify)
        self.assertTrue(value[2]['release_ready'])

    def test_health_is_nonblocking_during_a_cold_verification(self):
        with self.cache._lock:
            self.assertEqual(self.cache.status()['coverage_status'], 'checking')
        self.assertEqual(self.cache.status()['coverage_status'], 'unchecked')

    def test_base_json_bytes_are_checked_even_when_size_and_mtime_are_restored(self):
        path = self.root / 'units.json'
        path.write_bytes(b'{"name":"aaa"}')
        self.cache.get(self.load, self.verify)
        before = path.stat()
        with path.open('r+b') as stream:
            stream.write(b'{"name":"bbb"}')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertFalse(self.cache.status()['release_ready'])
        self.cache.get(self.load, self.verify)
        self.assertEqual(self.verify.call_count, 2)

    def test_published_scope_watches_only_its_originals_not_unpublished_national_json(self):
        cache = SnapshotCache(self.root, base_paths=())
        cache.get(self.load, self.verify)
        (self.root / 'admissions.json').write_text('{"pending":"not published"}', 'utf-8')
        cache.get(self.load, self.verify)
        self.assertEqual(self.verify.call_count, 1)
        before = self.source.stat()
        self.source.write_text('SOURCE', 'utf-8')
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        cache.get(self.load, self.verify)
        self.assertEqual(self.verify.call_count, 2)


if __name__ == '__main__':
    unittest.main()
