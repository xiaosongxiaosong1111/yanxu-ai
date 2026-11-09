"""Research planning ledger: deterministic gaps, budgets and Skill contract checks."""
import unittest

from research_plan import build_plan


def candidate(**overrides):
    row = {'id': 'p1', 'unit_id': 'u1', 'unit_name': '测试大学', 'year': 2027,
           'eligibility_evidence_complete': False, 'tuition': None,
           'subjects': [{'code': code, 'name': name, 'scope': 'national', 'exam_unit': slot}
                        for slot, code, name in [(1, '101', '思想政治理论'), (2, '201', '英语一'),
                                                 (3, '301', '数学一'), (4, '408', '计算机学科专业基础')]]}
    row.update(overrides)
    return row


class PlanTests(unittest.TestCase):
    def build(self, programs=None, **overrides):
        values = {'task': {'id': 'r_1', 'target_year': 2028, 'reference_year': 2027},
                  'programs': programs if programs is not None else {'p1': candidate()},
                  'sources': {}, 'algorithm': {'matching': {'summary': {'input_count': 1}}},
                  'gaps': [], 'usage': {'searches': 1, 'documents': 0, 'tool_calls': 3, 'rounds': 2},
                  'loaded_skills': ('understand-intent', 'research-official-sources', 'verify-evidence',
                                    'compare-plans'),
                  'executed_tools': ('find_candidates', 'compare_candidates')}
        values.update(overrides)
        return build_plan(**values)

    def test_missing_eligibility_and_tuition_stay_unknown_with_prioritized_next_actions(self):
        plan = self.build()
        facts = {(row['candidate_id'], row['fact']): row for row in plan['facts']}
        self.assertEqual(facts[('p1', 'eligibility')]['status'], 'unknown')
        self.assertIsNone(facts[('p1', 'eligibility')]['value'])
        self.assertEqual(facts[('p1', 'tuition')]['status'], 'unknown')
        self.assertIsNone(facts[('p1', 'tuition')]['value'])
        self.assertEqual(facts[('p1', 'subjects')]['status'], 'known')
        self.assertEqual({row['tool'] for row in plan['next_actions']}, {'discover_official_entry'})
        self.assertEqual({row['args']['kind'] for row in plan['next_actions']}, {'charter', 'fees'})
        self.assertEqual([row['priority'] for row in plan['next_actions']], [1, 2])
        self.assertEqual(plan['missing_fact_count'], 3)
        self.assertEqual(facts[('p1', 'duration_years')]['status'], 'unknown')
        self.assertEqual(plan['goals'][1]['status'], 'partial')
        self.assertIn('unknown', plan['goals'][1]['completion'])

    def test_zero_or_blank_cost_is_unknown_not_free(self):
        plan = self.build({'p1': candidate(tuition=0, eligibility_evidence_complete=True)})
        tuition = next(row for row in plan['facts'] if row['fact'] == 'tuition')
        self.assertEqual(tuition['status'], 'unknown')
        self.assertIsNone(tuition['value'])
        self.assertTrue(any(row['args'].get('kind') == 'fees' for row in plan['next_actions']))

    def test_completion_is_not_claimed_when_candidates_or_algorithm_are_missing(self):
        no_algorithm = self.build(algorithm={})
        self.assertFalse(no_algorithm['completion']['ready'])
        self.assertIn('algorithm', no_algorithm['completion']['remaining_goals'])
        self.assertIn('不能把模型说完成当作研究完成', no_algorithm['completion']['statement'])
        empty = self.build(programs={})
        self.assertFalse(empty['completion']['ready'])
        self.assertEqual(empty['goals'][0]['status'], 'open')

    def test_budget_is_finite_and_never_negative(self):
        plan = self.build(usage={'searches': 9, 'documents': 9, 'tool_calls': 99})
        self.assertEqual(plan['budget']['remaining'], {'searches': 0, 'documents': 0, 'tool_calls': 0})
        self.assertIn('保留已有成果', plan['budget']['stop_policy'])

    def test_skill_loading_execution_and_checked_result_are_independent(self):
        plan = self.build()
        skills = {row['id']: row for row in plan['skills']}
        self.assertTrue(skills['understand-intent']['checked'])
        self.assertEqual(skills['understand-intent']['status'], 'checked')
        self.assertTrue(skills['compare-plans']['checked'])
        self.assertFalse(skills['verify-evidence']['checked'])
        self.assertNotEqual(skills['verify-evidence']['status'], 'checked')
        only_loaded = self.build(executed_tools=())
        loaded = {row['id']: row for row in only_loaded['skills']}
        self.assertEqual(loaded['compare-plans']['status'], 'loaded')
        self.assertFalse(loaded['compare-plans']['checked'])
        not_loaded = self.build(loaded_skills=())
        self.assertEqual({row['id']: row for row in not_loaded['skills']}['compare-plans']['status'], 'not_loaded')

    def test_official_evidence_must_carry_real_year_and_sha_before_verification_passes(self):
        weak = {'s1': {'id': 's1', 'unit_id': 'u1', 'status': 'official_original_read', 'year': None,
                       'sha256': 'a' * 64}}
        self.assertFalse({row['id']: row for row in self.build(sources=weak, executed_tools=('search_knowledge_segments',))['skills']}['verify-evidence']['checked'])
        strong = {'s1': {'id': 's1', 'unit_id': 'u1', 'status': 'official_original_read', 'year': 2027,
                         'sha256': 'a' * 64}}
        self.assertTrue({row['id']: row for row in self.build(sources=strong, executed_tools=('search_knowledge_segments',))['skills']}['verify-evidence']['checked'])

    def test_search_leads_alone_never_mark_official_sources_checked(self):
        lead = {'s1': {'id': 's1', 'unit_id': 'u1', 'status': 'search_lead',
                       'year': 2027, 'url': 'https://grad.example.edu.cn/'}}
        lead_plan = self.build(sources=lead, executed_tools=('search_official_sources',))
        lead_skill = {row['id']: row for row in lead_plan['skills']}['research-official-sources']
        self.assertFalse(lead_skill['checked'])
        self.assertEqual(lead_skill['status'], 'executed')
        self.assertIn('尚未取得带实际年度', lead_skill['reason'])
        undated = {'s1': {'id': 's1', 'unit_id': 'u1', 'status': 'official_original_read',
                          'year': None, 'sha256': 'a' * 64}}
        self.assertFalse({row['id']: row for row in self.build(
            sources=undated, executed_tools=('search_official_sources',))['skills']}['research-official-sources']['checked'])
        for status in ('official_original_read', 'published_snapshot'):
            strong = {'s1': {'id': 's1', 'unit_id': 'u1', 'status': status, 'year': 2027,
                             'sha256': 'a' * 64 if status == 'official_original_read' else None}}
            self.assertTrue({row['id']: row for row in self.build(
                sources=strong, executed_tools=('search_official_sources',))['skills']}['research-official-sources']['checked'])

    def test_previous_report_carries_candidate_and_evidence_versions_for_inheritance(self):
        previous = {'candidates': [{'id': 'p1'}], 'evidence': [{'id': 's1'}, {'id': 's2'}]}
        task = {'id': 'r_2', 'target_year': 2028, 'reference_year': 2027,
                'previous_turn_id': 1, 'previous_report': previous}
        plan = self.build(task=task)
        self.assertEqual(plan['previous']['turn_id'], 1)
        self.assertEqual(plan['previous']['candidate_ids'], ['p1'])
        self.assertEqual(plan['previous']['evidence_ids'], ['s1', 's2'])
        self.assertIn('只重算受影响', plan['previous']['inherit'])

    def source(self, **changes):
        return {'id': 's1', 'unit_id': 'u1', 'year': 2027, 'status': 'official_original_read',
                'title': '测试大学2027年硕士研究生学费与学制公示', 'sha256': 'a' * 64,
                'knowledge_id': 'k1', 'usable': True, 'knowledge_current': True,
                'quality': {'state': 'accepted', 'reusable': True},
                'extraction': {'id': 'ex1', 'generation_id': 'kg1'}, **changes}

    def test_original_to_segment_read_and_fact_proposal_never_invents_values_or_quotes(self):
        source = self.source()
        plan = self.build(sources={'s1': source})
        tuition = next(row for row in plan['facts'] if row['fact'] == 'tuition')
        self.assertEqual(tuition['next_action']['tool'], 'search_knowledge_segments')
        self.assertEqual(tuition['next_action']['args']['entry_id'], 'k1')
        source['segments'] = [{'id': 'sg1', 'char_start': 10, 'char_end': 30}]
        plan = self.build(sources={'s1': source})
        tuition = next(row for row in plan['facts'] if row['fact'] == 'tuition')
        self.assertEqual(tuition['next_action']['tool'], 'read_knowledge_segment')
        receipt = {'segment_id': 'sg1', 'source_id': 's1', 'entry_id': 'k1',
                   'char_start': 10, 'char_end': 30, 'year': 2027, 'unit_id': 'u1',
                   'sha256': 'a' * 64, 'text_sha256': 'b' * 64,
                   'extraction_id': 'ex1', 'generation_id': 'kg1'}
        plan = self.build(sources={'s1': source}, read_receipts=[receipt])
        action = next(row for row in plan['facts'] if row['fact'] == 'tuition')['next_action']
        self.assertEqual(action['tool'], 'propose_admission_facts')
        self.assertEqual(action['args'], {'program_id': 'p1'})
        self.assertEqual(action['needs_input'], ['drafts'])
        self.assertNotIn('quote', action['args'])
        self.assertNotIn('value', action['args'])

    def test_wrong_source_year_unit_quality_or_read_generation_does_not_advance_fact(self):
        for change in ({'year': 2026}, {'unit_id': 'other'}, {'usable': False},
                       {'knowledge_current': False}, {'quality': {'state': 'pending', 'reusable': False}}):
            with self.subTest(change=change):
                plan = self.build(sources={'s1': self.source(**change)})
                action = next(row for row in plan['facts'] if row['fact'] == 'tuition')['next_action']
                self.assertEqual(action['tool'], 'discover_official_entry')
        source = self.source(segments=[{'id': 'sg1', 'char_start': 10, 'char_end': 30}])
        plan = self.build(sources={'s1': source}, read_receipts=[{'segment_id': 'sg1', 'source_id': 's1',
            'entry_id': 'k1', 'generation_id': 'old', 'char_start': 10, 'char_end': 30}])
        self.assertEqual(next(row for row in plan['facts'] if row['fact'] == 'tuition')['next_action']['tool'],
                         'read_knowledge_segment')

    def test_snapshot_must_match_unit_year_and_kind_before_paid_fallback(self):
        current = {'unit_id': 'u1', 'year': 2027, 'kind': 'fees', 'stage': 'entry_verified',
                   'document_sources': []}
        for change in ({'unit_id': 'other'}, {'year': 2026}, {'kind': 'charter'}):
            with self.subTest(change=change):
                plan = self.build(official_discovery=[{**current, **change}])
                self.assertEqual(next(row for row in plan['facts'] if row['fact'] == 'tuition')
                                 ['next_action']['tool'], 'discover_official_entry')
        plan = self.build(official_discovery=[current])
        self.assertEqual(next(row for row in plan['facts'] if row['fact'] == 'tuition')
                         ['next_action']['tool'], 'search_official_sources')
        plan = self.build(official_discovery=[current], searched_units=['u1'])
        self.assertFalse(any(row['tool'] == 'search_official_sources' for row in plan['next_actions']))

    def test_actions_rotate_schools_and_fee_duration_share_one_discovery(self):
        programs = {f'p{i}': candidate(id=f'p{i}', unit_id=f'u{i}') for i in range(1, 11)}
        plan = self.build(programs)
        self.assertEqual(len(plan['next_actions']), 8)
        self.assertEqual(len({row['args']['unit_id'] for row in plan['next_actions']}), 8)
        plan = self.build()
        self.assertEqual(sum(row['args'].get('kind') == 'fees' for row in plan['next_actions']), 1)

    def test_applied_partial_or_pending_fact_does_not_repeat_its_read_or_claim_complete(self):
        source = self.source(segments=[{'id': 'sg1', 'char_start': 10, 'char_end': 30}])
        audit = {'facts': [{'program_id': 'p1', 'field': 'tuition', 'state': 'needs_review',
                            'proof': {'draft': {'citation': {'segment_id': 'sg1'}}}}]}
        plan = self.build(sources={'s1': source}, admission_facts=audit)
        action = next(row for row in plan['facts'] if row['fact'] == 'tuition')['next_action']
        self.assertNotEqual(action.get('tool'), 'read_knowledge_segment')
        program = candidate(eligibility_evidence_complete=True, requires_review=True)
        plan = self.build({'p1': program})
        self.assertEqual(next(row for row in plan['facts'] if row['fact'] == 'eligibility')['status'], 'unknown')

    def test_missing_scope_candidates_can_start_public_catalog_discovery(self):
        plan = self.build(programs={}, task={'reference_year': 2027, 'target_year': 2028, 'unit_ids': ['u1', 'u2']})
        self.assertEqual([row['args']['unit_id'] for row in plan['next_actions']], ['u1', 'u2'])
        self.assertEqual({row['args']['kind'] for row in plan['next_actions']}, {'catalog'})

    def test_nonfinite_fees_missing_period_or_duration_remain_unknown(self):
        for fee in ({'amount': float('inf'), 'period': 'year'}, {'amount': 12000, 'period': None}):
            with self.subTest(fee=fee):
                plan = self.build({'p1': candidate(tuition=fee, duration_years=float('inf'))})
                self.assertEqual(next(row for row in plan['facts'] if row['fact'] == 'tuition')['status'], 'unknown')
                self.assertEqual(next(row for row in plan['facts'] if row['fact'] == 'duration_years')['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
