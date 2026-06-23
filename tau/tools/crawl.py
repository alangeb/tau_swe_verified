"""Web crawler — follows links from a seed URL, extracts content. Pure stdlib: urllib, html.parser, json."""

from __future__ import annotations

from tools import ToolMetadata

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from agent_core import TauBot

from .fetch import _extract_links, _fetch_single
# ── Tool interface ────────────────────────────────────────────────

metadata = ToolMetadata(
    name="crawl",
    description=(
        "Crawl a website by following links from a seed URL. "
        "Extracts content from multiple pages, converts to markdown. "
        "Configurable depth, max pages, and domain restriction."
    ),
    max_size=524288,
)


# ── Crawl Cache ────────────────────────────────────────────────────

_CRAWL_CACHE_DIR = os.path.join(os.getenv("TMPDIR", "/tmp"), "tau_crawl_cache")


def _crawl_cache_key(seed: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "_", seed.lower()[:100])


# ── Link Filter ────────────────────────────────────────────────────

def _is_internal_link(url: str, seed_domain: str) -> bool:
    """Check if URL is on the same domain as seed."""
    try:
        parsed = urlparse(url)
        return parsed.netloc == seed_domain
    except Exception:
        return False


def _is_valid_page(url: str) -> bool:
    """Check if URL looks like a content page (not assets)."""
    skip_exts = (
        ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
        ".woff", ".woff2", ".ttf", ".zip", ".pdf",
    )
    skip_paths = ("/favicon.ico", "/robots.txt", "/sitemap.xml")
    parsed = urlparse(url)
    if parsed.path.lower().endswith(skip_exts):
        return False
    if parsed.path in skip_paths:
        return False
    if parsed.fragment:
        return False  # Skip anchor-only links
    return True


# ── Crawler ────────────────────────────────────────────────────────

_MAX_QUEUE_SIZE = 200  # Cap BFS queue to prevent memory exhaustion


def _crawl(
    seed_url: str, max_pages: int = 5, depth: int = 2,
    same_domain: bool = True, filter_type: str = "fit", timeout: int = 10,
) -> list[dict]:
    """Crawl a website starting from seed_url. Returns list of {url, title, content, depth} dicts."""
    if not seed_url.startswith(("http://", "https://")):
        seed_url = "https://" + seed_url

    seed_domain = urlparse(seed_url).netloc
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(seed_url, 0)]
    results: list[dict] = []

    while queue and len(results) < max_pages:
        url, current_depth = queue.pop(0)

        if url in visited:
            continue
        visited.add(url)
        if current_depth > depth:
            continue
        if same_domain and not _is_internal_link(url, seed_domain):
            continue
        if not _is_valid_page(url):
            continue

        result = _fetch_single(url, filter_type, timeout=timeout, use_cache=True)
        if result.get("error"):
            continue

        content = result.get("content", "")
        meta = result.get("metadata", {})

        results.append({
            "url": url, "title": meta.get("title", ""),
            "content": content[:5000],  # Limit per page
            "depth": current_depth, "word_count": meta.get("word_count", 0),
        })

        # Extract links from the SAME fetch (no double-fetch)
        if current_depth < depth:
            raw_html = result.get("raw_html", "")
            if raw_html:
                links = _extract_links(raw_html, url)
                for link in links:
                    link_url = link["url"]
                    if link_url not in visited and len(queue) < _MAX_QUEUE_SIZE:
                        if same_domain and not _is_internal_link(link_url, seed_domain):
                            continue
                        if _is_valid_page(link_url):
                            queue.append((link_url, current_depth + 1))

    return results


# ── Args schema ───────────────────────────────────────────────────

@dataclass
class Args:
    url: str = field(metadata={"description": "Seed URL to start crawling"})
    max_pages: int = field(default=5, metadata={"description": "Maximum pages to crawl"})
    depth: int = field(default=2, metadata={"description": "Maximum crawl depth"})
    same_domain: bool = field(default=True, metadata={"description": "Only follow same-domain links"})
    filter_type: str = field(default="fit", metadata={"description": "Content filter: raw, fit"})
    timeout: int = field(default=10, metadata={"description": "Timeout per page in seconds"})



# ── Execution ─────────────────────────────────────────────────────

def run(
    url: str, agent: "TauBot", tool_call_id: str | None = None,
    max_pages: int = 5, depth: int = 2, same_domain: bool = True,
    filter_type: str = "fit", timeout: int = 10,
) -> str:
    """Crawl a website starting from the given URL."""
    results = _crawl(url, max_pages, depth, same_domain, filter_type, timeout)

    if not results:
        return f"No pages crawled from {url}."

    summary = (
        f"# Crawl Results\n\n"
        f"Seed: {url}\n"
        f"Pages crawled: {len(results)}\n"
        f"Depth: {depth}\n\n"
    )

    for i, page in enumerate(results, 1):
        summary += f"## [{i}] {page['title']} (depth {page['depth']})\n"
        summary += f"URL: {page['url']}\n"
        summary += f"Words: {page['word_count']}\n\n"
        summary += page["content"][:3000]
        summary += "\n\n---\n\n"

    return summary
