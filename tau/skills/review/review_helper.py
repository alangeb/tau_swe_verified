#!/usr/bin/env python3
"""Code review helper - automated review utilities."""

import subprocess
from typing import Optional


def run_review(path: str = ".") -> dict:
    """Run automated code review pipeline."""
    results = {
        "pyscan": run_pyscan(path),
        "pyanalyze": run_pyanalyze(path),
        "ruff": run_ruff(path),
    }
    return results


def run_pyscan(path: str) -> str:
    """Run pyscan on path."""
    result = subprocess.run(
        f"python3 -c \"from pyscan import scan; print(scan('{path}'))\"",
        shell=True, capture_output=True, text=True
    )
    return result.stdout


def run_pyanalyze(path: str) -> str:
    """Run pyanalyze on path."""
    result = subprocess.run(
        f"python3 -c \"from pyanalyze import analyze; print(analyze('{path}'))\"",
        shell=True, capture_output=True, text=True
    )
    return result.stdout


def run_ruff(path: str) -> str:
    """Run ruff check on path."""
    result = subprocess.run(
        f"ruff check {path} 2>&1",
        shell=True, capture_output=True, text=True
    )
    return result.stdout


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    results = run_review(path)
    for tool, output in results.items():
        print(f"=== {tool} ===")
        print(output[:500])
