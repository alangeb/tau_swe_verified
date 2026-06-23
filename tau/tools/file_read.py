"""Read a text file with line numbers. Use offset/limit for large files."""

from __future__ import annotations

from tools import ToolMetadata

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

from .lib.sandbox import check_path, get_allowed_paths

# ── Tool interface ────────────────────────────────────────────────

FUZZY_FILENAME_THRESHOLD = 0.6  # char-match ratio for fuzzy filename suggestions

metadata = ToolMetadata(
    name="file_read",
    description=(
        "Read a text file with line numbers. Use offset/limit for large files. "
        "Supports directory listing, image/PDF preview, and fuzzy filename matching. "
    ),
    aliases_cmd=["read_file", "read"],
    aliases_arg={"path": "file_path", "file": "file_path"},
    max_size=131072,
)


# ── Args schema ───────────────────────────────────────────────────

@dataclass
class Args:
    file_path: str = field(metadata={"description": "Path to file (absolute or relative)"})
    offset: int = field(default=1, metadata={"description": "1-indexed start line"})
    limit: int = field(default=100, metadata={"description": "Max lines to read (go up to 1000 if intend to read large files in full)"})



# ── Execution ─────────────────────────────────────────────────────

def run(
    file_path: str, agent: "TauBot", tool_call_id: str | None,
    offset: int = 1, limit: int = 100,
) -> str:
    """Read a text file with line numbers."""
    if offset < 1:
        return "ERROR: offset must be >= 1"
    if limit < 1:
        return "ERROR: limit must be >= 1"

    path, err = check_path(metadata.name, agent, file_path, allowed_paths=get_allowed_paths(agent), write_operation=False)
    if err:
        return err

    if not path.exists():
        similar: list[str] = []
        if path.parent.exists():
            fname = path.name.lower()
            for item in path.parent.iterdir():
                if item.is_file():
                    iname = item.name.lower()
                    if (
                        fname in iname
                        or iname in fname
                        or (
                            len(fname) >= 3
                            and len(iname) >= 3
                            and sum(1 for c in fname if c in iname) >= len(fname) * FUZZY_FILENAME_THRESHOLD
                        )
                    ):
                        similar.append(item.name)
        msg = f"ERROR: File not found: {file_path}"
        if similar:
            msg += f"\nDid you mean: {', '.join(similar)}"
        return msg

    if path.is_dir():
        try:
            entries = sorted(path.iterdir())
            output = [f"{path}:"]
            for i, entry in enumerate(entries, start=offset):
                output.append(
                    f"{i}: {entry.name}/" if entry.is_dir() else f"{i}: {entry.name}"
                )
            sliced = output[offset - 1 : offset - 1 + limit]
            result = "\n".join(sliced)
            return f"{result}\n\nTotal entries: {len(entries)}"
        except Exception as e:
            return f"ERROR: {e}"

    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        return f"ERROR: File is not a text file: {file_path}"
    except Exception as e:
        return f"ERROR: {e}"

    selected = lines[offset - 1 : offset - 1 + limit]
    output = []
    for i, line in enumerate(selected, start=offset):
        stripped = line.rstrip('\n')
        output.append(f"{i}: {stripped}")
    result = "\n".join(output)
    if len(lines) > limit + offset - 1:
        next_off = offset + limit
        return f"{result}\n\n... (next offset: {next_off}, total lines: {len(lines)})"
    return result
