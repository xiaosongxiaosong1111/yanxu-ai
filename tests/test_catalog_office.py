import io
from pathlib import Path
import unittest
import zipfile

from collectors.catalog_office import extract_office_html, parse_catalog_office, _number_display
from collectors.documents import DocumentError
from service_verification import plain_text


def package(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items(): archive.writestr(name, value)
    return stream.getvalue()


def docx(rows):
    from html import escape
    table = ''.join('<w:tr>' + ''.join('<w:tc><w:p><w:r><w:t>' + escape(cell) + '</w:t></w:r></w:p></w:tc>' for cell in row) + '</w:tr>' for row in rows)
    return package({'word/document.xml': '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>测试大学2027年硕士研究生招生专业目录</w:t></w:r></w:p><w:tbl>' + table + '</w:tbl></w:body></w:document>'})


class CatalogOfficeTests(unittest.TestCase):
    def rows(self):
        return [['专业', '研究方向', '学习方式', '初试科目'], ['081200计算机科学与技术', '00不区分研究方向', '全日制', '①101思想政治理论②201英语（一）或203日语③301数学（一）④408计算机学科专业基础']]

    def test_docx_original_text_and_catalog_or_survive_without_fabricated_page_numbers(self):
        raw = docx(self.rows())
        text = extract_office_html(raw)
        self.assertIn('测试大学2027年硕士研究生招生专业目录', plain_text(text))
        result = parse_catalog_office(raw, {'id': '1', 'name': '测试大学'}, 2027, {'id': 'd', 'url': 'https://example.edu.cn/catalog.docx'})
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 1)
        self.assertEqual(result['programs'][0]['catalog_parser'], 'office_table_v1')
        self.assertEqual({s['code'] for s in result['programs'][0]['subjects'] if s['exam_unit'] == 2}, {'201', '203'})
        self.assertTrue(all(e.get('page') is None for e in result['programs'][0]['evidence']))

    def test_text_is_html_escaped(self):
        text = extract_office_html(docx([['<script>alert(1)</script>', 'A&B']]))
        self.assertNotIn('<script>', text)
        self.assertIn('&lt;script&gt;', text)
        self.assertIn('A&B', plain_text(text))

    def test_legacy_binary_is_rejected_instead_of_decoded_as_text(self):
        with self.assertRaises(DocumentError): extract_office_html(bytes.fromhex('d0cf11e0a1b11ae1') + b'not a zip')

    def test_path_traversal_and_macros_and_xml_entities_are_rejected(self):
        for files in [ {'../word/document.xml': '<x />'}, {'word/document.xml': '<x />', 'word/vbaProject.bin': 'x'}, {'word/document.xml': '<!DOCTYPE x [<!ENTITY a "secret">]><x>&a;</x>'} ]:
            with self.subTest(files=list(files)), self.assertRaises(DocumentError): extract_office_html(package(files))

    def test_docx_explicit_vertical_merges_preserve_professional_identity(self):
        raw = package({'word/document.xml': '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>081200计算机科学与技术</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>01方向</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc><w:tc><w:p><w:r><w:t>02方向</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'})
        html = extract_office_html(raw)
        from collectors.history import _Tables
        tables = _Tables(); tables.feed(html)
        self.assertEqual(tables.tables[0][1][0], '081200计算机科学与技术')

    def xlsx(self, sheet, extra=None):
        return package({'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="2027年目录" sheetId="1" r:id="rId1"/></sheets></workbook>', 'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="worksheet"/></Relationships>', 'xl/worksheets/sheet1.xml': sheet, **(extra or {})})

    def test_xlsx_shared_inline_strings_numeric_cells_and_explicit_merges(self):
        raw = self.xlsx('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>01方向</t></is></c><c r="C1"><v>20</v></c></row><row r="2"><c r="B2" t="inlineStr"><is><t>02方向</t></is></c></row></sheetData><mergeCells><mergeCell ref="A1:A2"/></mergeCells></worksheet>', {'xl/sharedStrings.xml': '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><r><t>081200</t></r><r><t>计算机科学与技术</t></r></si></sst>'})
        from collectors.history import _Tables
        tables = _Tables(); tables.feed(extract_office_html(raw))
        self.assertEqual(tables.tables[0][1][0], '081200计算机科学与技术')
        self.assertEqual(tables.tables[0][0][2], '20')

    def test_xlsx_formula_cached_result_and_external_sheet_are_not_trusted(self):
        with self.assertRaises(DocumentError):
            extract_office_html(self.xlsx('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><f>WEBSERVICE("https://example.com")</f><v>101</v></c></row></sheetData></worksheet>'))
        with self.assertRaises(DocumentError):
            extract_office_html(self.xlsx('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1000000"><c r="A1000000"><v>1</v></c></row></sheetData></worksheet>'))


    def test_duplicate_cell_and_overlapping_merge_are_rejected(self):
        for body in [
            '<sheetData><row r="1"><c r="A1"><v>1</v></c><c r="A1"><v>2</v></c></row></sheetData>',
            '<sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData><mergeCells><mergeCell ref="A1:A2"/><mergeCell ref="A2:A3"/></mergeCells>'
        ]:
            with self.subTest(body=body), self.assertRaises(DocumentError):
                extract_office_html(self.xlsx('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' + body + '</worksheet>'))


    def test_old_excel_zero_padded_numeric_codes_keep_the_displayed_zeros(self):
        self.assertEqual(_number_display(81200.0, '000000'), '081200')
        self.assertEqual(_number_display(3.0, '000'), '003')
        self.assertEqual(_number_display(3.0, 'General'), '3')
        self.assertEqual(_number_display(3.0, '@'), '3')
        self.assertEqual(_number_display(3.0, '0_ '), '3')
        self.assertEqual(_number_display(8000.0, '#,##0'), '8,000')
        with self.assertRaises(DocumentError): _number_display(3.0, '0.0E+00')


    def test_split_exam_code_name_columns_pair_or_and_keep_original_cell_quotes(self):
        rows = [
            ['201空天科学与技术学院'] * 15,
            ['专业代码', '专业名称', '学习方式', '方向代码', '专业方向名称', '统考招生人数', '考试科目', '考试科目', '考试科目', '考试科目', '考试科目', '考试科目', '考试科目', '考试科目', '复试要求及相关说明'],
            ['专业代码', '专业名称', '学习方式', '方向代码', '专业方向名称', '统考招生人数', '思想政治理论/管理类综合能力', '思想政治理论/管理类综合能力', '外国语', '外国语', '业务课一', '业务课一', '业务课二', '业务课二', '复试要求及相关说明'],
            ['专业代码', '专业名称', '学习方式', '方向代码', '专业方向名称', '统考招生人数', '科目代码', '科目名称', '科目代码', '科目名称', '科目代码', '科目名称', '科目代码', '科目名称', '复试要求及相关说明'],
            ['080100', '力学', '全日制', '04', '材料与结构冲击动力学', '4', '101', '思想政治理论', '201或202或203', '英语（一）或俄语或日语', '301', '数学（一）', '838', '工程力学基础', '笔试科目：数学、理论力学、材料力学']]
        result = parse_catalog_office(docx(rows), {'id': '1', 'name': '测试大学'}, 2027, {'id': 'd', 'url': 'https://example.edu.cn/catalog.docx'})
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 1)
        p = result['programs'][0]
        self.assertEqual(p['department_code'], '201')
        self.assertEqual(p['direction'], '04材料与结构冲击动力学')
        self.assertEqual({s['code'] for s in p['subjects'] if s['exam_unit'] == 2}, {'201', '202', '203'})
        self.assertIn('201或202或203', [ref['quote'] for ref in p['exam_evidence']])
        self.assertNotIn('201英语（一）或202俄语或203日语', [ref['quote'] for ref in p['exam_evidence']])


    def test_paired_management_catalog_explicit_no_exam_slots_stay_two_units(self):
        rows = [['专业代码', '专业名称', '学习方式', '方向代码', '专业方向名称', '科目代码', '科目名称', '科目代码', '科目名称', '科目代码', '科目名称', '科目代码', '科目名称'],
                ['125100', '工商管理', '全日制', '00', '不区分研究方向', '199', '管理类综合能力', '204', '英语（二）', '-', '无', '--', '无']]
        result = parse_catalog_office(docx(rows), {'id': '1', 'name': '测试大学'}, 2027, {'id': 'd', 'url': 'https://example.edu.cn/catalog.docx'})
        self.assertEqual(result['issues'], [])
        self.assertEqual({s['exam_unit'] for s in result['programs'][0]['subjects']}, {1, 2})


    def test_real_bit_2027_legacy_excel_preserves_all_357_directions(self):
        raw = (Path(__file__).parent / 'fixtures/catalog_office/bit-2027.xls').read_bytes()
        unit = {'id': '367884', 'name': '北京理工大学', 'province': '北京'}
        result = parse_catalog_office(raw, unit, 2027,
            {'id': 'bit', 'url': 'https://grd.bit.edu.cn/docs/2026-10/b1b149be9486483aa6614537c7db5ef0.xls'})
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 357)
        self.assertEqual(len(result['programs']), 357)
        self.assertEqual(len({p['id'] for p in result['programs']}), 357)
        self.assertTrue(any(p['department_code'] == '280' and p['department'] == '珠海校区' for p in result['programs']))
        mechanical = next(p for p in result['programs'] if p['department_code']=='202' and p['code']=='080100')
        self.assertEqual({s['code'] for s in mechanical['subjects'] if s['exam_unit']==2}, {'201','202','203'})


    def test_xlsx_numeric_display_styles_preserve_official_code_zeros(self):
        styles = '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="2"><numFmt numFmtId="164" formatCode="000000"/><numFmt numFmtId="165" formatCode="000"/></numFmts><cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="165"/></cellXfs></styleSheet>'
        sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" s="1"><v>81200</v></c><c r="B1" s="2"><v>3</v></c><c r="C1"><v>3.0</v></c></row></sheetData></worksheet>'
        from collectors.history import _Tables
        tables = _Tables(); tables.feed(extract_office_html(self.xlsx(sheet, {'xl/styles.xml': styles})))
        self.assertEqual(tables.tables[0][0], ['081200', '003', '3'])

    def test_xlsx_unknown_styles_and_non_textual_cell_types_fail_closed(self):
        for attributes, value in [('s="99"', '81200'), ('t="b"', '1'), ('t="e"', '#VALUE!'), ('t="d"', '2027-01-01')]:
            with self.subTest(attributes=attributes), self.assertRaises(DocumentError):
                extract_office_html(self.xlsx('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" ' + attributes + '><v>' + value + '</v></c></row></sheetData></worksheet>'))

    def test_xlsx_hidden_sheets_rows_and_columns_are_not_public_catalog_facts(self):
        sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>内部草稿</t></is></c></row></sheetData></worksheet>'
        for marker in ['hidden', 'veryHidden']:
            raw = self.xlsx(sheet)
            with zipfile.ZipFile(io.BytesIO(raw)) as source:
                files = {name: source.read(name) for name in source.namelist()}
            files['xl/workbook.xml'] = files['xl/workbook.xml'].decode().replace('sheetId="1"', 'sheetId="1" state="' + marker + '"')
            with self.subTest(state=marker), self.assertRaises(DocumentError): extract_office_html(package(files))
        for modified in [sheet.replace('<row r="1">', '<row r="1" hidden="1">'), sheet.replace('<sheetData>', '<cols><col min="1" max="1" hidden="true"/></cols><sheetData>')]:
            with self.subTest(sheet=modified), self.assertRaises(DocumentError): extract_office_html(self.xlsx(modified))

    def test_legacy_xls_hidden_sheet_row_and_column_fail_closed(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from collectors.catalog_office import _xls
        for sheet in [SimpleNamespace(visibility=1), SimpleNamespace(visibility=0, rowinfo_map={0: SimpleNamespace(hidden=1)}, colinfo_map={}), SimpleNamespace(visibility=0, rowinfo_map={}, colinfo_map={0: SimpleNamespace(hidden=1)})]:
            book = SimpleNamespace(nsheets=1, sheets=lambda: [sheet], release_resources=lambda: None)
            dependency = SimpleNamespace(open_workbook=lambda **_: book, XLRDError=ValueError, compdoc=SimpleNamespace(CompDocError=ValueError))
            with self.subTest(sheet=sheet), patch('collectors.catalog_office._xlrd_dependency', return_value=dependency), self.assertRaises(DocumentError): _xls(b'raw')


    def test_real_cfu_compound_header_preserves_college_identity_and_different_exam_scopes(self):
        raw = (Path(__file__).parent / 'fixtures/catalog_office/cfu-2027.docx').read_bytes()
        result = parse_catalog_office(raw, {'id': '499721721', 'name': '赤峰大学'}, 2027,
            {'id': 'cfu', 'url': 'https://www.cfu.edu.cn/_upload/article/files/6f/d3/be506d044ef7ae4baec40196d11a/73c55cde-6cfd-4e3a-a495-6de99082044b.docx'})
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 33)
        self.assertEqual(len(result['programs']), 33)
        self.assertEqual(len({p['id'] for p in result['programs']}), 33)
        language = [p for p in result['programs'] if p['code'] == '045103']
        self.assertEqual({p['department_code'] for p in language}, {'002', '003'})
        mongolian = next(p for p in language if p['department_code'] == '002')
        chinese = next(p for p in language if p['department_code'] == '003')
        self.assertTrue(mongolian['department'].startswith('蒙古学学院'))
        self.assertTrue(chinese['department'].startswith('文学院'))
        self.assertEqual({s['code'] for s in mongolian['subjects'] if s['exam_unit'] == 4}, {'831'})
        self.assertEqual({s['code'] for s in chinese['subjects'] if s['exam_unit'] == 4}, {'832'})
        self.assertTrue(all(p['department_code'] and p.get('department_scope') != 'unit' for p in result['programs']))


if __name__ == '__main__': unittest.main()
