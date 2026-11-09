"""The executable research plan must reach every model round as bounded data."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from research_service import ResearchService
from tests.test_research_service import FakeAdmission, FakeAI, FakeSettings, wait_done


def plans(messages):
    result = []
    for message in messages:
        try:
            value = json.loads(message.get('content', ''))
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and 'research_plan' in value:
            result.append(value['research_plan'])
    return result


class ResearchPlanContextTests(unittest.TestCase):
    def test_initial_and_post_tool_rounds_receive_live_plan_and_queries(self):
        temporary = tempfile.TemporaryDirectory(prefix='plan-context-')
        self.addCleanup(temporary.cleanup)
        ai = FakeAI([
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'find-1', 'name': 'find_candidates',
                 'arguments': {'query': '计算机', 'unit_id': '123'}}]},
            {'role': 'assistant', 'content': '请核对条件。', 'tool_calls': []},
        ])
        service = ResearchService(Path(temporary.name) / 'data', FakeAdmission(),
                                  settings=FakeSettings(), ai_client=ai)
        self.addCleanup(service.close)
        task = wait_done(service, service.create({'query': '计算机', 'unit_ids': ['123']})['id'])
        self.assertEqual(len(ai.calls), 2)
        first, second = [plans(messages) for messages in ai.calls]
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertIn('scope', first[0]['completion']['remaining_goals'])
        self.assertEqual(second[0]['next_actions'], task['report']['plan']['next_actions'])
        self.assertTrue(any(action['args']['query'] == '学费 收费标准 收费公示'
                            for action in second[0]['next_actions']))
        self.assertEqual(first[0]['budget']['used']['tool_calls'], 0)
        self.assertEqual(second[0]['budget']['used']['tool_calls'], 1)

    def test_plan_survives_history_compaction_and_source_invalidation(self):
        current = {'next_actions': [{'tool': 'search_official_sources',
                                    'args': {'unit_id': '123', 'query': '新收费规则'}}],
                   'goals': [], 'missing_fact_count': 1,
                   'budget': {'remaining': {'searches': 1}}, 'completion': {'ready': False}}
        for invalidated in (False, True):
            with self.subTest(invalidated=invalidated):
                tools = SimpleNamespace(refresh_knowledge=lambda: invalidated,
                    plan=lambda: deepcopy(current), sources={}, inheritance={}, field_candidates=[],
                    programs={}, algorithm={}, questions=lambda: [], usage={})
                base = [{'role': 'system', 'content': 'test'}, {'role': 'user', 'content': 'request'}]
                history = [*base, *[{'role': 'assistant', 'content': str(index)} for index in range(9)]]
                before = deepcopy(history)
                result = ResearchService._context(base, history, tools)
                self.assertEqual(plans(result), [current])
                self.assertEqual(history, before)

    def test_large_plan_facts_do_not_hide_the_next_actions_or_leak_raw_sources(self):
        raw = 'UNTRUSTED_ORIGINAL' * 10000
        plan = {'next_actions': [{'tool': 'search_official_sources',
                                'args': {'unit_id': '123', 'query': '学费'}}],
                'goals': [], 'missing_fact_count': 100,
                'facts': [{'evidence': raw}] * 100,
                'budget': {'remaining': {'searches': 2}}, 'completion': {'ready': False}}
        tools = SimpleNamespace(refresh_knowledge=lambda: False, plan=lambda: plan)
        result = ResearchService._context([], [], tools)
        self.assertEqual(plans(result)[0]['next_actions'], plan['next_actions'])
        self.assertNotIn('UNTRUSTED_ORIGINAL', json.dumps(result))
        self.assertLess(len(result[-1]['content']), 6000)


if __name__ == '__main__':
    unittest.main()
