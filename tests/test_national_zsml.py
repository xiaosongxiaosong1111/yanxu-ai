import hashlib
import json
from pathlib import Path
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from collectors.national_zsml import catalog_base, discover_candidates, collect_candidates
import collectors.national_zsml as national_zsml


UNIT = {'id': '123', 'name': '甲大学', 'unit_code': '10001', 'province': '北京',
        'official_url': 'https://www.example.edu.cn/'}
ENTRY = 'https://yz.example.edu.cn/zsbm/zsml/sszsml/index/2027'


def original(root, text, url='https://yz.chsi.com.cn/zsgs/zsjz/detail.action?schCode=10001&zsgsId=9', filename='source.html'):
    path = root/'data'/filename; path.parent.mkdir(parents=True, exist_ok=True)
    raw = text.encode(); path.write_bytes(raw)
    return {'id': filename, 'unit_id': '123', 'url': url, 'local_path': path.relative_to(root).as_posix(),
            'sha256': hashlib.sha256(raw).hexdigest(), 'content_type': 'text/html', 'year': 2027, 'kind': 'charter'}


class NationalZsmlTests(unittest.TestCase):
    def test_base_keeps_system_prefix_and_rejects_wrong_year_credentials_and_unrelated_paths(self):
        self.assertEqual(catalog_base(ENTRY), 'https://yz.example.edu.cn/zsbm')
        self.assertEqual(catalog_base(ENTRY.replace('https:', 'http:')), 'http://yz.example.edu.cn/zsbm')
        for url in [ENTRY.replace('2027', '2026'), ENTRY.replace('2027', '20270'),
                    ENTRY.replace('yz.example', 'user:pass@yz.example'), 'https://localhost/zsml/sszsml/index/2027',
                    'https://yz.example.edu.cn/admin', 'https://[broken']:
            self.assertIsNone(catalog_base(url))

    def test_all_units_scan_raw_hashes_and_deduplicate_same_entry_from_multiple_publications(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            html = '<h1>甲大学2027年硕士研究生招生章程</h1><p>招生目录：<a href="'+ENTRY+'">2027专业目录</a></p>'
            doc = original(root, html)
            national = {'units': [{'unit_id': '123', 'pages': [doc, doc]}, {'unit_id': '123', 'pages': [doc]}]}
            discovered = discover_candidates({'units': [UNIT]}, national, root)
            self.assertEqual(len(discovered['candidates']), 1)
            self.assertEqual(discovered['metadata']['scanned_units'], 1)
            candidate = discovered['candidates'][0]
            self.assertEqual(candidate['base_url'], 'https://yz.example.edu.cn/zsbm')
            self.assertEqual(candidate['unit_id'], '123')
            self.assertEqual(candidate['evidence'][0]['sha256'], doc['sha256'])
            (root/doc['local_path']).write_text('tampered', 'utf-8')
            self.assertEqual(discover_candidates({'units': [UNIT]}, national, root)['candidates'], [])

    def test_wrong_school_chsi_identifiers_and_cross_school_catalogs_cannot_bind_a_unit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for url, entry in [
                ('https://yz.chsi.com.cn/zsgs/zsjz/detail.action?schCode=10002', ENTRY),
                ('https://yz.chsi.com.cn/sch/schoolInfo--schId-999.dhtml', ENTRY),
                ('https://yz.chsi.com.cn/zsgs/zsjz/detail.action?schCode=10001', ENTRY.replace('example.edu.cn', 'other.edu.cn'))]:
                doc = original(root, '<h1>甲大学2027年硕士研究生招生章程</h1><a href="'+entry+'">目录</a>', url)
                found = discover_candidates({'units': [UNIT]}, {'units': [{'unit_id': '123', 'pages': [doc]}]}, root)
                self.assertEqual(found['candidates'], [])

    def test_unknown_official_domain_needs_own_annual_publication_and_preserves_binding_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = original(root, '<h1>甲大学2027年硕士研究生招生章程</h1><p>专业目录：'+ENTRY+'</p>')
            result = discover_candidates({'units': [{**UNIT, 'official_url': None}]},
                {'units': [{'unit_id': '123', 'pages': [doc]}]}, root)
            self.assertEqual(len(result['candidates']), 1)
            self.assertTrue(result['candidates'][0]['binding_required'])
            self.assertEqual(result['candidates'][0]['binding_evidence']['url'], doc['url'])

    def test_pdfs_are_inspected_for_original_directory_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = original(root, '%PDF-1.0 original', 'https://www.example.edu.cn/2027-charter.pdf', filename='charter.pdf')
            with patch('collectors.national_zsml.extract_pdf_text', return_value='甲大学2027年硕士招生章程\n目录 '+ENTRY) as extract:
                result = discover_candidates({'units': [UNIT]}, {'units': [{'unit_id': '123', 'pages': [doc]}]}, root)
                replay = discover_candidates({'units': [UNIT]}, {'units': [{'unit_id': '123', 'pages': [doc]}]}, root)
            self.assertEqual(len(result['candidates']), 1)
            self.assertEqual(len(replay['candidates']), 1)
            self.assertEqual(extract.call_count, 1)

    def test_failed_collection_is_saved_with_its_scope_and_never_reported_verified(self):
        candidate = {'unit_id': '123', 'unit_name': '甲大学', 'base_url': 'https://yz.example.edu.cn',
                     'unit': UNIT, 'entry_url': ENTRY, 'evidence': [], 'binding_required': False}
        with tempfile.TemporaryDirectory() as folder, patch('collectors.national_zsml.collect_zsml', side_effect=ValueError('incomplete public tree')):
            root = Path(folder)
            events = []
            result = collect_candidates([candidate], root, workers=1, progress=lambda rows: events.append(len(rows)))
            self.assertEqual(result['units'][0]['state'], 'failed')
            self.assertIn('incomplete public tree', result['units'][0]['error'])
            self.assertFalse(result['metadata']['catalog_complete'])
            self.assertEqual(events, [1])

    def test_failed_pdf_extraction_and_outside_data_paths_are_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = original(root, '%PDF-corrupt', 'https://www.example.edu.cn/2027.pdf', filename='bad.pdf')
            outside = {**doc, 'id': 'outside', 'url': 'https://www.example.edu.cn/other.pdf', 'local_path': '../outside.pdf'}
            with patch('collectors.national_zsml.extract_pdf_text', side_effect=ValueError('PDF extraction failed')):
                result = discover_candidates({'units': [UNIT]}, {'units': [
                    {'unit_id': 'missing-index-unit', 'pages': [doc]}, {'unit_id': '123', 'pages': [doc, outside]}]}, root)
            self.assertEqual(result['candidates'], [])
            self.assertEqual(len(result['errors']), 2)

    def test_live_pipeline_replays_whole_tree_and_resumes_only_unchanged_originals(self):
        tree = [{'id': '001', 'name': '001 计算机学院', 'children': [
                 {'id': '085404', 'name': '085404 计算机技术', 'scode': '21'}]}]
        html = '''<table id="zsml-table"><tr><th>专业及方向</th><th>初试科目</th></tr>
        <tr class="zsml-talbe-yx-zy"><td>085404（专业学位）计算机技术</td>
        <td rowspan="2">①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr>
        <tr class="zsml-talbe-yx-zy-yjfx"><td>01（全日制）计算机技术</td></tr></table>'''
        candidate = {'unit_id': '123', 'unit_name': '甲大学', 'base_url': 'https://yz.example.edu.cn',
                     'unit': UNIT, 'entry_url': ENTRY, 'evidence': [], 'binding_required': False}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def fetch(url, directory, unit, year, kind, title, **kwargs):
                text = json.dumps(tree, ensure_ascii=False) if url.endswith('getTreeData') else html
                filename = 'tree.json' if url.endswith('getTreeData') else 'page.html'
                document = original(root, text, url, filename)
                return {**document, 'kind': 'catalog', 'status': 'parsed'}, text.encode()
            with patch('collectors.catalog_zsml.fetch_snapshot', side_effect=fetch):
                result = collect_candidates([candidate], root, workers=1)
            self.assertEqual(result['units'][0]['state'], 'verified')
            self.assertEqual(result['units'][0]['programs'], 1)
            with patch('collectors.national_zsml.collect_zsml', side_effect=AssertionError('cached valid originals must not refetch')):
                resumed = collect_candidates([candidate], root, workers=1)
            self.assertTrue(resumed['units'][0]['resumed'])
            (root/'data/page.html').write_text('tampered', 'utf-8')
            with patch('collectors.catalog_zsml.fetch_snapshot', side_effect=fetch):
                repaired = collect_candidates([candidate], root, workers=1)
            self.assertEqual(repaired['units'][0]['state'], 'verified')
            self.assertFalse(repaired['units'][0].get('resumed', False))
            (root/repaired['units'][0]['result_path']).write_text('{half-written', 'utf-8')
            with patch('collectors.catalog_zsml.fetch_snapshot', side_effect=fetch):
                recovered = collect_candidates([candidate], root, workers=1)
            self.assertEqual(recovered['units'][0]['state'], 'verified')

    def test_partial_verification_and_already_collected_units_stay_distinct(self):
        candidate = {'unit_id': '123', 'unit_name': '甲大学', 'base_url': 'https://yz.example.edu.cn',
                     'unit': UNIT, 'entry_url': ENTRY, 'evidence': [], 'binding_required': False}
        incomplete = {'programs': [], 'documents': [], 'issues': ['缺少院系页'],
                      'expected_records': 2, 'expected_nodes': 1, 'fetched_nodes': 0, 'retest_rules': []}
        with tempfile.TemporaryDirectory() as folder, patch('collectors.national_zsml.collect_zsml', return_value=incomplete):
            root = Path(folder)
            partial = collect_candidates([candidate], root, workers=1)
            self.assertEqual(partial['units'][0]['state'], 'partial')
            written = json.loads((root/partial['units'][0]['result_path']).read_text('utf-8'))
            self.assertFalse(written['verification']['unit_reconciles'])
            skipped = collect_candidates([candidate], root, skip={'123'})
            self.assertEqual(skipped['units'][0]['state'], 'already_collected')

    def test_official_domain_bindings_are_replayed_instead_of_trusting_manifest_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binding_doc = original(root, '<h1>甲大学2027年硕士研究生招生</h1><a href="https://www.example.edu.cn/">甲大学官方网站</a>', filename='binding.html')
            publication = original(root, '<h1>甲大学2027年硕士招生目录</h1><a href="'+ENTRY+'">目录</a>',
                                   'https://www.example.edu.cn/2027/catalog.htm', filename='catalog.html')
            binding = {'url': 'https://www.example.edu.cn/', 'evidence': binding_doc}
            row = {'unit_id': '123', 'pages': [publication], 'bindings': [binding]}
            found = discover_candidates({'units': [{**UNIT, 'official_url': None}]}, {'units': [row]}, root)
            self.assertEqual(len(found['candidates']), 1)
            forged = {**row, 'bindings': [{**binding, 'url': 'https://other.edu.cn/'}]}
            self.assertEqual(discover_candidates({'units': [{**UNIT, 'official_url': None}]}, {'units': [forged]}, root)['candidates'], [])

    def test_separate_source_groups_can_bind_only_through_the_real_saved_chsi_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binding_doc = original(root, '<h1>甲大学2027年硕士研究生招生</h1><a href="https://www.example.edu.cn/">甲大学官方网站</a>', filename='binding.html')
            publication = original(root, '<h1>甲大学2027年硕士招生目录</h1><a href="'+ENTRY+'">目录</a>',
                                   'https://www.example.edu.cn/2027/catalog.htm', filename='catalog.html')
            rows = [{'unit_id': '123', 'pages': [binding_doc]}, {'unit_id': '123', 'pages': [publication]}]
            found = discover_candidates({'units': [{**UNIT, 'official_url': None}]}, {'units': rows}, root)
            self.assertEqual(len(found['candidates']), 1)
            self.assertEqual(found['candidates'][0]['binding_evidence']['url'], binding_doc['url'])

    def test_homepage_pass_reuses_shared_bounded_collector_with_official_entry_first(self):
        registry = {'units': [UNIT, {**UNIT, 'id': 'unknown', 'official_url': None}]}
        calls = []
        def collect(unit, inventory, directory, **kwargs):
            calls.append((unit, inventory, kwargs))
            return {'unit_id': unit['id'], 'unit_name': unit['name'], 'pages': [], 'status': 'collected', 'errors': []}
        with tempfile.TemporaryDirectory() as folder, patch('collectors.national_zsml.collect_unit', side_effect=collect):
            root = Path(folder)
            doc = original(root, '<title>甲大学</title>', UNIT['official_url'])
            result = national_zsml.crawl_homepages(registry, {'units': [{'unit_id': '123', 'pages': [doc]}]}, root, workers=1, max_pages=6)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2]['previous']['frontier'][0]['url'], UNIT['official_url'])
        self.assertEqual(calls[0][2]['previous']['frontier'][0]['priority'], -100)
        self.assertEqual(calls[0][1]['pages'][0]['snapshot_path'], doc['local_path'])
        self.assertEqual(result['metadata']['official_entry_units'], 1)
        self.assertFalse(result['metadata']['catalog_complete'])

    def test_homepage_originals_are_actually_saved_and_a_missing_resume_file_is_recovered(self):
        unit = {**UNIT, 'source_url': 'https://yz.chsi.com.cn/sch/schoolInfo--schId-123.dhtml'}
        raw = ('<h1>甲大学2027年硕士招生目录</h1><a href="'+ENTRY+'">2027专业目录</a>').encode()
        def fetch(url, owner):
            return {'url': url, 'raw': raw, 'text': raw.decode(), 'content_type': 'text/html',
                    'sha256': hashlib.sha256(raw).hexdigest(), 'checked_at': '2026-10-30T01:00:00+00:00'}
        with tempfile.TemporaryDirectory() as folder, patch('collectors.national_zsml.HostPacer', return_value=fetch):
            root = Path(folder); events = []
            first = national_zsml.crawl_homepages({'units': [unit]}, {'units': []}, root, workers=1,
                                                max_pages=1, progress=lambda rows: events.append(len(rows)))
            doc = first['units'][0]['pages'][0]
            self.assertEqual(doc['url'], unit['official_url'])
            self.assertEqual((root/doc['local_path']).read_bytes(), raw)
            self.assertEqual(events, [1])
            (root/doc['local_path']).unlink()
            resumed = national_zsml.crawl_homepages({'units': [unit]}, {'units': []}, root, workers=1, max_pages=1)
            self.assertTrue((root/doc['local_path']).is_file())
            self.assertGreaterEqual(resumed['metadata']['originals'], 1)

    def test_homepage_pass_keeps_failures_visible_and_validates_request_budget(self):
        with tempfile.TemporaryDirectory() as folder, patch('collectors.national_zsml.collect_unit', side_effect=ValueError('official page unavailable')):
            root = Path(folder)
            failed = national_zsml.crawl_homepages({'units': [UNIT]}, {'units': []}, root, workers=1, max_pages=1)
            self.assertEqual(failed['units'][0]['status'], 'failed')
            self.assertIn('official page unavailable', failed['units'][0]['errors'][0]['message'])
            with self.assertRaises(ValueError):
                national_zsml.crawl_homepages({'units': [UNIT]}, {'units': []}, root, max_pages=0)

    def test_parallel_snapshot_writers_both_finish_and_leave_one_complete_json(self):
        gate = threading.Barrier(2)
        write_text = Path.write_text
        def synchronized_write(path, value, *args, **kwargs):
            result = write_text(path, value, *args, **kwargs)
            if path.suffix == '.tmp':
                gate.wait(timeout=3)
            return result
        with tempfile.TemporaryDirectory() as folder, patch.object(Path, 'write_text', synchronized_write):
            path = Path(folder)/'state.json'
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [pool.submit(national_zsml._write, path, {'value': value}) for value in (1, 2)]
                for job in jobs:
                    job.result()
            self.assertIn(json.loads(path.read_text('utf-8'))['value'], (1, 2))


if __name__ == '__main__':
    unittest.main()
