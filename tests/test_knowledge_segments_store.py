"""Temporary SQLite checks for exact, bounded source segment storage."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from knowledge_store import KnowledgeError, KnowledgeStore


class KnowledgeSegmentStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='n3-store-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'data'
        self.store = KnowledgeStore(self.root)

    def original(self, *, unit='u1', year=2027, suffix='catalog', pages=None, separator='\n'):
        pages = pages or [{'page': 1, 'text': f'{year}年硕士研究生招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。'}]
        raw = ('%PDF-1.4\nsynthetic store fixture ' + suffix).encode()
        sha = hashlib.sha256(raw).hexdigest()
        source = {'url': 'https://grad.example.edu.cn/' + suffix + '.pdf', 'title': f'{year}年硕士研究生招生专业目录',
                  'unit_id': unit, 'unit_name': '合成夹具大学', 'year': year, 'requested_year': 2028,
                  'status': 'official_original_read', 'sha256': sha, 'excerpt': pages[0]['text']}
        document = {'url': source['url'], 'raw': raw, 'sha256': sha, 'attribution_status': 'verified',
                    'text': separator.join(page['text'] for page in pages), 'pages': pages}
        return source, document

    def save(self, **options):
        source, document = self.original(**options)
        saved = self.store.save_source(source, document=document)
        self.assertEqual(saved['quality']['state'], 'accepted')
        return saved, source, document

    @staticmethod
    def long_pages():
        return [{'page': page, 'text': ('2027年硕士研究生招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。\n' if page == 1 else '')
                 + '常规招生说明。' * 260 + ('\n第二十一章 报考条件\n跨专业考生须完成特别加试课程，但符合明确豁免条件者除外。' if page == 21 else '')}
                for page in range(1, 23)]

    def test_late_pages_are_searchable_and_read_as_exact_extraction_slices(self):
        saved, _, _ = self.save(pages=self.long_pages())
        self.assertLessEqual(len(self.store.get(saved['id'])['text']), 32000)
        hits = self.store.search_segments(query='特别加试', entry_id=saved['id'])
        self.assertGreater(hits['total'], 0)
        segment = hits['segments'][0]
        self.assertEqual(segment['page_start'], 21)
        read = self.store.read_segment(segment['id'], after=1, max_chars=6000)
        self.assertIn('特别加试', read['segment']['text'])
        self.assertLessEqual(read['returned_chars'], 6000)
        with closing(sqlite3.connect(self.store.path)) as connection:
            text = connection.execute('SELECT text FROM knowledge_extractions WHERE id=?', (segment['extraction_id'],)).fetchone()[0]
        self.assertEqual(segment['text'], text[segment['char_start']:segment['char_end']])

    def test_repeated_text_on_different_pages_keeps_distinct_positions(self):
        pages = self.long_pages()
        pages[2] = {'page': 3, 'text': '第一学院\n重复约束条款只适用于该学院。'}
        pages[20] = {'page': 21, 'text': '第二学院\n重复约束条款只适用于该学院。'}
        saved, _, _ = self.save(pages=pages)
        hits = self.store.search_segments(query='重复约束', entry_id=saved['id'])['segments']
        self.assertEqual({hit['page_start'] for hit in hits}, {3, 21})
        self.assertEqual(len({hit['id'] for hit in hits}), 2)
        self.assertEqual(len({hit['char_start'] for hit in hits}), 2)

    def test_pdf_form_feed_and_blank_page_do_not_drop_page_numbers(self):
        pages = [{'page': 1, 'text': self.original()[1]['pages'][0]['text']},
                 {'page': 2, 'text': ''}, {'page': 3, 'text': '第三页特别考试科目为901。'}]
        saved, _, _ = self.save(pages=pages, separator='\f')
        hit = self.store.search_segments(query='特别考试', entry_id=saved['id'])['segments'][0]
        self.assertEqual(hit['page_start'], 3)

    def test_same_derivation_is_idempotent_and_does_not_change_inputs(self):
        source, document = self.original()
        before = deepcopy((source, document))
        first = self.store.save_source(source, document=document)
        second = self.store.save_source(source, document=document)
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(first['extraction']['generation_id'], second['extraction']['generation_id'])
        with closing(sqlite3.connect(self.store.path)) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM knowledge_extractions').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT count(*) FROM knowledge_generations').fetchone()[0], 1)
        self.assertEqual((source, document), before)

    def test_scope_and_year_filters_precede_segment_pagination(self):
        saved, _, _ = self.save(suffix='wanted')
        for index in range(12):
            self.save(unit='other', year=2026, suffix='other-' + str(index))
        result = self.store.search_segments(query='计算机', unit_id='u1', year=2027, page_size=1)
        self.assertEqual(result['segments'][0]['entry_id'], saved['id'])
        self.assertEqual(result['total'], 1)

    def test_missing_or_tampered_original_cannot_return_trusted_segments(self):
        saved, source, _ = self.save()
        segment = self.store.search_segments(query='计算机')['segments'][0]
        blob = self.root / 'runtime/research-sources' / (source['sha256'] + '.bin')
        blob.write_bytes(b'X' * blob.stat().st_size)
        self.assertEqual(self.store.search_segments(query='计算机')['segments'], [])
        with self.assertRaises(KnowledgeError):
            self.store.read_segment(segment['id'])
        with closing(sqlite3.connect(self.store.path)) as connection:
            self.assertTrue(connection.execute('SELECT text FROM knowledge_extractions').fetchone()[0])

    def test_pending_sources_are_not_trusted_even_when_they_have_segments(self):
        source, document = self.original()
        saved = self.store.save_source({**source, 'year': None}, document=document)
        self.assertEqual(saved['quality']['state'], 'pending')
        self.assertEqual(self.store.search_segments(query='计算机')['segments'], [])
        untrusted = self.store.search_segments(query='计算机', quality='pending')['segments'][0]
        with self.assertRaises(KnowledgeError):self.store.read_segment(untrusted['id'])
        self.assertEqual(self.store.read_segment(untrusted['id'], trusted_only=False)['segment']['entry_id'], saved['id'])

    def test_empty_scanned_pdf_records_ocr_need_without_a_successful_index(self):
        source,document=self.original(pages=[{'page':1,'text':''}])
        saved=self.store.save_source(source,document=document)
        self.assertEqual(saved['quality']['state'],'pending')
        self.assertEqual(saved['extraction']['extraction_status'],'needs_ocr')
        self.assertEqual(saved['index_state'],'failed')
        self.assertEqual(self.store.search_segments()['segments'],[])
        with closing(sqlite3.connect(self.store.path)) as connection:
            self.assertIsNone(connection.execute('SELECT current_generation_id FROM knowledge_entry_state').fetchone()[0])
            self.assertEqual(connection.execute('SELECT state FROM knowledge_index_jobs').fetchone()[0],'failed')

    def test_windows_and_ids_are_validated(self):
        self.assertEqual(self.store.search_segments()['segments'], [])
        for options in [{'page': True}, {'page_size': 101}, {'year': '2027'}, {'unit_id': []}, {'entry_id': '../x'}, {'quality': []}]:
            with self.subTest(options=options), self.assertRaises(KnowledgeError):self.store.search_segments(**options)
        saved, _, _ = self.save()
        segment = self.store.search_segments(entry_id=saved['id'])['segments'][0]
        for options in [{'before': True}, {'before': -1}, {'after': 4}, {'max_chars': 0}, {'max_chars': 20001}, {'trusted_only': 1}]:
            with self.subTest(options=options), self.assertRaises(KnowledgeError):self.store.read_segment(segment['id'], **options)
        with self.assertRaises(KnowledgeError):self.store.read_segment('../x')

    def test_read_budget_cannot_be_bypassed_by_search_text_or_context_payloads(self):
        saved,_,_=self.save(pages=[{'page':1,'text':'2027年硕士研究生招生专业目录\n第一章 报考条件\n081200 计算机科学与技术 初试科目101、201、301、408。'}])
        hit=self.store.search_segments(query='计算机',entry_id=saved['id'])['segments'][0]
        result=self.store.read_segment(hit['id'],before=1,after=1,max_chars=13)
        self.assertNotIn('search_text',result['segment'])
        self.assertTrue(all('text' not in span for span in result['segment'].get('context_spans',[])))
        self.assertEqual(sum(len(item['text']) for item in [result['segment'],*result['contexts'],*result['neighbors']]),result['returned_chars'])
        self.assertLessEqual(result['returned_chars'],13)

    def test_inactive_source_does_not_reuse_previously_accepted_segments(self):
        saved,_,_=self.save()
        hit=self.store.search_segments(entry_id=saved['id'])['segments'][0]
        with closing(sqlite3.connect(self.store.path)) as connection:
            connection.execute("UPDATE knowledge_entry_state SET validity='revoked' WHERE entry_id=?",(saved['id'],))
            connection.commit()
        self.assertFalse(self.store.get(saved['id'])['usable'])
        self.assertEqual(self.store.search_segments()['segments'],[])
        with self.assertRaises(KnowledgeError):self.store.read_segment(hit['id'])

    def test_without_segment_fts_search_uses_the_same_scope_safe_fallback(self):
        saved, _, _ = self.save()
        with closing(sqlite3.connect(self.store.path)) as connection:
            connection.execute('DROP TABLE IF EXISTS knowledge_segments_fts')
            connection.commit()
        result = self.store.search_segments(query='计算机', year=2027)
        self.assertEqual(result['segments'][0]['entry_id'], saved['id'])

    def test_failed_replacement_generation_rolls_back_current_text_and_index(self):
        saved, _, _ = self.save()
        current = saved['extraction']['generation_id']
        with patch('knowledge_repository.SEGMENT_VERSION', 'structure-test-v2'):
            with patch('knowledge_repository._write_segments', side_effect=sqlite3.IntegrityError('synthetic write abort')):
                with self.assertRaises(KnowledgeError):self.store.rebuild_segments(saved['id'])
        self.assertEqual(self.store.get(saved['id'])['extraction']['generation_id'], current)
        self.assertTrue(self.store.search_segments(query='计算机')['segments'])
        with closing(sqlite3.connect(self.store.path)) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM knowledge_generations').fetchone()[0],1)
            self.assertEqual(connection.execute('SELECT count(*) FROM knowledge_index_jobs').fetchone()[0],2)
            self.assertEqual(connection.execute("SELECT count(*) FROM knowledge_index_jobs WHERE state='failed'").fetchone()[0],1)

    def test_old_schema_is_read_without_mutating_it_or_inventing_full_text(self):
        saved, _, _ = self.save()
        with closing(sqlite3.connect(self.store.path)) as connection:
            names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'knowledge_%'")]
            for name in names:
                if name not in {'knowledge_entries', 'knowledge_provenance', 'knowledge_fts'} and not name.startswith('knowledge_fts_'):
                    connection.execute('DROP TABLE IF EXISTS "' + name + '"')
            connection.execute('DROP INDEX knowledge_quality_state')
            connection.execute('ALTER TABLE knowledge_entries DROP COLUMN quality')
            connection.commit()
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        old = KnowledgeStore(self.root)
        self.assertEqual(old.get(saved['id'])['quality']['state'], 'pending')
        self.assertEqual(old.search_segments()['segments'], [])
        with closing(sqlite3.connect(self.store.path)) as connection:
            self.assertEqual(tables, {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")})


if __name__ == '__main__':unittest.main()
