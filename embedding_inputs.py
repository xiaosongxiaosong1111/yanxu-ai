"""Token-bounded embedding windows, separate from exact canonical quotations.

Every window keeps `text` as an exact sub-span of the original segment in absolute
coordinates, and `embedding_text` as the exact string whose sha256 (after the document
prefix) is `input_sha` — the same string the transport sends to the model. The budget
covers the prefix and composed context, so the tail of a long clause (negations,
exceptions, emoji) is never silently truncated.
"""
import hashlib
import re

from embedding_catalog import checked_embedding_config
from embedding_local import token_length
from provider_catalog import TransportError

_SENTENCE = re.compile(r'[。；！？.!?;\n]')
_BAD_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_MAX_CONTEXTS = 8
_MAX_WINDOWS = 64
_LOCAL_CONTEXT_BUDGET = 96
_MIN_TEXT_BUDGET = 8


def _plain(value, limit):
    if not isinstance(value, str) or len(value) > limit or _BAD_CHARS.search(value):
        raise TransportError('invalid_embedding_input', '向量输入片段格式不正确。')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise TransportError('invalid_embedding_input', '向量输入包含不完整的字符。') from None
    return value


def _checked_segment(segment):
    if not isinstance(segment, dict):
        raise TransportError('invalid_embedding_input', '向量片段格式不正确。')
    text = _plain(segment.get('text'), 200000)
    start, end = segment.get('char_start'), segment.get('char_end')
    if type(start) is not int or type(end) is not int or start < 0 or end - start != len(text):
        raise TransportError('invalid_embedding_input', '向量片段坐标与原文不一致。')
    return text, start


def _checked_contexts(contexts):
    if not isinstance(contexts, list) or len(contexts) > _MAX_CONTEXTS:
        raise TransportError('invalid_embedding_input', '上下文数量超出限制。')
    result = []
    for item in contexts:
        if not isinstance(item, dict):
            raise TransportError('invalid_embedding_input', '上下文格式不正确。')
        start, end = item.get('char_start'), item.get('char_end')
        if type(start) is not int or type(end) is not int or start < 0 or start >= end:
            raise TransportError('invalid_embedding_input', '上下文坐标不正确。')
        text = _plain(item.get('text'), 600)
        if not text.strip():
            raise TransportError('invalid_embedding_input', '上下文不能是空白内容。')
        if end - start != len(text):
            raise TransportError('invalid_embedding_input', '上下文坐标与文字不一致。')
        result.append(text)
    return result


def _chunks(text, start):
    pieces, position = [], 0
    for match in _SENTENCE.finditer(text):
        end = match.end()
        pieces.append((start + position, start + end))
        position = end
    if position < len(text):
        pieces.append((start + position, start + len(text)))
    return pieces


def _hard_split(piece, measure, budget):
    left, right = piece
    rows = []
    while left < right:
        if measure(left, right) <= budget:
            rows.append((left, right))
            break
        cut = min(right - 1, left + max(1, int((right - left) * 0.6)))
        while cut > left + 1 and measure(left, cut) > budget:
            cut = left + max(1, (cut - left) // 2)
        if cut <= left or measure(left, left + 1) > budget:
            raise TransportError('embedding_input_too_long', '向量前缀或上下文耗尽了输入限额，无法保留原文。')
        rows.append((left, cut))
        left = cut
    return rows


def prepare_inputs(segment, contexts, config):
    config = checked_embedding_config(config)
    text, base = _checked_segment(segment)
    headings = _checked_contexts(contexts)
    prefix = config['document_prefix']
    limit = config['input_limit']
    local = config['input_limit_unit'] == 'tokens'
    data_dir = config.get('_data_dir')
    if local and not data_dir:
        raise TransportError('invalid_embedding_config', '本机模型需要指定数据目录。')

    def measure(start, end=None):
        if end is None:
            return token_length(data_dir, start) if local else len(start)
        body = text[start-base:end-base]
        return token_length(data_dir, prefix + body) if local else len(prefix) + len(body)

    prefix_cost = measure(prefix)
    # measure() is prefix-inclusive, so the room left for body+context is limit - prefix_cost.
    if limit - prefix_cost < _MIN_TEXT_BUDGET:
        raise TransportError('embedding_input_too_long', '向量前缀过长，无法保留原文片段。')
    context_block, context_cost = '', 0
    if headings:
        ceiling = min(_LOCAL_CONTEXT_BUDGET, (limit - prefix_cost) // 4)
        block = '\n'.join(headings)
        while block and (token_length(data_dir, prefix + block) if local else len(prefix + block)) > prefix_cost + ceiling:
            block = block[:max(0, len(block) // 2)].rstrip()
        if block:
            context_cost = (token_length(data_dir, prefix + block + '\n') if local else len(prefix + block + '\n')) - prefix_cost
            if limit - prefix_cost - context_cost >= _MIN_TEXT_BUDGET:
                context_block = block
            else:
                context_cost = 0
    # Both measures already include the prefix, so compare them against the full limit
    # minus only the context overhead; subtracting prefix_cost here would double-count it.
    # Local tokenization is not perfectly additive at the context/body junction, so keep a
    # two-token margin for it.
    text_budget = limit - context_cost - (2 if local else 0)
    windows, current = [], None
    for piece in _chunks(text, base):
        for left, right in _hard_split(piece, measure, text_budget):
            if current is None:
                current = (left, right)
            elif measure(current[0], right) > text_budget:
                windows.append(current)
                current = (left, right)
            else:
                current = (current[0], right)
    if current is not None:
        windows.append(current)
    if len(windows) > _MAX_WINDOWS:
        raise TransportError('embedding_input_too_long', '片段过长，无法在向量输入限额内完整分窗。')
    result = []
    for left, right in windows:
        window_text = text[left-base:right-base]
        embedding_text = context_block + '\n' + window_text if context_block else window_text
        model_input = prefix + embedding_text
        if (token_length(data_dir, model_input) if local else len(model_input)) > limit:
            raise TransportError('embedding_input_too_long', '向量窗口超出模型输入限额。')
        result.append({'text': window_text, 'char_start': left, 'char_end': right,
                       'embedding_text': embedding_text,
                       'input_sha': hashlib.sha256(model_input.encode('utf-8')).hexdigest(),
                       'window_id': 'w_' + hashlib.sha256(
                           f"{segment.get('id', '')}|{left}|{right}".encode('utf-8')).hexdigest()[:24]})
    return result
