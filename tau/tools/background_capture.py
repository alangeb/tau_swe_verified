from __future__ import annotations

from tools import ToolMetadata

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .lib.session_utils import validate_session, capture_pane

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ──
metadata = ToolMetadata(
    name="background_capture",
    description="Capture tmux pane output with scrollback history.",
    aliases_cmd=["run_background_capture"],
    max_size=131072,
)


# ── Args schema ──
@dataclass
class Args:
    session_name: str = field(
        metadata={"description": "Session name (required, must start with tmux-agent-)"}
    )
    scrollback: int = field(
        default=30, metadata={"description": "Number of scrollback lines"}
    )


# ── Execution ──
def run(
    session_name: str,
    agent: TauBot,
    tool_call_id: str | None = None,
    scrollback: int = 30,
) -> str:
    """Capture pane output with scrollback history."""
    if err := validate_session(session_name):
        return err
    if scrollback < 0:
        return "ERROR: scrollback must be non-negative"
    if scrollback == 0:
        return ""

    return capture_pane(session_name, scrollback) or "No output captured"
