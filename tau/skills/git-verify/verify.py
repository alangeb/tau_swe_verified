#!/usr/bin/env python3
"""Git verify helper - quick diff verification utilities."""

import subprocess
from typing import Optional


def get_diff_stats(path: str = ".") -> dict:
    """Get git diff statistics."""
    result = subprocess.run(
        f"git -C {path} diff --stat HEAD 2>/dev/null",
        shell=True, capture_output=True, text=True
    )
    if not result.stdout.strip():
        return {"status": "clean", "files": []}
    
    files = []
    for line in result.stdout.strip().split('\n'):
        if line.strip():
            parts = line.rsplit(' ', 1)
            if len(parts) == 2:
                files.append({"file": parts[0].strip(), "changes": parts[1].strip()})
    
    return {"status": "modified", "files": files}


def get_full_diff(path: str = ".") -> str:
    """Get full git diff."""
    result = subprocess.run(
        f"git -C {path} diff HEAD 2>/dev/null",
        shell=True, capture_output=True, text=True
    )
    return result.stdout


def check_debug_code(path: str = ".") -> list[str]:
    """Check for leftover debug code."""
    patterns = ["print(", "breakpoint()", "import pdb", "pdb.set_trace()", "# TODO", "# FIXME"]
    result = subprocess.run(
        f"git -C {path} diff HEAD 2>/dev/null | grep -E {'|'.join(patterns)}",
        shell=True, capture_output=True, text=True
    )
    return [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    stats = get_diff_stats(path)
    print(f"Status: {stats['status']}")
    if stats['files']:
        print(f"Modified files: {len(stats['files'])}")
        for f in stats['files']:
            print(f"  {f['file']}: {f['changes']}")
    
    debug = check_debug_code(path)
    if debug:
        print(f"\nDebug code found: {len(debug)}")
        for d in debug[:10]:
            print(f"  {d[:100]}")
