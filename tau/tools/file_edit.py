"""Edit a file by replacing exact text. old_string must match exactly."""

from __future__ import annotations

from tools import ToolMetadata

import difflib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

from .lib.sandbox import check_path, get_allowed_paths
# ── Constants ─────────────────────────────────────────────────────

SIMILARITY_CUTOFF = 0.3  # Minimum SequenceMatcher ratio to consider a line "similar"
FUZZY_MATCH_THRESHOLD = 0.85  # High-confidence threshold for auto-matching


# ── Tool interface ────────────────────────────────────────────────

metadata = ToolMetadata(
    name="file_edit",
    description=(
        "Edit a file by replacing exact text. old_string must match exactly. "
        "Replaces the first occurrence of old_string with new_string."
    ),
    aliases_cmd=["edit", "edit_file"],
    aliases_arg={"file": "file_path"},
    max_size=8192,
)


# ── Args schema ───────────────────────────────────────────────────

@dataclass
class Args:
    file_path: str = field(metadata={"description": "Absolute path to file"})
    old_string: str = field(metadata={"description": "Exact text to replace"})
    new_string: str = field(metadata={"description": "Replacement text"})


# ── Helpers ───────────────────────────────────────────────────────

def _find_best_match(content: str, old_string: str) -> tuple[str | None, float, int, int]:
    """Find the best matching substring in content for old_string.

    Returns (matched_text, ratio, start_line, end_line) or (None, 0.0, 0, 0) if no match.
    Searches line-by-line for single-line old_string, or multi-line for multi-line old_string.
    """
    lines = content.split("\n")
    old_lines = old_string.split("\n")

    # Single-line old_string: search each line
    if len(old_lines) == 1:
        best_ratio = 0.0
        best_line = None
        best_line_num = 0
        old_lower = old_lines[0].lower()

        for i, line in enumerate(lines):
            low = line.lower().strip()
            if old_lower and low:
                matcher = difflib.SequenceMatcher(None, old_lower, low)
                ratio = matcher.ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_line = line
                    best_line_num = i + 1  # 1-indexed

        if best_ratio >= FUZZY_MATCH_THRESHOLD and best_line is not None:
            return (best_line, best_ratio, best_line_num, best_line_num)

    # Multi-line old_string: search consecutive line blocks
    elif len(old_lines) <= len(lines):
        best_ratio = 0.0
        best_block = None
        best_start = 0

        for i in range(len(lines) - len(old_lines) + 1):
            block = "\n".join(lines[i:i + len(old_lines)])
            matcher = difflib.SequenceMatcher(None, old_string.lower(), block.lower())
            ratio = matcher.ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_block = block
                best_start = i + 1  # 1-indexed

        if best_ratio >= FUZZY_MATCH_THRESHOLD and best_block is not None:
            return (best_block, best_ratio, best_start, best_start + len(old_lines) - 1)

    return (None, 0.0, 0, 0)


def _build_error_message(content: str, old_string: str) -> str:
    """Build a detailed error message when old_string is not found."""
    similar_lines: list[tuple[int, str, float]] = []
    lines = content.split("\n")
    old_first = (
        old_string.split("\n")[0].lower()
        if "\n" in old_string
        else old_string.lower()
    )

    for i, line in enumerate(lines):
        low = line.lower().strip()
        if old_first and low:
            matcher = difflib.SequenceMatcher(None, old_first, low)
            ratio = matcher.ratio()
            if ratio > SIMILARITY_CUTOFF:
                similar_lines.append((i + 1, line, ratio))

    similar_lines.sort(key=lambda x: x[2], reverse=True)

    msg = "ERROR: old_string not found in file.\n\n"
    msg += "💡 Tip: Use file_read to check the current file content before editing.\n\n"

    if similar_lines:
        best_line_num, best_line, best_ratio = similar_lines[0]
        msg += f"Closest match (line {best_line_num}, {best_ratio:.0%} similar):\n"

        # Show context: 5 lines before and after the best match
        best_0idx = best_line_num - 1
        start = max(0, best_0idx - 5)
        end = min(len(lines), best_0idx + 6)

        context_lines = []
        for i in range(start, end):
            marker = "→ " if i == best_0idx else "  "
            context_lines.append(f"{marker}{i+1}: {lines[i]}")

        msg += "\n".join(context_lines)

        if len(similar_lines) > 1:
            msg += "\n\nOther similar lines:\n" + "\n".join(
                f"  {num}: {line}" for num, line, _ in similar_lines[1:5]
            )
            if len(similar_lines) > 5:
                msg += f"\n  ...and {len(similar_lines)-5} more similar lines"
    else:
        msg += "No similar content found. The file may have changed significantly."

    return msg


# ── Execution ─────────────────────────────────────────────────────

def run(
    file_path: str, old_string: str, new_string: str,
    agent: "TauBot", tool_call_id: str | None,
) -> str:
    """Replace exact text in a file."""
    if not file_path:
        return "ERROR: file_path is required."
    if not old_string:
        return "ERROR: old_string is required."
    if not new_string:
        return "ERROR: new_string is required."
    if old_string == new_string:
        return "ERROR: old_string and new_string are identical."

    path, err = check_path(metadata.name, agent, file_path, allowed_paths=get_allowed_paths(agent), write_operation=True)
    if err:
        return err

    if path.is_dir():
        return f"ERROR: '{file_path}' is a directory."

    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        return "ERROR: File is not a text file."
    except Exception as e:
        return f"ERROR: {e}"

    # Try exact match first
    if old_string in content:
        actual_match = old_string
    else:
        # Try fuzzy match with high confidence threshold
        matched_text, match_ratio, start_line, end_line = _find_best_match(content, old_string)
        if matched_text is not None:
            actual_match = matched_text
        else:
            return _build_error_message(content, old_string)

    # Replace only the first occurrence
    new_content = content.replace(actual_match, new_string, 1)

    try:
        old_lines = content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = "".join(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=str(path),
                tofile=str(path),
                lineterm="",
            )
        )
    except Exception:
        diff = "(diff failed)"

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        return f"ERROR: {e}"

    added = len(new_lines) - len(old_lines)
    result = f"Edit applied: {file_path}\nReplaced 1 occurrence\nTotal line # changed: {added:+d}\n\nDiff:\n{diff}\n"

    # Warn if fuzzy match was used
    if actual_match != old_string:
        result += f"⚠️  WARNING: Used fuzzy match ({match_ratio:.0%} similar) instead of exact match.\n"
        result += f"   Matched text: {actual_match!r}\n"
        result += f"   Provided text: {old_string!r}\n"

    return result
