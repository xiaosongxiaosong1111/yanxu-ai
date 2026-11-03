import copy
import json
import unittest

from collectors.catalog_chsi import parse_chsi_detail_html, parse_chsi_directions_json


UNIT = {'id': '367878', 'name': '北京大学', 'unit_code': '10001', 'province': '北京'}
DOC = {'id': 'directions', 'url': 'https://yz.chsi.com.cn/zsml/rs/yjfxs.do', 'method': 'POST',
       'request_body': 'dwdm=10001&zydm=010101'}
CONTEXT = '''<h1>{{curYear}}年硕士专业目录</h1><script>
cxvo: {"schId":"367878","dwdm":"10001","dwmc":"北京大学"},
curYear: "2027", xxfsList: [{value: '1', name: '全日制'}, {value: '2', name: '非全日制'}]
</script>'''


def combination(language='201', name='英语（一）', third='611'):
    return {f'km{i}Vo': {'kskmdm': code, 'kskmmc': title, 'cksm': '见招生简章'}
            for i, code, title in [(1, '101', '思想政治理论'), (2, language, name),
                                    (3, third, '哲学综合'), (4, '824', '马克思主义哲学')]}


ROW = {'id': 'bc0b537d2729eb089f2c49661b286a75', 'dwdm': '10001', 'dwmc': '北京大学',
       'schId': '367878', 'ksfsdm': '21', 'ksfsmc': '统考', 'yxsdm': '023', 'yxsmc': '哲学系',
       'zydm': '010101', 'zymc': '马克思主义哲学', 'xwlx': 'xs', 'yjfxdm': '01',
       'yjfxmc': '马克思主义哲学史', 'xxfs': '1', 'nzsrs': 0, 'nzsrsstr': '专业：2(不含推免)',
       'zybz': '考试科目③“哲学综合”考察中国哲学和西方哲学基础知识。', 'kskmz': [combination()]}


def detail_html(row=ROW):
    values = [('招生单位：', '(10001)北京大学'), ('考试方式：', '统考'), ('院系所：', '(023)哲学系'),
              ('专业：', "(010101)({{xwlx === 'xs' ? '学术学位' : '专业学位'}})马克思主义哲学"),
              ('学习方式：', "{{1==1 ? '全日制' : '非全日制'}}"), ('研究方向：', '(01)马克思主义哲学史'),
              ('拟招生人数：', '专业：2(不含推免)')]
    return ('<h1>{{curYear}}年硕士专业目录</h1>' + ''.join(
        '<div class="name">' + label + '</div><div class="value">' + value + '</div>' for label, value in values)
        + '<template v-for="ite,ind in kskm">' + ''.join('{{ite.km' + str(slot) + 'Vo.kskmdm}}' for slot in range(1, 5)) + '</template>'
        + '<script>\n id: ' + json.dumps(row['id']) + ',\n bz: ' + json.dumps(row['zybz'])
        + ',\n kskm: ' + json.dumps(row['kskmz']) + ',\n xwlx: "xs",\n curYear: "2027",\n</script>')


def payload(rows=None, total=None):
    rows = copy.deepcopy([ROW] if rows is None else rows)
    return {'zsmlcxModel': {'dwdm': '10001', 'zydm': '010101', 'start': '0'},
            'msg': {'curPage': 1, 'pageCount': 10, 'totalCount': len(rows) if total is None else total,
                    'size': 10, 'list': rows}}


class ChsiDirectionsTests(unittest.TestCase):
    def parse(self, value, context=CONTEXT):
        return parse_chsi_directions_json(value, UNIT, 2027, DOC, context_html=context)

    def test_visible_json_identities_scope_and_quota_are_preserved_without_zero_placeholder(self):
        raw = payload()
        before = copy.deepcopy(raw)
        result = self.parse(raw)
        self.assertEqual(raw, before)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 1)
        self.assertFalse(result['catalog_scope_complete'])
        self.assertTrue(result['not_publishable_without_detail'])
        row = result['programs'][0]
        self.assertTrue(row['requires_public_gate_proof'])
        self.assertFalse(row['catalog_publishable'])
        self.assertEqual(row['id'], '367878:2027:023:010101:01:全日制')
        self.assertEqual(row['planned_enrollment']['count'], 2)
        self.assertEqual(row['planned_enrollment']['scope'], 'department_program')
        self.assertFalse(row['planned_enrollment']['includes_recommendation_exemption'])
        self.assertTrue(row['requires_review'])
        self.assertFalse(row['eligibility_evidence_complete'])
        self.assertEqual(row['chsi_direction_id'], ROW['id'])
        self.assertEqual(row['catalog_document_id'], DOC['id'])
        empty = copy.deepcopy(ROW); empty['nzsrsstr'] = ''
        self.assertIsNone(self.parse(payload([empty]))['programs'][0].get('planned_enrollment'))

    def test_year_and_school_must_be_declared_by_saved_official_page(self):
        for context in [CONTEXT.replace('2027', '2026'), CONTEXT.replace('10001', '10002'),
                        CONTEXT.replace('367878', '367879'), CONTEXT.replace('北京大学', '其他大学'),
                        CONTEXT.replace('硕士专业目录', '博士专业目录')]:
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.parse(payload(), context)
        for change in [{'method': 'GET'}, {'url': 'https://yz.chsi.com.cn/zsml/yjfxdetail?id=x'},
                       {'request_body': 'dwdm=10002&zydm=010101'}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_chsi_directions_json(payload(), UNIT, 2027, {**DOC, **change}, context_html=CONTEXT)

    def test_only_unified_exam_rows_are_projected_and_unknown_types_are_not_silently_dropped(self):
        single = {**ROW, 'id': 'single', 'ksfsdm': '23', 'ksfsmc': '单独考试'}
        result = self.parse(payload([ROW, single]))
        self.assertEqual(len(result['programs']), 1)
        self.assertEqual(result['expected_records'], 1)
        self.assertEqual(result['excluded_records'][0]['source_direction_id'], 'single')
        self.assertIn('23', result['excluded_records'][0]['reason'])
        unknown = {**ROW, 'id': 'unknown', 'ksfsdm': None}
        result = self.parse(payload([unknown]))
        self.assertEqual(result['programs'], [])
        self.assertTrue(result['issues'])
        self.assertEqual(result['expected_records'], 1)
        for change in [{'ksfsdm': '99'}, {'ksfsmc': '推免'}]:
            result = self.parse(payload([{**ROW, **change}]))
            self.assertEqual(result['programs'], [])
            self.assertTrue(result['issues'])

    def test_independent_language_choices_can_be_flattened_only_when_full_combinations_are_equal(self):
        row = {**ROW, 'kskmz': [combination(), combination('202', '俄语'), combination('203', '日语'), combination('254', '德语')]}
        parsed = self.parse(payload([row]))['programs'][0]
        self.assertTrue(parsed['exam_matching_supported'])
        self.assertEqual(len(parsed['exam_combinations']), 4)
        self.assertEqual([s['code'] for s in parsed['subjects']], ['101', '201', '202', '203', '254', '611', '824'])
        self.assertTrue(all(s['choice_group'] == 'exam-unit-2' for s in parsed['subjects'][1:5]))
        self.assertEqual(parsed['subjects'][1]['scope'], 'national')
        self.assertEqual(parsed['subjects'][4]['scope'], UNIT['id'])

    def test_correlated_exam_combinations_remain_intact_and_cannot_be_mixed(self):
        row = {**ROW, 'kskmz': [combination(), combination('203', '日语', third='612')]}
        result = self.parse(payload([row])); parsed = result['programs'][0]
        self.assertFalse(parsed['exam_matching_supported'])
        self.assertEqual(parsed['subjects'], [])
        self.assertEqual(len(parsed['exam_combinations']), 2)
        self.assertTrue(result['issues'])

    def test_bad_identities_and_missing_exam_slots_fail_without_filling_from_another_row(self):
        for change in [{'dwdm': '10002'}, {'schId': 'other'}, {'zydm': '010102'}, {'xxfs': '3'}, {'yjfxdm': ''}]:
            result = self.parse(payload([{**ROW, **change}]))
            self.assertEqual(result['programs'], [])
            self.assertTrue(result['issues'])
        row = copy.deepcopy(ROW); del row['kskmz'][0]['km3Vo']
        result = self.parse(payload([row]))
        self.assertTrue(result['issues'])
        self.assertFalse(result['programs'][0]['exam_matching_supported'])

    def test_pagination_counts_do_not_turn_first_page_into_complete_catalog(self):
        result = self.parse(payload(total=25))
        self.assertFalse(result['catalog_scope_complete'])
        self.assertFalse(result['page_scope']['complete'])
        self.assertEqual(result['page_scope']['official_total'], 25)
        self.assertEqual(result['page_scope']['observed_rows'], 1)
        for malformed in [{'msg': '请登录'}, {'msg': {'list': {}}}, {'msg': {'list': []}}]:
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                self.parse(malformed)

    def test_three_exam_units_and_explicit_zero_quota_keep_actual_scope(self):
        row = copy.deepcopy(ROW)
        row['kskmz'][0]['km4Vo'] = {'kskmdm': '--', 'kskmmc': '无', 'cksm': ''}
        row['nzsrsstr'] = '研究方向：0(含推免)'
        parsed = self.parse(payload([row]))['programs'][0]
        self.assertTrue(parsed['exam_matching_supported'])
        self.assertEqual([s['exam_unit'] for s in parsed['subjects']], [1, 2, 3])
        self.assertEqual(parsed['planned_enrollment']['count'], 0)
        self.assertEqual(parsed['planned_enrollment']['scope'], 'direction')
        self.assertTrue(parsed['planned_enrollment']['includes_recommendation_exemption'])
        row['kskmz'][0]['km1Vo']['kskmmc'] = '英语'
        result = self.parse(payload([row]))
        self.assertTrue(result['issues'])
        self.assertEqual(result['programs'][0]['subjects'], [])

    def test_unknown_institution_inputs_and_duplicate_directions_never_count_as_complete(self):
        result = self.parse(payload([ROW, ROW]))
        self.assertTrue(result['issues'])
        for unit in [{}, {**UNIT, 'unit_code': None}]:
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                parse_chsi_directions_json(payload(), unit, 2027, DOC, context_html=CONTEXT)

    def test_real_vue_bound_detail_json_is_parsed_without_executing_scripts(self):
        doc = {'id': 'detail', 'url': 'https://yz.chsi.com.cn/zsml/yjfxdetail?id=' + ROW['id'], 'method': 'GET'}
        result = parse_chsi_detail_html(detail_html(), UNIT, 2027, doc, context_html=CONTEXT)
        self.assertEqual(result['issues'], [])
        row = result['programs'][0]
        self.assertEqual(row['id'], '367878:2027:023:010101:01:全日制')
        self.assertEqual([s['code'] for s in row['subjects']], ['101', '201', '611', '824'])
        self.assertEqual(row['notes'], [ROW['zybz']])
        self.assertEqual(row['catalog_document_id'], 'detail')
        self.assertEqual(row['subjects'][0]['evidence']['kind'], 'structured_fields')
        self.assertFalse(result['catalog_scope_complete'])
        for html in [detail_html().replace('v-for="ite,ind in kskm"', 'v-for="other in unrelated"'),
                     detail_html().replace('kskm: [', 'kskm: execute(['),
                     detail_html().replace('北京大学', '其他大学'),
                     detail_html().replace("{{1==1 ? '全日制' : '非全日制'}}", "{{3==1 ? '全日制' : '非全日制'}}")]:
            with self.subTest(html=html), self.assertRaises(ValueError):
                parse_chsi_detail_html(html, UNIT, 2027, doc, context_html=CONTEXT)


if __name__ == '__main__':
    unittest.main()
