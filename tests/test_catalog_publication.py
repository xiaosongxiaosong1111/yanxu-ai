import hashlib
import tempfile
from pathlib import Path
import unittest

from collectors.catalog_publication import publication_context_signature, verify_catalog_publication


class CatalogPublicationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.unit = {'id': '1', 'unit_code': '10001', 'name': '测试大学', 'official_url': 'https://test.edu.cn/'}
        self.child = self.root / 'catalog.pdf'
        self.child.write_bytes(b'%PDF-source')
        self.parent = self.root / 'publication.html'
        self.doc = {'id': 'catalog', 'unit_id': '1', 'year': 2027, 'kind': 'catalog',
                    'url': 'https://test.edu.cn/files/catalog.pdf', 'local_path': 'catalog.pdf',
                    'sha256': hashlib.sha256(self.child.read_bytes()).hexdigest()}
        self.save('<h1>测试大学2027年硕士研究生招生专业目录</h1><a href="/files/catalog.pdf">计算机学院目录</a>')

    def save(self, html):
        self.parent.write_text(html, 'utf8')
        self.doc = {**self.doc, 'publication_page': {'id': 'pub', 'unit_id': '1', 'year': 2027,
                    'url': 'https://test.edu.cn/admission/2027.htm', 'local_path': 'publication.html',
                    'sha256': hashlib.sha256(self.parent.read_bytes()).hexdigest(), 'content_type': 'text/html'}}

    def verify(self, document=None):
        return verify_catalog_publication(document or self.doc, self.unit, 2027, project_root=self.root)

    def test_official_current_parent_and_actual_relative_href_prove_attachment_context(self):
        actual = self.verify()
        self.assertTrue(actual['verified'])
        self.assertEqual(actual['mode'], None)
        self.assertEqual(actual['documents'][0]['kind'], 'publication')
        self.assertEqual(actual['documents'][0]['year'], 2027)
        self.assertEqual({row['quote'] for row in actual['evidence']}, {'测试大学2027年硕士研究生招生专业目录', '计算机学院目录'})
        self.assertTrue(all(row['url'] == self.doc['publication_page']['url'] and row['page'] == 1 for row in actual['evidence']))

    def test_link_text_mention_and_hidden_or_script_links_do_not_replace_actual_href(self):
        for link in ['<a href="other.pdf">catalog.pdf</a>', '<script><a href="/files/catalog.pdf">目录</a></script>',
                     '<div hidden><a href="/files/catalog.pdf">目录</a></div>', '<p>附件地址 /files/catalog.pdf</p>']:
            self.save('<h1>测试大学2027年硕士研究生招生专业目录</h1>' + link)
            with self.subTest(link=link): self.assertFalse(self.verify()['verified'])

    def test_wrong_year_foreign_parent_and_unscoped_chsi_are_rejected(self):
        for html in ['<h1>测试大学2026年硕士招生目录</h1><a href="/files/catalog.pdf">2027年目录</a>',
                     '<h1>另一所大学2027年硕士招生目录</h1><a href="/files/catalog.pdf">目录</a>']:
            self.save(html)
            self.assertFalse(self.verify()['verified'])
        self.save('<h1>测试大学2027年硕士招生目录</h1><a href="https://test.edu.cn/files/catalog.pdf">目录</a>')
        for url in ['https://other.edu.cn/2027.htm', 'https://yz.chsi.com.cn/sch/schoolInfo--schId-2.dhtml']:
            self.assertFalse(self.verify({**self.doc, 'publication_page': {**self.doc['publication_page'], 'url': url}})['verified'])

    def test_original_file_hashes_paths_and_context_identity_are_checked(self):
        for doc in [{**self.doc, 'sha256': 'a'*64}, {**self.doc, 'local_path': '../outside.pdf'},
                    {**self.doc, 'unit_id': '2'}, {**self.doc, 'year': 2026},
                    {**self.doc, 'publication_page': {**self.doc['publication_page'], 'sha256': 'b'*64}},
                    {**self.doc, 'publication_page': {**self.doc['publication_page'], 'unit_id': '2'}},
                    {**self.doc, 'publication_page': {**self.doc['publication_page'], 'local_path': '../outside.html'}}]:
            with self.subTest(doc=doc): self.assertFalse(self.verify(doc)['verified'])

    def test_same_size_mtime_restored_parent_tampering_changes_cache_signature(self):
        before = publication_context_signature(self.doc, self.unit, 2027, project_root=self.root)
        changed_id = publication_context_signature({**self.doc, 'id': 'different-id'}, self.unit, 2027, project_root=self.root)
        self.assertNotEqual(before, changed_id)
        stamp = self.parent.stat()
        body = self.parent.read_text('utf8').replace('2027', '2026')
        self.parent.write_text(body, 'utf8')
        import os
        os.utime(self.parent, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertFalse(self.verify()['verified'])
        self.assertNotEqual(before, publication_context_signature(self.doc, self.unit, 2027, project_root=self.root))

    def test_attachment_label_can_explicitly_identify_whole_file_learning_mode(self):
        self.save('<h1>测试大学2027年硕士研究生招生目录</h1><a href="/files/catalog.pdf">2027年全日制学术学位硕士研究生招生专业目录.pdf</a>')
        actual = self.verify()
        self.assertEqual(actual['mode'], '全日制')
        self.assertEqual(actual['mode_evidence'][0]['quote'], '2027年全日制学术学位硕士研究生招生专业目录.pdf')

    def test_unconditional_whole_unit_statement_can_supply_mode_but_conditions_cannot(self):
        heading = '<h1>测试大学2027年硕士研究生招生目录</h1>'
        link = '<a href="/files/catalog.pdf">计算机学院目录</a>'
        for text, expected in [('我校2027年仅招收全日制硕士研究生。', '全日制'),
                               ('本目录所列各专业均为非全日制。', '非全日制'),
                               ('报考我校部分专业的考生须选择全日制。', None),
                               ('我校原则上仅招收全日制硕士研究生。', None),
                               ('如选择非全日制，请咨询学院。', None)]:
            self.save(heading + '<p>' + text + '</p>' + link)
            with self.subTest(text=text): self.assertEqual(self.verify()['mode'], expected)

    def test_historical_link_label_and_foreign_student_only_parent_do_not_supply_context(self):
        for heading, label in [('测试大学2027年硕士研究生招生目录', '2026年硕士招生目录'),
                               ('测试大学2027年来华留学生硕士招生目录', '计算机学院')]:
            self.save('<h1>' + heading + '</h1><a href="/files/catalog.pdf">' + label + '</a>')
            self.assertFalse(self.verify()['verified'])

    def test_service_data_root_resolves_data_prefixed_snapshot_paths_inside_root(self):
        data = self.root / 'data'
        data.mkdir()
        (data / 'catalog.pdf').write_bytes(self.child.read_bytes())
        (data / 'publication.html').write_bytes(self.parent.read_bytes())
        doc = {**self.doc, 'local_path': 'data/catalog.pdf',
               'publication_page': {**self.doc['publication_page'], 'local_path': 'data/publication.html'}}
        self.assertTrue(verify_catalog_publication(doc, self.unit, 2027, project_root=data)['verified'])
        relative = {**doc, 'local_path': 'catalog.pdf', 'publication_page': {**doc['publication_page'], 'local_path': 'publication.html'}}
        self.assertTrue(verify_catalog_publication(relative, self.unit, 2027, project_root=data)['verified'])
        self.assertFalse(verify_catalog_publication({**doc, 'local_path': '../catalog.pdf'}, self.unit, 2027, project_root=data)['verified'])


if __name__ == '__main__': unittest.main()
