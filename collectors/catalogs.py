"""Source-specific catalog parsers; gaps never turn into synthetic admissions data."""
import argparse
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode

from .documents import PROJECT_ROOT, DocumentError, decode_text, extract_pdf_text, fetch_snapshot, save_pdf_text

NANKAI_PAGE = "https://yzb.nankai.edu.cn/2026/1008/c5509a603818/page.htm"
NANKAI_PDF = "https://yzb.nankai.edu.cn/_upload/article/files/b6/06/7f20fc4644de808f280c78c70df0/c2aba7d0-ab05-47cb-8b2f-602563307d6c.pdf"
ZZU_BASE = "https://yzbm.zzu.edu.cn"
NATIONAL = {
    "101": {"思想政治理论", "统考思想政治理论", "政治", "思想政治理论(全国统一命题科目)"}, "199": {"管理类综合能力", "管理类联考综合能力"},
    "201": {"英语(一)", "统考英语一"}, "202": {"俄语"}, "203": {"日语"}, "204": {"英语(二)", "英语(二)(全国统一命题科目)"},
    "301": {"数学(一)", "统考数学一"}, "302": {"数学(二)"}, "303": {"数学(三)"},
    "306": {"临床医学综合能力(西医)"}, "307": {"临床医学综合能力(中医)"},
    # Exact published aliases, not suffix stripping: SCUEC's 2024-07-02 notice
    # /jyxy/info/1036/2847.htm and NCU's official 2026 catalogue
    # /docs/2025-10/16fd752172db430e980d1c23929a1c77.pdf explicitly name the unified tests.
    "311": {"教育学专业基础", "教育学专业基础综合"},
    "312": {"心理学专业基础", "心理学专业基础综合"}, "313": {"历史学专业基础"},
    "314": {"数学(农)"}, "315": {"化学(农)"}, "333": {"教育综合"},
    "396": {"经济类综合能力"}, "397": {"法律硕士专业基础(法学)", "法硕联考专业基础(法学)"},
    "398": {"法律硕士专业基础(非法学)", "法硕联考专业基础(非法学)"}, "408": {"计算机学科专业基础", "计算机学科专业基础综合"},
    "414": {"植物生理学与生物化学"}, "415": {"动物生理学与生物化学"},
    "497": {"法律硕士综合(法学)", "法硕联考综合(法学)"}, "498": {"法律硕士综合(非法学)", "法硕联考综合(非法学)"},
}
RESTRICTION = re.compile(r"限[^。；]*报考|不(?:招收|接受|接收)|只(?:招收|接受)|同等学力|跨专业|本科毕业|工作经验|须具备|必须具备|仅限")
HEADER = re.compile(r"考试方式\s*院系所\s*专业\s*研究方向\s*学习方式\s*专业研究方向备注\s*考试科目")


def compact(value):
    return re.sub(r"\s+", "", value or "").replace("（", "(").replace("）", ")")


def evidence(document, quote, page=None):
    return {"document_id": document["id"], "url": document["url"], "quote": quote.strip(), "page": page}


def parse_subjects(value, unit_id, document, page=None):
    text = re.sub(r"\s+", "", value or "")
    parts = list(re.finditer(r"([①②③④])([^①②③④]+)", text))
    subjects, issues = [], []
    for part in parts:
        exam_unit = "①②③④".index(part.group(1)) + 1
        alternatives = re.split(r"(?:或者|或)", part.group(2))
        for alternative in alternatives:
            if re.fullmatch(r"[-—－]*(?:无|不考)", alternative):
                continue
            match = re.fullmatch(r"(\d{3})(.+)", alternative)
            if not match:
                issues.append(f"第{exam_unit}单元科目无法解析：{alternative}")
                continue
            code, name = match.groups()
            national = code in NATIONAL and compact(name) in NATIONAL[code]
            if code in NATIONAL and not national:
                issues.append(f"全国统考代码与名称不一致，需核验：{code}{name}")
            subject = {"code": code, "name": name, "scope": "national" if national else str(unit_id), "exam_unit": exam_unit,
                       "evidence": evidence(document, f"{code}{name}", page)}
            if len(alternatives) > 1:
                subject["choice_group"] = f"exam-unit-{exam_unit}"
            subjects.append(subject)
    units = {subject["exam_unit"] for subject in subjects}
    if not units or units != set(range(1, max(units) + 1)) or len(units) not in (2, 3, 4):
        issues.append("初试单元缺失或顺序不连续")
    return subjects, issues


def _program(unit, year, department, code, name, direction, mode, subjects, note, document, page=None, degree=None):
    department_code = re.match(r"\d{3}", department)
    direction_code = re.match(r"[0-9A-Z]{2}", direction)
    identity = [str(unit["id"]), str(year), department_code.group() if department_code else department,
                code, direction_code.group() if direction_code else direction, mode]
    subject_issues = []
    for subject in subjects:
        alias = re.sub(r'^(英语|数学)([一二三])$', r'\1(\2)', compact(subject.get('name')).strip('；;。'))
        if subject.get('code') in NATIONAL and alias not in NATIONAL[subject['code']]:
            subject_issues = [*subject_issues, {'exam_unit': subject.get('exam_unit'),
                'code': subject['code'], 'name': subject.get('name'),
                'reason': '原文全国统考科目代码与名称不一致，须向招生单位核实；未据此推断或更改科目。',
                'evidence': deepcopy(subject.get('evidence'))}]
    return {
        "id": ":".join(identity), "unit_id": str(unit["id"]), "unit_name": unit["name"], "year": year,
        "department": re.sub(r"^\d{3}\s*", "", department), "department_code": department_code.group() if department_code else None,
        "code": code, "name": name, "direction": direction,
        "study_mode": mode, "degree_type": degree, "province": unit.get("province"), "campus": None,
        "subjects": subjects, "tuition": None, "duration_years": None, "conditions": [],
        "requires_review": True, "eligibility_evidence_complete": False,
        "unparsed_condition_notes": [note] if RESTRICTION.search(note) else [], "notes": [note] if note else [],
        "evidence": evidence(document, f"{department} {code}{name} {direction} {mode} " + " ".join(f"{s['code']}{s['name']}" for s in subjects), page),
        **({'subject_identity_issues': subject_issues, 'exam_matching_supported': False} if subject_issues else {}),
    }


def parse_nankai_raw(text, unit, year, document):
    starts = list(re.finditer(r"21全国统考", text))
    programs, issues = [], []
    for index, start in enumerate(starts):
        raw = text[start.start():starts[index + 1].start() if index + 1 < len(starts) else len(text)]
        cleaned = HEADER.sub("", raw).replace("\f", " ")
        match = re.match(r"21全国统考\s+(\d{3}.+?)\s+([0-9A-Z]{6})(.+?)\s+([0-9A-Z]{2})(.+?)\s+\(([12])\)(全日制|非全日制)", cleaned, re.S)
        if not match or "①" not in cleaned:
            issues.append(f"原始第{index + 1}行无法完整解析")
            continue
        department, code, name, direction_code, direction_name, _, mode = match.groups()
        remainder = cleaned[match.end():]
        note, subject_text = remainder.split("①", 1)
        page = text.count("\f", 0, start.start()) + 1
        subjects, subject_issues = parse_subjects("①" + subject_text, unit["id"], document, page)
        direction = f"{direction_code} {re.sub(r'\s+', '', direction_name)}"
        program = _program(unit, year, re.sub(r"\s+", "", department), code, re.sub(r"\s+", "", name), direction, mode,
                           subjects, re.sub(r"\s+", "", note), document, page)
        program["evidence"] = evidence(document, raw, page)
        if subject_issues:
            program["requires_review"] = True
            issues.extend(f"{program['id']}：{issue}" for issue in subject_issues)
        programs.append(program)
    return {"programs": programs, "expected_records": len(starts), "issues": issues, "retest_rules": []}


class _Table(HTMLParser):
    _VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._depth, self._row, self._cell = [], 0, None, None
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self._skip_depth:
            if tag not in self._VOID:
                self._skip_depth += 1
            return
        if 'tooltip-content' in attributes.get('class', '').split():
            self._skip_depth = 1
            return
        if tag == "table" and (self._depth or attributes.get("id") == "zsml-table"):
            self._depth += 1
        if not self._depth:
            return
        if tag == "tr":
            self._row = {"class": attributes.get("class", ""), "cells": []}
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {"attrs": attributes, "text": ""}
        elif tag == "br" and self._cell is not None:
            self._cell["text"] += " "

    def handle_data(self, data):
        if self._cell is not None and not self._skip_depth:
            self._cell["text"] += data

    def handle_endtag(self, tag):
        if self._skip_depth:
            if tag not in self._VOID:
                self._skip_depth -= 1
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._cell["text"] = re.sub(r"\s+", " ", self._cell["text"]).strip()
            self._row["cells"].append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1


def _expanded_rows(rows):
    spans = {}
    for row_index, row in enumerate(rows):
        cells, next_spans = {}, {}
        for column, (remaining, cell) in spans.items():
            cells[column] = cell
            if remaining > 1:
                next_spans[column] = (remaining - 1, cell)
        column = 0
        for original in row["cells"]:
            while column in cells:
                column += 1
            try:
                colspan = max(1, min(16, int(original["attrs"].get("colspan", 1))))
                rowspan = max(1, min(1000, int(original["attrs"].get("rowspan", 1))))
            except ValueError:
                colspan, rowspan = 1, 1
            cell = {**original, "origin": row_index}
            for offset in range(colspan):
                cells[column + offset] = cell
                if rowspan > 1:
                    next_spans[column + offset] = (rowspan - 1, cell)
            column += colspan
        spans = next_spans
        yield {**row, "index": row_index, "expanded": cells}


def parse_zzu_html(html, unit, department, year, document):
    parser = _Table()
    parser.feed(html)
    rows = list(_expanded_rows(parser.rows))
    expected = len(re.findall(r'class=["\'][^"\']*\bzsml-talbe-yx-zy-yjfx\b', html))
    result = {"programs": [], "expected_records": expected, "issues": [], "retest_rules": []}
    header, header_row, group_programs = None, None, []
    for row in rows:
        cells = row["expanded"]
        if row["class"] == "zsml-talbe-yx-zy":
            header = re.match(r"([0-9A-Z]{6})\s*(.*)", cells.get(0, {}).get("text", ""))
            header_row, group_programs = row["index"], []
        elif "zsml-talbe-yx-zy-yjfx" in row["class"]:
            direction = re.match(r"([0-9A-Z]{2})\s*[（(](全日制|非全日制)[）)]\s*(.*)", cells.get(0, {}).get("text", ""))
            if not header or not direction or 2 not in cells:
                result["issues"].append(f"方向行{row['index']}无法完整解析")
                continue
            code, raw_name = header.groups()
            degree = "专业学位" if "专业学位" in raw_name else "学术学位" if "学术学位" in raw_name else None
            name = re.sub(r"[（(](?:专业学位|学术学位|全国统考)[）)]", "", raw_name).strip()
            direction_code, mode, direction_name = direction.groups()
            subjects, issues = parse_subjects(cells[2]["text"], unit["id"], document)
            program = _program(unit, year, department, code, name, f"{direction_code} {direction_name}", mode, subjects, "", document, degree=degree)
            program["evidence"] = [evidence(document, header.group(0)), evidence(document, cells[0]["text"])]
            program["exam_evidence"] = evidence(document, cells[2]["text"])
            if issues:
                program["requires_review"] = True
                result["issues"].extend(f"{program['id']}：{issue}" for issue in issues)
            quota_text = cells.get(1, {}).get("text", "")
            if re.fullmatch(r"\d+", quota_text):
                scope = "direction" if cells[1]["origin"] == row["index"] else "department_program"
                program["planned_enrollment"] = {"count": int(quota_text), "scope": scope, "includes_recommendation_exemption": False,
                                                 "estimated": True, "evidence": evidence(document, f"拟招统考人数：{quota_text}")}
            retest = cells.get(3, {}).get("text", "")
            if retest:
                result["retest_rules"].append({"id": f"retest:{program['id']}", "unit_id": str(unit["id"]), "unit_name": unit["name"],
                                               "year": year, "title": f"{department} {code} 复试与加试科目", "program_codes": [code],
                                               "requirements": [], "requires_review": True, "evidence": evidence(document, retest), "notes": [retest]})
            group_programs.append(program)
            result["programs"].append(program)
        elif row["cells"] and row["cells"][0]["text"] == "备注":
            note = " ".join(cell["text"] for cell in row["cells"][1:]).strip()
            for program in group_programs:
                program["notes"] = [note] if note else []
                program["requires_review"] = program["requires_review"] or bool(RESTRICTION.search(note))
                if note:
                    program["evidence"] = [*program["evidence"], evidence(document, note)]
    if not rows or not expected:
        result["issues"].append("没有可核对的方向表格；可能未公开或访问受限")
    return result


def parse_zzu_tree(tree):
    if not isinstance(tree, list):
        raise ValueError("官方目录树不是院系列表")
    nodes = []
    for department in tree:
        for program in department.get("children") or []:
            if not re.fullmatch(r"[0-9A-Z]{6}", str(program.get("id", ""))):
                raise ValueError("目录树专业代码缺失或格式错误")
            nodes.append({"department_id": str(department["id"]), "department": department["name"],
                          "code": str(program["id"]), "name": program.get("name"), "exam_type": str(program.get("scode", "21"))})
    return nodes


def catalog_status(parsed, unit, year):
    records = parsed["programs"]
    valid = all(program.get("code") and program.get("name")
                and (program.get("department") or program.get("department_scope") == "unit" and program.get("department") == "")
                and program.get("study_mode")
                and program.get("subjects") and all(subject.get("code") and subject.get("name") and subject.get("scope") and subject.get("exam_unit") for subject in program["subjects"])
                for program in records)
    unique = len({program["id"] for program in records}) == len(records)
    complete = bool(records) and len(records) == parsed["expected_records"] and not parsed["issues"] and valid and unique
    return {"unit_id": str(unit["id"]), "year": year, "status": "verified" if complete else "needs_review",
            "expected_records": parsed["expected_records"], "parsed_records": len(records), "verified_records": len(records) if complete else 0,
            "notes": ["仅核对该单位该年度招生目录的专业方向与初试科目，不代表资格条件、录取历史或全国目录完整。", *parsed["issues"]],
            "unit_name": unit["name"]}


def _catalog_scope(row):
    return str(row.get("unit_id", "")), row.get("year")


def _merge_catalog_rows(previous, collected, field, retained):
    scopes = {_catalog_scope(entry["status"]) for entry in collected}
    old = [{**row, "stale": True} if _catalog_scope(row) in retained else row
           for row in previous.get(field, []) if _catalog_scope(row) not in scopes or _catalog_scope(row) in retained]
    fresh = [row for entry in collected if _catalog_scope(entry["status"]) not in retained for row in entry.get(field, [])]
    return old + fresh


def _merge_catalog_documents(previous, collected, retained):
    documents = {row['id']: row for row in previous.get('documents', [])}
    for entry in collected:
        scope = _catalog_scope(entry['status'])
        incoming = entry.get('documents', [])
        current_ids = {row['id'] for row in incoming if row.get('kind') == 'catalog'}
        keep_old = scope in retained
        for identity, row in list(documents.items()):
            if row.get('kind') != 'catalog' or _catalog_scope(row) != scope:
                continue
            if keep_old:
                documents[identity] = {**row, 'catalog_active': row.get('catalog_active', True), 'stale': True}
            elif identity not in current_ids:
                documents[identity] = {**row, 'catalog_active': False, 'version_state': 'superseded'}
        for row in incoming:
            if row.get('kind') != 'catalog':
                documents[row['id']] = row
            elif not keep_old or row['id'] not in documents:
                documents[row['id']] = {**row, 'catalog_active': not keep_old,
                                        'version_state': 'pending_review' if keep_old else 'current'}
    return list(documents.values())


def merge_admissions(collected, previous=None):
    previous, collected = deepcopy(previous or {}), deepcopy(collected)
    old_scopes = {_catalog_scope(row) for row in previous.get("programs", [])}
    retained = {_catalog_scope(entry["status"]) for entry in collected
                if entry["status"].get("status") != "verified" and _catalog_scope(entry["status"]) in old_scopes}
    statuses = {_catalog_scope(row): row for row in previous.get("metadata", {}).get("unit_catalog_status", [])}
    statuses.update({_catalog_scope(entry["status"]): entry["status"] for entry in collected})
    notes = [*previous.get("metadata", {}).get("notes", []),
             "全国完整性发布门槛保持关闭；当前仅收录已完成采集的真实官方目录。", "2027目录为2028规划的历史参考。"]
    return {
        **previous,
        "metadata": {**previous.get("metadata", {}), "target_year": 2028, "reference_year": 2027, "catalog_complete": False,
                     "checked_at": datetime.now(timezone.utc).isoformat(), "unit_catalog_status": list(statuses.values()),
                     "notes": list(dict.fromkeys(notes))},
        "documents": _merge_catalog_documents(previous, collected, retained),
        "programs": _merge_catalog_rows(previous, collected, "programs", retained),
        "score_lines": previous.get("score_lines", []), "admitted_statistics": previous.get("admitted_statistics", []),
        "retest_rules": _merge_catalog_rows(previous, collected, "retest_rules", retained),
        "transfer_rules": previous.get("transfer_rules", []), "notices": previous.get("notices", []),
    }


def resolve_unit(registry, name):
    return next((unit for unit in registry.get("units", []) if unit.get("name") == name), None) or _missing_unit(name)


def _missing_unit(name):
    raise ValueError(f"全国索引中尚未找到{name}，不得用学校代码冒充CHSI标识")


def collect_nankai(unit, snapshot_dir, refresh=False, offline=False):
    document, _ = fetch_snapshot(NANKAI_PDF, snapshot_dir / "nankai-2027", unit, 2027, "catalog", "南开大学2027年硕士考试招生专业目录", refresh=refresh, offline=offline)
    raw = extract_pdf_text(PROJECT_ROOT / document["local_path"])
    document = save_pdf_text(document, raw, PROJECT_ROOT)
    parsed = parse_nankai_raw(raw, unit, 2027, document)
    mode_count = len(re.findall(r"\([12]\)(?:全日制|非全日制)", raw))
    if mode_count != parsed["expected_records"] or raw.count("①") != parsed["expected_records"]:
        parsed["issues"].append("PDF考试方式、学习方式与初试组合的独立计数不一致")
    status = catalog_status(parsed, unit, 2027)
    status["source_reconciliation"] = {"exam_rows": parsed["expected_records"], "study_modes": mode_count, "exam_combinations": raw.count("①"), "pages": len(raw.rstrip("\f\r\n ").split("\f"))}
    return {**parsed, "documents": [document], "status": status}


def collect_zzu(unit, snapshot_dir, workers=3, refresh=False, offline=False):
    directory = snapshot_dir / "zzu-2027"
    tree_doc, raw = fetch_snapshot(f"{ZZU_BASE}/zsml/getTreeData", directory, unit, 2027, "catalog", "郑州大学2027年官方院系专业树", data=urlencode({"nd": "2027", "zslb": "1"}).encode(), refresh=refresh, offline=offline)
    nodes = parse_zzu_tree(json.loads(decode_text(raw)))
    result = {"programs": [], "documents": [tree_doc], "expected_records": 0, "issues": [], "retest_rules": []}
    throttle, next_request = threading.Lock(), [0.0]

    def collect_node(node):
        if not offline:
            with throttle:
                delay = next_request[0] - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                next_request[0] = time.monotonic() + .25
        url = f"{ZZU_BASE}/zsml/sszsml/zsmlYjfx/2027/{node['department_id']}/{node['code']}?ksfs={node['exam_type']}"
        document, content = fetch_snapshot(url, directory, unit, 2027, "catalog", f"郑州大学2027年{node['department']} {node['code']}专业目录", refresh=refresh, offline=offline)
        parsed = parse_zzu_html(decode_text(content), unit, node["department"], 2027, document)
        if any(program["code"] != node["code"] for program in parsed["programs"]):
            parsed["issues"].append("方向页专业代码与官方目录树不一致")
        return document, parsed

    succeeded = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 3))) as executor:
        tasks = {executor.submit(collect_node, node): node for node in nodes}
        for task in as_completed(tasks):
            node = tasks[task]
            try:
                document, parsed = task.result()
            except (DocumentError, ValueError) as error:
                result["issues"].append(f"{node['department_id']}/{node['code']}未完成：{error}")
                continue
            succeeded += 1
            result["documents"].append(document)
            for key in ("programs", "issues", "retest_rules"):
                result[key].extend(parsed[key])
            result["expected_records"] += parsed["expected_records"]
            if succeeded % 40 == 0:
                print(f"郑州大学：已获取 {succeeded}/{len(nodes)} 个官方专业页", flush=True)
    status = catalog_status(result, unit, 2027)
    if succeeded != len(nodes):
        status["status"], status["verified_records"] = "needs_review", 0
    status["source_reconciliation"] = {"expected_program_pages": len(nodes), "fetched_program_pages": succeeded,
                                       "departments": len({node["department_id"] for node in nodes})}
    result["programs"].sort(key=lambda program: program["id"])
    return {**result, "status": status}


def _failed_catalog_entry(unit, error):
    return {"programs": [], "documents": [], "retest_rules": [],
            "status": {"unit_id": str(unit["id"]), "unit_name": unit["name"], "year": 2027,
                       "status": "needs_review", "expected_records": None, "parsed_records": 0,
                       "verified_records": 0, "notes": [f"最新官方目录采集失败，保留旧数据待复核：{error}"]}}


def main():
    parser = argparse.ArgumentParser(description="采集真实官方招生目录并保留完整性缺口")
    parser.add_argument("--units", type=Path, default=PROJECT_ROOT / "data/units.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data/admissions.json")
    parser.add_argument("--snapshot-dir", type=Path, default=PROJECT_ROOT / "data/snapshots/catalogs")
    parser.add_argument("--sources", default="nankai,zzu")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--offline", action="store_true", help="只按已校验快照重新解析，不新增网络请求")
    args = parser.parse_args()
    registry = json.loads(args.units.read_text("utf-8"))
    previous = json.loads(args.output.read_text("utf-8")) if args.output.exists() else None
    collected = []
    for source in args.sources.split(","):
        name, collector = {"nankai": ("南开大学", collect_nankai), "zzu": ("郑州大学", collect_zzu)}[source]
        unit = resolve_unit(registry, name)
        try:
            entry = collector(unit, args.snapshot_dir, refresh=args.refresh, offline=args.offline)
        except (DocumentError, ValueError) as error:
            entry = _failed_catalog_entry(unit, error)
        collected.append(entry)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(merge_admissions(collected, previous=previous), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(args.output)
        print(json.dumps({"unit": name, "programs": len(entry["programs"]), "status": entry["status"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
