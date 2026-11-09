"""Bound exact original blocks sent to a model, without disguising a preview as a read."""
import json


_BLOCK = ('id', 'segment_id', 'entry_id', 'source_id', 'extraction_id', 'generation_id',
          'kind', 'role', 'char_start', 'char_end', 'text', 'page_start', 'page_end', 'row_complete',
          'continues_previous', 'continues_next', 'content_truncated', 'original_url')
_SOURCE = ('id', 'knowledge_id', 'unit_id', 'unit_name', 'year', 'title', 'url', 'sha256',
           'status', 'usable', 'original_available', 'validity')


def _metadata(value, keys):
    result = {}
    for key in keys:
        child = value.get(key)
        if key not in value or not isinstance(child, (str, int, float, bool, type(None))):
            continue
        maximum = 2048 if key in {'url', 'original_url'} else 400 if key == 'title' else 120
        if isinstance(child, str) and len(child) > maximum:
            continue
        result[key] = child
    return result


def _block(value):
    result = _metadata(value, [key for key in _BLOCK if key != 'text'])
    if isinstance(value.get('text'), str):
        result['text'] = value['text']
    source = value.get('source')
    if isinstance(source, dict):
        result['source'] = _metadata(source, _SOURCE)
        quality = source.get('quality') or {}
        result['source']['quality'] = _metadata(quality, ('state', 'reusable'))
        extraction = source.get('extraction') or {}
        result['source']['extraction'] = _metadata(extraction, ('id', 'generation_id', 'extraction_status'))
    return result


def project_segment_read(value, limit=6000):
    """Keep whole visible blocks; omitted bodies cannot create a read receipt."""
    selected = _block(value['segment'])
    result = {'segment': selected, 'contexts': [], 'neighbors': [],
              'truncated': bool(value.get('truncated')), 'needs_narrower_read': False}
    fits = lambda item: len(json.dumps(item, ensure_ascii=False, allow_nan=False)) <= limit - 240
    if not fits(result):
        # Link/title metadata already exists in the source ledger. It must not
        # crowd an otherwise complete row out of the model's bounded read.
        result = {**result, 'segment': {key: child for key, child in selected.items()
                                       if key not in {'source', 'original_url'}}}
    if not fits(result):
        result = {**result, 'segment': {key: child for key, child in result['segment'].items() if key != 'text'},
                  'truncated': True, 'needs_narrower_read': True}
        result['segment'] = {**result['segment'], 'content_truncated': True, 'row_complete': False}
    for group in ('contexts', 'neighbors'):
        for original in value.get(group, [])[:12]:
            if not isinstance(original, dict):
                continue
            candidate = {**result, group: [*result[group], _block(original)]}
            if fits(candidate):
                result = candidate
            else:
                result = {**result, 'truncated': True, 'needs_narrower_read': True}
    if result['needs_narrower_read']:
        result['notice'] = '部分完整块超出本次模型阅读预算，未传送正文。请缩小读取范围或重新定位完整目录行；缺失正文不能作准入依据。'
    result['returned_chars'] = sum(len(item.get('text', '')) for item in
                                   [result['segment'], *result['contexts'], *result['neighbors']])
    return result
