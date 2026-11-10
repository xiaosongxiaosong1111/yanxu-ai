"""Detect download containers; structural parsing and evidence approval happen later."""
from email.message import Message
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urlsplit


OFFICE_MEDIA = {
    'application/msword': '.doc',
    'application/vnd.ms-excel': '.xls',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
}
MEDIA_FOR_SUFFIX = {suffix: media for media, suffix in OFFICE_MEDIA.items()}
OLE_SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'


def office_link(url):
    """A file-shaped link is only a discovery hint, never a content-type proof."""
    try:
        parts = urlsplit(url)
    except (ValueError, UnicodeError):
        return False
    values = [parts.path] + [value for key, entries in parse_qs(parts.query).items()
        if key.lower() in {'attvalue', 'filename', 'file', 'path', 'url'} for value in entries]
    return any(PurePosixPath(value).suffix.lower() in {'.pdf', '.doc', '.docx', '.xls', '.xlsx'}
               for value in values)


def _filename_suffix(disposition):
    message = Message()
    message['Content-Disposition'] = disposition
    filename = message.get_filename()
    if not filename:
        return None
    filename = unquote(filename)
    if any(ord(char) < 32 or ord(char) == 127 or char in '/\\' for char in filename):
        return None
    return PurePosixPath(filename).suffix.lower()


def _office_payload(raw, url, media, disposition):
    ole = raw.startswith(OLE_SIGNATURE)
    zipped = raw.startswith(b'PK\x03\x04')
    supported = {'.doc', '.xls'} if ole else {'.docx', '.xlsx'} if zipped else set()
    if not supported:
        return None
    filename = _filename_suffix(disposition)
    declared = OFFICE_MEDIA.get(media)
    if declared and declared not in supported or filename and filename not in supported:
        return None
    if declared and filename and declared != filename:
        return None
    if declared:
        suffix, reason = declared, 'content_type'
    elif filename in supported:
        suffix, reason = filename, 'content_disposition'
    elif zipped and PurePosixPath(urlsplit(url).path).suffix.lower() in supported:
        # Retain legacy OOXML URL hints; this does not verify ZIP member structure.
        suffix, reason = PurePosixPath(urlsplit(url).path).suffix.lower(), 'url_suffix'
    else:
        return None
    return {'content_type': MEDIA_FOR_SUFFIX[suffix], 'suffix': suffix,
            'type_detection': ('ole' if ole else 'zip') + '_signature+' + reason}


def classify_source_payload(raw, url, content_type, content_disposition=''):
    """Return a container type, preserving HTML charset; never promote by filename alone."""
    media = content_type.split(';', 1)[0].lower().strip()
    if raw.startswith(b'%PDF-'):
        return {'content_type': 'application/pdf', 'suffix': '.pdf',
                'type_detection': 'pdf_signature'}
    office = _office_payload(raw, url, media, content_disposition)
    if office:
        return office
    if raw.startswith((b'PK\x03\x04', OLE_SIGNATURE[:4])):
        return None
    if media in {'text/html', 'application/xhtml+xml'}:
        return {'content_type': content_type, 'suffix': None,
                'type_detection': 'html_content_type'}
    return None
