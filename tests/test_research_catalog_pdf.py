from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research_catalog_pdf import parse_live_catalog_pdf


_HEAD = ('学院、专业及研究方向','人数','考试科目','招生要求')
_TITLE = '示例大学2027年硕士研究生招生专业目录（全日制）'
_EXAMS = '①101思想政治理论②204英语（二）③333教育综合④847阅读与写作'
_COURSE = '045103 学科教学（专业学位）'
_UNIT = {'name':'示例大学','id':'12345','official_url':'https://yz.example.edu.cn/'}
_GROUP_HEAD = ('专业代码、名称及研究方向','学习方式及\n招生人数','考试科目代码及名称','备注')


def grouped_fixture(*, shift=0, header=_GROUP_HEAD, note='', cut=False, after='070200 物理学'):
    title = '示例大学 2027 年硕士研究生招生专业目录'
    college = '教师教育学院'
    first = ('①101 思想政治理论','②204 英语（二）','③333 教育综合','④847 阅读与写作')
    second = ('①101 思想政治理论','②201 英语（一）','③301 数学（一）','④805 普通物理')
    grid = table([header,('','','','')])
    grid.bbox = (0,10,400,160)
    grid.rows = [SimpleNamespace(cells=[(i*100,top,(i+1)*100,bottom) for i in range(4)])
                 for top,bottom in [(10,30),(30,160)]]
    boxes = grid.rows[1].cells
    columns = {boxes[0]:[(33,college),(45,_COURSE),(58,'01 语文教育'),(94,after)],
               boxes[1]:[(45,'全日制'),(58,'4 人'),(94,'非全日制'),(107,'2 人')],
               boxes[2]:[(45+shift,first[0]),(58,first[1]),(70,first[2]),(91 if cut else 82,first[3]),
                         *zip((94,107,120,133),second)],
               boxes[3]:[(45,note)] if note else []}
    target = SimpleNamespace(height=300,find_tables=lambda: [grid],crop=lambda box: SimpleNamespace(
        extract_text_lines=lambda: [{'top':y,'bottom':y+6,'text':value} for y,value in columns[box]]))
    source = pdf(grid,grid)
    open_pdf = source.open

    def open_grouped(path):
        result = open_pdf(path)
        result.pages = [target,target]
        return result

    header_line = '\n'.join(header)
    raw = '\n'.join([header_line,college,_COURSE+' 全日制 '+first[0]+' '+note,
                    '01 语文教育 4 人 '+first[1],*first[2:],after+' 非全日制 '+second[0],
                    '2 人 '+second[1],*second[2:]])
    return SimpleNamespace(open=open_grouped),'1\n'+title+'\n'+header_line+'\f2\n'+raw


def table(rows):
    cells = []
    for i,row in enumerate(rows):
        boxes = ([(0,i,40,i+1),None,None,None] if all(not c for c in row[1:]) else
                 [(j*10,i,(j+1)*10,i+1) for j in range(len(row))])
        cells.append(SimpleNamespace(cells=boxes))
    return SimpleNamespace(bbox=(0,0,40,len(rows)),rows=cells,extract=lambda **_: rows)


def pdf(*tables):
    pages = [SimpleNamespace(height=100,find_tables=lambda t=t: [t]) for t in tables]

    class Pdf:
        doc = SimpleNamespace(is_extractable=True)
        def __init__(self):
            self.pages = pages
        def __enter__(self):
            return self
        def __exit__(self,*_):
            pass

    return SimpleNamespace(open=lambda _: Pdf())


class LivePdfTests(unittest.TestCase):
    def _parse(self,extracted,raw_text,*,sha=True,unit=_UNIT):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'original.pdf'
            raw = b'%PDF-1.7\nfixture'
            path.write_bytes(raw)
            document = {'url':'https://yz.example.edu.cn/catalog.pdf','id':'original','year':2027,
                        'unit_id':_UNIT['id'],'sha256':hashlib.sha256(raw).hexdigest() if sha else 'wrong'}
            with patch('research_catalog_pdf._plumber',return_value=extracted), \
                 patch('research_catalog_pdf.extract_pdf_text',return_value=raw_text), \
                 patch.dict(sys.modules,{'pdfminer.psparser':SimpleNamespace(PSException=ValueError)}):
                return parse_live_catalog_pdf(path,unit,2027,document,major_code='045103')

    def test_same_grid_cell_carries_printed_mode_college_and_exam(self):
        rows = [_HEAD,('024 教师学院','','',''),(_COURSE+'\n01 语文教育','5',_EXAMS,'')]
        raw = '\n'.join([_TITLE,' '.join(_HEAD),'024 教师学院',_COURSE+' 5 '+_EXAMS,'01 语文教育'])
        [program] = self._parse(pdf(table(rows)),raw)['programs']
        self.assertEqual(program['id'],'12345:2027:024:045103:01:全日制')
        self.assertEqual([item['code'] for item in program['subjects']],['101','204','333','847'])
        self.assertEqual(program['source_locator']['page'],1)
        self.assertTrue(all(item['quote'] in raw for item in program['evidence'] + program['exam_evidence']))
        self.assertFalse(program['eligibility_evidence_complete'])

    def test_grouped_college_grid_binds_local_mode_and_exam_before_next_major(self):
        [program] = self._parse(*grouped_fixture())['programs']
        self.assertEqual(program['direction'],'01语文教育')
        self.assertEqual(program['study_mode'],'全日制')
        self.assertEqual(program['department'],'教师教育学院')
        self.assertEqual([item['code'] for item in program['subjects']],['101','204','333','847'])
        self.assertEqual(program['source_locator'],{'page':2,'table':1,'row':2,
                         'identity_box':[0,45,100,64],'exam_box':[200,45,300,88]})
        self.assertEqual({item['page'] for item in program['exam_evidence']},{2})
        self.assertEqual({item['page'] for item in program['evidence']},{1,2})

    def test_grouped_college_grid_declines_ambiguous_or_non_unified_columns(self):
        for args in ({'shift':4}, {'cut':True}, {'after':'07020? 物理学'}, {'note':'仅招收推免'},
                     {'header':(*_GROUP_HEAD[:2],'复试科目','备注')}):
            with self.subTest(args=args):
                self.assertEqual(self._parse(*grouped_fixture(**args))['programs'],[])
        grid, raw = grouped_fixture()
        for invalid in (raw.replace('2027 年','2026 年'), '旧材料\n'+raw,
                        raw+'\n其他大学2027年硕士研究生招生专业目录',
                        raw+'\n示例大学2026年硕士研究生招生目录'):
            self.assertEqual(self._parse(grid,invalid)['programs'],[])

    def test_merged_programme_cell_only_shares_exams_with_printed_count_alignment(self):
        codes = _COURSE+'\n045104 学科教学数学（专业学位）'
        raw = '\n'.join([_TITLE,' '.join(_HEAD),'024 教师学院',codes,_EXAMS])
        rows = [_HEAD,('024 教师学院','','',''),(codes,'5\n6',_EXAMS,'')]
        self.assertEqual(len(self._parse(pdf(table(rows)),raw)['programs']),1)
        ambiguous = [*rows[:2],(codes,'11',_EXAMS,'')]
        self.assertEqual(self._parse(pdf(table(ambiguous)),raw)['programs'],[])

    def test_explicit_mode_before_degree_and_hyphenated_department(self):
        title = '示例大学 2027 年全日制专业学位硕士研究生招生专业目录'
        faculty = '024-教师学院、语文教育研究院（联系电话见院系网页）'
        rows = [_HEAD,(faculty,'','',''),(_COURSE,'5',_EXAMS,'')]
        raw = '\n'.join([title,' '.join(_HEAD),faculty,_COURSE+' 5 '+_EXAMS])
        [program] = self._parse(pdf(table(rows)),raw)['programs']
        self.assertEqual(program['id'],'12345:2027:024:045103::全日制')
        self.assertEqual(program['department'],'教师学院、语文教育研究院')
        self.assertEqual([item['code'] for item in program['subjects']],['101','204','333','847'])
        self.assertEqual(self._parse(pdf(table(rows)),raw.replace(title,title+'（非全日制）'))['programs'],[])

    def test_adjacent_faculty_continuation_does_not_carry_previous_major(self):
        first = [_HEAD,('024 教师学院','','',''),('045104 学科教学数学\n02 数学史','5',_EXAMS,'')]
        last = [_HEAD,('03 数学教育\n'+_COURSE+'\n01 语文教育','5',_EXAMS,'')]
        raw1 = '\n'.join([_TITLE,' '.join(_HEAD),'024 教师学院','045104 学科教学数学',_EXAMS,'02 数学史'])
        raw2 = '\n'.join([' '.join(_HEAD),'03 数学教育',_COURSE+' 5 '+_EXAMS,'01 语文教育'])
        [program] = self._parse(pdf(table(first),table(last)),raw1+'\f'+raw2)['programs']
        self.assertEqual(program['direction'],'01语文教育')
        self.assertIn(('024 教师学院',1),[(item['quote'],item['page']) for item in program['evidence']])
        self.assertEqual(self._parse(pdf(table(first),table(last)),raw1+'\f'+raw2.replace(' 5 '+_EXAMS,'\n'+_EXAMS))['programs'],[])

    def test_separate_department_and_inline_study_mode(self):
        title = '2027年招收攻读硕士学位研究生专业目录'
        head = ('学院代码及名称', '专业代码、专业名称、研究方向代码、研究方向名称',
                '初试考试科目代码及科目名称', '复试考试科目代码及科目名称', '备注')
        direction = '01. (全日制) 语文教育'
        earlier = '045104 学科教学数学\n01. (全日制) 数学教育'
        raw = '\n'.join([title, ' '.join(head), '024 教师学院', earlier, _EXAMS, _COURSE, direction, _EXAMS])
        rows = [head, ('024 教师学院',earlier,_EXAMS,'复试',''),
                ('',_COURSE+'\n'+direction,_EXAMS,'复试','')]
        grid = table(rows)
        grid.rows[1].cells[0] = (0,1,10,3)
        grid.rows[2].cells[0] = None
        [program] = self._parse(pdf(grid),raw)['programs']
        self.assertEqual(program['id'],'12345:2027:024:045103:01:全日制')
        self.assertIn((direction,1),[(ref['quote'],ref['page']) for ref in program['evidence']])
        self.assertEqual([item['code'] for item in program['subjects']],['101','204','333','847'])
        self.assertEqual(self._parse(pdf(grid),raw.replace(title,'其他大学'+title))['programs'],[])
        self.assertEqual(self._parse(pdf(grid),raw.replace(title,title.replace('2027','2026')))['programs'],[])
        grid.rows[1].cells[0] = (0,1,10,2)
        self.assertEqual(self._parse(pdf(grid),raw)['programs'],[])

    def test_split_mode_column_and_physical_continuation(self):
        title = '示例大学2027年硕士研究生招生专业目录'
        head = ('专业代码及名称','研究方向','学习方式','拟招生人数','初试考试科目','备注')
        first = table([head,('024 教师学院','','','','',''),
                       ('045104 学科教学数学','01 数学教育','(1)全日制','2',_EXAMS,'')])
        first.bbox = (0,0,60,3)
        first.rows[1].cells = [(0,1,60,2),None,None,None,None,None]
        rows = [(_COURSE,'01 语文教育','(1)全日制','5',_EXAMS,''),
                ('','02 语文比较教育','(2)非全日制','3',_EXAMS,'')]
        second = table(rows)
        second.bbox = (0,0,60,2)
        second.rows[0].cells[0] = (0,0,10,2)
        second.rows[1].cells[0] = None
        raw = '\n'.join([title,' '.join(head),'024 教师学院','045104 学科教学数学','01 数学教育','(1)全日制',_EXAMS])
        raw += '\f' + '\n'.join([_COURSE,'01 语文教育','(1)全日制',_EXAMS,'02 语文比较教育','(2)非全日制',_EXAMS])
        programs = self._parse(pdf(first,second),raw)['programs']
        self.assertEqual({row['id'] for row in programs},{'12345:2027:024:045103:01:全日制',
                                                           '12345:2027:024:045103:02:非全日制'})
        self.assertTrue(all(row['source_locator']['page']==2 for row in programs))
        second.bbox = (0,35,60,75)
        self.assertEqual(self._parse(pdf(first,second),raw)['programs'],[])

    def test_fail_closed_for_wrong_owner_year_missing_mode_hash_and_grid(self):
        rows = [_HEAD,('024 教师学院','','',''),(_COURSE+'\n01 语文教育','5',_EXAMS,'')]
        raw = '\n'.join([_TITLE,' '.join(_HEAD),'024 教师学院',_COURSE+' 5 '+_EXAMS,'01 语文教育'])
        for changed in (raw.replace('示例大学','别的大学'),raw.replace('2027年','2026年'),
                        raw.replace('（全日制）',''),raw.replace(_EXAMS,'复试科目'),
                        raw.replace('考试科目','复试科目')):
            self.assertEqual(self._parse(pdf(table(rows)),changed)['programs'],[])
        self.assertEqual(self._parse(pdf(table(rows)),raw,sha=False)['programs'],[])
        bad = deepcopy(table(rows))
        bad.rows[2].cells[2] = (20,1,30,2)
        self.assertEqual(self._parse(pdf(bad),raw)['programs'],[])
