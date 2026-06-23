---
name: search-replace
description: Find and replace patterns across files — grep, file_read, file_edit, verify (also load: ast-grep, file-ops)
category: development
---

# Search Replace

## When
"find and replace", "update pattern", "search and modify", "bulk edit"

## Sequence
```bash
grep -rn "pattern" .                        # Find occurrences
file_read(path="<file>")                   # Read context
file_edit(path="<file>", old="old", new="new")  # Edit
grep -rn "pattern" .                       # Verify
```

## Complex Patterns
For AST-level search/replace, use `ast-grep` instead:
```bash
ast-grep -p '$A && $A()' --rewrite '$A?.()' -U src/
```

## Checklist
- [ ] All occurrences found
- [ ] Context reviewed before editing
- [ ] Edits verified with grep
- [ ] No unintended changes

## Related Skills
- `ast-grep` — complex AST search/rewrite
- `code-review-workflow` — verify changes
- `git-verify` — confirm modifications
- `agent-browser` — web content extraction
- `shell_scripting` — automate search/replace workflows
