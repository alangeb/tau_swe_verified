# TAU.md — TauBot Developer Index

**This file is the developer index into `designs/`.** Read this before working on Tau code. All design documents live in `designs/`.

## Before You Start

1. **ALWAYS run `info`, `pyscan`, and `pyanalyze`** on the target file/directory to understand the current structure.
2. Read relevant design documents in `designs/` for rationale behind existing patterns.
3. Use `skill` tool to search for applicable skills before starting work.

## Quick Reference

| Task | Where to Look |
|------|---------------|
| Architecture & flow | `designs/ARCHITECTURE.md` |
| Design decisions | `designs/DECISIONS.md` |
| Tool implementation | `designs/TOOLS.md` |
| Command implementation | `designs/COMMANDS.md` |
| Skill implementation | `designs/SKILLS.md` |
| Testing | `designs/TESTING.md` |
| Context management | `designs/CONTEXT.md` |

## Making Changes

### Code Style

- `from __future__ import annotations` for forward references
- Dataclasses for all structured data (models, tool args, config)
- `__all__` exports declared in every module
- Type hints for public functions (in progress)
- Local imports to avoid circular dependencies

### Testing Changes

```bash
# Quick manual test
./tau.py "test prompt"
./tau.py "/status"

# Unit tests (requires pytest)
cd src && pytest

# End-to-end tests (requires LLM endpoint, ~100 seconds)
bash sanity.sh
```

## Debugging

### Quick Checks

```bash
./tau.py --debug "test prompt"
./tau.py "/status"
./tau.py "/ctx trace"
```

### Log Files

All session artifacts live in `LOG_DIR` (default: `~/.local/tau/`):

| File | Purpose |
|------|---------|
| `{prefix}.log` | Agent log (stdout/stderr) |
| `{prefix}.audit.log` | Structured audit log |
| `{prefix}.context` | Conversation context (JSON) |
| `tool_output/` | Full tool outputs (disk backup) |
| `logerror/` | Failed test archives |

### In-Agent Debugging

| Command | Purpose |
|---------|---------|
| `/status` | PIDs, context size, tokens, cache hit rates, model info |
| `/ctx summary` | Context size and token estimates |
| `/ctx trace` | Raw context dump (debug) |
| `/ctx tool` | Tool messages only |
| `/ctx assistant` | Assistant messages only |
| `/tools` | List available tools |
| `/exec tool args` | Execute a tool directly |

## Rules

### Git Workflow

| Rule | Details |
|------|---------|
| Atomic | Every task result = git commit (PASS or FAIL) |
| No partials | No partial states, no checkpoints to restore |
| Truth | Git commit = final truth |
| Worktrees | Use `git` skill (worktree_ops.sh) for parallel development |

### Security

| Rule | Details |
|------|---------|
| Environment | Run in sandboxed environment (Docker/VM) |
| Exposure | Do NOT expose to public internet |
| Credentials | Do NOT provide production credentials |
| Access | Restrict filesystem and network access |
| Code | Assume ALL generated code may be unsafe |

### File Naming Conventions

| Pattern | Purpose | Example |
|---------|---------|---------|
| `agent_*.py` | Core agent modules | `agent_core.py`, `agent_llm.py` |
| `tc_*.sh` | Test files | `tc_1.0.1_basic.sh` |
| `TASK_##.md` | Task files | `TASK_01.md` |
| `skills/*/SKILL.md` | Skill files | `skills/tau_testsuite/SKILL.md` |
| `*.md`/`*.py` in `commands/` | Command files | `delegate.py`, `pyprep.md` |

### Agent Behavior (from AGENT.md)

| Rule | Details |
|------|---------|
| Skills | Use `skill` tool on every new user request |
| Delegation | Use `fork` (full memory) or `subagent` (blank slate) |
| Planning | Use `plan` tool for task management |
| Changes | No code changes until user explicitly asks |
| Analysis | Use `pyscan` + `pyanalyze` before modifying Python code |
| Testing | Always test after changes |
| Process | **NEVER kill `tau.py` process** |
| Verification | Run `sanity.sh` to verify changes (~100 sec) |

## Links

- [Root README (../README.md)](../README.md) — User-facing documentation
- [Test Suite (../test/README.md)](../test/README.md) — Test documentation
- [Design Docs (designs/)](designs/) — Architecture, decisions, implementation guides
