"""Coordinate-aware public PDF tables with independent raw-text citations."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

from .catalog_html import _subjects
from .catalogs import _program, compact, evidence
from .documents import DocumentError, PROJECT_ROOT, extract_pdf_text


PARSER = 'pdf_table_v1'
_MAJOR = re.compile(r'^([0-9A-Z]{6}|[0-9A-Z]{4})(?![0-9A-Z])\s*(.+)')
_DIRECTION = re.compile(r'^[0-9A-Z]{2}(?![0-9A-Z])\s*[^\d\W_].*', re.UNICODE)


def _clean(value):
    return re.sub(r'\s+', '', value or '')


def extract_pdf_layout(path):
    """Poppler bbox is separate from the established raw-text extraction engine."""
    local = PROJECT_ROOT / 'tools/poppler-26.07.0' / ('pdftotext.exe' if os.name == 'nt' else 'pdftotext')
    executable = os.environ.get('GRAD_PDFTOTEXT') or (str(local) if local.is_file() else shutil.which('pdftotext'))
    if not executable:
        raise DocumentError('PDF坐标解析需要支持-bbox-layout的Poppler pdftotext')
    try:
        process = subprocess.run([executable, '-bbox-layout', '-enc', 'UTF-8', str(path), '-'],
                                 capture_output=True, check=True, timeout=90)
        if len(process.stdout) > 64 * 1024 * 1024:
            raise DocumentError('PDF坐标文本超过64MB，保留原文等待分批解析')
        tree = ET.fromstring(process.stdout.decode('utf-8'))
    except (subprocess.SubprocessError, OSError, UnicodeDecodeError, ET.ParseError) as error:
        raise DocumentError('PDF坐标提取失败；请指定支持-bbox-layout的Poppler，原目录保留待复核') from error
    pages = []
    for page_node in (node for node in tree.iter() if node.tag.endswith('}page') or node.tag == 'page'):
        lines = []
        for node in (node for node in page_node.iter() if node.tag.endswith('}line') or node.tag == 'line'):
            words = []
            for word in node:
                if not (word.tag.endswith('}word') or word.tag == 'word'):
                    continue
                words.append({'text': ''.join(word.itertext()), **{key: float(word.attrib[attr])
                              for key, attr in [('x0', 'xMin'), ('x1', 'xMax'), ('y0', 'yMin'), ('y1', 'yMax')]}})
            if words:
                lines.append({'text': ' '.join(w['text'] for w in words), 'words': words,
                              **{key: float(node.attrib[attr]) for key, attr in
                                 [('x0', 'xMin'), ('x1', 'xMax'), ('y0', 'yMin'), ('y1', 'yMax')]}})
        pages.append({'page': len(pages) + 1, 'width': float(page_node.attrib['width']),
                      'height': float(page_node.attrib['height']), 'lines': lines,
                      'words': [word for line in lines for word in line['words']]})
    cairo = Path(executable).with_name('pdftocairo.exe' if os.name == 'nt' else 'pdftocairo')
    for page in pages:
        table_words = any(re.match(r'^①\s*[1-9]\d{2}', w['text']) for w in page['words'])
        page['grid'] = _pdf_grid(cairo, path, page['page']) if cairo.is_file() and (_header(page) or table_words) else []
    return {'pages': pages, 'raw_text': extract_pdf_text(path)}


def _pdf_grid(executable, path, page):
    try:
        process = subprocess.run([str(executable), '-svg', '-f', str(page), '-l', str(page), str(path), '-'],
                                 capture_output=True, check=True, timeout=45)
        tree = ET.fromstring(process.stdout)
    except (subprocess.SubprocessError, OSError, ET.ParseError) as error:
        raise DocumentError('PDF表格边界提取失败，不能猜测合并单元格作用范围') from error
    return _svg_grid(tree)


def _draw_nodes(tree):
    pending = [(tree, [1, 0, 0, 1, 0, 0])]
    number = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
    while pending:
        node, parent = pending.pop()
        if node.tag.rsplit('}', 1)[-1] in {'defs', 'symbol', 'clipPath'}:
            continue
        transform = re.fullmatch(r'matrix\(([^)]+)\)', node.get('transform', ''))
        local = list(map(float, re.findall(number, transform[1]))) if transform else [1, 0, 0, 1, 0, 0]
        if len(local) != 6 or (node.get('transform') and not transform):
            continue
        a,b,c,d,e,f = parent
        g,h,i,j,k,l = local
        matrix = [a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f]
        yield node, matrix
        pending.extend((child, matrix) for child in reversed(list(node)))


def _svg_grid(tree):
    segments = []
    number = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
    pattern = r'\s*M\s*(' + number + r')\s+(' + number + r')\s+L\s*(' + number + r')\s+(' + number + r')\s*'
    for node, matrix in _draw_nodes(tree):
        match = re.fullmatch(pattern, node.get('d', ''))
        rectangle = re.fullmatch(r'\s*M\s*' + number + r'\s+' + number + r'(?:\s+L\s*' + number + r'\s+' + number + r'){3}\s+Z(?:\s+M\s*' + number + r'\s+' + number + r')?\s*', node.get('d', ''))
        if match and node.get('stroke', 'none') != 'none':
            coords = list(map(float, match.groups()))
        elif rectangle and node.get('fill', 'none') != 'none' and '100%' not in node.get('fill', ''):
            values = list(map(float, re.findall(number, node.get('d'))))[:8]
            xs, ys = values[::2], values[1::2]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            if x1-x0 < 1.5 and y1-y0 >= 20:
                coords = [(x0+x1)/2, y0, (x0+x1)/2, y1]
            elif y1-y0 < 1.5 and x1-x0 >= 20:
                coords = [x0, (y0+y1)/2, x1, (y0+y1)/2]
            else:
                continue
        else:
            continue
        a, b, c, d, e, f = matrix
        x0, y0, x1, y1 = coords
        x0, y0, x1, y1 = a*x0+c*y0+e, b*x0+d*y0+f, a*x1+c*y1+e, b*x1+d*y1+f
        if (abs(x0-x1) < .8 and abs(y1-y0) >= 20) or (abs(y0-y1) < .8 and abs(x1-x0) >= 20):
            segments.append([min(x0,x1), min(y0,y1), max(x0,x1), max(y0,y1)])
    return segments


def _cell(page, x, y):
    horizontal = [line for line in page.get('grid', []) if abs(line[1]-line[3]) < .8 and line[0]-.8 <= x <= line[2]+.8]
    vertical = [line for line in page.get('grid', []) if abs(line[0]-line[2]) < .8 and line[1]-.8 <= y <= line[3]+.8]
    above = [line[1] for line in horizontal if line[1] < y-.4]
    below = [line[1] for line in horizontal if line[1] > y+.4]
    left = [line[0] for line in vertical if line[0] < x-.4]
    right = [line[0] for line in vertical if line[0] > x+.4]
    return [max(left), max(above), min(right), min(below)] if above and below and left and right else None


def _cell_lines(page, box):
    rows = []
    for line in page['lines']:
        words = [word for word in line['words'] if box[0] <= (word['x0']+word['x1'])/2 <= box[2]
                 and box[1] <= (word['y0']+word['y1'])/2 <= box[3]]
        if words:
            rows.append((page['page'], {**line, 'words': words, 'text': ' '.join(word['text'] for word in words)}))
    return sorted(rows, key=lambda row: (row[1]['y0'], row[1]['x0']))


def _header(page):
    lines = page['lines']
    initial = next((line for line in sorted(lines, key=lambda row: row['y0'])
                    if re.search(r'初试科目|^考试科目$', _clean(line['text']))), None)
    if initial is None:
        return None
    headers = [line for line in lines if abs(line['y0'] - initial['y0']) < 28]
    major = next((line for line in headers if re.search(r'专业代码|学科代码|专业名称|^专业$', _clean(line['text']))), None)
    if major is None:
        return None
    initial_words = [w for w in initial['words'] if '初试' in w['text'] or w['text'] == '考试科目']
    anchor = initial_words[0]['x0'] if initial_words else initial['x0']
    right_headers = [line['x0'] for line in headers if line['x0'] > anchor + 15
                     and re.search(r'复试|备注|同等学力|加试|是否招收|报考条件', _clean(line['text']))]
    next_column = min(right_headers, default=page['width'] - 10)
    markers = [w['x0'] for w in page['words'] if w['y0'] > initial['y1']
               and re.match(r'^[①②③④]\s*(?:[1-9]\d{2}|[-—无])', w['text'])
               and major['x0']+20 < w['x0'] < next_column - 5]
    left = min(markers, key=lambda x: abs(x - anchor)) - 3 if markers else anchor - 10
    right = (left + next_column) / 2 if right_headers else next_column
    counts = [line['x0'] for line in headers if line['x0'] < left
              and line['x0'] > major['x0']+15
              and re.search(r'拟招|人数|招生计划|推免', _clean(line['text']))]
    mode_line = next((line for line in headers if re.search(r'学习方式|学习形式', _clean(line['text']))), None)
    mode_x = (mode_line['x0']+mode_line['x1'])/2 if mode_line else None
    initial_box = _cell(page, (initial['x0']+initial['x1'])/2, (initial['y0']+initial['y1'])/2)
    if initial_box:
        left, right = initial_box[0], initial_box[2]
    identity_right = min(counts, default=left) - 3
    if mode_line:
        mode_box = _cell(page, mode_x, (mode_line['y0']+mode_line['y1'])/2)
        identity_right = min(identity_right, mode_box[0] if mode_box else mode_line['x0']-10)
    direction_line = next((line for line in headers if _clean(line['text']) == '研究方向'), None)
    direction_box = _cell(page, (direction_line['x0']+direction_line['x1'])/2,
                          (direction_line['y0']+direction_line['y1'])/2) if direction_line else None
    return {'y': max(initial['y1'], major['y1']) + 1, 'left': left, 'right': right,
            'major_right': identity_right, 'major_x': major['x0'],
            'major_name_right': direction_box[0] if direction_box else identity_right,
            'department_x': next(((line['x0']+line['x1'])/2 for line in headers
                                  if line['x0'] < major['x0']-10 and re.search(r'学院|院系', _clean(line['text']))), None),
            'mode_x': mode_x,
            'header': initial, 'page': page['page']}


def _headers(layout):
    result, previous = {}, None
    for page in layout['pages']:
        header = _header(page)
        if header is None and previous and page.get('grid'):
            markers = [w for w in page['words'] if re.match(r'^①\s*[1-9]\d{2}', w['text'])
                       and abs(w['x0']-previous['left']) < 25]
            majors = [line for line in page['lines'] if _MAJOR.match(line['text']) and line['x0'] < previous['left']]
            if markers and majors:
                header = {**previous, 'y': 10, 'page': page['page'], 'inherited_header': True}
        if header:
            result[page['page']], previous = header, header
    return result


def _select(line, left, right):
    words = [word for word in line['words'] if left <= word['x0'] < right]
    return {**line, 'words': words, 'text': ' '.join(word['text'] for word in words)} if words else None


def _department(line):
    text = _clean(line['text'])
    match = re.match(r'(?:院系所代码及名称[:：])?(\d{3})[-—]?([^\d].*)', text)
    if not match or not re.search(r'学院|学部|学系|中心|研究所|实验室', match[2]):
        return None
    name = re.split(r'[（(]联系电话|拟招生人数|联系人', match[2], maxsplit=1)[0]
    return match[1] + name.rstrip('（(')


def _regions(layout):
    regions, manifests, current, department = [], [], None, None
    headers = _headers(layout)
    for page in layout['pages']:
        header = headers.get(page['page'])
        if header is None:
            continue
        manifests.append({'page': page['page'], 'initial_x': [header['left'], header['right']]})
        ordered = sorted(page['lines'], key=lambda row: (row['y0'], row['x0']))
        intro = [(line, _department(line)) for line in ordered if line['y0'] < header['y']]
        intro = [(line, name) for line, name in intro if name]
        if intro:
            line, name = intro[-1]
            department = {'name': name, 'lines': [(page['page'], line)]}
        body = [line for line in ordered if header['y'] <= line['y0'] <= page['height'] - 25]
        majors = [(line, selected) for line in body
                  if (selected := _select(line, 0, header['major_name_right'])) and _MAJOR.match(selected['text'])]
        page_regions = []
        for index, (line, identity) in enumerate(majors):
            major = _MAJOR.match(identity['text'])
            if index:
                previous_top = majors[index - 1][0]['y0']
                prior = [candidate['y1'] for candidate in body if previous_top <= candidate['y0'] < line['y0']
                         and (part := _select(candidate, 0, header['major_right']))
                         and (_DIRECTION.match(_clean(part['text'])) or _MAJOR.match(part['text']))]
                top = (max(prior, default=previous_top) + line['y0']) / 2
            else:
                top = header['y'] if current is None else line['y0']
            box = _cell(page, identity['words'][0]['x0']+2, (identity['y0']+identity['y1'])/2)
            if box:
                top = box[1]
            region = {'code': major[1], 'name': major[2], 'department': department,
                      'identity': [(page['page'], identity)], 'exam': [], 'notes': [],
                      'page': page['page'], 'top': top, 'major_x': identity['x0']}
            page_regions.append(region)
            regions.append(region)
        previous = current
        for source in body:
            if source['y0'] < header['y'] or source['y0'] > page['height'] - 25:
                continue
            identity = _select(source, 0, header['major_right'])
            exam = source if header['left'] <= source['x0'] < header['right'] else None
            notes = _select(source, header['right'], page['width'])
            current = next((region for region in reversed(page_regions) if region['top'] <= source['y0']), previous)
            name = _department(identity) if identity else None
            if name:
                department = {'name': name, 'lines': [(page['page'], identity)]}
                if current:
                    current['department'] = department
                continue
            major_part = _select(source, 0, header['major_name_right'])
            major = _MAJOR.match(major_part['text']) if major_part else None
            if major:
                if current:
                    current['department'] = department
                    remainder = _select(source, header['major_name_right'], header['major_right'])
                    if remainder:
                        current['identity'].append((page['page'], remainder))
            elif current and identity and _clean(identity['text']):
                current['identity'].append((page['page'], identity))
            if current and exam:
                current['exam'].append((page['page'], exam))
            if current and notes:
                current['notes'].append((page['page'], notes))
    return regions, manifests


def _references(rows, document, raw_pages):
    refs, missing = [], False
    for page, line in rows:
        raw = compact(raw_pages[page - 1]) if 0 < page <= len(raw_pages) else ''
        values = [line['text']] if compact(line['text']) in raw else [w['text'] for w in line['words']]
        for value in values:
            if compact(value) and compact(value) in raw:
                refs.append(evidence(document, value, page))
            elif compact(value):
                missing = True
    return list({(r['page'], r['quote']): r for r in refs}.values()), missing


def _mode(value):
    return list(dict.fromkeys(re.findall(r'非全日制|(?<!非)全日制', value)))


def _identities(region):
    directions, continuation, name_rows = [], [], [region['identity'][0]]
    for page, line in region['identity'][1:]:
        text = _clean(line['text'])
        if _DIRECTION.match(text):
            directions.append({'text': text, 'rows': [(page, line)]})
        elif directions and line['x0'] >= region['major_x'] - 3 and not re.search(r'联系电话|学院|拟招|第\d+页', text):
            directions[-1]['text'] += text
            directions[-1]['rows'].append((page, line))
        elif not directions and line['x0'] >= region['major_x'] - 3 and re.match(r'^[\u4e00-\u9fff（(]', text):
            continuation.append(text)
            name_rows.append((page, line))
    name = _clean(region['name']) + ''.join(continuation)
    degree = '专业学位' if '专业学位' in name else '学术学位' if '学术学位' in name else None
    name = re.sub(r'[（(](?:学术学位|专业学位|全日制|非全日制)[）)]', '', name)
    original_name = _clean(region['name']) + ''.join(continuation)
    return name, degree, directions, _mode(original_name), name_rows


def _initial_rows(rows):
    result = []
    for page, line in rows:
        boundary = re.search(r'复试(?:专业)?(?:课|科目)?|同等学力|加试|参考书目', line['text'])
        if boundary:
            prefix = line['text'][:boundary.start()].strip()
            if prefix:
                result.append((page, {**line, 'text': prefix}))
            break
        result.append((page, line))
    return result


def _target_exam(region, target, pages, headers, document, raw_pages, unit_id):
    page_number, identity = (target.get('anchor_rows') or target['rows'] or region['identity'])[0]
    page = pages.get(page_number)
    header = headers.get(page_number)
    box = _cell(page, (header['header']['x0']+header['header']['x1'])/2,
                (identity['y0']+identity['y1'])/2) if header else None
    if box is None:
        return [], [], ['初试单元格的真实PDF网格边界未能核对']
    rows = _initial_rows(_cell_lines(page, box))
    text = _clean(''.join(line['text'] for _, line in rows))
    if re.search(r'任选一组|考试科目组合|方案[一二123]|科目组[一二123]', text):
        return [], [], ['跨单元考试组合需专门适配，不能平铺为独立备选']
    subjects, _, issues = _subjects(text, unit_id, document)
    refs, missing = _references(rows, document, raw_pages)
    if missing:
        issues.append('PDF坐标文字与原文页无法逐字对照')
    for subject in subjects:
        quote = subject['code'] + subject['name']
        subject['evidence'] = evidence(document, quote if compact(quote) in compact(raw_pages[page_number-1]) else subject['code'], page_number)
    return subjects, refs, issues


def _target_department(region, target, pages, headers):
    page_number, identity = (target.get('anchor_rows') or target['rows'] or region['identity'])[0]
    page, header = pages.get(page_number), headers.get(page_number)
    if header and header.get('department_x') is not None:
        box = _cell(page, header['department_x'], (identity['y0']+identity['y1'])/2)
        if box:
            rows = _cell_lines(page, box)
            text = _clean(''.join(line['text'] for _, line in rows))
            if re.fullmatch(r'\d{3}[^\d]{2,60}', text):
                return {'name': text, 'lines': rows}
    return region['department']


def _target_modes(region, target, pages, headers, fallback):
    modes = _mode(target['text']) or fallback
    page_number, identity = (target.get('anchor_rows') or target['rows'] or region['identity'])[0]
    page, header = pages.get(page_number), headers.get(page_number)
    if header and header.get('mode_x') is not None:
        box = _cell(page, header['mode_x'], (identity['y0']+identity['y1'])/2)
        if box:
            rows = _cell_lines(page, box)
            return _mode(''.join(line['text'] for _, line in rows)), rows
    return modes, []


def _registration_policy(layout):
    first = layout['pages'][0] if layout.get('pages') else {}
    text = _clean(''.join(line['text'] for line in first.get('lines', [])))
    all_programs = '以下专业均按专业招生' in text
    marked = '仅标“★”的专业按研究方向招生' in text and '其他专业均按专业招生' in text
    if not (all_programs or marked) or '00不区分研究方向' not in text or '表中所列研究方向仅供参考' not in text:
        return None
    rows = [(first['page'], line) for line in first['lines']
            if re.search(r'仅标|其他专业均按专业|以下专业均按专业|网上报名|究方向|仅供参考', _clean(line['text']))]
    return {'all': all_programs, 'rows': rows}


def _parse_region(region, title_rows, default_modes, unit, year, document, raw_pages, pages, headers, policy):
    name, degree, directions, inline_modes, name_rows = _identities(region)
    starred = '★' in name
    name = name.replace('★', '')
    modes = inline_modes or default_modes
    targets = directions or [{'text': '', 'rows': []}]
    grouped_topics = bool(policy and (policy['all'] or not starred))
    if grouped_topics:
        targets = [{'text': '00不区分研究方向', 'rows': policy['rows'],
                    'anchor_rows': (directions[0]['rows'] if directions else region['identity'][:1])}]
    expected = sum(max(1, len(_mode(target['text']) or modes)) for target in targets)
    all_text = ''.join(line['text'] for _, line in region['identity'] + region['exam'] + region['notes'])
    if re.search(r'任选一组|考试科目组合|方案[一二123]|科目组[一二123]|仅招收推免|只招收推免|单独考试', all_text):
        return [], expected, ['跨单元组合或特殊招生范围未独立确认，保留原文待复核']
    issues = []
    identity_refs, identity_missing = _references(name_rows + title_rows, document, raw_pages)
    if identity_missing:
        issues.append('PDF坐标文字与原文页无法逐字对照')
    if issues:
        return [], expected, issues
    programs = []
    for target in targets:
        target_modes, mode_rows = _target_modes(region, target, pages, headers, modes)
        if not target_modes:
            issues.append('原文未明确该专业或方向的学习方式')
            continue
        subjects, exam_refs, exam_issues = _target_exam(region, target, pages, headers, document, raw_pages, unit['id'])
        if exam_issues:
            issues.extend(f'{target["text"] or "专业"}：{issue}' for issue in exam_issues)
            continue
        if grouped_topics:
            signatures = []
            for topic in directions:
                topic_subjects, _, topic_issues = _target_exam(region, topic, pages, headers, document, raw_pages, unit['id'])
                signatures.append([(s['exam_unit'], s['code'], compact(s['name'])) for s in topic_subjects] if not topic_issues else None)
            if any(signature != [(s['exam_unit'], s['code'], compact(s['name'])) for s in subjects] for signature in signatures):
                issues.append('按专业报名的参考课题存在不同初试组合，不能合并为未经核对的确定科目')
                continue
        direction_refs, missing = _references(target['rows'], document, raw_pages)
        department = _target_department(region, target, pages, headers)
        department_refs, department_missing = _references((department or {}).get('lines', []), document, raw_pages)
        mode_refs, mode_missing = _references(mode_rows, document, raw_pages)
        if missing or department_missing or mode_missing:
            return [], expected, ['研究方向原文无法逐字定位']
        for mode in target_modes:
            program = _program(unit, year, (department or {}).get('name', ''), region['code'], name,
                               target['text'], mode, subjects, '', document, region['page'], degree=degree)
            program = {**program, 'evidence': [*identity_refs, *department_refs, *mode_refs, *direction_refs], 'exam_evidence': exam_refs,
                       'catalog_parser': PARSER, 'source_locator': {'page': region['page'], 'top': region['top'], 'code': region['code']}}
            if not target['text']:
                program['direction_scope'] = 'program'
            if grouped_topics:
                program['direction_scope'] = 'program'
                program['research_topics'] = [topic['text'] for topic in directions]
            if not department:
                program['department_scope'] = 'unit'
            if len(region['code']) == 4:
                program['code_level'] = 'discipline'
            programs.append(program)
    return programs, expected, issues


def _has_title(layout, unit, year):
    name = compact(unit.get('name', ''))
    for page in layout.get('pages', []):
        lines = sorted(page['lines'], key=lambda row: (row['y0'], row['x0']))
        for index, line in enumerate(lines):
            value = compact(line['text'])
            if not value.startswith(name) or line['y0'] > page['height']*.4:
                continue
            candidates = [value]
            if index+1 < len(lines) and lines[index+1]['y0'] < line['y1']+40 and (value == name or value.startswith(name+str(year))):
                candidates.append(value + compact(lines[index+1]['text']))
            if any(text.startswith(name+str(year)+'年') and '硕士' in text
                   and re.search(r'招生.*(?:目录|章程|简章)', text) for text in candidates):
                return True
    return False


def _source_conflict(layout, unit, year):
    owner = compact(unit.get('name', ''))
    for page in layout.get('pages', []):
        lines = sorted(page['lines'], key=lambda row: (row['y0'], row['x0']))
        for index, line in enumerate(lines):
            value = compact(line['text'])
            if line['y0'] > page['height']*.4 or len(value) > 180:
                continue
            complete = re.search(r'硕士.*招生.*(?:目录|章程|简章)', value)
            if not complete and index+1 < len(lines) and lines[index+1]['y0'] < line['y1']+40:
                value += compact(lines[index+1]['text'])
            if not re.match(r'(?:[\u4e00-\u9fff]{2,60}(?:大学|学院)|20\d{2}年)', value) or not re.search(r'硕士.*招生.*(?:目录|章程|简章)', value):
                continue
            years = set(re.findall(r'(?<!\d)(20\d{2})(?!\d)', value))
            if years and years != {str(year)}:
                return '原PDF招生标题明示其他年度，不能由发布页覆盖'
            named = re.match(r'([\u4e00-\u9fff]{2,60}?大学(?:\([^)]*\))?)', value)
            if named and named[1] not in owner and owner not in named[1]:
                return '原PDF招生标题明确属于其他大学，不能由发布页覆盖'
    return None


def parse_catalog_pdf_tables(layout, unit, year, document, *, project_root=PROJECT_ROOT):
    """Shared initial cells are accepted only for one unambiguous subject set."""
    raw_text = layout.get('raw_text', '')
    raw_pages = raw_text.rstrip('\f\r\n ').split('\f')
    result = {'programs': [], 'expected_records': 0, 'issues': [], 'retest_rules': [], 'documents': [],
              'table_manifests': [], 'catalog_format': PARSER}
    from .catalog_publication import verify_catalog_publication, whole_document_mode
    publication = verify_catalog_publication(document, unit, year, project_root=project_root)
    if year != 2027 or document.get('year') != year or str(document.get('unit_id')) != str(unit.get('id')):
        return {**result, 'issues': ['原PDF主标题、招生单位或2027参考年度未能独立核对']}
    conflict = _source_conflict(layout, unit, year)
    if conflict:
        return {**result, 'issues': [conflict]}
    if not _has_title(layout, unit, year) and not publication['verified']:
        return {**result, 'issues': ['原PDF主标题未明确所选学校年度，发布页链亦未通过核对：' + publication['reason']]}
    if publication['verified']:
        result['documents'] = publication['documents']
    regions, manifests = _regions(layout)
    result['table_manifests'] = manifests
    if not manifests:
        return {**result, 'issues': ['未发现能区分初试、复试与专业身份的PDF表头']}
    first_header = manifests[0]['page']
    first_page_header = _headers(layout)[first_header]
    title_rows = [(page['page'], line) for page in layout['pages'][:first_header]
                  for line in page['lines'] if line['y0'] < (first_page_header['y'] if page['page'] == first_header else 150)
                  and (compact(unit['name']) == compact(line['text']) or ('2027' in line['text'] and re.search(r'招生.*(?:目录|简章)|目录', line['text'])))]
    title_text = ''.join(line['text'] for _, line in title_rows)
    default_modes = _mode(title_text)
    if len(default_modes) != 1:
        default_modes = []
    policy_mode = whole_document_mode(raw_text, unit, year, allow_project=len({r['code'] for r in regions}) == 1)
    mode_refs = []
    for claim in policy_mode['claims']:
        page = next((index for index, text in enumerate(raw_pages, 1) if compact(claim['quote']) in compact(text)), None)
        if page:
            mode_refs.append(evidence(document, claim['quote'], page))
    modes = set(default_modes + ([policy_mode['mode']] if policy_mode['mode'] else [])
                + ([publication['mode']] if publication['verified'] and publication['mode'] else []))
    if len(modes) > 1 or policy_mode['conflict'] or publication['mode_conflict']:
        return {**result, 'issues': ['文件标题、全文明确规则或发布页的学习方式相互矛盾，暂不采用确定值']}
    default_modes = list(modes)
    if publication['verified']:
        mode_refs = [*mode_refs, *publication['evidence'], *publication['mode_evidence']]
    pages = {page['page']: page for page in layout['pages']}
    headers = _headers(layout)
    policy = _registration_policy(layout)
    consumed = {(region['page'], round(region['identity'][0][1]['y0'], 2), region['code']) for region in regions}
    for page in layout['pages']:
        markers = [(line, _MAJOR.match(line['text'].lstrip('★☆*＊'))) for line in page['lines']]
        markers = [(line, marker) for line, marker in markers if marker]
        exam_markers = any(re.search(r'[①②③④]\s*[1-9]\d{2}|初试科目|101\s*思想政治|199\s*管理类', line['text']) for line in page['lines'])
        if page['page'] not in headers and markers and exam_markers:
            result['issues'].append(f'第{page["page"]}页含招生专业与初试标记，但未能核对表头和合并单元格，不能忽略该页')
            result['expected_records'] += len(markers)
        elif page['page'] in headers:
            header = headers[page['page']]
            lost = [(line, marker) for line, marker in markers if header['y'] <= line['y0'] <= page['height']-25
                    and line['x0'] < header['major_right']
                    and (page['page'], round(line['y0'], 2), marker[1]) not in consumed]
            if lost:
                result['issues'].append(f'第{page["page"]}页有{len(lost)}条原始专业标记未消费，保留部分数据')
                result['expected_records'] += len(lost)
    if len(raw_pages) != len(layout['pages']):
        result['issues'].append('原文页数与坐标提取页数不一致，完整性核对未通过')
    for region in regions:
        programs, expected, issues = _parse_region(region, title_rows, default_modes, unit, year, document, raw_pages, pages, headers, policy)
        result['programs'].extend({**program, 'evidence': list({(r['document_id'], r['page'], r['quote']): r for r in [*program['evidence'], *mode_refs]}.values())} for program in programs)
        result['expected_records'] += expected
        result['issues'].extend(f'{region["code"]}第{region["page"]}页：{issue}' for issue in issues)
    if not regions:
        result['issues'].append('表头已识别，但未能定位带真实代码的专业行')
    if len({row['id'] for row in result['programs']}) != len(result['programs']):
        result['issues'].append('存在重复的专业方向身份，完整目录仍需复核')
    return result
