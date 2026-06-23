"""Subagent and Fork Spawning Module for TauBot.

Delegation patterns:
- **Subagent**: Isolated agent with fresh context (blank slate).
- **Fork**: Agent inheriting parent's full conversation history.

Both create in-process TauBot instances sharing parent config, tools, and skills.

Nesting restrictions prevent unbounded recursion:
  Level 0-1: Full capabilities.  Level 2+: No further subagents/forks.
"""

import copy
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from agent_config import Config
from agent_context import TauContext

if TYPE_CHECKING:
    from agent_core import TauBot
    from agent_tool_filter import ToolFilter

logger = logging.getLogger(__name__)

__all__ = [
    "NESTING_DEPTH_THRESHOLD",
    "invoke_subagent_sync",
    "invoke_fork_sync",
]

# Default nesting depth threshold (exported for backward compatibility).
NESTING_DEPTH_THRESHOLD = 2


# ── Fork isolation helpers ─────────────────────────────────────────────────


def _create_fork_isolation() -> tuple[str, Path]:
    """Return (fork_id, temp_dir) for resource isolation."""
    fork_id = str(uuid.uuid4())[:8]
    temp_dir = Path(tempfile.mkdtemp(prefix=f"tau-fork-{os.getpid()}-{fork_id}-"))
    return fork_id, temp_dir


def _cleanup_fork_isolation(temp_dir: Path) -> None:
    """Remove fork's isolated temp directory (best-effort)."""
    try:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
    except OSError:
        pass


def _make_isolated_path(base_path: Path, fork_id: str) -> Path:
    """Append fork_id to a file path for isolation."""
    stem = base_path.stem
    suffix = base_path.suffix
    parent = base_path.parent
    return parent / f"{stem}-{fork_id}{suffix}"


# ── Subagent creation ────────────────────────────────────────────────────


def _create_subagent(
    parent_agent: "TauBot",
    config: Config | None,
    tool_filter: "ToolFilter | None",
) -> "TauBot":
    """Create a new TauBot inheriting configuration from parent."""
    from agent_core import TauBot

    return TauBot(
        config=config if config is not None else getattr(parent_agent, "config", None),
        llm_group_name=parent_agent.current_group_name,
        max_context_tokens=parent_agent.max_context_tokens,
        tool_filter=tool_filter,
    )


# ── Nesting restriction text ─────────────────────────────────────────────


def _nesting_restriction_text(nesting_count: int) -> str:
    """Restriction text appended to system prompts near nesting limit."""
    return (
        f"[[You are already a subagent/fork running at nesting level "
        f"{nesting_count}: You MUST NOT use the fork tool, "
        f"You MUST NOT use the subagent tool, "
        f"You MUST NOT do further forks/subagents]]"
    )


def _nesting_restriction_suffix(nesting_count: int) -> str:
    """Restriction suffix appended to fork context near nesting limit."""
    return (
        f"\n\n[[You are running at nesting level {nesting_count}: "
        f"You MUST NOT use the fork tool, You MUST NOT use the subagent tool, "
        f"You MUST NOT do further forks/subagents]]"
    )


# ── Public API ───────────────────────────────────────────────────────────


def invoke_subagent_sync(
    prompt: str,
    system_prompt: str,
    parent_agent: "TauBot",
    nesting_count: int = 0,
    tool_filter: "ToolFilter | None" = None,
    config: Config | None = None,
    nesting_threshold: int = 2,
) -> str:
    """Spawn an isolated subagent with a fresh context (no parent history).

    The subagent inherits tools and skills but starts with a blank conversation.
    Nesting restrictions are applied when depth exceeds the threshold.
    """
    subagent = _create_subagent(parent_agent, config, tool_filter)
    subagent.nesting_count = nesting_count + 1
    subagent.original_task = prompt

    if nesting_count >= nesting_threshold - 1:
        system_prompt += _nesting_restriction_text(nesting_count)

    subagent.context = TauContext([{"role": "system", "content": system_prompt}])

    # Track subagent lifecycle in audit log.
    parent_agent._session.audit_writer.subagent_start(prompt)
    start_time = time.monotonic()
    try:
        return subagent.invoke_with_tools(prompt)
    finally:
        duration_s = time.monotonic() - start_time
        parent_agent._session.audit_writer.subagent_end(duration_s)


def invoke_fork_sync(
    prompt: str,
    parent_context: TauContext,
    parent_agent: "TauBot",
    nesting_count: int = 0,
    tool_call_id: str | None = None,
    tool_filter: "ToolFilter | None" = None,
    config: Config | None = None,
    nesting_threshold: int = 2,
) -> str:
    """Spawn a fork inheriting a deep copy of the parent context.

    The fork receives the full parent conversation history. Pending tool calls
    are marked PENDING; the fork's own call is marked FORK as responder.
    Nesting restrictions are applied when depth exceeds the threshold.
    """
    fork_id = ""
    temp_dir: Path | None = None
    try:
        fork_id, temp_dir = _create_fork_isolation()

        # Pass parent audit file path and nesting level to fork for unified audit logging.
        # Fork reads these via os.getenv() during AuditWriter lazy init.
        # We set them briefly and clean up in finally to limit subprocess inheritance window.
        parent_audit_file = str(parent_agent._session.audit_file)
        os.environ["TAU_PARENT_AUDIT_FILE"] = parent_audit_file
        os.environ["TAU_FORK_NESTING"] = str(nesting_count + 1)

        fork = _create_subagent(parent_agent, config, tool_filter)
        fork.nesting_count = nesting_count + 1
        fork.original_task = prompt

        fork.context = TauContext(copy.deepcopy(parent_context.to_list()))

        nesting_suffix = ""
        if fork.nesting_count >= nesting_threshold - 1:
            nesting_suffix = _nesting_restriction_suffix(fork.nesting_count)

        # Only pass fork_tool_call_id if it exists in the parent's pending tool calls.
        # The fork context is a deep copy of parent messages — if the tool_call_id
        # is not in the parent's pending calls, it won't be found in the fork's context,
        # triggering a spurious "no pending calls to mark" validation warning.
        effective_tool_call_id = None
        if tool_call_id is not None:
            parent_pending = parent_context.get_pending_tool_ids()
            if tool_call_id in parent_pending:
                effective_tool_call_id = tool_call_id
            else:
                logger.debug(
                    "fork_tool_call_id '%s' not in parent pending calls %s — "
                    "passing None to avoid spurious warning",
                    tool_call_id, sorted(parent_pending),
                )

        fork.context.prepare_fork_context(
            task=(
                "You successfully forked! You are the fork now. "
                "Work exactly on this TASK (do not work on other things - they will be taken care of), "
                "then end your turn with the end_turn tool call. "
                "TASK: {prompt}"
            ),
            fork_tool_call_id=effective_tool_call_id,
            nesting_suffix=nesting_suffix,
        )

        # Track fork lifecycle in audit log.
        parent_agent._session.audit_writer.fork_start(prompt)
        start_time = time.monotonic()
        result = fork.invoke_with_tools(f"{prompt}")
        duration_s = time.monotonic() - start_time
        parent_agent._session.audit_writer.fork_end(duration_s)

        parent_context.clear_fork_metadata()

        return result
    finally:
        if temp_dir is not None:
            _cleanup_fork_isolation(temp_dir)
        # Clean up audit env vars so they don't leak to subsequent operations.
        os.environ.pop("TAU_PARENT_AUDIT_FILE", None)
        os.environ.pop("TAU_FORK_NESTING", None)
