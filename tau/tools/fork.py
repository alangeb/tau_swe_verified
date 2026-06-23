from __future__ import annotations

from tools import ToolMetadata

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agent_subagent import NESTING_DEPTH_THRESHOLD, invoke_fork_sync

if TYPE_CHECKING:
    from agent_core import TauBot


# ── Tool metadata ────────────────────────────────────────────────────────────

metadata = ToolMetadata(
    name="fork",
    description=(
        'Fork = "Clone yourself with all memories". Spawn a subagent inheriting your '
        "entire conversation history, tools, and skills. "
        "SYNCHRONOUS — this call BLOCKS until the fork completes and returns its result (fork does not run in background). "
        "For TRUE background/async work, use background_* tools to start a separate tau.py process. "
        "Use when: task needs your complete knowledge, outcome matters more than process. "
        "Avoid when: isolation needed (use subagent instead), very large context. "
        "A fork is synchronous — it helps preserve context capacity."
    ),
    timeout=86400,
    max_size=262144,
)

_last_call: str | None = None  # Double-call confirmation state


# ── Args schema ──────────────────────────────────────────────────────────────

@dataclass
class Args:
    task: str = field(
        metadata={
            "description": "The comprehensive but concise task description for the fork subagent to execute (must be very explicite)."
        }
    )



# ── Execution ────────────────────────────────────────────────────────────────

def run(task: str, agent: "TauBot", tool_call_id: str | None) -> str:
    global _last_call

    if agent is None:
        return "ERROR: fork tool must be invoked via TauBot (agent parameter is None)"

    nesting_count = agent.nesting_count

    if nesting_count >= NESTING_DEPTH_THRESHOLD:
        return f"ERROR: Maximum nesting depth (2) exceeded. Cannot spawn subagent at depth {nesting_count}."

    if nesting_count >= 1 and task != _last_call:
        _last_call = task
        return (
            f"⚠️  WARNING: You are already running inside a subagent/fork (depth {nesting_count}).\n"
            f"\n"
            f"Spawning another nested subagent has been BLOCKED.\n"
            f"\n"
            f"If you need to spawn another subagent, issue the EXACT SAME fork command again.\n"
            f"To execute this command, issue the EXACT SAME command again, in-sequence, one by one (don't try todo 2 or more things in parallel).\n"
        )

    _last_call = None

    return invoke_fork_sync(
        prompt=task,
        parent_context=agent.context,
        parent_agent=agent,
        nesting_count=nesting_count,
        tool_call_id=tool_call_id,
        tool_filter=None,  # Children always get unrestricted tool access.
    )
