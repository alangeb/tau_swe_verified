#!/bin/bash
# background_session.sh — Common tmux background session management patterns
# Usage: source this file or run individual functions

# Create and run a background task with smart monitoring
bg_run() {
    local name="${1:-task}"
    local cmd="$2"
    local max_s="${3:-300}"
    local idle_s="${4:-30}"
    local keywords="${5:-}"
    session="tmux-agent-${name}"
    background_new(session_name="$session", command="$cmd")
    background_wait(session_name="$session", max_seconds="$max_s", idle_seconds="$idle_s", keywords="$keywords")
}

# Run tests in background
bg_tests() {
    local test_dir="${1:-$HOME/tau/test}"
    bg_run "tests" "cd $test_dir && ./run" 1800 60 "FAILED|PASSED|ERROR|Completed"
}

# Run build in background
bg_build() {
    local build_dir="${1:-.}"
    bg_run "build" "cd $build_dir && make" 600 45 "error|complete|done"
}

# List active sessions with status
bg_status() {
    background_ls
    echo ""
    for session in $(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep tmux-agent-); do
        echo "--- $session ---"
        background_tail(session_name="$session", lines=3)
    done
}

# Cleanup all agent sessions
bg_cleanup() {
    background_kill(session_name="")
    echo "All agent sessions killed"
}
