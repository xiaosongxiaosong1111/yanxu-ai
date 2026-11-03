"""Provider presets and strict endpoint validation; no credentials in public metadata."""
import ipaddress
import re
from urllib.parse import urlsplit


class TransportError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code,self.message,self.status=code,message,status


def _profile(identifier,label,base,protocol='openai',models='/models',note='',docs=''):
    return {'id':identifier,'label':label,'default_base_url':base,'protocol':protocol,
            'models_path':models,'note':note,'docs_url':docs}


PROVIDERS=(
    _profile('deepseek','DeepSeek','https://api.deepseek.com',docs='https://api-docs.deepseek.com/api/list-models'),
    _profile('openai','OpenAI','https://api.openai.com/v1',docs='https://platform.openai.com/docs/api-reference/models/list'),
    _profile('anthropic','Anthropic · Claude','https://api.anthropic.com/v1','anthropic',docs='https://platform.claude.com/docs/en/api/models/list'),
    _profile('gemini','Google · Gemini','https://generativelanguage.googleapis.com/v1beta','gemini',docs='https://ai.google.dev/api/models'),
    _profile('dashscope','阿里云百炼 · 通义','https://dashscope.aliyuncs.com/compatible-mode/v1',models='/api/v1/models',
             note='新版工作空间可能要求专属域名；请在高级设置填写控制台提供的兼容地址。'),
    _profile('moonshot','Moonshot · Kimi','https://api.moonshot.cn/v1'),
    _profile('zhipu','智谱 · GLM','https://open.bigmodel.cn/api/paas/v4',models=None,note='请填写当前账号已开通的模型 ID。'),
    _profile('ark','火山方舟 · 豆包','https://ark.cn-beijing.volces.com/api/v3',models=None,note='需要已开通模型的 ID 或接入点 ID。'),
    _profile('siliconflow','硅基流动','https://api.siliconflow.cn/v1',models='/models?sub_type=chat'),
    _profile('hunyuan','腾讯混元','https://api.hunyuan.cloud.tencent.com/v1',models=None,note='旧混元兼容接口；新 TokenHub 账号请使用自定义兼容接口。'),
    _profile('tokenhub','腾讯 TokenHub','https://tokenhub.tencentmaas.com/v1',note='需为当前地域创建 Key 并开通相应模型。'),
    _profile('openrouter','OpenRouter','https://openrouter.ai/api/v1',note='模型目录并不代表当前 Key 已获所有模型访问权限。'),
    _profile('minimax','MiniMax','https://api.minimax.cn/v1',models=None,note='请填写控制台提供的模型 ID。'),
    _profile('xai','xAI · Grok','https://api.x.ai/v1'),
    _profile('ollama','Ollama · 本地模型','http://127.0.0.1:11434/v1',note='读取本机已安装模型，不要求 API Key。'),
    _profile('custom','自定义 · OpenAI 兼容接口','',note='仅接受公共 HTTPS 接口；本地模型请选择 Ollama。'),
)
_ALL={p['id']:p for p in PROVIDERS}
_ALL['yuanbao']=_profile('yuanbao','元宝搜索','https://api.wsa.cloud.tencent.com/SearchPro',models=None)
MODEL_ID_PATTERN=r'[a-zA-Z0-9][a-zA-Z0-9._:/@+-]*(?:\[1m\])?'
YUANBAO_ENDPOINTS={'api_key':'https://api.wsa.cloud.tencent.com/SearchPro','standard':'https://wsa.tencentcloudapi.com/'}


def _checked_yuanbao(value):
    if any(not isinstance(value.get(key,''),str) for key in ('api_key','secret_id','secret_key')):
        raise TransportError('invalid_credentials','元宝搜索凭据格式不正确，请检查填写内容。')
    if not isinstance(value.get('model',''),str) or value.get('model',''):
        raise TransportError('invalid_model','元宝搜索无需填写模型 ID。')
    mode=value.get('auth_mode','api_key')
    if not isinstance(mode,str) or mode not in YUANBAO_ENDPOINTS:
        raise TransportError('invalid_auth_mode','请选择元宝搜索接入方式。')
    endpoint=YUANBAO_ENDPOINTS[mode];base=value.get('base_url') or endpoint
    if not isinstance(base,str) or base.rstrip('/')!=endpoint.rstrip('/'):
        raise TransportError('unsafe_endpoint','元宝搜索仅允许所选接入方式对应的固定官方接口。')
    fields=('secret_id','secret_key') if mode=='standard' else ('api_key',)
    if any(value.get(key) for key in {'api_key','secret_id','secret_key'}-set(fields)):
        raise TransportError('mixed_credentials','两种接入方式的凭据不能混用。')
    credentials={key:value.get(key,'') for key in fields}
    for key,secret in credentials.items():
        if not isinstance(secret,str) or len(secret)>4096 or any(ord(c)<33 or ord(c)>126 for c in secret):
            raise TransportError('invalid_credentials','元宝搜索凭据格式不正确，请检查填写内容。')
        if not secret:raise TransportError('missing_credentials','请填写完整的 SecretId 和 SecretKey。' if mode=='standard' else '请填写元宝搜索服务 API Key。')
    if mode=='standard' and (len(credentials['secret_id'])>256 or not re.fullmatch(r'[A-Za-z0-9_-]+',credentials['secret_id'])):
        raise TransportError('invalid_credentials','SecretId 格式不正确。')
    return {**_ALL['yuanbao'],'provider':'yuanbao','auth_mode':mode,'base_url':endpoint,'model':'','api_key':'',**credentials}


def checked_config(value):
    if not isinstance(value,dict) or not isinstance(value.get('provider'),str) or value.get('provider') not in _ALL:
        raise TransportError('invalid_provider','请选择支持的服务商。')
    if value['provider']=='yuanbao':return _checked_yuanbao(value)
    if any(key in value for key in ('auth_mode','secret_id','secret_key')):
        raise TransportError('invalid_settings','标准签名凭据仅用于元宝搜索。')
    profile=_ALL[value['provider']]
    base=value.get('base_url') or profile['default_base_url']
    if not isinstance(base,str) or len(base)>1024 or re.search(r'[\x00-\x20\x7f]',base):
        raise TransportError('invalid_endpoint','接口地址格式不正确。')
    try:parts=urlsplit(base);port=parts.port
    except ValueError:raise TransportError('invalid_endpoint','接口地址格式不正确。') from None
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment or not parts.hostname:
        raise TransportError('invalid_endpoint','地址不能包含凭据、查询参数或片段。')
    local=profile['id']=='ollama'
    if local:
        allowed=base.rstrip('/')==profile['default_base_url']
    else:
        allowed=parts.scheme=='https' and port in {None,443}
        if profile['id']!='custom':
            default=urlsplit(profile['default_base_url'])
            hosts={default.hostname}
            extras={'moonshot':{'api.moonshot.ai'},'siliconflow':{'api.siliconflow.com'},
                    'minimax':{'api.minimax.io','api.minimaxi.com'},'dashscope':{'dashscope-intl.aliyuncs.com'},
                    'tokenhub':{'tokenhub-intl.tencentmaas.com'}}
            allowed=allowed and (parts.hostname in hosts|extras.get(profile['id'],set()) or
                profile['id']=='dashscope' and bool(re.fullmatch(r'[a-zA-Z0-9-]+\.(?:cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com',parts.hostname)))
        try:allowed=allowed and ipaddress.ip_address(parts.hostname).is_global
        except ValueError:allowed=allowed and parts.hostname!='localhost' and not parts.hostname.endswith(('.local','.localhost'))
    if not allowed or '..' in parts.path or '\\' in base:
        raise TransportError('unsafe_endpoint','请使用所选厂商的官方 HTTPS 地址；本地接口仅允许 Ollama。')
    key=value.get('api_key','')
    if not isinstance(key,str) or len(key)>4096 or any(ord(c)<33 or ord(c)>126 for c in key):
        raise TransportError('invalid_key','API Key 格式不正确。')
    if not key and not local:raise TransportError('missing_key','请填写 API Key。')
    model=value.get('model','')
    if not isinstance(model,str) or len(model)>200 or model and not re.fullmatch(MODEL_ID_PATTERN,model):
        raise TransportError('invalid_model','模型 ID 格式不正确。')
    return {**profile,'provider':profile['id'],'base_url':base.rstrip('/'),'api_key':key,'model':model}
