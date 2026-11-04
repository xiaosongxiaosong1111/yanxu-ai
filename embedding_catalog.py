"""Independent embedding presets and model identity, never chat-provider assumptions."""
import hashlib
import json
import re
from provider_catalog import TransportError, checked_config

LOCAL_MODEL = 'BAAI/bge-small-zh-v1.5'
LOCAL_REVISION = '46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59'
INPUT_VERSION = 'canonical-token-windows-v1'
MODEL_SHA256 = '1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38'
MODEL_SIZE = 94781076
# FastEmbed 0.9.1 loads tokenizer.json plus the config/special-token maps; vocab.txt is not
# part of the prepared model and must not be required.
MODEL_FILES = {
    'config.json': '3d567ab38f275cb9e8ee7d8d3e19e2cb87b21cf8',
    'tokenizer.json': '207dee12768c4f916b42c0cf49db26a92a84367e',
    'tokenizer_config.json': '3c6108e1da5d33e5934fe594e6e58479a0a79050',
    'special_tokens_map.json': '3c3ddd2e5b709f54f4c4e110fd6b9f0884d5f7ec',
}
_PRESETS = (
    ('local_bge', '本机中文模型 · BGE', '', LOCAL_MODEL, False, True, None,
     'https://qdrant.tech/documentation/embeddings/fastembed/'),
    ('siliconflow', '硅基流动', 'https://api.siliconflow.cn/v1', 'BAAI/bge-m3', True, False,
     '/models?sub_type=embedding', 'https://api-docs.siliconflow.cn/docs/api/embeddings-post'),
    ('dashscope', '阿里云百炼', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'text-embedding-v4', True, False,
     None, 'https://www.alibabacloud.com/help/en/model-studio/embedding'),
    ('openai', 'OpenAI', 'https://api.openai.com/v1', 'text-embedding-3-small', True, False,
     '/models', 'https://developers.openai.com/api/reference/resources/embeddings/methods/create'),
    ('ollama', 'Ollama · 本地', 'http://127.0.0.1:11434/v1', 'embeddinggemma', False, True,
     '/api/tags', 'https://docs.ollama.com/api/embed'),
    ('custom', '自定义 Embedding 兼容接口', '', '', True, False,
     '/models', 'https://developers.openai.com/api/reference/resources/embeddings/methods/create'),
)


def public_providers():
    keys = ('id', 'label', 'default_base_url', 'default_model', 'requires_key', 'local', 'models_path', 'docs_url')
    return [{**dict(zip(keys, row)), 'base_url': row[2], 'default_dimension': 512 if row[0] == 'local_bge' else None,
             'note': '仅为向量模型候选；实际能力与维数须测试。'} for row in _PRESETS]


def _string(value, limit, *, empty=True):
    if not isinstance(value, str) or len(value) > limit or not empty and not value:
        raise TransportError('invalid_embedding_config', '向量模型配置格式不正确。')
    if any(ord(char) < 32 and char not in '\n\t' or ord(char) == 127 for char in value):
        raise TransportError('invalid_embedding_config', '向量配置不能包含控制字符。')
    try: value.encode('utf-8')
    except UnicodeError: raise TransportError('invalid_embedding_config', '向量配置字符格式不正确。') from None
    return value


def checked_embedding_config(value=None):
    if value is None: value = {'provider': 'local_bge'}
    if not isinstance(value, dict) or not isinstance(value.get('provider'), str):
        raise TransportError('invalid_embedding_config', '请选择向量模型服务。')
    preset = next((item for item in public_providers() if item['id'] == value['provider']), None)
    if preset is None: raise TransportError('invalid_provider', '该服务未声明支持向量模型。')
    if any(key in value for key in ('auth_mode', 'secret_id', 'secret_key')):
        raise TransportError('invalid_embedding_config', '向量模型不使用元宝签名凭据。')
    model = value.get('model') or preset['default_model']
    if preset['id'] == 'local_bge':
        if model != LOCAL_MODEL or value.get('api_key') or value.get('base_url'):
            raise TransportError('invalid_embedding_config', '本机模型使用固定文件，不需要 API Key 或网络地址。')
        base, key = '', ''
    else:
        remote = checked_config({**value, 'model': model})
        base, key = remote['base_url'], remote['api_key']
    revision = _string(value.get('model_revision', value.get('revision')) or (LOCAL_REVISION if preset['id'] == 'local_bge' else 'unversioned'), 160)
    if not re.fullmatch(r'[A-Za-z0-9._:/@+-]+', revision) or preset['id'] == 'local_bge' and revision != LOCAL_REVISION:
        raise TransportError('invalid_embedding_config', '模型版本标识不正确。')
    dimension = value.get('dimension', 512 if preset['id'] == 'local_bge' else None)
    if dimension is not None and (type(dimension) is not int or not 1 <= dimension <= 8192):
        raise TransportError('invalid_embedding_dimension', '向量维数必须是有效整数。')
    if preset['id'] == 'local_bge' and dimension != 512:
        raise TransportError('invalid_embedding_dimension', '当前本机中文模型输出固定为 512 维。')
    limit = value.get('input_limit', 512)
    if type(limit) is not int or not 32 <= limit <= 512 or value.get('normalization', 'l2') != 'l2':
        raise TransportError('invalid_embedding_config', '向量输入上限或归一化方式不正确。')
    result = {**preset, 'provider': preset['id'], 'base_url': base, 'api_key': key, 'model': model,
              'model_revision': revision, 'revision': revision, 'dimension': dimension, 'normalization': 'l2',
              'document_prefix': _string(value.get('document_prefix', ''), 256),
              'query_prefix': _string(value.get('query_prefix', ''), 256),
              'input_version': INPUT_VERSION, 'input_limit': limit,
              'input_limit_unit': 'tokens' if preset['id'] == 'local_bge' else 'unicode_chars_conservative',
              'provider_token_limit_verified': preset['id'] == 'local_bge'}
    return {**result, **{key: value[key] for key in ('profile_id', '_data_dir') if key in value}}


def profile(config):
    config = checked_embedding_config(config)
    identity = {key: config[key] for key in ('provider', 'base_url', 'model', 'model_revision', 'dimension',
                 'normalization', 'document_prefix', 'query_prefix', 'input_version', 'input_limit', 'input_limit_unit')}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return {**identity, 'revision': identity['model_revision'], 'fingerprint': hashlib.sha256(encoded).hexdigest()}
