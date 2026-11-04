"""Explicit local derivations preserve authority, audit failures and never use summaries."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from knowledge_store import KnowledgeError, KnowledgeStore


class KnowledgeReindexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='n3-reindex-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = KnowledgeStore(self.root)

    def save(self, *, body=None, excerpt='检索摘要', raw=None):
        body = body or '2027年硕士研究生招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。'
        raw = raw or ('<html><title>2027年硕士研究生招生专业目录</title><main>' + body + '</main></html>').encode()
        source = {'url':'https://grad.example.edu.cn/catalog.html', 'unit_id':'u1', 'unit_name':'合成大学',
                  'year':2027, 'requested_year':2028, 'title':'2027年硕士研究生招生专业目录',
                  'status':'official_original_read', 'sha256':hashlib.sha256(raw).hexdigest(),
                  'excerpt':excerpt, 'checked_at':'2026-10-01T00:00:00Z'}
        document = {'raw':raw, 'text':body, 'pages':[{'page':1,'text':body}], 'url':source['url'],
                    'content_type':'application/pdf' if raw.startswith(b'%PDF-') else 'text/html', 'attribution_status':'verified'}
        return self.store.save_source(source, document=document)

    def remove_derivations(self):
        with closing(sqlite3.connect(self.store.path)) as connection:
            for name in ('knowledge_segments','knowledge_generations','knowledge_extractions',
                         'knowledge_entry_state','knowledge_index_jobs'):
                connection.execute('DELETE FROM '+name)
            connection.commit()

    def test_explicit_legacy_reextract_uses_verified_raw_not_preview_and_keeps_date(self):
        body = ('2027年硕士研究生招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。\n'
                + '常规条款。'*7000 + '\n未出现在旧摘要中的特别加试课程。')
        saved = self.save(body=body)
        self.remove_derivations()
        with closing(sqlite3.connect(self.store.path)) as connection:
            connection.execute('UPDATE knowledge_entries SET full_text=?,pages=?',('只有旧预览','[]'))
            connection.commit()
        result = self.store.rebuild_segments(saved['id'])
        self.assertEqual(result['state'], 'succeeded')
        hit = self.store.search_segments(query='特别加试',entry_id=saved['id'])['segments'][0]
        self.assertIn('特别加试', self.store.read_segment(hit['id'])['segment']['text'])
        self.assertEqual(self.store.get(saved['id'])['checked_at'], '2026-10-01T00:00:00Z')

    def test_failed_new_generation_has_auditable_job_and_retry_keeps_old_usable(self):
        saved = self.save()
        original = saved['extraction']['generation_id']
        with patch('knowledge_repository.SEGMENT_VERSION','structure-retry-test'):
            with patch('knowledge_repository._write_segments',side_effect=sqlite3.IntegrityError('private/path')):
                with self.assertRaises(KnowledgeError) as error:self.store.rebuild_segments(saved['id'])
            self.assertNotIn('private/path', str(error.exception))
            failed = next(item for item in self.store.jobs(saved['id'])['jobs'] if item['state']=='failed')
            self.assertEqual(failed['attempts'],1)
            self.assertEqual(self.store.get(saved['id'])['extraction']['generation_id'],original)
            self.assertTrue(self.store.search_segments(query='计算机')['segments'])
            self.assertEqual(self.store.rebuild_segments(saved['id'])['state'],'succeeded')
            retried = next(item for item in self.store.jobs(saved['id'])['jobs'] if item['id']==failed['id'])
            self.assertEqual(retried['state'],'succeeded')
            self.assertEqual(retried['attempts'],2)

    def test_explicit_reextract_does_not_retain_personal_roster_in_new_tables(self):
        saved = self.save()
        self.remove_derivations()
        private = '2027年硕士拟录取名单\n姓名 考生编号 初试成绩 复试成绩\n张三 100001234567890 360 86'
        raw = ('<html><main>'+private+'</main></html>').encode()
        digest = hashlib.sha256(raw).hexdigest()
        (self.root/'runtime/research-sources'/(digest+'.bin')).write_bytes(raw)
        with closing(sqlite3.connect(self.store.path)) as connection:
            row=connection.execute('SELECT payload FROM knowledge_entries').fetchone()
            payload=json.loads(row[0]);payload['sha256']=digest
            payload['authority_evidence']={**payload['authority_evidence'],'sha256':digest}
            connection.execute('UPDATE knowledge_entries SET sha256=?,payload=?',(digest,json.dumps(payload)))
            connection.commit()
        result=self.store.rebuild_segments(saved['id'])
        self.assertEqual(result['state'],'excluded')
        self.assertEqual(self.store.get(saved['id'])['quality']['state'],'excluded')
        with closing(sqlite3.connect(self.store.path)) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM knowledge_extractions').fetchone()[0],0)

    def test_scanned_doc_cannot_promote_search_excerpt_to_full_text(self):
        saved = self.save(body=' ',raw=b'%PDF-1.4\nscan fixture')
        self.assertEqual(saved['quality']['state'],'pending')
        self.assertEqual(saved['extraction']['extraction_status'],'needs_ocr')
        self.assertLessEqual(saved['extraction']['text_length'],1)
        self.assertEqual(self.store.search_segments(quality='all')['segments'],[])

    def test_legacy_reextract_rechecks_actual_text_year_instead_of_old_quality(self):
        old='2026年硕士研究生招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。'
        saved=self.save(raw=('<html><main>'+old+'</main></html>').encode())
        self.remove_derivations()
        result=self.store.rebuild_segments(saved['id'])
        self.assertEqual(result['state'],'needs_review')
        self.assertEqual(self.store.get(saved['id'])['quality']['state'],'pending')
        self.assertEqual(self.store.search_segments()['segments'],[])

    def test_reextract_checks_the_exact_bytes_used_after_the_initial_gate(self):
        saved=self.save()
        self.remove_derivations()
        path=self.root/'runtime/research-sources'/(saved['sha256']+'.bin')
        original=path.read_bytes()
        checked=self.store._blob_available
        def check_then_change(digest):
            valid=checked(digest)
            path.write_bytes(b'<html><main>Changed source</main></html>')
            return valid
        with patch.object(self.store,'_blob_available',side_effect=check_then_change):
            with self.assertRaises(KnowledgeError) as error:self.store.rebuild_segments(saved['id'])
        self.assertEqual(error.exception.code,'invalid_knowledge_snapshot')
        path.write_bytes(original)
        self.assertEqual(self.store.search_segments()['segments'],[])


if __name__=='__main__':unittest.main()
