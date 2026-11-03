import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs
from unittest.mock import MagicMock, patch

import collectors.annual_registry as annual

from collectors.annual_registry import (
    AnnualRegistryError, build_annual_registry, directory_context,
    parse_public_units, parse_province_units, verify_charter, collect_annual_registry,
)


CONTEXT_HTML = '''<title>硕士专业目录</title><script>curYear: "2027", login: false</script>
<div>登录后，可查看更多内容</div>'''
CONTEXT = {'year': 2027, 'login_limited': True}
UNIT = {'id': '123', 'name': '甲大学', 'unit_code': '10001', 'province': '北京'}


def response(rows=None, total=1):
    return {'flag': True, 'msg': {'curPage': 1, 'pageCount': 10,
        'size': len(rows or [record()]), 'totalCount': total,
        'totalPage': (total + 9) // 10, 'list': rows or [record()]}}


def record(code='10001', name='甲大学', identifier='123', province='11'):
    return {'dwdm': code, 'dwmc': name, 'schId': identifier, 'szssm': province, 'szss': '北京'}


class AnnualParserTests(unittest.TestCase):
    def test_current_year_is_taken_from_directory_context_and_never_from_copyright(self):
        self.assertEqual(directory_context(CONTEXT_HTML), CONTEXT)
        for html in [CONTEXT_HTML.replace('2027', '2026'), '<p>Copyright 2027</p>', '<p>登录后查看更多</p>']:
            with self.assertRaises(AnnualRegistryError):
                directory_context(html)

    def test_only_public_first_page_is_accepted_and_completeness_requires_all_rows(self):
        first = parse_public_units(response([record(code=str(10001+i), identifier=str(123+i)) for i in range(10)], total=27), '11', CONTEXT)
        self.assertFalse(first['complete'])
        self.assertEqual(first['reported_total'], 27)
        self.assertEqual(first['units'][0]['unit_code'], '10001')
        self.assertEqual(first['units'][0]['annual_status'], 'confirmed')
        self.assertEqual(first['units'][0]['structured_fields']['dwdm'], '10001')
        self.assertNotIn('quote', first['units'][0])
        self.assertTrue(parse_public_units(response(), '11', CONTEXT)['complete'])
        paginated = response(); paginated['msg']['curPage'] = 2
        with self.assertRaises(AnnualRegistryError):
            parse_public_units(paginated, '11', CONTEXT)

    def test_public_api_size_is_page_capacity_even_when_the_province_has_fewer_units(self):
        payload = response([record(), record('10002', identifier='124')], total=2)
        payload['msg']['size'] = 10
        parsed = parse_public_units(payload, '11', CONTEXT)
        self.assertTrue(parsed['complete'])
        self.assertEqual(parsed['observed_count'], 2)
        missing = copy.deepcopy(payload); missing['msg']['list'] = missing['msg']['list'][:1]
        with self.assertRaises(AnnualRegistryError):
            parse_public_units(missing, '11', CONTEXT)

    def test_invalid_identity_province_counts_and_error_responses_never_become_success(self):
        broken = [
            {'flag': False, 'msg': '请登录'}, response([record(province='12')]),
            response([record(code='100010001')]), response([record(identifier='')]),
            response([record(), record()], total=2),
        ]
        for payload in broken:
            with self.subTest(payload=payload), self.assertRaises(AnnualRegistryError):
                parse_public_units(payload, '11', CONTEXT)
        with self.assertRaises(AnnualRegistryError):
            parse_public_units(response(), '11', {**CONTEXT, 'year': 2026})

    def test_province_list_requires_annual_admissions_unit_title_and_code_columns(self):
        html = '''<title>浙江省2027年硕士研究生招生单位网址和咨询电话</title><table>
        <tr><td>单位代码</td><td>单位名称</td><td>官方网站</td></tr>
        <tr><td>10335</td><td>浙江大学</td><td>https://www.zju.edu.cn/</td></tr>
        <tr><td>10336</td><td>杭州电子科技大学</td><td>https://www.hdu.edu.cn/</td></tr></table>'''
        value = parse_province_units(html, '33')
        self.assertTrue(value['complete'])
        self.assertEqual([p['unit_code'] for p in value['units']], ['10335', '10336'])
        for invalid in [html.replace('2027', '2026'), html.replace('招生单位网址', '报考点网址'),
                        html.replace('单位代码', '报考点代码'), html.replace('10336', '10335'),
                        html.replace('10336', '1033'), html.replace('10336', '')]:
            with self.subTest(html=invalid), self.assertRaises(AnnualRegistryError):
                parse_province_units(invalid, '33')

    def test_charter_verification_reads_saved_hash_and_same_school_annual_title(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'data/source.html'; source.parent.mkdir()
            raw = '<h1>甲大学2027年硕士研究生招生章程</h1><p>学校代码10001</p>'.encode()
            source.write_bytes(raw)
            doc = {'id': 'doc', 'url': 'https://yz.chsi.com.cn/zsgs/zsjz/detailall.action?schCode=10001',
                   'local_path': 'data/source.html', 'sha256': hashlib.sha256(raw).hexdigest()}
            self.assertEqual(verify_charter(doc, UNIT, root)['annual_status'], 'confirmed')
            for invalid in [{**doc, 'sha256': '0'*64}, {**doc, 'url': doc['url'].replace('10001', '10002')},
                            {**doc, 'local_path': '../other.html'}]:
                self.assertIsNone(verify_charter(invalid, UNIT, root))
            source.write_text('<h1>甲大学2027年博士研究生招生简章</h1>', 'utf-8')
            updated = {**doc, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
            self.assertIsNone(verify_charter(updated, UNIT, root))
            source.write_text('<div class="article-title">甲大学2027年硕士研究生招生章程</div>', 'utf-8')
            article = {**doc, 'url': doc['url'].replace('detailall.action', 'detail.action') + '&zsgsId=123',
                       'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
            self.assertIsNotNone(verify_charter(article, UNIT, root))
            source.write_text('<a href="detail.action?schCode=10001">甲大学2027年硕士研究生招生章程</a>', 'utf-8')
            navigation = {**doc, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
            self.assertIsNone(verify_charter(navigation, UNIT, root))

    def test_merging_keeps_unknowns_and_aliases_without_claiming_a_national_total(self):
        registry = {'units': [UNIT, {'id': '456', 'name': '乙研究所', 'unit_code': '80001', 'province': '北京'}]}
        original = copy.deepcopy(registry)
        public = {**parse_public_units(response(), '11', CONTEXT), 'document': {'id': 'public'}}
        result = build_annual_registry(registry, [public], [], year=2027)
        self.assertEqual(registry, original)
        self.assertFalse(result['metadata']['complete'])
        self.assertIsNone(result['metadata']['official_total'])
        statuses = {row['unit_id']: row['annual_status'] for row in result['units']}
        self.assertEqual(statuses, {'123': 'confirmed', '456': 'unknown'})
        self.assertEqual(result['metadata']['confirmed_units'], 1)
        self.assertEqual(result['metadata']['unconfirmed_index_units'], 1)
        self.assertEqual(result['units'][0]['evidence'][0]['document_id'], 'public')

    def test_conflicting_school_codes_and_unproved_renames_are_not_silently_merged(self):
        for conflicting in [record(code='10002'), record(name='别的大学'), record(identifier='999')]:
            public = {**parse_public_units(response([conflicting]), '11', CONTEXT), 'document': {'id': 'public'}}
            result = build_annual_registry({'units': [UNIT]}, [public], [])
            self.assertEqual(result['metadata']['confirmed_units'], 0)
            self.assertTrue(result['identity_conflicts'])
        index_without_admissions_code = {**UNIT, 'unit_code': '4111000001'}
        public = {**parse_public_units(response(), '11', CONTEXT), 'document': {'id': 'public'}}
        clarified = build_annual_registry({'units': [index_without_admissions_code]}, [public], [])
        self.assertEqual(clarified['units'][0]['index_unit_code'], '4111000001')
        self.assertEqual(clarified['units'][0]['unit_code'], '10001')
        self.assertEqual(clarified['units'][0]['annual_status'], 'confirmed')

    def test_collection_saves_all_public_first_pages_and_offline_replays_exact_raw_hashes(self):
        registry = {'units': [UNIT]}
        requests = []
        def download(url, body=None):
            requests.append((url, body))
            if url.endswith('/dw.do'):
                return CONTEXT_HTML.encode(), 'text/html;charset=utf-8'
            if url.endswith('/dws.do'):
                form = parse_qs(body.decode())
                self.assertEqual(form['curPage'], ['1'])
                self.assertEqual(form['start'], ['0'])
                province = form['ssdm'][0]
                payload = response() if form.get('dwdm') == ['10001'] else response([record(code='100'+province, identifier=province, province=province, name=province+'大学')])
                return json.dumps(payload, ensure_ascii=False).encode(), 'application/json'
            return ('<title>浙江省2027年硕士研究生招生单位网址和咨询电话</title><table>'
                    '<tr><td>单位代码</td><td>单位名称</td></tr>'
                    '<tr><td>10335</td><td>浙江大学</td></tr></table>').encode(), 'text/html'
        with tempfile.TemporaryDirectory() as folder, patch('collectors.annual_registry._download', side_effect=download):
            root = Path(folder)
            value = collect_annual_registry(registry, root, delay=0)
            self.assertEqual(value['metadata']['public_query_provinces'], 31)
            self.assertEqual(value['metadata']['errors'], [])
            self.assertEqual(len(requests), 33)
            before = len(requests)
            replay = collect_annual_registry(registry, root, offline=True, delay=0)
            self.assertEqual(len(requests), before)
            self.assertEqual(replay['units'], value['units'])
            self.assertTrue(annual.verify_annual_registry(value, registry, root))
            wrong_metadata = {**value, 'metadata': {**value['metadata'], 'complete': True, 'official_total': 999}}
            self.assertFalse(annual.verify_annual_registry(wrong_metadata, registry, root))
            for doc in replay['documents']:
                self.assertEqual(hashlib.sha256((root/doc['local_path']).read_bytes()).hexdigest(), doc['sha256'])
            exact = collect_annual_registry(registry, root, exact_queries=True, delay=0)
            self.assertTrue(annual.verify_annual_registry(exact, registry, root))
            self.assertEqual(exact['unit_query_manifests'][0]['match_status'], 'found')
            changed = copy.deepcopy(exact)
            query_id = changed['unit_query_manifests'][0]['source_document_id']
            next(doc for doc in changed['documents'] if doc['id'] == query_id)['request_body'] = 'curPage=2'
            self.assertFalse(annual.verify_annual_registry(changed, registry, root))
            (root/replay['documents'][0]['local_path']).write_text('tampered', 'utf-8')
            with self.assertRaises(AnnualRegistryError):
                collect_annual_registry(registry, root, offline=True, delay=0)

    def test_partial_collection_exposes_failures_and_never_turns_them_into_empty_success(self):
        def download(url, body=None):
            if url.endswith('/dw.do'):
                return CONTEXT_HTML.encode(), 'text/html'
            raise OSError('public source temporarily unavailable')
        with tempfile.TemporaryDirectory() as folder, patch('collectors.annual_registry._download', side_effect=download):
            result = collect_annual_registry({'units': [UNIT]}, Path(folder), delay=0)
        self.assertEqual(result['metadata']['public_query_provinces'], 0)
        self.assertEqual(len(result['metadata']['errors']), 32)
        self.assertEqual(result['units'][0]['annual_status'], 'unknown')
        self.assertFalse(result['metadata']['complete'])

    def test_network_reads_are_bounded_and_redirects_cannot_escape_the_public_sources(self):
        source = MagicMock()
        source.__enter__.return_value = source
        source.geturl.return_value = annual.CONTEXT_URL
        source.headers = {'Content-Type': 'text/html'}
        source.read.return_value = b'<html>public</html>'
        opener = MagicMock(); opener.open.return_value = source
        with patch('collectors.annual_registry.build_opener', return_value=opener):
            self.assertEqual(annual._download(annual.CONTEXT_URL), (b'<html>public</html>', 'text/html'))
            source.read.assert_called_once_with(annual.MAX_BYTES + 1)
            for raw in [b'', b'x'*(annual.MAX_BYTES + 1)]:
                source.read.return_value = raw
                with self.assertRaises(AnnualRegistryError):
                    annual._download(annual.CONTEXT_URL)
            source.geturl.return_value = 'https://unrelated.example/'
            with self.assertRaises(AnnualRegistryError):
                annual._download(annual.CONTEXT_URL)
        for url in ['http://yz.chsi.com.cn/zsml/dw.do', 'https://127.0.0.1/', 'https://yz.chsi.com.cn/private']:
            with self.subTest(url=url), self.assertRaises(AnnualRegistryError):
                annual._download(url)
        with self.assertRaises(AnnualRegistryError):
            annual._Redirect().redirect_request(None, None, 302, '', {}, 'https://unrelated.example/')

    def test_exact_public_queries_record_zero_results_without_inventing_doctoral_only(self):
        payload = response(); payload['msg'].update({'totalCount': 0, 'list': [], 'totalPage': 0})
        value = annual.parse_exact_query(payload, UNIT, '11', CONTEXT)
        self.assertEqual(value['match_status'], 'not_listed')
        self.assertEqual(value['units'], [])
        self.assertEqual(value['query_kind'], 'exact_unit')
        found = annual.parse_exact_query(response(), UNIT, '11', CONTEXT)
        self.assertEqual(found['match_status'], 'found')
        ambiguous = annual.parse_exact_query(response([record(identifier='999')]), UNIT, '11', CONTEXT)
        self.assertEqual(ambiguous['match_status'], 'identity_conflict')

    def test_national_scope_opens_only_when_every_province_and_index_gap_reconciles(self):
        originals, sources = [], []
        for index, (province_code, province) in enumerate(annual.PROVINCES.items()):
            identifier, code, name = str(1000+index), str(10000+index), province+'甲大学'
            originals.append({'id': identifier, 'unit_code': code, 'name': name, 'province': province})
            row = {'unit_id': identifier, 'unit_code': code, 'unit_name': name, 'province': province,
                   'annual_status': 'confirmed', 'evidence_kind': 'current_master_catalog_entry',
                   'structured_fields': {'dwdm': code, 'dwmc': name}}
            sources.append({'province_code': province_code, 'province': province, 'reported_total': 1,
                'units': [row], 'complete': True, 'document': {'id': province_code}})
        value = build_annual_registry({'units': originals}, sources, [])
        self.assertTrue(value['metadata']['complete'])
        self.assertEqual(value['metadata']['official_total'], 31)
        self.assertFalse(build_annual_registry({'units': originals}, sources[:-1], [])['metadata']['complete'])
        with_unknown = build_annual_registry({'units': [*originals, UNIT]}, sources, [])
        self.assertFalse(with_unknown['metadata']['complete'])
        contradicted = build_annual_registry({'units': originals}, [{**sources[0], 'reported_total': 2}, *sources[1:]], [
            {**sources[0], 'document': {'id': 'contradiction'}}])
        self.assertFalse(contradicted['metadata']['complete'])

    def test_exact_zero_queries_are_retained_as_distinct_unlisted_evidence(self):
        second = {**UNIT, 'id': '456', 'unit_code': '10002', 'name': '乙大学'}
        unknown = {**UNIT, 'id': '789', 'unit_code': '10003', 'name': '丙大学', 'province': None}
        registry = {'units': [UNIT, second, unknown]}
        def download(url, body=None):
            payload = response()
            if parse_qs(body.decode())['dwdm'] == ['10002']:
                payload['msg'].update({'list': [], 'totalCount': 0, 'totalPage': 0})
            return json.dumps(payload).encode(), 'application/json'
        with tempfile.TemporaryDirectory() as folder, patch('collectors.annual_registry._download', side_effect=download):
            root = Path(folder); directory = root/'data/source'; directory.mkdir(parents=True)
            results, errors = annual._exact_queries(registry, set(), directory, root, CONTEXT,
                                                    offline=False, delay=0, progress=None)
            merged = build_annual_registry(registry, results, [])
        self.assertEqual([row['match_status'] for row in results], ['found', 'not_listed'])
        self.assertEqual(len(errors), 1)
        states = {row['unit_id']: row['annual_status'] for row in merged['units']}
        self.assertEqual(states, {'123': 'confirmed', '456': 'not_listed', '789': 'unknown'})
        zero = next(row for row in merged['units'] if row['unit_id'] == '456')
        self.assertEqual(zero['evidence'][0]['structured_fields']['totalCount'], 0)
        self.assertNotIn('doctoral_only', json.dumps(zero))

    def test_wait_responses_are_retried_normally_and_do_not_poison_the_source_cache(self):
        pending = json.dumps({'invokeStatus': 'WAIT', 'taskId': 'public-task'}).encode()
        finished = json.dumps(response()).encode()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); directory = root/'data/source'; directory.mkdir(parents=True)
            with patch('collectors.annual_registry._download', side_effect=[(pending, 'application/json'), (finished, 'application/json')]) as fetch, patch('collectors.annual_registry.time.sleep'):
                doc, raw = annual._snapshot(annual.QUERY_URL, directory, root, data=b'curPage=1')
            self.assertEqual(raw, finished)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), doc['sha256'])
            with patch('collectors.annual_registry._download') as fetch:
                reused, raw = annual._snapshot(annual.QUERY_URL, directory, root, data=b'curPage=1', offline=True)
            fetch.assert_not_called()
            self.assertTrue(reused['from_cache'])

    def test_corrupt_cache_metadata_can_refresh_online_but_cannot_pass_offline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); directory = root/'data/source'; directory.mkdir(parents=True)
            with patch('collectors.annual_registry._download', return_value=(CONTEXT_HTML.encode(), 'text/html')):
                annual._snapshot(annual.CONTEXT_URL, directory, root)
            pointer = next(directory.glob('*.metadata.json'))
            pointer.write_text('{broken', 'utf-8')
            with self.assertRaises(AnnualRegistryError):
                annual._snapshot(annual.CONTEXT_URL, directory, root, offline=True)
            with patch('collectors.annual_registry._download', return_value=(CONTEXT_HTML.encode(), 'text/html')) as fetch:
                doc, raw = annual._snapshot(annual.CONTEXT_URL, directory, root)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(json.loads(pointer.read_text('utf-8'))['sha256'], hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    unittest.main()
