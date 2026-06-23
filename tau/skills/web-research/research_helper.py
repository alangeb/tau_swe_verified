#!/usr/bin/env python3
"""Web research helper - research automation utilities."""

import subprocess
from typing import Optional


def search_web(query: str, limit: int = 10) -> list[dict]:
    """Search web using DuckDuckGo."""
    result = subprocess.run(
        f"ddgr '{query}' -n {limit} 2>/dev/null",
        shell=True, capture_output=True, text=True
    )
    results = []
    for line in result.stdout.strip().split('\n'):
        if line.strip():
            results.append({"snippet": line})
    return results


def fetch_url(url: str) -> str:
    """Fetch and extract content from URL."""
    result = subprocess.run(
        f"curl -s '{url}' 2>/dev/null",
        shell=True, capture_output=True, text=True
    )
    return result.stdout


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        results = search_web(sys.argv[1])
        for r in results[:5]:
            print(r['snippet'][:100])
    else:
        print("Usage: python3 research_helper.py <query>")
