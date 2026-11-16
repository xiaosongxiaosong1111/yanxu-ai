from types import SimpleNamespace
from unittest import TestCase

from research_catalog_pdf_unruled import parse_unruled_catalog


UNIT = {'name':'示例大学','id':'12345'}
DOCUMENT = {'id':'catalog','url':'https://yz.example.edu.cn/catalog.pdf'}
TITLE = '示例大学2027年硕士研究生招生专业目录'
HEAD = ['学科、专业名称（代码）、研究方向','指导教师','指导教师职称','考试科目']
EXAM_TOP = '①101 思想政治理论②201 英语'
EXAM_BOTTOM = '一③301 数学一④806 材料力学'
FACULTY = '002 理学院'
OTHER = '学科（专业）：物理学（070200）（学术型）'
MAJOR = '学科（专业）：力学（080100）（学术型）'
DIRECTION = '01 能源装备动力学'
SECOND = '02 数值计算'


def page(identity, exams, headers=HEAD):
    boxes = [(column*100,20,(column+1)*100,150) for column in range(4)]
    table = SimpleNamespace(rows=[None,SimpleNamespace(cells=boxes)],extract=lambda **_: [headers,['']*4])
    lines = {boxes[0]:identity,boxes[3]:exams}

    def crop(box):
        return SimpleNamespace(extract_text_lines=lambda: [
            {'top':top,'bottom':top+5,'text':text} for top,text in lines[box]])
    return SimpleNamespace(find_tables=lambda: [table],crop=crop)


class CatalogMergedDirectionTests(TestCase):
    def parse(self, *, delta=0, title=TITLE, first_headers=HEAD, foreign_heading=''):
        first = page([(23,FACULTY),(30,OTHER),(40,'01 实验物理')], [], first_headers)
        unexpected = [(62,foreign_heading)] if foreign_heading else []
        second = page([(22,OTHER),(33,'01 应用物理'),(55,MAJOR),*unexpected,(70,DIRECTION),(98,SECOND)],
                      [(33,'①101思想政治理论②201英语一③301数学一④805普通物理'),
                       (70+delta,EXAM_TOP),(78+delta,EXAM_BOTTOM),(98,'同上')])
        first_raw = '\n'.join([title,*first_headers,FACULTY,OTHER,'01 实验物理'])
        second_raw = '\n'.join([title,*HEAD,OTHER,'01 应用物理','①101思想政治理论②201英语一③301数学一④805普通物理',
                                MAJOR,foreign_heading,DIRECTION+' 教师名字 '+EXAM_TOP,EXAM_BOTTOM,SECOND+' 同上'])
        pdf = SimpleNamespace(pages=[first,second])
        return parse_unruled_catalog(pdf,[first_raw,second_raw],UNIT,2027,DOCUMENT,'080100')

    def test_replays_only_directly_aligned_exams_and_preserves_unknown_mode(self):
        [row] = self.parse()
        self.assertEqual(row['id'],'12345:2027:002:080100:01:')
        self.assertIsNone(row['study_mode'])
        self.assertEqual([exam['code'] for exam in row['subjects']],['101','201','301','806'])
        self.assertEqual(row['degree_type'],'学术学位')
        self.assertEqual([ref['page'] for ref in row['exam_evidence']],[2,2])
        self.assertIn((FACULTY,1),[(ref['quote'],ref['page']) for ref in row['evidence']])
        self.assertFalse(any('教师名字' in ref['quote'] for ref in row['evidence'] + row['exam_evidence']))

    def test_rejects_unaligned_exam_wrong_cohort_and_missing_adjacent_geometry(self):
        self.assertEqual(self.parse(delta=3),[])
        self.assertEqual(self.parse(foreign_heading='特殊招生类别'),[])
        self.assertEqual(self.parse(title=TITLE.replace('2027','2026')),[])
        self.assertEqual(self.parse(first_headers=[*HEAD[:3],'复试科目']),[])
