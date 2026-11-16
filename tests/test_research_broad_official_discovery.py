"""Regional searches bind official leads to school identities before reading."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from grad_engine import validate_profile
from provider_catalog import TransportError
from research_tools import ResearchTools
from research_store import ResearchError
from tests.test_research_service import FakeAdmission, FakeSettings


def school(identifier, name, url, province='天津'):
    return {'id': identifier, 'name': name, 'official_url': url, 'province': province}


def result(url, title='2027年硕士招生专业目录'):
    return {'url': url, 'title': title, 'content': '搜索摘要不能证明具体科目。'}


class BroadOfficialDiscoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='regional-official-leads-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.admission = FakeAdmission()
        self.settings = FakeSettings()
        self.settings.yuanbao_key = lambda: 'fixture-search-key'
        self.network = Mock()

    def search(self, rows, *, unit_ids=(), provinces=(), args=None):
        self.network.request_json.return_value = {'Response': {'Pages': rows, 'RequestId': 'rp-fixture'}}
        self.tools = ResearchTools(self.admission, self.settings, {
            'id': 'r_' + 'a' * 32, 'query': '京津冀 085404 初试科目',
            'profile': validate_profile({'provinces': list(provinces)}),
            'unit_ids': list(unit_ids), 'target_year': 2028, 'reference_year': 2027,
        }, self.directory, network=self.network)
        return self.tools.run('search_official_sources', args or {'query': '085404 硕士招生专业目录'})

    def test_nameless_search_uses_published_compatible_program_as_a_directed_search_seed(self):
        self.admission.units = [school('123','甲大学','https://grad.example.edu.cn/','北京'),
                                school('456','乙大学','https://admissions.example.ac.cn/','北京')]
        def program(uid, fourth, omit=()):
            return {'id':uid, 'unit_id':uid, 'code':'085404', 'year':2027,
                    'subjects':[{'code':code,'name':name,'exam_unit':slot,'scope':'national'}
                                for slot,(code,name) in enumerate([('101','政治'),('204','英语二'),
                                  ('302','数学二'),(fourth,'专业基础')],1) if slot not in omit]}
        self.admission.rows = [program('123','853',omit=(3,)),program('456','853')]
        target = 'https://admissions.example.ac.cn/catalog'
        def search(config, method, path, body, **kwargs):
            rows = ([result(target)] if body.get('Site') == 'admissions.example.ac.cn' else
                    [result('https://admissions.example.ac.cn/law', '乙大学法学院2027年硕士招生专业目录')])
            return {'Response':{'Pages':rows,'RequestId':'fixture-real-shape'}}
        self.network.request_json.side_effect = search
        value = self.search([], provinces=('北京',), args={'query':'北京 085404 计算机技术 不考408、201'})
        payloads = [call.args[3] for call in self.network.request_json.call_args_list]
        self.assertEqual([p.get('Site') for p in payloads], [None,None,'admissions.example.ac.cn'])
        self.assertTrue(all('不考408' not in p['Query'] for p in payloads))
        self.assertEqual(value['request_count'],3)
        self.assertEqual(value['results'][0]['unit_id'],'456')
        self.assertEqual(value['results'][0]['search_channel'],'published_index_seed')
        self.assertEqual(value['results'][0]['status'],'search_lead')
        self.assertEqual(self.tools.programs,{})

    def test_broad_catalog_navigation_prioritizes_relevant_official_college(self):
        self.admission.units = [school('123','甲大学','https://grad.example.edu.cn/'),
                                school('456','乙大学','https://admissions.example.ac.cn/')]
        charter = result('https://grad.example.edu.cn/catalog',
                         '环境科学与工程学院2027年硕士研究生招生简章')
        other = result('https://admissions.example.ac.cn/catalog')
        self.search([other,charter], provinces=('天津',),
                    args={'query':'天津 085701 环境工程 初试 817'})
        first = next(row for row in self.tools.plan()['next_actions']
                     if row['tool']=='discover_official_entry')
        self.assertEqual(first['args']['unit_id'],'123')

    def test_school_name_in_provider_title_can_route_but_cannot_supply_subjects(self):
        self.admission.units = [school('123','甲大学','https://grad.example.edu.cn/')]
        third = result('https://www.offcn.com/kaoyan/catalog.html','甲大学考研初试考什么')
        outcome = self.search([third], provinces=('天津',),
                              args={'query':'天津 105400 护理 初试 308'})
        self.assertEqual(outcome['failure_stage'],'third_party_only')
        self.assertFalse(outcome.get('verified_catalog_evidence'))
        first = next(row for row in self.tools.plan()['next_actions']
                     if row['tool']=='discover_official_entry')
        self.assertEqual(first['args']['unit_id'],'123')
        self.assertFalse(self.tools.programs)

    def test_missing_provider_catalog_uses_only_scoped_current_official_index(self):
        self.admission.units = [school('123','甲大学','https://grad.example.edu.cn/'),
                                school('456','乙大学','https://admissions.example.ac.cn/','北京')]
        source = 'https://grad.example.edu.cn/approved-catalog'
        def row(url, unit_id='123', year=2027):
            return {'unit_id':unit_id,'year':year,'url':url,'publisher_url':url,
                    'title':f'{year}年硕士研究生招生专业目录'}
        entries = (row(source), row('https://grad.example.edu.cn/old',year=2026),
                   row('https://shadow.grad.example.edu.cn/forged'),
                   row('https://admissions.example.ac.cn/other','456'))
        with patch('catalog_entry_index.reviewed_entries',return_value=entries):
            result = self.search([], provinces=('天津',), args={'query':'天津 105400 护理 初试 308'})
            self.assertEqual(result['results'], [])
            self.assertEqual(result['target_status'],'not_found')
            leads = result['catalog_index_leads']
            self.assertEqual([(lead['unit_id'],lead['url'],lead['year']) for lead in leads],
                             [('123',source,None)])
            self.assertFalse(leads[0].get('original_available'))
            action = next(item for item in self.tools.plan()['next_actions']
                          if item['tool']=='discover_official_entry')
            self.assertEqual(action['args']['unit_id'],'123')
            self.assertFalse(self.tools.programs)
            self.tools.navigation_fetcher = Mock(side_effect=ResearchError('test_stop','no body'))
            review = self.tools.run(action['tool'],action['args'])
            self.assertEqual(self.tools.navigation_fetcher.call_args_list[0].args[0],source)
            self.assertEqual(review['usage']['paid_searches'],0)

    def test_unregistered_college_result_is_an_ownership_candidate_only(self):
        self.admission.units = [school('123','甲大学','https://grad.example.edu.cn/')]
        sibling = 'https://physics.example.edu.cn/2027/item'
        fake = 'https://physics.example.edu.cn.unrelated.org/2027/item'
        title = '甲大学物理学院2027年硕士招生专业目录'
        self.search([result(sibling,title),result(fake,title)], provinces=('天津',),
                    args={'query':'天津 080900 电子科学与技术 初试 807'})
        routes = [row for row in self.tools.sources.values()
                  if row.get('source_type')=='yuanbao_catalog_owner_candidate']
        self.assertEqual([(row['url'],row.get('year')) for row in routes],[(sibling,None)])
        self.assertFalse(self.tools.programs)
        self.assertTrue(any(row['tool']=='discover_official_entry' for row in self.tools.plan()['next_actions']))

    def test_repeated_first_official_catalog_keeps_priority_over_regional_index(self):
        self.admission.units = [school('123','甲大学','https://grad.example.edu.cn/'),
                                school('456','乙大学','https://admissions.example.ac.cn/')]
        catalog = 'https://grad.example.edu.cn/current'
        other = 'https://admissions.example.ac.cn/current'
        index = {'unit_id':'456','year':2027,'url':other,'publisher_url':other,
                 'title':'乙大学2027年硕士招生专业目录'}
        with patch('catalog_entry_index.reviewed_entries',return_value=(index,)):
            self.search([result(catalog,'甲大学工学院2027年硕士招生专业目录')],
                        provinces=('天津',),args={'query':'天津 085408 光电信息工程 初试 881'})
            first = next(row for row in self.tools.plan()['next_actions']
                         if row['tool']=='discover_official_entry')
            self.assertEqual(first['args']['unit_id'],'123')

    def test_school_hint_prefers_current_catalog_to_subject_change_headline(self):
        self.admission.units = [school('123','甲大学','https://grad.example.edu.cn/'),
                                school('456','乙大学','https://admissions.example.ac.cn/')]
        update = result('https://www.offcn.com/kaoyan/change.html',
                        '乙大学2027年硕士招生考试部分初试科目807调整公告')
        directory = result('https://www.offcn.com/kaoyan/catalog.html',
                           '甲大学2027年硕士研究生招生目录')
        self.search([update,directory],provinces=('天津',),
                    args={'query':'天津 080900 电子科学与技术 初试 807 电路原理'})
        queries = [call.args[3] for call in self.network.request_json.call_args_list]
        self.assertEqual(queries[-1].get('Site'),'grad.example.edu.cn')

    def test_exact_syllabus_hint_can_lead_to_formal_directory_on_other_site(self):
        self.admission.units = [school('123','甲大学','https://grad.example.edu.cn/'),
                                school('456','乙大学','https://admissions.example.ac.cn/')]
        other = result('https://www.offcn.com/kaoyan/catalog.html',
                       '乙大学2027年硕士研究生招生目录')
        syllabus = result('https://www.offcn.com/kaoyan/syllabus.html',
                          '甲大学2027年硕士研究生考试大纲 816 地球物理学基础')
        self.search([other,syllabus],provinces=('天津',),args={'query':'天津 070800 地球物理学 初试 816'})
        queries = [call.args[3] for call in self.network.request_json.call_args_list]
        self.assertEqual(queries[-1].get('Site'),'grad.example.edu.cn')

    def test_broad_rewrite_error_preserves_first_request_lead(self):
        url = 'https://grad.example.edu.cn/initial-catalog'
        self.network.request_json.side_effect = [
            {'Response':{'Pages':[result(url)],'RequestId':'first-request'}},
            TransportError('provider_rate_limited','fixture throttled',429),
        ]
        value = self.search([], args={'query':'085404 计算机技术 不考408'})
        self.assertEqual([row['url'] for row in value['results']], [url])
        self.assertEqual(value['request_count'],2)
        self.assertEqual(value['partial_search_errors'][0]['code'],'search_rate_limited')

    def test_verified_original_recovers_missed_program_without_counting_as_yuanbao_hit(self):
        subjects = [{'code':code,'name':name,'exam_unit':slot,'scope':'national'}
                    for slot,(code,name) in enumerate([('101','政治'),('204','英语二'),
                        ('302','数学二'),('408','计算机基础')],1)]
        program = {'id':'p-123','unit_id':'123','code':'085404','year':2027,'name':'计算机技术',
                   'direction':'','study_mode':'全日制','subjects':subjects,'unit_name':'测试大学'}
        ref = {'url':'https://grad.example.edu.cn/catalog.pdf','verification':'exact_quote','relation':'supports',
               'catalog_active':True,'version_state':'current','unit_id':'123','year':2027,
               'sha256':'a'*64,'quote':'085404 计算机技术 101 204 302 408'}

        def proof(*, source=ref, revision='fixture-r1', subject_rows=subjects, status='verified'):
            return {'revision':revision,'program':program,'claims':[
                {'id':'catalog','status':'verified','value':program,'evidence':[ref]},
                {'id':'subjects','status':status,'value':subject_rows,'evidence':[source]}]}

        self.admission.rows = [program]
        self.admission.evidence = Mock()
        for label, evidence, query in [
            ('exact', proof(), '085404 计算机技术 考408'),
            ('off_year', proof(source={**ref,'year':2026}), '085404 计算机技术 考408'),
            ('wrong_revision', proof(revision='other'), '085404 计算机技术 考408'),
            ('other_document', proof(source={**ref,'sha256':'b'*64}), '085404 计算机技术 考408'),
            ('unverified', proof(status='needs_review'), '085404 计算机技术 考408'),
            ('subject_mismatch', proof(subject_rows=subjects[:-1]), '085404 计算机技术 考408'),
            ('excluded', proof(), '085404 计算机技术 不考408'),
        ]:
            with self.subTest(label=label):
                self.admission.evidence.return_value = evidence
                response = self.search([], args={'query':query})
                self.assertEqual(response['results'], [])
                self.assertEqual(response['target_status'], 'not_found')
                self.assertEqual(response['request_count'], self.tools.usage['searches'])
                proof_rows = response.get('verified_catalog_evidence', [])
                if label == 'exact':
                    self.assertEqual([(row['program_id'], row['url']) for row in proof_rows], [('p-123',ref['url'])])
                    self.assertEqual(self.tools.programs.keys(), {'p-123'})
                    self.assertEqual(self.tools.sources[proof_rows[0]['source_id']]['program_ids'], ['p-123'])
                    self.assertEqual(self.tools.usage['documents'],0)
                else:
                    self.assertEqual(proof_rows, [])
                    self.assertEqual(self.tools.programs, {})

    def test_unique_registered_origin_binds_without_treating_summary_as_fact(self):
        official = 'https://grad.example.edu.cn/catalog-2027'
        response = self.search([result(official), result('https://not-a-unit.edu.cn/catalog')])
        self.assertEqual([row['url'] for row in response['results']], [official])
        lead = response['results'][0]
        self.assertEqual((lead['unit_id'], lead['unit_name']), ('123', '测试大学'))
        self.assertEqual((lead['source_tier'], lead['status']), ('official', 'search_lead'))
        self.assertEqual(lead['requested_kind'], 'catalog')
        self.assertEqual(lead['requested_year'], 2027)
        self.assertIsNone(lead['year'])
        self.assertFalse(lead['original_available'])
        self.assertEqual(self.tools._research_pool, {})

    def test_ambiguous_shared_origin_is_unbound_even_with_one_school_selected(self):
        self.admission.units.append(school('456', '另一研究院', 'https://grad.example.edu.cn/'))
        response = self.search([result('https://grad.example.edu.cn/catalog')], unit_ids=('123',))
        self.assertEqual(response['results'], [])
        self.assertEqual(response['target_status'], 'not_found')

    def test_shared_host_with_distinct_approved_paths_binds_only_matching_school(self):
        self.admission.units = [school('123', '第一研究院', 'https://shared.example.edu.cn/first/'),
                                school('456', '第二研究院', 'https://shared.example.edu.cn/second/')]
        exact = 'https://shared.example.edu.cn/first/catalog'
        response = self.search([result(exact), result('https://shared.example.edu.cn/first-lookalike/catalog'),
                                result('https://shadow.shared.example.edu.cn/first/catalog')])
        self.assertEqual([(row['url'], row['unit_id']) for row in response['results']], [(exact, '123')])

    def test_explicit_school_and_province_scopes_do_not_widen(self):
        self.admission.units.append(school('456', '北京测试学院', 'https://graduate.example.ac.cn/', '北京'))
        rows = [result('https://grad.example.edu.cn/catalog'), result('https://graduate.example.ac.cn/catalog')]
        for scope in ({'unit_ids': ('123',)}, {'provinces': ('天津',)}):
            with self.subTest(scope=scope):
                response = self.search(rows, **scope)
                self.assertEqual([row['unit_id'] for row in response['results']], ['123'])

    def test_public_province_in_search_text_limits_catalog_leads_before_profile_confirmation(self):
        self.admission.units.append(school('456','山东样本大学','https://admissions.example.ac.cn/','山东'))
        hits = [result('https://grad.example.edu.cn/catalog'),result('https://admissions.example.ac.cn/catalog')]
        response = self.search(hits, args={'query':'天津 085404 计算机技术 初试'})
        self.assertEqual({row['unit_id'] for row in response['results']},{'123'})

    def test_chsi_navigation_and_third_party_keep_distinct_trust_states(self):
        self.admission.units[0]['official_url'] = 'https://yz.chsi.com.cn/sch/schoolInfo--schId-123.dhtml'
        chsi = result('https://yz.chsi.com.cn/sch/schoolInfo--schId-123.dhtml')
        external = result('https://www.offcn.com/kaoyan/catalog.html')
        response = self.search([chsi, external])
        self.assertEqual([row['url'] for row in response['results']], [external['url']])
        self.assertEqual(response['failure_stage'], 'third_party_only')
        self.assertIsNone(response['results'][0]['unit_id'])

    def test_known_dynamic_scope_must_still_be_current(self):
        owned = 'https://extra.example.org/graduate/catalog'
        authority = {'unit_id': '123', 'state': 'verified', 'replay_verified': True,
                     'scope': {'scheme': 'https', 'host': 'extra.example.org', 'port': 443,
                               'path': '/graduate/', 'path_mode': 'prefix'}}
        for keep in (True, False):
            with self.subTest(keep=keep):
                def active_unit(tools, identifier):
                    return {**tools._official_base_units[identifier],
                            'official_authorities': [authority] if keep else []}

                def initialized(tools, *args, **kwargs):
                    initialize(tools, *args, **kwargs)
                    unit = tools.units['123']
                    tools.units['123'] = {**unit, 'official_authorities': [authority]}

                from research_official_tools import initialize
                with patch('research_official_tools.initialize', side_effect=initialized), \
                     patch('research_official_tools.current_unit', side_effect=active_unit):
                    response = self.search([result(owned)])
                self.assertEqual([row['unit_id'] for row in response['results']], ['123'] if keep else [])


if __name__ == '__main__':
    unittest.main()
