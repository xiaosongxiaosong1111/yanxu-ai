"""Prepared failure-accounting contract probes, all provider requests are fakes."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from knowledge_store import KnowledgeError
from probe_fixtures import Fixture


class QueryAccounting(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='n4b-query-accounting-')
        self.addCleanup(temporary.cleanup)
        self.fixture=Fixture(Path(temporary.name)/'data')
        self.addCleanup(self.fixture.close)
        self.fixture.save();self.fixture.index.build()

    def accounting(self,error):
        value=getattr(error,'retrieval_usage',None)
        self.assertIsInstance(value,dict,'Failed semantic queries must preserve request-local accounting')
        encoded=json.dumps(value,ensure_ascii=False)
        self.assertNotIn('independent-fake-only',encoded)
        self.assertNotIn('private-request-canary',encoded)
        self.assertEqual(value['embedding_calls'],1)
        return value

    def test_known_usage_survives_ann_failure(self):
        def fail():raise OSError('internal backend private path')
        self.fixture.backend.on_search=fail
        with self.assertRaises(KnowledgeError) as caught:
            self.fixture.index.search('private-request-canary',year=2027,allow_remote=True)
        value=self.accounting(caught.exception)
        self.assertTrue(value['usage_known'])
        self.assertEqual(value['successful_embedding_calls'],1)
        self.assertEqual(value['input_tokens'],1)

    def test_provider_timeout_is_attempted_call_with_unknown_usage(self):
        self.fixture.embedding.failure=TimeoutError('provider failed before returning usage')
        with self.assertRaises(KnowledgeError) as caught:
            self.fixture.index.search('private-request-canary',year=2027,allow_remote=True)
        value=self.accounting(caught.exception)
        self.assertFalse(value['usage_known'])
        self.assertEqual(value['successful_embedding_calls'],0)
        self.assertIsNone(value['input_tokens'])
        self.assertEqual(self.fixture.backend.queries,[])

    def test_cancel_after_embedding_return_keeps_known_usage(self):
        event=threading.Event()
        self.fixture.embedding.callback=event.set
        with self.assertRaises(KnowledgeError) as caught:
            self.fixture.index.search('private-request-canary',year=2027,allow_remote=True,stop_event=event)
        value=self.accounting(caught.exception)
        self.assertTrue(value['usage_known'])
        self.assertEqual(value['input_tokens'],1)
        self.assertEqual(self.fixture.backend.queries,[])


if __name__=='__main__':
    report=Path(__file__).resolve().parents[4]/'data/runtime/node4/claude-b/report.json'
    if not report.is_file():raise SystemExit('Claude B report not submitted; probes are not running')
    unittest.main()
