import copy
import unittest

from grad_engine import EngineValidationError
from planning_engine import plan_routes, suggest_adjustments
from test_engine import SUBJECTS, profile, program, rule, subject


class RouteTests(unittest.TestCase):
    def test_routes_preserve_scope_alternatives_and_distinct_coverage(self):
        alternatives = [SUBJECTS[0], subject("201", 2, choice_group="lang"),
                        subject("204", 2, choice_group="lang"), *SUBJECTS[2:]]
        rows = [program("zero", code="085404", department="学院", direction="01", subjects=alternatives),
                program("zero-direction", code="085404", department="学院", direction="02"),
                program("one", unit_id="other", code="085404", department="学院", direction="01",
                        subjects=SUBJECTS[:3] + [subject("408", 4, "other")]),
                program("two", subjects=SUBJECTS[:2] + [subject("301", 3), subject("901", 4, "other")]),
                program("unknown", subjects=[subject("408", 4)])]
        result = plan_routes(rows, profile(max_subject_changes=0))
        zero, one, two = result["routes"]
        self.assertEqual(zero["coverage"], {"program_count": 2, "department_program_count": 1, "unit_count": 1})
        self.assertEqual((one["coverage"]["program_count"], two["coverage"]["program_count"]), (3, 4))
        self.assertEqual(one["added_ids"], ["one"])
        self.assertEqual(zero["unknown_subject_count"], 1)
        self.assertFalse(any(entry["program"]["id"] == "unknown" for entry in two["candidates"]))
        self.assertIn("reasons", one["candidates"][0])
        self.assertTrue(result["note"])

    def test_counts_and_id_deltas_are_not_truncated_to_the_twenty_displayed(self):
        rows = [program(str(index), code="085404", department="学院", direction=str(index)) for index in range(29)]
        rows.append(copy.deepcopy(rows[0]))
        result = plan_routes(rows, profile())
        zero = result["routes"][0]
        self.assertEqual(zero["coverage"]["program_count"], 29)
        self.assertEqual(len(zero["candidates"]), 20)
        self.assertEqual(len(zero["retained_ids"]), 29)
        self.assertEqual(zero["matched_count"], 29)

    def test_unknown_eligibility_stays_pending_and_incomplete_preparation_has_no_route(self):
        rows = [program("pending", eligibility_evidence_complete=False)]
        result = plan_routes(rows, profile())
        self.assertEqual(result["routes"][0]["needs_confirmation_count"], 1)
        self.assertEqual(result["routes"][0]["matched_count"], 0)
        result = plan_routes(rows, profile(subjects=[]))
        self.assertTrue(all(not route["candidates"] for route in result["routes"]))
        self.assertEqual(result["routes"][0]["unknown_subject_count"], 1)
        self.assertTrue(result["questions"])

    def test_routes_respect_other_hard_facts_and_reference_year(self):
        rows = [program("education", conditions=[rule("education", "eq", "master")]),
                program("province", province="北京"), program("old", year=2026)]
        result = plan_routes(rows, profile(provinces=["天津"]))
        self.assertTrue(all(route["coverage"]["program_count"] == 0 for route in result["routes"]))
        self.assertEqual(result["status"], "no_candidates")

    def test_inputs_and_nested_return_values_are_isolated(self):
        rows, values = [program()], profile()
        saved = copy.deepcopy((rows, values))
        result = plan_routes(rows, values)
        result["routes"][0]["candidates"][0]["program"]["name"] = "changed"
        self.assertEqual((rows, values), saved)
        self.assertNotEqual(result["routes"][1]["candidates"][0]["program"]["name"], "changed")


class AdjustmentTests(unittest.TestCase):
    def test_minimum_user_allowed_change_and_non_dominated_options(self):
        rows = [program("local-change", subjects=SUBJECTS[:3] + [subject("901", 4, "other")]),
                program("other-city", province="河南")]
        result = suggest_adjustments(rows, profile(provinces=["天津"], max_subject_changes=0),
                                     {"max_subject_changes": [0, 1, 2], "provinces": [["天津"], ["天津", "河南"]]})
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["suggestions"])
        self.assertTrue(all(item["cost"] == 1 for item in result["suggestions"]))
        self.assertEqual({tuple(item["changed_fields"]) for item in result["suggestions"]},
                         {("max_subject_changes",), ("provinces",)})
        self.assertTrue(all(item["feasible"] for item in result["suggestions"]))

    def test_goal_can_require_two_explicitly_allowed_changes(self):
        rows = [program("target", province="河南", subjects=SUBJECTS[:3] + [subject("901", 4, "other")])]
        result = suggest_adjustments(rows, profile(provinces=["天津"], max_subject_changes=0),
                                     {"max_subject_changes": [1], "provinces": [["天津", "河南"]]})
        self.assertEqual(len(result["suggestions"]), 1)
        self.assertEqual(result["suggestions"][0]["cost"], 2)
        self.assertEqual(result["suggestions"][0]["added_ids"], ["target"])

    def test_pending_one_field_option_cannot_hide_a_feasible_two_field_option(self):
        changed = SUBJECTS[:3] + [subject("901", 4, "other")]
        rows = [program("pending", subjects=changed, eligibility_evidence_complete=False),
                program("known", province="河南", subjects=changed)]
        result = suggest_adjustments(rows, profile(provinces=["天津"], max_subject_changes=0),
                                     {"max_subject_changes": [1], "provinces": [["天津", "河南"]]})
        self.assertEqual({(item["cost"], item["feasible"]) for item in result["suggestions"]}, {(1, False), (2, True)})

    def test_unknown_tuition_cannot_be_made_feasible_by_raising_the_budget(self):
        rows = [program("unknown", province="河南", tuition=None)]
        result = suggest_adjustments(rows, profile(provinces=["天津"], max_annual_tuition=10000),
                                     {"provinces": [["天津", "河南"]], "max_annual_tuition": [15000]})
        self.assertEqual(len(result["suggestions"]), 1)
        item = result["suggestions"][0]
        self.assertEqual(item["changed_fields"], ["provinces"])
        self.assertFalse(item["feasible"])
        self.assertEqual(item["needs_confirmation_count"], 1)
        self.assertIn("核验", item["explanation"])

    def test_no_new_candidates_is_no_improvement_and_hard_facts_are_immutable(self):
        rows = [program("blocked", conditions=[rule("education", "eq", "master")])]
        result = suggest_adjustments(rows, profile(), {"max_subject_changes": [0, 1, 2]})
        self.assertEqual(result["status"], "no_improvement")
        self.assertFalse(result["suggestions"])
        with self.assertRaises(EngineValidationError):
            suggest_adjustments(rows, profile(), {"education": ["master"]})

    def test_input_validation_and_combination_bound(self):
        invalid = [None, [], {"provinces": "天津"}, {"max_subject_changes": [True]},
                   {"max_subject_changes": [3]}, {"max_annual_tuition": [-1]}, {"provinces": [[]] * 129}]
        for allowed in invalid:
            with self.subTest(allowed=allowed), self.assertRaises(EngineValidationError):
                suggest_adjustments([], profile(), allowed)
        for goal in (True, 0, "1", 10001):
            with self.subTest(goal=goal), self.assertRaises(EngineValidationError):
                suggest_adjustments([], profile(), {}, goal)

    def test_goal_without_enough_candidates_is_explicit_and_inputs_unchanged(self):
        rows, values, allowed = [program("other", province="河南")], profile(provinces=["天津"]), {"provinces": [["天津", "河南"]]}
        saved = copy.deepcopy((rows, values, allowed))
        result = suggest_adjustments(rows, values, allowed, goal_min_candidates=2)
        self.assertEqual(result["status"], "no_solution")
        self.assertFalse(result["suggestions"])
        self.assertEqual((rows, values, allowed), saved)


if __name__ == "__main__":
    unittest.main()
