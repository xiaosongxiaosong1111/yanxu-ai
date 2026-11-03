"""Restore the versioned public data bundle without network access or overwriting data."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import zipfile


MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_FILES = 20000
CHUNK = 64 * 1024
_SHA = re.compile(r'[0-9a-f]{64}')
_RESERVED = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', re.I)


class BundleError(ValueError):
    pass


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 500 or '\\' in value:
        raise BundleError('Unsafe data member path.')
    parts = value.split('/')
    if (parts[0] != 'data' or len(parts) < 2 or parts[1] in {'runtime', 'bundles'} or
            any(not item or item in {'.', '..'} or item.endswith((' ', '.')) or
                re.search(r'[\x00-\x1f<>:"|?*]', item) or _RESERVED.fullmatch(item) for item in parts)):
        raise BundleError('Unsafe data member path.')
    return PurePosixPath(value)


def _hash_ok(value):
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _is_link(path):
    try:
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)
    except FileNotFoundError: return False


def _target(root, name):
    path = root.joinpath(*_path(name).parts)
    for entry in (root, *path.relative_to(root).parents):
        candidate = entry if entry == root else root / entry
        if _is_link(candidate): raise BundleError('A target directory is a symbolic link or junction.')
        if candidate.exists() and not candidate.is_dir(): raise BundleError('A data parent is not a directory.')
    if _is_link(path): raise BundleError('A target file is a symbolic link or junction.')
    if not path.resolve().is_relative_to(root): raise BundleError('Data target escapes the selected project.')
    return path


def _digest(stream, limit):
    total = 0
    digest = hashlib.sha256()
    while block := stream.read(CHUNK):
        total += len(block)
        if total > limit: raise BundleError('Data exceeds the declared size or extraction limit.')
        digest.update(block)
    return total, digest.hexdigest()


def _file_digest(path, limit):
    with path.open('rb') as stream: return _digest(stream, limit)


def _manifest(bundle_dir):
    path = bundle_dir / 'manifest.json'
    if _is_link(path) or not 0 < path.stat().st_size <= MAX_MANIFEST_BYTES:
        raise BundleError('The public bundle manifest is missing or too large.')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise BundleError('Unsupported public bundle schema.')
    packages = value.get('packages')
    if not isinstance(packages, list) or not 0 < len(packages) <= 100:
        raise BundleError('The public manifest must contain bounded archive packages.')
    names, members, total = set(), set(), 0
    for package in packages:
        if not isinstance(package, dict): raise BundleError('Invalid archive description.')
        name, size = package.get('name'), package.get('size')
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,99}\.zip', name) or name in names:
            raise BundleError('Unsafe or duplicate archive filename.')
        names.add(name)
        if type(size) is not int or not 0 < size <= MAX_ARCHIVE_BYTES or not _hash_ok(package.get('sha256')):
            raise BundleError('Invalid archive size or SHA-256.')
        files = package.get('files')
        if not isinstance(files, list) or not files: raise BundleError('Empty archive file manifest.')
        for file in files:
            if not isinstance(file, dict): raise BundleError('Invalid data file description.')
            member = str(_path(file.get('path')))
            if member.casefold() in members: raise BundleError('Duplicate data member path.')
            members.add(member.casefold())
            size = file.get('size')
            if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES or not _hash_ok(file.get('sha256')):
                raise BundleError('Invalid data member size or SHA-256.')
            total += size
            if total > MAX_TOTAL_BYTES or len(members) > MAX_FILES: raise BundleError('Public data bundle is too large.')
    return packages


def _archive(bundle_dir, package):
    path = bundle_dir / package['name']
    if _is_link(path) or not path.is_file(): raise BundleError('An archive is missing or linked elsewhere.')
    if _file_digest(path, MAX_ARCHIVE_BYTES) != (package['size'], package['sha256']):
        raise BundleError('An archive does not match its committed SHA-256 and size.')
    return path


def _check_archive(path, package):
    expected = {file['path']: file for file in package['files']}
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) != len(expected) or {entry.filename for entry in entries} != set(expected):
            raise BundleError('Archive members do not match the explicit public manifest.')
        for entry in entries:
            _path(entry.filename)
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if entry.is_dir() or mode not in {0, stat.S_IFREG} or entry.flag_bits & 1:
                raise BundleError('Archive links, directories or encrypted entries are not supported.')
            file = expected[entry.filename]
            if entry.file_size != file['size']: raise BundleError('Archive member size differs from its manifest.')
            with archive.open(entry) as stream:
                if _digest(stream, file['size']) != (file['size'], file['sha256']):
                    raise BundleError('Archive member SHA-256 does not match its manifest.')


def _existing(path, file):
    if not path.exists(): return False
    if not path.is_file() or _file_digest(path, MAX_FILE_BYTES) != (file['size'], file['sha256']):
        raise BundleError('An existing data file differs; it has been preserved. Restore into a fresh clone.')
    return True


def _extract(archive, file, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.' + destination.name + '.', suffix='.tmp', delete=False) as out:
            temporary = Path(out.name)
            with archive.open(file['path']) as source:
                total, digest = 0, hashlib.sha256()
                while block := source.read(CHUNK):
                    total += len(block)
                    if total > file['size']: raise BundleError('Archive changed while restoring a data file.')
                    digest.update(block); out.write(block)
            if (total, digest.hexdigest()) != (file['size'], file['sha256']):
                raise BundleError('Archive changed while restoring a data file.')
        # Hard-link creation is atomic and refuses to overwrite a file created after preflight.
        try: os.link(temporary, destination)
        except FileExistsError:
            if _is_link(destination) or not _existing(destination, file): raise BundleError('The data target changed during restoration.')
            return False
        return True
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def restore(bundle_dir, target_dir):
    try:
        bundle_dir, raw_root = Path(bundle_dir).resolve(), Path(target_dir).absolute()
        if _is_link(raw_root): raise BundleError('The project target cannot be a symbolic link or junction.')
        root = raw_root.resolve()
        packages = _manifest(bundle_dir)
        archives, restored, skipped = [], 0, 0
        # Verify every package, member and existing destination before adding any files.
        for package in packages:
            path = _archive(bundle_dir, package)
            _check_archive(path, package)
            for file in package['files']: _existing(_target(root, file['path']), file)
            archives.append((path, package))
        for path, package in archives:
            with zipfile.ZipFile(path) as archive:
                for file in package['files']:
                    destination = _target(root, file['path'])
                    if _existing(destination, file): skipped += 1
                    elif _extract(archive, file, destination): restored += 1
                    else: skipped += 1
        return {'restored': restored, 'skipped': skipped, 'files': restored + skipped,
                'mode': 'offline_public_data_only', 'published': False}
    except BundleError: raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile) as error:
        raise BundleError('The public data bundle could not be safely restored: ' + type(error).__name__) from None


def main(argv=None):
    project = Path(__file__).resolve().parents[1]
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--bundle-dir', type=Path, default=project / 'data/bundles')
    cli.add_argument('--target-dir', type=Path, default=project)
    args = cli.parse_args(argv)
    try: print(json.dumps(restore(args.bundle_dir, args.target_dir), ensure_ascii=False))
    except BundleError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
