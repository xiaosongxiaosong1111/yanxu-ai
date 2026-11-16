"""The pinned archive accepts only a visible literal reference to its own PDF."""
import hashlib
import unittest
from unittest.mock import patch

from collectors import catalog_ycit_vsb as replay
from collectors.catalog_publication import _PublicationHTML


class YcitViewerReplayTests(unittest.TestCase):
    def test_exact_visible_viewer_and_source_bytes_required(self):
        path = '/virtual_attach_file.vsb?afc=' + 'A' * 48 + '&oid=1790821392&tid=1157&nid=11652&e=.pdf'
        literal = ('<script>var vsb_pdf_image_data = [];try{'
                   f'showVsbpdfIframe("{path}","100%","600","0","border:1px solid #DDDDDD",'
                   'vsb_pdf_image_data);}catch(e){}</script>')
        document = {'url': 'https://yjsc.ycit.edu.cn' + path, 'sha256': replay.SOURCE_SHA}

        def links(body, source=document, raw=b'%PDF-fixture'):
            parser = _PublicationHTML(body)
            parser.feed(body)
            parser.close()
            fingerprint = hashlib.sha256(body.encode('utf8')).hexdigest()
            parent = {'url': replay.PARENT_URL, 'sha256': fingerprint}
            with patch.object(replay, 'PARENT_SHA', fingerprint):
                return replay.exact_ycit_viewer_links(body, parser, parent, source, raw)

        body = '<main>' + literal + '</main>'
        result = links(body)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['url'], document['url'])
        self.assertEqual(result[0]['recipe'], replay.RECIPE)
        origin = result[0]['evidence'][0]
        self.assertEqual(body[origin['start']:origin['end']], origin['snippet'])
        self.assertEqual(links('<main hidden>' + literal + '</main>'), [])
        self.assertEqual(links('<head><base href="/elsewhere/"></head>' + body), [])
        self.assertEqual(links(body, {**document, 'url': document['url'] + '&extra=1'}), [])
        self.assertEqual(links(body, {**document, 'sha256': '0' * 64}), [])
        self.assertEqual(links(body, raw=b'<html>attachment unavailable</html>'), [])
