"""One immutable-by-copy catalogue revision, including every saved source file."""

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import stat
import threading


class SnapshotCache:
    def __init__(self, root, *, base_paths=None):
        self.root = Path(root)
        self._lock = threading.Lock()
        names = ('units.json', 'admissions.json', 'history.json', 'program_enrichment.json')
        self._base = tuple(self.root / name for name in names) if base_paths is None else tuple(Path(path) for path in base_paths)
        self._paths = self._base
        self._signature, self._value, self._revision = None, None, None

    def _fingerprint(self, paths):
        result = []
        root = self.root.resolve()
        for path in paths:
            try:
                info = path.lstat()
                reparse = stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)
                link = (os.readlink(path), str(path.resolve())) if reparse else None
                identity = (info.st_ino, info.st_mode, link)
                if stat.S_ISDIR(info.st_mode) and not reparse:
                    result.append((str(path.absolute()), *identity))
                else:
                    target = path.stat() if reparse else info
                    # NTFS permits same-length edits with restored mtime; bytes must still invalidate trust.
                    digest=None
                    if stat.S_ISREG(target.st_mode) and path.resolve().is_relative_to(root):
                        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                    result.append((str(path.absolute()), *identity, target.st_mtime_ns, target.st_ctime_ns, target.st_size, target.st_ino, digest))
            except (OSError, ValueError):
                result.append((str(path.absolute()), None))
        return tuple(result)

    def _dependencies(self, value):
        paths = set(self._base)
        def visit(item):
            if isinstance(item, dict):
                for key, child in item.items():
                    if key in {'local_path', 'snapshot_path', 'text_path'} and isinstance(child, str) and child:
                        path = Path(child)
                        if not path.is_absolute():
                            path = (self.root.parent if path.parts and path.parts[0] == self.root.name else self.root) / path
                        paths.add(path)
                    else:
                        visit(child)
            elif isinstance(item, (list, tuple)):
                for child in item: visit(child)
        visit(value)
        paths.update(parent for path in tuple(paths) for parent in path.parents)
        return tuple(sorted(paths, key=str))

    def get(self, load, verify):
        with self._lock:
            signature = self._fingerprint(self._paths)
            if signature != self._signature:
                for _ in range(3):
                    base_before = self._fingerprint(self._base)
                    registry, admissions = load()
                    paths = self._dependencies((registry, admissions))
                    before = self._fingerprint(paths)
                    coverage = verify(registry, admissions)
                    after = self._fingerprint(paths)
                    if before == after and base_before == self._fingerprint(self._base):
                        revision = hashlib.sha256(repr(after).encode()).hexdigest()[:24]
                        self._paths, self._signature = paths, after
                        self._revision, self._value = revision, (registry, admissions, coverage)
                        break
                else:
                    raise OSError('Source files changed while checking the catalogue.')
            return (*deepcopy(self._value), self._revision)

    def status(self):
        if not self._lock.acquire(blocking=False):
            return {'release_ready': False, 'data_revision': None, 'coverage_status': 'checking'}
        try:
            valid = self._value is not None and self._fingerprint(self._paths) == self._signature
            return {'release_ready': bool(valid and self._value[2]['release_ready']),
                    'data_revision': self._revision if valid else None,
                    'coverage_status': 'checked' if valid else 'unchecked'}
        finally:
            self._lock.release()
