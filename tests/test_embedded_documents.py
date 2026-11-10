"""Untrusted attachment discovery from visible, published document viewers."""
import unittest

from collectors.embedded_documents import extract_embedded_documents


PAGE = 'https://grad.example.edu.cn/info/1040/5535.htm'
SDCA_PDF = '/__local/0/F6/E8/D6A5ECB3A9D0883F614638BA858_05E2B5F5_CE07D.pdf'
ECUST_PDF = '/_upload/article/files/bf/41/3eff17e34c75a18efeced1216a5d/09ffa93d-a8f6-4a5c-90cc-87bdbd703293.pdf'


class EmbeddedDocumentsTests(unittest.TestCase):
    def extract(self, html):
        return extract_embedded_documents(html, PAGE)

    def assert_proof(self, html, row):
        self.assertEqual(row['kind'], 'attachment')
        self.assertNotIn('year', row)
        self.assertNotIn('authority', row)
        self.assertNotIn('verified', row)
        for proof in row['evidence']:
            self.assertEqual(proof['source_url'], PAGE)
            self.assertEqual(html[proof['start']:proof['end']], proof['snippet'])

    def test_real_sdca_published_vsb_viewer(self):
        # Exact viewer shape from graduate.sdca.edu.cn/info/1040/5535.htm.
        html = ('<p><script>var vsb_pdf_image_data = ["/first.jpg","/second.jpg"];'
                f'showVsbpdfIframe("{SDCA_PDF}","100%","600","0",'
                '"border:1px solid #DDDDDD",vsb_pdf_image_data);</script></p>')
        row, = self.extract(html)
        self.assertEqual(row['url'], 'https://grad.example.edu.cn' + SDCA_PDF)
        self.assertEqual(row['href'], SDCA_PDF)
        self.assertEqual(row['recipe'], 'vsb_pdf')
        self.assert_proof(html, row)

    def test_real_ecust_sudy_pdf_player(self):
        html = (f'<div pdfsrc="{ECUST_PDF}" swsrc="/unused.swf" '
                'sudyfile-attr="{\'title\':\'华东理工大学2027年学术学位硕士研究生招生专业目录.pdf\'}" '
                'class="wp_pdf_player" sudyplayer="wp_pdf_player" contenteditable="false"></div>')
        row, = self.extract(html)
        self.assertEqual(row['href'], ECUST_PDF)
        self.assertEqual(row['label'], '华东理工大学2027年学术学位硕士研究生招生专业目录.pdf')
        self.assertEqual(row['recipe'], 'sudy_pdfsrc')
        self.assert_proof(html, row)

    def test_sudyplayer_attribute_alone_is_explicit_viewer(self):
        row, = self.extract('<div sudyplayer="wp_pdf_player" pdfsrc="/catalog.pdf"></div>')
        self.assertEqual(row['url'], 'https://grad.example.edu.cn/catalog.pdf')

    def test_base_href_used_for_all_supported_viewers(self):
        for viewer in ['<iframe src="catalog.pdf"></iframe>',
                       '<embed src="catalog.pdf">',
                       '<object data="catalog.pdf"></object>',
                       '<div class="wp_pdf_player" pdfsrc="catalog.pdf"></div>',
                       '<script>showVsbpdfIframe("catalog.pdf","100%");</script>']:
            with self.subTest(viewer=viewer):
                html = '<base href="/files/"><div>' + viewer + '</div>'
                row, = self.extract(html)
                self.assertEqual(row['url'], 'https://grad.example.edu.cn/files/catalog.pdf')
                self.assertEqual(row['href'], 'catalog.pdf')
                self.assert_proof(html, row)

    def test_hidden_first_base_does_not_change_resolution(self):
        html = ('<div hidden><base href="https://elsewhere.example/"></div>'
                '<base href="/files/"><iframe src="catalog.pdf"></iframe>')
        row, = self.extract(html)
        self.assertEqual(row['url'], 'https://grad.example.edu.cn/files/catalog.pdf')

    def test_cross_origin_base_does_not_grant_cross_origin_attachment(self):
        self.assertEqual(self.extract('<base href="https://other.edu.cn/"><iframe src="a.pdf"></iframe>'), [])

    def test_relative_base_has_its_own_provenance(self):
        html = '<base href="/files/"><iframe src="catalog.pdf"></iframe>'
        row, = self.extract(html)
        self.assertEqual(len(row['evidence']), 2)
        self.assert_proof(html, row)

    def test_iframe_embed_object_static_candidates(self):
        for tag, attr in [('iframe', 'src'), ('embed', 'src'), ('object', 'data')]:
            with self.subTest(tag=tag):
                html = f'<{tag} {attr}="/download?file=catalog" title="目录原文"></{tag}>'
                row, = self.extract(html)
                self.assertEqual(row['href'], '/download?file=catalog')
                self.assertEqual(row['label'], '目录原文')
                self.assert_proof(html, row)

    def test_hidden_and_inert_subtrees_are_rejected(self):
        for wrapper in ['<div hidden>{}</div>', '<div inert>{}</div>',
                        '<div aria-hidden="true">{}</div>',
                        '<div style="display: none !important">{}</div>',
                        '<div style="visibility:hidden!important">{}</div>',
                        '<div role="tooltip">{}</div>', '<div class="popover-content">{}</div>',
                        '<div class="d-none">{}</div>', '<template>{}</template>', '<noscript>{}</noscript>']:
            for viewer in ['<iframe src="/a.pdf"></iframe>',
                           '<div class="wp_pdf_player" pdfsrc="/a.pdf"></div>',
                           '<script>showVsbpdfIframe("/a.pdf","100%");</script>']:
                with self.subTest(wrapper=wrapper, viewer=viewer):
                    self.assertEqual(self.extract(wrapper.format(viewer)), [])

    def test_hidden_void_element_does_not_hide_following_viewer(self):
        html = '<input hidden><iframe src="/a.pdf"></iframe>'
        row, = self.extract(html)
        self.assertEqual(row['href'], '/a.pdf')

    def test_hidden_nested_closing_tag_cannot_reveal_outer_hidden_subtree(self):
        html = '<div hidden><span></span><iframe src="/a.pdf"></iframe></div>'
        self.assertEqual(self.extract(html), [])

    def test_unclosed_hidden_subtree_remains_hidden(self):
        self.assertEqual(self.extract('<div hidden><iframe src="/a.pdf"></iframe>'), [])

    def test_comments_attributes_dead_code_and_arbitrary_scripts_are_not_links(self):
        for html in ['<!-- <iframe src="/a.pdf"></iframe> -->',
                     '<div title=\'<iframe src="/a.pdf"></iframe>\'></div>',
                     '<script>var text = \'showVsbpdfIframe("/a.pdf","100%");\';</script>',
                     '<script>function unused(){showVsbpdfIframe("/a.pdf","100%");}</script>',
                     '<script>if(false){showVsbpdfIframe("/a.pdf","100%");}</script>',
                     '<script>var unrelated="/a.pdf";</script>',
                     '<div pdfsrc="/a.pdf"></div>',
                     '<div class="not_wp_pdf_player" pdfsrc="/a.pdf"></div>',
                     '<span class="wp_pdf_player" pdfsrc="/a.pdf"></span>',
                     '<a href="/a.pdf">Normal anchors belong to the existing collector</a>']:
            with self.subTest(html=html):
                self.assertEqual(self.extract(html), [])

    def test_external_schemes_ports_and_userinfo_rejected(self):
        for target in ['https://other.edu.cn/a.pdf', 'http://grad.example.edu.cn/a.pdf',
                       '//elsewhere.edu.cn/a.pdf', 'javascript:alert(1)', 'data:application/pdf,x',
                       'file:///a.pdf', 'https://u:p@grad.example.edu.cn/a.pdf',
                       'https://grad.example.edu.cn:8443/a.pdf']:
            with self.subTest(target=target):
                self.assertEqual(self.extract(f'<iframe src="{target}"></iframe>'), [])

    def test_duplicates_are_one_candidate_with_original_evidence(self):
        html = '<iframe src="/a.pdf"></iframe><embed src="/a.pdf">'
        row, = self.extract(html)
        self.assertEqual(row['href'], '/a.pdf')
        self.assert_proof(html, row)

    def test_input_limits_and_invalid_arguments(self):
        self.assertEqual(extract_embedded_documents(None, PAGE), [])
        self.assertEqual(extract_embedded_documents('<iframe src="/a.pdf">', 'file:///x'), [])
        self.assertEqual(self.extract(' ' * (2 * 1024 * 1024 + 1)), [])

    def test_invalid_base_and_structure_limits_fail_closed(self):
        self.assertEqual(self.extract('<base href="http://["><iframe src="/a.pdf"></iframe>'), [])
        self.assertEqual(self.extract('<div>' * 257 + '<iframe src="/a.pdf">'), [])

    def test_many_normal_anchors_do_not_hide_the_document_viewer(self):
        html = '<a href="/navigation">菜单</a>' * 250 + '<iframe src="/catalog.pdf"></iframe>'
        row, = self.extract(html)
        self.assertEqual(row['href'], '/catalog.pdf')


if __name__ == '__main__':
    unittest.main()
