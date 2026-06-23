---
name: tool_template
description: Template for creating agent tools. Python modules in tools/. Mandatory: name, description, Pydantic Args, run(). No main(). agent + tool_call_id MANDATORY. (also load: skill_template, command_template)
category: development
---

# Tool Template

## When
"create tool", "tool format", "new tool", "tool template", "write tool"

## Mandatory
- **No module docstring** — `name` and `description` serve as docs
- **Pydantic required** — `BaseModel` for Args
- **Complete Args** — all fields with precise types + descriptions
- **No `main()`** — tools are library modules, not standalone scripts
- **`agent` + `tool_call_id`** — MANDATORY for ALL tools, no defaults

## Structure
```python
from __future__ import annotations

name = "tool_name"
description = """Tool description in markdown."""
timeout = 180                      # Optional, default 180s
aliases_cmd = ["alt_name"]         # Optional
aliases_arg = {"p": "path"}        # Optional

from pydantic import BaseModel, Field

class Args(BaseModel):
    arg1: str = Field(description="Description")
    arg2: int = Field(description="Description", ge=0)

def run(arg1: str, arg2: int, agent: 'TauBot', tool_call_id: str | None) -> str:
    return "result"
```

## Metadata
| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `name` | `str` | — | **Mandatory.** Canonical tool name |
| `description` | `str` | — | **Mandatory.** Markdown for LLM |
| `timeout` | `int` | `180` | Seconds. Long-running (fork, subagent) = `86400` |
| `aliases_cmd` | `list[str]` | `[]` | Alternative names, resolved silently |
| `aliases_arg` | `dict[str, str]` | `{}` | Alt param names → canonical, resolved silently |

## Key Distinctions
- `exec wait=True`: sends + waits 0.5s, returns output
- `exec wait=False`: sends + returns immediately
- `send_keys`: text WITHOUT C-m, for interactive input
- `capture scrollback=N`: N lines history from pane buffer
- `tail lines=N`: last N lines from captured output

## Common Pitfalls
| Pitfall | Fix |
|---------|-----|
| Passing `wait` to `ls` as `all_sessions` | Separate params in `run()` |
| `scrollback=0` expects history | Use `≥30` |
| Confusing `send_keys` with `exec` | `send_keys` = input, `exec` = execute |

## Related Skills
- `skill_template` — creating skills (sibling concept)
- `command_template` — creating commands (sibling concept)
