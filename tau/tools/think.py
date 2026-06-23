from __future__ import annotations

import time

from tools import ToolMetadata

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

from dataclasses import dataclass, field

from agent_console import error
from agent_console_primitives import format_duration_ms
from agent_subagent import invoke_fork_sync
from agent_tool_filter import ToolFilter

# ── Tool metadata ──

metadata = ToolMetadata(
    name="think",
    description="""
Pure reasoning pass on inherited context. Lightweight — context copy (~1MB) is
negligible compared to single-token decoding, and keeps prefix cache intact.
The fork analyzes the conversation and returns structured analysis to its parent.

Use when stuck in a loop, when assumptions changed, or for explicit planning.
The fork has NO tools except end_turn — it is a pure thinker, not an actor.

DELEGATION HIERARCHY:
1. Internal reasoning → Always start here. Use your built-in reasoning for all tasks.
2. Fork → Use for complex tasks that benefit from a dedicated analysis pass.
3. Think tool → Use ONLY when internal reasoning fails and you need a dedicated
   analytical pass before acting.

USE ONLY when:
- You are genuinely stuck in meta-analysis loops (repeatedly asking yourself
  "what should I do?" without making progress)
- The task or assumptions have changed mid-execution in a way that breaks
  your current plan
- You see unexpected results that require deep re-analysis before proceeding
- You have a complex, multi-part task that benefits from explicit planning

NEVER use:
- As a first step before starting a task — just begin working immediately
- For routine analysis — your own reasoning is sufficient
- As a substitute for thinking — this tool spawns a fork, it is not free

NOTE: The system may automatically invoke think for loop detection and periodic
reflection. This is separate from proactive use — you do not control it.

Remember: you delegate the thinking. You do not do it yourself.
""",
    timeout=3600,
    max_size=65536,
)

# ── Constants ──

THINK_TOOL_ALLOWLIST = frozenset({"end_turn"})

# Pre-computed: sorted, comma-separated list of permitted tools.
_ALLOWED_TOOLS_STR = ", ".join(sorted(THINK_TOOL_ALLOWLIST))

# Pre-computed: invariant header for think-mode prompts.
_THINK_HEADER = (
    "=== THINK MODE: PURE REASONING ===\n\n"
    "You are a THINKER — a philosopher, not an actor.\n"
    "You analyze, reason, and plan. You CANNOT take actions.\n\n"
    f"PERMITTED TOOLS: {_ALLOWED_TOOLS_STR}\n"
    "You MUST NOT use any tool not listed above.\n\n"
    "RULES:\n"
    "- You MAY NOT call any tool except end_turn.\n"
    "- You MAY NOT read files, search code, or investigate.\n"
    "- Your entire output is your REASONING — analysis returned to your parent.\n"
    "- Examine the conversation context above. Analyze what happened,\n"
    "  what went wrong, and what should be done differently.\n"
    "- Return your analysis via end_turn. Be concise: 2-5 paragraphs maximum.\n"
    "- You MUST call end_turn immediately with your analysis. NO EXCEPTIONS.\n"
)


# ── Args schema ──

@dataclass
class Args:
    """Think arguments."""
    question: str = field(
        default="",
        metadata={
            "description": (
                "Optional question or topic to focus thinking on. "
                "If empty, analyzes the current task generally."
                "Only use for pure thinking, if anything needs to be done (including investigating) then use fork."
            )
        },
    )



# ── Helpers ──

def _build_prompt(question: str) -> str:
    """Build the fork prompt based on whether a question was provided."""
    if question:
        return (
            f"QUESTION:\n{question}\n\n"
            f"{_THINK_HEADER}"
            "Answer with a focused analysis of the question.\n\n"
            "When finished, call end_turn with your final analysis as the message."
        )
    return (
        _THINK_HEADER
        + "Think hard about our current task. "
        "Answer with a comprehensive plan about what should be done to address our current task. "
        "What do we already know for certain? What do we need to determine?"
        + "\n\nWhen finished, call end_turn with your final plan as the message."
    )


def _build_safe_fallback(question: str, error: str, duration_ms: float | None = None) -> str:
    """Build a concise safe fallback when fork fails."""
    q_short = question[:200] if question else "(no question)"
    duration_info = ""
    if duration_ms is not None:
        duration_info = f" (ran for {format_duration_ms(duration_ms)})"
    return (
        f"[Think: fork failed ({error}{duration_info}), inline analysis follows]\n\n"
        f"Question: {q_short}\n\n"
        "Reflect inline: What is our goal? What progress have we made? "
        "What are the next steps? Are we stuck in a pattern?"
    )


# ── Execution ──

def run(question: str = "", agent: "TauBot" = None, tool_call_id: str | None = None) -> str:
    """Spawn a forked subagent for focused thinking.

    NEVER fails — always returns a useful response, even if the fork
    cannot be spawned. Uses safe fallback analysis as last resort.
    """
    if agent is None:
        # Even this edge case gets a useful response
        return (
            "[Think: no agent context available]\n\n"
            "Inline analysis: Without agent context, I cannot access the full conversation. "
            "Consider ensuring the think tool is invoked through the proper agent interface."
        )

    # Hard guard: scan the ENTIRE context for pending tool calls.
    for msg in agent.context.get_messages():
        if msg.get("role") == "tool" and msg.get("content", "").startswith("PENDING"):
            call_id = msg.get("tool_call_id", "unknown")
            return (
                f"⚠️  You are already thinking. A tool call is still pending (ID: {call_id}).\n"
                f"\nSpawning think has been BLOCKED.\n\n"
                f"Double-call will NOT help — this keeps rejecting until the\n"
                f"pending tool call is resolved. Resolve it first, then try again.\n"
            )

    prompt = _build_prompt(question)
    start_time = time.monotonic()

    try:
        return invoke_fork_sync(
            prompt=prompt,
            parent_context=agent.context,
            parent_agent=agent,
            nesting_count=agent.nesting_count,
            tool_call_id=tool_call_id,
            tool_filter=ToolFilter(
                allowlist=THINK_TOOL_ALLOWLIST,
                denied_message=(
                    "Tool '{tool_name}' is not permitted in think mode. "
                    "You are a pure thinker — you MAY ONLY call end_turn. "
                    "Reformulate using only end_turn."
                ),
            ),
            config=agent.config,
            nesting_threshold=agent.config.nesting.depth_threshold,
        )
    except Exception as e:
        # NEVER propagate the exception — always return something useful
        duration_ms = (time.monotonic() - start_time) * 1000
        error_name = type(e).__name__
        error(f"Think fork failed ({error_name}): {e} (duration: {format_duration_ms(duration_ms)})")
        # Audit log the failure
        if agent and hasattr(agent, "_session"):
            agent._session.audit_writer.tool_error(
                tool_call_id or "think",
                error_name,
                str(e),
                stack_trace=None,
                tool_name="think",
                tool_args={"question": question[:200]},
                parent_chain=None,
                nesting_level=agent.nesting_count,
                duration_ms=duration_ms,
                concurrent_ops=None,
            )
        return _build_safe_fallback(question, error_name, duration_ms=duration_ms)
