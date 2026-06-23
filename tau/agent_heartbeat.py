"""Heartbeat management for TauBot.

Handles idle detection, activity tracking, and heartbeat fork execution.
Extracted from the TauBot god class to provide a focused, single-responsibility
module for heartbeat-related concerns.

Key class:
- HeartbeatManager: Orchestrates heartbeat activity tracking and idle detection
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_command_registry import prepare_command_prompt
from agent_console import error, warning
from agent_console_primitives import blank_line, status
from agent_subagent import invoke_fork_sync

if TYPE_CHECKING:
    from agent_core import TauBot


# Maximum heartbeat fork attempts (initial + retries).
_MAX_HEARTBEAT_ATTEMPTS = 3


@dataclass(frozen=True)
class HeartbeatResponse:
    """Parsed heartbeat response with structured exit state.

    Attributes:
        action: One of "prompt" or "no_action".
        task: Task description if action is "prompt", else None.
    """
    action: str
    task: str | None


def _parse_heartbeat_response(raw: str) -> HeartbeatResponse | None:
    """Parse a raw heartbeat response into a structured ``HeartbeatResponse``.

    Accepts two valid exit states:
    - ``<PROMPT>task</PROMPT>`` → action="prompt", task=content
    - ``<NO_ACTION>`` → action="no_action", task=None

    Uses ``re.search`` (not ``re.match``) to tolerate surrounding text
    (reasoning, commentary) that LLMs frequently emit.  Falls back to
    legacy ``PROMPT:`` format as a second pass.

    Returns ``None`` if the response does not match either pattern.
    """
    if raw is None:
        return None
    stripped = raw.strip()

    # 1. XML-style tags — search anywhere in the response (tolerant)
    match = re.search(r"<PROMPT>\s*(.+?)\s*</PROMPT>", stripped, re.DOTALL)
    if match:
        return HeartbeatResponse(action="prompt", task=match.group(1).strip())

    if re.search(r"<NO_ACTION>", stripped):
        return HeartbeatResponse(action="no_action", task=None)

    # 2. Legacy fallback — ``PROMPT: task`` on the first line
    first_line = stripped.split("\n")[0]
    match = re.match(r"^PROMPT:\s*(.+)$", first_line, re.IGNORECASE)
    if match:
        return HeartbeatResponse(action="prompt", task=match.group(1).strip())

    return None


class HeartbeatManager:
    """Manage heartbeat activity tracking and idle detection.

    Encapsulates the heartbeat logic previously embedded in TauBot:
    - Activity timestamp tracking
    - Idle time detection
    - Heartbeat fork execution with response validation

    The manager holds a reference to the parent agent so that ``run_heartbeat()``
    requires no parameters. This avoids the bloated 4-parameter call that
    leaked agent internals across module boundaries.

    Attributes:
        enabled: Whether heartbeat checking is enabled.
        interval_seconds: Idle threshold in seconds before triggering heartbeat.
        last_activity_time: Timestamp of last agent activity.
    """

    def __init__(
        self,
        enabled: bool,
        interval_seconds: int | None,
        agent: "TauBot",
    ):
        """Initialize the heartbeat manager.

        Args:
            enabled: Whether heartbeat checking is enabled.
            interval_seconds: Idle threshold in seconds before triggering heartbeat.
            agent: Parent TauBot reference (used internally for forking).
        """
        self.enabled = enabled
        self.interval_seconds = interval_seconds
        self.last_activity_time = time.time()
        self._agent = agent

    def touch_activity(self) -> None:
        """Update the last activity timestamp.

        Records the current time as the last activity moment. Used by the
        heartbeat mechanism to track idle time and determine when to trigger
        a heartbeat check.
        """
        self.last_activity_time = time.time()

    def _fork_and_validate(
        self,
        prompt: str,
        nesting_count: int,
    ) -> HeartbeatResponse | None:
        """Fork a heartbeat check and validate the response.

        Retries up to ``_MAX_HEARTBEAT_ATTEMPTS`` times if the response
        does not match the expected structured format.

        Returns:
            Parsed ``HeartbeatResponse`` on success, or ``None`` on persistent failure.
        """
        agent = self._agent
        base_prompt = prompt  # Prevent cumulative nudge duplication

        for attempt in range(1, _MAX_HEARTBEAT_ATTEMPTS + 1):
            current_prompt = base_prompt
            if attempt > 1:
                current_prompt = (
                    base_prompt
                    + "\n\nIMPORTANT: Your previous response did not match the required format. "
                    "You MUST respond with exactly one of:\n"
                    "  <PROMPT>task description</PROMPT>\n"
                    "  <NO_ACTION>"
                )

            try:
                raw = invoke_fork_sync(
                    prompt=current_prompt,
                    parent_context=agent.context,
                    parent_agent=agent,
                    nesting_count=nesting_count,
                )
            except Exception as e:
                error(f"Heartbeat fork failed: {e}")
                return None

            parsed = _parse_heartbeat_response(raw)
            if parsed is not None:
                return parsed

            if attempt < _MAX_HEARTBEAT_ATTEMPTS:
                warning(
                    f"Heartbeat response format invalid (attempt {attempt}), retrying..."
                )
            else:
                error("Heartbeat response format invalid after all retries")
        return None

    def run_heartbeat(self) -> HeartbeatResponse | None:
        """Run a heartbeat check if the agent has been idle past the interval.

        Checks if the agent has been idle for longer than the configured heartbeat
        interval. If so, forks a heartbeat check using a forked subagent.

        Before forking, compresses the context if it exceeds 80% of the maximum
        to avoid sending oversized contexts to the fork (which would waste tokens
        and risk API rejection).

        Validates the fork's response against the structured exit-state format
        and retries if the format is invalid.

        Returns:
            ``HeartbeatResponse`` with parsed action/task if executed, otherwise ``None``.
            Returns ``None`` if heartbeat is disabled, interval not set, or idle time
            is below the threshold.
        """
        agent = self._agent

        if not self.enabled or self.interval_seconds is None:
            return None

        # Pre-heartbeat compression guard — only when actually needed.
        estimated = agent.context.estimate_tokens()
        if estimated / agent.max_context_tokens >= 0.80:
            agent.context.compress(0.30, agent, agent.get_all_tools())

        idle_seconds = time.time() - self.last_activity_time
        if idle_seconds < self.interval_seconds:
            return None

        prompt = prepare_command_prompt("heartbeat")
        if not prompt:
            error("Heartbeat command file not found or empty")
            self.touch_activity()  # prevent rapid re-trigger on failure
            return None

        status(f"[HEARTBEAT] Idle {int(idle_seconds)}s, forking check-in...")
        blank_line()

        return self._fork_and_validate(prompt, agent.nesting_count)
