"""Multi-turn inheritance: reuse revalidated evidence and stop repeating unchanged search."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest

from grad_engine import validate_profile
from research_service import ResearchService
from research_tools import ResearchTools
from tests.test_research_service import FakeAI, FakeAdmission, FakeSettings, wait_done

SHA = 'a' * 64
URL = 'https://grad.example.edu.cn/2027-fees.html'


def evidence(identifier='s_' + 'a' * 20, **overrides):
    row = {'id': identifier, 'unit_id': '123', 'unit_name': '测试大学', 'year': 2027,
           'status': 'official_original_read', 'title': '2027年硕士研究生招生简章',
           'url': URL, 'sha256': SHA, 'field_verification': 'not_structured',
           'excerpt': '报考条件：具有国家承认的本科毕业学历。\n学费标准为12000元/年，各专业相同。'}
    row.update(overrides)
    return row


def candidate(**overrides):
    row = {'id': 'p1', 'unit_id': '123', 'unit_name': '测试大学', 'year': 2027,
           'name': '计算机科学与技术', 'code': '081200', 'department': '计算机学院',
           'direction': '00不区分方向', 'study_mode': '全日制', 'province': '天津',
           'tuition': None, 'requires_review': True, 'eligibility_evidence_complete': False,
           'subjects': [{'code': '408', 'name': '计算机学科专业基础', 'scope': 'national', 'exam_unit': 4}],
           'evidence': [{'url': 'https://grad.example.edu.cn/2027-catalog.html', 'title': '2027目录', 'year': 2027}]}
    row.update(overrides)
    return row


class InheritingAdmission(FakeAdmission):
    def __init__(self):
        super().__init__()
        self.rows = [candidate()]

    def catalog_program(self, identifier, revision=None):
        row = next((deepcopy(item) for item in self.rows if item['id'] == identifier), None)
        if row is None:
            raise LookupError('unknown program')
        return {'program': row, 'revision': 'fixture-r1'}


def task(**overrides):
    value = {'id': 'r_1', 'query': '想考计算机', 'profile': validate_profile({}),
             'unit_ids': [], 'confirmed_fields': [], 'target_year': 2028, 'reference_year': 2027,
             'turn_id': 1, 'previous_turn_id': 0, 'turn_changes': [], 'unit_scope_changed': False}
    value.update(overrides)
    return value


def continue_when_ready(service, payload):
    for _ in range(200):
        if service.get(payload['research_id'])['can_continue']:
            return service.continue_research(payload)
        time.sleep(.01)
    raise AssertionError('worker did not release the previous turn')


class InheritanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def tools(self, previous, **overrides):
        return ResearchTools(InheritingAdmission(), None, task(previous_report=previous, **overrides), self.data)

    def test_previous_candidates_and_sources_are_revalidated_before_reuse(self):
        broken = evidence('s_' + 'c' * 20, url='javascript:alert(1)')
        stale_year = candidate(id='old', year=2026)
        tools = self.tools({'candidates': [{'id': 'p1'}, {'id': 'gone'}, {'id': 'old'}],
                            'evidence': [evidence(), broken]})
        self.assertEqual(tools.inheritance['candidate_ids'], ['p1'])
        self.assertIn('gone', tools.inheritance['stale_candidate_ids'])
        self.assertIn('old', tools.inheritance['stale_candidate_ids'])
        self.assertEqual(tools.inheritance['source_ids'], ['s_' + 'a' * 20])
        self.assertIn('p1', tools.programs)
        self.assertEqual(tools.programs['p1']['province'], '天津', 'full catalog row is re-read, not the trimmed report')
        self.assertTrue(tools.inheritance['reused'])
        report = tools.report()
        self.assertEqual(report['inheritance']['candidate_ids'], ['p1'])
        self.assertTrue(report['plan']['inheritance']['reused'])

    def test_changed_conditions_only_recompute_the_algorithm_without_new_search(self):
        tools = self.tools({'candidates': [{'id': 'p1'}], 'evidence': [evidence()]},
                           turn_changes=['max_annual_tuition'])
        tools.compare({})
        report = tools.report()
        self.assertEqual(report['plan']['inheritance']['profile_changed'], ['max_annual_tuition'])
        self.assertEqual(report['inheritance']['candidate_ids'], ['p1'])
        self.assertTrue(report['plan']['inheritance']['reused'])
        self.assertIn('只重算确定性算法', report['plan']['inheritance']['recalculation'])

    def test_two_agreeing_originals_upgrade_unknown_tuition_and_keep_field_provenance(self):
        second = evidence('s_' + 'b' * 20, sha256='b' * 64,
                          url='https://grad.example.edu.cn/2027-fees-2.html',
                          excerpt='各专业学费均为12000元/年，收费标准一致。')
        tools = self.tools({'candidates': [{'id': 'p1'}], 'evidence': [evidence(), second]})
        tools.compare({})
        report = tools.report()
        tuition = next(row for row in report['field_candidates'] if row['field'] == 'tuition')
        self.assertEqual(tuition['status'], 'verified')
        self.assertEqual(report['candidates'][0]['tuition']['amount'], 12000)
        self.assertEqual(report['candidates'][0]['tuition']['verified_by'], 'two_source_agreement')
        facts = {(row['candidate_id'], row['fact']): row for row in report['plan']['facts']}
        self.assertEqual(facts[('p1', 'tuition')]['status'], 'known')
        decision = next(row for row in report['decisions'] if row['candidate_id'] == 'p1')
        self.assertTrue(decision['deterministic'])
        self.assertTrue(decision['original_links'], 'recommendation must return to a saved original')
        self.assertTrue(any(reason['evidence'] for reason in decision['reasons']))

    def test_conflicting_tuition_pauses_the_candidate_and_keeps_eligibility_unknown(self):
        second = evidence('s_' + 'b' * 20, sha256='b' * 64,
                          url='https://grad.example.edu.cn/2027-fees-2.html',
                          excerpt='各专业学费均为15000元/年，收费标准一致。')
        tools = self.tools({'candidates': [{'id': 'p1'}], 'evidence': [evidence(), second]})
        tools.compare({})
        report = tools.report()
        self.assertIsNone(report['candidates'][0]['tuition'])
        self.assertTrue(report['candidates'][0]['requires_review'])
        self.assertEqual(report['candidates'][0]['field_conflicts'][0]['paused'], True)
        self.assertTrue(any('冲突' in gap for gap in report['gaps']))
        eligibility = next(row for row in report['plan']['facts'] if row['fact'] == 'eligibility')
        self.assertEqual(eligibility['status'], 'unknown')

    def test_eligibility_draft_does_not_remove_the_unknown_state(self):
        tools = self.tools({'candidates': [{'id': 'p1'}], 'evidence': [evidence()]})
        tools.compare({})
        report = tools.report()
        self.assertFalse(report['candidates'][0]['eligibility_evidence_complete'])
        fact = next(row for row in report['plan']['facts'] if row['fact'] == 'eligibility')
        self.assertEqual(fact['status'], 'unknown')
        self.assertIsNone(fact['value'])


class ServiceInheritanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.data.mkdir()

    def service(self, messages):
        ai = FakeAI(messages)
        service = ResearchService(self.data, InheritingAdmission(), FakeSettings(), ai)
        self.addCleanup(service.close)
        return service

    def test_continuing_a_research_reuses_candidates_without_an_automatic_search(self):
        service = self.service([
            {'role': 'assistant', 'tool_calls': [{'id': 'c1', 'name': 'find_candidates',
                                                  'arguments': {'query': '计算机'}}]},
            {'role': 'assistant', 'content': '先保留候选。'}])
        first = wait_done(service, service.create({'query': '想考计算机'})['id'])
        second = wait_done(service, continue_when_ready(
            service, {'research_id': first['id'], 'profile': {'max_annual_tuition': 8000}})['id'])
        self.assertEqual(second['turn_id'], 2)
        self.assertEqual(second['turn_changes'], ['max_annual_tuition'])
        self.assertFalse(second['unit_scope_changed'])
        self.assertEqual(second['report']['inheritance']['candidate_ids'], ['p1'])
        self.assertEqual(second['report']['inheritance']['previous_turn_id'], 1)
        events = [event for event in second['events'] if event.get('turn_id') == 2]
        self.assertTrue(any(event['stage'] == 'inherit' for event in events))
        self.assertFalse(any(event['stage'] == 'knowledge' for event in events),
                         'unchanged evidence must not trigger another automatic knowledge search')
        self.assertEqual(second['report']['plan']['inheritance']['profile_changed'],
                         ['max_annual_tuition'])


if __name__ == '__main__':
    unittest.main()
