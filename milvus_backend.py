"""One lazy native Milvus Lite backend, confined to this project's runtime directory."""
import json
from pathlib import Path
import re
import threading

_POOL={}
_POOL_LOCK=threading.RLock()


class MilvusBackend:
    def __init__(self,data_dir):
        base=Path(data_dir).resolve()
        self.path=base/'runtime/vector-index/milvus.db'
        # Re-check the final resolved path against the project data directory so an
        # outward junction/symlink on runtime/vector-index is refused before the
        # client (or any file) is created.
        if not self.path.resolve().is_relative_to(base):
            raise ValueError('Vector index path escapes the project data directory.')
        self._key=str(self.path);self._closed=False
        with _POOL_LOCK:
            if self._key not in _POOL:
                from pymilvus import MilvusClient
                self.path.parent.mkdir(parents=True,exist_ok=True)
                _POOL[self._key]={'client':MilvusClient(str(self.path)),'lock':threading.RLock(),'refs':0}
            self._shared=_POOL[self._key];self._shared['refs']+=1

    @staticmethod
    def _name(space):
        value=space.get('id')
        if not isinstance(value,str) or not re.fullmatch(r'vs_[0-9a-f]{32}',value):raise ValueError('Invalid vector space.')
        return 'yanxu_'+value

    def _handle(self):
        if self._shared is None:raise ValueError('Milvus backend is already closed.')
        return self._shared

    def ensure_space(self,space):
        name=self._name(space)
        with self._handle()['lock']:
            client=self._handle()['client']
            if not client.has_collection(name):
                client.create_collection(name,dimension=space['dimension'],id_type='string',max_length=128,
                                         metric_type='COSINE',auto_id=False)
            description=client.describe_collection(name)
            field=next((field for field in description.get('fields',[]) if field.get('name')=='vector'),{})
            if int(field.get('params',{}).get('dim',0))!=space['dimension']:raise ValueError('Vector dimension changed.')
            client.load_collection(name)

    def upsert(self,space,records):
        # The ANN store keeps only the deterministic id and the vector; authoritative
        # fields stay in SQLite and the payload is never treated as fact.
        projected=[{'id':row['id'],'vector':row['vector']} for row in records]
        with self._handle()['lock']:
            self._handle()['client'].upsert(self._name(space),projected)

    def contains(self,space,identifiers):
        if not identifiers:return set()
        with self._handle()['lock']:
            client=self._handle()['client'];client.load_collection(self._name(space))
            return {row['id'] for row in client.get(self._name(space),ids=identifiers,output_fields=['id'])}

    def search(self,space,vector,allowed_ids,limit):
        if not allowed_ids:return []
        if len(allowed_ids)>256 or any(not re.fullmatch(r'vi_[0-9a-f]{32}',value) for value in allowed_ids):
            raise ValueError('Invalid vector allowlist.')
        with self._handle()['lock']:
            client=self._handle()['client'];client.load_collection(self._name(space))
            rows=client.search(self._name(space),data=[vector],filter='id in '+json.dumps(allowed_ids),
                               limit=limit,output_fields=['id'],search_params={'metric_type':'COSINE'})
        return [{'id':row['id'],'score':row['distance']} for row in rows[0]]

    def close(self):
        with _POOL_LOCK:
            if self._closed:return
            self._closed=True
            shared=self._shared
            self._shared=None
            if shared is None:return
            shared['refs']-=1
            if not shared['refs']:
                with shared['lock']:shared['client'].close()
                _POOL.pop(self._key,None)
                # Milvus Lite serves the database from an embedded gRPC server
                # that holds the LOCK file; releasing the server is what lets a
                # following process reopen the same data directory.
                try:
                    from milvus_lite.server_manager import server_manager_instance
                    server_manager_instance.release_server(self._key)
                except Exception:
                    pass
