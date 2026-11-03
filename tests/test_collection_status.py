import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import collection_status
from collection_status import CollectionStatus
from json_reader import SnapshotReadError
from service import AdmissionService, ServiceError


class CollectionStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.units = [{'id': str(i), 'unit_code': f'{10000+i}', 'name': name, 'province': '天津'}
                      for i, name in enumerate(['甲大学', '乙大学', '丙研究所', '丁大学'], 1)]
        self.write('units.json', {'units': self.units})
        self.write('admissions.json', {'metadata': {'reference_year': 2027, 'unit_catalog_status': [
            {'unit_id': '1', 'year': 2027, 'status': 'verified', 'expected_records': 1, 'parsed_records': 1, 'verified_records': 1}]},
            'programs': [{'id': 'p1', 'unit_id': '1', 'year': 2027}, {'id': 'p2', 'unit_id': '2', 'year': 2027}]})
        self.write('national_sources.json', {'metadata': {'year': 2027}, 'units': [
            {'unit_id': '3', 'year': 2027, 'status': 'truncated', 'frontier': [{'url': 'https://example.edu.cn/pending'}],
             'documents': [{'url': 'https://example.edu.cn/catalog.pdf', 'title': '2027目录', 'kind': 'catalog',
                            'year': 2027, 'local_path': 'SECRET/path.pdf', 'sha256': 'a'*64}],
             'errors': [{'code': 'timeout', 'error': 'SECRET/path.pdf'}]},
            {'unit_id': '4', 'year': 2027, 'status': 'restricted', 'errors': [{'code': 'restricted'}]}]})
        self.store = CollectionStatus(self.root)

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value, ensure_ascii=False), 'utf-8')

    def test_all_indexed_units_remain_visible_with_separate_catalog_and_fetch_states(self):
        data = self.store.query()
        self.assertEqual(data['total'], 4)
        self.assertEqual(data['summary']['indexed_units'], 4)
        self.assertEqual(data['summary']['units_with_programs'], 2)
        self.assertFalse(data['summary']['national_complete'])
        by_id = {row['unit_id']: row for row in data['units']}
        self.assertEqual(by_id['1']['catalog_status'], 'recorded_verified')
        self.assertEqual(by_id['2']['catalog_status'], 'partial')
        self.assertEqual(by_id['3']['catalog_status'], 'not_collected')
        self.assertEqual(by_id['3']['collection_status'], 'truncated')
        self.assertEqual(by_id['3']['pending_pages'], 1)
        self.assertIn('尚待解析', by_id['3']['missing_reason'])
        self.assertEqual(by_id['4']['collection_status'], 'restricted')

    def test_progress_does_not_expose_paths_raw_error_text_or_unsafe_links(self):
        value = self.store.query(unit_id='3')
        encoded = json.dumps(value)
        self.assertNotIn('SECRET', encoded)
        self.assertNotIn('local_path', encoded)
        self.assertEqual(value['units'][0]['sources'][0]['url'], 'https://example.edu.cn/catalog.pdf')
        self.write('national_sources.json', {'units': [{'unit_id': '3', 'year': 2027, 'status': 'failed',
            'documents': [{'url': 'javascript:alert(1)', 'title': '<script>text</script>'}]}]})
        self.assertEqual(self.store.query(unit_id='3')['units'][0]['sources'], [])

    def test_access_gates_and_failed_responses_are_not_saved_originals(self):
        blocked = [
            {'status': 'restricted'}, {'status': 'failed'}, {'status': 'failure'},
            {'status': 'error'}, {'status': 'pending'}, {'status': 'running'},
            {'status': 'unknown'}, {'status': 'fetched', 'classification': 'access_gate'},
            {'classification': 'access_gate'},
        ]
        for container in ('pages', 'documents'):
            for fields in blocked:
                with self.subTest(container=container, fields=fields):
                    self.write('national_sources.json', {'units': [{
                        'unit_id': '3', 'year': 2027, 'status': 'restricted',
                        container: [{'url': 'https://example.edu.cn/gated.pdf',
                                     'sha256': 'a' * 64, **fields}],
                        'errors': [{'code': 'restricted', 'error': 'SECRET/access-gate.html'}],
                    }]})
                    row = self.store.query(unit_id='3')['units'][0]
                    self.assertEqual(row['source_count'], 0)
                    self.assertEqual(row['sources'], [])
                    self.assertIn('限制访问', row['missing_reason'])
                    self.assertNotIn('已保存官方原文', row['missing_reason'])
                    self.assertEqual(row['errors'][0]['code'], 'restricted')
                    self.assertNotIn('SECRET', json.dumps(row))

    def test_successful_originals_survive_failed_duplicates_and_legacy_metadata(self):
        original = 'https://example.edu.cn/catalog.pdf'
        self.write('national_sources.json', {'units': [{
            'unit_id': '3', 'year': 2027, 'status': 'collected',
            'documents': [
                {'url': original, 'status': 'restricted', 'classification': 'access_gate'},
                {'url': 'https://example.edu.cn/legacy.pdf', 'sha256': 'a' * 64},
                {'url': 'https://example.edu.cn/parsed.pdf', 'status': 'parsed'},
                {'url': 'https://example.edu.cn/verified.pdf', 'status': 'verified'},
            ],
            'pages': [{'url': original, 'status': 'fetched', 'title': '真实目录'},
                      {'url': original, 'status': 'fetched', 'title': '重复记录'}],
        }]})
        row = self.store.query(unit_id='3')['units'][0]
        self.assertEqual(row['source_count'], 4)
        by_url = {source['url']: source for source in row['sources']}
        self.assertEqual(by_url[original]['status'], 'fetched')
        self.assertEqual(by_url[original]['title'], '真实目录')

    def test_stale_collected_status_does_not_hide_access_restrictions(self):
        for fields, errors in [({'status': 'restricted'}, []),
                               ({'status': 'fetched', 'classification': 'access_gate'}, []),
                               ({'status': 'failed'}, [{'code': 'restricted'}])]:
            with self.subTest(fields=fields, errors=errors):
                self.write('national_sources.json', {'units': [{
                    'unit_id': '3', 'year': 2027, 'status': 'collected',
                    'documents': [{'url': 'https://example.edu.cn/gated.pdf', **fields}],
                    'errors': errors,
                }]})
                row = self.store.query(unit_id='3')['units'][0]
                self.assertEqual(row['source_count'], 0)
                self.assertIn('限制访问', row['missing_reason'])
                self.assertNotIn('没有招生', row['missing_reason'])

    def test_failed_attachment_retains_gap_reason_without_claiming_saved_original(self):
        self.write('national_sources.json', {'units': [{
            'unit_id': '3', 'year': 2027, 'status': 'failed',
            'documents': [{'url': 'https://example.edu.cn/unavailable.pdf', 'status': 'failed'}],
            'errors': [{'code': 'fetch_error'}],
        }]})
        row = self.store.query(unit_id='3')['units'][0]
        self.assertEqual(row['source_count'], 0)
        self.assertEqual(row['sources'], [])
        self.assertEqual(row['catalog_status'], 'not_collected')
        self.assertIn('采集缺口', row['missing_reason'])
        self.assertNotIn('已保存', row['missing_reason'])
        self.assertEqual(row['errors'][0]['code'], 'fetch_error')

    def test_invalid_filters_are_rejected_and_pagination_is_bounded(self):
        for args in [{'q': []}, {'status': 'garbage'}, {'unit_id': 'missing'}, {'page': 0}, {'page_size': 201}, {'page': True}]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.store.query(**args)
        page = self.store.query(page=2, page_size=2)
        self.assertEqual([u['unit_id'] for u in page['units']], ['3', '4'])
        self.assertEqual(self.store.query(q='研究所')['total'], 1)
        self.assertEqual(self.store.query(status='not_collected')['total'], 2)

    def test_known_snapshot_shape_errors_are_storage_failures(self):
        valid = {
            'units.json': {'metadata': {}, 'units': self.units},
            'admissions.json': {'metadata': {'reference_year': 2027, 'unit_catalog_status': []}, 'programs': []},
            'national_sources.json': {'metadata': {}, 'units': []},
            'national_catalog.json': {'metadata': {}, 'units': []},
            'annual_registry_2027.json': {'metadata': {}, 'units': [], 'documents': []},
        }
        cases = [
            ('units.json', []),
            ('units.json', {'metadata': {}, 'units': 1}),
            ('units.json', {'metadata': {}, 'units': [{'id': '1'}]}),
            ('admissions.json', {'metadata': [], 'programs': []}),
            ('admissions.json', {'metadata': {'reference_year': []}, 'programs': []}),
            ('admissions.json', {'metadata': {}, 'programs': [None]}),
            ('admissions.json', {'metadata': {'unit_catalog_status': [None]}, 'programs': []}),
            ('national_sources.json', {'metadata': {}, 'units': [None]}),
            ('national_sources.json', {'metadata': {}, 'units': [{'documents': 1}]}),
            ('national_sources.json', {'metadata': {}, 'units': [{'pages': 1}]}),
            ('national_sources.json', {'metadata': {}, 'units': [{'errors': 1}]}),
            ('national_sources.json', {'metadata': {}, 'units': [{'frontier': 1}]}),
            ('national_sources.json', {'metadata': {}, 'units': [{'status': []}]}),
            ('national_sources.json', {'metadata': {}, 'units': [{'errors': [{'code': []}]}]}),
            ('national_catalog.json', {'metadata': {}, 'units': [{'files': 1}]}),
            ('national_catalog.json', {'metadata': {}, 'units': [{'files': [{'status': []}]}]}),
            ('annual_registry_2027.json', {'metadata': {}, 'units': [], 'documents': [None]}),
            ('annual_registry_2027.json', {'metadata': {'annual_scope_status': 'verified'}, 'units': [],
                                           'documents': [{'local_path': None, 'sha256': 'a' * 64}]}),
            ('annual_registry_2027.json', {'metadata': {'annual_scope_status': 'verified'}, 'units': [],
                                           'documents': [{'local_path': '\0', 'sha256': 'a' * 64}]}),
            ('annual_registry_2027.json', {'metadata': {'annual_scope_status': 'verified'}, 'units': [],
                                           'documents': [{'local_path': '', 'sha256': []}]}),
        ]
        for filename, invalid in cases:
            with self.subTest(filename=filename, invalid=invalid):
                for name, value in valid.items():
                    self.write(name, value)
                self.write(filename, invalid)
                with self.assertRaises(SnapshotReadError):
                    CollectionStatus(self.root).query()

    def test_large_admissions_cache_keeps_only_fields_needed_for_progress(self):
        metadata = {'reference_year': 2027, 'unit_catalog_status': [
            {'unit_id': '1', 'year': 2027, 'status': 'verified',
             'expected_records': 1, 'parsed_records': 1, 'verified_records': 1}]}
        self.write('admissions.json', {'metadata': metadata, 'programs': [
            {'id': 'p1', 'unit_id': '1', 'year': 2027, 'large_unused_row': {'text': 'x' * 1000}},
            {'id': 'p2', 'unit_id': '2', 'year': 2026, 'large_unused_row': ['y' * 1000]}]})
        with patch.object(collection_status, 'STREAM_THRESHOLD', 1), \
                patch.object(collection_status, 'read_json', side_effect=AssertionError('whole tree read')):
            value = CollectionStatus(self.root)._read('admissions.json')
        self.assertEqual(value, {'metadata': metadata, 'programs': [
            {'unit_id': '1', 'year': 2027}, {'unit_id': '2', 'year': 2026}]})

    def test_large_admissions_projection_rejects_invalid_shapes_and_trailing_data(self):
        cases = [
            '[]',
            '{"metadata":[],"programs":[]}',
            '{"metadata":{},"programs":{}}',
            '{"metadata":{},"programs":[null]}',
            '{"metadata":{},"programs":[]} trailing',
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                (self.root / 'admissions.json').write_text(raw, 'utf-8')
                with patch.object(collection_status, 'STREAM_THRESHOLD', 1), \
                        self.assertRaises(SnapshotReadError):
                    CollectionStatus(self.root)._read('admissions.json')

    def test_large_admissions_projection_accepts_utf8_bom(self):
        raw = json.dumps({'metadata': {}, 'programs': []}).encode('utf-8')
        (self.root / 'admissions.json').write_bytes(b'\xef\xbb\xbf' + raw)
        with patch.object(collection_status, 'STREAM_THRESHOLD', 1):
            self.assertEqual(CollectionStatus(self.root)._read('admissions.json'),
                             {'metadata': {}, 'programs': []})

    def test_large_admissions_projection_reads_one_file_version(self):
        old = json.dumps({'metadata': {'reference_year': 2027},
                          'programs': [{'unit_id': 'old', 'year': 2027}]}).encode()
        new = json.dumps({'metadata': {'reference_year': 2028},
                          'programs': [{'unit_id': 'new', 'year': 2028}]}).encode()
        with patch.object(collection_status, 'STREAM_THRESHOLD', 1), \
                patch.object(Path, 'open', side_effect=[io.BytesIO(old), io.BytesIO(old), io.BytesIO(new)]) as opened:
            value = CollectionStatus(self.root)._read('admissions.json')
        self.assertEqual(value, {'metadata': {'reference_year': 2027},
                                 'programs': [{'unit_id': 'old', 'year': 2027}]})
        self.assertEqual(opened.call_count, 1)

    def test_large_admissions_projection_rejects_overflow_in_discarded_fields(self):
        cases = [
            '{"metadata":{"ignored":1e9999},"programs":[]}',
            '{"metadata":{},"programs":[{"unit_id":"1","year":2027,"ignored":-1e9999}]}',
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                (self.root / 'admissions.json').write_text(raw, 'utf-8')
                with patch.object(collection_status, 'STREAM_THRESHOLD', 1), \
                        self.assertRaises(SnapshotReadError):
                    CollectionStatus(self.root)._read('admissions.json')

    def test_storage_shape_error_is_service_unavailable(self):
        self.write('units.json', [])
        with self.assertRaises(ServiceError) as error:
            AdmissionService(self.root).collection_status()
        self.assertEqual((error.exception.code, error.exception.status), ('data_unavailable', 503))

    def test_replayed_index_coverage_is_not_an_annual_official_total(self):
        value = self.store.query()
        self.assertIsNone(value['summary']['annual_official_total'])
        self.assertEqual(value['summary']['annual_confirmed_units'], 0)
        self.write('annual_registry_2027.json', {'metadata': {'annual_scope_status': 'provisional'},
            'units': [{'unit_id': '1', 'annual_status': 'confirmed'}, {'unit_id': '3', 'annual_status': 'unknown'}]})
        value = self.store.query()
        self.assertEqual(value['summary']['annual_confirmed_units'], 1)
        self.assertIsNone(value['summary']['annual_official_total'])

    def test_stale_years_and_self_reported_counts_do_not_pass_directory_checks(self):
        self.write('national_sources.json', {'units': [{'unit_id': '4', 'year': 2026, 'status': 'collected'}]})
        self.write('admissions.json', {'metadata': {'reference_year': 2027, 'unit_catalog_status': [
            {'unit_id': '1', 'year': 2027, 'status': 'verified', 'expected_records': 300, 'parsed_records': 300, 'verified_records': 300}]},
            'programs': [{'id': 'p1', 'unit_id': '1', 'year': 2027}]})
        value = self.store.query(unit_id='1')['units'][0]
        self.assertEqual(value['catalog_status'], 'partial')
        self.assertEqual(self.store.query(unit_id='4')['units'][0]['collection_status'], 'pending')

    def test_annual_total_requires_original_replay_and_invalidates_changed_proof(self):
        source = self.root / 'annual-proof.html'
        source.write_text('original', 'utf-8')
        self.write('annual_registry_2027.json', {'metadata': {'annual_scope_status': 'verified', 'official_total': 4},
            'documents': [{'local_path': source.name, 'sha256': 'a'*64}],
            'units': [{'unit_id': str(i), 'annual_status': 'confirmed'} for i in range(1, 5)]})
        with patch('collectors.annual_registry.verify_annual_registry', side_effect=[True, False]) as verify:
            self.assertEqual(self.store.query()['summary']['annual_official_total'], 4)
            self.assertEqual(self.store.query()['summary']['annual_official_total'], 4)
            self.assertEqual(verify.call_count, 1)
            source.write_text('tampered', 'utf-8')
            summary = self.store.query()['summary']
            self.assertIsNone(summary['annual_official_total'])
            self.assertEqual(summary['annual_confirmed_units'], 0)
            self.assertEqual(verify.call_count, 2)

    def test_annual_missing_counts_exclude_unlisted_index_records(self):
        self.write('annual_registry_2027.json', {'metadata': {'annual_scope_status': 'verified', 'official_total': 3},
            'units': [{'unit_id':str(i),'annual_status':'confirmed' if i<4 else 'not_listed'} for i in range(1,5)]})
        with patch('collectors.annual_registry.verify_annual_registry', return_value=True):
            summary = self.store.query()['summary']
        self.assertEqual(summary['annual_units_with_programs'], 2)
        self.assertEqual(summary['annual_units_without_programs'], 1)
        self.assertEqual(summary['not_listed_index_units'], 1)

    def test_file_parse_gaps_remain_separate_from_catalogue_ingestion(self):
        self.write('national_catalog.json', {'units':[{'unit_id':'3','year':2027,'program_count':2,
            'files':[{'status':'needs_adapter','url':'https://example.edu.cn/a.pdf','parser':'pdf_table_v1',
                      'reason':'C:/Users/private/token.txt','local_path':'SECRET'},
                     {'status':'ready','url':'https://example.edu.cn/b.pdf'},
                     {'status':'failure','url':'javascript:alert(1)'}]}]})
        row=self.store.query(unit_id='3')['units'][0]
        self.assertEqual(row['program_count'],0)
        self.assertEqual(row['parsing_status'],'needs_adapter')
        self.assertEqual(row['parsing_summary']['ready'],1)
        self.assertEqual(len(row['parsing_gaps']),1)
        self.assertNotIn('private',json.dumps(row))
        self.assertNotIn('SECRET',json.dumps(row))


if __name__ == '__main__':
    unittest.main()
