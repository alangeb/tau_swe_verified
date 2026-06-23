from __future__ import annotations

from tools import ToolMetadata

import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ──
metadata = ToolMetadata(
    name="background_ls",
    description="List active tmux sessions (agent sessions only by default).",
    aliases_cmd=["run_background_ls"],
    max_size=4096,
)


# ── Args schema ──
@dataclass
class Args:
    all_sessions: bool = field(
        default=False,
        metadata={
            "description": "List all sessions (True) or only agent sessions (False)"
        },
    )



# ── Execution ──
def run(
    agent: TauBot, tool_call_id: str | None = None, all_sessions: bool = False
) -> str:
    """List active tmux sessions."""
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            start_new_session=True,
        )
        if result.returncode != 0:
            return f"ERROR: Failed to list sessions: {result.stderr.strip()}"

        sessions = [line.strip() for line in result.stdout.split("\n") if line.strip()]
        if not all_sessions:
            sessions = [s for s in sessions if s.startswith("tmux-agent-")]

        if not sessions:
            label = "tmux sessions" if all_sessions else "active tmux sessions"
            return f"No {label} found"
        return "\n".join(sessions)
    except Exception as e:
        return f"ERROR: Failed to list sessions: {e}"
