"""Structural validation for plain-text LLM responses (no tool calls).

Detection-only module: when the LLM returns a response without any tool calls,
this module checks whether the response is structurally sound or defective
(truncated, unclosed tags, malformed tool-call fragments).

IMPORTANT: A structurally valid response does NOT end the turn. The model MUST
explicitly call the `end_turn` tool to complete the turn. This module only
detects defects so the caller can inject a recovery reminder.

Key function:
- is_valid_end_of_turn: Checks structural integrity (misleading legacy name)

Checks performed (in order):
1. Empty response check
2. Truncation detection (finish_reason == "length")
3. Unclosed thinking tags in reasoning channel
4. Unclosed thinking tags in response text
5. Malformed tool-call syntax fragments

Recovery is handled by the caller (invoke_with_tools_loop in agent_core.py).
"""

from dataclasses import dataclass
from enum import Enum

from agent_llm import (
    INCOMPLETE_TOOL_CALL_PATTERNS,
    THINKING_TAG_PAIRS,
)


class ValidationErrorType(str, Enum):
    """Machine-readable error identifiers for end-of-turn validation."""
    EMPTY = "empty"
    TRUNCATED = "truncated"
    UNCLOSED_THINKING_REASONING = "unclosed_thinking_reasoning"
    UNCLOSED_THINKING_RESPONSE = "unclosed_thinking_response"
    MALFORMED_TOOL_CALL = "malformed_tool_call"


@dataclass(frozen=True)
class ValidationError:
    """Structured result from end-of-turn validation.

    Attributes:
        error_type: Machine-readable error identifier (ValidationErrorType).
    """

    error_type: ValidationErrorType


def _is_empty_response(response_text: str, _reasoning: str | None, _finish_reason: str | None) -> bool:
    """Check if response text is empty or whitespace-only."""
    return not (response_text or "").strip()


def _is_truncated(_response_text: str, _reasoning: str | None, finish_reason: str | None) -> bool:
    """Check if response was truncated by token limit."""
    return finish_reason == "length"


def _has_unclosed_thinking_in_reasoning(_response_text: str, reasoning_content: str | None, _finish_reason: str | None) -> bool:
    """Check if reasoning content contains unbalanced thinking tags."""
    if not reasoning_content:
        return False
    return any(reasoning_content.count(o) != reasoning_content.count(c) for o, c in THINKING_TAG_PAIRS)


def _has_unclosed_thinking_in_response(response_text: str | None, _reasoning: str | None, _finish_reason: str | None) -> bool:
    """Check if response text contains unbalanced thinking tags."""
    if not response_text:
        return False
    return any(response_text.count(o) != response_text.count(c) for o, c in THINKING_TAG_PAIRS)


def _has_malformed_tool_call_syntax(response_text: str | None, _reasoning: str | None, _finish_reason: str | None) -> bool:
    """Check if response text contains malformed tool-call syntax fragments."""
    if not response_text:
        return False
    return any(pattern in response_text for pattern in INCOMPLETE_TOOL_CALL_PATTERNS)


# Validation checks as a pipeline: (check_function, error_type).
#
# ACCEPTED DESIGN TRADE-OFF: Each check receives (response_text, reasoning_content,
# finish_reason) — a uniform 3-parameter signature. Most functions only use 1-2 of
# these parameters (prefixed with _ to indicate unused). This is intentional: the
# uniform call pattern `check(response_text, reasoning_content, finish_reason)` is
# cleaner and more maintainable than varying signatures per check. The minor
# "unused parameter" code smell is an acceptable cost for pipeline simplicity.
#
_CHECKS = [
    (_is_empty_response, ValidationErrorType.EMPTY),
    (_is_truncated, ValidationErrorType.TRUNCATED),
    (_has_unclosed_thinking_in_reasoning, ValidationErrorType.UNCLOSED_THINKING_REASONING),
    (_has_unclosed_thinking_in_response, ValidationErrorType.UNCLOSED_THINKING_RESPONSE),
    (_has_malformed_tool_call_syntax, ValidationErrorType.MALFORMED_TOOL_CALL),
]


def is_valid_end_of_turn(
    response_text: str,
    finish_reason: str | None,
    reasoning_content: str | None,
) -> ValidationError | None:
    """Check structural integrity of a plain-text LLM response.

    Detection-only: performs structural checks and returns a ``ValidationError``
    if defects are found, or ``None`` if the response is structurally sound.

    IMPORTANT: A structurally sound response (returning ``None``) does NOT mean
    the turn can end. The model MUST explicitly call the ``end_turn`` tool to
    complete the turn. This function only detects structural defects (truncation,
    unclosed tags, malformed tool-call syntax) so the caller knows whether to
    inject a recovery reminder.

    The name is a legacy artifact — it checks structural soundness, not turn-ending
    eligibility.

    Checks performed (in order of cost-efficiency):

    1. **Empty Response**: Empty or whitespace-only responses are invalid.
    2. **Truncation**: Responses with ``finish_reason="length"`` are incomplete.
    3. **Unclosed Thinking Tags (Reasoning)**: Checks reasoning_content for
       unbalanced thinking tags.
    4. **Unclosed Thinking Tags (Response)**: Checks response_text for
       unbalanced thinking tags.
    5. **Malformed Tool-Call Syntax**: Detects incomplete tool-call fragments
       in the response text.

    Args:
        response_text: The assistant's text response (may be empty).
        finish_reason: Model stop reason (e.g., "stop", "length", "tool_calls").
        reasoning_content: Separate reasoning channel content (may be None).

    Returns:
        ``None`` if the response is structurally sound (no defects found).
        ``ValidationError`` if the response is defective, describing the error.
    """

    for check, error_type in _CHECKS:
        if check(response_text, reasoning_content, finish_reason):
            return ValidationError(error_type=error_type)
    return None


__all__ = [
    "ValidationError",
    "is_valid_end_of_turn",
]
