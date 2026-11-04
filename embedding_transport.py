"""Explicit, bounded local/remote embedding calls; importing does not load a model.

Remote responses are validated fail-closed: explicit indices, exact count, finite
non-zero floats, stable dimension, and the model identity echoed by the service. A
successful chat call never stands in for an embedding result.
"""
import math
import re

from embedding_catalog import checked_embedding_config, profile, LOCAL_MODEL
from embedding_inputs import prepare_inputs as _prepare_inputs
from provider_catalog import TransportError, MODEL_ID_PATTERN

_MAX_BATCH = 32
_MAX_DIMENSION = 8192
_KNOWN_INPUT_TOKENS = ('input_tokens', 'prompt_tokens')


class _HTTP:
    def __init__(self, request=None):
        self._request = request


def _normalize(vector):
    peak = max(abs(value) for value in vector)
    scaled = [value / peak for value in vector]
    norm = math.sqrt(sum(value * value for value in scaled))
    return [value / norm for value in scaled]


def _clean_usage(value):
    if not isinstance(value, dict):
        return {}, False
    clean = {key: item for key, item in value.items()
             if isinstance(key, str) and type(item) is int and 0 <= item < 10 ** 9}
    incoming = next((clean[key] for key in _KNOWN_INPUT_TOKENS if key in clean), None)
    if incoming is None:
        return {}, False
    outgoing = next((clean[key] for key in ('output_tokens', 'completion_tokens') if key in clean), 0)
    return {'input_tokens': incoming, 'output_tokens': outgoing,
            'total_tokens': clean.get('total_tokens', incoming + outgoing)}, True


class EmbeddingTransport:
    profile = staticmethod(profile)

    def __init__(self, data_dir, transport=None):
        self.data_dir = data_dir
        self.http = _HTTP(transport)
        self._ai = None
        self._observed = {}

    def prepare_inputs(self, segment, contexts, config):
        return _prepare_inputs(segment, contexts, {**config, '_data_dir': str(self.data_dir)})

    def models(self, config):
        config = checked_embedding_config(config)
        if config['provider'] == 'local_bge':
            return {'models': [{'id': LOCAL_MODEL, 'label': '本机中文模型 · BGE'}],
                    'discovery': 'local_fixed', 'authentication_verified': True,
                    'notice': '本机模型使用固定公开文件，无需选择其他模型。'}
        if config.get('models_path') is None:
            # Same no-catalog contract as AI models(): no wire call, no fabricated
            # authentication; the operator fills in the served model ID manually.
            return {'models': [], 'discovery': 'manual', 'authentication_verified': False,
                    'notice': '该服务不提供公开模型目录，请手动填写已开通的向量模型 ID；向量能力与维度以实际向量测试为准。'}
        value = self._request(config, 'GET', config['models_path'], None, None)
        if not isinstance(value, dict):
            raise TransportError('invalid_response', '模型列表格式无法识别。', 502)
        if config['provider'] == 'ollama':
            rows = value.get('models', [])
            if not isinstance(rows, list):
                raise TransportError('invalid_response', '模型列表格式无法识别。', 502)
            models = []
            for row in rows[:1000]:
                if not isinstance(row, dict):
                    continue
                for identifier in (row.get('model'), row.get('name')):
                    if isinstance(identifier, str) and len(identifier) <= 200 \
                            and re.fullmatch(MODEL_ID_PATTERN, identifier):
                        models.append({'id': identifier, 'label': identifier})
            return {'models': list({row['id']: row for row in models}.values()), 'discovery': 'remote',
                    'authentication_verified': False,
                    'notice': '目录仅为候选；向量能力与维度以实际向量测试为准。'}
        rows = value.get('data', [])
        if not isinstance(rows, list):
            raise TransportError('invalid_response', '模型列表格式无法识别。', 502)
        models = []
        for row in rows[:1000]:
            identifier = row.get('id') if isinstance(row, dict) else None
            if isinstance(identifier, str) and len(identifier) <= 200 and re.fullmatch(MODEL_ID_PATTERN, identifier):
                models.append({'id': identifier, 'label': str(row.get('label') or identifier)[:200]})
        return {'models': models, 'discovery': 'remote', 'authentication_verified': False,
                'notice': '目录仅为候选；向量能力与维度以实际向量测试为准。'}

    def embed(self, config, texts, purpose='document', stop_event=None):
        config = checked_embedding_config(config)
        if purpose not in ('document', 'query'):
            raise TransportError('invalid_embedding_input', '向量输入用途不正确。')
        if stop_event is not None and stop_event.is_set():
            raise TransportError('cancelled', '向量任务已取消。', 409)
        inputs = self._bounded_inputs(config, texts, purpose)
        fingerprint = profile(config)['fingerprint']
        if config['provider'] == 'local_bge':
            result = self._embed_local(config, inputs, fingerprint, stop_event)
        else:
            payload, raw_model, usage = self._embed_remote(config, inputs, stop_event)
            result = self._finish(config, payload, raw_model, len(inputs), usage, fingerprint)
        if stop_event is not None and stop_event.is_set():
            raise TransportError('cancelled', '向量任务已取消，已取消的向量结果不会发布。', 409)
        return result

    def _bounded_inputs(self, config, texts, purpose):
        if not isinstance(texts, list) or not 1 <= len(texts) <= _MAX_BATCH:
            raise TransportError('invalid_embedding_input', '向量输入数量必须在 1 到 32 条之间。')
        prefix = config['query_prefix'] if purpose == 'query' else config['document_prefix']
        limit = config['input_limit']
        local = config['input_limit_unit'] == 'tokens'
        inputs = []
        for text in texts:
            if not isinstance(text, str) or not text or len(text) > 100000:
                raise TransportError('invalid_embedding_input', '向量输入必须是有长度的文本。')
            if any(ord(char) < 32 and char not in '\n\t\r' or ord(char) == 127 for char in text):
                raise TransportError('invalid_embedding_input', '向量输入不能包含控制字符。')
            try:
                text.encode('utf-8')
            except UnicodeError:
                raise TransportError('invalid_embedding_input', '向量输入包含不完整的字符。') from None
            model_input = prefix + text
            if local:
                from embedding_local import token_length
                if token_length(self.data_dir, model_input) > limit:
                    raise TransportError('embedding_input_too_long',
                                         '单条向量输入超出模型 token 限额，请先精确分窗。')
            elif len(model_input) > limit:
                raise TransportError('embedding_input_too_long',
                                     '单条向量输入超出保守字符限额，请先分窗。')
            inputs.append(model_input)
        return inputs

    def _embed_local(self, config, inputs, fingerprint, stop_event):
        from embedding_local import embed as local_embed, token_length
        vectors = local_embed(inputs, self.data_dir, stop_event)
        tokens = sum(token_length(self.data_dir, item) for item in inputs)
        return self._finish(config, list(enumerate(vectors)), LOCAL_MODEL, len(inputs),
                            ({'input_tokens': tokens, 'output_tokens': 0, 'total_tokens': tokens}, True),
                            fingerprint)

    def _embed_remote(self, config, inputs, stop_event):
        if not config['model']:
            raise TransportError('missing_model', '请选择或填写向量模型 ID。')
        body = {'model': config['model'], 'input': inputs, 'encoding_format': 'float'}
        value = self._request(config, 'POST', '/embeddings', body, stop_event)
        rows = value.get('data') if isinstance(value, dict) else None
        if not isinstance(rows, list) or len(rows) != len(inputs):
            raise TransportError('invalid_model_response', '向量服务返回数量与请求不一致。', 502)
        raw_model = value.get('model')
        if not isinstance(raw_model, str) or raw_model != config['model']:
            raise TransportError('embedding_model_mismatch', '向量服务返回的模型身份与配置不一致。', 502)
        seen, vectors = set(), []
        for row in rows:
            if not isinstance(row, dict):
                raise TransportError('invalid_model_response', '向量服务返回格式不正确。', 502)
            index = row.get('index')
            if type(index) is not int or not 0 <= index < len(inputs) or index in seen:
                raise TransportError('invalid_model_response', '向量服务返回的序号缺失、越界或重复。', 502)
            seen.add(index)
            vectors.append((index, row.get('embedding')))
        usage, known = _clean_usage(value.get('usage'))
        return vectors, raw_model, (usage, known)

    def _finish(self, config, indexed, raw_model, count, usage, fingerprint):
        vectors = [None] * count
        dimension = None
        for index, vector in indexed:
            if not isinstance(vector, list) or not vector or len(vector) > _MAX_DIMENSION:
                raise TransportError('invalid_model_response', '向量格式或维度不正确。', 502)
            if any(type(value) not in (int, float) or isinstance(value, bool) for value in vector):
                raise TransportError('invalid_model_response', '向量分量必须是数值。', 502)
            # A legal JSON integer may exceed the float range (10**1000); math.isfinite
            # would raise a raw OverflowError, so the bound is enforced explicitly.
            try:
                representable = all(math.isfinite(value) for value in vector)
            except OverflowError:
                raise TransportError('invalid_model_response', '向量分量超出可表示的数值范围。', 502) from None
            if not representable:
                raise TransportError('invalid_model_response', '向量分量必须是有穷数值。', 502)
            if dimension is None:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise TransportError('embedding_dimension_drift', '同一批次内向量维度不一致。', 502)
            if all(value == 0 for value in vector):
                raise TransportError('invalid_model_response', '向量服务返回了无效零向量。', 502)
            vectors[index] = vector
        if dimension is None or any(vector is None for vector in vectors):
            raise TransportError('invalid_model_response', '向量服务返回缺少部分结果。', 502)
        if config['dimension'] is not None and dimension != config['dimension']:
            raise TransportError('embedding_dimension_mismatch', '向量维度与已验证配置不一致，请重新测试或重建索引。', 502)
        known = self._observed.get(fingerprint)
        if known is not None and known != dimension:
            raise TransportError('embedding_dimension_drift', '同一模型配置返回了不同的向量维度。', 502)
        self._observed[fingerprint] = dimension
        usage, usage_known = usage
        return {'vectors': [_normalize(vector) for vector in vectors], 'dimension': dimension,
                'model': raw_model, 'model_revision': config['model_revision'],
                'usage': usage, 'usage_known': usage_known, 'input_count': count}

    def _request(self, config, method, path, body, stop_event):
        injected = self.http._request
        if injected is not None:
            try:
                return injected(config, method, path, body)
            except TransportError:
                raise
            except Exception:
                raise TransportError('provider_unavailable', '向量服务暂时不可用，请检查网络后重试。', 503) from None
        if self._ai is None:
            from ai_transport import AITransport
            self._ai = AITransport()
        return self._ai.request_json(config, method, path, body, stop_event=stop_event)
