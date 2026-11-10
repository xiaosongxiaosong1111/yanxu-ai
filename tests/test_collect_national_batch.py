import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import ANY, Mock, patch

from public_sources import SourceError
from tools.collect_national_batch import main, run_batch


class NationalBatchRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.units = {'metadata': {'total': 3}, 'units': [
            {'id': identity, 'name': '大学' + identity, 'unit_code': '10' + identity,
             'source_url': 'https://yz.chsi.com.cn/sch/schoolInfo--schId-' + identity + '.dhtml',
             'official_url': 'https://u' + identity + '.edu.cn/'} for identity in ('1', '2', '3')]}
        self.batch = self.write('docs/batch.json', {'batch_id': 'b001',
            'expansion': [{'unit_id': '2', 'unit_code': '102', 'year': 2027}],
            'backfill': [{'unit_id': '1', 'unit_code': '101', 'year': 2027}]})
        self.units_path = self.write('data/units.json', self.units)
        self.inventory = self.write('data/batch_runs/b001/inventory.json', {
            'units': [{'unit_id': '1', 'candidates': [], 'pages': []},
                      {'unit_id': '2', 'candidates': [], 'pages': []}]})
        self.output = self.root / 'data/batch_runs/b001/sources.json'
        self.snapshots = self.root / 'data/snapshots/national-b001'
        self.committed = patch('tools.collect_national_batch._committed_batch',
                               side_effect=lambda root, path: path.read_bytes())
        self.committed.start()
        self.addCleanup(self.committed.stop)
        self.pacer = Mock(return_value={'url': 'https://u1.edu.cn/new'})
        self.factory = Mock(return_value=self.pacer)
        self.arguments = None

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def collect(self, registry, inventory, output, snapshot_dir, **kwargs):
        self.arguments = (registry, inventory, output, snapshot_dir, kwargs)
        return {'metadata': {'catalog_complete': False}, 'units': [
            {'unit_id': row['id'], 'status': 'collected', 'pages': [], 'documents': []}
            for row in registry['units']]}

    def run_batch(self, **kwargs):
        return run_batch(self.root, self.batch, self.inventory, self.output,
                         self.snapshots, collector=self.collect,
                         pacer_factory=self.factory, **kwargs)

    def add_cache(self, raw, *, mime='text/html', url='https://u1.edu.cn/catalog',
                  relative='data/snapshots/old/catalog.html', field='snapshot_path'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        value = json.loads(self.inventory.read_text('utf-8'))
        value['units'][0]['pages'] = [{'status': 'fetched', 'url': url,
            field: relative, 'sha256': hashlib.sha256(raw).hexdigest(),
            'content_type': mime, 'checked_at': '2026-11-01T00:00:00+00:00'}]
        self.inventory.write_text(json.dumps(value), encoding='utf-8')
        return path

    def test_two_unit_scope_uses_full_registry_without_mutating_inputs(self):
        before = self.units_path.read_bytes()
        result = self.run_batch()
        registry, _, _, _, options = self.arguments
        self.assertEqual([unit['id'] for unit in registry['units']], ['2', '1'])
        self.assertEqual(self.units_path.read_bytes(), before)
        self.assertEqual(options['workers'], 2)
        self.assertEqual(options['project_root'], self.root)
        self.assertEqual(options['year'], 2027)
        self.factory.assert_called_once_with(interval=2, fetcher=ANY)
        metadata = result['metadata']['batch_runner']
        self.assertEqual(metadata['run_status'], 'completed')
        self.assertEqual(metadata['final_status_counts'], {'collected': 2})
        self.assertEqual(metadata['source_files']['batch']['sha256'], hashlib.sha256(self.batch.read_bytes()).hexdigest())
        self.assertFalse(metadata['parameters']['network'])
        self.assertEqual(json.loads(self.output.read_text('utf-8')), result)

    def test_offline_fetcher_never_calls_network_and_reports_disabled_not_restricted(self):
        self.run_batch()
        fetcher = self.arguments[-1]['fetcher']
        for url in ('https://u1.edu.cn/new', 'https://yz.chsi.com.cn/new'):
            with self.subTest(url=url), self.assertRaises(SourceError) as error:
                fetcher(url, self.units['units'][0])
            self.assertEqual(error.exception.code, 'network_disabled')
        self.pacer.assert_not_called()

    def test_network_opt_in_uses_pacer_but_still_blocks_chsi(self):
        self.run_batch(network=True, interval=3, workers=1)
        self.factory.assert_called_once_with(interval=3, fetcher=ANY)
        fetcher = self.arguments[-1]['fetcher']
        self.assertEqual(fetcher('https://u1.edu.cn/new', self.units['units'][0])['url'], 'https://u1.edu.cn/new')
        with self.assertRaises(SourceError) as error:
            fetcher('https://yz.chsi.com.cn/new', self.units['units'][0])
        self.assertEqual(error.exception.code, 'network_disabled')
        self.assertEqual(self.pacer.call_count, 1)

    def test_network_pacer_injects_per_hop_chsi_deny_into_original_transport(self):
        self.run_batch(network=True)
        guarded = self.factory.call_args.kwargs.get('fetcher')
        self.assertTrue(callable(guarded))
        with patch('tools.collect_national_batch.fetch_source', create=True) as source:
            guarded('https://u1.edu.cn/new', self.units['units'][0])
        source.assert_called_once_with('https://u1.edu.cn/new', self.units['units'][0],
                                       blocked_hosts=('chsi.com.cn',))

    def test_cached_html_reused_without_network_and_input_is_preserved(self):
        raw = '<h1>大学1 2027年硕士招生专业目录</h1>'.encode()
        self.add_cache(raw, url=self.units['units'][0]['source_url'])
        before = self.inventory.read_bytes()
        self.run_batch()
        page = self.arguments[-1]['fetcher'](self.units['units'][0]['source_url'], self.units['units'][0])
        self.assertEqual(page['raw'], raw)
        self.assertEqual(page['checked_at'], '2026-11-01T00:00:00+00:00')
        self.assertEqual(self.inventory.read_bytes(), before)
        self.assertEqual(len(self.arguments[1]['units'][0]['pages']), 1)
        self.pacer.assert_not_called()

    def test_cached_pdf_uses_real_mime_and_local_path_not_html_cache_branch(self):
        raw = b'%PDF-1.4\noriginal'
        self.add_cache(raw, mime='application/pdf', relative='data/snapshots/old/catalog.pdf', field='local_path')
        self.run_batch()
        page = self.arguments[-1]['fetcher']('https://u1.edu.cn/catalog', self.units['units'][0])
        self.assertEqual(page['raw'], raw)
        self.assertEqual(page['text'], '')
        self.assertEqual(page['content_type'], 'application/pdf')
        self.assertEqual(self.arguments[1]['units'][0]['pages'], [])

    def test_cached_binary_retains_original_response_headers_and_detection(self):
        self.add_cache(b'%PDF-1.4\noriginal', mime='application/pdf')
        value = json.loads(self.inventory.read_text('utf-8'))
        headers = {'response_content_type': 'text/html; charset=UTF-8',
            'response_content_disposition': 'attachment; filename="catalog.pdf"',
            'type_detection': 'pdf_magic'}
        value['units'][0]['pages'][0].update(headers)
        self.inventory.write_text(json.dumps(value), encoding='utf-8')
        self.run_batch()
        page = self.arguments[-1]['fetcher']('https://u1.edu.cn/catalog', self.units['units'][0])
        self.assertEqual({name: page.get(name) for name in headers}, headers)

    def test_cache_changed_after_validation_fails_without_network_fallback(self):
        path = self.add_cache(b'<h1>original</h1>')
        self.run_batch(network=True)
        path.write_bytes(b'<h1>tampered</h1>')
        with self.assertRaises(SourceError) as error:
            self.arguments[-1]['fetcher']('https://u1.edu.cn/catalog', self.units['units'][0])
        self.assertEqual(error.exception.code, 'invalid_cache')
        self.pacer.assert_not_called()

    def test_cache_tampering_fails_without_network_fallback(self):
        path = self.add_cache(b'<h1>original</h1>')
        path.write_bytes(b'<h1>tampered</h1>')
        with self.assertRaisesRegex(ValueError, 'SHA'):
            self.run_batch(network=True)
        self.pacer.assert_not_called()
        self.assertIsNone(self.arguments)

    def test_cache_outside_snapshot_root_is_rejected(self):
        self.add_cache(b'private', relative='data/runtime/private.html')
        with self.assertRaises(ValueError):
            self.run_batch()

    def test_foreign_or_duplicate_inventory_units_are_rejected(self):
        for rows in ([{'unit_id': '3'}], [{'unit_id': '1'}, {'unit_id': '1'}]):
            with self.subTest(rows=rows):
                self.write('data/batch_runs/b001/inventory.json', {'units': rows})
                with self.assertRaises(ValueError):
                    self.run_batch()

    def test_invalid_batch_and_registry_shapes_fail_before_collection(self):
        original_batch = json.loads(self.batch.read_text('utf-8'))
        for registry in ([], {'units': None}):
            with self.subTest(registry=registry), self.assertRaises(ValueError):
                self.write('data/units.json', registry)
                self.run_batch()
        self.write('data/units.json', self.units)
        for batch in ({}, {**original_batch, 'expansion': None},
                      {**original_batch, 'expansion': [None]},
                      {**original_batch, 'expansion': [], 'backfill': []}):
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                self.write('docs/batch.json', batch)
                self.run_batch()

    def test_invalid_cache_metadata_and_payloads_are_rejected(self):
        for key, value in (('snapshot_path', None), ('sha256', 'wrong'),
                           ('content_type', None), ('url', 'file:///private')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.add_cache(b'<h1>original</h1>')
                inventory = json.loads(self.inventory.read_text('utf-8'))
                inventory['units'][0]['pages'][0][key] = value
                self.write('data/batch_runs/b001/inventory.json', inventory)
                self.run_batch()
        for raw, mime in ((b'', 'text/html'), (b'not pdf', 'application/pdf'),
                          (b'%PDF-1.4\noriginal', 'text/html')):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.add_cache(raw, mime=mime)
                self.run_batch()

    def test_unfetched_pages_are_not_read_or_promoted_to_originals(self):
        self.write('data/batch_runs/b001/inventory.json', {'units': [
            {'unit_id': '1', 'pages': [{'status': 'failed', 'snapshot_path': 'private'}]}]})
        self.run_batch()
        self.assertEqual(self.arguments[1]['units'][0]['pages'], [])
        self.assertEqual(self.arguments[-1]['fetcher'].__name__, 'fetch')

    def test_unknown_duplicate_wrong_year_and_code_batch_entries_are_rejected(self):
        for entry in ({'unit_id': '3', 'unit_code': 'wrong', 'year': 2027},
                      {'unit_id': '404', 'year': 2027}, {'unit_id': '1', 'year': 2027},
                      {'unit_id': '2', 'unit_code': '102', 'year': 2026}):
            with self.subTest(entry=entry):
                value = json.loads(self.batch.read_text('utf-8'))
                value['expansion'] = [entry]
                self.batch.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.run_batch()

    def test_unsafe_output_snapshot_and_input_collisions_are_rejected(self):
        for output, snapshot in (
            (self.root / 'data/admissions.json', self.snapshots),
            (self.root.parent / 'outside.json', self.snapshots),
            (self.output, self.root / 'data/runtime/runner'),
            (self.inventory, self.snapshots),
            (self.output, self.root / 'data/snapshots')):
            with self.subTest(output=output, snapshot=snapshot), self.assertRaises(ValueError):
                run_batch(self.root, self.batch, self.inventory, output, snapshot,
                          collector=self.collect, pacer_factory=self.factory)

    def test_invalid_limits_fail_before_collection(self):
        for options in ({'interval': 1.9}, {'interval': float('nan')}, {'workers': 3},
                        {'workers': True}, {'max_pages': 0}, {'max_depth': -1}, {'network': 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_batch(**options)

    def test_atomic_stage_cannot_overwrite_input_inventory(self):
        inventory = self.root / 'data/batch_runs/b001/result.json.tmp'
        inventory.write_bytes(self.inventory.read_bytes())
        before = inventory.read_bytes()
        with self.assertRaises(ValueError):
            run_batch(self.root, self.batch, inventory,
                self.root / 'data/batch_runs/b001/result.json', self.snapshots,
                collector=self.collect, pacer_factory=self.factory)
        self.assertEqual(inventory.read_bytes(), before)

    def test_collector_failure_preserves_failed_run_metadata_and_reraises(self):
        with self.assertRaisesRegex(RuntimeError, 'test failure'):
            run_batch(self.root, self.batch, self.inventory, self.output, self.snapshots,
                      collector=Mock(side_effect=RuntimeError('test failure')), pacer_factory=self.factory)
        saved = json.loads(self.output.read_text('utf-8'))
        self.assertEqual(saved['metadata']['batch_runner']['run_status'], 'failed')
        self.assertEqual(saved['metadata']['batch_runner']['error_type'], 'RuntimeError')

    def test_real_collector_offline_keeps_network_disabled_reason_and_cached_original(self):
        raw = '<h1>大学1 2027年硕士研究生招生专业目录</h1>'.encode()
        self.add_cache(raw, url='https://u1.edu.cn/')
        result = run_batch(self.root, self.batch, self.inventory, self.output, self.snapshots,
                           pacer_factory=self.factory, max_pages=3, max_depth=0)
        first = next(row for row in result['units'] if row['unit_id'] == '1')
        self.assertTrue(first['documents'])
        self.assertTrue(any(error['code'] == 'network_disabled' for error in first['errors']))
        self.assertFalse(any(error['code'] == 'restricted' for error in first['errors']))
        self.pacer.assert_not_called()

    def test_committed_batch_validation_rejects_uncommitted_bytes(self):
        self.committed.stop()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'add', 'docs/batch.json'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', 'test batch'], check=True, capture_output=True)
        self.run_batch()
        self.batch.write_bytes(self.batch.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'HEAD'):
            self.run_batch()

    def test_cli_default_offline_and_explicit_network_flag(self):
        arguments = ['--root', str(self.root), '--batch', str(self.batch), '--inventory', str(self.inventory),
                     '--output', str(self.output), '--snapshot-dir', str(self.snapshots)]
        with patch('tools.collect_national_batch.run_batch', return_value={'metadata': {}}) as runner:
            self.assertEqual(main(arguments), 0)
            self.assertFalse(runner.call_args.kwargs['network'])
            self.assertEqual(main(arguments + ['--network']), 0)
            self.assertTrue(runner.call_args.kwargs['network'])


if __name__ == '__main__':
    unittest.main()
