import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

import collectors.registry as registry

from collectors.registry import (
    RegistryParseError,
    collect_registry,
    decode_registry_html,
    fetch_registry_page,
    parse_registry_page,
)


def entry(identifier, name, province="北京", code="10001", extra=""):
    return f'''<div class="sch-item"><img src="https://t1.chei.com.cn/common/xh/{code}.jpg">
      <div class="info-box"><div class="sch-title"><a class="name js-yxk-yxmc" href="/sch/schoolInfo--schId-{identifier}.dhtml">{name}</a>
      <span class="sch-tag">双一流</span></div><div class="sch-department"><i>\ue6a4</i>{province}<span class="col-line"></span>
      <span>主管部门：</span>教育部</div><div class="sch-link"><a href="/sswbgg/?dwdm={code}&amp;ssdm=11">网报公告</a>{extra}</div></div></div>'''


def page(items, current=1, pages=2, size=2):
    last = (pages - 1) * size
    return f'''<html><body><div class="sch-list-container">{''.join(items)}</div>
      <div class="ch-page"><a href="/sch/?start={size}">2</a><a href="/sch/?start={last}">{pages}</a>
      <input id="p_num" value="{current}"></div><script>if (Num > {pages}) return false;
      pagenum = (Num - 1) * {size};</script></body></html>'''


class RegistryParserTests(unittest.TestCase):
    def test_extracts_official_identity_code_and_province_without_tags(self):
        html = page([entry("367878", "北京大学"), entry("90002", "某研究所", "上海", "80002")])
        units, pagination = parse_registry_page(html, "https://yz.chsi.com.cn/sch/")
        self.assertEqual([unit["name"] for unit in units], ["北京大学", "某研究所"])
        self.assertEqual(units[0]["id"], "367878")
        self.assertEqual(units[0]["chsi_id"], "367878")
        self.assertEqual(units[0]["unit_code"], "10001")
        self.assertEqual(units[1]["province"], "上海")
        self.assertEqual(units[0]["source_url"], "https://yz.chsi.com.cn/sch/schoolInfo--schId-367878.dhtml")
        self.assertIsNone(units[0]["official_url"])
        self.assertEqual(pagination["total_pages"], 2)
        self.assertEqual(pagination["page_size"], 2)
        self.assertEqual(pagination["invalid_items"], 0)

    def test_real_pagination_formula_and_query_page_are_respected(self):
        html = page([entry("1", "学院")], current=2, pages=47, size=20)
        units, pagination = parse_registry_page(html, "https://yz.chsi.com.cn/sch/?start=20")
        self.assertEqual(pagination["current_page"], 2)
        self.assertEqual(pagination["total_pages"], 47)
        self.assertEqual(pagination["page_size"], 20)
        self.assertEqual(pagination["last_offset"], 920)
        self.assertEqual(len(units), 1)

    def test_name_entities_and_safe_explicit_official_link_are_preserved(self):
        html = page([entry("1", "甲 &amp; 乙研究所", "天津", extra='<a href="https://grad.example.edu.cn/">研究生招生网</a><a href="javascript:alert(1)">官网</a>')])
        units, _ = parse_registry_page(html)
        self.assertEqual(units[0]["name"], "甲 & 乙研究所")
        self.assertEqual(units[0]["official_url"], "https://grad.example.edu.cn/")

    def test_bad_items_are_counted_and_unknown_province_is_not_guessed(self):
        html = page([entry("1", "北京某大学", "未知所在地"), '<div class="sch-item"><a class="name" href="/bad">缺失身份</a></div>'])
        units, pagination = parse_registry_page(html)
        self.assertEqual(len(units), 1)
        self.assertIsNone(units[0]["province"])
        self.assertEqual(pagination["invalid_items"], 1)
        self.assertEqual(pagination["raw_items"], 2)
        with self.assertRaises(RegistryParseError):
            parse_registry_page('<html><title>请登录或完成验证码</title></html>')
        with self.assertRaises(RegistryParseError):
            parse_registry_page(42)

    def test_utf8_bytes_roundtrip_and_invalid_bytes_fail(self):
        self.assertEqual(decode_registry_html("南开大学".encode("utf-8")), "南开大学")
        self.assertEqual(decode_registry_html(b"\xef\xbb\xbftext"), "text")
        with self.assertRaises(UnicodeDecodeError):
            decode_registry_html(b"\xff\xfeinvalid")


class RegistryCollectionTests(unittest.TestCase):
    def test_manual_evidence_requires_a_safe_matching_url_and_aware_timestamp(self):
        evidence = {"homepage_url": "https://verified.example.edu.cn/", "checked_at": "2026-10-28T10:30:00+00:00", "verification": "homepage_name_and_admissions_navigation"}
        valid = {"official_url": evidence["homepage_url"], "official_url_evidence": evidence}
        self.assertTrue(registry._verified_official_site(valid))
        invalid = [
            {**valid, "official_url": "https://[invalid"},
            {**valid, "official_url": "javascript:alert(1)"},
            {**valid, "official_url": "https://user:password@verified.example.edu.cn/"},
            {**valid, "official_url_evidence": {**evidence, "homepage_url": "https://other.example/"}},
            {**valid, "official_url_evidence": {**evidence, "checked_at": "2026-10-28T10:30:00"}},
            {**valid, "official_url_evidence": {**evidence, "checked_at": "invalid"}},
            {**valid, "official_url_evidence": {**evidence, "verification": "search_snippet"}},
        ]
        for unit in invalid:
            with self.subTest(unit=unit):
                self.assertFalse(registry._verified_official_site(unit))

    def test_refresh_preserves_only_manually_verified_matching_official_sites(self):
        initial = page([entry("1", "甲大学"), entry("2", "乙大学", code="10002"), entry("3", "丙大学", code="10003")], pages=1).encode()
        updated = page([
            entry("1", "甲大学", extra='<a href="https://unverified.example/">官网</a>'),
            entry("2", "乙大学", code="10002"),
            entry("3", "丙大学", code="99999"),
            entry("4", "丁大学", extra='<a href="https://new.example/">官网</a>'),
        ], pages=1).encode()
        evidence = {
            "homepage_url": "https://verified.example.edu.cn/",
            "checked_at": "2026-10-28T10:30:00+00:00",
            "verification": "homepage_name_and_admissions_navigation",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "units.json"
            snapshots = Path(directory) / "snapshots"
            previous = collect_registry(output, snapshots, fetcher=lambda _: initial, delay=0)
            previous = {**previous, "units": [
                {**unit, "official_url": evidence["homepage_url"], **({"official_url_evidence": evidence} if unit["id"] != "2" else {})}
                for unit in previous["units"]
            ]}
            output.write_text(json.dumps(previous), encoding="utf-8")
            refreshed = collect_registry(output, snapshots, fetcher=lambda _: updated, delay=0)
        by_id = {unit["id"]: unit for unit in refreshed["units"]}
        self.assertEqual(by_id["1"]["official_url"], evidence["homepage_url"])
        self.assertEqual(by_id["1"]["official_url_evidence"], evidence)
        self.assertIsNone(by_id["2"]["official_url"])
        self.assertIsNone(by_id["3"]["official_url"])
        self.assertIsNone(by_id["4"]["official_url"])
        self.assertNotIn("official_url_evidence", by_id["3"])
        self.assertEqual(by_id["4"]["official_url_candidate"], "https://new.example/")

    def test_failed_refresh_keeps_previous_units_explicitly_stale(self):
        good = page([entry("1", "甲大学"), entry("2", "乙大学")], pages=1).encode()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "units.json"
            snapshots = Path(directory) / "snapshots"
            previous = collect_registry(output, snapshots, fetcher=lambda _: good, delay=0)
            old_snapshot = Path(previous["metadata"]["pages"][0]["snapshot_path"])
            failed = collect_registry(output, snapshots, fetcher=lambda _: b'<html>access restricted</html>', delay=0, retries=0)
            self.assertFalse(failed["metadata"]["complete"])
            self.assertEqual(failed["metadata"]["retained_units_count"], 2)
            self.assertEqual(failed["metadata"]["latest_attempt_unit_count"], 0)
            self.assertEqual(len(failed["units"]), 2)
            self.assertTrue(all(unit["stale"] for unit in failed["units"]))
            self.assertEqual(old_snapshot.read_bytes(), good)
            self.assertEqual(failed["units"][0]["checked_at"], previous["units"][0]["checked_at"])

    def test_collects_every_page_and_hashes_exact_raw_snapshot(self):
        first = page([entry("1", "甲大学"), entry("2", "乙大学")]).encode()
        second = page([entry("3", "丙研究所")], current=2).encode()
        requested = []

        def fetcher(url):
            requested.append(url)
            return second if "start=2" in url else first

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "units.json"
            snapshots = Path(directory) / "snapshots"
            result = collect_registry(output, snapshots, fetcher=fetcher, delay=0)
            self.assertEqual(len(requested), 2)
            self.assertTrue(result["metadata"]["complete"])
            self.assertEqual(result["metadata"]["expected_pages"], 2)
            self.assertEqual(result["metadata"]["fetched_pages"], [1, 2])
            self.assertEqual(result["metadata"]["failed_pages"], [])
            self.assertEqual(len(result["units"]), 3)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), result)
            evidence = result["metadata"]["pages"][0]
            self.assertEqual(evidence["sha256"], hashlib.sha256(first).hexdigest())
            self.assertEqual(Path(evidence["snapshot_path"]).read_bytes(), first)
            self.assertIn("不代表", "".join(result["metadata"]["notes"]))

    def test_failed_page_is_retried_then_reported_without_fake_completeness(self):
        first = page([entry("1", "甲大学"), entry("2", "乙大学")]).encode()
        calls = []

        def fetcher(url):
            calls.append(url)
            if "start=2" in url:
                raise TimeoutError("network unavailable")
            return first

        with tempfile.TemporaryDirectory() as directory:
            result = collect_registry(Path(directory) / "units.json", Path(directory) / "snapshots", fetcher=fetcher, delay=0, retries=1)
        self.assertFalse(result["metadata"]["complete"])
        self.assertEqual(result["metadata"]["fetched_pages"], [1])
        self.assertEqual(result["metadata"]["failed_pages"], [2])
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(result["units"]), 2)

    def test_duplicate_identity_and_incomplete_nonfinal_page_close_gate(self):
        first = page([entry("1", "甲大学"), entry("2", "乙大学")]).encode()
        second = page([entry("2", "乙大学")], current=2).encode()
        with tempfile.TemporaryDirectory() as directory:
            result = collect_registry(Path(directory) / "units.json", Path(directory) / "snapshots", fetcher=lambda url: second if "start=2" in url else first, delay=0)
        self.assertEqual(result["metadata"]["duplicate_ids"], ["2"])
        self.assertFalse(result["metadata"]["complete"])
        self.assertEqual(len(result["units"]), 2)
        short = page([entry("3", "不完整的第一页")]).encode()
        with tempfile.TemporaryDirectory() as directory:
            result = collect_registry(Path(directory) / "units.json", Path(directory) / "snapshots", fetcher=lambda _: short, delay=0, retries=0)
        self.assertFalse(result["metadata"]["complete"])
        self.assertIn(1, result["metadata"]["invalid_pages"])

    def test_first_page_failure_is_saved_as_incomplete_result(self):
        with tempfile.TemporaryDirectory() as directory:
            result = collect_registry(Path(directory) / "units.json", Path(directory) / "snapshots", fetcher=lambda _: b'<html>blocked</html>', delay=0, retries=0)
            self.assertEqual(result["metadata"]["expected_pages"], 0)
            self.assertEqual(result["metadata"]["failed_pages"], [1])
            self.assertFalse(result["metadata"]["complete"])
            self.assertEqual(result["units"], [])


class RegistryFetchTests(unittest.TestCase):
    def test_redirect_is_rejected_before_a_new_request_is_constructed(self):
        handler = registry._IndexRedirectHandler()
        request = Request("https://yz.chsi.com.cn/sch/")
        for target in ["http://127.0.0.1/", "https://evil.example/sch/", "https://yz.chsi.com.cn/other/", "https://yz.chsi.com.cn:443/sch/", "https://user:password@yz.chsi.com.cn/sch/"]:
            with self.subTest(target=target), self.assertRaises(RegistryParseError):
                handler.redirect_request(request, None, 302, "Found", {}, target)
        redirected = handler.redirect_request(request, None, 302, "Found", {}, "https://yz.chsi.com.cn/sch/?start=20")
        self.assertEqual(redirected.full_url, "https://yz.chsi.com.cn/sch/?start=20")
        self.assertLessEqual(handler.max_redirections, 3)

    def test_rejects_nonofficial_sources_before_network_access(self):
        with patch("collectors.registry.build_opener") as request:
            for url in ["http://127.0.0.1/", "https://example.com/sch/", "https://yz.chsi.com.cn@evil.example/sch/", "https://yz.chsi.com.cn/other/"]:
                with self.assertRaises(ValueError):
                    fetch_registry_page(url)
            request.assert_not_called()

    def test_fetch_respects_response_limit_and_redirect_validation(self):
        with patch("collectors.registry.build_opener") as request:
            response = request.return_value.open.return_value.__enter__.return_value
            response.geturl.return_value = "https://yz.chsi.com.cn/sch/"
            response.read.return_value = b"ok"
            self.assertEqual(fetch_registry_page("https://yz.chsi.com.cn/sch/"), b"ok")
            response.read.return_value = b"x" * (2 * 1024 * 1024 + 1)
            with self.assertRaises(RegistryParseError):
                fetch_registry_page("https://yz.chsi.com.cn/sch/")
            response.geturl.return_value = "https://account.chsi.com.cn/"
            with self.assertRaises(RegistryParseError):
                fetch_registry_page("https://yz.chsi.com.cn/sch/")


if __name__ == "__main__":
    unittest.main()
