import copy
from pathlib import Path
import unittest

from collectors.catalog_pdf import parse_catalog_pdf, sniff_catalog_pdf
from service_verification import canonical, exam_groups


class RowCatalogPdfTests(unittest.TestCase):
    def setUp(self):
        self.unit = {'id': '2125721162', 'name': '齐齐哈尔医学院', 'province': '黑龙江'}
        self.document = {'id': 'qmu-source', 'unit_id': self.unit['id'], 'year': 2027,
                         'url': 'https://yjs.qmu.edu.cn/catalog.pdf'}
        self.text = (Path(__file__).parent / 'fixtures/catalog_pdf/qmu-2027.txt').read_text('utf8')

    def parse(self, text=None, unit=None, year=2027):
        return parse_catalog_pdf(self.text if text is None else text, unit or self.unit, year, self.document)

    def test_real_complete_medical_catalog_and_page_exact_evidence(self):
        before = copy.deepcopy(self.unit)
        result = self.parse()
        self.assertEqual(sniff_catalog_pdf(self.text), 'row-v1')
        self.assertEqual(result['expected_records'], 48)
        self.assertEqual(len(result['programs']), 48)
        self.assertEqual(result['issues'], [])
        self.assertEqual(len({p['id'] for p in result['programs']}), 48)
        self.assertEqual(len({p['department_code'] for p in result['programs']}), 9)
        self.assertEqual(self.unit, before)
        pages = self.text.rstrip('\f\r\n ').split('\f')
        for program in result['programs']:
            self.assertTrue(program['requires_review'])
            self.assertFalse(program['eligibility_evidence_complete'])
            self.assertEqual(program['study_mode'], '全日制')
            original = canonical(' '.join(item['quote'] for item in program['evidence']))
            self.assertIn(program['code'], original)
            self.assertIn(canonical(program['name']), original)
            self.assertIn(len({s['exam_unit'] for s in program['subjects']}), (3, 4))
            for item in [*program['evidence'], program['exam_evidence']]:
                self.assertIn(canonical(item['quote']), canonical(pages[item['page'] - 1]))
            self.assertEqual(set(exam_groups(program['exam_evidence']['quote'])), {s['exam_unit'] for s in program['subjects']})
        biology = next(p for p in result['programs'] if p['code'] == '071000')
        self.assertEqual(biology['department'], '基础医学院')
        self.assertEqual(biology['subjects'][-1]['code'], '809')
        self.assertEqual(biology['subjects'][-1]['scope'], self.unit['id'])
        self.assertEqual(biology['planned_enrollment']['count'], 18)

    def test_or_choices_preserve_slot_and_school_identity(self):
        text = self.text.replace('②201英语（一）③716', '②201英语（一）或202俄语③716', 1)
        result = self.parse(text)
        self.assertEqual(result['issues'], [])
        alternatives = [s for s in result['programs'][0]['subjects'] if s['exam_unit'] == 2]
        self.assertEqual([s['code'] for s in alternatives], ['201', '202'])
        self.assertEqual(len({s['choice_group'] for s in alternatives}), 1)

    def test_missing_subject_or_mode_is_not_silently_complete(self):
        for old, new in [('③716医学综合', '③716'), ('21全国统考 8', '21全国统考 未公开')]:
            with self.subTest(new=new):
                result = self.parse(self.text.replace(old, new, 1))
                self.assertEqual(result['expected_records'], 48)
                self.assertTrue(result['issues'])

    def test_wrong_year_school_and_partial_pages_fail_closed(self):
        self.assertTrue(self.parse(year=2028)['issues'])
        self.assertTrue(self.parse(unit={**self.unit, 'name': '其他大学'})['issues'])
        self.assertTrue(self.parse(self.text.split('\f')[0])['issues'])

    def test_duplicate_direction_and_mode_number_are_reported(self):
        first = next(line for line in self.text.splitlines() if '01病理学技术' in line)
        self.assertTrue(self.parse(self.text.replace(first, first + '\n' + first, 1))['issues'])
        self.assertTrue(self.parse(self.text.replace('(1)全日制', '(2)全日制', 1))['issues'])

    def test_unknown_layout_cannot_be_mistaken_for_a_full_catalog(self):
        self.assertIsNone(sniff_catalog_pdf('2027年招生简章 专业代码 101 思想政治理论'))
        with self.assertRaises(ValueError):
            parse_catalog_pdf(self.text, self.unit, 2027, self.document, format='guess')


if __name__ == '__main__':
    unittest.main()
