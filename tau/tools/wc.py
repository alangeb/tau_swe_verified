from __future__ import annotations

from tools import ToolMetadata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

import subprocess
from dataclasses import dataclass, field

# ── Tool metadata ──

metadata = ToolMetadata(
    name="wc",
    description=(
        "Count lines, words, and characters in a file. "
        "Pass flags (lines, words, chars) to filter output; defaults to all."
    ),
    max_size=2048,
    timeout=10,
)


# ── Args schema ──

@dataclass
class Args:
    path: str = field(metadata={"description": "File path to analyze"})
    lines: bool = field(default=False, metadata={"description": "Count lines"})
    words: bool = field(default=False, metadata={"description": "Count words"})
    chars: bool = field(default=False, metadata={"description": "Count characters"})



# ── Execution ──

def run(
    path: str,
    agent: TauBot,
    tool_call_id: str | None = None,
    lines: bool = False,
    words: bool = False,
    chars: bool = False,
) -> str:
    """Count lines, words, and/or characters in a file."""
    try:
        cmd = ["wc"]
        if lines:
            cmd.append("-l")
        if words:
            cmd.append("-w")
        if chars:
            cmd.append("-c")
        cmd.append(path)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, start_new_session=True)
        if result.returncode == 0:
            return result.stdout.strip()
        return f"ERROR: {result.stderr.strip()}"

    except subprocess.TimeoutExpired:
        return "ERROR: wc timed out after 10 seconds."
    except Exception as e:
        return f"ERROR: {e}"
