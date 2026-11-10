"""Guarded attachment downloads must not trust a server's HTML MIME label."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from collectors import national_sources as sources
from public_sources import SourceError


UNIT = {'id': '123', 'name': '样本大学', 'unit_code': '10001',
        'source_url': 'https://yz.chsi.com.cn/sch/schoolInfo--schId-123.dhtml',
        'official_url': 'https://www.sample.edu.cn/'}
URL = 'https://grad.sample.edu.cn/cms/file/14d604d7f0c641f6860cb9dc1c040b74'
HTML_TYPE = 'text/html;charset=UTF-8'
XLS_TYPE = 'application/vnd.ms-excel'
DOC_TYPE = 'application/msword'
XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
PDF = b'%PDF-1.7\noriginal catalog bytes\n%%EOF'
OLE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'original sheet bytes'
ZIP = b'PK\x03\x04original OOXML bytes'


class Response(io.BytesIO):
    def __init__(self, body, headers, status=200):
        super().__init__(body)
        self.status = status
        self.headers = headers

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


def resolver(*args, **kwargs):
    return [(2, 1, 6, '', ('93.184.216.34', 443))]


def fetch(raw, content_type=HTML_TYPE, disposition='', *, url=URL, **kwargs):
    headers = {'Content-Type': content_type, 'Content-Disposition': disposition,
               'Content-Length': str(len(raw))}
    return sources.fetch_source(url, UNIT, resolver=resolver,
        transport=lambda *args: Response(raw, headers), **kwargs)


class BinaryHeaderTests(unittest.TestCase):
    def test_static_viewer_attachment_uses_correct_base_and_inherited_source_year(self):
        html = ('<base inert href="https://other.edu.cn/wrong/">'
                '<base href="https://grad.sample.edu.cn/">'
                '<title>样本大学2027年硕士招生目录</title>'
                '<div class="wp_pdf_player" pdfsrc="cms/file/abc.pdf"></div>')
        title, year, quote, parser = sources._html_info(html, UNIT, 2027,
                                                       'https://grad.sample.edu.cn/info/17/1.htm')
        document = {'url': 'https://grad.sample.edu.cn/info/17/1.htm', 'kind': 'catalog',
                    'year': year, 'year_evidence': {'quote': quote}}
        links = sources._page_links(document, parser, 2027, UNIT)
        self.assertIn('https://grad.sample.edu.cn/cms/file/abc.pdf', [row['url'] for row in links])
        self.assertTrue(all(row['year'] == 2027 for row in links))

    def test_unknown_year_fee_navigation_can_be_classified_by_original_heading(self):
        html = '<title>2027年研究生学费标准</title><main>按官方表格分类收费</main>'
        page = {'url': 'https://grad.sample.edu.cn/cost', 'raw': html.encode(),
                'text': html, 'content_type': HTML_TYPE, 'sha256': hashlib.sha256(html.encode()).hexdigest()}
        with tempfile.TemporaryDirectory() as temp:
            result = sources.collect_unit(UNIT, {'candidates': [{'url': page['url'], 'title': '学费标准'}]},
                Path(temp) / 'snapshots', project_root=Path(temp),
                fetcher=lambda url, unit: {**page, 'url': url}, max_pages=1)
        self.assertEqual(result['documents'][0]['kind'], 'attachment')
        self.assertEqual(result['documents'][0]['year'], 2027)
        self.assertEqual(result['documents'][0]['year_status'], 'explicit_original')
        self.assertFalse(result['catalog_complete'])

    def test_bnu_extensionless_fee_attachment_is_a_candidate_when_parent_has_year(self):
        url = ('https://yz.bnu.edu.cn/core/ucontent/fmfile/dd_article_attachment?attname=opath'
               '&attvalue=/202610/a6ae472856d41013c3f9134913a14323.pdf'
               '&name=专业学位硕士学习方式、学制学费一览表')
        item = sources.classify_link('附件2：专业学位硕士学习方式、学制学费一览表', url, 2027,
                                     parent_kind='charter', parent_year=2027)
        self.assertEqual(item['kind'], 'attachment')
        self.assertEqual(item['year_status'], 'linked_publication')
        self.assertEqual(item['year'], 2027)

    def test_gxu_extensionless_fee_and_plan_links_are_not_lost(self):
        url = 'https://yjsc.gxu.edu.cn/system/_content/download.jsp?wbfileid=18233193'
        for label in ('2027年硕士学费标准', '2027年硕士学制', '2027年硕士招生计划'):
            with self.subTest(label=label):
                item = sources.classify_link(label, url, 2027)
                self.assertEqual(item['kind'], 'attachment')
                self.assertEqual(item['year_status'], 'explicit_link')

    def test_fee_navigation_without_year_does_not_gain_attachment_year(self):
        item = sources.classify_link('研究生学费标准', '/cost', 2027)
        self.assertEqual(item['kind'], 'navigation')
        self.assertIsNone(item['year'])
        self.assertEqual(item['year_status'], 'unknown')

    def test_historical_doctoral_and_excluded_fee_links_are_not_promoted(self):
        for label in ('2026年硕士学费标准', '2027年博士学制', '2027年本科招生收费标准',
                      '2027年硕士复试计划', '2027年研究生奖学金'):
            with self.subTest(label=label):
                self.assertIsNone(sources.classify_link(label, '/cost', 2027,
                                  parent_kind='charter', parent_year=2027))

    def test_file_query_hint_requires_recognized_parameter_and_parent_year(self):
        good = '/attachment?attvalue=%2F202610%2Fcatalog.pdf&name=附件2'
        item = sources.classify_link('附件2', good, 2027, parent_kind='charter', parent_year=2027)
        self.assertEqual(item['kind'], 'attachment')
        self.assertIsNone(sources.classify_link('附件2', good, 2027))
        self.assertIsNone(sources.classify_link('附件2', '/attachment?message=catalog.pdf', 2027,
                                             parent_kind='charter', parent_year=2027))

    def test_malformed_file_hint_cannot_crash_seed_classification(self):
        self.assertIsNone(sources.classify_link('附件2', 'http://[', 2027,
                                             parent_kind='charter', parent_year=2027))

    def test_blocked_host_is_checked_before_dns_or_transport(self):
        dns, transport = Mock(), Mock()
        with self.assertRaises(SourceError) as caught:
            sources.fetch_source(URL, UNIT, resolver=dns, transport=transport,
                                 blocked_hosts=('grad.sample.edu.cn',))
        self.assertEqual(caught.exception.code, 'network_disabled')
        dns.assert_not_called()
        transport.assert_not_called()

    def test_redirect_to_blocked_host_never_performs_second_lookup_or_request(self):
        dns = Mock(side_effect=resolver)
        transport = Mock(return_value=Response(b'', {
            'Location': 'https://yz.chsi.com.cn/sch/schoolInfo--schId-123.dhtml'}, 302))
        with self.assertRaises(SourceError) as caught:
            sources.fetch_source(URL, UNIT, resolver=dns, transport=transport,
                                 blocked_hosts=('yz.chsi.com.cn',))
        self.assertEqual(caught.exception.code, 'network_disabled')
        self.assertEqual(dns.call_count, 1)
        self.assertEqual(transport.call_count, 1)

    def test_blocked_parent_host_covers_subdomains_without_suffix_false_positives(self):
        dns, transport = Mock(), Mock()
        with self.assertRaises(SourceError) as caught:
            sources.fetch_source(URL, UNIT, resolver=dns, transport=transport,
                                 blocked_hosts=('SAMPLE.EDU.CN.',))
        self.assertEqual(caught.exception.code, 'network_disabled')
        dns.assert_not_called()
        transport.assert_not_called()
        self.assertEqual(fetch(PDF, blocked_hosts=('ample.edu.cn',))['raw'], PDF)

    def test_blocked_host_string_is_not_silently_treated_as_characters(self):
        with self.assertRaises(SourceError) as caught:
            fetch(PDF, blocked_hosts='sample.edu.cn')
        self.assertEqual(caught.exception.code, 'invalid_limit')

    def test_pacer_uses_injected_fetcher_and_retains_lazy_default(self):
        custom = Mock(return_value={'raw': b'custom'})
        self.assertEqual(sources.HostPacer(0, fetcher=custom)(URL, UNIT), {'raw': b'custom'})
        custom.assert_called_once_with(URL, UNIT)
        pacer = sources.HostPacer(0)
        with patch.object(sources, 'fetch_source', return_value={'raw': b'default'}) as default:
            self.assertEqual(pacer(URL, UNIT), {'raw': b'default'})
        default.assert_called_once_with(URL, UNIT)

    def test_real_shaped_extensionless_pdf_with_html_mime_is_not_decoded(self):
        disposition = 'attachment; filename=%E5%B1%B1%E4%B8%9C2027%E4%B8%93%E4%B8%9A%E7%9B%AE%E5%BD%95.pdf'
        page = fetch(PDF, disposition=disposition)
        self.assertEqual(page['raw'], PDF)
        self.assertEqual(page['text'], '')
        self.assertEqual(page['content_type'], 'application/pdf')
        self.assertEqual(page['response_content_type'], HTML_TYPE)
        self.assertEqual(page['response_content_disposition'], disposition)
        self.assertEqual(page['type_detection'], 'pdf_signature')
        self.assertEqual(page['sha256'], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(page['url'], URL)

    def test_real_shaped_extensionless_xls_uses_disposition_and_full_ole_magic(self):
        disposition = 'attachment; filename=2027%E5%B9%B4%E8%80%83%E8%AF%95%E7%A7%91%E7%9B%AE.xls'
        page = fetch(OLE, disposition=disposition)
        self.assertEqual(page['raw'], OLE)
        self.assertEqual(page['text'], '')
        self.assertEqual(page['content_type'], XLS_TYPE)
        self.assertEqual(page['response_content_type'], HTML_TYPE)
        self.assertEqual(page['response_content_disposition'], disposition)
        self.assertEqual(page['type_detection'], 'ole_signature+content_disposition')

    def test_pdf_magic_can_confirm_an_extensionless_response_without_filename(self):
        for content_type in (HTML_TYPE, 'application/octet-stream', '', XLS_TYPE):
            with self.subTest(content_type=content_type):
                page = fetch(PDF, content_type)
                self.assertEqual(page['content_type'], 'application/pdf')
                self.assertEqual(page['text'], '')

    def test_encoded_and_uppercase_disposition_filenames_are_supported(self):
        for disposition in (
            "attachment; filename*=UTF-8''%E7%9B%AE%E5%BD%95.XLS",
            'attachment; filename="%E7%9B%AE%E5%BD%95.XLS"',
            'attachment; filename="2027年目录.XLS"',
        ):
            with self.subTest(disposition=disposition):
                self.assertEqual(fetch(OLE, disposition=disposition)['content_type'], XLS_TYPE)

    def test_ooxml_disposition_with_html_mime_is_supported(self):
        page = fetch(ZIP, disposition='attachment; filename="2027.xlsx"')
        self.assertEqual(page['content_type'], XLSX_TYPE)
        self.assertEqual(page['type_detection'], 'zip_signature+content_disposition')
        self.assertEqual(page['text'], '')

    def test_office_mime_and_signature_work_without_filename(self):
        for raw, mime in ((OLE, XLS_TYPE), (OLE, DOC_TYPE), (ZIP, XLSX_TYPE)):
            with self.subTest(mime=mime):
                page = fetch(raw, mime)
                self.assertEqual(page['content_type'], mime)
                self.assertEqual(page['text'], '')
                self.assertIn('content_type', page['type_detection'])

    def test_zip_url_suffix_remains_a_supported_legacy_hint(self):
        page = fetch(ZIP, 'application/octet-stream', url=URL + '.xlsx')
        self.assertEqual(page['content_type'], XLSX_TYPE)
        self.assertEqual(page['type_detection'], 'zip_signature+url_suffix')

    def test_fake_pdf_header_or_disposition_cannot_promote_html_to_binary(self):
        raw = '<title>招生简章</title><p>普通正文</p>'.encode()
        page = fetch(raw, disposition='attachment; filename="catalog.pdf"', url=URL + '.pdf')
        self.assertEqual(page['raw'], raw)
        self.assertEqual(page['content_type'], HTML_TYPE)
        self.assertIn('普通正文', page['text'])
        self.assertEqual(page['type_detection'], 'html_content_type')
        with self.assertRaises(SourceError) as caught:
            fetch(raw, 'application/pdf', disposition='attachment; filename="catalog.pdf"')
        self.assertEqual(caught.exception.code, 'unsupported_type')

    def test_truncated_or_mismatched_office_signatures_are_not_supported(self):
        for raw, mime, name in (
            (b'\xd0\xcf\x11\xe0original', XLS_TYPE, 'catalog.xls'),
            (b'\xd0\xcf\x11\xe0original', HTML_TYPE, 'catalog.xls'),
            (b'not an office file', XLS_TYPE, 'catalog.xls'),
            (OLE, HTML_TYPE, 'catalog.xlsx'),
            (ZIP, HTML_TYPE, 'catalog.xls'),
            (OLE, HTML_TYPE, ''),
            (OLE, 'application/octet-stream', ''),
        ):
            with self.subTest(raw=raw, mime=mime, name=name):
                with self.assertRaises(SourceError) as caught:
                    fetch(raw, mime, f'attachment; filename="{name}"')
                self.assertEqual(caught.exception.code, 'unsupported_type')

    def test_ole_url_suffix_alone_does_not_claim_a_doc_or_xls_subtype(self):
        with self.assertRaises(SourceError) as caught:
            fetch(OLE, 'application/octet-stream', url=URL + '.xls')
        self.assertEqual(caught.exception.code, 'unsupported_type')

    def test_ambiguous_compatible_office_subtypes_are_rejected(self):
        for raw, mime, name in ((OLE, DOC_TYPE, 'catalog.xls'),
                                (ZIP, XLSX_TYPE, 'catalog.docx')):
            with self.subTest(mime=mime, name=name):
                with self.assertRaises(SourceError) as caught:
                    fetch(raw, mime, f'attachment; filename="{name}"')
                self.assertEqual(caught.exception.code, 'unsupported_type')

    def test_filename_path_or_controls_are_not_office_type_hints(self):
        for name in ('../catalog.xls', r'C:\catalog.xls', '..%2Fcatalog.xls',
                     'line%0D%0Abreak.xls', 'nul%00.xls'):
            with self.subTest(name=name):
                with self.assertRaises(SourceError) as caught:
                    fetch(OLE, disposition=f'attachment; filename="{name}"')
                self.assertEqual(caught.exception.code, 'unsupported_type')

    def test_plain_html_retains_charset_and_restricted_download_is_still_blocked(self):
        for mime in (HTML_TYPE, 'application/xhtml+xml; charset=UTF-8'):
            with self.subTest(mime=mime):
                page = fetch('<title>招生信息</title>'.encode(), mime)
                self.assertEqual(page['content_type'], mime)
                self.assertEqual(page['response_content_type'], mime)
                self.assertIn('招生信息', page['text'])
        with self.assertRaises(SourceError) as caught:
            fetch('<title>安全验证</title>'.encode(), disposition='attachment; filename="2027.pdf"')
        self.assertEqual(caught.exception.code, 'restricted')

    def test_storage_keeps_original_header_provenance_and_hashed_filename(self):
        disposition = 'attachment; filename="2027年考试科目.xls"'
        page = fetch(OLE, disposition=disposition)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            document = sources._stored(page, {'url': URL, 'kind': 'subjects'}, root / 'snapshots', root, UNIT, 2027)
            key = hashlib.sha256(URL.encode()).hexdigest()[:16]
            digest = hashlib.sha256(OLE).hexdigest()[:16]
            self.assertEqual(document['local_path'], f'snapshots/{key}-{digest}.xls')
            self.assertEqual((root / document['local_path']).read_bytes(), OLE)
            self.assertEqual(document['response_content_type'], HTML_TYPE)
            self.assertEqual(document['response_content_disposition'], disposition)
            self.assertEqual(document['type_detection'], 'ole_signature+content_disposition')
            self.assertEqual(document['content_type'], XLS_TYPE)

    def test_disposition_never_supplies_the_saved_path_even_for_pdf(self):
        page = fetch(PDF, disposition='attachment; filename="../../outside.pdf"')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            document = sources._stored(page, {'url': URL, 'kind': 'catalog'}, root / 'snapshots', root, UNIT, 2027)
            self.assertEqual((root / document['local_path']).read_bytes(), PDF)
            self.assertEqual(len(list(root.rglob('*'))), 2)
            self.assertNotIn('outside', document['local_path'])

    def test_mislabelled_binary_downloads_still_obey_limits_and_unit_ownership(self):
        with self.assertRaises(SourceError) as caught:
            fetch(PDF, disposition='attachment; filename="2027.pdf"', max_bytes=len(PDF) - 1)
        self.assertEqual(caught.exception.code, 'too_large')
        transport = Mock(return_value=Response(PDF, {'Content-Type': HTML_TYPE,
            'Content-Disposition': 'attachment; filename="2027.pdf"'}))
        with self.assertRaises(SourceError) as caught:
            sources.fetch_source('https://other.edu.cn/cms/file/1', UNIT, resolver=resolver, transport=transport)
        self.assertEqual(caught.exception.code, 'wrong_unit')
        transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
