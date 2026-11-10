import hashlib
from html import escape
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from collectors.national_sources import (
    binding_from_html, classify_link, collect_national, collect_unit,
    fetch_source, source_allowed, normalized_url, HostPacer, main,
)
from public_sources import SourceError


UNIT = {'id': '123', 'name': '样本大学', 'unit_code': '10001',
        'source_url': 'https://yz.chsi.com.cn/sch/schoolInfo--schId-123.dhtml',
        'official_url': 'https://www.sample.edu.cn/'}
BASE = 'https://yz.chsi.com.cn/zsgs/zsjz/detailall.action?schCode=10001'


class Response(io.BytesIO):
    def __init__(self, body=b'', status=200, **headers):
        super().__init__(body)
        self.status = status
        self.headers = {'Content-Type': 'text/html; charset=utf-8', **headers}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


def response(url, text, content_type='text/html; charset=utf-8'):
    raw = text.encode() if isinstance(text, str) else text
    return {'url': url, 'raw': raw, 'text': text if isinstance(text, str) else '',
            'sha256': hashlib.sha256(raw).hexdigest(), 'content_type': content_type}


class NationalSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'data/snapshots/national/2027/123'

    def test_current_catalogs_and_attachments_take_priority_over_old_or_transfer(self):
        self.assertEqual(classify_link('2027年硕士招生专业目录', '/2027.pdf', 2027)['kind'], 'catalog')
        self.assertIsNone(classify_link('2026年硕士招生专业目录', '/2026.pdf', 2027))
        self.assertIsNone(classify_link('2027年硕士调剂办法', '/transfer', 2027))
        self.assertIsNone(classify_link('2027年博士招生目录', '/doctor', 2027))
        attachment = classify_link('附件1：学术学位目录.xlsx', '/files/1.xlsx', 2027,
                                   parent_kind='charter', parent_year=2027)
        self.assertEqual(attachment['year_status'], 'linked_publication')
        self.assertEqual(attachment['kind'], 'catalog')
        self.assertIsNone(classify_link('研究生奖学金', '/award.pdf', 2027))

    def test_http_200_captcha_download_page_is_not_an_excel_original(self):
        raw = '<title>附件下载</title><form><input name="codeValue"><img src="createimage.jsp">请输入验证码</form>'.encode()
        with self.assertRaises(SourceError) as error:
            fetch_source('https://grad.sample.edu.cn/catalog.xls', UNIT,
                resolver=lambda *args, **kwargs: [(2, 1, 6, '', ('93.184.216.34', 443))],
                transport=lambda *args: Response(raw))
        self.assertEqual(error.exception.code, 'restricted')

    def test_vsb_download_gate_without_form_is_restricted_and_never_an_original(self):
        html=('<title>附件下载</title><script>function setCode(){window.location.href="/download?codeValue="+'
              'document.getElementById("codeValue").value}</script><div class="code"><h1>请输入验证码下载附件</h1>'
              '<div class="code_form"><input type="text" name="codeValue" id="codeValue">'
              '<img src="/system/resource/js/filedownload/createimage.jsp"></div></div>')
        with self.assertRaises(SourceError) as error:
            fetch_source('https://grad.sample.edu.cn/system/_content/download.jsp?wbfileid=123',UNIT,
                resolver=lambda *args,**kwargs:[(2,1,6,'',('93.184.216.34',443))],
                transport=lambda *args:Response(html.encode()))
        self.assertEqual(error.exception.code,'restricted')
        fetch=lambda url,unit:response(url,html)
        result=collect_unit(UNIT,{},self.directory,project_root=self.root,fetcher=fetch)
        self.assertEqual(result['status'],'restricted');self.assertFalse(result['pages']);self.assertFalse(result['documents'])
        raw=html.encode();path=self.root/'cached-gate.html';path.write_bytes(raw)
        previous={'unit_id':'123','year':2027,'pages':[{'url':BASE,'local_path':'cached-gate.html',
            'content_type':'text/html','sha256':hashlib.sha256(raw).hexdigest(),'kind':'catalog','year':2027,'checked_at':'2026-10-30T00:00:00Z'}]}
        resumed=collect_unit(UNIT,{},self.directory,project_root=self.root,fetcher=fetch,previous=previous)
        self.assertFalse(resumed['documents']);self.assertEqual(path.read_bytes(),raw)
        legitimate='<title>样本大学2027年招生章程</title><main>公开目录</main>'+html[html.index('<div class="code">'):]
        valid=collect_unit(UNIT,{},self.directory,project_root=self.root,previous={},
            fetcher=lambda url,unit:response(url,legitimate))
        self.assertTrue(valid['pages'])

    def test_extensionless_office_download_keeps_actual_type_and_signature(self):
        raw = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1original'
        mime = 'application/vnd.ms-excel'
        fetched = fetch_source('https://grad.sample.edu.cn/download.jsp?id=1', UNIT,
            resolver=lambda *args, **kwargs: [(2,1,6,'',('93.184.216.34',443))],
            transport=lambda *args: Response(raw, **{'Content-Type':mime}))
        result = collect_unit(UNIT, {}, self.directory, project_root=self.root,
            fetcher=lambda url, unit: {**fetched,'url':url}, max_pages=1)
        self.assertTrue(result['pages'][0]['local_path'].endswith('.xls'))
        self.assertEqual((self.root/result['pages'][0]['local_path']).read_bytes(),raw)
        with self.assertRaises(SourceError):
            fetch_source('https://grad.sample.edu.cn/download.jsp?id=1',UNIT,
                resolver=lambda *args,**kwargs:[(2,1,6,'',('93.184.216.34',443))],
                transport=lambda *args: Response(b'garbage', **{'Content-Type':mime}))

    def test_cached_and_resumed_captcha_is_removed_without_rejecting_report_form(self):
        html = '<title>附件下载</title><form><input name="codeValue"><img src="createimage.jsp">请输入验证码</form>'
        fetch = lambda url, unit: response(url, html)
        first = collect_unit(UNIT, {}, self.directory, project_root=self.root, fetcher=fetch)
        self.assertFalse(first['pages'])
        self.assertEqual(first['status'], 'restricted')
        self.directory.mkdir(parents=True, exist_ok=True)
        raw = html.encode(); local = self.directory/'old.xls'; local.write_bytes(raw)
        old = {'unit_id': '123', 'year': 2027, 'pages': [{'url': BASE,
            'local_path': local.relative_to(self.root).as_posix(), 'content_type':'text/html',
            'sha256': hashlib.sha256(raw).hexdigest(), 'kind':'catalog'}]}
        second = collect_unit(UNIT, {}, self.directory, project_root=self.root, fetcher=fetch, previous=old)
        self.assertFalse(second['pages'])
        legitimate = '<title>样本大学2027年招生章程</title><main>公开目录</main><footer>'+html[html.index('<form>'):]+'</footer>'
        third = collect_unit(UNIT, {}, self.directory, project_root=self.root,
            fetcher=lambda url, unit: response(url, legitimate), previous={})
        self.assertTrue(third['pages'])

    def test_official_binding_requires_exact_saved_chsi_link_and_semantic_role(self):
        html = '<h1>样本大学</h1><p>研究生招生网址：<a href="https://grad.sample.edu.cn/">研究生招生网</a></p>'
        html += '<a href="https://other.edu.cn/ad">其他大学</a><a href="https://evil.com/">学校官网</a>'
        document = {'url': UNIT['source_url'], 'sha256': hashlib.sha256(html.encode()).hexdigest(),
                    'local_path': 'data/profile.html'}
        unknown = {**UNIT, 'official_url': None}
        bindings = binding_from_html(unknown, document, html)
        self.assertEqual([value['url'] for value in bindings], ['https://grad.sample.edu.cn/'])
        self.assertEqual(bindings[0]['evidence']['sha256'], document['sha256'])
        self.assertEqual(bindings[0]['evidence']['quote'], 'https://grad.sample.edu.cn/')
        self.assertFalse(binding_from_html(unknown, {**document, 'url': document['url'].replace('123', '999')}, html))
        self.assertFalse(binding_from_html(unknown, {**document, 'sha256': '0' * 64}, html))
        self.assertFalse(binding_from_html(unknown, {**document, 'url': 'https://other.edu.cn/'}, html))

    def test_hidden_links_and_hidden_ownership_statements_do_not_bind(self):
        for attribute in ['hidden', 'aria-hidden="true"', 'style="display: none"', 'class="tooltip"']:
            html = '<h1>样本大学</h1><div '+attribute+'><p>我校研究生招生网址：https://other.edu.cn/</p><a href="https://other.edu.cn/">研究生招生网</a></div>'
            doc = {'url':UNIT['source_url'], 'sha256':hashlib.sha256(html.encode()).hexdigest()}
            with self.subTest(attribute=attribute):
                self.assertFalse(binding_from_html({**UNIT,'official_url':None},doc,html))

    def test_shared_chsi_codes_and_domains_are_unit_scoped(self):
        self.assertTrue(source_allowed(BASE, UNIT))
        self.assertFalse(source_allowed(BASE.replace('10001', '10002'), UNIT))
        self.assertFalse(source_allowed('https://yz.chsi.com.cn/page?schId=999', UNIT))
        self.assertTrue(source_allowed('https://grad.sample.edu.cn/a.pdf', UNIT))
        self.assertFalse(source_allowed('https://sample.edu.cn.evil.org/a', UNIT))
        self.assertFalse(source_allowed('http://127.0.0.1/', UNIT))

    def test_unscoped_other_school_article_cannot_bind_an_official_domain(self):
        html = '<h1>其他大学</h1><a href="https://www.other.edu.cn/">学校官网</a>'
        doc = {'url': 'https://yz.chsi.com.cn/zsgs/zsjz/article.action?id=999',
               'sha256': hashlib.sha256(html.encode()).hexdigest()}
        self.assertEqual(binding_from_html({**UNIT, 'official_url': None}, doc, html), [])

    def test_plaintext_official_home_stops_before_chinese_and_uses_safe_origin(self):
        html = '<h1>样本大学</h1><p>招生网址：https://grad.sample.edu.cn/index.htm在线咨询及电子邮箱</p>'
        doc = {'url': UNIT['source_url'], 'sha256': hashlib.sha256(html.encode()).hexdigest()}
        result = binding_from_html({**UNIT, 'official_url': None}, doc, html)
        self.assertEqual(result[0]['url'], 'https://grad.sample.edu.cn/')
        self.assertEqual(result[0]['evidence']['quote'], 'https://grad.sample.edu.cn/index.htm')

    def test_school_owned_admissions_roles_accept_real_wording_but_not_another_school(self):
        for prefix in ['我校研究生院网站（网址：', '学校研招网主页：', '研究生处网站网址：',
                       '相关目录见我院研究生部官网（网址：', '样本大学研究生招生信息网：']:
            html = '<h1>样本大学2027年硕士招生章程</h1><p>'+prefix+'https://grad.sample.edu.cn/catalog）</p>'
            doc = {'url': BASE, 'sha256': hashlib.sha256(html.encode()).hexdigest(), 'year': 2027}
            self.assertEqual([b['url'] for b in binding_from_html({**UNIT, 'official_url': None}, doc, html)],
                             ['https://grad.sample.edu.cn/'], prefix)
        html = '<h1>样本大学2027年硕士招生章程</h1><p>部分科目借用其他大学，参考其他大学研究生院网站（https://other.edu.cn/）。</p>'
        doc = {'url': BASE, 'sha256': hashlib.sha256(html.encode()).hexdigest(), 'year': 2027}
        self.assertEqual(binding_from_html({**UNIT, 'official_url': None}, doc, html), [])

    def test_whole_site_chsi_navigation_does_not_consume_each_school_budget(self):
        def fetch(url, unit):
            return response(url, '<h1>样本大学2027年硕士招生章程</h1>'
                '<a href="https://yz.chsi.com.cn/zsml/">硕士招生专业目录</a>'
                '<a href="https://yz.chsi.com.cn/zsgs/zsjz/">硕士招生章程</a>')
        calls = Mock(side_effect=fetch)
        result = collect_unit(UNIT, {}, self.directory, project_root=self.root, fetcher=calls)
        self.assertEqual(calls.call_count, 3)
        self.assertFalse(result['frontier'])
        self.assertFalse(any(p['url'].endswith('/zsml/') for p in result['pages']))

    def test_resume_rechecks_missing_original_instead_of_silently_skipping(self):
        def fetch(url, unit):
            return response(url, '<h1>样本大学2027年硕士招生章程</h1>')
        first = collect_unit(UNIT, {}, self.directory, project_root=self.root, fetcher=fetch)
        original = first['pages'][0]
        (self.root/original['local_path']).unlink()
        again = Mock(side_effect=fetch)
        result = collect_unit(UNIT, {}, self.directory, project_root=self.root, fetcher=again)
        self.assertIn(original['requested_url'], [call.args[0] for call in again.call_args_list])
        self.assertTrue((self.root/result['pages'][0]['local_path']).is_file())

    def test_cached_chsi_snapshots_are_used_and_wrong_hash_is_not_reused(self):
        raw = '<h1>样本大学2027年硕士招生章程</h1>'
        p = self.root/'profile.html'
        p.write_text(raw, 'utf-8')
        inventory = {'pages': [{'url': BASE, 'status': 'fetched', 'snapshot_path': str(p),
            'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}]}
        fetch = Mock(side_effect=lambda url, unit: response(url, raw))
        collect_unit(UNIT, inventory, self.directory, project_root=self.root, fetcher=fetch, max_pages=1)
        fetch.assert_not_called()
        p.write_text('changed', 'utf-8')
        other = self.root/'other'
        collect_unit(UNIT, inventory, other, project_root=self.root, fetcher=fetch, max_pages=1)
        fetch.assert_called_once()

    def test_safe_download_preserves_office_original_and_rejects_other_redirects(self):
        resolver = lambda *args, **kwargs: [(2, 1, 6, '', ('93.184.216.34', 443))]
        raw = b'PK\x03\x04OfficeOriginal'
        data = fetch_source('https://grad.sample.edu.cn/catalog.xlsx', UNIT, resolver=resolver,
            transport=lambda *args: Response(raw, **{'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}))
        self.assertEqual(data['raw'], raw)
        self.assertEqual(data['sha256'], hashlib.sha256(raw).hexdigest())
        blocked = Mock(return_value=Response(status=302, Location='http://127.0.0.1/private'))
        with self.assertRaises(SourceError):
            fetch_source('https://grad.sample.edu.cn/catalog', UNIT, resolver=resolver, transport=blocked)
        self.assertEqual(blocked.call_count, 1)
        for body, headers in [(b'not a PDF', {'Content-Type': 'application/pdf'}),
                              (b'EXE', {'Content-Type': 'application/octet-stream'})]:
            with self.subTest(body=body), self.assertRaises(SourceError):
                fetch_source('https://grad.sample.edu.cn/a.pdf', UNIT, resolver=resolver,
                    transport=lambda *args: Response(body, **headers))

    def test_network_limits_http_failures_login_and_oversize_are_explicit(self):
        resolver = lambda *args, **kwargs: [(2, 1, 6, '', ('93.184.216.34', 443))]
        for result, limit in [(Response(status=403), 20), (Response(status=500), 20),
                              (Response(status=302), 20),
                              (Response('<title>登录</title>'.encode()), 100),
                              (Response(b'x'*21), 20),
                              (Response(b'x', **{'Content-Length':'19'}), 20),
                              (Response(b''), 20),
                              (Response(b'x', **{'Content-Length':'21'}), 20)]:
            with self.subTest(status=result.status, limit=limit), self.assertRaises(SourceError):
                fetch_source('https://grad.sample.edu.cn/a', UNIT, max_bytes=limit,
                    resolver=resolver, transport=lambda *args: result)
        for limit in (0, -1, True, 33*1024*1024):
            with self.assertRaises(SourceError):
                fetch_source('https://grad.sample.edu.cn/a', UNIT, max_bytes=limit)
        self.assertIsNone(normalized_url('javascript:alert(1)'))
        self.assertIsNone(normalized_url('https://user:password@sample.edu.cn/'))
        self.assertIsNone(normalized_url('https://sample.edu.cn:8080/'))
        self.assertIsNone(normalized_url('https://[invalid/'))
        self.assertIn('%E7%9B%AE%E5%BD%95', normalized_url('/目录.pdf', 'https://sample.edu.cn/'))

    def test_source_collection_errors_and_empty_frontier_remain_incomplete(self):
        def fetch(url, unit):
            raise SourceError('http_error', 'HTTP 500')
        result = collect_unit(UNIT, {}, self.directory, project_root=self.root, fetcher=fetch)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['errors']), 3)
        self.assertFalse(result['catalog_complete'])

    def test_navigation_year_is_not_an_original_document_year(self):
        def fetch(url, unit):
            return response(url, '<h1>硕士招生</h1><nav><a href="/">样本大学2027年硕士招生章程</a></nav>')
        result = collect_unit(UNIT, {}, self.directory, project_root=self.root, fetcher=fetch, max_pages=1)
        self.assertIsNone(result['pages'][0]['year'])
        self.assertFalse(result['documents'])

    def test_rendered_chsi_original_restores_catalog_links_and_resume_frontier(self):
        content = '<h1>样本大学2027年硕士招生章程</h1><p>招生网址：https://grad.sample.edu.cn/</p><a href="https://grad.sample.edu.cn/2027/catalog.pdf">2027年硕士专业目录</a>'
        html = '<input type="hidden" id="hid_content" value="' + escape(content, quote=True) + '"><div id="cnt_body_wrapper"></div><script>$("#cnt_body_wrapper").html($("#hid_content").val());</script>'
        target = 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?schCode=10001&infoId=1'
        def fetch(url, unit):
            if url == BASE:
                return response(target, html)
            return response(url, '<h1>样本大学</h1>')
        unknown = {**UNIT, 'official_url': None}
        first = collect_unit(unknown, {}, self.directory, project_root=self.root, fetcher=fetch, max_pages=1)
        self.assertEqual(first['documents'][0]['year_status'], 'explicit_original')
        self.assertTrue(first['bindings'])
        self.assertTrue(any(item['url'].endswith('catalog.pdf') for item in first['frontier']))
        checkpoint = json.loads((self.directory/'state.json').read_text('utf-8'))
        checkpoint['frontier'] = []
        checkpoint['bindings'] = []
        (self.directory/'state.json').write_text(json.dumps(checkpoint), 'utf-8')
        called = Mock(side_effect=lambda url, unit: response(url, b'%PDF-original', 'application/pdf') if url.endswith('.pdf') else response(url, '<h1>样本大学</h1>'))
        second = collect_unit(unknown, {}, self.directory, project_root=self.root, fetcher=called, max_pages=10)
        self.assertTrue(any(call.args[0].endswith('catalog.pdf') for call in called.call_args_list))
        self.assertTrue(any(page['url'].endswith('catalog.pdf') for page in second['pages']))

    def test_host_pacing_and_cli_reuse_the_existing_bounded_fetch(self):
        with patch('collectors.national_sources.fetch_source', return_value={'raw': b'original'}) as fetch:
            pacer = HostPacer(.001)
            self.assertEqual(pacer('https://grad.sample.edu.cn/a', UNIT)['raw'], b'original')
            pacer('https://grad.sample.edu.cn/b', UNIT)
            self.assertEqual(fetch.call_count, 2)
        units = self.root/'units.json'
        inventory = self.root/'inventory.json'
        units.write_text(json.dumps({'units':[UNIT]}), 'utf-8')
        inventory.write_text(json.dumps({'units':[]}), 'utf-8')
        with patch('collectors.national_sources.collect_national') as collect:
            main(['--units',str(units),'--inventory',str(inventory),'--output',str(self.root/'out.json'),
                  '--workers','4','--max-pages','120','--max-depth','10','--refresh'])
            self.assertEqual(collect.call_args.kwargs['max_pages'],120)
            self.assertEqual(collect.call_args.kwargs['max_depth'],10)
            self.assertTrue(collect.call_args.kwargs['refresh'])
        with patch('sys.stderr',io.StringIO()), self.assertRaises(SystemExit):
            main(['--year','2026'])

    def test_crawl_follows_current_catalog_and_office_attachment_beyond_four_pages(self):
        base = 'https://grad.sample.edu.cn/'
        pages = {base: '<a href="/2027/charter">2027年硕士招生章程</a>',
                 base+'2027/charter': '<h1>样本大学2027年硕士招生章程</h1><a href="/2027/catalog">招生专业目录</a>',
                 base+'2027/catalog': '<h1>样本大学2027年硕士专业目录</h1><a href="/2027/dep">001学院目录</a>',
                 base+'2027/dep': '<h1>样本大学2027年硕士目录</h1><a href="/2027/detail">研究方向及初试科目</a>',
                 base+'2027/detail': '<h1>样本大学2027年硕士专业目录</h1><a href="/files/catalog.xlsx">附件1专业目录.xlsx</a>',
                 base+'files/catalog.xlsx': b'PK\x03\x04OfficeOriginal',
                 BASE: '<h1>样本大学2027年硕士招生章程</h1>', UNIT['source_url']: '<h1>样本大学</h1>'}
        calls = []
        def fetch(url, unit):
            calls.append(url)
            value = pages[url]
            return response(url, value, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if isinstance(value, bytes) else 'text/html')
        result = collect_unit({**UNIT, 'official_url': base}, {}, self.directory, project_root=self.root, fetcher=fetch, max_depth=8)
        self.assertIn(base+'files/catalog.xlsx', calls)
        self.assertGreater(len(result['pages']), 4)
        self.assertFalse(result['catalog_complete'])
        file = next(doc for doc in result['documents'] if doc['url'].endswith('.xlsx'))
        self.assertEqual((self.root/file['local_path']).read_bytes(), b'PK\x03\x04OfficeOriginal')
        self.assertEqual(file['year_status'], 'linked_publication')

    def test_limit_preserves_frontier_and_resume_never_reports_catalog_complete(self):
        def fetch(url, unit):
            return response(url, '<h1>样本大学2027年硕士目录</h1><a href="https://grad.sample.edu.cn/2027/next">招生专业目录</a>')
        first = collect_unit(UNIT, {}, self.directory, project_root=self.root, fetcher=fetch, max_pages=1)
        self.assertEqual(first['status'], 'truncated')
        self.assertTrue(first['frontier'])
        self.assertTrue((self.directory/'state.json').is_file())
        seen = []
        def resumed(url, unit):
            seen.append(url)
            return response(url, '<h1>样本大学2027年硕士招生章程</h1>')
        second = collect_unit(UNIT, {}, self.directory, project_root=self.root, fetcher=resumed, max_pages=12)
        self.assertNotIn(first['pages'][0]['url'], seen)
        self.assertFalse(second['frontier'])
        self.assertFalse(second['catalog_complete'])

    def test_transient_failures_retry_on_resume_with_a_bounded_attempt_count(self):
        failing=Mock(side_effect=SourceError('timeout','source timed out'))
        first=collect_unit(UNIT,{},self.directory,project_root=self.root,fetcher=failing)
        self.assertFalse(first['pages'])
        recovered=Mock(side_effect=lambda url,unit: response(url,'<h1>样本大学2027年硕士招生章程</h1>'))
        second=collect_unit(UNIT,{},self.directory,project_root=self.root,fetcher=recovered)
        self.assertTrue(second['pages'])
        self.assertTrue(recovered.called)
        self.assertFalse(any(e['url']==BASE for e in second['errors']))
        for _ in range(4):
            collect_unit(UNIT,{},self.directory,project_root=self.root,fetcher=failing,previous=first)
            first=json.loads((self.directory/'state.json').read_text('utf-8'))
        self.assertLessEqual(len([call for call in failing.call_args_list if call.args[0]==BASE]),3)

    def test_saved_deep_official_entry_is_used_in_addition_to_origin(self):
        deep='https://grad.sample.edu.cn/2027/catalog.htm'
        fetch=Mock(side_effect=lambda url,unit: response(url,'<h1>样本大学2027年硕士招生专业目录</h1>'))
        collect_unit({**UNIT,'official_url_evidence':{'linked_url':deep}}, {}, self.directory,
            project_root=self.root,fetcher=fetch)
        self.assertIn(deep,[call.args[0] for call in fetch.call_args_list])
        fetch.reset_mock()
        collect_unit({**UNIT,'official_url_evidence':{'linked_url':'https://other.edu.cn/catalog'}}, {},
            self.directory,project_root=self.root,fetcher=fetch,previous={})
        self.assertNotIn('https://other.edu.cn/catalog',[call.args[0] for call in fetch.call_args_list])

    def test_persistent_chsi_host_stop_blocks_network_but_other_official_sites_continue(self):
        gate=self.root/'data/runtime/chsi-host-gate.json';gate.parent.mkdir(parents=True)
        gate.write_text(json.dumps({'host':'yz.chsi.com.cn','status':'rate_limited'}),'utf-8')
        fetch=Mock(side_effect=lambda url,unit: response(url,'<h1>样本大学2027年硕士招生专业目录</h1>'))
        result=collect_unit(UNIT,{},self.directory,project_root=self.root,fetcher=fetch)
        self.assertTrue(result['pages'])
        self.assertTrue(fetch.called)
        self.assertFalse(any('yz.chsi.com.cn' in call.args[0] for call in fetch.call_args_list))
        self.assertTrue(any(error['code']=='restricted' for error in result['errors']))

    def test_missing_official_binding_is_explicit_and_other_units_continue(self):
        units = [{**UNIT, 'official_url': None}, {**UNIT, 'id': '456', 'unit_code': '10002',
            'source_url': UNIT['source_url'].replace('123', '456')}]
        def fetch(url, unit):
            if unit['id'] == '123': raise SourceError('restricted', 'HTTP 403')
            return response(url, '<h1>样本大学2027年硕士招生章程</h1>')
        output = self.root/'data/national_sources.json'
        result = collect_national({'units': units}, {'units': []}, output,
            self.root/'data/snapshots/national', project_root=self.root, fetcher=fetch, workers=2)
        self.assertEqual(len(result['units']), 2)
        self.assertEqual(result['metadata']['total_units'], 2)
        self.assertFalse(result['metadata']['catalog_complete'])
        self.assertEqual(result['units'][0]['status'], 'restricted')
        self.assertTrue(result['units'][1]['pages'])
        self.assertEqual(json.loads(output.read_text('utf-8'))['metadata']['total_units'], 2)

    def test_html_base_resolves_real_attachments_instead_of_nested_fallback_paths(self):
        article = 'https://grad.sample.edu.cn/info/17/42057.htm'
        origin = 'https://grad.sample.edu.cn/'
        unit = {**UNIT, 'source_url': article, 'official_url': article, 'unit_code': None}
        html = ('<head><base href="' + origin + '"><base href="https://other.edu.cn/"></head>'
                '<h1>样本大学2027年硕士招生章程</h1>'
                '<a href="cms/file/catalog.pdf">2027年硕士招生专业目录.pdf</a>')
        calls = []
        def fetch(url, unit):
            calls.append(url)
            return response(url, b'%PDF-original', 'application/pdf') if url == origin + 'cms/file/catalog.pdf' else response(url, html)
        result = collect_unit(unit, {}, self.directory, project_root=self.root, fetcher=fetch, max_pages=12)
        self.assertEqual(calls, [article, origin + 'cms/file/catalog.pdf'])
        self.assertFalse(result['frontier'])
        self.assertEqual(result['documents'][-1]['source_url'], article)

    def test_identical_parent_child_html_with_growing_paths_stops_expansion_but_keeps_original(self):
        entry = 'https://grad.sample.edu.cn/index/list.htm'
        unit = {**UNIT, 'source_url': entry, 'official_url': entry, 'unit_code': None}
        html = ('<h1>样本大学2027年硕士招生专业目录</h1>'
                '<a href="index/list.htm">招生工作</a>'
                '<a href="/catalog.pdf">2027年硕士专业目录.pdf</a>')
        fetch = Mock(side_effect=lambda url, unit: response(url, b'%PDF-original', 'application/pdf')
                     if url.endswith('/catalog.pdf') else response(url, html))
        result = collect_unit(unit, {}, self.directory, project_root=self.root, fetcher=fetch,
                              max_pages=20, max_depth=15)
        self.assertEqual(fetch.call_count, 3)
        self.assertFalse(result['frontier'])
        aliases = [page for page in result['pages'] if page.get('crawl_alias_of')]
        self.assertEqual(len(aliases), 1)
        self.assertEqual(aliases[0]['crawl_alias_of'], entry)
        self.assertEqual((self.root / aliases[0]['local_path']).read_bytes(), html.encode())
        self.assertEqual(aliases[0]['crawl_expansion_status'], 'duplicate_path_growth')

    def test_resume_rebuilds_base_links_and_drops_only_obsolete_saved_html_frontier(self):
        article = 'https://grad.sample.edu.cn/info/17/42057.htm'
        origin = 'https://grad.sample.edu.cn/'
        unit = {**UNIT, 'source_url': article, 'official_url': article, 'unit_code': None}
        html = ('<base href="' + origin + '"><h1>样本大学2027年硕士招生章程</h1>'
                '<a href="cms/file/catalog.pdf">2027年硕士专业目录.pdf</a>')
        first = collect_unit(unit, {}, self.directory, project_root=self.root,
                             fetcher=lambda url, unit: response(url, html), max_pages=1)
        wrong = origin + 'info/17/cms/file/catalog.pdf'
        extra = origin + 'extra.pdf'
        previous = {**first, 'frontier': [
            {'url': wrong, 'kind': 'catalog', 'title': '2027年硕士专业目录.pdf',
             'source_url': article, 'depth': 1},
            {'url': extra, 'kind': 'catalog', 'title': '2027年补充专业目录.pdf',
             'source_url': origin + 'not-in-saved-pages', 'depth': 1}]}
        calls = Mock(side_effect=lambda url, unit: response(url, b'%PDF-original', 'application/pdf'))
        second = collect_unit(unit, {}, self.directory, project_root=self.root, fetcher=calls, previous=previous)
        fetched = [call.args[0] for call in calls.call_args_list]
        self.assertNotIn(wrong, fetched)
        self.assertIn(origin + 'cms/file/catalog.pdf', fetched)
        self.assertIn(extra, fetched)
        self.assertFalse(second['frontier'])

    def test_national_totals_distinguish_original_urls_from_distinct_original_bytes(self):
        entry = 'https://grad.sample.edu.cn/index/list.htm'
        unit = {**UNIT, 'source_url': entry, 'official_url': entry, 'unit_code': None}
        html = ('<h1>样本大学2027年硕士招生专业目录</h1>'
                '<a href="index/list.htm">招生工作</a><a href="/catalog.pdf">招生专业目录.pdf</a>')
        result = collect_national({'units': [unit]}, {'units': []}, self.root / 'data/report.json',
            self.root / 'data/snapshots/national', project_root=self.root, max_pages=20,
            fetcher=lambda url, unit: response(url, b'%PDF-original', 'application/pdf')
            if url.endswith('/catalog.pdf') else response(url, html))
        meta = result['metadata']
        self.assertEqual(meta['original_count'], 3)
        self.assertEqual(meta['original_url_count'], 3)
        self.assertEqual(meta['unique_original_count'], 2)
        self.assertEqual(meta['duplicate_original_url_count'], 1)
        self.assertFalse(meta['catalog_complete'])

    def test_resume_keeps_original_alias_chain_but_replays_explicit_base_once(self):
        article = 'https://grad.sample.edu.cn/info/17/42057.htm'
        origin = 'https://grad.sample.edu.cn/'
        unit = {**UNIT, 'source_url': article, 'official_url': article, 'unit_code': None}
        html = ('<base href="' + origin + '"><h1>样本大学2027年硕士招生章程</h1>'
                '<a href="cms/file/catalog.pdf">2027年硕士专业目录.pdf</a>')
        first = collect_unit(unit, {}, self.directory, project_root=self.root,
                             fetcher=lambda url, unit: response(url, html), max_pages=1)
        original = first['pages'][0]
        alias1 = {**original, 'id': 'old-alias-1', 'url': origin + 'info/17/cms/file/catalog.pdf',
                  'requested_url': origin + 'info/17/cms/file/catalog.pdf', 'source_url': article, 'depth': 1}
        alias2 = {**alias1, 'id': 'old-alias-2', 'url': origin + 'info/17/cms/file/cms/file/catalog.pdf',
                  'requested_url': origin + 'info/17/cms/file/cms/file/catalog.pdf',
                  'source_url': alias1['url'], 'depth': 2}
        obsolete = {'url': alias2['url'] + '/another', 'kind': 'catalog', 'depth': 3,
                    'source_url': alias2['url']}
        previous = {**first, 'pages': [original, alias1, alias2], 'frontier': [obsolete],
                    'unresolved_links': [obsolete]}
        calls = Mock(side_effect=lambda url, unit: response(url, b'%PDF-original', 'application/pdf'))
        second = collect_unit(unit, {}, self.directory, project_root=self.root, fetcher=calls, previous=previous)
        self.assertEqual([call.args[0] for call in calls.call_args_list], [origin + 'cms/file/catalog.pdf'])
        self.assertEqual(len(second['pages']), 4)
        self.assertEqual(sum(bool(page.get('crawl_alias_of')) for page in second['pages']), 2)
        self.assertFalse(second['deferred_links'])
        self.assertFalse(second['frontier'])
        self.assertFalse(second['unresolved_links'])
        self.assertEqual(second['suppressed_obsolete_link_count'], 1)

    def test_unbound_base_does_not_authorize_another_school_and_invalid_first_base_is_not_replaced(self):
        article = 'https://grad.sample.edu.cn/info/entry.htm'
        unit = {**UNIT, 'source_url': article, 'official_url': article, 'unit_code': None}
        html = ('<base href="https://other.edu.cn/"><h1>样本大学2027年硕士招生章程</h1>'
                '<a href="catalog.pdf">招生专业目录.pdf</a>')
        calls = Mock(side_effect=lambda url, unit: response(url, html))
        result = collect_unit(unit, {}, self.directory, project_root=self.root, fetcher=calls)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(result['unresolved_links'][0]['url'], 'https://other.edu.cn/catalog.pdf')
        bad = html.replace('<base href="https://other.edu.cn/">',
                           '<base href="javascript:alert(1)"><base href="https://other.edu.cn/">')
        fetched = []
        def fetch(url, unit):
            fetched.append(url)
            return response(url, bad) if url == article else response(url, b'%PDF-original', 'application/pdf')
        collect_unit(unit, {}, self.directory, project_root=self.root, fetcher=fetch, previous={})
        self.assertEqual(fetched, [article, 'https://grad.sample.edu.cn/info/catalog.pdf'])

    def test_growing_path_with_different_original_content_remains_a_real_page(self):
        entry = 'https://grad.sample.edu.cn/index/list.htm'
        nested = 'https://grad.sample.edu.cn/index/index/list.htm'
        unit = {**UNIT, 'source_url': entry, 'official_url': entry, 'unit_code': None}
        pages = {entry: '<a href="index/list.htm">硕士招生</a>',
                 nested: '<h1>样本大学2027年硕士招生目录</h1><a href="/catalog.pdf">招生专业目录.pdf</a>',
                 'https://grad.sample.edu.cn/catalog.pdf': b'%PDF-original'}
        result = collect_unit(unit, {}, self.directory, project_root=self.root,
            fetcher=lambda url, unit: response(url, pages[url], 'application/pdf' if isinstance(pages[url], bytes) else 'text/html'))
        self.assertEqual(len(result['pages']), 3)
        self.assertFalse(any(page.get('crawl_alias_of') for page in result['pages']))
        self.assertFalse(result['deferred_links'])


if __name__ == '__main__':
    unittest.main()
