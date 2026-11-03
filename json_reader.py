"""Read large local snapshots without keeping a second Unicode copy in memory."""
import importlib
import importlib.util
from decimal import Decimal
import json
import math
from pathlib import Path
import sys
import threading

STREAM_THRESHOLD = 32 * 1024 * 1024
_PARSER_VERSION = '3.4.0.post0'
_IMPORT_LOCK = threading.Lock()


class SnapshotReadError(ValueError):
    pass


def _nonfinite(value):
    raise SnapshotReadError('Non-finite numbers are not valid source data.')


def _finite_float(value):
    number = float(value)
    return number if math.isfinite(number) else _nonfinite(value)


def _stream_parser():
    with _IMPORT_LOCK:
        try:
            module = sys.modules.get('ijson')
            if module is None:
                path = Path(__file__).parent / '.runtime-deps/ijson/__init__.py'
                if path.is_file():
                    spec = importlib.util.spec_from_file_location('ijson', path, submodule_search_locations=[str(path.parent)])
                    module = importlib.util.module_from_spec(spec)
                    sys.modules['ijson'] = module
                    spec.loader.exec_module(module)
                else:
                    module = importlib.import_module('ijson')
            if getattr(module, '__version__', None) != _PARSER_VERSION:
                raise ImportError('ijson version mismatch')
            return module
        except (ImportError, OSError, RuntimeError, AttributeError, SyntaxError) as error:
            raise SnapshotReadError('大资料库需要 ijson==3.4.0.post0，请按运行文档安装并重启服务。') from error


def _finite_events(events):
    for prefix, event, value in events:
        # Preserve unbounded integers; convert decimal tokens before building containers.
        if event == 'number' and isinstance(value, Decimal):
            value = _finite_float(value)
        yield prefix, event, value


def read_json(path):
    path = Path(path)
    if path.stat().st_size <= STREAM_THRESHOLD:
        try:
            return json.loads(path.read_text(encoding='utf-8-sig'), parse_constant=_nonfinite,
                              parse_float=_finite_float)
        except (ValueError, UnicodeError) as error:
            raise SnapshotReadError('Invalid JSON source data.') from error
    parser = _stream_parser()
    try:
        with path.open('rb') as stream:
            if stream.read(3) != b'\xef\xbb\xbf':
                stream.seek(0)
            values = parser.items(_finite_events(parser.parse(stream, use_float=False)), '')
            value = next(values)
            # Exhaust the parser so trailing garbage cannot pass as a valid snapshot.
            if next(values, None) is not None:
                raise SnapshotReadError('Multiple JSON roots are not valid source data.')
            return value
    except (parser.JSONError, StopIteration, OverflowError) as error:
        raise SnapshotReadError('Invalid JSON source data.') from error
