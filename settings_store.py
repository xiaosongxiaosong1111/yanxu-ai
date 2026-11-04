"""Local encrypted provider settings, short lived draft-bound test receipts, and
independent embedding profiles bound to serving vector spaces."""
from datetime import datetime,timezone
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

from ai_transport import AITransport,TransportError
from embedding_catalog import checked_embedding_config,public_providers
from embedding_transport import EmbeddingTransport
from provider_catalog import PROVIDERS,checked_config
from secret_store import SecretBox


class SettingsError(Exception):
    def __init__(self,code,message,status=400):
        super().__init__(message);self.code,self.message,self.status=code,message,status


def _now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
_SECRET_FIELDS=('api_key','secret_id','secret_key')
_KINDS=('ai','yuanbao','embedding')
_PROFILE_PATTERN=re.compile(r'ep_[0-9a-f]{32}')
_PROFILE_PREFIX='embedding_profile:'
_DEFAULT_PROVIDER={'ai':'','yuanbao':'yuanbao','embedding':'local_bge'}


class SettingsService:
    def __init__(self,data_dir,transport=None,embedding=None):
        self.data_dir=Path(data_dir);self.path=self.data_dir/'runtime/settings.sqlite'
        self.transport=transport or AITransport();self.box=SecretBox()
        self.embedding=embedding
        self._lock=threading.RLock();self._tickets={};self._session={}

    def providers(self):
        return {'providers':[{**p,'base_url':p['default_base_url'],'supports_models':p['models_path'] is not None,
                              'requires_key':p['id']!='ollama'} for p in PROVIDERS],
                'embedding_providers':public_providers()}

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        connection=sqlite3.connect(self.path,timeout=10)
        try:
            with connection:
                connection.execute('CREATE TABLE IF NOT EXISTS settings(kind TEXT PRIMARY KEY,payload TEXT NOT NULL,secret BLOB NOT NULL)')
                yield connection
        finally:connection.close()

    def _read(self,kind):
        with self._lock:
            if kind in self._session:return dict(self._session[kind])
            if not self.path.exists():return None
            try:
                with self._connection() as connection:row=connection.execute('SELECT payload,secret FROM settings WHERE kind=?',(kind,)).fetchone()
                if row is None:return None
                config=json.loads(row[0]);secret=self.box.decrypt(bytes(row[1]))
                if not isinstance(config,dict):raise ValueError('shape')
                version=config.get('_credential_format',1)
                config={key:value for key,value in config.items() if key!='_credential_format'}
                if type(version) is not int or version not in {1,2}:raise ValueError('credentials version')
                if version==1:return {**config,'api_key':secret}
                credentials=json.loads(secret)
                if version!=2 or not isinstance(credentials,dict) or any(key not in _SECRET_FIELDS or not isinstance(value,str) for key,value in credentials.items()):
                    raise ValueError('credentials')
                return {**config,**credentials}
            except (OSError,sqlite3.Error,ValueError,TypeError):
                raise SettingsError('settings_unavailable','本机配置无法解密或读取，请重新配置。',503) from None

    @staticmethod
    def _public(config,kind):
        if not config:return {'kind':kind,'status':'not_configured','has_key':False,'key_source':'none',
                              'provider':_DEFAULT_PROVIDER[kind],'model':'','base_url':'','tested_at':None,
                              **({'auth_mode':'api_key'} if kind=='yuanbao' else {}),
                              **({'dimension':None,'response_model':'','model_revision':'','profile_id':'','normalization':'l2'} if kind=='embedding' else {})}
        standard=kind=='yuanbao' and config.get('auth_mode')=='standard'
        has_key=bool(config.get('secret_id') and config.get('secret_key')) if standard else bool(config.get('api_key'))
        public={key:config.get(key) for key in ('provider','model','base_url','status','tested_at','saved_at')}|{'kind':kind,'has_key':has_key,'key_source':'saved',**({'auth_mode':config.get('auth_mode','api_key')} if kind=='yuanbao' else {})}
        if kind=='embedding':
            public={**public,'dimension':config.get('dimension'),'response_model':config.get('response_model',''),
                    'model_revision':config.get('model_revision',''),'profile_id':config.get('profile_id',''),
                    'normalization':config.get('normalization','l2')}
        return public

    def public_settings(self):
        return {'ai':self._public(self._read('ai'),'ai'),'yuanbao':self._public(self._read('yuanbao'),'yuanbao'),
                'embedding':self._public(self._read('embedding'),'embedding'),
                'storage':{'mode':self.box.mode,'persistent':self.box.persistent},'providers':self.providers()['providers']}

    def _draft(self,payload):
        if not isinstance(payload,dict) or any(key not in {'kind','provider','api_key','base_url','model','use_saved_key','validation_token','auth_mode','secret_id','secret_key'} for key in payload):
            raise SettingsError('invalid_settings','配置字段格式不正确。')
        if ('use_saved_key' in payload and type(payload['use_saved_key']) is not bool
                or not isinstance(payload.get('validation_token',''),str) or len(payload.get('validation_token',''))>128
                or any(not isinstance(payload.get(key,''),str) for key in _SECRET_FIELDS)
                or payload.get('base_url') is not None and not isinstance(payload['base_url'],str)):
            raise SettingsError('invalid_settings','配置字段格式不正确。')
        kind=payload.get('kind','ai')
        if not isinstance(kind,str) or kind not in _KINDS:raise SettingsError('invalid_settings','配置类型不正确。')
        if kind!='yuanbao' and any(key in payload for key in ('auth_mode','secret_id','secret_key')):raise SettingsError('invalid_settings','标准签名凭据仅用于元宝搜索。')
        provider='yuanbao' if kind=='yuanbao' else payload.get('provider')
        draft={key:payload[key] for key in ('auth_mode','secret_id','secret_key') if key in payload}
        draft={**draft,'provider':provider,'api_key':payload.get('api_key',''),'base_url':payload.get('base_url'),'model':payload.get('model','')}
        if payload.get('use_saved_key'):
            saved=self._read(kind)
            requested_base=payload.get('base_url')
            same=bool(saved and saved.get('provider')==provider and (not requested_base or isinstance(requested_base,str) and requested_base.rstrip('/')==str(saved.get('base_url','')).rstrip('/')))
            if kind=='yuanbao':same=same and draft.get('auth_mode','api_key')==saved.get('auth_mode','api_key')
            has_new=any(draft.get(key) for key in _SECRET_FIELDS)
            if not has_new and same:draft={**draft,**{key:saved.get(key,'') for key in _SECRET_FIELDS if key in saved}}
            elif not has_new:raise SettingsError('missing_key','该接入方式需要填写自己的完整凭据。')
        try:
            config=checked_embedding_config(draft) if kind=='embedding' else checked_config(draft)
        except TransportError as error:raise SettingsError(error.code,error.message,error.status) from None
        return kind,config

    @staticmethod
    def _signature(kind,config):
        return hashlib.sha256(json.dumps([kind,*[config.get(key,'') for key in ('provider','base_url','model','model_revision','auth_mode',*_SECRET_FIELDS)]],ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def profile_id(config, dimension=None):
        """Deterministic binding of model identity, credentials and the actually observed
        vector dimension. A key change or a new observed dimension gets a new profile so
        old serving spaces keep their own encrypted snapshot untouched."""
        basis=json.dumps([config.get('provider',''),config.get('base_url',''),config.get('model',''),
                          config.get('model_revision',''),config.get('api_key',''),
                          int(dimension) if type(dimension) is int and not isinstance(dimension,bool) and dimension>0 else None],
                         ensure_ascii=False)
        return 'ep_'+hashlib.sha256(basis.encode()).hexdigest()[:32]

    def _embedding_transport(self):
        return self.embedding or EmbeddingTransport(self.data_dir)

    def models(self,payload):
        kind,config=self._draft(payload)
        try:
            return self._embedding_transport().models(config) if kind=='embedding' else self.transport.models(config)
        except TransportError as error:raise SettingsError(error.code,error.message,error.status) from None

    def test(self,payload):
        kind,config=self._draft(payload)
        try:
            if kind=='yuanbao':result=self.transport.test_yuanbao(config if config['auth_mode']=='standard' else config['api_key'])
            elif kind=='embedding':
                if not config['model']:raise SettingsError('missing_model','请选择或填写向量模型 ID。')
                texts=('面向跨专业考生的加试科目说明。','但本专业不含同等学力考生。')
                response=self._embedding_transport().embed(config,list(texts),purpose='document')
                vectors=response.get('vectors');dimension=response.get('dimension')
                if not isinstance(vectors,list) or len(vectors)!=len(texts) or type(dimension) is not int or not 1<=dimension<=8192:
                    raise SettingsError('invalid_model_response','服务未返回有效向量，请确认所选模型支持向量生成。',502)
                if config['dimension'] is not None and dimension!=config['dimension']:
                    raise SettingsError('embedding_dimension_mismatch','向量维度与配置不一致，请核对模型。',502)
                result={'status':'connected','model':config['model'],'dimension':dimension,
                        'response_model':str(response.get('model',''))[:200]}
            else:
                if not config['model']:raise SettingsError('missing_model','请选择或填写模型 ID。')
                response=self.transport.chat(config,[{'role':'user','content':'请只回复 OK。'}])
                message=response.get('message',{})
                if not isinstance(message.get('content'),str) or not message['content'].strip():
                    raise SettingsError('empty_model_response','模型接受请求但未产生有效回复，请选择可用的对话模型。',502)
                result={'status':'connected','model':config['model']}
        except TransportError as error:raise SettingsError(error.code,error.message,error.status) from None
        token=secrets.token_urlsafe(32);tested=_now()
        with self._lock:
            self._tickets={key:value for key,value in self._tickets.items() if value['expires']>time.monotonic()}
            if len(self._tickets)>128:self._tickets={}
            receipt={'signature':self._signature(kind,config),'expires':time.monotonic()+600,'tested_at':tested}
            if kind=='embedding':receipt['dimension']=result['dimension'];receipt['response_model']=result['response_model']
            self._tickets[token]=receipt
        return {**result,'tested_at':tested,'validation_token':token,'notice':'真实服务调用已完成；保存后才对后续研究生效。'}

    def _store(self,kind,value):
        if self.box.persistent:
            credentials={key:value[key] for key in _SECRET_FIELDS if key in value}
            secret=self.box.encrypt(json.dumps(credentials,ensure_ascii=False))
            public={k:v for k,v in value.items() if k not in _SECRET_FIELDS}|{'_credential_format':2}
            with self._connection() as connection:
                connection.execute('INSERT OR REPLACE INTO settings(kind,payload,secret) VALUES(?,?,?)',(kind,json.dumps(public,ensure_ascii=False),secret))
        self._session={**self._session,kind:value}

    def save(self,payload):
        kind,config=self._draft(payload)
        with self._lock:
            receipt=self._tickets.get(payload.get('validation_token',''),{})
            valid=bool(receipt and receipt['expires']>time.monotonic() and receipt['signature']==self._signature(kind,config))
            fields=('provider','base_url','model',*_SECRET_FIELDS,'auth_mode') if kind!='embedding' else \
                   ('provider','base_url','model','model_revision','api_key')
            value={key:config[key] for key in fields if config.get(key)}
            value={**value,'status':'connected' if valid else 'not_tested','tested_at':receipt.get('tested_at') if valid else None,'saved_at':_now()}
            if kind=='embedding':
                value['profile_id']=self.profile_id(config,receipt.get('dimension') if valid else None)
                value['normalization']=config.get('normalization','l2')
                if valid:
                    value['dimension']=receipt['dimension'];value['response_model']=receipt['response_model']
            try:
                if kind=='embedding' and valid:
                    self._store(_PROFILE_PREFIX+value['profile_id'],{**value,'kind':'embedding'})
                self._store(kind,value)
            except (OSError,sqlite3.Error):raise SettingsError('settings_save_failed','配置未能安全保存，请重试。',503) from None
        return self.public_settings()

    def delete(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'kind'} or not isinstance(payload['kind'],str) or payload['kind'] not in _KINDS:
            raise SettingsError('invalid_settings','请选择要清除的配置。')
        with self._lock:
            kind=payload['kind']
            if self.path.exists():
                try:
                    with self._connection() as connection:
                        if kind=='embedding':
                            connection.execute("DELETE FROM settings WHERE kind=? OR kind LIKE ?",(kind,_PROFILE_PREFIX+'%'))
                        else:connection.execute('DELETE FROM settings WHERE kind=?',(kind,))
                except sqlite3.Error:raise SettingsError('settings_save_failed','配置暂时无法清除。',503) from None
            self._session={key:value for key,value in self._session.items() if key!=kind and not (kind=='embedding' and key.startswith(_PROFILE_PREFIX))}
        return self.public_settings()

    def runtime_ai(self):
        config=self._read('ai')
        if not config:raise SettingsError('not_configured','请先配置 AI 服务。',503)
        if config.get('status')!='connected':
            raise SettingsError('model_not_verified','请先测试并保存当前 AI 模型，验证成功后再开始研究。',503)
        return checked_config(config)

    def yuanbao_key(self):
        config=self._read('yuanbao') or {}
        return config.get('api_key','') if config.get('auth_mode','api_key')=='api_key' else ''

    def runtime_yuanbao(self):
        config=self._read('yuanbao')
        if config is None:return None
        try:return checked_config(config)
        except TransportError as error:raise SettingsError(error.code,error.message,error.status) from None

    def runtime_embedding(self,profile_id=None):
        """Desired saved config, or the encrypted snapshot of a profile bound to a
        serving vector space. Deleted profiles degrade honestly; chat keys are never borrowed."""
        if profile_id is None:
            config=self._read('embedding')
            if not config:raise SettingsError('not_configured','请先配置知识检索向量模型。',503)
            if config.get('status')!='connected':
                raise SettingsError('model_not_verified','请先测试并保存当前向量模型，验证成功后再建立或查询语义索引。',503)
            try:return checked_embedding_config(config)
            except TransportError as error:raise SettingsError(error.code,error.message,error.status) from None
        if not isinstance(profile_id,str) or not _PROFILE_PATTERN.fullmatch(profile_id):
            raise SettingsError('invalid_settings','向量配置编号不正确。')
        config=self._read(_PROFILE_PREFIX+profile_id)
        if not config or config.get('status')!='connected':
            raise SettingsError('embedding_profile_unavailable','该语义索引绑定的向量模型配置已被移除，请重新测试并保存后再使用。',503)
        try:config=checked_embedding_config(config)
        except TransportError as error:raise SettingsError(error.code,error.message,error.status) from None
        if config.get('profile_id')!=profile_id:
            raise SettingsError('embedding_profile_unavailable','该语义索引绑定的向量模型配置不再可用。',503)
        return config
