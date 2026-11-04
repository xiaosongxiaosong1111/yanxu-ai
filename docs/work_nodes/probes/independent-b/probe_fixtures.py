"""Independent, disposable public-source fixtures; no real provider or user state."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading

from knowledge_store import KnowledgeStore
from knowledge_vectors import VectorIndex


class Settings:
    def __init__(self):
        self.current={'profile_id':'ep_'+'1'*32,'provider':'custom','base_url':'https://embedding.example.com/v1',
            'api_key':'independent-fake-only','model':'fixed-vector-fixture-v1','model_revision':'revision-1',
            'dimension':3,'normalization':'l2','document_prefix':'','query_prefix':'',
            'input_version':'independent-v1','input_limit':512,'input_limit_unit':'unicode_chars_conservative',
            'local':False}
        self.saved={self.current['profile_id']:deepcopy(self.current)}

    def runtime_embedding(self,profile_id=None):
        return deepcopy(self.saved[profile_id] if profile_id else self.current)

    def rotate(self,identifier='2',**changes):
        self.current={**self.current,**changes,'profile_id':'ep_'+identifier*32}
        self.saved={**self.saved,self.current['profile_id']:deepcopy(self.current)}


class Embedding:
    def __init__(self):
        self.calls=[];self.failure=None;self.callback=None
        self.entered=threading.Event();self.release=threading.Event();self.block=False

    @staticmethod
    def profile(config):
        names=('provider','base_url','model','model_revision','dimension','normalization',
               'document_prefix','query_prefix','input_version','input_limit','input_limit_unit')
        identity={key:config[key] for key in names}
        encoded=json.dumps(identity,sort_keys=True,separators=(',',':')).encode()
        return {**identity,'fingerprint':hashlib.sha256(encoded).hexdigest()}

    @staticmethod
    def prepare_inputs(segment,contexts,config):
        text=segment['text'];value=config['document_prefix']+text
        return [{'text':text,'embedding_text':text,'char_start':segment['char_start'],
                 'char_end':segment['char_end'],'input_sha':hashlib.sha256(value.encode()).hexdigest(),
                 'window_id':'w_'+hashlib.sha256(value.encode()).hexdigest()[:24]}]

    def embed(self,config,texts,purpose='document',stop_event=None):
        self.calls.append({'profile_id':config['profile_id'],'api_key':config['api_key'],
                           'texts':tuple(texts),'purpose':purpose})
        if self.block:
            self.entered.set()
            if not self.release.wait(8):raise TimeoutError('independent bounded wait')
        if self.failure:raise self.failure
        if self.callback:self.callback()
        dimension=config['dimension']
        vectors=[[1.0]+[0.0]*(dimension-1) for _ in texts]
        return {'vectors':vectors,'dimension':dimension,'model':config['model'],
                'model_revision':config['model_revision'],'usage':{'input_tokens':len(texts)},
                'usage_known':True,'input_count':len(texts)}


class Backend:
    def __init__(self):self.rows={};self.queries=[];self.fail=False;self.on_search=None
    def ensure_space(self,space):self.rows.setdefault(space['id'],{})
    def upsert(self,space,records):
        if self.fail:raise OSError('independent secret path must not leak')
        self.rows[space['id']].update({row['id']:deepcopy(row) for row in records})
    def contains(self,space,identifiers):return set(identifiers)&set(self.rows.get(space['id'],{}))
    def search(self,space,vector,allowed_ids,limit):
        self.queries.append(tuple(allowed_ids))
        if not allowed_ids:raise AssertionError('Unfiltered ANN request is forbidden')
        result=[{'id':identifier,'score':1.0} for identifier in sorted(self.rows.get(space['id'],{})) if identifier in allowed_ids][:limit]
        if self.on_search:self.on_search()
        return result
    def close(self):pass


class Fixture:
    def __init__(self,data_dir,backend=None):
        self.root=Path(data_dir);self.store=KnowledgeStore(self.root);self.settings=Settings()
        self.embedding=Embedding();self.backend=backend or Backend()
        self.index=VectorIndex(self.store,self.settings,self.embedding,lambda:self.backend)

    def save(self,name='wanted',unit='unit-a',year=2027,text=None):
        text=text or f'{year}年硕士研究生招生专业目录\n081200 计算机科学与技术\n初试科目101、201、301、408。\n\n跨专业考生须参加加试。'
        raw=('%PDF-1.4\nindependent public fixture '+name).encode()
        source={'url':'https://graduate.example.edu.cn/'+name+'.pdf','title':f'{year}年硕士招生专业目录',
            'unit_id':unit,'unit_name':'独立临时夹具大学','year':year,'requested_year':2028,
            'status':'official_original_read','sha256':hashlib.sha256(raw).hexdigest()}
        document={'raw':raw,'text':text,'pages':[{'page':1,'text':text}],
                  'url':source['url'],'attribution_status':'verified'}
        return self.store.save_source(source,document=document)

    def close(self):
        self.embedding.release.set();self.index.close()
