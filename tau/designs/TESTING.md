# Testing — Guide

## Manual Testing

```bash
# Quick test
./tau.py "hello"

# Chained inputs
./tau.py "X=1" "/fork what is X"

# With specific LLM group
./tau.py --llm cuda "test prompt"
```

## Unit Tests

```bash
cd src && pytest          # Full suite
cd src && pytest test_agent_context.py  # Specific module
```

42 test files covering: context, LLM pipeline, tools, delegation, A2A, config, console, loop detection, models, file paths, tool validation, compression, edge cases, phantom detection.

## End-to-End Tests

```bash
bash sanity.sh            # Full e2e suite (~100 seconds, requires LLM endpoint)
```

`sanity.sh` is the **gold standard** — tests CLI, positional args, tool calling, fork functionality, continue command.

## Test Suite Skill

The `tau_testsuite` skill documents:
- Fast A2A testing (batch tests against single agent instance)
- Test structure and naming conventions
- Helper functions (`expect_*`, `log_*`, `create_test_file`, `run_tool_capture`)
- Test case organization by group (tc_1.* through tc_10.*)

See `skills/tau_testsuite.md` for full test suite documentation.

## Testing Rules

| Rule | Details |
|------|---------|
| Gold standard | `sanity.sh` — end-to-end tests requiring LLM endpoint (~100 sec) |
| Unit tests | `cd src && pytest` — 42 test files covering core modules |
| Naming | `tc_<major>.<minor>.<idx>_<name>.sh` (e.g., `tc_1.0.1_basic.sh`) |
| Structure | SETUP → EXECUTE → VALIDATE → CLEANUP |
| Helpers | `expect_*()` functions — print PASS/FAIL, return 0/1 — **DO NOT INVERT** |
| Fast tests | `tc_10.*` group — batch A2A tests against single agent instance |
| Prompts | **NEVER modify `sanity.sh` prompts** — deliberately crafted |
