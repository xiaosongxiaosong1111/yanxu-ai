"""Saved charters must not inherit catalogue semantics from platform chrome."""
import hashlib
from html import escape
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from collectors.national_catalog import _adapter, collect_saved
from tests.test_national_catalog import HTML, UNIT


CHSI_URL = 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?zsgsId=123&schCode=10001'
VIEW_URL = 'https://yz.chsi.com.cn/sch/viewZszc--schId-123,infoId-456.dhtml'
NAVIGATION = '<div class="menu"><a href="/zsml/">专业目录</a></div>'
CHARTER = '<h1>样本大学2027年硕士研究生招生章程</h1><p>初试考试科目以专业目录为准。</p>'
FEES = '<table><tr><th>专业</th><th>年学费</th></tr><tr><td>计算机</td><td>①8000元</td></tr></table>'
INDEX = '<h1>样本大学2027年硕士研究生招生专业目录</h1><a href="https://grad.sample.edu.cn/one">计算机学院</a>'


def hidden_charter(body):
    return (NAVIGATION + '<input type="hidden" id="hid_content" value="' + escape(body, quote=True)
            + '"><div id="cnt_body_wrapper"></div><script>'
            + '$("#cnt_body_wrapper").html($("#hid_content").val());</script>')


class NationalCatalogDocumentKindTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def dispatch(self, html, *, url=CHSI_URL, kind='charter'):
        document = {'id': 'source', 'url': url, 'kind': kind, 'year': 2027,
                    'content_type': 'text/html;charset=utf-8'}
        return _adapter(html.encode(), self.root / 'source.html', document, UNIT, self.root)

    def test_hidden_charter_does_not_use_chsi_platform_catalogue_navigation(self):
        self.assertEqual(self.dispatch(hidden_charter(CHARTER))[:2], (None, 'not_catalog'))

    def test_visible_charter_does_not_use_chsi_platform_catalogue_navigation(self):
        html = NAVIGATION + '<div class="yxk-news-contain">' + CHARTER + '</div>'
        self.assertEqual(self.dispatch(html, url=VIEW_URL)[:2], (None, 'not_catalog'))

    def test_known_chsi_charter_is_not_catalogue_even_when_discovery_kind_was_catalogue(self):
        self.assertEqual(self.dispatch(hidden_charter(CHARTER), kind='catalog')[:2], (None, 'not_catalog'))

    def test_unpublished_hidden_table_or_index_is_not_recovered_as_visible_content(self):
        for body in [HTML, INDEX]:
            with self.subTest(body=body):
                html = NAVIGATION + '<input type="hidden" id="hid_content" value="' + escape(body, quote=True) + '">'
                self.assertEqual(self.dispatch(html)[:2], (None, 'not_catalog'))

    def test_unpublished_hidden_article_cannot_fall_back_to_a_table_outside_the_article(self):
        html = (HTML + '<input type="hidden" id="hid_content" value="'
                + escape(CHARTER, quote=True) + '"><div id="cnt_body_wrapper"></div>')
        self.assertEqual(self.dispatch(html)[:2], (None, 'not_catalog'))

    def test_fee_table_and_prose_exam_words_are_not_an_initial_exam_table(self):
        self.assertEqual(self.dispatch(CHARTER + FEES, url='https://grad.sample.edu.cn/charter')[:2],
                         (None, 'not_catalog'))

    def test_two_unrelated_tables_cannot_supply_each_others_catalogue_columns(self):
        html = CHARTER + FEES + '<table><tr><td>考试科目详见目录</td></tr></table>'
        self.assertEqual(self.dispatch(html, url='https://grad.sample.edu.cn/charter')[:2],
                         (None, 'not_catalog'))

    def test_hidden_exam_table_is_not_a_real_catalogue(self):
        html = CHARTER + '<div hidden>' + HTML + '</div>'
        self.assertEqual(self.dispatch(html, url='https://grad.sample.edu.cn/charter')[:2],
                         (None, 'not_catalog'))

    def test_charter_with_genuine_catalogue_table_keeps_its_table_adapter(self):
        for html, url in [(CHARTER + HTML, 'https://grad.sample.edu.cn/charter'),
                          (hidden_charter(CHARTER + HTML), CHSI_URL),
                          (NAVIGATION + '<div class="yxk-news-contain">' + HTML + '</div>', VIEW_URL)]:
            with self.subTest(url=url):
                self.assertEqual(self.dispatch(html, url=url), ('html_table_v1', None, ''))

    def test_genuine_catalogue_table_and_independently_scoped_index_remain_supported(self):
        self.assertEqual(self.dispatch(HTML, url='https://grad.sample.edu.cn/catalog', kind='catalog')[0],
                         'html_table_v1')
        self.assertEqual(self.dispatch(INDEX, url='https://grad.sample.edu.cn/catalog', kind='catalog')[0],
                         'catalog_index_v1')
        self.assertEqual(self.dispatch(INDEX, url='https://grad.sample.edu.cn/catalog')[0], 'catalog_index_v1')

    def test_dynamic_catalogue_is_unresolved_instead_of_being_reported_empty(self):
        self.assertEqual(self.dispatch('<h1>样本大学2027年硕士研究生招生专业目录</h1><iframe src="/data"></iframe>',
                                       url='https://grad.sample.edu.cn/catalog', kind='catalog')[1],
                         'needs_adapter')

    def test_image_only_pdf_requires_ocr_review_instead_of_being_declared_not_catalogue(self):
        for discovery_kind, title in [('catalog', '2027年硕士研究生招生专业目录'),
                                      ('tuition', '2027年学费标准'), ('attachment', '')]:
            with self.subTest(discovery_kind=discovery_kind):
                document = {'url': 'https://grad.sample.edu.cn/document.pdf', 'year': 2027,
                            'kind': 'catalog', 'discovery_kind': discovery_kind, 'title': title}
                with patch('collectors.national_catalog.extract_pdf_text', return_value='\n\f\t'):
                    parser, status, reason = _adapter(b'%PDF-image', self.root / 'image.pdf',
                                                      document, UNIT, self.root)
                self.assertIsNone(parser)
                self.assertEqual(status, 'needs_adapter')
                self.assertIn('OCR', reason)

    def test_directory_with_only_its_text_title_stays_unresolved(self):
        document = {'url': 'https://grad.sample.edu.cn/catalog.pdf', 'year': 2027,
                    'kind': 'catalog', 'discovery_kind': 'catalog',
                    'title': '2027年硕士研究生招生专业目录'}
        with patch('collectors.national_catalog.extract_pdf_text', return_value=document['title']):
            parser, status, _ = _adapter(b'%PDF-title', self.root / 'title.pdf', document, UNIT, self.root)
        self.assertIsNone(parser)
        self.assertEqual(status, 'needs_adapter')

    def test_text_fee_pdf_is_not_mistaken_for_a_directory(self):
        document = {'url': 'https://grad.sample.edu.cn/fees.pdf', 'year': 2027,
                    'kind': 'catalog', 'discovery_kind': 'tuition', 'title': '2027年学费标准'}
        with patch('collectors.national_catalog.extract_pdf_text', return_value='专业 学费 计算机 8000元/年'):
            parser, status, _ = _adapter(b'%PDF-fees', self.root / 'fees.pdf', document, UNIT, self.root)
        self.assertEqual((parser, status), (None, 'not_catalog'))

    def saved_document(self, name, html, *, url, kind='catalog'):
        path = self.root / 'data/snapshots/national/2027/123' / (name + '.html')
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = html.encode()
        path.write_bytes(raw)
        return {'id': name, 'unit_id': '123', 'url': url, 'kind': kind, 'year': 2027,
                'status': 'fetched', 'content_type': 'text/html;charset=utf-8',
                'sha256': hashlib.sha256(raw).hexdigest(), 'local_path': path.relative_to(self.root).as_posix()}

    def test_missing_homepage_uses_saved_exact_owner_declaration_without_network(self):
        document = self.saved_document('table', HTML, url='https://grad.sample.edu.cn/catalog')
        charter = self.saved_document('charter', CHARTER + '<p>学校研究生院官网：https://grad.sample.edu.cn/。</p>',
                                      url=CHSI_URL, kind='charter')
        unrelated = self.saved_document('news', '<p>校园新闻</p>', url='https://grad.sample.edu.cn/news')
        malformed = {**charter, 'id': 'missing-path', 'local_path': None}
        unit = {key: value for key, value in UNIT.items() if key != 'official_url'}
        national = {'units': [{'unit_id': '123', 'status': 'collected',
                              'pages': [unrelated, malformed, charter], 'documents': [document]}]}
        with patch('public_sources.fetch_official_document', side_effect=AssertionError('network forbidden')):
            report = collect_saved({'units': [unit]}, national, self.root / 'data/report.json', project_root=self.root)
        file = report['units'][0]['files'][0]
        self.assertEqual(file['status'], 'ready')
        self.assertEqual(file['binding']['url'], 'https://grad.sample.edu.cn/')
        self.assertNotIn('official_url', unit)

    def test_failed_homepage_ownership_proof_cannot_be_substituted_with_peer_site(self):
        document = self.saved_document('table', HTML, url='https://grad.sample.edu.cn/catalog')
        charter = self.saved_document('charter', CHARTER + '<p>友情链接：其他大学官网：https://grad.sample.edu.cn/。</p>',
                                      url=CHSI_URL, kind='charter')
        unit = {key: value for key, value in UNIT.items() if key != 'official_url'}
        registry = {'units': [unit, {**UNIT, 'id': '456', 'name': '其他大学'}]}
        national = {'units': [{'unit_id': '123', 'status': 'collected', 'pages': [charter], 'documents': [document]}]}
        report = collect_saved(registry, national, self.root / 'data/report.json', project_root=self.root, unit_ids=['123'])
        self.assertEqual(report['units'][0]['files'][0]['status'], 'failure')
        self.assertEqual(report['metadata']['program_count'], 0)

    def test_existing_primary_homepage_does_not_hide_a_proven_secondary_domain(self):
        unit = {**UNIT, 'official_url': 'https://admission.sample.ac.cn/'}
        before = json.dumps(unit, sort_keys=True)
        document = self.saved_document('table', HTML, url='https://grad.sample.edu.cn/catalog')
        charter = self.saved_document('charter', CHARTER + '<p>学校研究生招生网站：https://grad.sample.edu.cn/。</p>',
                                      url=CHSI_URL, kind='charter')
        national = {'units': [{'unit_id': '123', 'status': 'collected', 'pages': [charter], 'documents': [document]}]}
        with patch('public_sources.fetch_official_document', side_effect=AssertionError('network forbidden')):
            report = collect_saved({'units': [unit]}, national, self.root / 'data/report.json', project_root=self.root)
        file = report['units'][0]['files'][0]
        self.assertEqual(file['status'], 'ready')
        self.assertEqual(file['binding']['url'], 'https://grad.sample.edu.cn/')
        self.assertEqual(file['binding']['evidence']['sha256'], charter['sha256'])
        self.assertEqual(json.dumps(unit, sort_keys=True), before)

    def test_peer_declaration_cannot_extend_an_existing_primary_homepage(self):
        unit = {**UNIT, 'official_url': 'https://admission.sample.ac.cn/'}
        primary = self.saved_document('primary', HTML, url='https://admission.sample.ac.cn/catalog')
        peer = self.saved_document('peer', HTML, url='https://grad.peer.edu.cn/catalog')
        charter = self.saved_document('charter', CHARTER + '<p>其他大学研究生院官网：https://grad.peer.edu.cn/。</p>',
                                      url=CHSI_URL, kind='charter')
        national = {'units': [{'unit_id': '123', 'status': 'collected', 'pages': [charter], 'documents': [primary, peer]}]}
        registry = {'units': [unit, {**UNIT, 'id': '456', 'name': '其他大学'}]}
        report = collect_saved(registry, national, self.root / 'data/report.json', project_root=self.root, unit_ids=['123'])
        states = {row['source_id']: row for row in report['units'][0]['files']}
        self.assertEqual(states['primary']['status'], 'ready')
        self.assertEqual(states['peer']['status'], 'failure')
        self.assertEqual(report['metadata']['program_count'], 1)

    def test_changed_binding_original_cannot_reuse_a_parse_with_stale_ownership_evidence(self):
        unit = {key: value for key, value in UNIT.items() if key != 'official_url'}
        document = self.saved_document('table', HTML, url='https://grad.sample.edu.cn/catalog')
        body = CHARTER + '<p>学校研究生招生网站：https://grad.sample.edu.cn/。</p>'
        charter = self.saved_document('charter', body, url=CHSI_URL, kind='charter')
        national = {'units': [{'unit_id': '123', 'status': 'collected', 'pages': [charter], 'documents': [document]}]}
        with patch('collectors.national_catalog.parser_revision', return_value='stable-test-parser'):
            def collect():
                return collect_saved({'units': [unit]}, national, self.root / 'data/report.json', project_root=self.root)
            self.assertEqual(collect()['metadata']['cache_hits'], 0)
            self.assertEqual(collect()['metadata']['cache_hits'], 1)
            updated = self.saved_document('charter', body + '<p>原文更新说明。</p>', url=CHSI_URL, kind='charter')
            national = {'units': [{**national['units'][0], 'pages': [updated]}]}
            result = collect()
        self.assertEqual(result['metadata']['cache_hits'], 0)
        self.assertEqual(result['units'][0]['files'][0]['binding']['evidence']['sha256'], updated['sha256'])

    def test_official_urls_are_part_of_the_saved_parse_unit_context(self):
        document = self.saved_document('table', HTML, url='https://grad.sample.edu.cn/catalog')
        national = {'units': [{'unit_id': '123', 'status': 'collected', 'pages': [], 'documents': [document]}]}
        with patch('collectors.national_catalog.parser_revision', return_value='stable-test-parser'):
            first = collect_saved({'units': [UNIT]}, national, self.root / 'data/report.json', project_root=self.root)
            unit = {**UNIT, 'official_urls': [{'url': 'https://grad.sample.edu.cn/',
                                             'evidence': {'url': CHSI_URL, 'quote': '本校研究生院官网'}}]}
            second = collect_saved({'units': [unit]}, national, self.root / 'data/report.json', project_root=self.root)
        self.assertEqual(first['metadata']['cache_hits'], 0)
        self.assertEqual(second['metadata']['cache_hits'], 0)
        cache_path = self.root / second['units'][0]['files'][0]['cache_path']
        cached_unit = json.loads(cache_path.read_text('utf-8'))['value']['unit']
        self.assertEqual(cached_unit['official_urls'], unit['official_urls'])

    def test_recommendation_quota_in_first_pdf_rows_does_not_exclude_unified_catalogue(self):
        text = ('样本大学2027年硕士研究生招生专业目录\n名称（编号） 考试科目 备注\n'
                '001公共管理学院\n联系人：甲老师\n联系电话：123456\n120400公共管理学\n'
                '00不区分研究方向（全日制）\n拟招生人数：64，其中推免生人数：12\n'
                '101思想政治理论\n201英语（一）\n614公共管理学\n809社会研究方法')
        document = {'url': 'https://grad.sample.edu.cn/catalog.pdf', 'year': 2027,
                    'kind': 'catalog', 'discovery_kind': 'catalog', 'title': '2027年硕士招生专业目录'}
        with patch('collectors.national_catalog.extract_pdf_text', return_value=text):
            parser, status, _ = _adapter(b'%PDF-quota', self.root / 'quota.pdf', document, UNIT, self.root)
        self.assertIsNotNone(parser)
        self.assertIsNone(status)

    def test_actual_wrapped_special_route_titles_still_exclude_or_defer_pdf(self):
        titles = [('样本大学2027年\n推荐免试硕士研究生招生专业目录', 'not_catalog'),
                  ('2027年\n推荐免试硕士研究生招生专业目录', 'not_catalog'),
                  ('样本大学2027年硕士研究生招生专业目录（全国统考\n与推荐免试）', 'needs_adapter'),
                  ('样本大学2027年\n单独考试硕士研究生招生专业目录', 'not_catalog')]
        document = {'url': 'https://grad.sample.edu.cn/catalog.pdf', 'year': 2027,
                    'kind': 'catalog', 'discovery_kind': 'catalog', 'title': '2027年硕士招生专业目录'}
        for title, expected in titles:
            with self.subTest(title=title):
                with patch('collectors.national_catalog.extract_pdf_text', return_value=title + '\n名称 考试科目 备注'):
                    parser, status, _ = _adapter(b'%PDF-scope', self.root / 'scope.pdf', document, UNIT, self.root)
                self.assertIsNone(parser)
                self.assertEqual(status, expected)

    def test_adjacent_route_subtitle_is_part_of_pdf_scope(self):
        document = {'url': 'https://grad.sample.edu.cn/catalog.pdf', 'year': 2027,
                    'kind': 'catalog', 'discovery_kind': 'catalog', 'title': '2027年硕士招生专业目录'}
        for subtitle, expected in [('（单独考试）', 'not_catalog'), ('（推荐免试）', 'not_catalog'),
                                   ('（全国统考与推荐免试）', 'needs_adapter')]:
            with self.subTest(subtitle=subtitle):
                text = '样本大学2027年硕士研究生招生专业目录\n' + subtitle + '\n名称 考试科目 备注'
                with patch('collectors.national_catalog.extract_pdf_text', return_value=text):
                    parser, status, _ = _adapter(b'%PDF-scope', self.root / 'scope.pdf', document, UNIT, self.root)
                self.assertIsNone(parser)
                self.assertEqual(status, expected)

    def test_scope_subtitle_probe_does_not_read_quotas_or_post_header_notes(self):
        document = {'url': 'https://grad.sample.edu.cn/catalog.pdf', 'year': 2027,
                    'kind': 'catalog', 'discovery_kind': 'catalog', 'title': '2027年硕士招生专业目录'}
        for rows in ['（拟招生人数64，其中推免生12）\n名称 考试科目 备注',
                     '名称 考试科目 备注\n（推荐免试）']:
            with self.subTest(rows=rows):
                text = '样本大学2027年硕士研究生招生专业目录\n' + rows
                with patch('collectors.national_catalog.extract_pdf_text', return_value=text):
                    parser, status, _ = _adapter(b'%PDF-scope', self.root / 'scope.pdf', document, UNIT, self.root)
                self.assertIsNotNone(parser)
                self.assertIsNone(status)

    def pdf_dispatch_with_grid(self, header, *, verified=True, text=None):
        source = self.root / 'grid.pdf'
        source.write_bytes(b'%PDF-grid')
        table = MagicMock()
        table.extract.return_value = [header, ['内容'] * len(header)]
        first, second = MagicMock(), MagicMock()
        first.find_tables.return_value = [table]
        pdf = MagicMock()
        pdf.doc.is_extractable = True
        pdf.pages = [first, second]
        plumber = MagicMock()
        plumber.open.return_value.__enter__.return_value = pdf
        document = {'url': 'https://grad.sample.edu.cn/catalog.pdf', 'year': 2027,
                    'kind': 'catalog', 'discovery_kind': 'catalog', 'title': '2027年硕士招生专业目录'}
        text = text or '样本大学2027年硕士研究生招生专业目录\n院、系所 专业代码及名称 学习方式 研究方向 招生人数 考试科目 备注'
        with patch('collectors.national_catalog.extract_pdf_text', return_value=text), \
             patch('collectors.catalog_pdf_national._plumber', return_value=plumber):
            result = _adapter(source.read_bytes(), source, document, UNIT, self.root,
                              publication_verified=verified)
        second.find_tables.assert_not_called()
        return result, plumber

    def test_verified_ecust_physical_header_selects_v3_with_wrapped_labels(self):
        header = ['院、系所', '专业代码及名称', '学习\n方式', '研究方向', '招生\n人数', '考试科目', '备注']
        result, plumber = self.pdf_dispatch_with_grid(header)
        self.assertEqual(result, ('pdf_table_national_v3', None, ''))
        plumber.open.assert_called_once()

    def test_v3_dispatch_requires_verified_publication_and_exact_physical_header(self):
        header = ['院、系所', '专业代码及名称', '学习\n方式', '研究方向', '招生\n人数', '考试科目', '备注']
        result, plumber = self.pdf_dispatch_with_grid(header, verified=False)
        self.assertEqual(result[0], 'pdf_table_v1')
        plumber.open.assert_not_called()
        for other in [['学院', *header[1:]], [*header[:5], '复试科目', '备注'],
                      ['说明', '含院、系所、学习方式、考试科目等信息']]:
            with self.subTest(header=other):
                result, _ = self.pdf_dispatch_with_grid(other)
                self.assertEqual(result[0], 'pdf_table_v1')

    def test_existing_row_and_nankai_pdf_dispatch_precedes_new_grid_probe(self):
        header = ['院、系所', '专业代码及名称', '学习\n方式', '研究方向', '招生\n人数', '考试科目', '备注']
        cases = [('样本大学2027年硕士招生专业目录\n院系所专业研究方向学习方式考试方式拟招生人数考试科目\n'
                  '(1)全日制21全国统考', 'pdf_row_v1'),
                 ('样本大学2027年硕士招生专业目录\n考试方式21全国统考 考试科目', 'nankai_raw_v1')]
        for text, expected in cases:
            with self.subTest(expected=expected):
                result, plumber = self.pdf_dispatch_with_grid(header, text=text)
                self.assertEqual(result[0], expected)
                plumber.open.assert_not_called()

    def test_table_with_wrong_year_is_pending_instead_of_a_ready_catalogue(self):
        document = self.saved_document('wrong-year', HTML.replace('2027年', '2026年'),
                                       url='https://grad.sample.edu.cn/catalog')
        national = {'units': [{'unit_id': '123', 'status': 'collected', 'pages': [], 'documents': [document]}]}
        report = collect_saved({'units': [UNIT]}, national, self.root / 'data/report.json', project_root=self.root)
        self.assertEqual(report['units'][0]['files'][0]['status'], 'needs_adapter')
        self.assertEqual(report['metadata']['program_count'], 0)

    def test_changed_embedded_link_rules_invalidate_saved_parse_cache(self):
        import collectors.national_catalog as catalog
        document = self.saved_document('table', HTML, url='https://grad.sample.edu.cn/catalog')
        national = {'units': [{'unit_id': '123', 'status': 'collected', 'pages': [], 'documents': [document]}]}
        code_root = self.root / 'code'
        code_collectors = code_root / 'collectors'
        code_collectors.mkdir(parents=True)
        module = code_collectors / 'national_catalog.py'
        module.write_text('# scheduler implementation\n', 'utf-8')
        publication = code_collectors / 'catalog_publication.py'
        publication.write_text('# publication verifier implementation\n', 'utf-8')
        dependencies = [code_collectors / 'embedded_documents.py', code_root / 'public_navigation.py',
                        code_collectors / 'official_bindings.py', code_root / 'national_entry_proof.py',
                        code_collectors / 'catalog_pdf_national_v3.py', code_collectors / 'catalog_pdf_national_v2.py',
                        code_collectors / 'catalog_pdf_national.py', code_collectors / 'documents.py']
        for path in dependencies:
            path.write_text('# original supported publisher rules\n', 'utf-8')
        with patch.object(catalog, '__file__', str(module)), patch.object(catalog, 'PROJECT_ROOT', code_root):
            def collect():
                return collect_saved({'units': [UNIT]}, national, self.root / 'data/report.json', project_root=self.root)
            previous = collect()['metadata']['parser_revision']
            self.assertEqual(collect()['metadata']['cache_hits'], 1)
            for dependency in dependencies:
                with self.subTest(dependency=dependency.name):
                    dependency.write_text('# changed supported publisher rules\n', 'utf-8')
                    changed = collect()['metadata']
                    self.assertNotEqual(changed['parser_revision'], previous)
                    self.assertEqual(changed['cache_hits'], 0)
                    self.assertEqual(collect()['metadata']['cache_hits'], 1)
                    previous = changed['parser_revision']

    def test_offline_scheduler_preserves_discovery_kind_and_genuine_charter_rows(self):
        originals = self.root / 'data/snapshots/national/2027/123'
        originals.mkdir(parents=True)
        sources = []
        for name, html in [('charter', CHARTER + '<a href="/catalog">招生专业目录</a>'),
                           ('table', CHARTER + HTML)]:
            raw = html.encode()
            path = originals / (name + '.html')
            path.write_bytes(raw)
            sources.append({'id': name, 'unit_id': '123', 'url': 'https://grad.sample.edu.cn/' + name,
                            'sha256': hashlib.sha256(raw).hexdigest(), 'local_path': path.relative_to(self.root).as_posix(),
                            'kind': 'charter', 'year': 2027, 'status': 'fetched', 'content_type': 'text/html;charset=utf-8'})
        national = {'units': [{'unit_id': '123', 'status': 'collected', 'documents': sources, 'pages': []}]}
        with patch('public_sources.fetch_official_document', side_effect=AssertionError('network forbidden')):
            report = collect_saved({'units': [UNIT]}, national, self.root / 'data/report.json', project_root=self.root)
        states = {row['source_id']: row for row in report['units'][0]['files']}
        self.assertEqual(states['charter']['status'], 'not_catalog')
        self.assertIsNone(states['charter']['parser'])
        self.assertEqual(states['table']['status'], 'ready')
        entry = json.loads((self.root / report['units'][0]['ready_entry_path']).read_text('utf-8'))
        self.assertEqual(len(entry['programs']), 1)
        self.assertEqual(entry['documents'][0]['kind'], 'catalog')


if __name__ == '__main__':
    unittest.main()
