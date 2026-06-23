from __future__ import annotations

from tools import ToolMetadata

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .lib.session_utils import validate_session, capture_pane

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ──
metadata = ToolMetadata(
    name="background_tail",
    description="Show last N lines from tmux pane output.",
    aliases_cmd=["run_background_tail"],
    max_size=131072,
)


# ── Args schema ──
@dataclass
class Args:
    session_name: str = field(
        metadata={"description": "Session name (required, must start with tmux-agent-)"}
    )
    lines: int = field(
        default=10, metadata={"description": "Number of lines to show from end"}
    )


# ── Execution ──
def run(
    session_name: str, agent: TauBot, tool_call_id: str | None = None, lines: int = 10
) -> str:
    """Show last N lines from tmux pane output."""
    if err := validate_session(session_name):
        return err
    if lines < 0:
        return "ERROR: lines must be non-negative"
    if lines == 0:
        return ""

    return capture_pane(session_name, lines) or "No output captured"
