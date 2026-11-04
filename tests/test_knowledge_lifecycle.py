"""Temporary SQLite lifecycle and index-job checks; no user data or providers."""
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_lifecycle import (LifecycleError, change_state, claim_job, fail_job,
                                 finish_job, inspect_events, inspect_jobs, queue_job,
                                 select_current, state_for)
from knowledge_schema import ensure_schema


ENTRY = 'k_' + 'a' * 24
SECOND = 'k_' + 'b' * 24
EXTRACTION = 'ex_' + 'a' * 32
OLD_GENERATION = 'kg_' + '0' * 32
GENERATION = 'kg_' + 'a' * 32


class KnowledgeLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='n3-lifecycle-')
        self.addCleanup(temporary.cleanup)
        self.connection = sqlite3.connect(Path(temporary.name) / 'fixture.sqlite')
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row
        with self.connection:
            ensure_schema(self.connection)
            self.source(ENTRY)
            self.extraction()

    def source(self, identifier, *, family='family-a', unit='u1', year=2027,
               quality='accepted', status='official_original_read'):
        payload = json.dumps({'quality': {'version': 1, 'state': quality,
                              'reusable': quality == 'accepted', 'reasons': []}})
        self.connection.execute('''INSERT INTO knowledge_entries
            (id,family,version_key,url,unit_id,unit_name,year,requested_year,status,title,
             sha256,full_text,pages,first_seen_at,last_seen_at,payload,quality)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (identifier, family, identifier, 'https://grad.example.edu.cn/fixture', unit,
             '临时合成学校', year, 2028, status, '2027年硕士招生专业目录', 'a' * 64,
             '合成公开目录', '[]', '2026-11-03', '2026-11-03', payload, quality))

    def extraction(self, identifier=EXTRACTION, entry_id=ENTRY, version='parser-v1'):
        self.connection.execute('''INSERT INTO knowledge_extractions
            (id,entry_id,raw_sha,extractor_version,text_sha256,text,pages,metadata,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)''',
            (identifier, entry_id, 'a' * 64, version, 'b' * 64, '合成公开条款', '[]', '{}', 'now'))

    def generation(self, identifier=GENERATION, *, entry=ENTRY, extraction=EXTRACTION,
                   version='structure-v1', state='running'):
        self.connection.execute('''INSERT INTO knowledge_generations
            (id,entry_id,extraction_id,segmenter_version,state,created_at) VALUES(?,?,?,?,?,?)''',
            (identifier, entry, extraction, version, state, 'now'))

    def queued(self):
        result = queue_job(self.connection, ENTRY, EXTRACTION, 'structure-v1')
        self.assertEqual(result.get('state'), 'pending')
        self.assertEqual(result.get('attempts'), 0)
        self.assertRegex(result.get('id', ''), r'^kj_[0-9a-f]{32}$')
        return result

    def running(self):
        queued = self.queued()
        running = claim_job(self.connection, queued['id'])
        self.assertEqual(running.get('state'), 'running')
        self.assertEqual(running.get('attempts'), 1)
        return running

    def test_revocation_preserves_quality_acquisition_and_auditable_generation(self):
        self.generation(OLD_GENERATION, version='structure-v0', state='succeeded')
        self.connection.execute('INSERT INTO knowledge_entry_state VALUES(?,?,?,?,?,?)',
                                (ENTRY, 'active', '', None, OLD_GENERATION, 'now'))
        result = change_state(self.connection, ENTRY, 'revoked', '原文已撤回')
        self.assertEqual(result.get('validity'), 'revoked')
        self.assertEqual(result.get('current_generation_id'), OLD_GENERATION)
        source = self.connection.execute('SELECT * FROM knowledge_entries WHERE id=?', (ENTRY,)).fetchone()
        self.assertEqual(source['quality'], 'accepted')
        self.assertEqual(source['status'], 'official_original_read')
        events = inspect_events(self.connection, ENTRY)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['previous_state'], 'active')
        self.assertEqual(events[0]['new_state'], 'revoked')

    def test_repeating_the_same_state_and_reason_is_idempotent(self):
        change_state(self.connection, ENTRY, 'revoked', '撤回')
        change_state(self.connection, ENTRY, 'revoked', '撤回')
        self.assertEqual(len(inspect_events(self.connection, ENTRY)), 1)
        change_state(self.connection, ENTRY, 'revoked', '补充撤回理由')
        self.assertEqual(len(inspect_events(self.connection, ENTRY)), 2)

    def test_pending_cannot_be_activated_or_selected(self):
        self.connection.execute("UPDATE knowledge_entries SET quality='pending' WHERE id=?", (ENTRY,))
        for action in [lambda: change_state(self.connection, ENTRY, 'active', '已确认'),
                       lambda: select_current(self.connection, ENTRY, '采用此版本'),
                       lambda: queue_job(self.connection, ENTRY, EXTRACTION, 'structure-v1')]:
            with self.subTest(action=action), self.assertRaises(LifecycleError):
                action()

    def test_accepted_flag_cannot_replace_missing_source_identity(self):
        for field in ['unit_id', 'family', 'url']:
            with self.subTest(field=field):
                original = self.connection.execute(f'SELECT {field} FROM knowledge_entries WHERE id=?', (ENTRY,)).fetchone()[0]
                self.connection.execute(f'UPDATE knowledge_entries SET {field}=? WHERE id=?', ('', ENTRY))
                with self.assertRaises(LifecycleError):
                    select_current(self.connection, ENTRY, '原文身份须明确')
                self.connection.execute(f'UPDATE knowledge_entries SET {field}=? WHERE id=?', (original, ENTRY))

    def test_supersession_requires_the_same_explicit_year_unit_and_family(self):
        self.source(SECOND)
        for field, value in [('year', 2026), ('year', None), ('unit_id', 'other'), ('family', 'other')]:
            with self.subTest(field=field, value=value):
                self.connection.execute(f'UPDATE knowledge_entries SET {field}=? WHERE id=?', (value, SECOND))
                with self.assertRaises(LifecycleError):
                    change_state(self.connection, ENTRY, 'superseded', '采用已审新版本', SECOND)
                self.connection.execute('UPDATE knowledge_entries SET year=2027,unit_id=\'u1\',family=\'family-a\' WHERE id=?', (SECOND,))
        result = change_state(self.connection, ENTRY, 'superseded', '采用已审新版本', SECOND)
        self.assertEqual(result.get('validity'), 'superseded')
        self.assertEqual(result.get('replaced_by'), SECOND)

    def test_explicit_selection_marks_competing_version_superseded(self):
        self.source(SECOND)
        change_state(self.connection, ENTRY, 'conflict', '两版内容冲突')
        result = select_current(self.connection, ENTRY, '人工核对采用原版')
        self.assertEqual(result.get('validity'), 'active')
        other = state_for(self.connection, SECOND)
        self.assertEqual(other.get('validity'), 'superseded')
        self.assertEqual(other.get('replaced_by'), ENTRY)

    def test_selection_does_not_overwrite_an_independent_revocation(self):
        self.source(SECOND)
        change_state(self.connection, SECOND, 'revoked', '该版本已撤回')
        select_current(self.connection, ENTRY, '保留此版本')
        self.assertEqual(state_for(self.connection, SECOND)['validity'], 'revoked')

    def test_restore_cannot_silently_create_two_active_versions(self):
        self.source(SECOND)
        change_state(self.connection, ENTRY, 'revoked', '先撤回')
        with self.assertRaises(LifecycleError):
            change_state(self.connection, ENTRY, 'active', '直接恢复')
        self.assertEqual(state_for(self.connection, ENTRY)['validity'], 'revoked')

    def test_queue_claim_and_repeat_queue_are_idempotent(self):
        first = self.queued()
        second = queue_job(self.connection, ENTRY, EXTRACTION, 'structure-v1')
        self.assertEqual(first['id'], second.get('id'))
        claimed = claim_job(self.connection, first['id'])
        self.assertEqual(claimed.get('attempts'), 1)
        with self.assertRaises(LifecycleError):
            claim_job(self.connection, first['id'])
        self.assertEqual(len(inspect_jobs(self.connection, ENTRY)), 1)

    def test_failed_retry_has_a_new_attempt_and_rejects_stale_finish(self):
        job = self.running()
        failed = fail_job(self.connection, job['id'], 'segment_failed', attempt=1)
        self.assertEqual(failed.get('state'), 'failed')
        queued = queue_job(self.connection, ENTRY, EXTRACTION, 'structure-v1', retry=True)
        self.assertEqual(queued.get('state'), 'pending')
        self.assertEqual(queued.get('attempts'), 1)
        current = claim_job(self.connection, job['id'])
        self.assertEqual(current.get('attempts'), 2)
        self.generation()
        with self.assertRaises(LifecycleError):
            finish_job(self.connection, job['id'], GENERATION, attempt=1)
        done = finish_job(self.connection, job['id'], GENERATION, attempt=2)
        self.assertEqual(done.get('state'), 'succeeded')
        self.assertEqual(state_for(self.connection, ENTRY)['current_generation_id'], GENERATION)

    def test_revocation_cancels_running_job_and_prevents_completion(self):
        job = self.running()
        self.generation()
        change_state(self.connection, ENTRY, 'revoked', '原文失效')
        self.assertEqual(inspect_jobs(self.connection, ENTRY)[0]['state'], 'cancelled')
        with self.assertRaises(LifecycleError):
            finish_job(self.connection, job['id'], GENERATION, attempt=1)
        self.assertEqual(state_for(self.connection, ENTRY)['validity'], 'revoked')
        self.assertIsNone(state_for(self.connection, ENTRY)['current_generation_id'])

    def test_restore_does_not_allow_a_cancelled_attempt_to_complete(self):
        job = self.running()
        self.generation()
        change_state(self.connection, ENTRY, 'revoked', '暂停')
        change_state(self.connection, ENTRY, 'active', '重新核验通过')
        with self.assertRaises(LifecycleError):
            finish_job(self.connection, job['id'], GENERATION, attempt=1)
        self.assertIsNone(state_for(self.connection, ENTRY)['current_generation_id'])

    def test_failed_generation_keeps_previous_successful_pointer(self):
        self.generation(OLD_GENERATION, version='structure-v0', state='succeeded')
        self.connection.execute('INSERT INTO knowledge_entry_state VALUES(?,?,?,?,?,?)',
                                (ENTRY, 'active', '', None, OLD_GENERATION, 'now'))
        job = self.running()
        self.generation()
        fail_job(self.connection, job['id'], 'segment_failed', attempt=1)
        self.assertEqual(state_for(self.connection, ENTRY)['current_generation_id'], OLD_GENERATION)
        self.assertEqual(self.connection.execute('SELECT state FROM knowledge_generations WHERE id=?', (GENERATION,)).fetchone()[0], 'failed')

    def test_old_success_replay_does_not_rewind_a_newer_generation(self):
        job = self.running();self.generation()
        finish_job(self.connection, job['id'], GENERATION, attempt=1)
        second_extraction = 'ex_' + 'b' * 32
        second_generation = 'kg_' + 'b' * 32
        self.extraction(second_extraction, version='parser-v2')
        queued = queue_job(self.connection, ENTRY, second_extraction, 'structure-v1')
        second = claim_job(self.connection, queued['id'])
        self.generation(second_generation, extraction=second_extraction)
        finish_job(self.connection, second['id'], second_generation, attempt=second['attempts'])
        finish_job(self.connection, job['id'], GENERATION, attempt=1)
        self.assertEqual(state_for(self.connection, ENTRY)['current_generation_id'], second_generation)

    def test_job_cannot_borrow_another_entrys_extraction_or_generation(self):
        self.source(SECOND)
        with self.assertRaises(LifecycleError):
            queue_job(self.connection, SECOND, EXTRACTION, 'structure-v1')
        job = self.running()
        self.generation(entry=SECOND)
        with self.assertRaises(LifecycleError):
            finish_job(self.connection, job['id'], GENERATION, attempt=1)

    def test_mutations_do_not_commit_the_callers_transaction(self):
        self.connection.commit()
        self.connection.execute('''CREATE TRIGGER fail_audit BEFORE INSERT ON knowledge_state_events
                                  BEGIN SELECT RAISE(ABORT, 'fixture abort'); END''')
        self.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.connection:
                change_state(self.connection, ENTRY, 'revoked', '审计写入失败')
        self.assertEqual(state_for(self.connection, ENTRY)['validity'], 'active')

    def test_invalid_reasons_ids_states_and_error_codes_are_rejected(self):
        for reason in ['', '  ', 'x' * 501, 'secret_key=private-canary', 'TC3-HMAC-SHA256 Credential=fixture']:
            with self.subTest(reason=reason), self.assertRaises(LifecycleError):
                change_state(self.connection, ENTRY, 'revoked', reason)
        for identifier in ['../data', None, 'k_bad']:
            with self.subTest(identifier=identifier), self.assertRaises(LifecycleError):
                state_for(self.connection, identifier)
        with self.assertRaises(LifecycleError):
            change_state(self.connection, ENTRY, 'trusted', '无效状态')
        job = self.running()
        for code in ['../private/path', 'Bearer private-token', 'x' * 100]:
            with self.subTest(code=code), self.assertRaises(LifecycleError):
                fail_job(self.connection, job['id'], code, attempt=1)

    def test_inspection_limits_are_bounded_and_unknown_entry_is_explicit(self):
        self.queued()
        for limit in [0, -1, True, 101]:
            with self.subTest(limit=limit), self.assertRaises(LifecycleError):
                inspect_jobs(self.connection, ENTRY, limit=limit)
        with self.assertRaises(LifecycleError) as caught:
            state_for(self.connection, SECOND)
        self.assertEqual(caught.exception.status, 404)

    def test_legacy_state_read_is_read_only_and_does_not_create_schema(self):
        with closing(sqlite3.connect(':memory:')) as legacy:
            legacy.row_factory = sqlite3.Row
            legacy.execute('CREATE TABLE knowledge_entries(id TEXT PRIMARY KEY)')
            legacy.execute('INSERT INTO knowledge_entries VALUES(?)', (ENTRY,))
            before = list(legacy.execute('SELECT name FROM sqlite_master'))
            self.assertEqual(state_for(legacy, ENTRY)['validity'], 'active')
            self.assertEqual(list(legacy.execute('SELECT name FROM sqlite_master')), before)

    def test_corrupt_quality_metadata_fails_closed(self):
        for quality in [[], {'version': True, 'state': 'accepted', 'reusable': True}]:
            with self.subTest(quality=quality):
                self.connection.execute('UPDATE knowledge_entries SET payload=? WHERE id=?',
                                        (json.dumps({'quality': quality}), ENTRY))
                with self.assertRaises(LifecycleError):
                    change_state(self.connection, ENTRY, 'active', '复核后恢复')

    def test_pending_peer_does_not_become_an_authoritative_restore_conflict(self):
        self.source(SECOND, quality='pending', status='search_lead')
        change_state(self.connection, ENTRY, 'revoked', '先暂停')
        result = change_state(self.connection, ENTRY, 'active', '原文复核通过')
        self.assertEqual(result['validity'], 'active')
        self.assertEqual(self.connection.execute('SELECT quality FROM knowledge_entries WHERE id=?', (SECOND,)).fetchone()[0], 'pending')

    def test_selection_cannot_cross_url_even_if_a_family_value_is_corrupt(self):
        self.source(SECOND)
        self.connection.execute('UPDATE knowledge_entries SET url=? WHERE id=?',
                                ('https://grad.example.edu.cn/another-scope', SECOND))
        select_current(self.connection, ENTRY, '选择同一原文的版本')
        self.assertEqual(state_for(self.connection, SECOND)['validity'], 'active')

    def test_finish_checks_that_it_still_owns_the_running_job(self):
        import knowledge_lifecycle
        job = self.running();self.generation()
        original = knowledge_lifecycle._generation
        def cancelled_after_validation(*args):
            value = original(*args)
            self.connection.execute("UPDATE knowledge_index_jobs SET state='cancelled' WHERE id=?", (job['id'],))
            return value
        with patch('knowledge_lifecycle._generation', side_effect=cancelled_after_validation):
            with self.assertRaises(LifecycleError):
                finish_job(self.connection, job['id'], GENERATION, attempt=1)
        self.assertIsNone(state_for(self.connection, ENTRY)['current_generation_id'])
        self.assertEqual(self.connection.execute('SELECT state FROM knowledge_generations WHERE id=?', (GENERATION,)).fetchone()[0], 'running')

    def test_failure_from_an_old_lease_cannot_fail_the_new_attempt_generation(self):
        import knowledge_lifecycle
        job = self.running();self.generation()
        original = knowledge_lifecycle._attempt
        def retried_after_validation(*args):
            original(*args)
            self.connection.execute('UPDATE knowledge_index_jobs SET attempts=2 WHERE id=?', (job['id'],))
        with patch('knowledge_lifecycle._attempt', side_effect=retried_after_validation):
            with self.assertRaises(LifecycleError):
                fail_job(self.connection, job['id'], 'segment_failed', attempt=1)
        self.assertEqual(self.connection.execute('SELECT state FROM knowledge_generations WHERE id=?', (GENERATION,)).fetchone()[0], 'running')


if __name__ == '__main__':
    unittest.main()
