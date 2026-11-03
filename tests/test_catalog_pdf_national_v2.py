import copy
import unittest
from unittest.mock import patch
from tests.test_catalog_pdf_national import fixture,table
from collectors.catalog_pdf_national import PARSER as V1
from collectors.catalog_pdf_national_v2 import parse_catalog_pdf_national_v2,PARSER


class PdfNationalV2Tests(unittest.TestCase):
    def parse(self,layout):
        with patch('collectors.catalog_pdf_national.verify_catalog_publication',return_value={
                'verified':False,'documents':[],'evidence':[],'mode_evidence':[],
                'mode':None,'mode_conflict':False}):
            return parse_catalog_pdf_national_v2(layout,{'id':'u','name':'样本大学','province':'测试'},2027,
                {'id':'d','unit_id':'u','year':2027,'url':'https://sample.edu.cn/2027.pdf'})

    def source(self,rows,spans=None):
        layout=fixture();layout['pages'][0]['tables']=[table(rows,spans)]
        layout['raw_text']='样本大学2027年硕士研究生招生专业目录\n'+'\n'.join(value for row in rows for value in row if value)
        return layout

    def test_explicit_mode_in_first_note_line_is_used_without_mutating_input(self):
        rows=[['学院代码及名称','专业代码及名称','初试科目','备注'],
            ['001教育学院','045118学前教育','①101思想政治理论\n②204英语（二）\n③333教育综合\n④801教育理论','全日制\n专业学位\n（三年制）'],
            [None,None,None,'非全日制\n专业学位\n（三年制）']]
        value=self.source(rows,{(1,0):(2,1),(1,1):(2,1),(1,2):(2,1)});original=copy.deepcopy(value)
        result=self.parse(value)
        self.assertEqual(value,original)
        self.assertEqual({p['study_mode'] for p in result['programs']},{'全日制','非全日制'})
        self.assertTrue(all(p['catalog_parser']==PARSER for p in result['programs']))
        self.assertEqual(V1,'pdf_table_national_v1')

    def test_conditional_note_is_not_a_learning_mode_declaration(self):
        rows=[['专业代码及名称','初试科目','备注'],
            ['081200计算机科学与技术','①101思想政治理论\n②201英语（一）\n③301数学（一）\n④408计算机学科专业基础','仅接收全日制本科毕业生']]
        result=self.parse(self.source(rows))
        self.assertEqual(result['programs'],[])
        self.assertTrue(result['issues'])

    def test_two_initial_exam_columns_keep_their_original_slots_and_or_choices(self):
        rows=[['学院','专业','研究方向','学习方式','初试统考科目','初试自命题科目'],
            ['001机电学院','080200机械工程','01机械制造','全日制','①101思想政治理论\n②201英语一或203日语\n③301数学一','④801机械设计'],
            [None,None,'02机械电子',None,None,'④802控制理论']]
        value=self.source(rows,{(1,0):(2,1),(1,1):(2,1),(1,3):(2,1),(1,4):(2,1)})
        result=self.parse(value)
        self.assertEqual(len(result['programs']),2,result['issues'])
        self.assertEqual([s['code'] for s in result['programs'][0]['subjects']],['101','201','203','301','801'])
        self.assertEqual([s['code'] for s in result['programs'][1]['subjects']],['101','201','203','301','802'])
        self.assertTrue(all(ref['quote'] in value['raw_text'] for p in result['programs'] for ref in p['exam_evidence']))

    def test_parent_exam_header_with_two_subheaders_is_not_misread_as_half_an_exam(self):
        rows=[['学院','专业代码及名称','学习方式','考试科目',''],
              [None,None,None,'统考科目','自命题科目'],
              ['001文学院','050101文艺学','全日制','①101思想政治理论\n②201英语一','③614评论写作\n④805文学基础']]
        result=self.parse(self.source(rows,{(0,0):(2,1),(0,1):(2,1),(0,2):(2,1)}))
        self.assertEqual(len(result['programs']),1,result['issues'])
        self.assertEqual([s['code'] for s in result['programs'][0]['subjects']],['101','201','614','805'])

    def test_professional_field_code_is_preserved_instead_of_parent_category_code(self):
        rows=[['学院（代码）','招生专业','专业领域','研究方向','学习方式','初试科目'],
              ['信息学院（001）','0854电子信息','085412网络与信息安全','不区分研究方向','全日制','①101思想政治理论\n②204英语（二）\n③302数学（二）\n④408计算机学科专业基础']]
        result=self.parse(self.source(rows))
        self.assertEqual(len(result['programs']),1,result['issues'])
        self.assertEqual(result['programs'][0]['code'],'085412')
        self.assertEqual(result['programs'][0]['department_code'],'001')

    def test_coupled_initial_plans_are_not_flattened_during_slot_conversion(self):
        rows=[['专业代码及名称','学习方式','初试统考科目','初试自命题科目'],
            ['081200计算机科学与技术','全日制','①101思想政治理论\n②201英语一','③301数学一或302数学二\n④801程序设计或802数据结构（须成套选择）']]
        result=self.parse(self.source(rows))
        self.assertEqual(result['programs'],[])
        self.assertTrue(result['issues'])

    def test_numeric_parenthesized_slots_are_normalized_without_forging_quotes(self):
        rows=[['专业代码及名称','学习方式','考试科目'],
              ['081200计算机科学与技术','全日制','(1) 101思想政治理论\n(2) 201英语一\n(3) 301数学一\n(4) 408计算机学科专业基础']]
        value=self.source(rows);result=self.parse(value)
        self.assertEqual(len(result['programs']),1,result['issues'])
        self.assertTrue(all(ref['quote'] in value['raw_text'] for ref in result['programs'][0]['exam_evidence']))

    def test_wrapped_major_mode_is_explicit_and_degree_years_do_not_hide_degree(self):
        rows=[['学院','专业代码及名称','学位类型及学制','初试科目'],
              ['001信息学院','081200计算机科学与技术（全日\n制）','学术学位\n3年','①101思想政治理论\n②201英语一\n③301数学一\n④408计算机学科专业基础']]
        value=self.source(rows);result=self.parse(value)
        self.assertEqual(len(result['programs']),1,result['issues'])
        self.assertEqual(result['programs'][0]['study_mode'],'全日制')
        self.assertEqual(result['programs'][0]['degree_type'],'学术学位')
        self.assertTrue(all(ref['quote'] in value['raw_text'] for ref in result['programs'][0]['evidence']))

    def test_faculty_contact_lines_are_not_part_of_its_name(self):
        rows=[['招生学院代码名称及联系方式','专业代码及名称','学习方式','初试科目'],
              ['012\n计算机科学与技术\n学院\n王老师\n13800000000','081200计算机科学与技术','全日制','①101思想政治理论\n②201英语一\n③301数学一\n④408计算机学科专业基础']]
        value=self.source(rows);result=self.parse(value)
        self.assertEqual(result['programs'][0]['department'],'计算机科学与技术学院')
        self.assertEqual(result['programs'][0]['department_code'],'012')
        self.assertTrue(all(ref['quote'] in value['raw_text'] for ref in result['programs'][0]['evidence']))

    def test_admissions_condition_does_not_supply_mode_from_major_column(self):
        rows=[['专业代码及名称','初试科目'],
              ['081200计算机科学与技术（仅招全日制本科毕业生）','①101思想政治理论\n②201英语一\n③301数学一\n④408计算机学科专业基础']]
        result=self.parse(self.source(rows))
        self.assertEqual(result['programs'],[])
        self.assertTrue(result['issues'])

    def test_faculty_banner_in_code_name_table_is_retained_as_original_heading(self):
        rows=[['专业代码\n专业名称','研究方向','初试科目'],
              ['学院：001 理学院 联系人：王老师','',''],
              ['070100\n数学','01基础数学','①101思想政治理论\n②201英语一\n③601数学分析\n④801高等代数']]
        value=self.source(rows);value['raw_text']=value['raw_text'].replace('硕士研究生招生专业目录','全日制硕士研究生招生专业目录')
        result=self.parse(value)
        self.assertEqual(len(result['programs']),1,result['issues'])
        self.assertEqual((result['programs'][0]['department_code'],result['programs'][0]['department']),('001','理学院'))
        self.assertTrue(all(ref['quote'] in value['raw_text'] for ref in result['programs'][0]['evidence']))

    def test_discipline_field_hierarchy_keeps_each_literal_registration_code(self):
        rows=[['学科','学科/专业领域','学习方式','初试科目'],
              ['0839网络空间安全（学术型学位）','不区分研究方向','全日制','①101思想政治理论\n②201英语一\n③301数学一\n④408计算机学科专业基础'],
              ['0854电子信息（专业学位）','085401新一代电子信息技术','全日制','①101思想政治理论\n②201英语一\n③301数学一\n④831信号与系统'],
              [None,'085402通信工程',None,None]]
        result=self.parse(self.source(rows,{(2,0):(2,1),(2,2):(2,1),(2,3):(2,1)}))
        self.assertEqual([p['code'] for p in result['programs']],['0839','085401','085402'],result['issues'])
        self.assertEqual(result['programs'][0]['direction'],'不区分研究方向')
        self.assertTrue(all(p['direction']=='' for p in result['programs'][1:]))
        self.assertEqual([p['degree_type'] for p in result['programs']],['学术学位','专业学位','专业学位'])

    def test_explicit_after_enrolment_direction_assignment_stays_program_level(self):
        description='本专业下设企业管理、会计学、物流与供应链管理3个研究方向，入学之后根据实际情况再分方向。'
        rows=[['专业代码及名称','研究方向','学习方式','初试科目'],
              ['120200工商管理学',description,'全日制','①101思想政治理论\n②201英语一\n③303数学三\n④813管理学']]
        value=self.source(rows);result=self.parse(value)
        self.assertEqual(len(result['programs']),1,result['issues'])
        record=result['programs'][0]
        self.assertEqual(record['direction'],'')
        self.assertEqual(record['direction_scope'],'program')
        self.assertIn(description,record['notes'])
        self.assertTrue(any(ref['quote']==description for ref in record['evidence']))

    def test_a_direction_count_alone_is_not_treated_as_post_enrolment_assignment(self):
        rows=[['专业代码及名称','研究方向','学习方式','初试科目'],
              ['120200工商管理学','本专业设置3个研究方向，请选择报考','全日制','①101思想政治理论\n②201英语一\n③303数学三\n④813管理学']]
        result=self.parse(self.source(rows))
        self.assertEqual(result['programs'],[])
        self.assertTrue(result['issues'])

    def test_headerless_next_page_uses_identical_geometry_for_metadata_schema_only(self):
        first=[['招生学院代码名称及联系方式','专业代码及名称','学习方式','初试科目','研究方向','备注'],
               ['001\n理学院\n王老师\n13800000000','070100数学','全日制','①101思想政治理论\n②201英语一\n③601数学分析\n④801高等代数','不区分研究方向','']]
        second=[['002\n商学院\n李老师\n13900000000','120200工商管理学（学术学位）','全日制','①101思想政治理论\n②201英语一\n③303数学三\n④813管理学','本专业下设企业管理、会计学、物流与供应链管理3个研究方向，入学之后根据实际情况再分方向。','初试考试《设计基础》科目，考生自备画笔。本专业遵循目录要求。']]
        value=self.source(first)
        value['pages'].append({**copy.deepcopy(value['pages'][0]),'page':2,'tables':[table(second)]})
        value['raw_text']+='\f'+'\n'.join(v for row in second for v in row if v)
        result=self.parse(value)
        self.assertEqual(len(result['programs']),2,result['issues'])
        self.assertEqual(result['programs'][1]['department'],'商学院')
        self.assertEqual(result['programs'][1]['source_locator']['row'],1)
        self.assertEqual(result['programs'][1]['direction_scope'],'program')
        value['pages'][1]['tables'][0]['bbox'][0]=10
        result=self.parse(value)
        self.assertEqual(len(result['programs']),1)
        self.assertTrue(result['issues'])

    def test_combined_major_cell_keeps_space_separated_zero_direction_code(self):
        rows=[['学院代码、名称、专业代码、名称及研究方向','初试科目'],
              ['002 文学院',''],
              ['045103 学科教学（语文）\n00 不区分研究方向','①101思想政治理论\n②204英语（二）\n③333教育综合\n④844教育研究方法']]
        value=self.source(rows);value['raw_text']=value['raw_text'].replace('硕士研究生招生专业目录','非全日制硕士研究生招生专业目录')
        result=self.parse(value)
        self.assertEqual(len(result['programs']),1,result['issues'])
        record=result['programs'][0]
        self.assertEqual((record['name'],record['direction']),('学科教学（语文）','00不区分研究方向'))
        self.assertEqual(record['id'],'u:2027:002:045103:00:非全日制')
        self.assertNotIn('direction_scope',record)
        self.assertTrue(all(ref['quote'] in value['raw_text'] for ref in record['evidence']))

    def test_actual_degree_chapter_title_covers_only_contiguous_matching_catalogue_pages(self):
        layout={'pages':[],'raw_text':''};raw=[]
        for number,code,degree in [(1,'081200','学术型'),(2,'083500',None),(3,None,None),
                                   (4,'080900',None),(5,'085400','专业型')]:
            title='样本大学2027年招收攻读硕士学位研究生'
            label='（'+degree+'）招生专业目录及简介' if degree else ''
            rows=[['专业代码及名称','学习方式','初试科目'],
                  [code+'测试专业' if code else '', '全日制','①101思想政治理论\n②201英语一\n③301数学一\n④408计算机学科专业基础']]
            physical=table(rows)
            physical['bbox'][1]+=150;physical['bbox'][3]+=150
            for row in physical['rows']:
                for box in row['cells']:
                    if box:box[1]+=150;box[3]+=150
            lines=[{'text':title,'y1':90},{'text':label,'y1':120}] if degree else []
            if code is None:lines=[{'text':'学院简介中介绍（专业型）招生专业目录及简介','y1':100}]
            lines=[{**line,'y0':line['y1']-15,'x0':0,'x1':600} for line in lines]
            layout['pages'].append({'page':number,'width':600,'height':800,'tables':[physical] if code else [],'lines':lines})
            raw.append('\n'.join([title if degree else '',label,*[v for row in rows for v in row]]) if code else lines[0]['text'])
        layout['raw_text']='\f'.join(raw)
        result=self.parse(layout)
        self.assertEqual(len(result['programs']),4,result['issues'])
        self.assertEqual([p['degree_type'] for p in result['programs']],['学术学位','学术学位',None,'专业学位'])
        continued=result['programs'][1]
        self.assertTrue(any(ref['page']==1 and '学术型' in ref['quote'] for ref in continued['evidence']))
        self.assertTrue(all(ref['quote'] in raw[ref['page']-1] for p in result['programs'] for ref in p['evidence']))


if __name__=='__main__':unittest.main()
