"""Signal end of turn with a final message. Sets ``force_end_turn`` to cause the main loop to append the result and return immediately."""

from __future__ import annotations

from tools import ToolMetadata

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot


# ── Constants ────────────────────────────────────────────────────────────────

ENDTURN_SENTINEL = "ENDTURN"


# ── Tool metadata ────────────────────────────────────────────────────────────

metadata = ToolMetadata(
    name="end_turn",
    description=(
        "Signal the end of the current turn and provide a final message. "
        "MANDATORY: You MUST call this tool to end every turn. "
        "Only call end_turn when you are finished - you will not be able to continue afterwards. "
        "end_turn MUST be the only tool call in an assistant message. "
        "Plain text responses without end_turn will NOT end the turn. "
        "To end the turn, call the end_turn tool with the message parameter. "
        "Pass 'ENDTURN' to use your last substantive assistant message as the "
        "final response, or pass your final response directly."
    ),
    max_size=8192,
)


# ── Args schema ──────────────────────────────────────────────────────────────

@dataclass
class Args:
    """Arguments for the end_turn tool."""
    message: str = field(
        metadata={
            "description": (
                "The final message to append as the last assistant output "
                "when the turn ends. Use 'ENDTURN' to signal that your answer "
                "is already in the conversation above — the system will use "
                "your last substantive message as the final response. "
                "Only call end_turn when you are finished - you will not be "
                "able to continue afterwards. "
            )
        }
    )



# ── Execution ────────────────────────────────────────────────────────────────

def run(message: str, agent: "TauBot", tool_call_id: str | None = None) -> str:
    """End the current turn immediately by setting ``force_end_turn``.

    Handles the ENDTURN sentinel: when message is 'ENDTURN', resolves to the
    last substantive assistant message so the model doesn't have to repeat itself.
    """
    stripped = message.strip()

    if not stripped or stripped == ENDTURN_SENTINEL:
        # Empty string or ENDTURN sentinel → resolve to last substantive response.
        # REJECT if none was tracked — force explicit message.
        if not agent.last_substantive_response:
            raise ValueError(
                "ENDTURN rejected: no substantive response was tracked this turn. "
                "You must call end_turn with your actual response text."
            )
        resolved = agent.last_substantive_response
        agent.force_end_turn = resolved
    else:
        agent.force_end_turn = stripped

    return agent.force_end_turn
