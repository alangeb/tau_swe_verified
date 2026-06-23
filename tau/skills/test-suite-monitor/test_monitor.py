#!/usr/bin/env python3
"""Test suite monitor helper - test monitoring utilities."""

import subprocess
import time
from typing import Optional


def start_tests(path: str = "$HOME/tau/test") -> str:
    """Start test suite in background."""
    result = subprocess.run(
        f"cd {path} && ./run &",
        shell=True, capture_output=True, text=True
    )
    return result.stdout


def check_test_status(path: str = "$HOME/tau/test") -> dict:
    """Check test suite status."""
    result = subprocess.run(
        f"find {path}/output -name 'status.json' -exec grep '\"status\"' {{}} \\; 2>/dev/null | sort | uniq -c",
        shell=True, capture_output=True, text=True
    )
    return {"status": result.stdout}


def monitor_tests(session_name: str, interval: int = 120, timeout: int = 3600) -> str:
    """Monitor background test session."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        result = subprocess.run(
            f"tmux capture-pane -t {session_name} 2>/dev/null",
            shell=True, capture_output=True, text=True
        )
        if "can't find pane" in result.stderr:
            return "SESSION_ENDED"
        if "Test Suite Completed!" in result.stdout:
            return "COMPLETED"
        time.sleep(interval)
    return "TIMEOUT"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        status = check_test_status()
        print(status['status'])
    else:
        print("Usage: python3 test_monitor.py")
