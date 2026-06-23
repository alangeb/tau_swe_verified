"""Declarative console message registry for TauBot.

All simple display functions are defined as declarative message templates.
Complex display functions live in agent_console_display.py.
"""
from __future__ import annotations

import sys
from typing import Callable

from agent_console_primitives import (
    _log_audit,
    display_error,
    display_info,
    display_success,
    display_warning,
)


# ── Declarative Message Registry ─────────────────────────────────────────────

class _ConsoleMessage:
    """Declarative message template that generates a callable display function.

    Replaces thin wrapper functions like:
        def unknown_command_error(cmd_name: str) -> None:
            display_error(f"Unknown command: /{cmd_name}")

    With registry entries:
        unknown_command_error = _msg("error", "Unknown command: /{cmd_name}")
    """
    __slots__ = ("_level", "_template", "_audit", "_writer")

    def __init__(
        self,
        level: str,
        template: str,
        *,
        audit: bool = False,
        writer: Callable[[str], None] | None = None,
    ) -> None:
        self._level = level
        self._template = template
        self._audit = audit
        self._writer = writer

    def __call__(self, *args, **kwargs) -> None:
        msg = self._template.format(*args, **kwargs)
        if self._writer is not None:
            self._writer(msg)
        elif self._level == "error":
            display_error(msg)
        elif self._level == "warning":
            display_warning(msg)
        elif self._level == "success":
            display_success(msg)
        elif self._level == "info":
            display_info(msg)
        else:
            display_info(msg)
        if self._audit:
            _log_audit(self._level, msg)


def _msg(
    level: str,
    template: str,
    *,
    audit: bool = False,
    writer: Callable[[str], None] | None = None,
) -> _ConsoleMessage:
    """Create a console message template."""
    return _ConsoleMessage(level, template, audit=audit, writer=writer)


# ── Message Definitions ──────────────────────────────────────────────────────

# Error display
error = _msg("error", "{}", audit=True)
warning = _msg("warning", "{}", audit=True)
invalid_mode_error = _msg("error", "Invalid mode '{}'. Valid modes: {}")
unknown_command_error = _msg("error", "Unknown command: /{}")
unknown_tool_error = _msg("error", "Unknown tool: {}")
no_run_function_error = _msg("error", "Tool has no run function: {}")
command_file_not_found = _msg("error", "Command file not found: {}")
log_dir_error = _msg("error", "ERROR: Cannot create log directory {}: {}")
exec_tool_fail = _msg("error", "Error executing tool: {}")
dynamic_command_result = _msg("success", "{}")

# Message display
user_echo = _msg("info", ">>> {}", writer=lambda t: sys.stdout.write(f"{t}\n"))
assistant_message_display = _msg("success", "[ASSISTANT] {}")
user_message_display = _msg("info", "[USER] {}", writer=lambda t: sys.stdout.write(f"{t}\n"))

# Flow control
restart_flow_info = _msg("info", "[Restarting agent...] Command: {}")
restart_flow_success = _msg("success", "Exiting current agent...")
restart_failure = _msg("error", "Failed to restart agent: {}")
restart_fallback_failure = _msg("error", "Fallback also failed: {}")
force_exit_message = _msg("info", "\n\nForced exit requested. Cleaning up...")
interrupted_message = _msg("info", "\n\nInterrupted. Press Ctrl+C again to force exit.")

# Loop warnings
loop_warning_display = _msg("error", "{}")

# Subagent/fork
subagent_start_display = _msg("info", "[Starting subagent for: {}...]")


def subagent_usage() -> None:
    display_info("Usage: /subagent <task>")
    display_info("Example: /subagent Search for latest Python typing features")


def fork_usage() -> None:
    display_info("Usage: /fork <task>")
    display_info("Example: /fork Continue our discussion about Python typing")


fork_display = _msg("info", "/fork {}")


def subagent_output_header() -> None:
    from agent_console_primitives import blank_line
    display_info("[Subagent output:")
    blank_line()


subagent_output_footer = _msg("info", "[End of subagent output]")
subagent_error = _msg("error", "Subagent error: {}")
fork_error = _msg("error", "Fork error: {}")

# A2A/agent
agents_json = _msg("info", "{}")
agent_card_json = _msg("info", "{}")
agent_status_message = _msg("warning", "{}")
a2a_cli_error = _msg("error", "{}")
a2a_started_message = _msg("info", "[A2A server started: {}]")
agent_a2a_response = _msg("info", "[{}]", writer=lambda t: sys.stdout.write(f"{t}\n"))

# Compression
compress_success = _msg("success", "[COMPRESS] Compression successful.")
compress_fail = _msg("error", "[COMPRESS] Compression failed.")

# Context display
context_cleared_success = _msg("info", "Context cleared.")
context_restored = _msg("info", "[Context restored: {} messages from {}]")

# Tool display
tool_result = _msg("info", "{}")
no_tools_message = _msg("warning", "No tools available.")
shell_tool_not_available = _msg("error", "Tool 'bash' not available.")
shell_command_usage = _msg("info", "Usage: ! <command>")
tools_loaded_message = _msg("info", "[Loaded {} tools]")
tool_blocked = _msg("warning", "Tool '{}' is blocked. Available: {}")

# exec_usage: static message, no args
exec_usage = _msg("info", "Usage: /exec toolname arg1=val1 arg2=val2 ...")


# ── __all__ exports ──────────────────────────────────────────────────────────

__all__ = [
    # Registry
    "_ConsoleMessage", "_msg",
    # Error display
    "error", "warning", "invalid_mode_error",
    "unknown_command_error", "unknown_tool_error", "no_run_function_error",
    "command_file_not_found", "log_dir_error", "exec_tool_fail",
    "dynamic_command_result",
    # Message display
    "user_echo", "assistant_message_display", "user_message_display",
    # Flow control
    "restart_flow_info", "restart_flow_success", "restart_failure",
    "restart_fallback_failure", "force_exit_message", "interrupted_message",
    # Loop warnings
    "loop_warning_display",
    # Subagent/fork
    "subagent_start_display", "subagent_usage", "fork_usage", "fork_display",
    "subagent_output_header", "subagent_output_footer",
    "subagent_error", "fork_error",
    # A2A/agent
    "agents_json", "agent_card_json", "agent_status_message",
    "a2a_cli_error", "a2a_started_message", "agent_a2a_response",
    # Compression
    "compress_success", "compress_fail",
    # Context display
    "context_cleared_success", "context_restored",
    # Tool display
    "tool_result", "no_tools_message", "shell_tool_not_available",
    "shell_command_usage", "tools_loaded_message", "tool_blocked",
    "exec_usage",
]


# Register our warning() function as the callback for agent_session warnings.
from agent_audit_bridge import register_console_warning_callback
register_console_warning_callback(warning)
