import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collectors.catalog_zsml import collect_zsml, parse_zsml_html, parse_zsml_tree


UNIT = {'id': '368259', 'name': '南昌大学', 'province': '江西', 'official_url': 'https://gsas.ncu.edu.cn/'}
DOCUMENT = {'id': 'source', 'url': 'https://gsas.ncu.edu.cn/zsml/sszsml/zsmlYjfx/2027/001/030100?ksfs=21'}
EXAMS = '①101思想政治理论②201英语（一）或203日语③643专业综合一④822专业综合二'
HTML = '''<table id="zsml-table"><thead><tr><th>专业及方向</th><th>初试科目</th><th>复试、同等学力加试科目</th><th>预计招生人数</th></tr></thead>
<tbody><tr class="zsml-talbe-yx-zy"><td>030100（学术学位）法学</td><td rowspan="3">''' + EXAMS + '''</td><td rowspan="3">复试科目：①0011017法理学综合；加试①0011012刑法分论</td><td rowspan="3">49</td></tr>
<tr class="zsml-talbe-yx-zy-yjfx"><td>01（全日制）法学理论</td></tr><tr class="zsml-talbe-yx-zy-yjfx"><td>02（非全日制）宪法学</td></tr>
<tr><td>备注</td><td colspan="3">同等学力条件须核验。</td></tr></tbody></table>'''
TREE = [{'id': '001', 'name': '001 法学院', 'children': [{'id': '030100', 'name': '030100 法学', 'scode': '21'}]}]


class ZsmlParserTests(unittest.TestCase):
    def test_initial_exams_follow_headers_not_the_old_zhengzhou_column_index(self):
        result = parse_zsml_html(HTML, UNIT, '001 法学院', 2027, DOCUMENT)
        self.assertEqual(result['expected_records'], 2)
        self.assertEqual(result['issues'], [])
        self.assertEqual(len(result['programs']), 2)
        row = result['programs'][0]
        self.assertEqual([s['code'] for s in row['subjects']], ['101', '201', '203', '643', '822'])
        self.assertEqual([s['exam_unit'] for s in row['subjects']], [1, 2, 2, 3, 4])
        self.assertEqual(row['subjects'][1]['choice_group'], row['subjects'][2]['choice_group'])
        self.assertEqual(row['subjects'][-1]['scope'], UNIT['id'])
        self.assertEqual(row['degree_type'], '学术学位')
        self.assertEqual(row['exam_evidence']['quote'], EXAMS)
        self.assertNotIn('001', [s['code'] for s in row['subjects']])
        self.assertTrue(row['requires_review'])
        self.assertFalse(row['eligibility_evidence_complete'])
        self.assertEqual(row['planned_enrollment']['scope'], 'department_program')
        self.assertIsNone(row['planned_enrollment']['includes_recommendation_exemption'])
        self.assertEqual(result['programs'][1]['study_mode'], '非全日制')

    def test_missing_and_ambiguous_initial_headers_cannot_parse_a_retest_column(self):
        for replacement in ['复试科目', '初试科目与复试科目']:
            result = parse_zsml_html(HTML.replace('<th>初试科目</th>', '<th>'+replacement+'</th>'), UNIT, '001 法学院', 2027, DOCUMENT)
            self.assertTrue(result['issues'])
            self.assertEqual(result['programs'], [])

    def test_malformed_direction_is_counted_and_missing_subjects_block_completeness(self):
        result = parse_zsml_html(HTML.replace('02（非全日制）宪法学', '方向无法识别'), UNIT, '001 法学院', 2027, DOCUMENT)
        self.assertEqual(result['expected_records'], 2)
        self.assertEqual(len(result['programs']), 1)
        self.assertTrue(result['issues'])
        result = parse_zsml_html(HTML.replace(EXAMS, '①101思想政治理论③待公布'), UNIT, '001 法学院', 2027, DOCUMENT)
        self.assertTrue(result['issues'])

    def test_tree_rejects_duplicates_invalid_shapes_and_ignores_other_exam_types(self):
        tree = copy.deepcopy(TREE)
        tree[0]['children'].append({'id': '035101', 'name': '035101 法律', 'scode': '23'})
        result = parse_zsml_tree(tree)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['department_id'], '001')
        self.assertEqual(result[0]['code'], '030100')
        for invalid in [{}, [], [{'id':'001','name':'法学院','children':'wrong'}], [TREE[0], TREE[0]], [{'id':'001','name':'法学院','children':[{'id':'0301','name':'法学','scode':'21'}]}]]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_zsml_tree(invalid)

    def test_tree_cannot_silently_drop_a_program_whose_exam_type_is_missing(self):
        missing = copy.deepcopy(TREE)
        missing[0]['children'].append({'id': '035101', 'name': '法律'})
        with self.assertRaises(ValueError):
            parse_zsml_tree(missing)

    def test_tree_preserves_opaque_routes_separately_from_declared_program_codes(self):
        tree = copy.deepcopy(TREE)
        tree[0]['children'] = [
            {'id': '030100_1', 'name': '030100 法学', 'scode': '21'},
            {'id': '030100_2', 'name': '030100 法学', 'scode': '21'},
        ]
        nodes = parse_zsml_tree(tree)
        self.assertEqual([node['route_id'] for node in nodes], ['030100_1', '030100_2'])
        self.assertEqual([node['code'] for node in nodes], ['030100', '030100'])
        self.assertTrue(all('study_mode' not in node for node in nodes))
        for route, title in [('030100_1', '035101 法律'), ('../030100', '030100 法学'),
                             ('030100_1', '法学'), ('030100_x', '030100 法学')]:
            invalid = copy.deepcopy(tree)
            invalid[0]['children'][0] = {'id': route, 'name': title, 'scode': '21'}
            with self.subTest(route=route, title=title), self.assertRaises(ValueError):
                parse_zsml_tree(invalid)

    def test_explicit_recommendation_only_notes_exclude_only_exact_named_directions(self):
        cases = [
            ('02日语笔译方向仅招收推免生。', [('01', '英语笔译'), ('02', '日语笔译')], ['01']),
            ('01纺织工程、03染整工程方向仅招收推免生。',
             [('01', '纺织工程'), ('02', '材料工程'), ('03', '染整工程')], ['02']),
        ]
        for note, directions, retained in cases:
            with self.subTest(note=note):
                initial = retained[0] + '方向：' + EXAMS
                html = ('<table id="zsml-table"><tr><th>专业及方向</th><th>初试科目</th></tr>'
                        '<tr class="zsml-talbe-yx-zy"><td>030100 法学</td></tr>' + ''.join(
                    '<tr class="zsml-talbe-yx-zy-yjfx"><td>' + code + '（全日制）' + name + '</td><td>' + initial + '</td></tr>'
                    for code, name in directions) + '<tr><td>备注</td><td>1.本硕士点不招收同等学力考生； 2.' + note + '</td></tr></table>')
                result = parse_zsml_html(html, UNIT, '001 法学院', 2027, DOCUMENT)
                self.assertEqual(result['issues'], [])
                self.assertEqual(result['expected_records'], len(retained))
                self.assertEqual([p['direction'].split()[0] for p in result['programs']], retained)
                self.assertEqual(result['excluded_recommendation_only'], len(directions) - len(retained))
                self.assertTrue(all(note in e['evidence'][1]['quote'] for e in result['excluded_records']))
                self.assertTrue(all(e.get('id') and e.get('direction') for e in result['excluded_records']))
                ambiguous = parse_zsml_html(html.replace(note, note.replace(directions[-1][1], '其他方向名称')),
                                            UNIT, '001 法学院', 2027, DOCUMENT)
                self.assertEqual(ambiguous['excluded_recommendation_only'], 0)

    def test_collect_uses_opaque_route_and_table_mode_without_interpreting_suffix(self):
        tree = copy.deepcopy(TREE)
        tree[0]['children'][0]['id'] = '030100_2'
        with tempfile.TemporaryDirectory() as folder:
            requested = []
            def fetch(url, *args, **kwargs):
                requested.append(url)
                if url.endswith('getTreeData'):
                    return {'id':'tree','url':url}, json.dumps(tree).encode()
                return {**DOCUMENT, 'url':url}, HTML.encode()
            with patch('collectors.catalog_zsml.fetch_snapshot', side_effect=fetch):
                result = collect_zsml(UNIT, Path(folder)/'data', base_url='https://gsas.ncu.edu.cn', offline=True)
            self.assertIn('/001/030100_2?ksfs=21', requested[-1])
            self.assertEqual(result['issues'], [])
            self.assertEqual([row['study_mode'] for row in result['programs']], ['全日制', '非全日制'])

    def test_qinghai_header_variant_keeps_quota_before_initial_exam_columns(self):
        html = '''<table id="zsml-table"><tr><th>专业、方向及学习方式</th><th>拟招生人数</th><th>初试科目</th><th>复试、同等学力加试科目</th></tr>
        <tr class="zsml-talbe-yx-zy"><td>030501（学术学位）马克思主义基本原理（全国统考）</td>
        <td rowspan="2">11</td><td rowspan="2">①101思想政治理论②201英语（一）③615马克思主义基本原理④805中国马克思主义理论与实践</td>
        <td rowspan="2">复试科目：①F00100001英语口语</td></tr>
        <tr class="zsml-talbe-yx-zy-yjfx"><td>00（全日制）不区分研究方向</td></tr></table>'''
        result = parse_zsml_html(html, UNIT, '001 马克思主义学院', 2027, DOCUMENT)
        self.assertEqual(result['issues'], [])
        row = result['programs'][0]
        self.assertEqual(row['name'], '马克思主义基本原理')
        self.assertEqual([subject['code'] for subject in row['subjects']], ['101', '201', '615', '805'])
        self.assertEqual(row['planned_enrollment']['count'], 11)
        self.assertEqual(row['planned_enrollment']['scope'], 'department_program')

    def test_direction_scoped_initial_blocks_never_spill_into_other_directions(self):
        grouped = ('01（全日制）方向：①101思想政治理论②201英语（一）③352口腔综合④--无 '
                   '02（非全日制）方向：①101思想政治理论②204英语（二）③352口腔综合④--无')
        result = parse_zsml_html(HTML.replace(EXAMS, grouped), UNIT, '001 法学院', 2027, DOCUMENT)
        self.assertEqual(result['issues'], [])
        self.assertEqual([s['code'] for s in result['programs'][0]['subjects']], ['101', '201', '352'])
        self.assertEqual([s['code'] for s in result['programs'][1]['subjects']], ['101', '204', '352'])
        self.assertEqual(result['programs'][0]['exam_evidence']['quote'],
                         '①101思想政治理论②201英语（一）③352口腔综合④--无')
        missing = parse_zsml_html(HTML.replace(EXAMS, grouped.replace('02（非全日制）方向：', '03（非全日制）方向：')),
                                  UNIT, '001 法学院', 2027, DOCUMENT)
        self.assertTrue(missing['issues'])
        self.assertEqual(missing['programs'][1]['subjects'], [])

    def test_direction_scopes_without_repeated_study_mode_never_merge_exam_routes(self):
        grouped = ('01方向：①101思想政治理论②201英语（一）③622物理化学④859无机化学 '
                   '02方向：①101思想政治理论②201英语（一）③642高分子化学④875高分子物理')
        result = parse_zsml_html(HTML.replace(EXAMS, grouped), UNIT, '001 法学院', 2027, DOCUMENT)
        self.assertEqual(result['issues'], [])
        self.assertEqual([s['code'] for s in result['programs'][0]['subjects']], ['101', '201', '622', '859'])
        self.assertEqual([s['code'] for s in result['programs'][1]['subjects']], ['101', '201', '642', '875'])

    def test_ecnu_flags_fees_and_campus_follow_their_own_columns(self):
        html = '''<table id="zsml-table"><tr><th>学科及研究方向</th><th>总计划</th><th>含推免计划</th>
        <th>初试科目</th><th>推免招生</th><th>统考招生</th><th>培养 校区</th><th>学制</th><th>总学费</th></tr>
        <tr class="zsml-talbe-yx-zy"><td>045100（专业学位）教育</td></tr>
        <tr class="zsml-talbe-yx-zy-yjfx"><td>01（非全日制）学科教学（语文）</td><td>22</td><td>0</td>
        <td>①101-思想政治理论②204-英语（二）③333-教育综合④847-文学基础</td><td>否</td><td>是</td><td>闵行校区</td><td>3年</td><td>6.6万元</td></tr>
        <tr class="zsml-talbe-yx-zy-yjfx"><td>02（全日制）仅推免方向</td><td>2</td><td>2</td>
        <td></td><td>是</td><td>否</td><td>普陀校区</td><td>3年</td><td>2.4万元</td></tr></table>'''
        result = parse_zsml_html(html, UNIT, '101 教育学院', 2027, DOCUMENT)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 1)
        self.assertEqual(result['excluded_recommendation_only'], 1)
        row = result['programs'][0]
        self.assertEqual(row['tuition']['amount'], 66000)
        self.assertEqual(row['tuition']['period'], 'total')
        self.assertEqual(row['duration_years'], 3)
        self.assertEqual(row['campus'], '闵行校区')
        self.assertEqual(row['subjects'][0]['name'], '思想政治理论')
        self.assertEqual(row['subjects'][0]['scope'], 'national')
        self.assertIn('101-思想政治理论', row['exam_evidence']['quote'])
        self.assertTrue(row['planned_enrollment']['includes_recommendation_exemption'])
        unknown = parse_zsml_html(html.replace('<td>否</td><td>普陀校区', '<td>待公布</td><td>普陀校区'), UNIT, '101 教育学院', 2027, DOCUMENT)
        self.assertTrue(unknown['issues'])
        self.assertEqual(unknown['excluded_recommendation_only'], 0)

    def test_ustc_header_reads_unified_exam_plan_without_using_recommendation_column(self):
        html = '''<table id="zsml-table"><tr><th>研究方向</th><th>拟接收 统考数</th><th>拟接收 推免硕士数</th><th>初试科目</th></tr>
        <tr class="zsml-talbe-yx-zy"><td>070100（学术学位）数学</td><td rowspan="2">15</td><td rowspan="2">60</td>
        <td rowspan="2">①101思想政治理论②201英语（一）③620数学分析④842线性代数与解析几何</td></tr>
        <tr class="zsml-talbe-yx-zy-yjfx"><td>00（全日制）不区分研究方向</td></tr></table>'''
        result = parse_zsml_html(html, UNIT, '001 数学学院', 2027, DOCUMENT)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['programs'][0]['planned_enrollment']['count'], 15)
        self.assertFalse(result['programs'][0]['planned_enrollment']['includes_recommendation_exemption'])

    def test_duration_uses_only_explicit_same_table_scope(self):
        value = HTML.replace('<tr><td>备注</td>',
                             '<tr><td>学制及考试特殊用具说明</td><td colspan="3">学制三年，绘图工具自备</td></tr><tr><td>备注</td>')
        result = parse_zsml_html(value, UNIT, '001 法学院', 2027, DOCUMENT)
        self.assertEqual([p['duration_years'] for p in result['programs']], [3, 3])
        self.assertEqual(result['programs'][0]['duration_evidence']['quote'], '学制三年，绘图工具自备')
        ambiguous = parse_zsml_html(value.replace('学制三年，绘图工具自备', '全日制学制三年，非全日制二年'),
                                    UNIT, '001 法学院', 2027, DOCUMENT)
        self.assertTrue(all(p['duration_years'] is None for p in ambiguous['programs']))

    def test_table_declarations_and_tree_identity_are_never_mutated(self):
        before = copy.deepcopy(UNIT)
        result = parse_zsml_html(HTML, UNIT, '001 法学院', 2027, DOCUMENT)
        self.assertEqual(UNIT, before)
        self.assertTrue(all(row['year'] == 2027 for row in result['programs']))
        self.assertEqual(result['programs'][0]['id'], '368259:2027:001:030100:01:全日制')

    def test_collect_preserves_full_tree_and_failure_counts_without_synthetic_directions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def fetch(url, directory, unit, year, kind, title, **kwargs):
                if url.endswith('getTreeData'):
                    return {'id':'tree','url':url}, json.dumps(TREE).encode()
                return dict(DOCUMENT), HTML.encode()
            with patch('collectors.catalog_zsml.fetch_snapshot', side_effect=fetch):
                value = collect_zsml(UNIT, root/'data/snapshots/catalogs', base_url='https://gsas.ncu.edu.cn', project_root=root, offline=True)
            self.assertEqual(value['expected_nodes'], 1)
            self.assertEqual(value['fetched_nodes'], 1)
            self.assertEqual(value['failed_nodes'], [])
            self.assertEqual(len(value['programs']), 2)
            self.assertEqual(value['documents'][0]['catalog_parser'], 'zsml_tree')
            self.assertEqual(value['documents'][1]['catalog_parser'], 'zsml_table')


if __name__ == '__main__':
    unittest.main()
