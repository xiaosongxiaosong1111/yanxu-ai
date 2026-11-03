"""Regression cases for real-grid PDF catalogues, independent of old PDF adapters."""
import copy
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from collectors.catalog_pdf_national import parse_catalog_pdf_national, extract_pdf_national_layout, _plumber, PARSER
from collectors.documents import DocumentError


def table(texts, spans=None):
    spans=spans or {}
    rows=[]
    for i,row in enumerate(texts):
        cells=[]
        for j,value in enumerate(row):
            if value is None: cells.append(None);continue
            down,across=spans.get((i,j),(1,1))
            cells.append([j*100,i*30,(j+across)*100,(i+down)*30])
        rows.append({'texts':row,'cells':cells})
    return {'bbox':[0,0,len(texts[0])*100,len(texts)*30],'rows':rows}


def fixture():
    texts=[['学院','专业代码及名称','研究方向','学习方式','初试科目','复试科目'],
        ['001计算机学院','081200计算机科学与技术','01人工智能','全日制','①101思想政治理论\n②201英语（一）\n③301数学（一）\n④408计算机学科专业基础','复试专业综合'],
        [None,None,'02网络安全',None,None,'复试专业综合'],
        ['002信息学院','085400电子信息','01软件工程','非全日制','①101思想政治理论\n②204英语（二）\n③302数学（二）\n④801程序设计','复试程序设计']]
    t=table(texts,{(1,0):(2,1),(1,1):(2,1),(1,3):(2,1),(1,4):(2,1)})
    raw='样本大学2027年硕士研究生招生专业目录\n'+'\n'.join(v for row in texts for v in row if v)
    return {'raw_text':raw,'pages':[{'page':1,'width':600,'height':800,'tables':[t],'lines':[]}]}


class PdfNationalTests(unittest.TestCase):
    def parse(self, value=None, unit=None, year=2027, document=None):
        with patch('collectors.catalog_pdf_national.verify_catalog_publication',return_value={
                'verified':False,'documents':[],'evidence':[],'mode_evidence':[],
                'mode':None,'mode_conflict':False}):
            return parse_catalog_pdf_national(value or fixture(),unit or {'id':'u','name':'样本大学','province':'测试'},year,
                document or {'id':'d','unit_id':'u','year':2027,'url':'https://sample.edu.cn/2027.pdf'})

    def test_actual_cell_spans_preserve_faculty_and_exam_combinations(self):
        value=fixture();original=copy.deepcopy(value);r=self.parse(value)
        self.assertEqual(value,original)
        self.assertEqual(len(r['programs']),3,r['issues'])
        self.assertEqual(r['expected_records'],3)
        self.assertEqual(r['issues'],[])
        first,second,third=r['programs']
        self.assertEqual((first['department_code'],first['department']),('001','计算机学院'))
        self.assertEqual(second['department_code'],'001')
        self.assertEqual(third['department_code'],'002')
        self.assertEqual([s['code'] for s in second['subjects']],['101','201','301','408'])
        self.assertEqual([s['code'] for s in third['subjects']],['101','204','302','801'])
        self.assertEqual(third['study_mode'],'非全日制')
        self.assertTrue(all(p['catalog_parser']==PARSER for p in r['programs']))
        for p in r['programs']:
            for ref in p['evidence']+p['exam_evidence']:
                self.assertIn(ref['quote'],value['raw_text'].split('\f')[ref['page']-1])

    def test_blank_cell_without_physical_span_never_borrows_previous_initial_exam(self):
        v=fixture();v['pages'][0]['tables'][0]['rows'][1]['cells'][4][3]=60
        r=self.parse(v)
        self.assertEqual(len(r['programs']),2)
        self.assertTrue(r['issues'])
        self.assertEqual(r['expected_records'],3)

    def test_or_options_remain_inside_their_exam_unit(self):
        v=fixture();v['pages'][0]['tables'][0]['rows'][1]['texts'][4]='①101思想政治理论\n②201英语（一）或203日语\n③301数学（一）\n④408计算机学科专业基础'
        v['raw_text']+='\n②201英语（一）或203日语'
        r=self.parse(v)
        self.assertEqual([s['code'] for s in r['programs'][0]['subjects'] if s['exam_unit']==2],['201','203'])
        self.assertTrue(all(s['choice_group']=='exam-unit-2' for s in r['programs'][0]['subjects'] if s['exam_unit']==2))

    def test_direction_followed_by_six_digit_field_code_does_not_drop_registration_choices(self):
        rows=[['专业代码及名称','学习方式','初试科目'],
              ['0452 体育','全日制','①101思想政治理论\n②204英语（二）\n③346体育综合'],
              ['01 045201 体育教学',None,None],['02 045202 运动训练',None,None],
              ['03 045203 竞赛组织',None,None],['04 045204 社会体育指导',None,None]]
        v=fixture();v['pages'][0]['tables']=[table(rows,{(1,1):(5,1),(1,2):(5,1)})]
        v['raw_text']='样本大学2027年硕士研究生招生专业目录\n'+'\n'.join(x for row in rows for x in row if x)
        r=self.parse(v)
        self.assertEqual(len(r['programs']),4,r['issues'])
        self.assertEqual({p['code'] for p in r['programs']},{'0452'})
        self.assertEqual({p['direction'][:2] for p in r['programs']},{'01','02','03','04'})
        self.assertFalse(any(p['direction']=='' for p in r['programs']))

    def test_single_cell_faculty_heading_is_retained_for_compound_catalogue(self):
        rows=[['院系所、专业、研究方向','学习方式','初试科目'],
              ['001法学院','',''],['030100法学','全日制','①101思想政治理论\n②201英语（一）\n③710法学综合\n④810专业基础'],
              ['01法学理论',None,None],['002信息学院','',''],
              ['081200计算机科学与技术','全日制','①101思想政治理论\n②201英语（一）\n③301数学（一）\n④408计算机学科专业基础'],
              ['01计算机系统',None,None]]
        v=fixture();v['pages'][0]['tables']=[table(rows,{(2,1):(2,1),(2,2):(2,1),(5,1):(2,1),(5,2):(2,1)})]
        v['raw_text']='样本大学2027年硕士研究生招生专业目录\n'+'\n'.join(x for row in rows for x in row if x)
        r=self.parse(v)
        self.assertEqual([p['department_code'] for p in r['programs']],['001','002'])
        self.assertFalse(any(p.get('department_scope')=='unit' for p in r['programs']))

    def test_conditional_cross_unit_plans_never_turn_into_cartesian_or_options(self):
        v=fixture();rows=v['pages'][0]['tables'][0]['rows']
        rows[0]['texts'][5]='备注'
        rows[1]['texts'][4]='①101思想政治理论\n②201英语（一）\n③301数学（一）或302数学（二）\n④801程序设计或802数据结构'
        rows[1]['texts'][5]='第三、四单元须按方案一301+801或方案二302+802成套选择'
        v['raw_text']+='\n'+rows[1]['texts'][4]+'\n'+rows[1]['texts'][5]
        r=self.parse(v)
        self.assertFalse(any(p['code']=='081200' and p['direction'].startswith('01') for p in r['programs']))
        self.assertTrue(any('组合' in issue for issue in r['issues']))

    def test_repeated_physical_rows_merge_only_when_all_directory_facts_match(self):
        v=fixture();t=v['pages'][0]['tables'][0]
        duplicate=copy.deepcopy(t['rows'][3]);duplicate['cells']=[[c[0],c[1]+30,c[2],c[3]+30] if c else None for c in duplicate['cells']]
        t['rows'].append(duplicate);t['bbox'][3]+=30
        r=self.parse(v)
        self.assertEqual(len(r['programs']),3,r['issues'])
        self.assertEqual(r['expected_records'],3)
        self.assertEqual(len(next(p for p in r['programs'] if p['code']=='085400')['duplicate_source_locators']),2)
        v['pages'][0]['tables'][0]['rows'][-1]['texts'][4]='①101思想政治理论\n②204英语（二）\n③302数学（二）\n④802数据结构'
        v['raw_text']+='\n'+v['pages'][0]['tables'][0]['rows'][-1]['texts'][4]
        r=self.parse(v)
        self.assertFalse(any(p['code']=='085400' for p in r['programs']))
        self.assertTrue(any('冲突' in text for text in r['issues']))

    def test_extraction_uses_both_grid_and_native_sources_without_reconstructing_quotations(self):
        v=fixture();page=v['pages'][0];t=page['tables'][0]
        rows=[SimpleNamespace(cells=row['cells']) for row in t['rows']]
        physical=SimpleNamespace(bbox=t['bbox'],rows=rows,extract=lambda **kwargs:[row['texts'] for row in t['rows']])
        pp=SimpleNamespace(width=600,height=800,find_tables=lambda:[physical],extract_text_lines=lambda:[{'text':'样本大学2027年硕士研究生招生专业目录','x0':10,'x1':200,'top':10,'bottom':30}])
        class PDF:
            pages=[pp]
            doc=SimpleNamespace(is_extractable=True)
            def __enter__(self):return self
            def __exit__(self,*args):return None
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.pdf';path.write_bytes(b'%PDF-original')
            with patch('collectors.catalog_pdf_national.extract_pdf_layout',return_value=v),patch('collectors.catalog_pdf_national._plumber',return_value=SimpleNamespace(open=lambda _:PDF())):
                layout=extract_pdf_national_layout(path)
            self.assertEqual(layout['raw_text'],v['raw_text'])
            self.assertEqual(len(layout['pages'][0]['tables'][0]['rows']),4)
            self.assertIn('native',layout['pages'][0])
            with patch('collectors.catalog_pdf_national.extract_pdf_layout',return_value=v),patch('collectors.catalog_pdf_national._plumber',return_value=SimpleNamespace(open=lambda _:PDF())):
                PDF.doc=SimpleNamespace(is_extractable=False)
                with self.assertRaises(DocumentError):extract_pdf_national_layout(path)
            with patch('collectors.catalog_pdf_national.extract_pdf_layout',return_value=v),patch('collectors.catalog_pdf_national._plumber',side_effect=ValueError('bad grid')):
                with self.assertRaises(DocumentError):extract_pdf_national_layout(path)
            path.write_bytes(b'')
            with self.assertRaises(DocumentError):extract_pdf_national_layout(path)
            with self.assertRaises(DocumentError):extract_pdf_national_layout(path.with_name('missing.pdf'))

    def test_optional_dependency_is_isolated_and_fails_with_a_useful_error(self):
        with patch('collectors.catalog_pdf_national.importlib.import_module',return_value='module'):
            self.assertEqual(_plumber(),'module')
        with tempfile.TemporaryDirectory() as folder,patch('collectors.catalog_pdf_national.PROJECT_ROOT',Path(folder)),patch('collectors.catalog_pdf_national.importlib.import_module',side_effect=ImportError('missing')):
            with self.assertRaisesRegex(DocumentError,'隔离依赖'):_plumber()

    def test_page_count_source_identity_and_whole_document_mode_conflicts_remain_gaps(self):
        v=fixture();v['raw_text']+='\fsecond page'
        self.assertTrue(any('页数' in issue for issue in self.parse(v)['issues']))
        v=fixture();v['pages'][0]['tables']=[]
        self.assertTrue(self.parse(v)['issues'])
        with patch('collectors.catalog_pdf_national.verify_catalog_publication',return_value={'verified':True,'mode':'全日制','mode_conflict':False,'documents':[],'evidence':[],'mode_evidence':[]}):
            r=parse_catalog_pdf_national(fixture(),{'id':'u','name':'样本大学'},2027,{'id':'d','unit_id':'u','year':2027,'url':'https://sample.edu.cn/a.pdf'})
            self.assertFalse(any(p['study_mode']=='非全日制' for p in r['programs']))
            self.assertTrue(any('矛盾' in text for text in r['issues']))

    def test_faculty_contact_lines_are_not_faculty_names_and_two_digit_codes_remain_literal(self):
        v=fixture();row=v['pages'][0]['tables'][0]['rows'][3]
        row['texts'][0]='04信息学院\n联系人：教师甲\n联系电话：12345678901'
        v['raw_text']+='\n'+row['texts'][0]
        r=self.parse(v);p=next(p for p in r['programs'] if p['code']=='085400')
        self.assertEqual((p['department_code'],p['department']),('04','信息学院'))
        self.assertIn(':04:',p['id'])
        self.assertNotIn('联系人',p['department'])

    def test_a_new_table_cannot_borrow_the_previous_faculty_without_a_continuation(self):
        v=fixture();rows=[['学院','专业代码及名称','研究方向','学习方式','初试科目'],
               ['', '081600测绘科学与技术','01测绘工程','全日制','①101思想政治理论\n②201英语（一）\n③301数学（一）\n④803测绘学']]
        v['pages'][0]['tables'].append(table(rows))
        v['raw_text']+='\n'+'\n'.join(x for row in rows for x in row if x)
        r=self.parse(v)
        self.assertFalse(any(p['code']=='081600' for p in r['programs']))
        self.assertTrue(any('学院列' in text for text in r['issues']))

    def test_explicit_new_major_does_not_cite_the_previous_pages_major(self):
        v=fixture();new=copy.deepcopy(v['pages'][0]);new['page']=2
        rows=[['学院','专业代码及名称','研究方向','学习方式','初试科目','复试科目'],
              ['003测绘学院','081600测绘科学与技术','01测绘工程','全日制','①101思想政治理论\n②201英语（一）\n③301数学（一）\n④803测绘学','复试综合']]
        new['tables']=[table(rows)];v['pages'].append(new)
        v['raw_text']+='\f'+'\n'.join(x for row in rows for x in row if x)
        r=self.parse(v);p=next(p for p in r['programs'] if p['code']=='081600')
        self.assertFalse(any('085400' in ref['quote'] for ref in p['evidence']))
        self.assertTrue(any('081600' in ref['quote'] for ref in p['evidence']))

    def test_department_code_header_does_not_discard_the_official_leading_zero(self):
        v=fixture();rows=[['院系代码','院系名称','专业类别及代码','学习方式','研究方向','考试科目'],
              ['008','电子工程系','085400电子信息','全日制','不区分研究方向','①101思想政治理论\n②204英语（二）\n③302数学（二）\n④408计算机学科专业基础']]
        v['pages'][0]['tables']=[table(rows)];v['raw_text']+='\n'+'\n'.join(x for row in rows for x in row if x)
        r=self.parse(v);p=r['programs'][0]
        self.assertEqual(p['department_code'],'008')
        self.assertEqual(p['department'],'电子工程系')
        self.assertIn(':008:',p['id'])

    def test_academic_and_professional_chapter_types_keep_their_original_headings(self):
        v=fixture();page=v['pages'][0];first=page['tables'][0];first['rows']=first['rows'][:3]
        first['bbox']=[0,50,600,140]
        for row in first['rows']:
            row['cells']=[[x0,y0+50,x1,y1+50] if cell else None for cell in row['cells'] for x0,y0,x1,y1 in ([cell] if cell else [[0,0,0,0]])]
        second=table([['学院','专业代码及名称','研究方向','学习方式','初试科目','复试科目'],
            ['002信息学院','085400电子信息','01软件工程','全日制','①101思想政治理论\n②204英语（二）\n③302数学（二）\n④801程序设计','复试专业基础']])
        second['bbox']=[0,300,600,360]
        for row in second['rows']:
            row['cells']=[[c[0],c[1]+300,c[2],c[3]+300] if c else None for c in row['cells']]
        page['tables']=[first,second]
        page['lines']=[{'text':'一、学术学位硕士研究生招生目录','y0':10,'y1':25,'x0':10,'x1':300},
                       {'text':'二、专业学位硕士研究生招生目录','y0':250,'y1':270,'x0':10,'x1':300}]
        v['raw_text']+='\n一、学术学位硕士研究生招生目录\n二、专业学位硕士研究生招生目录\n复试专业基础'
        r=self.parse(v)
        self.assertEqual({p['degree_type'] for p in r['programs'] if p['code']=='081200'},{'学术学位'})
        self.assertEqual({p['degree_type'] for p in r['programs'] if p['code']=='085400'},{'专业学位'})

    def test_explicit_recommendation_only_directions_never_enter_unified_catalogue(self):
        v=fixture();row=v['pages'][0]['tables'][0]['rows'][1]
        row['texts'][2]='01人工智能（仅招收推免生）'
        v['raw_text']+='\n'+row['texts'][2]
        r=self.parse(v)
        self.assertFalse(any('仅招收推免' in p['direction'] for p in r['programs']))
        self.assertEqual(len(r['excluded_records']),1)

    def test_wrong_school_year_or_mixed_special_scope_are_not_adopted(self):
        for unit,year,doc in [({'id':'other','name':'样本大学'},2027,None),(None,2026,None),
                (None,2027,{'id':'d','unit_id':'u','year':2028,'url':'https://sample.edu.cn/x.pdf'})]:
            self.assertEqual(self.parse(unit=unit,year=year,document=doc)['programs'],[])
        v=fixture();v['raw_text']=v['raw_text'].replace('硕士研究生招生专业目录','单独考试硕士研究生招生专业目录')
        self.assertEqual(self.parse(v)['programs'],[])

    def test_a_mode_in_an_early_body_row_is_not_a_whole_document_declaration(self):
        v=fixture();rows=v['pages'][0]['tables'][0]['rows']
        rows[1]['texts'][3]='';rows[3]['texts'][3]=''
        v['raw_text']='样本大学2027年硕士研究生招生专业目录\n学习方式\n全日制\n'+v['raw_text']
        r=self.parse(v)
        self.assertEqual(r['programs'],[])

    def test_missing_mode_is_not_guessed_from_common_university_practice(self):
        v=fixture();rows=v['pages'][0]['tables'][0]['rows']
        rows[1]['texts'][3]='';rows[3]['texts'][3]=''
        r=self.parse(v)
        self.assertEqual(r['programs'],[])
        self.assertTrue(r['issues'])

    def test_corrupt_pdf_text_cannot_receive_synthetic_quotes(self):
        v=fixture();v['raw_text']=v['raw_text'].replace('程序设计','伪造课程')
        r=self.parse(v)
        self.assertFalse(any(p['code']=='085400' for p in r['programs']))
        self.assertTrue(any('原文' in x for x in r['issues']))

    def test_retest_column_cannot_enter_initial_subjects(self):
        v=fixture();v['pages'][0]['tables'][0]['rows'][1]['texts'][5]='①999复试附加科目'
        v['raw_text']+='\n①999复试附加科目'
        r=self.parse(v)
        self.assertFalse(any(s['code']=='999' for p in r['programs'] for s in p['subjects']))

    def test_suspicious_phone_number_is_not_a_four_digit_major(self):
        v=fixture();v['pages'][0]['tables'][0]['rows'][3]['texts'][1]='0991联系电话'
        v['raw_text']+='\n0991联系电话'
        r=self.parse(v)
        self.assertFalse(any(p['code']=='0991' for p in r['programs']))
        self.assertTrue(r['issues'])


if __name__=='__main__': unittest.main()
