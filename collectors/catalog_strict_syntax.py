"""Bounded literal catalogue grammar; uncertainty rejects the whole input cell."""
import re

from .catalogs import NATIONAL, compact, evidence


MAX_CELL_CHARS = 65536
MAX_ITEMS = 256
_OPEN = {'(': ')', '（': '）', '[': ']', '【': '】'}
_CLOSE = set(_OPEN.values())
_DIRECTION = re.compile(r'(?P<code>\d{4}(?!\d)|\d{2}(?=\d[A-Za-z]|[^\d]|$)|[A-Za-z]\d|\d[A-Za-z])[ \t]*[.、．:]?[ \t]*(?P<name>.+)', re.S)
_INTERNAL_CODE = re.compile(r'(?<![A-Za-z0-9])\d{2}(?!\d)(?=[\u4e00-\u9fffA-Za-z])')
_SLOT_CHARS = dict(zip('①②③④⑴⑵⑶⑷', [1, 2, 3, 4] * 2))
_MARK = re.compile(r'[①②③④⑴⑵⑶⑷]|(?<!\d)[1-4][、.．](?=\s*[1-9]\d{2})|科目[一二三四][:：]|[（(][1-4][）)](?=\s*[1-9]\d{2})')
_TAIL_MARK = re.compile(r'(?:(?<!\d)[1-4][、.．]|[（(][1-4][）)])(?=[\s/—－-]*(?:无|不考|不设考试科目)?\Z)')
_RESIDUAL_CODE = re.compile(r'(?<!\d)[1-9]\d{2}(?!\d)')


def _bounded(text):
    return isinstance(text, str) and bool(text.strip()) and len(text) <= MAX_CELL_CHARS


def _balanced(text):
    stack = []
    for char in text:
        if char in _OPEN:
            stack.append(_OPEN[char])
            if len(stack) > 64:
                return False
        elif char in _CLOSE and (not stack or stack.pop() != char):
            return False
    return not stack


def _direction_error(reason):
    return {'directions': [], 'issues': [reason]}


def _internal_direction(text):
    stack = []
    for index, char in enumerate(text):
        if char in _OPEN:
            stack.append(_OPEN[char])
        elif char in _CLOSE:
            stack.pop()
        elif not stack and _INTERNAL_CODE.match(text, index):
            return True
    return False


def _printed_direction(value):
    match = _DIRECTION.fullmatch(value.strip(' \t\r\n；;。'))
    if not match:
        return None
    code, name = match['code'], match['name'].strip(' \t\r\n；;。')
    if not name or _internal_direction(name):
        return None
    return {'code': code, 'name': name, 'label': code + name}


def _direction_starts(text):
    stack, starts, last = [], [], None
    for index, char in enumerate(text):
        if char in _OPEN:
            stack.append(_OPEN[char])
        elif char in _CLOSE:
            stack.pop()
        else:
            boundary = last in {None, '；', ';'}
            if not stack and boundary and _DIRECTION.match(text, index):
                starts.append(index)
        if char in '\r\n':
            last = None
        elif not char.isspace():
            last = char
    return starts


def parse_strict_directions(text, code=None):
    """Read complete printed labels; an alpha code needs its own explicit column."""
    if not _bounded(text) or not _balanced(text):
        return _direction_error('方向格为空、过长或括号未闭合，未猜测方向')
    value = text.strip()
    if code is not None:
        if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9]{2,4}', code.strip()):
            return _direction_error('方向编号列未声明唯一支持的字面编号')
        code = code.strip()
        printed = _DIRECTION.fullmatch(value)
        if printed and printed['code'] != code:
            return _direction_error('方向编号列与名称格中的编号冲突')
        if (value == code or ';' in value or '；' in value
                or any(_DIRECTION.match(line.lstrip()) for line in value.splitlines()[1:])):
            return _direction_error('方向编号列对应的名称为空或混列多个方向')
        return {'directions': [{'code': code, 'name': value, 'label': code + value}], 'issues': []}
    if re.fullmatch(r'(?:不区分|不设|不分)(?:研究)?方向', value):
        return {'directions': [{'code': '', 'name': value, 'label': value}], 'issues': []}
    starts = _direction_starts(value)
    if not starts or starts[0] != 0 or len(starts) > MAX_ITEMS:
        return _direction_error('方向原格缺少可靠的印刷编号边界')
    directions = [_printed_direction(value[start:starts[index + 1] if index + 1 < len(starts) else len(value)])
                  for index, start in enumerate(starts)]
    if any(direction is None for direction in directions):
        return _direction_error('方向整格存在未分清的编号或名称，未拆出伪方向')
    if len({direction['code'] for direction in directions}) != len(directions):
        return _direction_error('方向原格重复编号，未静默合并')
    return {'directions': directions, 'issues': []}


def _subject_result(subjects=None, refs=None, issues=None, scope='unknown'):
    return {'subjects': subjects or [], 'exam_evidence': refs or [], 'issues': issues or [], 'exam_scope': scope}


def _slot(mark):
    if mark in _SLOT_CHARS:
        return _SLOT_CHARS[mark]
    if mark.startswith('科目'):
        return '一二三四'.index(mark[2]) + 1
    return int(re.search('[1-4]', mark)[0])


def _initial_text(raw):
    return re.split(r'复试(?:科目)?[:：]|同等学力(?:加试|考生)', raw, maxsplit=1)[0]


def _with_trailing_mark(text, marks):
    mark = _TAIL_MARK.search(text.rstrip(), marks[-1].end())
    if not mark or _slot(mark[0]) != len(marks) + 1:
        return marks
    starts = '1234' if mark[0][0] in '1234' else '(（'
    if any(previous[0][0] not in starts for previous in marks):
        raise ValueError('末尾疑似初试单元编号与前文格式不一致')
    return [*marks, mark]


def _sections(raw, slots):
    if slots is not None:
        if (not isinstance(slots, dict) or len(slots) not in {2, 3, 4}
                or any(type(slot) is not int for slot in slots)
                or set(slots) != set(range(1, len(slots) + 1))
                or any(not isinstance(value, str) or len(value) > MAX_CELL_CHARS for value in slots.values())):
            raise ValueError('初试列的单元声明不连续或类型无效')
        result = []
        for slot, value in sorted(slots.items()):
            text = _initial_text(value).strip()
            mark = _MARK.match(text)
            if mark:
                if _slot(mark[0]) != slot:
                    raise ValueError('初试列角色与原格单元编号冲突')
                text = text[mark.end():]
            result.append((slot, text, value))
        return result
    if not _bounded(raw):
        raise ValueError('初试原格为空、过长或类型无效')
    text = _initial_text(raw)
    marks = list(_MARK.finditer(text))
    if not marks or len(marks) > 4 or re.search('[⑤⑥⑦⑧⑨⑸⑹⑺⑻⑼]', text):
        raise ValueError('原格没有唯一支持的初试单元声明')
    prefix = compact(text[:marks[0].start()])
    if prefix and not re.fullmatch(r'(?:初试(?:考试)?科目|考试科目)[:：]?', prefix):
        raise ValueError('初试单元前存在未声明的混合内容')
    marks = _with_trailing_mark(text, marks)
    order = [_slot(mark[0]) for mark in marks]
    if order not in [[1, 2], [1, 2, 3], [1, 2, 3, 4]]:
        raise ValueError('初试单元重复、缺失或顺序不明')
    return [(order[index], text[mark.end():marks[index + 1].start() if index + 1 < len(marks) else len(text)], raw)
            for index, mark in enumerate(marks)]


def _starts_code(text):
    value = text.lstrip()
    while value and value[0] in _OPEN:
        value = value[1:].lstrip()
    return bool(re.match(r'[1-9]\d{2}(?!\d)', value))


def _unwrap(value):
    if not value or value[0] not in _OPEN or not _starts_code(value[1:]):
        return value
    stack = []
    for index, char in enumerate(value):
        if char in _OPEN:
            stack.append(_OPEN[char])
        elif char in _CLOSE:
            stack.pop()
            if not stack:
                return value[1:-1].strip() if index == len(value) - 1 else value
    return value


def _choice_parts(value):
    stack, parts, start, index = [], [], 0, 0
    while index < len(value):
        char = value[index]
        if char in _OPEN:
            stack.append(_OPEN[char])
        elif char in _CLOSE:
            stack.pop()
        elif not stack:
            token = next((word for word in ['或者', '或', '/', '、'] if value.startswith(word, index)), None)
            if token and _starts_code(value[index + len(token):]):
                parts.append(value[start:index]); start = index + len(token); index += len(token) - 1
                if len(parts) >= MAX_ITEMS:
                    raise ValueError('初试备选数量超过解析上限')
            elif token in {'或', '或者'}:
                raise ValueError('备选连接词后没有明确的三位科目代码')
        index += 1
    return [*parts, value[start:]] if parts else []


def _alternatives(text, depth=0, choice=False):
    value = text.strip(' \t\r\n；;、，,。')
    if not value or not _balanced(value) or depth > 16:
        raise ValueError('初试备选为空或括号不完整')
    unwrapped = _unwrap(value)
    if unwrapped != value:
        leaves = _alternatives(unwrapped, depth + 1, choice)
    else:
        parts = _choice_parts(value)
        leaves = [leaf for part in parts for leaf in _alternatives(part, depth + 1, True)] if parts else [value]
    if (choice or len(leaves) > 1) and text.rstrip(' \t\r\n；;，,。').endswith(('/', '、')):
        raise ValueError('初试备选分隔符后缺少明确科目')
    if len(leaves) > MAX_ITEMS:
        raise ValueError('初试备选数量超过解析上限')
    return leaves


def _national(code, name):
    value = re.sub(r'^(英语|数学)([一二三])$', r'\1(\2)', compact(name))
    return code in NATIONAL and value in NATIONAL[code]


def _subject(alternative, slot, unit_id, document, quote, group):
    match = re.fullmatch(r'\s*([1-9]\d{2})(?!\d)\s*[-－—:：|]?\s*(.+?)\s*', alternative, re.S)
    if not match:
        raise ValueError('科目代码或完整名称缺失')
    code, name = match[1], match[2].strip(' \t\r\n；;、，,。')
    if (not re.search('[A-Za-z\u4e00-\u9fff]', name) or _RESIDUAL_CODE.search(name)
            or _MARK.search(name) or '或' in name):
        raise ValueError('科目名称仍含未分解编号、单元或备选语法')
    national = _national(code, name)
    if code in NATIONAL and not national:
        raise ValueError('全国统考代码与名称不一致，未改成校内科目')
    result = {'code': code, 'name': name, 'scope': 'national' if national else str(unit_id),
              'exam_unit': slot, 'evidence': evidence(document, quote)}
    return {**result, 'choice_group': f'exam-unit-{slot}'} if group else result


def _exam_scope(subjects):
    single = lambda subject: bool(re.search(r'^(?:单独考试|单考|强军单考)|[（(](?:单独考试|单考|强军单考)[）)]', compact(subject['name'])))
    flags = [(subject['exam_unit'], single(subject)) for subject in subjects]
    if any({flag for number, flag in flags if number == slot} == {True, False} for slot, _ in flags):
        return 'mixed'
    if any(flag for _, flag in flags):
        return 'single'
    return 'unknown' if any(subject['code'] in {'111', '240'} for subject in subjects) else 'unified'


def parse_strict_subjects(raw, unit_id, document, slots=None):
    """Parse every declared unit; blank units and unresolved choices fail together."""
    if (not isinstance(unit_id, str) or not unit_id.strip()
            or not isinstance(document, dict) or any(not isinstance(document.get(key), str) or not document[key]
                                            for key in ['id', 'url'])):
        return _subject_result(issues=['初试证据文档身份缺失'])
    refs, subjects = [], []
    try:
        sections = _sections(raw, slots)
        source_text = '；'.join(section for _, section, _ in sections)
        if re.search(r'成套|任选一组|科目组|方案[一二123]|(?:第?[三四34]|[③④]).{0,25}(?:对应|搭配|配对)', source_text):
            raise ValueError('跨单元成套或对应选择未独立适配，未平铺备选')
        for slot, section, quote in sections:
            if quote.strip() and quote.strip() not in [ref['quote'] for ref in refs]:
                refs.append(evidence(document, quote))
            if re.fullmatch(r'[-—－/]*\s*(?:无|不考|不设考试科目)', section.strip()):
                continue
            if not section.strip():
                raise ValueError(f'第{slot}单元已声明但内容为空，未降格为较少单元')
            choices = _alternatives(section)
            subjects.extend(_subject(choice, slot, unit_id, document, quote, len(choices) > 1) for choice in choices)
        active = {subject['exam_unit'] for subject in subjects}
        if len(active) not in {2, 3, 4} or active != set(range(1, len(active) + 1)):
            raise ValueError('实际初试单元不连续或不足两门')
        if len({(subject['exam_unit'], subject['code']) for subject in subjects}) != len(subjects):
            raise ValueError('同单元原文备选科目重复，未静默合并')
        scope = _exam_scope(subjects)
        if scope in {'mixed', 'unknown'}:
            return _subject_result(refs=refs, issues=['统考与单考混合或适用范围未明'], scope=scope)
        return _subject_result(subjects, refs, scope=scope)
    except (ValueError, TypeError) as error:
        return _subject_result(refs=refs, issues=[str(error)])
