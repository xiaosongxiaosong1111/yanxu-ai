import tempfile
from pathlib import Path
import unittest

from research_store import ResearchStore, ResearchError


class ResearchStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'research.sqlite'
        self.store=ResearchStore(self.path)

    def test_task_events_are_incremental_and_mutations_do_not_alias(self):
        row=self.store.create({'query':'我想考计算机','profile':{},'unit_ids':[]},'queued')
        event=self.store.event(row['id'],'plan','研究计划','准备核对来源。')
        value=self.store.get(row['id']);self.assertEqual(len(value['events']),1)
        self.assertEqual(value['last_event_id'],event)
        self.assertEqual(self.store.get(row['id'],event)['events'],[])
        value['profile']['private']='x';self.assertEqual(self.store.get(row['id'])['profile'],{})

    def test_recovery_does_not_restart_billable_jobs(self):
        active=self.store.create({'query':'408','profile':{},'unit_ids':[]},'running')
        waiting=self.store.create({'query':'计算机','profile':{},'unit_ids':[]},'waiting_configuration')
        again=ResearchStore(self.path)
        self.assertEqual(again.get(active['id'])['status'],'interrupted')
        self.assertEqual(again.get(waiting['id'])['status'],'waiting_configuration')

    def test_unknown_identity_and_bad_cursor_rejected(self):
        with self.assertRaises(ResearchError):self.store.get('../secret')
        row=self.store.create({'query':'408','profile':{},'unit_ids':[]},'queued')
        with self.assertRaises(ResearchError):self.store.get(row['id'],True)
        with self.assertRaises(ResearchError):self.store.get('r_'+'0'*32)

    def test_listing_is_bounded_and_report_keeps_structured_data(self):
        row=self.store.create({'query':'408','profile':{},'unit_ids':[]},'queued')
        self.store.update(row['id'],status='completed',report={'candidates':[],'summary':'真实任务'})
        self.assertEqual(self.store.list(1)['tasks'][0]['report']['summary'],'真实任务')
        with self.assertRaises(ResearchError):self.store.list(101)

    def test_event_cursor_does_not_skip_pages_over_two_hundred(self):
        row=self.store.create({'query':'408'},'queued')
        for index in range(203):self.store.event(row['id'],'x','测试事件',str(index))
        first=self.store.get(row['id'])
        self.assertEqual(len(first['events']),200);self.assertTrue(first['has_more_events'])
        second=self.store.get(row['id'],first['last_event_id'])
        self.assertEqual(len(second['events']),3);self.assertFalse(second['has_more_events'])
        self.assertEqual(second['last_event_id'],second['latest_event_id'])
