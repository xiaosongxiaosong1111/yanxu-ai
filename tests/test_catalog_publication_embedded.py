"""Offline publication inheritance for real university viewer shapes."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from collectors.catalog_publication import verify_catalog_publication


class EmbeddedPublicationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.unit = {'id': '1', 'name': '测试大学', 'official_url': 'https://test.edu.cn/'}
        self.parent_url = 'https://test.edu.cn/info/17/42057.htm'
        self.url = 'https://test.edu.cn/files/catalog.pdf'
        self.heading = '<h1>测试大学2027年全日制硕士研究生招生专业目录</h1>'
        self.original = b'%PDF-original-bytes'
        (self.root / 'catalog.pdf').write_bytes(self.original)

    def verify(self, body, *, heading=None, head='', url=None, parent_year=2027):
        self.html = '<html><head>' + head + '</head><body>' + (self.heading if heading is None else heading) + body + '</body></html>'
        raw = self.html.encode('utf8')
        (self.root / 'publication.html').write_bytes(raw)
        document = {'id': 'catalog', 'unit_id': '1', 'year': 2027,
            'url': url or self.url, 'local_path': 'catalog.pdf',
            'sha256': hashlib.sha256(self.original).hexdigest(),
            'publication_page': {'unit_id': '1', 'year': parent_year,
                'url': self.parent_url, 'local_path': 'publication.html',
                'content_type': 'text/html', 'sha256': hashlib.sha256(raw).hexdigest()}}
        return verify_catalog_publication(document, self.unit, 2027, project_root=self.root)

    def test_vsb_runtime_viewer_inherits_real_heading_and_mode(self):
        result = self.verify('<main><script>var vsb_pdf_image_data=["/first.jpg"];'
            'showVsbpdfIframe("/files/catalog.pdf","100%","600","0",'
            '"border:1px solid #DDDDDD",vsb_pdf_image_data);</script><p>查看目录原文</p></main>')
        self.assertTrue(result['verified'], result['reason'])
        self.assertEqual(result['mode'], '全日制')
        self.assertEqual(result['documents'][0]['year'], 2027)
        self.assert_relation(result, 'vsb_pdf')

    def test_sudy_viewer_proves_link_without_faking_metadata_as_visible_quote(self):
        body = ('<main><div pdfsrc="/files/catalog.pdf" class="wp_pdf_player" '
            'sudyfile-attr="{\'title\':\'2027年非全日制硕士招生专业目录.pdf\'}" '
            'sudyplayer="wp_pdf_player"></div><p>目录见附件。</p></main>')
        result = self.verify(body, heading='<h1>测试大学2027年硕士招生专业目录</h1>')
        self.assertTrue(result['verified'], result['reason'])
        self.assertIsNone(result['mode'])
        self.assertEqual([row['quote'] for row in result['evidence']], ['测试大学2027年硕士招生专业目录'])
        self.assert_relation(result, 'sudy_pdfsrc')

    def assert_relation(self, result, recipe):
        relations = result.get('link_evidence', [])
        self.assertTrue(relations)
        self.assertIn(recipe, {row['recipe'] for row in relations})
        for row in relations:
            self.assertEqual(row['url'], self.url)
            for proof in row['evidence']:
                self.assertEqual(proof['source_url'], self.parent_url)
                self.assertEqual(self.html[proof['start']:proof['end']], proof['snippet'])

    def test_static_iframe_embed_object_are_exact_links(self):
        for tag, attr in [('iframe', 'src'), ('embed', 'src'), ('object', 'data')]:
            with self.subTest(tag=tag):
                result = self.verify(f'<main><{tag} {attr}="/files/catalog.pdf"></{tag}><p>附件。</p></main>')
                self.assertTrue(result['verified'], result['reason'])

    def test_head_base_resolves_anchor_and_viewer_inside_main(self):
        for component in ['<a href="catalog.pdf">2027年全日制专业目录</a>',
                          '<iframe src="catalog.pdf"></iframe>',
                          '<div class="wp_pdf_player" pdfsrc="catalog.pdf"></div>']:
            with self.subTest(component=component):
                result = self.verify('<main>' + component + '<p>附件。</p></main>', head='<base href="/files/">')
                self.assertTrue(result['verified'], result['reason'])

    def test_base_exact_sdau_cms_link_shape_resolves_from_site_root(self):
        result = self.verify('<a href="cms/file/14d604d7f0c641f6860cb9dc1c040b74">2027年硕士研究生招生专业目录.pdf</a>',
            head='<base href="https://test.edu.cn/">',
            url='https://test.edu.cn/cms/file/14d604d7f0c641f6860cb9dc1c040b74')
        self.assertTrue(result['verified'], result['reason'])

    def test_hidden_or_inert_viewer_and_anchor_do_not_establish_context(self):
        for wrapper in ['<div hidden>{}</div>', '<div inert>{}</div>',
                        '<div aria-hidden="true">{}</div>',
                        '<div style="display:none!important">{}</div>',
                        '<div role="tooltip">{}</div>', '<template>{}</template>']:
            for viewer in ['<iframe src="/files/catalog.pdf"></iframe>',
                           '<script>showVsbpdfIframe("/files/catalog.pdf","100%");</script>',
                           '<div class="wp_pdf_player" pdfsrc="/files/catalog.pdf"></div>',
                           '<a href="/files/catalog.pdf">目录</a>']:
                with self.subTest(wrapper=wrapper, viewer=viewer):
                    self.assertFalse(self.verify(wrapper.format(viewer))['verified'])

    def test_components_in_navigation_outside_actual_main_cannot_prove_link(self):
        for viewer in ['<iframe src="/files/catalog.pdf"></iframe>',
                       '<script>showVsbpdfIframe("/files/catalog.pdf","100%");</script>',
                       '<div class="wp_pdf_player" pdfsrc="/files/catalog.pdf"></div>']:
            with self.subTest(viewer=viewer):
                self.assertFalse(self.verify('<nav>' + viewer + '</nav><main><p>正文没有附件。</p></main>')['verified'])

    def test_metadata_year_exclusion_and_foreign_url_remain_rejections(self):
        for title in ['2026年硕士招生专业目录.pdf', '2027年推免生招生目录.pdf',
                      '2027年留学生硕士招生目录.pdf']:
            with self.subTest(title=title):
                self.assertFalse(self.verify('<div class="wp_pdf_player" pdfsrc="/files/catalog.pdf" '
                    'sudyfile-attr="{\'title\':\'' + title + '\'}"></div>')['verified'])
        self.assertFalse(self.verify('<iframe src="https://foreign.edu.cn/catalog.pdf"></iframe>')['verified'])

    def test_viewer_cannot_fix_wrong_or_absent_parent_year_or_identity(self):
        for heading in ['', '<h1>测试大学2026年硕士招生目录</h1>',
                        '<h1>其他大学2027年硕士招生目录</h1>',
                        '<h1>测试大学2027年推免生招生目录</h1>']:
            with self.subTest(heading=heading):
                self.assertFalse(self.verify('<iframe src="/files/catalog.pdf"></iframe>', heading=heading)['verified'])

    def test_dead_js_arbitrary_url_and_comments_are_not_publication_links(self):
        for body in ['<script>function unused(){showVsbpdfIframe("/files/catalog.pdf","100%");}</script>',
                     '<script>if(false){showVsbpdfIframe("/files/catalog.pdf","100%");}</script>',
                     '<script>const text="/files/catalog.pdf";</script>',
                     '<!-- <iframe src="/files/catalog.pdf"></iframe> -->']:
            with self.subTest(body=body):
                self.assertFalse(self.verify(body)['verified'])

    def test_base_cannot_redirect_relative_href_to_another_official_subdomain(self):
        for body in ['<a href="catalog.pdf">2027年专业目录</a>', '<iframe src="catalog.pdf"></iframe>']:
            with self.subTest(body=body):
                result = self.verify(body, head='<base href="https://other.test.edu.cn/files/">',
                    url='https://other.test.edu.cn/files/catalog.pdf')
                self.assertFalse(result['verified'])

    def test_wrong_attachment_context_is_rejected_even_if_same_original_bytes(self):
        self.assertFalse(self.verify('<iframe src="/files/unrelated.pdf"></iframe>')['verified'])

    def test_encoded_reserved_path_separators_are_not_equivalent_attachment_urls(self):
        for encoded, literal in [('%2F', '/'), ('%26', '&'), ('%3B', ';'), ('%3A', ':')]:
            with self.subTest(encoded=encoded):
                body = '<a href="/files/folder' + literal + 'catalog.pdf">2027年专业目录</a>'
                self.assertFalse(self.verify(body, url='https://test.edu.cn/files/folder' + encoded + 'catalog.pdf')['verified'])

    def test_base_relation_proof_retains_exact_original_base_markup(self):
        result = self.verify('<a href="catalog.pdf">2027年专业目录</a>', head='<base href="/files/">')
        self.assertTrue(result['verified'])
        spans = [proof for row in result.get('link_evidence', []) for proof in row['evidence']]
        self.assertIn('<base href="/files/">', [row['snippet'] for row in spans])

    def test_encoded_unreserved_and_unicode_path_equivalence_is_retained(self):
        self.assertTrue(self.verify('<a href="/files/%63atalog.pdf">目录</a>')['verified'])
        self.assertTrue(self.verify('<a href="/files/目录.pdf">目录</a>',
            url='https://test.edu.cn/files/%E7%9B%AE%E5%BD%95.pdf')['verified'])

    def test_cross_origin_base_cannot_override_an_explicit_absolute_official_link(self):
        result = self.verify('<a href="https://test.edu.cn/files/catalog.pdf">目录</a>',
            head='<base href="https://elsewhere.example/">')
        self.assertTrue(result['verified'], result['reason'])

    def test_hidden_base_is_ignored_and_later_visible_base_is_replayed(self):
        for attribute in ['hidden', 'inert', 'aria-hidden="true"', 'class="hidden"', 'role="tooltip"']:
            with self.subTest(attribute=attribute):
                result = self.verify('<a href="catalog.pdf">目录</a>', head=(
                    '<base ' + attribute + ' href="/wrong/"><base href="/files/">'))
                self.assertTrue(result['verified'], result['reason'])


if __name__ == '__main__':
    unittest.main()
