import copy
from pathlib import Path
import unittest

from collectors.catalog_html import parse_catalog_html, catalog_pagination


class CatalogHTMLTests(unittest.TestCase):
    def parse(self, name, unit_id, unit_name):
        text = (Path(__file__).parent / 'fixtures' / 'catalog_html' / (name + '.html')).read_text('utf-8')
        document = {'id': 'd', 'url': 'https://yz.chsi.com.cn/sch/viewZszc--schId-' + unit_id + '.dhtml'}
        return parse_catalog_html(text, {'id': unit_id, 'name': unit_name, 'province': '测试省'}, 2027, document)

    def test_real_lnpu_spans_directions_languages_and_initial_not_retest(self):
        result = self.parse('lnpu', '368040', '辽宁石油化工大学')
        self.assertGreater(len(result['programs']), 50)
        self.assertEqual(result['expected_records'], len(result['programs']))
        self.assertEqual(result['issues'], [])
        chemical = [p for p in result['programs'] if p['code'] == '081700']
        self.assertEqual(len(chemical), 4)
        self.assertEqual({p['direction'][:2] for p in chemical}, {'01', '02', '03', '04'})
        mechanical = next(p for p in result['programs'] if p['code'] == '080200')
        languages = [s for s in mechanical['subjects'] if s['exam_unit'] == 2]
        self.assertEqual({s['code'] for s in languages}, {'201', '202', '203'})
        self.assertEqual(len({s['choice_group'] for s in languages}), 1)
        self.assertTrue(all(s['code'] != '物理化学' for p in result['programs'] for s in p['subjects']))
        self.assertTrue(all(p['eligibility_evidence_complete'] is False for p in result['programs']))

    def test_real_cqust_keeps_direction_specific_subjects(self):
        result = self.parse('cqust', '302975907', '重庆科技大学')
        self.assertEqual(result['issues'], [])
        rows = [p for p in result['programs'] if p['code'] == '082000']
        self.assertEqual(len(rows), 3)
        self.assertEqual({s['code'] for p in rows if p['direction'].startswith('03') for s in p['subjects'] if s['exam_unit'] == 4}, {'815'})
        self.assertTrue(all(p['degree_type'] == '学术学位' for p in rows))
        self.assertTrue(all(p['name'] == '石油与天然气工程' for p in rows))

    def test_real_hezhou_separate_exam_columns(self):
        result = self.parse('hezhou', '3274414302', '贺州学院')
        self.assertEqual(result['issues'], [])
        food = next(p for p in result['programs'] if p['code'] == '095500')
        self.assertEqual([s['code'] for s in food['subjects']], ['101', '204', '701', '801'])

    def test_missing_or_duplicate_units_fail_closed_without_invented_subjects(self):
        html = '<table><tr><th>学院</th><th>专业</th><th>研究方向</th><th>学习方式</th><th>初试科目</th></tr><tr><td>001计算机学院</td><td>081200计算机科学与技术</td><td>00不区分方向</td><td>全日制</td><td>①101思想政治理论②201英语（一）④408计算机学科专业基础</td></tr></table>'
        before = copy.deepcopy(html)
        result = parse_catalog_html(html, {'id': '1', 'name': '测试大学'}, 2027, {'id': 'd', 'url': 'https://example.edu.cn/catalog'})
        self.assertTrue(result['issues'])
        self.assertEqual(html, before)
        self.assertEqual(result['expected_records'], 1)


    def parse_inline(self, body):
        return parse_catalog_html(body, {'id': '1', 'name': '测试大学'}, 2027,
                                  {'id': 'd', 'url': 'https://example.edu.cn/catalog'})

    def test_missing_initial_exam_row_is_counted_and_cannot_be_complete(self):
        result = self.parse_inline('<table><tr><th>学院</th><th>专业</th><th>研究方向</th><th>学习方式</th><th>初试科目</th></tr>'
            '<tr><td>001学院</td><td>081200计算机科学与技术</td><td>00不区分方向</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr>'
            '<tr><td>001学院</td><td>083500软件工程</td><td>00不区分方向</td><td>全日制</td><td>待公布</td></tr></table>')
        self.assertEqual(result['expected_records'], 2)
        self.assertEqual(len(result['programs']), 1)
        self.assertTrue(result['issues'])

    def test_program_level_source_does_not_invent_a_direction(self):
        result = self.parse_inline('<p>我校硕士研究生为全日制硕士研究生。</p><table><tr><th>专业学位领域名称及代码</th><th>初试科目及代码</th></tr>'
            '<tr><td>食品与营养（095500）</td><td>①101思想政治理论②204英语（二）③338生物化学④838食品微生物学</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 1)
        program = result['programs'][0]
        self.assertEqual(program['code'], '095500')
        self.assertEqual(program['name'], '食品与营养')
        self.assertEqual(program['direction'], '')
        self.assertEqual(program['direction_scope'], 'program')
        self.assertEqual(program['study_mode'], '全日制')
        self.assertIn('我校硕士研究生为全日制硕士研究生。', [e['quote'] for e in program['evidence']])

    def test_hierarchical_major_rows_and_title_mode_have_literal_evidence(self):
        result = self.parse_inline('<h2>测试大学2027年全日制学术学位硕士研究生招生专业目录</h2><table><tr><th>专业代码、名称及研究方向</th><th>初试科目</th></tr>'
            '<tr><td colspan="2">020200应用经济学</td></tr><tr><td>01产业经济学</td><td>①101思想政治理论②201英语（一）③303数学（三）④801经济学基础</td></tr>'
            '<tr><td>02区域经济学</td><td>①101思想政治理论②201英语（一）③303数学（三）④801经济学基础</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 2)
        self.assertEqual({p['direction'] for p in result['programs']}, {'01产业经济学', '02区域经济学'})
        self.assertTrue(all(p['code'] == '020200' and p['name'] == '应用经济学' for p in result['programs']))

    def test_mode_plan_columns_use_positive_counts_and_do_not_drop_part_time(self):
        result = self.parse_inline('<table><tr><th>培养学院</th><th>专业名称及代码</th><th>研究方向</th><th>非全日制</th><th>全日制</th><th>初试科目</th></tr>'
            '<tr><td>100学院</td><td>045115小学教育</td><td>00不区分研究方向</td><td>20</td><td>50</td><td>①101思想政治理论②204英语（二）③333教育综合④804教育研究方法</td></tr>'
            '<tr><td>100学院</td><td>045104学科教学（数学）</td><td>00不区分研究方向</td><td>0</td><td>23</td><td>①101思想政治理论②204英语（二）③333教育综合④802数学教育概论</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 3)
        self.assertEqual(len(result['programs']), 3)
        self.assertEqual({p['study_mode'] for p in result['programs'] if p['code'] == '045115'}, {'全日制', '非全日制'})

    def test_separate_code_and_name_columns_override_name_only_major_header(self):
        result = self.parse_inline('<table><tr><th>专业</th><th>专业代码</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr>'
            '<tr><td>土木工程(学术学位)</td><td>081400</td><td>01岩土工程</td><td>全日制</td><td>①101思想政治理论②201英语一③301数学一④865结构力学复试科目：桥梁隧道工程</td></tr></table>')
        self.assertEqual(result['issues'], [])
        program = result['programs'][0]
        self.assertEqual(program['name'], '土木工程')
        self.assertEqual(program['degree_type'], '学术学位')
        self.assertEqual(program['subjects'][-1]['name'], '结构力学')

    def test_unknown_mode_stays_a_gap_and_unparseable_direction_not_program_level(self):
        result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>初试科目</th></tr>'
            '<tr><td>081200计算机科学与技术</td><td>01方向A;02方向B</td><td>①101思想政治理论②201英语一③301数学一④408计算机学科专业基础</td></tr></table>')
        self.assertEqual(result['programs'], [])
        self.assertTrue(result['issues'])


    def test_notes_and_totals_do_not_become_extra_candidates(self):
        result = self.parse_inline('<h2>测试大学2027年全日制学术学位硕士研究生招生专业目录</h2><table><tr><th>专业代码、名称及研究方向</th><th>初试科目</th><th>备注</th></tr>'
            '<tr><td colspan="3">081200计算机科学与技术</td></tr>'
            '<tr><td>01计算机应用</td><td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td><td></td></tr>'
            '<tr><td colspan="2">备注：学习方式为全日制</td><td></td></tr>'
            '<tr><td colspan="2">3.2027年拟招生人数以上级下达为准。</td><td></td></tr>'
            '<tr><td>合计</td><td>——</td><td>30</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 1)

    def test_four_digit_discipline_code_is_preserved_without_appending_zeroes(self):
        result = self.parse_inline('<p>我院招收的硕士研究生培养方式是全日制，学制为3年。</p><table><tr><th>专业名称（代码）、研究方向</th><th>初试科目（代码）</th></tr>'
            '<tr><td>材料科学与工程（0805）1.高分子材料2.先进弹性材料</td><td>①101思想政治理论②201英语（一）③302数学（二）④866高分子化学与物理</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 2)
        self.assertTrue(all(p['code'] == '0805' and p['code_level'] == 'discipline' for p in result['programs']))
        self.assertEqual({p['direction'] for p in result['programs']}, {'1.高分子材料', '2.先进弹性材料'})


    def test_unconsumed_major_heading_cannot_silently_disappear(self):
        result = self.parse_inline('<h2>测试大学2027年全日制学术学位硕士研究生招生专业目录</h2><table><tr><th>专业代码、名称及研究方向</th><th>初试科目</th></tr>'
            '<tr><td colspan="2">081200计算机科学与技术</td></tr><tr><td>01计算机应用</td><td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr>'
            '<tr><td colspan="2">083500软件工程</td></tr></table>')
        self.assertEqual(len(result['programs']), 1)
        self.assertEqual(result['expected_records'], 2)
        self.assertTrue(result['issues'])

    def test_unrecognized_catalog_table_prevents_complete_reconciliation(self):
        result = self.parse_inline('<table><tr><th>专业代码</th><th>研究方向</th><th>初试科目另附</th></tr><tr><td>081200</td><td>01计算机应用</td><td>待公布</td></tr></table>')
        self.assertTrue(result['issues'])


    def test_hust_code_hierarchy_preserves_modes_and_pipe_delimited_or_subjects(self):
        body = '<table><tr><th>编码</th><th>名称</th><th>拟招人数</th><th>考试科目</th><th>备注</th></tr>' \
            '<tr><td>403</td><td>法学院</td><td>100</td><td></td><td></td></tr>' \
            '<tr><td>035101</td><td>法律（非法学）（全日制）</td><td>30</td><td></td><td></td></tr>' \
            '<tr><td>00</td><td>不区分研究方向</td><td></td><td>①101|思想政治理论②201|英语（一）或203|日语或243|德语③398|法律硕士专业基础（非法学）④498|法律硕士综合（非法学）</td><td>不招收同等学力考生</td></tr>' \
            '<tr><td>035101</td><td>法律（非法学）（非全日制）</td><td>20</td><td></td><td></td></tr>' \
            '<tr><td>00</td><td>不区分研究方向</td><td></td><td>①101|思想政治理论②201|英语（一）③398|法律硕士专业基础（非法学）④498|法律硕士综合（非法学）</td><td></td></tr></table>'
        result = self.parse_inline(body)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 2)
        full, part = result['programs']
        self.assertEqual(full['department_code'], '403')
        self.assertEqual(full['department'], '法学院')
        self.assertEqual(full['name'], '法律（非法学）')
        self.assertEqual(full['direction'], '00不区分研究方向')
        self.assertEqual(full['catalog_document_id'], 'd')
        self.assertEqual({s['code'] for s in full['subjects'] if s['exam_unit'] == 2}, {'201', '203', '243'})
        self.assertEqual({p['study_mode'] for p in result['programs']}, {'全日制', '非全日制'})
        self.assertTrue(full['unparsed_condition_notes'])

    def test_hust_continuation_uses_original_previous_page_refs(self):
        header = '<table><tr><th>编码</th><th>名称</th><th>拟招人数</th><th>考试科目</th><th>备注</th></tr>'
        first = self.parse_inline(header + '<tr><td>530</td><td>第一临床学院</td><td>100</td><td></td><td></td></tr>'
            '<tr><td>105100</td><td>临床医学（全日制）</td><td>30</td><td></td><td></td></tr>'
            '<tr><td>01</td><td>内科学</td><td></td><td>①101|思想政治理论②201|英语（一）③306|临床医学综合能力（西医）</td><td></td></tr></table>')
        body = header + '<tr><td>02</td><td>老年医学</td><td></td><td>①101|思想政治理论②201|英语（一）③306|临床医学综合能力（西医）</td><td></td></tr></table>'
        second = parse_catalog_html(body, {'id': '1', 'name': '测试大学'}, 2027,
            {'id': 'd2', 'url': 'https://example.edu.cn/catalog'}, context=first['continuation_context'])
        self.assertEqual(second['issues'], [])
        p = second['programs'][0]
        self.assertEqual(p['code'], '105100')
        self.assertEqual(p['direction'], '02老年医学')
        self.assertEqual(p['catalog_document_id'], 'd2')
        self.assertEqual({ref['document_id'] for ref in p['evidence']}, {'d', 'd2'})
        orphan = self.parse_inline(body)
        self.assertTrue(orphan['issues'])
        self.assertEqual(orphan['programs'], [])

    def test_hust_explicit_recommendation_only_is_excluded_but_missing_exam_is_gap(self):
        body = '<table><tr><th>编码</th><th>名称</th><th>拟招人数</th><th>考试科目</th><th>备注</th></tr>' \
            '<tr><td>530</td><td>第一临床学院</td><td>100</td><td></td><td></td></tr>' \
            '<tr><td>105100</td><td>临床医学（全日制）</td><td>30</td><td></td><td></td></tr>' \
            '<tr><td>01</td><td>脑机接口</td><td></td><td></td><td>仅接收推免生，不招收同等学力考生</td></tr>' \
            '<tr><td>02</td><td>老年医学</td><td></td><td>待公布</td><td></td></tr></table>'
        result = self.parse_inline(body)
        self.assertEqual(result['expected_records'], 1)
        self.assertEqual(result['programs'], [])
        self.assertTrue(result['issues'])

    def test_direction_embedded_modes_are_not_cross_producted(self):
        result = self.parse_inline('<table><tr><th>学院代码及名称</th><th>专业代码及名称</th><th>类型</th><th>研究方向（学习方式）</th><th>初试科目</th></tr>'
            '<tr><td>001机械工程学院</td><td>085501机械工程</td><td>专业学位</td><td>01先进制造（全日制）02工程实践（非全日制）</td><td>①101思想政治理论②204英语（二）③302数学（二）④816机械原理</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 2)
        self.assertEqual({(p['direction'], p['study_mode']) for p in result['programs']}, {('01先进制造', '全日制'), ('02工程实践', '非全日制')})


    def test_hust_pager_selected_anchor_without_href_keeps_final_page_number(self):
        text = '<a id="ctl_Pager1_-1_lnkPage1_-1" href="javascript:one">1</a><a id="ctl_Pager1_-1_lnkPage2_-1" href="javascript:two">2</a><a id="ctl_Pager1_-1_lnkPage3_-1" class="aspNetDisabled none" style="color:Red;">3</a>'
        self.assertEqual(catalog_pagination(text), {'current': 3, 'total': 3})


    def test_chsi_published_hidden_html_body_is_parsed_without_changing_original_bytes(self):
        from html import escape
        content = '<h2>测试大学2027年硕士研究生招生专业目录</h2><table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>初试科目</th></tr><tr><td>081200计算机科学与技术</td><td>00不区分研究方向</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr></table>'
        source = '<input id="hid_content" type="hidden" value="' + escape(content, quote=True) + '"><div id="cnt_body_wrapper"></div><script>$("#cnt_body_wrapper").html($("#hid_content").val())</script>'
        result = parse_catalog_html(source, {'id': '1', 'name': '测试大学'}, 2027,
            {'id': 'd', 'url': 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?schCode=10001'})
        self.assertEqual(result['issues'], [])
        self.assertEqual(len(result['programs']), 1)


    def test_official_global_mode_clause_can_supply_missing_table_column(self):
        body = '<p>2027年机械总院（代码82701）招生类别为全日制、非定向硕士研究生。</p><table><tr><th>专业</th><th>研究方向</th><th>考试科目</th></tr><tr><td>081200计算机科学与技术</td><td>01计算机应用</td><td>⑴101思想政治理论⑵201英语一⑶301数学一⑷408计算机学科专业基础</td></tr></table>'
        result = self.parse_inline(body)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['programs'][0]['study_mode'], '全日制')
        self.assertEqual([s['code'] for s in result['programs'][0]['subjects']], ['101', '201', '301', '408'])
        self.assertIn('招生类别为全日制', ''.join(ref['quote'] for ref in result['programs'][0]['evidence']))

    def test_mixed_academic_professional_mode_clause_does_not_become_global_full_time(self):
        result = self.parse_inline('<p>学术学位硕士研究生的学习方式为全日制；公共管理硕士学习方式为非全日制。</p><table><tr><th>专业</th><th>研究方向</th><th>考试科目</th></tr><tr><td>125200公共管理</td><td>00不区分研究方向</td><td>①199管理类综合能力②204英语（二）</td></tr></table>')
        self.assertEqual(result['programs'], [])
        self.assertTrue(result['issues'])

    def test_numbered_initial_exam_units_keep_the_literal_source_quote(self):
        source = '1、101思想政治理论2、201英语(一)3、302数学(二)4、801有机化学'
        result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr><tr><td>081704应用化学</td><td>00不区分研究方向</td><td>全日制</td><td>' + source + '</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertIn(source, [ref['quote'] for ref in result['programs'][0]['exam_evidence']])


    def test_combined_college_major_direction_column_keeps_original_college_hierarchy(self):
        result = self.parse_inline('<h2>2027年全日制硕士研究生拟招生专业目录</h2><table><tr><th>学院、专业、研究方向</th><th>考试科目</th><th>备注</th></tr><tr><td colspan="3">001经济学院</td></tr><tr><td>020200应用经济学01产业经济研究02区域经济研究</td><td>①101思想政治理论②201英语（一）③303数学（三）④802宏微观经济学</td><td></td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 2)
        self.assertTrue(all(p['department_code'] == '001' and p['department'] == '经济学院' for p in result['programs']))

    def test_major_summary_exam_is_not_an_extra_direction_when_child_modes_follow(self):
        result = self.parse_inline('<table><tr><th>专业代码、名称及研究方向</th><th>招生计划</th><th>初试科目</th><th>备注</th></tr>'
            '<tr><td>001文学院（Tel:022-23765853）</td><td>62</td><td></td><td></td></tr>'
            '<tr><td>050101（学术学位）文艺学</td><td>8</td><td>①101思想政治理论②201英语（一）③666中外文学基础④866语言文学综合</td><td></td></tr>'
            '<tr><td>01（全日制）文学理论</td><td></td><td>①101思想政治理论②201英语（一）③666中外文学基础④866语言文学综合</td><td></td></tr>'
            '<tr><td>02（全日制）文艺美学</td><td></td><td>①101思想政治理论②201英语（一）③666中外文学基础④866语言文学综合</td><td></td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 2)
        self.assertEqual({p['direction'] for p in result['programs']}, {'01文学理论', '02文艺美学'})
        self.assertTrue(all(p['name'] == '文艺学' and p['department'] == '文学院' for p in result['programs']))


    def test_direction_specific_exam_ranges_do_not_mix_subjects_between_directions(self):
        result = self.parse_inline('<h2>2027年全日制硕士研究生拟招生专业目录</h2><table><tr><th>学院、专业、研究方向</th><th>考试科目</th><th>备注</th></tr><tr><td>130100艺术学01民族音乐学02西方音乐史03舞蹈教育学</td><td>（方向01-02）①101思想政治理论②201英语（一）或203日语③622中西音乐史④818音乐分析（03方向）①101思想政治理论②201英语（一）或203日语③623中外舞蹈史④819舞蹈艺术概论</td><td></td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 3)
        dance = next(p for p in result['programs'] if p['direction'].startswith('03'))
        self.assertEqual({s['code'] for s in dance['subjects'] if s['exam_unit'] == 4}, {'819'})
        self.assertTrue(all({s['code'] for s in p['subjects'] if s['exam_unit'] == 4} == {'818'} for p in result['programs'] if not p['direction'].startswith('03')))


    def test_direction_list_with_repeated_mode_labels_shares_only_its_declared_exam_block(self):
        result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr><tr><td>135600美术与书法</td><td>01中国画02油画03书法</td><td>全日制</td><td>01（全日制）、02（全日制）方向：①101思想政治理论②204英语（二）③676中外美术史④918素描03（全日制）方向：①101思想政治理论②204英语（二）③676中外美术史④912书法</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 3)
        self.assertEqual({s['code'] for p in result['programs'] if p['direction'].startswith('01') for s in p['subjects'] if s['exam_unit']==4}, {'918'})


    def test_unmarked_exam_codes_preserve_declared_order_and_or_groups(self):
        text = '101思想政治理论201英语（一）或202俄语或203日语301数学（一）805机械原理'
        result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr><tr><td>080200机械工程</td><td>01机械制造</td><td>全日制</td><td>' + text + '</td></tr></table>')
        self.assertEqual(result['issues'], [])
        p = result['programs'][0]
        self.assertEqual({s['code'] for s in p['subjects'] if s['exam_unit']==2}, {'201','202','203'})
        self.assertEqual({s['code'] for s in p['subjects'] if s['exam_unit']==4}, {'805'})
        self.assertIn(text, [ref['quote'] for ref in p['exam_evidence']])

    def test_unmarked_list_without_a_valid_first_exam_unit_remains_a_gap(self):
        result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr><tr><td>080200机械工程</td><td>01机械制造</td><td>全日制</td><td>201英语一301数学一805机械原理</td></tr></table>')
        self.assertEqual(result['programs'], [])
        self.assertTrue(result['issues'])


    def test_explicit_same_as_above_exam_is_resolved_only_within_the_same_major(self):
        text = '<table><tr><th>专业代码、名称及研究方向</th><th>学习方式</th><th>初试科目</th><th>备注</th></tr><tr><td>040人工智能学院</td><td></td><td></td><td></td></tr><tr><td>081200计算机科学与技术</td><td></td><td></td><td></td></tr><tr><td>01计算机体系结构</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td><td></td></tr><tr><td>02计算机软件</td><td>全日制</td><td>同上</td><td></td></tr><tr><td>083500软件工程</td><td></td><td></td><td></td></tr><tr><td>01软件开发</td><td>全日制</td><td>同上</td><td></td></tr></table>'
        result = self.parse_inline(text)
        self.assertEqual(len(result['programs']), 2)
        self.assertEqual(result['expected_records'], 3)
        self.assertTrue(result['issues'])
        second = result['programs'][1]
        self.assertEqual(second['code'], '081200')
        self.assertEqual({s['code'] for s in second['subjects'] if s['exam_unit']==4}, {'408'})
        self.assertIn('同上', [ref['quote'] for ref in second['exam_evidence']])


    def test_explicit_recommendation_only_generic_direction_is_audited_as_excluded(self):
        body = '<table><tr><th>专业代码、名称及研究方向</th><th>学习方式</th><th>初试科目</th><th>备注</th></tr><tr><td>081200计算机科学与技术</td><td></td><td></td><td></td></tr><tr><td>01工程硕博士专项</td><td>全日制</td><td></td><td>仅招收推免生</td></tr></table>'
        result = self.parse_inline(body)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['programs'], [])
        self.assertEqual(result['expected_records'], 0)
        self.assertEqual(result['excluded_recommendation_only'], 1)
        excluded = self.parse_inline(body.replace('仅招收推免生', '不接收统考生'))
        self.assertEqual(excluded['issues'], [])
        self.assertEqual(excluded['programs'], [])
        self.assertEqual(excluded['expected_records'], 0)


    def test_nonstandard_published_direction_keeps_full_number_under_its_real_major(self):
        result = self.parse_inline('<table><tr><th>专业代码、名称及研究方向</th><th>学习方式</th><th>初试科目</th><th>备注</th></tr><tr><td>042未来教育学院</td><td></td><td></td><td></td></tr><tr><td>045101教育管理</td><td></td><td></td><td>不接收推荐免试专业学位</td></tr><tr><td>0101不设方向（珠海）</td><td>全日制</td><td>①101思想政治理论②204英语（二）③333教育综合④839教育实践与方法</td><td></td></tr></table>')
        self.assertEqual(len(result['programs']), 1)
        p = result['programs'][0]
        self.assertEqual(p['code'], '045101')
        self.assertEqual(p['direction'], '0101不设方向（珠海）')
        self.assertTrue(p['id'].endswith(':0101:全日制'))
        self.assertTrue(p['requires_review'])
        self.assertFalse(p['eligibility_evidence_complete'])
        self.assertTrue(result['issues'])


    def test_ai_letters_after_two_digit_direction_are_name_not_a_four_character_code(self):
        result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr><tr><td>070801固体地球物理学</td><td>01AI驱动的地震形变研究</td><td>全日制</td><td>①101思想政治理论②201英语（一）③302数学（二）④826地球物理学</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertTrue(result['programs'][0]['id'].endswith(':01:全日制'))
        self.assertNotIn('direction_code', result['programs'][0])

    def test_explicit_single_exam_is_excluded_from_unified_catalog_without_identity_conflict(self):
        result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr><tr><td>120400公共管理学</td><td>00不区分研究方向</td><td>全日制</td><td>①101思想政治理论②201英语（一）③614行政管理学④814管理学</td></tr><tr><td>120400公共管理学（单独考试）</td><td>00不区分研究方向</td><td>全日制</td><td>①111思想政治理论（单独考试）②281英语（单独考试）③614行政管理学④814管理学</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 1)
        self.assertEqual(len(result['programs']), 1)
        self.assertEqual(result['excluded_non_unified_exam'], 1)


    def test_department_code_name_header_prevents_same_major_across_colleges_collision(self):
        result = self.parse_inline('<table><tr><th>院系代码名称</th><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th><th>备注</th></tr><tr><td>010甲学院</td><td>080200机械工程</td><td>01机械制造</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④801机械基础</td><td></td></tr><tr><td>020乙学院</td><td>080200机械工程</td><td>01机械制造</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④802机械设计</td><td></td></tr><tr><td>020乙学院</td><td>080200机械工程</td><td>02机械工程（仅从推免生选拔）</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④800仅从推免生选拔</td><td></td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 2)
        self.assertEqual({p['department_code'] for p in result['programs']}, {'010','020'})
        self.assertEqual(result['excluded_recommendation_only'], 1)

    def test_separate_original_department_and_direction_code_columns_are_not_discarded(self):
        result = self.parse_inline('<table><tr><th>院系码</th><th>学院</th><th>专业代码</th><th>专业名称</th><th>研究方向码</th><th>研究方向名称</th><th>学习方式</th><th>考试科目</th></tr><tr><td>001</td><td>基础医学院</td><td>071002</td><td>动物学</td><td>00</td><td>不区分研究方向</td><td>全日制</td><td>①101思想政治理论②201英语（一）③616细胞生物学④821分子生物学</td></tr></table>')
        self.assertEqual(result['issues'], [])
        p = result['programs'][0]
        self.assertEqual(p['department_code'],'001')
        self.assertEqual(p['direction'],'00不区分研究方向')
        self.assertIn('00',[ref['quote'] for ref in p['evidence']])


    def test_school_level_admissions_unit_is_not_truncated_into_a_department(self):
        body = '<h2>2027年硕士研究生招生专业目录</h2><table><tr><th>招生单位</th><th>专业</th><th>学习方式</th><th>考试科目</th></tr><tr><td>82703机械科学研究总院(北京机电研究所)</td><td>080503材料加工工程</td><td>全日制</td><td>①101思想政治理论②201英语一③302数学二④814材料科学基础</td></tr></table>'
        result = parse_catalog_html(body, {'id':'367912','name':'机械科学研究总院(北京机电研究所)','unit_code':'82703'}, 2027, {'id':'d','url':'https://example.edu.cn/catalog'})
        self.assertEqual(result['issues'], [])
        p = result['programs'][0]
        self.assertEqual(p['department'],'')
        self.assertIsNone(p['department_code'])
        self.assertEqual(p['department_scope'],'unit')


    def test_explicitly_hidden_rows_and_tables_cannot_supply_catalog_facts(self):
        header = '<tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr>'
        row = '<tr><td>081200计算机科学与技术</td><td>00不区分研究方向</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr>'
        html = '<div hidden><table>' + header + row.replace('081200', '083500') + '</table></div>' + '<table>' + header + row + row.replace('<tr>', '<tr style="display: none">').replace('081200', '083500') + '</table>' + '<div aria-hidden="true"><table>' + header + row + '</table></div>'
        result = self.parse_inline(html)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 1)
        self.assertEqual(len(result['programs']), 1)

    def test_hidden_global_mode_statement_does_not_fill_a_visible_gap(self):
        result = self.parse_inline('<p style="visibility:hidden">我校硕士研究生为全日制硕士研究生。</p><table><tr><th>专业</th><th>研究方向</th><th>考试科目</th></tr><tr><td>081200计算机科学与技术</td><td>00不区分研究方向</td><td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr></table>')
        self.assertEqual(result['programs'], [])
        self.assertTrue(result['issues'])


    def test_independent_single_exam_directions_are_excluded_but_soldier_and_minorities_stay(self):
        exam = '①101思想政治理论②201英语（一）③301数学（一）④801专业基础'
        header = '<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr>'
        rows = ''.join('<tr><td>080200机械工程</td><td>' + direction + '</td><td>全日制</td><td>' + exam + '</td></tr>' for direction in ['01普通方向', '02强军单考', '03单独考试', '04退役大学生士兵计划', '05少数民族骨干计划'])
        result = self.parse_inline(header + rows + '</table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 3)
        self.assertEqual(result['excluded_non_unified_exam'], 2)
        self.assertEqual({p['direction'][:2] for p in result['programs']}, {'01', '04', '05'})

    def test_exam_method_codes_and_labels_are_not_mistaken_for_initial_retest_phase(self):
        exam = '①101思想政治理论②201英语（一）③301数学（一）④801专业基础'
        for heading in ['考试方式', '考试类型']:
            with self.subTest(heading=heading):
                header = '<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>' + heading + '</th><th>考试科目</th></tr>'
                rows = ''.join('<tr><td>080200机械工程</td><td>' + str(i).zfill(2) + '方向</td><td>全日制</td><td>' + method + '</td><td>' + exam + '</td></tr>' for i, method in enumerate(['21', '21全国统考', '全国统考', '23', '23单独考试', '推荐免试'], 1))
                result = self.parse_inline(header + rows + '</table>')
                self.assertEqual(result['issues'], [])
                self.assertEqual(result['expected_records'], 3)
                self.assertEqual(len(result['programs']), 3)
                self.assertEqual(result['excluded_non_unified_exam'], 2)
                self.assertEqual(result['excluded_recommendation_only'], 1)

    def test_mixed_or_unknown_exam_method_is_a_counted_gap(self):
        for method in ['21/23', '21全国统考或23单独考试', '待公布', '']:
            with self.subTest(method=method):
                result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试方式</th><th>考试科目</th></tr><tr><td>080200机械工程</td><td>01方向</td><td>全日制</td><td>' + method + '</td><td>①101思想政治理论②201英语（一）③301数学（一）④801专业基础</td></tr></table>')
                self.assertEqual(result['expected_records'], 1)
                self.assertEqual(result['programs'], [])
                self.assertTrue(result['issues'])

    def test_mixed_multi_direction_single_exam_cell_is_not_silently_treated_as_one_scope(self):
        result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr><tr><td>080200机械工程</td><td>01普通方向；02强军单考</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④801专业基础</td></tr></table>')
        self.assertEqual(result['expected_records'], 2)
        self.assertEqual(result['programs'], [])
        self.assertTrue(result['issues'])

    def test_separate_professional_code_columns_with_a_trailing_note_do_not_crash(self):
        result = self.parse_inline('<table><tr><th>专业代码</th><th>专业名称</th><th>学习方式</th><th>初试科目</th></tr><tr><td>080200</td><td>机械工程</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④801专业基础</td></tr><tr><td></td><td>目录说明</td><td>备注</td><td></td></tr></table>')
        self.assertEqual(len(result['programs']), 1)
        self.assertEqual(result['expected_records'], 1)
        self.assertEqual(result['issues'], [])

    def test_real_ustl_single_exam_subject_rows_are_excluded_from_unified_projection(self):
        result = self.parse('ustl-2027', '368038', '辽宁科技大学')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 173)
        self.assertEqual(len(result['programs']), 173)
        self.assertEqual(result['excluded_non_unified_exam'], 3)
        self.assertFalse(any('单独考试' in subject['name'] for program in result['programs'] for subject in program['subjects']))
        self.assertTrue(any(program['study_mode'] == '非全日制' for program in result['programs']))

    def test_mixed_unified_and_single_exam_subject_alternatives_remain_a_gap(self):
        exam = '①101思想政治理论或111思想政治理论（单独考试）②201英语（一）③301数学（一）④801专业基础'
        result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>初试科目</th></tr><tr><td>080200机械工程</td><td>01普通方向</td><td>全日制</td><td>' + exam + '</td></tr></table>')
        self.assertEqual(result['expected_records'], 1)
        self.assertEqual(result['programs'], [])
        self.assertTrue(result['issues'])
        self.assertEqual(result['excluded_non_unified_exam'], 0)

    def test_negative_single_exam_notice_in_notes_or_subject_parenthesis_does_not_exclude(self):
        exam = '①101思想政治理论②201英语（一）③301数学（一）④801专业基础（不招收单独考试考生）'
        result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>初试科目</th><th>备注</th></tr><tr><td>080200机械工程</td><td>01普通方向</td><td>全日制</td><td>' + exam + '</td><td>不招收单独考试考生</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 1)
        self.assertEqual(len(result['programs']), 1)
        self.assertEqual(result['excluded_non_unified_exam'], 0)

    def test_hierarchical_code_table_single_exam_subjects_keep_audit_count(self):
        result = self.parse_inline('<table><tr><th>编码</th><th>名称</th><th>拟招人数</th><th>考试科目</th><th>备注</th></tr><tr><td>100</td><td>学院</td><td></td><td></td><td></td></tr><tr><td>080200</td><td>机械工程（全日制）</td><td></td><td></td><td></td></tr><tr><td>01</td><td>不区分方向</td><td>2</td><td>①111思想政治理论（单独考试）②241单独考试英语③612单独考试数学④801专业基础</td><td></td></tr></table>')
        self.assertEqual(result['programs'], [])
        self.assertEqual(result['expected_records'], 0)
        self.assertEqual(result['excluded_non_unified_exam'], 1)
        self.assertEqual(result['issues'], [])

    def test_hust_explicit_single_exam_direction_is_excluded_with_audit_count(self):
        result = self.parse_inline('<table><tr><th>编码</th><th>名称</th><th>拟招人数</th><th>考试科目</th><th>备注</th></tr><tr><td>100</td><td>学院</td><td></td><td></td><td></td></tr><tr><td>080200</td><td>机械工程（全日制）</td><td></td><td></td><td></td></tr><tr><td>01</td><td>强军单考</td><td>2</td><td>①111思想政治理论②281英语③611数学④801专业基础</td><td></td></tr></table>')
        self.assertEqual(result['programs'], [])
        self.assertEqual(result['expected_records'], 0)
        self.assertEqual(result['excluded_non_unified_exam'], 1)
        self.assertEqual(result['issues'], [])


    def test_decorated_initial_exam_and_major_headers_keep_literal_cells(self):
        result = self.parse_inline('<table><tr><th>招生学院</th><th>招生专业/领域（标★的为学硕）</th><th>研究方向</th><th>学习方式</th><th>考试科目（加粗的为我校自命题科目）</th></tr>'
            '<tr><td>001机械与汽车工程学院</td><td>★080200机械工程</td><td>00不区分研究方向</td><td>(1)全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④812机械设计基础</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 1)
        row = result['programs'][0]
        self.assertEqual((row['code'], row['name'], row['department_code']), ('080200', '机械工程', '001'))
        self.assertEqual([s['code'] for s in row['subjects']], ['101', '201', '301', '812'])
        self.assertIn('★080200机械工程', [ref['quote'] for ref in row['evidence']])

    def test_explicit_school_department_and_initial_code_header_aliases(self):
        for department, exam in [('学院代码和名称', '初试考试代码及科目'), ('系所代码及名称', '考试科目代码及名称')]:
            with self.subTest(exam=exam):
                result = self.parse_inline(f'<table><tr><th>{department}</th><th>专业名称（含代码）</th><th>研究方向</th><th>学习方式</th><th>{exam}</th></tr>'
                    '<tr><td>601经济学院</td><td>025400国际商务</td><td>00不区分研究方向</td><td>全日制</td><td>101思想政治理论204英语（二）396经济类综合能力434国际商务专业基础</td></tr></table>')
                self.assertEqual(result['issues'], [])
                row = result['programs'][0]
                self.assertEqual((row['department'], row['department_code']), ('经济学院', '601'))
                self.assertEqual([s['code'] for s in row['subjects']], ['101', '204', '396', '434'])

    def test_two_digit_spanning_faculty_rows_do_not_merge_same_major_between_departments(self):
        header = '<tr><th>专业代码及名称</th><th>研究方向代码及名称</th><th>学习方式</th><th>初试科目</th></tr>'
        initial = '①101思想政治理论②204英语（二）③331社会工作原理④437社会工作实务'
        result = self.parse_inline('<table>' + header +
            '<tr><td colspan="4">03法学与公共管理学部联系电话：0831-3532847</td></tr>'
            f'<tr><td>035200社会工作</td><td>00不区分研究方向</td><td>全日制</td><td>{initial}</td></tr>'
            '<tr><td colspan="4">05社会发展学院</td></tr>'
            f'<tr><td>035200社会工作</td><td>00不区分研究方向</td><td>全日制</td><td>{initial}</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(len(result['programs']), 2)
        self.assertEqual({row['department_code'] for row in result['programs']}, {'03', '05'})
        self.assertEqual(len({row['id'] for row in result['programs']}), 2)
        self.assertEqual(next(row['department'] for row in result['programs'] if row['department_code'] == '03'), '法学与公共管理学部')

    def test_spanning_direction_with_faculty_word_is_not_a_department_header(self):
        result = self.parse_inline('<table><tr><th>专业代码及名称</th><th>研究方向代码及名称</th><th>学习方式</th><th>初试科目</th></tr>'
            '<tr><td colspan="4">01计算机学院</td></tr><tr><td colspan="4">02跨学院研究方向</td></tr>'
            '<tr><td>081200计算机科学与技术</td><td>00不区分研究方向</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['programs'][0]['department_code'], '01')
        self.assertEqual(result['programs'][0]['direction'], '00不区分研究方向')

    def test_explicit_preheader_faculty_and_reference_section_do_not_corrupt_office_catalog(self):
        result = self.parse_inline('<table><tr><td colspan="5">院系所代码及名称：001马克思主义学院学院网站：https://example.edu.cn/联系人：甲老师</td></tr>'
            '<tr><th>专业代码</th><th>专业名称</th><th>研究方向</th><th>学习方式</th><th>初试考试科目</th></tr>'
            '<tr><td>030500</td><td>马克思主义理论</td><td>01马克思主义基本原理</td><td>全日制</td><td>①101思想政治理论②201英语（一）③651马克思主义基本原理概论④851中国化马克思主义概论</td></tr>'
            '<tr><td colspan="5">参考书目及考试大纲</td></tr>'
            '<tr><td>考试科目</td><td>考试大纲</td><td>参考书目</td><td>出版社</td><td>出版时间</td></tr>'
            '<tr><td>851中国化马克思主义概论</td><td>理论与历史</td><td>参考教材</td><td>某出版社</td><td>2026</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 1)
        row = result['programs'][0]
        self.assertEqual((row['department_code'], row['department']), ('001', '马克思主义学院'))
        self.assertIn('院系所代码及名称：001马克思主义学院学院网站：https://example.edu.cn/联系人：甲老师', [ref['quote'] for ref in row['evidence']])


    def test_headerless_catalog_continuation_is_a_gap_instead_of_false_full_coverage(self):
        initial = '①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础'
        result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>初试科目</th></tr>'
            f'<tr><td>081200计算机科学与技术</td><td>00不区分研究方向</td><td>全日制</td><td>{initial}</td></tr></table>'
            f'<table><tr><td>083500软件工程</td><td>00不区分研究方向</td><td>全日制</td><td>{initial}</td></tr></table>')
        self.assertEqual(len(result['programs']), 1)
        self.assertTrue(any('表2' in issue for issue in result['issues']))

    def test_unrelated_fee_table_with_major_codes_does_not_become_a_missing_exam_table(self):
        result = self.parse_inline('<table><tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>初试科目</th></tr>'
            '<tr><td>081200计算机科学与技术</td><td>00不区分研究方向</td><td>全日制</td><td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr></table>'
            '<table><tr><th>收费类别</th><th>专业代码</th><th>年学费</th></tr><tr><td>全日制硕士</td><td>081200</td><td>8000</td></tr></table>')
        self.assertEqual(result['issues'], [])
        self.assertEqual(len(result['programs']), 1)


    def test_explicit_department_contact_list_does_not_become_part_of_faculty_name(self):
        result = self.parse_inline('<table><tr><td colspan="4">院系所代码及名称：016非全日制硕士教育中心学院网站、联系人、邮箱、联系电话：略（参考商学院、经济金融学院）</td></tr>'
            '<tr><th>专业</th><th>研究方向</th><th>学习方式</th><th>初试科目</th></tr>'
            '<tr><td>125100工商管理</td><td>01企业管理</td><td>非全日制</td><td>①199管理类综合能力②204英语（二）</td></tr></table>')
        self.assertEqual(result['issues'], [])
        row = result['programs'][0]
        self.assertEqual((row['department_code'], row['department']), ('016', '非全日制硕士教育中心'))


if __name__ == '__main__':
    unittest.main()
