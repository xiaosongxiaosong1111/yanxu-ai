"""Recorded official navigation is discoverable without evaluating JavaScript."""
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from public_navigation import MAX_CANDIDATES, extract_navigation
from public_sources import SourceError, extract_saved_document, fetch_navigation_resource, fetch_official_document
from public_sources import official_source_url


FIXTURES = Path(__file__).parent / 'fixtures' / 'official_discovery' / 'navigation'
UNIT = {'id': '123', 'name': '样本大学', 'official_url': 'https://example.edu.cn/'}


def record(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def recorded_snippet(name, needle):
    row = record(name)
    return row['requested_url'], next(item['raw_html_snippets'][0] for item in row['snippets']
                                      if item['needle'] == needle)


def resolver(host, port, **kwargs):
    return [(2, 1, 6, '', ('93.184.216.34', port))]


class Response(io.BytesIO):
    def __init__(self, body=b'', status=200, **headers):
        super().__init__(body)
        self.status = status
        self.headers = {'Content-Type': 'text/html; charset=utf-8', **headers}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class NavigationExtractionTests(unittest.TestCase):
    def test_complete_bisu_recordings_match_original_hashes_and_resolve_latest_notice(self):
        html_raw = (FIXTURES / 'bisu-homepage.html').read_bytes()
        script_raw = (FIXTURES / 'bisu-action.js').read_bytes()
        for name, raw in [('bisu-homepage.html', html_raw), ('bisu-action.js', script_raw),
                          ('sssri-navigation.js', (FIXTURES / 'sssri-navigation.js').read_bytes())]:
            self.assertEqual(hashlib.sha256(raw).hexdigest(), record(name + '.record.json')['sha256'])
        script_url = record('bisu-action.js.record.json')['url']
        rows = extract_navigation(html_raw.decode('utf-8'), 'https://yanzhao.bisu.edu.cn/',
                                  script_sources={script_url: script_raw.decode('utf-8')})
        article = next(row for row in rows if row['url'].endswith('/content/WS6abf29facf1e7b21db96eb68.html'))
        self.assertIn('2027年硕士研究生招生章程', article['label'])
        source_texts = {'https://yanzhao.bisu.edu.cn/': html_raw.decode('utf-8'),
                        script_url: script_raw.decode('utf-8')}
        for proof in article['evidence']:
            self.assertEqual(proof['snippet'], source_texts[proof['source_url']][proof['start']:proof['end']])

    def test_bisu_requires_actual_referenced_script_before_resolving_data_id(self):
        sources = record('bisu-evidence.json')['sources']
        page, script = sources['homepage'], sources['script']
        html = '<script src="' + script['requested_url'] + '"></script>' + page['raw_snippet']
        without = extract_navigation(html, page['requested_url'])
        self.assertEqual([row['kind'] for row in without], ['script'])
        rows = extract_navigation(html, page['requested_url'],
                                  script_sources={script['requested_url']: script['raw_snippet']})
        article = next(row for row in rows if row['kind'] == 'page')
        self.assertEqual(article['url'], page['mapping']['derived_url'])
        self.assertIn(page['mapping']['title'], article['label'])
        self.assertEqual(article['role'], 'onclick')
        self.assertEqual({item['source_url'] for item in article['evidence']},
                         {page['requested_url'], script['requested_url']})

    def test_unreferenced_or_comment_only_bisu_script_cannot_prove_a_route(self):
        sources = record('bisu-evidence.json')['sources']
        page, script = sources['homepage'], sources['script']
        self.assertEqual(extract_navigation(page['raw_snippet'], page['requested_url'],
            script_sources={script['requested_url']: script['raw_snippet']}), [])
        html = '<script src="' + script['requested_url'] + '"></script>' + page['raw_snippet']
        rows = extract_navigation(html, page['requested_url'],
            script_sources={script['requested_url']: '/*' + script['raw_snippet'] + '*/'})
        self.assertEqual([row['kind'] for row in rows], ['script'])

    def test_cug_recorded_click_route_needs_json_dom_and_published_code(self):
        html = (FIXTURES / 'cug-index.html').read_text(encoding='utf-8')
        target = 'https://yz.cug.edu.cn/page/detail/PVKZRL/892/66288'
        rows = extract_navigation(html, 'https://yz.cug.edu.cn/')
        article = next(row for row in rows if row['url'] == target)
        self.assertIn('2027年招收攻读硕士', article['label'])
        self.assertEqual(article['role'], 'published_route')
        self.assertGreaterEqual(len(article['evidence']), 3)
        for changed in (html.replace("'/page/detail/'", "'/unproved/'"),
                        html.replace('id="66288"', 'id="999999"'),
                        html.replace('return "";', 'return "https://external.invalid";'),
                        html.replace('return url;', 'return "https://external.invalid";')):
            self.assertNotIn(target, [row['url'] for row in extract_navigation(changed, 'https://yz.cug.edu.cn/')])

    def test_sssri_recorded_document_writeln_exposes_anchor_and_iframe(self):
        url = record('sssri-navigation.js.record.json')['url']
        snippet = (FIXTURES / 'sssri-navigation.js').read_text(encoding='utf-8')
        rows = extract_navigation('<script src="' + url + '"></script>', 'https://www.sssri.com/',
                                  script_sources={url: snippet})
        frame = next(row for row in rows if row['url'].endswith('/col/col4322/index.html'))
        self.assertEqual(frame['role'], 'document_write')
        self.assertEqual(frame['evidence'][0]['source_url'], url)

    def test_sssri_recorded_literal_location_transitions(self):
        for name, suffix in [('sssri_hr.json', '/col/col2121/index.html'),
                             ('sssri_grad_redirect.json', '/col2121/art/2015/art_2121_36941.html')]:
            url, snippet = recorded_snippet(name, 'location.href')
            rows = extract_navigation(snippet, url)
            self.assertIn('https://www.sssri.com' + suffix, [row['url'] for row in rows])

    def test_recorded_vsb_pdf_call_and_native_pdf_frame(self):
        html = (FIXTURES / 'ynufe-2027.html').read_text(encoding='utf-8')
        rows = extract_navigation(html, 'https://www.ynufe.edu.cn/yjsy/info/1010/6011.htm')
        target = 'https://www.ynufe.edu.cn/__local/7/25/DF/27615BDAF8824CC661AAAF40574_E463C9FE_7A812.pdf'
        row = next(item for item in rows if item['url'] == target)
        self.assertEqual((row['kind'], row['role']), ('pdf', 'vsb_pdf'))
        rows = extract_navigation('<iframe src="/catalog.pdf#page=1"></iframe>'
                                  '<iframe src="https://other.edu.cn/a.pdf"></iframe>',
                                  'https://example.edu.cn/article')
        self.assertEqual([row['url'] for row in rows], ['https://example.edu.cn/catalog.pdf'])

    def test_hnucm_recorded_vsb_snippet_remains_attachment_navigation(self):
        url, snippet = recorded_snippet('hnucm_article.json', 'showVsbpdfIframe')
        snippet = snippet[snippet.index('showVsbpdfIframe'):]
        rows = extract_navigation('<script>' + snippet.split('</script>')[0] + '</script>', url)
        self.assertTrue(any(row['url'].endswith('4E68EC0D8303646DF91EAC60F67_0E05D953_348BD.pdf') for row in rows))

    def test_code_in_comments_strings_templates_and_inert_html_is_not_navigation(self):
        statement = 'location.href="/not-published";'
        for code in ['/*' + statement + '*/', '// ' + statement,
                     'const example = \'location.href="/not-published";\';',
                     '`' + statement + '`', 'if(false){' + statement + '}',
                     'function unused(){' + statement + '}', 'if(false)' + statement]:
            self.assertEqual(extract_navigation('<script>' + code + '</script>', 'https://example.edu.cn/'), [])
        for tag in ('template', 'noscript'):
            self.assertEqual(extract_navigation('<' + tag + '><a href="/not-published">X</a></' + tag + '>',
                                                'https://example.edu.cn/'), [])
        self.assertEqual(extract_navigation('<script type="application/json">' + statement + '</script>',
                                            'https://example.edu.cn/'), [])

    def test_document_write_accepts_only_literal_argument_and_no_arbitrary_expressions(self):
        html = '''<script>document.writeln("<a href='/catalog'>招生目录</a>");</script>'''
        self.assertEqual(extract_navigation(html, 'https://example.edu.cn/')[0]['url'], 'https://example.edu.cn/catalog')
        for argument in ['fetch("/private")', '"<a href=\'/catalog\'>" + secret', '`<a href="/catalog">`']:
            self.assertEqual(extract_navigation('<script>document.write(' + argument + ');</script>',
                                                'https://example.edu.cn/'), [])

    def test_normal_links_are_bounded_safe_deduplicated_and_have_exact_source_spans(self):
        html = '<a href="/catalog">目录</a><area href="/catalog" alt="duplicate">'
        rows = extract_navigation(html, 'https://example.edu.cn/')
        self.assertEqual(len(rows), 1)
        for proof in rows[0]['evidence']:
            self.assertEqual(html[proof['start']:proof['end']], proof['snippet'])
        invalid = ['javascript:alert(1)', 'file:///private', 'http://127.0.0.1/',
                   'https://u:p@example.edu.cn/', 'https://example.edu.cn:8443/x', '#', 'data:text/html,x']
        self.assertEqual(extract_navigation(''.join('<a href="' + value + '">X</a>' for value in invalid),
                                            'https://example.edu.cn/'), [])
        many = ''.join('<a href="/page' + str(i) + '">X</a>' for i in range(MAX_CANDIDATES + 40))
        self.assertEqual(len(extract_navigation(many, 'https://example.edu.cn/')), MAX_CANDIDATES)

    def test_coal_recorded_cms_cannot_widen_ports_without_scoped_authority(self):
        record_url = record('coal-cms-record.json')['articleURL']
        self.assertEqual(extract_navigation('<a href="' + record_url + '">公开章程</a>',
                                            'https://example.edu.cn/'), [])

    def test_navigation_never_changes_canonical_body_or_table_text(self):
        raw = '<main><table><tr><td>081200</td><td>④408</td></tr></table></main><script>location.href="/catalog";</script>'.encode()
        document = extract_saved_document(raw, 'text/html', 'https://example.edu.cn/')
        self.assertEqual(document['text'], '081200\t④408')
        self.assertNotIn('location.href', document['text'])
        self.assertTrue(any(row['url'].endswith('/catalog') for row in document['navigation']))


class NavigationFetchTests(unittest.TestCase):
    def test_scoped_cug_omitted_campus_title_needs_visible_full_name_or_exact_school_code(self):
        unit = {'id': '368369', 'name': '中国地质大学(武汉)', 'unit_code': '10491',
                'official_url': 'https://yz.cug.edu.cn/'}
        scope = {'scheme': 'https', 'host': 'yz.cug.edu.cn', 'port': 443,
                 'path': '/', 'path_mode': 'prefix', 'account': None, 'public_ip_exception': False}
        scoped = {**unit, 'official_authorities': [{'unit_id': unit['id'], 'state': 'verified',
                  'replay_verified': True, 'scope': scope, 'expires_at': '2099-01-01T00:00:00+00:00'}]}
        raw = (FIXTURES / 'cug-index.html').read_bytes()
        page = fetch_official_document(unit['official_url'], scoped, resolver=resolver,
                                      transport=lambda *args: Response(raw))
        self.assertEqual(page['attribution_status'], 'verified')
        for body, accepted in [
                ('<h1>中国地质大学研究生招生网</h1><p>招生单位代码：10491</p>', True),
                ('<h1>中国地质大学研究生招生网</h1><p>招生单位代码：104910</p>', False),
                ('<h1>中国地质大学研究生招生网</h1><script>中国地质大学(武汉)</script>', False),
                ('<h1>中国地质大学(北京)研究生招生网</h1><p>中国地质大学(武汉)</p>', False),
                ('<h1>另一大学招生章程</h1><p>中国地质大学(武汉)</p>', False)]:
            with self.subTest(body=body):
                if accepted:
                    page = fetch_official_document(unit['official_url'], scoped, resolver=resolver,
                        transport=lambda *args: Response(body.encode()))
                    self.assertEqual(page['attribution_status'], 'verified')
                else:
                    with self.assertRaises(SourceError) as error:
                        fetch_official_document(unit['official_url'], scoped, resolver=resolver,
                            transport=lambda *args: Response(body.encode()))
                    self.assertEqual(error.exception.code, 'wrong_unit')
        with self.assertRaises(SourceError):
            fetch_official_document(unit['official_url'], unit, resolver=resolver,
                transport=lambda *args: Response('<h1>中国地质大学研究生招生网</h1><p>中国地质大学(武汉)</p>'.encode()))

    def test_bisu_homepage_uses_first_visible_h1_and_not_later_department_cards(self):
        raw = (FIXTURES / 'bisu-homepage.html').read_bytes()
        unit = {'id': '367863', 'name': '北京第二外国语学院', 'official_url': 'https://yanzhao.bisu.edu.cn/'}
        page = fetch_official_document(unit['official_url'], unit, resolver=resolver,
                                      transport=lambda *args: Response(raw))
        self.assertEqual(page['document_heading'], 'MTA/MBA教育中心')
        self.assertEqual(page['attribution_status'], 'verified')
        self.assertIs(page['network_verified'], True)
        raw = '<h1 hidden>隐藏学院</h1><h1>样本大学招生章程</h1><h1>其他研究院卡片</h1>'.encode()
        page = fetch_official_document(UNIT['official_url'], UNIT, resolver=resolver,
                                      transport=lambda *args: Response(raw))
        self.assertEqual(page['document_heading'], '样本大学招生章程')
        with self.assertRaises(SourceError) as error:
            fetch_official_document(UNIT['official_url'], UNIT, resolver=resolver,
                transport=lambda *args: Response('<h1>另一大学招生章程</h1><p>提到样本大学</p>'.encode()))
        self.assertEqual(error.exception.code, 'wrong_unit')

    def test_http_200_explicit_captcha_and_login_challenge_are_restricted(self):
        pages = [
            '<title>Security verification</title><form><input name="captcha"></form><body>Please complete verification</body>',
            '<title>用户登录</title><form><input name="username"><input type="password"></form><p>请先登录后访问</p>',
            '<h1>请输入验证码下载附件</h1><input id="codeValue">',
            '<title>Access Denied</title><p>You do not have permission to access this page.</p>',
        ]
        for html in pages:
            with self.subTest(html=html):
                with self.assertRaises(SourceError) as error:
                    fetch_official_document(UNIT['official_url'], UNIT, resolver=resolver,
                        transport=lambda *args: Response(html.encode()))
                self.assertEqual(error.exception.code, 'restricted')

    def test_recorded_nsmc_download_captcha_is_restricted_with_html_or_pdf_header(self):
        raw = (FIXTURES / 'nsmc-download-captcha.html').read_bytes()
        pdf_extractor = Mock()
        for media in ('text/html; charset=utf-8', 'application/pdf'):
            with self.subTest(media=media):
                with self.assertRaises(SourceError) as error:
                    fetch_official_document('https://example.edu.cn/catalog.pdf', UNIT, resolver=resolver,
                        pdf_extractor=pdf_extractor,
                        transport=lambda *args: Response(raw, **{'Content-Type': media}))
                self.assertEqual(error.exception.code, 'restricted')
        pdf_extractor.assert_not_called()

    def test_normal_admissions_captcha_instructions_and_footer_login_do_not_block_body(self):
        html = '''<title>样本大学2027年招生章程</title><main><h1>样本大学2027年招生章程</h1>
        <p>网上报名时请输入验证码，登录报名系统完成信息确认。验证码仅用于登录与下载。</p>
        <form><input name="search"><button>搜索</button></form></main>
        <footer><a href="/login">用户登录</a><form><input type="password"><input name="captcha"></form></footer>'''
        page = fetch_official_document(UNIT['official_url'], UNIT, resolver=resolver,
                                      transport=lambda *args: Response(html.encode()))
        self.assertEqual(page['extraction_status'], 'extracted')
        self.assertIn('网上报名时请输入验证码', page['text'])

    def test_explicit_challenge_header_is_restricted_but_cdn_header_alone_is_not(self):
        raw = '<h1>样本大学招生章程</h1><p>学制3年。</p>'.encode()
        with self.assertRaises(SourceError) as error:
            fetch_official_document(UNIT['official_url'], UNIT, resolver=resolver,
                transport=lambda *args: Response(raw, **{'cf-mitigated': 'challenge'}))
        self.assertEqual(error.exception.code, 'restricted')
        page = fetch_official_document(UNIT['official_url'], UNIT, resolver=resolver,
            transport=lambda *args: Response(raw, **{'Server': 'cloudflare', 'CF-Ray': 'test'}))
        self.assertEqual(page['extraction_status'], 'extracted')

    def test_quarantined_navigation_can_still_surface_public_links_from_access_gate(self):
        raw = '<title>Security verification</title><input name="captcha"><a href="/public-help">官方访问说明</a>'.encode()
        resource = fetch_navigation_resource(UNIT['official_url'], UNIT, resolver=resolver,
                                            transport=lambda *args: Response(raw))
        self.assertTrue(resource['navigation_only'])
        self.assertEqual(extract_navigation(resource['text'], resource['url'])[0]['url'],
                         'https://example.edu.cn/public-help')

    def test_active_authority_restricts_exact_host_department_path_and_expiry(self):
        scope = {'scheme': 'https', 'host': 'example.edu.cn', 'port': 443,
                 'path': '/department/', 'path_mode': 'prefix', 'account': None,
                 'public_ip_exception': False}
        row = {'unit_id': '123', 'state': 'verified', 'replay_verified': True,
               'scope': scope, 'expires_at': '2099-01-01T00:00:00+00:00'}
        unit = {**UNIT, 'official_authorities': [row]}
        self.assertTrue(official_source_url('https://example.edu.cn/department/catalog', unit))
        for url in ('https://example.edu.cn/other', 'https://other.example.edu.cn/department/catalog',
                    'https://example.edu.cn/department-other/catalog'):
            self.assertFalse(official_source_url(url, unit))
        for changed in ({'state': 'revoked'}, {'replay_verified': False}, {'unit_id': '999'},
                        {'expires_at': '2000-01-01T00:00:00+00:00'}, {'expires_at': 'bad-date'},
                        {'expires_at': '2099-01-01T00:00:00'}):
            self.assertFalse(official_source_url('https://example.edu.cn/department/catalog',
                {**UNIT, 'official_authorities': [{**row, **changed}]}))

    def test_explicit_public_ip_cms_scope_is_pinned_without_global_port_changes(self):
        scope = {'scheme': 'http', 'host': '125.34.90.207', 'port': 8088,
                 'path': '/api/doctorRecruit/cms/', 'path_mode': 'prefix',
                 'account': None, 'public_ip_exception': True}
        url = 'http://125.34.90.207:8088/api/doctorRecruit/cms/getArticle/114'
        transport = Mock(return_value=Response(b'{"code":200}', **{'Content-Type': 'application/json'}))
        dns = Mock()
        resource = fetch_navigation_resource(url, authority_scope=scope, include_json=True,
                                             resolver=dns, transport=transport)
        dns.assert_not_called()
        self.assertEqual(transport.call_args[0][1], ('125.34.90.207', 8088, ('125.34.90.207',)))
        self.assertTrue(resource['navigation_only'])
        self.assertIs(resource['network_verified'], True)
        self.assertNotIn('extraction', resource)
        self.assertFalse(official_source_url(url, UNIT))
        html = '<a href="' + url + '">公开CMS文章</a>'
        self.assertEqual(extract_navigation(html, url), [])
        self.assertEqual(extract_navigation(html, url, authority_scope=scope)[0]['url'], url)

    def test_scope_redirect_cannot_escape_its_path_or_change_to_a_private_ip(self):
        scope = {'scheme': 'https', 'host': 'example.edu.cn', 'port': 443,
                 'path': '/public/', 'path_mode': 'prefix', 'account': None,
                 'public_ip_exception': False}
        transport = Mock(return_value=Response(status=302, Location='/private/admin'))
        dns = Mock(side_effect=resolver)
        with self.assertRaises(SourceError) as error:
            fetch_navigation_resource('https://example.edu.cn/public/nav.js', authority_scope=scope,
                                      resolver=dns, transport=transport)
        self.assertEqual(error.exception.code, 'wrong_unit')
        self.assertEqual((dns.call_count, transport.call_count), (1, 1))
        for host in ('127.0.0.1', '10.0.0.1'):
            with self.assertRaises(SourceError):
                fetch_navigation_resource('http://' + host + ':8088/public/nav.js',
                    authority_scope={**scope, 'scheme': 'http', 'host': host,
                                     'port': 8088, 'public_ip_exception': True}, transport=Mock())

    def test_json_requires_explicit_scope_even_for_registered_unit(self):
        with self.assertRaises(SourceError) as error:
            fetch_navigation_resource('https://example.edu.cn/api', UNIT, include_json=True)
        self.assertEqual(error.exception.code, 'invalid_limit')

    def test_script_fetch_preserves_raw_hash_and_does_not_create_document_evidence(self):
        raw = b'document.writeln("<a href=\'/catalog\'>Catalog</a>");'
        resource = fetch_navigation_resource('https://example.edu.cn/navigation.js', UNIT,
            resolver=resolver, transport=lambda *args: Response(raw, **{'Content-Type': 'application/javascript'}))
        self.assertEqual(resource['raw'], raw)
        self.assertEqual(resource['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertTrue(resource['navigation_only'])
        self.assertNotIn('extraction', resource)
        self.assertNotIn('pages', resource)

    def test_script_redirect_is_authority_checked_before_dns(self):
        transport = Mock(return_value=Response(status=302, Location='https://other.edu.cn/nav.js'))
        dns = Mock(side_effect=resolver)
        with self.assertRaises(SourceError) as error:
            fetch_navigation_resource('https://example.edu.cn/navigation.js', UNIT, resolver=dns, transport=transport)
        self.assertEqual(error.exception.code, 'wrong_unit')
        self.assertEqual((dns.call_count, transport.call_count), (1, 1))

    def test_navigation_rejects_pdf_json_and_excess_bytes(self):
        for media in ('application/pdf', 'application/json', 'application/octet-stream'):
            with self.assertRaises(SourceError) as error:
                fetch_navigation_resource('https://example.edu.cn/nav.js', UNIT, resolver=resolver,
                    transport=lambda *args: Response(b'%PDF-', **{'Content-Type': media}))
            self.assertEqual(error.exception.code, 'unsupported_type')
        with self.assertRaises(SourceError) as error:
            fetch_navigation_resource('https://example.edu.cn/nav.js', UNIT, max_bytes=4,
                resolver=resolver, transport=lambda *args: Response(b'12345'))
        self.assertEqual(error.exception.code, 'too_large')


if __name__ == '__main__':
    unittest.main()
