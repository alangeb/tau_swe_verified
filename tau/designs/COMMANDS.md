# Commands — Implementation Guide

## Three-Tier Dispatch

Priority: **`.py` → builtin → `.md`**. If both `.py` and `.md` exist for the same name, `.py` wins (warning displayed).

## Markdown Commands (`.md`)

Create `commands/my_command.md`:

```markdown
---
description: What this command does
---

Command content with placeholder substitution:
- $1, $2 — positional arguments
- $* — all remaining arguments
- $1+ — from first onward
- ${time}, ${date}, ${datetime} — dynamic placeholders
```

## Python Commands (`.py`)

Create `commands/my_command.py`:

```python
NAME: str = "my_command"
DESCRIPTION: str = "What this command does"
# Optional:
ALIASES_CMD: list[str] = ["alias1"]

def run(agent: "TauBot", args: list[str]) -> None:
    """Execute with full agent access."""
    ...
```

## Key Rules

1. Python commands have **full `TauBot` access** — manage their own context, call tools, spawn subagents.
2. **No return value** — commands manage their own context directly.
3. **Dynamically loaded at runtime** — no caching, fresh each time.
4. **See `command_template` skill** for the full template.

## Command Implementation Rules

| Rule | Details |
|------|---------|
| Python commands | `NAME`, `DESCRIPTION`, `run(agent: TauBot, args: list[str]) -> None` |
| Markdown commands | YAML frontmatter (`description:`), placeholder substitution |
| Placeholders | `$1`, `$2` (positional), `$*` (all), `$1+` (from first), `${time}`, `${date}`, `${datetime}` |
| Dispatch | Three-tier: `.py` → builtin → `.md` (Python wins over markdown) |
| Location | `commands/` directory |
| Loading | Dynamic at runtime — no caching, fresh scan every call |

## Why This Design?

Three-tier dispatch allows upgrading from simple `.md` prompts to full-featured `.py` commands. Python commands get full agent access for complex workflows (loops, conditionals, state management).
