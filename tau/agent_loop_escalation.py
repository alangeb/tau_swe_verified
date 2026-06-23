"""Loop escalation management for TauBot.

Handles loop detection escalation, reflection injection, and recovery from
invalid end-of-turn states. Extracted from the TauBot god class to provide
a focused, single-responsibility module for loop-related concerns.

Key class:
- LoopEscalationManager: Orchestrates loop escalation, recovery, and reflection
"""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING

from agent_console import (
    loop_warning,
    tool_start,
)
from agent_console_primitives import format_duration_ms
from agent_context import (
    TauContext,
    get_last_real_user_prompt,
)
from agent_loop_detect import LoopDetector
from agent_reflection import ReflectionScheduler

if TYPE_CHECKING:
    from agent_core import TauBot


class LoopEscalationManager:
    """Manage loop detection escalation, reflection injection, and recovery.

    Encapsulates the loop escalation logic previously embedded in TauBot:
    - Escalation handling (levels 1-4)
    - Recovery from invalid end-of-turn states
    - Periodic reflection injection

    Attributes:
        loop_detector: LoopDetector instance for pattern detection.
        reflection_scheduler: ReflectionScheduler for adaptive reflection timing.
        context: TauContext instance for context manipulation.
        agent: Parent TauBot reference for agent state access.
    """

    def __init__(
        self,
        loop_detector: LoopDetector,
        reflection_scheduler: ReflectionScheduler,
        context: TauContext,
        agent: "TauBot",
    ):
        """Initialize the loop escalation manager.

        Args:
            loop_detector: LoopDetector instance for pattern detection.
            reflection_scheduler: ReflectionScheduler for adaptive reflection timing.
            context: TauContext instance for context manipulation.
            agent: Parent TauBot reference for agent state access.
        """
        self._loop_detector = loop_detector
        self._reflection_scheduler = reflection_scheduler
        self._context = context
        self._agent = agent

    def set_reflection_scheduler(self, scheduler: ReflectionScheduler) -> None:
        """Replace the reflection scheduler (e.g., when switching LLM groups)."""
        self._reflection_scheduler = scheduler

    def handle_loop_escalation(self) -> bool:
        """Handle loop escalation based on current escalation level.

        Displays appropriate console warnings and injects context messages
        to break the loop. At level 4, forces end of turn.

        Returns:
            True if the loop should continue, False if turn should end.
        """
        info = self._loop_detector.get_escalation_info()
        level = info["escalation_level"]

        if level >= 4:
            # Nuclear option: force end of turn
            self._agent.force_end_turn = (
                f"Loop detection forced turn termination after "
                f"{info['total_warnings']} warnings "
                f"(escalation level {level})."
            )
            loop_warning(4, f"Turn terminated: {info['total_warnings']} loop warnings")
            # Note: caller (invoke_with_tools_loop) appends self.force_end_turn
            # to context — do NOT duplicate the append here.
            return False

        if level >= 3:
            # Critical: force think mode with synthetic user message
            loop_warning(3, f"Forced think mode: {info['total_warnings']} warnings")
            last_real = get_last_real_user_prompt(self._context.get_messages())
            self._context.append_assistant(
                f"I am clearly stuck in a loop ({info['total_warnings']} warnings). "
                f"I need to step back and think. I will only use the 'think' tool "
                f"to break out of this loop.",
                None,
            )
            self._context.append_synthetic_user(
                "escalation",
                f"CRITICAL: You have received {info['total_warnings']} loop "
                f"warnings this turn.\n\n"
                f"Current task: {last_real}\n\n"
                f"You MUST use the 'think' tool now. Do not call any other tools "
                f"until you have analyzed the situation. After thinking, you may "
                f"resume normal operations."
            )
            # Reset loop detector after injection to prevent repeated escalation
            # that would cause consecutive assistant messages (OpenAI violation)
            self._loop_detector.reset()
            return True

        if level >= 2:
            # Warning level: inject context to break the pattern
            loop_warning(2, f"Loop warning: {info['total_warnings']} warnings")
            last_real = get_last_real_user_prompt(self._context.get_messages())
            self._context.append_assistant(
                f"I may be stuck in a loop ({info['total_warnings']} warnings). "
                f"Let me reconsider.",
                None,
            )
            self._context.append_synthetic_user(
                "escalation",
                f"You have received {info['total_warnings']} loop warnings this turn. "
                f"Current task: {last_real}\n\n"
                f"Stop repeating the same pattern. Consider using the 'think' tool "
                f"to re-analyze, changing your approach, or providing a text "
                f"response instead of tool calls."
            )
            # Reset loop detector after injection to prevent repeated escalation
            # that would cause consecutive assistant messages (OpenAI violation)
            self._loop_detector.reset()
            return True

        # Level 1: informational warning only
        if level >= 1:
            loop_warning(1, f"Possible loop detected: {info['total_warnings']} warnings")

        return True

    def recover_from_invalid_end_of_turn(
        self,
        response_text: str,
        reasoning_content: str | None,
    ) -> None:
        """Recover from invalid end-of-turn by injecting a synthetic bridge message.

        Uses the same synthetic bridge pattern as _recover_from_missing_end_turn
        and append_assistant() auto-bridges. This maintains consistent context
        alternation across all recovery paths.

        Args:
            response_text: The defective assistant response text.
            reasoning_content: The reasoning content (if any).
        """
        from agent_context import get_last_real_user_prompt

        # Get the last REAL user prompt (not synthetic escalation messages)
        last_real_prompt = get_last_real_user_prompt(self._context.get_messages())

        # Build recovery instructions
        recovery_text = (
            "Your previous response was structurally incomplete "
            "(truncated, unclosed tags, or malformed). "
            "Please complete your response properly. "
            "When done, call `end_turn` to end your turn."
        )

        # Get the last real user prompt to provide context
        synthetic_content = f"{recovery_text}\n\nOriginal prompt for this turn:\n{last_real_prompt}"

        # Append synthetic user bridge - consistent with other recovery paths
        self._context.append_synthetic_user("recovery", synthetic_content)

    def inject_early_reflection(self, question: str | None = None) -> None:
        """Inject an entry microplan reflection BEFORE the first LLM call.

        Unlike periodic reflection, this runs at loop entry to give the agent
        a chance to plan before acting. Uses a task-focused prompt.
        """
        from agent_tool_executor import execute_tool_call

        last_real_prompt = get_last_real_user_prompt(self._context.get_messages())

        if question is None:
            question = (
                f"(1) What is our goal? "
                f"(2) What does the user want: {last_real_prompt}? "
                f"(3) What is the high-level plan to accomplish this? "
                f"(4) What tools will likely be needed? "
                "Be concise — 3 to 5 sentences max."
            )

        tool_call_id = f"early_reflect_{uuid.uuid4().hex[:8]}"

        concise_summary = "Entry reflection completed."

        synthetic_tc = {
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": "think",
                "arguments": json.dumps({"question": question}),
            },
        }

        self._context.append_assistant(
            concise_summary,
            [synthetic_tc],
        )

        tool_start("think [early-reflect]", "entry microplan — system-initiated")

        try:
            result = execute_tool_call(
                {"id": tool_call_id, "name": "think", "args_dict": {"question": question}},
                self._agent,
            )
        except Exception as e:
            result = f"[Early reflection failed: {type(e).__name__}: {str(e)[:200]}]"
        self._context.append_tool(result, tool_call_id)

    def inject_reflection(self, question: str | None = None) -> None:
        """Inject a periodic reflection into the tool loop.

        Uses a decoupled approach:
        - Fork receives the FULL last real user prompt (rich context for thinking)
        - Main context receives a CONCISE summary (reduces pollution)

        Args:
            question: Optional custom reflection question. If None, uses default
                reflection prompt based on the last real user input.
        """
        from agent_tool_executor import execute_tool_call

        # Get the last REAL user prompt (not synthetic escalation messages)
        last_real_prompt = get_last_real_user_prompt(self._context.get_messages())

        # Build the reflection question
        if question is None:
            question = (
                f"(1) What is our goal? "
                f"(2) What have we accomplished so far? "
                f"(3) Are we on track for: {last_real_prompt}? "
                f"(4) What are the next 2-3 steps? "
                "Be concise — 3 to 5 sentences max."
            )

        tool_call_id = f"reflect_{uuid.uuid4().hex[:8]}"

        # Build the CONCISE assistant message for main context (reduced pollution)
        concise_summary = "Reflection completed."

        synthetic_tc = {
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": "think",
                "arguments": json.dumps({"question": question}),
            },
        }

        self._context.append_assistant(
            concise_summary,
            [synthetic_tc],
        )

        # Display console message for automatic reflection
        tool_start("think [auto-reflect]", "periodic reflection — system-initiated")

        # Execute think — never change tool_filter (would break prefix caching).
        try:
            start = time.monotonic()
            result = execute_tool_call(
                {"id": tool_call_id, "name": "think", "args_dict": {"question": question}},
                self._agent,
            )
        except Exception as e:
            # Make think never fail — use a safe fallback with debug info
            elapsed = time.monotonic() - start
            result = (
                f"[Reflection failed after {format_duration_ms(elapsed * 1000)}: "
                f"{type(e).__name__}: {str(e)[:200]}]"
            )
        finally:
            self._reflection_scheduler.mark_reflection_done()
        self._context.append_tool(result, tool_call_id)
