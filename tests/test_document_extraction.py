"""Bounded canonical extraction preserves source positions without changing raw bytes."""
import hashlib
import importlib
import io
import sys
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import Mock, patch

from public_sources import SourceError, fetch_official_document
from tests.test_public_sources import Response, UNIT, resolver


DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
OLE = bytes.fromhex('d0cf11e0a1b11ae1')


def package(files):
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return target.getvalue()


def docx_xml(text='报名要求', extra=''):
    return package({'[Content_Types].xml': '<Types/>',
        'word/document.xml': '<w:document xmlns:w="urn:word"><w:body>'
        '<w:p><w:r><w:t>' + text + '</w:t></w:r></w:p>' + extra + '</w:body></w:document>'})


def xlsx_xml(target='worksheets/sheet1.xml'):
    return package({'[Content_Types].xml': '<Types/>',
        'xl/workbook.xml': '<workbook xmlns:r="urn:rel"><sheets><sheet name="收费表" sheetId="1" r:id="r1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships><Relationship Id="r1" Target="' + target + '"/></Relationships>',
        'xl/sharedStrings.xml': '<sst><si><t>专业</t></si><si><t>学费</t></si><si><t>计算机</t></si></sst>',
        'xl/worksheets/sheet1.xml': '<worksheet><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>8000</v></c></row></sheetData></worksheet>'})


class CanonicalExtractionTests(unittest.TestCase):
    def canonical(self, document, **kwargs):
        self.assertIsNotNone(importlib.util.find_spec('document_extraction'), 'N3 canonical extraction is not implemented')
        return importlib.import_module('document_extraction').canonical_document(document, **kwargs)

    def assert_spans(self, value):
        self.assertEqual(value['text_sha256'], hashlib.sha256(value['text'].encode()).hexdigest())
        for page in value['pages']:
            self.assertEqual(page['text'], value['text'][page['char_start']:page['char_end']])

    def test_pdf_retains_page_21_and_long_single_page(self):
        pages = [{'page': number, 'text': ('正文' * 1000 if number == 7 else '常规条款') + f'第{number}页'}
                 for number in range(1, 23)]
        value = self.canonical({'format': 'pdf', 'pages': pages})
        self.assertEqual(value['extraction_status'], 'complete')
        self.assertEqual(len(value['pages']), 22)
        self.assertGreater(len(value['pages'][6]['text']), 1600)
        self.assertIn('第21页', value['pages'][20]['text'])
        self.assert_spans(value)

    def test_pdf_form_feed_and_blank_page_preserve_physical_page_numbers(self):
        value = self.canonical({'content_type': 'application/pdf', 'text': '第一页\f\f第三页\f'})
        self.assertEqual([page['page'] for page in value['pages']], [1, 2, 3])
        self.assertEqual(value['pages'][1]['text'], '')
        self.assertNotIn('\f', value['text'])
        self.assertEqual(value['page_kind'], 'physical')
        self.assert_spans(value)

    def test_canonical_offsets_are_unicode_code_points_and_crlf_is_explicit(self):
        value = self.canonical({'text': '标题\r\n😀报考条件\r保留例外', 'format': 'html'})
        self.assertEqual(value['text'], '标题\n😀报考条件\n保留例外')
        self.assertEqual(value['page_kind'], 'logical')
        self.assert_spans(value)

    def test_limits_keep_truthful_partial_and_valid_spans(self):
        document = {'format': 'pdf', 'pages': [{'page': i, 'text': '页面内容' * 100} for i in range(1, 30)]}
        value = self.canonical(document, max_text_chars=1000, max_pages=10)
        self.assertEqual(value['extraction_status'], 'partial')
        self.assertLessEqual(len(value['text']), 1000)
        self.assertLessEqual(len(value['pages']), 10)
        self.assert_spans(value)
        self.assertEqual(len(document['pages']), 29)

    def test_excerpt_and_image_pdf_do_not_claim_full_text(self):
        excerpt = self.canonical({'excerpt': '招生摘要', 'text_origin': 'captured_excerpt'})
        self.assertEqual(excerpt['extraction_status'], 'partial')
        self.assertFalse(excerpt['full_extraction'])
        scan = self.canonical({'format': 'pdf', 'text': '\f', 'raw': b'%PDF-1.7 image fixture'})
        self.assertEqual(scan['extraction_status'], 'needs_ocr')

    def test_invalid_limits_pages_and_control_characters_fail_closed(self):
        for kwargs in [{'max_text_chars': True}, {'max_text_chars': 0}, {'max_pages': 1001}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.canonical({'text': '正文'}, **kwargs)
        for document in [{'text': '正文\x00'}, {'text': '正文\v'}, {'pages': [{'page': True, 'text': '正文'}]},
                         {'format': 'pdf', 'pages': [{'page': 2, 'text': '二'}, {'page': 1, 'text': '一'}]}]:
            with self.subTest(document=document), self.assertRaises(ValueError):
                self.canonical(document)

    def test_source_metadata_does_not_leak_paths_and_partial_page_set_is_not_full(self):
        value = self.canonical({'format': 'pdf', 'pages': [
            {'page': 21, 'text': '后续条款', 'local_path': 'private/example.pdf', 'char_start': 0}]})
        self.assertEqual(value['extraction_status'], 'partial')
        self.assertTrue(value['missing_pages'])
        self.assertNotIn('local_path', value['pages'][0])
        for document in [None, {'format': {}}, {'raw': 'not bytes'}, {'content_type': []}, {'pages': {}},
                         {'text': False}, {'excerpt': []}, {'pages': [{'page': 1}]},
                         {'pages': [{'page': 1, 'text': False}]}]:
            with self.subTest(document=document), self.assertRaises(ValueError):
                self.canonical(document)

    def test_full_text_and_page_text_cannot_disagree_and_claim_complete(self):
        with self.assertRaises(ValueError):
            self.canonical({'format': 'pdf', 'text': '正文\f重要例外\f',
                            'pages': [{'page': 1, 'text': '正文'}]})
        with self.assertRaises(ValueError):
            self.canonical({'format': 'html', 'text': '包含后续条款的全文',
                            'pages': [{'page': 1, 'text': '截断内容'}]})


class FetchExtractionTests(unittest.TestCase):
    def fetch(self, raw, media='text/html; charset=utf-8', **kwargs):
        return fetch_official_document('https://grad.example.edu.cn/a', UNIT, resolver=resolver,
            transport=lambda *args: Response(raw, **{'Content-Type': media}), **kwargs)

    def test_fetch_preserves_legacy_pdf_and_adds_complete_canonical_derivation(self):
        text = '\f'.join(['第' + str(i) + '页' for i in range(1, 23)]) + '\f'
        raw = b'%PDF-1.7 fixture'
        document = self.fetch(raw, 'application/pdf', pdf_extractor=lambda path: text)
        self.assertEqual(document['text'], text)
        self.assertEqual(document['extraction_status'], 'extracted')
        self.assertIn('extraction', document, 'A full canonical derivation must accompany the bounded reader')
        self.assertEqual(document['extraction']['extraction_status'], 'complete')
        self.assertEqual(len(document['extraction']['pages']), 22)
        self.assertEqual(document['raw'], raw)

    def test_html_and_empty_html_keep_old_status_contract(self):
        document = self.fetch('<h1>2027年招生章程</h1><p>报考条件</p>'.encode())
        self.assertEqual(document['extraction_status'], 'extracted')
        self.assertIn('extraction', document)
        self.assertEqual(document['format'], 'html')
        self.assertEqual(self.fetch(b'<script>hidden()</script>')['extraction_status'], 'empty_text')

    def test_body_implicitly_closes_misplaced_head_without_losing_official_text(self):
        raw = ('<!DOCTYPE HTML><html><title>招生简章</title><head><body><header>导航</header>'
               '<article><h1>2027年招生简章</h1><p>同等学力考生应同时满足专业和外语要求。</p>'
               '<script>不可见脚本</script></article></body></html>').encode()
        document = self.fetch(raw)
        self.assertIn('同等学力考生应同时满足专业和外语要求。', document['text'])
        self.assertEqual(document['document_title'], '招生简章')
        self.assertNotIn('不可见脚本', document['text'])
        self.assertNotIn('导航', document['text'])

    def test_docx_reads_paragraphs_tables_and_marks_logical_positions(self):
        table = '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>专业</w:t></w:r></w:p></w:tc>'
        table += '<w:tc><w:p><w:r><w:t>学费</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        raw = docx_xml(extra=table)
        document = self.fetch(raw, DOCX)
        self.assertIn('报名要求', document['text'])
        self.assertIn('专业\t学费', document['text'])
        self.assertEqual(document['extraction']['page_kind'], 'logical')
        self.assertEqual(document['extraction']['format'], 'docx')

    def test_docx_content_controls_keep_restrictions_and_cell_positions(self):
        control = '<w:sdt><w:sdtPr/><w:sdtContent><w:p><w:r><w:t>不接受跨专业考生</w:t></w:r></w:p></w:sdtContent></w:sdt>'
        table = '<w:tbl><w:tr><w:tc>' + control + '</w:tc><w:tc><w:p><w:r><w:t>408</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        document = self.fetch(docx_xml(extra=control + table), DOCX)
        self.assertIn('不接受跨专业考生\t408', document['text'])
        self.assertEqual(document['text'].count('不接受跨专业考生'), 2)
        self.assertEqual(document['extraction_state'], 'complete')

    def test_unsupported_word_blocks_and_nested_tables_cannot_claim_complete(self):
        cases = ['<w:altChunk/>', '<w:sdt><w:sdtPr/></w:sdt>',
                 '<w:tbl><w:tr><w:tc><w:tbl><w:tr><w:tc><w:p><w:r><w:t>例外</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>']
        for extra in cases:
            with self.subTest(extra=extra):
                document = self.fetch(docx_xml(extra=extra), DOCX)
                self.assertEqual(document['extraction_state'], 'partial')
                self.assertFalse(document['extraction']['full_extraction'])
                self.assertIn('报名要求', document['text'])

    def test_xlsx_resolves_shared_strings_and_sheet_rows_without_physical_pages(self):
        document = self.fetch(xlsx_xml(), XLSX)
        self.assertIn('专业\t学费', document['text'])
        self.assertIn('计算机\t8000', document['text'])
        self.assertEqual(document['extraction']['page_kind'], 'logical')
        self.assertEqual(document['extraction']['pages'][0]['sheet'], '收费表')

    def test_hidden_excel_columns_are_omitted_with_empty_cells_and_partial_status(self):
        with zipfile.ZipFile(io.BytesIO(xlsx_xml())) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        files['xl/worksheets/sheet1.xml'] = files['xl/worksheets/sheet1.xml'].replace(
            b'<sheetData>', b'<cols><col min="2" max="2" hidden="1"/></cols><sheetData>').replace(
            b'<v>8000</v></c>', b'<v>8000</v></c><c r="C2"><v>901</v></c>')
        document = self.fetch(package(files), XLSX)
        self.assertIn('计算机\t\t901', document['text'])
        self.assertNotIn('8000', document['text'])
        self.assertEqual(document['extraction_state'], 'partial')
        self.assertFalse(document['extraction']['full_extraction'])

    def test_hidden_excel_column_bounds_fail_closed(self):
        with zipfile.ZipFile(io.BytesIO(xlsx_xml())) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        original = files['xl/worksheets/sheet1.xml']
        for first, last in [('x', '2'), ('0', '2'), ('3', '2'), ('1', '16385')]:
            columns = f'<cols><col min="{first}" max="{last}" hidden="true"/></cols>'.encode()
            files = {**files, 'xl/worksheets/sheet1.xml': original.replace(b'<sheetData>', columns + b'<sheetData>')}
            with self.subTest(first=first, last=last), self.assertRaises(SourceError):
                self.fetch(package(files), XLSX)

    def test_doc_and_scanned_pdf_expose_real_support_state(self):
        document = self.fetch(OLE + b'old doc fixture', 'application/msword')
        self.assertEqual(document['extraction']['extraction_status'], 'unsupported')
        self.assertEqual(document['text'], '')
        scan = self.fetch(b'%PDF-1.7 image fixture', 'application/pdf', pdf_extractor=lambda path: '\f')
        self.assertEqual(scan['extraction']['extraction_status'], 'needs_ocr')

    def test_media_magic_mismatch_is_not_interpreted_as_another_format(self):
        for raw, media in [(b'%PDF-1.7 wrong', DOCX), (docx_xml(), 'text/html'),
                           (docx_xml(), XLSX), (b'not ole', 'application/msword')]:
            with self.subTest(media=media), self.assertRaises(SourceError):
                self.fetch(raw, media)

    def test_office_parser_limit_remains_partial_in_fetch_metadata(self):
        module = importlib.import_module('document_extraction')
        with patch.object(module, 'MAX_OFFICE_ROWS', 1):
            document = self.fetch(xlsx_xml(), XLSX)
        self.assertEqual(document['extraction_state'], 'partial')
        self.assertEqual(document['extraction']['extraction_status'], 'partial')
        self.assertNotIn('8000', document['text'])

    def test_unhandled_word_notes_are_partial_and_hidden_excel_sheet_is_not_policy_text(self):
        word = package({'[Content_Types].xml': '<Types/>',
            'word/document.xml': '<document><body><p><t>报考条件</t></p></body></document>',
            'word/footnotes.xml': '<footnotes><footnote>另有例外</footnote></footnotes>'})
        self.assertEqual(self.fetch(word, DOCX)['extraction_state'], 'partial')
        data = xlsx_xml()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        files['xl/workbook.xml'] = files['xl/workbook.xml'].replace(b'sheetId="1"', b'state="hidden" sheetId="1"')
        document = self.fetch(package(files), XLSX)
        self.assertEqual(document['text'], '')
        self.assertEqual(document['extraction_state'], 'partial')

    def test_unsafe_archive_paths_and_external_xml_are_rejected(self):
        cases = [package({'[Content_Types].xml': '<Types/>', 'word/document.xml': '<document/>', '../evil.xml': 'x'}),
                 b'',
                 xlsx_xml('../external.xml')]
        xml = '<!DOCTYPE document [<!ENTITY xx SYSTEM "file:///private">]><document>&xx;</document>'
        cases[1] = package({'[Content_Types].xml': '<Types/>', 'word/document.xml': xml})
        for index, raw in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(SourceError):
                self.fetch(raw, XLSX if index == 2 else DOCX)

    def test_zip_expansion_and_member_budgets_are_enforced_before_parse(self):
        module = importlib.import_module('document_extraction')
        with patch.object(module, 'MAX_UNCOMPRESSED_BYTES', 32), self.assertRaises(SourceError):
            self.fetch(docx_xml(), DOCX)
        with patch.object(module, 'MAX_ZIP_MEMBERS', 1), self.assertRaises(SourceError):
            self.fetch(docx_xml(), DOCX)

    def test_legacy_xls_adapter_closes_resources_and_reports_partial_rows(self):
        sheet = SimpleNamespace(ncols=2, nrows=2, name='费用表', row_values=lambda row: [['专业', '学费'], ['计算机', 8000]][row])
        book = SimpleNamespace(nsheets=1, sheet_by_index=lambda index: sheet, release_resources=Mock())
        with patch('xlrd.open_workbook', return_value=book):
            document = self.fetch(OLE + b'controlled workbook fixture', 'application/vnd.ms-excel')
        self.assertIn('计算机\t8000', document['text'])
        self.assertEqual(document['extraction_state'], 'complete')
        self.assertEqual(document['extraction']['page_kind'], 'logical')
        book.release_resources.assert_called_once()
        with patch('xlrd.open_workbook', return_value=book), patch('document_extraction.MAX_OFFICE_ROWS', 1):
            self.assertEqual(self.fetch(OLE, 'application/vnd.ms-excel')['extraction_state'], 'partial')

    def test_missing_legacy_xls_dependency_and_corrupt_ole_have_explicit_results(self):
        with patch.dict(sys.modules, {'xlrd': None}):
            self.assertEqual(self.fetch(OLE, 'application/vnd.ms-excel')['extraction_state'], 'unsupported')
        with self.assertRaises(SourceError) as error:
            self.fetch(OLE + b'broken workbook', 'application/vnd.ms-excel')
        self.assertEqual(error.exception.code, 'extraction_error')

    def test_nested_or_utf16_dtd_xml_and_bad_package_structure_are_rejected(self):
        dtd = '<!DOCTYPE document [<!ENTITY xx "text">]><document>&xx;</document>'.encode('utf-16')
        deep = ('<document>' + '<p>' * 70 + 'text' + '</p>' * 70 + '</document>').encode()
        for xml in [dtd, deep, b'<document>', b'<document/>']:
            with self.subTest(xml=xml[:20]), self.assertRaises(SourceError):
                self.fetch(package({'[Content_Types].xml': '<Types/>', 'word/document.xml': xml}), DOCX)
        for raw in [b'PK\x03\x04broken', package({'word/document.xml': '<document/>'})]:
            with self.subTest(raw=raw[:20]), self.assertRaises(SourceError):
                self.fetch(raw, DOCX)

    def test_parser_invalid_text_is_a_source_error_without_internal_details(self):
        with self.assertRaises(SourceError) as error:
            self.fetch(b'%PDF-1.7 fixture', 'application/pdf', pdf_extractor=lambda path: '正文\x00')
        self.assertEqual(error.exception.code, 'extraction_error')


class SavedDocumentExtractionTests(unittest.TestCase):
    def extract(self, *args, **kwargs):
        module = importlib.import_module('public_sources')
        self.assertTrue(hasattr(module, 'extract_saved_document'), 'Offline original re-extraction is missing')
        return module.extract_saved_document(*args, **kwargs)

    def test_saved_pdf_reextracts_without_network_or_fresh_authority_claim(self):
        raw = b'%PDF-1.7 saved fixture'
        with patch('public_sources.socket.getaddrinfo', side_effect=AssertionError('No network')):
            document = self.extract(raw, url='https://grad.example.edu.cn/original',
                                    pdf_extractor=lambda path: '第一页\f\f第三页\f')
        self.assertEqual(document['raw'], raw)
        self.assertEqual(document['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(document['extraction']['pages'][2]['page'], 3)
        self.assertEqual(document['attribution_status'], 'not_rechecked')
        self.assertIsNone(document['checked_at'])
        self.assertFalse(document['network_verified'])

    def test_saved_html_is_inferred_conservatively_and_unknown_bytes_stay_unsupported(self):
        document = self.extract(b'<main><h1>2027 charter</h1><p>Requirement</p></main>')
        self.assertEqual(document['format'], 'html')
        self.assertIn('Requirement', document['text'])
        with self.assertRaises(SourceError) as error:
            self.extract(b'unknown binary')
        self.assertEqual(error.exception.code, 'unsupported_type')

    def test_saved_extraction_retains_size_time_and_parser_error_boundaries(self):
        for timeout in [True, 0, 61]:
            with self.subTest(timeout=timeout), self.assertRaises(SourceError):
                self.extract(b'%PDF-fixture', timeout=timeout)
        with patch('public_sources.MAX_BYTES', 8), self.assertRaises(SourceError):
            self.extract(b'%PDF-fixture')
        with self.assertRaises(SourceError) as error:
            self.extract(b'%PDF-fixture', pdf_extractor=Mock(side_effect=ValueError('private path')))
        self.assertEqual(error.exception.code, 'extraction_error')


if __name__ == '__main__':
    unittest.main()
