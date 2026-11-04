"""Additional independent B edge cases, fake providers and temporary data only."""
from contextlib import closing
from copy import copy
import logging
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import patch

from knowledge_store import KnowledgeError
import milvus_backend
from probe_fixtures import Fixture


class VectorRegressions(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='n4b-independent-regression-')
        self.addCleanup(temporary.cleanup)
        self.fixture=Fixture(Path(temporary.name)/'data')
        self.addCleanup(self.fixture.close)

    def served_ids(self):
        result=self.fixture.index.search('跨专业',year=2027,limit=100,allow_remote=True)
        return {row['id'] for row in result['segments']}

    def test_n3_save_does_not_implicitly_create_n4_tables(self):
        fixture=self.fixture;fixture.save()
        with closing(sqlite3.connect(fixture.store.path)) as connection:
            tables=[row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'knowledge_vector_%'")]
        self.assertEqual(tables,[])

    def test_same_space_cancel_preserves_all_previously_served_items(self):
        fixture=self.fixture;fixture.save();fixture.index.build()
        baseline=self.served_ids();active=fixture.index.status()['active_space_id']
        original=fixture.index.cache.get
        def cancel_during_cache(*args):
            vector=original(*args)
            fixture.index.cancel_build()
            return vector
        with patch.object(fixture.index.cache,'get',side_effect=cancel_during_cache):
            fixture.index.start_build()
            deadline=time.monotonic()+5
            while (fixture.index.status()['building'] or fixture.index._thread.is_alive()) and time.monotonic()<deadline:
                time.sleep(.02)
        self.assertEqual(fixture.index.status()['active_space_id'],active)
        self.assertEqual(self.served_ids(),baseline)

    def test_known_provider_usage_survives_cache_put_failure(self):
        fixture=self.fixture;fixture.save();fixture.index.build()
        with patch.object(fixture.index.cache,'get',return_value=None),patch.object(fixture.index.cache,'put',side_effect=OSError('fake disk full')):
            with self.assertRaises(KnowledgeError):fixture.index.build()
        status=fixture.index.status()
        with self.subTest('round-usage'):
            self.assertTrue(status['last_build']['usage_known'])
        with self.subTest('known-tokens'):
            self.assertEqual(status['last_build']['input_tokens'],1)
        with closing(sqlite3.connect(fixture.store.path)) as connection:
            rows=connection.execute("SELECT usage,usage_known FROM knowledge_vector_jobs WHERE state IN ('failed','uncertain')").fetchall()
        self.assertTrue(rows)
        with self.subTest('item-usage'):
            self.assertTrue(rows[0][1])

    def test_failed_same_space_rebuild_preserves_previously_served_items(self):
        fixture=self.fixture;fixture.save();fixture.index.build();baseline=self.served_ids()
        with patch.object(fixture.index.cache,'get',return_value=None),patch.object(fixture.index.cache,'put',side_effect=OSError('fake disk full')):
            with self.assertRaises(KnowledgeError):fixture.index.build()
        self.assertEqual(self.served_ids(),baseline)

    def mock_backend(self,refs,release):
        path=str(self.fixture.root/'runtime/vector-index/milvus.db')
        calls=[];shared={'client':SimpleNamespace(close=lambda:calls.append('client-close')),
                         'lock':threading.RLock(),'refs':refs}
        backend=milvus_backend.MilvusBackend.__new__(milvus_backend.MilvusBackend)
        backend._key=path;backend._closed=False;backend._shared=shared
        parent=ModuleType('milvus_lite');parent.__path__=[]
        child=ModuleType('milvus_lite.server_manager')
        child.server_manager_instance=SimpleNamespace(release_server=release,
            stop_all=lambda:(_ for _ in ()).throw(AssertionError('Do not stop unrelated servers')))
        return backend,shared,path,calls,{'milvus_lite':parent,'milvus_lite.server_manager':child}

    def test_last_ref_releases_only_its_database(self):
        released=[]
        backend,shared,path,calls,modules=self.mock_backend(2,released.append)
        other=copy(backend)
        with patch.dict(sys.modules,modules),patch.dict(milvus_backend._POOL,{path:shared},clear=True):
            backend.close()
            self.assertEqual(released,[]);self.assertEqual(calls,[])
            other.close()
        self.assertEqual(released,[path]);self.assertEqual(calls,['client-close'])

    def test_server_release_failure_is_visible(self):
        def fail(path):raise OSError('fake embedded lock release failure')
        backend,shared,path,calls,modules=self.mock_backend(1,fail)
        records=[]
        class Handler(logging.Handler):
            def emit(self,record):records.append(record)
        handler=Handler(level=logging.WARNING);logger=logging.getLogger();logger.addHandler(handler)
        raised=False;result=None
        try:
            with patch.dict(sys.modules,modules),patch.dict(milvus_backend._POOL,{path:shared},clear=True):
                try:result=backend.close()
                except Exception:raised=True
        finally:logger.removeHandler(handler)
        self.assertTrue(raised or records or result is False or isinstance(result,dict) and result.get('error_code'),
                        'The adapter silently swallowed its server release failure')


if __name__=='__main__':
    report=Path(__file__).resolve().parents[4]/'data/runtime/node4/claude-b/report.json'
    if not report.is_file():raise SystemExit('Claude B report not submitted; probes are not running')
    unittest.main()
