#!/usr/bin/env python3
"""Dependency management helpers — venv, pip, requirements."""
import subprocess, sys, os

def create_venv(path=".venv"):
    """Create virtual environment. Returns True if successful."""
    result = subprocess.run(
        [sys.executable, "-m", "venv", path],
        capture_output=True, text=True
    )
    return result.returncode == 0

def get_activate_cmd(path=".venv"):
    """Return activation command for venv."""
    return f"source {path}/bin/activate"

def install_packages(packages: list[str], requirements_file: str = None):
    """Install packages or requirements file."""
    cmd = ["pip", "install"]
    if requirements_file:
        cmd.extend(["-r", requirements_file])
    else:
        cmd.extend(packages)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result

def freeze_requirements(output="requirements.txt"):
    """Generate requirements.txt from current environment."""
    result = subprocess.run(
        ["pip", "freeze"], capture_output=True, text=True
    )
    if result.returncode == 0:
        with open(output, "w") as f:
            f.write(result.stdout)
    return result

def check_outdated():
    """Check for outdated packages."""
    result = subprocess.run(
        ["pip", "list", "--outdated"], capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else result.stderr

def check_conflicts():
    """Check for dependency conflicts."""
    result = subprocess.run(
        ["pip", "check"], capture_output=True, text=True
    )
    return result

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: deps_check.py [create|install|freeze|outdated|check]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    if cmd == "create":
        path = sys.argv[2] if len(sys.argv) > 2 else ".venv"
        if create_venv(path):
            print(f"Created venv: {path}")
            print(f"Activate: {get_activate_cmd(path)}")
    elif cmd == "freeze":
        out = sys.argv[2] if len(sys.argv) > 2 else "requirements.txt"
        freeze_requirements(out)
        print(f"Frozen to {out}")
    elif cmd == "outdated":
        print(check_outdated())
    elif cmd == "check":
        result = check_conflicts()
        print(result.stdout if result.returncode == 0 else result.stderr)
    elif cmd == "install":
        pkgs = sys.argv[2:]
        result = install_packages(pkgs)
        print(result.stdout)
