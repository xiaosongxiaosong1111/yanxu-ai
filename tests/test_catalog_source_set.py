import hashlib
from pathlib import Path
import tempfile
import unittest

from collectors.catalog_source_set import parse_catalog_index, source_set_reconciliation
from service_verification import SourceVerifier


class CatalogSourceSetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.unit = {'id': '100', 'name': '测试大学', 'official_url': 'https://www.a.edu.cn'}
        self.title = '<h1>测试大学2027年硕士研究生招生专业目录</h1>'
        self.index = self.title + '<article><a href="/a.pdf">计算机学院</a><a href="/b.pdf">软件学院</a></article>'

    def document(self, identity, content, url, parser='html_table_v1'):
        path = self.root / (identity + '.html')
        path.write_text(content, 'utf-8')
        return {'id': identity, 'unit_id': '100', 'year': 2027, 'kind': 'catalog', 'status': 'parsed',
                'url': url, 'local_path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'catalog_parser': parser}

    def test_index_replays_all_original_members_and_cannot_trust_a_shorter_supplied_list(self):
        index = self.document('index', self.index, 'https://yz.a.edu.cn/index', 'catalog_index_v1')
        first = self.document('a', '原始目录一', 'https://yz.a.edu.cn/a.pdf')
        second = self.document('b', '原始目录二', 'https://yz.a.edu.cn/b.pdf')
        verifier = SourceVerifier(self.root)
        proof = source_set_reconciliation(self.unit, 2027, [index, first, second], verifier)
        self.assertTrue(proof['complete'])
        self.assertEqual(proof['expected_sources'], 2)
        index['members'] = ['https://yz.a.edu.cn/a.pdf']
        self.assertFalse(source_set_reconciliation(self.unit, 2027, [index, first], verifier)['complete'])

    def test_dynamic_unknown_attachments_and_scope_limited_index_stay_incomplete(self):
        for suffix in ['<a href="javascript:showMore()">更多专业目录</a>', '<iframe src="/more"></iframe>',
                       '<a href="/unknown.pdf">附件一</a>', '<a href="/next">下一页</a>']:
            result = parse_catalog_index(self.index + suffix, 'https://yz.a.edu.cn/index', self.unit, 2027)
            self.assertTrue(result['issues'], suffix)
        result = parse_catalog_index(self.index.replace('硕士研究生', '全日制硕士研究生'),
                                     'https://yz.a.edu.cn/index', self.unit, 2027)
        self.assertTrue(result['issues'])

    def test_explicit_alternate_formats_need_exactly_one_original_per_label(self):
        html = self.title + '<a href="/a.pdf">计算机学院（PDF版）</a><a href="/a.docx">计算机学院（Word版）</a>'
        parsed = parse_catalog_index(html, 'https://yz.a.edu.cn/index', self.unit, 2027)
        self.assertEqual(len(parsed['groups']), 1)
        index = self.document('index', html, 'https://yz.a.edu.cn/index', 'catalog_index_v1')
        pdf = self.document('pdf', '原文', 'https://yz.a.edu.cn/a.pdf')
        office = self.document('word', '原文', 'https://yz.a.edu.cn/a.docx')
        verifier = SourceVerifier(self.root)
        self.assertTrue(source_set_reconciliation(self.unit, 2027, [index, pdf], verifier)['complete'])
        self.assertFalse(source_set_reconciliation(self.unit, 2027, [index, pdf, office], verifier)['complete'])

    def test_same_label_without_explicit_format_is_not_an_alternate_source(self):
        html = self.title + '<a href="/academic.pdf">专业目录</a><a href="/professional.pdf">专业目录</a>'
        index = self.document('index', html, 'https://yz.a.edu.cn/index', 'catalog_index_v1')
        academic = self.document('academic', '原文', 'https://yz.a.edu.cn/academic.pdf')
        professional = self.document('professional', '原文', 'https://yz.a.edu.cn/professional.pdf')
        verifier = SourceVerifier(self.root)
        self.assertFalse(source_set_reconciliation(self.unit, 2027, [index, academic], verifier)['complete'])
        self.assertTrue(source_set_reconciliation(self.unit, 2027, [index, academic, professional], verifier)['complete'])
        repeated_format = html.replace('>专业目录<', '>专业目录（PDF版）<')
        self.assertTrue(parse_catalog_index(repeated_format, index['url'], self.unit, 2027)['issues'])

    def test_unknown_external_member_and_changed_index_bytes_never_pass(self):
        result = parse_catalog_index(self.index + '<a href="https://other.edu.cn/c.pdf">机械学院</a>',
                                     'https://yz.a.edu.cn/index', self.unit, 2027)
        self.assertTrue(result['issues'])
        index = self.document('index', self.index, 'https://yz.a.edu.cn/index', 'catalog_index_v1')
        (self.root / index['local_path']).write_text(self.index + 'changed', 'utf-8')
        self.assertFalse(source_set_reconciliation(self.unit, 2027, [index], SourceVerifier(self.root))['complete'])

    def paginated_sources(self, *, explicit_total=True):
        head = '<table><tr><th>编码</th><th>名称</th><th>拟招人数</th><th>考试科目</th><th>备注</th></tr>'
        exam = '①101思想政治理论②201英语（一）③306临床医学综合能力（西医）'
        def page(department, current, total, body):
            form = '<select name="ctl00$contentParent$drpYx">' + ''.join('<option value="' + value + '"' + (' selected' if value == department else '') + '>' + value + '|临床学院</option>' for value in ['530', '531']) + '</select>'
            form += '<select name="ctl00$contentParent$drpNd"><option value="2027" selected>2027</option></select>'
            pager = ''.join('<a id="ctl_Pager1_-1_lnkPage' + str(number) + '_-1"' + (' class="aspNetDisabled"' if number == current else ' href="javascript:page"') + '>' + str(number) + '</a>' for number in range(1, total + 1)) if total > 1 else ''
            return self.title + form + head + body + '</table>' + pager + (f'<span>共{total}页</span>' if explicit_total else '')
        major = '<tr><td>530</td><td>第一临床学院</td><td>100</td><td></td><td></td></tr><tr><td>105100</td><td>临床医学（全日制）</td><td>30</td><td></td><td></td></tr>'
        direction = lambda code, name: '<tr><td>' + code + '</td><td>' + name + '</td><td></td><td>' + exam + '</td><td></td></tr>'
        first = self.document('p1', page('530', 1, 2, major + direction('01', '内科学')), 'https://yz.a.edu.cn/catalog')
        second = self.document('p2', page('530', 2, 2, direction('02', '老年医学')), 'https://yz.a.edu.cn/catalog')
        second['catalog_previous_id'] = 'p1'
        third = self.document('d531', page('531', 1, 1, major.replace('530', '531') + direction('01', '内科学')), 'https://yz.a.edu.cn/catalog')
        return {row['id']: row for row in [first, second, third]}

    def test_form_pages_replay_original_predecessor_context_and_all_members(self):
        documents = self.paginated_sources()
        verifier = SourceVerifier(self.root)
        rows = []
        for document in documents.values():
            parsed = verifier._source_programs(document, self.unit, 2027, {}, documents)
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed['issues'], [])
            rows.extend(parsed['programs'])
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(verifier.program(row, documents, self.unit) for row in rows))
        self.assertTrue(verifier.unit_reconciles(self.unit, 2027, rows, documents))
        second = next(row for row in rows if row['direction'].startswith('02'))
        self.assertEqual({ref['document_id'] for ref in second['evidence']}, {'p1', 'p2'})
        self.assertFalse(verifier.program(second, {'p2': documents['p2']}, self.unit))
        self.assertFalse(verifier.unit_reconciles(self.unit, 2027, rows, {key: row for key, row in documents.items() if key != 'p2'}))
        cycle = {**documents, 'p1': {**documents['p1'], 'catalog_previous_id': 'p2'}}
        self.assertFalse(verifier.program(second, cycle, self.unit))

    def test_visible_form_page_buttons_without_independent_total_are_not_complete(self):
        documents = self.paginated_sources(explicit_total=False)
        proof = source_set_reconciliation(self.unit, 2027, list(documents.values()), SourceVerifier(self.root))
        self.assertFalse(proof['complete'])
        self.assertTrue(any('总页数' in issue for issue in proof['issues']))

    def test_catalog_index_self_navigation_is_not_a_missing_department_but_centers_are_members(self):
        html = self.title + '<a href="https://yz.a.edu.cn/index">硕士专业目录</a><a href="/center.html">035基础教育质量监测协同创新中心</a>'
        parsed = parse_catalog_index(html, 'https://yz.a.edu.cn/index', self.unit, 2027)
        self.assertEqual(parsed['issues'], [])
        self.assertEqual(parsed['groups'], [{'https://yz.a.edu.cn/center.html'}])
        placeholder = parse_catalog_index(html + '<a href="#not-parsed">另一份专业目录</a>',
            'https://yz.a.edu.cn/index', self.unit, 2027)
        self.assertTrue(placeholder['issues'])


if __name__ == '__main__':
    unittest.main()
