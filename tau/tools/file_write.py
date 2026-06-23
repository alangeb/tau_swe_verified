"""Create or overwrite a file with content. Auto-creates parent directories."""

from __future__ import annotations

from tools import ToolMetadata

import difflib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

from .lib.sandbox import check_path, get_allowed_paths
# ── Tool interface ────────────────────────────────────────────────

metadata = ToolMetadata(
    name="file_write",
    description=(
        "Create or overwrite a file with content. Auto-creates parent directories. "
        "Returns a unified diff on overwrite. For targeted edits, use file_edit."
    ),
    aliases_cmd=["write_file", "write"],
    aliases_arg={"file": "file_path"},
    max_size=8192,
)


# ── Args schema ───────────────────────────────────────────────────

@dataclass
class Args:
    file_path: str = field(
        metadata={"description": "Path to the file to write (relative or absolute)"}
    )
    content: str = field(metadata={"description": "Content to write to the file"})



# ── Execution ─────────────────────────────────────────────────────

def run(file_path: str, content: str, agent: "TauBot", tool_call_id: str | None) -> str:
    """Create or overwrite a file with content."""
    if not file_path:
        return "ERROR: file_path is required."

    path, err = check_path(metadata.name, agent, file_path, allowed_paths=get_allowed_paths(agent), write_operation=True)
    if err:
        return err

    if path.is_dir():
        return f"ERROR: '{file_path}' is a directory, not a file."

    old_content: str | None = None
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                old_content = f.read()
        except UnicodeDecodeError:
            return f"ERROR: Existing file is not a text file (UTF-8 decode error) — {file_path}"
        except PermissionError:
            return f"ERROR: Permission denied reading file — {file_path}"
        except Exception as e:
            return f"ERROR: Failed to read existing file {file_path}: {str(e)}"

    parent_dir = path.parent
    if parent_dir and not parent_dir.exists():
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            return f"ERROR: Permission denied creating directory: {parent_dir}"
        except Exception as e:
            return f"ERROR: Failed to create directory {parent_dir}: {str(e)}"

    diff = ""
    if old_content is not None:
        try:
            old_lines = old_content.splitlines(keepends=True)
            new_lines = content.splitlines(keepends=True)
            diff = "".join(
                difflib.unified_diff(
                    old_lines, new_lines,
                    fromfile=str(path), tofile=str(path), lineterm="",
                )
            )
        except Exception:
            diff = "(diff failed)"

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except PermissionError:
        return f"ERROR: Permission denied writing to file — {file_path}"
    except Exception as e:
        return f"ERROR: Failed to write file {file_path}: {str(e)}"

    if old_content is not None:
        added = len(content.splitlines()) - len(old_content.splitlines())
        return f"Updated: {file_path}\nLines changed: {added:+d}\n\nDiff:\n{diff}\n"
    added = len(content.splitlines())
    return f"Created: {file_path}\nLines: {added}\n"
