#!/usr/bin/env python3
"""Error recovery helper - error pattern detection and recovery utilities."""

import subprocess
from typing import Optional


def detect_errors(audit_file: str) -> dict:
    """Detect error patterns in audit file."""
    result = subprocess.run(
        f"grep -c 'TOOL_ERROR\\|TOOL_BLOCKED' {audit_file} 2>/dev/null",
        shell=True, capture_output=True, text=True
    )
    error_count = int(result.stdout.strip()) if result.stdout.strip() else 0
    
    result = subprocess.run(
        f"grep 'TOOL_ERROR' {audit_file} | grep -oP 'error_type=\\K\\w+' | sort | uniq -c | sort -rn 2>/dev/null",
        shell=True, capture_output=True, text=True
    )
    error_types = result.stdout.strip().split('\n') if result.stdout.strip() else []
    
    return {
        "error_count": error_count,
        "error_types": error_types,
    }


def check_recovery(audit_file: str) -> bool:
    """Check if session recovered from errors."""
    result = subprocess.run(
        f"grep -c 'TOOL_RESULT.*status=success' {audit_file} 2>/dev/null",
        shell=True, capture_output=True, text=True
    )
    success_count = int(result.stdout.strip()) if result.stdout.strip() else 0
    
    result = subprocess.run(
        f"grep -c 'TOOL_ERROR' {audit_file} 2>/dev/null",
        shell=True, capture_output=True, text=True
    )
    error_count = int(result.stdout.strip()) if result.stdout.strip() else 0
    
    return success_count > 0 and error_count > 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        errors = detect_errors(sys.argv[1])
        print(f"Errors: {errors['error_count']}")
        if errors['error_types']:
            print("Error types:")
            for et in errors['error_types']:
                print(f"  {et}")
        recovered = check_recovery(sys.argv[1])
        print(f"Recovered: {recovered}")
    else:
        print("Usage: python3 error_helper.py <audit_file>")
