#!/bin/bash
# test_runner.sh — Test suite runner helpers
# Usage: source this file or run individual functions

# Run single test
run_test() {
    local test_name="$1"
    local test_dir="${2:-$HOME/tau/test}"
    cd "$test_dir" && ./run "$test_name"
}

# Run test group
run_group() {
    local group="$1"
    local test_dir="${2:-$HOME/tau/test}"
    cd "$test_dir" && ./run "${group}.*"
}

# Run all tests
run_all() {
    local test_dir="${1:-$HOME/tau/test}"
    cd "$test_dir" && ./run
}

# Check test results
check_results() {
    local test_dir="${1:-$HOME/tau/test}"
    find "$test_dir/output" -name "status.json" 2>/dev/null | \
        xargs grep '"status"' 2>/dev/null | sort | uniq -c
}

# Find failed tests
find_failures() {
    local test_dir="${1:-$HOME/tau/test}"
    find "$test_dir/output" -name "status.json" -exec grep -l '"FAIL"' {} \; 2>/dev/null
}
