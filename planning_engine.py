"""Finite, explicit planning over source-backed constraints; never admission odds."""

from copy import deepcopy
from itertools import product
from math import prod

from grad_engine import EngineValidationError, match_programs, validate_profile


ALLOWED_ADJUSTMENTS = {"max_subject_changes", "provinces", "max_annual_tuition"}
DISPLAY_NOTE = "覆盖数和增减列表核对全部传入资料，候选仅展示前20个方向；待核实不代表可申请，不预测录取概率。"


def _program_key(program, direction=True):
    key = (program.get("unit_id") or program.get("unit_name") or program.get("id"),
           program.get("department_code") or program.get("department") or "",
           program.get("code") or program.get("name") or program.get("id"),
           program.get("study_mode") or "")
    return key + (program.get("direction") or program.get("id"),) if direction else key


def _entries(result):
    distinct = {}
    for entry in result["matches"] + result["needs_confirmation"]:
        distinct.setdefault(_program_key(entry["program"]), entry)
    return list(distinct.values())


def _coverage(entries):
    return {"program_count": len(entries),
            "department_program_count": len({_program_key(entry["program"], False) for entry in entries}),
            "unit_count": len({entry["program"]["unit_id"] for entry in entries if entry["program"].get("unit_id")})}


def _view(result):
    entries = _entries(result)
    known = [entry for entry in entries if entry["subject_changes"]["count"] is not None]
    return {"coverage": _coverage(known), "candidate_ids": [entry["program"]["id"] for entry in known],
            "matched_count": sum(entry["status"] == "matched" for entry in known),
            "needs_confirmation_count": sum(entry["status"] == "unknown" for entry in known),
            "unknown_subject_count": len(entries) - len(known), "candidates": deepcopy(known[:20]),
            "summary": deepcopy(result["summary"]), "display_limit": 20, "note": DISPLAY_NOTE}


def _deltas(view, baseline):
    current_ids, base_ids = set(view["candidate_ids"]), set(baseline["candidate_ids"])
    return {"added_ids": sorted(current_ids - base_ids), "retained_ids": sorted(current_ids & base_ids),
            "removed_ids": sorted(base_ids - current_ids)}


def _questions(results):
    questions = {}
    for result in results:
        for question in result["questions"]:
            previous = questions.get(question["field"])
            if previous is None or question["affected_count"] > previous["affected_count"]:
                questions[question["field"]] = deepcopy(question)
    return sorted(questions.values(), key=lambda question: (-question["affected_count"], question["field"]))


def plan_routes(programs, profile):
    """Compare zero, one and two exam-unit changes without relaxing other facts."""
    profile = validate_profile(profile)
    baseline_result = match_programs(programs, profile)
    baseline, routes, results = _view(baseline_result), [], [baseline_result]
    for limit in (0, 1, 2):
        result = match_programs(programs, {**profile, "max_subject_changes": limit})
        view = _view(result)
        routes.append({**view, "id": f"subjects-{limit}", "label": f"最多调整{limit}个考试单元",
                       "max_subject_changes": limit, **_deltas(view, baseline)})
        results.append(result)
    status = "ok" if any(route["coverage"]["program_count"] for route in routes) else "needs_input" if any(
        route["unknown_subject_count"] for route in routes) else "no_candidates"
    return {"status": status, "baseline": baseline, "routes": routes, "questions": _questions(results),
            "note": "每条路线均保留原学历、背景、工作年限、地区和预算；科目身份不完整的方向单独列为无法归入路线。" + DISPLAY_NOTE}


def _validate_options(profile, allowed_changes, goal):
    if type(goal) is not int or not 1 <= goal <= 10000:
        raise EngineValidationError("goal_min_candidates必须为1至10000的整数")
    if not isinstance(allowed_changes, dict) or allowed_changes.keys() - ALLOWED_ADJUSTMENTS:
        raise EngineValidationError("仅可枚举用户明确允许的科目调整数、地区和年学费，不能改变学历等硬事实")
    options = {}
    for field, values in allowed_changes.items():
        if not isinstance(values, list) or not values or len(values) > 128:
            raise EngineValidationError("每项允许调整须为非空有限枚举数组，最多128项")
        normalized = [deepcopy(profile[field])]
        for value in values:
            if field == "max_subject_changes" and (type(value) is not int or value not in (0, 1, 2)):
                raise EngineValidationError("备考建议仅枚举0、1、2个考试单元调整")
            checked = validate_profile({**profile, field: value})[field]
            if checked not in normalized:
                normalized.append(checked)
        options[field] = normalized
    if prod(len(values) for values in options.values()) > 128:
        raise EngineValidationError("允许调整的组合不得超过128个，请缩小枚举范围")
    return options


def _adjustment_explanation(changes, view, feasible):
    labels = {"max_subject_changes": "考试单元调整上限", "provinces": "意向地区", "max_annual_tuition": "年学费上限"}
    fields = "、".join(labels[field] for field in changes)
    conclusion = "已知条件匹配达到所设候选目标，仍仅为参考年度核对。" if feasible else "新增方向仍需核验资格或费用，不能认定可申请。"
    counts = f"调整后有{view['matched_count']}个已知条件匹配方向、{view['needs_confirmation_count']}个待核实方向。"
    return f"仅调整用户允许的{fields}；保留其余事实。" + counts + conclusion + DISPLAY_NOTE


def _suggestion(changes, result, baseline, goal):
    view = _view(result)
    feasible = view["matched_count"] >= goal
    return {**view, **_deltas(view, baseline), "changes": deepcopy(changes), "changed_fields": sorted(changes),
            "cost": len(changes), "feasible": feasible,
            "explanation": _adjustment_explanation(changes, view, feasible)}


def _distance(suggestion, profile):
    distance = 0
    for field, value in suggestion["changes"].items():
        previous = profile[field]
        if field == "provinces":
            distance += len(set(value) ^ set(previous))
        elif previous is not None and value is not None:
            distance += abs(value - previous)
    return distance


def _dominates(left, right):
    left_ids, right_ids = set(left["candidate_ids"]), set(right["candidate_ids"])
    at_least = left_ids >= right_ids and left["matched_count"] >= right["matched_count"]
    strict = left_ids > right_ids or left["matched_count"] > right["matched_count"]
    return at_least and strict and left["cost"] <= right["cost"]


def _minimal_options(suggestions, profile):
    minimum = {state: min(item["cost"] for item in suggestions if item["feasible"] == state)
               for state in {item["feasible"] for item in suggestions}}
    options = [item for item in suggestions if item["cost"] == minimum[item["feasible"]]]
    distinct = {}
    for item in sorted(options, key=lambda option: _distance(option, profile)):
        key = (tuple(item["changed_fields"]), tuple(sorted(item["candidate_ids"])), item["matched_count"])
        distinct.setdefault(key, item)
    options = list(distinct.values())
    return [{**item, "id": f"adjustment-{index}"} for index, item in enumerate(
        (item for item in options if not any(_dominates(other, item) for other in options)), 1)]


def suggest_adjustments(programs, profile, allowed_changes, goal_min_candidates=1):
    """Search at most 128 user-authorized combinations and retain minimal changes."""
    profile = validate_profile(profile)
    options = _validate_options(profile, allowed_changes, goal_min_candidates)
    baseline_result = match_programs(programs, profile)
    baseline, improvements, results = _view(baseline_result), [], [{"questions": baseline_result["questions"]}]
    found_new = False
    for values in product(*options.values()):
        changes = {field: deepcopy(value) for field, value in zip(options, values) if value != profile[field]}
        if not changes:
            continue
        result = match_programs(programs, {**profile, **changes})
        results.append({"questions": result["questions"]})
        item = _suggestion(changes, result, baseline, goal_min_candidates)
        found_new = found_new or bool(item["added_ids"])
        if item["added_ids"] and item["coverage"]["program_count"] >= goal_min_candidates:
            improvements.append(item)
    suggestions = _minimal_options(improvements, profile) if improvements else []
    status = "ok" if suggestions else "no_solution" if found_new else "no_improvement"
    return {"status": status, "baseline": baseline, "suggestions": suggestions,
            "goal_min_candidates": goal_min_candidates, "questions": _questions(results),
            "note": "仅搜索已显式允许的枚举，优先最少变更字段；不自动改变学历、背景、工作年限。" + DISPLAY_NOTE}
