"""Fixed Yuanbao endpoints and Tencent TC3 signing over the exact transmitted bytes."""
from datetime import datetime,timezone
import hashlib
import hmac
import json
import time

from provider_catalog import checked_config,TransportError


def build_request(config,body,timestamp=None):
    config=checked_config(config)
    if config['provider']!='yuanbao' or not isinstance(body,dict):
        raise TransportError('invalid_request','元宝搜索请求格式不正确。')
    try:raw=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,UnicodeError):raise TransportError('invalid_request','元宝搜索请求格式不正确。') from None
    if len(raw)>1024*1024:raise TransportError('request_too_large','元宝搜索请求超过安全上限。')
    headers={'Accept':'application/json','Content-Type':'application/json'}
    if config['auth_mode']=='api_key':
        return config['base_url'],raw,{**headers,'Authorization':'Bearer '+config['api_key']}
    stamp=int(time.time()) if timestamp is None else timestamp
    if type(stamp) is not int or not 0<=stamp<=253402300799:
        raise TransportError('invalid_timestamp','签名时间不正确，请检查系统时间。')
    date=datetime.fromtimestamp(stamp,timezone.utc).strftime('%Y-%m-%d')
    host='wsa.tencentcloudapi.com';scope=date+'/wsa/tc3_request'
    canonical='POST\n/\n\ncontent-type:application/json\nhost:'+host+'\n\ncontent-type;host\n'+hashlib.sha256(raw).hexdigest()
    to_sign='TC3-HMAC-SHA256\n'+str(stamp)+'\n'+scope+'\n'+hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    def sign(key,message):return hmac.new(key,message.encode('utf-8'),hashlib.sha256).digest()
    secret_date=sign(('TC3'+config['secret_key']).encode('utf-8'),date)
    secret_service=sign(secret_date,'wsa');secret_signing=sign(secret_service,'tc3_request')
    signature=hmac.new(secret_signing,to_sign.encode('utf-8'),hashlib.sha256).hexdigest()
    authorization='TC3-HMAC-SHA256 Credential='+config['secret_id']+'/'+scope+', SignedHeaders=content-type;host, Signature='+signature
    return config['base_url'],raw,{**headers,'Host':host,'Authorization':authorization,'X-TC-Action':'SearchPro',
                                      'X-TC-Version':'2025-05-08','X-TC-Timestamp':str(stamp)}


def checked_response(value):
    body=value.get('Response',value) if isinstance(value,dict) else None
    if isinstance(body,dict) and 'Error' in body:
        error=body['Error'];remote=error.get('Code','') if isinstance(error,dict) else ''
        remote=remote if isinstance(remote,str) else ''
        if remote in {'AuthFailure.SignatureExpire','AuthFailure.SignatureExpired'}:
            code,message,status='signature_expired','腾讯云签名已过期，请同步本机时间后重试。',503
        elif remote.startswith('AuthFailure'):
            code,message,status='provider_auth_failed','腾讯云鉴权未通过，请检查 SecretId、SecretKey 与密钥状态。',503
        elif remote.startswith('UnauthorizedOperation'):
            code,message,status='provider_permission_denied','元宝搜索未授权，请检查凭据及账号的服务权限。',503
        elif remote.startswith('RequestLimitExceeded'):
            code,message,status='provider_rate_limited','元宝搜索正在限流，请稍后重试。',503
        elif remote.startswith('ResourceNotFound'):
            code,message,status='search_not_enabled','元宝搜索服务尚未开通，请在腾讯云控制台检查。',503
        elif remote.startswith('ResourceUnavailable'):
            code,message,status='search_unavailable','元宝搜索暂不可用，请检查账号余额及服务状态。',503
        else:code,message,status='provider_request_failed','元宝搜索未接受本次请求，请检查服务状态后重试。',503
        raise TransportError(code,message,status)
    if not isinstance(body,dict):
        raise TransportError('invalid_response','元宝搜索响应无法识别，请检查服务权限。',502)
    if 'Pages' not in body:
        # SearchPro omits Pages entirely when the index has no hit for the query
        # (notably with a Site filter). A RequestId plus the echoed Query or
        # Version identifies that valid empty response without inventing content.
        empty=(isinstance(body.get('RequestId'),str) and bool(body['RequestId'])
               and (isinstance(body.get('Query'),str) or isinstance(body.get('Version'),str)))
        if empty:return {**body,'Pages':[]}
        raise TransportError('invalid_response','元宝搜索响应无法识别，请检查服务权限。',502)
    if not isinstance(body.get('Pages'),list):
        raise TransportError('invalid_response','元宝搜索响应无法识别，请检查服务权限。',502)
    return body
