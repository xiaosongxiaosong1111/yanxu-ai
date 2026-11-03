import hashlib
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from collectors.catalogs import (
    parse_subjects, parse_nankai_raw, parse_zzu_html, parse_zzu_tree,
    catalog_status, merge_admissions, resolve_unit,
    collect_nankai, collect_zzu, main as collector_main, _program,
)
from collectors.documents import (
    DocumentError, decode_text, extract_pdf_text, fetch_snapshot,
)
from service_verification import SourceVerifier


UNIT = {"id": "test-1", "name": "测试院校", "province": "测试省"}
DOC = {"id": "doc-1", "url": "https://example.edu.cn/catalog.pdf"}
RAW = """考试方式 院系所 专业 研究方向 学习方式 专业研究方向备注 考试科目
21全国统考 035计算机学院 081200计算机科学与技术
00不区分研究方向 (1)全日制
①101思想政治理论②201英语（一）或203日语③301数学（一）④408计算机学科专业基础
\f考试方式 院系所 专业 研究方向 学习方式 专业研究方向备注 考试科目
21全国统考 005文学院 050100中国语言文学 01文学理论 (2)非全日制
限中文专业本科毕业生报考。
①101思想政治理论②201英语（一）③701文学基础④431文学综合
"""
HTML = """<html><table id="zsml-table"><thead><tr><th>专业及方向</th><th>拟招统考人数</th><th>初试科目</th><th>复试</th></tr></thead><tbody>
<tr class="zsml-talbe-yx-zy"><td><h5>085404（专业学位）计算机技术（全国统考）</h5></td><td rowspan="3">30</td><td rowspan="3">①101思想政治理论②204英语（二）③302数学（二）④408计算机学科专业基础</td><td rowspan="3">复试科目：程序设计</td></tr>
<tr class="zsml-talbe-yx-zy-yjfx"><td>01（全日制）智能软件</td></tr>
<tr class="zsml-talbe-yx-zy-yjfx"><td>02（非全日制）软件系统</td></tr>
<tr><td>备注</td><td colspan="4"><pre>限计算机相关专业报考。</pre></td></tr>
</tbody></table></html>"""


class CatalogParsingTests(unittest.TestCase):
    def test_official_101_politics_abbreviation_preserves_raw_name_and_national_identity(self):
        subjects,issues=parse_subjects('①101政治②201英语（一）③301数学（一）④408计算机学科专业基础',UNIT['id'],DOC)
        self.assertEqual(issues,[])
        self.assertEqual(subjects[0]['name'],'政治')
        self.assertEqual(subjects[0]['scope'],'national')
        row=_program(UNIT,2027,'001测试学院','081200','计算机科学与技术','01测试方向','全日制',subjects,'',DOC)
        self.assertNotIn('subject_identity_issues',row)

    def test_explicit_national_exam_note_keeps_original_label_without_false_identity_conflict(self):
        raw='①101思想政治理论（全国统一命题科目）②204英语（二）（全国统一命题科目）③302数学（二）④408计算机学科专业基础'
        subjects,issues=parse_subjects(raw,UNIT['id'],DOC)
        self.assertEqual(issues,[])
        self.assertEqual([row['scope'] for row in subjects],['national']*4)
        self.assertEqual(subjects[0]['name'],'思想政治理论（全国统一命题科目）')

    def test_subjects_keep_exam_units_or_groups_and_verified_identity(self):
        rows, issues = parse_subjects("①101思想政治理论②201英语（一）或203日语③302数学（二）④431金融学综合", UNIT["id"], DOC)
        self.assertEqual(issues, [])
        self.assertEqual([r["exam_unit"] for r in rows], [1, 2, 2, 3, 4])
        self.assertEqual(rows[1]["choice_group"], rows[2]["choice_group"])
        self.assertEqual(rows[1]["scope"], "national")
        self.assertEqual(rows[-1]["scope"], UNIT["id"])
        self.assertIn("431金融学综合", rows[-1]["evidence"]["quote"])

    def test_code_alone_does_not_make_a_subject_national(self):
        rows, issues = parse_subjects("①101思想政治理论②204英语（二）③302数学（二）④408校内自命题综合", "school-a", DOC)
        self.assertEqual(rows[-1]["scope"], "school-a")
        self.assertTrue(issues)

    def test_education_and_psychology_exact_published_long_names_preserve_originals(self):
        for code,name in [('311','教育学专业基础综合'),('312','心理学专业基础综合')]:
            rows,issues=parse_subjects(f'①101思想政治理论②201英语（一）③{code}{name}',UNIT['id'],DOC)
            self.assertEqual(issues,[])
            self.assertEqual(rows[-1]['scope'],'national')
            self.assertEqual(rows[-1]['name'],name)
            wrong,_=parse_subjects(f'①101思想政治理论②201英语（一）③{code}校内{name}',UNIT['id'],DOC)
            self.assertEqual(wrong[-1]['scope'],UNIT['id'])
        wrong,_=parse_subjects('①101思想政治理论②201英语（一）③312心理学专业综合',UNIT['id'],DOC)
        self.assertEqual(wrong[-1]['scope'],UNIT['id'])

    def test_official_national_subject_aliases_preserve_the_literal_source_name(self):
        text = '①101统考思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础综合'
        rows, issues = parse_subjects(text, UNIT['id'], DOC)
        self.assertEqual(issues, [])
        self.assertEqual(rows[0]['scope'], 'national')
        self.assertEqual(rows[0]['name'], '统考思想政治理论')
        self.assertEqual(rows[-1]['scope'], 'national')
        self.assertEqual(rows[-1]['name'], '计算机学科专业基础综合')
        bad, issues = parse_subjects(text.replace('408计算机学科专业基础综合', '408校内计算机学科专业基础综合'), UNIT['id'], DOC)
        self.assertEqual(bad[-1]['scope'], UNIT['id'])
        self.assertTrue(issues)

    def test_exact_legacy_law_exam_aliases_are_national_and_keep_the_original_names(self):
        for code, name in [('398', '法硕联考专业基础（非法学）'), ('498', '法硕联考综合（非法学）'),
                           ('397', '法硕联考专业基础（法学）'), ('497', '法硕联考综合（法学）')]:
            with self.subTest(code=code):
                subjects, issues = parse_subjects(f'①101思想政治理论②201英语（一）③{code}{name}', UNIT['id'], DOC)
                self.assertEqual(issues, [])
                self.assertEqual(subjects[-1]['name'], name)
                self.assertEqual(subjects[-1]['scope'], 'national')
                row = _program(UNIT, 2027, '001法学院', '035101', '法律', '01法律', '全日制', subjects, '', DOC)
                self.assertNotIn('subject_identity_issues', row)
                self.assertNotIn('exam_matching_supported', row)

    def test_exact_prefixed_national_english_one_and_math_one_are_not_identity_conflicts(self):
        subjects, issues = parse_subjects('①101思想政治理论②201统考英语一③301统考数学一④801综合', UNIT['id'], DOC)
        self.assertEqual(issues, [])
        self.assertEqual([s['scope'] for s in subjects[1:3]], ['national', 'national'])
        self.assertEqual([s['name'] for s in subjects[1:3]], ['统考英语一', '统考数学一'])
        row = _program(UNIT, 2027, '001学院', '081200', '计算机', '01方向', '全日制', subjects, '', DOC)
        self.assertNotIn('subject_identity_issues', row)
        self.assertNotIn('exam_matching_supported', row)

    def test_contradictory_national_code_and_name_preserve_original_evidence_but_disable_matching(self):
        text = '①101思想政治理论②201英语（一）③301数学（二）④801环境工程'
        subjects, _ = parse_subjects(text, UNIT['id'], DOC)
        before = deepcopy(subjects)
        row = _program(UNIT, 2027, '010环境学院', '083000', '环境科学与工程', '01环境工程',
            '全日制', subjects, '保留官方备注。', DOC)
        self.assertEqual(subjects, before)
        self.assertEqual(row['subjects'][2], before[2])
        self.assertEqual(row['notes'], ['保留官方备注。'])
        self.assertFalse(row['exam_matching_supported'])
        self.assertEqual(len(row['subject_identity_issues']), 1)
        self.assertEqual(row['subject_identity_issues'][0]['code'], '301')
        self.assertEqual(row['subject_identity_issues'][0]['name'], '数学（二）')
        self.assertEqual(row['subject_identity_issues'][0]['evidence'], before[2]['evidence'])
        good = _program(UNIT, 2027, '010环境学院', '083000', '环境科学与工程', '01环境工程',
            '全日制', [dict(subject, name='数学（一）') if subject['code'] == '301' else subject for subject in subjects], '', DOC)
        self.assertNotIn('subject_identity_issues', good)
        self.assertNotIn('exam_matching_supported', good)

    def test_two_and_three_unit_exams_are_not_padded_to_four(self):
        two, _ = parse_subjects("①199管理类综合能力②204英语（二）", "a", DOC)
        three, _ = parse_subjects("①101思想政治理论②201英语（一）③311教育学专业基础", "a", DOC)
        self.assertEqual({r["exam_unit"] for r in two}, {1, 2})
        self.assertEqual({r["exam_unit"] for r in three}, {1, 2, 3})

    def test_official_empty_unit_placeholders_are_absent_exams_not_parse_failures(self):
        rows, issues = parse_subjects("①199管理类综合能力②204英语（二）③-无④--无", "a", DOC)
        self.assertEqual(len(rows), 2)
        self.assertEqual(issues, [])
        rows, issues = parse_subjects("①101思想政治理论②201英语（一）③313历史学专业基础④--无", "a", DOC)
        self.assertEqual(len(rows), 3)
        self.assertEqual(issues, [])

    def test_bad_subject_parts_are_explicit_issues(self):
        rows, issues = parse_subjects("①101思想政治理论③未知科目", "a", DOC)
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(len(issues), 1)
        self.assertTrue(parse_subjects("", "a", DOC)[1])

    def test_nankai_raw_recovers_wrapping_and_page_evidence(self):
        result = parse_nankai_raw(RAW, UNIT, 2027, DOC)
        self.assertEqual(result["expected_records"], 2)
        self.assertEqual(len(result["programs"]), 2)
        first, second = result["programs"]
        self.assertEqual(first["code"], "081200")
        self.assertEqual(first["direction"], "00 不区分研究方向")
        self.assertEqual(first["study_mode"], "全日制")
        self.assertEqual(first["evidence"]["page"], 1)
        self.assertEqual(second["evidence"]["page"], 2)
        self.assertTrue(second["requires_review"])
        self.assertIsNone(second["tuition"])
        self.assertTrue(first["requires_review"])
        self.assertFalse(first["eligibility_evidence_complete"])
        self.assertEqual(first["department"], "计算机学院")
        self.assertEqual(first["department_code"], "035")

    def test_nankai_incomplete_rows_stay_in_audit_not_invented(self):
        result = parse_nankai_raw(RAW + "21全国统考 失败的条目", UNIT, 2027, DOC)
        self.assertEqual(result["expected_records"], 3)
        self.assertEqual(len(result["programs"]), 2)
        self.assertTrue(result["issues"])
        self.assertEqual(catalog_status(result, UNIT, 2027)["status"], "needs_review")

    def test_zzu_inherits_merged_exam_cells_and_shared_plan_scope(self):
        result = parse_zzu_html(HTML, UNIT, "035 计算机学院", 2027, DOC)
        self.assertEqual(result["expected_records"], 2)
        self.assertEqual(len(result["programs"]), 2)
        first, second = result["programs"]
        self.assertEqual(first["degree_type"], "专业学位")
        self.assertEqual(second["study_mode"], "非全日制")
        self.assertEqual(second["subjects"][-1]["code"], "408")
        self.assertEqual(first["planned_enrollment"]["scope"], "department_program")
        self.assertEqual(first["planned_enrollment"]["count"], 30)
        self.assertTrue(first["requires_review"])
        self.assertEqual(len(result["retest_rules"]), 2)

    def test_zzu_bad_direction_and_no_table_are_never_complete(self):
        bad = HTML.replace("01（全日制）智能软件", "无法读取方向")
        result = parse_zzu_html(bad, UNIT, "035 计算机学院", 2027, DOC)
        self.assertTrue(result["issues"])
        self.assertEqual(len(result["programs"]), 1)
        self.assertTrue(parse_zzu_html("<p>访问受限</p>", UNIT, "035", 2027, DOC)["issues"])

    def test_zzu_evidence_keeps_actual_header_direction_and_complete_exam_fragments(self):
        import re
        from html import unescape
        from collectors.catalogs import compact
        source = compact(unescape(re.sub(r'<[^>]+>', '', HTML)))
        for program in parse_zzu_html(HTML, UNIT, '035 计算机学院', 2027, DOC)['programs']:
            self.assertIsInstance(program['evidence'], list)
            for item in program['evidence'] + [program['exam_evidence']]:
                self.assertIn(compact(item['quote']), source)
            self.assertIn('①', program['exam_evidence']['quote'])
            self.assertIn('④', program['exam_evidence']['quote'])

    def test_zzu_program_name_excludes_nested_tooltip_introduction(self):
        title = '085404（专业学位）计算机技术（全国统考）'
        decorated = '<span class="tooltip-item">' + title + '</span>'
        decorated += '<span class="tooltip-content clearfix"><p>学科介绍<span>很长的历史</span><br>不属于专业名称</p></span>'
        html = HTML.replace(title, decorated)
        self.assertNotEqual(html, HTML)
        programs = parse_zzu_html(html, UNIT, '035 计算机学院', 2027, DOC)['programs']
        self.assertTrue(all(program['name'] == '计算机技术' for program in programs))
        self.assertTrue(all('学科介绍' not in str(program['evidence']) for program in programs))

    def test_tree_enumerates_every_official_program_node(self):
        tree = [{"id": "001", "name": "001 学院", "children": [{"id": "025100", "name": "025100 金融", "scode": "21"}]}]
        rows = parse_zzu_tree(tree)
        self.assertEqual(rows[0]["department_id"], "001")
        self.assertEqual(rows[0]["code"], "025100")
        with self.assertRaises(ValueError): parse_zzu_tree({"error": "restricted"})
        with self.assertRaises(ValueError): parse_zzu_tree([{"id": "001", "children": [{"id": "bad"}]}])

    def test_catalog_reconciliation_requires_external_count_and_all_core_fields(self):
        parsed = parse_nankai_raw(RAW, UNIT, 2027, DOC)
        self.assertEqual(catalog_status(parsed, UNIT, 2027)["status"], "verified")
        bad = {**parsed, "expected_records": 3}
        self.assertEqual(catalog_status(bad, UNIT, 2027)["verified_records"], 0)
        empty = {"programs": [], "expected_records": 0, "issues": []}
        self.assertEqual(catalog_status(empty, UNIT, 2027)["status"], "needs_review")
        repeated = {**parsed, "programs": parsed["programs"] * 2, "expected_records": 4}
        self.assertEqual(catalog_status(repeated, UNIT, 2027)["status"], "needs_review")

    def test_merge_preserves_missing_arrays_and_never_sets_global_complete(self):
        parsed = parse_nankai_raw(RAW, UNIT, 2027, DOC)
        result = merge_admissions([{"documents": [DOC], **parsed, "status": catalog_status(parsed, UNIT, 2027)}])
        self.assertEqual(len(result["programs"]), 2)
        self.assertEqual(result["score_lines"], [])
        self.assertFalse(result["metadata"]["catalog_complete"])
        self.assertEqual(result["metadata"]["unit_catalog_status"][0]["unit_id"], "test-1")

    def test_unit_identity_resolves_by_exact_official_name(self):
        data = {"units": [UNIT]}
        self.assertEqual(resolve_unit(data, "测试院校")["id"], "test-1")
        with self.assertRaises(ValueError): resolve_unit(data, "不存在大学")

    def test_partial_refresh_retains_other_units_years_and_history_without_mutation(self):
        status = {"unit_id": "a", "year": 2027, "status": "verified"}
        previous = {"metadata": {"last_completed_admission_year": 2026, "notes": ["Retain manual provenance"],
                                 "unit_catalog_status": [status, {"unit_id": "b", "year": 2027}]},
                    "programs": [{"id": "old-a", "unit_id": "a", "year": 2027}, {"id": "b", "unit_id": "b", "year": 2027},
                                 {"id": "a-history", "unit_id": "a", "year": 2026}],
                    "documents": [{"id": "old-evidence", "unit_id": "a", "year": 2027}],
                    "retest_rules": [{"id": "old-retest", "unit_id": "a", "year": 2027}, {"id": "b-retest", "unit_id": "b", "year": 2027}]}
        history_fields = ("score_lines", "admitted_statistics", "transfer_rules", "notices")
        previous = {**previous, **{key: [{"id": key, "unit_id": "a", "year": 2027}] for key in history_fields}}
        original = deepcopy(previous)
        entry = {"status": status, "programs": [{"id": "new-a", "unit_id": "a", "year": 2027}],
                 "documents": [{"id": "new-evidence", "unit_id": "a", "year": 2027}], "retest_rules": []}
        result = merge_admissions([entry], previous=previous)
        self.assertEqual({row["id"] for row in result["programs"]}, {"new-a", "b", "a-history"})
        self.assertEqual({row["id"] for row in result["documents"]}, {"old-evidence", "new-evidence"})
        self.assertEqual([row["id"] for row in result["retest_rules"]], ["b-retest"])
        for key in history_fields: self.assertEqual(result[key], previous[key])
        self.assertEqual(result["metadata"]["last_completed_admission_year"], 2026)
        self.assertIn("Retain manual provenance", result["metadata"]["notes"])
        self.assertEqual(len(result["metadata"]["unit_catalog_status"]), 2)
        self.assertFalse(result["metadata"]["catalog_complete"])
        self.assertEqual(previous, original)

    def test_failed_refresh_retains_previous_scope_marked_stale_and_closes_gate(self):
        previous = {"programs": [{"id": "previous", "unit_id": "a", "year": 2027}],
                    "retest_rules": [{"id": "old-rule", "unit_id": "a", "year": 2027}],
                    "metadata": {"unit_catalog_status": [{"unit_id": "a", "year": 2027, "status": "verified"}]}}
        entry = {"status": {"unit_id": "a", "year": 2027, "status": "needs_review"},
                 "programs": [{"id": "partial", "unit_id": "a", "year": 2027}]}
        result = merge_admissions([entry], previous=previous)
        self.assertEqual([row["id"] for row in result["programs"]], ["previous"])
        self.assertTrue(result["programs"][0]["stale"])
        self.assertTrue(result["retest_rules"][0]["stale"])
        self.assertEqual(result["metadata"]["unit_catalog_status"][0]["status"], "needs_review")
        first_attempt = merge_admissions([entry])
        self.assertEqual([row["id"] for row in first_attempt["programs"]], ["partial"])


class DocumentTests(unittest.TestCase):
    def test_cache_identity_includes_unit_year_kind_and_request_body(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = DOC["url"]
        response.read.return_value = b"official content"
        response.headers = {"Content-Type": "text/html"}
        contexts = [(UNIT, 2027, "catalog", None), ({**UNIT, "id": "other"}, 2027, "catalog", None),
                    (UNIT, 2028, "catalog", None), (UNIT, 2027, "charter", None),
                    (UNIT, 2027, "catalog", b"year=2027"), (UNIT, 2027, "catalog", b"")]
        with tempfile.TemporaryDirectory() as folder, patch("collectors.documents.build_opener") as opener:
            opener.return_value.open.return_value = response
            documents = [fetch_snapshot(DOC["url"], Path(folder), unit, year, kind, "title", data=data, project_root=Path(folder))[0]
                         for unit, year, kind, data in contexts]
            self.assertEqual(len({row["id"] for row in documents}), len(contexts))
            self.assertEqual(opener.return_value.open.call_count, len(contexts))
            for (unit, year, kind, data), original in zip(contexts, documents):
                cached, _ = fetch_snapshot(DOC["url"], Path(folder), unit, year, kind, "title", data=data, project_root=Path(folder), offline=True)
                self.assertEqual((cached["unit_id"], cached["year"], cached["kind"]), (str(unit["id"]), year, kind))
                self.assertEqual(cached["id"], original["id"])

    def test_legacy_cache_is_only_used_for_exact_context_and_valid_content(self):
        with tempfile.TemporaryDirectory() as folder, patch("collectors.documents.build_opener") as opener:
            root = Path(folder)
            raw = b"legacy official content"
            (root / "source.html").write_bytes(raw)
            key = hashlib.sha256(DOC["url"].encode()).hexdigest()[:20]
            metadata = {**DOC, "unit_id": UNIT["id"], "year": 2027, "kind": "catalog",
                        "sha256": hashlib.sha256(raw).hexdigest(), "local_path": "source.html"}
            (root / f"{key}.metadata.json").write_text(json.dumps(metadata), encoding="utf8")
            cached, content = fetch_snapshot(DOC["url"], root, UNIT, 2027, "catalog", "title", project_root=root, offline=True)
            self.assertEqual(cached["id"], DOC["id"])
            self.assertEqual(content, raw)
            for unit, year, kind in [({**UNIT, "id": "other"}, 2027, "catalog"), (UNIT, 2028, "catalog"), (UNIT, 2027, "charter")]:
                with self.assertRaises(DocumentError):
                    fetch_snapshot(DOC["url"], root, unit, year, kind, "title", project_root=root, offline=True)
            opener.assert_not_called()

    def test_decode_supports_declared_and_utf8_or_gbk(self):
        self.assertEqual(decode_text("资料".encode("utf8")), "资料")
        self.assertEqual(decode_text("资料".encode("gb18030")), "资料")
        self.assertEqual(decode_text(b"abc", "text/html; charset=UTF-8"), "abc")

    def test_pdf_extraction_reports_missing_tool_and_failed_command(self):
        with patch("collectors.documents.shutil.which", return_value=None):
            with self.assertRaises(DocumentError): extract_pdf_text(Path("missing.pdf"))
        with patch("collectors.documents.shutil.which", return_value="pdftotext"), patch("collectors.documents.subprocess.run", side_effect=subprocess.CalledProcessError(1, "pdftotext")):
            with self.assertRaises(DocumentError): extract_pdf_text(Path("bad.pdf"))
        good = subprocess.CompletedProcess([], 0, b"page1\fpage2\f", b"")
        with patch("collectors.documents.shutil.which", return_value="pdftotext"), patch("collectors.documents.subprocess.run", return_value=good):
            self.assertEqual(extract_pdf_text(Path("valid.pdf")), "page1\fpage2\f")

    def test_snapshot_validates_host_preserves_hash_and_serves_cache(self):
        raw = b"<html>official catalog</html>"
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = raw
        response.headers = {"Content-Type": "text/html"}
        response.geturl.return_value = "https://example.edu.cn/catalog"
        with tempfile.TemporaryDirectory() as folder:
            with patch("collectors.documents.build_opener") as opener:
                opener.return_value.open.return_value = response
                document, content = fetch_snapshot("https://example.edu.cn/catalog", Path(folder), UNIT, 2027, "catalog", "官方目录", project_root=Path(folder))
                self.assertEqual(content, raw)
                self.assertEqual(document["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertTrue((Path(folder) / document["local_path"]).exists())
                second, content2 = fetch_snapshot("https://example.edu.cn/catalog", Path(folder), UNIT, 2027, "catalog", "官方目录", project_root=Path(folder), offline=True)
                self.assertEqual(second["id"], document["id"])
                self.assertEqual(content2, raw)
                self.assertEqual(opener.return_value.open.call_count, 1)
            for url in ["file:///etc/passwd", "https://127.0.0.1/a", "https://evil.example/a"]:
                with self.assertRaises(DocumentError): fetch_snapshot(url, Path(folder), UNIT, 2027, "catalog", "bad")

    def test_offline_mode_never_falls_back_to_network(self):
        with tempfile.TemporaryDirectory() as folder, patch("collectors.documents.build_opener") as opener:
            with self.assertRaises(DocumentError): fetch_snapshot("https://example.edu.cn/missing", Path(folder), UNIT, 2027, "catalog", "test", project_root=Path(folder), offline=True)
            opener.assert_not_called()

    def test_cached_snapshot_cannot_escape_its_source_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / "snapshots"
            directory.mkdir()
            secret = root / "unrelated.txt"
            secret.write_bytes(b"not a source document")
            key = hashlib.sha256(b"https://example.edu.cn/catalog").hexdigest()[:20]
            poisoned = {"id": "bad", "url": "https://example.edu.cn/catalog", "unit_id": UNIT["id"], "year": 2027, "kind": "catalog",
                        "local_path": "unrelated.txt", "sha256": hashlib.sha256(secret.read_bytes()).hexdigest()}
            (directory / f"{key}.metadata.json").write_text(json.dumps(poisoned), "utf8")
            with self.assertRaises(DocumentError):
                fetch_snapshot("https://example.edu.cn/catalog", directory, UNIT, 2027, "catalog", "test", project_root=root, offline=True)

    def test_snapshot_download_errors_and_oversize_never_create_fake_document(self):
        from urllib.error import URLError
        with tempfile.TemporaryDirectory() as folder, patch("collectors.documents.build_opener") as opener:
            opener.return_value.open.side_effect = URLError("offline")
            with self.assertRaises(DocumentError): fetch_snapshot("https://example.edu.cn/catalog", Path(folder), UNIT, 2027, "catalog", "test")
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://example.edu.cn/catalog"
        response.read.return_value = b""
        with tempfile.TemporaryDirectory() as folder, patch("collectors.documents.build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(DocumentError): fetch_snapshot("https://example.edu.cn/catalog", Path(folder), UNIT, 2027, "catalog", "test")


class CollectorIntegrationTests(unittest.TestCase):
    def test_same_url_new_pdf_version_reconciles_current_scope_and_preserves_history(self):
        unit = {**UNIT, 'official_url': 'https://example.edu.cn'}
        with tempfile.TemporaryDirectory() as folder, patch('collectors.documents.extract_pdf_text', return_value=RAW):
            root = Path(folder)
            documents, entries = [], []
            for index, raw in enumerate((b'%PDF-original', b'%PDF-original\n')):
                path = root / f'version-{index}.pdf'
                path.write_bytes(raw)
                document = {**DOC, 'id': f'doc-{index}', 'unit_id': UNIT['id'], 'year': 2027,
                            'kind': 'catalog', 'status': 'parsed', 'local_path': path.name,
                            'sha256': hashlib.sha256(raw).hexdigest()}
                parsed = parse_nankai_raw(RAW, unit, 2027, document)
                documents.append(document)
                entries.append({**parsed, 'documents': [document], 'status': catalog_status(parsed, unit, 2027)})
            verifier = SourceVerifier(root)
            fresh = merge_admissions([entries[1]])
            self.assertTrue(verifier.unit_reconciles(unit, 2027, fresh['programs'], {d['id']: d for d in fresh['documents']}))
            previous = {**merge_admissions([entries[0]]), 'score_lines': [{'id': 'old-score', 'evidence': {'document_id': 'doc-0'}}]}
            original = deepcopy(previous)
            merged = merge_admissions([entries[1]], previous)
            self.assertTrue(verifier.unit_reconciles(unit, 2027, merged['programs'], {d['id']: d for d in merged['documents']}))
            self.assertEqual(len(merged['documents']), 2)
            self.assertFalse(next(d for d in merged['documents'] if d['id'] == 'doc-0')['catalog_active'])
            self.assertTrue(path.exists())
            self.assertEqual(merged['score_lines'], previous['score_lines'])
            self.assertEqual(previous, original)
            tampered = deepcopy(merged)
            tampered['programs'][0]['name'] = 'forged'
            self.assertFalse(verifier.unit_reconciles(unit, 2027, tampered['programs'], {d['id']: d for d in tampered['documents']}))
            failed = merge_admissions([{**entries[1], 'status': {**entries[1]['status'], 'status': 'needs_review'}}], previous)
            self.assertTrue(next(d for d in failed['documents'] if d['id'] == 'doc-0')['catalog_active'])
            self.assertFalse(next(d for d in failed['documents'] if d['id'] == 'doc-1')['catalog_active'])
            self.assertTrue(failed['programs'][0]['stale'])

    def test_cli_collector_failure_retains_scope_stale_and_records_failed_status(self):
        previous = {"metadata": {"unit_catalog_status": [{"unit_id": "b", "year": 2027, "status": "verified"}]},
                    "programs": [{"id": "b-old", "unit_id": "b", "year": 2027}]}
        entry = {"status": {"unit_id": UNIT["id"], "year": 2027, "status": "verified"},
                 "programs": [{"id": "a-new", "unit_id": UNIT["id"], "year": 2027}]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry, output = root / "units.json", root / "admissions.json"
            registry.write_text(json.dumps({"units": [{**UNIT, "name": "南开大学"}, {"id": "b", "name": "郑州大学"}]}), encoding="utf8")
            output.write_text(json.dumps(previous), encoding="utf8")
            args = ["catalogs", "--units", str(registry), "--output", str(output)]
            with patch("sys.argv", args), patch("collectors.catalogs.collect_nankai", return_value=entry), patch("builtins.print"), \
                 patch("collectors.catalogs.collect_zzu", side_effect=DocumentError("source unavailable")):
                collector_main()
            result = json.loads(output.read_text("utf8"))
            self.assertEqual({row["id"] for row in result["programs"]}, {"a-new", "b-old"})
            self.assertTrue(next(row for row in result["programs"] if row["id"] == "b-old")["stale"])
            status = next(row for row in result["metadata"]["unit_catalog_status"] if row["unit_id"] == "b")
            self.assertEqual(status["status"], "needs_review")
            self.assertEqual(status["verified_records"], 0)
            self.assertIn("source unavailable", " ".join(status["notes"]))

    def test_cli_partial_refresh_does_not_remove_existing_other_unit_or_history(self):
        previous = {"metadata": {"last_completed_admission_year": 2026},
                    "programs": [{"id": "other", "unit_id": "other", "year": 2027}],
                    "score_lines": [{"id": "historical-score", "unit_id": UNIT["id"], "year": 2026}]}
        entry = {"status": {"unit_id": UNIT["id"], "year": 2027, "status": "verified"},
                 "programs": [{"id": "fresh", "unit_id": UNIT["id"], "year": 2027}]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry, output = root / "units.json", root / "admissions.json"
            registry.write_text(json.dumps({"units": [{**UNIT, "name": "南开大学"}]}), encoding="utf8")
            output.write_text(json.dumps(previous), encoding="utf8")
            args = ["catalogs", "--units", str(registry), "--output", str(output), "--sources", "nankai"]
            with patch("sys.argv", args), patch("collectors.catalogs.collect_nankai", return_value=entry), patch("builtins.print"):
                collector_main()
            result = json.loads(output.read_text("utf8"))
            self.assertEqual({row["id"] for row in result["programs"]}, {"other", "fresh"})
            self.assertEqual(result["score_lines"], previous["score_lines"])
            self.assertEqual(result["metadata"]["last_completed_admission_year"], 2026)

    def test_nankai_reconciles_three_independent_source_markers(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            source.write_bytes(b"%PDF-test")
            document = {**DOC, "local_path": str(source)}
            with patch("collectors.catalogs.PROJECT_ROOT", Path(folder)), patch("collectors.catalogs.fetch_snapshot", return_value=(document, b"pdf")), patch("collectors.catalogs.extract_pdf_text", return_value=RAW):
                entry = collect_nankai(UNIT, Path(folder))
                self.assertEqual(entry["status"]["status"], "verified")
                self.assertEqual(entry["status"]["source_reconciliation"]["pages"], 2)
                extracted = entry["documents"][0]
                self.assertEqual(len(extracted["text_pages"]), 2)
                text_file = Path(folder) / extracted["text_path"]
                self.assertEqual(hashlib.sha256(text_file.read_bytes()).hexdigest(), extracted["text_sha256"])
                self.assertEqual(text_file.read_text("utf8"), RAW)
                for page in extracted["text_pages"]:
                    self.assertEqual(hashlib.sha256((Path(folder) / page["local_path"]).read_bytes()).hexdigest(), page["sha256"])
            with patch("collectors.catalogs.PROJECT_ROOT", Path(folder)), patch("collectors.catalogs.fetch_snapshot", return_value=(document, b"pdf")), patch("collectors.catalogs.extract_pdf_text", return_value=RAW + "①"):
                self.assertEqual(collect_nankai(UNIT, Path(folder))["status"]["status"], "needs_review")

    def test_zzu_failed_program_page_keeps_unit_incomplete(self):
        tree = [{"id": "035", "name": "035 计算机学院", "children": [{"id": "085404", "scode": "21"}, {"id": "085405", "scode": "21"}]}]
        def download(url, *args, **kwargs):
            if url.endswith("getTreeData"): return DOC, json.dumps(tree).encode()
            if "085405" in url: raise DocumentError("访问失败")
            return DOC, HTML.encode("utf8")
        with tempfile.TemporaryDirectory() as folder, patch("collectors.catalogs.fetch_snapshot", side_effect=download), patch("collectors.catalogs.time.sleep"):
            entry = collect_zzu(UNIT, Path(folder), workers=1)
        self.assertEqual(len(entry["programs"]), 2)
        self.assertEqual(entry["status"]["status"], "needs_review")
        self.assertEqual(entry["status"]["source_reconciliation"]["fetched_program_pages"], 1)
        self.assertTrue(entry["issues"])

    def test_zzu_program_page_identity_must_match_index(self):
        tree = [{"id": "035", "name": "035 计算机学院", "children": [{"id": "085405", "scode": "21"}]}]
        def download(url, *args, **kwargs):
            return (DOC, json.dumps(tree).encode()) if url.endswith("getTreeData") else (DOC, HTML.encode("utf8"))
        with tempfile.TemporaryDirectory() as folder, patch("collectors.catalogs.fetch_snapshot", side_effect=download):
            entry = collect_zzu(UNIT, Path(folder))
        self.assertEqual(entry["status"]["status"], "needs_review")
        self.assertIn("专业代码", entry["issues"][0])

    def test_cli_writes_only_collected_real_source_records_atomically(self):
        parsed = parse_nankai_raw(RAW, UNIT, 2027, DOC)
        entry = {**parsed, "documents": [DOC], "status": catalog_status(parsed, UNIT, 2027)}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry = root / "units.json"
            registry.write_text(json.dumps({"units": [{**UNIT, "name": "南开大学"}]}), "utf8")
            output = root / "admissions.json"
            args = ["catalogs", "--units", str(registry), "--output", str(output), "--sources", "nankai", "--offline"]
            with patch("sys.argv", args), patch("collectors.catalogs.collect_nankai", return_value=entry), patch("builtins.print"):
                collector_main()
            result = json.loads(output.read_text("utf8"))
            self.assertFalse(result["metadata"]["catalog_complete"])
            self.assertEqual(len(result["programs"]), 2)


if __name__ == "__main__":
    unittest.main()
