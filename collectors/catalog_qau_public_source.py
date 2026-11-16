"""Scratch QAU public attachment-button evidence, with no temporary URL tokens in receipts."""
import hashlib
import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from public_sources import official_source_url
from collectors.catalog_publication import _PublicationHTML, _read
from collectors.catalogs import compact
from collectors.embedded_documents import _PublishedHTML


UNIT_ID = '368296'
HOST = 'yjszsw.qau.edu.cn'
YEAR = 2027
BUTTON_INDEX = 1
BUTTON_COUNT = 5
_BUTTON_PREFIX = 'attach'
_CALLBACK_PATH = '/getAttaDownloadurl'
_DOWNLOAD_PATH = '/attachdownload'
_RESOURCE = re.compile(r'/userfiles/image/test2026/2026/[0-9]{2}/[0-9]{10,24}\.pdf')
_HEX64 = re.compile('[0-9a-f]{64}')
# Exact bytes independently captured from this fixed 2027 school publication.
_ARCHIVE_SHA256 = {
    'article': '93b03c18696855af6844b0b0c2be9ef92aba0448ce4bf4b65ef09cfc09306091',
    'script': '73bd2e8645a7180438b8bbe87bfa990758f9bd7ab5d8d97f097b96cc8964726e',
    'receipt': '03c98a46362267eb6fcec16b413325646ad1d3c2aa7b8b5bddf8fd16e62b8803',
    'pdf': '70df9dc7609ba5dec26177bd3d5afab7c3a72a4ccf27085b01e37595705ae97e',
}
_KEYS = {'schema','article_id','article_sha256','script_sha256','callback_path','callback_sha256',
         'button_index','button_label','download_path','resource_path','pdf_sha256'}


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _article_binding(url, html, js, unit):
    parts = urlsplit(url)
    match = re.fullmatch(r'/content/tzgg/([0-9a-f]{32})', parts.path)
    if (parts.scheme != 'https' or parts.netloc != HOST or parts.query or parts.fragment or not match
            or str(unit.get('id')) != UNIT_ID or not official_source_url(url, unit)):
        raise ValueError('文章入口不属于青岛农业大学官方通知')
    article_id = match[1]
    parsed = _PublicationHTML(html)
    parsed.feed(html)
    parsed.close()
    expected_heading = f'{YEAR}年{unit["name"]}硕士研究生招生章程、专业目录和参考书目'
    label = f'2-{unit["name"]}{YEAR}年硕士研究生招生专业目录.pdf'
    if expected_heading not in {compact(heading) for heading in parsed.headings}:
        raise ValueError('发布页自身标题未确定招生单位、年度和目录范围')
    nodes = _PublishedHTML(html)
    nodes.feed(html)
    nodes.close()
    buttons = [node for node in nodes.nodes if node['attrs'].get('id') == _BUTTON_PREFIX + str(BUTTON_INDEX)]
    if len(buttons) != 1 or buttons[0]['hidden'] or buttons[0]['tag'] != 'a':
        raise ValueError('目录附件按钮缺失、重复或隐藏')
    visible = [link for link in parsed.publication_links if link['start'] == buttons[0]['start']
               and link['label'] == label and link['href'] == 'javascript:void(0);']
    if len(visible) != 1 or nodes.base:
        raise ValueError('公开目录按钮没有绑定准确附件标签')
    links = [urljoin(url, node['attrs']['src']) for node in nodes.nodes if node['tag'] == 'script'
             and not node['hidden'] and node['attrs'].get('src', '').endswith('/front.js')]
    if len(links) != 1:
        raise ValueError('公开附件处理脚本引用不唯一')
    script_url = urlsplit(links[0])
    if (script_url.scheme, script_url.netloc) != (parts.scheme, parts.netloc) or script_url.query or script_url.fragment:
        raise ValueError('公开附件处理脚本不是相同官方来源')
    call = f"""Cms.attachment('','news','{article_id}',"{BUTTON_COUNT}",'{_BUTTON_PREFIX}');"""
    inline = [compact(html[start:end]) for start,end,context in parsed.script_ranges if context['visible']]
    if sum(src.count(call) for src in inline) != 1:
        raise ValueError('未能从原件重放公开附件按钮的文章 ID、模型和索引')
    script = compact(js)
    declaration = 'Cms.attachment=function(base,model,contentId,n,prefix){'
    route = '$.get(base+"/getAttaDownloadurl",{"contentid":contentId,"modelname":model,"n":n},'
    setter = '$("#"+prefix+i).attr("href",base+data[i]);'
    if script.count(declaration) != 1 or not re.search(
            re.escape(declaration) + re.escape(route) + r'.{0,400}?' + re.escape(setter), script):
        raise ValueError('未能从官方脚本重放附件查询与同序号按钮绑定')
    return article_id, label, links[0]


def _expected_callback(url, article_id):
    parsed = urlsplit(url)
    return ((parsed.scheme, parsed.netloc, parsed.path, parsed.fragment) == ('https', HOST, _CALLBACK_PATH, '')
            and parse_qs(parsed.query, strict_parsing=True) == {
                'contentid':[article_id], 'modelname':['news'], 'n':[str(BUTTON_COUNT)]})


def build_capture_receipt(manifest, raw_callback, pdf, article, script, unit):
    """Inspect the exact private capture once, retaining only its hash and stable fields."""
    article_url = manifest['parent']
    article_id, label, _ = _article_binding(article_url, article, script, unit)
    callback, original = manifest['callback'], manifest['original']
    if (not _expected_callback(callback['url'], article_id) or callback['final_url'] != callback['url']
            or callback.get('sha256') != _sha(raw_callback)):
        raise ValueError('公开附件查询路径或返回字节摘要不符')
    rows = json.loads(raw_callback)
    if not isinstance(rows, list) or len(rows) != BUTTON_COUNT or not all(isinstance(row,str) for row in rows):
        raise ValueError('公开附件查询的索引不完整')
    selected = urljoin(article_url, rows[BUTTON_INDEX])
    if selected != manifest['exact_button_published_url'] or selected != original['url'] or selected != original['final_url']:
        raise ValueError('附件按钮与实际 PDF 响应未绑定同一路径')
    path = urlsplit(selected)
    if (path.scheme, path.netloc, path.path, path.fragment) != ('https', HOST, _DOWNLOAD_PATH, ''):
        raise ValueError('公开附件目标域名或下载处理器不符')
    params = parse_qs(path.query, strict_parsing=True)
    if (set(params) != {'url','contentid','t','passcode'} or any(len(value)!=1 for value in params.values())
            or params['contentid'] != [article_id] or not _RESOURCE.fullmatch(params['url'][0])):
        raise ValueError('附件公开下载参数未指向本文章的 PDF 资源')
    if original.get('type') != 'PDF' or not pdf.startswith(b'%PDF-') or original.get('sha256') != _sha(pdf):
        raise ValueError('目录下载内容不是已核对的 PDF 字节')
    return {'schema':'qau_public_button_capture_v1','article_id':article_id,'article_sha256':_sha(article.encode()),
            'script_sha256':_sha(script.encode()),'callback_path':_CALLBACK_PATH,'callback_sha256':_sha(raw_callback),
            'button_index':BUTTON_INDEX,'button_label':label,'download_path':_DOWNLOAD_PATH,
            'resource_path':params['url'][0],'pdf_sha256':_sha(pdf)}


def verify_qau_public_source(document, unit, root):
    """Replay local integrity and public UI mapping against a previously verified capture receipt."""
    try:
        context = document['qau_public_download']
        article, script, capture = (context[key] for key in ('article','script','receipt'))
        if any(record.get('year') != YEAR or record.get('unit_id') != UNIT_ID
               for record in (document,article,script,capture)):
            raise ValueError('来源记录与原年度招生单位不符')
        if document['url'] != article['url'] or capture['url'] != article['url']:
            raise ValueError('候选的公开原文地址必须为稳定的学校发布页')
        declared = {key: record.get('sha256') for key, record in
                    (('article',article),('script',script),('receipt',capture),('pdf',document))}
        if declared != _ARCHIVE_SHA256:
            raise ValueError('目录发布页、脚本、脱敏记录或PDF不属于固定2027官方原件包')
        article_raw,_ = _read(article,root,2*1024*1024)
        script_raw,_ = _read(script,root,1024*1024)
        capture_raw,_ = _read(capture,root,64*1024)
        pdf_raw,_ = _read(document,root,32*1024*1024)
        if not all((article_raw,script_raw,capture_raw,pdf_raw)) or not pdf_raw.startswith(b'%PDF-'):
            raise ValueError('原发布页、按钮脚本、下载记录或目录 PDF 字节已变化')
        article_id, label, script_url = _article_binding(
            article['url'],article_raw.decode('utf-8'),script_raw.decode('utf-8'),unit)
        if script_url != script['url']:
            raise ValueError('保存的脚本不是发布页实际引用脚本')
        receipt = json.loads(capture_raw)
        if (not isinstance(receipt,dict) or set(receipt)!=_KEYS
                or receipt['schema']!='qau_public_button_capture_v1'
                or receipt['article_id']!=article_id or receipt['button_index']!=BUTTON_INDEX
                or receipt['button_label']!=label or receipt['callback_path']!=_CALLBACK_PATH
                or receipt['download_path']!=_DOWNLOAD_PATH
                or receipt['article_sha256']!=article['sha256'] or receipt['script_sha256']!=script['sha256']
                or receipt['pdf_sha256']!=document['sha256']
                or not _HEX64.fullmatch(receipt['callback_sha256']) or not _RESOURCE.fullmatch(receipt['resource_path'])):
            raise ValueError('脱敏下载记录与发布页、按钮索引或 PDF 摘要不符')
        return {'verified':True,'source_url':article['url'],'pdf_sha256':document['sha256'],
                'button_label':label,'capture_sha256':capture['sha256']}
    except (AttributeError,KeyError,TypeError,ValueError,UnicodeError,RecursionError) as exc:
        return {'verified':False,'reason':str(exc)}
