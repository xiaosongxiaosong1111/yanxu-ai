import tempfile
import unittest

import official_search
from official_authority import OfficialAuthority, _scope
from public_sources import official_source_url
import unit_bindings
from unit_bindings import enrich


class OfficialSearchSiteChoiceTests(unittest.TestCase):
    def test_preserves_the_registered_exact_admissions_subdomain(self):
        self.assertEqual(official_search.preferred_site(
            {'name': '北方工业大学', 'official_url': 'https://yjsy.ncut.edu.cn/'}), 'yjsy.ncut.edu.cn')
        self.assertEqual(official_search.preferred_site(
            {'name': '样本大学', 'official_url': 'https://www.example.edu.cn/'}), 'example.edu.cn')

    def test_curated_admissions_hosts_are_available_in_page_and_scoped_research(self):
        rows = [
            ('368512', '10674', '昆明理工大学', 'https://www.swugs.ynu.edu.cn/',
             'https://yjs.kust.edu.cn/info/1254/4413.htm', 'yjs.kust.edu.cn'),
            ('367996', '10109', '太原科技大学', 'http://yjs.tyust.edu.cn/',
             'https://yjsxy.tyust.edu.cn/info/1203/3712.htm', 'yjsxy.tyust.edu.cn'),
        ]
        with tempfile.TemporaryDirectory() as root:
            authority = OfficialAuthority(root)
            for unit_id, code, name, registered, catalog, host in rows:
                with self.subTest(unit=name):
                    unit = enrich({'id': unit_id, 'unit_code': code, 'name': name,
                                   'official_url': registered})
                    self.assertEqual(unit['official_url'], registered)
                    for scoped in (unit, authority.enrich(unit)):
                        self.assertEqual(official_search.preferred_site(scoped), host)
                        self.assertTrue(official_source_url(catalog, scoped))
                        self.assertTrue(official_source_url(registered, scoped))
                        self.assertFalse(official_source_url('https://unrelated.edu.cn/', scoped))

    def test_only_active_same_unit_admissions_scope_can_supply_a_discovered_host(self):
        unit = {'id': '1', 'name': '样本大学', 'official_url': 'https://example.edu.cn/'}
        registered = {'unit_id': '1', 'state': 'verified', 'replay_verified': True,
                      'publisher_role': 'registered', 'scope': _scope(unit['official_url'])}
        url = 'https://grad.other.ac.cn/catalog/2027.htm'
        admission = {**registered, 'publisher_role': 'admissions', 'candidate_url': url,
                     'scope': _scope(url)}
        for changes in ({'state': 'revoked'}, {'unit_id': '2'}, {'replay_verified': False},
                        {'expires_at': '2000-01-01T00:00:00+00:00'},
                        {'candidate_url': 'https://grad.other.ac.cn/different.htm'}):
            with self.subTest(changes=changes):
                enriched = {**unit, 'official_authorities': [registered, {**admission, **changes}]}
                self.assertEqual(official_search.preferred_site(enriched), 'example.edu.cn')
        self.assertEqual(official_search.preferred_site(
            {**unit, 'official_authorities': [registered, admission]}), 'grad.other.ac.cn')

    def test_rejects_unscoped_or_shared_hosts_as_site_targets(self):
        for unit in (None, {}, {'official_url': 'https://yz.chsi.com.cn/'},
                     {'official_url': 'https://mp.weixin.qq.com/'},
                     {'official_url': 'https://yjs.example.edu.cn/', 'official_authorities': []}):
            with self.subTest(unit=unit):
                self.assertEqual(official_search.preferred_site(unit), '')


    def test_kust_consortium_entrance_is_exact_and_never_the_normal_search_site(self):
        special = 'https://www.swugs.ynu.edu.cn/'
        unit = {'id': '368512', 'unit_code': '10674', 'name': '昆明理工大学', 'official_url': special}
        general = 'https://yjs.kust.edu.cn/info/1254/4413.htm'
        unknown_yunnan = 'https://www.ynu.edu.cn/catalog.htm'
        enriched = enrich(unit)
        self.assertIs(unit_bindings.consortium_admissions_scope(special, unit), True)
        self.assertIs(unit_bindings.consortium_admissions_scope(unknown_yunnan, unit), False)
        self.assertIsNone(unit_bindings.consortium_admissions_scope(general, unit))
        self.assertTrue(official_source_url(special, enriched))
        self.assertFalse(official_source_url(unknown_yunnan, enriched))
        self.assertFalse(official_source_url('https://unknown.swugs.ynu.edu.cn/', enriched))
        self.assertTrue(official_source_url(general, enriched))
        self.assertEqual(official_search.preferred_site(unit), '')

        yunnan = {'id': '368510', 'unit_code': '10673', 'name': '云南大学',
                  'official_url': 'https://www.ynu.edu.cn/'}
        self.assertIsNone(unit_bindings.consortium_admissions_scope(unknown_yunnan, yunnan))
        self.assertTrue(official_source_url(unknown_yunnan, yunnan))
