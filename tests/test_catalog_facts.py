import hashlib
import json
import unittest

from collectors.catalog_facts import parse_catalog_facts, audit_catalog_facts


UNIT = {'id': '123', 'name': '样本大学', 'unit_code': '10001', 'official_url': 'https://grad.sample.edu.cn/'}
URL = 'https://grad.sample.edu.cn/2027/catalog.htm'
EXAM = '①101思想政治理论②201英语（一）或203日语③301数学（一）④801专业基础'


def source(table, title='样本大学2027年硕士研究生招生专业目录', **extra):
    raw = ('<html><head><title>'+title+'</title></head><body><h1>'+title+'</h1>'+table+'</body></html>').encode()
    doc = {'id': 'sample-catalog', 'unit_id': '123', 'year': 2027, 'url': URL,
           'sha256': hashlib.sha256(raw).hexdigest(), 'kind': 'catalog', 'content_type': 'text/html', **extra}
    return raw, doc


def table(rows, headers=('专业代码', '专业名称', '初试科目')):
    return '<table><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr>'+''.join(
        '<tr>'+''.join('<td>'+c+'</td>' for c in row)+'</tr>' for row in rows)+'</table>'


def snapshot(document, headers, values):
    return {'source_sha256': document['sha256'], 'auditor': 'browser_dom_v1', 'tables': [[
        [{'text': value, 'origin_row': number, 'column': index+1}
         for index, value in enumerate(row)] for number, row in enumerate((headers, values), 1)]]}


class CatalogFactTests(unittest.TestCase):
    def test_audit_checks_exact_identity_optional_values_and_required_evidence(self):
        headers = ('专业代码', '专业名称', '初试科目', '学院', '研究方向', '考试方式', '备注')
        values = ('081200', '计算机科学与技术', EXAM, '001信息学院', '01人工智能', '全国统考', '原文备注')
        raw, doc = source(table([values], headers))
        facts = parse_catalog_facts(raw, UNIT, 2027, doc)['facts']
        dom = snapshot(doc, headers, values)
        self.assertTrue(audit_catalog_facts(raw, UNIT, 2027, doc, facts, dom)['verified'])
        changes = {'code': '0812', 'name': '计算机', 'department': '999虚构学院',
                   'direction': '99不存在的方向', 'admission_method': '22推荐免试', 'notes': '虚构备注'}
        for field, value in changes.items():
            broken = json.loads(json.dumps(facts)); broken[0][field] = value
            self.assertFalse(audit_catalog_facts(raw, UNIT, 2027, doc, broken, dom)['verified'], field)
            broken = json.loads(json.dumps(facts)); broken[0]['field_evidence'][field] = []
            self.assertFalse(audit_catalog_facts(raw, UNIT, 2027, doc, broken, dom)['verified'], 'dropped '+field)
        for field, value in [('source_document_id', 'other-document'), ('unit_name', '其他大学')]:
            broken = json.loads(json.dumps(facts)); broken[0][field] = value
            self.assertFalse(audit_catalog_facts(raw, UNIT, 2027, doc, broken, dom)['verified'], field)

    def test_malformed_fact_evidence_fails_closed_without_raising(self):
        raw, doc = source(table([('081200', '计算机科学与技术', EXAM)]))
        facts = parse_catalog_facts(raw, UNIT, 2027, doc)['facts']
        dom = snapshot(doc, ('专业代码', '专业名称', '初试科目'), ('081200', '计算机科学与技术', EXAM))
        for field, value in [('field_evidence', []), ('field_evidence', {'code': None}),
                             ('row_evidence', [None]), ('source_locator', None), ('subjects', [None])]:
            broken = json.loads(json.dumps(facts)); broken[0][field] = value
            self.assertFalse(audit_catalog_facts(raw, UNIT, 2027, doc, broken, dom)['verified'], field)

    def test_combined_identity_and_embedded_direction_are_exactly_audited(self):
        for identity, expected_direction in [('081200计算机科学与技术01人工智能02软件安全', '01人工智能02软件安全'),
                                              ('计算机科学与技术（081200）', None)]:
            headers, values = ('专业代码及名称', '初试科目'), (identity, EXAM)
            raw, doc = source(table([values], headers))
            facts = parse_catalog_facts(raw, UNIT, 2027, doc)['facts']
            self.assertEqual(facts[0]['direction'], expected_direction)
            dom = snapshot(doc, headers, values)
            self.assertTrue(audit_catalog_facts(raw, UNIT, 2027, doc, facts, dom)['verified'])
            for field, value in [('code', '0812'), ('name', '计算机'), ('direction', '01虚构')]:
                broken = json.loads(json.dumps(facts)); broken[0][field] = value
                self.assertFalse(audit_catalog_facts(raw, UNIT, 2027, doc, broken, dom)['verified'], field)

    def test_recommendation_only_rows_are_excluded_but_negative_and_mixed_notes_remain(self):
        for field in ('研究方向', '备注'):
            for excluded in ('仅接收推荐免试生', '仅招收推免生', '不接收统考生', '只招收推荐免试研究生'):
                raw, doc = source(table([('081200', '计算机科学与技术', EXAM, excluded)],
                                        ('专业代码', '专业名称', '初试科目', field)))
                self.assertFalse(parse_catalog_facts(raw, UNIT, 2027, doc)['facts'], (field, excluded))
            for ordinary in ('不接收推免生', '不接收推荐免试生', '接受统考与推免', '仅接收统考生', '不招收单独考试考生'):
                raw, doc = source(table([('081200', '计算机科学与技术', EXAM, ordinary)],
                                        ('专业代码', '专业名称', '初试科目', field)))
                self.assertEqual(len(parse_catalog_facts(raw, UNIT, 2027, doc)['facts']), 1, (field, ordinary))

    def test_linked_exam_alternatives_in_exam_or_notes_cannot_be_independent_choices(self):
        linked = '①101思想政治理论②201英语一③301数学一或302数学二④801程序设计或802数据结构'
        for phrase in ('须成套选择', '须配套选择', '③④必须对应选择', '考试科目三与考试科目四组合选考'):
            for use_notes in (False, True):
                exam, note = (linked, phrase) if use_notes else (linked+'（'+phrase+'）', '')
                raw, doc = source(table([('081200', '计算机科学与技术', exam, note)],
                                        ('专业代码', '专业名称', '初试科目', '备注')))
                self.assertFalse(parse_catalog_facts(raw, UNIT, 2027, doc)['facts'], phrase)
    def test_missing_mode_is_an_original_fact_with_all_or_options_never_a_program(self):
        raw, doc = source(table([('081200', '计算机科学与技术', EXAM)]))
        result = parse_catalog_facts(raw, UNIT, 2027, doc)
        self.assertEqual(len(result['facts']), 1)
        fact = result['facts'][0]
        self.assertEqual((fact['code'], fact['name']), ('081200', '计算机科学与技术'))
        self.assertIsNone(fact['study_mode'])
        self.assertIn('study_mode', fact['missing_fields'])
        self.assertFalse(fact['matching_allowed'])
        self.assertNotIn('program_id', fact)
        self.assertNotIn('id', fact)
        self.assertEqual([s['code'] for s in fact['subjects']], ['101', '201', '203', '301', '801'])
        self.assertEqual(fact['exam_groups'][1]['selection'], 'one_of')
        self.assertEqual(fact['field_evidence']['subjects'][0]['quote'], EXAM)
        self.assertTrue(fact['fact_id'].startswith('catalog-fact-'))
        self.assertEqual(fact['fact_status'], 'pending_independent_audit')

    def test_quotes_keep_original_cell_whitespace_and_locator_never_synthesize_summary(self):
        raw, doc = source(table([('081200', '计算机科学\n与技术', '①101 思想政治理论\n②201 英语（一）③301 数学（一）④801 专业基础')]))
        fact = parse_catalog_facts(raw, UNIT, 2027, doc)['facts'][0]
        self.assertEqual(fact['field_evidence']['name'][0]['quote'], '计算机科学\n与技术')
        self.assertEqual(fact['field_evidence']['name'][0]['source_locator']['row'], 2)
        self.assertEqual(fact['field_evidence']['subjects'][0]['source_locator']['column'], 3)

    def test_missing_major_or_incomplete_exams_never_borrow_a_neighbor_row(self):
        raw, doc = source(table([('081200', '计算机科学与技术', EXAM),
                                 ('', '', '同上'), ('083500', '软件工程', '①101政治②201英语')]))
        result = parse_catalog_facts(raw, UNIT, 2027, doc)
        self.assertEqual(len(result['facts']), 1)
        self.assertTrue(result['gaps'])

    def test_explicit_rowspan_can_point_to_original_cell_but_blank_cells_cannot(self):
        html = ('<table><tr><th>专业代码</th><th>专业名称</th><th>初试科目</th><th>研究方向</th></tr>'
                '<tr><td rowspan="2">081200</td><td rowspan="2">计算机科学与技术</td><td>'+EXAM+'</td><td>01人工智能</td></tr>'
                '<tr><td>'+EXAM+'</td><td>02软件安全</td></tr></table>')
        raw, doc = source(html)
        facts = parse_catalog_facts(raw, UNIT, 2027, doc)['facts']
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[1]['field_evidence']['code'][0]['source_locator']['origin_row'], 2)
        self.assertEqual(facts[1]['source_locator']['row'], 3)
        self.assertNotEqual(facts[0]['fact_id'], facts[1]['fact_id'])

    def test_explicit_modes_and_same_source_general_mode_statement_prefer_normal_parser(self):
        raw, doc = source(table([('081200', '计算机科学与技术', EXAM, '全日制')],
                                 ('专业代码', '专业名称', '初试科目', '学习方式')))
        self.assertFalse(parse_catalog_facts(raw, UNIT, 2027, doc)['facts'])
        raw, doc = source('<p>我校硕士研究生学习方式均为全日制。</p>'+table([('081200', '计算机科学与技术', EXAM)]))
        self.assertFalse(parse_catalog_facts(raw, UNIT, 2027, doc)['facts'])

    def test_hidden_rows_wrong_year_wrong_school_hash_and_special_routes_fail_closed(self):
        for attribute in ['hidden', 'aria-hidden="true"', 'style="display:none"', 'class="d-none"']:
            html = table([('081200', '计算机科学与技术', EXAM)]).replace('<tr><td>', '<tr '+attribute+'><td>')
            raw, doc = source(html)
            self.assertFalse(parse_catalog_facts(raw, UNIT, 2027, doc)['facts'])
        for title in ['样本大学2026年硕士招生目录', '样本大学2028年硕士招生目录（附2027参考）',
                      '其他大学2027年硕士招生目录', '样本大学2027年推荐免试硕士招生目录',
                      '样本大学2027年单独考试硕士招生目录']:
            raw, doc = source(table([('081200', '计算机科学与技术', EXAM)]), title)
            self.assertFalse(parse_catalog_facts(raw, UNIT, 2027, doc)['facts'], title)
        raw, doc = source(table([('081200', '计算机科学与技术', EXAM)]))
        self.assertFalse(parse_catalog_facts(raw, UNIT, 2027, {**doc, 'sha256':'0'*64})['facts'])
        self.assertFalse(parse_catalog_facts(raw, UNIT, 2027, {**doc, 'unit_id':'999'})['facts'])

    def test_conditional_exam_groups_and_single_exam_rows_are_not_flattened(self):
        for exam in [EXAM+'（01方向）；'+EXAM+'（02方向）', '①111单独考试政治②241单独考试英语③611数学④801专业基础']:
            raw, doc = source(table([('081200', '计算机科学与技术', exam)]))
            self.assertFalse(parse_catalog_facts(raw, UNIT, 2027, doc)['facts'])

    def test_independent_dom_audit_rejects_changed_quote_subject_or_coordinate(self):
        raw, doc = source(table([('081200', '计算机科学与技术', EXAM)]))
        facts = parse_catalog_facts(raw, UNIT, 2027, doc)['facts']
        dom = {'source_sha256':doc['sha256'],'tables':[[
            [{'text':h,'origin_row':1,'column':i+1} for i,h in enumerate(['专业代码','专业名称','初试科目'])],
            [{'text':v,'origin_row':2,'column':i+1} for i,v in enumerate(['081200','计算机科学与技术',EXAM])]]],
            'auditor':'browser_dom_v1'}
        self.assertTrue(audit_catalog_facts(raw, UNIT, 2027, doc, facts, dom)['verified'])
        for mutate in ['quote', 'subject', 'coordinate', 'source', 'drop_subject', 'table', 'group', 'foreign_url', 'header']:
            broken = json.loads(json.dumps(facts))
            snapshot = json.loads(json.dumps(dom))
            if mutate=='quote':broken[0]['field_evidence']['name'][0]['quote']='软件工程'
            elif mutate=='subject':broken[0]['subjects'][-1]['name']='其他专业课'
            elif mutate=='coordinate':broken[0]['source_locator']['row']=3
            elif mutate=='source':snapshot['source_sha256']='0'*64
            elif mutate=='drop_subject':broken[0]['subjects'].pop()
            elif mutate=='table':broken[0]['source_locator']['table']=2
            elif mutate=='group':broken[0]['exam_groups'][1]['selection']='required'
            elif mutate=='header':broken[0]['source_locator']['header_row']=2
            else:broken[0]['field_evidence']['name'][0]['url']='https://other.edu.cn/wrong'
            self.assertFalse(audit_catalog_facts(raw, UNIT, 2027, doc, broken, snapshot)['verified'], mutate)

    def test_three_exam_units_with_explicit_fourth_none_keep_subject_name_exact(self):
        exam = '①101思想政治理论②201英语（一）③707医学综合④无'
        raw, doc = source(table([('100400', '公共卫生与预防医学', exam)]))
        facts = parse_catalog_facts(raw, UNIT, 2027, doc)['facts']
        dom = {'source_sha256':doc['sha256'],'auditor':'browser_dom_v1','tables':[[
            [{'text':h,'origin_row':1,'column':i+1} for i,h in enumerate(['专业代码','专业名称','初试科目'])],
            [{'text':v,'origin_row':2,'column':i+1} for i,v in enumerate(['100400','公共卫生与预防医学',exam])]]]}
        self.assertEqual(facts[0]['subjects'][-1]['name'], '医学综合')
        self.assertTrue(audit_catalog_facts(raw, UNIT, 2027, doc, facts, dom)['verified'])

    def test_declared_separate_slots_and_management_pair_preserve_full_structure(self):
        raw, doc = source(table([('125100','工商管理','199管理类综合能力','204英语（二）')],
                                 ('专业代码','专业名称','考试科目一','考试科目二')))
        facts = parse_catalog_facts(raw, UNIT, 2027, doc)['facts']
        self.assertEqual(len(facts), 1)
        self.assertEqual([s['exam_unit'] for s in facts[0]['subjects']], [1,2])
        dom = {'source_sha256':doc['sha256'],'auditor':'browser_dom_v1','tables':[[
            [{'text':h,'origin_row':1,'column':i+1} for i,h in enumerate(['专业代码','专业名称','考试科目一','考试科目二'])],
            [{'text':v,'origin_row':2,'column':i+1} for i,v in enumerate(['125100','工商管理','199管理类综合能力','204英语（二）'])]]]}
        self.assertTrue(audit_catalog_facts(raw, UNIT, 2027, doc, facts, dom)['verified'])

    def test_malformed_inputs_unpublished_year_and_explicit_non_unified_method_fail_closed(self):
        raw, doc = source(table([('081200','计算机科学与技术',EXAM,'23单独考试')],
                                 ('专业代码','专业名称','初试科目','考试方式')))
        self.assertFalse(parse_catalog_facts(raw, UNIT, 2027, doc)['facts'])
        for value in [None, b'', 'not bytes']:
            self.assertFalse(parse_catalog_facts(value, UNIT, 2027, doc)['facts'])
        self.assertFalse(parse_catalog_facts(raw, UNIT, True, doc)['facts'])
        self.assertFalse(parse_catalog_facts(raw, None, 2027, doc)['facts'])
        self.assertFalse(parse_catalog_facts(raw, UNIT, 2027, None)['facts'])
        self.assertFalse(parse_catalog_facts(raw, UNIT, 2027, {**doc,'year':[]})['facts'])

    def test_empty_or_malformed_audit_cannot_mark_a_source_complete(self):
        raw, doc = source(table([('081200','计算机科学与技术',EXAM)]))
        snapshot = {'source_sha256':doc['sha256'],'auditor':'browser_dom_v1','tables':[]}
        for facts in [[], None, [None]]:
            self.assertFalse(audit_catalog_facts(raw, UNIT, 2027, doc, facts, snapshot)['verified'])


if __name__ == '__main__':
    unittest.main()
