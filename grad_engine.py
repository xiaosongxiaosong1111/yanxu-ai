"""Source-backed admission constraints and preference comparisons, never admission odds."""

from collections import Counter
from copy import deepcopy
from math import inf, isfinite


class EngineValidationError(ValueError):
    pass


PROFILE_FIELDS = {
    "target_year", "reference_year", "education", "background", "work_years", "study_mode",
    "max_total_tuition", "max_annual_tuition", "provinces", "subjects", "max_subject_changes", "preferences", "ranking_mode",
}
FACT_FIELDS = PROFILE_FIELDS | {
    "total", "politics", "foreign_language", "business1", "business2", "program_code",
    "first_choice_program_code", "degree_type", "exam_type", "plan_type", "english_certificate",
}
SUBJECT_FIELDS = {"code", "name", "scope", "exam_unit", "choice_group"}
WEIGHTS = {"cost": 2, "subjects": 4, "location": 2}
OPERATORS = {"eq", "==", "ne", "!=", "in", "not_in", "contains", "prefix_in", "gte", ">=", "lte", "<=", "gt", ">", "lt", "<", "subjects_match", "eq_scoped"}
LABELS = {
    "education": "学历", "background": "本科专业", "work_years": "工作年限", "study_mode": "学习方式",
    "subjects": "备考科目", "max_annual_tuition": "年学费上限", "max_total_tuition": "全程学费上限",
    "provinces": "意向地区", "max_subject_changes": "允许调整的科目数", "total": "初试总分",
    "politics": "政治成绩", "foreign_language": "外语成绩", "business1": "业务课一成绩",
    "business2": "业务课二成绩", "first_choice_program_code": "一志愿专业代码",
}
UNKNOWN = {"unknown", "pending", "tbd", "待核验", "未知", "尚未确定", "未填写"}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _text(value, field, limit=120, empty=False):
    if not isinstance(value, str) or len(value) > limit or any(ord(char) < 32 for char in value):
        raise EngineValidationError(f"{field}必须是长度合适的文本")
    value = value.strip()
    if not empty and not value:
        raise EngineValidationError(f"{field}不能为空")
    return value


def _year(value, field):
    if type(value) is not int or not 2020 <= value <= 2100:
        raise EngineValidationError(f"{field}必须是有效招生年度")
    return value


def _limit(value, field, maximum=1000000, integer=False):
    if value is None:
        return None
    if not _number(value) or value < 0 or value > maximum or (integer and type(value) is not int):
        raise EngineValidationError(f"{field}必须是有效的非负数")
    return value


def _validate_subjects(values):
    if not isinstance(values, list) or len(values) > 32:
        raise EngineValidationError("subjects必须是有限的科目数组")
    result = []
    for value in values:
        if not isinstance(value, dict) or value.keys() - SUBJECT_FIELDS:
            raise EngineValidationError("科目包含不支持的字段")
        subject = {"code": _text(value.get("code"), "科目代码", 30),
                   "name": _text(value.get("name", ""), "科目名称", empty=True),
                   "scope": _text(value.get("scope"), "命题单位")}
        if "exam_unit" in value:
            if type(value["exam_unit"]) is not int or value["exam_unit"] not in range(1, 5):
                raise EngineValidationError("exam_unit必须是1至4的考试单元")
            subject["exam_unit"] = value["exam_unit"]
        if "choice_group" in value:
            if "exam_unit" not in value:
                raise EngineValidationError("可选科目必须注明考试单元")
            if not isinstance(value["choice_group"], (str, int)) or isinstance(value["choice_group"], bool):
                raise EngineValidationError("可选科目组必须是文本或整数标识")
            subject["choice_group"] = _text(str(value["choice_group"]), "可选科目组", 50)
        result.append(subject)
    return result


def validate_profile(value):
    if not isinstance(value, dict) or value.keys() - PROFILE_FIELDS:
        raise EngineValidationError("个人条件不是有效对象或包含不支持的字段")
    target = _year(value.get("target_year", 2028), "target_year")
    reference = _year(value.get("reference_year", 2027), "reference_year")
    if reference > target:
        raise EngineValidationError("参考年度不能晚于目标年度")
    education = value.get("education", "unknown")
    if education not in ("bachelor", "master", "associate", "unknown"):
        raise EngineValidationError("education不在支持范围内")
    study_mode = value.get("study_mode", "unknown")
    if study_mode not in ("全日制", "非全日制", "unknown"):
        raise EngineValidationError("study_mode不在支持范围内")
    ranking_mode = value.get("ranking_mode", "balanced")
    if ranking_mode not in ("balanced", "subjects", "cost"):
        raise EngineValidationError("ranking_mode必须是balanced、subjects或cost")
    provinces = value.get("provinces", [])
    if not isinstance(provinces, list) or len(provinces) > 40:
        raise EngineValidationError("provinces必须是地区数组")
    provinces = list(dict.fromkeys(_text(region, "意向地区", 30) for region in provinces))
    preferences = value.get("preferences", {})
    if not isinstance(preferences, dict) or preferences.keys() - WEIGHTS.keys():
        raise EngineValidationError("preferences包含不支持的偏好")
    weights = {key: _limit(preferences.get(key, default), key, 5) for key, default in WEIGHTS.items()}
    if any(weight is None for weight in weights.values()):
        raise EngineValidationError("偏好权重不能为空")
    background = value.get("background")
    return {
        "target_year": target, "reference_year": reference, "education": education,
        "background": _text(background, "本科专业", empty=True) if background is not None else None,
        "work_years": _limit(value.get("work_years"), "工作年限", 80), "study_mode": study_mode,
        "max_total_tuition": _limit(value.get("max_total_tuition"), "全程学费上限", 10000000),
        "max_annual_tuition": _limit(value.get("max_annual_tuition"), "年学费上限", 10000000),
        "provinces": provinces, "subjects": _validate_subjects(value.get("subjects", [])),
        "max_subject_changes": _limit(value.get("max_subject_changes"), "允许调整科目数", 4, True),
        "preferences": weights, "ranking_mode": ranking_mode,
    }


def _unknown(value):
    return value is None or isinstance(value, str) and (not value.strip() or value.strip().lower() in UNKNOWN)


def _identity(subject):
    if not isinstance(subject, dict):
        return None
    code, scope = subject.get("code"), subject.get("scope")
    if _unknown(code) or _unknown(scope) or not isinstance(code, str) or not isinstance(scope, str):
        return None
    return code.strip(), scope.strip()


def _subject_units(subjects):
    if not isinstance(subjects, list) or not subjects or len(subjects) > 32:
        return {}, True
    units = {}
    for index, subject in enumerate(subjects):
        if not isinstance(subject, dict):
            return {}, True
        if subject.get("choice_group") is not None and "exam_unit" not in subject:
            return {}, True
        slot = subject.get("exam_unit", index + 1)
        if type(slot) is not int or slot not in range(1, 5):
            return {}, True
        units.setdefault(slot, []).append(subject)
    for options in units.values():
        if len(options) > 1:
            groups = {str(option.get("choice_group", "")) for option in options}
            if len(groups) != 1 or not next(iter(groups)):
                return units, True
    return units, any(_identity(subject) is None for options in units.values() for subject in options)


def _subject_changes(program_subjects, prepared_subjects):
    target, target_unknown = _subject_units(program_subjects)
    prepared, preparation_unknown = _subject_units(prepared_subjects)
    target_unknown = target_unknown or sorted(target) not in ([1, 2], [1, 2, 3], [1, 2, 3, 4])
    uncertain = target_unknown or preparation_unknown
    added, removed, missing = [], [], []
    count = 0
    for slot in sorted(target.keys() | prepared.keys()):
        desired, current = target.get(slot, []), prepared.get(slot, [])
        if not desired:
            removed.extend(deepcopy(current[:1]))
            count += 1
        elif not current:
            missing.append(slot)
            uncertain = True
        elif not target_unknown and not preparation_unknown:
            common = {_identity(subject) for subject in desired} & {_identity(subject) for subject in current}
            if not common:
                added.extend(deepcopy(desired[:1]))
                removed.extend(deepcopy(current[:1]))
                count += 1
    return {"added": added, "removed": removed, "count": None if uncertain else count,
            "uncertain": uncertain, "program_units": sorted(target), "prepared_units": sorted(prepared),
            "missing_units": missing, "structural_difference": set(target) != set(prepared)}


def _source(value):
    values = value if isinstance(value, list) else [value]
    return [deepcopy(item) for item in values if isinstance(item, dict) and item]


def _reason(field, status, message, evidence=None, label=None, children=None):
    result = {"field": field, "label": label or LABELS.get(field, field), "status": status,
              "message": message, "evidence": _source(evidence)}
    if children is not None:
        result["children"] = children
    return result


def _compare(actual, op, expected, field):
    if field == "subjects" and op in ("eq", "==", "subjects_match", "eq_scoped"):
        change = _subject_changes(expected, actual)
        return None if change["uncertain"] else change["count"] == 0
    if _unknown(expected):
        return None
    if op in ("eq", "==", "ne", "!="):
        if isinstance(actual, str) and not isinstance(expected, str) or _number(actual) and not _number(expected):
            return None
        answer = actual == expected
        return answer if op in ("eq", "==") else not answer
    if op in ("in", "not_in") and isinstance(expected, list):
        answer = actual in expected
        return answer if op == "in" else not answer
    if op == "contains" and isinstance(actual, (str, list)):
        return expected in actual if isinstance(expected, str) else None
    if op == "prefix_in" and isinstance(actual, str) and isinstance(expected, list):
        return any(actual.startswith(prefix) for prefix in expected) if expected and all(isinstance(prefix, str) and prefix for prefix in expected) else None
    if op in ("gte", ">=", "lte", "<=", "gt", ">", "lt", "<"):
        if field == "education":
            degrees = {"associate": 1, "bachelor": 2, "master": 3}
            if not isinstance(actual, str) or not isinstance(expected, str):
                return None
            actual, expected = degrees.get(actual), degrees.get(expected)
        if not _number(actual) or not _number(expected):
            return None
        if op in ("gte", ">="):
            return actual >= expected
        if op in ("lte", "<="):
            return actual <= expected
        return actual > expected if op in ("gt", ">") else actual < expected
    return None


def _condition(condition, facts, inherited_evidence, supported_fields, depth=0):
    if not isinstance(condition, dict) or depth > 5:
        return _reason("catalog", "unknown", "条件尚未结构化核验", inherited_evidence), set()
    evidence = condition.get("evidence") or inherited_evidence
    if "when" in condition:
        guard, guard_missing = _condition(condition["when"], facts, evidence, supported_fields, depth + 1)
        if guard["status"] == "excluded":
            return _reason(condition.get("field", "requirements"), "matched", "此项条件对当前已知情况不适用", evidence, condition.get("label")), set()
        result, missing = _condition({key: value for key, value in condition.items() if key != "when"}, facts, evidence, supported_fields, depth + 1)
        if guard["status"] == "unknown" and result["status"] != "matched":
            return {**result, "status": "unknown", "message": "适用条件尚未确认，需继续核验"}, missing | guard_missing
        return result, missing
    return _condition_body(condition, facts, evidence, supported_fields, depth)


def _condition_body(condition, facts, evidence, supported_fields, depth):
    label = condition.get("label")
    if "all" in condition or "any" in condition:
        if "all" in condition and "any" in condition:
            return _reason("catalog", "unknown", "条件组合存在歧义，需核验原文", evidence), set()
        operator = "all" if "all" in condition else "any"
        nodes = condition[operator]
        if not isinstance(nodes, list) or not nodes or len(nodes) > 30:
            return _reason("catalog", "unknown", "条件组合尚未核验", evidence), set()
        results = [_condition(node, facts, evidence, supported_fields, depth + 1) for node in nodes]
        statuses = [reason["status"] for reason, _ in results]
        if operator == "all":
            status = "excluded" if "excluded" in statuses else "unknown" if "unknown" in statuses else "matched"
        else:
            status = "matched" if "matched" in statuses else "unknown" if "unknown" in statuses else "excluded"
        missing = set().union(*(fields for reason, fields in results if reason["status"] == "unknown")) if status == "unknown" else set()
        return _reason("requirements", status, "组合条件已逐项核对", evidence, label, [reason for reason, _ in results]), missing
    field, op = condition.get("field"), condition.get("op")
    if not isinstance(field, str) or field not in supported_fields or not isinstance(op, str) or op not in OPERATORS or not _source(evidence):
        return _reason(str(field or "catalog"), "unknown", "该条件缺少可执行字段或出处，需核验原文", evidence, label), set()
    actual = facts.get(field)
    if _unknown(actual):
        answer = None
    else:
        answer = _compare(actual, op, condition.get("value"), field)
    status = "unknown" if answer is None else "matched" if answer else "excluded"
    missing = {field} if answer is None and _unknown(actual) else set()
    message = {"matched": "已知情况符合这项明示条件", "excluded": "已知情况与这项明示条件冲突",
               "unknown": "信息不足或规则无法直接判断，需继续核验"}[status]
    return _reason(field, status, message, evidence, label), missing


def _tuition(program):
    tuition = program.get("tuition")
    if not isinstance(tuition, dict):
        return None, None
    amount, period, total = tuition.get("amount"), tuition.get("period"), tuition.get("total")
    amount = amount if _number(amount) and amount >= 0 else None
    total = total if _number(total) and total >= 0 else None
    annual = amount if period == "year" else None
    if total is None and period == "total":
        total = amount
    duration = program.get("duration_years")
    if total is None and annual is not None and _number(duration) and duration > 0:
        total = annual * duration
    return annual, total


def _exam_matching_blocked(program):
    return program.get('exam_matching_supported') is False or bool(program.get('subject_identity_issues'))


def _subject_result(program, profile):
    changes = _subject_changes(program.get('subjects'), profile['subjects'])
    evidence = program.get('evidence')
    if _exam_matching_blocked(program):
        changes = {**changes, 'count': None, 'uncertain': True, 'added': [], 'removed': []}
        message = ('原文科目代码与名称存在待核矛盾，暂不参与科目匹配或备考建议。'
                   if program.get('subject_identity_issues') else '当前原文科目套组尚不支持可靠匹配，暂不参与备考建议。')
        return changes, _reason('subjects', 'excluded', message, program.get('exam_evidence') or evidence), set()
    if changes['uncertain']:
        fields = {'subjects'} if not profile['subjects'] or changes['missing_units'] else set()
        return changes, _reason('subjects', 'unknown', '科目身份、考试单元或当前备考信息不完整，不能判作零换科', evidence), fields
    limit = profile['max_subject_changes']
    status = 'excluded' if limit is not None and changes['count'] > limit else 'matched'
    message = (f"按完整科目身份核对，需调整{changes['count']}个考试单元" if status == 'matched'
               else '需要调整的考试单元超过已设上限')
    return changes, _reason('subjects', status, message, evidence), set()


def _program_result(program, profile):
    evidence = program.get("evidence")
    conditions = program.get("conditions")
    reasons, missing_fields = [], set()
    if program.get("eligibility_evidence_complete") is not True:
        reasons.append(_reason("catalog", "unknown", "仅有招生目录不足以证明资格要求完整，章程与学院条件仍待核验", evidence))
    if not isinstance(conditions, list) or not _source(evidence):
        reasons.append(_reason("catalog", "unknown", "完整报考条件或原始出处尚未核验", evidence))
    else:
        for condition in conditions:
            reason, fields = _condition(condition, profile, evidence, PROFILE_FIELDS)
            reasons.append(reason)
            missing_fields |= fields
    if program.get("requires_review", bool(program.get("notes"))):
        reasons.append(_reason("catalog", "unknown", "资料中仍有未解析的实质条件，需核验原文", evidence))
    annual, total = _tuition(program)
    for field, actual in (("max_annual_tuition", annual), ("max_total_tuition", total)):
        limit = profile[field]
        if limit is not None:
            status = "unknown" if actual is None else "matched" if actual <= limit else "excluded"
            message = "相同计费口径下符合预算" if status == "matched" else "已知学费超过预算" if status == "excluded" else "计费口径或学制不足，无法核对预算"
            fee_evidence = (program["tuition"].get("evidence") or evidence) if isinstance(program.get("tuition"), dict) else evidence
            reasons.append(_reason(field, status, message, fee_evidence))
    for field, actual, expected in (("study_mode", program.get("study_mode"), profile["study_mode"]),
                                    ("provinces", program.get("province"), profile["provinces"])):
        if _unknown(expected) or field == "provinces" and not expected:
            continue
        answer = actual in expected if field == "provinces" else actual == expected
        status = "unknown" if _unknown(actual) else "matched" if answer else "excluded"
        reasons.append(_reason(field, status, "符合已填写的硬条件" if status == "matched" else "资料不足，需确认" if status == "unknown" else "与已填写的硬条件冲突", evidence))
    changes, subject_reason, subject_fields = _subject_result(program, profile)
    reasons.append(subject_reason)
    missing_fields |= subject_fields
    statuses = [reason["status"] for reason in reasons]
    status = "excluded" if "excluded" in statuses else "unknown" if "unknown" in statuses else "matched"
    entry = {"program": deepcopy(program), "status": status, "reasons": reasons,
             "missing": [reason["message"] for reason in reasons if reason["status"] == "unknown"],
             "subject_changes": changes, "rank_score": None, "rank_interval": None,
             "components": {"cost": None, "subjects": None, "location": None}, "pareto": None,
             "historical_reference": True}
    return entry, missing_fields


def _frontier(vectors):
    """Three objectives use a prefix-maximum skyline, avoiding pairwise national scans."""
    points = sorted({tuple(vector) + (0,) * (3 - len(vector)) for vector in vectors}, reverse=True)
    if not points:
        return set()
    second = {value: index + 1 for index, value in enumerate(sorted({point[1] for point in points}, reverse=True))}
    tree = [-inf] * (len(second) + 1)
    frontier = set()
    for point in points:
        index = second[point[1]]
        cursor, maximum = index, -inf
        while cursor:
            maximum = max(maximum, tree[cursor])
            cursor -= cursor & -cursor
        if maximum < point[2]:
            frontier.add(point)
        while index < len(tree):
            tree[index] = max(tree[index], point[2])
            index += index & -index
    return frontier


def _ranking_basis(entry, profile, active, cost_index):
    if _exam_matching_blocked(entry['program']):
        return {'mode': profile['ranking_mode'], 'method': 'unavailable', 'fields': [], 'missing_fields': ['subjects'],
                'value': None, 'priority': 1, 'explanation': '原文科目存在待核问题或当前套组不支持匹配，未参与排序。'}
    mode, changes = profile["ranking_mode"], entry["subject_changes"]["count"]
    missing = [key for key in active if entry["components"][key] is None]
    fields, value, method = [], None, "unavailable"
    explanation = "可用排序资料不足，保持相同依据项目的原始顺序。"
    if mode == "cost" and _tuition(entry["program"])[cost_index] is not None:
        value = -_tuition(entry["program"])[cost_index]
        fields, method = ["cost"], "total_cost" if cost_index == 1 else "annual_cost"
        explanation = "按可比较的已知全程学费由低到高排序。" if cost_index == 1 else "全程费用不可比较，按已知年学费排序。"
    elif mode == "balanced":
        fields = [key for key in active if entry["components"][key] is not None]
        denominator = sum(profile["preferences"][key] for key in fields)
        if denominator:
            value = sum(entry["components"][key] * profile["preferences"][key] for key in fields) / denominator * 100
            method = "weighted" if not missing else "known_components"
            explanation = "按完整偏好分数排序。" if not missing else "综合分资料不全，仅按已知偏好分项排序；未知分项未赋零。"
    if value is None and changes is not None and (mode != "balanced" or active):
        value, fields = -changes, ["subjects"]
        method = "subjects" if mode == "subjects" else "subjects_fallback"
        explanation = "按需调整的已知考试单元数由少到多排序。" if mode == "subjects" else "可比学费或偏好资料缺失，回退按已知科目调整数排序，不能据此比较费用。"
    priority = int(mode == "cost" and method not in ("total_cost", "annual_cost"))
    return {"mode": mode, "method": method, "fields": fields, "missing_fields": missing,
            "value": value, "priority": priority, "explanation": explanation}


def _rank(entries, profile):
    eligible = [entry for entry in entries if entry["status"] != "excluded"]
    costs = [_tuition(entry["program"])[1] for entry in eligible]
    known_costs = [cost for cost in costs if cost is not None]
    low, high = (min(known_costs), max(known_costs)) if known_costs else (None, None)
    active = [key for key, weight in profile["preferences"].items() if weight > 0 and key != "location"]
    cost_index = 1 if known_costs or not any(_tuition(entry["program"])[0] is not None for entry in eligible) else 0
    denominator = sum(profile["preferences"][key] for key in active)
    scored = []
    for entry in entries:
        if entry["status"] == "excluded":
            scored.append(entry)
            continue
        program, changes = entry["program"], entry["subject_changes"]
        cost = _tuition(program)[1]
        cost_score = None if cost is None else 1 if low == high else (high - cost) / (high - low)
        subject_score = None if changes["count"] is None else 1 - min(1, changes["count"] / max(len(changes["program_units"]), len(changes["prepared_units"]), 1))
        location_score = None if not profile["provinces"] or _unknown(program.get("province")) else float(program["province"] in profile["provinces"])
        components = {"cost": cost_score, "subjects": subject_score, "location": location_score}
        complete = bool(active) and all(components[key] is not None for key in active)
        known_sum = sum(components[key] * profile["preferences"][key] for key in active if components[key] is not None)
        missing_weight = sum(profile["preferences"][key] for key in active if components[key] is None)
        interval = [round(100 * known_sum / denominator, 2), round(100 * (known_sum + missing_weight) / denominator, 2)] if denominator else None
        scored.append({**entry, "components": components, "rank_score": interval[0] if complete else None,
                       "rank_interval": interval, "ranking_note": "分数仅表示已设偏好匹配程度，不是录取概率；未知分项不赋零值。"})
    vectors = [tuple(entry["components"][key] for key in active) for entry in scored
               if entry["status"] == "matched" and entry["rank_score"] is not None]
    frontier = _frontier(vectors)
    return [{**entry, "ranking_basis": _ranking_basis(entry, profile, active, cost_index),
             "pareto": tuple(entry["components"][key] for key in active) + (0,) * (3 - len(active)) in frontier
             if entry["status"] == "matched" and entry["rank_score"] is not None else None} for entry in scored]


def _sort_entries(entries):
    return sorted(entries, key=lambda entry: (entry["ranking_basis"]["priority"], entry["ranking_basis"]["value"] is None,
                                             -(entry["ranking_basis"]["value"] or 0)))


def match_programs(programs, profile):
    profile = validate_profile(profile)
    if not isinstance(programs, list) or any(not isinstance(program, dict) for program in programs):
        raise EngineValidationError("programs必须是招生项目数组")
    current = [program for program in programs if program.get("year") == profile["reference_year"]]
    results = [_program_result(program, profile) for program in current]
    entries = _rank([entry for entry, _ in results], profile)
    questions = Counter(field for entry, fields in results if entry["status"] != "excluded" for field in fields)
    buckets = {"matches": _sort_entries([entry for entry in entries if entry["status"] == "matched"]),
               "excluded": [entry for entry in entries if entry["status"] == "excluded"],
               "needs_confirmation": _sort_entries([entry for entry in entries if entry["status"] == "unknown"])}
    return {**buckets, "questions": [{"field": field, "label": f"请补充{LABELS.get(field, field)}", "affected_count": count}
                                     for field, count in sorted(questions.items(), key=lambda item: (-item[1], item[0]))],
            "summary": {"input_count": len(programs), "reference_count": len(current),
                        "ignored_year_count": len(programs) - len(current), "matched_count": len(buckets["matches"]),
                        "excluded_count": len(buckets["excluded"]), "unknown_count": len(buckets["needs_confirmation"]),
                        "target_year": profile["target_year"], "reference_year": profile["reference_year"],
                        "ranking_mode": profile["ranking_mode"],
                        "ranking_warning": "综合偏好分或排序资料不完整；请查看每项排序依据，缺失费用不代表免费，待核实不代表可申请。"
                        if any(entry["rank_score"] is None or entry["ranking_basis"]["method"] in ("subjects_fallback", "annual_cost")
                               for entry in entries if entry["status"] != "excluded") else None,
                        "historical_reference": True, "algorithm": "three_state_constraints+scoped_subject_distance+weighted_preferences+pareto_skyline"}}


def compare_score_lines(lines, unit_ids=None):
    if not isinstance(lines, list) or any(not isinstance(line, dict) for line in lines):
        raise EngineValidationError("分数资料必须是数组")
    if unit_ids is not None and (not isinstance(unit_ids, (list, tuple, set)) or any(not isinstance(value, str) for value in unit_ids)):
        raise EngineValidationError("unit_ids必须是单位标识数组")
    selected = [deepcopy(line) for line in lines if unit_ids is None or line.get("unit_id") in unit_ids]
    return sorted(selected, key=lambda line: (str(line.get("unit_id", "")), -line["year"] if type(line.get("year")) is int else 0,
                                              str(line.get("kind", "")), str(line.get("program_code", "")),
                                              str(line.get("study_mode", "")), str(line.get("plan_type", ""))))


def _validate_facts(facts):
    if not isinstance(facts, dict) or facts.keys() - FACT_FIELDS:
        raise EngineValidationError("历史试算条件包含不支持的字段")
    result = deepcopy(facts)
    supplied_profile = {field: value for field, value in facts.items() if field in PROFILE_FIELDS and value is not None}
    normalized_profile = validate_profile(supplied_profile)
    result.update({field: normalized_profile[field] for field in supplied_profile})
    limits = {"total": 500, "politics": 100, "foreign_language": 100, "business1": 300, "business2": 300}
    for field, value in result.items():
        if value is None or field in PROFILE_FIELDS:
            continue
        if field in limits:
            _limit(value, field, limits[field])
        elif field == "english_certificate" and isinstance(value, bool):
            continue
        else:
            result[field] = _text(value, field, 120)
    return result


def match_historical_rules(rules, facts, year):
    year = _year(year, "year")
    facts = _validate_facts(facts)
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise EngineValidationError("历史规则必须是数组")
    buckets = {"matched": [], "excluded": [], "needs_confirmation": []}
    for rule in rules:
        if rule.get("year") != year:
            continue
        requirements, evidence = rule.get("requirements"), rule.get("evidence")
        reasons = [_condition(condition, facts, evidence, FACT_FIELDS)[0] for condition in requirements] if isinstance(requirements, list) else []
        if not isinstance(requirements, list) or not _source(evidence):
            reasons.append(_reason("catalog", "unknown", "历史公告的完整条件或出处尚未核验", evidence))
        if rule.get("requires_review") or rule.get("notes") and rule.get("notes_reviewed") is not True:
            reasons.append(_reason("catalog", "unknown", "公告中仍有未核验条件，不能据此认定可申请", evidence))
        statuses = [reason["status"] for reason in reasons]
        status = "excluded" if "excluded" in statuses else "unknown" if "unknown" in statuses else "matched"
        item = {"rule": deepcopy(rule), "status": status, "reasons": reasons,
                "missing": [reason["message"] for reason in reasons if reason["status"] == "unknown"],
                "historical_reference": True}
        buckets[{"matched": "matched", "excluded": "excluded", "unknown": "needs_confirmation"}[status]].append(item)
    return {**buckets, "year": year, "historical_reference": True,
            "note": "仅按该历史年度已核验条件试算，不代表目标年度开放名额或录取机会。"}


def _order_changes(baseline, result):
    before = [entry["program"]["id"] for entry in baseline["matches"] + baseline["needs_confirmation"]]
    after = [entry["program"]["id"] for entry in result["matches"] + result["needs_confirmation"]]
    positions = {identifier: index for index, identifier in enumerate(before, 1)}
    changes = [{"id": identifier, "before": positions[identifier], "after": index}
               for index, identifier in enumerate(after, 1) if identifier in positions and positions[identifier] != index]
    return {"order_before": before, "order_after": after, "rank_changes": changes}


def sensitivity_analysis(programs, profile, scenarios):
    profile = validate_profile(profile)
    if not isinstance(scenarios, list) or len(scenarios) > 12:
        raise EngineValidationError("试算场景必须为最多12项的数组")
    allowed = {"provinces", "max_annual_tuition", "max_total_tuition", "max_subject_changes", "preferences", "subjects", "study_mode", "ranking_mode"}
    baseline = match_programs(programs, profile)
    base_ids = {entry["program"]["id"] for entry in baseline["matches"] + baseline["needs_confirmation"]}
    outputs = []
    for scenario in scenarios:
        if not isinstance(scenario, dict) or scenario.keys() - {"name", "changes"}:
            raise EngineValidationError("试算场景结构不正确")
        name = _text(scenario.get("name"), "场景名称", 80)
        changes = scenario.get("changes")
        if not isinstance(changes, dict) or changes.keys() - allowed:
            raise EngineValidationError("场景仅能调整用户明确允许变化的偏好与备考条件")
        values = {**profile, **deepcopy(changes)}
        if "preferences" in changes:
            if not isinstance(changes["preferences"], dict):
                raise EngineValidationError("场景偏好必须是对象")
            values["preferences"] = {**profile["preferences"], **changes["preferences"]}
        result = match_programs(programs, values)
        candidates = result["matches"] + result["needs_confirmation"]
        ids = {entry["program"]["id"] for entry in candidates}
        outputs.append({"name": name, "changes": deepcopy(changes), "added": sorted(ids - base_ids),
                        "removed": sorted(base_ids - ids), "retained": sorted(ids & base_ids),
                        "added_matched": sorted(entry["program"]["id"] for entry in result["matches"] if entry["program"]["id"] not in base_ids),
                        "added_needs_confirmation": sorted(entry["program"]["id"] for entry in result["needs_confirmation"] if entry["program"]["id"] not in base_ids),
                        "summary": result["summary"], "historical_reference": True,
                        "candidates": candidates[:20], **_order_changes(baseline, result)})
    return {"baseline": {"candidate_ids": sorted(base_ids), "summary": baseline["summary"]}, "scenarios": outputs,
            "note": "显式场景试算不会自动放宽原条件；科目调整仅供报考前规划。"}
