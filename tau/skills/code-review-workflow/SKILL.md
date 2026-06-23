---
name: code-review-workflow
description: Complete code review pipeline — pyscan, pyanalyze, ruff, black, summary (also load: review, python_best_practices, ast-grep, code-simplifier, git, git-verify, plan_template, project-onboard, python_debugging, bug_investigation, performance, documentation, git-advanced, search-replace, security-audit, file-ops)
category: code-quality
---

# Code Review Workflow

## When
"review code", "check quality", "audit codebase", "code health check"

## Sequence
```bash
pyscan(path=".")                          # Structural inventory
pyanalyze(path=".")                        # Usage analysis, unused code
ruff check --fix <file>                   # Auto-fix linting
ruff check <file>                          # Verify remaining issues
black <file>                                # Format
# Optional: mypy <file>                   # Type check (expensive)
```

## Output
```
=== CODE REVIEW: <file> ===
## Structural Issues: [pyscan findings]
## Usage Issues: [pyanalyze findings]
## Linting: [ruff findings]
## Formatting: [black applied]
## Summary: [issues found, severity, recommendations]
```

## Related Skills
- `review` — detailed manual review process
- `python_best_practices` — linting/formatting sequence
- `ast-grep` — complex search/rewrite
- `git` — commit changes after review
- `context_management` — delegate review to subagent
- `bug_investigation` — systematic bug analysis
- `code-simplifier` — code clarity improvements
- `plan_template` — structured task planning
- `python_debugging` — interactive debugging
