---
name: bug_investigation
description: Systematically investigate bugs using automated tools, identify root causes, propose fixes (also load: python_debugging, code-review-workflow, ast-grep, graphify, agent-browser, error-recovery, performance, security-audit, git-advanced, idea)
category: development
---

# Bug Investigation

## When
"investigate bug", "root cause", "debug issue", "find bug", "why does this fail"

## Tool Sequence (ALWAYS first)
1. `pyscan(path=".")` — structural inventory, call relationships
2. `pyanalyze(path=".")` — unused functions/imports, dead code
3. `grep` — pattern search, call sites, execution paths

## Process
1. **Hypothesis**: Formulate specific hypotheses from symptoms + tool output
2. **Verify**: Design tests per hypothesis — grep call sites, trace paths, check state
3. **Root Cause**: Single clear explanation of WHY
4. **Fix**: Type (quick/architectural), Location, Change, Risk
5. **Verify**: Test steps, edge cases, regression tests

## Common Root Causes
- Return value ignored in call chain
- Thread target not detected by AST tools
- State not properly passed between layers
- Assumption violation (expected vs actual)

## Checklist
- [ ] pyscan + pyanalyze + grep run
- [ ] ≥2 hypotheses tested
- [ ] Root cause identified
- [ ] Fix proposal with specific code changes
- [ ] Verification plan defined

## Related Skills
- `python_debugging` — interactive debugging with background
- `code-review-workflow` — automated code analysis
- `ast-grep` — complex pattern search
- `context_management` — delegate investigation
- `plan_template` — structure investigation steps
- `tau_audit` — analyze agent behavior patterns
