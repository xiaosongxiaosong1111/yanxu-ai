"""Independent repository boundaries: exact locations and trust-preserving metadata."""
from contextlib import closing
from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from knowledge_schema import ensure_schema
from knowledge_store import KnowledgeStore


class KnowledgeRepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='n3-repository-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = KnowledgeStore(self.root)

    def save(self, pages=None, suffix='catalog', year=2027, unit='u1', media='application/pdf'):
        pages = pages or [{'page': 1, 'text': '2027年硕士研究生招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。'}]
        raw = (('%PDF-1.4\n' if media == 'application/pdf' else '<html>') + suffix).encode()
        digest = hashlib.sha256(raw).hexdigest()
        source = {'url': 'https://grad.example.edu.cn/' + suffix, 'title': '2027年硕士研究生招生专业目录',
                  'unit_id': unit, 'unit_name': '合成大学', 'year': year, 'status': 'official_original_read',
                  'sha256': digest, 'excerpt': pages[0]['text']}
        document = {'url': source['url'], 'sha256': digest, 'raw': raw, 'attribution_status': 'verified',
                    'content_type': media, 'text': '\n'.join(page['text'] for page in pages), 'pages': pages}
        return self.store.save_source(source, document=document)

    def test_context_header_retains_its_own_physical_page_and_exact_span(self):
        entry = self.save(pages=[
            {'page': 1, 'text': '2027年硕士研究生招生专业目录\n😀 说明。\n081200 计算机科学与技术'},
            {'page': 2, 'text': '专业代码\t专业名称\t业务课'},
            {'page': 3, 'text': '081200\t计算机科学与技术\t101 201 301 408'},
        ])
        segment = self.store.search_segments(entry_id=entry['id'], query='408')['segments'][0]
        result = self.store.read_segment(segment['id'])
        self.assertEqual(result['segment']['page_start'], 3)
        headers = [item for item in result['contexts'] if item['role'] == 'table_header']
        self.assertEqual(len(headers), 1)
        self.assertEqual((headers[0]['page_start'], headers[0]['page_end']), (2, 2))
        with closing(sqlite3.connect(self.store.path)) as connection:
            text = connection.execute('SELECT text FROM knowledge_extractions WHERE id=?',
                                      (segment['extraction_id'],)).fetchone()[0]
        self.assertEqual(headers[0]['text'], text[headers[0]['char_start']:headers[0]['char_end']])

    def test_context_location_is_clipped_with_its_exact_quote(self):
        entry = self.save(pages=[
            {'page': 1, 'text': '2027年硕士研究生招生专业目录\n081200 计算机科学与技术'},
            {'page': 2, 'text': '专业代码\t专业名称\t业务课'},
            {'page': 3, 'text': '081200\t计算机科学与技术\t101 201 301 408'},
        ])
        segment = self.store.search_segments(entry_id=entry['id'], query='408')['segments'][0]
        result = self.store.read_segment(segment['id'], max_chars=len(segment['text']) + 4)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['contexts'][0]['text']), 4)
        self.assertEqual(result['contexts'][0]['page_start'], 1)

    def test_cross_page_context_quote_clipping_recomputes_its_last_page(self):
        entry = self.save(pages=[
            {'page': 1, 'text': '2027年硕士研究生招生专业目录\n081200'},
            {'page': 2, 'text': '计算机科学与技术'},
            {'page': 3, 'text': '专业代码\t专业名称\t业务课\n081200\t计算机科学与技术\t101 201 301 408'},
        ])
        segment = self.store.search_segments(entry_id=entry['id'], query='408')['segments'][0]
        full = self.store.read_segment(segment['id'])
        heading = next(context for context in full['contexts'] if context['role'] == 'heading')
        self.assertEqual((heading['page_start'], heading['page_end']), (1, 2))
        clipped = self.store.read_segment(segment['id'], max_chars=len(segment['text']) + 4)
        self.assertEqual(clipped['contexts'][0]['text'], '0812')
        self.assertEqual((clipped['contexts'][0]['page_start'], clipped['contexts'][0]['page_end']), (1, 1))

    def test_schema_failure_rolls_back_new_tables_and_preserves_old_rows(self):
        entry = self.save()
        with closing(sqlite3.connect(self.store.path)) as connection:
            payload = connection.execute('SELECT payload FROM knowledge_entries WHERE id=?', (entry['id'],)).fetchone()[0]
            with patch('knowledge_schema.DERIVATION_SCHEMA', (
                    'CREATE TABLE migration_probe(value TEXT)', 'INVALID MIGRATION SQL')):
                with self.assertRaises(sqlite3.OperationalError), connection:
                    ensure_schema(connection)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='migration_probe'").fetchone())
            self.assertEqual(connection.execute('SELECT payload FROM knowledge_entries WHERE id=?',
                                                (entry['id'],)).fetchone()[0], payload)

    def test_segment_source_exposes_computed_usable_without_mutating_metadata(self):
        entry = self.save()
        hit = self.store.search_segments(entry_id=entry['id'])['segments'][0]
        self.assertIs(hit['source']['usable'], True)
        before = deepcopy(hit['source']['quality'])
        hit['source']['quality']['state'] = 'excluded'
        again = self.store.read_segment(hit['id'])['segment']
        self.assertEqual(again['source']['quality'], before)
        self.assertIs(again['source']['usable'], True)

    def test_current_generation_and_validity_are_filtered_before_page_limit(self):
        entry = self.save(suffix='wanted')
        for index in range(3):
            stale = self.save(suffix='stale-' + str(index))
            with closing(sqlite3.connect(self.store.path)) as connection:
                connection.execute("UPDATE knowledge_entry_state SET validity='revoked' WHERE entry_id=?", (stale['id'],))
                connection.commit()
        with closing(sqlite3.connect(self.store.path)) as connection:
            current = connection.execute('SELECT current_generation_id FROM knowledge_entry_state WHERE entry_id=?',
                                         (entry['id'],)).fetchone()[0]
            connection.execute('UPDATE knowledge_generations SET state=? WHERE id=?', ('failed', current))
            connection.commit()
        result = self.store.search_segments(query='计算机', page_size=1)
        self.assertEqual(result['segments'], [])
        self.assertEqual(result['total'], 0)

    def test_legacy_quality_column_migration_cannot_promote_accepted_payload(self):
        entry = self.save()
        with closing(sqlite3.connect(self.store.path)) as connection:
            connection.execute('DROP INDEX knowledge_quality_state')
            connection.execute('ALTER TABLE knowledge_entries DROP COLUMN quality')
            connection.commit()
            ensure_schema(connection)
            connection.commit()
        public = self.store.get(entry['id'])
        self.assertEqual(public['quality']['state'], 'pending')
        self.assertFalse(public['usable'])
        with closing(sqlite3.connect(self.store.path)) as connection:
            quality = connection.execute('SELECT quality FROM knowledge_entries WHERE id=?', (entry['id'],)).fetchone()[0]
        self.assertEqual(quality, 'pending')


if __name__ == '__main__':
    unittest.main()
