"""Bounded content-addressed vector cache; never a source of admission facts.

The cache only stores vectors derived from already validated embedding inputs. Its
identity is (space fingerprint, actual model input sha, dimension), so a query vector,
a private archive or an API key can never enter the shared cache, and different
profiles/dimensions/prefixes can never be mixed.
"""
import hashlib
import json
import math
import re
import struct
import uuid
from pathlib import Path


def identifier(prefix, *parts):
    value=json.dumps(parts,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    return prefix+hashlib.sha256(value).hexdigest()[:32]


def checked_vector(vector, dimension):
    if (not isinstance(vector,(list,tuple)) or type(dimension) is not int or not 1<=dimension<=32768
            or len(vector)!=dimension or any(type(value) not in (int,float) or not math.isfinite(value) for value in vector)):
        raise ValueError('Invalid embedding vector.')
    norm=math.sqrt(sum(float(value)**2 for value in vector))
    if not math.isfinite(norm) or norm<=0:raise ValueError('Invalid embedding norm.')
    return [float(value)/norm for value in vector]


class VectorCache:
    def __init__(self, data_dir):
        self.data_dir=Path(data_dir).resolve()
        root=self.data_dir/'runtime/vector-index/cache'
        # An outward junction/symlink on runtime/vector-index must be rejected before any
        # write: the resolved root has to stay inside the project data directory.
        if not root.resolve().is_relative_to(self.data_dir):
            raise ValueError('Vector cache directory escapes the project data directory.')
        self.root=root.resolve()

    def _path(self, space_id, input_sha):
        if not re.fullmatch(r'vs_[0-9a-f]{32}',space_id) or not re.fullmatch('[0-9a-f]{64}',input_sha):
            raise ValueError('Invalid vector cache identity.')
        path=(self.root/space_id/(input_sha+'.bin')).resolve()
        if not path.is_relative_to(self.root) or not path.is_relative_to(self.data_dir):
            raise ValueError('Unsafe vector cache path.')
        return path

    def get(self, space_id, input_sha, dimension):
        path=self._path(space_id,input_sha)
        try:
            with path.open('rb') as stream:raw=stream.read(32+4*dimension+1)
            if len(raw)!=32+4*dimension or hashlib.sha256(raw[32:]).digest()!=raw[:32]:return None
            return checked_vector(list(struct.unpack('<'+'f'*dimension,raw[32:])),dimension)
        except (OSError,ValueError,OverflowError,struct.error):return None

    def put(self, space_id, input_sha, vector, dimension):
        path=self._path(space_id,input_sha);path.parent.mkdir(parents=True,exist_ok=True)
        raw=struct.pack('<'+'f'*dimension,*checked_vector(vector,dimension))
        temporary=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_bytes(hashlib.sha256(raw).digest()+raw)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
