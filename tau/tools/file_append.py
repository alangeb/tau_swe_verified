"""Append content to a file. Creates the file if missing; auto-creates parent dirs."""

from __future__ import annotations

from tools import ToolMetadata

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

from .lib.sandbox import check_path, get_allowed_paths

# ── Tool interface ────────────────────────────────────────────────

metadata = ToolMetadata(
    name="file_append",
    description=(
        "Append content to a file. Creates the file if missing; auto-creates parent dirs. "
        "Content is appended exactly as given (no automatic newline). Use file_write to overwrite."
    ),
    aliases_cmd=["append_file", "append"],
    aliases_arg={"file": "file_path"},
    max_size=8192,
)


# ── Args schema ───────────────────────────────────────────────────

@dataclass
class Args:
    file_path: str = field(metadata={"description": "Path to the file to append to (relative or absolute)"})
    content: str = field(metadata={"description": "Content to append to the file"})


# ── Execution ─────────────────────────────────────────────────────

def run(file_path: str, content: str, agent: "TauBot", tool_call_id: str | None) -> str:
    """Append content to a file."""
    if not file_path:
        return "ERROR: file_path is required."

    path, err = check_path(metadata.name, agent, file_path, allowed_paths=get_allowed_paths(agent), write_operation=True)
    if err:
        return err

    if path.is_dir():
        return f"ERROR: '{file_path}' is a directory, not a file."

    parent_dir = path.parent
    if parent_dir and not parent_dir.exists():
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            return f"ERROR: Permission denied creating directory: {parent_dir}"
        except Exception as e:
            return f"ERROR: Failed to create directory {parent_dir}: {str(e)}"

    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
    except PermissionError:
        return f"ERROR: Permission denied writing to file — {file_path}"
    except Exception as e:
        return f"ERROR: Failed to append to file {file_path}: {str(e)}"

    lines = len(content.splitlines())
    return f"Appended {lines} line{'s' if lines != 1 else ''} to {file_path}"
