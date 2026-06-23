#!/usr/bin/env python3
"""status.py — SWE-bench Verified pipeline status reporter.

Reads results.jsonl and artifacts/*/meta.json for combined view.

Usage:
    python3 status.py              # Full report (default)
    python3 status.py summary      # One-line summary
    python3 status.py by_repo      # Grouped by repository
"""
import argparse
import json
import re
from pathlib import Path
from typing import Any

from config import BASE_DIR, DATASET_NAME, DATASET_SPLIT, RESULTS_FILE

# ANSI color codes
GREEN = "\033[92m"  # success / resolved
RED = "\033[91m"    # failure
YELLOW = "\033[93m" # warnings / patch issues
CYAN = "\033[96m"  # neutral / other
RESET = "\033[0m"  # reset
BOLD = "\033[1m"   # bold

# Strip ANSI escape codes for length calculations
ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return ANSI_RE.sub("", text)


MARK = {
    "resolved": "+",
    "pass": "+",
    "eval_passed": "+",
    "failed": "-",
    "fail": "-",
    "eval_failed": "-",
    "patch_apply_failed": "!",
    "patch_failed": "!",
    "reset_failed": "!",
    "reset_error": "!",
    "eval_error": "E",
    "no_base_commit": "E",
    "no_patch": "!",
    "timeout": "T",
    "unknown": "?",
}

LABEL = {
    "resolved": "RESOLVED",
    "pass": "PASS",
    "eval_passed": "EVAL_PASSED",
    "failed": "FAILED",
    "fail": "FAILED",
    "eval_failed": "EVAL_FAIL",
    "patch_apply_failed": "PATCH_APPLY_FAIL",
    "patch_failed": "PATCH_FAIL",
    "reset_failed": "RESET_FAIL",
    "reset_error": "RESET_ERROR",
    "eval_error": "EVAL_ERROR",
    "no_base_commit": "NO_BASE_COMMIT",
    "no_patch": "NO_PATCH",
    "timeout": "TIMEOUT",
    "unknown": "UNKNOWN",
}


def fmt_duration(seconds: float | None) -> str:
    """Format a duration in seconds to a human-readable string.

    Examples:
        30 → "30s"
        120 → "2.0m"
        7200 → "2.0h"
    """
    if not seconds or seconds == 0:
        return "N/A"
    s = float(seconds)
    if s < 60:
        return f"{s:.0f}s"
    elif s < 3600:
        return f"{s / 60:.1f}m"
    else:
        return f"{s / 3600:.1f}h"


def fmt_patch(size: int | None) -> str:
    """Format a patch size in bytes to a human-readable string.

    Examples:
        500 → "500B"
        2048 → "2.0KB"
        1048576 → "1.0MB"
    """
    if not size or size == 0:
        return "0B"
    s = int(size)
    if s < 1024:
        return f"{s}B"
    elif s < 1024 * 1024:
        return f"{s / 1024:.1f}KB"
    else:
        return f"{s / (1024 * 1024):.1f}MB"


def get_repo(instance_id: str) -> str:
    """Extract repository name from instance ID.

    'django__django-12345' → 'django'
    'unknown' → 'unknown'
    """
    return instance_id.split("__")[0] if "__" in instance_id else "unknown"


def normalize_status(fix_status: str | None, eval_status: str | None) -> str:
    """Determine overall status from fix + eval results.

    Priority: patch_failed > eval_status > fix_status > unknown.
    """
    if fix_status == "patch_failed":
        return "patch_failed"
    if eval_status in ("resolved", "pass", "eval_passed"):
        return "resolved"
    if eval_status in ("failed", "eval_failed", "fail"):
        return "failed"
    if eval_status:
        return eval_status
    return fix_status or "unknown"


def load_results() -> list[dict[str, Any]]:
    """Load results from results.jsonl, skipping malformed lines."""
    results: list[dict[str, Any]] = []
    results_file = BASE_DIR / RESULTS_FILE.name if isinstance(RESULTS_FILE, Path) else RESULTS_FILE
    if isinstance(results_file, str):
        results_file = Path(results_file)
    if not results_file.exists():
        return results
    with open(results_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return results


def _build_index_map() -> dict[str, int]:
    """Build a mapping from instance_id to dataset index for sorting."""
    idx_map: dict[str, int] = {}
    try:
        from datasets import load_dataset
        ds = load_dataset(DATASET_NAME, split=DATASET_SPLIT)
        for i, row in enumerate(ds):
            iid = row.get("instance_id", "")
            if iid:
                idx_map[iid] = i
    except Exception:
        # Fallback: if dataset can't be loaded, return empty map (no sorting)
        pass
    return idx_map


def deduplicate(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the last result per instance_id."""
    seen: dict[str, dict[str, Any]] = {}
    for r in results:
        iid = r.get("instance_id", "")
        seen[iid] = r
    return list(seen.values())


def _color_for_eval_status(status: str) -> str:
    """Return ANSI colour for the EVAL column (mark + eval label)."""
    if status in ("resolved", "pass", "eval_passed"):
        return GREEN
    # Everything that is NOT resolved/passed is bad in the EVAL column
    return RED


def _color_for_fix_status(status: str) -> str:
    """Return ANSI colour for the FIX column."""
    if status in ("resolved", "pass"):
        return CYAN
    if status in ("failed", "fail", "eval_failed"):
        return RED
    if status in ("patch_apply_failed", "patch_failed", "reset_failed", "reset_error"):
        return YELLOW
    return CYAN


def _c(color: str, text: str) -> str:
    """Wrap text in ANSI color codes."""
    return f"{color}{text}{RESET}"


def _fmt_col(width: int, text: str, color: str, align: str = "^") -> str:
    """Format a column: pad to width based on VISUAL length (ignoring ANSI codes), then apply color.

    This ensures columns align correctly despite ANSI escape codes being invisible.
    """
    # Pad the plain text to the desired width
    padded = f"{text:{align}{width}}"
    # Apply color to the entire padded string
    return _c(color, padded)


def cmd_summary(args: argparse.Namespace) -> None:
    """Print a one-line summary of results."""
    results = deduplicate(load_results())
    idx_map = _build_index_map()
    results.sort(key=lambda r: idx_map.get(r.get("instance_id", ""), 9999))
    counts = {"resolved": 0, "failed": 0, "patch_apply_failed": 0, "patch_failed": 0, "other": 0}
    for r in results:
        status = normalize_status(r.get("fix_status"), r.get("eval_status"))
        if status in ("resolved", "failed", "patch_apply_failed", "patch_failed"):
            counts[status] += 1
        else:
            counts["other"] += 1
    total = len(results)
    parts = [
        f"Total: {total}",
        f"{_c(GREEN, '+' + str(counts['resolved']))}",
        f"{_c(RED, '-' + str(counts['failed']))}",
        f"{_c(YELLOW, '!' + str(counts['patch_apply_failed'] + counts['patch_failed']))}",
    ]
    if counts["other"]:
        parts.append(f"{_c(CYAN, '?' + str(counts['other']))}")
    print(" | ".join(parts))


def cmd_full(args: argparse.Namespace) -> None:
    """Print a full status report with per-instance details."""
    results = deduplicate(load_results())
    # Sort by dataset index so row numbers match --start N
    idx_map = _build_index_map()
    results.sort(key=lambda r: idx_map.get(r.get("instance_id", ""), 9999))
    counts = {"resolved": 0, "failed": 0, "patch_apply_failed": 0, "patch_failed": 0, "other": 0}

    # Column widths (visual characters, not including ANSI codes)
    W = {"mark": 1, "idx": 3, "iid": 42, "fix": 14, "eval": 18, "duration": 10, "patch": 8}

    # Build header with centered alignment
    header = (
        f"{'M':^{W['mark']}} {'#':^{W['idx']}} {'INSTANCE':<{W['iid']}} "
        f"{'FIX':^{W['fix']}} {'EVAL':^{W['eval']}} {'DURATION':>{W['duration']}} {'PATCH':^{W['patch']}}"
    )
    sep = "-" * len(header)

    print(sep)
    print(f"SWE-bench Verified — PIPELINE STATUS")
    print(sep)
    print(header)
    print(sep)

    total_duration = 0
    for i, r in enumerate(results, 1):
        iid = r.get("instance_id", "unknown")
        ds_idx = idx_map.get(iid, "?")
        fix = r.get("fix_status", "?")
        eval_s = r.get("eval_status") or "N/A"
        duration = r.get("duration_seconds", 0) or r.get("total_duration", 0) or 0
        patch = r.get("patch_size", 0) or 0
        status = normalize_status(fix, eval_s)

        if status in counts:
            counts[status] += 1
        else:
            counts["other"] += 1
        total_duration += duration

        mark = MARK.get(eval_s) or MARK.get(status, "?")
        label = LABEL.get(eval_s) or LABEL.get(status, status)
        fix_label = LABEL.get(fix, fix)

        # Determine colours — separate functions for FIX vs EVAL columns
        eval_color = _color_for_eval_status(status)
        fix_color = _color_for_fix_status(fix)

        # Format each column: pad plain text to width, then apply colour
        mark_colored = _fmt_col(W["mark"], mark, eval_color, "^")
        idx_str = str(ds_idx) if ds_idx != "?" else "?"
        idx_colored = _fmt_col(W["idx"], idx_str, RESET, "^")
        iid_colored = _fmt_col(W["iid"], iid, RESET, "<")
        fix_colored = _fmt_col(W["fix"], fix_label, fix_color, "^")
        eval_colored = _fmt_col(W["eval"], label, eval_color, "^")
        dur_str = fmt_duration(duration)
        dur_colored = _fmt_col(W["duration"], dur_str, RESET, ">")
        patch_str = fmt_patch(patch)
        patch_colored = _fmt_col(W["patch"], patch_str, RESET, "^")

        line = f"{mark_colored} {idx_colored} {iid_colored} {fix_colored} {eval_colored} {dur_colored} {patch_colored}"
        print(line)

    print(sep)
    print("SUMMARY")
    print(sep)
    total = len(results)
    resolved = counts["resolved"]
    failed = counts["failed"]
    patch_fail = counts["patch_apply_failed"] + counts["patch_failed"]
    other = counts["other"]

    # Build summary with aligned columns.
    # Strategy: format the plain-text line first (correct visual alignment),
    # then colour only the leading symbol so values stay vertically aligned.
    def _sline(prefix: str, label: str, value: str, color: str) -> str:
        """Return a summary line with aligned columns.

        Builds the line as plain text for correct padding, then wraps
        only the prefix symbol in ANSI colour codes.
        """
        line = f"  {prefix} {label:<23}{value:>6}"
        # Replace the prefix (position 2, length 1) with its coloured version.
        return line[:2] + _c(color, prefix) + line[3:]

    print(_sline(" ", "Total instances:", str(total), RESET))
    print(_sline("+", "Resolved:", str(resolved), GREEN))
    print(_sline("-", "Failed (eval):", str(failed), RED))
    print(_sline("!", "Patch issues:", str(patch_fail), YELLOW))
    if other > 0:
        print(_sline("?", "Other:", str(other), CYAN))
    if total > 0:
        rate = resolved * 100 / total
        avg = total_duration / total
        rate_color = GREEN if rate >= 50 else (YELLOW if rate > 0 else RED)
        print(_sline(" ", "Success rate:", f"{rate:.1f}%", rate_color))
        print(_sline(" ", "Avg duration:", fmt_duration(avg), RESET))
        print(_sline(" ", "Total time:", fmt_duration(total_duration), RESET))
    print(sep)


def cmd_by_repo(args: argparse.Namespace) -> None:
    """Print results grouped by repository."""
    results = deduplicate(load_results())
    idx_map = _build_index_map()
    results.sort(key=lambda r: idx_map.get(r.get("instance_id", ""), 9999))
    repos = {}
    for r in results:
        iid = r.get("instance_id", "")
        repo = get_repo(iid)
        repos.setdefault(repo, []).append(r)

    print(f"\nResults by repository ({len(repos)} repos):\n")
    for repo in sorted(repos):
        items = repos[repo]
        resolved = sum(1 for m in items if normalize_status(m.get("fix_status"), m.get("eval_status")) == "resolved")
        total = len(items)
        rate = resolved / total * 100 if total > 0 else 0
        color = GREEN if rate >= 50 else (YELLOW if rate > 0 else RED)
        print(f"  {_c(color, f'{repo:<30} {resolved:3d}/{total:3d}  ({rate:.1f}%)')}")


def main() -> None:
    """CLI entry point for status reporting."""
    parser = argparse.ArgumentParser(description="SWE-bench Verified pipeline status reporter")
    parser.add_argument("mode", nargs="?", default="full", choices=["full", "summary", "by_repo"], help="Report mode")
    args = parser.parse_args()
    commands = {"full": cmd_full, "summary": cmd_summary, "by_repo": cmd_by_repo}
    commands[args.mode](args)


if __name__ == "__main__":
    main()
