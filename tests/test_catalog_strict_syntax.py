"""Strict syntax is a candidate grammar, not a semantic approval shortcut."""
import unittest

from collectors.catalog_strict_syntax import parse_strict_directions, parse_strict_subjects


DOCUMENT = {'id': 'strict-source', 'url': 'https://grad.example.edu.cn/catalog'}
FOUR = '①101思想政治理论②201英语（一）③301数学（一）④801数据结构'


class StrictDirectionsTests(unittest.TestCase):
    def test_ai_inside_a_chinese_name_is_not_a_direction_code(self):
        result = parse_strict_directions('01智能系统与AI应用')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['directions'], [{'code': '01', 'name': '智能系统与AI应用', 'label': '01智能系统与AI应用'}])

    def test_real_line_boundaries_and_semicolons_preserve_two_directions(self):
        for text in ['01智能系统与AI应用\n02机器人', '01智能系统与AI应用；02机器人', '01智能系统与AI应用;02机器人']:
            with self.subTest(text=text):
                result = parse_strict_directions(text)
                self.assertFalse(result['issues'])
                self.assertEqual([item['code'] for item in result['directions']], ['01', '02'])
                self.assertEqual(result['directions'][0]['name'], '智能系统与AI应用')

    def test_concatenated_directions_are_not_guessed(self):
        result = parse_strict_directions('01智能系统与AI应用02机器人')
        self.assertTrue(result['issues'])
        self.assertEqual(result['directions'], [])

    def test_explicit_printed_alpha_code_is_allowed(self):
        for code in ['FF', 'A1', '01', '0101']:
            with self.subTest(code=code):
                result = parse_strict_directions('AI与安全应用', code=code)
                self.assertFalse(result['issues'])
                self.assertEqual(result['directions'], [{'code': code, 'name': 'AI与安全应用', 'label': code + 'AI与安全应用'}])

    def test_explicit_code_does_not_strip_literal_name_prefix(self):
        for name in ['FFmpeg技术', 'FF安全技术', '01AI应用']:
            code = '01' if name.startswith('01') else 'FF'
            result = parse_strict_directions(name, code=code)
            self.assertEqual(result['directions'][0]['name'], name)
            self.assertEqual(result['directions'][0]['label'], code + name)

    def test_conflicting_or_invalid_explicit_code_fails(self):
        for text, code in [('02机器人', '01'), ('方向', ''), ('方向', '01/02'), ('方向', True), ('', '01')]:
            with self.subTest(text=text, code=code):
                result = parse_strict_directions(text, code=code)
                self.assertTrue(result['issues'])
                self.assertEqual(result['directions'], [])

    def test_brackets_in_names_are_preserved(self):
        text = '01AI应用（含语言模型（LLM））\n02机器人'
        result = parse_strict_directions(text)
        self.assertFalse(result['issues'])
        self.assertEqual(result['directions'][0]['label'], '01AI应用（含语言模型（LLM））')

    def test_unbalanced_bracket_fails_whole_cell(self):
        for text in ['01AI应用（未闭合\n02机器人', '01方向）', '01方向(子方向]']:
            result = parse_strict_directions(text)
            self.assertTrue(result['issues'])
            self.assertEqual(result['directions'], [])

    def test_implicit_alpha_prose_does_not_become_a_code(self):
        self.assertTrue(parse_strict_directions('AI应用')['issues'])
        self.assertTrue(parse_strict_directions('FF安全技术')['issues'])

    def test_ascii_name_and_genuine_alpha_digit_code(self):
        for text in ['01AI驱动技术', 'A1安全技术', '01GPU64位系统', '013D打印技术']:
            self.assertFalse(parse_strict_directions(text)['issues'])

    def test_explicit_code_does_not_require_guessing_wrapped_name(self):
        result = parse_strict_directions('智能系统\n与AI应用', code='FF')
        self.assertFalse(result['issues'])
        self.assertEqual(result['directions'][0]['name'], '智能系统\n与AI应用')

    def test_duplicate_printed_direction_code_fails_the_cell(self):
        self.assertTrue(parse_strict_directions('01人工智能\n01机器人')['issues'])

    def test_deep_or_excessive_labels_fail_without_unbounded_work(self):
        self.assertTrue(parse_strict_directions('01方向' + '(' * 65 + ')' * 65)['issues'])
        text = '\n'.join(f'{index:04d}方向' for index in range(1, 258))
        self.assertTrue(parse_strict_directions(text)['issues'])

    def test_explicit_no_direction_statement_is_literal(self):
        for text in ['不区分研究方向', '不设研究方向']:
            result = parse_strict_directions(text)
            self.assertEqual(result['directions'], [{'code': '', 'name': text, 'label': text}])

    def test_invalid_or_oversized_input_fails(self):
        for text in [None, '', '  ', '备注：01方向', 'x' * 65537]:
            self.assertTrue(parse_strict_directions(text)['issues'])


class StrictSubjectsTests(unittest.TestCase):
    def parse(self, raw=FOUR, **kwargs):
        return parse_strict_subjects(raw, 'u1', DOCUMENT, **kwargs)

    def test_four_unit_positive_and_exact_evidence(self):
        result = self.parse()
        self.assertFalse(result['issues'])
        self.assertEqual(result['exam_scope'], 'unified')
        self.assertEqual([subject['code'] for subject in result['subjects']], ['101', '201', '301', '801'])
        self.assertEqual([subject['scope'] for subject in result['subjects']], ['national', 'national', 'national', 'u1'])
        self.assertTrue(all(subject['evidence']['quote'] == FOUR for subject in result['subjects']))
        self.assertEqual(result['exam_evidence'][0]['quote'], FOUR)

    def test_empty_declared_fourth_unit_cannot_downgrade_to_three(self):
        for tail in ['', ' ', '—', '---']:
            result = self.parse('①101思想政治理论②201英语（一）③301数学（一）④' + tail)
            self.assertTrue(result['issues'])
            self.assertEqual(result['subjects'], [])

    def test_empty_final_numbered_unit_is_not_part_of_previous_subject_name(self):
        for raw in [
            '1、101思想政治理论2、201英语（一）3、621体育综合4、',
            '(1)101思想政治理论(2)201英语（一）(3)621体育综合(4)',
            '（1）101思想政治理论（2）201英语（一）（3）621体育综合（4）——',
            '①101思想政治理论②201英语（一）③621体育综合(4)',
        ]:
            with self.subTest(raw=raw):
                result = self.parse(raw)
                self.assertTrue(result['issues'])
                self.assertEqual(result['subjects'], [])

    def test_explicit_no_exam_in_numbered_tail_and_literal_name_parentheses(self):
        for raw in [
            '1、101思想政治理论2、201英语（一）3、621体育综合4、无',
            '(1)101思想政治理论(2)201英语（一）(3)621体育综合(4)不考',
        ]:
            result = self.parse(raw)
            self.assertEqual(result['issues'], [])
            self.assertEqual([s['name'] for s in result['subjects']][-1], '体育综合')
        result = self.parse(FOUR.replace('801数据结构', '801数据结构（4）'))
        self.assertEqual(result['subjects'][-1]['name'], '数据结构（4）')

    def test_true_two_and_three_units(self):
        for text, expected in [('①199管理类综合能力②204英语（二）', ['199', '204']),
                               ('①101思想政治理论②201英语（一）③306临床医学综合能力（西医）', ['101', '201', '306'])]:
            result = self.parse(text)
            self.assertFalse(result['issues'])
            self.assertEqual([subject['code'] for subject in result['subjects']], expected)

    def test_explicit_no_exam_tail_is_not_unknown(self):
        result = self.parse('①199管理类综合能力②204英语（二）③-无④--无')
        self.assertFalse(result['issues'])
        self.assertEqual([subject['exam_unit'] for subject in result['subjects']], [1, 2])

    def test_active_unit_after_no_exam_is_rejected(self):
        result = self.parse('①101思想政治理论②201英语（一）③不考④801数据结构')
        self.assertTrue(result['issues'])

    def test_duplicate_or_skipped_units_fail(self):
        for raw in ['①101思想政治理论③301数学（一）④801数据结构',
                    '①101思想政治理论②201英语（一）③301数学（一）③801数据结构',
                    '②201英语（一）①101思想政治理论',
                    FOUR + '⑤901其他科目']:
            self.assertTrue(self.parse(raw)['issues'])

    def test_parenthesized_or_is_a_complete_choice_group(self):
        raw = FOUR.replace('④801数据结构', '④801数据结构或（802操作系统）')
        result = self.parse(raw)
        self.assertFalse(result['issues'])
        fourth = [subject for subject in result['subjects'] if subject['exam_unit'] == 4]
        self.assertEqual([(s['code'], s['name'], s['choice_group']) for s in fourth],
                         [('801', '数据结构', 'exam-unit-4'), ('802', '操作系统', 'exam-unit-4')])
        self.assertTrue(all(subject['evidence']['quote'] == raw for subject in fourth))

    def test_nested_or_and_multiple_connectors(self):
        for fragment in ['801数据结构或者（802操作系统或（803组成原理））',
                         '801数据结构/802操作系统、803组成原理']:
            result = self.parse(FOUR.replace('801数据结构', fragment))
            self.assertFalse(result['issues'])
            self.assertEqual([s['code'] for s in result['subjects'] if s['exam_unit'] == 4], ['801', '802', '803'])

    def test_dangling_separator_does_not_become_a_complete_choice_group(self):
        for fragment in [
            '801数据结构或（802操作系统、）',
            '801数据结构/802操作系统/',
            '（801数据结构或802操作系统）、',
            '801数据结构//802操作系统',
        ]:
            with self.subTest(fragment=fragment):
                result = self.parse(FOUR.replace('801数据结构', fragment))
                self.assertTrue(result['issues'])
                self.assertEqual(result['subjects'], [])

    def test_name_parentheses_and_literal_slash_are_not_alternatives(self):
        result = self.parse(FOUR.replace('801数据结构', '801C/C++程序设计（含Linux）'))
        self.assertFalse(result['issues'])
        self.assertEqual(result['subjects'][-1]['name'], 'C/C++程序设计（含Linux）')

    def test_unbalanced_or_or_unconsumed_code_fails(self):
        for fragment in ['801数据结构或（802操作系统', '801数据结构（或802操作系统）',
                         '801数据结构或操作系统', '801数据结构802操作系统', '801']:
            result = self.parse(FOUR.replace('801数据结构', fragment))
            self.assertTrue(result['issues'])
            self.assertEqual(result['subjects'], [])

    def test_linked_cross_unit_scheme_is_not_flattened(self):
        for note in ['③④成套选择', '第三、第四单元对应选考', '方案一二选一']:
            result = self.parse(FOUR + note)
            self.assertTrue(result['issues'])

    def test_explicit_single_exam_is_not_unified(self):
        result = self.parse('①111单独考试思想政治理论②240单独考试英语③601数学④801程序设计')
        self.assertFalse(result['issues'])
        self.assertEqual(result['exam_scope'], 'single')

    def test_or_mixing_single_and_unified_exam_is_rejected(self):
        result = self.parse('①101思想政治理论或111单独考试思想政治理论②201英语（一）③601数学④801程序设计')
        self.assertEqual(result['exam_scope'], 'mixed')
        self.assertTrue(result['issues'])
        self.assertEqual(result['subjects'], [])

    def test_unproved_single_exam_code_is_not_silently_unified(self):
        result = self.parse('①111思想政治理论②240英语③601数学④801程序设计')
        self.assertEqual(result['exam_scope'], 'unknown')
        self.assertTrue(result['issues'])

    def test_separate_printed_slot_columns(self):
        slots = {1: '101思想政治理论', 2: '201英语（一）', 3: '301数学（一）', 4: '801数据结构或（802操作系统）'}
        result = self.parse('', slots=slots)
        self.assertFalse(result['issues'])
        self.assertEqual(result['subjects'][-1]['evidence']['quote'], slots[4])
        self.assertEqual(len(result['exam_evidence']), 4)

    def test_separate_slots_preserve_unknown_empty_column(self):
        result = self.parse('', slots={1: '101思想政治理论', 2: '201英语（一）', 3: '301数学（一）', 4: ''})
        self.assertTrue(result['issues'])
        self.assertEqual(result['subjects'], [])

    def test_separate_slots_reject_conflicting_body_unit(self):
        result = self.parse('', slots={1: '②101思想政治理论', 2: '201英语（一）'})
        self.assertTrue(result['issues'])

    def test_invalid_slots_or_input_or_document(self):
        for slots in [[], {True: '101政治', 2: '201英语（一）'}, {1: '101政治', 3: '301数学（一）'}, {'1': '101政治', '2': '201英语（一）'}]:
            self.assertTrue(self.parse('', slots=slots)['issues'])
        for raw in [None, '', 'x' * 65537, '备注' + FOUR, '101思想政治理论201英语（一）']:
            self.assertTrue(self.parse(raw)['issues'])
        self.assertTrue(parse_strict_subjects(FOUR, 'u1', {})['issues'])
        self.assertTrue(parse_strict_subjects(FOUR, None, DOCUMENT)['issues'])

    def test_national_name_conflict_is_not_reclassified_local(self):
        result = self.parse(FOUR.replace('201英语（一）', '201数学'))
        self.assertTrue(result['issues'])
        self.assertEqual(result['subjects'], [])

    def test_duplicate_alternative_does_not_hide_bad_coverage(self):
        self.assertTrue(self.parse(FOUR.replace('801数据结构', '801数据结构或801数据结构'))['issues'])

    def test_excessive_alternatives_are_bounded(self):
        fragment = '或'.join('801数据结构' for _ in range(258))
        result = self.parse(FOUR.replace('801数据结构', fragment))
        self.assertTrue(result['issues'])
        self.assertEqual(result['subjects'], [])


if __name__ == '__main__':
    unittest.main()
