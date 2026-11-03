import unittest
import hashlib
import tempfile
from pathlib import Path

from collectors.catalog_html_national import parse_catalog_html_national


UNIT = {'id': 'u', 'name': '样本大学', 'province': '北京',
        'official_url': 'https://grad.sample.edu.cn/'}
DOC = {'id': 'doc', 'unit_id': 'u', 'year': 2027,
       'url': 'https://grad.sample.edu.cn/catalog', 'kind': 'catalog'}
EXAMS = '①101思想政治理论②201英语（一）或203日语③301数学（一）④408计算机学科专业基础'


def table(headers, rows):
    return '<table>' + ''.join('<tr>' + ''.join('<td>' + text + '</td>' for text in row) + '</tr>'
                              for row in [headers, *rows]) + '</table>'


def page(body, title='样本大学2027年硕士研究生招生专业目录'):
    return '<h1>' + title + '</h1>' + body


class NationalHTMLTests(unittest.TestCase):
    def parse(self, body, **kwargs):
        return parse_catalog_html_national(page(body), kwargs.get('unit', UNIT), 2027,
                                          kwargs.get('document', DOC))

    def test_explicit_exam_and_two_digit_department_headers_keep_every_language(self):
        text = table(['系所代码及名称', '专业代码及名称', '拟招生人数', '研究方向代码及名称',
                      '学习方式', '考试科目代码及名称'],
                     [['06计算机学院', '081200计算机科学与技术', '20', '01软件理论', '全日制', EXAMS]])
        result = self.parse(text)
        self.assertEqual(result['issues'], [])
        row = result['programs'][0]
        self.assertEqual(row['id'], 'u:2027:06:081200:01:全日制')
        self.assertEqual((row['department_code'], row['department']), ('06', '计算机学院'))
        self.assertEqual([s['code'] for s in row['subjects']], ['101', '201', '203', '301', '408'])
        self.assertEqual(row['source_locator'], {'table': 1, 'row': 2})
        self.assertEqual(row['catalog_parser'], 'html_national_v1')
        self.assertIn('06计算机学院', [ref['quote'] for ref in row['evidence']])

    def test_learning_mode_is_read_from_explicit_quota_cell_without_using_its_count(self):
        result = self.parse(table(['院系所', '专业代码和名称', '招生总人数（包括推免生人数）',
                                  '学位类型', '研究方向', '初试科目', '复试科目'],
                                 [['建筑学院', '081300建筑学', '全日制38人（3人）', '学术学位',
                                   '00不区分研究方向', EXAMS, '建筑设计']]))
        self.assertEqual(len(result['programs']), 1)
        row = result['programs'][0]
        self.assertEqual(row['study_mode'], '全日制')
        self.assertNotIn('planned_enrollment', row)
        self.assertIn('全日制38人（3人）', [ref['quote'] for ref in row['evidence']])

    def test_numeric_quota_does_not_imply_full_time(self):
        result = self.parse(table(['院系所', '专业代码和名称', '招生总人数（包括推免生人数）',
                                  '研究方向', '初试科目'],
                                 [['建筑学院', '081300建筑学', '38人', '00不区分研究方向', EXAMS]]))
        self.assertEqual(result['programs'], [])
        self.assertTrue(any('学习方式' in issue for issue in result['issues']))

    def test_unnumbered_direction_is_not_assigned_a_fictional_zero_code(self):
        exam = '初试：①101思想政治理论②201英语一③701专业基础综合（一）复试：1.面试2.英语测试'
        result = self.parse(table(['招生代码及专业', '研究方向', '拟招生人数', '考试科目'],
                                 [['040301体育人文社会学', '体育社会学', '全日制：10', exam]]))
        row = result['programs'][0]
        self.assertEqual(row['direction'], '体育社会学')
        self.assertEqual(row['id'], 'u:2027::040301:体育社会学:全日制')
        self.assertNotIn('direction_code', row)
        self.assertEqual([s['code'] for s in row['subjects']], ['101', '201', '701'])
        self.assertEqual(row['subjects'][2]['name'], '专业基础综合（一）')
        self.assertIn('体育社会学', [ref['quote'] for ref in row['evidence']])

    def test_duplicate_major_columns_use_original_mode_inside_direction(self):
        result = self.parse(table(['专业/专业领域', '专业/专业领域', '研究方向', '培养单位',
                                  '专业研究方向特色', '考试科目', '基本学制'],
                                 [['035200社会工作', '035200社会工作', '00（非全日制）不分方向',
                                   '公共管理学部', '真实特色', EXAMS, '三年']]))
        self.assertEqual(len(result['programs']), 1)
        self.assertEqual(result['programs'][0]['study_mode'], '非全日制')
        self.assertEqual(result['programs'][0]['department'], '公共管理学部')

    def test_full_time_policy_requires_a_whole_school_explicit_statement(self):
        body = table(['招生院所', '专业名称', '类别', '学制', '研究方向', '初试考试科目'],
                     [['101应用文理学院', '060100考古学', '学硕', '3年', '01考古理论', EXAMS]])
        result = self.parse('<p>学校招收全日制非定向就业和全日制定向就业硕士研究生。</p>' + body)
        self.assertEqual(len(result['programs']), 1)
        self.assertIn('学校招收全日制非定向就业和全日制定向就业硕士研究生。',
                      [ref['quote'] for ref in result['programs'][0]['evidence']])
        self.assertEqual(self.parse('<p>报考者须为全日制普通高校毕业生。</p>' + body)['programs'], [])
        self.assertEqual(len(self.parse('<p>学校坚持立德树人、按需招生的原则，招收全日制非定向就业和全日制定向就业硕士研究生。</p>' + body)['programs']), 1)
        self.assertEqual(self.parse('<p>我校部分学院招收全日制非定向就业和全日制定向就业硕士研究生。</p>' + body)['programs'], [])

    def test_hidden_tables_are_not_accepted(self):
        grid = table(['专业', '研究方向', '学习方式', '考试科目代码及名称'],
                     [['081200计算机科学与技术', '01软件', '全日制', EXAMS]])
        self.assertEqual(self.parse('<div hidden>' + grid + '</div>')['programs'], [])

    def test_wrong_year_unit_and_unsupported_mixed_scope_are_rejected(self):
        grid = table(['专业', '研究方向', '学习方式', '考试科目代码及名称'],
                     [['081200计算机科学与技术', '01软件', '全日制', EXAMS]])
        for html, unit, doc in [(page(grid, '样本大学2026年硕士研究生招生专业目录'), UNIT, DOC),
                                (page(grid), UNIT, {**DOC, 'url': 'https://foreign.edu.cn/catalog'}),
                                (page(grid, '另一大学2027年硕士研究生招生专业目录'), UNIT, DOC),
                                (page(grid, '样本大学2027年单独考试硕士研究生招生目录'), UNIT, DOC)]:
            with self.subTest(html=html[:80], doc=doc):
                self.assertEqual(parse_catalog_html_national(html, unit, 2027, doc)['programs'], [])

    def test_unconsumed_catalog_table_prevents_a_false_complete_result(self):
        first = table(['专业', '研究方向', '学习方式', '考试科目代码及名称'],
                      [['081200计算机科学与技术', '01软件', '全日制', EXAMS]])
        other = '<table><tr><td>081300建筑学</td><td>00建筑</td><td>全日制</td><td>' + EXAMS + '</td></tr></table>'
        result = self.parse(first + other)
        self.assertEqual(len(result['programs']), 1)
        self.assertTrue(any('未适配' in issue or '缺少表头' in issue for issue in result['issues']))

    def test_empty_or_unparseable_exam_is_counted_as_a_gap(self):
        result = self.parse(table(['专业', '研究方向', '学习方式', '考试科目代码及名称'],
                                 [['081200计算机科学与技术', '01软件', '全日制', '待公布']]))
        self.assertEqual(result['expected_records'], 1)
        self.assertEqual(result['programs'], [])
        self.assertTrue(result['issues'])

    def test_field_code_in_the_more_specific_duplicate_column_does_not_collapse_into_a_discipline(self):
        body = table(['专业/专业领域', '专业/专业领域', '研究方向', '培养单位', '考试科目'],
                     [['0854电子信息', '085404计算机技术', '00（全日制）不分方向', '计算机学院', EXAMS],
                      ['0854电子信息', '085405软件工程', '00（全日制）不分方向', '计算机学院', EXAMS]])
        result = self.parse(body)
        self.assertEqual([row['code'] for row in result['programs']], ['085404', '085405'])
        self.assertEqual(len({row['id'] for row in result['programs']}), 2)

    def test_six_digit_field_in_direction_column_is_not_truncated_to_a_two_digit_direction(self):
        result = self.parse(table(['招生代码及专业', '研究方向', '拟招生人数', '考试科目'],
                                 [['0452体育硕士', '045201体育教学', '全日制：10', EXAMS]]))
        row = result['programs'][0]
        self.assertEqual((row['code'], row['name'], row['direction']), ('045201', '体育教学', ''))
        self.assertEqual(row['direction_scope'], 'program')
        self.assertIn('045201体育教学', [ref['quote'] for ref in row['evidence']])

    def test_another_independent_recruiting_unit_is_not_mistaken_for_a_department(self):
        result = self.parse('<p>我校硕士研究生学习方式均为全日制。</p>' + table(
            ['招生单位', '专业', '研究方向', '考试科目'],
            [['12345另一研究院', '081200计算机科学与技术', '01软件', EXAMS]]))
        self.assertEqual(result['programs'], [])
        self.assertTrue(any('招生单位' in reason for reason in result['issues']))

    def test_validated_same_year_charter_can_supply_the_missing_mode_with_its_own_citation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = '<h1>样本大学2027年硕士研究生招生章程</h1><p>我校各学科专业硕士研究生学习方式均为全日制，学生需全脱产在校学习。</p>'
            path = root / 'data/snapshots/charter.html'
            path.parent.mkdir(parents=True)
            path.write_text(original, 'utf-8')
            context = {'id': 'mode-doc', 'unit_id': 'u', 'year': 2027,
                       'url': 'https://grad.sample.edu.cn/charter', 'local_path': 'data/snapshots/charter.html',
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'kind': 'charter',
                       'content_type': 'text/html;charset=utf-8'}
            html = page(table(['专业', '研究方向', '考试科目代码及名称'],
                              [['081200计算机科学与技术', '01软件', EXAMS]]))
            result = parse_catalog_html_national(html, UNIT, 2027,
                {**DOC, 'learning_mode_document': context}, project_root=root)
            self.assertEqual(result['programs'][0]['study_mode'], '全日制')
            citations = result['programs'][0]['evidence']
            self.assertTrue(any(ref['document_id'] == 'mode-doc' and '学习方式均为全日制' in ref['quote'] for ref in citations))
            self.assertEqual(result['documents'][0]['kind'], 'catalog_context')
            for change in [{'sha256': '0' * 64}, {'year': 2026}, {'unit_id': 'other'},
                           {'url': 'https://foreign.edu.cn/charter'}, {'local_path': '../outside.html'}]:
                bad = parse_catalog_html_national(html, UNIT, 2027,
                    {**DOC, 'learning_mode_document': {**context, **change}}, project_root=root)
                self.assertEqual(bad['programs'], [])

    def test_charter_bachelor_conditions_and_qualified_modes_do_not_supply_a_default(self):
        phrases = ['全日制本科毕业生可以报考。', '部分专业硕士研究生学习方式均为全日制。',
                   '除工商管理外，我校各学科专业硕士研究生学习方式均为全日制。',
                   '我校部分校区各学科专业硕士研究生学习方式均为全日制。']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'data/snapshots/charter.html'
            path.parent.mkdir(parents=True)
            for phrase in phrases:
                path.write_text('<h1>样本大学2027年硕士研究生招生章程</h1><p>' + phrase + '</p>', 'utf-8')
                context = {'id': 'ctx', 'unit_id': 'u', 'year': 2027, 'url': DOC['url'],
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'local_path': 'data/snapshots/charter.html'}
                html = page(table(['专业', '研究方向', '考试科目代码及名称'],
                                  [['081200计算机科学与技术', '01软件', EXAMS]]))
                self.assertEqual(parse_catalog_html_national(html, UNIT, 2027,
                    {**DOC, 'learning_mode_document': context}, project_root=root)['programs'], [])

    def test_headerless_annual_link_cannot_certify_a_charters_year(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'data/snapshots/charter.html'
            path.parent.mkdir(parents=True)
            path.write_text('<title>样本大学研究生院</title><p>我校各学科专业硕士研究生学习方式均为全日制。</p>'
                            '<a href="/new">2027年硕士研究生招生章程</a>', 'utf-8')
            context = {'id': 'ctx', 'unit_id': 'u', 'year': 2027, 'url': DOC['url'],
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                       'local_path': 'data/snapshots/charter.html'}
            html = page(table(['专业', '研究方向', '考试科目代码及名称'],
                              [['081200计算机科学与技术', '01软件', EXAMS]]))
            self.assertEqual(parse_catalog_html_national(html, UNIT, 2027,
                {**DOC, 'learning_mode_document': context}, project_root=root)['programs'], [])

    def test_imac_continuation_uses_explicit_full_time_catalog_title_not_neighboring_cells(self):
        unit = {**UNIT, 'id': '2311267403', 'name': '内蒙古艺术学院', 'official_url': 'https://yjs.imac.edu.cn/'}
        doc = {**DOC, 'unit_id': unit['id'], 'url': 'https://yjs.imac.edu.cn/html-yjs/jzml/20260928/catalog.shtml'}
        headers = ['院系所', '专业', '研究方向', '学习方式', '普通计划/退役大学生士兵计划',
                   '考试科目', '参考书目', '备注']
        first = table(headers, [['007文化学院', '130100艺术学', '01艺术理论', '（1）全日制', '7/0', EXAMS, '原书', '']])
        second = table([], [['001音乐学院', '135200音乐', '01作曲', '', '4/0', EXAMS, '原书', '']]).replace('<tr></tr>', '')
        html = page(first + second, '内蒙古艺术学院2027年全日制硕士研究生招生目录')
        result = parse_catalog_html_national(html, unit, 2027, doc)
        self.assertEqual(len(result['programs']), 2)
        self.assertEqual(result['expected_records'], 2)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['programs'][1]['source_locator'], {'table': 2, 'row': 1})


if __name__ == '__main__':
    unittest.main()
