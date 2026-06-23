---
name: file-ops
description: File operation patterns — read, edit, write, glob, ls workflows. Batch operations, verification, safe editing. (also load: search-replace, shell_scripting, code-review-workflow)
category: development
---

# File Operations

## When
"read file", "edit file", "write file", "find files", "list directory", "file workflow", "batch edit"

## Common Patterns

### Read → Edit → Verify
```bash
file_read(path="file.py")           # Read full context
file_edit(path="file.py", old="x", new="y")  # Targeted edit
grep -n "y" file.py                 # Verify change
```

### Batch Operations
```bash
glob(pattern="*.py")               # Find files
for f in *.py; do grep "TODO" "$f"; done  # Search all
ls -la *.py                         # List with details
```

### Safe File Writing
```bash
file_write(path="new.py", content="...")   # Create/overwrite
file_read(path="new.py")                   # Verify content
```

## Rules
- **Always read before edit** — get full context
- **Verify after edit** — grep for changes
- **Use glob for batch** — find all matching files
- **ls for exploration** — directory structure

## Related Skills
- `search-replace` — find and replace patterns
- `shell_scripting` — batch file operations
- `code-review-workflow` — verify changes
