#!/bin/bash
# common_patterns.sh — Shell scripting pattern reference
# Source for quick access to common patterns

# Text processing shortcuts
text_freq() { sort | uniq -c | sort -rn; }
text_unique() { sort -u; }
text_count() { grep -c "$1" "$2"; }

# File operations
find_py() { find . -name "*.py" -type f "$@"; }
find_large() { find . -name "*.py" -size +100k; }
find_recent() { find . -name "*.py" -mtime -1; }

# Process management
bg_run() {
    "$@" &
    echo "PID: $!"
}

pid_alive() {
    ps -p "$1" > /dev/null 2>&1
}

# Quick stats
file_stats() {
    echo "Lines: $(wc -l < "$1")"
    echo "Words: $(wc -w < "$1")"
    echo "Chars: $(wc -c < "$1")"
}
