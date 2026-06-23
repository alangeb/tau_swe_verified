from __future__ import annotations

from tools import ToolMetadata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

from dataclasses import dataclass, field

from agent_subagent import NESTING_DEPTH_THRESHOLD, invoke_subagent_sync

# ── Tool metadata ──

metadata = ToolMetadata(
    name="subagent",
    description=(
        'Subagent = "Delegate to a fresh intern with these instructions". Spawn a subagent '
        "with blank slate context — knows ONLY what you tell it in the task. "
        "SYNCHRONOUS — this call BLOCKS until the subagent completes and returns its result. "
        "The subagent does NOT run in the background. It does NOT 'report back later'. "
        "When you call subagent, execution pauses here and waits for the subagent to finish. "
        "For TRUE background/async work, use background_* tools to start a separate tau.py process. "
        "Use when: complete isolation needed, task is very well-defined. "
        "Avoid when: task needs your knowledge/context (use fork instead). "
        "A subagent is synchronous — it helps preserve context capacity."
    ),
    timeout=86400,
    max_size=262144,
)

_last_call: str | None = None  # Double-call confirmation state


# ── Args schema ──

@dataclass
class Args:
    task: str = field(
        metadata={
            "description": "The complete task description for the subagent to execute."
        }
    )



# ── Execution ──

def run(task: str, agent: "TauBot", tool_call_id: str | None = None) -> str:
    """Spawn an isolated subagent with blank-slate context."""
    global _last_call

    if agent is None:
        return "ERROR: subagent tool must be invoked via TauBot (agent parameter is None)"

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
            f"If you need to spawn another subagent, issue the EXACT SAME subagent command again.\n"
            f"To execute this command, issue the EXACT SAME command again, in-sequence, one by one (don't try todo 2 or more things in parallel).\n"
        )

    _last_call = None

    return invoke_subagent_sync(
        prompt=task,
        system_prompt=agent.context.get_system(),
        parent_agent=agent,
        nesting_count=nesting_count,
        tool_filter=None,  # Children always get unrestricted tool access.
    )
