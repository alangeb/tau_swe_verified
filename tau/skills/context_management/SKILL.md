---
name: context_management
description: Manage agent context — fork for full memory, subagent for blank slate, context capacity optimization (also load: background, plan_template, error-recovery)
category: development
---

# Context Management

## When
"delegate task", "fork subagent", "background work", "context full", "spawn agent"

## Fork vs Subagent
| Mode | Memory | Use When |
|------|---------|----------|
| `fork` | Full conversation history | Task needs context, knowledge inheritance |
| `subagent` | Blank slate, task-only | Isolation needed, self-contained task |

## Patterns

### Fork — Inherit Knowledge
```python
fork(task="Analyze X with all current context")
```
- Synchronous — blocks until done
- Inherits ALL conversation history
- Use when task depends on prior decisions

### Subagent — Isolated
```python
subagent(task="Do X independently, here is everything you need: ...")
```
- Synchronous — blocks until done
- Blank slate — knows ONLY what task says
- Use for isolated, well-defined tasks

### Background — Asynchronous
```python
background_new(command="tau.py 'task'")
background_wait(session_name="...", max_seconds=600, idle_seconds=30)
```
- Runs in parallel with other work
- Use for long-running, independent tasks

## Context Capacity Rules
- Fork = expensive (full context clone)
- Subagent = cheap (minimal context)
- Background = cheapest (separate process)
- Prefer subagent over fork when possible
- Prefer background over both for fire-and-forget

## Related Skills
- `background` — async task execution
- `plan_template` — break tasks into delegatable units
- `bug_investigation` — delegate investigation to subagent
- `code-review-workflow` — delegate review tasks
- `tau_audit` — analyze agent behavior patterns
