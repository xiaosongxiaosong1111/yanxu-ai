"""Resolve static public PDF.js references for the national crawler."""
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from public_navigation import _origin, _target


_VIEWER_PATH = '/system/resource/pdfjs/viewer.html'
_FILE_QUERY = re.compile(r'file=([^&;#]+)')
_LOCAL_PDF = re.compile(r'/__local/(?:[A-Za-z0-9_-]+/){0,12}[A-Za-z0-9_-]+\.(?i:pdf)')


def expand_pdfjs_references(embedded_rows, page_url):
    """Replace a same-origin VSB PDF.js wrapper with its literal static PDF."""
    page = _target(page_url, page_url)
    if not page:
        return []
    rows = []
    for row in embedded_rows:
        viewer = _target(row['url'], page)
        if not viewer or _origin(viewer) != _origin(page):
            continue
        parts = urlsplit(viewer)
        if row['recipe'] != 'iframe' or parts.path != _VIEWER_PATH:
            rows.append(dict(row))
            continue
        query = _FILE_QUERY.fullmatch(parts.query)
        file_path = unquote(query[1]) if query else ''
        if _LOCAL_PDF.fullmatch(file_path):
            url = urlunsplit((parts.scheme, parts.netloc, file_path, '', ''))
            rows.append({**row, 'url': url, 'viewer_url': viewer, 'recipe': 'vsb_pdfjs_file'})
    return rows
