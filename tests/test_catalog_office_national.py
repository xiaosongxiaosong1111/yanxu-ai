"""Original worksheet merge ranges define the scope of vertically split exams."""
import io
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape

from collectors.catalog_office_national import parse_catalog_office_national, workbook_layout
from collectors.documents import DocumentError


def workbook(cells, merges=()):
    rows = sorted({int(''.join(filter(str.isdigit, key))) for key in cells})
    body = ''.join('<row r="%s">%s</row>' % (row, ''.join(
        '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (key, escape(value))
        for key, value in cells.items() if int(''.join(filter(str.isdigit, key))) == row)) for row in rows)
    root = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="'+root+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="目录" sheetId="1" r:id="r1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="'+root+'"><sheetData>'+body+'</sheetData><mergeCells>'+''.join('<mergeCell ref="'+value+'"/>' for value in merges)+'</mergeCells></worksheet>')
    return data.getvalue()


UNIT = {'id': 'sample', 'name': '样本大学', 'province': '北京'}
DOCUMENT = {'id': 'office', 'url': 'https://sample.edu.cn/catalog.xlsx', 'unit_id': 'sample', 'year': 2027}


def cells():
    return {'A1': '样本大学2027年硕士研究生招生专业目录',
            'A2': '学院代码：001 学院名称：计算机学院 咨询电话：010-12345678',
            'A3': '专业代码', 'B3': '专业名称', 'C3': '研究方向', 'D3': '学习方式',
            'E3': '学位类别', 'H3': '初试科目',
            'A4': '081200', 'B4': '计算机科学与技术', 'C4': '01人工智能',
            'C6': '02软件理论', 'D4': '全日制', 'E4': '学术学位',
            'H4': '①101思想政治理论', 'H5': '②201英语（一）',
            'H6': '③301数学（一）', 'H7': '④408计算机学科专业基础'}


def archive_with(raw, changes):
    result=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(result,'w') as target:
        entries={name:original.read(name) for name in original.namelist()}
        for name,value in {**entries,**changes}.items():target.writestr(name,value)
    return result.getvalue()


class OfficeNationalTests(unittest.TestCase):
    def test_one_complete_exam_block_covers_all_directions_in_its_real_major_merge(self):
        raw = workbook(cells(), ['A4:A7','B4:B7','C4:C5','C6:C7','D4:D7','E4:E7'])
        result = parse_catalog_office_national(raw, UNIT, 2027, DOCUMENT)
        self.assertEqual(result['issues'], [])
        self.assertEqual(len(result['programs']), 2)
        self.assertEqual(result['expected_records'], 2)
        for program in result['programs']:
            self.assertEqual([row['code'] for row in program['subjects']], ['101','201','301','408'])
            self.assertEqual(program['department_code'], '001')
            self.assertEqual(program['source_locator']['sheet'], '目录')

    def test_repeated_first_slot_starts_a_new_context_not_an_or_option(self):
        data = {**cells(), 'C4': '01通信方向', 'C8': '02计算方向', 'H7': '④891通信原理',
                'H8': '①101思想政治理论','H9': '②204英语（二）',
                'H10': '③302数学（二）','H11': '④408计算机学科专业基础'}
        data.pop('C6')
        raw = workbook(data, ['A4:A11','B4:B11','C4:C7','C8:C11','D4:D11','E4:E11'])
        result = parse_catalog_office_national(raw, UNIT, 2027, DOCUMENT)
        self.assertEqual(result['issues'], [])
        self.assertEqual(len(result['programs']), 2)
        self.assertEqual([[s['code'] for s in p['subjects']] for p in result['programs']],
                         [['101','201','301','891'], ['101','204','302','408']])

    def test_missing_major_merge_never_borrows_the_next_rows_subjects(self):
        result = parse_catalog_office_national(workbook(cells()), UNIT, 2027, DOCUMENT)
        self.assertTrue(result['issues'])
        self.assertEqual(result['programs'], [])

    def test_other_year_and_non_unified_header_are_not_adopted(self):
        for title in ['样本大学2026年硕士研究生招生专业目录', '样本大学2027年单独考试招生专业目录']:
            result = parse_catalog_office_national(workbook({**cells(), 'A1': title}), UNIT, 2027, DOCUMENT)
            self.assertTrue(result['issues'])
            self.assertEqual(result['programs'], [])

    def test_declared_document_identity_and_year_must_match_original_context(self):
        for changed in [{'unit_id':'different'}, {'year':2026}]:
            result = parse_catalog_office_national(workbook(cells(),['A4:A7','B4:B7','C4:C5','C6:C7','D4:D7','E4:E7']),UNIT,2027,{**DOCUMENT,**changed})
            self.assertEqual(result['programs'],[])
            self.assertTrue(result['issues'])

    def test_explicit_hierarchical_faculty_major_and_modes_keep_only_initial_exams(self):
        data={'A1':'样本大学2027年硕士研究生招生专业目录','A2':'学院（培养单位）、专业及研究方向',
              'D2':'考试科目','A3':'010 计算机学院（010-12345678）','A4':'081200 计算机科学与技术',
              'A5':'01 人工智能（全日制）','A6':'02 计算理论（全日制）',
              'D5':'①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础\n复试科目：①501计算机理论'}
        result=parse_catalog_office_national(workbook(data,['D5:D6']),UNIT,2027,DOCUMENT)
        self.assertEqual(result['issues'],[])
        self.assertEqual(len(result['programs']),2)
        self.assertTrue(all(len(row['subjects'])==4 for row in result['programs']))
        self.assertEqual(result['programs'][1]['department_code'],'010')
        self.assertNotIn('501',{s['code'] for p in result['programs'] for s in p['subjects']})

    def test_flat_rows_use_only_the_actual_merged_faculty_and_major(self):
        data={'A1':'样本大学2027年硕士研究生招生专业目录','A2':'院系所','B2':'专业','C2':'研究方向',
              'D2':'学习方式','J2':'考试科目','A3':'301农学院','B3':'090100作物学',
              'C3':'01作物栽培','C4':'02作物遗传','D3':'(1)全日制','D4':'(1)全日制',
              'J3':'①101思想政治理论②201英语（一）③315化学（农）④414植物生理学与生物化学',
              'J4':'①101思想政治理论②201英语（一）③315化学（农）④414植物生理学与生物化学'}
        result=parse_catalog_office_national(workbook(data,['A3:A4','B3:B4']),UNIT,2027,DOCUMENT)
        self.assertEqual(result['issues'],[])
        self.assertEqual(len(result['programs']),2)
        self.assertTrue(all(row['department']=='农学院' for row in result['programs']))
        self.assertEqual([s['scope'] for s in result['programs'][0]['subjects']],['national']*4)

    def test_source_title_mode_applies_to_unlabelled_directions_but_year_notes_are_not_directions(self):
        data={'A1':'样本大学2027年硕士研究生招生专业目录（全日制）',
              'A2':'培养单位、专业及研究方向','C2':'初试科目','A3':'010 计算机学院',
              'A4':'081200 计算机科学与技术','A5':'01人工智能',
              'A6':'2027年计划以正式文件为准（全日制）',
              'C5':'①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础'}
        result=parse_catalog_office_national(workbook(data,['C5:C6']),UNIT,2027,DOCUMENT)
        self.assertEqual(result['issues'],[])
        self.assertEqual(len(result['programs']),1)
        self.assertEqual(result['programs'][0]['study_mode'],'全日制')
        self.assertTrue(any(ref['quote']==data['A1'] for ref in result['programs'][0]['evidence']))

    def test_direction_with_immediate_latin_name_is_not_a_six_character_major_code(self):
        data={'A1':'样本大学2027年硕士研究生招生专业目录（全日制）',
              'A2':'培养单位、专业及研究方向','C2':'初试科目','A3':'040 机械工程学院',
              'A4':'140300 设计学','A5':'12AIGC审美计算科技美育（全日制）',
              'C5':'①101思想政治理论②201英语（一）③630设计理论④833设计基础'}
        result=parse_catalog_office_national(workbook(data),UNIT,2027,DOCUMENT)
        self.assertEqual(result['issues'],[])
        self.assertEqual(len(result['programs']),1)
        self.assertEqual(result['programs'][0]['code'],'140300')
        self.assertEqual(result['programs'][0]['direction'],'12AIGC审美计算科技美育')

    def test_real_vertical_quota_column_is_not_degree_and_original_name_annotation_is_used(self):
        data={**cells(),'B4':'电子信息（专业学位）','E3':'全日制拟招生人数','E4':'50'}
        result=parse_catalog_office_national(workbook(data,['A4:A7','B4:B7','C4:C5','C6:C7','D4:D7','E4:E7']),UNIT,2027,DOCUMENT)
        self.assertEqual(result['issues'],[])
        self.assertEqual(result['programs'][0]['degree_type'],'专业学位')
        self.assertEqual(result['programs'][0]['name'],'电子信息（专业学位）')

    def test_real_legacy_xls_keeps_numeric_leading_zeros_and_original_merged_geometry(self):
        raw=(Path(__file__).parent/'fixtures/catalog_office/bit-2027.xls').read_bytes()
        self.assertTrue(raw.startswith(bytes.fromhex('d0cf11e0a1b11ae1')))
        layout=workbook_layout(raw)
        self.assertEqual(len(layout),1)
        sheet=layout[0]
        self.assertEqual((sheet['cells'][(6,2)],sheet['cells'][(6,5)]),('080100','01'))
        self.assertEqual(sheet['cells'][(6,14)],'848')
        self.assertIn((1,2,1,16),sheet['merges'])
        self.assertEqual((len(sheet['cells']),len(sheet['merges'])),(4986,729))

    def test_shared_strings_and_numeric_code_styles_preserve_registration_identity(self):
        raw=workbook(cells(),['A4:A7','B4:B7','C4:C5','C6:C7','D4:D7','E4:E7'])
        with zipfile.ZipFile(io.BytesIO(raw)) as source:
            sheet=source.read('xl/worksheets/sheet1.xml').decode()
        sheet=sheet.replace('<c r="A4" t="inlineStr"><is><t>081200</t></is></c>','<c r="A4" s="1"><v>81200</v></c>')
        sheet=sheet.replace('<c r="B4" t="inlineStr"><is><t>计算机科学与技术</t></is></c>','<c r="B4" t="s"><v>0</v></c>')
        sheet=sheet.replace('<c r="D4" t="inlineStr"><is><t>全日制</t></is></c>','<c r="D4" t="str"><v>全日制</v></c>')
        sheet=sheet.replace('</sheetData>','<row r="8"><c r="B8"/></row></sheetData>')
        xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        raw=archive_with(raw,{
            'xl/worksheets/sheet1.xml':sheet,
            'xl/sharedStrings.xml':'<sst xmlns="'+xmlns+'"><si><r><t>计算机科学</t></r><r><t>与技术</t></r></si></sst>',
            'xl/styles.xml':'<styleSheet xmlns="'+xmlns+'"><numFmts><numFmt numFmtId="164" formatCode="000000"/></numFmts><cellXfs><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs></styleSheet>'})
        result=parse_catalog_office_national(raw,UNIT,2027,DOCUMENT)
        self.assertEqual(result['issues'],[])
        self.assertTrue(all((p['code'],p['name'],p['study_mode'])==('081200','计算机科学与技术','全日制') for p in result['programs']))
        self.assertEqual(len(result['programs']),2)

    def test_titleless_workbook_needs_actual_own_publication_and_unchanged_attachment_hash(self):
        data={**cells(),'A1':'2027年硕士研究生招生专业目录'}
        raw=workbook(data,['A4:A7','B4:B7','C4:C5','C6:C7','D4:D7','E4:E7'])
        self.assertFalse(parse_catalog_office_national(raw,UNIT,2027,DOCUMENT)['programs'])
        unit={**UNIT,'official_url':'https://sample.edu.cn/'}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'catalog.xlsx').write_bytes(raw)
            html='<h1>样本大学2027年硕士研究生招生专业目录</h1><a href="/catalog.xlsx">计算机学院目录</a>'
            (root/'publication.html').write_text(html,'utf-8')
            document={**DOCUMENT,'local_path':'catalog.xlsx','sha256':hashlib.sha256(raw).hexdigest(),
                'publication_page':{'id':'pub','unit_id':'sample','year':2027,'url':'https://sample.edu.cn/2027.htm',
                    'local_path':'publication.html','sha256':hashlib.sha256((root/'publication.html').read_bytes()).hexdigest(),
                    'content_type':'text/html'}}
            result=parse_catalog_office_national(raw,unit,2027,document,project_root=root)
            self.assertEqual(result['issues'],[])
            self.assertEqual(len(result['programs']),2)
            self.assertEqual(result['documents'][0]['id'],'pub')
            (root/'catalog.xlsx').write_bytes(raw+b'tampered')
            self.assertFalse(parse_catalog_office_national(raw,unit,2027,document,project_root=root)['programs'])

    def test_vertical_identity_mode_and_degree_conflicts_never_fabricate_an_option(self):
        merges=['A4:A7','B4:B7','C4:C5','C6:C7','D4:D7','E4:E7']
        for changes in [{'A2':''},{'B4':''},{'C4':'未编号方向','C6':'未编号方向'},
                        {'D4':'全日制、非全日制'}, {'B4':'计算机科学与技术（专业学位）'}]:
            with self.subTest(changes=changes):
                result=parse_catalog_office_national(workbook({**cells(),**changes},merges),UNIT,2027,DOCUMENT)
                self.assertTrue(result['issues'])
                self.assertEqual(result['programs'],[])

    def test_incomplete_or_unlabelled_vertical_exam_rows_are_not_inferred(self):
        merges=['A4:A7','B4:B7','C4:C5','C6:C7','D4:D7','E4:E7']
        for changes in [{'H5':'说明：请选择对应科目'},{'H6':'②203日语'},
                        {'H4':'②201英语（一）'},{'H7':'④408'}]:
            with self.subTest(changes=changes):
                result=parse_catalog_office_national(workbook({**cells(),**changes},merges),UNIT,2027,DOCUMENT)
                self.assertTrue(result['issues'])
                self.assertEqual(result['programs'],[])

    def test_hierarchical_mode_and_exam_gaps_and_conflicting_duplicate_directions_stay_visible(self):
        data={'A1':'样本大学2027年硕士研究生招生专业目录',
              'A2':'培养单位、专业及研究方向','C2':'初试科目','A3':'010 计算机学院',
              'A4':'081200 计算机科学与技术','A5':'01人工智能（全日制）',
              'C5':'①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础'}
        variants=[{**data,'A5':'01人工智能'},{**data,'C5':'①101思想政治理论③301数学（一）'},
                  {**data,'A6':data['A5'],'C6':data['C5'].replace('408计算机学科专业基础','801程序设计')}]
        for variant in variants:
            with self.subTest(variant=variant):
                result=parse_catalog_office_national(workbook(variant),UNIT,2027,DOCUMENT)
                self.assertEqual(result['programs'],[])
                self.assertTrue(result['issues'])

    def test_unsupported_layout_and_hidden_rows_fail_before_any_candidates(self):
        result=parse_catalog_office_national(workbook({'A1':cells()['A1'],'A2':'非目录内容'}),UNIT,2027,DOCUMENT)
        self.assertFalse(result['programs'])
        self.assertTrue(result['issues'])
        raw=workbook(cells())
        with zipfile.ZipFile(io.BytesIO(raw)) as source:
            sheet=source.read('xl/worksheets/sheet1.xml').decode().replace('<row r="4">','<row r="4" hidden="1">')
        with self.assertRaises(DocumentError):workbook_layout(archive_with(raw,{'xl/worksheets/sheet1.xml':sheet}))


if __name__ == '__main__': unittest.main()
