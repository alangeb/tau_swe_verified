"""Native web search — DuckDuckGo HTML + Mojeek. Uses ONLY Python stdlib."""

from __future__ import annotations

from tools import ToolMetadata

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ────────────────────────────────────────────────
metadata = ToolMetadata(
    name="search",
    description=(
        "Search the web for relevant pages using DuckDuckGo and Mojeek. "
        "Returns JSON array of {title, url, snippet, source}. "
        "Supports time filtering: 'd' (day), 'w' (week), 'm' (month), 'y' (year). "
        "For definitions and factual lookups, use 'lookup' instead."
    ),
    max_size=32768,
    timeout=30,
)

# ── Cache ────────────────────────────────────────────────────────

_CACHE_DIR = os.path.join(os.getenv("TMPDIR", "/tmp"), "tau_search_cache")
_CACHE_TTL = 300  # 5 minutes


def _cache_key(query: str, timelimit: str | None = None) -> str:
    suffix = f"_{timelimit}" if timelimit else ""
    return re.sub(r"[^a-z0-9]", "_", f"search_{query}{suffix}".lower()[:100])


def _cleanup_cache(cache_dir: str, ttl: int) -> None:
    try:
        cutoff = time.time() - ttl
        for fname in os.listdir(cache_dir):
            fpath = os.path.join(cache_dir, fname)
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
    except OSError:
        pass


def _load_cache(query: str, timelimit: str | None = None) -> list[dict] | None:
    path = os.path.join(_CACHE_DIR, f"{_cache_key(query, timelimit)}.json")
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > _CACHE_TTL:
        return None
    with open(path, "r") as f:
        return json.load(f)


def _save_cache(query: str, results: list[dict], timelimit: str | None = None) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    _cleanup_cache(_CACHE_DIR, _CACHE_TTL)
    path = os.path.join(_CACHE_DIR, f"{_cache_key(query, timelimit)}.json")
    with open(path, "w") as f:
        json.dump(results, f)


# ── SearXNG (optional first-attempt) ────────────────────────────

def _try_searxng(query: str, base_url: str, limit: int = 10) -> list[dict]:
    try:
        url = f"{base_url}/search?q={quote(query)}&format=json&language=auto&categories=general"
        result = subprocess.run(
            ["curl", "-s", "--connect-timeout", "1", "--max-time", "3", url],
            capture_output=True,
            text=True,
            timeout=5,
            start_new_session=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
        raw = data.get("results", [])
        if not raw:
            return []
        normalized: list[dict] = []
        for item in raw[:limit]:
            normalized.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
                "source": "searxng",
            })
        return normalized
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return []


# ── User-Agent ───────────────────────────────────────────────────

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_DDG_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
    "Referer": "https://duckduckgo.com/",
    "Origin": "https://duckduckgo.com",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# ── Time limit helpers ───────────────────────────────────────────

TIME_MAP = {
    "d": "d", "day": "d", "1d": "d",
    "w": "w", "week": "w", "1w": "w", "7d": "w",
    "m": "m", "month": "m", "1m": "m", "3m": "m",
    "y": "y", "year": "y", "1y": "y",
}


def _normalize_timelimit(timelimit: str | None) -> str | None:
    if not timelimit:
        return None
    return TIME_MAP.get(timelimit.strip().lower())


# ── DuckDuckGo HTML Search (POST) ────────────────────────────────

def _search_ddg_html(
    query: str, limit: int = 10, timelimit: str | None = None
) -> list[dict]:
    url = "https://html.duckduckgo.com/html/"
    payload = f"q={quote(query)}&b=&l=us-en"
    if timelimit:
        payload += f"&df={timelimit}"
    data = payload.encode()

    try:
        req = Request(url, data=data, headers=_DDG_HEADERS)
        with urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (URLError, HTTPError, TimeoutError, OSError):
        return []

    if "captcha" in html.lower() or "anomaly" in html.lower():
        return []

    results: list[dict] = []
    pattern = re.compile(
        r'<a rel="nofollow" class="result__a" href="([^"]*)"[^>]*>(.*?)</a>', re.DOTALL
    )
    matches = pattern.findall(html)

    for href, text in matches:
        if "y.js?" in href:
            continue
        clean_text = re.sub(r"<[^>]+>", "", text).strip()
        if not clean_text:
            continue
        results.append({
            "title": clean_text,
            "url": href,
            "snippet": "",
            "source": "duckduckgo",
        })

    return results[:limit]


# ── Mojeek Search ────────────────────────────────────────────────

def _search_mojeek(
    query: str, limit: int = 10, timelimit: str | None = None
) -> list[dict]:
    url = f"https://www.mojeek.com/search?q={quote(query)}"
    if timelimit:
        url += f"&t={timelimit}"
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (URLError, HTTPError, TimeoutError, OSError):
        return []

    if "captcha" in html.lower() or "anomaly" in html.lower():
        return []

    results: list[dict] = []
    h2_pattern = re.compile(r"<h2[^>]*>(.*?)</h2>", re.DOTALL)
    p_pattern = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL)

    h2_tags = h2_pattern.findall(html)
    p_tags = p_pattern.findall(html)

    for h2, p in zip(h2_tags[:limit], p_tags[:limit]):
        link_match = re.search(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', h2, re.DOTALL)
        if link_match:
            url = link_match.group(1)
            title = re.sub(r"<[^>]+>", "", link_match.group(2)).strip()
            snippet = re.sub(r"<[^>]+>", "", p).strip()[:300]
            if "Results " in snippet and "from" in snippet:
                continue
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "source": "mojeek",
            })

    return results


# ── Combined Search ──────────────────────────────────────────────

def _dedup(results: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in results:
        url = r.get("url", "").lower()
        if url and url not in seen:
            seen.add(url)
            deduped.append(r)
    return deduped


def _do_search(query: str, limit: int = 10, timelimit: str | None = None) -> list[dict]:
    all_results: list[dict] = []
    all_results.extend(_search_ddg_html(query, limit, timelimit))
    all_results.extend(_search_mojeek(query, limit, timelimit))
    return _dedup(all_results)[:limit]


# ── Args schema ──────────────────────────────────────────────────

@dataclass
class Args:
    query: str = field(metadata={"description": "Search query string"})
    limit: int = field(default=10, metadata={"description": "Max results to return"})
    cache: bool = field(default=True, metadata={"description": "Use local cache"})
    timelimit: str = field(
        default="",
        metadata={
            "description": "Time filter: d (day), w (week), m (month), y (year). Empty = no filter."
        },
    )



# ── Execution ────────────────────────────────────────────────────

def run(
    query: str,
    agent: "TauBot",
    tool_call_id: str | None = None,
    limit: int = 10,
    cache: bool = True,
    timelimit: str = "",
) -> str:
    tl = _normalize_timelimit(timelimit) if timelimit else None

    if cache:
        cached = _load_cache(query, tl)
        if cached:
            return json.dumps(cached, indent=2)

    # Try SearXNG first if configured
    searxng_url = ""
    try:
        from agent_config import get_config
        cfg = get_config()
        searxng_url = cfg.external_services.searxng_url
    except Exception:
        pass

    if searxng_url:
        results = _try_searxng(query, searxng_url, limit)
        if results:
            if cache:
                _save_cache(query, results, tl)
            return json.dumps(results, indent=2)

    # Fallback to native DDG+Mojeek
    results = _do_search(query, limit, tl)

    if cache and results:
        _save_cache(query, results, tl)

    if not results:
        return "No results found."

    return json.dumps(results, indent=2)
