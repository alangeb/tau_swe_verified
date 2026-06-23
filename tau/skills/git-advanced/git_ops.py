#!/usr/bin/env python3
"""Git advanced helper - advanced git operations."""

import subprocess
from typing import Optional


def git_log_analysis(path: str = ".", since: str = "1 week ago") -> list[dict]:
    """Analyze git log."""
    result = subprocess.run(
        f"git -C {path} log --oneline --stat --since='{since}' 2>/dev/null",
        shell=True, capture_output=True, text=True
    )
    commits = []
    current_commit = None
    for line in result.stdout.strip().split('\n'):
        if line and not line.startswith(' '):
            current_commit = {"hash": line[:7], "message": line[9:], "files": []}
            commits.append(current_commit)
        elif current_commit and line.startswith(' '):
            current_commit['files'].append(line.strip())
    return commits


def git_bisect_start(path: str = ".") -> str:
    """Start git bisect."""
    result = subprocess.run(
        f"git -C {path} bisect start 2>&1",
        shell=True, capture_output=True, text=True
    )
    return result.stdout + result.stderr


def git_stash_ops(path: str = ".", action: str = "list") -> str:
    """Git stash operations."""
    result = subprocess.run(
        f"git -C {path} stash {action} 2>&1",
        shell=True, capture_output=True, text=True
    )
    return result.stdout + result.stderr


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        commits = git_log_analysis()
        for c in commits[:5]:
            print(f"{c['hash']} {c['message']}")
            for f in c['files'][:3]:
                print(f"  {f}")
    else:
        print("Usage: python3 git_advanced.py")
