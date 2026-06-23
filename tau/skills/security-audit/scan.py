#!/usr/bin/env python3
"""Security audit helper - quick security scanning utilities."""

import subprocess
import re
from typing import Optional


def scan_secrets(path: str = ".") -> list[dict]:
    """Scan for potential secrets in code."""
    patterns = {
        "password": r'(?i)password\s*=\s*["\'][^"\']+["\']',
        "api_key": r'(?i)api_key\s*=\s*["\'][^"\']+["\']',
        "secret": r'(?i)secret\s*=\s*["\'][^"\']+["\']',
        "token": r'(?i)token\s*=\s*["\'][^"\']+["\']',
        "aws_key": r'AKIA[0-9A-Z]{16}',
        "github_token": r'ghp_[a-zA-Z0-9]{36}',
    }
    
    findings = []
    for pattern_name, pattern in patterns.items():
        result = subprocess.run(
            f"grep -rn '{pattern}' {path} --include='*.py' --include='*.md' --include='*.json' --include='*.yaml' --include='*.yml' 2>/dev/null",
            shell=True, capture_output=True, text=True
        )
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                findings.append({"type": pattern_name, "location": line})
    
    return findings


def check_sensitive_files(path: str = ".") -> list[str]:
    """Find sensitive files."""
    patterns = ["*.pem", "*.key", "*.crt", ".env", "*.env.*", "*.pgpass", ".netrc"]
    files = []
    for pattern in patterns:
        result = subprocess.run(
            f"find {path} -name '{pattern}' 2>/dev/null",
            shell=True, capture_output=True, text=True
        )
        files.extend(result.stdout.strip().split('\n'))
    return [f for f in files if f.strip()]


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    print("=== Security Scan ===")
    secrets = scan_secrets(path)
    if secrets:
        print(f"\nSecrets found: {len(secrets)}")
        for s in secrets[:10]:
            print(f"  {s['type']}: {s['location'][:100]}")
    else:
        print("\nNo secrets found")
    
    sensitive = check_sensitive_files(path)
    if sensitive:
        print(f"\nSensitive files: {len(sensitive)}")
        for f in sensitive:
            print(f"  {f}")
