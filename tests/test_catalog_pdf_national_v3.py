"""Conservative physical-column metadata normalization, retaining all originals."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from collectors.catalog_pdf_national_v3 import PARSER, parse_catalog_pdf_national_v3, prepare_layout
from tests.test_catalog_pdf_national import fixture, table


EXAM = '①101思想政治理论\n②201英语（一）\n③301数学（一）\n④408计算机学科专业基础'
UNIT = {'id': 'u', 'name': '样本大学', 'province': '测试'}
DOCUMENT = {'id': 'd', 'unit_id': 'u', 'year': 2027, 'url': 'https://sample.edu.cn/2027.pdf'}


def source(rows=None):
    rows = rows or [['学院', '专业代码及名称', '研究方向', '学习\n方式', '初试科目'],
        ['001计算机学院', '081200计算机科学与技术', '01人工智能', '全日\n制', EXAM],
        ['001计算机学院', '081200计算机科学与技术', '02软件工程', '非全日\n制', EXAM]]
    value = fixture()
    value['pages'][0]['tables'] = [table(rows)]
    value['raw_text'] = '样本大学2027年硕士研究生招生专业目录\n' + '\n'.join(v for row in rows for v in row if v)
    value['pages'][0]['native'] = {'lines': [], 'words': [],
        'native_refs': [{'original': 'unchanged', 'bbox': [1, 2, 3, 4]}]}
    return value


def parse(value):
    with patch('collectors.catalog_pdf_national.verify_catalog_publication', return_value={
        'verified': False, 'documents': [], 'evidence': [], 'mode_evidence': [], 'mode': None, 'mode_conflict': False}):
        return parse_catalog_pdf_national_v3(value, UNIT, 2027, DOCUMENT)


class PdfNationalV3Tests(unittest.TestCase):
    def test_wrapped_explicit_modes_parse_with_original_raw_citations(self):
        value = source()
        original = deepcopy(value)
        result = parse(value)
        self.assertEqual(value, original)
        self.assertEqual({row['study_mode'] for row in result['programs']}, {'全日制', '非全日制'}, result['issues'])
        self.assertTrue(all(row['catalog_parser'] == PARSER for row in result['programs']))
        self.assertEqual(len(result['normalization_manifest']), 2)
        for program in result['programs']:
            for ref in program['evidence'] + program['exam_evidence']:
                self.assertIn(ref['quote'], value['raw_text'].split('\f')[ref['page'] - 1])

    def test_profile_preserves_raw_geometry_native_and_does_not_mutate_input(self):
        value = source()
        original = deepcopy(value)
        normalized, changes = prepare_layout(value)
        self.assertEqual(value, original)
        self.assertEqual(normalized['raw_text'], value['raw_text'])
        self.assertEqual(normalized['pages'][0]['native'], value['pages'][0]['native'])
        for before, after in zip(value['pages'][0]['tables'], normalized['pages'][0]['tables']):
            self.assertEqual(before['bbox'], after['bbox'])
            self.assertEqual([r['cells'] for r in before['rows']], [r['cells'] for r in after['rows']])
        self.assertEqual(changes[0]['original'], '全日\n制')
        self.assertEqual(changes[0]['normalized'], '全日制')
        self.assertFalse(changes[0]['inherited_column_role'])

    def test_conditions_or_explanations_and_empty_mode_are_never_compacted(self):
        for mode in ['', '仅招收全日\n制本科毕业生', '全日\n制或非全日制', '全日\n制（仅部分方向）', '全日制\n详见说明']:
            with self.subTest(mode=mode):
                value = source([['学院', '专业代码及名称', '学习方式', '初试科目'],
                    ['001计算机学院', '081200计算机科学与技术', mode, EXAM]])
                normalized, changes = prepare_layout(value)
                self.assertEqual(normalized, value)
                self.assertEqual(changes, [])
                self.assertEqual(parse(value)['programs'], [], '说明文字不能只取首行当学习方式')

    def test_missing_named_mode_column_is_not_inferred_from_note(self):
        value = source([['学院', '专业代码及名称', '备注', '初试科目'],
            ['001计算机学院', '081200计算机科学与技术', '全日\n制', EXAM]])
        normalized, changes = prepare_layout(value)
        self.assertEqual(normalized, value)
        self.assertEqual(changes, [])
        self.assertEqual(parse(value)['programs'], [])

    def continuation(self):
        value = source()
        second = [['002信息学院', '085400电子信息', '01通信', '全日\n制', EXAM]]
        value['pages'].append({**deepcopy(value['pages'][0]), 'page': 2, 'tables': [table(second)]})
        value['raw_text'] += '\f' + '\n'.join(v for row in second for v in row if v)
        return value

    def test_adjacent_matching_grid_inherits_only_column_role_not_blank_values(self):
        value = self.continuation()
        normalized, changes = prepare_layout(value)
        self.assertEqual(normalized['pages'][1]['tables'][0]['rows'][0]['texts'][3], '全日制')
        self.assertTrue(changes[-1]['inherited_column_role'])
        self.assertEqual(changes[-1]['header_proof']['page'], 1)
        value['pages'][1]['tables'][0]['rows'][0]['texts'][3] = ''
        normalized, changes = prepare_layout(value)
        self.assertEqual(normalized['pages'][1]['tables'][0]['rows'][0]['texts'][3], '')
        self.assertFalse(any(row['page'] == 2 for row in changes))

    def test_different_grid_gap_late_table_and_nonfirst_table_do_not_inherit(self):
        for mutation in ['outer', 'inner', 'gap', 'late', 'second_table', 'column_count']:
            with self.subTest(mutation=mutation):
                value = self.continuation()
                page = value['pages'][1]
                grid = page['tables'][0]
                if mutation == 'outer': grid['bbox'][0] += 3
                if mutation == 'inner': grid['rows'][0]['cells'][3][0] += 3
                if mutation == 'gap': page['page'] = 3
                if mutation == 'late': grid['bbox'][1] = 400
                if mutation == 'second_table': page['tables'].insert(0, {'bbox': [0, 0, 500, 5], 'rows': []})
                if mutation == 'column_count':
                    grid['rows'][0]['texts'].append('')
                    grid['rows'][0]['cells'].append(None)
                normalized, changes = prepare_layout(value)
                self.assertFalse(any(row['page'] > 1 for row in changes))

    def test_mode_without_physical_cell_or_cross_column_cell_is_not_normalized(self):
        for box in [None, [300, 30, 500, 60]]:
            with self.subTest(box=box):
                value = source()
                value['pages'][0]['tables'][0]['rows'][1]['cells'][3] = box
                normalized, changes = prepare_layout(value)
                self.assertEqual(normalized['pages'][0]['tables'][0]['rows'][1]['texts'][3], '全日\n制')
                self.assertFalse(any(row['row'] == 2 for row in changes))

    def test_explicit_ecust_department_header_preserves_two_colleges_with_same_major(self):
        value = source([['院、系所', '专业代码及名称', '研究方向', '学习\n方式', '初试科目'],
            ['001\n化工学院\n王老师\n13800000000', '081200计算机科学与技术', '01人工智能', '全日\n制', EXAM],
            ['002\n化学与分子工程学院\n李老师\n13900000000', '081200计算机科学与技术', '01人工智能', '全日\n制', EXAM]])
        original = deepcopy(value)
        result = parse(value)
        self.assertEqual(len(result['programs']), 2, result['issues'])
        self.assertEqual({p['department_code'] for p in result['programs']}, {'001', '002'})
        self.assertEqual({p['department'] for p in result['programs']}, {'化工学院', '化学与分子工程学院'})
        self.assertEqual(len({p['id'] for p in result['programs']}), 2)
        self.assertTrue(all(p.get('department_scope') != 'unit' for p in result['programs']))
        self.assertEqual(value, original)

    def test_explicit_department_identity_loss_fails_closed(self):
        value = source([['院、系所', '专业代码及名称', '学习方式', '初试科目'],
            ['001化工学院', '081200计算机科学与技术', '全日\n制', EXAM]])
        invalid = {'id': 'u:missing', 'department': '', 'department_scope': 'unit',
            'department_code': None, 'source_locator': {'table': 1, 'row': 2, 'page': 1}}
        with patch('collectors.catalog_pdf_national_v3.parse_catalog_pdf_national_v2',
            return_value={'programs': [invalid], 'issues': [], 'expected_records': 1}):
            result = parse_catalog_pdf_national_v3(value, UNIT, 2027, DOCUMENT)
        self.assertEqual(result['programs'], [])
        self.assertTrue(any('院系' in issue for issue in result['issues']))

    def test_real_shape_cross_page_initial_cell_cannot_publish_its_truncated_prefix(self):
        first = [['学院', '专业代码及名称', '学习方式', '研究方向', '考试科目'],
            ['002化学与分子工程学院', '070302分析化学', '全日制',
             '01天然产物的分离和分析\n02化学与生物传感器', '①101思想政治理论\n②201英语（一）']]
        second = [['', '', '', '03化学信息学\n04生命分析化学', '③602分析化学\n④802物理化学']]
        value = source(first)
        value['pages'].append({**deepcopy(value['pages'][0]), 'page': 2, 'tables': [table(second)]})
        value['raw_text'] += '\f' + '\n'.join(v for row in second for v in row if v)
        result = parse(value)
        self.assertEqual(result['programs'], [])
        self.assertTrue(any('初试' in issue and '续' in issue for issue in result['issues']))
        self.assertTrue(result.get('rejected_source_rows'))

    def test_course_name_tail_and_last_unit_on_next_page_also_invalidate_prefix(self):
        first = [['学院', '专业代码及名称', '学习方式', '研究方向', '考试科目'],
            ['002人文学院', '030300社会学', '全日制', '01社会学理论',
             '①101思想政治理论\n②201英语（一）\n③623社会科学研']]
        second = [['', '', '', '', '究方法\n④832社会学综合']]
        value = source(first)
        value['pages'].append({**deepcopy(value['pages'][0]), 'page': 2, 'tables': [table(second)]})
        value['raw_text'] += '\f' + '\n'.join(v for row in second for v in row if v)
        self.assertEqual(parse(value)['programs'], [])

    def test_complete_two_unit_program_is_not_rejected_due_to_unit_count_alone(self):
        value = source([['学院', '专业代码及名称', '学习方式', '考试科目'],
            ['001商学院', '125100工商管理', '非全日制', '①199管理类综合能力\n②204英语（二）']])
        result = parse(value)
        self.assertEqual(len(result['programs']), 1, result['issues'])
        self.assertEqual(len(result['programs'][0]['subjects']), 2)

    def test_blank_explicit_mode_does_not_borrow_a_whole_document_mode(self):
        value = source([['学院', '专业代码及名称', '学习方式', '考试科目'],
            ['001计算机学院', '081200计算机科学与技术', '', EXAM]])
        value['raw_text'] = value['raw_text'].replace('硕士研究生招生专业目录', '全日制硕士研究生招生专业目录')
        self.assertEqual(parse(value)['programs'], [])

    def test_new_direction_with_fresh_initial_unit_one_is_not_a_split_exam_cell(self):
        value = self.continuation()
        current = value['pages'][1]['tables'][0]['rows'][0]
        current['texts'] = ['', '', '03新方向', '全日制', EXAM]
        value['raw_text'] = value['raw_text'].split('\f')[0] + '\f' + '\n'.join(current['texts'])
        result = parse(value)
        self.assertFalse(result.get('rejected_source_rows'))
        self.assertTrue(any(program['direction'].startswith('03') for program in result['programs']), result['issues'])

    def test_parenthesized_ai_is_not_a_registration_direction_or_a_truncated_label(self):
        for direction in ['01基础数学（数论）\n04计算数学（数据科学与\nAI数学）\n05概率论（统计',
                          '01基础数学（数论）\n04计算数学（数据科学与\nAI数学）\n05概率论（统计）']:
            with self.subTest(direction=direction):
                value = source([['学院', '专业代码及名称', '学习方式', '研究方向', '考试科目'],
                    ['009数学学院', '070100数学', '全日制', direction, EXAM]])
                result = parse(value)
                self.assertEqual(result['programs'], [])
                self.assertTrue(any('方向' in item['reason'] for item in result.get('rejected_source_rows', [])))

    def test_ascii_direction_name_cannot_merge_two_printed_numeric_labels(self):
        value = source([['学院', '专业代码及名称', '学习方式', '研究方向', '考试科目'],
            ['020商学院', '125100工商管理', '非全日制',
             '03科创人工智能班\n04EMBA大健康产业管理班', '①199管理类综合能力\n②204英语（二）']])
        result = parse(value)
        self.assertEqual(result['programs'], [])
        self.assertTrue(result.get('rejected_source_rows'))

    def test_unparenthesized_published_alphanumeric_direction_code_remains_valid(self):
        value = source([['学院', '专业代码及名称', '学习方式', '研究方向', '考试科目'],
            ['001计算机学院', '081200计算机科学与技术', '全日制', 'A1人工智能\n0A网络安全\n02软件工程', EXAM]])
        result = parse(value)
        self.assertEqual({p['direction'][:2] for p in result['programs']}, {'A1', '0A', '02'}, result['issues'])

    def test_ai_inside_direction_prose_is_not_a_new_printed_direction(self):
        for direction in ['01人工智能与AI技术', '01计算机科学\n02人工智能与AI应用',
                          '01人工智能与\nAI技术', '01人工智能与\nAI 技术', 'AI 技术']:
            with self.subTest(direction=direction):
                value = source([['学院', '专业代码及名称', '学习方式', '研究方向', '考试科目'],
                    ['001计算机学院', '081200计算机科学与技术', '全日制', direction, EXAM]])
                result = parse(value)
                self.assertTrue(not result['programs'] or len(result['programs']) == (2 if direction.startswith('01计算机') else 1))
                self.assertFalse(any(p['direction'].startswith('AI') for p in result['programs']))

    def test_repeated_program_identity_on_next_page_does_not_close_initial_cell(self):
        first = [['学院', '专业代码及名称', '学习方式', '研究方向', '考试科目'],
            ['002化学学院', '070302分析化学', '全日制', '01分析技术', '①101思想政治理论\n②201英语（一）']]
        second = [['002化学学院', '070302分析化学', '全日制', '03化学信息学', '③602分析化学\n④802物理化学']]
        value = source(first)
        value['pages'].append({**deepcopy(value['pages'][0]), 'page': 2, 'tables': [table(second)]})
        value['raw_text'] += '\f' + '\n'.join(v for row in second for v in row if v)
        self.assertEqual(parse(value)['programs'], [])

    def test_repeated_mode_or_department_on_next_page_does_not_close_initial_cell(self):
        for department, mode in [('', '全日制'), ('002化学学院', '全日制')]:
            with self.subTest(department=department, mode=mode):
                first = [['学院', '专业代码及名称', '学习方式', '研究方向', '考试科目'],
                    ['002化学学院', '070302分析化学', '全日制', '01分析技术', '①101思想政治理论\n②201英语（一）']]
                second = [[department, '', mode, '03化学信息学', '③602分析化学\n④802物理化学']]
                value = source(first)
                value['pages'].append({**deepcopy(value['pages'][0]), 'page': 2, 'tables': [table(second)]})
                value['raw_text'] += '\f' + '\n'.join(v for row in second for v in row if v)
                self.assertEqual(parse(value)['programs'], [])


if __name__ == '__main__':
    unittest.main()
