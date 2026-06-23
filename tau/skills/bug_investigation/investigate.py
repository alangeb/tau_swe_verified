#!/usr/bin/env python3
"""Bug investigation helper - systematic bug analysis workflow."""

import subprocess
import json
from pathlib import Path
from typing import Optional


def run_cmd(cmd: str, cwd: Optional[str] = None) -> tuple[str, int]:
    """Run command and return (output, returncode)."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    return result.stdout, result.returncode


def pyscan_summary(path: str = ".") -> dict:
    """Run pyscan and return structured summary."""
    out, rc = run_cmd(f"python3 -c \"from pyscan import scan; import json; print(json.dumps(scan('{path}'), indent=2))\"")
    if rc != 0:
        return {"error": out}
    return json.loads(out)


def find_call_sites(pattern: str, path: str = ".") -> list[str]:
    """Find all call sites for a function/pattern."""
    out, _ = run_cmd(f"grep -rn '{pattern}' {path} | grep -v 'def {pattern}'")
    return [line.strip() for line in out.strip().split('\n') if line.strip()]


def check_thread_targets(func_name: str, path: str = ".") -> list[str]:
    """Check if function is used as thread target."""
    out, _ = run_cmd(f"grep -rn 'threading.Thread(target={func_name}' {path}")
    return [line.strip() for line in out.strip().split('\n') if line.strip()]


def check_callbacks(func_name: str, path: str = ".") -> list[str]:
    """Check if function is used as callback."""
    out, _ = run_cmd(f"grep -rn '= {func_name}' {path} | grep -v 'def {func_name}'")
    return [line.strip() for line in out.strip().split('\n') if line.strip()]


def investigate_bug(func_name: str, path: str = ".") -> dict:
    """Run full bug investigation workflow."""
    return {
        "call_sites": find_call_sites(func_name, path),
        "thread_targets": check_thread_targets(func_name, path),
        "callbacks": check_callbacks(func_name, path),
    }


if __name__ == "__main__":
    import sys
    func = sys.argv[1] if len(sys.argv) > 1 else None
    if func:
        result = investigate_bug(func)
        print(json.dumps(result, indent=2))
    else:
        print("Usage: python3 bug_investigation.py <function_name>")
