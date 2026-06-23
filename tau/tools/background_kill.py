from __future__ import annotations

from tools import ToolMetadata

import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ──
metadata = ToolMetadata(
    name="background_kill",
    description="Kill a specific tmux session or all agent sessions (tmux-agent-* prefix).",
    aliases_cmd=["run_background_kill"],
    max_size=2048,
)


# ── Args schema ──
@dataclass
class Args:
    session_name: str = field(
        default="",
        metadata={"description": "Session name (empty = kill all agent sessions)"},
    )



# ── Helpers ──
def _list_sessions() -> list[str]:
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        start_new_session=True,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.split("\n") if line.strip()]


def _kill_session(name: str) -> str:
    result = subprocess.run(
        ["tmux", "kill-session", "-t", name], capture_output=True, text=True, start_new_session=True
    )
    if result.returncode == 0:
        return f"Killed session '{name}'"
    if "no session" in result.stderr.lower():
        return f"ERROR: Session '{name}' does not exist"
    return f"ERROR: Failed to kill session '{name}': {result.stderr.strip()}"


def _kill_all_agent_sessions() -> int:
    sessions = _list_sessions()
    agent_sessions = [s for s in sessions if s.startswith("tmux-agent-")]
    if not agent_sessions:
        return 0
    killed = sum(
        1
        for s in agent_sessions
        if subprocess.run(
            ["tmux", "kill-session", "-t", s], capture_output=True, start_new_session=True
        ).returncode
        == 0
    )
    return killed


# ── Execution ──
def run(
    agent: TauBot, tool_call_id: str | None = None, session_name: str = ""
) -> str:
    """Kill a tmux session or all agent sessions."""
    try:
        if not session_name:
            killed = _kill_all_agent_sessions()
            return f"Killed {killed} agent session(s)"
        return _kill_session(session_name)
    except Exception as e:
        return f"ERROR: Failed to kill session(s): {e}"
