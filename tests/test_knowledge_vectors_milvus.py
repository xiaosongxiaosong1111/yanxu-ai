"""Real native Milvus Lite persistence and cross-process reopen; skipped without pymilvus."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
try:
    import pymilvus  # noqa: F401
    HAVE_MILVUS=True
except Exception:
    HAVE_MILVUS=False

from milvus_backend import MilvusBackend


def _space(tag,dimension=4):
    return {'id':'vs_'+tag,'dimension':dimension}


def _record(identifier,vector):
    return {'id':identifier,'vector':vector,'segment_id':'sg_fixture','entry_id':'k_fixture',
            'generation_id':'kg_fixture','unit_id':'u1','year':2027,'visibility':'public'}


CHILD_SCRIPT=(
    'import json,sys\n'
    'sys.path.insert(0,sys.argv[1])\n'
    'from milvus_backend import MilvusBackend\n'
    'data_dir,space_id,dimension=sys.argv[2],sys.argv[3],int(sys.argv[4])\n'
    'vector=json.loads(sys.argv[5]);identifiers=json.loads(sys.argv[6])\n'
    'backend=MilvusBackend(data_dir)\n'
    "rows=backend.search({'id':space_id,'dimension':dimension},vector,identifiers,len(identifiers))\n"
    "print('RESULT'+json.dumps(rows))\n"
)


@unittest.skipUnless(HAVE_MILVUS,'pymilvus is not installed in this environment')
class MilvusBackendTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='n4-milvus-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        scripts=tempfile.TemporaryDirectory(prefix='n4-milvus-scripts-');self.addCleanup(scripts.cleanup)
        self.script=Path(scripts.name)/'reopen_child.py'
        self.script.write_text(CHILD_SCRIPT,encoding='utf-8')

    def test_fixed_vectors_roundtrip_allowlist_and_contains(self):
        backend=MilvusBackend(self.root);self.addCleanup(backend.close)
        space=_space('a'*32)
        backend.ensure_space(space)
        first,second='vi_'+'1'*32,'vi_'+'2'*32
        backend.upsert(space,[_record(first,[1.0,0.0,0.0,0.0]),_record(second,[0.0,1.0,0.0,0.0])])
        self.assertEqual(backend.contains(space,[first,second]),{first,second})
        rows=backend.search(space,[0.9,0.1,0.0,0.0],[first,second],2)
        self.assertEqual([row['id'] for row in rows],[first,second])
        # allowlist applies before ranking: a missing id never comes back
        rows=backend.search(space,[0.0,1.0,0.0,0.0],[second],2)
        self.assertEqual([row['id'] for row in rows],[second])

    def test_second_process_reopens_released_collection_and_loads_before_query(self):
        space=_space('b'*32)
        first,second='vi_'+'3'*32,'vi_'+'4'*32
        vector=[0.6,0.8,0.0,0.0]
        backend=MilvusBackend(self.root)
        backend.ensure_space(space)
        backend.upsert(space,[_record(first,[1.0,0.0,0.0,0.0]),_record(second,[0.0,1.0,0.0,0.0])])
        expected=backend.search(space,vector,[first,second],2)
        backend.close()
        done=subprocess.run([sys.executable,'-X','utf8','-I',str(self.script),str(ROOT),str(self.root),
                             space['id'],'4',json.dumps(vector),json.dumps([first,second])],
                            capture_output=True,text=True,timeout=300,
                            env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'})
        self.assertEqual(done.returncode,0,done.stderr[-2000:])
        self.assertIn('RESULT',done.stdout)
        rows=json.loads(done.stdout.split('RESULT',1)[1])
        self.assertEqual([row['id'] for row in rows],[row['id'] for row in expected])
        self.assertGreater(rows[0]['score'],rows[-1]['score'])

    def test_database_stays_inside_the_project_data_directory(self):
        backend=MilvusBackend(self.root);self.addCleanup(backend.close)
        self.assertTrue(backend.path.resolve().is_relative_to(self.root.resolve()))


if __name__=='__main__':
    unittest.main()
