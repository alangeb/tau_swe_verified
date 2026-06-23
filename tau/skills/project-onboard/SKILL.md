---
name: project-onboard
description: Understand a new project — info, pyscan, pyanalyze, plan, initial file reads (also load: code-review-workflow, readme_template, dependency_management, idea)
category: development
---

# Project Onboard

## When
"understand project", "new project", "what is this codebase", "project overview"

## Sequence
```bash
info                                        # Working dir, PID, model, context
pyscan(path=".")                             # Structure: files, classes, functions
pyanalyze(path=".")                          # Usage: unused code, imports
plan(action="create")                        # Initial plan
file_read(path="README.md")                  # Project docs
file_read(path="CLAUDE.md")                  # Coding standards
```

## Output
```
=== PROJECT OVERVIEW ===
## Location: [cwd]
## Scale: [N files, N LOC, N classes, N functions]
## Quality: [unused code, issues]
## Structure: [key modules, dependencies]
## Standards: [coding conventions, tools]
```

## Related Skills
- `code-review-workflow` — deeper analysis
- `review` — detailed review process
- `python_best_practices` — linting/formatting
- `dependency_management` — discover project dependencies
- `readme_template` — document project structure
