import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collectors.catalog_pdf_department_blocks import (matches_jxau_pdf_layout, matches_nchu_pdf_layout,
                                                       parse_catalog_pdf_department_blocks,
                                                       parse_jxau_layout, parse_nchu_layout)


UNIT = {'id': '368265', 'unit_code': '10406', 'name': '南昌航空大学'}
DOC = {'id': 'source', 'unit_id': UNIT['id'], 'year': 2027, 'url': 'https://yjs.nchu.edu.cn/catalog.pdf'}
SUMMARY = ['学院代码', '学院名称', '专业代码', '专业名称（加★专业具有博士学位授予权）', '学位类型', '学习形式']
DETAIL = ['学院、学科（类别）（专业（领域））、拟招生人数及研究方向', '初试科目',
          '初试自命题科目参考书', '复试、同等学力加试科目及参考书']
FACULTY = '001 材料科学与工程学院 联系人：老师，联系电话：0791-83863516'
MAJOR = '080500 材料科学与工程（学术学位）\n拟招收全日制硕士研究生81 名'
EXAM = '①101 思想政治理论\n②201 英语（一）\n③302 数学（二）\n④812 材料科学基础'
DIRECTIONS = '01 材料学\n02 材料物理与化学'


def row(values, top, *, merged=False, exam_bottom=None):
    cells = [[10 * col, top, 10 * (col + 1), top + 10] for col in range(len(values))]
    if merged:
        cells = [[0, top, 10 * len(values), top + 10], *[None] * (len(values) - 1)]
    elif exam_bottom:
        cells[1][3] = exam_bottom
    elif len(values) == 4 and not values[1]:
        cells[1] = None
    return {'texts': values, 'cells': cells}


def page(number, rows, heading=''):
    table = {'bbox': [0, 0, 10 * len(rows[0]['texts']), 10 * len(rows)], 'rows': rows}
    raw = '\n'.join([heading, *[value for line in rows for value in line['texts'] if value]])
    return {'page': number, 'tables': [table], 'native': None}, raw


def fixture():
    return [page(1, [row(SUMMARY, 0), row(['001', '材料科学与工程学院', '080500', '材料科学与工程', '学术学位', '全日制'], 10)],
                 '南昌航空大学 2027 年硕士研究生招生专业目录\n单位代码：10406 地址：江西'),
            page(2, [row(DETAIL, 0), row([FACULTY, '', '', ''], 10, merged=True),
                     row([MAJOR, EXAM, '', ''], 20, exam_bottom=40), row([DIRECTIONS, '', '', ''], 30)])]


def replay(parts):
    layout = {'pages': [page for page, _ in parts], 'raw_text': '\f'.join(raw for _, raw in parts)}
    return parse_nchu_layout(layout, UNIT, 2027, DOC)


class NchuDepartmentBlocksTests(unittest.TestCase):
    def test_actual_merged_exam_cell_binds_two_directions_and_original_locators(self):
        result = replay(fixture())
        self.assertEqual(result['issues'], [])
        self.assertEqual([p['direction'] for p in result['programs']], ['01材料学', '02材料物理与化学'])
        for program in result['programs']:
            self.assertEqual((program['department_code'], program['study_mode'], program['degree_type']),
                             ('001', '全日制', '学术学位'))
            self.assertEqual([s['code'] for s in program['subjects']], ['101', '201', '302', '812'])
            self.assertEqual(program['source_locator']['exam_row'], 3)
            self.assertEqual(program['source_locator']['direction_row'], 4)
            self.assertTrue(program['requires_review'])

    def test_adjacent_but_unmerged_exam_does_not_create_a_candidate(self):
        parts = fixture()
        parts[1][0]['tables'][0]['rows'][2]['cells'][1][3] = 30
        self.assertEqual(replay(parts)['programs'], [])

    def test_cross_page_department_needs_a_matching_summary_identity(self):
        parts = fixture()
        other = MAJOR.replace('080500', '080600')
        continuation = page(3, [row(DETAIL, 0), row([other, EXAM, '', ''], 10, exam_bottom=30),
                               row([DIRECTIONS, '', '', ''], 20)])
        result = replay([*parts, continuation])
        self.assertEqual({p['code'] for p in result['programs']}, {'080500'})
        self.assertTrue(any('第3页' in issue for issue in result['issues']))

    def test_duplicate_direction_id_is_held_instead_of_silently_picked(self):
        parts = fixture()
        second = copy.deepcopy(parts[1][0])
        second['page'] = 3
        result = replay([*parts, (second, parts[1][1])])
        self.assertEqual(result['programs'], [])
        self.assertTrue(result['rejected_source_rows'])

    def test_department_absent_from_anchored_summary_cannot_claim_the_source(self):
        parts = fixture()
        other = FACULTY.replace('001 材料科学与工程学院', '002 另一大学材料学院')
        parts[1] = page(2, [row(DETAIL, 0), row([other, '', '', ''], 10, merged=True),
                           row([MAJOR, EXAM, '', ''], 20, exam_bottom=40), row([DIRECTIONS, '', '', ''], 30)])
        self.assertEqual(replay(parts)['programs'], [])

    def test_same_coded_faculty_may_print_an_internal_parenthetical_alias(self):
        parts = fixture()
        rows = parts[1][0]['tables'][0]['rows']
        internal = FACULTY.replace(' 联系人', '（材料工程学院） 联系人')
        parts[1] = page(2, [rows[0], row([internal, '', '', ''], 10, merged=True), *rows[2:]])
        self.assertEqual([p['department'] for p in replay(parts)['programs']],
                         ['材料科学与工程学院(材料工程学院)'] * 2)
        external = internal.replace('材料工程学院', '其他大学工程学院')
        parts[1] = page(2, [rows[0], row([external, '', '', ''], 10, merged=True), *rows[2:]])
        self.assertEqual(replay(parts)['programs'], [])

    def test_unreplayable_source_and_official_publication_are_held(self):
        unit = {**UNIT, 'official_url': 'https://yjs.nchu.edu.cn/'}
        raw = b'%PDF-1.7 fixture'
        document = {**DOC, 'sha256': hashlib.sha256(raw).hexdigest()}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.pdf'
            source.write_bytes(raw)
            self.assertIn('官方原件', parse_catalog_pdf_department_blocks(source, unit, 2026, document)['issues'][0])
            self.assertIn('摘要', parse_catalog_pdf_department_blocks(source, unit, 2027, {**document, 'sha256': ''})['issues'][0])
            self.assertIn('发布页', parse_catalog_pdf_department_blocks(source, unit, 2027, document)['issues'][0])
            with patch('collectors.catalog_pdf_department_blocks.verify_catalog_publication',
                       return_value={'verified': True, 'documents': []}):
                self.assertIn('重放失败', parse_catalog_pdf_department_blocks(source, unit, 2027, document)['issues'][0])
        self.assertFalse(matches_nchu_pdf_layout(source, '另一单位目录', unit, 2027))
        self.assertFalse(matches_jxau_pdf_layout(source, '另一单位目录', JXAU, 2027))

    def test_geometry_does_not_substitute_for_an_exact_exam_quote(self):
        parts = fixture()
        parts[1] = (parts[1][0], parts[1][1].replace('材料科学基础', '其他科目'))
        result = replay(parts)
        self.assertEqual(result['programs'], [])
        self.assertIn('逐字核对', result['issues'][0])


JXAU = {'id': '368269', 'unit_code': '10410', 'name': '江西农业大学'}
JXAU_SUMMARY = ['学院代码', '学院', '专业代码', '专业（领域、类别）名称', '所授学位', '学制', '学习形式']
ACADEMIC = ['专业代码、名称及研究方向', '指导教师', '初试科目', '复试及同等学力加试科目']
PROFESSIONAL = ['专业学位类别代码及名称', '专业领域代码、名称及研究方向', '初试科目',
                '复试及同等学力加试科目', '学习形式']
JXAU_FACULTY = '学院代码、名称及联系电话：001 农学院（0791-83813587）拟招生人数：80 人左右'


def jxau_replay(academic_title='江西农业大学 2027 年学术型硕士研究生招生专业目录\n学术型专业仅招收全日制硕士研究生。',
                professional_title='江西农业大学 2027 年专业学位硕士研究生招生专业目录',
                professional_faculty=JXAU_FACULTY):
    cover = {'page': 1, 'tables': [], 'native': None}
    parts = [(cover, '江西农业大学 2027 年硕士研究生\n招生专业目录'),
             page(2, [row(JXAU_SUMMARY, 0), row(['001', '农学院', '071002', '动物学', '理学', '3年', '全日制'], 10)]),
             page(3, [row(ACADEMIC, 0), row([JXAU_FACULTY, '', '', ''], 10, merged=True),
                      row(['071002 动物学\n01 动物进化', '导师', EXAM, '其他科目'], 20)], academic_title),
             page(4, [row(PROFESSIONAL, 0), row([professional_faculty, '', '', '', ''], 10, merged=True),
                      row(['0951 农业硕士', '095131 农艺与种业\n01 作物栽培', EXAM, '其他科目', '全日制\n非全日制'], 20)],
                  professional_title),
             page(5, [row(PROFESSIONAL, 0), row([JXAU_FACULTY, '', '', '', ''], 10, merged=True),
                      row(['0951 农业硕士', '095132 资源利用与植物保护\n01 植物保护', EXAM, '', '全日制'], 20)],
                  '江西农业大学 2027 年与中国科学院动物研究所联合培养\n硕士研究生招生专业目录')]
    layout = {'pages': [page for page, _ in parts], 'raw_text': '\f'.join(raw for _, raw in parts)}
    return parse_jxau_layout(layout, JXAU, 2027, {**DOC, 'unit_id': JXAU['id']})


class JxauDepartmentBlocksTests(unittest.TestCase):
    def test_academic_and_professional_chapters_have_independent_mode_evidence(self):
        result = jxau_replay()
        self.assertEqual(result['issues'], [])
        self.assertEqual([(p['code'], p['degree_type'], p['study_mode']) for p in result['programs']], [
            ('071002', '学术学位', '全日制'), ('095131', '专业学位', '全日制'), ('095131', '专业学位', '非全日制')])
        self.assertTrue(all(p['source_locator']['exam_row'] == 3 for p in result['programs']))

    def test_academic_mode_is_not_guessed_and_wrong_owner_title_never_opens_a_chapter(self):
        result = jxau_replay('江西农业大学 2027 年学术型硕士研究生招生专业目录',
                             '另一大学 2027 年专业学位硕士研究生招生专业目录')
        self.assertEqual(result['programs'], [])

    def test_matching_faculty_code_supports_the_exact_own_school_prefix(self):
        own = JXAU_FACULTY.replace('农学院', '江西农业大学农学院')
        result = jxau_replay(professional_faculty=own)
        self.assertEqual([p['department'] for p in result['programs'] if p['code'] == '095131'], ['江西农业大学农学院'] * 2)
        foreign = JXAU_FACULTY.replace('农学院', '另一大学农学院')
        self.assertFalse(any(p['code'] == '095131' for p in jxau_replay(professional_faculty=foreign)['programs']))
