import copy
import unittest

from decision_engine import analyze, diversify, rank_stability, value_of_information
from grad_engine import EngineValidationError, LABELS, PROFILE_FIELDS, match_programs
from tests.test_engine import EVIDENCE, SUBJECTS


def program(identifier, unit_id, unit_name, province, annual, study_mode='全日制'):
    return {'id': identifier, 'unit_id': unit_id, 'unit_name': unit_name, 'year': 2027,
            'name': '计算机科学与技术', 'province': province, 'study_mode': study_mode,
            'subjects': copy.deepcopy(SUBJECTS), 'conditions': [], 'requires_review': False,
            'eligibility_evidence_complete': True, 'evidence': [copy.deepcopy(EVIDENCE)],
            'tuition': {'amount': annual, 'period': 'year'}, 'duration_years': 3}


def profile(**overrides):
    return {'education': 'bachelor', 'subjects': copy.deepcopy(SUBJECTS), **overrides}


PROGRAMS = [program('tj1', 'unit-a', '天津甲大学', '天津', 8000),
            program('tj2', 'unit-a', '天津甲大学', '天津', 9000),
            program('bj1', 'unit-b', '北京乙大学', '北京', 20000),
            program('sh1', 'unit-c', '上海丙大学', '上海', 25000)]


class ValueOfInformationTests(unittest.TestCase):
    def test_ranks_only_enumerable_unknown_conditions_and_measures_changes(self):
        result = value_of_information(PROGRAMS, profile(), top_k=2)
        self.assertEqual(result['version'], 'decision_v1')
        self.assertEqual(result['baseline_top_k'], ['tj1', 'tj2'])
        fields = [row['field'] for row in result['questions']]
        self.assertIn('provinces', fields)
        self.assertIn('study_mode', fields)
        self.assertTrue(set(fields) <= PROFILE_FIELDS)
        for row in result['questions']:
            self.assertIn(row['field'], LABELS)
            self.assertTrue(row['options'])
            self.assertGreaterEqual(row['impact']['priority'], 0)
            self.assertLessEqual(row['impact']['priority'], 1)
            self.assertTrue(row['explanation'])
        provinces = next(row for row in result['questions'] if row['field'] == 'provinces')
        self.assertGreater(provinces['impact']['changed_options'], 0)
        self.assertTrue(provinces['impact']['entered_ids'])
        self.assertTrue(provinces['impact']['left_ids'])
        self.assertIn('不是录取概率', result['note'])
        self.assertIn(result['next_question']['field'], PROFILE_FIELDS)
        self.assertGreater(result['next_question']['priority'], 0)

    def test_resolving_a_condition_that_changes_nothing_is_deprioritized(self):
        result = value_of_information(PROGRAMS, profile(education='unknown'), top_k=4)
        education = next(row for row in result['questions'] if row['field'] == 'education')
        self.assertEqual(education['impact']['changed_options'], 0)
        self.assertEqual(education['impact']['priority'], 0.0)
        self.assertIn('可以稍后再确认', education['explanation'])

    def test_region_questions_do_not_ask_the_same_missing_information_twice(self):
        unset = value_of_information(PROGRAMS, profile(), top_k=2)
        fields = [row['field'] for row in unset['questions']]
        self.assertIn('provinces', fields)
        self.assertNotIn('preferred_provinces', fields)
        preferred = value_of_information(PROGRAMS, profile(provinces=['天津']), top_k=2)
        fields = [row['field'] for row in preferred['questions']]
        self.assertNotIn('provinces', fields)
        self.assertIn('preferred_provinces', fields)

    def test_unknown_fee_limits_are_not_treated_as_free(self):
        result = value_of_information(PROGRAMS, profile(), top_k=2)
        annual = next(row for row in result['questions'] if row['field'] == 'max_annual_tuition')
        self.assertEqual(annual['options'], [8000, 9000, 20000, 25000])
        values = match_programs(PROGRAMS, profile())
        self.assertEqual(len(values['matches']), 4)

    def test_no_candidates_is_reported_instead_of_invented(self):
        result = value_of_information(PROGRAMS, profile(provinces=['不存在地区']), top_k=3)
        self.assertEqual(result['baseline_top_k'], [])
        self.assertEqual(result['questions'], [])
        self.assertIsNone(result['next_question'])
        self.assertIn('没有可比较候选', result['note'])

    def test_invalid_limits_are_rejected(self):
        for value in (0, 13, True, 2.5, '2'):
            with self.subTest(value=value), self.assertRaises(EngineValidationError):
                value_of_information(PROGRAMS, profile(), top_k=value)


class StabilityTests(unittest.TestCase):
    def test_explicit_scenarios_report_frequency_not_admission_odds(self):
        scenarios = [{'name': '只考虑天津', 'changes': {'provinces': ['天津']}},
                     {'name': '只考虑上海', 'changes': {'provinces': ['上海']}}]
        result = rank_stability(PROGRAMS, profile(), top_k=2, scenarios=scenarios)
        self.assertEqual(result['baseline_top_k'], ['tj1', 'tj2'])
        self.assertEqual(result['summary']['scenario_count'], 2)
        frequencies = {row['candidate_id']: row['top_k_frequency'] for row in result['stability']}
        self.assertLess(frequencies['tj1'], 1.0)
        self.assertLess(frequencies['sh1'], 1.0)
        self.assertGreater(result['summary']['volatile_count'], 0)
        self.assertIn('不是录取概率', result['note'])
        for row in result['stability']:
            self.assertGreaterEqual(row['top_k_frequency'], 0)
            self.assertLessEqual(row['top_k_frequency'], 1)
            self.assertLessEqual(row['rank_min'], row['rank_max'])

    def test_default_scenarios_are_derived_from_high_impact_unknowns(self):
        result = rank_stability(PROGRAMS, profile(), top_k=2)
        self.assertTrue(result['scenarios'])
        self.assertLessEqual(len(result['scenarios']), 8)
        for row in result['scenarios']:
            self.assertTrue(row['name'])
            self.assertTrue(row['changes'])

    def test_scenario_validation_keeps_user_conditions_only(self):
        for scenario in ({'name': 'x', 'changes': {}}, {'name': '', 'changes': {'provinces': ['天津']}},
                         {'name': 'x', 'changes': {'api_key': 'k'}}, {'changes': {'provinces': ['天津']}},
                         {'name': 'x', 'changes': {'provinces': ['天津']}, 'extra': 1}):
            with self.subTest(scenario=scenario), self.assertRaises(EngineValidationError):
                rank_stability(PROGRAMS, profile(), scenarios=[scenario])
        with self.assertRaises(EngineValidationError):
            rank_stability(PROGRAMS, profile(), scenarios=[{'name': 'x', 'changes': {'provinces': ['天津']}}] * 9)


class DiversifyTests(unittest.TestCase):
    def test_default_selection_spreads_across_schools(self):
        result = diversify(PROGRAMS, profile(), limit=2)
        self.assertEqual([row['candidate_id'] for row in result['selected']], ['tj1', 'bj1'])
        self.assertEqual(len({row['unit_id'] for row in result['selected']}), 2)
        self.assertIn('不改变资格', result['note'])

    def test_province_key_can_outrank_a_second_program_in_the_same_school(self):
        programs = [PROGRAMS[0], program('tj3', 'unit-d', '天津丁大学', '天津', 8500), PROGRAMS[2]]
        unit_key = diversify(programs, profile(), limit=2, key='unit_id')
        province_key = diversify(programs, profile(), limit=2, key='province')
        self.assertEqual([row['candidate_id'] for row in unit_key['selected']], ['tj1', 'tj3'])
        self.assertEqual([row['candidate_id'] for row in province_key['selected']], ['tj1', 'bj1'])
        for kwargs in ({'limit': 0}, {'limit': 13}, {'key': 'name'}, {'relevance_weight': -0.1}, {'relevance_weight': 1.1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(EngineValidationError):
                diversify(PROGRAMS, profile(), **kwargs)

    def test_pending_candidates_are_offered_only_as_labelled_tentative_spread(self):
        condition = [{'field': 'background', 'op': 'eq', 'value': '临床医学',
                      'evidence': copy.deepcopy(PROGRAMS[0]['evidence'][0])}]
        pending = [{**PROGRAMS[0], 'id': 'pending-a', 'conditions': copy.deepcopy(condition)},
                   {**PROGRAMS[2], 'id': 'pending-b', 'conditions': copy.deepcopy(condition)}]
        result = diversify(pending, profile(), limit=2)
        self.assertEqual(result['basis'], 'needs_confirmation')
        self.assertEqual([row['candidate_id'] for row in result['selected']], ['pending-a', 'pending-b'])
        self.assertTrue(all(row['status'] == 'unknown' for row in result['selected']))
        self.assertIn('不代表已通过资格核对', result['note'])

    def test_no_matches_returns_an_empty_explained_list(self):
        result = diversify(PROGRAMS, profile(provinces=['不存在地区']), limit=3)
        self.assertEqual(result['selected'], [])
        self.assertEqual(result['basis'], 'none')
        self.assertIn('没有可展示的候选', result['note'])


class AnalyzeTests(unittest.TestCase):
    def test_payload_is_deterministic_and_complete(self):
        first = analyze(PROGRAMS, profile(), top_k=3)
        second = analyze(PROGRAMS, profile(), top_k=3)
        self.assertEqual(first, second)
        self.assertEqual(first['version'], 'decision_v1')
        self.assertEqual(first['value_of_information']['top_k'], 3)
        self.assertEqual(first['stability']['top_k'], 3)
        self.assertEqual(first['diversified']['limit'], 3)
        self.assertIn('不输出录取概率', first['note'])

    def test_invalid_top_k_is_rejected(self):
        with self.assertRaises(EngineValidationError):
            analyze(PROGRAMS, profile(), top_k=0)


if __name__ == '__main__':
    unittest.main()
