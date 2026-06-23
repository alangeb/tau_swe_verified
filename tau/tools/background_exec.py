from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tools import ToolMetadata

from .lib.session_utils import validate_session, strip_ansi

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ──
metadata = ToolMetadata(
    name="background_exec",
    description=(
        "Execute a command in a tmux session. Sends command with C-m (Enter), "
        "optionally waits for output. When wait=True, polls until the shell prompt "
        "returns (command completed) or timeout reached. Use wait=False for fire-and-forget."
    ),
    aliases_cmd=["run_background_exec", "bg_exec"],
    max_size=131072,
)


# ── Args schema ──
@dataclass
class Args:
    session_name: str = field(
        metadata={"description": "Session name (required, must start with tmux-agent-)"}
    )
    command: str = field(metadata={"description": "Command to execute"})
    wait: bool = field(
        default=True,
        metadata={
            "description": (
                "Wait for command completion (shell prompt returns). "
                "Defaults to True. Use False for fire-and-forget."
            )
        },
    )
    timeout: float = field(
        default=30.0,
        metadata={
            "description": (
                "Max seconds to wait for command completion when wait=True. "
                "Defaults to 30."
            )
        },
    )


def _capture_pane(session_name: str) -> str:
    """Capture tmux pane output."""
    result = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", session_name],
        capture_output=True,
        text=True,
        start_new_session=True,
    )
    if result.returncode != 0:
        return ""
    return strip_ansi(result.stdout.strip())


def _is_prompt(output: str) -> bool:
    """Check if the last line looks like a shell prompt."""
    lines = output.strip().split("\n")
    if not lines:
        return False
    last_line = lines[-1].strip()
    if not last_line:
        return False
    # Match: user@host:path$ or bare $ > % #
    # Empty string is NEVER a prompt — removed trailing |) that matched empty.
    return bool(re.search(r"^(?:\w+@[\w.-]+:\S*[$]|[$]|>|%|#)\s*$", last_line))


# ── Execution ──
def run(
    session_name: str,
    command: str,
    agent: TauBot,
    tool_call_id: str | None = None,
    wait: bool = True,
    timeout: float = 30.0,
) -> str:
    """Execute a command in a tmux session.
    
    When wait=True: polls until shell prompt returns (command completed) or timeout.
    When wait=False: sends command and returns immediately.
    """
    if err := validate_session(session_name):
        return err

    try:
        # Record output before sending command (to detect new output later)
        before_output = _capture_pane(session_name)

        # Send the command
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, command, "C-m"],
            capture_output=True,
            text=True,
            start_new_session=True,
        )
        if not wait:
            return f"Sent command to session '{session_name}'"

        # Poll for command completion (shell prompt returns)
        start_time = time.time()
        poll_interval = 0.2  # 200ms polling
        max_polls = int(timeout / poll_interval)

        for _ in range(max_polls):
            time.sleep(poll_interval)
            current_output = _capture_pane(session_name)

            # Check if we got new output AND the prompt returned
            if current_output and current_output != before_output:
                if _is_prompt(current_output):
                    return current_output

        # Timeout reached - return whatever we have
        final_output = _capture_pane(session_name)
        if final_output:
            return (
                f"WARNING: Timed out after {timeout:.1f}s (command may still be running)\n"
                f"Output so far:\n{final_output}"
            )
        return f"Command sent to session '{session_name}' but could not capture output"
    except Exception as e:
        return f"ERROR: Failed to execute command: {e}"
