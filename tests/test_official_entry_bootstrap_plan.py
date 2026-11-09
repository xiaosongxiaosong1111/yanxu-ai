"""An empty catalog must advance from failed navigation to a bounded search."""
import unittest
from research_plan import build_plan


class OfficialEntryBootstrapPlanTests(unittest.TestCase):
    def plan(self, *, scope=('u1',), discovery=(), searched=(), searches=0):
        return build_plan({'reference_year':2027,'target_year':2028,'unit_ids':list(scope)},
            {},{}, {},[],{'searches':searches},official_discovery=discovery,searched_units=searched)

    def test_no_seed_navigation_failure_advances_to_search_without_inventing_candidates(self):
        first=self.plan()
        self.assertEqual(first['next_actions'][0]['tool'],'discover_official_entry')
        result=self.plan(discovery=[{'unit_id':'u1','year':2027,'kind':'catalog','stage':'no_lead'}])
        self.assertEqual(result['next_actions'][0]['tool'],'search_official_sources')
        self.assertEqual(result['next_actions'][0]['args']['unit_id'],'u1')
        self.assertEqual(result['facts'],[])

    def test_an_executed_unit_without_a_preselected_scope_can_also_advance(self):
        result=self.plan(scope=(),discovery=[{'unit_id':'u1','year':2027,'kind':'catalog','stage':'no_lead'}])
        self.assertEqual(result['next_actions'][0]['tool'],'search_official_sources')

    def test_search_is_not_repeated_and_other_schools_keep_their_next_step(self):
        result=self.plan(scope=('u1','u2'),searched=('u1',),
            discovery=[{'unit_id':'u1','year':2027,'kind':'catalog','stage':'verification_required'}])
        self.assertEqual([row['args']['unit_id'] for row in result['next_actions']],['u2'])
        exhausted=self.plan(searches=4,discovery=[{'unit_id':'u1','year':2027,'kind':'catalog','stage':'no_lead'}])
        self.assertEqual(exhausted['next_actions'],[])

    def test_a_different_year_or_document_kind_cannot_skip_current_discovery(self):
        for change in ({'year':2026},{'kind':'charter'},{'unit_id':'other'}):
            with self.subTest(change=change):
                result=self.plan(discovery=[{'unit_id':'u1','year':2027,'kind':'catalog',**change}])
                self.assertEqual(result['next_actions'][0]['tool'],'discover_official_entry')


if __name__=='__main__':unittest.main()
