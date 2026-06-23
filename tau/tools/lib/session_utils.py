"""Shared utilities for tmux session management."""

import re
import subprocess

__all__ = ["session_exists", "validate_session", "strip_ansi", "capture_pane"]


def session_exists(name: str) -> bool:
    """Check if a tmux session with the given name exists."""
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        start_new_session=True,
    )
    if result.returncode != 0:
        return False
    return name in result.stdout


def validate_session(session_name: str) -> str | None:
    """Return error message if session_name is invalid, else None."""
    if not session_name.startswith("tmux-agent-"):
        return "ERROR: Session name must start with 'tmux-agent-'"
    if not session_exists(session_name):
        return f"ERROR: Session '{session_name}' does not exist"
    return None


def strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def capture_pane(session_name: str, lines: int = 30) -> str:
    """Capture tmux pane and return last N lines.

    Captures full pane content and slices to last N lines in Python,
    since tmux -S flag counts from history buffer end, not content end.

    Args:
        session_name: tmux session name.
        lines: Number of lines to return from end.

    Returns:
        Last N lines of pane output, or empty string if capture fails.
    """
    result = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", session_name],
        capture_output=True,
        text=True,
        start_new_session=True,
    )
    if result.returncode != 0:
        return ""
    all_lines = strip_ansi(result.stdout).split("\n")
    last_lines = all_lines[-lines:] if len(all_lines) >= lines else all_lines
    return "\n".join(last_lines).strip() or ""
