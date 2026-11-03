"""Exact, same-document fee and duration policies; unresolved scopes stay unknown."""
from copy import deepcopy
import math
import re

from public_sources import official_source_url
from service_verification import canonical, plain_text
from .catalogs import evidence
from .history import _Tables


_BREAK = '\u241e'


class _PolicyTables(_Tables):
    def handle_starttag(self, tag, attrs):
        if tag in {'p', 'br', 'li'} and self.stack and self.stack[-1]['cell'] is not None:
            cell = self.stack[-1]['cell']
            if any(part.strip() for part in cell['text']):
                cell['text'].append(_BREAK)
        super().handle_starttag(tag, attrs)


def _clean(value):
    return re.sub(r'\s+', '', value or '')


def _refs(document, values):
    return [evidence(document, value, 1) for value in dict.fromkeys(values) if value]


def _number(value, suffix):
    match = re.fullmatch(r'(\d+(?:\.\d+)?)' + suffix, _clean(value))
    if not match:
        return None
    number = float(match[1])
    if not math.isfinite(number) or not 0 < number <= 10000000:
        return None
    return int(number) if number.is_integer() else number


def _per_year(value):
    return bool(re.search(r'每(?:生)?(?:每)?(?:学)?年|元/(?:生[·./])?(?:学年|年)|元/人/学年', value))


def _table_policies(tables, text, document):
    candidates = []
    equal_modes = '全日制学生与非全日制学生收费标准一致' in text
    for table_index, marked_table in enumerate(tables, 1):
        table = [[cell.replace(_BREAK, '') for cell in row] for row in marked_table]
        header_info = None
        for row_index, row in enumerate(table[:4]):
            name = next((i for i, cell in enumerate(row) if cell in {'专业', '招生专业', '专业名称'}), None)
            fee = next((i for i, cell in enumerate(row) if cell.startswith('学费') or cell == '收费标准'), None)
            if name is not None and fee is not None:
                header_info = (row_index, name, fee, next((i for i, c in enumerate(row) if c == '学制'), None),
                               next((i for i, c in enumerate(row) if c in {'学习形式', '学习方式'}), None))
                break
        if header_info is None:
            continue
        header_index, name_column, fee_column, duration_column, mode_column = header_info
        header = table[header_index]
        degree_column = next((i for i, c in enumerate(header) if c in {'学位类型', '学位类别'}), None)
        for row_index, row in enumerate(table[header_index + 1:], header_index + 2):
            if len(row) <= max(name_column, fee_column):
                continue
            name_text, fee_text = row[name_column], row[fee_column]
            names = [part for part in marked_table[row_index - 1][name_column].split(_BREAK) if part]
            amount = _number(fee_text, r'元(?:/(?:生[·./])?(?:学年|年))?')
            annual = _per_year(header[fee_column]) or _per_year(fee_text)
            duration = _number(row[duration_column], r'年') if duration_column is not None and len(row) > duration_column else None
            mode = row[mode_column] if mode_column is not None and len(row) > mode_column else None
            modes = [mode] if mode in {'全日制', '非全日制'} else ['全日制', '非全日制'] if mode is None and equal_modes else []
            if not name_text or not modes:
                continue
            fee_quotes = [header[fee_column], name_text, fee_text]
            continuous = ''.join(row[name_column:fee_column + 1])
            if continuous in text:
                fee_quotes = [header[fee_column], continuous]
            duration_quotes = [header[duration_column], name_text, row[duration_column]] if duration is not None else []
            if duration is not None:
                continuous = ''.join(row[name_column:duration_column + 1])
                if continuous in text:
                    duration_quotes = [continuous]
            candidates.append({'name_text': name_text, 'names': names, 'modes': modes, 'amount': amount if annual else None,
                               'duration': duration, 'tuition_evidence': _refs(document, fee_quotes),
                               'duration_evidence': _refs(document, duration_quotes),
                               'degree_type': row[degree_column] if degree_column is not None and len(row) > degree_column else None,
                               'locator': {'table': table_index, 'row': row_index}})
    return candidates


def _general_policies(text, document):
    candidates = []
    patterns = (
        (r'所有学科专业学制均为(\d+(?:\.\d+)?)年', 'duration', ['全日制', '非全日制']),
        (r'学校招收的硕士研究生为专业学位硕士研究生，学制为(\d+(?:\.\d+)?)年', 'duration', ['全日制', '非全日制']),
        (r'我所硕士研究生学制为(\d+(?:\.\d+)?)年，学习方式为全日制', 'duration', ['全日制']),
        (r'我校各招生专业学费均为(\d+(?:\.\d+)?)元/人/学年', 'amount', ['全日制', '非全日制']),
        (r'我所对2027年度新入学研究生收取学费，收费标准为每生每学年(\d+(?:\.\d+)?)元，学费按每学年收取', 'amount', ['全日制']),
    )
    for pattern, field, modes in patterns:
        for match in re.finditer(pattern, text):
            value = float(match[1])
            candidates.append({'name_text': None, 'modes': modes, field: int(value) if value.is_integer() else value,
                               ('tuition_evidence' if field == 'amount' else 'duration_evidence'): _refs(document, [match[0]])})
    return candidates


def _liaoning_duration(text, document):
    statement = '全日制：会计硕士学制2年；翻译硕士学制2.5年；其他各专业（领域）硕士学制均为3年'
    candidates = []
    # This complete exception sentence is matched as a unit, never the trailing default alone.
    if statement in text:
        candidates.append({'name_text': None, 'modes': ['全日制'], 'duration': 3,
                           'duration_exceptions': {'125300': 2, '0551': 2.5},
                           'duration_evidence': _refs(document, [statement])})
    statement = '非全日制（一次性缴纳）：会计硕士、工商管理硕士学制2.5年；工学专业学位学制均为3.5年'
    if statement in text:
        candidates.extend({'name_text': None, 'modes': ['非全日制'], 'duration': years,
                           'code_prefixes': prefixes, 'duration_evidence': _refs(document, [statement])}
                          for prefixes, years in [(['125300', '125100'], 2.5), (['085'], 3.5)])
    return candidates


def _reviewed_policies(text, unit, document):
    # Source-specific clauses are deliberately exact; changes need review, not a broader default.
    rules = {
        '367988': [(r'我院2027年面向全国招收全日制学术型硕士和专业学位硕士研究生，学制(3)年', 'duration'),
                   (r'学费标准[:：]学术型、专业型硕士生(8000)元/年', 'amount')],
        '367986': [(r'学费为每年(8000)元（生活困难学生可按我院奖助政策申请学费相应标准的减免）', 'amount')],
        '367782': [(r'我院招收的硕士研究生培养方式是全日制，学制为(3)年，修业年限最长为4年', 'duration')],
        '367898': [(r'中监所全日制硕士研究生基本学制为(3)年，因特殊情况经批准最长可延期2年毕业', 'duration'),
                   (r'中监所硕士研究生学费为每生每年(8000)元', 'amount')],
        '3416382019': [(r'2027年我校“食品与营养”“资源与环境”2个专业学位硕士点计划招收全日制硕士研究生（详见《安康学院2027年硕士研究生招生专业目录》），学制(三)年', 'duration'),
                       (r'各招生专业学费为(8000)元/人(?:·|&middot;)学年', 'amount')],
        '368101': [(r'学习形式为全日制。基本学制(2\.5)年，特别优秀的学生允许提前毕业', 'duration')],
        '368103': [(r'录取研究生学习方式为全日制、学制为(3)年', 'duration')],
        '368531': [(r'我所研究生录取类别为非定向培养研究生，学习形式为全日制，研究生培养学制为(3)年', 'duration')],
        '368496': [(r'航天科工十院招收的是国家计划内全日制学术型定向硕士研究生，学制(2\.5)年', 'duration')],
    }
    candidates = []
    for pattern, field in rules.get(str(unit.get('id')), []):
        for match in re.finditer(pattern, text):
            value = 3 if match[1] == '三' else float(match[1])
            candidates.append({'name_text': None, 'modes': ['全日制'], field: value,
                               ('tuition_evidence' if field == 'amount' else 'duration_evidence'): _refs(document, [match[0]])})
    if str(unit.get('id')) == '367788' and '学术学位学制2.5年，专业学位学制3年，目前暂不收学费' in text:
        quote = '学术学位学制2.5年，专业学位学制3年，目前暂不收学费'
        candidates.extend({'name_text': None, 'modes': ['全日制'], 'duration': value,
                           'degree_types': [degree], 'duration_evidence': _refs(document, [quote])}
                          for degree, value in [('学术学位', 2.5), ('专业学位', 3)])
    return candidates


def extract_inline_policies(html, unit, year, document):
    text = _clean(plain_text(html))
    correct = (type(year) is int and year == 2027 and str(document.get('unit_id')) == str(unit.get('id'))
               and document.get('year') == year and document.get('status') in {'parsed', 'verified'}
               and official_source_url(document.get('url'), unit)
               and re.search(re.escape(unit.get('name', '')) + r'2027年[^。]{0,45}硕士[^。]{0,45}招生', text))
    if not correct:
        return {'candidates': [], 'general_policies': []}
    parser = _PolicyTables()
    parser.feed(html)
    return {'candidates': _table_policies(parser.tables, text, document),
            'general_policies': _general_policies(text, document) + _liaoning_duration(text, document) + _reviewed_policies(text, unit, document)}


def _matches(candidate, program):
    if program.get('study_mode') not in candidate['modes']:
        return False
    if candidate.get('code_prefixes') and not any(str(program.get('code', '')).startswith(prefix) for prefix in candidate['code_prefixes']):
        return False
    if candidate.get('degree_types') and program.get('degree_type') not in candidate['degree_types']:
        return False
    if candidate.get('degree_type') and program.get('degree_type') and candidate['degree_type'] != program['degree_type']:
        return False
    names = candidate.get('name_text')
    if names is None:
        return True
    name = canonical(program.get('name', ''))
    if str(program.get('unit_id')) == '302975907':
        aliases = {('085401', '新一代电子信息技术(含量子技术等)'): '新一代电子信息技术',
                   ('085905', '市政工程(含给排水等)'): '市政工程',
                   ('085906', '人工环境工程(含供热、通风及空调等)'): '人工环境工程'}
        name = aliases.get((program.get('code'), name), name)
    project = bool(re.search(r'中外合作|联合培养', names))
    program_project = bool(re.search(r'中外合作|联合培养', str(program.get('direction', '')) + str(program.get('notes', '')) + program.get('name', '')))
    if project != program_project:
        return False
    normalize = lambda value: re.sub(r'\((?:中外合作办学|联合培养)\)', '', canonical(value))
    return len(name) >= 2 and normalize(name) in {normalize(value) for value in candidate['names']}


def _field(matches, field, program):
    values, refs = [], []
    for candidate in matches:
        value = candidate.get(field)
        if field == 'duration' and candidate.get('duration_exceptions'):
            value = next((duration for prefix, duration in candidate['duration_exceptions'].items()
                          if str(program.get('code', '')).startswith(prefix)), value)
        if value is not None and value > 0:
            values.append(value)
            refs.extend(candidate.get('tuition_evidence' if field == 'amount' else 'duration_evidence', []))
    return (values[0], refs) if values and len(set(values)) == 1 else (None, [])


def apply_inline_policies(programs, html, unit, year, document):
    """Attach only unique explicit amounts, annual periods and same-school scopes."""
    policies = extract_inline_policies(html, unit, year, document)
    result = []
    for program in programs:
        updated = deepcopy(program)
        if str(program.get('unit_id')) != str(unit.get('id')) or program.get('year') != year:
            result.append(updated)
            continue
        matches = [candidate for candidate in [*policies['candidates'], *policies['general_policies']] if _matches(candidate, program)]
        amount, fee_refs = _field(matches, 'amount', program)
        duration, duration_refs = _field(matches, 'duration', program)
        if program.get('tuition') is None and amount is not None:
            updated = {**updated, 'tuition': {'amount': amount, 'period': 'year', 'currency': 'CNY', 'evidence': fee_refs}}
        if program.get('duration_years') is None and duration is not None:
            updated = {**updated, 'duration_years': duration, 'duration_evidence': duration_refs}
        result.append(updated)
    return result
