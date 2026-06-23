#!/usr/bin/env python3
"""Code review pipeline — pyscan, pyanalyze, ruff, black sequence."""
import subprocess, sys, os, json

def run_review(path=".", tools=None):
    """Run full review pipeline on path. Returns dict of results."""
    if tools is None:
        tools = ["pyscan", "pyanalyze", "ruff", "black"]
    
    results = {}
    for tool in tools:
        try:
            if tool == "pyscan":
                # Use pyscan tool (not CLI)
                results["pyscan"] = {"status": "use pyscan tool", "path": path}
            elif tool == "pyanalyze":
                results["pyanalyze"] = {"status": "use pyanalyze tool", "path": path}
            elif tool == "ruff":
                result = subprocess.run(
                    ["ruff", "check", "--fix", path],
                    capture_output=True, text=True, timeout=60
                )
                results["ruff"] = {
                    "status": "success" if result.returncode == 0 else "issues",
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode
                }
            elif tool == "black":
                result = subprocess.run(
                    ["black", "--check", "--diff", path],
                    capture_output=True, text=True, timeout=60
                )
                results["black"] = {
                    "status": "clean" if result.returncode == 0 else "needs-format",
                    "stdout": result.stdout,
                    "returncode": result.returncode
                }
        except FileNotFoundError:
            results[tool] = {"status": "not-installed"}
        except subprocess.TimeoutExpired:
            results[tool] = {"status": "timeout"}
    
    return results

def format_report(results: dict, file: str = ".") -> str:
    """Format review results as markdown report."""
    lines = [f"=== CODE REVIEW: {file} ===", ""]
    
    for tool, result in results.items():
        lines.append(f"## {tool.upper()}")
        if result.get("status") == "use pyscan tool":
            lines.append(f"Path: {result['path']} — use pyscan(path='{result['path']}')")
        elif result.get("status") == "not-installed":
            lines.append(f"NOT INSTALLED — pip install {tool}")
        else:
            lines.append(f"Status: {result['status']}")
            if "stdout" in result and result["stdout"]:
                lines.append(f"Output: {result['stdout'][:500]}")
        lines.append("")
    
    return "\n".join(lines)

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    results = run_review(path)
    print(format_report(results, path))
