---
name: tau_testsuite
description: Tool test suite guide - fast A2A tests, structured testcases, helpers, consistency (also load: test-suite-monitor, dependency_management)
category: development
---

# Test Suite

## When
"create test", "test suite", "write test case", "test structure", "test helpers"

## Quick Start
```bash
cd $HOME/tau/test && ./run           # All tests
cd $HOME/tau/test && ./run tc_1.0.1  # Specific test
cd $HOME/tau/test && ./run tc_1.*    # Group 1
```

## Test Groups
| Prefix | Group | Purpose |
|--------|-------|---------|
| `tc_1.X.X` | 1 | Basic commands |
| `tc_2.X.X` | 2 | File operations |
| `tc_3.X.X` | 3 | Python code gen |
| `tc_4.X.X` | 4 | Context management |
| `tc_5.X.X` | 5 | Project-level testing |
| `tc_6.X.X` | 6 | Edge cases |
| `tc_7.X.X` | 7 | External web queries |
| `tc_8.X.X` | 8 | A2A protocol |
| `tc_9.X.X` | 9 | Tmux background |
| `tc_10.X.X` | 10 | Fast A2A suites |

## Helpers (sourced via `func`)
| Function | Purpose |
|----------|---------|
| `expect_equal exp act msg name` | Exact match |
| `expect_contains needle haystack msg name` | Substring |
| `expect_not_contains needle haystack msg name` | Not found |
| `expect_file_exists file msg name` | File exists |
| `expect_not_file_exists file msg name` | File deleted |
| `expect_file_contains file needle msg name` | File content |
| `expect_numeric exp act op msg name` | Numeric (eq/gt/lt) |
| `expect_numeric_range label haystack min msg name` | Range |

## Critical Rules

### DO NOT Invert Helper Results
`expect_*` functions handle PASS/FAIL internally. Trust their return value.
```bash
# CORRECT
if expect_file_exists "output.txt" "File created" "$TEST_NAME"; then
    TEST_RESULT="PASS"
else
    TEST_RESULT="FAIL"
fi

# WRONG — inverts logic
if ! expect_file_exists "output.txt" ...
```

### Prefer Side Effects Over Output Parsing
- ✓ File created → `expect_file_exists`
- ✓ File modified → `expect_file_contains`
- ✗ Testing for your own input verbatim (circular)

### Display Input on Failure
Store full result, display on assertion failure for debugging.

### Single Responsibility
One thing per test. Name: `tc_<major>.<minor>.<idx>_<name>.sh`

## Test Template
```bash
setup_test "$BASH_SOURCE"
cp "$AGENT_PATH" "$DUT_PATH"
TEST_NAME="tc_X.Y.Z_name"
result=$(run_tool_capture "$output_file" "$TEST_TIMEOUT" "instruction")
expect_file_exists "output.txt" "File created" "$TEST_NAME"
TEST_RESULT="PASS"
cleanup_test "$BASH_SOURCE"
```

## A2A Fast Tests
```bash
python "$DUT_PATH" --keep-alive > "$output_file" 2>&1 &
AGENT_PID=$!
timeout 10 sh -c 'until [ -S "/tmp/taua2a-${AGENT_PID}.sock" ]; do sleep 0.1; done'
result=$(python "$DUT_PATH" --pid "$AGENT_PID" "query" 2>&1)
cleanup_a2a_agent "$AGENT_PID"
```

## Background Monitoring
- `sleep 120` minimum between polls
- Watch for session termination or completion messages
- Check `status.json` files for results

## Checklist
- [ ] Required header (`@group`, `@name`, `@tags`, `@timeout`, `@description`)
- [ ] Uses `setup_test` → `run_tool_capture` → assertions → `cleanup_test`
- [ ] Prefers side effects over output parsing
- [ ] Does NOT invert `expect_*` logic
- [ ] Single responsibility
- [ ] Output dir has all expected files
- [ ] `status.json` correct structure
- [ ] Runs locally without manual setup

## Related Skills
- `test-suite-monitor` — background test monitoring workflow
- `background` — tmux session management
