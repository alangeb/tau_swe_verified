# Tools — Implementation Guide

## Tool Contract

Every tool is a Python module in `tools/` with:

```python
from tools import ToolMetadata
from dataclasses import dataclass

metadata = ToolMetadata(
    name="tool_name",
    description="What it does",
    # Optional:
    aliases_cmd=["alias1"],
    aliases_arg={"old": "new"},
    max_size=10000,  # Output truncation threshold
    timeout=180,     # Execution timeout (seconds)
)

@dataclass
class Args:
    """Tool arguments — auto-converted to JSON Schema."""
    param: str

def run(param: str, agent: "TauBot", tool_call_id: str | None) -> str:
    """Execute tool. Return string result."""
    ...
```

## Key Rules

1. **Auto-discovered** via `tools/__init__.py` — no manual registration needed.
2. **`agent` and `tool_call_id` are MANDATORY** for all tools.
3. **Use `tools/validation.py`** for `_dataclass_to_json_schema()` to generate JSON Schema.
4. **Use `tools/lib/sandbox.py`** for path validation (`check_path`, `validate_path`) — enforces working directory boundaries.
5. **No `main()` functions** — tools are not standalone scripts.
6. **See `tool_template` skill** for the full template and examples.

## Common Patterns

```python
def run(param: str, agent: "TauBot", tool_call_id: str | None) -> str:
    # Use sandbox validation for file paths
    from tools.lib.sandbox import check_path
    resolved, err = check_path("my_tool", agent, filepath)
    if err:
        return err
    # Do work...
    return result
```

**Key rules**: `agent` and `tool_call_id` are MANDATORY. Use `tools/lib/sandbox.py` for path validation. No `main()` functions.

## Tool Implementation Rules

| Rule | Details |
|------|---------|
| Location | Python modules in `tools/` directory |
| Mandatory | `metadata: ToolMetadata` at module level (replaces old `name`/`description` variables) |
| Args | `Args` dataclass with complete type definitions |
| Signature | `run(**kwargs) -> str` matching Args model |
| Optional | `aliases_cmd`, `aliases_arg`, `max_size`, `timeout` in `ToolMetadata` |
| Discovery | Auto-discovered via `tools/__init__.py` |
| Errors | Return error strings — never escape exceptions |
| Sandbox | Use `tools/lib/sandbox.py` for all file paths |

## Why This Design?

Tools are dynamically discovered to avoid registration overhead. The `Args` dataclass pattern gives us auto-generated JSON Schema for LLM function calling. Sandbox validation prevents the agent from escaping the working directory.
