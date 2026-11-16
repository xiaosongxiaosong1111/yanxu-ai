"""Chapter-scoped QAU catalogue modes and physical course-cell regression tests."""
import unittest

from collectors.catalog_qau_pdf import parse_qau_layout
from tests.test_catalog_pdf_national import table


UNIT = {'id': 'u', 'name': '样本大学'}
DOC = {'id': 'd', 'unit_id':'u', 'year':2027, 'url':'https://sample.edu.cn/catalog.html'}
HEADER = ['招生专业代码、名称及研究方向', '拟招生计划数','初试科目','备注']
PRO = ['招生领域代码、名称及研究方向', *HEADER[1:]]
DEP = ['101 农学院', '联系人：老师；电话：0000-12345678','','']
EXAM = '①101 思想政治理论\n②201 英语（一）\n③302 数学（二）\n④815 机械原理'
MAJOR = ['082800 农业工程\n01 农业机械化工程\n02 农业电气化与自动化','12',EXAM,'复试测试']
CHAPTERS = [('全日制','学术学位'),('全日制','专业学位'),('非全日制','专业学位')]


def page(number, rows, heading=None, left=0):
    grid=table(rows)
    grid['bbox']=[left,100,400+left,100+30*len(rows)]
    for raw_row in grid['rows']:
        for cell in raw_row['cells']:
            if cell:
                cell[0]+=left;cell[2]+=left;cell[1]+=100;cell[3]+=100
    lines=[{'text':'样本大学','y1':30},{'text':heading,'y1':60}] if heading else []
    raw='\n'.join([*(line['text'] for line in lines),*(cell for row in rows for cell in row if cell)])
    return {'page':number,'height':800,'lines':lines,'tables':[grid]},raw


def dataset(first=MAJOR, more=None, false_heading=None):
    end=2 if more else 1
    cover=['样本大学','2027 年硕士研究生招生专业目录']
    chapters=[]
    for offset,(mode,degree) in enumerate(CHAPTERS):
        start=(1,end+1,end+2)[offset]
        stop=(end,end+1,end+2)[offset]
        title=f'2027年{mode}{degree}硕士研究生招生专业目录'
        cover.append(f'{"一二三"[offset]}、样本大学{title}{start:02d}-{stop:02d}页')
        direction=first if offset==0 else [MAJOR[0].replace('082800','085900',1),*MAJOR[1:]]
        heading=false_heading if offset==0 and false_heading else title
        chapters.append(page(start+1,[HEADER if offset==0 else PRO,DEP,direction],heading))
        if offset==0 and more:
            chapters.append(more)
    physical,raw=zip(*chapters)
    return {'raw_text':'\f'.join(['\n'.join(cover),*raw]),
            'pages':[{'page':1,'tables':[],'lines':[],'height':800},*physical]}


class QauPdfTests(unittest.TestCase):
    def test_physical_three_chapters_bind_mode_degree_and_course_evidence(self):
        result=parse_qau_layout(dataset(),UNIT,2027,DOC)
        self.assertEqual(result['issues'],[])
        self.assertEqual(len(result['programs']),6)
        self.assertEqual({(p['study_mode'],p['degree_type']) for p in result['programs']},set(CHAPTERS))
        self.assertTrue(all(p['url']==DOC['url'] for p in result['programs'][0]['evidence']))
        self.assertTrue(all(not p['eligibility_evidence_complete'] for p in result['programs']))

    def test_exact_direction_scoped_course_and_special_direction_hold(self):
        exam='①101 思想政治理论\n②201 英语（一）\n③302 数学（二）\n④01 方向和 03 方向：815 机械原理\n02 方向：804 电路'
        row=[MAJOR[0]+'\n03 环境工程（工程硕博专项）',MAJOR[1],exam,MAJOR[3]]
        result=parse_qau_layout(dataset(first=row),UNIT,2027,DOC)
        self.assertEqual(result['issues'],[])
        first=result['programs'][:2]
        self.assertEqual({p['direction'][:2]:p['subjects'][-1]['code'] for p in first},{'01':'815','02':'804'})
        self.assertEqual(len(result['excluded_source_rows']),1)
        self.assertTrue(all('专项' not in p['direction'] for p in result['programs']))

    def test_same_grid_can_inherit_trailing_department_but_unbound_grid_is_held(self):
        other=['082900 控制科学与工程\n01 自动化', '5',EXAM,'']
        allowed=parse_qau_layout(dataset(more=page(3,[other])),UNIT,2027,DOC)
        self.assertEqual(allowed['issues'],[])
        continued=next(p for p in allowed['programs'] if p['code']=='082900')
        self.assertEqual(continued['department_code'],'101')
        self.assertEqual({ref['page'] for ref in continued['exam_evidence']},{3})
        blocked=parse_qau_layout(dataset(more=page(3,[other],left=10)),UNIT,2027,DOC)
        self.assertTrue(blocked['issues'])
        self.assertFalse(any(p['code']=='082900' for p in blocked['programs']))

    def test_subjects_cannot_be_borrowed_from_another_page(self):
        other=['082900 控制科学与工程\n01 自动化','5',EXAM,'']
        physical,raw=page(3,[other])
        result=parse_qau_layout(dataset(more=(physical,raw.replace(EXAM,''))),UNIT,2027,DOC)
        self.assertTrue(result['issues'])
        self.assertFalse(any(p['code']=='082900' for p in result['programs']))

    def test_title_scope_conflict_or_nonexclusive_direction_claim_holds(self):
        bad=dataset(false_heading='2027年非全日制学术学位硕士研究生招生专业目录')
        self.assertFalse(parse_qau_layout(bad,UNIT,2027,DOC)['programs'])
        malformed='①101 思想政治理论\n②201 英语（一）\n③302 数学（二）\n④01 方向：815 机械原理'
        row=[*MAJOR[:2],malformed,MAJOR[3]]
        output=parse_qau_layout(dataset(first=row),UNIT,2027,DOC)
        self.assertTrue(output['issues'])
        self.assertFalse(any(p['degree_type']=='学术学位' for p in output['programs']))
