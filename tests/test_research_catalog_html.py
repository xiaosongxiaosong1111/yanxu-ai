import unittest

from research_catalog_html import parse_catalog_html_blocks


UNIT = {'id': '123', 'name': '测试大学', 'official_url': 'https://grad.example.edu.cn/'}
DOCUMENT = {'id': 'official', 'url': 'https://grad.example.edu.cn/catalog.html'}
HEADED_HEADER = '<tr><th colspan="2">研究方向</th><th>拟招生计划</th><th>学习方式</th><th>学制</th><th>初试科目</th><th>复试科目</th><th>备注</th></tr>'
HEADING = '<tr><td colspan="2">专业代码及名称：</td><td colspan="6">045103 学科教学（语文）[专业学位]</td></tr>'
HEADED_DIRECTION = '<tr><td colspan="2">01 不区分研究方向</td><td>105</td><td>全日制</td><td>3</td><td>①101 思想政治理论<br>②204 英语（二）<br>③333 教育综合<br>④847 阅读与写作</td><td>Z0405 写作</td><td>不招收同等学力考生。</td></tr>'
LISTED_HEADER = '<tr><th>专业代码、名称及研究方向</th><th>拟招生人数</th><th>考试科目</th><th>复试笔试科目及备注</th></tr>'


def row(*cells):
    return '<tr>' + ''.join(f'<td>{cell}</td>' for cell in cells) + '</tr>'


def headed():
    return row('024 教师教育学院') + HEADING + HEADED_HEADER + HEADED_DIRECTION


def listed():
    return (row('2027年专业学位类专业目录') + LISTED_HEADER + row('(025)社会发展研究院', '', '', '')
            + row('035200社会工作', '40', '', '') + row('00(全日制)不区分研究方向', '', '①101思想政治理论', '社会工作')
            + row('', '', '②204英语二', '') + row('', '', '③331社会工作原理', '') + row('', '', '④437社会工作实务', '')
            + row('045400应用心理', '14', '', ''))


def parse(rows, *, title='2027年测试大学硕士研究生招生专业目录', document=DOCUMENT):
    return parse_catalog_html_blocks(f'<title>{title}</title><table>{rows}</table>', UNIT, 2027, document)['programs']


def brochure():
    return (
        '<h2>环境科学与工程学院、大湾区环境研究院2027年硕士研究生招生简章</h2>'
        '<p>测试大学环境科学与工程学科。</p><p>全日制工程硕士学制为三年。</p>'
        '<p>（四）招生专业及考试科目</p><div><table>'
        + row('学科、专业名称（代码）<br>研究方向', '考试科目', '同等学力加试科目', '专业科目参考书目')
        + '<tr><td>083000<br>环境科学与工程</td><td>①101思想政治理论②201英语（一）③302数学（二）④817环境学</td>'
        '<td rowspan="2">加试科目仅供参考</td><td>参考书目</td></tr>'
        + row('085701<br>环境工程', '①101思想政治理论②204英语（二）③302数学（二）④817环境学', '参考书目')
        + '</table></div><p>招生单位名称：环境科学与工程学院</p>'
    )


def college_catalog(*, unsafe_exam_span=False, owner='电子学院', year=2027):
    title = f'测试大学电子学院 {year}年硕士研究生招生专业目录'
    header = row('序号', '招生院系', '招生专业代码、名称', '研究方向', '拟招生人数', '招生院系联系方式',
                 '初试考试科目', '复试内容', '同等学力加试科目', '初试参考书目', '复试、加试参考书目')
    first = row('1', owner, '045105 学科教学（物理）', '00 不区分研究方向', '7', '联系学院',
                '▲①101思想政治理论▲②204英语（二）▲③333教育综合★④846教育研究方法',
                '复试', '加试', '初试书目', '复试书目').replace(f'<td>{owner}</td>', f'<td rowspan="2">{owner}</td>')
    second = row('2', '085408 光电信息工程', '00 不区分研究方向', '9', '联系学院',
                 '▲①101思想政治理论▲②204英语（二）▲③302数学（二）★④881数字电子技术',
                 '复试', '加试', '初试书目', '复试书目')
    if unsafe_exam_span:
        first = first.replace('<td>▲①', '<td rowspan="2">▲①')
        second = second.replace('<td>▲①101思想政治理论▲②204英语（二）▲③302数学（二）★④881数字电子技术</td>', '')
    return f'<title>{title}-电子学院</title><table><tr><td colspan="11">{title}</td></tr>{header}{first}{second}</table>'


def split_catalog(*, span=' rowspan="2"', title='004 信息科学与工程学院2027年硕士研究生招生目录',
                  second_exam='', first_exam='①101思想政治理论②201英语一③301数学一④807电路原理、模拟电子技术'):
    header = row('专业代码', '专业名称', '拟招生人数', '初试科目', '复试科目')
    first = ('<tr><td>080900</td><td>电子科学与技术</td><td>38</td>'
             f'<td{span}>{first_exam}</td><td>④999禁止提取</td></tr>')
    second = row('085408', '光电信息工程', '58', *([second_exam] if second_exam else []), 'M01数字电子技术')
    return f'<title>{title}-招生网</title><h1>{title}</h1><table>{header}{first}{second}</table>'


class GroupedHtmlExamCatalogTests(unittest.TestCase):
    def test_split_catalog_reads_explicit_exam_span_and_cites_its_actual_origin(self):
        first, shared = parse_catalog_html_blocks(split_catalog(), UNIT, 2027, DOCUMENT)['programs']
        self.assertEqual(first['id'], '123:2027:004:080900::')
        self.assertEqual(shared['id'], '123:2027:004:085408::')
        for program in (first, shared):
            self.assertEqual([subject['code'] for subject in program['subjects']], ['101', '201', '301', '807'])
            self.assertEqual(program['subjects'][-1]['name'], '电路原理、模拟电子技术')
            exam, = program['exam_evidence']
            self.assertEqual((exam['row_index'], exam['column_index']), (2, 4))
        self.assertEqual(shared['source_locator']['row'], 3)
        self.assertEqual(shared['source_locator']['exam_row'], 2)
        self.assertIsNone(shared['study_mode'])

    def test_split_catalog_never_fills_blank_scope_or_treats_retest_as_initial(self):
        for html, expected in (
            (split_catalog(span=''), ['080900']),
            (split_catalog(second_exam='①101思想政治理论②201英语一③301数学一④888其它'), ['080900']),
            (split_catalog(span=' colspan="2"'), []),
            (split_catalog(title='004 信息科学与工程学院2026年硕士研究生招生目录'), []),
            (split_catalog(title='其它大学004 信息科学与工程学院2027年硕士研究生招生目录'), []),
            (split_catalog(title='004 其它大学信息科学与工程学院2027年硕士研究生招生目录'), []),
            (split_catalog().replace('<table>', '<table><tbody>').replace('</tr><tr><td>085408',
             '</tr></tbody><tbody><tr><td>085408').replace('</table>', '</tbody></table>'), ['080900']),
            (split_catalog().replace('<table>', '<table><tbody>').replace('</tr><tr><td>085408',
             '</tr></tbody><tr><td>085408'), ['080900']),
            (split_catalog().replace('复试科目', '初试科目'), []),
        ):
            with self.subTest(expected=expected, html=html):
                result = parse_catalog_html_blocks(html, UNIT, 2027, DOCUMENT)
                self.assertEqual([program['code'] for program in result['programs']], expected)
        self.assertFalse(parse_catalog_html_blocks(
            split_catalog(), UNIT, 2027, {**DOCUMENT, 'url': 'https://other.edu.cn/catalog.html'})['programs'])

    def test_college_catalog_binds_each_explicit_exam_cell_under_merged_department(self):
        rows = parse_catalog_html_blocks(college_catalog(), UNIT, 2027, DOCUMENT)['programs']
        first, second = rows
        self.assertEqual([s['code'] for s in first['subjects']], ['101', '204', '333', '846'])
        self.assertEqual([s['code'] for s in second['subjects']], ['101', '204', '302', '881'])
        self.assertEqual(second['department'], '电子学院')
        self.assertIsNone(second['study_mode'])
        self.assertEqual(second['source_locator'], {'table': 1, 'row': 4, 'department_row': 3})
        exam, = second['exam_evidence']
        self.assertEqual(exam['column_index'], 6)
        self.assertTrue(exam['quote'].endswith('★④881数字电子技术'))

    def test_college_catalog_rejects_carried_exam_conflicting_owner_and_wrong_year(self):
        for html in (college_catalog(owner='其它学院'), college_catalog(year=2026)):
            self.assertFalse(parse_catalog_html_blocks(html, UNIT, 2027, DOCUMENT)['programs'])
        rows = parse_catalog_html_blocks(college_catalog(unsafe_exam_span=True), UNIT, 2027, DOCUMENT)['programs']
        self.assertNotIn('085408', [program['code'] for program in rows])

    def test_major_header_and_explicit_exam_column_keep_separate_citations(self):
        program, = parse(headed())
        self.assertEqual(program['id'], '123:2027:024:045103:01:全日制')
        self.assertEqual(program['name'], '学科教学（语文）')
        self.assertEqual([s['code'] for s in program['subjects']], ['101', '204', '333', '847'])
        self.assertEqual([r['row_index'] for r in program['evidence']], [1, 2, 4, 4])
        self.assertEqual(program['exam_evidence'][0]['column_index'], 5)
        self.assertEqual(program['exam_evidence'][0]['url'], DOCUMENT['url'])
        self.assertFalse(program['eligibility_evidence_complete'])

    def test_separate_subject_lines_are_bound_to_one_explicit_direction(self):
        program, = parse(listed(), title='测试大学2027年硕士研究生专业目录')
        self.assertEqual(program['id'], '123:2027:025:035200:00:全日制')
        self.assertEqual([s['code'] for s in program['subjects']], ['101', '204', '331', '437'])
        self.assertEqual([r['row_index'] for r in program['exam_evidence']], [5, 6, 7, 8])
        self.assertTrue(all(r['column_index'] == 3 for r in program['exam_evidence']))
        self.assertEqual(program['subjects'][3]['evidence']['quote'], '④437社会工作实务')

    def test_reject_wrong_year_title_school_or_host(self):
        for title in ('2026年测试大学硕士研究生招生专业目录', '测试大学2027年硕士研究生招生专业目录调整公告',
                      '其它大学2027年硕士研究生专业目录'):
            with self.subTest(title=title):
                self.assertEqual(parse(headed(), title=title), [])
        self.assertEqual(parse(headed(), document={**DOCUMENT, 'url': 'https://other.edu.cn/catalog.html'}), [])

    def test_reject_hidden_cells_conflicting_heads_and_duplicate_direction(self):
        for rows in (
            headed().replace('④847 阅读与写作', '<span hidden>④847 写作</span>'),
            headed().replace('①101 思想政治理论<br>', '<span style="visibility:hidden">①101 思想政治理论</span><br>'),
            headed().replace(HEADED_HEADER, HEADED_HEADER.replace('初试科目', '参考书目')),
            headed().replace(HEADED_DIRECTION, HEADED_DIRECTION + HEADED_DIRECTION),
            headed().replace(HEADED_DIRECTION, row('不明培养单位') + HEADED_DIRECTION),
        ):
            with self.subTest(rows=rows):
                self.assertEqual(parse(rows), [])

    def test_reject_ambiguous_or_incomplete_listed_exam_blocks(self):
        for rows in (
            listed().replace('<td></td><td></td><td>②', '<td>01(全日制)社会服务</td><td></td><td>②'),
            listed().replace('③331社会工作原理', ''),
            listed().replace('②204英语二', '③204英语二'),
            listed().replace('2027年专业学位类专业目录', '2026年专业学位类专业目录'),
            listed().replace('035200社会工作', '035200社会工作<br>045400应用心理'),
        ):
            with self.subTest(rows=rows):
                self.assertEqual(parse(rows), [])

    def test_college_brochure_keeps_separate_subject_columns_and_unknown_mode(self):
        programs = parse_catalog_html_blocks(brochure(), UNIT, 2027, DOCUMENT)['programs']
        majors = {item['code']: item for item in programs}
        self.assertEqual(set(majors), {'083000', '085701'})
        self.assertEqual([s['code'] for s in majors['083000']['subjects']], ['101', '201', '302', '817'])
        found = majors['085701']
        self.assertEqual([s['code'] for s in found['subjects']], ['101', '204', '302', '817'])
        self.assertEqual(found['department'], '环境科学与工程学院')
        self.assertTrue(all(not found[key] for key in ('study_mode', 'direction')))
        self.assertEqual(found['direction_scope'], 'program')
        self.assertEqual(found['exam_evidence'][0]['column_index'], 2)
        self.assertFalse(found['eligibility_evidence_complete'])

    def test_excludes_a_major_explicitly_closed_to_unified_exam_students(self):
        for label in ('（仅招收推免）', '（不接收统考）'):
            with self.subTest(label=label):
                self.assertEqual(parse(headed().replace('学科教学（语文）', '学科教学（语文）'+label)), [])
                self.assertEqual(parse(listed().replace('035200社会工作', '035200社会工作'+label)), [])
                programs = parse_catalog_html_blocks(
                    brochure().replace('085701<br>环境工程', '085701<br>环境工程'+label),
                    UNIT, 2027, DOCUMENT)['programs']
                self.assertEqual([program['code'] for program in programs], ['083000'])

    def test_brochure_accepts_own_labeled_exam_section_only(self):
        for original in (
            brochure().replace('2027年', '2026年'),
            '<h2>环境科学与工程学院2026年硕士研究生招生简章</h2>' + brochure(),
            brochure().replace('招生单位名称：环境科学与工程学院', '招生单位名称：其它学院'),
            brochure().replace('（四）招生专业及考试科目', '（四）考试大纲'),
            brochure().replace('<td rowspan="2">加试科目', '<td>加试科目')
                      .replace('<td>①101思想政治理论②201', '<td rowspan="2">①101思想政治理论②201'),
            brochure().replace('测试大学环境科学与工程学科。', '其它大学环境科学与工程学科。'),
        ):
            self.assertEqual(parse_catalog_html_blocks(original, UNIT, 2027, DOCUMENT)['programs'], [])
