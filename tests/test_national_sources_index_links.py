"""A verified static catalogue index can lead to plainly named college pages."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from collectors import national_sources as sources
from public_sources import SourceError


INDEX = 'https://yz.bnu.edu.cn/content/zyml/2027/ssyx.html'
FIRST = 'https://yz.bnu.edu.cn/content/zyml/2027/ssxx_yxsm-001.html'
SECOND = 'https://yz.bnu.edu.cn/content/zyml/2027/ssxx_yxsm-002.html'
UNIT = {'id': '367859', 'name': '北京师范大学', 'source_url': INDEX,
        'official_url': INDEX}
HEADING = '<h1>2027年北京师范大学硕士研究生招生专业目录</h1>'
LINKS = ('<a href="ssxx_yxsm-001.html">001哲学学院</a>'
         '<a href="ssxx_yxsm-002.html">002经济与工商管理学院</a>')


def page(url, html):
    raw = html.encode()
    return {'url': url, 'raw': raw, 'text': html, 'content_type': 'text/html',
            'sha256': hashlib.sha256(raw).hexdigest()}


class CatalogIndexLinkTests(unittest.TestCase):
    def links(self, html, *, kind='catalog', year=2027, url=INDEX):
        title, source_year, quote, parser = sources._html_info(html, UNIT, 2027, url)
        return sources._page_links({'url': url, 'kind': kind, 'year': year,
            'year_evidence': {'url': url, 'quote': quote}}, parser, 2027, UNIT)

    def run_collection(self, root, html, previous=None):
        calls = []
        def fetch(url, unit):
            calls.append(url)
            if url == INDEX:
                return page(url, html)
            if url in {FIRST, SECOND}:
                return page(url, '<h1>学院目录原件</h1>')
            raise SourceError('network_disabled', '测试不访问网络')
        result = sources.collect_unit(UNIT, {}, root / 'snapshots', project_root=root,
                                      fetcher=fetch, previous=previous, max_pages=10)
        return result, calls

    def test_plain_college_labels_from_static_school_year_index_are_fetched(self):
        with tempfile.TemporaryDirectory() as temp:
            result, calls = self.run_collection(Path(temp), HEADING + LINKS)
        self.assertEqual(set(calls), {INDEX, FIRST, SECOND})
        self.assertEqual({row['url'] for row in result['documents']}, {INDEX, FIRST, SECOND})
        self.assertFalse(result['catalog_complete'])
        children = [row for row in result['documents'] if row['url'] != INDEX]
        self.assertTrue(all(row['source_url'] == INDEX and row['year'] == 2027 for row in children))

    def test_resume_saved_index_discovers_previously_omitted_children(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = (HEADING + LINKS).encode()
            (root / 'index.html').write_bytes(raw)
            previous = {'unit_id': UNIT['id'], 'year': 2027, 'pages': [{
                'url': INDEX, 'local_path': 'index.html', 'sha256': hashlib.sha256(raw).hexdigest(),
                'kind': 'catalog', 'year': 2027, 'content_type': 'text/html', 'depth': 0}]}
            result, calls = self.run_collection(root, HEADING + LINKS, previous)
        self.assertEqual(set(calls), {FIRST, SECOND})
        self.assertEqual(len(result['documents']), 3)
        self.assertFalse(result['catalog_complete'])

    def test_visible_anchor_intersection_excludes_hidden_and_inert_colleges(self):
        html = HEADING + LINKS + ('<div hidden><a href="hidden.html">隐藏学院</a></div>'
            '<div inert><a href="inert.html">不可交互学院</a></div>')
        self.assertEqual({row['url'] for row in self.links(html)}, {FIRST, SECOND})

    def test_dynamic_index_does_not_expand_plain_college_links(self):
        for tail in ('<iframe src="/more"></iframe>', '<select><option>学院</option></select>',
                     '<a href="javascript:more()">更多学院</a>'):
            with self.subTest(tail=tail):
                self.assertEqual(self.links(HEADING + LINKS + tail), [])

    def test_wrong_year_school_or_limited_scope_does_not_expand(self):
        for heading in (HEADING.replace('2027', '2026'), HEADING.replace('北京师范大学', '其他大学'),
                        HEADING.replace('硕士研究生', '全日制硕士研究生')):
            with self.subTest(heading=heading):
                self.assertEqual(self.links(heading + LINKS), [])

    def test_ordinary_charter_and_non_catalog_document_do_not_expand_colleges(self):
        self.assertEqual(self.links(HEADING.replace('专业目录', '章程') + LINKS, kind='charter'), [])
        self.assertEqual(self.links(HEADING + LINKS, kind='charter'), [])
        self.assertEqual(self.links(HEADING + LINKS, year=None), [])

    def test_foreign_or_onclick_college_link_keeps_index_untrusted(self):
        for extra in ('<a href="https://other.edu.cn/catalog">其他学院</a>',
                      '<a href="more.html" onclick="loadMore()">其他学院</a>'):
            with self.subTest(extra=extra):
                self.assertEqual(self.links(HEADING + LINKS + extra), [])

    def test_hidden_heading_does_not_supply_catalog_scope(self):
        html = '<div hidden>' + HEADING + '</div><h1>院系介绍</h1>' + LINKS
        self.assertEqual(self.links(html), [])

    def test_member_path_does_not_bypass_explicit_conflicting_year_or_exclusions(self):
        html = HEADING + LINKS + ('<a href="old.html">2026年历史学院</a>'
            '<a href="retest.html">2027年复试学院</a>')
        self.assertEqual({row['url'] for row in self.links(html)}, {FIRST, SECOND})

    def test_visible_viewer_cannot_promote_hidden_anchor_to_catalog_member(self):
        html = HEADING + LINKS + ('<div hidden><a href="hidden.pdf">隐藏学院</a></div>'
            '<div class="wp_pdf_player" pdfsrc="hidden.pdf"></div>')
        links = self.links(html)
        catalog = {row['url'] for row in links if row['kind'] == 'catalog'}
        self.assertEqual(catalog, {FIRST, SECOND})
        self.assertEqual(next(row for row in links if row['url'].endswith('/hidden.pdf'))['kind'], 'attachment')

    def test_base_resolution_cannot_turn_group_into_an_unseen_target(self):
        html = '<base href="/another/">' + HEADING + LINKS
        self.assertEqual(self.links(html), [])


if __name__ == '__main__':
    unittest.main()
