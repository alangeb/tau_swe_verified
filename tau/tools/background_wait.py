"""Wait for background tmux session with idle/keyword detection.

Monitors a tmux session's output and returns when:
- Maximum time elapsed (max_seconds)
- Output has been idle (idle_seconds)
- Keywords found in output
- Session died
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tools import ToolMetadata
from .lib.session_utils import session_exists, validate_session, capture_pane

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ──
metadata = ToolMetadata(
    name="background_wait",
    description=(
        "Wait for background tmux session with idle/keyword detection. "
        "PREFERRED over `bash sleep N` for ANY background task monitoring. "
        "Set max_seconds very high (e.g., 600-1800) so the task can run to completion. "
        "Use a SHORT idle_seconds (e.g., 15-30) to detect hangs quickly. "
        "ALWAYS provide multiple keywords covering success, failure, and error patterns "
        "(e.g., 'error|warning|complete|done|FAILED|SUCCESS|PASSED|Traceback|Exception'). "
        "Returns immediately when: (1) any keyword matches output, "
        "(2) output has been idle for idle_seconds (likely hung), "
        "(3) max_seconds elapsed (hard timeout), or (4) session died. "
        "Use this INSTEAD of `bash sleep 180` — it detects hangs, catches errors early, "
        "and returns results automatically."
    ),
    timeout=3600,
)


# ── Args schema ──
@dataclass
class Args:
    session_name: str = field(
        metadata={"description": "Session name (required, must start with tmux-agent-)"}
    )
    max_seconds: int = field(
        metadata={"description": "Maximum seconds to wait before returning (required, >= 1)"}
    )
    idle_seconds: int = field(
        metadata={"description": "Return early if no output for this many seconds (required, >= 1)"}
    )
    keywords: str = field(
        default="",
        metadata={"description": "Regex pattern to match against output (optional). Examples: 'error|warning', 'complete|done', 'FAILED|SUCCESS'"}
    )
    tail_lines: int = field(
        default=30,
        metadata={"description": "Number of lines to return from output (optional, default 30)"}
    )
    poll_interval: int = field(
        default=1,
        metadata={"description": "Seconds between output checks (optional, default 1)"}
    )


def _session_alive(session_name: str) -> bool:
    """Check if session is still alive."""
    return session_exists(session_name)


def _format_result(current_output: str, tail_lines: int) -> str:
    """Format the result with last N lines."""
    if tail_lines > 0 and current_output.strip():
        lines = current_output.strip().split("\n")[-tail_lines:]
        return "\n".join(lines)
    return ""


# ── Execution ──
def run(
    session_name: str,
    agent: "TauBot",
    tool_call_id: str | None = None,
    max_seconds: int = 60,
    idle_seconds: int = 30,
    keywords: str = "",
    tail_lines: int = 30,
    poll_interval: int = 1,
) -> str:
    """Wait for background session with idle/keyword detection."""
    if err := validate_session(session_name):
        return err
    if max_seconds < 1:
        return "ERROR: max_seconds must be >= 1"
    if idle_seconds < 1:
        return "ERROR: idle_seconds must be >= 1"
    if tail_lines < 0:
        return "ERROR: tail_lines must be >= 0"
    if poll_interval < 1:
        return "ERROR: poll_interval must be >= 1"

    # Compile keyword regex if provided
    keyword_pattern = None
    if keywords:
        try:
            keyword_pattern = re.compile(keywords, re.IGNORECASE)
        except re.error:
            return f"ERROR: Invalid regex pattern: {keywords}"

    # Track output changes for idle detection
    last_output = ""
    last_output_time = time.time()
    start_time = time.time()

    # Poll loop
    while True:
        elapsed = time.time() - start_time

        # Hard timeout check first
        if elapsed >= max_seconds:
            return (
                f"TIMEOUT: Max wait {max_seconds}s reached\n"
                f"Output:\n{_format_result(last_output, tail_lines)}"
            )

        # Check if session is still alive FIRST (before capture)
        if not _session_alive(session_name):
            return (
                f"SESSION DEAD: Session '{session_name}' no longer exists after {elapsed:.0f}s\n"
                f"Last output:\n{last_output}"
            )

        # Capture current output
        current_output = capture_pane(session_name, max(tail_lines, 100))

        # Check for keyword match
        if keyword_pattern and keyword_pattern.search(current_output):
            return (
                f"KEYWORD MATCH: '{keywords}' found after {elapsed:.0f}s\n"
                f"Output:\n{_format_result(current_output, tail_lines)}"
            )

        # Check for idle (no new output for idle_seconds)
        if current_output != last_output:
            last_output = current_output
            last_output_time = time.time()

        idle_time = time.time() - last_output_time
        if idle_time >= idle_seconds and last_output:
            return (
                f"IDLE: No output for {idle_time:.0f}s (threshold: {idle_seconds}s)\n"
                f"Output:\n{_format_result(last_output, tail_lines)}"
            )

        time.sleep(poll_interval)
