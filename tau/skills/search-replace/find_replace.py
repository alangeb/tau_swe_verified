#!/usr/bin/env python3
"""Search and replace helper — find patterns, verify replacements."""
import subprocess, sys, os, re

def find_occurrences(pattern: str, path=".", file_pattern="*", exclude=None):
    """Find all occurrences of pattern in files."""
    cmd = ["grep", "-rn", pattern, path]
    if file_pattern and file_pattern != "*":
        cmd.extend(["--include", file_pattern])
    if exclude:
        cmd.extend(["--exclude-dir", exclude])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    matches = []
    for line in result.stdout.strip().split("\n"):
        if line:
            parts = line.split(":")
            if len(parts) >= 3:
                matches.append({
                    "file": parts[0],
                    "line": parts[1],
                    "content": ":".join(parts[2:])
                })
    return matches

def verify_replacement(pattern: str, path=".", file_pattern="*"):
    """Verify no occurrences remain after replacement."""
    matches = find_occurrences(pattern, path, file_pattern)
    return len(matches) == 0, matches

def find_and_count(pattern: str, path=".", file_pattern="*"):
    """Count occurrences of pattern."""
    cmd = ["grep", "-rc", pattern, path]
    if file_pattern and file_pattern != "*":
        cmd.extend(["--include", file_pattern])
    result = subprocess.run(cmd, capture_output=True, text=True)
    counts = {}
    for line in result.stdout.strip().split("\n"):
        if line:
            parts = line.rsplit(":", 1)
            if len(parts) == 2:
                counts[parts[0]] = int(parts[1])
    return counts

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: find_replace.py <pattern> [path] [file_pattern]")
        sys.exit(1)
    
    pattern = sys.argv[1]
    path = sys.argv[2] if len(sys.argv) > 2 else "."
    file_pattern = sys.argv[3] if len(sys.argv) > 3 else "*"
    
    counts = find_and_count(pattern, path, file_pattern)
    total = sum(counts.values())
    print(f"Found {total} occurrences in {len(counts)} files:")
    for f, c in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {f}: {c}")
