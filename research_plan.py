"""Deterministic research ledger: goals, evidence gaps, budgets and skill checks.

The model may propose the next step in prose, but this module decides what is still
missing and whether a Skill has actually produced a checkable result. It never turns
model prose into admission facts.
"""
from copy import deepcopy
from evidence import exam_unresolved_reason
from grad_engine import _subject_units

FACT_LABELS = {
    'eligibility': '报考资格与适用范围',
    'subjects': '考试科目与命题范围',
    'tuition': '学费与收费口径',
}
FACT_QUERIES = {
    'eligibility': '招生简章 报考条件 学历 专业范围',
    'subjects': '招生专业目录 考试科目 命题范围',
    'tuition': '学费 收费标准 收费公示',
}
FACT_ORDER = ('eligibility', 'subjects', 'tuition')

SKILL_CONTRACTS = {
    'understand-intent': {
        'input': '用户原句、已确认条件与当前候选范围',
        'tools': ('find_units', 'find_candidates', 'propose_profile'),
        'output': '不超过三个待确认关键问题，或已确认范围下的候选查询',
    },
    'research-official-sources': {
        'input': '学校、真实参考年度与有限检索预算',
        'tools': ('find_units', 'find_candidates', 'search_official_sources', 'search_knowledge',
                  'search_knowledge_segments', 'propose_source_candidates'),
        'output': '可回到学校官网或本机留档的来源，保留实际年度与读取范围',
    },
    'verify-evidence': {
        'input': '本轮发现的来源与原文片段',
        'tools': ('read_official_source', 'search_knowledge', 'search_knowledge_segments',
                  'read_knowledge_segment', 'propose_source_candidates'),
        'output': '带来源、年度、范围和原文位置的证据；未读到时保持未知',
    },
    'compare-plans': {
        'input': '已发现候选、用户已确认条件',
        'tools': ('compare_candidates', 'plan_subject_routes', 'suggest_adjustments'),
        'output': '由确定性算法产生的匹配、路线或调整试算，不由模型编造',
    },
    'assess-transfer-options': {
        'input': '复试/调剂规则主题与用户考试科目',
        'tools': ('read_knowledge', 'read_official_source', 'search_official_sources'),
        'output': '区分历史相容与当年缺额的规则依据，不保证调剂结果',
    },
}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _candidate_fact(program, fact):
    identifier = program.get('id')
    if fact == 'eligibility':
        complete = program.get('eligibility_evidence_complete') is True
        return {
            'candidate_id': identifier, 'fact': fact, 'label': FACT_LABELS[fact],
            'status': 'known' if complete else 'unknown',
            'value': True if complete else None,
            'evidence': [],
            'next_action': None if complete else {
                'tool': 'search_official_sources', 'args': {'query': FACT_QUERIES[fact],
                                                             'unit_id': program.get('unit_id'),
                                                             'year': program.get('year')},
                'reason': '资格未知时优先核对当年招生简章与报考条件，保留 unknown。',
                'completion': '取得带年份、学校范围和原文位置的官方条款；否则继续标为未知。',
            },
        }
    if fact == 'subjects':
        value = program.get('subjects')
        units, invalid = _subject_units(value)
        unresolved = exam_unresolved_reason(program)
        known = not unresolved and not invalid and sorted(units) in ([1, 2], [1, 2, 3], [1, 2, 3, 4])
        return {
            'candidate_id': identifier, 'fact': fact, 'label': FACT_LABELS[fact],
            'status': 'known' if known else 'unknown',
            'value': deepcopy(value) if known else None,
            'evidence': [],
            'next_action': None if known else {
                'tool': 'search_official_sources', 'args': {'query': FACT_QUERIES[fact],
                                                             'unit_id': program.get('unit_id'),
                                                             'year': program.get('year')},
                'reason': unresolved or '考试单元或科目身份尚不完整，先核对专业目录，不能根据常识补默认科目。',
                'completion': '在官方目录中找到科目组合与命题范围；否则不参与换科计算。',
            },
        }
    raw = program.get('tuition')
    amount = raw.get('amount') if isinstance(raw, dict) else raw
    period = raw.get('period') if isinstance(raw, dict) else None
    known = _number(amount)
    return {
        'candidate_id': identifier, 'fact': fact, 'label': FACT_LABELS[fact],
        'status': 'known' if known else 'unknown',
        'value': amount if known else None,
        'period': period if known else None,
        'evidence': deepcopy(raw.get('evidence')) if isinstance(raw, dict) and known else [],
        'next_action': None if known else {
            'tool': 'search_official_sources', 'args': {'query': FACT_QUERIES[fact],
                                                         'unit_id': program.get('unit_id'),
                                                         'year': program.get('year')},
            'reason': '费用未知时优先核对收费公示或招生简章中的收费标准，未知费用不当免费。',
            'completion': '取得明确金额、收费单位和适用年度的原文；否则保留未知。',
        },
    }


def _source_evidence(sources, unit_id):
    rows = []
    for source in sources.values():
        if unit_id and source.get('unit_id') not in (None, unit_id):
            continue
        if source.get('status') not in {'official_original_read', 'published_snapshot'}:
            continue
        rows.append({'source_id': source.get('id'), 'status': source.get('status'),
                     'year': source.get('year'), 'sha256': source.get('sha256'),
                     'knowledge_id': source.get('knowledge_id'),
                     'current': source.get('knowledge_current') is not False})
    return rows


def _skill_contracts(loaded, executed, sources, algorithm, programs, questions, gaps):
    result = []
    official = [source for source in sources.values() if source.get('status') == 'official_original_read']
    verified = [source for source in official if type(source.get('year')) is int and source.get('sha256')]
    # A search lead has a URL but no actual year or readable original, so it
    # cannot satisfy the "source with an actual year" contract. Only official
    # text or a published snapshot may mark this Skill as checked.
    dated = [source for source in sources.values()
             if source.get('status') in {'official_original_read', 'published_snapshot'}
             and type(source.get('year')) is int]
    for name, contract in SKILL_CONTRACTS.items():
        is_loaded = name in (loaded or ())
        used = sorted(tool for tool in contract['tools'] if tool in (executed or ()))
        checked, reason = False, '规程尚未加载或相关工具尚未执行。'
        if is_loaded and used:
            if name == 'understand-intent':
                checked = bool(programs or questions)
                reason = '已有候选或可执行的关键问题。' if checked else '尚未形成候选或关键问题。'
            elif name == 'research-official-sources':
                checked = bool(dated)
                reason = ('已有带实际年度的官方原文或已发布来源。' if checked
                          else '尚未取得带实际年度的官方原文或已发布来源。')
            elif name == 'verify-evidence':
                checked = bool(verified)
                reason = '已有带实际年度和 SHA 的官方原文。' if checked else '尚未读到带实际年度和 SHA 的官方原文。'
            elif name == 'compare-plans':
                checked = bool(algorithm.get('matching'))
                reason = '确定性匹配算法已执行。' if checked else '确定性匹配算法尚未完成。'
            elif name == 'assess-transfer-options':
                checked = 'read_knowledge' in (executed or ())
                reason = '复试/调剂规则已读取。' if checked else '尚未读取复试/调剂规则。'
        status = 'not_loaded' if not is_loaded else 'checked' if checked else 'executed' if used else 'loaded'
        if is_loaded and gaps and not checked:
            status = 'partial' if status in {'loaded', 'executed'} else status
        result.append({'id': name, 'label': contract['input'].split('、')[0], 'loaded': is_loaded,
                       'executed_tools': used, 'status': status, 'checked': checked,
                       'reason': reason, 'output': contract['output'],
                       'loaded_checked_executed_are_independent': True})
    return result


def build_plan(task, programs, sources, algorithm, gaps, usage, loaded_skills=(), executed_tools=(),
               questions=(), previous_report=None, inheritance=None, field_candidates=()):
    """Return a JSON-safe plan. Missing facts remain unknown, never zero or confirmed."""
    programs = programs or {}
    algorithm = algorithm or {}
    if previous_report is None and isinstance(task, dict):
        previous_report = task.get('previous_report')
    facts = []
    for program in programs.values():
        for fact in FACT_ORDER:
            value = _candidate_fact(program, fact)
            if not value.get('evidence'):
                value['evidence'] = _source_evidence(sources, program.get('unit_id'))
            facts.append(value)
    missing = [row for row in facts if row['status'] == 'unknown']
    actions = [row['next_action'] for row in missing if row.get('next_action')]
    unique, seen = [], set()
    for action in actions:
        key = (action['args'].get('unit_id'), action['args'].get('query'))
        if key in seen:
            continue
        seen.add(key)
        unique.append({'priority': len(unique) + 1, **deepcopy(action)})
    if not programs:
        for source in sources.values():
            if source.get('seeded') and source.get('status') == 'search_lead':
                unique.append({'priority': len(unique)+1, 'tool': 'read_official_source',
                    'args': {'source_id': source['id'], 'unit_id': source.get('unit_id')},
                    'reason': '用户指定的资料尚待核验；先重新读取受控官网原文，不能直接当作方向事实。',
                    'completion': '取得实际年度、可核验原件与完整正文，通过准入后再定位专业行。'})
                continue
            if (source.get('status') != 'official_original_read' or source.get('knowledge_current') is False
                    or source.get('usable') is False or source.get('year') != task.get('reference_year')):
                continue
            segments = source.get('segments') or []
            if not segments:
                unique.append({'priority': len(unique)+1, 'tool': 'search_knowledge_segments',
                    'args': {'query': '招生专业目录 考试科目', 'entry_id': source.get('knowledge_id'),
                             'unit_id': source.get('unit_id'), 'year': source.get('year')},
                    'reason': '没有已发布候选时，先在已取得的官方原文中定位完整专业行与考试科目。',
                    'completion': '取得当前提取版本的真实片段ID；未取得时不制造占位方向。'})
                continue
            segment = segments[0]
            unique.extend([
                {'priority': len(unique)+1, 'tool': 'read_knowledge_segment',
                 'args': {'segment_id': segment['id'], 'before': 1, 'after': 1, 'max_chars': 3200},
                 'reason': '精读专业完整行、表头与限定条件，不能用搜索摘要生成方向。',
                 'completion': '明确读到当前版本的完整行与逐字引用范围。'},
                {'priority': len(unique)+2, 'tool': 'propose_source_candidates',
                 'args': {'source_id': source['id'], 'segment_id': segment['id']},
                 'reason': '精读后按完整行和科目逐字引用构造最多六个草案，再交确定性校验生成临时方向。',
                 'completion': '只采用工具校验通过的临时方向；学校和年度由来源推导，资格仍未知。'}])
            break
    goals = [
        {'id': 'scope', 'label': '确定可研究的合理范围', 'status': 'done' if programs else 'open',
         'completion': '取得有限、可解释的候选，并标明不是全国全集。'},
        {'id': 'evidence', 'label': '补齐关键事实', 'status': 'done' if not missing else 'partial',
         'completion': '资格、科目与费用有真实年度和原文位置；没有资料时保持 unknown。'},
        {'id': 'algorithm', 'label': '用确定性算法比较', 'status': 'done' if algorithm.get('matching') else 'open',
         'completion': '匹配、路线和调整试算只来自程序，不由模型文本替代。'},
        {'id': 'report', 'label': '形成可追溯成果',
         'status': 'done' if programs and algorithm.get('matching') else 'partial',
         'completion': '候选、证据、未知项和下一步均可从报告回到原文。'},
    ]
    ready = bool(programs and algorithm.get('matching')
                 and not any(goal['status'] == 'open' for goal in goals[:3]))
    previous = {}
    if isinstance(previous_report, dict):
        previous = {
            'turn_id': task.get('previous_turn_id'),
            'candidate_ids': [row.get('id') for row in previous_report.get('candidates', [])
                              if isinstance(row, dict) and row.get('id')],
            'evidence_ids': [row.get('id') for row in previous_report.get('evidence', [])
                             if isinstance(row, dict) and row.get('id')],
            'inherit': '未修改的范围与证据优先复用；条件变化只重算受影响候选和算法。',
        }
    return {
        'version': 1,
        'goals': goals,
        'facts': facts,
        'missing_fact_count': len(missing),
        'next_actions': unique[:8],
        'gaps': list(dict.fromkeys(str(gap) for gap in (gaps or []) if gap))[:12],
        'budget': {
            'limits': {'rounds': 8, 'searches': 4, 'documents': 4, 'tool_calls': 20,
                       'seconds': 180, 'total_tokens': 40000},
            'used': {key: usage.get(key, 0) for key in ('rounds', 'searches', 'documents',
                                                        'tool_calls', 'total_tokens') if isinstance(usage, dict)},
            'remaining': {
                'searches': max(0, 4 - (usage.get('searches', 0) if isinstance(usage, dict) else 0)),
                'documents': max(0, 4 - (usage.get('documents', 0) if isinstance(usage, dict) else 0)),
                'tool_calls': max(0, 20 - (usage.get('tool_calls', 0) if isinstance(usage, dict) else 0)),
            },
            'stop_policy': '任何预算或时间上限结束时保留已有成果，返回继续计划；不自动重复付费调用。',
        },
        'skills': _skill_contracts(loaded_skills, executed_tools, sources, algorithm, programs, questions, gaps),
        'completion': {
            'ready': ready,
            'statement': ('证据与算法已达到可报告边界；未知项仍保留。' if ready else
                          '当前成果可以查看，但仍有开放目标；不能把模型说完成当作研究完成。'),
            'remaining_goals': [goal['id'] for goal in goals if goal['status'] != 'done'],
        },
        'previous': previous,
        'inheritance': {
            **(deepcopy(inheritance) if isinstance(inheritance, dict) else {}),
            'field_candidate_count': len(field_candidates or ()),
            'reused_sources_and_candidates_are_revalidated': True,
            'recalculation': ('未变化的候选与证据复用；只重算确定性算法。'
                              if isinstance(inheritance, dict) and inheritance.get('reused') else
                              '没有可复用成果，本轮按当前条件重新研究。'),
        },
    }
