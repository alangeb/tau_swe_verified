"""Native web fetch (stdlib only). Fetches HTML, extracts main content, strips noise, converts to compact markdown."""

from __future__ import annotations

from tools import ToolMetadata

import gzip
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from agent_core import TauBot

from .lib.html_to_md import extract_main_content, html_to_markdown, strip_noise
# ── Tool interface ────────────────────────────────────────────────
metadata = ToolMetadata(
    name="fetch",
    description=(
        "Fetch and extract content from web pages natively. "
        "Converts HTML to compact markdown with aggressive noise stripping. "
        "Targets only article body text (no infoboxes, references, categories). "
        "Hard character limits prevent context explosion. "
        "No external services required."
    ),
    max_size=262144,
    timeout=30,
)

# ── Cache ──────────────────────────────────────────────────────────

_CACHE_DIR = os.path.join(os.getenv("TMPDIR", "/tmp"), "tau_fetch_cache")
_CACHE_TTL = 3600  # 1 hour


def _url_key(url: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "_", url.lower()[:200])


def _cleanup_cache(cache_dir: str, ttl: int) -> None:
    """Remove stale cache files older than *ttl* seconds."""
    try:
        cutoff = time.time() - ttl
        for fname in os.listdir(cache_dir):
            fpath = os.path.join(cache_dir, fname)
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
    except OSError:
        pass


def _load_cache(url: str) -> str | None:
    path = os.path.join(_CACHE_DIR, f"{_url_key(url)}.md")
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > _CACHE_TTL:
        return None
    with open(path, "r") as f:
        return f.read()


def _save_cache(url: str, content: str) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    _cleanup_cache(_CACHE_DIR, _CACHE_TTL)
    path = os.path.join(_CACHE_DIR, f"{_url_key(url)}.md")
    with open(path, "w") as f:
        f.write(content)


# ── Crawl4AI Legacy Fetch (optional first-attempt) ──────────────

def _try_crawl4ai_single(
    url: str, base_url: str, filter_type: str = "fit", query: str = "extract main content",
) -> str | None:
    """Try Crawl4AI /md endpoint. Returns raw markdown or None on failure."""
    try:
        payload = json.dumps({"url": url, "f": filter_type, "q": query, "c": "0"})
        cmd = (
            f"curl -s --connect-timeout 1 --max-time 3 "
            f'"{base_url}/md" '
            f'-H "Content-Type: application/json" '
            f"-d '{payload}'"
        )
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5, start_new_session=True)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, Exception):
        return None


def _try_crawl4ai_multi(
    urls: list[str], base_url: str, filter_type: str = "fit", query: str = "extract main content",
) -> list[dict] | None:
    """Try Crawl4AI /crawl endpoint. Returns list[dict] or None on failure."""
    try:
        payload = json.dumps({"urls": urls, "f": filter_type, "q": query, "c": "0"})
        cmd = (
            f"curl -s --connect-timeout 1 --max-time 5 "
            f'"{base_url}/crawl" '
            f'-H "Content-Type: application/json" '
            f"-d '{payload}'"
        )
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8, start_new_session=True)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        data = json.loads(result.stdout)
        return data if isinstance(data, list) else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return None


# ── HTTP Fetcher ───────────────────────────────────────────────────

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _fetch_url(url: str, timeout: int = 15) -> tuple[str, dict]:
    """Fetch a URL and return (html_content, metadata_dict)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.8,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, identity",
        "Connection": "close",
    }

    meta: dict = {
        "url": url, "status": 0, "content_type": "", "title": "",
        "og_title": "", "og_description": "", "og_image": "",
        "language": "", "favicon": "", "word_count": 0, "fetch_time_ms": 0,
    }

    t0 = time.time()
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            meta["status"] = resp.status
            meta["content_type"] = resp.headers.get("Content-Type", "")
            raw = resp.read()
            if resp.headers.get("Content-Encoding", "") == "gzip":
                raw = gzip.decompress(raw)
            content = raw.decode("utf-8", errors="replace")
            meta["fetch_time_ms"] = int((time.time() - t0) * 1000)
            meta["url"] = resp.url
    except HTTPError as e:
        meta["status"] = e.code
        try:
            content = e.read().decode("utf-8", errors="replace")
        except Exception:
            content = ""
    except (URLError, TimeoutError, OSError) as e:
        return "", {**meta, "error": str(e)}

    if "text/html" in meta["content_type"]:
        _extract_metadata(content, meta)

    return content, meta


# ── Metadata Extractor ────────────────────────────────────────────

def _extract_metadata(html_text: str, meta: dict) -> None:
    """Extract title and OG metadata from the <head> section only."""
    head_end = html_text.lower().find("</head>")
    head = html_text[:head_end] if head_end > 0 else html_text[:10000]

    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
    if m:
        meta["title"] = m.group(1).strip()

    for prop, key in (
        ("og:title", "og_title"), ("og:description", "og_description"), ("og:image", "og_image"),
    ):
        m = re.search(
            rf'<meta[^>]*property="[^"]*{prop}[^"]*"[^>]*content="([^"]*)"', head, re.I
        )
        if m:
            meta[key] = m.group(1)

    m = re.search(r'<html[^>]*lang="([^"]*)"', head, re.I)
    if m:
        meta["language"] = m.group(1)

    m = re.search(r'<link[^>]*rel="[^"]*icon[^"]*"[^>]*href="([^"]*)"', head, re.I)
    if m:
        meta["favicon"] = m.group(1)


# ── Link Extractor ────────────────────────────────────────────────

def _extract_links(html_text: str, base_url: str, limit: int = 20) -> list[dict]:
    """Extract links from HTML."""
    links: list[dict] = []
    seen: set[str] = set()
    pat = re.compile(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.I)
    for match in pat.finditer(html_text):
        href = match.group(1).strip()
        text = re.sub(r"<[^>]+>", "", match.group(2)).strip() or href
        if not href.startswith(("http://", "https://", "mailto:", "#")):
            href = urljoin(base_url, href)
        if href not in seen and not href.startswith("#"):
            seen.add(href)
            links.append({"url": href, "text": text[:100]})
        if len(links) >= limit:
            break
    return links


# ── Crawl4AI config helper ───────────────────────────────────────

def _get_crawl4ai_url() -> str:
    """Return configured Crawl4AI URL, or empty string if not configured."""
    try:
        from agent_config import get_config
        return get_config().external_services.crawl4ai_url
    except Exception:
        return ""


# ── Single URL Fetch ──────────────────────────────────────────────

def _fetch_single(
    url: str, filter_type: str = "fit", query: str = "extract main content",
    timeout: int = 15, use_cache: bool = True, max_length: int = 30000,
) -> dict:
    """Fetch a single URL and return structured result. Includes ``raw_html`` key for callers like crawl."""
    if use_cache:
        cached = _load_cache(url)
        if cached:
            return {"url": url, "content": cached, "cached": True}

    # Try Crawl4AI first if configured
    crawl4ai_url = _get_crawl4ai_url()
    if crawl4ai_url:
        md = _try_crawl4ai_single(url, crawl4ai_url, filter_type, query)
        if md:
            return {"url": url, "content": md, "content_type": "markdown",
                    "metadata": {"word_count": len(md.split())}}

    raw_html, meta = _fetch_url(url, timeout)

    if meta.get("error"):
        return {"url": url, "error": meta["error"], "status": meta.get("status", 0)}
    if meta.get("status", 0) >= 400:
        return {"url": url, "error": f"HTTP {meta['status']}", "status": meta["status"]}

    content_type = meta.get("content_type", "")

    # JSON response — return as-is (limited)
    if "application/json" in content_type:
        return {"url": url, "content": raw_html[:max_length], "content_type": "json", "metadata": meta}

    # HTML → extract content → markdown
    if "text/html" in content_type:
        if filter_type == "raw":
            md = raw_html[:max_length]
        else:
            main_html = extract_main_content(raw_html)
            main_html = strip_noise(main_html)
            md = html_to_markdown(main_html, strip_noise_flag=False, max_chars=max_length)

        meta["word_count"] = len(md.split())

        if use_cache:
            _save_cache(url, md)

        links = _extract_links(raw_html, url) if filter_type != "raw" else []

        return {
            "url": url, "content": md, "raw_html": raw_html,
            "content_type": "markdown", "metadata": meta, "links": links,
        }

    # Plain text or other
    return {"url": url, "content": raw_html[:max_length], "content_type": content_type or "text/plain", "metadata": meta}


# ── Args schema ───────────────────────────────────────────────────

@dataclass
class Args:
    url: str = field(metadata={"description": "URL to fetch (or comma-separated URLs for batch)"})
    filter_type: str = field(default="fit", metadata={"description": "Content filter: raw, fit, bm25"})
    query: str = field(default="extract main content", metadata={"description": "Query for bm25 filter"})
    timeout: int = field(default=15, metadata={"description": "Timeout in seconds per URL"})
    cache: bool = field(default=True, metadata={"description": "Use local cache"})
    links: bool = field(default=False, metadata={"description": "Include extracted links in output"})
    metadata_only: bool = field(default=False, metadata={"description": "Return only metadata, no content"})
    max_length: int = field(default=30000, metadata={"description": "Max output length in characters"})



# ── Execution ─────────────────────────────────────────────────────

def _format_result(result: dict, links: bool, metadata_only: bool) -> dict:
    """Post-process a fetch result for output."""
    if not links:
        result.pop("links", None)
    if metadata_only:
        return {"url": result.get("url", ""), "metadata": result.get("metadata", {})}
    return result


def run(
    url: str, agent: "TauBot", tool_call_id: str | None = None,
    filter_type: str = "fit", query: str = "extract main content",
    timeout: int = 15, cache: bool = True, links: bool = False,
    metadata_only: bool = False, max_length: int = 30000,
) -> str:
    """Fetch and process one or more URLs with optional Crawl4AI first-attempt."""
    urls = [u.strip() for u in url.split(",") if u.strip()]

    # Multi-URL batch
    if len(urls) > 1:
        # Try Crawl4AI multi for batch URLs if configured
        crawl4ai_url = _get_crawl4ai_url()
        if crawl4ai_url:
            multi_results = _try_crawl4ai_multi(urls, crawl4ai_url, filter_type, query)
            if multi_results:
                return json.dumps(multi_results, indent=2, ensure_ascii=False)

        # Fallback to native
        results = [
            _format_result(_fetch_single(u, filter_type, query, timeout, cache, max_length), links, metadata_only)
            for u in urls
        ]
        return json.dumps(results, indent=2, ensure_ascii=False)

    # Single URL
    result = _fetch_single(urls[0], filter_type, query, timeout, cache, max_length)

    if metadata_only:
        return json.dumps(result.get("metadata", {}), indent=2, ensure_ascii=False)

    if result.get("error"):
        return f"ERROR: {result['error']}"

    content = result.get("content", "")
    parts: list[str] = []
    meta = result.get("metadata", {})

    if meta.get("title"):
        parts.append(f"# {meta['title']}")
        parts.append("")

    if meta.get("og_description") and not metadata_only:
        parts.append(f"> {meta['og_description']}")
        parts.append("")

    parts.append(content)

    if links and result.get("links"):
        parts.append("")
        parts.append("## Links")
        for link in result["links"][:15]:
            parts.append(f"- [{link['text']}]({link['url']})")

    output = "\n".join(parts)
    if len(output) > max_length:
        orig_len = len(output)
        output = output[:max_length] + f"\n\n[... truncated (total exceeded {orig_len} chars)]"
    return output
