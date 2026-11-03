import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import json_reader
from service import AdmissionService, ServiceError


class JsonReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'admissions.json'

    def test_streamed_values_preserve_unicode_numbers_and_bom(self):
        expected = {'metadata': {'标题': '真实原文', 'year': 2027},
                    'programs': [{'id': '学校:001', 'fee': 123.5, 'integer': 2**80,
                                  'flags': [True, None, False], 'note': '↗天津'}]}
        for encoding in ('utf-8', 'utf-8-sig'):
            self.path.write_text(json.dumps(expected, ensure_ascii=False), encoding=encoding)
            with patch.object(json_reader, 'STREAM_THRESHOLD', 1), patch.object(Path, 'read_text', side_effect=AssertionError('whole-file read')):
                actual = json_reader.read_json(self.path)
            self.assertEqual(actual, expected)
            self.assertIs(type(actual['programs'][0]['fee']), float)

    def test_streamed_invalid_or_trailing_data_is_rejected(self):
        for text in ('', '{', '{"v":NaN}', '{"v":Infinity}', '{"v":1e9999}', '{"v":-1e9999}',
                     '{"v":1}' + ' '*70000 + '{}', '{"v":1}' + ' '*70000 + 'junk'):
            with self.subTest(text=text[:30]):
                self.path.write_text(text, encoding='utf-8')
                with patch.object(json_reader, 'STREAM_THRESHOLD', -1), self.assertRaises(ValueError):
                    json_reader.read_json(self.path)

    def test_small_reader_rejects_nonfinite(self):
        for text in ('{"v":NaN}', '{"v":Infinity}', '{"v":1e9999}', '{"v":-1e9999}'):
            self.path.write_text(text, encoding='utf-8')
            with self.assertRaises(ValueError):
                json_reader.read_json(self.path)

    def test_missing_dependency_is_an_actionable_value_error(self):
        self.path.write_text('{}', encoding='utf-8')
        with patch.object(json_reader, 'STREAM_THRESHOLD', 1), \
                patch.object(json_reader, '__file__', str(self.path.parent/'json_reader.py')), \
                patch.dict(json_reader.sys.modules, {'ijson': None}), \
                patch.object(json_reader.importlib, 'import_module', side_effect=ModuleNotFoundError):
            with self.assertRaisesRegex(ValueError, 'ijson==3.4.0.post0'):
                json_reader.read_json(self.path)

    def test_incompatible_ambient_parser_is_not_silently_used(self):
        self.path.write_text('{}', encoding='utf-8')
        with patch.object(json_reader, 'STREAM_THRESHOLD', 1), \
                patch.dict(json_reader.sys.modules, {'ijson': SimpleNamespace(__version__='0.1')}):
            with self.assertRaisesRegex(json_reader.SnapshotReadError, 'ijson==3.4.0.post0'):
                json_reader.read_json(self.path)

    def test_service_reports_unavailable_without_partial_records(self):
        self.path.write_text('{"metadata":{},"programs":[]}' + ' '*70000 + '{}', encoding='utf-8')
        with patch.object(json_reader, 'STREAM_THRESHOLD', 1), self.assertRaises(ServiceError) as error:
            AdmissionService(self.path.parent)._load('admissions.json', ('programs',))
        self.assertEqual(error.exception.code, 'data_unavailable')

    def test_collection_corruption_is_server_failure_not_query_error(self):
        self.path.write_text('{"metadata":{},"programs":[]}' + ' '*70000 + '{}', encoding='utf-8')
        service = AdmissionService(self.path.parent)
        with patch.object(json_reader, 'STREAM_THRESHOLD', 1), self.assertRaises(ServiceError) as error:
            service.collection_status()
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(error.exception.code, 'data_unavailable')


if __name__ == '__main__':
    unittest.main()
