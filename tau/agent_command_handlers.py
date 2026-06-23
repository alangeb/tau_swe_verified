"""Command handlers and registry for TauBot.

Contains:
- @_command decorator and registry (single source of truth for built-in commands)
- CommandHandlersMixin with all /command handler methods

The registry is populated by @_command decorators at class-body execution time.
Query functions (get_command_info, get_builtin_cmd_names, etc.) provide access
to the registry data.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from agent_console import (
    assistant_message_display,
    compress_fail, compress_success,
    context_cleared_success, context_dump_with_json,
    fork_display, fork_error, fork_usage,
    show_agent_card, show_commands, show_help, show_tools, show_tools_json,
    subagent_error, subagent_output_footer, subagent_output_header,
    subagent_start_display, subagent_usage,
    warning,
    agent_status,
)
from agent_console_primitives import blank_line, echo, status
from agent_lifecycle import AgentLifecycle
from agent_models import InputMessage
from agent_subagent import invoke_fork_sync, invoke_subagent_sync

__all__ = [
    # Registry decorator
    "_command",
    # Registry queries
    "BUILTIN_CMD_NAMES",
    "get_builtin_cmd_names",
    "get_command_info",
    "get_all_command_info",
    "get_primary_command_info",
    "get_subcommands",
    # Internal registry access
    "_get_cmd_name_to_method",
    # Mixin
    "CommandHandlersMixin",
]

# ── Registry ──────────────────────────────────────────────────────────────────
# Maps handler method name → (primary_name, aliases_tuple, description, subcommands_tuple).
# Populated by @_command decorators at class-body execution time.
_COMMAND_REGISTRY: dict[str, tuple[str, tuple[str, ...], str, tuple[str, ...]]] = {}


def _command(*names: str, desc: str = "", subcommands: tuple[str, ...] = ()) -> Any:
    """Decorator that registers a method as a command handler.

    The first name in *names is the primary command name. Remaining names are
    aliases. The description is taken from the explicit ``desc`` parameter.
    If ``desc`` is empty, the first non-empty line of the method's docstring
    is used as a fallback.

    ``subcommands`` declares known subcommand names (e.g., ("full", "summary", "trace")).
    When set, ``/cmd help`` and ``/cmd -h`` will show usage for this command.

    The registry entry is stored in _COMMAND_REGISTRY as:
        method_name → (primary_name, aliases_tuple, description, subcommands_tuple)

    This is the single source of truth for built-in command names AND descriptions.
    """
    if not names:
        raise ValueError("@_command requires at least one name")

    def decorator(func):
        description = desc
        if not description and func.__doc__:
            # Fallback: extract first non-empty line of docstring as description
            for line in func.__doc__.strip().splitlines():
                stripped = line.strip()
                if stripped:
                    description = stripped
                    break
        primary = names[0]
        aliases = names[1:] if len(names) > 1 else ()
        _COMMAND_REGISTRY[func.__name__] = (primary, aliases, description, subcommands)
        return func
    return decorator


# ── Reverse lookup (command name → method name) ──────────────────────────────
# Computed lazily at first access.  Static because the registry is populated
# once at module import time and never modified at runtime.
_CMD_NAME_TO_METHOD: dict[str, str] = {}


def _get_cmd_name_to_method() -> dict[str, str]:
    """Return the reverse-lookup dict: command name → handler method name."""
    if not _CMD_NAME_TO_METHOD:
        for method_name, (primary, aliases, _, _) in _COMMAND_REGISTRY.items():
            _CMD_NAME_TO_METHOD[primary] = method_name
            for alias in aliases:
                _CMD_NAME_TO_METHOD[alias] = method_name
    return _CMD_NAME_TO_METHOD


# ── BUILTIN_CMD_NAMES derivation ─────────────────────────────────────────────
# Returns a frozenset of all registered command names.  Used by console modules
# to detect user commands that shadow built-in slash commands.


def get_builtin_cmd_names() -> frozenset[str]:
    """Return all registered command names as a frozenset."""
    return frozenset(_get_cmd_name_to_method().keys())


# Module-level frozenset — computed lazily on first access via __getattr__.
# Exported here (command registry) rather than in agent_models.py to avoid
# a cross-layer dependency: data models should not depend on command registry.
BUILTIN_CMD_NAMES: frozenset[str]  # type: ignore[valid-type]


def __getattr__(name: str) -> frozenset[str]:
    """Lazy-init BUILTIN_CMD_NAMES on first access (self-installs into globals)."""
    if name == "BUILTIN_CMD_NAMES":
        val = get_builtin_cmd_names()
        globals()[name] = val  # Self-install — __getattr__ fires exactly ONCE
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── Command info lookup ──────────────────────────────────────────────────────
# Returns command metadata (primary, aliases, description, subcommands) for a given command name.


def get_command_info(cmd_name: str) -> tuple[str, tuple[str, ...], str, tuple[str, ...]] | None:
    """Return (primary, aliases, description, subcommands) for a command name, or None if not found."""
    method_name = _get_cmd_name_to_method().get(cmd_name)
    if method_name is None:
        return None
    entry = _COMMAND_REGISTRY.get(method_name)
    if entry is None:
        return None
    return entry


# ── Cached all-command info ──────────────────────────────────────────────────
# Lazy-init cache matching the pattern of _CMD_NAME_TO_METHOD.
_ALL_CMD_INFO: dict[str, tuple[str, tuple[str, ...], str, tuple[str, ...]]] | None = None


def get_all_command_info() -> dict[str, tuple[str, tuple[str, ...], str, tuple[str, ...]]]:
    """Return all command info: command_name → (primary, aliases, description, subcommands).

    Cached on first call — the registry is static after import time.
    Keys include both primary names and aliases (for lookup by any name).
    """
    global _ALL_CMD_INFO
    if _ALL_CMD_INFO is None:
        _ALL_CMD_INFO = {}
        for method_name, (primary, aliases, desc, subcmds) in _COMMAND_REGISTRY.items():
            _ALL_CMD_INFO[primary] = (primary, aliases, desc, subcmds)
            for alias in aliases:
                _ALL_CMD_INFO[alias] = (primary, aliases, desc, subcmds)
    return _ALL_CMD_INFO


# ── Primary-only iteration cache ─────────────────────────────────────────────
# Returns only primary_name → (primary, aliases, description, subcommands).
# Use this for iteration (show_help, show_commands) — no alias-skipping needed.
_PRIMARY_CMD_INFO: dict[str, tuple[str, tuple[str, ...], str, tuple[str, ...]]] | None = None


def get_primary_command_info() -> dict[str, tuple[str, tuple[str, ...], str, tuple[str, ...]]]:
    """Return command info keyed by primary name only.

    Cached on first call — the registry is static after import time.
    Use this for iteration over commands (no alias-deduplication needed).
    """
    global _PRIMARY_CMD_INFO
    if _PRIMARY_CMD_INFO is None:
        _PRIMARY_CMD_INFO = {}
        for method_name, (primary, aliases, desc, subcmds) in _COMMAND_REGISTRY.items():
            _PRIMARY_CMD_INFO[primary] = (primary, aliases, desc, subcmds)
    return _PRIMARY_CMD_INFO


# ── Subcommand lookup ────────────────────────────────────────────────────────
def get_subcommands(cmd_name: str) -> tuple[str, ...]:
    """Return subcommands tuple for a command name, or empty tuple if none."""
    info = get_command_info(cmd_name)
    if info is None:
        return ()
    return info[3]


# ── Command Handlers ────────────────────────────────────────────────────────

class CommandHandlersMixin:
    """Mixin providing all /command handlers for TauBot.

    Contains all _cmd_* methods that handle slash commands (/help, /tools, /exit,
    /heartbeat, /llm, /exec, /subagent, /fork, /ctx, /tool, /assistant,
    /agent_card, /restart, /clear, /continue, /name, /compress, /audit).
    """

    @_command("help", "h", subcommands=())
    def _cmd_help(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Show this help message."""
        show_help()

    @_command("tools", subcommands=())
    def _cmd_tools(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """List available tools with descriptions."""
        show_tools()

    @_command("toolsjson", subcommands=())
    def _cmd_tools_json(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Display all tools as JSON schema."""
        show_tools_json()

    @_command("status", subcommands=())
    def _cmd_status(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Show agent status: tokens, context, model, cache."""
        agent_status(self.get_status())

    @_command("commands", subcommands=())
    def _cmd_commands(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """List all available commands."""
        show_commands()

    @_command("exit", "quit", subcommands=())
    def _cmd_exit(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /exit command to gracefully terminate the agent.

        Sets the global _exit_requested flag to signal the main loop to terminate.
        The exit summary is printed by InputHandler._exit() to avoid duplication.

        Args:
            cmd_full: Full command string (unused, kept for interface consistency).
            msg: Optional InputMessage object (unused, kept for interface consistency).
        """
        del msg  # Unused parameter
        AgentLifecycle.set_exit_requested(True)

    @_command("heartbeat", subcommands=("on", "off"))
    def _cmd_heartbeat(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /heartbeat command to toggle or configure heartbeat.

        Manages the heartbeat feature which periodically checks if the agent
        is idle and can trigger actions. Supports multiple modes:
        - No arguments: Display current heartbeat status
        - "on": Enable heartbeat with current or default interval
        - "off": Disable heartbeat
        - <seconds>: Set heartbeat interval and enable

        Args:
            cmd_full: Full command string. Examples:
                - "/heartbeat" - Show current status
                - "/heartbeat on" - Enable heartbeat
                - "/heartbeat off" - Disable heartbeat
                - "/heartbeat 300" - Set interval to 300 seconds
            msg: Optional InputMessage object (unused, kept for interface consistency).

        Displays:
            - Current heartbeat status and idle time
            - Confirmation of enable/disable/interval change
            - Usage help for invalid arguments
        """
        parts = cmd_full.split()
        sub = parts[1].strip().lower() if len(parts) > 1 else ""

        if not sub:
            if self._heartbeat.enabled:
                status(
                    f"Heartbeat: ON (interval {self._heartbeat.interval_seconds}s, "
                    f"idle {int(time.time() - self._heartbeat.last_activity_time)}s)"
                )
            else:
                status("Heartbeat: OFF")
            return

        if sub == "on":
            if self._heartbeat.interval_seconds is None:
                self._heartbeat.interval_seconds = 600
            self._heartbeat.enabled = True
            status(f"Heartbeat enabled ({self._heartbeat.interval_seconds}s)")
        elif sub == "off":
            self._heartbeat.enabled = False
            status("Heartbeat disabled")
        elif sub.isdigit() and int(sub) > 0:
            self._heartbeat.interval_seconds = int(sub)
            self._heartbeat.enabled = True
            status(f"Heartbeat set to {self._heartbeat.interval_seconds}s")
        else:
            warning(
                "Usage: /heartbeat | /heartbeat on | /heartbeat off | /heartbeat <seconds>"
            )

    @_command("llm", subcommands=())
    def _cmd_llm(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /llm command to list or switch LLM groups.

        Lists all available LLM groups if called without arguments, or switches
        to a specified LLM group if an argument is provided.

        Args:
            cmd_full: Full command string. Examples:
                - "/llm" - Lists all available LLM groups
                - "/llm <group_name>" - Switches to the specified group
            msg: Optional InputMessage object (unused, kept for interface consistency).

        Displays:
            - List of available LLM groups with current group marked
            - Error message if specified group is not found
            - Confirmation message on successful switch
        """
        parts = cmd_full.strip().split()
        if len(parts) == 1:
            for name, _ in self.llm_groups.items():
                marker = " <- current" if name == self.current_group_name else ""
                echo(f"  {name}{marker}")
        else:
            group_name = parts[1]
            if group_name not in self.llm_groups:
                echo(f"Unknown LLM group: {group_name}")
                echo(f"Available: {', '.join(self.llm_groups.keys())}")
                return
            self.current_group_name = group_name
            self._rebuild_client(clear_overrides=True)
            echo(f"Switched to LLM group: {group_name}")

    @_command("exec", subcommands=())
    def _cmd_exec(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /exec command to execute a tool directly.

        Parses and executes a tool with arguments provided in the command string.
        The tool name and arguments are extracted by removing the "/exec" prefix.

        Args:
            cmd_full: Full command string (e.g., "/exec cd path=..").
                The tool and arguments are extracted by removing "/exec" prefix.
            msg: Optional InputMessage object (unused, kept for interface consistency).

        Displays:
            - Tool execution result or error message
        """
        args = cmd_full.removeprefix("/exec").strip()
        self._exec_tool(args)

    @_command("subagent", subcommands=())
    def _cmd_subagent(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /subagent command to run an isolated subagent.

        Creates a new subagent with an isolated context to execute a task.
        The subagent does not inherit the parent's conversation history,
        making it suitable for tasks requiring complete isolation.

        Args:
            cmd_full: Full command string (e.g., "/subagent <task description>").
                The task is extracted by removing the "/subagent" prefix.
            msg: Optional InputMessage object (unused, kept for interface consistency).

        Displays:
            - Usage message if no task provided
            - Progress indicators during execution
            - Result or error message after completion

        NOTE: In normal operation, these commands are always dispatched after a complete
        turn ending with `assistant`. The synthetic `user → assistant` pair appended to
        context is therefore always valid. If an invalid sequence is ever observed, the
        cause is a pre-existing context violation — these commands do not create invalid
        sequences from valid state.
        """
        task = cmd_full.removeprefix("/subagent").strip()
        if not task:
            subagent_usage()
            return
        subagent_start_display(task[:60])
        blank_line()
        try:
            result = invoke_subagent_sync(
                prompt=task,
                system_prompt=self.context.get_system(),
                parent_agent=self,
                nesting_count=self.nesting_count,
            )
            subagent_output_header()
            blank_line()
            echo(result)
            blank_line()
            subagent_output_footer()
            self.context.append_user(f"{task}")
            self.context.append_assistant(result, None)
        except (TypeError, KeyError, RuntimeError) as e:
            subagent_error(str(e))
            self.context.append_user(f"{task}")
            self.context.append_assistant(f"Subagent failed with error: {e}", None)

    @_command("fork", subcommands=())
    def _cmd_fork(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /fork command to run a forked agent with inherited context.

        Creates a forked subagent that inherits the complete parent context and state.
        Unlike /subagent, the fork has access to the full conversation history,
        making it suitable for tasks that need context continuity.

        Args:
            cmd_full: Full command string (e.g., "/fork <task description>").
                The task is extracted by removing the "/fork" prefix.
            msg: Optional InputMessage object (unused, kept for interface consistency).

        Displays:
            - Usage message if no task provided
            - Progress indicators during execution
            - Result or error message after completion

        NOTE: In normal operation, these commands are always dispatched after a complete
        turn ending with `assistant`. The synthetic `user → assistant` pair appended to
        context is therefore always valid. If an invalid sequence is ever observed, the
        cause is a pre-existing context violation — these commands do not create invalid
        sequences from valid state.
        """
        task = cmd_full.removeprefix("/fork").strip()
        if not task:
            fork_usage()
            return
        fork_display(task)
        blank_line()
        try:
            result = invoke_fork_sync(
                prompt=task,
                parent_context=self.context,
                parent_agent=self,
                nesting_count=self.nesting_count,
            )
            assistant_message_display(result)
            self.context.append_user(f"{task}")
            self.context.append_assistant(result, None)
        except (TypeError, KeyError, RuntimeError) as e:
            fork_error(str(e))
            self.context.append_user(f"{task}")
            self.context.append_assistant(f"Fork failed with error: {e}", None)

    @_command("ctx", subcommands=("full", "summary", "tool", "trace", "user", "assistant"))
    def _cmd_ctx(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /ctx command to display the context.

        Displays the current context in summary mode by default, or in the mode
        specified by the command arguments.

        Args:
            cmd_full: Full command string. Examples:
                - "/ctx" - Show summary (default)
                - "/ctx full" - Show full context
                - "/ctx trace" - Show debug trace
            msg: Optional InputMessage object (unused, kept for interface consistency).

        Displays:
            - Context information in the specified mode
        """
        mode = cmd_full.removeprefix("/ctx").strip() or "summary"
        echo(
            self.context.dump(
                mode=mode,
                max_tokens=self.max_context_tokens,
                exact_tokens=self._session.last_exact_context_tokens,
            )
        )

    @_command("tool", subcommands=())
    def _cmd_tool(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /tool command to display tool messages only.

        Displays only the tool messages from the context.

        Args:
            cmd_full: Full command string (unused, kept for interface consistency).
            msg: Optional InputMessage object (unused, kept for interface consistency).

        Displays:
            - Tool messages from the context
        """
        echo(
            self.context.dump(
                mode="tool",
                max_tokens=self.max_context_tokens,
                exact_tokens=self._session.last_exact_context_tokens,
            )
        )

    @_command("assistant", subcommands=())
    def _cmd_assistant(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /assistant command to display assistant messages only.

        Displays only the assistant messages from the context.

        Args:
            cmd_full: Full command string (unused, kept for interface consistency).
            msg: Optional InputMessage object (unused, kept for interface consistency).

        Displays:
            - Assistant messages from the context
        """
        echo(
            self.context.dump(
                mode="assistant",
                max_tokens=self.max_context_tokens,
                exact_tokens=self._session.last_exact_context_tokens,
            )
        )

    @_command("agent_card", subcommands=())
    def _cmd_agent_card(
        self, cmd_full: str, msg: Optional[InputMessage] = None
    ) -> None:
        """Handle the /agent_card command to display the agent card as JSON.

        Displays the agent card containing comprehensive information about the
        agent's current state, capabilities, and context metrics.

        Args:
            cmd_full: Full command string (unused, kept for interface consistency).
            msg: Optional InputMessage object containing request_id for the agent card.

        Displays:
            - Agent card JSON with formatted indentation
        """
        context_dump_with_json(
            json.dumps(show_agent_card(self.get_status()), indent=2)
        )

    @_command("restart", subcommands=())
    def _cmd_restart(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /restart command to restart the agent.

        Restarts the agent process while preserving the current context and
        configuration.

        Args:
            cmd_full: Full command string. The restart arguments are extracted
                by removing the "/restart" prefix.
            msg: Optional InputMessage object (unused, kept for interface consistency).

        Displays:
            - Restart command being executed
            - Error messages if restart fails
        """
        self._handle_restart(cmd_full.removeprefix("/restart").strip() or "")

    @_command("clear", subcommands=())
    def _cmd_clear(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /clear command to clear the context.

        Clears all messages from the context except the system prompt and
        displays a confirmation message.

        Args:
            cmd_full: Full command string (unused, kept for interface consistency).
            msg: Optional InputMessage object (unused, kept for interface consistency).
        """
        self.clear_context()
        context_cleared_success()

    @_command("continue", subcommands=("list", "preview"))
    def _cmd_continue(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /continue command to load previous contexts.

        Supports multiple subcommands for loading and previewing saved contexts:
        - No arguments: Load the latest context from the same terminal session
        - "list": List saved contexts (default 25, or specify count)
        - "<n>": Load context by ID
        - "preview <n>": Preview the last 3 messages of context by ID

        Args:
            cmd_full: Full command string. Examples:
                - "/continue" - Load latest context
                - "/continue list" - List contexts
                - "/continue list 50" - List last 50 contexts
                - "/continue 5" - Load context #5
                - "/continue preview 5" - Preview context #5
            msg: Optional InputMessage object (unused, kept for interface consistency).

        Displays:
            - Context restoration success/failure messages
            - List of contexts for "list" subcommand
            - Preview of context for "preview" subcommand
            - Usage help for invalid arguments
        """
        self._handle_continue(cmd_full.removeprefix("/continue").strip())

    @_command("name", subcommands=())
    def _cmd_name(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /name command to display or set the agent name.

        If no argument is provided, displays the current agent name.
        If an argument is provided, updates the agent name to the new value.

        Args:
            cmd_full: Full command string (e.g., "/name" or "/name <new_name>").
                The name is extracted by removing the "/name" prefix.
            msg: Optional InputMessage object (unused, kept for interface consistency).
        """
        name_arg = cmd_full.removeprefix("/name").strip()
        if not name_arg:
            echo(f"{self.agent_name}")
        else:
            self.agent_name = name_arg
            echo(f"Agent name updated to: {self.agent_name}")

    @_command("compress", subcommands=())
    def _cmd_compress(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """Handle the /compress command to reduce context size.

        Attempts to compress the conversation context by removing older messages
        while preserving recent conversation history. Uses a 30% retention ratio
        to reduce token count when approaching limits.

        Args:
            cmd_full: Full command string (unused, kept for interface consistency).
            msg: Optional InputMessage object (unused, kept for interface consistency).

        Displays:
            - Success message if compression succeeded
            - Failure message if compression failed
        """
        if self.context.compress(0.30, self, self.get_all_tools()):
            compress_success()
        else:
            compress_fail()

    @_command("audit", subcommands=("short", "long", "full"))
    def _cmd_audit(self, cmd_full: str, msg: Optional[InputMessage] = None) -> None:
        """View audit log with colored flow visualization.

        Usage:
            /audit                        → current session, short mode
            /audit short                  → current session, short mode (default)
            /audit long                   → current session, long mode
            /audit full                   → current session, full mode
            /audit /path/to/file.audit    → specified file, short mode
            /audit short /path/file.audit → specified file, short mode
        """
        from agent_console_audit import show_audit

        # Parse arguments: positional only — first token is mode, second is file.
        # This avoids ambiguity when a filename contains "short", "long", or "full".
        rest = cmd_full.removeprefix("/audit").strip()
        parts = rest.split()

        mode = "short"
        file_arg = None

        if parts:
            if parts[0] in ("short", "long", "full"):
                mode = parts[0]
                if len(parts) > 1:
                    file_arg = parts[1]
            else:
                file_arg = parts[0]

        # Resolve audit file path
        if file_arg:
            audit_path = Path(file_arg)
        else:
            audit_path = self._session.audit_file
            # Flush buffered records so the file exists and is up-to-date
            self._session.audit_writer.flush()

        show_audit(audit_path, mode)
