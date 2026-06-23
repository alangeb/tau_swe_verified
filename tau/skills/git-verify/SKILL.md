---
name: git-verify
description: Verify code changes with git diff, review modifications, confirm correctness (also load: code-review-workflow, python_best_practices, review, search-replace)
category: development
---

# Git Verify

## When
"verify changes", "check diff", "review modifications", "confirm changes", "git diff review"

## Sequence
```bash
git -C . diff --stat HEAD              # Summary
git -C . diff HEAD                     # Full
# Optional: git -C . diff HEAD -- <file>  # Specific file
```

## Checklist
- [ ] Changes match intent
- [ ] No unintended modifications
- [ ] Formatting consistent
- [ ] No leftover debug code

## Related Skills
- `code-review-workflow` — full review pipeline
- `python_best_practices` — linting/formatting
- `review` — detailed code review
