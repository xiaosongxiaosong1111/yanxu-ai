"""Deterministic decision support on top of the three-state admission engine.

This module answers three questions without inventing facts:

* Which missing condition would most change the current shortlist?
* How stable is the shortlist when an unknown condition is resolved differently?
* Which small set of candidates covers different schools or regions?

Every number is derived from ``grad_engine.match_programs`` under explicitly
enumerated user choices. No model score, admission probability or fabricated
field enters the result.
"""
from collections import Counter
from copy import deepcopy

from grad_engine import (
    LABELS, PROFILE_FIELDS, EngineValidationError, match_programs,
    rank_order_key, validate_profile,
)


VERSION = 'decision_v1'
EDUCATION_LABELS = {'bachelor': '本科', 'master': '硕士', 'associate': '专科'}
MAX_TOP_K = 12
MAX_SCENARIOS = 8


def _is_number(value):
    return type(value) in (int, float) and value == value and value not in (float('inf'), float('-inf'))


def _row_id(entry):
    program = entry.get('program') if isinstance(entry, dict) else None
    return program.get('id') if isinstance(program, dict) else None


def _row_status(entry):
    return entry.get('status') if isinstance(entry, dict) else None


def _label(entry):
    program = entry.get('program') if isinstance(entry, dict) else None
    if not isinstance(program, dict):
        return ''
    parts = [program.get('unit_name'), program.get('name'), program.get('direction')]
    return ' · '.join(str(part) for part in parts if part)[:160]


def _top_rows(result, limit):
    """Prefer certain matches, then fill with current unknowns in rank order."""
    rows, seen = [], set()
    for entry in [*result.get('matches', []), *result.get('needs_confirmation', [])]:
        identifier = _row_id(entry)
        if not isinstance(identifier, str) or identifier in seen:
            continue
        seen.add(identifier)
        rows.append(entry)
        if len(rows) >= limit:
            break
    return rows


def _top_ids(result, limit):
    return [_row_id(entry) for entry in _top_rows(result, limit)]


def _unique(values):
    return list(dict.fromkeys(values))


def _province_options(programs):
    counts = Counter()
    for program in programs:
        province = program.get('province')
        if isinstance(province, str) and province.strip():
            counts[province.strip()] += 1
    return [[name] for name, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:4]]


def _tuition_values(programs, field):
    values = []
    for program in programs:
        tuition = program.get('tuition') if isinstance(program.get('tuition'), dict) else {}
        amount, period, total = tuition.get('amount'), tuition.get('period'), tuition.get('total')
        annual = amount if period == 'year' and _is_number(amount) and amount >= 0 else None
        whole = total if _is_number(total) and total >= 0 else amount if period == 'total' and _is_number(amount) and amount >= 0 else None
        value = annual if field == 'max_annual_tuition' else whole
        if value is not None:
            values.append(int(value))
    return sorted(set(values))


def _quantiles(values, limit=4):
    values = sorted(set(values))
    if len(values) <= limit:
        return values
    indexes = sorted({round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)})
    return [values[index] for index in indexes]


def _option_label(field, option):
    if field in {'provinces', 'preferred_provinces'}:
        return '、'.join(option) if option else '不限'
    if field in {'max_annual_tuition', 'max_total_tuition'}:
        return f'不高于 {option:,} 元'
    if field == 'education':
        return EDUCATION_LABELS.get(option, str(option))
    return str(option)


def _questions(programs, profile):
    """Only ask about fields that are genuinely unknown and enumerable."""
    questions = []
    if not profile['provinces']:
        options = _province_options(programs)
        if options:
            questions.append({'field': 'provinces', 'label': LABELS['provinces'], 'options': options,
                              'suggested_question': '你的意向地区是哪里？这会先排除地区不符的候选。'})
    elif not profile['preferred_provinces']:
        # Ask the soft preference only after the hard region choice exists;
        # otherwise both questions describe the same missing information.
        options = _province_options(programs)
        if options:
            questions.append({'field': 'preferred_provinces', 'label': LABELS['preferred_provinces'],
                              'options': options, 'suggested_question': '如果没有硬性地区限制，你更希望优先考虑哪里？'})
    if profile['study_mode'] == 'unknown':
        questions.append({'field': 'study_mode', 'label': LABELS['study_mode'],
                          'options': ['全日制', '非全日制'],
                          'suggested_question': '你希望读全日制还是非全日制？两者的费用和条件分别核对。'})
    if profile['education'] == 'unknown':
        questions.append({'field': 'education', 'label': LABELS['education'],
                          'options': ['bachelor', 'master', 'associate'],
                          'suggested_question': '你目前的最高学历是什么？学历会改变可研究的资格范围。'})
    if profile['max_annual_tuition'] is None:
        options = _quantiles(_tuition_values(programs, 'max_annual_tuition'))
        if options:
            questions.append({'field': 'max_annual_tuition', 'label': LABELS['max_annual_tuition'],
                              'options': options,
                              'suggested_question': '你的年学费预算上限是多少？未知学费不会按免费处理。'})
    if profile['max_total_tuition'] is None:
        options = _quantiles(_tuition_values(programs, 'max_total_tuition'))
        if options:
            questions.append({'field': 'max_total_tuition', 'label': LABELS['max_total_tuition'],
                              'options': options,
                              'suggested_question': '你的全程学费预算上限是多少？学制未知时不会推算总额。'})
    return questions


def _impact(baseline_ids, variants, top_k):
    """Measure how often an option changes the shortlist, not how large scores are."""
    changed = first_changed = 0
    entered, left, displacement = set(), set(), []
    example, example_weight = None, -1
    for option, ids, _ in variants:
        added = [identifier for identifier in ids if identifier not in baseline_ids]
        removed = [identifier for identifier in baseline_ids if identifier not in ids]
        if ids != baseline_ids:
            changed += 1
        if ids[:1] != baseline_ids[:1]:
            first_changed += 1
        entered.update(added)
        left.update(removed)
        displacement.extend(abs(ids.index(identifier) - baseline_ids.index(identifier))
                            for identifier in set(ids) & set(baseline_ids))
        weight = len(added) + len(removed) + (top_k if ids[:1] != baseline_ids[:1] else 0)
        if weight > example_weight:
            example_weight = weight
            example = {'option': option, 'top_k': ids, 'entered': added, 'left': removed}
    total = len(variants)
    average_displacement = sum(displacement) / len(displacement) if displacement else 0.0
    priority = (0.45 * changed / total + 0.35 * first_changed / total
                + 0.20 * min(1.0, average_displacement / max(1, top_k)))
    return {'options_tested': total, 'changed_options': changed, 'first_choice_changes': first_changed,
            'entered_ids': sorted(entered), 'left_ids': sorted(left),
            'average_displacement': round(average_displacement, 3),
            'priority': round(priority, 4), 'example': example}


def value_of_information(programs, profile, top_k=5):
    """Rank missing conditions by how much resolving them would change the top-k."""
    if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
        raise EngineValidationError(f'top_k必须为1至{MAX_TOP_K}的整数')
    profile = validate_profile(profile)
    if not isinstance(programs, list) or any(not isinstance(program, dict) for program in programs):
        raise EngineValidationError('programs必须是招生项目数组')
    baseline = match_programs(programs, profile)
    baseline_rows = _top_rows(baseline, top_k)
    baseline_ids = [_row_id(entry) for entry in baseline_rows]
    if not baseline_ids:
        return {'version': VERSION, 'top_k': top_k, 'baseline_top_k': [],
                'questions': [], 'next_question': None,
                'note': '当前没有可比较候选；请先补充候选或放宽已填写条件。'}
    questions = []
    for question in _questions(programs, profile):
        variants = []
        for option in question['options']:
            values = {**deepcopy(profile), question['field']: deepcopy(option)}
            try:
                result = match_programs(programs, values)
            except EngineValidationError:
                continue
            rows = _top_rows(result, top_k)
            variants.append((option, [_row_id(entry) for entry in rows], rows))
        if not variants:
            continue
        impact = _impact(baseline_ids, variants, top_k)
        labels = {_row_id(entry): _label(entry) for entry in baseline_rows}
        for _, _, rows in variants:
            labels.update({_row_id(entry): _label(entry) for entry in rows})
        example = impact['example']
        if example is not None:
            example = {**example, 'option_label': _option_label(question['field'], example['option']),
                       'entered_labels': [labels.get(identifier, identifier) for identifier in example['entered']],
                       'left_labels': [labels.get(identifier, identifier) for identifier in example['left']]}
            impact['example'] = example
        if impact['changed_options'] == 0:
            explanation = f'无论选择哪个选项，当前前 {top_k} 名都不变，可以稍后再确认。'
        else:
            explanation = (f'在测试的 {impact["options_tested"]} 个选项中，'
                           f'{impact["changed_options"]} 个会改变前 {top_k} 名；'
                           f'最多有 {len(impact["entered_ids"])} 个候选进入、'
                           f'{len(impact["left_ids"])} 个退出。')
        questions.append({**question, 'impact': impact, 'explanation': explanation})
    questions.sort(key=lambda row: (-row['impact']['priority'], row['field']))
    top = questions[0] if questions else None
    return {'version': VERSION, 'top_k': top_k, 'baseline_top_k': baseline_ids,
            'baseline_labels': {_row_id(entry): _label(entry) for entry in baseline_rows},
            'questions': questions,
            'next_question': ({'field': top['field'], 'label': top['label'],
                               'question': top['suggested_question'],
                               'priority': top['impact']['priority']} if top and top['impact']['priority'] > 0 else None),
            'note': '优先级只表示“确认后对当前候选集合的影响”，不是录取概率；未知字段不会按默认值补齐。'}


def _jaccard(left, right):
    left, right = set(left), set(right)
    return round(len(left & right) / len(left | right), 4) if left | right else 1.0


def _default_scenarios(programs, profile, top_k):
    """Derive a small set of scenarios from the highest-impact unknown conditions."""
    questions = value_of_information(programs, profile, top_k=top_k)['questions']
    scenarios = []
    for question in questions[:2]:
        for option in question['options'][:3]:
            scenarios.append({'name': f"{question['label']}：{_option_label(question['field'], option)}",
                              'changes': {question['field']: deepcopy(option)}})
    return scenarios[:MAX_SCENARIOS]


def rank_stability(programs, profile, top_k=5, scenarios=()):
    """Count how often each candidate stays in the top-k under explicit scenarios."""
    if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
        raise EngineValidationError(f'top_k必须为1至{MAX_TOP_K}的整数')
    profile = validate_profile(profile)
    if not isinstance(programs, list) or any(not isinstance(program, dict) for program in programs):
        raise EngineValidationError('programs必须是招生项目数组')
    if not isinstance(scenarios, (list, tuple)) or len(scenarios) > MAX_SCENARIOS:
        raise EngineValidationError(f'试算场景必须为最多{MAX_SCENARIOS}项的数组')
    if not scenarios:
        scenarios = _default_scenarios(programs, profile, top_k)
    rows = []
    for scenario in scenarios:
        if not isinstance(scenario, dict) or scenario.keys() - {'name', 'changes'}:
            raise EngineValidationError('试算场景结构不正确')
        name = scenario.get('name')
        changes = scenario.get('changes')
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise EngineValidationError('试算场景名称不正确')
        if not isinstance(changes, dict) or not changes or changes.keys() - PROFILE_FIELDS:
            raise EngineValidationError('试算场景仅能调整已支持的用户条件')
        try:
            values = {**deepcopy(profile), **deepcopy(changes)}
            if isinstance(changes.get('preferences'), dict):
                values['preferences'] = {**profile['preferences'], **deepcopy(changes['preferences'])}
            result = match_programs(programs, values)
        except EngineValidationError as error:
            raise EngineValidationError(f'试算场景“{name.strip()}”无效：{error}') from error
        rows.append({'name': name.strip(), 'changes': deepcopy(changes),
                     'top_k': _top_ids(result, top_k)})
    baseline = match_programs(programs, profile)
    baseline_ids = _top_ids(baseline, top_k)
    if not baseline_ids:
        return {'version': VERSION, 'top_k': top_k, 'baseline_top_k': [], 'scenarios': rows,
                'stability': [], 'summary': {'scenario_count': len(rows), 'stable_count': 0,
                                             'volatile_count': 0, 'average_jaccard': 0.0},
                'note': '当前没有可比较候选。'}
    labels = {_row_id(entry): _label(entry) for entry in _top_rows(baseline, top_k)}
    observations = [baseline_ids, *[row['top_k'] for row in rows]]
    ids = _unique(identifier for observation in observations for identifier in observation)
    stability = []
    for identifier in ids:
        positions = []
        for observation in observations:
            if identifier in observation:
                positions.append(observation.index(identifier) + 1)
        stability.append({'candidate_id': identifier, 'label': labels.get(identifier, identifier),
                          'appearances': len(positions), 'top_k_frequency': round(len(positions) / len(observations), 4),
                          'first_frequency': round(sum(position == 1 for position in positions) / len(observations), 4),
                          'rank_min': min(positions) if positions else None,
                          'rank_max': max(positions) if positions else None,
                          'baseline_rank': baseline_ids.index(identifier) + 1 if identifier in baseline_ids else None})
    stability.sort(key=lambda row: (row['baseline_rank'] is None, row['baseline_rank'] or 99,
                                    -row['top_k_frequency'], row['candidate_id']))
    jaccards = [_jaccard(baseline_ids, row['top_k']) for row in rows]
    return {'version': VERSION, 'top_k': top_k, 'baseline_top_k': baseline_ids,
            'scenarios': [{'name': row['name'], 'changes': row['changes'], 'top_k': row['top_k'],
                           'jaccard': _jaccard(baseline_ids, row['top_k'])} for row in rows],
            'stability': stability,
            'summary': {'scenario_count': len(rows),
                        'stable_count': sum(row['top_k_frequency'] == 1.0 for row in stability),
                        'volatile_count': sum(row['top_k_frequency'] < 1.0 for row in stability),
                        'average_jaccard': round(sum(jaccards) / len(jaccards), 4) if jaccards else 1.0},
            'note': '稳定性表示在前 k 名中出现频率，不是录取概率；频率低说明结论依赖尚未确认的条件。'}


def _similarity(left, right, key):
    a, b = left['program'], right['program']
    if key == 'province':
        if a.get('province') and a.get('province') == b.get('province'):
            return 1.0
        if a.get('unit_id') and a.get('unit_id') == b.get('unit_id'):
            return 0.5
    else:
        if a.get('unit_id') and a.get('unit_id') == b.get('unit_id'):
            return 1.0
        if a.get('province') and a.get('province') == b.get('province'):
            return 0.5
    return 0.0


def _relevance(entry, index, total):
    """Rank percentile keeps MMR comparable across differently scaled scores."""
    return 1.0 - index / max(1, total - 1)


def diversify(programs, profile, limit=5, key='unit_id', relevance_weight=0.65):
    """MMR selection so a short list does not become ten copies of one school."""
    if type(limit) is not int or not 1 <= limit <= MAX_TOP_K:
        raise EngineValidationError(f'limit必须为1至{MAX_TOP_K}的整数')
    if key not in {'unit_id', 'province'}:
        raise EngineValidationError('diversity_key必须是unit_id或province')
    if not _is_number(relevance_weight) or not 0 <= relevance_weight <= 1:
        raise EngineValidationError('relevance_weight必须在0到1之间')
    profile = validate_profile(profile)
    result = match_programs(programs, profile)
    entries = [entry for entry in result['matches'] if isinstance(entry, dict)]
    basis = 'matched'
    if not entries:
        # Never turn an unknown candidate into a match. When nothing is certain,
        # still offer spread-out *pending* options, labelled as such.
        entries = [entry for entry in result['needs_confirmation'] if isinstance(entry, dict)]
        basis = 'needs_confirmation'
    if not entries:
        return {'version': VERSION, 'limit': limit, 'selected': [], 'basis': 'none',
                'note': '当前没有可展示的候选，未生成分散推荐。'}
    selected, selected_entries, remaining = [], [], list(enumerate(entries))
    while remaining and len(selected) < limit:
        best = None
        for index, entry in remaining:
            similarity = max((_similarity(entry, chosen, key) for chosen in selected_entries), default=0.0)
            score = relevance_weight * _relevance(entry, index, len(entries)) - (1 - relevance_weight) * similarity
            candidate = (score, rank_order_key(entry, profile), index, entry, similarity)
            if best is None or (-candidate[0], candidate[1]) < (-best[0], best[1]):
                best = candidate
        score, _, index, entry, similarity = best
        program = entry['program']
        selected.append({'candidate_id': program.get('id'), 'unit_id': program.get('unit_id'),
                         'unit_name': program.get('unit_name'), 'name': program.get('name'),
                         'province': program.get('province'), 'status': _row_status(entry),
                         'mmr_score': round(score, 4), 'similarity_to_selected': round(similarity, 4)})
        selected_entries.append(entry)
        remaining = [(position, row) for position, row in remaining if row is not entry]
    return {'version': VERSION, 'limit': limit, 'key': key, 'relevance_weight': relevance_weight,
            'basis': basis, 'selected': selected,
            'note': ('分散推荐只在明确匹配候选中进行；它改变展示顺序，不改变资格、分数或已填条件。'
                     if basis == 'matched' else
                     '当前没有明确匹配的候选；以下仅为待确认候选的分散展示，不代表已通过资格核对。')}


def analyze(programs, profile, top_k=5, scenarios=()):
    """One deterministic decision-support payload for the agent report."""
    return {'version': VERSION,
            'value_of_information': value_of_information(programs, profile, top_k=top_k),
            'stability': rank_stability(programs, profile, top_k=top_k, scenarios=scenarios),
            'diversified': diversify(programs, profile, limit=top_k),
            'note': '本模块只比较现有候选在显式条件组合下的结果；不补造字段，不输出录取概率。'}
