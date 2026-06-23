---
name: skill_template
description: Create new skill or modify existing skills. Template with skill definitions. (also load: tool_template, command_template, tau_audit, tauskillmaintenance, _taudoc, caveman, readme_template, documentation, reference)
category: development
---

# Skill Format

## When
"create skill", "skill format", "new skill", "skill template", "write skill"

## Required Frontmatter
```yaml
---
name: skill_name
description: One-liner for search
category: category_name
---
```
Missing header = skill fails to load.

## Placement
- `skills/` directory (sibling to `tools/`)
- `.md` extension only
- No leading underscore in filenames

## Rules
- Project-specific knowledge only — not general Python/CLI basics
- One topic per skill
- Concise — skip what the model already knows
- Include code examples

## Discovery
- Auto-discovered on startup. List via `skill`. Search via `skill <keywords>`. Load via `skill {"skill_name": "name"}`.
- Full content injected into context as tool result message when loaded.

## Related Skills
- `tool_template` — creating agent tools (sibling concept)
- `command_template` — creating commands (sibling concept)
- `tauskillmaintenance` — audit and maintain skills
- `caveman` — concise writing style
- `_taudoc` — documentation structure
- `tau_audit` — analyze agent behavior
