"""Local encrypted provider settings and short lived, draft-bound test receipts."""
from datetime import datetime,timezone
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time

from ai_transport import AITransport,TransportError
from provider_catalog import PROVIDERS,checked_config
from secret_store import SecretBox


class SettingsError(Exception):
    def __init__(self,code,message,status=400):
        super().__init__(message);self.code,self.message,self.status=code,message,status


def _now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
_SECRET_FIELDS=('api_key','secret_id','secret_key')


class SettingsService:
    def __init__(self,data_dir,transport=None):
        self.data_dir=Path(data_dir);self.path=self.data_dir/'runtime/settings.sqlite'
        self.transport=transport or AITransport();self.box=SecretBox()
        self._lock=threading.RLock();self._tickets={};self._session={}

    def providers(self):
        return {'providers':[{**p,'base_url':p['default_base_url'],'supports_models':p['models_path'] is not None,
                              'requires_key':p['id']!='ollama'} for p in PROVIDERS]}

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
        if not config:return {'kind':kind,'status':'not_configured','has_key':False,'key_source':'none','provider':'' if kind=='ai' else 'yuanbao','model':'','base_url':'','tested_at':None,**({'auth_mode':'api_key'} if kind=='yuanbao' else {})}
        standard=kind=='yuanbao' and config.get('auth_mode')=='standard'
        has_key=bool(config.get('secret_id') and config.get('secret_key')) if standard else bool(config.get('api_key'))
        return {key:config.get(key) for key in ('provider','model','base_url','status','tested_at','saved_at')}|{'kind':kind,'has_key':has_key,'key_source':'saved',**({'auth_mode':config.get('auth_mode','api_key')} if kind=='yuanbao' else {})}

    def public_settings(self):
        return {'ai':self._public(self._read('ai'),'ai'),'yuanbao':self._public(self._read('yuanbao'),'yuanbao'),
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
        if not isinstance(kind,str) or kind not in {'ai','yuanbao'}:raise SettingsError('invalid_settings','配置类型不正确。')
        if kind!='yuanbao' and any(key in payload for key in ('auth_mode','secret_id','secret_key')):
            raise SettingsError('invalid_settings','标准签名凭据仅用于元宝搜索。')
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
        try:config=checked_config(draft)
        except TransportError as error:raise SettingsError(error.code,error.message,error.status) from None
        return kind,config

    def models(self,payload):
        _,config=self._draft(payload)
        try:return self.transport.models(config)
        except TransportError as error:raise SettingsError(error.code,error.message,error.status) from None

    @staticmethod
    def _signature(kind,config):
        return hashlib.sha256(json.dumps([kind,*[config.get(key,'') for key in ('provider','base_url','model','auth_mode',*_SECRET_FIELDS)]],ensure_ascii=False).encode()).hexdigest()

    def test(self,payload):
        kind,config=self._draft(payload)
        try:
            if kind=='yuanbao':result=self.transport.test_yuanbao(config if config['auth_mode']=='standard' else config['api_key'])
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
            self._tickets[token]={'signature':self._signature(kind,config),'expires':time.monotonic()+600,'tested_at':tested}
        return {**result,'tested_at':tested,'validation_token':token,'notice':'真实服务调用已完成；保存后才对后续研究生效。'}

    def save(self,payload):
        kind,config=self._draft(payload)
        with self._lock:
            receipt=self._tickets.get(payload.get('validation_token',''),{})
            valid=bool(receipt and receipt['expires']>time.monotonic() and receipt['signature']==self._signature(kind,config))
            value={key:config[key] for key in ('provider','base_url','model',*_SECRET_FIELDS,'auth_mode') if key in config}
            value={**value,'status':'connected' if valid else 'not_tested','tested_at':receipt.get('tested_at') if valid else None,'saved_at':_now()}
            try:
                if self.box.persistent:
                    credentials={key:value[key] for key in _SECRET_FIELDS if key in value}
                    secret=self.box.encrypt(json.dumps(credentials,ensure_ascii=False));public={k:v for k,v in value.items() if k not in _SECRET_FIELDS}
                    public={**public,'_credential_format':2}
                    with self._connection() as connection:
                        connection.execute('INSERT OR REPLACE INTO settings(kind,payload,secret) VALUES(?,?,?)',(kind,json.dumps(public,ensure_ascii=False),secret))
                self._session={**self._session,kind:value}
            except (OSError,sqlite3.Error):raise SettingsError('settings_save_failed','配置未能安全保存，请重试。',503) from None
        return self.public_settings()

    def delete(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'kind'} or not isinstance(payload['kind'],str) or payload['kind'] not in {'ai','yuanbao'}:
            raise SettingsError('invalid_settings','请选择要清除的配置。')
        with self._lock:
            kind=payload['kind']
            if self.path.exists():
                try:
                    with self._connection() as connection:connection.execute('DELETE FROM settings WHERE kind=?',(kind,))
                except sqlite3.Error:raise SettingsError('settings_save_failed','配置暂时无法清除。',503) from None
            self._session={key:value for key,value in self._session.items() if key!=kind}
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
