"""Large publication payloads stream without retaining a second national corpus."""
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

import catalog_index
from catalog_index import CatalogIndex, CatalogIndexError


class CatalogIndexMemoryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.index = CatalogIndex(Path(folder.name) / 'runtime/catalog-index.sqlite')
        self.registry = {'units': [{'id': 'u', 'name': '样本大学'}]}
        self.coverage = {'unit_catalog_status': [{'unit_id': 'u', 'status': 'partial'}]}
        self.peaks = {}

    def source(self, kind, count=64, *, notes=''):
        common = {'unit_id': 'u', 'year': 2027, 'notes': notes}
        rows = [{**common, 'id': str(index)} for index in range(count)]
        if kind == 'programs':
            rows = [{**row, 'name': '计算机', 'code': '081200', 'subjects': []} for row in rows]
        elif kind == 'documents':
            rows = [{**row, 'kind': 'catalog', 'url': 'https://sample.edu.cn/catalog'} for row in rows]
        return {'metadata': {'reference_year': 2027}, 'programs': [], 'documents': [], kind: rows}

    def publish(self, source, revision='new'):
        return self.index.sync(self.registry, source, self.coverage, revision)

    def measured_publication(self, kind, *, source_work=False):
        # Allocate input before tracing: the limit measures extra publication memory.
        source = self.source(kind, notes='x' * (256 * 1024))

        def projected(*args):
            transient_controller = bytearray(6 * 1024 * 1024)
            self.assertEqual(len(transient_controller), 6 * 1024 * 1024)
            return []

        tracemalloc.start()
        try:
            if source_work:
                with patch('catalog_index._source_documents', side_effect=projected):
                    self.publish(source)
            else:
                self.publish(source)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.peaks[kind] = peak
        self.assertLess(peak, 8 * 1024 * 1024,
                        f'{kind}: extra peak {peak:,} bytes retained serialized corpus')
        with self.index.read_connection() as connection:
            table = 'records' if kind == 'notices' else kind
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 64)
            first, last = connection.execute(
                'SELECT MIN(length(payload)), MAX(length(payload)) FROM ' + table).fetchone()
        self.assertGreaterEqual(first, 256 * 1024)
        self.assertGreaterEqual(last, first)

    def test_program_payload_memory_stays_bounded_during_source_replay(self):
        self.measured_publication('programs', source_work=True)

    def test_document_payload_memory_stays_bounded(self):
        self.measured_publication('documents')

    def test_history_payload_memory_stays_bounded(self):
        self.measured_publication('notices')

    def assert_old_publication(self):
        self.assertEqual(self.index.metadata()['revision'], 'old')
        self.assertEqual([row['id'] for row in self.index.query_programs()['programs']], ['0'])

    def test_late_program_encoding_failure_keeps_old_publication(self):
        self.publish(self.source('programs', count=1), 'old')
        incoming = self.source('programs', count=24)
        incoming['programs'][-1] = {**incoming['programs'][-1], 'notes': {'not-json'}}
        with self.assertRaises(CatalogIndexError):
            self.publish(incoming)
        self.assert_old_publication()

    def test_source_memory_error_is_not_swallowed_or_published(self):
        self.publish(self.source('programs', count=1), 'old')
        with patch('catalog_index._source_documents', side_effect=MemoryError('controller replay')):
            with self.assertRaises(MemoryError):
                self.publish(self.source('programs', count=24))
        self.assert_old_publication()

    def test_mid_stream_memory_error_rolls_back_and_propagates(self):
        self.publish(self.source('programs', count=1), 'old')
        original = catalog_index.encoded

        def exhausted(value):
            if value.get('id') == '23':
                raise MemoryError('single payload allocation')
            return original(value)

        with patch('catalog_index.encoded', side_effect=exhausted):
            with self.assertRaises(MemoryError):
                self.publish(self.source('programs', count=24))
        self.assert_old_publication()

    def test_late_document_encoding_failure_keeps_old_publication(self):
        self.publish(self.source('programs', count=1), 'old')
        incoming = self.source('documents', count=24)
        incoming['documents'][-1] = {**incoming['documents'][-1], 'notes': {'not-json'}}
        with self.assertRaises(CatalogIndexError):
            self.publish(incoming)
        self.assert_old_publication()

    def test_sql_failure_after_partial_inserts_rolls_back_old_revision(self):
        self.publish(self.source('programs', count=1), 'old')
        with closing(sqlite3.connect(self.index.path)) as connection:
            connection.execute("CREATE TRIGGER reject_last BEFORE INSERT ON programs "
                               "WHEN NEW.program_id='23' BEGIN SELECT RAISE(ABORT,'stop'); END")
        with self.assertRaises(CatalogIndexError):
            self.publish(self.source('programs', count=24))
        self.assert_old_publication()

    def test_same_revision_still_rejects_unencodable_history(self):
        self.publish(self.source('programs', count=1), 'old')
        incoming = self.source('programs', count=1)
        incoming['notices'] = [{'id': 'bad', 'unit_id': 'u', 'year': 2027, 'notes': {'not-json'}}]
        with self.assertRaises(CatalogIndexError):
            self.publish(incoming, 'old')
        self.assert_old_publication()


if __name__ == '__main__':
    unittest.main()
