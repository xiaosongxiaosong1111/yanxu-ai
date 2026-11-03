import copy
import math
import random
import unittest

from grad_engine import (
    EngineValidationError, compare_score_lines, match_historical_rules,
    match_programs, sensitivity_analysis, validate_profile,
)
from json_reader import read_json


EVIDENCE = {"document_id": "fixture-catalog", "url": "https://example.edu.cn/catalog", "quote": "测试夹具的明示要求", "page": 1}


def subject(code, unit, scope="national", **extra):
    return {"code": code, "name": code, "scope": scope, "exam_unit": unit, **extra}


SUBJECTS = [subject("101", 1), subject("204", 2), subject("302", 3), subject("408", 4)]


def program(identifier="a", **overrides):
    return {
        "id": identifier, "unit_id": "fixture-unit", "unit_name": "测试夹具学校", "year": 2027,
        "name": "测试专业", "province": "天津", "study_mode": "全日制",
        "subjects": copy.deepcopy(SUBJECTS), "conditions": [], "requires_review": False,
        "eligibility_evidence_complete": True,
        "evidence": [copy.deepcopy(EVIDENCE)], "tuition": {"amount": 10000, "period": "year"},
        "duration_years": 3, **overrides,
    }


def profile(**overrides):
    return {"education": "bachelor", "background": "计算机科学与技术", "subjects": copy.deepcopy(SUBJECTS), **overrides}


def rule(field, op, value, **extra):
    return {"field": field, "op": op, "value": value, "evidence": copy.deepcopy(EVIDENCE), **extra}


def one(result):
    return (result["matches"] + result["needs_confirmation"] + result["excluded"])[0]


class ValidationTests(unittest.TestCase):
    def test_defaults_and_fresh_objects(self):
        before = profile()
        saved = copy.deepcopy(before)
        result = validate_profile(before)
        self.assertEqual((result["target_year"], result["reference_year"]), (2028, 2027))
        self.assertEqual(result["study_mode"], "unknown")
        self.assertEqual(result["background"], "计算机科学与技术")
        self.assertEqual(before, saved)
        self.assertIsNot(result["subjects"], before["subjects"])
        self.assertIsNot(validate_profile({})["subjects"], validate_profile({})["subjects"])

    def test_rejects_sensitive_unknown_and_bad_top_level(self):
        for value in (None, [], "profile", {"api_key": "secret"}, {"scores": {"total": 300}}):
            with self.subTest(value=value), self.assertRaises(EngineValidationError):
                validate_profile(value)

    def test_numeric_boolean_nan_and_out_of_range_are_rejected(self):
        for value in ({"max_annual_tuition": True}, {"work_years": -1}, {"max_total_tuition": math.inf},
                      {"preferences": {"cost": math.nan}}, {"max_subject_changes": 1.5},
                      {"target_year": 2028, "reference_year": 2029}, {"preferences": {"unknown": 1}}):
            with self.subTest(value=value), self.assertRaises(EngineValidationError):
                validate_profile(value)

    def test_subjects_require_scoped_identity_and_unambiguous_unit(self):
        for subjects in ([{"code": "408", "scope": "national", "exam_unit": 0}],
                         [{"code": "408", "scope": "national", "choice_group": "or"}],
                         [{"code": "408", "scope": "national", "api_key": "secret"}],
                         [{"code": "408", "scope": ""}],
                         [subject("408", 4, choice_group={"not": "a group"})], [None]):
            with self.subTest(subjects=subjects), self.assertRaises(EngineValidationError):
                validate_profile({"subjects": subjects})

    def test_public_domain_values(self):
        result = validate_profile({"education": "unknown", "study_mode": "非全日制", "max_annual_tuition": 0,
                                   "provinces": ["天津", "天津"], "subjects": [subject("日语", 2)],
                                   "preferences": {"cost": 0, "subjects": 5, "location": 1}})
        self.assertEqual(result["provinces"], ["天津"])
        self.assertEqual(result["subjects"][0]["exam_unit"], 2)
        for value in ({"education": "doctor"}, {"study_mode": "在职随便读"}, {"provinces": "天津"},
                      {"background": "a" * 121}, {"subjects": {}}, {"preferences": []}):
            with self.subTest(value=value), self.assertRaises(EngineValidationError):
                validate_profile(value)


class QualificationTests(unittest.TestCase):
    def test_explicit_match_conflict_and_unknown_are_separate(self):
        rows = [program("yes", conditions=[rule("education", "in", ["bachelor", "master"])]),
                program("no", conditions=[rule("work_years", "gte", 3)]),
                program("unknown", conditions=[rule("background", "eq", "临床医学")])]
        result = match_programs(rows, profile(work_years=1, background=None))
        self.assertEqual([x["program"]["id"] for x in result["matches"]], ["yes"])
        self.assertEqual([x["program"]["id"] for x in result["excluded"]], ["no"])
        self.assertEqual([x["program"]["id"] for x in result["needs_confirmation"]], ["unknown"])
        self.assertEqual(result["questions"][0]["field"], "background")
        self.assertTrue(one(result)["historical_reference"])

    def test_unparsed_or_missing_source_conditions_do_not_pass(self):
        for row in (program(requires_review=True), program(conditions=None), program(evidence=[]),
                    program(conditions=[{"field": "education", "op": "eq", "value": "bachelor"}], evidence=[])):
            with self.subTest(row=row):
                item = one(match_programs([row], profile()))
                self.assertEqual(item["status"], "unknown")
                self.assertTrue(item["missing"])

    def test_catalog_alone_never_proves_complete_eligibility(self):
        for complete in (False, None):
            row = program(eligibility_evidence_complete=complete)
            self.assertEqual(one(match_programs([row], profile()))["status"], "unknown")
        row = program()
        del row["eligibility_evidence_complete"]
        self.assertEqual(one(match_programs([row], profile()))["status"], "unknown")
        row["conditions"] = [rule("work_years", "gte", 3)]
        self.assertEqual(one(match_programs([row], profile(work_years=0)))["status"], "excluded")

    def test_all_and_any_rules_preserve_boolean_logic(self):
        any_rule = {"any": [rule("education", "eq", "master"), rule("work_years", "gte", 2)], "evidence": EVIDENCE}
        all_rule = {"all": [rule("education", "eq", "bachelor"), rule("work_years", "gte", 2)], "evidence": EVIDENCE}
        self.assertEqual(one(match_programs([program(conditions=[any_rule])], profile(work_years=3)))["status"], "matched")
        self.assertEqual(one(match_programs([program(conditions=[all_rule])], profile(work_years=1)))["status"], "excluded")
        result = match_programs([program(conditions=[any_rule])], profile(education="master"))
        self.assertEqual(one(result)["status"], "matched")
        self.assertFalse(result["questions"])

    def test_conditional_rules_only_apply_when_the_guard_applies(self):
        requirement = rule("work_years", "gte", 2, when=rule("education", "eq", "associate"))
        self.assertEqual(one(match_programs([program(conditions=[requirement])], profile(work_years=0)))["status"], "matched")
        self.assertEqual(one(match_programs([program(conditions=[requirement])], profile(education="associate", work_years=0)))["status"], "excluded")
        self.assertEqual(one(match_programs([program(conditions=[requirement])], profile(education="unknown", work_years=0)))["status"], "unknown")

    def test_conditional_rule_groups_do_not_drop_the_guard(self):
        requirement = {"all": [rule("work_years", "gte", 2)],
                       "when": rule("education", "eq", "associate"), "evidence": EVIDENCE}
        self.assertEqual(one(match_programs([program(conditions=[requirement])], profile(work_years=0)))["status"], "matched")
        self.assertEqual(one(match_programs([program(conditions=[requirement])], profile(education="associate", work_years=0)))["status"], "excluded")

    def test_malformed_source_values_are_unknown_instead_of_false_exclusions(self):
        for requirement in (rule("education", "gte", {}), rule("education", "eq", ["bachelor"]),
                            rule("background", "prefix_in", [None]), rule(["bad field"], "eq", "bachelor")):
            with self.subTest(requirement=requirement):
                self.assertEqual(one(match_programs([program(conditions=[requirement])], profile()))["status"], "unknown")

    def test_unknown_operators_or_unfillable_source_fields_are_reviewed(self):
        for condition in (rule("education", "do_magic", "bachelor"), rule("unparsed_certificate", "eq", True),
                          {"any": [], "evidence": EVIDENCE}, "not parsed"):
            with self.subTest(condition=condition):
                result = match_programs([program(conditions=[condition])], profile())
                self.assertEqual(one(result)["status"], "unknown")
                self.assertFalse(any(q["field"] == "unparsed_certificate" for q in result["questions"]))

    def test_unexecutable_rule_does_not_ask_an_unhelpful_profile_question(self):
        result = match_programs([program(conditions=[rule("education", "unsupported", "bachelor")])], profile(education="unknown"))
        self.assertEqual(one(result)["status"], "unknown")
        self.assertNotIn("education", [question["field"] for question in result["questions"]])

    def test_reference_year_and_all_candidate_rows_are_preserved(self):
        rows = [program(str(i)) for i in range(61)] + [program("old", year=2026)]
        result = match_programs(rows, profile())
        self.assertEqual(len(result["matches"]), 61)
        self.assertEqual(result["summary"]["input_count"], 62)
        self.assertEqual(result["summary"]["ignored_year_count"], 1)
        self.assertTrue(result["summary"]["historical_reference"])

    def test_questions_prioritize_affected_candidates_and_ignore_excluded(self):
        rows = [program("a", conditions=[rule("work_years", "gte", 1)]),
                program("b", conditions=[rule("work_years", "gte", 2), rule("education", "eq", "bachelor")]),
                program("x", province="北京", conditions=[rule("background", "eq", "医学")])]
        result = match_programs(rows, {"education": "unknown", "provinces": ["天津"], "subjects": SUBJECTS})
        self.assertEqual(result["questions"][0]["field"], "work_years")
        self.assertEqual(result["questions"][0]["affected_count"], 2)
        self.assertNotIn("background", [q["field"] for q in result["questions"]])


class SubjectTests(unittest.TestCase):
    def test_explicit_unsupported_or_disputed_source_exams_are_excluded_without_zero_change_or_rank(self):
        rows = [program('normal'), program('unsupported', exam_matching_supported=False),
                program('disputed', subject_identity_issues=[{'code': '301', 'name': '数学（二）'}]),
                program('coupled', subjects=[], exam_matching_supported=False,
                        exam_combinations=[{'km1': '101'}, {'km1': '101'}], requires_review=True)]
        saved = copy.deepcopy(rows)
        for mode in ('balanced', 'cost', 'subjects'):
            with self.subTest(mode=mode):
                result = match_programs(rows, profile(max_subject_changes=0, ranking_mode=mode))
                self.assertEqual([entry['program']['id'] for entry in result['matches']], ['normal'])
                self.assertEqual(result['needs_confirmation'], [])
                self.assertEqual(len(result['excluded']), 3)
                self.assertEqual(result['questions'], [])
                for entry in result['excluded']:
                    self.assertIsNone(entry['subject_changes']['count'])
                    self.assertTrue(entry['subject_changes']['uncertain'])
                    self.assertIsNone(entry['rank_score'])
                    self.assertIsNone(entry['ranking_basis']['value'])
                    self.assertTrue(any(reason['field'] == 'subjects' and reason['status'] == 'excluded'
                                        for reason in entry['reasons']))
        self.assertEqual(rows, saved)

    def test_shared_source_exam_guard_cannot_reenter_routes_adjustments_or_sensitivity(self):
        from planning_engine import plan_routes, suggest_adjustments
        rows = [program('normal'), program('unsupported', province='河南', exam_matching_supported=False),
                program('disputed', province='河南', subject_identity_issues=[{'code': '301', 'name': '数学（二）'}])]
        values = profile(provinces=['天津'], max_subject_changes=0)
        saved = copy.deepcopy((rows, values))
        routes = plan_routes(rows, values)
        self.assertEqual(routes['baseline']['candidate_ids'], ['normal'])
        self.assertTrue(all(route['candidate_ids'] == ['normal'] for route in routes['routes']))
        suggestions = suggest_adjustments(rows, values, {'provinces': [['天津', '河南']], 'max_subject_changes': [2]})
        self.assertEqual(suggestions['suggestions'], [])
        scenarios = sensitivity_analysis(rows, values, [{'name': '扩大地区与换科范围',
            'changes': {'provinces': ['天津', '河南'], 'max_subject_changes': 2, 'ranking_mode': 'cost'}}])
        self.assertEqual(scenarios['baseline']['candidate_ids'], ['normal'])
        self.assertEqual(scenarios['scenarios'][0]['order_after'], ['normal'])
        self.assertEqual(scenarios['scenarios'][0]['added'], [])
        self.assertEqual([entry['program']['id'] for entry in scenarios['scenarios'][0]['candidates']], ['normal'])
        self.assertEqual((rows, values), saved)

    def test_same_code_different_school_is_not_equivalent(self):
        desired = SUBJECTS[:3] + [subject("901", 4, "school-a")]
        prepared = SUBJECTS[:3] + [subject("901", 4, "school-b")]
        changes = one(match_programs([program(subjects=desired)], profile(subjects=prepared)))["subject_changes"]
        self.assertEqual(changes["count"], 1)
        self.assertEqual(changes["added"][0]["scope"], "school-a")
        self.assertEqual(changes["removed"][0]["scope"], "school-b")

    def test_alternatives_choose_the_minimum_change_per_exam_unit(self):
        alternatives = [SUBJECTS[0], subject("201", 2, choice_group="language"),
                        subject("204", 2, choice_group="language"), *SUBJECTS[2:]]
        changes = one(match_programs([program(subjects=alternatives)], profile()))["subject_changes"]
        self.assertEqual(changes["count"], 0)
        self.assertEqual(changes["program_units"], [1, 2, 3, 4])

    def test_two_and_three_exam_units_and_legacy_ordered_arrays(self):
        for count in (2, 3, 4):
            values = [{k: v for k, v in subject.items() if k != "exam_unit"} for subject in SUBJECTS[:count]]
            item = one(match_programs([program(subjects=values)], profile(subjects=values)))
            self.assertEqual(item["subject_changes"]["count"], 0)
            self.assertFalse(item["subject_changes"]["uncertain"])

    def test_missing_preparation_is_not_zero_changes(self):
        for prepared in ([], [SUBJECTS[0], SUBJECTS[3]]):
            item = one(match_programs([program()], profile(subjects=prepared)))
            self.assertIsNone(item["subject_changes"]["count"])
            self.assertTrue(item["subject_changes"]["uncertain"])
            self.assertTrue(any(q["field"] == "subjects" for q in match_programs([program()], profile(subjects=prepared))["questions"]))

    def test_ambiguous_or_unknown_catalog_combinations_require_review(self):
        invalid = [SUBJECTS + [subject("999", 4)],
                   [{"code": "201", "name": "英语一", "scope": "national", "choice_group": "lang"},
                    {"code": "204", "name": "英语二", "scope": "national", "choice_group": "lang"}],
                   [{"code": "408", "name": "科目", "scope": "unknown"}]]
        for subjects in invalid:
            with self.subTest(subjects=subjects):
                changes = one(match_programs([program(subjects=subjects)], profile()))["subject_changes"]
                self.assertTrue(changes["uncertain"])
                self.assertIsNone(changes["count"])

    def test_partial_catalog_is_not_treated_as_a_complete_exam_structure(self):
        partial = [subject("408", 4)]
        item = one(match_programs([program(subjects=partial)], profile(subjects=partial)))
        self.assertTrue(item["subject_changes"]["uncertain"])
        self.assertEqual(item["status"], "unknown")

    def test_max_changes_is_hard_only_for_a_known_distance(self):
        changed = SUBJECTS[:3] + [subject("901", 4, "school-a")]
        self.assertEqual(one(match_programs([program(subjects=changed)], profile(max_subject_changes=0)))["status"], "excluded")
        self.assertEqual(one(match_programs([program()], profile(subjects=[], max_subject_changes=0)))["status"], "unknown")


class RankingTests(unittest.TestCase):
    def test_ranking_modes_are_explicit_and_validated(self):
        self.assertEqual(validate_profile({})["ranking_mode"], "balanced")
        for mode in ("balanced", "subjects", "cost"):
            self.assertEqual(validate_profile({"ranking_mode": mode})["ranking_mode"], mode)
        for mode in (None, [], "odds"):
            with self.subTest(mode=mode), self.assertRaises(EngineValidationError):
                validate_profile({"ranking_mode": mode})

    def test_unknown_cost_does_not_hide_the_known_zero_change_candidate(self):
        rows = [program("changed", tuition=None, subjects=SUBJECTS[:3] + [subject("901", 4, "school-a")]),
                program("zero", tuition=None)]
        result = match_programs(rows, profile())
        self.assertEqual([entry["program"]["id"] for entry in result["matches"]], ["zero", "changed"])
        self.assertTrue(all(entry["rank_score"] is None for entry in result["matches"]))
        self.assertIn("subjects", result["matches"][0]["ranking_basis"]["fields"])
        self.assertTrue(result["summary"]["ranking_warning"])

    def test_explicit_cost_and_subject_modes_keep_unknown_cost_last(self):
        rows = [program("unchanged", tuition={"amount": 20000, "period": "year"}),
                program("cheap", tuition={"amount": 1000, "period": "year"},
                        subjects=SUBJECTS[:3] + [subject("901", 4, "school-a")]),
                program("unknown", tuition=None)]
        subject_result = match_programs(rows, profile(ranking_mode="subjects"))
        cost_result = match_programs(rows, profile(ranking_mode="cost"))
        self.assertEqual(subject_result["matches"][0]["program"]["id"], "unchanged")
        self.assertEqual([entry["program"]["id"] for entry in cost_result["matches"]], ["cheap", "unchanged", "unknown"])
        missing = cost_result["matches"][-1]
        self.assertIsNone(missing["rank_score"])
        self.assertIn("学费", missing["ranking_basis"]["explanation"])

    def test_region_weights_do_not_change_the_score_of_hard_filtered_candidates(self):
        rows = [program("a"), program("b", tuition={"amount": 5000, "period": "year"})]
        low = match_programs(rows, profile(provinces=["天津"], preferences={"location": 0}))
        high = match_programs(rows, profile(provinces=["天津"], preferences={"location": 5}))
        self.assertEqual([entry["rank_score"] for entry in low["matches"]],
                         [entry["rank_score"] for entry in high["matches"]])

    def test_annual_cost_only_is_a_separate_explicit_sorting_basis(self):
        rows = [program("higher", duration_years=None),
                program("lower", tuition={"amount": 8000, "period": "year"}, duration_years=None)]
        result = match_programs(rows, profile(ranking_mode="cost"))
        self.assertEqual([entry["program"]["id"] for entry in result["matches"]], ["lower", "higher"])
        self.assertTrue(all(entry["rank_score"] is None for entry in result["matches"]))
        self.assertEqual(result["matches"][0]["ranking_basis"]["method"], "annual_cost")

    def test_missing_requested_cost_sort_has_an_explicit_subject_fallback(self):
        result = match_programs([program(tuition=None)], profile(ranking_mode="cost"))
        basis = one(result)["ranking_basis"]
        self.assertEqual(basis["method"], "subjects_fallback")
        self.assertIn("回退", basis["explanation"])

    def test_preference_only_sensitivity_reports_reordering(self):
        rows = [program("subjects", tuition={"amount": 20000, "period": "year"}),
                program("cheap", tuition={"amount": 1000, "period": "year"},
                        subjects=SUBJECTS[:3] + [subject("901", 4, "school-a")])]
        result = sensitivity_analysis(rows, profile(preferences={"subjects": 5, "cost": 0}),
                                      [{"name": "费用优先", "changes": {"preferences": {"subjects": 0, "cost": 5}}}])
        scenario = result["scenarios"][0]
        self.assertFalse(scenario["added"])
        self.assertFalse(scenario["removed"])
        self.assertNotEqual(scenario["order_before"], scenario["order_after"])
        self.assertEqual(len(scenario["rank_changes"]), 2)

    def test_real_zhengzhou_zero_change_program_precedes_changed_programs(self):
        from pathlib import Path
        rows = read_json(Path(__file__).resolve().parents[1] / "data/admissions.json")["programs"]
        target = next(row for row in rows if row["unit_id"] == "368317" and
                      {subject["code"] for subject in row["subjects"]} == {"101", "201", "301", "408"})
        prepared = [{key: value for key, value in item.items() if key in {"code", "name", "scope", "exam_unit", "choice_group"}}
                    for item in target["subjects"]]
        result = match_programs(rows, profile(subjects=prepared))
        candidates = result["matches"] + result["needs_confirmation"]
        self.assertEqual(candidates[0]["subject_changes"]["count"], 0)
        self.assertLess(next(index for index, item in enumerate(candidates) if item["program"]["id"] == target["id"]), 20)

    def test_annual_and_total_tuition_are_independent_hard_limits(self):
        rows = [program("annual", tuition={"amount": 20000, "period": "year"}, duration_years=3),
                program("total", tuition={"amount": 40000, "period": "total"}),
                program("missing", tuition=None)]
        result = match_programs(rows, profile(max_annual_tuition=15000, max_total_tuition=50000))
        self.assertEqual([x["program"]["id"] for x in result["excluded"]], ["annual"])
        self.assertEqual({x["program"]["id"] for x in result["needs_confirmation"]}, {"total", "missing"})

    def test_budget_explanation_retains_its_source(self):
        item = one(match_programs([program()], profile(max_annual_tuition=15000)))
        budget = next(reason for reason in item["reasons"] if reason["field"] == "max_annual_tuition")
        self.assertEqual(budget["evidence"], [EVIDENCE])

    def test_unknown_duration_is_not_invented_total_cost(self):
        item = one(match_programs([program(duration_years=None)], profile(max_total_tuition=40000)))
        self.assertEqual(item["status"], "unknown")
        self.assertIsNone(item["components"]["cost"])

    def test_location_and_learning_mode_cannot_be_compensated_by_weights(self):
        result = match_programs([program("city", province="北京"), program("mode", study_mode="非全日制")],
                                profile(provinces=["天津"], study_mode="全日制", preferences={"cost": 5, "subjects": 5, "location": 0}))
        self.assertEqual(len(result["excluded"]), 2)

    def test_known_cost_and_subject_tradeoffs_form_a_pareto_front(self):
        rows = [program("dominated", tuition={"amount": 15000, "period": "year"}),
                program("best", tuition={"amount": 8000, "period": "year"}),
                program("cheap_change", tuition={"amount": 5000, "period": "year"},
                        subjects=SUBJECTS[:3] + [subject("901", 4, "school-a")])]
        result = match_programs(rows, profile(preferences={"cost": 3, "subjects": 3, "location": 0}))
        frontier = {x["program"]["id"] for x in result["matches"] if x["pareto"]}
        self.assertEqual(frontier, {"best", "cheap_change"})
        self.assertGreater(next(x for x in result["matches"] if x["program"]["id"] == "best")["rank_score"],
                           next(x for x in result["matches"] if x["program"]["id"] == "dominated")["rank_score"])

    def test_missing_data_has_no_fake_zero_cost_or_precise_score(self):
        result = match_programs([program("known"), program("missing", tuition=None)], profile())
        item = next(x for x in result["matches"] if x["program"]["id"] == "missing")
        self.assertIsNone(item["components"]["cost"])
        self.assertIsNone(item["rank_score"])
        self.assertIsNone(item["pareto"])
        self.assertEqual(len(item["rank_interval"]), 2)
        self.assertNotIn("admission_probability", item)

    def test_equal_vectors_all_remain_pareto_and_zero_weights_do_not_divide(self):
        result = match_programs([program("a"), program("b")], profile())
        self.assertTrue(all(item["pareto"] for item in result["matches"]))
        result = match_programs([program()], profile(preferences={"cost": 0, "subjects": 0, "location": 0}))
        self.assertIsNone(one(result)["rank_score"])

    def test_skyline_matches_independent_pairwise_definition(self):
        from grad_engine import _frontier
        rng = random.Random(8127)
        for dimensions in (1, 2, 3):
            for _ in range(15):
                vectors = [tuple(rng.randrange(8) for _ in range(dimensions)) for _ in range(35)]
                expected = {point + (0,) * (3 - dimensions) for point in vectors
                            if not any(all(a >= b for a, b in zip(other, point)) and
                                       any(a > b for a, b in zip(other, point)) for other in vectors)}
                self.assertEqual(_frontier(vectors), expected)

    def test_sensitivity_is_explicit_and_does_not_modify_the_original(self):
        rows = [program("local"), program("other", province="北京")]
        before = profile(provinces=["天津"])
        saved = copy.deepcopy(before)
        result = sensitivity_analysis(rows, before, [{"name": "扩大地区", "changes": {"provinces": ["天津", "北京"]}}])
        self.assertEqual(result["scenarios"][0]["added"], ["other"])
        self.assertEqual(before, saved)
        with self.assertRaises(EngineValidationError):
            sensitivity_analysis(rows, before, [{"name": "改学历", "changes": {"education": "master"}}])

    def test_input_programs_and_rules_are_never_mutated(self):
        rows = [program("a"), program("b", tuition=None)]
        saved = copy.deepcopy(rows)
        result = match_programs(rows, profile())
        result["matches"][0]["program"]["name"] = "changed returned value"
        self.assertEqual(rows, saved)


class HistoryTests(unittest.TestCase):
    def test_score_lines_keep_year_kind_missing_values_and_scope(self):
        lines = [{"unit_id": "a", "year": 2026, "kind": "program", "total": 310, "business2": None, "plan_type": "ordinary"},
                 {"unit_id": "a", "year": 2025, "kind": "admitted_minimum", "total": 300, "plan_type": "special"},
                 {"unit_id": "b", "year": 2026, "kind": "national", "total": 280}]
        result = compare_score_lines(lines, ["a"])
        self.assertEqual({row["year"] for row in result}, {2025, 2026})
        self.assertEqual({row["kind"] for row in result}, {"program", "admitted_minimum"})
        self.assertIsNone(next(row for row in result if row["year"] == 2026)["business2"])
        result[0]["total"] = -1
        self.assertNotEqual(lines[0]["total"], -1)

    def test_historical_rules_are_year_specific_and_source_backed(self):
        rows = [{"id": "ok", "year": 2026, "requirements": [rule("total", "gte", 300)], "evidence": EVIDENCE},
                {"id": "no", "year": 2026, "requirements": [rule("total", "gte", 400)], "evidence": EVIDENCE},
                {"id": "unknown", "year": 2026, "requirements": [rule("business2", "gte", 100)], "evidence": EVIDENCE},
                {"id": "old", "year": 2025, "requirements": [], "evidence": EVIDENCE}]
        result = match_historical_rules(rows, {"total": 320}, 2026)
        self.assertEqual(result["matched"][0]["rule"]["id"], "ok")
        self.assertEqual(result["excluded"][0]["rule"]["id"], "no")
        self.assertEqual(result["needs_confirmation"][0]["rule"]["id"], "unknown")
        self.assertTrue(result["historical_reference"])

    def test_historical_notes_without_review_or_requirements_stay_unknown(self):
        for row in ({"year": 2026, "requirements": [], "notes": "另有专业限制", "evidence": EVIDENCE},
                    {"year": 2026, "requirements": [], "requires_review": True, "evidence": EVIDENCE},
                    {"year": 2026, "evidence": EVIDENCE}):
            self.assertEqual(len(match_historical_rules([row], {}, 2026)["needs_confirmation"]), 1)

    def test_prefix_and_subject_rules_use_complete_identity(self):
        rows = [{"year": 2026, "requirements": [rule("first_choice_program_code", "prefix_in", ["0854"]),
                                                    rule("subjects", "subjects_match", SUBJECTS)], "evidence": EVIDENCE}]
        result = match_historical_rules(rows, {"first_choice_program_code": "085404", "subjects": SUBJECTS}, 2026)
        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(len(match_historical_rules(rows, {"first_choice_program_code": "081200", "subjects": SUBJECTS}, 2026)["excluded"]), 1)

    def test_subject_equality_compares_identity_not_display_names_or_extra_evidence(self):
        source_subjects = [{**subject, "name": "官方完整名称", "evidence": EVIDENCE} for subject in SUBJECTS]
        rows = [{"year": 2026, "requirements": [rule("subjects", "eq", source_subjects)], "evidence": EVIDENCE}]
        result = match_historical_rules(rows, {"subjects": SUBJECTS}, 2026)
        self.assertEqual(len(result["matched"]), 1)

    def test_history_rejects_sensitive_facts_and_invalid_year(self):
        for facts, year in (({"api_key": "secret"}, 2026), ({"total": math.nan}, 2026), ({}, True),
                            ({"education": []}, 2026), ({"background": {}}, 2026),
                            ({"first_choice_program_code": True}, 2026), ({"total": 600}, 2026)):
            with self.subTest(facts=facts, year=year), self.assertRaises(EngineValidationError):
                match_historical_rules([], facts, year)


if __name__ == "__main__":
    unittest.main()
