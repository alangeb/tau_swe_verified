"""Low-level console primitives for TauBot.

All other console modules import from here. This is the foundation layer.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

from agent_models import Colors

__all__ = [
    "_log_audit_lock",
    "_log_audit",
    "compute_duration",
    "format_duration_ms",
    "_cw",
    "_role_color",
    "echo",
    "blank_line",
    "echo_no_newline",
    "prompt",
    "status",
    "reasoning",
    "verbose",
    # Parameterized display helpers
    "display_error",
    "display_warning",
    "display_success",
    "display_info",
]

# Thread-safe recursion guard for _log_audit.
# Prevents audit → console → audit infinite loops.
_log_audit_lock = threading.Lock()


def _log_audit(level: str, message: str) -> None:
    """Bridge console output to audit log.

    Calls the global audit writer if available. No-op if audit is not initialized
    or if the writer fails (audit must never break console output).
    Uses a lock to prevent audit → console → audit recursion.
    """
    if not _log_audit_lock.acquire(blocking=False):
        return  # Already in progress — prevent recursion
    try:
        if level == "error":
            from agent_audit_bridge import log_console_error
            log_console_error(message)
        elif level == "warning":
            from agent_audit_bridge import log_console_warning
            log_console_warning(message)
    finally:
        _log_audit_lock.release()


def compute_duration(obj: object, attr: str = "_start_time") -> float:
    """Compute session duration from a start-time attribute on *obj*.

    Returns 0 if *obj* has no such attribute (e.g., start time never set).
    """
    return time.time() - getattr(obj, attr, time.time()) if hasattr(obj, attr) else 0


def format_duration_ms(duration_ms: float) -> str:
    """Format a duration in milliseconds into a human-readable string.

    Uses seconds for values >= 1000ms, milliseconds otherwise.
    """
    if duration_ms >= 1000:
        return f"{duration_ms / 1000:.1f}s"
    return f"{duration_ms:.0f}ms"


# ── Primitives ──────────────────────────────────────────────────────────────


def _cw(color: str, text: str, newline: bool = True) -> None:
    """Write colorized text to stdout."""
    sys.stdout.write(f"{color}{text}{Colors.RESET}" + ("\n" if newline else ""))


def _role_color(role: str) -> str:
    """Return ANSI color code for the specified message role."""
    if role == "user":
        return Colors.RESET
    if role in ("tool", "tool_call"):
        return Colors.CYAN
    return Colors.GREEN


def echo(text: str, newline: bool = True) -> None:
    sys.stdout.write(text + ("\n" if newline else ""))


def blank_line() -> None:
    sys.stdout.write("\n")


def echo_no_newline(text: str) -> None:
    sys.stdout.write(text)


def prompt(text: str = "") -> None:
    _cw(Colors.CYAN, text, newline=False)
    sys.stdout.flush()


def status(text: str) -> None:
    _cw(Colors.CYAN, text)


def reasoning(text: str) -> None:
    _cw(Colors.REASONING, f"[REASON] {text}")


def verbose(text: str) -> None:
    _cw(Colors.GREEN, text)


# ── Parameterized display helpers ──────────────────────────────────────────
# These replace thin wrapper functions. Used by agent_console_messages.py
# and agent_console_display.py. Each helper takes a color and a message,
# providing a consistent interface for displaying formatted console output.


def display_error(text: str) -> None:
    """Display an error message in red."""
    _cw(Colors.RED, text)


def display_warning(text: str) -> None:
    """Display a warning message in yellow."""
    _cw(Colors.YELLOW, text)


def display_success(text: str) -> None:
    """Display a success message in green."""
    _cw(Colors.GREEN, text)


def display_info(text: str) -> None:
    """Display an informational message in cyan."""
    _cw(Colors.CYAN, text)
