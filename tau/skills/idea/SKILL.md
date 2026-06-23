---
name: idea
description: Capture ideas for features, fixes, improvements. Store in subconscious/ideas/ as .md files. (also load: plan_template, project-onboard)
category: development
---

# Idea Capture

## When
New feature, bug fix, refactoring, process improvement, novel concept.

## Location
`subconscious/ideas/` — pre-existing. DO NOT create.

## Process
1. **Clarify** — WHAT, not HOW. No code, no design.
2. **Capture** — one .md per idea (append if related).
3. **Return** — file path.

## Template
```markdown
---
id: "unique-id"
title: "Concise title"
status: "new"
created: "YYYY-MM-DD"
---

# Idea: [Title]

## What
[Problem or opportunity]

## Target
[Component/file/system affected]

## Change
[Specific scope]

## Success Criteria
[Definition of done]

## Testing
[Verification steps]
```

## Rules
- CAPTURE ONLY — no implementation, no design
- One idea per file (append if related)
- Clarify before writing
- Consistent naming & dates

## Related Skills
- `plan_template` — turn ideas into structured plans
- `project-onboard` — understand project context before ideating
- `bug_investigation` — capture bug findings as ideas
