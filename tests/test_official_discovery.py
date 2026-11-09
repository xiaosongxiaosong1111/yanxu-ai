import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from grad_engine import validate_profile
import official_search
import unit_bindings
from public_sources import official_roots, official_source_url
from research_store import ResearchError
from research_tools import LIMITS, ResearchTools
from tests.test_research_service import FakeAdmission, FakeSettings


def _pages(*rows):
    """Normalized response body, as every caller sees it after checked_response."""
    return {'Pages': list(rows), 'RequestId': 'fixture-request'}


def _raw(*rows):
    """Raw SearchPro envelope, as the transport receives it."""
    return {'Response': {'Pages': list(rows), 'RequestId': 'fixture-request'}}


def _page(title, url, **extra):
    return json.dumps({'title': title, 'url': url, **extra}, ensure_ascii=False)


class DiscoveryAdmission(FakeAdmission):
    """Catalog fixture whose units exercise binding, alias and no-binding cases."""

    def __init__(self):
        super().__init__()
        self.units = [
            {'id': 'ucas', 'name': '中国科学院大学', 'province': '北京', 'official_url': ''},
            {'id': 'ucass', 'name': '中国社会科学院大学', 'province': '北京', 'official_url': ''},
            {'id': 'plain', 'name': '无绑定大学', 'province': '天津', 'official_url': ''},
        ]
        self.rows = [{**self.rows[0], 'unit_id': 'ucas', 'unit_name': '中国科学院大学'}]


class LadderTests(unittest.TestCase):
    def test_site_filter_falls_back_once_and_counts_real_requests(self):
        bodies, calls = [], []

        def call(body):
            bodies.append(body)
            calls.append(body)
            if len(calls) == 1:
                return _pages(_page('转载', 'https://unrelated.example/info'))
            return _pages(_page('招生目录', 'https://admission.ucas.ac.cn/zsml'))

        result = official_search.run_ladder(
            call, queries=['2027年 中国科学院大学 目录', '中国科学院大学 研究生院 2027 目录'],
            is_official=lambda url: 'ucas.ac.cn' in url,
            site='ucas.edu.cn', roots=('ucas.edu.cn', 'ucas.ac.cn'), candidate_enabled=False)
        self.assertEqual(len(bodies), 2)
        self.assertIn('Site', bodies[0])
        self.assertNotIn('Site', bodies[1])
        self.assertTrue(result['site_fallback'])
        self.assertEqual(result['request_count'], 2)
        self.assertEqual([row['url'] for row in result['official_results']],
                         ['https://admission.ucas.ac.cn/zsml'])

    def test_empty_pages_stops_after_the_fallback_without_a_third_variant(self):
        calls = []

        def call(body):
            calls.append(body)
            return {'RequestId': 'empty'}

        result = official_search.run_ladder(call, queries=['q'], is_official=lambda url: True,
                                            site='a.edu.cn', roots=('a.edu.cn',))
        self.assertEqual(len(calls), 2)
        self.assertFalse(result['site_fallback'])
        self.assertEqual(result['queries_tried'], ['q'])
        self.assertEqual(result['official_results'], [])

    def test_third_party_query_respects_the_caller_budget_and_marks_it(self):
        calls = []

        def call(body):
            calls.append(body)
            return _pages(_page('经验', 'https://www.offcn.com/a'))

        result = official_search.run_ladder(
            call, queries=['q'], is_official=lambda url: False, include_third=True,
            third_query='q 经验', can_call=lambda: len(calls) < 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(result['budget_exhausted'])
        self.assertEqual(result['third_results'], [])

    def test_third_party_rows_stay_leads_with_an_explicit_tier(self):
        result = official_search.run_ladder(
            lambda body: _pages(_page('经验', 'https://www.offcn.com/a')),
            queries=['q'], is_official=lambda url: False, include_third=True, third_query='q 经验')
        self.assertEqual(result['third_results'][0]['source_tier'], 'third_party_reference')
        self.assertEqual(result['third_results'][0]['source_status'], 'third_party_unverified_reference')
        self.assertEqual(official_search.failure_stage([], [], result['third_results'], 0, False),
                         'third_party_only')


class BindingTests(unittest.TestCase):
    def test_reviewed_binding_covers_both_registered_domains(self):
        unit = unit_bindings.enrich({'id': 'ucas', 'name': '中国科学院大学', 'official_url': ''})
        self.assertEqual(official_roots(unit), ('ucas.edu.cn', 'ucas.ac.cn'))
        self.assertTrue(official_source_url('https://admission.ucas.ac.cn/info/1', unit))
        self.assertTrue(official_source_url('https://www.ucas.edu.cn/', unit))
        self.assertFalse(official_source_url('https://ucas.edu.cn.evil.example/info', unit))
        self.assertEqual(unit['official_url'], 'https://www.ucas.edu.cn/')
        self.assertEqual(unit['aliases'], ['国科大'])

    def test_second_school_binds_its_admissions_subdomain(self):
        unit = unit_bindings.enrich({'id': 'ucass', 'name': '中国社会科学院大学', 'official_url': ''})
        self.assertEqual(official_roots(unit), ('ucass.edu.cn',))
        self.assertTrue(official_source_url('https://skdzs.ucass.edu.cn/sszs1/zsxx.htm', unit))
        self.assertEqual(unit_bindings.resolve_query('社科大')['canonical'], '中国社会科学院大学')
        self.assertEqual(unit_bindings.expand_query('社科大 计算机'), '中国社会科学院大学 计算机')
        self.assertEqual(unit_bindings.expand_query('国科大'), '中国科学院大学')
        self.assertEqual(unit_bindings.expand_query('社科大x 计算机'), '社科大x 计算机')
        self.assertEqual(unit_bindings.expand_query(''), '')
        self.assertEqual(unit_bindings.expand_query(None), None)

    def test_external_overlay_rejects_rows_without_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'official_bindings.json'
            path.write_text(json.dumps({'units': [
                {'name': '测试第二大学', 'aliases': ['测试二大'], 'domains': [
                    {'url': 'https://zs.test.edu.cn/', 'evidence': 'https://www.test.edu.cn/'},
                    {'url': 'https://no-evidence.test.edu.cn/'},
                    {'url': 'https://yz.chsi.com.cn/x', 'evidence': 'https://www.test.edu.cn/'},
                ]},
                {'name': '缺证据大学', 'domains': [{'url': 'https://x.test.ac.cn/'}]},
            ]}, ensure_ascii=False), encoding='utf-8')
            names = [row['name'] for row in unit_bindings.rows(path)]
            self.assertIn('测试第二大学', names)
            self.assertNotIn('缺证据大学', names)
            unit = unit_bindings.enrich({'id': 'x', 'name': '测试第二大学', 'official_url': ''}, path)
            self.assertEqual(len(unit['official_urls']), 1)
            self.assertEqual(unit['official_urls'][0]['url'], 'https://zs.test.edu.cn/')
            self.assertEqual(unit_bindings.resolve_query('测试二大', path)['canonical'], '测试第二大学')


class AgentSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.data.mkdir()
        self.settings = FakeSettings()
        self.settings.yuanbao_key = lambda: 'fixture-service-key'
        self.task = {'query': 'fixture', 'follow_ups': [], 'profile': validate_profile({}),
                     'unit_ids': [], 'reference_year': 2027, 'target_year': 2028}
        self.network = Mock()
        raw = b'fixture original bytes'
        self.fetcher = Mock(return_value={
            'url': 'https://skdzs.ucass.edu.cn/sszs1/zsxx.htm', 'raw': raw,
            'sha256': hashlib.sha256(raw).hexdigest(), 'document_title': '2027年招生信息',
            'document_heading': '2027年招生信息', 'text': '招生信息原文',
            'pages': [{'page': 1, 'text': '招生信息原文'}],
            'attribution_status': 'verified', 'checked_at': '2026-11-05',
            'links': [
                {'url': 'https://skdzs.ucass.edu.cn/sszs1/zsxx.htm#top', 'title': '招生信息'},
                {'url': 'https://skdzs.ucass.edu.cn/info/1166/2740.htm', 'title': '2027年硕士研究生招生简章'},
                {'url': 'https://skdzs.ucass.edu.cn/files/catalog.pdf', 'title': '附件：专业目录'},
                {'url': 'https://other.example.com/info', 'title': '外站转载'},
                {'url': 'https://yz.chsi.com.cn/zsml/index.htm', 'title': '研招网'},
                {'url': 'https://user:pass@skdzs.ucass.edu.cn/private', 'title': '带凭据'},
                {'url': 'javascript:alert(1)', 'title': '脚本'},
            ]})
        self.tools = ResearchTools(DiscoveryAdmission(), self.settings, self.task, self.data,
                                   network=self.network, source_fetcher=self.fetcher)

    def request_bodies(self):
        return [call.args[3] for call in self.network.request_json.call_args_list]

    def test_short_name_finds_the_unit_and_hands_back_a_readable_entry(self):
        result = self.tools.run('find_units', {'query': '国科大'})
        self.assertEqual(result['total'], 1)
        row = result['units'][0]
        self.assertEqual(row['name'], '中国科学院大学')
        self.assertEqual(row['matched_by'], ['alias'])
        self.assertEqual(row['binding_status'], 'bound')
        self.assertIn('official_source_id', row)
        self.assertEqual(len(row['official_binding']['domains']), 2)
        self.assertTrue(all(item['evidence'] for item in row['official_binding']['domains']))
        self.assertEqual(row['official_domains'], ['https://www.ucas.edu.cn/', 'https://admission.ucas.ac.cn/'])
        self.assertEqual(result['alias_resolution'], {'alias': '国科大', 'canonical': '中国科学院大学'})
        raw = b'fixture original bytes'
        self.fetcher.return_value = {**self.fetcher.return_value, 'url': 'https://www.ucas.edu.cn/',
                                     'raw': raw, 'sha256': hashlib.sha256(raw).hexdigest()}
        read = self.tools.run('read_official_source', {'source_id': row['official_source_id'],
                                                       'unit_id': 'ucas'})
        self.assertEqual(read['status'], 'official_original_read')
        self.assertEqual(read['unit_id'], 'ucas')

    def test_unresolved_short_name_reports_the_missing_index_entry(self):
        result = self.tools.run('find_units', {'query': '不存在的简称'})
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['reason'], 'index_no_match')
        self.assertIn('覆盖不完整', result['notice'])

    def test_search_accepts_the_cross_domain_admissions_site(self):
        self.network.request_json.return_value = _raw(
            _page('招生目录', 'https://admission.ucas.ac.cn/zsml'))
        result = self.tools.search_sources({'query': '硕士招生专业目录', 'unit_id': 'ucas'})
        self.assertEqual(len(self.request_bodies()), 1)
        self.assertEqual(self.request_bodies()[0]['Site'], 'ucas.edu.cn')
        self.assertEqual(result['request_count'], 1)
        self.assertEqual(result['binding_status'], 'bound')
        self.assertEqual(result['failure_stage'], 'official_found')
        self.assertEqual(result['outcome'], 'official_results')
        self.assertEqual(self.tools.usage['searches'], 1)

    def test_agent_and_page_share_the_same_site_fallback_budget(self):
        responses = [
            _raw(_page('转载', 'https://unrelated.example/info')),
            _raw(_page('招生目录', 'https://admission.ucas.ac.cn/zsml')),
        ]
        self.network.request_json.side_effect = lambda *args, **kwargs: responses.pop(0)
        result = self.tools.search_sources({'query': '硕士招生专业目录', 'unit_id': 'ucas'})
        self.assertEqual(len(self.request_bodies()), 2)
        self.assertIn('Site', self.request_bodies()[0])
        self.assertNotIn('Site', self.request_bodies()[1])
        self.assertTrue(result['site_fallback'])
        self.assertEqual(result['request_count'], 2)
        self.assertEqual(self.tools.usage['searches'], 2)
        self.assertEqual(result['results'][0]['url'], 'https://admission.ucas.ac.cn/zsml')

    def test_missing_binding_is_reported_not_as_an_empty_official_site(self):
        responses = [
            _raw(_page('无绑定大学2027年硕士研究生招生简章', 'https://zs.plain.example.cn/a'),
                 _page('无绑定大学2027年硕士研究生招生专业目录', 'https://zs.plain.example.cn/b')),
            _raw(_page('考研经验', 'https://www.offcn.com/a')),
        ]
        self.network.request_json.side_effect = lambda *args, **kwargs: responses.pop(0)
        result = self.tools.search_sources({'query': '硕士招生简章', 'unit_id': 'plain'})
        # An unbound unit has no site root to filter on: primary and one wider
        # rewrite. Third-party rows already present are labelled at no extra
        # request; no dedicated lead query is paid for.
        self.assertEqual(len(self.request_bodies()), 2)
        self.assertEqual(result['binding_status'], 'missing')
        self.assertEqual(result['failure_stage'], 'binding_missing')
        self.assertEqual(result['outcome'], 'official_domain_candidates')
        self.assertEqual(result['candidate_hosts'], ['zs.plain.example.cn'])
        self.assertEqual(result['third_party_count'], 1)
        self.assertEqual(result['request_count'], 2)
        self.assertEqual(self.tools.usage['searches'], 2)
        self.assertIn('尚未登记可信官网', result['notice'])
        self.assertIn('不代表官网没有资料', result['notice'])
        self.assertIn('第三方', result['notice'])
        self.assertFalse(any(row['source_tier'] == 'official' for row in result['results']))
        self.assertTrue(any(row['source_tier'] == 'third_party_reference' for row in result['results']))
        candidate = next(row for row in result['results'] if row['source_tier'] == 'official_domain_candidate')
        with self.assertRaises(ResearchError):
            self.tools.read_source({'source_id': candidate['id'], 'unit_id': 'plain'})

    def test_bound_index_no_hit_is_not_described_as_an_empty_site(self):
        self.network.request_json.return_value = {'RequestId': 'empty', 'Query': 'q'}
        result = self.tools.search_sources({'query': '目录', 'unit_id': 'ucas'})
        self.assertEqual(result['failure_stage'], 'index_no_hit')
        self.assertEqual(result['request_count'], 3)
        self.assertIn('不代表资料不存在', result['notice'])
        self.assertFalse(result['budget_exhausted'])

    def test_search_budget_stops_before_the_next_paid_request(self):
        self.tools.usage['searches'] = LIMITS['max_searches'] - 1
        self.network.request_json.return_value = {'RequestId': 'empty', 'Query': 'q'}
        result = self.tools.search_sources({'query': '目录', 'unit_id': 'ucas'})
        self.assertEqual(result['request_count'], 1)
        self.assertTrue(result['budget_exhausted'])
        self.assertEqual(self.tools.usage['searches'], LIMITS['max_searches'])


class AgentReadAndDiscoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.data.mkdir()
        self.settings = FakeSettings()
        self.task = {'query': 'fixture', 'follow_ups': [], 'profile': validate_profile({}),
                     'unit_ids': [], 'reference_year': 2027, 'target_year': 2028}
        self.network = Mock()
        self.html = (
            '<html><body><nav><a href="/zs/">招生信息</a><a href="https://yz.chsi.com.cn/">研招网</a>'
            '<a href="https://login.example.edu.cn/login">登录</a></nav>'
            '<a href="/info/1166/2740.htm">2027年硕士研究生招生简章</a>'
            '<a href="/files/2027-catalog.pdf">附件：专业目录</a>'
            '<a href="https://other.example.com/x">外站</a>'
            '<a href="javascript:void(0)">脚本</a></body></html>')
        self.links = [
            {'url': 'https://skdzs.ucass.edu.cn/sszs1/zsxx.htm#top', 'title': '招生信息'},
            {'url': 'https://skdzs.ucass.edu.cn/info/1166/2740.htm', 'title': '2027年硕士研究生招生简章'},
            {'url': 'https://skdzs.ucass.edu.cn/files/catalog.pdf', 'title': '附件：专业目录'},
            {'url': 'https://other.example.com/info', 'title': '外站转载'},
            {'url': 'https://yz.chsi.com.cn/zsml/index.htm', 'title': '研招网'},
            {'url': 'https://user:pass@skdzs.ucass.edu.cn/private', 'title': '带凭据'},
            {'url': 'javascript:alert(1)', 'title': '脚本'},
        ]
        self.fetcher = Mock(side_effect=self.fetch)
        self.tools = ResearchTools(DiscoveryAdmission(), self.settings, self.task, self.data,
                                   network=self.network, source_fetcher=self.fetcher)

    def fetch(self, url, unit, **kwargs):
        value = self.html if '1166/2740' in url else f'<html><body>{url} 官方原文</body></html>'
        raw = value.encode()
        return {'url': url, 'raw': raw, 'sha256': hashlib.sha256(raw).hexdigest(),
                'document_title': '2027年招生信息', 'document_heading': '2027年招生信息',
                'text': value, 'pages': [{'page': 1, 'text': value}],
                'attribution_status': 'verified', 'checked_at': '2026-11-05',
                'links': self.links}

    def entry(self):
        row = self.tools.find_units({'query': '社科大'})['units'][0]
        return row['official_source_id']

    def test_read_source_registers_only_same_site_links(self):
        result = self.tools.run('read_official_source', {'source_id': self.entry(), 'unit_id': 'ucass'})
        urls = [row['url'] for row in result['links']]
        self.assertIn('https://skdzs.ucass.edu.cn/info/1166/2740.htm', urls)
        self.assertIn('https://skdzs.ucass.edu.cn/files/catalog.pdf', urls)
        self.assertNotIn('https://other.example.com/info', urls)
        self.assertNotIn('https://yz.chsi.com.cn/zsml/index.htm', urls)
        self.assertFalse(any('user:pass' in url or url.startswith('javascript:') for url in urls))
        self.assertEqual(len(urls), len(set(urls)))
        self.assertTrue(all(row.get('source_id') for row in result['links']))
        self.assertEqual(len(result['links']), 3)

    def test_discovery_is_bounded_ranked_and_attributed_per_hop(self):
        entry = self.entry()
        self.tools.run('read_official_source', {'source_id': entry, 'unit_id': 'ucass'})
        result = self.tools.run('discover_official_links', {'source_id': entry, 'unit_id': 'ucass',
                                                            'depth': 1, 'limit': 2})
        self.assertEqual(result['request_count'], 0)
        self.assertEqual(len(result['links']), 2)
        self.assertEqual(result['links'][0]['kind'], 'pdf')
        self.assertEqual(result['links'][0]['url'], 'https://skdzs.ucass.edu.cn/files/catalog.pdf')
        self.assertEqual(result['deeper_links'], [])
        self.assertEqual(self.fetcher.call_count, 1)

    def test_depth_two_reading_uses_navigation_and_document_budgets(self):
        entry = self.entry()
        self.tools.run('read_official_source', {'source_id': entry, 'unit_id': 'ucass'})
        result = self.tools.run('discover_official_links', {'source_id': entry, 'unit_id': 'ucass',
                                                            'depth': 2, 'limit': 2})
        self.assertGreaterEqual(result['request_count'], 1)
        self.assertEqual(self.tools.usage['navigation_fetches'], result['request_count'])
        self.assertLessEqual(self.tools.usage['navigation_fetches'], LIMITS['max_navigation_fetches'])
        self.assertTrue(all(item.get('parent_url') for item in result['deeper_links']))
        self.assertTrue(all('skdzs.ucass.edu.cn' in item['url'] for item in result['deeper_links']))

    def test_discovery_budget_and_depth_are_enforced(self):
        entry = self.entry()
        with self.assertRaises(ResearchError):
            self.tools.discover_links({'source_id': entry, 'unit_id': 'ucass', 'depth': 3})
        with self.assertRaises(ResearchError):
            self.tools.discover_links({'source_id': entry, 'unit_id': 'ucass', 'limit': LIMITS['max_links'] + 1})
        for _ in range(LIMITS['max_discoveries']):
            self.tools.discover_links({'source_id': entry, 'unit_id': 'ucass'})
        with self.assertRaises(ResearchError) as caught:
            self.tools.discover_links({'source_id': entry, 'unit_id': 'ucass'})
        self.assertEqual(caught.exception.code, 'research_budget_exceeded')

    def test_discovery_refreshes_an_old_read_that_saved_no_links(self):
        entry = self.entry()
        self.tools.sources[entry] = {**self.tools.sources[entry], 'status': 'official_original_read',
                                     'excerpt': '旧留档没有入口'}
        result = self.tools.discover_links({'source_id': entry, 'unit_id': 'ucass'})
        self.assertEqual(self.fetcher.call_count, 1)
        self.assertEqual(result['request_count'], 1)
        self.assertEqual(len(result['links']), 3)

    def test_link_from_another_unit_is_not_readable(self):
        entry = self.entry()
        self.tools.run('read_official_source', {'source_id': entry, 'unit_id': 'ucass'})
        links = self.tools.sources[entry]['links']
        child = links[0]['source_id']
        with self.assertRaises(ResearchError) as caught:
            self.tools.read_source({'source_id': child, 'unit_id': 'ucas'})
        self.assertEqual(caught.exception.code, 'wrong_unit')


if __name__ == '__main__':
    unittest.main()
