#!/usr/bin/env python3
"""Project onboarding helper — gather project info, generate overview."""
import subprocess, sys, os, json

def gather_info(path="."):
    """Gather basic project info."""
    info = {"path": os.path.abspath(path), "files": 0, "lines": 0}
    
    # Count files and lines
    result = subprocess.run(
        ["find", path, "-name", "*.py", "-type", "f"],
        capture_output=True, text=True
    )
    py_files = result.stdout.strip().split("\n") if result.stdout.strip() else []
    info["py_files"] = len(py_files)
    
    if py_files:
        result = subprocess.run(
            ["wc", "-l"] + py_files,
            capture_output=True, text=True
        )
        # Last line of wc output is total
        lines = result.stdout.strip().split("\n")
        if lines and "total" in lines[-1]:
            info["lines"] = int(lines[-1].split()[0])
    
    # Check for common files
    for f in ["README.md", "CLAUDE.md", "pyproject.toml", "setup.py", "requirements.txt", "Makefile"]:
        info[f] = os.path.exists(os.path.join(path, f))
    
    # Git info
    result = subprocess.run(
        ["git", "-C", path, "rev-parse", "--show-toplevel"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        info["git_root"] = result.stdout.strip()
    
    result = subprocess.run(
        ["git", "-C", path, "branch", "--show-current"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        info["branch"] = result.stdout.strip()
    
    return info

def format_overview(info: dict) -> str:
    """Format project overview as markdown."""
    lines = ["=== PROJECT OVERVIEW ===", ""]
    lines.append(f"## Location: {info['path']}")
    lines.append(f"## Scale: {info.get('py_files', 0)} Python files, {info.get('lines', 0)} lines")
    
    # Git info
    if "branch" in info:
        lines.append(f"## Branch: {info['branch']}")
    if "git_root" in info:
        lines.append(f"## Git root: {info['git_root']}")
    
    # Files present
    files = [k for k, v in info.items() if k in [
        "README.md", "CLAUDE.md", "pyproject.toml", "setup.py",
        "requirements.txt", "Makefile"
    ] and v]
    if files:
        lines.append(f"## Files: {', '.join(files)}")
    
    return "\n".join(lines)

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    info = gather_info(path)
    print(format_overview(info))
