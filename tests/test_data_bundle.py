from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile


MODULE_PATH = Path(__file__).resolve().parents[1] / 'tools/prepare_data.py'
SPEC = importlib.util.spec_from_file_location('prepare_data', MODULE_PATH)
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)
BUILD_SPEC = importlib.util.spec_from_file_location('build_public_bundle', MODULE_PATH.with_name('build_public_bundle.py'))
builder = importlib.util.module_from_spec(BUILD_SPEC)
BUILD_SPEC.loader.exec_module(builder)


class PublicDataBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packs = self.root / 'bundles'
        self.packs.mkdir()
        self.target = self.root / 'fresh-project'
        self.entries = {'data/units.json': b'{"units":[]}', 'data/snapshots/catalog/source.html': '官方目录'.encode()}
        self.manifest = self.fixture(self.entries)

    def fixture(self, entries, *, extra=None, symlink=False):
        archive = self.packs / 'catalog-001.zip'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
            for name, raw in entries.items():
                if symlink:
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    output.writestr(info, raw)
                else: output.writestr(name, raw)
            if extra: output.writestr(extra, b'not-in-manifest')
        package = {'name': archive.name, 'size': archive.stat().st_size,
                   'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                   'files': [{'path': name, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                             for name, raw in entries.items()]}
        manifest = {'schema_version': 1, 'packages': [package]}
        self.write_manifest(manifest)
        return manifest

    def write_manifest(self, manifest):
        (self.packs / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def test_offline_restore_is_complete_and_repeating_skips_identical_files(self):
        result = bundle.restore(self.packs, self.target)
        self.assertEqual(result['restored'], 2)
        self.assertEqual(result['skipped'], 0)
        for path, raw in self.entries.items(): self.assertEqual((self.target / path).read_bytes(), raw)
        result = bundle.restore(self.packs, self.target)
        self.assertEqual(result['restored'], 0)
        self.assertEqual(result['skipped'], 2)
        self.assertFalse((self.target / 'data/runtime').exists())

    def test_bad_archive_digest_is_rejected_before_any_target_files(self):
        archive = self.packs / 'catalog-001.zip'
        archive.write_bytes(archive.read_bytes() + b'changed')
        with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.assertFalse(self.target.exists())

    def test_bad_member_digest_cannot_partially_restore_an_earlier_file(self):
        self.manifest['packages'][0]['files'][1]['sha256'] = '0' * 64
        self.write_manifest(self.manifest)
        with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.assertFalse(self.target.exists())

    def test_an_existing_different_file_is_preserved_and_no_new_file_is_added(self):
        target = self.target / 'data/units.json'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'keep-private-local-data')
        with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.assertEqual(target.read_bytes(), b'keep-private-local-data')
        self.assertFalse((self.target / 'data/snapshots').exists())

    def test_traversal_and_platform_alias_paths_are_rejected(self):
        for path in ('../escape', '/absolute', 'data/../escape', 'data\\escape', 'C:/escape',
                     'data//empty', 'data/./dot', 'data/NUL.txt', 'data/folder./x', 'data/file:stream'):
            with self.subTest(path=path):
                self.fixture({path: b'unsafe'})
                with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
                self.assertFalse(self.target.exists())

    def test_only_data_files_are_allowed_and_runtime_or_bundles_are_never_targets(self):
        for path in ('settings.sqlite', 'server.py', 'data/runtime/settings.sqlite', 'data/bundles/new.zip'):
            with self.subTest(path=path):
                self.fixture({path: b'unsafe'})
                with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)

    def test_extra_missing_or_duplicate_archive_members_are_rejected(self):
        self.fixture(self.entries, extra='data/unexpected.txt')
        with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.fixture(self.entries)
        self.manifest['packages'][0]['files'].append(dict(self.manifest['packages'][0]['files'][0]))
        self.write_manifest(self.manifest)
        with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.assertFalse(self.target.exists())

    def test_zip_symlink_entry_is_rejected(self):
        self.fixture({'data/link': b'../../private'}, symlink=True)
        with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.assertFalse(self.target.exists())

    def test_member_size_limits_and_schema_are_validated(self):
        for key, value in (('size', True), ('size', -1), ('sha256', 'not-a-sha')):
            with self.subTest(key=key):
                manifest = json.loads(json.dumps(self.manifest))
                manifest['packages'][0]['files'][0][key] = value
                self.write_manifest(manifest)
                with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.write_manifest({**self.manifest, 'schema_version': 2})
        with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.write_manifest(self.manifest)
        with patch.object(bundle, 'MAX_TOTAL_BYTES', 1), self.assertRaises(bundle.BundleError):
            bundle.restore(self.packs, self.target)

    def test_a_bundle_filename_cannot_traverse_or_be_an_absolute_path(self):
        for name in ('../catalog.zip', 'C:/catalog.zip', '/catalog.zip', 'sub/catalog.zip'):
            with self.subTest(name=name):
                manifest = json.loads(json.dumps(self.manifest)); manifest['packages'][0]['name'] = name
                self.write_manifest(manifest)
                with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)

    def test_destination_directory_symlink_is_rejected(self):
        outside = self.root / 'outside'; outside.mkdir()
        self.target.mkdir()
        try: (self.target / 'data').symlink_to(outside, target_is_directory=True)
        except OSError: self.skipTest('Directory symlink creation is unavailable for this account.')
        with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.assertEqual(list(outside.iterdir()), [])

    def test_manifest_without_packages_or_unreadable_manifest_is_a_domain_error(self):
        for raw in ('not-json', '{}', '{"schema_version":1,"packages":[]}', '[]', '{"schema_version":true,"packages":[]}'):
            with self.subTest(raw=raw):
                (self.packs / 'manifest.json').write_text(raw, encoding='utf-8')
                with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)

    def test_a_file_in_a_parent_position_refuses_the_entire_restore(self):
        self.target.mkdir()
        (self.target / 'data').write_bytes(b'keep-this-file')
        with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.assertEqual((self.target / 'data').read_bytes(), b'keep-this-file')

    def test_archive_metadata_and_missing_packages_are_checked(self):
        for change in ({'size': True}, {'size': -1}, {'sha256': 'broken'}, {'files': []}, {'files': None}):
            with self.subTest(change=change):
                manifest = json.loads(json.dumps(self.manifest))
                manifest['packages'][0].update(change); self.write_manifest(manifest)
                with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.write_manifest(self.manifest)
        (self.packs / 'catalog-001.zip').unlink()
        with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)

    def test_manifest_bound_and_member_sizes_are_not_just_trusted(self):
        with patch.object(bundle, 'MAX_MANIFEST_BYTES', 1), self.assertRaises(bundle.BundleError):
            bundle.restore(self.packs, self.target)
        manifest = json.loads(json.dumps(self.manifest)); manifest['packages'][0]['files'][0]['size'] += 1
        self.write_manifest(manifest)
        with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.write_manifest(self.manifest)
        with patch.object(bundle, 'MAX_FILES', 1), self.assertRaises(bundle.BundleError):
            bundle.restore(self.packs, self.target)

    def test_a_zip_crc_failure_is_reported_without_a_partial_output(self):
        archive = self.packs / 'catalog-001.zip'; raw = bytearray(archive.read_bytes())
        offset = raw.index(b'PK\x01\x02'); raw[offset + 16] ^= 1
        archive.write_bytes(raw)
        self.manifest['packages'][0]['sha256'] = hashlib.sha256(raw).hexdigest()
        self.write_manifest(self.manifest)
        with self.assertRaises(bundle.BundleError): bundle.restore(self.packs, self.target)
        self.assertFalse(self.target.exists())

    def test_concurrent_destination_creation_never_overwrites_another_file(self):
        def conflict(source, target):
            Path(target).write_bytes(b'concurrent-owner')
            raise FileExistsError()
        with patch.object(bundle.os, 'link', conflict), self.assertRaises(bundle.BundleError):
            bundle.restore(self.packs, self.target)
        self.assertEqual((self.target / 'data/units.json').read_bytes(), b'concurrent-owner')
        self.assertEqual(list(self.target.rglob('*.tmp')), [])

    def test_identical_concurrent_creation_is_safe_and_cli_is_offline(self):
        def identical(source, target):
            Path(target).write_bytes(Path(source).read_bytes())
            raise FileExistsError()
        with patch.object(bundle.os, 'link', identical):
            result = bundle.restore(self.packs, self.target)
        self.assertEqual(result['skipped'], 2)
        output = io.StringIO()
        with redirect_stdout(output):
            code = bundle.main(['--bundle-dir', str(self.packs), '--target-dir', str(self.target)])
        self.assertEqual(code, 0); self.assertFalse(json.loads(output.getvalue())['published'])
        with redirect_stderr(io.StringIO()):
            self.assertEqual(bundle.main(['--bundle-dir', str(self.root / 'missing')]), 1)


class PublicMetadataProjectionTests(unittest.TestCase):
    def test_private_results_and_their_derived_statistics_are_not_distributed(self):
        value = {'metadata': {'notes': []}, 'documents': [
            {'id': 'roster', 'kind': 'admission_results', 'local_path': 'data/snapshots/roster.html'},
            {'id': 'score', 'kind': 'score', 'local_path': 'data/snapshots/score.pdf'}],
            'admitted_statistics': [{'id': 'derived'}], 'score_lines': [{'id': 'line'}], 'transfer_rules': [{'id': 'rule'}]}
        projected, counts = builder.public_history(value)
        self.assertEqual([row['id'] for row in projected['documents']], ['score'])
        self.assertEqual(projected['admitted_statistics'], [])
        self.assertEqual(projected['score_lines'], value['score_lines'])
        self.assertEqual(counts, {'result_documents': 1, 'admitted_statistics': 1})
        self.assertEqual(len(value['documents']), 2)

    def test_only_in_project_data_paths_are_projected_and_secrets_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            value = {'pages': [{'snapshot_path': str(root / 'data/snapshots/registry/page.html'), 'sha256': 'a' * 64}]}
            projected = builder.portable_metadata(value, root)
            self.assertEqual(projected['pages'][0]['snapshot_path'], 'data/snapshots/registry/page.html')
            self.assertEqual(projected['pages'][0]['sha256'], 'a' * 64)
            self.assertTrue(Path(value['pages'][0]['snapshot_path']).is_absolute())
            relative = {'pages': [{'local_path': 'data/snapshots/source.html'}]}
            self.assertIs(builder.portable_metadata(relative, root), relative)
            for value in ({'local_path': str(root.parent / 'outside.html')}, {'api_key': 'never-package'},
                          {'local_path': 'data/runtime/research.sqlite'}):
                with self.subTest(value=value), self.assertRaises(ValueError): builder.portable_metadata(value, root)


class PublicBundleBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        data = self.project / 'data'; (data / 'snapshots').mkdir(parents=True)
        for name in ('registry.html', 'catalog.html', 'line.pdf', 'private-roster.html'):
            (data / 'snapshots' / name).write_bytes(('fixture-' + name).encode())
        self.units = {'metadata': {'pages': [{'snapshot_path': str(data / 'snapshots/registry.html')}]},
                      'units': [{'id': 'u1'}]}
        self.admissions = {'metadata': {'reference_year': 2027, 'target_year': 2028},
                           'documents': [{'id': 'doc', 'kind': 'catalog', 'local_path': 'data/snapshots/catalog.html'}],
                           'programs': [{'id': 'p1', 'unit_id': 'u1'}]}
        values = {'units.json': self.units, 'admissions.json': self.admissions,
                  'history.json': {'metadata': {}, 'documents': [
                      {'id': 'private', 'kind': 'admission_results', 'local_path': 'data/snapshots/private-roster.html'},
                      {'id': 'line', 'kind': 'score', 'local_path': 'data/snapshots/line.pdf'}],
                      'admitted_statistics': [{'id': 'private-derived'}], 'score_lines': [{'id': 'line'}], 'transfer_rules': []},
                  'program_enrichment.json': {'documents': [], 'rules': []},
                  'annual_registry_2027.json': {'metadata': {'official_total': 1}},
                  'catalog_semantic_audits.json': {'audits': []},
                  'catalog_facts.json': {'documents': [], 'facts': []}}
        for name, value in values.items(): (data / name).write_text(json.dumps(value), encoding='utf-8')

    def test_build_restore_closure_is_portable_and_excludes_private_result_sources(self):
        original = (self.project / 'data/units.json').read_bytes()
        with patch.object(builder, 'SOURCE_CHUNK', 1): result = builder.build(self.project)
        self.assertEqual(result['dataset']['programs'], 1)
        self.assertEqual(result['dataset']['individual_result_documents_excluded'], 1)
        self.assertGreater(result['packages'], 2)
        target = Path(self.temp.name) / 'restored'
        restored = bundle.restore(self.project / 'data/bundles', target)
        self.assertEqual(restored['files'], result['files'])
        self.assertTrue((target / 'data/snapshots/catalog.html').is_file())
        self.assertFalse((target / 'data/snapshots/private-roster.html').exists())
        units = json.loads((target / 'data/units.json').read_text('utf-8'))
        self.assertEqual(units['metadata']['pages'][0]['snapshot_path'], 'data/snapshots/registry.html')
        history = json.loads((target / 'data/history.json').read_text('utf-8'))
        self.assertEqual(history['admitted_statistics'], [])
        self.assertEqual(len(history['score_lines']), 1)
        self.assertEqual((self.project / 'data/units.json').read_bytes(), original)

    def test_builder_rejects_unexpected_personal_main_catalog_or_missing_originals(self):
        path = self.project / 'data/admissions.json'
        for update in ({'admitted_statistics': [{'id': 'not-public'}]},
                       {'documents': [{'id': 'private', 'kind': 'admission_results'}]}):
            with self.subTest(update=update):
                path.write_text(json.dumps({**self.admissions, **update}), encoding='utf-8')
                with self.assertRaises(ValueError): builder.build(self.project)
        path.write_text(json.dumps(self.admissions), encoding='utf-8')
        (self.project / 'data/snapshots/catalog.html').unlink()
        with self.assertRaises(ValueError): builder.build(self.project)
        self.assertFalse((self.project / 'data/bundles').exists())

    def test_builder_cli_reports_offline_scope_and_refuses_missing_data(self):
        output = io.StringIO()
        with redirect_stdout(output): code = builder.main(['--project-root', str(self.project)])
        self.assertEqual(code, 0); self.assertEqual(json.loads(output.getvalue())['dataset']['index_units'], 1)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(builder.main(['--project-root', str(Path(self.temp.name) / 'missing')]), 1)


if __name__ == '__main__': unittest.main()
