"""Bounded downloads and immutable source snapshots from official admissions sites."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 32 * 1024 * 1024


class DocumentError(ValueError):
    pass


def _official_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = host.endswith((".edu.cn", ".ac.cn", ".gov.cn")) or host == "yz.chsi.com.cn"
    if parsed.scheme != "https" or not allowed or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise DocumentError("只允许已知官方高校、科研院所或政府的 HTTPS 来源")
    return url


class OfficialRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return super().redirect_request(request, fp, code, msg, headers, _official_url(newurl))


def decode_text(raw, content_type=""):
    declared = re.search(r"charset\s*=\s*([\w-]+)", content_type, re.I)
    encodings = [declared.group(1)] if declared else []
    for encoding in [*encodings, "utf-8-sig", "gb18030"]:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    raise DocumentError("来源文本编码无法可靠识别")


def _write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _cached_snapshot(pointers, directory, project_root, context):
    for pointer in pointers:
        if not pointer.exists():
            continue
        try:
            cached = json.loads(pointer.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(cached, dict) or any(cached.get(key) != value for key, value in context.items()):
            continue
        if not isinstance(cached.get("local_path"), str) or not isinstance(cached.get("sha256"), str):
            continue
        source = (Path(project_root) / cached["local_path"]).resolve()
        if not source.is_relative_to(directory.resolve()):
            raise DocumentError("来源快照路径超出所属目录，拒绝使用缓存")
        if source.is_file():
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() == cached["sha256"]:
                return cached, content


def fetch_snapshot(url, directory, unit, year, kind, title, *, data=None, project_root=PROJECT_ROOT, refresh=False, offline=False):
    _official_url(url)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    context = {"url": url, "unit_id": str(unit["id"]), "year": int(year), "kind": kind}
    identity = {**context, "method": "GET" if data is None else "POST", "body_sha256": hashlib.sha256(data or b"").hexdigest()}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    pointer = directory / f"{key}.metadata.json"
    legacy_key = hashlib.sha256(url.encode() + (data or b"")).hexdigest()[:20]
    pointers = [pointer, directory / f"{legacy_key}.metadata.json"] if data != b"" else [pointer]
    cached = None if refresh else _cached_snapshot(pointers, directory, project_root, context)
    if cached:
        return cached
    if offline:
        raise DocumentError("离线模式缺少有效快照，未发起网络请求")
    request = Request(url, data=data, headers={"User-Agent": "Mozilla/5.0 (compatible; GradScopePublicCatalog/1.0)", "Accept": "text/html,application/pdf,application/json"})
    try:
        with build_opener(OfficialRedirect()).open(request, timeout=30) as response:
            _official_url(response.geturl())
            raw = response.read(MAX_BYTES + 1)
            content_type = response.headers.get("Content-Type", "")
    except (URLError, TimeoutError, OSError) as error:
        raise DocumentError(f"官方来源下载失败：{type(error).__name__}") from error
    if not raw or len(raw) > MAX_BYTES:
        raise DocumentError("官方文件为空或超过32MB下载上限")
    sha = hashlib.sha256(raw).hexdigest()
    extension = ".pdf" if raw.startswith(b"%PDF-") else ".json" if "json" in content_type else ".html"
    destination = directory / f"{key}-{sha[:12]}{extension}"
    destination.write_bytes(raw)
    document = {
        "id": f"doc-{key}-{sha[:12]}", "unit_id": str(unit["id"]),
        "title": title, "url": url, "year": int(year), "kind": kind,
        "published_at": None, "checked_at": datetime.now(timezone.utc).isoformat(),
        "sha256": sha, "local_path": destination.resolve().relative_to(Path(project_root).resolve()).as_posix(),
        "status": "parsed", "content_type": content_type, "notes": [],
    }
    _write_json(pointer, document)
    return document, raw


def extract_pdf_text(path):
    executable = shutil.which("pdftotext")
    if not executable:
        raise DocumentError("PDF提取依赖pdftotext未安装或不在PATH")
    try:
        result = subprocess.run([executable, "-raw", "-enc", "UTF-8", str(path), "-"], capture_output=True, check=True, timeout=60)
        return result.stdout.decode("utf-8")
    except (subprocess.SubprocessError, UnicodeDecodeError, OSError) as error:
        raise DocumentError("PDF文字提取失败，保留原始文件等待复核") from error


def save_pdf_text(document, text, project_root=PROJECT_ROOT):
    """Preserve exact extraction bytes and numbered pages beside the original PDF."""
    root = Path(project_root).resolve()
    source = (root / document["local_path"]).resolve()
    if not source.is_relative_to(root):
        raise DocumentError("PDF来源文件超出项目目录")
    raw = text.encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    text_path = source.with_name(f"{source.stem}-text-{sha[:12]}.txt")
    text_path.write_bytes(raw)
    pages_directory = source.with_suffix(".pages")
    pages_directory.mkdir(exist_ok=True)
    pages = text.split("\f")
    if not pages[-1].strip():
        pages = pages[:-1]
    page_evidence = []
    for index, page_text in enumerate(pages, 1):
        page_raw = page_text.encode("utf-8")
        page_sha = hashlib.sha256(page_raw).hexdigest()
        path = pages_directory / f"page-{index:03d}-{page_sha[:12]}.txt"
        path.write_bytes(page_raw)
        page_evidence.append({"page": index, "local_path": path.relative_to(root).as_posix(), "sha256": page_sha})
    return {**document, "text_path": text_path.relative_to(root).as_posix(), "text_sha256": sha,
            "text_extractor": "pdftotext -raw -enc UTF-8", "text_pages": page_evidence}
