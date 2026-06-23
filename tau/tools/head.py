from __future__ import annotations

from tools import ToolMetadata

import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot


# ── Tool metadata ────────────────────────────────────────────────────────────

metadata = ToolMetadata(
    name="head",
    description="Display the first N lines of a file. Useful for quick previews of large files.",
    max_size=32768,
    timeout=10,
)


# ── Args schema ──────────────────────────────────────────────────────────────

@dataclass
class Args:
    path: str = field(metadata={"description": "File path to read"})
    lines: int = field(default=10, metadata={"description": "Number of lines to read"})



# ── Execution ────────────────────────────────────────────────────────────────

def run(
    path: str, agent: TauBot, tool_call_id: str | None = None, lines: int = 10
) -> str:
    try:
        result = subprocess.run(
            ["head", "-n", str(lines), path],
            capture_output=True,
            text=True,
            timeout=10,
            start_new_session=True,
        )
        if result.returncode == 0:
            return result.stdout
        return f"ERROR: {result.stderr.strip()}"

    except subprocess.TimeoutExpired:
        return "ERROR: head timed out after 10 seconds."
    except Exception as e:
        return f"ERROR: {e}"
