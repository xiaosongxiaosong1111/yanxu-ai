"""Build an explicit, privacy-filtered offline distribution from adopted source files."""
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import sys
import zipfile


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path: sys.path.insert(0, str(PROJECT))
from json_reader import read_json
from source_privacy import contains_personal_roster
from tools.prepare_data import CHUNK, MAX_ARCHIVE_BYTES, MAX_FILE_BYTES, _path


NAMES = ('units.json', 'admissions.json', 'history.json', 'program_enrichment.json',
         'annual_registry_2027.json', 'catalog_semantic_audits.json', 'catalog_facts.json')
PATH_KEYS = {'local_path', 'snapshot_path', 'text_path'}
SENSITIVE_KEYS = {'api_key', 'secret_id', 'secret_key', 'authorization', 'cookie', 'password', 'access_token'}
SOURCE_CHUNK = 32 * 1024 * 1024


def source_path(value, project):
    if not isinstance(value, str) or not value: raise ValueError('Invalid adopted source path.')
    project = Path(project).resolve()
    path = Path(value)
    if not path.is_absolute(): path = (project if path.parts[0] == 'data' else project / 'data') / path
    path = path.resolve()
    if not path.is_relative_to(project / 'data'): raise ValueError('Adopted source escapes project data.')
    _path(path.relative_to(project).as_posix())
    return path


def public_history(value):
    documents = value.get('documents', [])
    allowed = [row for row in documents if row.get('kind') != 'admission_results' and
               not contains_personal_roster(str(row.get('title', '')))]
    counts = {'result_documents': len(documents) - len(allowed),
              'admitted_statistics': len(value.get('admitted_statistics', []))}
    metadata = {**value.get('metadata', {}), 'public_distribution': {
        'individual_result_sources_included': False,
        'note': 'Public package omits individual result rosters and derived admission-score samples; official school lines remain separate.'}}
    return {**value, 'metadata': metadata, 'documents': allowed, 'admitted_statistics': []}, counts


def portable_metadata(value, project):
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key.casefold() in SENSITIVE_KEYS: raise ValueError('Credentials cannot enter a public data package.')
            if key in PATH_KEYS and isinstance(child, str) and child:
                portable = source_path(child, project).relative_to(Path(project).resolve()).as_posix()
                result[key] = child if portable == child else portable
            else: result[key] = portable_metadata(child, project)
        return result if any(result[key] is not child for key, child in value.items()) else value
    if isinstance(value, list):
        result = [portable_metadata(child, project) for child in value]
        return result if any(a is not b for a, b in zip(result, value)) else value
    if isinstance(value, str) and re.search(r'[A-Za-z]:[\\/]Users[\\/]|^/(?:Users|home)/', value, re.I):
        raise ValueError('Personal filesystem information needs an explicit reviewed projection.')
    return value


def dependencies(value, project):
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PATH_KEYS and isinstance(child, str) and child: result.add(source_path(child, project))
            else: result.update(dependencies(child, project))
    elif isinstance(value, list):
        for child in value: result.update(dependencies(child, project))
    return result


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _zip_package(folder, name, entries):
    destination = folder / name
    temporary = destination.with_suffix('.zip.tmp')
    records = []
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for member, value in entries:
                _path(member)
                info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3; info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                stream = io.BytesIO(value) if isinstance(value, bytes) else value.open('rb')
                digest, size = hashlib.sha256(), 0
                with stream, archive.open(info, 'w', force_zip64=True) as output:
                    while block := stream.read(CHUNK):
                        size += len(block)
                        if size > MAX_FILE_BYTES: raise ValueError('An adopted source exceeds the public file limit.')
                        digest.update(block); output.write(block)
                records.append({'path': member, 'size': size, 'sha256': digest.hexdigest()})
        size = temporary.stat().st_size
        if size > MAX_ARCHIVE_BYTES: raise ValueError('A compressed public package exceeds 50 MiB.')
        with temporary.open('rb') as source: digest = hashlib.file_digest(source, 'sha256').hexdigest()
        temporary.replace(destination)
        return {'name': name, 'size': size, 'sha256': digest, 'files': records}
    finally: temporary.unlink(missing_ok=True)


def _public_inputs(project):
    original = {name: read_json(project / 'data' / name) for name in NAMES}
    for row in original['admissions.json'].get('documents', []):
        if row.get('kind') == 'admission_results' or contains_personal_roster(str(row.get('title', ''))):
            raise ValueError('The main catalog unexpectedly includes personal result documents; review it before packaging.')
    if original['admissions.json'].get('admitted_statistics'):
        raise ValueError('Admission-result statistics in the main catalog require a separate privacy review.')
    history, excluded = public_history(original['history.json'])
    projected = {**original, 'history.json': history}
    projected = {name: portable_metadata(value, project) for name, value in projected.items()}
    sources = set().union(*(dependencies(value, project) for value in projected.values()))
    if any(not path.is_file() or path.is_symlink() for path in sources):
        raise ValueError('An adopted original is missing or linked; the public bundle was not built.')
    entries = []
    for name in NAMES:
        path = project / 'data' / name
        if projected[name] is original[name]: value = path
        elif path.stat().st_size <= 32 * 1024 * 1024: value = _bytes(projected[name])
        else: raise ValueError('Large catalog projection requires a new explicit privacy review.')
        entries.append(('data/' + name, value))
    admissions = projected['admissions.json']; programs = admissions.get('programs', [])
    scope = {'reference_year': admissions.get('metadata', {}).get('reference_year'),
             'target_year': admissions.get('metadata', {}).get('target_year'),
             'index_units': len(projected['units.json'].get('units', [])),
             'annual_units': projected['annual_registry_2027.json'].get('metadata', {}).get('official_total'),
             'program_units': len({row['unit_id'] for row in programs}), 'programs': len(programs),
             'catalog_documents': len(admissions.get('documents', [])),
             'school_score_lines': len(history.get('score_lines', [])),
             'transfer_rules': len(history.get('transfer_rules', [])),
             'enrichment_rules': len(projected['program_enrichment.json'].get('rules', [])),
             'pending_original_table_rows': len(projected['catalog_facts.json'].get('facts', [])),
             'individual_result_documents_excluded': excluded['result_documents'],
             'derived_admission_statistics_excluded': excluded['admitted_statistics']}
    return entries, sorted(sources), scope


def build(project=PROJECT):
    project = Path(project).resolve()
    entries, sources, scope = _public_inputs(project)
    folder = project / 'data/bundles'; folder.mkdir(parents=True, exist_ok=True)
    packages = [_zip_package(folder, 'catalog-data.zip', entries)]
    batch, size, number = [], 0, 1
    for path in sources:
        length = path.stat().st_size
        if batch and size + length > SOURCE_CHUNK:
            packages.append(_zip_package(folder, f'sources-{number:03}.zip', batch))
            number += 1; batch, size = [], 0
        batch.append((path.relative_to(project).as_posix(), path)); size += length
    if batch: packages.append(_zip_package(folder, f'sources-{number:03}.zip', batch))
    manifest = {'schema_version': 1, 'dataset': scope, 'packages': packages,
        'policy': {'network_required': False, 'runtime_databases_included': False,
                   'originals_unchanged': True, 'personal_results_included': False,
                   'discovery_archives_included': False, 'portable_metadata_paths': True,
                   'note': 'Only adopted catalog, annual-unit, fee, school-line and pending-table source dependencies are distributed. Source acquisition state is not nationwide completion.'}}
    temporary = folder / 'manifest.json.tmp'; temporary.write_bytes(_bytes(manifest)); temporary.replace(folder / 'manifest.json')
    return {'dataset': scope, 'packages': len(packages), 'files': sum(len(p['files']) for p in packages),
            'archive_bytes': sum(p['size'] for p in packages),
            'uncompressed_bytes': sum(f['size'] for p in packages for f in p['files']),
            'manifest': 'data/bundles/manifest.json'}


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--project-root', type=Path, default=PROJECT)
    args = cli.parse_args(argv)
    try: print(json.dumps(build(args.project_root), ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Public bundle build refused: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
