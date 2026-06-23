---
name: ast-grep
description: AST code search/rewrite. Enhanced grep for complex search or search&replace tasks (also load: code-review-workflow, search-replace, bug_investigation, review)
category: development
---

# ast-grep

## When
"AST search", "code pattern search", "complex search replace", "rewrite code pattern", "structural search"

## Search
```bash
ast-grep -p '$A.context' agent_core.py
ast-grep -p '$FUNC(' tools/
ast-grep -p 'bash' tools/
ast-grep -p 'class TauBot' .
```

## Replace
```bash
ast-grep -p '$A && $A()' --rewrite '$A?.()' -U src/
ast-grep -p '$A || $A()' --rewrite '$A ?? $A' -U src/
```

## Rule Template
```yaml
id: rule-name
message: Description
severity: warning
language: Python
rule:
  pattern: Your pattern
```

## Limitations
- **Thread targets NOT detected**: `threading.Thread(target=fn)` → verify with `grep -rn "target=fn"`
- **Callbacks NOT detected**: `obj.on_event = fn` → verify with `grep -rn "= fn"`
- **Higher-order functions NOT detected**: `map(fn, data)` → verify with `grep -rn "map(\|filter(\|reduce("`

## Mandatory Verification
```bash
# After ast-grep flags something unused:
grep -r "suspected_unused(" . | grep -v "def suspected_unused"
grep -rn "threading.Thread(target=" .
grep -rn "= function_name" . | grep -v "def"
```
Triple-check before deleting: AST says unused → grep confirms → manual review confirms not callback/thread target.

## Related Skills
- `code-review-workflow` — complete review pipeline
- `review` — detailed code review process
- `bug_investigation` — root cause analysis
