"""N2 regressions: distinct intent conditions and full-range candidate selection."""
from copy import deepcopy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from grad_engine import EngineValidationError, match_programs, validate_profile
from planning_engine import plan_routes
from research_service import ResearchService
from research_store import ResearchError
from research_tools import ResearchTools
from service import ServiceError
from tests.test_engine import EVIDENCE, SUBJECTS, program, profile, rule, subject
from tests.test_research_service import FakeAdmission, FakeAI, FakeSettings, wait_done
from tests.test_research_workflow import FakeKnowledge


def one(result):
    return (result["matches"] + result["needs_confirmation"] + result["excluded"])[0]


def mandatory_408(**overrides):
    return program(subjects=deepcopy(SUBJECTS), **overrides)


def alternative_408_or_901(identifier="alternative", **overrides):
    return program(identifier, subjects=SUBJECTS[:3] + [
        subject("408", 4, choice_group="or"),
        subject("901", 4, "school-a", choice_group="or"),
    ], **overrides)


def prepared_408():
    return SUBJECTS[:3] + [subject("408", 4)]


class IntentConditionTests(unittest.TestCase):
    def test_hard_province_excludes_but_soft_preference_only_reorders(self):
        rows = [program("tj", province="天津"), program("bj", province="北京")]
        hard = match_programs(rows, profile(provinces=["天津"]))
        self.assertEqual([entry["program"]["id"] for entry in hard["matches"]], ["tj"])
        self.assertEqual([entry["program"]["id"] for entry in hard["excluded"]], ["bj"])

        soft = match_programs(rows, profile(preferred_provinces=["天津"]))
        self.assertEqual(soft["excluded"], [])
        self.assertEqual([entry["program"]["id"] for entry in soft["matches"]], ["tj", "bj"])
        self.assertTrue(all(entry["status"] == "matched" for entry in soft["matches"]))
        self.assertEqual(soft["excluded_count"] if "excluded_count" in soft else len(soft["excluded"]), 0)

    def test_new_profile_fields_are_validated_deduplicated_and_isolated(self):
        value = {"preferred_provinces": ["天津", "天津"],
                 "excluded_subjects": [{"code": "408", "scope": "national"},
                                       {"code": "408", "scope": "national"}]}
        before = deepcopy(value)
        result = validate_profile(value)
        self.assertEqual(result["preferred_provinces"], ["天津"])
        self.assertEqual(result["excluded_subjects"], [{"code": "408", "scope": "national"}])
        self.assertEqual(result["provinces"], [])
        self.assertEqual(value, before)
        for bad in ({"preferred_provinces": "天津"}, {"excluded_subjects": ["408"]},
                    {"excluded_subjects": [{"scope": "national"}]},
                    {"excluded_subjects": [{"code": "408", "exam_unit": 4}]}):
            with self.subTest(bad=bad), self.assertRaises(EngineValidationError):
                validate_profile(bad)

    def test_exclusion_is_independent_of_missing_preparation(self):
        result = match_programs([mandatory_408()], profile(
            subjects=[], excluded_subjects=[{"code": "408", "scope": "national"}]))
        item = one(result)
        self.assertEqual(item["status"], "excluded")
        self.assertTrue(any(reason["field"] == "excluded_subjects" and reason["status"] == "excluded"
                            for reason in item["reasons"]))
        self.assertIsNone(item["subject_changes"]["count"])
        self.assertEqual(result["questions"], [])

    def test_legal_or_alternative_is_kept_and_recomputed_without_the_forbidden_subject(self):
        row = alternative_408_or_901()
        exclusion = [{"code": "408", "scope": "national"}]
        forbidden_prepared = match_programs([row], profile(
            subjects=prepared_408(), excluded_subjects=exclusion, max_subject_changes=0))
        item = one(forbidden_prepared)
        self.assertEqual(item["subject_changes"]["count"], 1)
        self.assertEqual(item["subject_changes"]["added"][0]["code"], "901")
        self.assertEqual(item["subject_changes"]["removed"][0]["code"], "408")
        self.assertEqual(item["status"], "excluded")

        legal_prepared = match_programs([row], profile(
            subjects=SUBJECTS[:3] + [subject("901", 4, "school-a")], excluded_subjects=exclusion))
        item = one(legal_prepared)
        self.assertEqual(item["subject_changes"]["count"], 0)
        self.assertEqual(item["status"], "matched")

    def test_prepared_subject_and_exam_willingness_are_separate(self):
        row = program("other-exam", subjects=SUBJECTS[:3] + [subject("901", 4, "school-a")])
        result = match_programs([row], profile(
            subjects=prepared_408(), excluded_subjects=[{"code": "408", "scope": "national"}]))
        item = one(result)
        self.assertEqual(item["status"], "matched")
        self.assertEqual(item["subject_changes"]["count"], 1)
        self.assertFalse(any(reason["field"] == "subjects" and reason["status"] == "excluded"
                             for reason in item["reasons"]))
        self.assertIn("other-exam", plan_routes([row], profile(
            subjects=prepared_408(), excluded_subjects=[{"code": "408", "scope": "national"}],
            max_subject_changes=1))["routes"][1]["candidate_ids"])

    def test_same_code_in_another_scope_is_not_the_forbidden_subject(self):
        school_408 = program("school-408", subjects=SUBJECTS[:3] + [subject("408", 4, "school-a")])
        exclusion = [{"code": "408", "scope": "national"}]
        item = one(match_programs([school_408], profile(
            subjects=SUBJECTS[:3] + [subject("408", 4, "school-a")], excluded_subjects=exclusion)))
        self.assertEqual(item["status"], "matched")
        self.assertFalse(any(reason["field"] == "excluded_subjects" and reason["status"] == "excluded"
                             for reason in item["reasons"]))

        explicit = match_programs([school_408], profile(
            subjects=SUBJECTS[:3] + [subject("408", 4, "school-a")],
            excluded_subjects=[{"code": "408", "scope": "school-a"}]))
        self.assertEqual(one(explicit)["status"], "excluded")

    def test_unknown_subject_scope_stays_pending_instead_of_matching(self):
        unknown = program("unknown-scope", subjects=SUBJECTS[:3] + [
            {"code": "408", "name": "计算机学科专业基础", "scope": "待核验", "exam_unit": 4}])
        item = one(match_programs([unknown], profile(
            subjects=prepared_408(), excluded_subjects=[{"code": "408", "scope": "national"}])))
        self.assertEqual(item["status"], "unknown")
        self.assertIsNone(item["subject_changes"]["count"])

    def test_unproven_alternative_keeps_the_direction_pending(self):
        row = program("unknown-alternative", subjects=SUBJECTS[:3] + [
            subject("408", 4, choice_group="or"),
            subject("901", 4, "待核验", choice_group="or")])
        item = one(match_programs([row], profile(
            subjects=prepared_408(), excluded_subjects=[{"code": "408", "scope": "national"}])))
        self.assertEqual(item["status"], "unknown")
        self.assertIsNone(item["subject_changes"]["count"])
        self.assertFalse(any(reason["field"] == "subjects" and reason["status"] == "matched"
                             for reason in item["reasons"]))

    def test_malformed_or_mixed_choice_groups_are_not_washed_by_exclusion(self):
        prepared = SUBJECTS[:3] + [subject("901", 4, "school-a")]
        exclusion = [{"code": "408", "scope": "national"}]
        rows = [
            program("no-group", subjects=SUBJECTS[:3] + [subject("408", 4), subject("901", 4, "school-a")]),
            program("mixed-group", subjects=SUBJECTS[:3] + [subject("408", 4, choice_group="a"),
                                                           subject("901", 4, "school-a", choice_group="b")]),
            alternative_408_or_901("legal-or"),
        ]
        result = match_programs(rows, profile(subjects=prepared, excluded_subjects=exclusion))
        entries = {entry["program"]["id"]: entry for kind in ("matches", "needs_confirmation", "excluded")
                   for entry in result[kind]}
        for identifier in ("no-group", "mixed-group"):
            with self.subTest(identifier=identifier):
                item = entries[identifier]
                self.assertEqual(item["status"], "unknown")
                self.assertIsNone(item["subject_changes"]["count"])
                self.assertFalse(any(reason["field"] == "subjects" and reason["status"] == "matched"
                                     for reason in item["reasons"]))
        legal = entries["legal-or"]
        self.assertEqual(legal["status"], "matched")
        self.assertEqual(legal["subject_changes"]["count"], 0)

    def test_definite_conflict_survives_an_unrelated_unknown_unit(self):
        row = program("mixed", subjects=[
            subject("101", 1), subject("201", 2),
            {"code": "301", "name": "数学一", "scope": "待核验", "exam_unit": 3},
            subject("408", 4)])
        item = one(match_programs([row], profile(
            subjects=deepcopy(SUBJECTS), excluded_subjects=[{"code": "408", "scope": "national"}])))
        self.assertEqual(item["status"], "excluded")
        self.assertTrue(any(reason["field"] == "excluded_subjects" and reason["status"] == "excluded"
                            for reason in item["reasons"]))
        self.assertIsNone(item["subject_changes"]["count"])

    def test_bare_self_proposed_code_without_scope_stays_pending(self):
        row = program("self", subjects=SUBJECTS[:3] + [subject("901", 4, "school-a")])
        prepared = SUBJECTS[:3] + [subject("901", 4, "school-a")]
        bare = one(match_programs([row], profile(
            subjects=prepared, excluded_subjects=[{"code": "901"}])))
        self.assertEqual(bare["status"], "unknown")
        self.assertIsNone(bare["subject_changes"]["count"])
        other = one(match_programs([row], profile(
            subjects=prepared, excluded_subjects=[{"code": "901", "scope": "school-b"}])))
        self.assertEqual(other["status"], "matched")
        self.assertEqual(other["subject_changes"]["count"], 0)
        same = one(match_programs([row], profile(
            subjects=prepared, excluded_subjects=[{"code": "901", "scope": "school-a"}])))
        self.assertEqual(same["status"], "excluded")

    def test_explicit_unknown_exclusion_scope_is_rejected(self):
        for scope in ("unknown", "pending", "待核验"):
            with self.subTest(scope=scope), self.assertRaises(EngineValidationError):
                validate_profile({"excluded_subjects": [{"code": "901", "scope": scope}]})
        self.assertEqual(validate_profile({"excluded_subjects": [{"code": "408"}]})["excluded_subjects"],
                         [{"code": "408"}])


class SoftPreferenceRankingTests(unittest.TestCase):
    def rows(self):
        return [program("tj", province="天津", tuition={"amount": 30000, "period": "year"}),
                program("bj", province="北京", tuition={"amount": 8000, "period": "year"})]

    def test_soft_preference_never_overrides_cost_or_subject_sorting(self):
        cost = match_programs(self.rows(), profile(preferred_provinces=["天津"], ranking_mode="cost"))
        self.assertEqual([entry["program"]["id"] for entry in cost["matches"]], ["bj", "tj"])
        changed = program("tj-change", province="天津",
                          subjects=SUBJECTS[:3] + [subject("901", 4, "school-a")])
        zero = program("bj-zero", province="北京")
        subjects = match_programs([changed, zero], profile(
            preferred_provinces=["天津"], ranking_mode="subjects"))
        self.assertEqual([entry["program"]["id"] for entry in subjects["matches"]], ["bj-zero", "tj-change"])

    def test_zero_location_weight_does_not_reorder_balanced(self):
        result = match_programs(self.rows(), profile(
            preferred_provinces=["天津"], preferences={"cost": 5, "subjects": 0, "location": 0}))
        self.assertEqual([entry["program"]["id"] for entry in result["matches"]], ["bj", "tj"])
        self.assertGreater(result["matches"][0]["rank_score"], result["matches"][1]["rank_score"])

    def test_positive_location_weight_is_used_in_balanced_score(self):
        result = match_programs(self.rows(), profile(
            preferred_provinces=["天津"], preferences={"cost": 0, "subjects": 0, "location": 5}))
        self.assertEqual([entry["program"]["id"] for entry in result["matches"]], ["tj", "bj"])
        self.assertEqual(result["matches"][0]["rank_score"], 100)
        self.assertEqual(result["matches"][1]["rank_score"], 0)


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.data.mkdir()

    def tools(self, query, profile_value=None, confirmed_fields=()):
        task = {"query": query, "follow_ups": [], "profile": validate_profile(profile_value or {}),
                "unit_ids": [], "reference_year": 2027, "target_year": 2028,
                "confirmed_fields": list(confirmed_fields)}
        return ResearchTools(FakeAdmission(), FakeSettings(), task, self.data,
                             knowledge_store=FakeKnowledge(), network=Mock())

    def test_exclusion_proposal_needs_a_quote_and_does_not_change_prepared_subjects(self):
        tools = self.tools("我想考计算机，不考408")
        before = deepcopy(tools.task["profile"])
        proposal = tools.run("propose_profile", {
            "updates": {"excluded_subjects": [{"code": "408", "scope": "national"}]},
            "evidence": [{"field": "excluded_subjects", "quote": "不考408"}]})
        self.assertTrue(proposal["requires_confirmation"])
        self.assertEqual(proposal["updates"], {"excluded_subjects": [{"code": "408", "scope": "national"}]})
        self.assertEqual(proposal["profile"]["subjects"], [])
        self.assertEqual(proposal["profile"]["excluded_subjects"],
                         [{"code": "408", "scope": "national"}])
        self.assertEqual(tools.task["profile"], before)
        self.assertEqual(proposal["condition_types"], {"excluded_subjects": "excluded"})
        with self.assertRaises(ResearchError):
            tools.propose_profile({"updates": {"excluded_subjects": [{"code": "408"}]}, "evidence": []})

    def test_hard_and_soft_province_proposals_are_distinct_and_each_needs_a_quote(self):
        tools = self.tools("只去天津，但优先天津")
        proposal = tools.run("propose_profile", {
            "updates": {"provinces": ["天津"], "preferred_provinces": ["天津"]},
            "evidence": [{"field": "provinces", "quote": "只去天津"},
                         {"field": "preferred_provinces", "quote": "优先天津"}]})
        self.assertEqual(proposal["profile"]["provinces"], ["天津"])
        self.assertEqual(proposal["profile"]["preferred_provinces"], ["天津"])
        self.assertEqual(proposal["condition_types"],
                         {"provinces": "hard", "preferred_provinces": "soft"})
        with self.assertRaises(ResearchError):
            tools.propose_profile({"updates": {"preferred_provinces": ["天津"]}, "evidence": []})

    def test_conflicting_update_is_shown_for_clarification_not_silently_replaced(self):
        tools = self.tools("我现在想改成北京", {"provinces": ["天津"]}, confirmed_fields=["provinces"])
        proposal = tools.run("propose_profile", {
            "updates": {"provinces": ["北京"]},
            "evidence": [{"field": "provinces", "quote": "改成北京"}]})
        self.assertTrue(proposal["requires_confirmation"])
        self.assertEqual(proposal["conflicts"][0]["field"], "provinces")
        self.assertEqual(proposal["conflicts"][0]["current"], ["天津"])
        self.assertEqual(proposal["conflicts"][0]["proposed"], ["北京"])
        self.assertEqual(tools.task["profile"]["provinces"], ["天津"])

    def test_service_applies_confirmed_exclusion_and_reexcludes_candidates(self):
        ai = FakeAI([
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "f", "name": "find_candidates", "arguments": {"query": "计算机", "unit_id": "123"}}]},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "p", "name": "propose_profile", "arguments": {
                    "updates": {"excluded_subjects": [{"code": "408", "scope": "national"}]},
                    "evidence": [{"field": "excluded_subjects", "quote": "不考408"}]}}]},
            {"role": "assistant", "content": "请确认。", "tool_calls": []},
        ])
        service = ResearchService(self.data / "service", FakeAdmission(), FakeSettings(), ai)
        self.addCleanup(service.close)
        task = wait_done(service, service.create({"query": "我想考计算机，不考408"})["id"])
        self.assertEqual(task["status"], "awaiting_input")
        self.assertEqual(task["profile"]["excluded_subjects"], [])
        self.assertEqual(task["profile"]["subjects"], [])
        while task["id"] in service._controls:
            threading.Event().wait(.01)
        ai.messages = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "f2", "name": "find_candidates", "arguments": {"query": "计算机", "unit_id": "123"}}]},
            {"role": "assistant", "content": "已按确认条件重新比较。", "tool_calls": []},
        ]
        continued = service.continue_research({
            "research_id": task["id"], "profile": task["report"]["profile_proposal"]["updates"]})
        done = wait_done(service, continued["id"])
        self.assertEqual(done["profile"]["excluded_subjects"], [{"code": "408", "scope": "national"}])
        self.assertEqual(done["profile"]["subjects"], [])
        self.assertEqual(done["report"]["candidates"], [])
        selection = done["report"]["coverage"]["selection"]
        self.assertEqual(selection["hard_excluded_count"], 1)
        self.assertEqual(selection["selected_count"], 0)
        self.assertEqual(done["report"]["algorithm"]["matching"]["summary"]["input_count"], 0)

    def test_explicit_unit_selection_still_caps_at_eight(self):
        service = ResearchService(self.data / "scope", FakeAdmission(), FakeSettings(enabled=False))
        self.addCleanup(service.close)
        with self.assertRaises(ResearchError) as error:
            service.create({"query": "计算机", "unit_ids": [f"u{i}" for i in range(1, 10)]})
        self.assertEqual(error.exception.code, "invalid_scope")


class PagedAdmission:
    """Bounded synthetic publication that records every page request."""

    def __init__(self, units, rows, total_override=None, on_page=None, revisions=None,
                 fail_on_page=None, fail_with=None):
        self.units = deepcopy(units)
        self.rows = deepcopy(rows)
        self.total_override = total_override or {}
        self.on_page = on_page
        self.revisions = list(revisions or [])
        self.fail_on_page = fail_on_page
        self.fail_with = fail_with
        self.requests = []

    def catalog_bootstrap(self):
        return {"units": deepcopy(self.units), "revision": "fixture-r1",
                "meta": {"reference_year": 2027, "target_year": 2028},
                "coverage": {"catalog_complete": False}}

    def catalog_programs(self, unit_id=None, year=None, q="", offset=0, limit=20, revision=None):
        self.requests.append({"unit_id": unit_id, "year": year, "q": q,
                              "offset": offset, "limit": limit, "revision": revision})
        if self.on_page:
            self.on_page(len(self.requests))
        if self.fail_on_page and len(self.requests) == self.fail_on_page:
            raise self.fail_with
        rows = [row for row in self.rows
                if (unit_id is None or row.get("unit_id") == unit_id)
                and (year is None or row.get("year") == year)
                and (not q or q.casefold() in " ".join(
                    [str(row.get(key, "")) for key in ("unit_name", "name", "code", "department", "direction")]
                    + [f"{item.get('code', '')} {item.get('name', '')}" for item in row.get("subjects", [])]).casefold())]
        total = self.total_override.get(unit_id if unit_id is not None else "", len(rows))
        index = len(self.requests) - 1
        revision = self.revisions[index] if self.revisions and index < len(self.revisions) else "fixture-r1"
        return {"programs": deepcopy(rows[offset:offset + limit]), "total": total,
                "revision": revision}


def candidate_row(identifier, **overrides):
    overrides.setdefault("name", "计算机技术")
    return program(identifier, **overrides)


class CandidateSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.data.mkdir()

    def tools(self, admission, profile_value=None, unit_ids=(), query="计算机", stop_event=None,
              reference_year=2027, deadline=None):
        task = {"query": query, "follow_ups": [], "profile": validate_profile(profile_value or {}),
                "unit_ids": list(unit_ids), "reference_year": reference_year,
                "target_year": reference_year + 1, "confirmed_fields": []}
        return ResearchTools(admission, FakeSettings(), task, self.data,
                             knowledge_store=FakeKnowledge(), network=Mock(), stop_event=stop_event,
                             deadline=deadline)

    def unit(self, identifier, province="天津", program_count=1):
        return {"id": identifier, "name": f"学校{identifier}", "province": province,
                "official_url": f"https://{identifier}.edu.cn", "program_count": program_count}

    def test_single_school_thirteenth_direction_survives_full_hard_filter_scan(self):
        units = [self.unit("u1", program_count=13)]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1", province="北京") for i in range(1, 13)]
        rows.append(candidate_row("p13", unit_id="u1", unit_name="学校u1", province="天津"))
        admission = PagedAdmission(units, rows)
        tools = self.tools(admission, {"provinces": ["天津"], "subjects": deepcopy(SUBJECTS)})
        result = tools.find_candidates({"query": "计算机"})
        self.assertEqual([candidate["id"] for candidate in result["candidates"]], ["p13"])
        self.assertEqual(result["total_in_query"], 13)
        self.assertEqual(result["scanned_count"], 13)
        self.assertEqual(result["hard_excluded_count"], 12)
        self.assertEqual(result["pending_count"], 0)
        self.assertEqual(result["selected_count"], 1)
        self.assertTrue(result["scan_complete"])
        self.assertEqual(result["status"], "complete")
        self.assertTrue(all(request["limit"] <= 200 for request in admission.requests))
        self.assertTrue(all(request["revision"] == "fixture-r1" for request in admission.requests))
        self.assertEqual(tools.report()["coverage"]["selection"]["total_in_query"], 13)

    def test_global_thousand_and_first_direction_is_still_selectable(self):
        units = [self.unit("u1", program_count=1001)]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1", study_mode="非全日制")
                for i in range(1, 1001)]
        rows.append(candidate_row("p1001", unit_id="u1", unit_name="学校u1", study_mode="全日制"))
        admission = PagedAdmission(units, rows)
        tools = self.tools(admission, {"study_mode": "全日制"})
        result = tools.find_candidates({"query": "计算机"})
        self.assertEqual([candidate["id"] for candidate in result["candidates"]], ["p1001"])
        self.assertEqual(result["total_in_query"], 1001)
        self.assertEqual(result["scanned_count"], 1001)
        self.assertEqual(result["hard_excluded_count"], 1000)
        self.assertTrue(result["scan_complete"])
        self.assertGreaterEqual(len(admission.requests), 6)

    def test_province_ninth_school_is_not_dropped_by_an_eight_school_cap(self):
        units = [self.unit(f"u{i}", program_count=1) for i in range(1, 10)]
        rows = [candidate_row(f"p{i}", unit_id=f"u{i}", unit_name=f"学校u{i}", study_mode="非全日制")
                for i in range(1, 9)]
        rows.append(candidate_row("p9", unit_id="u9", unit_name="学校u9", study_mode="全日制"))
        admission = PagedAdmission(units, rows)
        tools = self.tools(admission, {"provinces": ["天津"], "study_mode": "全日制"})
        result = tools.find_candidates({"query": "计算机"})
        self.assertEqual([candidate["id"] for candidate in result["candidates"]], ["p9"])
        self.assertEqual(result["scanned_count"], 9)
        self.assertEqual(result["total_in_query"], 9)
        self.assertEqual(set(result["queried_unit_ids"]), {f"u{i}" for i in range(1, 10)})
        self.assertTrue(result["scan_complete"])

    def test_province_without_school_never_falls_back_to_national_scan(self):
        units = [self.unit("u1")]
        admission = PagedAdmission(units, [candidate_row("p1", unit_id="u1")])
        tools = self.tools(admission, {"provinces": ["河北"]})
        result = tools.find_candidates({"query": "计算机"})
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["total_in_query"], 0)
        self.assertEqual(result["scanned_count"], 0)
        self.assertTrue(result["scan_complete"])
        self.assertTrue(result["scope_no_match"])
        self.assertEqual(admission.requests, [])
        self.assertIn("河北", result["notice"])

    def test_partial_scan_is_reported_without_hiding_scanned_candidates(self):
        units = [self.unit("u1", program_count=500)]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1") for i in range(1, 201)]
        admission = PagedAdmission(units, rows, total_override={"u1": 500})
        tools = self.tools(admission, {"provinces": ["天津"]})
        result = tools.find_candidates({"query": "计算机"})
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["scanned_count"], 200)
        self.assertEqual(result["total_in_query"], 500)
        self.assertEqual(result["selected_count"], 12)
        self.assertTrue(any("部分" in gap or "未遍历" in gap for gap in tools.gaps))

    def test_cancellation_is_checked_between_pages(self):
        stop = threading.Event()
        units = [self.unit("u1", program_count=1001)]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1") for i in range(1, 1002)]
        admission = PagedAdmission(units, rows, on_page=lambda count: stop.set() if count == 1 else None)
        tools = self.tools(admission, stop_event=stop)
        with self.assertRaises(ResearchError) as error:
            tools.find_candidates({"query": "计算机"})
        self.assertEqual(error.exception.code, "cancelled")
        self.assertEqual(len(admission.requests), 1)
        self.assertTrue(tools.programs)
        self.assertFalse(tools.candidate_metrics["scan_complete"])

    def test_selection_is_deterministic_and_replaces_candidates_when_conditions_change(self):
        units = [self.unit("u1", program_count=12), self.unit("u2", program_count=12)]
        first_rows = [candidate_row(f"a{i}", unit_id="u1", unit_name="学校u1", study_mode="非全日制")
                      for i in range(1, 13)]
        second_rows = [candidate_row(f"b{i}", unit_id="u2", unit_name="学校u2", study_mode="全日制")
                       for i in range(1, 13)]
        admission = PagedAdmission(units, [*first_rows, *second_rows])
        tools = self.tools(admission)
        first = tools.find_candidates({"query": "计算机", "unit_id": "u1"})
        again = tools.find_candidates({"query": "计算机", "unit_id": "u1"})
        self.assertEqual([candidate["id"] for candidate in first["candidates"]],
                         [candidate["id"] for candidate in again["candidates"]])
        self.assertTrue(all(candidate["unit_id"] == "u1" for candidate in first["candidates"]))

        tools.task["profile"] = validate_profile({"study_mode": "全日制"})
        replaced = tools.find_candidates({"query": "计算机", "unit_id": "u2"})
        self.assertEqual({candidate["unit_id"] for candidate in replaced["candidates"]}, {"u2"})
        self.assertEqual(set(tools.programs), {f"b{i}" for i in range(1, 13)})
        self.assertEqual(tools.ensure_algorithms()[:1], ["compare_candidates"])
        self.assertEqual(tools.algorithm["matching"]["summary"]["input_count"], 12)

    def test_excluded_subject_terms_are_removed_from_catalog_search_text(self):
        units = [self.unit("u1", program_count=2)]
        rows = [candidate_row("p1", unit_id="u1", unit_name="学校u1", name="计算机技术",
                              subjects=SUBJECTS[:3] + [subject("901", 4, "school-a")]),
                candidate_row("p2", unit_id="u1", unit_name="学校u1", name="软件工程")]
        admission = PagedAdmission(units, rows)
        tools = self.tools(admission, {"excluded_subjects": [{"code": "408", "scope": "national"}]})
        result = tools.find_candidates({"query": "计算机 不考408"})
        self.assertTrue(result["candidates"])
        self.assertTrue(all("408" not in request["q"] for request in admission.requests))
        self.assertIn("计算机", admission.requests[0]["q"])

    def test_full_rows_keep_conditions_and_province_for_filtering(self):
        units = [self.unit("u1", program_count=13)]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1",
                              conditions=[rule("education", "eq", "master")]) for i in range(1, 13)]
        rows.append(candidate_row("p13", unit_id="u1", unit_name="学校u1",
                                  conditions=[rule("education", "eq", "bachelor")]))
        admission = PagedAdmission(units, rows)
        tools = self.tools(admission, {"education": "bachelor"})
        result = tools.find_candidates({"query": "计算机"})
        self.assertEqual([candidate["id"] for candidate in result["candidates"]], ["p13"])
        self.assertEqual(result["hard_excluded_count"], 12)

    def test_province_scope_uses_identity_not_other_year_program_count(self):
        units = [self.unit("u1", program_count=0)]
        rows = [candidate_row("p1", unit_id="u1", year=2026)]
        admission = PagedAdmission(units, rows)
        tools = self.tools(admission, {"provinces": ["天津"], "reference_year": 2026,
                                       "subjects": deepcopy(SUBJECTS)}, reference_year=2026)
        result = tools.find_candidates({"query": "计算机"})
        self.assertEqual([candidate["id"] for candidate in result["candidates"]], ["p1"])
        self.assertEqual(result["queried_unit_ids"], ["u1"])
        self.assertEqual(result["total_in_query"], 1)

    def test_deadline_after_first_page_preserves_scanned_candidates(self):
        import time
        holder = {}
        units = [self.unit("u1", program_count=1001)]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1") for i in range(1, 1002)]
        admission = PagedAdmission(units, rows, on_page=lambda count: setattr(
            holder["tools"], "deadline", time.monotonic() - 1) if count == 1 else None)
        tools = self.tools(admission, {"provinces": ["天津"], "subjects": deepcopy(SUBJECTS)})
        holder["tools"] = tools
        result = tools.find_candidates({"query": "计算机"})
        self.assertEqual(result["scanned_count"], 200)
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["selected_count"], 12)
        self.assertTrue(tools.programs)
        self.assertFalse(tools.candidate_metrics["scan_complete"])

    def test_page_revision_mismatch_does_not_become_a_complete_recommendation(self):
        units = [self.unit("u1", program_count=401)]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1") for i in range(1, 402)]
        admission = PagedAdmission(units, rows, revisions=["fixture-r1", "fixture-r2", "fixture-r2"])
        tools = self.tools(admission, {"provinces": ["天津"], "subjects": deepcopy(SUBJECTS)})
        result = tools.find_candidates({"query": "计算机"})
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["status"], "partial")
        self.assertLess(result["scanned_count"], 401)
        self.assertTrue(result["candidates"])
        self.assertTrue(any("版本" in gap for gap in tools.gaps))

    def test_global_scan_revision_switch_preserves_original_revision_candidates(self):
        units = [self.unit("u1", program_count=500)]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1") for i in range(1, 501)]
        admission = PagedAdmission(units, rows, fail_on_page=2, fail_with=ServiceError(
            "revision_changed", "资料版本已更新，请刷新后重试。", 409))
        tools = self.tools(admission, {"subjects": deepcopy(SUBJECTS)})
        result = tools.find_candidates({"query": "计算机"})
        self.assertEqual(result["scanned_count"], 200)
        self.assertEqual(result["total_in_query"], 500)
        self.assertTrue(result["total_complete"])
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["selected_count"], 12)
        self.assertTrue(result["candidates"])
        self.assertEqual(len(admission.requests), 2)
        self.assertTrue(any("版本" in gap for gap in tools.gaps))
        self.assertFalse(tools.candidate_metrics["scan_complete"])
        self.assertEqual(tools.candidate_metrics["revision"], "fixture-r1")
        self.assertEqual(tools.report()["coverage"]["selection"]["scanned_count"], 200)

    def test_school_scan_revision_switch_preserves_original_revision_candidates(self):
        units = [self.unit("u1", program_count=500)]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1") for i in range(1, 501)]
        admission = PagedAdmission(units, rows, fail_on_page=2, fail_with=ServiceError(
            "revision_changed", "资料版本已更新，请刷新后重试。", 409))
        tools = self.tools(admission, {"provinces": ["天津"], "subjects": deepcopy(SUBJECTS)})
        result = tools.find_candidates({"query": "计算机"})
        self.assertEqual(result["scanned_count"], 200)
        self.assertEqual(result["total_in_query"], 500)
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["candidates"])
        self.assertEqual(result["queried_unit_ids"], ["u1"])
        self.assertEqual(result["unqueried_unit_ids"], [])
        self.assertEqual(result["incomplete_unit_ids"], ["u1"])
        self.assertTrue(result["total_complete"])
        self.assertEqual(len(admission.requests), 2)
        self.assertTrue(any("版本" in gap for gap in tools.gaps))

    def test_global_first_page_revision_switch_keeps_count_unknown(self):
        units = [self.unit("u1", program_count=500)]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1") for i in range(1, 501)]
        admission = PagedAdmission(units, rows, fail_on_page=1, fail_with=ServiceError(
            "revision_changed", "资料版本已更新，请刷新后重试。", 409))
        tools = self.tools(admission, {"subjects": deepcopy(SUBJECTS)})
        result = tools.find_candidates({"query": "计算机"})
        self.assertEqual(result["scanned_count"], 0)
        self.assertEqual(result["total_in_query"], 0)
        self.assertFalse(result["total_complete"])
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(admission.requests), 1)
        self.assertTrue(any("版本" in gap for gap in tools.gaps))

    def test_global_budget_before_first_page_keeps_count_unknown(self):
        import time
        units = [self.unit("u1", program_count=500)]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1") for i in range(1, 501)]
        admission = PagedAdmission(units, rows)
        tools = self.tools(admission, {"subjects": deepcopy(SUBJECTS)},
                           deadline=time.monotonic() - 1)
        result = tools.find_candidates({"query": "计算机"})
        self.assertEqual(result["scanned_count"], 0)
        self.assertEqual(result["total_in_query"], 0)
        self.assertFalse(result["total_complete"])
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(admission.requests, [])

    def test_unknown_scan_exception_is_not_swallowed_as_partial(self):
        units = [self.unit("u1", program_count=500)]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1") for i in range(1, 501)]
        for error in (RuntimeError("provider boom"),
                      ServiceError("data_unavailable", "目录暂不可用。", 503)):
            with self.subTest(error=type(error).__name__):
                admission = PagedAdmission(units, rows, fail_on_page=2, fail_with=error)
                tools = self.tools(admission, {"subjects": deepcopy(SUBJECTS)})
                with self.assertRaises(type(error)):
                    tools.find_candidates({"query": "计算机"})
                self.assertEqual(len(admission.requests), 2)

    def test_candidate_selection_uses_the_same_ranking_basis_as_the_engine(self):
        units = [self.unit("u1"), self.unit("u2")]
        rows = [candidate_row("tj", unit_id="u1", province="天津",
                              tuition={"amount": 30000, "period": "year"}),
                candidate_row("bj", unit_id="u2", province="北京",
                              tuition={"amount": 8000, "period": "year"})]
        admission = PagedAdmission(units, rows)
        cost = self.tools(admission, {"preferred_provinces": ["天津"], "ranking_mode": "cost",
                                      "subjects": deepcopy(SUBJECTS)}).find_candidates({"query": "计算机"})
        self.assertEqual([candidate["id"] for candidate in cost["candidates"]], ["bj", "tj"])
        balanced = self.tools(admission, {"preferred_provinces": ["天津"],
                                          "preferences": {"cost": 5, "subjects": 0, "location": 0},
                                          "subjects": deepcopy(SUBJECTS)}).find_candidates({"query": "计算机"})
        self.assertEqual([candidate["id"] for candidate in balanced["candidates"]], ["bj", "tj"])

    def test_union_selection_separates_query_counts_from_cumulative_selection(self):
        units = [self.unit("u1"), self.unit("u2")]
        rows = [candidate_row("a", unit_id="u1", unit_name="学校u1"),
                candidate_row("b", unit_id="u2", unit_name="学校u2")]
        admission = PagedAdmission(units, rows)
        tools = self.tools(admission, {"subjects": deepcopy(SUBJECTS)})
        tools.find_candidates({"query": "计算机", "unit_id": "u1"})
        result = tools.find_candidates({"query": "计算机", "unit_id": "u2"})
        self.assertEqual([candidate["id"] for candidate in result["candidates"]], ["a", "b"])
        self.assertEqual(result["total_in_query"], 1)
        self.assertEqual(result["queried_unit_ids"], ["u2"])
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["cumulative_selected_count"], 2)
        self.assertEqual(result["carried_over_count"], 1)
        self.assertEqual(result["query_candidate_ids"], ["b"])
        self.assertEqual(set(result["candidate_unit_ids"]), {"u1", "u2"})
        self.assertIn("累计", result["notice"])

    def test_candidate_refresh_invalidates_stale_adjustments(self):
        units = [self.unit("u1"), self.unit("u2")]
        rows = [candidate_row("a", unit_id="u1", unit_name="学校u1"),
                candidate_row("b", unit_id="u2", unit_name="学校u2")]
        admission = PagedAdmission(units, rows)
        tools = self.tools(admission, {"subjects": deepcopy(SUBJECTS)})
        tools.find_candidates({"query": "计算机", "unit_id": "u1"})
        tools.ensure_algorithms()
        tools.adjustments({"allowed_changes": {"max_subject_changes": [0, 1]}})
        self.assertIn("adjustments", tools.algorithm)
        tools.find_candidates({"query": "计算机", "unit_id": "u2"})
        tools.ensure_algorithms()
        self.assertNotIn("adjustments", tools.algorithm)
        self.assertEqual(tools.algorithm["matching"]["summary"]["input_count"], 2)


class ScanStatusServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.data.mkdir()

    def test_incomplete_scan_cannot_end_as_completed(self):
        units = [{"id": "u1", "name": "学校u1", "province": "天津",
                  "official_url": "https://u1.edu.cn", "program_count": 1}]
        rows = [candidate_row(f"p{i}", unit_id="u1", unit_name="学校u1") for i in range(1, 201)]
        admission = PagedAdmission(units, rows, total_override={"u1": 201})
        ai = FakeAI([
            {"role": "assistant", "tool_calls": [
                {"id": "f", "name": "find_candidates", "arguments": {"query": "计算机"}}]},
            {"role": "assistant", "content": "结束。", "tool_calls": []},
        ])
        service = ResearchService(self.data / "service", admission, FakeSettings(), ai)
        self.addCleanup(service.close)
        task = wait_done(service, service.create({
            "query": "计算机",
            "profile": {"provinces": ["天津"], "education": "bachelor",
                        "study_mode": "全日制", "subjects": deepcopy(SUBJECTS)}})["id"])
        self.assertEqual(task["status"], "partial")
        selection = task["report"]["coverage"]["selection"]
        self.assertFalse(selection["scan_complete"])
        self.assertEqual(selection["total_in_query"], 201)
        self.assertEqual(selection["scanned_count"], 200)


if __name__ == "__main__":
    unittest.main()
