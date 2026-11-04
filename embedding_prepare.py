"""Explicit, pinned preparation of the public local BGE model files.

Nothing in this module is called by ordinary status, import or polling paths. The
only entry point that downloads is :meth:`ModelPreparer.prepare` (or its async
``start_prepare`` wrapper), and it accepts no model name, directory or URL from a
caller. Files are fetched from the fixed public revision, verified before publication
and published into the existing runtime model directory with a same-filesystem
rename, so an interrupted or corrupt download can never become a usable model.
"""
import hashlib
from pathlib import Path
import shutil
import threading
import time
import uuid
from urllib.request import Request, urlopen

from embedding_catalog import LOCAL_MODEL, LOCAL_REVISION, MODEL_FILES, MODEL_SHA256, MODEL_SIZE
from embedding_local import model_dir

MODEL_BASE_URL = 'https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/' + LOCAL_REVISION + '/'
MODEL_FILE_SIZES = {
    'config.json': 739,
    'tokenizer.json': 439125,
    'tokenizer_config.json': 367,
    'special_tokens_map.json': 125,
    'model_optimized.onnx': MODEL_SIZE,
}
MODEL_FILE_ORDER = ('model_optimized.onnx', 'config.json', 'tokenizer.json',
                    'tokenizer_config.json', 'special_tokens_map.json')
MODEL_SPEC = {
    name: {'size': MODEL_FILE_SIZES[name],
           'sha1': MODEL_FILES.get(name),
           'sha256': MODEL_SHA256 if name == 'model_optimized.onnx' else None}
    for name in MODEL_FILE_ORDER
}
_CHUNK = 1024 * 1024
_MAX_SECONDS = 600
_SIGNATURES = {}
_SIGNATURE_LOCK = threading.Lock()


class PrepareError(Exception):
    """Stable, client-safe preparation failure; never carries a path or raw error."""
    def __init__(self, code, message, status=503):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _digest(path, algorithm):
    engine = hashlib.new(algorithm)
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b''):
            engine.update(chunk)
    return engine.hexdigest()


def _signature(root):
    rows = []
    for name in MODEL_FILE_ORDER:
        path = root / name
        try:
            info = path.stat()
        except OSError:
            return None
        rows.append((name, info.st_size, info.st_mtime_ns))
    return tuple(rows)


def _verified(root):
    """Verify every pinned file once per directory signature; read-only."""
    signature = _signature(root)
    if signature is None:
        return False, 'embedding_model_not_ready'
    with _SIGNATURE_LOCK:
        if _SIGNATURES.get(str(root)) == signature:
            return True, ''
    for name in MODEL_FILE_ORDER:
        spec = MODEL_SPEC[name]
        path = root / name
        if path.stat().st_size != spec['size']:
            return False, 'embedding_model_invalid'
        if spec['sha1'] and _digest(path, 'sha1') != spec['sha1']:
            return False, 'embedding_model_invalid'
        if spec['sha256'] and _digest(path, 'sha256') != spec['sha256']:
            return False, 'embedding_model_invalid'
    with _SIGNATURE_LOCK:
        _SIGNATURES[str(root)] = signature
    return True, ''


class ModelPreparer:
    """One lazy preparer per data directory; status() never touches the network."""
    def __init__(self, data_dir, opener=None):
        self.data_dir = Path(data_dir).resolve()
        self.root = model_dir(self.data_dir).resolve()
        if not self.root.is_relative_to(self.data_dir):
            raise ValueError('Local model directory escapes the project data directory.')
        self._opener = opener
        self._lock = threading.RLock()
        self._thread = None
        self._state = ''
        self._error = ''

    def _open(self, url):
        if self._opener is not None:
            return self._opener(url)
        request = Request(url, headers={'User-Agent': 'yanxu-local-model-prepare/1'})
        return urlopen(request, timeout=30)

    def status(self):
        with self._lock:
            state, error = self._state, self._error
            thread = self._thread
        if thread is not None and thread.is_alive():
            return self._status('preparing', '', 0)
        verified, problem = _verified(self.root)
        if verified:
            return self._status('ready', '', len(MODEL_FILE_ORDER))
        if state == 'failed':
            return self._status('failed', error or 'embedding_model_download_failed', 0)
        if problem == 'embedding_model_invalid' and any((self.root / name).is_file() for name in MODEL_FILE_ORDER):
            return self._status('invalid', problem, 0)
        return self._status('not_prepared', problem if problem != 'embedding_model_not_ready' else '', 0)

    def _status(self, state, error_code, files_verified):
        return {'state': state, 'prepared': state == 'ready', 'model': LOCAL_MODEL,
                'revision': LOCAL_REVISION, 'files_verified': files_verified,
                'total_files': len(MODEL_FILE_ORDER), 'error_code': error_code}

    def prepare(self):
        """Download, verify and publish once. Already-verified files are reused."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {**self._status('preparing', '', 0), 'reused': False, 'started': False}
        return self._execute_prepare()

    def _execute_prepare(self):
        verified, _ = _verified(self.root)
        if verified:
            return {**self._status('ready', '', len(MODEL_FILE_ORDER)), 'reused': True, 'started': False}
        with self._lock:
            self._state = 'preparing'
            self._error = ''
        try:
            self._download_and_publish()
        except PrepareError as error:
            with self._lock:
                self._state, self._error = 'failed', error.code
            raise
        except Exception:
            with self._lock:
                self._state, self._error = 'failed', 'embedding_model_download_failed'
            raise PrepareError('embedding_model_download_failed', '本机向量模型准备失败，请检查网络后重试。') from None
        with self._lock:
            self._state, self._error = 'ready', ''
        return {**self._status('ready', '', len(MODEL_FILE_ORDER)), 'reused': False, 'started': True}

    def start_prepare(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {**self._status('preparing', '', 0), 'started': False}
            verified, _ = _verified(self.root)
            if verified:
                return {**self._status('ready', '', len(MODEL_FILE_ORDER)), 'started': False}
            self._state, self._error = 'preparing', ''
            def run():
                try:
                    self._execute_prepare()
                except PrepareError:
                    pass
            self._thread = threading.Thread(target=run, name='yanxu-model-prepare', daemon=True)
            self._thread.start()
        return {**self._status('preparing', '', 0), 'started': True}

    def _download_and_publish(self):
        parent = self.root.parent
        parent.mkdir(parents=True, exist_ok=True)
        staging = parent / ('.tmp-' + uuid.uuid4().hex)
        staging.mkdir()
        started = time.monotonic()
        try:
            for name in MODEL_FILE_ORDER:
                if time.monotonic() - started > _MAX_SECONDS:
                    raise PrepareError('embedding_model_download_failed', '本机向量模型下载超时，请稍后重试。')
                self._fetch(name, staging / name)
            verified, problem = _verified(staging)
            if not verified:
                raise PrepareError(problem or 'embedding_model_invalid', '本机向量模型下载后校验未通过，请重试。')
            self._publish(staging)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def _fetch(self, name, target):
        spec = MODEL_SPEC[name]
        url = MODEL_BASE_URL + name
        try:
            response = self._open(url)
        except Exception:
            raise PrepareError('embedding_model_download_failed', '本机向量模型下载失败，请检查网络后重试。') from None
        try:
            total = 0
            with target.open('wb') as handle:
                while True:
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > spec['size']:
                        raise PrepareError('embedding_model_invalid', '本机向量模型文件大小不正确，请重试。')
                    handle.write(chunk)
        except PrepareError:
            raise
        except Exception:
            raise PrepareError('embedding_model_download_failed', '本机向量模型下载中断，请重试。') from None
        finally:
            close = getattr(response, 'close', None)
            if callable(close):
                close()
        if total != spec['size']:
            raise PrepareError('embedding_model_invalid', '本机向量模型文件大小不正确，请重试。')

    def _publish(self, staging):
        parent = self.root.parent
        backup = None
        if self.root.exists():
            backup = parent / ('.old-' + uuid.uuid4().hex)
            self.root.replace(backup)
        try:
            staging.replace(self.root)
        except Exception:
            if backup is not None and backup.exists() and not self.root.exists():
                backup.replace(self.root)
            raise PrepareError('embedding_model_download_failed', '本机向量模型发布失败，请重试。') from None
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
