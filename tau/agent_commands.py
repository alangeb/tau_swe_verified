"""Command dispatch for TauBot.

Provides:
- ``CommandManager``: unified resolution and dispatch across all sources

Model types ``CommandSource`` and ``CommandInfo`` are imported from
``agent_command_registry``. Discovery functions live there too.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from agent_command_handlers import _get_cmd_name_to_method, get_command_info
from agent_command_registry import (
    CommandInfo,
    CommandSource,
    get_command,
    get_py_command,
    load_command_content,
    load_py_command,
    parse_multi_prompt,
    strip_frontmatter,
)
from agent_console import (
    command_file_not_found,
    dynamic_command_result,
    error,
    warning,
)
from agent_models import InputMessage

if TYPE_CHECKING:
    from agent_core import TauBot

__all__ = [
    "CommandInfo",
    "CommandManager",
    "CommandSource",
    "MAX_MD_COMMAND_RECURSION",
]


# ── CommandManager ───────────────────────────────────────────────────────────

MAX_MD_COMMAND_RECURSION = 5


class CommandManager:
    """Unified command resolution and dispatch.

    Resolution priority (highest first):
        1. .py commands  — can override builtins
        2. Builtin commands — registered via @_command decorator
        3. .md commands  — markdown templates
    """

    # ── Handler loading ────────────────────────────────────────────────────
    # Builtin handlers are registered by @_command decorators at class-body
    # execution time in agent_command_handlers.  We load lazily on first
    # command resolution (double-check locking) to avoid importing handlers
    # when CommandManager is never used.
    _handlers_loaded: bool = False
    _handlers_lock: threading.Lock = threading.Lock()

    @classmethod
    def _ensure_handlers_loaded(cls) -> None:
        """Ensure builtin command handlers are loaded into the registry."""
        if not cls._handlers_loaded:
            with cls._handlers_lock:
                if not cls._handlers_loaded:
                    import agent_command_handlers  # noqa: F401
                    cls._handlers_loaded = True

    # ── Resolve ────────────────────────────────────────────────────────────

    @staticmethod
    def resolve_all(cmd_name: str) -> list[CommandInfo]:
        """Resolve *cmd_name* to ALL matching sources in priority order.

        Returns an empty list if no source matches.
        """
        CommandManager._ensure_handlers_loaded()
        matches: list[CommandInfo] = []

        # 1. .py commands (highest priority — can override builtins)
        py_cmd = get_py_command(cmd_name)
        if py_cmd is not None:
            matches.append(CommandInfo(
                name=cmd_name,
                source=CommandSource.PY,
                description=py_cmd.get("description", ""),
                file_path=py_cmd.get("file_path", ""),
            ))

        # 2. Builtin commands
        info = get_command_info(cmd_name)
        if info is not None:
            primary, _aliases, description, _subcmds = info
            method_name = _get_cmd_name_to_method().get(cmd_name)
            matches.append(CommandInfo(
                name=cmd_name,
                source=CommandSource.BUILTIN,
                description=description,
                handler_method=method_name,
            ))

        # 3. .md commands (lowest priority)
        md_cmd = get_command(cmd_name)
        if md_cmd is not None:
            matches.append(CommandInfo(
                name=cmd_name,
                source=CommandSource.MD,
                description=md_cmd.get("description", ""),
                file_path=md_cmd.get("file_path", ""),
            ))

        return matches

    # ── Dispatch ───────────────────────────────────────────────────────────

    @staticmethod
    def dispatch(
        cmd_name: str,
        cmd_full: str,
        msg: InputMessage | None,
        agent: TauBot,
    ) -> bool:
        """Resolve and execute a command, trying sources in priority order.

        Falls through to lower-priority sources when a higher one fails.
        Warns when .py succeeds but .md also exists.

        Returns True if a command was found and executed, False otherwise.
        """
        # Pre-dispatch: intercept help requests (help, -h, h)
        args = cmd_full.split()[1:]
        if args and args[0] in ("help", "-h", "h"):
            CommandManager._show_help(cmd_name, cmd_full, agent)
            return True

        infos = CommandManager.resolve_all(cmd_name)
        if not infos:
            return False

        # Pre-check for conflict: .py + .md both exist
        has_py = any(i.is_py for i in infos)
        has_md = any(i.is_md for i in infos)

        for info in infos:
            if info.is_py:
                ok = CommandManager._dispatch_py(info, cmd_full, agent)
                if ok:
                    if has_md:
                        warning(
                            f"Command '{info.name}' exists as both .py and .md — using .py version"
                        )
                    return True
                # .py failed to load → fall through to next source
            elif info.is_builtin:
                if CommandManager._dispatch_builtin(info, cmd_full, msg, agent):
                    return True
            else:  # MD
                if CommandManager._dispatch_md(info, cmd_full, agent):
                    return True

        return False

    # ── Help dispatch ──────────────────────────────────────────────────────

    @staticmethod
    def _show_help(cmd_name: str, cmd_full: str, agent: TauBot) -> None:
        """Show help for a command (builtin or external)."""
        from agent_command_registry import get_py_command_subcommands
        from agent_command_handlers import get_command_info
        from agent_console import show_command_help
        from agent_console_primitives import echo

        # Check builtin commands first
        info = get_command_info(cmd_name)
        if info is not None:
            primary, aliases, desc, subcmds = info
            if subcmds:
                show_command_help(cmd_name)
                return

        # Check external Python commands — prefer help_text over generic subcommand hint
        py_cmd = get_py_command(cmd_name)
        if py_cmd is not None:
            help_text = py_cmd.get("help_text", "")
            if help_text:
                echo(help_text)
                return

        # No help_text — show subcommand hints if available
        py_subcmds = get_py_command_subcommands(cmd_name)
        if py_subcmds:
            echo(f"Usage: /{cmd_name} [{', '.join(py_subcmds)}] [args...]\n")
            return

        # Fallback: show generic help
        echo(f"Usage: /{cmd_name} [args...]\n")
        echo("  Use /help to see all available commands.\n")

    # ── Source-specific dispatchers ────────────────────────────────────────

    @staticmethod
    def _dispatch_builtin(
        info: CommandInfo,
        cmd_full: str,
        msg: InputMessage | None,
        agent: TauBot,
    ) -> bool:
        method_name = info.handler_method
        if method_name is None:
            return False
        handler = getattr(agent, method_name, None)
        if handler is None:
            return False
        handler(cmd_full, msg)
        return True

    @staticmethod
    def _dispatch_py(
        info: CommandInfo,
        cmd_full: str,
        agent: TauBot,
    ) -> bool:
        mod = load_py_command(
            info.name,
            file_path=Path(info.file_path) if info.file_path else None,
        )
        if mod is None:
            return False
        args = cmd_full.split()[1:]
        try:
            mod.run(agent, args)
        except Exception as e:
            error(f"Command '{info.name}' failed: {e}")
        return True

    @staticmethod
    def _dispatch_md(
        info: CommandInfo,
        cmd_full: str,
        agent: TauBot,
    ) -> bool:
        """Execute a .md command with placeholder substitution.

        Guards against unbounded mutual recursion:
            _dispatch_md -> agent._handle_command -> CommandManager.dispatch -> _dispatch_md

        Uses per-agent depth counter (``agent._cmd_dispatch_depth``) capped at
        ``MAX_MD_COMMAND_RECURSION``.
        """
        agent._cmd_dispatch_depth += 1
        try:
            return CommandManager._dispatch_md_inner(info, cmd_full, agent)
        finally:
            agent._cmd_dispatch_depth -= 1

    @staticmethod
    def _dispatch_md_inner(
        info: CommandInfo,
        cmd_full: str,
        agent: TauBot,
    ) -> bool:
        """Inner dispatch — called only when depth < MAX_MD_COMMAND_RECURSION."""
        full_content = load_command_content(info.name)
        if not full_content:
            command_file_not_found(info.name)
            return False

        content = strip_frontmatter(full_content)

        args = cmd_full.split()[1:]
        for seq_content in parse_multi_prompt(content, args):
            if seq_content.strip().startswith("/"):
                if agent._cmd_dispatch_depth >= MAX_MD_COMMAND_RECURSION:
                    warning(
                        f"Command recursion limit ({MAX_MD_COMMAND_RECURSION}) reached — "
                        "stopping dispatch for: " + seq_content.strip()
                    )
                    continue
                sub_name = seq_content.strip()[1:].split()[0]
                agent._handle_command(
                    sub_name,
                    seq_content.strip(),
                    InputMessage(content=seq_content.strip(), source="command_file"),
                )
            else:
                res = agent.invoke_with_tools(seq_content)
                if res:
                    dynamic_command_result(res)

        return True
