"""Replay the exact official YCIT PDF viewer reference without running script."""
import re
from urllib.parse import urljoin


SOURCE_SHA = '57ca99aa3c02b7d22c3da2881701fdcef40a592609198cb05f97f402ad6a1e63'
PARENT_SHA = '1ecdad0c493513104d7ccdf48ea720aa976ce716257512862a6b3b572b7d90d1'
PARENT_URL = 'https://yjsc.ycit.edu.cn/info/1157/11652.htm'
RECIPE = 'vsb_literal_ycit_pdf'
_HREF = r'/virtual_attach_file\.vsb\?afc=[A-Za-z0-9_/-]{40,512}&oid=1790821392&tid=1157&nid=11652&e=\.pdf'
_SCRIPT = re.compile(
    r'<script>\s*var\s+vsb_pdf_image_data\s*=\s*\[\s*\]\s*;\s*try\s*\{\s*'
    r'showVsbpdfIframe\("(?P<href>' + _HREF + r')","100%","600","0",'
    r'"border:1px solid #DDDDDD",vsb_pdf_image_data\);\s*\}\s*catch\s*\(e\)\s*\{\s*\}\s*'
)


def exact_ycit_viewer_links(html, parser, parent, document, raw):
    if (parent.get('url') != PARENT_URL or parent.get('sha256') != PARENT_SHA or parser.base_href is not None
            or document.get('sha256') != SOURCE_SHA or not raw.startswith(b'%PDF-')):
        return []
    links = []
    for start,end,context in parser.script_ranges:
        if not context.get('visible') or parser.main and not context.get('main') or not 0 < end-start < 1024:
            continue
        snippet = html[start:end]
        if not (match := _SCRIPT.fullmatch(snippet)):
            continue
        href = match['href']
        url = urljoin(PARENT_URL,href)
        if url == document.get('url'):
            links.append({'url':url,'href':href,'recipe':RECIPE,'visible_label':False,'label':'',
                          'evidence':[{'source_url':PARENT_URL,'start':start,'end':end,'snippet':snippet}]})
    return links if len(links)==1 else []
