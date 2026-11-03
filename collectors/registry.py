"""Collect the public CHSI institution index, without implying catalog completeness."""

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SOURCE_URL = "https://yz.chsi.com.cn/sch/"
MAX_BYTES = 2 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/141.0.0.0 Safari/537.36"
PROVINCES = set("北京 天津 河北 山西 内蒙古 辽宁 吉林 黑龙江 上海 江苏 浙江 安徽 福建 江西 山东 河南 湖北 湖南 广东 广西 海南 重庆 四川 贵州 云南 西藏 陕西 甘肃 青海 宁夏 新疆 香港 澳门 台湾".split())
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class RegistryParseError(ValueError):
    """The returned page cannot substantiate a valid registry page."""


def _clean(value):
    return re.sub(r"\s+", " ", value).strip()


def _identifier(url):
    match = re.search(r"schId-(\d+)(?:[.,/]|$)", url)
    return match.group(1) if match else parse_qs(urlsplit(url).query).get("schId", [None])[0]


class _RegistryParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.stack = []
        self.current = None
        self.item_depth = None
        self.department_depth = None
        self.link = None
        self.entries = []
        self.offsets = [0]
        self.current_page = None
        self.raw_items = 0
        self.invalid_items = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in VOID_TAGS:
            self.stack = [*self.stack, tag]
        classes = attrs.get("class", "").split()
        if tag == "div" and "sch-item" in classes:
            if self.current is not None:
                self.invalid_items += 1
            self.current = {"name": "", "id": None, "unit_code": None, "province": None, "source_url": None, "official_url": None}
            self.item_depth = len(self.stack)
            self.raw_items += 1
        if tag == "input" and attrs.get("id") == "p_num" and attrs.get("value", "").isdigit():
            self.current_page = int(attrs["value"])
        if tag == "a":
            self._start_link(attrs, classes)
        if self.current is None:
            return
        if tag == "div" and "sch-department" in classes:
            self.department_depth = len(self.stack)
        if tag == "img":
            match = re.search(r"/common/xh/(\d+)\.jpg(?:\?|$)", attrs.get("src", ""))
            if match:
                self.current = {**self.current, "unit_code": match.group(1)}

    def _start_link(self, attrs, classes):
        href = urljoin(self.base_url, attrs.get("href", ""))
        parsed = urlsplit(href)
        query = parse_qs(parsed.query)
        if parsed.netloc == "yz.chsi.com.cn" and parsed.path == "/sch/":
            offset = query.get("start", [""])[0]
            if offset.isdigit():
                self.offsets = [*self.offsets, int(offset)]
        if self.current is None:
            return
        identifier = _identifier(href)
        name_link = identifier is not None and ("name" in classes or self.current["id"] is None)
        self.link = {"href": href, "name_link": name_link, "parts": []}
        if name_link:
            self.current = {**self.current, "id": identifier, "source_url": href}
        code = query.get("dwdm", [""])[0].strip()
        if code.isdigit() and parsed.netloc == "yz.chsi.com.cn":
            self.current = {**self.current, "unit_code": code}

    def handle_data(self, data):
        if self.current is None:
            return
        if self.link is not None:
            self.link = {**self.link, "parts": [*self.link["parts"], data]}
        if self.department_depth is not None and _clean(data) in PROVINCES:
            self.current = {**self.current, "province": _clean(data)}

    def handle_endtag(self, tag):
        depth = len(self.stack)
        if tag == "a" and self.link is not None:
            self._end_link()
        if tag == "div" and depth == self.department_depth:
            self.department_depth = None
        if tag == "div" and depth == self.item_depth:
            self._end_item()
        if tag in self.stack:
            reverse = self.stack[::-1].index(tag)
            self.stack = self.stack[:len(self.stack) - reverse - 1]

    def _end_link(self):
        label = _clean("".join(self.link["parts"]))
        if self.current is not None:
            if self.link["name_link"]:
                self.current = {**self.current, "name": label}
            parsed = urlsplit(self.link["href"])
            if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.hostname.endswith("chsi.com.cn") and not parsed.username and re.search(r"官网|官方网站|研究生招生网|学校主页|院校主页", label):
                self.current = {**self.current, "official_url": self.link["href"]}
        self.link = None

    def _end_item(self):
        if self.current and self.current["id"] and self.current["name"]:
            self.entries = [*self.entries, {**self.current, "chsi_id": self.current["id"], "checked_at": None}]
        else:
            self.invalid_items += 1
        self.current = None
        self.item_depth = None
        self.department_depth = None
        self.link = None


def parse_registry_page(html, base_url=SOURCE_URL):
    """Return (units, pagination); malformed or blocked pages never become empty success."""
    if not isinstance(html, str):
        raise RegistryParseError("Registry HTML must be text")
    parser = _RegistryParser(base_url)
    parser.feed(html)
    parser.close()
    if parser.current is not None:
        parser.invalid_items += 1
    if not parser.entries:
        raise RegistryParseError("No public institution records found; page may be unavailable or access restricted")
    formula = re.search(r"pagenum\s*=\s*\(\s*Num\s*-\s*1\s*\)\s*\*\s*(\d+)", html)
    page_size = int(formula.group(1)) if formula else math.gcd(*parser.offsets) or 20
    if page_size <= 0 or page_size > 100:
        raise RegistryParseError("Invalid registry page size")
    declared = re.search(r"Num\s*>\s*(\d+)", html)
    last_offset = max(parser.offsets)
    total_pages = max(int(declared.group(1)) if declared else 1, last_offset // page_size + 1)
    if total_pages > 200 or total_pages < 1:
        raise RegistryParseError("Invalid registry page count")
    offset = parse_qs(urlsplit(base_url).query).get("start", ["0"])[0]
    current_page = parser.current_page or (int(offset) // page_size + 1 if offset.isdigit() else 1)
    return parser.entries, {
        "current_page": current_page, "total_pages": total_pages,
        "page_size": page_size, "last_offset": (total_pages - 1) * page_size,
        "raw_items": parser.raw_items, "invalid_items": parser.invalid_items,
    }


def decode_registry_html(raw):
    return raw.decode("utf-8-sig")


def _is_index_url(url):
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.netloc == "yz.chsi.com.cn" and parsed.path == "/sch/"


class _IndexRedirectHandler(HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def redirect_request(self, request, response, code, message, headers, new_url):
        if not _is_index_url(new_url):
            raise RegistryParseError("Registry redirect target is outside the public index")
        return super().redirect_request(request, response, code, message, headers, new_url)


def fetch_registry_page(url, timeout=20):
    if not _is_index_url(url):
        raise ValueError("Only the public CHSI institution index is allowed")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with build_opener(_IndexRedirectHandler()).open(request, timeout=timeout) as response:
        if not _is_index_url(response.geturl()):
            raise RegistryParseError("Registry request redirected away from the public index")
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise RegistryParseError("Registry page exceeded the size limit")
    return raw


def _page_url(number, page_size):
    if number == 1:
        return SOURCE_URL
    parsed = urlsplit(SOURCE_URL)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode({"start": (number - 1) * page_size}), ""))


def _checked_at():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(output, value):
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=output.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _request_page(number, page_size, snapshot_dir, fetcher, delay, retries):
    url = _page_url(number, page_size)
    error = None
    for attempt in range(retries + 1):
        try:
            raw = fetcher(url)
            snapshot = snapshot_dir / f"page-{number:03}.html"
            snapshot.write_bytes(raw)
            entries, pagination = parse_registry_page(decode_registry_html(raw), url)
            checked_at = _checked_at()
            evidence = {"page": number, "url": url, "checked_at": checked_at, "sha256": hashlib.sha256(raw).hexdigest(), "snapshot_path": str(snapshot.resolve()), "record_count": len(entries), "raw_items": pagination["raw_items"], "invalid_items": pagination["invalid_items"]}
            return [{**entry, "checked_at": checked_at, "index_source_url": url} for entry in entries], pagination, evidence, None
        except (OSError, ValueError) as caught:
            error = f"{type(caught).__name__}: {caught}"
            if attempt < retries and delay:
                time.sleep(delay)
    return [], None, None, error


def _read_previous(output):
    try:
        cached = json.loads(output.read_text(encoding="utf-8"))
        return cached if isinstance(cached, dict) else {}
    except (OSError, ValueError):
        return {}


def _verified_official_site(unit):
    url = unit.get("official_url")
    evidence = unit.get("official_url_evidence")
    if not isinstance(url, str) or not isinstance(evidence, dict):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    if evidence.get("homepage_url") != url or evidence.get("verification") != "homepage_name_and_admissions_navigation":
        return False
    try:
        return datetime.fromisoformat(evidence["checked_at"]).tzinfo is not None
    except (KeyError, TypeError, ValueError):
        return False


def _preserve_official_site(unit, previous_units):
    clean = {key: value for key, value in unit.items() if key not in {"official_url", "official_url_evidence", "official_url_candidate"}}
    identity = (unit["id"], unit.get("unit_code"))
    previous = previous_units.get(identity, {}) if all(identity) else {}
    if _verified_official_site(previous):
        return {**clean, "official_url": previous["official_url"], "official_url_evidence": {**previous["official_url_evidence"]}}
    candidate = unit.get("official_url_candidate") or unit.get("official_url")
    return {**clean, "official_url": None, **({"official_url_candidate": candidate} if candidate else {})}


def _retain_previous(result, output):
    metadata = result["metadata"]
    previous = _read_previous(output)
    cached_units = previous.get("units", [])
    cached_units = [unit for unit in cached_units if isinstance(unit, dict) and isinstance(unit.get("id"), str) and isinstance(unit.get("name"), str)] if isinstance(cached_units, list) else []
    trusted_units = {(unit["id"], unit.get("unit_code")): unit for unit in cached_units if isinstance(unit.get("unit_code"), str)}
    current_ids = {unit["id"] for unit in result["units"]}
    retained = [{**unit, "stale": True} for unit in cached_units if unit["id"] not in current_ids] if not metadata["complete"] else []
    all_units = [_preserve_official_site(unit, trusted_units) for unit in [*result["units"], *retained]]
    old_metadata = previous.get("metadata", {})
    old_metadata = old_metadata if isinstance(old_metadata, dict) else {}
    return {"metadata": {
        **metadata, "unit_count": len(all_units), "latest_attempt_unit_count": len(result["units"]),
        "retained_units_count": len(retained),
        "last_successful_at": metadata["checked_at"] if metadata["complete"] else old_metadata.get("last_successful_at") or (old_metadata.get("checked_at") if old_metadata.get("complete") else None),
    }, "units": all_units}


def collect_registry(output, snapshot_dir, *, fetcher=None, delay=0.65, retries=2):
    """Fetch all declared pages serially; persist failures and exact snapshots."""
    if delay < 0 or retries < 0 or retries > 5:
        raise ValueError("delay must be non-negative and retries must be between 0 and 5")
    output, snapshot_dir = Path(output), Path(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir = Path(tempfile.mkdtemp(prefix=f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-", dir=snapshot_dir))
    fetcher = fetcher or fetch_registry_page
    first_entries, first_pagination, first_evidence, first_error = _request_page(1, 20, snapshot_dir, fetcher, delay, retries)
    expected_pages = first_pagination["total_pages"] if first_pagination else 0
    page_size = first_pagination["page_size"] if first_pagination else 20
    collected = {"units": {}, "pages": [], "failed": [], "errors": {}, "duplicates": [], "invalid": []}
    for number in range(1, max(expected_pages, 1) + 1):
        if number == 1:
            entries, pagination, evidence, error = first_entries, first_pagination, first_evidence, first_error
        else:
            if delay:
                time.sleep(delay)
            entries, pagination, evidence, error = _request_page(number, page_size, snapshot_dir, fetcher, delay, retries)
        if error:
            collected = {**collected, "failed": [*collected["failed"], number], "errors": {**collected["errors"], str(number): error}}
            continue
        invalid = pagination["invalid_items"] or pagination["current_page"] != number or pagination["total_pages"] != expected_pages or not entries or (number < expected_pages and len(entries) != page_size) or any(entry["province"] is None for entry in entries)
        collected = {**collected, "pages": [*collected["pages"], evidence], "invalid": [*collected["invalid"], number] if invalid else collected["invalid"]}
        for entry in entries:
            if entry["id"] in collected["units"]:
                collected = {**collected, "duplicates": [*collected["duplicates"], entry["id"]]}
            else:
                collected = {**collected, "units": {**collected["units"], entry["id"]: entry}}
    fetched_pages = [page["page"] for page in collected["pages"]]
    complete = bool(expected_pages) and len(fetched_pages) == expected_pages and not any(collected[key] for key in ("failed", "duplicates", "invalid"))
    result = {"metadata": {
        "source_url": SOURCE_URL, "checked_at": _checked_at(), "expected_pages": expected_pages,
        "page_size": page_size, "fetched_pages": fetched_pages, "failed_pages": collected["failed"],
        "page_errors": collected["errors"], "invalid_pages": collected["invalid"],
        "duplicate_ids": sorted(set(collected["duplicates"])), "pages": collected["pages"],
        "unit_count": len(collected["units"]), "complete": complete,
        "notes": ["仅代表研招网公开招生单位索引本轮分页采集，不代表全国专业目录、考试科目或2028招生资格已完整。", "未取得院校招生官网时official_url保留空值，source_url为研招网公开校页。"],
    }, "units": list(collected["units"].values())}
    result = _retain_previous(result, output)
    _write_json(output, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/units.json"))
    parser.add_argument("--snapshot-dir", type=Path, default=Path("data/snapshots/registry"))
    parser.add_argument("--delay", type=float, default=0.65)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args(argv)
    result = collect_registry(args.output, args.snapshot_dir, delay=args.delay, retries=args.retries)
    metadata = result["metadata"]
    print(json.dumps({"unit_count": len(result["units"]), "expected_pages": metadata["expected_pages"], "fetched_pages": len(metadata["fetched_pages"]), "failed_pages": metadata["failed_pages"], "invalid_pages": metadata["invalid_pages"], "complete": metadata["complete"]}, ensure_ascii=False))
    return 0 if metadata["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
