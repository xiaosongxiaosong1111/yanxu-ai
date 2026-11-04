"""Strict argument contracts for the local knowledge reader and maintenance API."""
import re


READ_ACTIONS = {
    '/api/knowledge/segments': ('segments', ('entry_id', 'q', 'unit_id', 'year', 'page', 'page_size', 'quality')),
    '/api/knowledge/segment': ('segment', ('segment_id', 'before', 'after', 'max_chars')),
    '/api/knowledge/jobs': ('jobs', ('entry_id',)),
}
WRITE_ACTIONS = {
    '/api/knowledge/state': 'state',
    '/api/knowledge/select-version': 'select-version',
    '/api/knowledge/reindex': 'reindex',
}


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required) <= set(value):
        raise ValueError('请求字段缺失或包含不支持的字段。')
    return value


def _identifier(value, prefix, digits):
    if not isinstance(value, str) or re.fullmatch(prefix + r'_[0-9a-f]{' + str(digits) + '}', value) is None:
        raise ValueError('知识资料或片段编号不正确。')
    return value


def _string(value, maximum, empty=True):
    # Reuse the store's secret/control-character boundary without constructing a store.
    from knowledge_store import KnowledgeError, _text
    try:
        return _text(value, maximum, empty)
    except KnowledgeError as error:
        raise ValueError(error.message) from None


def _number(value, lower, upper):
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,6}', value):
        value = int(value)
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError('年度、分页或补读范围格式不正确。')
    return value


def _search(payload):
    values = _object(payload, ('entry_id', 'q', 'unit_id', 'year', 'page', 'page_size', 'quality'))
    entry = _identifier(values['entry_id'], 'k', 24) if 'entry_id' in values else None
    year = values.get('year')
    if year == '':
        year = None
    if year is not None:
        year = _number(year, 1900, 2100)
    quality = values.get('quality', 'accepted')
    if quality not in ('accepted', 'pending', 'excluded', 'all'):
        raise ValueError('知识质量筛选参数不正确。')
    return {'entry_id': entry, 'query': _string(values.get('q', ''), 240),
            'unit_id': _string(values.get('unit_id', ''), 100), 'year': year,
            'page': _number(values.get('page', 1), 1, 100000),
            'page_size': _number(values.get('page_size', 8), 1, 50), 'quality': quality}


def _read(payload):
    values = _object(payload, ('segment_id', 'before', 'after', 'max_chars'), ('segment_id',))
    return {'segment_id': _identifier(values['segment_id'], 'sg', 32),
            'before': _number(values.get('before', 0), 0, 3),
            'after': _number(values.get('after', 0), 0, 3),
            'max_chars': _number(values.get('max_chars', 6000), 1, 12000), 'trusted_only': True}


def _entry(payload):
    values = _object(payload, ('entry_id',), ('entry_id',))
    return {'entry_id': _identifier(values['entry_id'], 'k', 24)}


def _choice(payload):
    values = _object(payload, ('entry_id', 'reason'), ('entry_id', 'reason'))
    return {'entry_id': _identifier(values['entry_id'], 'k', 24),
            'reason': _string(values['reason'], 500, False)}


def _state(payload):
    values = _object(payload, ('entry_id', 'validity', 'reason', 'replacement_id'), ('entry_id', 'validity', 'reason'))
    validity = values['validity']
    if validity not in ('active', 'revoked', 'conflict', 'superseded'):
        raise ValueError('资料有效状态不正确。')
    result = {'entry_id': _identifier(values['entry_id'], 'k', 24), 'validity': validity,
              'reason': _string(values['reason'], 500, False)}
    if validity == 'superseded':
        result['replacement_id'] = _identifier(values.get('replacement_id'), 'k', 24)
    elif 'replacement_id' in values:
        raise ValueError('仅替代操作可以指定替代资料。')
    return result


def _retrieve(payload):
    values = _object(payload, ('q', 'unit_id', 'year', 'entry_id', 'limit'), ('q',))
    query = _string(values.get('q'), 240, False)
    year = values.get('year')
    if year == '':
        year = None
    if year is not None:
        year = _number(year, 1900, 2100)
    entry = _identifier(values['entry_id'], 'k', 24) if 'entry_id' in values else None
    return {'q': query, 'unit_id': _string(values.get('unit_id', ''), 100),
            'year': year, 'entry_id': entry, 'limit': _number(values.get('limit', 8), 1, 20)}


def _empty(payload):
    _object(payload, ())
    return {}


_ACTIONS = {
    'segments': ('search_segments', _search), 'segment': ('read_segment', _read),
    'jobs': ('jobs', _entry), 'state': ('change_validity', _state),
    'select-version': ('choose_version', _choice), 'reindex': ('rebuild_segments', _entry),
}


def checked_request(action, payload, error_type):
    try:
        if not isinstance(action, str) or action not in _ACTIONS:
            raise ValueError('知识操作不受支持。')
        method, check = _ACTIONS[action]
        return method, check(payload)
    except ValueError as error:
        raise error_type('invalid_query', str(error)) from None


def checked_retrieve(payload, error_type):
    try:
        return _retrieve(payload)
    except ValueError as error:
        raise error_type('invalid_query', str(error)) from None


def checked_empty(payload, error_type):
    try:
        return _empty(payload)
    except ValueError as error:
        raise error_type('invalid_query', str(error)) from None
