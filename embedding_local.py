"""Verified local BGE ONNX inference. Only the pinned, already-prepared files are used;
nothing here ever downloads weights or reaches the network."""
import hashlib
import threading
from pathlib import Path

from embedding_catalog import LOCAL_MODEL, LOCAL_REVISION, MODEL_FILES, MODEL_SHA256, MODEL_SIZE
from provider_catalog import TransportError

_MODEL_SUBDIR = 'bge-small-zh-v1.5'
_BATCH_SIZE = 4
_VERIFIED = {}
_TOKENIZERS = {}
_MODELS = {}
_MODEL_LOCKS = {}
_GUARD = threading.Lock()


def model_dir(data_dir):
    return Path(data_dir) / 'runtime' / 'embedding-models' / _MODEL_SUBDIR / LOCAL_REVISION


def _digest(path, algorithm):
    engine = hashlib.new(algorithm)
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            engine.update(chunk)
    return engine.hexdigest()


def _signature(root):
    """Statistical signature of every required model file; any change forces re-hashing."""
    rows = []
    for name in ['model_optimized.onnx', *sorted(MODEL_FILES)]:
        path = root / name
        try:
            info = path.stat()
        except OSError:
            return None
        rows.append((name, info.st_size, info.st_mtime_ns))
    return tuple(rows)


def _verified_dir(data_dir):
    root = model_dir(data_dir)
    if not (root / 'model_optimized.onnx').is_file():
        raise TransportError('embedding_model_not_ready',
                             '本机中文向量模型尚未准备就绪；请在知识检索设置中完成一次连接测试，或先在联网环境下显式准备公开模型。')
    signature = _signature(root)
    if signature is None:
        raise TransportError('embedding_model_not_ready', '本机模型文件缺失，请重新准备公开模型文件。')
    if signature[0][1] != MODEL_SIZE:
        raise TransportError('embedding_model_invalid', '本机模型文件大小不正确，请重新准备公开模型文件。')
    with _GUARD:
        known = _VERIFIED.get(str(root))
    if known == signature:
        return root
    if _digest(root / 'model_optimized.onnx', 'sha256') != MODEL_SHA256:
        raise TransportError('embedding_model_invalid', '本机模型文件校验未通过，请重新准备公开模型文件。')
    for name, expected in MODEL_FILES.items():
        if _digest(root / name, 'sha1') != expected:
            raise TransportError('embedding_model_invalid', f'本机模型文件 {name} 校验未通过，请重新准备公开模型文件。')
    with _GUARD:
        _VERIFIED[str(root)] = signature
    return root


def _tokenizer(root):
    known = _TOKENIZERS.get(str(root))
    if known is not None:
        return known
    try:
        import tokenizers
        tokenizer = tokenizers.Tokenizer.from_file(str(root / 'tokenizer.json'))
    except Exception:
        raise TransportError('embedding_model_invalid', '本机模型分词器无法读取，请重新准备公开模型文件。') from None
    with _GUARD:
        _TOKENIZERS.setdefault(str(root), tokenizer)
    return _TOKENIZERS[str(root)]


def _model(root):
    key = str(root)
    with _GUARD:
        if key in _MODELS:
            return _MODELS[key], _MODEL_LOCKS.setdefault(key, threading.Lock())
    try:
        from fastembed import TextEmbedding
        model = TextEmbedding(model_name=LOCAL_MODEL, specific_model_path=str(root),
                              local_files_only=True, threads=2)
    except TransportError:
        raise
    except Exception:
        raise TransportError('embedding_model_failed', '本机向量模型未能加载，请检查模型文件后重试。', 503) from None
    with _GUARD:
        return _MODELS.setdefault(key, model), _MODEL_LOCKS.setdefault(key, threading.Lock())


def token_length(data_dir, text):
    """Token count of the exact model input, special tokens included."""
    root = _verified_dir(data_dir)
    return len(_tokenizer(root).encode(text).ids)


def embed(texts, data_dir, stop_event=None):
    """Ordered normalized vectors for already-bounded model inputs."""
    root = _verified_dir(data_dir)
    model, lock = _model(root)
    vectors = []
    for offset in range(0, len(texts), _BATCH_SIZE):
        if stop_event is not None and stop_event.is_set():
            raise TransportError('cancelled', '向量任务已取消。', 409)
        batch = texts[offset:offset + _BATCH_SIZE]
        try:
            with lock:
                arrays = list(model.embed(batch, batch_size=_BATCH_SIZE))
        except TransportError:
            raise
        except Exception:
            raise TransportError('embedding_model_failed', '本机向量推理失败，请稍后重试。', 503) from None
        if len(arrays) != len(batch):
            raise TransportError('embedding_model_failed', '本机向量输出数量不正确。', 502)
        for array in arrays:
            values = [float(value) for value in array]
            if len(values) != 512 or any(value != value or value in (float('inf'), float('-inf')) for value in values):
                raise TransportError('invalid_model_response', '本机向量输出格式不正确。', 502)
            peak = max(abs(value) for value in values)
            if peak == 0.0:
                raise TransportError('invalid_model_response', '本机模型产生了无效零向量。', 502)
            scaled = [value / peak for value in values]
            norm = sum(value * value for value in scaled) ** 0.5
            vectors.append([value / norm for value in scaled])
    return vectors
