from __future__ import annotations

from tools import ToolMetadata

import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .lib.sandbox import get_allowed_paths, validate_path
if TYPE_CHECKING:
    from agent_core import TauBot


# ── Tool metadata ────────────────────────────────────────────────────────────

metadata = ToolMetadata(
    name="grep",
    description=(
        "Search for patterns in files using grep (POSIX ERE regex). "
        "Supports recursive search, case-insensitive matching, result limiting, "
        "and 'lines'/'context' output formats. "
        "Common patterns: `def\\s+\\w+\\(` (functions), `class\\s+\\w+` (classes), "
        "`TODO|FIXME` (markers), `target=\\w+` (thread targets), `callback=\\w+` (callbacks). "
        "Use `recursive=True` for directories. Use `output_format='context'` for surrounding lines."
    ),
    max_size=131072,
    timeout=30,
)


# ── Args schema ──────────────────────────────────────────────────────────────

@dataclass
class Args:
    pattern: str = field(
        metadata={
            "description": "Pattern to search for (supports regex). Full POSIX ERE support. Examples: 'def', 'def\\\\s+', 'TODO|FIXME', 'class\\\\s+\\\\w+', '->\\\\s*str'."
        }
    )
    path: str = field(
        default=".", metadata={"description": "File or directory to search"}
    )
    recursive: bool = field(
        default=False, metadata={"description": "Search recursively"}
    )
    case_sensitive: bool = field(
        default=False,
        metadata={
            "description": "Case sensitive search (default: False for case-insensitive)"
        },
    )
    max_results: int = field(
        default=50, metadata={"description": "Maximum results to return"}
    )
    output_format: str = field(
        default="lines",
        metadata={
            "description": "Output format: 'lines' (file:line:content) or 'context' (with 2 lines before/after)"
        },
    )



# ── Execution ────────────────────────────────────────────────────────────────

def run(
    pattern: str,
    agent: "TauBot",
    tool_call_id: str | None = None,
    path: str = ".",
    recursive: bool = False,
    case_sensitive: bool = False,
    max_results: int = 50,
    output_format: str = "lines",
) -> str:
    search_path, err = validate_path(path, allowed_paths=get_allowed_paths(agent))
    if err:
        return err

    try:
        cmd = ["grep"]
        if not case_sensitive:
            cmd.append("-i")
        if recursive:
            cmd.append("-r")
        cmd.append("-E")
        cmd.extend(["-n", "-H"])
        if output_format == "context":
            cmd.extend(["-C", "2"])
        cmd.extend([pattern, path])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, start_new_session=True)

        if result.returncode == 0:
            if not result.stdout.strip():
                return "No matches found."
            return "\n".join(result.stdout.strip().split("\n")[:max_results])
        elif result.returncode == 1:
            return "No matches found."
        elif result.returncode == 2:
            return f"ERROR: grep syntax error - {result.stderr.strip()}"
        return f"ERROR: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return "ERROR: Grep timed out after 30 seconds."
    except Exception as e:
        return f"ERROR: {e}"
