---
name: code-simplifier
description: Simplify code for clarity, consistency, maintainability — preserve functionality (also load: code-review-workflow, review, python_best_practices)
category: coding
---

# Code Simplifier

## When
"simplify code", "refactor", "clean up code", "improve readability", "make code clearer"

## Rules
- **Preserve functionality** — never change behavior
- **Apply project standards** — see CLAUDE.md
- **Enhance clarity** — reduce nesting, eliminate redundancy, consolidate logic
- **Balance** — no oversimplification that hurts readability

## Avoid
- Nested ternaries → switch or if/else chains
- Dense one-liners → clarity over brevity
- Overly clever solutions
- Removing helpful abstractions

## Scope
Recently modified code only unless instructed otherwise.

## Process
1. Identify modified sections
2. Analyze for elegance/consistency improvements
3. Apply project standards
4. Verify functionality unchanged
5. Document significant changes only

## Related Skills
- `code-review-workflow` — complete review pipeline
- `review` — detailed code review process
- `caveman` — concise writing style
- `python_best_practices` — linting/formatting
