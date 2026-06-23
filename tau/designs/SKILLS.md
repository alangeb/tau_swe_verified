# Skills — Implementation Guide

## Skill Contract

Create `skills/my_skill/SKILL.md`:

```markdown
---
name: my_skill
description: What this skill does
category: development
---

Skill content — instructions loaded into forked subagent.
```

Skills are **directories** containing a single `SKILL.md` file. The directory name matches the skill name.

## Key Rules

1. Skills are loaded via the `skill` tool, which spawns a **forked subagent** with the skill content as additional instructions.
2. Skills have **full tool access** — they can use any tool the parent agent can use.
3. **See `skill_template` skill** for the full template.

## Skill Implementation Rules

| Rule | Details |
|------|---------|
| Format | Directory in `skills/` with `SKILL.md` inside |
| Frontmatter | `name:`, `description:`, `category:` (required in `SKILL.md`) |
| Execution | Loaded via `skill` tool → spawns forked subagent with skill content |
| Access | Full tool access (inherits parent agent capabilities) |
| Nature | Pre-built capabilities — not user-defined at runtime |

## Why This Design?

Skills are forked subagents to isolate specialized knowledge. The fork inherits the parent's context, so the skill has full situational awareness while following specialized instructions.
