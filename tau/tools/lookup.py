"""Lookup tool — Wikipedia + DuckDuckGo Instant Answer."""

from __future__ import annotations

from tools import ToolMetadata

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ────────────────────────────────────────────────

metadata = ToolMetadata(
    name="lookup",
    description=(
        "Look up definitions, facts, and encyclopedic information. "
        "Uses Wikipedia and DuckDuckGo Instant Answer. "
        "Returns JSON array of {title, url, snippet, source}. "
        "For general web search with time filtering, use 'search' instead."
    ),
    max_size=32768,
)

# ── Cache ────────────────────────────────────────────────────────

_CACHE_DIR = os.path.join(os.getenv("TMPDIR", "/tmp"), "tau_lookup_cache")
_CACHE_TTL = 3600  # 1 hour


def _cache_key(query: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", f"lookup_{query}".lower()[:100])


def _cleanup_cache(cache_dir: str, ttl: int) -> None:
    try:
        cutoff = time.time() - ttl
        for fname in os.listdir(cache_dir):
            fpath = os.path.join(cache_dir, fname)
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
    except OSError:
        pass


def _load_cache(query: str) -> list[dict] | None:
    path = os.path.join(_CACHE_DIR, f"{_cache_key(query)}.json")
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > _CACHE_TTL:
        return None
    with open(path, "r") as f:
        return json.load(f)


def _save_cache(query: str, results: list[dict]) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    _cleanup_cache(_CACHE_DIR, _CACHE_TTL)
    path = os.path.join(_CACHE_DIR, f"{_cache_key(query)}.json")
    with open(path, "w") as f:
        json.dump(results, f)


# ── Search sources ───────────────────────────────────────────────

def _lookup_wikipedia(query: str, limit: int = 5) -> list[dict]:
    base = "https://en.wikipedia.org/w/api.php"
    params = urlencode({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": str(limit),
        "srwhat": "text",
        "format": "json",
    })
    url = f"{base}?{params}"
    req = Request(url, headers={"User-Agent": "TauBot/1.0"})
    try:
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, OSError):
        return []

    results: list[dict] = []
    for item in data.get("query", {}).get("search", []):
        snippet = item.get("snippet", "")
        snippet = snippet.replace("<span class='searchmatch'><b>", "")
        snippet = snippet.replace("</b></span>", "")
        snippet = re.sub(r"<[^>]+>", "", snippet)
        title = item.get("title", "")
        pageid = item.get("pageid", "")
        url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
        results.append({
            "title": title,
            "url": url,
            "snippet": snippet[:500],
            "source": "wikipedia",
            "pageid": str(pageid),
        })
    return results


def _lookup_ddg_instant(query: str) -> list[dict]:
    url = f"https://api.duckduckgo.com/?q={quote(query)}&format=json"
    req = Request(url, headers={"User-Agent": "TauBot/1.0"})
    try:
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, OSError):
        return []

    results: list[dict] = []
    abstract = data.get("Abstract", "")
    if abstract:
        results.append({
            "title": data.get("Heading", query),
            "url": data.get("AbstractURL") or data.get("FirstURL", ""),
            "snippet": abstract[:500],
            "source": "duckduckgo_instant",
            "image": data.get("Image", ""),
        })
    return results


# ── Dedup & orchestration ────────────────────────────────────────

def _dedup(results: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in results:
        url = r.get("url", "").lower()
        if url and url not in seen:
            seen.add(url)
            deduped.append(r)
    return deduped


def _do_lookup(query: str, limit: int = 5) -> list[dict]:
    all_results: list[dict] = []
    all_results.extend(_lookup_ddg_instant(query))
    all_results.extend(_lookup_wikipedia(query, limit))
    return _dedup(all_results)[:limit]


# ── Args schema ──────────────────────────────────────────────────

@dataclass
class Args:
    query: str = field(
        metadata={
            "description": "Lookup query string (e.g., 'Python programming language', 'John Doe', 'quantum computing')"
        }
    )
    limit: int = field(default=5, metadata={"description": "Max results to return"})
    cache: bool = field(default=True, metadata={"description": "Use local cache"})



# ── Execution ────────────────────────────────────────────────────

def run(
    query: str,
    agent: "TauBot",
    tool_call_id: str | None = None,
    limit: int = 5,
    cache: bool = True,
) -> str:
    if cache:
        cached = _load_cache(query)
        if cached:
            return json.dumps(cached, indent=2)

    results = _do_lookup(query, limit)

    if cache and results:
        _save_cache(query, results)

    if not results:
        return "No results found."

    return json.dumps(results, indent=2)
