"""Change current working directory. Persists across tool calls within the same agent session."""

from __future__ import annotations

from tools import ToolMetadata

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot


# ── Tool metadata ────────────────────────────────────────────────────────────

metadata = ToolMetadata(
    name="cd",
    description=(
        "Change current working directory. Persists across tool calls within the "
        "same agent session. Use this for persistent cwd/pwd changes."
    ),
    max_size=2048,
)


# ── Args schema ──────────────────────────────────────────────────────────────

@dataclass
class Args:
    """Arguments for the cd tool."""
    path: str = field(
        metadata={"description": "Target directory path (absolute or relative to current cwd)"}
    )



# ── Execution ────────────────────────────────────────────────────────────────

def run(path: str, agent: TauBot, tool_call_id: str | None = None) -> str:
    """Change the working directory."""
    if not path:
        return "ERROR: path is required."

    try:
        p = Path(path)
        if not p.is_absolute():
            p = Path.cwd() / path
        p = p.resolve()

        if not p.is_dir():
            return f"ERROR: '{path}' is not a valid directory."

        os.chdir(p)
        return f"Changed directory to: {p}"
    except OSError as e:
        return f"ERROR: Failed to change to '{path}': {e}"
