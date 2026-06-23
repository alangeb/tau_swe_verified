"""Unified command registry — discovers, resolves, and loads commands.

Provides a single interface for discovering, resolving, and loading commands
from both Python modules and markdown files.

Architecture:
  CommandRegistry  —  caches discovered commands, resolves by name, loads modules.
  CommandSource    —  enum for command origin (PY, MD, BUILTIN).
  CommandInfo      —  dataclass holding resolved command metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any

from agent_plugin_loader import (
    discover_modules,
    load_module_from_path,
)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class CommandSource(Enum):
    """Origin of a command."""

    PY = auto()
    MD = auto()
    BUILTIN = auto()


@dataclass(frozen=True)
class CommandInfo:
    """Resolved command metadata."""

    name: str
    source: CommandSource
    description: str = ""
    file_path: str = ""
    handler_method: str | None = None
    subcommands: tuple[str, ...] = ()
    help_text: str = ""
    content: str = ""
    full_content: str = ""

    @property
    def is_py(self) -> bool:
        return self.source == CommandSource.PY

    @property
    def is_md(self) -> bool:
        return self.source == CommandSource.MD

    @property
    def is_builtin(self) -> bool:
        return self.source == CommandSource.BUILTIN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_commands_directory(directory: str | None = None) -> Path:
    """Resolve the commands directory path.

    Always returns ``.../commands`` relative to this file's parent,
    regardless of whether the directory actually exists.  Callers
    should check ``.exists()`` themselves.
    """
    if directory is not None:
        return Path(directory)
    return Path(__file__).resolve().parent / "commands"


def strip_frontmatter(content: str) -> str:
    """Remove leading YAML frontmatter (--- ... ---) from content."""
    if not content.startswith("---"):
        return content
    parts = content.split("---", 2)
    if len(parts) >= 3:
        return parts[2].strip()
    return content


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _discover_md_commands(directory_path: Path) -> list[dict]:
    """Discover .md command files in *directory_path*."""
    discovered: list[dict] = []
    if not directory_path.is_dir():
        return discovered

    for file_path in directory_path.glob("*.md"):
        command_name = file_path.name[:-3]
        full_content = file_path.read_text()

        description = ""
        command_content = full_content
        if full_content.startswith("---"):
            parts = full_content.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().split("\n"):
                    if line.strip().startswith("description:"):
                        description = line.split(":", 1)[1].strip()
                        break
                command_content = strip_frontmatter(full_content)

        discovered.append({
            "name": command_name,
            "description": description,
            "file_path": str(file_path),
            "content": command_content,
            "full_content": full_content,
            "type": "command",
        })

    return discovered


def _discover_py_commands(directory_path: Path) -> list[dict]:
    """Discover .py command modules in *directory_path*."""
    if not directory_path.is_dir():
        return []

    raw = discover_modules(
        directory_path,
        load_fresh=True,
        required_attrs=(),
        callable_attrs=(),
    )

    discovered: list[dict] = []
    for meta in raw:
        if not callable(meta.get("run")):
            continue
        mod = meta.get("module")
        discovered.append({
            "name": meta["name"],
            "description": meta["description"],
            "subcommands": getattr(mod, "subcommands", ()) if mod else (),
            "help_text": getattr(mod, "help_text", "") if mod else "",
            "file_path": meta["file_path"],
            "type": "py_command",
        })

    return discovered


# ---------------------------------------------------------------------------
# Placeholder substitution
# ---------------------------------------------------------------------------

def substitute_placeholders(content: str, args: list[str]) -> str:
    """Replace $N, $N+, and $* placeholders with argument values."""
    if not args:
        args = []
    result = content

    # Range: $1+ $2+ … (before positional to avoid $1+ → value+)
    for i in range(1, 10):
        replacement = " ".join(args[i - 1:]) if i <= len(args) else ""
        result = result.replace(f"${i}+", replacement)

    # Positional: $1 $2 …
    for i in range(1, 10):
        replacement = args[i - 1] if i <= len(args) else ""
        result = result.replace(f"${i}", replacement)

    # Collective: $*
    result = result.replace("$*", " ".join(args))
    return result


def substitute_dynamic_placeholders(content: str) -> str:
    """Replace ${time}, ${date}, ${datetime} with current values."""
    from datetime import datetime
    now = datetime.now()
    for placeholder, formatter in [
        ("${time}", lambda n: n.strftime("%H:%M:%S")),
        ("${date}", lambda n: n.strftime("%A, %B %d, %Y")),
        ("${datetime}", lambda n: n.strftime("%Y-%m-%d %H:%M:%S")),
    ]:
        content = content.replace(placeholder, formatter(now))
    return content


def parse_multi_prompt(content: str, args: list[str]) -> list[str]:
    """Split content on --- delimiters and substitute placeholders in each part."""
    if not content.strip():
        return []

    parts = re.split(r'^\s*---\s*$', content, flags=re.MULTILINE)
    parts = [part for part in parts if part.strip()]

    return [
        substitute_placeholders(substitute_dynamic_placeholders(part.strip()), args)
        for part in parts
    ]


# ---------------------------------------------------------------------------
# CommandRegistry
# ---------------------------------------------------------------------------

class CommandRegistry:
    """Unified registry for .py and .md commands.

    Lazily discovers commands on first access. Caches results to avoid
    repeated filesystem scans.

    Caching behavior:
        Commands are discovered once per CommandRegistry instance and
        cached until clear_cache() is called.  For production use where
        commands are added/removed at runtime, call clear_cache() after
        filesystem changes, or create a new CommandRegistry instance.

    Usage:
        registry = CommandRegistry()
        for cmd in registry.discover():
            print(cmd.name, cmd.source)

        info = registry.get("help")
        if info:
            print(info.description)
    """

    def __init__(self, directory: str | None = None):
        self._directory = directory
        self._md_cache: list[dict] | None = None
        self._py_cache: list[dict] | None = None

    @property
    def _directory_path(self) -> Path:
        return _resolve_commands_directory(self._directory)

    def _discover_md(self) -> list[dict]:
        if self._md_cache is None:
            self._md_cache = _discover_md_commands(self._directory_path)
        return self._md_cache

    def _discover_py(self) -> list[dict]:
        if self._py_cache is None:
            self._py_cache = _discover_py_commands(self._directory_path)
        return self._py_cache

    def discover(self, source: CommandSource | None = None) -> list[CommandInfo]:
        """Discover all commands, optionally filtered by source."""
        results: list[CommandInfo] = []

        if source is None or source == CommandSource.MD:
            for raw in self._discover_md():
                results.append(CommandInfo(
                    name=raw["name"],
                    source=CommandSource.MD,
                    description=raw.get("description", ""),
                    file_path=raw.get("file_path", ""),
                    content=raw.get("content", ""),
                    full_content=raw.get("full_content", ""),
                ))

        if source is None or source == CommandSource.PY:
            for raw in self._discover_py():
                results.append(CommandInfo(
                    name=raw["name"],
                    source=CommandSource.PY,
                    description=raw.get("description", ""),
                    file_path=raw.get("file_path", ""),
                    subcommands=raw.get("subcommands", ()),
                    help_text=raw.get("help_text", ""),
                ))

        return results

    def get(self, name: str, source: CommandSource | None = None) -> CommandInfo | None:
        """Find a command by name, optionally filtering by source."""
        for cmd in self.discover(source):
            if cmd.name == name:
                return cmd
        return None

    def get_names(self, source: CommandSource | None = None) -> list[str]:
        """Return sorted list of command names."""
        return sorted(cmd.name for cmd in self.discover(source))

    def find_conflicts(self) -> list[str]:
        """Return names that exist as both .py and .md commands."""
        py_names = set(self.get_names(CommandSource.PY))
        md_names = set(self.get_names(CommandSource.MD))
        return sorted(py_names & md_names)

    def load_py(self, name: str, file_path: Path | None = None) -> Any | None:
        """Load a .py command module by name."""
        if file_path is not None:
            mod = load_module_from_path(file_path, file_path.name[:-3])
            if mod is not None:
                mod_name = getattr(mod, "NAME", None) or getattr(mod, "name", None)
                if mod_name == name:
                    return mod
            return None

        directory_path = self._directory_path
        if not directory_path.is_dir():
            return None

        for file_path in directory_path.glob("*.py"):
            mod = load_module_from_path(file_path, file_path.name[:-3])
            if mod is None:
                continue
            mod_name = getattr(mod, "NAME", None) or getattr(mod, "name", None)
            if mod_name != name:
                continue
            return mod
        return None

    def load_content(self, name: str) -> str | None:
        """Load raw content of a .md command file."""
        file_path = self._directory_path / f"{name}.md"
        if not file_path.exists():
            return None
        return file_path.read_text()

    def get_subcommands(self, name: str) -> tuple[str, ...]:
        """Return subcommands for a .py command by name."""
        py_cache = self._discover_py()
        for raw in py_cache:
            if raw["name"] == name:
                return tuple(raw.get("subcommands", ()))
        return ()

    def clear_cache(self) -> None:
        """Clear discovery caches so the next call re-scans the filesystem.

        Call this after adding/removing command files at runtime, or
        create a new CommandRegistry instance instead.
        """
        self._md_cache = None
        self._py_cache = None


# ---------------------------------------------------------------------------
# Backward compatibility — legacy functions delegate to CommandRegistry
# ---------------------------------------------------------------------------

_default_registry: CommandRegistry | None = None


def _get_registry(directory: str | None = None) -> CommandRegistry:
    """Return a CommandRegistry for *directory*.

    When *directory* is ``None``, returns a cached singleton for the
    default commands directory.  When *directory* is provided, returns
    a new registry (no caching) — the caller owns its lifetime.
    """
    global _default_registry
    if directory is not None:
        return CommandRegistry(directory)
    if _default_registry is None:
        _default_registry = CommandRegistry()
    return _default_registry


# ── Legacy aliases (backward compatible) ──────────────────────────────────

def discover_commands(directory: str | None = None) -> list[dict]:
    """Legacy: discover .md commands. Delegates to CommandRegistry."""
    registry = _get_registry(directory)
    return [
        {
            "name": cmd.name,
            "description": cmd.description,
            "file_path": cmd.file_path,
            "content": cmd.content,
            "full_content": cmd.full_content,
            "type": "command",
        }
        for cmd in registry.discover(CommandSource.MD)
    ]


def get_command(name: str, directory: str | None = None) -> dict | None:
    """Legacy: find a .md command by name."""
    cmd = _get_registry(directory).get(name, CommandSource.MD)
    if cmd is None:
        return None
    return {
        "name": cmd.name,
        "description": cmd.description,
        "file_path": cmd.file_path,
        "content": cmd.content,
        "full_content": cmd.full_content,
        "type": "command",
    }


def load_command_content(name: str, directory: str | None = None) -> str | None:
    """Legacy: load raw .md command content."""
    return _get_registry(directory).load_content(name)


def discover_py_commands(directory: str | None = None) -> list[dict]:
    """Legacy: discover .py commands."""
    registry = _get_registry(directory)
    return [
        {
            "name": cmd.name,
            "description": cmd.description,
            "subcommands": cmd.subcommands,
            "help_text": cmd.help_text,
            "file_path": cmd.file_path,
            "type": "py_command",
        }
        for cmd in registry.discover(CommandSource.PY)
    ]


def get_py_command(name: str, directory: str | None = None) -> dict | None:
    """Legacy: find a .py command by name."""
    cmd = _get_registry(directory).get(name, CommandSource.PY)
    if cmd is None:
        return None
    return {
        "name": cmd.name,
        "description": cmd.description,
        "subcommands": cmd.subcommands,
        "help_text": cmd.help_text,
        "file_path": cmd.file_path,
        "type": "py_command",
    }


def load_py_command(
    name: str, directory: str | None = None, file_path: Path | None = None
) -> Any | None:
    """Legacy: load a .py command module."""
    return _get_registry(directory).load_py(name, file_path)


def get_py_command_names(directory: str | None = None) -> list[str]:
    """Legacy: sorted list of .py command names."""
    return _get_registry(directory).get_names(CommandSource.PY)


def get_py_command_subcommands(name: str, directory: str | None = None) -> tuple[str, ...]:
    """Legacy: subcommands for a .py command."""
    return _get_registry(directory).get_subcommands(name)


def get_md_command_names(directory: str | None = None) -> list[str]:
    """Legacy: sorted list of .md command names."""
    return _get_registry(directory).get_names(CommandSource.MD)


def find_command_conflicts(directory: str | None = None) -> list[str]:
    """Legacy: names that exist as both .py and .md."""
    return _get_registry(directory).find_conflicts()


def prepare_command_prompt(name: str, directory: str | None = None) -> str | None:
    """Legacy: load a command, strip frontmatter, resolve dynamic placeholders."""
    registry = _get_registry(directory)
    raw = registry.load_content(name)
    if not raw:
        return None
    content = strip_frontmatter(raw)
    if not content:
        return None
    return substitute_dynamic_placeholders(content)


__all__ = [
    # New unified API
    "CommandRegistry",
    "CommandSource",
    "CommandInfo",
    # Legacy backward-compatible functions
    "discover_commands",
    "get_command",
    "load_command_content",
    "discover_py_commands",
    "get_py_command",
    "load_py_command",
    "get_py_command_names",
    "get_py_command_subcommands",
    "get_md_command_names",
    "find_command_conflicts",
    "prepare_command_prompt",
    "parse_multi_prompt",
    "substitute_placeholders",
    "substitute_dynamic_placeholders",
    "strip_frontmatter",
]
