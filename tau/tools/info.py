from __future__ import annotations

from tools import ToolMetadata

import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ──

metadata = ToolMetadata(
    name="info",
    description=(
        "Return agent information: working directory, PID, model config, context usage, "
        "token stats, and execution context (nesting level, subagent/fork mode)."
    ),
    max_size=8192,
    timeout=10,
)


# ── Args schema ──

@dataclass
class Args:
    """No arguments required."""



# ── Git helpers ──

def _git_run(args: list[str]) -> str:
    result = subprocess.run(
        ["git"] + args, capture_output=True, text=True, timeout=5, start_new_session=True
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _get_git_info() -> list[str]:
    lines = []
    try:
        project_root = _git_run(["rev-parse", "--show-toplevel"])
        lines.append(f"Project Root: {project_root}")

        git_dir = _git_run(["rev-parse", "--git-dir"])
        git_common_dir = _git_run(["rev-parse", "--git-common-dir"])

        branch = _git_run(["rev-parse", "--abbrev-ref", "HEAD"])
        commit = _git_run(["rev-parse", "HEAD"])[:8]

        if git_common_dir != git_dir:
            lines.append("Git Mode: Worktree")
            lines.append(f"Worktree Root: {project_root}")
            lines.append(f"Master Root: {os.path.dirname(git_common_dir)}")
        else:
            lines.append("Git Mode: Standard Repository")
            lines.append(f"Git Directory: {git_dir}")
        lines.append(f"Branch: {branch}")
        lines.append(f"Commit: {commit}")

    except Exception as e:
        lines.append(f"Git Info: unavailable ({e})")

    return lines


# ── Execution ──

def run(agent: TauBot, tool_call_id: str | None) -> str:
    def safe(label: str, value_fn) -> str:
        try:
            return f"{label}: {value_fn()}"
        except Exception:
            return f"{label}: unavailable"

    lines = [
        "=== Agent Information ===",
        safe("Starting CWD", lambda: agent.original_cwd),
        safe("Current CWD", os.getcwd),
        safe("PID", os.getpid),
        safe("Parent PID", os.getppid),
        "",
        "=== Git Repository ===",
    ]
    lines.extend(_get_git_info())

    lines.extend([
        "",
        "=== Model Configuration ===",
        safe("Model", lambda: agent.model_name),
        safe("API Base", lambda: agent.base_url),
        safe("Context Limit", lambda: f"{agent.max_context_tokens} tokens"),
        "",
        "=== Context Usage ===",
    ])

    try:
        lines.append(f"Context Size: {agent.context.estimate_tokens()} bytes")
    except Exception:
        lines.append("Context Size: unavailable")

    try:
        tokens, percentage, byte_count, exact = agent.context.get_usage_stats(
            agent.max_context_tokens, agent._session.last_exact_context_tokens
        )
        note = "(exact)" if exact else "(estimated)"
        lines.append(
            f"Token Usage: {tokens} / {agent.max_context_tokens} tokens ({percentage * 100:.1f}% {note})"
        )
    except Exception:
        lines.append("Token Usage: unavailable")

    lines.extend(["", "=== Agent Execution Context ==="])

    try:
        nesting_level = agent.nesting_count
        lines.append(f"Nesting Level: {nesting_level}")
        if nesting_level > 0:
            has_inherited = len(agent.context) > 1 or (
                len(agent.context) == 1 and agent.context[0].get("role") != "system"
            )
            mode = (
                "fork (inherits parent context)"
                if has_inherited
                else "subagent (isolated context)"
            )
            lines.append(f"Execution Mode: {mode}")
        else:
            lines.append("Execution Mode: main agent")
    except Exception:
        lines.append("Agent Execution Context: unavailable")

    return "\n".join(lines)
