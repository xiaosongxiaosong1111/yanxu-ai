"""Replay the narrow first-public-item CHSI route from immutable HTTP originals."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from .catalog_chsi import _context, parse_chsi_detail_html
from .documents import PROJECT_ROOT, decode_text


def _address(document, path, method):
    address = urlsplit(document.get('url', ''))
    if (address.scheme != 'https' or address.hostname != 'yz.chsi.com.cn' or address.path != path
            or address.username or address.password or address.port not in {None, 443} or address.fragment
            or document.get('method') != method or document.get('requested_url', document.get('url')) != document.get('url')):
        raise ValueError('原始请求地址、方法或实际响应地址不匹配')
    return parse_qs(address.query, keep_blank_values=True)


def _read(document, root):
    if (not isinstance(document, dict) or not isinstance(document.get('id'), str)
            or not document['id'] or document.get('status') not in {200, 'parsed', 'verified'}
            or not isinstance(document.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', document['sha256'])
            or not isinstance(document.get('local_path'), str)):
        raise ValueError('原始HTTP来源元数据不完整')
    path = Path(document['local_path'])
    if not path.is_absolute():
        path = (root.parent if path.parts and path.parts[0] == root.name else root) / path
    path = path.resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('原始来源路径越界、缺失或过大')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != document['sha256']:
        raise ValueError('原始来源SHA不一致')
    return decode_text(raw, document.get('content_type', ''))


def _response(document, path, root, unit_code=None, program_code=None):
    if _address(document, path, 'POST'):
        raise ValueError('原始JSON请求不能伪装为GET或附加查询地址')
    body = document.get('request_body')
    if not isinstance(body, str): raise ValueError('未保留真实POST参数')
    request = parse_qs(body, keep_blank_values=True)
    if request.get('start') != ['0'] or request.get('curPage', ['1']) != ['1']:
        raise ValueError('仅接受正常公开首屏响应')
    if unit_code is not None and request.get('dwdm') != [unit_code]:
        raise ValueError('原始请求招生单位不符')
    if program_code is not None and request.get('zydm') != [program_code]:
        raise ValueError('原始请求专业不符')
    payload = json.loads(_read(document, root))
    if (not isinstance(payload, dict) or payload.get('flag') is not True or not isinstance(payload.get('msg'), dict)
            or payload['msg'].get('curPage') != 1 or not isinstance(payload['msg'].get('list'), list)
            or not payload['msg']['list']):
        raise ValueError('原始首屏响应不成功或没有公开条目')
    if unit_code is not None and str(payload.get('zsmlcxModel', {}).get('dwdm')) != unit_code:
        raise ValueError('原始响应查询单位不符')
    if program_code is not None and payload.get('zsmlcxModel', {}).get('zydm') != program_code:
        raise ValueError('原始响应查询专业不符')
    return payload['msg']['list']


def _identity(row, unit):
    return isinstance(row, dict) and all(str(row.get(field)) == str(unit.get(key))
        for field, key in [('schId', 'id'), ('dwdm', 'unit_code'), ('dwmc', 'name')])


def _public_template(html, sources, root):
    if re.findall(r'(?m)^\s*login\s*:\s*([^,\r\n]+)', html) != ['!!null']:
        raise ValueError('原始学校页不是已确认的未登录公开状态')
    expressions = [r'v-for=["\']item in list["\']', r'@click=["\']getListDetail\(item\)["\']',
        r':data=["\']item.data["\']', r'v-for=["\']ite,ind in params.row.kskmz["\']',
        r':href="\'/zsml/yjfxdetail\?id=\'\s*\+\s*params.row.id"',
        r'!login\s*&&\s*item.totalCount\s*>\s*item.pageSize', r'!login\s*&&\s*form.totalPage\s*>\s*1']
    if not all(re.search(pattern, html) for pattern in expressions):
        raise ValueError('原始页面不是已核验的公开首项模板')
    if not all('ite.km' + str(slot) + 'Vo.kskmdm' in html for slot in range(1, 5)):
        raise ValueError('页面没有真实绑定原始科目单元')
    linked = set(re.findall(r'<link\b[^>]*rel=["\']stylesheet["\'][^>]*href=["\']([^"\']+)', html, re.I))
    styles = []
    for source in sources:
        address = urlsplit(source.get('url', ''))
        if (source.get('url') not in linked or source.get('method') != 'GET' or address.scheme != 'https'
                or address.hostname != 't2.chei.com.cn' or address.port not in {None, 443}
                or address.username or address.password or address.query or address.fragment
                or not re.fullmatch(r'/yz/zsml/assets/pc/css/app-[\d.]+\.css', address.path)):
            raise ValueError('公开模板样式原文不匹配')
        styles.append(_read(source, root))
    style = re.sub(r'\s+', '', ''.join(styles))
    # This version covers only the known bottom-mask public first item, never additional rows.
    required = {'.need-login': ('height:80px', 'position:absolute', 'bottom:20px'),
                '.need-login-small': ('height:46px', 'bottom:12px')}
    for selector, declarations in required.items():
        blocks = re.findall(re.escape(selector) + r'\{([^{}]*)\}', style)
        properties = {value.split(':', 1)[0] for value in declarations}
        blocks = [block for block in blocks if any(value.split(':', 1)[0] in properties for value in block.split(';'))]
        if len(blocks) != 1 or not all(value in blocks[0].split(';') for value in declarations):
            raise ValueError('公开首项遮罩范围未核验，不能采纳预返回数据')


def _source_set(document, unit, year):
    if type(year) is not int or document.get('unit_id') != unit.get('id') or document.get('year') != year:
        raise ValueError('详情学校或年度不符')
    proof = document.get('public_gate_proof')
    if (not isinstance(proof, dict) or type(proof.get('version')) is not int or proof['version'] != 1 or proof.get('policy') != 'first_public_item_only'
            or proof.get('unit_id') != unit.get('id') or proof.get('year') != year
            or any(type(proof.get(key)) is not int or proof[key] != 0 for key in ('program_index', 'direction_index'))
            or type(proof.get('unit_index')) is not int or proof['unit_index'] != 0):
        raise ValueError('缺少可重放的正常公开首方向证明')
    originals = proof.get('originals')
    if not isinstance(originals, list) or not 5 <= len(originals) <= 12:
        raise ValueError('原始请求来源集合缺失或异常')
    sources = {item.get('id'): item for item in originals if isinstance(item, dict)}
    if len(sources) != len(originals): raise ValueError('原始请求身份重复')
    context = document.get('context_document')
    if not isinstance(context, dict) or context != sources.get(proof.get('context_document_id')):
        raise ValueError('详情没有绑定相同SHA的真实学校上下文')
    return proof, sources, context


def _school_context(context, sources, proof, unit, year, root):
    html = _read(context, root)
    _context(html, unit, year)
    query = _address(context, '/zsml/dwzy.do', 'GET')
    if any(query.get(field) != [str(unit[key])] for field, key in [('schId', 'id'), ('dwdm', 'unit_code'), ('dwmc', 'name')]):
        raise ValueError('学校上下文请求归属不符')
    unit_rows = _response(sources[proof['units_document_id']], '/zsml/rs/dws.do', root)
    if proof['unit_index'] >= len(unit_rows): raise ValueError('学校首屏位置越界')
    unit_row = unit_rows[proof['unit_index']]
    if not _identity(unit_row, unit) or query.get('sign') != [unit_row.get('sign')] or not unit_row.get('sign'):
        raise ValueError('学校链接未由真实首屏单位响应提供')
    return html


def _first_direction(sources, proof, unit, root):
    programs = _response(sources[proof['programs_document_id']], '/zsml/rs/dwzys.do', root, str(unit['unit_code']))
    filters = parse_qs(sources[proof['programs_document_id']].get('request_body', ''), keep_blank_values=True)
    if any(any(value for value in filters.get(key, [])) for key in (
            'zydm', 'zymc', 'xwlx', 'mldm', 'yjxkdm', 'xxfs', 'tydxs', 'jsggjh', 'jsxbjh')):
        raise ValueError('首公开专业证明不得通过额外筛选绕开原始首屏范围')
    if not _identity(programs[0], unit): raise ValueError('首专业学校身份不符')
    directions = _response(sources[proof['directions_document_id']], '/zsml/rs/yjfxs.do', root,
                           str(unit['unit_code']), programs[0].get('zydm'))
    direction = directions[0]
    if (not _identity(direction, unit) or direction.get('zydm') != programs[0].get('zydm')
            or direction.get('zymc') != programs[0].get('zymc') or direction.get('id') != proof.get('direction_id')
            or direction.get('ksfsdm') != '21' or direction.get('ksfsmc') not in {'统考', '全国统考'}):
        raise ValueError('首方向并非该专业正常公开统考条目')
    return direction


def _verified(document, unit, year, root):
    proof, sources, context = _source_set(document, unit, year)
    html = _school_context(context, sources, proof, unit, year, root)
    direction = _first_direction(sources, proof, unit, root)
    detail_query = _address(document, '/zsml/yjfxdetail', 'GET')
    if detail_query != {'id': [direction['id']]}: raise ValueError('详情链接不是实际公开首方向')
    rules = proof.get('rules_document_ids')
    if not isinstance(rules, list) or not rules: raise ValueError('公开模板规则原文缺失')
    _public_template(html, [sources[identifier] for identifier in rules], root)
    parsed = parse_chsi_detail_html(_read(document, root), unit, year, document, context_html=html)
    if len(parsed['programs']) != 1: raise ValueError('详情并非一个统考方向')
    row = parsed['programs'][0]
    fields = {'department_code': direction.get('yxsdm'), 'department': direction.get('yxsmc'),
              'code': direction.get('zydm'), 'name': direction.get('zymc'),
              'direction': str(direction.get('yjfxdm')) + ' ' + str(direction.get('yjfxmc')),
              'study_mode': {'1': '全日制', '2': '非全日制'}.get(direction.get('xxfs')),
              'chsi_direction_id': direction.get('id'), 'exam_combinations': direction.get('kskmz')}
    if any(row.get(key) != value for key, value in fields.items()): raise ValueError('公开首方向与实际详情字段不一致')
    # Re-read every dependency, including unreferenced audit originals: no mtime shortcuts.
    for source in sources.values(): _read(source, root)
    signature = hashlib.sha256(json.dumps({'document': document, 'unit': unit, 'year': year},
        ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    documents = [{**deepcopy(source), 'unit_id': unit['id'], 'year': year, 'kind': 'catalog_context',
                  'http_status': source.get('status'), 'status': 'parsed'} for source in sources.values()]
    return {'verified': True, 'context_html': html, 'documents': documents, 'signature': signature}


def verify_public_detail(document, unit, year, *, project_root=PROJECT_ROOT):
    """Validate saved public originals only; this helper never makes a network request."""
    try:
        return _verified(document, unit, year, Path(project_root).resolve())
    except (ValueError, TypeError, KeyError, AttributeError, OSError, IndexError) as error:
        return {'verified': False, 'reason': str(error), 'documents': []}
