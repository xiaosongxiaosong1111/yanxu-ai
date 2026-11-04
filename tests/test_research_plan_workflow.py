"""The research loop exposes the same plan ledger it uses for gaps and budgets."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from research_service import ResearchService
from tests.test_research_service import FakeAI, FakeAdmission, FakeSettings, wait_done


class PlanWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def service(self, messages):
        ai = FakeAI(messages)
        service = ResearchService(self.data, FakeAdmission(), FakeSettings(), ai)
        self.addCleanup(service.close)
        return service, ai

    def test_report_contains_goal_gap_skill_and_budget_ledger(self):
        service, _ = self.service([
            {'role': 'assistant', 'tool_calls': [{'id': 'c1', 'name': 'find_candidates',
                                                  'arguments': {'query': '计算机'}}]},
            {'role': 'assistant', 'content': '已完成。'}])
        task = wait_done(service, service.create({'query': '想考计算机'})['id'])
        plan = task['report']['plan']
        self.assertTrue(plan['completion']['ready'])
        self.assertEqual(plan['goals'][0]['status'], 'done')
        self.assertEqual(plan['goals'][2]['status'], 'done')
        self.assertEqual(plan['missing_fact_count'], 2)
        self.assertTrue(any('学费' in action['args']['query'] for action in plan['next_actions']))
        self.assertTrue(any('报考条件' in action['args']['query'] for action in plan['next_actions']))
        self.assertGreater(plan['budget']['used']['tool_calls'], 0)
        self.assertEqual(plan['budget']['remaining']['searches'], 4)
        skills = {row['id']: row for row in plan['skills']}
        self.assertEqual(skills['understand-intent']['status'], 'checked')
        self.assertEqual(skills['compare-plans']['status'], 'checked')
        self.assertEqual(skills['verify-evidence']['status'], 'executed')
        self.assertFalse(skills['verify-evidence']['checked'])

    def test_budget_exhaustion_keeps_partial_results_and_a_continue_plan(self):
        ai = FakeAI([])
        ai.chat = Mock(return_value={
            'message': {'role': 'assistant', 'tool_calls': [{'id': 'c1', 'name': 'find_candidates',
                                                             'arguments': {'query': '计算机'}}]},
            'usage': {'total_tokens': 50000}})
        service = ResearchService(self.data, FakeAdmission(), FakeSettings(), ai)
        self.addCleanup(service.close)
        task = wait_done(service, service.create({'query': '计算机'})['id'])
        self.assertEqual(task['status'], 'partial')
        plan = task['report']['plan']
        self.assertEqual(plan['budget']['used']['total_tokens'], 50000)
        self.assertEqual(plan['budget']['remaining']['tool_calls'], 19)
        self.assertIn('保留已有成果', plan['budget']['stop_policy'])
        self.assertTrue(plan['next_actions'])
        self.assertTrue(any('预算' in gap for gap in plan['gaps']))

    def test_no_candidate_means_open_scope_and_no_false_completion(self):
        service, _ = self.service([{'role': 'assistant', 'content': '已完成。'}])
        task = wait_done(service, service.create({'query': '不存在的专业'})['id'])
        plan = task['report']['plan']
        self.assertFalse(plan['completion']['ready'])
        self.assertEqual(plan['goals'][0]['status'], 'open')
        self.assertIn('scope', plan['completion']['remaining_goals'])
        self.assertIn('不能把模型说完成当作研究完成', plan['completion']['statement'])


if __name__ == '__main__':
    unittest.main()
