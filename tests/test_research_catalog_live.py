from unittest import TestCase
from unittest.mock import Mock, patch

from research_catalog_live import adopt, replay
from research_tools import _document_scope
from research_official_tools import official_search
from tests.test_research_broad_official_discovery import BroadOfficialDiscoveryTests, result


class LiveCatalogReplayTests(TestCase):
    def test_candidate_requires_exact_source_and_keeps_unprinted_fields_unknown(self):
        url = 'https://grad.example.edu.cn/catalog'
        source = {'id':'s','url':url,'unit_id':'unit','year':2027,'sha256':'a'*64}
        subjects = [{'exam_unit':slot,'code':code,'name':name,'scope':'national'}
                    for slot,code,name in [(1,'101','政治'),(2,'201','英语一'),(3,'308','护理综合')]]
        row = {'id':'row','unit_id':'unit','year':2027,'code':'105400','name':'护理','subjects':subjects,
               'evidence':[{'url':url,'quote':'105400 护理'}],
               'exam_evidence':[{'url':url,'quote':'101 201 308'}]}
        tools = Mock()
        tools.task = {'query':'福建 105400 护理 初试 308','reference_year':2027,'id':'r'}
        tools.sources = {'s':source}
        tools._live_catalog_query = tools.task['query']
        tools._source_candidate_records = {}
        tools._source_unavailable.return_value = False
        tools._unit.return_value = {'id':'unit'}
        with patch('research_candidate_tools._trusted',return_value=True), \
             patch('research_candidate_tools._sync_pool'), \
             patch('research_catalog_live._parse_rows',return_value=[row]):
            adopt(tools,'s')
            candidate = next(iter(tools._source_candidate_records.values()))
            self.assertIsNone(candidate.get('study_mode'))
            self.assertFalse(candidate['eligibility_evidence_complete'])
            self.assertEqual(replay(tools,candidate),candidate)
            self.assertEqual({ref['url'] for ref in candidate['evidence_refs']},{url})
            tools._source_unavailable.return_value = True
            self.assertIsNone(replay(tools,candidate))
            tools._source_unavailable.return_value = False
            tools._source_candidate_records = {}
            tools.task = {**tools.task,'query':'福建 105400 护理 不考308'}
            tools._live_catalog_query = '福建 105400 护理 初试308'
            adopt(tools,'s')
            self.assertEqual(tools._source_candidate_records,{})

    def test_split_cover_and_generic_master_catalog_title_establish_year(self):
        doc = {'text':'2027 年攻读硕士学位研究生\n招生章程及专业目录'}
        self.assertEqual(_document_scope(doc)[1],2027)
        title = '某大学2027年硕士研究生专业目录'
        self.assertEqual(_document_scope({'document_title':title}),(title,2027))
        self.assertTrue(official_search.target_matches({'title':title},2027,'catalog'))

    def test_exact_school_mention_in_non_official_result_only_routes_a_fresh_search(self):
        fixture = BroadOfficialDiscoveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original = 'https://grad.example.edu.cn/catalog'
        third = 'https://www.offcn.com/kaoyan/reference'

        def provider(config,method,path,body,**kwargs):
            rows = ([result(original)] if body.get('Site')=='grad.example.edu.cn' else
                    [result(third,'测试大学 2027 硕士初试 308 考试大纲')])
            return {'Response':{'Pages':rows}}

        fixture.network.request_json.side_effect = provider
        response = fixture.search([],provinces=('天津',),args={'query':'天津 105400 护理 初试 308'})
        verified_lead = next(row for row in response['results'] if row['url']==original)
        reference = next(row for row in response['results'] if row['url']==third)
        self.assertEqual(verified_lead['search_channel'],'yuanbao_school_hint')
        self.assertEqual(verified_lead['status'],'search_lead')
        self.assertIsNone(reference['unit_id'])
        self.assertEqual(fixture.tools.programs,{})
