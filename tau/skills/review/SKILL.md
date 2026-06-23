---
name: review
description: Code review process — detailed analysis, inventory, improvement plan (also load: code-review-workflow, python_best_practices, ast-grep, code-simplifier, git, git-verify, project-onboard)
category: code-quality
---

# Code Review

## When
"code review", "review code quality", "assess code", "evaluate code", "deep review"

## Rules
- ALWAYS `file_read` full files every time — even if previously read
- STRICT sequence — execute steps in exact order, never skip/merge/reorder

## Process

### 0. Pre-Analysis (Automated)
- `pyscan(path=".")` — structural inventory, call relationships
- `pyanalyze(path=".")` — unused functions/imports, dead code

### 1. Read Files (Complete First Pass)
- `file_read` ALL files in full — entire content, not partials

### 2. Complete Inventory (Second Pass)
- Catalog EVERY element: functions, classes, methods, variables, constants, types, configs, imports
- Leverage pyscan output from Step 0

### 3. Element Analysis (Third Pass — One at a Time)
- One-line comment per item explaining what it does
- Assess: correctness, clarity, conciseness, documentation, location, usage
- Evaluate: inline? remove?
- Use pyanalyze output for unused code candidates

### 4. Improvement Plan (Final Pass)
- Priority ranking: critical/high/medium/low
- Specific actionable changes with code examples
- Trade-off analysis

## Output Format
```
=== CODE REVIEW: <filename> ===

## Pre-Analysis
### pyscan: [results]
### pyanalyze: [results]

## Inventory
- <type>: <name> — <brief>

## Detailed Analysis
### <name>
- Purpose: <one-line>
- Assessment: <correct/clear/concise/doc'd/location/usage>
- Recommendation: <action>

## Improvement Plan
### Critical
1. <change> — <impact> — <example>

### High
...

## Summary
- Total items: <count>
- Critical: <count>
- High: <count>
- Rating: <score/10>
```

## Notes
- Never modify reviewed files
- Can output to file if requested
- Focus on code quality, not style preferences
- Consider project goals and constraints

## Related Skills
- `code-review-workflow` — complete automated pipeline
- `python_best_practices` — linting/formatting
- `ast-grep` — complex search/rewrite
- `code-simplifier` — code clarity improvements
- `git` — commit reviewed changes
