#!/usr/bin/env python3
"""audit_analyze.py — Quick audit log analysis helpers."""
import glob, re, os
from collections import Counter

def find_logs(pattern="~/.local/tau/log/*_2026*_1.audit"):
    """Find audit log files."""
    return sorted(glob.glob(os.path.expanduser(pattern)))

def tool_usage_summary(logs=None):
    """Count tool invocations across logs."""
    if logs is None:
        logs = find_logs()
    tools = Counter()
    for f in logs:
        with open(f) as fh:
            for line in fh:
                m = re.search(r"final_name='(\w+)'", line)
                if m:
                    tools[m.group(1)] += 1
    return tools.most_common()

def session_stats(log):
    """Quick stats for a single log."""
    stats = {"tools": Counter(), "errors": 0, "forks": 0, "subagents": 0}
    with open(log) as fh:
        for line in fh:
            if "TOOL_CALL" in line:
                m = re.search(r"final_name='(\w+)'", line)
                if m:
                    stats["tools"][m.group(1)] += 1
            if "TOOL_ERROR" in line:
                stats["errors"] += 1
            if "FORK_START" in line:
                stats["forks"] += 1
            if "SUBAGENT_START" in line:
                stats["subagents"] += 1
    return stats

def error_summary(logs=None):
    """Summarize errors across logs."""
    if logs is None:
        logs = find_logs()
    errors = []
    for f in logs:
        with open(f) as fh:
            in_error = False
            for line in fh:
                if "TOOL_ERROR" in line:
                    in_error = True
                    m = re.search(r"error_type=(\S+)", line)
                    if m:
                        errors.append(m.group(1))
                elif in_error and line.strip().startswith("|"):
                    continue
                else:
                    in_error = False
    return Counter(errors).most_common()

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "tools":
        for tool, count in tool_usage_summary():
            print(f"  {tool:30s} {count}")
    elif cmd == "errors":
        for err, count in error_summary():
            print(f"  {err:30s} {count}")
    elif cmd == "stats" and len(sys.argv) > 2:
        print(session_stats(sys.argv[2]))
    else:
        print("Usage: audit_analyze.py [tools|errors|stats <log>]")
