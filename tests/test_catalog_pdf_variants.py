import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from collectors.catalog_pdf_variants import parse_catalog_pdf_variants, _subject_text
from collectors.catalogs import compact


FIXTURES = Path(__file__).parent / 'fixtures/catalog_pdf'


def source(key):
    return json.loads((FIXTURES / (key + '-v2-excerpt.json')).read_text('utf8'))


class PdfVariantTests(unittest.TestCase):
    def parse(self, key, uid, name, publication_mode=None):
        unit = {'id': uid, 'name': name, 'province': '测试'}
        doc = {'id': 'd-' + uid, 'url': 'https://test.edu.cn/2027.pdf', 'year': 2027, 'unit_id': uid}
        # The real BL catalog relies on its separately verified official publication page.
        with patch('collectors.catalog_pdf_variants.verify_catalog_publication', return_value={
                'verified': True, 'documents': [], 'evidence': [], 'mode_evidence': [],
                'mode': publication_mode, 'mode_conflict': False, 'reason': ''}):
            result = parse_catalog_pdf_variants(source(key), unit, 2027, doc)
        for program in result['programs']:
            self.assertEqual(program['catalog_parser'], 'pdf_table_v2')
            self.assertTrue(program['requires_review'])
            self.assertFalse(program['eligibility_evidence_complete'])
            for ref in [*program['evidence'], *program['exam_evidence']]:
                self.assertIn(compact(ref['quote']), compact(source(key)['raw_text'].split('\f')[ref['page']-1]))
        return result

    def test_postfix_code_and_reference_topics_do_not_multiply_registration(self):
        result = self.parse('btbu', '367888', '北京工商大学')
        first = next(p for p in result['programs'] if p['code'] == '020200')
        self.assertEqual(first['name'], '应用经济学')
        self.assertEqual(first['department_code'], '101')
        self.assertEqual(first['department'], '经济学院')
        self.assertEqual(first['direction'], '00不区分研究方向')
        self.assertEqual(len(first['research_topics']), 6)
        self.assertEqual([s['code'] for s in first['subjects']], ['101','201','303','801'])
        self.assertEqual(len([p for p in result['programs'] if p['code'] == '020200']), 1)
        self.assertEqual(next(p['direction'] for p in result['programs'] if p['code']=='025400'),'00不区分研究方向')
        mba = [p for p in result['programs'] if p['code'] == '125100']
        self.assertEqual({p['study_mode'] for p in mba}, {'全日制','非全日制'})

    def test_numeric_initial_slots_exclude_tutors_retest_and_allow_or(self):
        result = self.parse('cugb', '367925', '中国地质大学(北京)')
        first = next(p for p in result['programs'] if p['code'] == '070500')
        self.assertEqual(first['department_code'], '301')
        self.assertEqual(first['study_mode'], '全日制')
        self.assertEqual([s['code'] for s in first['subjects']], ['101','201','610','809'])
        self.assertNotIn('冯海艳', first['direction'])
        geology = next(p for p in result['programs'] if p['code'] == '070900')
        foreign = [s['code'] for s in geology['subjects'] if s['exam_unit'] == 2]
        self.assertEqual(foreign, ['201','202','203'])
        self.assertTrue(all(s.get('choice_group') == 'exam-unit-2' for s in geology['subjects'] if s['exam_unit'] == 2))
        self.assertTrue(any('1.' in r['quote'] for r in geology['exam_evidence']))

    def test_chapter_mode_and_multiple_table_bands_do_not_drop_upper_page(self):
        result = self.parse('cupes', '367861', '首都体育学院')
        self.assertTrue(any(p['code'] == '040200' for p in result['programs']))
        self.assertTrue(any(p['code'] == '0403Z2' for p in result['programs']))
        self.assertTrue(any(p['code'] == '055200' for p in result['programs']))
        self.assertEqual(next(p['direction'] for p in result['programs'] if p['code']=='055200'),'01体育新闻')
        self.assertTrue(all(p['study_mode'] == '全日制' for p in result['programs']))
        self.assertTrue(any('全日制学术学位' in r['quote'] for p in result['programs'] if p['code']=='040200' for r in p['evidence']))

    def test_telephone_and_mba_department_never_become_major_code(self):
        xin = self.parse('368608', '368608', '新疆财经大学')
        self.assertTrue(xin['programs'])
        self.assertFalse(any(p['code']=='0991' for p in xin['programs']))
        first = next(p for p in xin['programs'] if p['code']=='020204')
        self.assertEqual(first['department_code'], '030')
        self.assertEqual(first['department'], '金融学院')
        economic = [p['direction'] for p in xin['programs'] if p['code']=='020208']
        self.assertIn('01社会统计',economic)
        self.assertIn('02经济统计',economic)
        self.assertFalse(any('01社会统计02' in d for d in economic))
        self.assertEqual(next(p['name'] for p in xin['programs'] if p['code']=='125603'),'工业工程与管理')
        self.assertEqual(next(p['name'] for p in xin['programs'] if p['code']=='035101'),'法律（非法学）')
        zjut = self.parse('368218', '368218', '浙江工业大学')
        self.assertFalse(any(p['code']=='099MBA' for p in zjut['programs']))
        mba = next(p for p in zjut['programs'] if p['code']=='125100')
        self.assertEqual(mba['department'], 'MBA教育中心')
        self.assertEqual(mba['department_code'], '099')

    def test_combined_pdf_line_cannot_leak_neighbor_major_into_department(self):
        result = self.parse('678581', '678581', '贵州民族大学')
        social = next(p for p in result['programs'] if p['code']=='035200')
        ethnic = next(p for p in result['programs'] if p['code']=='030400')
        self.assertEqual((social['department_code'], social['department']), ('021','社会学院'))
        self.assertEqual((ethnic['department_code'], ethnic['department']), ('022','民族学与历史学学院'))
        self.assertEqual([s['code'] for s in social['subjects']], ['101','204','331','437'])
        self.assertEqual([s['code'] for s in ethnic['subjects']], ['101','201','613','813'])
        self.assertEqual({p['direction'] for p in result['programs'] if p['code']=='050100'},
                         {'01语言学研究','02文学研究','03中国少数民族语言文学研究'})

    def test_split_college_labels_keep_same_major_directions_distinct(self):
        result = self.parse('367895', '367895', '北京林业大学')
        electronic = [p for p in result['programs'] if p['code']=='085400' and p['direction'].startswith('02')]
        self.assertEqual({p['department_code'] for p in electronic}, {'003','004'})
        self.assertEqual(len({p['id'] for p in electronic}), 2)
        signatures={p['department_code']:[s['code'] for s in p['subjects']] for p in electronic}
        self.assertEqual(signatures['003'],['101','201','301','808'])
        self.assertEqual(signatures['004'],['101','204','302','408'])
        material = next(p for p in result['programs'] if p['code']=='085600')
        self.assertEqual(material['department'], '材料科学与技术学院')

    def test_unknown_grid_and_invalid_scope_fail_closed_without_mutation(self):
        data = source('btbu'); before = copy.deepcopy(data)
        unit = {'id':'367888','name':'北京工商大学'}
        doc = {'id':'d','url':'https://test.edu.cn/a.pdf','year':2027,'unit_id':'367888'}
        wrong = parse_catalog_pdf_variants(data, unit, 2028, doc)
        self.assertFalse(wrong['programs']); self.assertTrue(wrong['issues'])
        self.assertEqual(data, before)
        data = {**data, 'pages': [{**p,'grid':[]} for p in data['pages']]}
        result = parse_catalog_pdf_variants(data, unit, 2027, doc)
        self.assertFalse(result['programs']); self.assertTrue(result['issues'])

    def test_numeric_normalization_only_converts_explicit_slots_and_code_brackets(self):
        self.assertEqual(_subject_text('1. (101)思想政治理论2.(201)英语（一）或(202)俄语'), '①101思想政治理论②201英语（一）或202俄语')
        self.assertEqual(_subject_text('①101思想政治理论②201英语（一）'), '①101思想政治理论②201英语（一）')

    def test_wrapped_postfix_major_name_uses_whole_original_cell(self):
        result=self.parse('btbu-wrap','367888','北京工商大学')
        majors=[p for p in result['programs'] if p['code']=='085606']
        self.assertEqual(len(majors),2)
        self.assertEqual({p['department_code'] for p in majors},{'104','111'})
        self.assertEqual({p['name'] for p in majors},{'轻化工程（含皮革、纸张、织物加工等）'})

    def test_unlabeled_merged_major_with_two_initial_paths_is_partial(self):
        result=self.parse('367885','367885','北京中医药大学','全日制')
        self.assertFalse(any(p['code']=='100601' and p['department_code']=='102' for p in result['programs']))
        self.assertTrue(any('100601' in i and '多套未命名初试' in i for i in result['issues']))

    def test_next_college_label_never_becomes_previous_direction(self):
        result=self.parse('368233','368233','淮北师范大学','非全日制')
        self.assertTrue(result['programs'])
        self.assertFalse(any('学院' in p['direction'] for p in result['programs']))
        self.assertEqual(next(p['direction'] for p in result['programs'] if p['code']=='045103'),'00不区分研究方向')

    def test_unrecognized_direction_only_continuation_page_is_not_silent(self):
        data=source('btbu');last=max(p['page'] for p in data['pages'])+1
        template=data['pages'][0]['lines'][0]
        make=lambda text,x,y:{**template,'text':text,'x0':x,'x1':x+100,'y0':y,'y1':y+12,
                             'words':[{'text':text,'x0':x,'x1':x+100,'y0':y,'y1':y+12}]}
        rows=[make('04 继续研究方向',50,100),make('①101思想政治理论',500,100)]
        data={**data,'pages':[*data['pages'],{'page':last,'width':1190,'height':842,'lines':rows,
                    'words':[w for l in rows for w in l['words']],'grid':[]}],
              'raw_text':data['raw_text']+'04 继续研究方向\n①101思想政治理论\f'}
        result=parse_catalog_pdf_variants(data,{'id':'367888','name':'北京工商大学'},2027,
                 {'id':'d','unit_id':'367888','year':2027,'url':'https://test.edu.cn/a.pdf'})
        self.assertTrue(any('继续页' in issue for issue in result['issues']))


if __name__ == '__main__':
    unittest.main()
