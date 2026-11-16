from types import SimpleNamespace as Box
import unittest

from research_catalog_pdf_brochure import parse_brochure_catalog


_UNIT = {'id':'456','name':'示例大学'}
_DOC = {'id':'brochure','url':'https://www.example.edu.cn/brochure.pdf'}
_TITLE = '示例大学农学院 2027 年硕士研究生招生简章'
_SECTION = '三、招生专业目录'
_HEADER = ('学院代码及\n名称','专业','研究方向','学习方式','初试科目','复试科目')
_EXAM = '①101思想政治理论\n②201英语（一）\n③624作物生理学\n④821农学概论（一）'
_NEIGHBOR_EXAM = '①101思想政治理论\n②201英语（一）\n③615植物学\n④414植物生理学与生物化学'


def _brochure(*, title=_TITLE, chapter=_SECTION, exam_bottom=60,
              other_proof=True, header=_HEADER, direction='02 遗传育种', extra_chapter=False,
              major_note='F082 复试'):
    data = [header,
            ('001 农学院','090100 作物学\n（学术学位）','01 作物栽培学与\n耕作学','全日制',_EXAM,major_note),
            ('','',direction,'','',''),
            ('','090900 草学\n（学术学位）','0 不区分研究方向','全日制',_NEIGHBOR_EXAM,'F077 复试')]
    positions = [((0,10),(0,10),(0,10),(0,10),(0,10),(0,10)),
                 ((10,80),(10,60),(10,35),(10,60),(10,exam_bottom),(10,60)),
                 (None,None,(35,60),None,None,None),
                 (None,(60,80),(60,80),(60,80),(60,80),(60,80))]
    grid = Box(bbox=(0,0,120,80),extract=lambda **_: data,
               rows=[Box(cells=[(col*20,pos[0],(col+1)*20,pos[1]) if pos else None
                                for col,pos in enumerate(row)]) for row in positions])
    headings = [{'text':chapter,'top':-20,'bottom':-14}]
    if extra_chapter:
        headings.append({'text':'四、报名条件','top':-8,'bottom':-2})
    page = Box(find_tables=lambda:[grid],extract_text_lines=lambda:headings)
    raw = [title,chapter,*header]
    if extra_chapter:
        raw.append('四、报名条件')
    raw.extend(('001 农学院 090100 作物学','（学术学位）','01 作物栽培学与','耕作学',
                '全日制 ①101 思想政治理论','②201 英语（一）','③624 作物生理学',
                '④821 农学概论（一）',direction if other_proof else '有别的打印方向',
                '090900 草学','（学术学位）','0 不区分研究方向','全日制',_NEIGHBOR_EXAM))
    return Box(pages=[Box(find_tables=lambda:[]),page]),[title,'\n'.join(raw[1:])]


class BrochureCatalogTests(unittest.TestCase):
    def parsed(self, code='090100', **options):
        pdf, raw = _brochure(**options)
        return parse_brochure_catalog(pdf,raw,_UNIT,2027,_DOC,code), raw

    def test_follows_only_a_physical_rowspan_for_printed_directions(self):
        programs, raw = self.parsed()
        self.assertEqual([p['direction'] for p in programs],['01作物栽培学与耕作学','02遗传育种'])
        for row, program in enumerate(programs,2):
            self.assertEqual(program['source_locator']['row'],row)
            self.assertEqual(program['source_locator']['page'],2)
            self.assertEqual(program['study_mode'],'全日制')
            self.assertEqual([s['code'] for s in program['subjects']],['101','201','624','821'])
            self.assertEqual([s['name'] for s in program['subjects']],
                             ['思想政治理论','英语（一）','作物生理学','农学概论（一）'])
            for ref in program['evidence']+program['exam_evidence']:
                self.assertIn(ref['quote'], raw[ref['page']-1])

    def test_does_not_relabel_neighbor_exam_codes(self):
        [program], _ = self.parsed(code='090900')
        self.assertEqual([s['code'] for s in program['subjects']],['101','201','615','414'])
        self.assertEqual(program['direction'],'不区分研究方向')
        self.assertEqual(self.parsed(code='095131')[0],[])

    def test_rejects_wrong_owner_year_department_and_section(self):
        for options in ({'title':_TITLE.replace('示例', '别的')}, {'title':_TITLE.replace('2027','2026')},
                        {'title':_TITLE.replace('农学院','草学院')},
                        {'title':_TITLE.replace('招生简章','推荐免试招生简章')},
                        {'chapter':'三、部分参考科目'}, {'extra_chapter':True},
                        {'header':(*_HEADER[:4],'复试科目','其他')}):
            with self.subTest(options=options):
                self.assertEqual(self.parsed(**options)[0],[])

    def test_missing_direction_proof_and_mismatched_merged_cell_do_not_transfer_exams(self):
        self.assertEqual([row['direction'] for row in self.parsed(other_proof=False)[0]],
                         ['01作物栽培学与耕作学'])
        self.assertEqual(self.parsed(exam_bottom=35)[0],[])

    def test_duplicate_program_extends_no_trust(self):
        programs,_ = self.parsed(direction='01 作物栽培学与耕作学')
        self.assertEqual(programs,[])

    def test_merged_special_admission_restriction_applies_to_every_direction(self):
        self.assertEqual(self.parsed(major_note='仅招收推免')[0],[])
