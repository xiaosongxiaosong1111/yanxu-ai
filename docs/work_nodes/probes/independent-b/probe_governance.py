"""Prepared independent B probes. Run only after the Claude B report is submitted."""
from contextlib import closing
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from knowledge_store import KnowledgeError
from probe_fixtures import Fixture


class IndependentVectorGovernance(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='n4b-independent-')
        self.addCleanup(temporary.cleanup)
        self.fixture=Fixture(Path(temporary.name)/'data')
        self.addCleanup(self.fixture.close)

    def test_cold_status_and_dirty_helpers_do_not_create_a_runtime_database(self):
        fixture=self.fixture
        self.assertFalse(fixture.store.path.exists())
        fixture.index.status()
        self.assertFalse(fixture.store.path.exists())
        self.assertEqual(fixture.embedding.calls,[])
        self.assertEqual(fixture.backend.queries,[])

    def test_same_model_key_rotation_adopts_new_ep_only_after_success(self):
        fixture=self.fixture;fixture.save();fixture.index.build()
        old=fixture.index.status()['active_profile_id']
        fingerprint=fixture.embedding.profile(fixture.settings.current)['fingerprint']
        second_fixture_key = 'second-independent-fake-only'
        fixture.settings.rotate(api_key=second_fixture_key)
        self.assertEqual(fixture.embedding.profile(fixture.settings.current)['fingerprint'],fingerprint)
        with patch.object(fixture.backend,'contains',side_effect=OSError('independent store read failure')):
            with self.assertRaises(KnowledgeError):fixture.index.build()
        self.assertEqual(fixture.index.status()['active_profile_id'],old)
        fixture.index.build()
        self.assertEqual(fixture.index.status()['active_profile_id'],fixture.settings.current['profile_id'])
        fixture.index.search('跨专业',unit_id='unit-a',year=2027,allow_remote=True)
        self.assertEqual(fixture.embedding.calls[-1]['api_key'],'second-independent-fake-only')
        document_calls=[value for value in fixture.embedding.calls if value['purpose']=='document']
        text_calls=[text for value in document_calls for text in value['texts']]
        self.assertEqual(len(text_calls),len(set(text_calls)))

    def test_model_switch_failure_keeps_old_query_model_and_space(self):
        fixture=self.fixture;fixture.save();fixture.index.build()
        old=fixture.index.status()
        fixture.settings.rotate(model='independent-model-v2',dimension=4)
        fixture.backend.fail=True
        with self.assertRaises(KnowledgeError):fixture.index.build()
        fixture.backend.fail=False
        fixture.index.search('跨专业',year=2027,allow_remote=True)
        self.assertEqual(fixture.index.status()['active_space_id'],old['active_space_id'])
        self.assertEqual(fixture.embedding.calls[-1]['profile_id'],old['active_profile_id'])

    def test_remote_get_guard_and_empty_scope_make_no_provider_call(self):
        fixture=self.fixture;fixture.save();fixture.index.build();count=len(fixture.embedding.calls)
        with self.assertRaises(KnowledgeError) as error:fixture.index.search('跨专业',year=2027)
        self.assertEqual(error.exception.code,'vector_remote_requires_action')
        self.assertEqual(fixture.index.search('跨专业',unit_id='missing',year=2027)['segments'],[])
        self.assertEqual(len(fixture.embedding.calls),count)
        self.assertEqual(fixture.backend.queries,[])

    def test_old_year_school_and_same_length_tamper_never_enter_ann_allowlist(self):
        fixture=self.fixture;wanted=fixture.save()
        other=fixture.save('wrong-unit',unit='unit-b')
        older=fixture.save('wrong-year',year=2026)
        revoked=fixture.save('revoked')
        tampered=fixture.save('tampered')
        fixture.index.build()
        fixture.store.change_validity(revoked['id'],'revoked','独立撤信验收')
        path=fixture.root/'runtime/research-sources'/(tampered['sha256']+'.bin')
        raw=path.read_bytes();path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        result=fixture.index.search('跨专业',unit_id='unit-a',year=2027,limit=1,allow_remote=True)
        self.assertEqual([value['entry_id'] for value in result['segments']],[wanted['id']])
        with closing(sqlite3.connect(fixture.store.path)) as connection:
            ids={row[0] for row in connection.execute('SELECT item_id FROM knowledge_vector_items WHERE entry_id=?',(wanted['id'],))}
        self.assertTrue(fixture.backend.queries)
        self.assertTrue(all(set(batch)<=ids for batch in fixture.backend.queries))

    def test_revocation_after_ann_is_rechecked_before_return(self):
        fixture=self.fixture;wanted=fixture.save();fixture.index.build()
        fixture.backend.on_search=lambda:fixture.store.change_validity(wanted['id'],'revoked','检索期间撤信')
        result=fixture.index.search('跨专业',year=2027,allow_remote=True)
        self.assertEqual(result['segments'],[])

    def test_dirty_after_explicit_n3_resegmentation(self):
        fixture=self.fixture;wanted=fixture.save();fixture.index.build()
        self.assertFalse(fixture.index.status()['dirty'])
        with patch('knowledge_repository.SEGMENT_VERSION','independent-n4b-resegment'):
            fixture.store.rebuild_segments(wanted['id'])
        self.assertTrue(fixture.index.status()['dirty'])
        result=fixture.index.search('跨专业',year=2027,allow_remote=True)
        self.assertEqual(result['segments'],[])

    def test_timeout_keeps_unknown_usage_in_build_summary(self):
        fixture=self.fixture;fixture.save();fixture.embedding.failure=TimeoutError('independent-timeout')
        with self.assertRaises(KnowledgeError):fixture.index.build()
        status=fixture.index.status()
        self.assertFalse(status['last_build']['usage_known'])
        self.assertGreater(status['jobs'].get('uncertain',0),0)

    def test_server_cancel_blocks_late_embedding_publication(self):
        fixture=self.fixture;fixture.save();fixture.embedding.block=True
        response=fixture.index.start_build()
        self.assertTrue(response['started'])
        self.assertTrue(fixture.embedding.entered.wait(5))
        fixture.index.cancel_build()
        fixture.embedding.release.set()
        deadline=time.monotonic()+5
        while fixture.index.status()['building'] and time.monotonic()<deadline:time.sleep(.02)
        status=fixture.index.status()
        self.assertFalse(status['building'])
        self.assertIsNone(status['active_space_id'])


if __name__=='__main__':
    root=Path(__file__).resolve().parents[4]
    report=root/'data/runtime/node4/claude-b/report.json'
    if not report.is_file():raise SystemExit('Claude B report not submitted; independent probes are not running.')
    unittest.main()
