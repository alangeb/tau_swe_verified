"""Tool registry — dynamically imports all tools from the tools directory.

Formal tool interface:
    Every tool module MUST expose:
        - metadata (ToolMetadata): consolidated tool metadata (required)
        - run (callable): execution function
        - Args (dataclass): argument schema
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from agent_plugin_loader import discover_modules, validate_module_has

from agent_console import warning

if TYPE_CHECKING:
    import types

# ── Constants ──────────────────────────────────────────────────────────────

# Global default max output size (bytes). Tools without an explicit max_size
# fall back to this.  Set to None to disable the guard entirely.
DEFAULT_TOOL_MAX_SIZE = 16384  # 16 KB


# ── Tool Metadata ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolMetadata:
    """Consolidated metadata for a single tool.

    Replaces scattered module-level variables (name, description, max_size,
    timeout, aliases_cmd, aliases_arg) with a single typed dataclass instance.

    Example:
        metadata = ToolMetadata(
            name="bash",
            description="Execute shell command...",
            max_size=524288,
            aliases_cmd=["shell"],
            aliases_arg={"command": "cmd"},
        )
    """

    name: str
    description: str
    max_size: int = DEFAULT_TOOL_MAX_SIZE
    timeout: int | None = None
    aliases_cmd: list[str] = dataclasses.field(default_factory=list)
    aliases_arg: dict[str, str] = dataclasses.field(default_factory=dict)


# ── Tool Module Protocol ──────────────────────────────────────────────────

class ToolModule(Protocol):
    """Formal protocol for tool modules.

    Every tool module MUST implement:
        - metadata: ToolMetadata (consolidated tool metadata, required)
        - run: callable (execution function)
        - Args: dataclass (argument schema)
    """

    metadata: ToolMetadata
    run: Any  # callable
    Args: Any  # dataclass


# ── Global registry ──────────────────────────────────────────────────────

CMD_ALIASES: dict[str, str] = {}
ARG_ALIASES: dict[str, dict[str, str]] = {}

# Map non-existent tool names to their closest real equivalents (LLM hallucination handling).
# Internal — merged into CMD_ALIASES at module load time; do not import directly.
_COMMON_ALIASES: dict[str, str] = {
    "test": "bash",       # Most common hallucination — audit showed 4 calls
    "read": "file_read",  "write": "file_write", "cat": "file_read",
    "rm": "bash", "mkdir": "bash", "cp": "bash", "mv": "bash",
}

TOOLS_DIR = Path(__file__).resolve().parent


# ── Tool loading & validation ────────────────────────────────────────────


def _validate_tool_module(mod: Any, module_name: str) -> list[str]:
    """Validate that a module conforms to the ToolModule protocol.

    Uses agent_plugin_loader.validate_module_has for core attribute checks,
    then adds the tool-specific Args dataclass validation inline.

    Returns a list of error messages (empty if valid).
    """
    # Core validation: metadata (ToolMetadata), run (callable)
    errors = validate_module_has(mod, ("metadata", "run"), ("run",))

    # Validate metadata is a ToolMetadata instance
    if hasattr(mod, "metadata") and not isinstance(mod.metadata, ToolMetadata):
        errors.append("'metadata' must be a ToolMetadata instance")

    # Tool-specific: check Args dataclass
    if not hasattr(mod, "Args"):
        errors.append("Missing 'Args' dataclass")
    elif not dataclasses.is_dataclass(mod.Args):
        errors.append("'Args' is not a dataclass")

    return errors


def _load_tool_modules() -> list[Any]:
    """Scan TOOLS_DIR for public .py modules conforming to ToolModule protocol.

    Uses agent_plugin_loader.discover_modules for shared discovery and core validation,
    then applies tool-specific Args dataclass validation.
    """
    modules: list[Any] = []

    # Discover modules using shared agent_plugin_loader infrastructure
    # load_fresh=False → package-relative cached loading (same as old importlib.import_module)
    discovered = discover_modules(
        TOOLS_DIR,
        load_fresh=False,
        package="tools",
        required_attrs=("metadata", "run"),
        callable_attrs=("run",),
        exclude=("validation", "graph"),
    )

    for meta in discovered:
        mod = meta["module"]
        name = meta["name"]

        # Tool-specific validation: Args dataclass
        if not hasattr(mod, "Args"):
            FAILED_TOOLS[name] = "Missing 'Args' dataclass"
            try:
                warning(
                    f"Tool '{name}' validation failed: Missing 'Args' dataclass"
                )
            except Exception:
                pass
            continue

        if not dataclasses.is_dataclass(mod.Args):
            FAILED_TOOLS[name] = "'Args' is not a dataclass"
            try:
                warning(
                    f"Tool '{name}' validation failed: 'Args' is not a dataclass"
                )
            except Exception:
                pass
            continue

        modules.append(mod)

    return modules


# ── ToolEntry ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolEntry:
    """Single source of truth for one tool.

    Wraps a ToolModule and provides centralized access to all tool metadata
    via the ToolMetadata reference, including auto-generated JSON Schema from
    the Args dataclass.
    """

    module: Any
    metadata: ToolMetadata
    _schema: dict = field(default=None, init=False)

    def __post_init__(self):
        """Compute schema eagerly at construction time — dataclasses are immutable."""
        from tools.validation import _dataclass_to_json_schema

        args_cls = getattr(self.module, "Args", None)
        object.__setattr__(
            self, "_schema",
            _dataclass_to_json_schema(args_cls)
            if args_cls is not None
            else {"type": "object", "properties": {}, "required": []},
        )

    # ── Backward-compatible property accessors (delegate to metadata/module)

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description

    @property
    def run(self) -> Any:
        return self.module.run

    @property
    def max_size(self) -> int:
        return self.metadata.max_size

    def to_dict(self) -> dict:
        """Convert to tool dict for LLM function calling."""
        return {
            "name": self.name,
            "description": self.description,
            "run": self.run,
            "args_schema": self._schema,
            "type": "tool",
        }

    def get_schema(self) -> dict:
        """Return the pre-computed JSON Schema for this tool's Args dataclass."""
        return self._schema

    def get_timeout(self) -> int:
        """Get the tool's execution timeout in seconds from metadata."""
        timeout = self.metadata.timeout
        if timeout is not None:
            return timeout
        from agent_tool_executor import DEFAULT_TOOL_TIMEOUT
        return DEFAULT_TOOL_TIMEOUT


# ── Module-level registry (built once at import) ─────────────────────────

TOOLS: dict[str, ToolEntry] = {}

# Track tools that failed to load — exposed for diagnostics via /tools command.
FAILED_TOOLS: dict[str, str] = {}


# Merge _COMMON_ALIASES into CMD_ALIASES so the lookup path resolves them.
for _ca_alias, _ca_canonical in _COMMON_ALIASES.items():
    if _ca_alias not in CMD_ALIASES:
        CMD_ALIASES[_ca_alias] = _ca_canonical

# Build registry + alias indices from loaded tool modules
for _mod in _load_tool_modules():
    _meta = _mod.metadata
    _name = _meta.name
    _aliases_cmd = _meta.aliases_cmd
    _aliases_arg = _meta.aliases_arg

    TOOLS[_name] = ToolEntry(
        module=_mod,
        metadata=_meta,
    )
    canonical = _name
    for alias in _aliases_cmd:
        if alias in CMD_ALIASES and CMD_ALIASES[alias] != canonical:
            warning(
                f"WARNING: Alias conflict — '{alias}' already maps to "
                f"'{CMD_ALIASES[alias]}', now also claimed by '{canonical}'. "
                f"Last-loaded wins."
            )
        CMD_ALIASES[alias] = canonical
    if _aliases_arg:
        ARG_ALIASES[canonical] = _aliases_arg


# ── Public API ───────────────────────────────────────────────────────────


def get_all_tools() -> list[dict]:
    """Return all tools as dicts with name, description, run, args_schema, type."""
    return [e.to_dict() for e in TOOLS.values()]


def get_tool_module(tool_name: str) -> Any | None:
    """Return the tool module by name, or None. Resolves aliases first."""
    entry = TOOLS.get(tool_name)
    if entry:
        return entry.module
    canonical = CMD_ALIASES.get(tool_name)
    if canonical:
        entry = TOOLS.get(canonical)
        if entry:
            return entry.module
    return None


def get_tool_entry(tool_name: str) -> ToolEntry | None:
    """Return the ToolEntry by name, or None. Resolves aliases first."""
    entry = TOOLS.get(tool_name)
    if entry:
        return entry
    canonical = CMD_ALIASES.get(tool_name)
    if canonical:
        return TOOLS.get(canonical)
    return None


def get_tool_schema(tool_name: str) -> dict | None:
    """Return the JSON Schema for a tool by name. Resolves aliases first."""
    entry = get_tool_entry(tool_name)
    if entry:
        return entry.get_schema()
    return None


def get_failed_tools() -> dict[str, str]:
    """Return failed tools for /tools command display."""
    return dict(FAILED_TOOLS)


__all__ = [
    "ToolMetadata",
    "ToolModule",
    "ToolEntry",
    "TOOLS",
    "get_all_tools",
    "get_tool_entry",
    "get_tool_schema",
    "CMD_ALIASES",
    "ARG_ALIASES",
    "get_tool_module",
    "TOOLS_DIR",
    "DEFAULT_TOOL_MAX_SIZE",
    "FAILED_TOOLS",
    "get_failed_tools",
]
