"""Reconcile an official catalogue index against the originals actually downloaded."""
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from public_sources import official_source_url, rendered_source_html
from .documents import decode_text


INDEX_PARSER = 'catalog_index_v1'
ATTACHMENT = re.compile(r'\.(?:pdf|docx?|xlsx?)(?:$|[?#])', re.I)
NON_CATALOG = re.compile(r'考试大纲|参考书|参考书目|复试|加试|收费|学费|招生章程|招生简章|报考须知')
CATALOG_LABEL = re.compile(r'专业目录|招生目录|招生专业|学术学位|专业学位|(?:学院|学部|研究院|中心|系)$')


def normalized_url(url):
    value = urlsplit(url)
    return urlunsplit((value.scheme.lower(), value.netloc.lower(), value.path, value.query, ''))


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.current, self.skip, self.dynamic = [], None, 0, False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {'nav', 'footer', 'header', 'aside', 'script', 'style'}: self.skip += 1
        if self.skip: return
        if tag == 'iframe' or tag == 'select': self.dynamic = True
        if tag == 'a': self.current = {'url': values.get('href', ''), 'parts': [], 'onclick': values.get('onclick')}

    def handle_endtag(self, tag):
        if tag in {'nav', 'footer', 'header', 'aside', 'script', 'style'} and self.skip:
            self.skip -= 1
        if tag == 'a' and self.current is not None:
            self.links.append({**self.current, 'text': ''.join(self.current['parts']).strip()})
            self.current = None

    def handle_data(self, value):
        if not self.skip and self.current is not None: self.current['parts'].append(value)


def parse_catalog_index(html, url, unit, year):
    from .catalog_batch import unit_scope_supported
    html = rendered_source_html(html, url)
    parser = _Links()
    parser.feed(html)
    issues, groups, formats = [], {}, {}
    if not unit_scope_supported(unit, year, [(INDEX_PARSER, html)]):
        issues.append('索引原文未证明该校该年度完整统考招生目录范围')
    if parser.dynamic:
        issues.append('索引含动态选择或内嵌页面，尚缺独立页面全集证明')
    for link in parser.links:
        text, href = re.sub(r'\s+', '', link['text']), link['url']
        if NON_CATALOG.search(text): continue
        label = re.sub(r'[（(]?(?:PDF|Word|DOCX|XLSX|Excel)版?[）)]?', '', text, flags=re.I)
        attachment, labelled = bool(ATTACHMENT.search(href)), bool(CATALOG_LABEL.search(label))
        if not attachment and not labelled:
            if re.search(r'下一页|展开更多|加载更多', text): issues.append('目录索引仍含未消费的分页或展开入口')
            continue
        if not labelled:
            issues.append('存在用途未明确的附件，不能据扩展名猜测目录全集')
            continue
        target = normalized_url(urljoin(url, href))
        if href and not href.startswith(('#', 'javascript:')) and not urlsplit(href).fragment and target == normalized_url(url):
            continue
        if not href or href.startswith(('#', 'javascript:')) or link['onclick'] or not official_source_url(target, unit):
            issues.append('目录条目缺少可核验的本校官方原始链接')
            continue
        # A shared label alone does not prove that academic/professional files are alternatives.
        key = ('format', label) if label != text else ('required', label, target)
        if label != text:
            match = re.search(r'PDF|Word|DOCX|XLSX|Excel', text, re.I)
            kind = match[0].lower()
            kind = 'word' if kind in {'word', 'docx'} else 'excel' if kind in {'xlsx', 'excel'} else kind
            if kind in formats.get(key, set()) and target not in groups.get(key, set()):
                issues.append('同名同格式目录存在不同来源，不能推定为可替代版本')
            formats.setdefault(key, set()).add(kind)
        groups.setdefault(key, set()).add(target)
    if not groups: issues.append('索引原文没有可逐项对账的目录链接')
    return {'groups': list(groups.values()), 'issues': list(dict.fromkeys(issues))}


class _DirectoryForm(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.select, self.option, self.departments, self.years = None, None, set(), set()
        self.selected_departments = set()
        self.pages, self.current = set(), set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'select': self.select = values.get('name', values.get('id', ''))
        if tag == 'option': self.option = values
        if tag == 'a':
            match = re.search(r'lnkPage(\d+)(?:_|$)', values.get('id', ''))
            if match:
                self.pages.add(int(match[1]))
                if 'aspNetDisabled' in values.get('class', '').split(): self.current.add(int(match[1]))

    def handle_endtag(self, tag):
        if tag == 'select': self.select = None
        if tag == 'option' and self.option is not None:
            value = self.option.get('value', '')
            if self.select and self.select.endswith('drpYx') and re.fullmatch(r'\d{3}', value):
                self.departments.add(value)
                if 'selected' in self.option: self.selected_departments.add(value)
            if self.select and self.select.endswith('drpNd') and 'selected' in self.option:
                self.years.add(value)
            self.option = None


def _form_reconciliation(unit, year, documents, verifier):
    expected, observed, issues = None, {}, []
    docmap = {row['id']: row for row in documents}
    found = False
    for document in documents:
        source = verifier.read(document)
        if not source or document.get('catalog_parser') != 'html_table_v1':
            return None
        raw = decode_text(source[1], document.get('content_type', ''))
        form = _DirectoryForm()
        form.feed(raw)
        if not form.departments: return None
        found = True
        if not verifier._document_allowed(document, unit) or form.years != {str(year)}:
            issues.append('公开目录表单的学校或所选年度不一致')
        if expected is not None and expected != form.departments:
            issues.append('各目录页原始院系全集不一致')
        expected = form.departments
        parsed = verifier._source_programs(document, unit, year, {}, docmap)
        departments = {row.get('department_code') for row in (parsed or {}).get('programs', [])}
        if parsed and not departments and parsed.get('excluded_recommendation_only', 0) > 0:
            departments = {parsed.get('continuation_context', {}).get('department_code')}
        if not parsed or parsed.get('issues') or len(departments) != 1:
            issues.append('院系分页缺少唯一可重放的原文院系身份')
            continue
        department = next(iter(departments))
        if form.selected_departments != {department}:
            issues.append('目录页原文所选院系与方向记录归属不一致')
        pages, current = form.pages or {1}, form.current or ({1} if not form.pages else set())
        if len(current) != 1 or pages != set(range(1, max(pages) + 1)):
            issues.append('院系目录分页标识不连续或缺少当前页')
            continue
        group = observed.setdefault(department, {'expected': pages, 'seen': set()})
        if len(pages) > 1:
            explicit_total = re.findall(r'共\s*(\d+)\s*页', raw)
            if not explicit_total or {int(value) for value in explicit_total} != {max(pages)}:
                issues.append('多页目录缺少独立总页数边界，不能把可见页码窗口推定为全集')
        if group['expected'] != pages or group['seen'] & current:
            issues.append('院系分页出现重复当前页或页码全集不一致')
        group['seen'].update(current)
    if not found: return None
    if set(observed) != expected or any(group['seen'] != group['expected'] for group in observed.values()):
        issues.append('公开院系和分页全集尚未逐项下载并核验')
    return {'complete': not issues, 'expected_departments': len(expected or []),
            'fetched_departments': len(observed), 'fetched_sources': len(documents),
            'issues': list(dict.fromkeys(issues))}


def source_set_reconciliation(unit, year, documents, verifier):
    form = _form_reconciliation(unit, year, documents, verifier)
    if form is not None: return form
    indexes = [row for row in documents if row.get('catalog_parser') == INDEX_PARSER]
    if len(indexes) != 1:
        return {'complete': False, 'expected_sources': None, 'fetched_sources': 0,
                'issues': ['尚无唯一可独立重放的完整目录索引']}
    index = indexes[0]
    source = verifier.read(index)
    if not source or not verifier._document_allowed(index, unit):
        return {'complete': False, 'issues': ['目录索引原文不可核验']}
    proof = parse_catalog_index(decode_text(source[1], index.get('content_type', '')), index['url'], unit, year)
    actual = [row for row in documents if row.get('catalog_parser') != INDEX_PARSER]
    urls = [normalized_url(row['url']) for row in actual]
    issues = list(proof['issues'])
    if len(urls) != len(set(urls)): issues.append('同一来源URL存在重复当前版本')
    expected = set().union(*proof['groups']) if proof['groups'] else set()
    if set(urls) - expected: issues.append('导入来源存在目录索引未列出的页面，全集范围仍需复核')
    if any(len(group & set(urls)) != 1 for group in proof['groups']):
        issues.append('目录索引条目存在未下载来源或同条目重复格式，尚未逐项对齐')
    if any(not verifier.pages(row, unit) for row in actual): issues.append('索引成员原文读取或官方归属核验失败')
    return {'complete': not issues, 'expected_sources': len(proof['groups']), 'fetched_sources': len(actual),
            'issues': list(dict.fromkeys(issues))}
