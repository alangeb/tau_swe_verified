#!/usr/bin/env python3
"""Python best practices helper - linting and formatting utilities."""

import subprocess
from typing import Optional


def lint_file(file_path: str) -> dict:
    """Run linting on a Python file."""
    results = {
        "ruff": run_ruff(file_path),
        "black": run_black(file_path),
    }
    return results


def run_ruff(file_path: str) -> str:
    """Run ruff check and auto-fix."""
    result = subprocess.run(
        f"ruff check --fix {file_path} 2>&1",
        shell=True, capture_output=True, text=True
    )
    return result.stdout


def run_black(file_path: str) -> str:
    """Run black formatting."""
    result = subprocess.run(
        f"black {file_path} 2>&1",
        shell=True, capture_output=True, text=True
    )
    return result.stdout


def run_mypy(file_path: str) -> str:
    """Run mypy type checking."""
    result = subprocess.run(
        f"mypy {file_path} 2>&1",
        shell=True, capture_output=True, text=True
    )
    return result.stdout


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        results = lint_file(sys.argv[1])
        for tool, output in results.items():
            print(f"=== {tool} ===")
            print(output)
    else:
        print("Usage: python3 lint_helper.py <file.py>")
