from __future__ import annotations

from tools import ToolMetadata

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .lib.sandbox import get_allowed_paths, validate_path
if TYPE_CHECKING:
    from agent_core import TauBot


# ── Tool metadata ────────────────────────────────────────────────────────────

metadata = ToolMetadata(
    name="glob",
    description=(
        "Find files matching a glob pattern. "
        "Common patterns: *.py (all Python files), **/*.py (recursive), config/{a,b}.json (alternation)."
    ),
    max_size=32768,
)


# ── Args schema ──────────────────────────────────────────────────────────────

@dataclass
class Args:
    pattern: str = field(
        metadata={
            "description": "Glob pattern (e.g., '*.py', '**/*.md', 'config/{a,b}.json')"
        }
    )
    path: str = field(
        default=".",
        metadata={"description": "Base directory (default: current directory)"},
    )
    recursive: bool = field(
        default=True, metadata={"description": "Search recursively"}
    )



# ── Execution ────────────────────────────────────────────────────────────────

def run(
    pattern: str,
    agent: "TauBot",
    tool_call_id: str | None = None,
    path: str = ".",
    recursive: bool = True,
) -> str:
    base_path, err = validate_path(path, allowed_paths=get_allowed_paths(agent))
    if err:
        return err

    if not base_path.exists():
        return f"ERROR: Base path not found: {path}"
    if not base_path.is_dir():
        return f"ERROR: Path is not a directory: {path}"

    try:
        matches = sorted(
            base_path.rglob(pattern) if recursive else base_path.glob(pattern)
        )

        if not matches:
            return f"No matches found for pattern '{pattern}' in '{path}'"

        return "\n".join(str(m.relative_to(base_path)) for m in matches)
    except Exception as e:
        return f"ERROR: Glob failed: {e}"
