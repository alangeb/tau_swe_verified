from __future__ import annotations

from tools import ToolMetadata

import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .lib.sandbox import get_allowed_paths, validate_path
if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ──

metadata = ToolMetadata(
    name="ls",
    description=(
        "List directory contents with optional formatting. "
        "Supports long format (-l), hidden files (-a), recursive (-R), "
        "reverse order, sorting by name/time/size/extension, and single-column output."
    ),
    aliases_cmd=["dir", "list"],
    max_size=32768,
    timeout=30,
)


# ── Args schema ──

@dataclass
class Args:
    path: str = field(
        default=".",
        metadata={"description": "Directory path to list (default: current directory)"},
    )
    long: bool = field(
        default=False,
        metadata={"description": "Long format with permissions, size, date (like -l)"},
    )
    all: bool = field(
        default=False,
        metadata={"description": "Include hidden entries starting with '.' (like -a)"},
    )
    recursive: bool = field(
        default=False,
        metadata={"description": "List subdirectories recursively (like -R)"},
    )
    reverse: bool = field(
        default=False,
        metadata={"description": "Reverse sort order (like -r)"},
    )
    sort: str = field(
        default="name",
        metadata={
            "description": "Sort by: 'name' (default), 'time' (modification time), 'size', or 'extension'"
        },
    )
    one_column: bool = field(
        default=False,
        metadata={"description": "Output one entry per line (like -1)"},
    )



# ── Execution ──

def run(
    path: str = ".",
    agent: "TauBot" = None,
    tool_call_id: str | None = None,
    long: bool = False,
    all: bool = False,
    recursive: bool = False,
    reverse: bool = False,
    sort: str = "name",
    one_column: bool = False,
) -> str:
    target_path, err = validate_path(path, allowed_paths=get_allowed_paths(agent))
    if err:
        return err

    if not target_path.exists():
        return f"ERROR: Path not found: {path}"
    if not target_path.is_dir():
        return f"ERROR: Not a directory: {path}"

    try:
        cmd = ["ls"]
        flags = []

        if long:
            flags.append("-l")
        if all:
            flags.append("-a")
        if recursive:
            flags.append("-R")
        if reverse:
            flags.append("-r")
        if one_column:
            flags.append("-1")

        if sort == "time":
            flags.append("-t")
        elif sort == "size":
            flags.append("-S")
        elif sort == "extension":
            flags.append("-X")

        cmd.extend(flags)
        cmd.append(str(target_path))

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, start_new_session=True)
        if result.returncode == 0:
            return result.stdout.strip()
        return f"ERROR: {result.stderr.strip()}"

    except subprocess.TimeoutExpired:
        return "ERROR: ls timed out after 30 seconds."
    except Exception as e:
        return f"ERROR: {e}"
