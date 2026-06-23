"""Execute tool calls with timeout, validation, and error handling.

Supports single tool calls and batch execution with:
- Signal-based timeout protection (default 180s) on Unix, thread-based on Windows
- Argument validation against tool schemas
- Output truncation for oversized results
- Loop detection and prevention
"""

import json
import os
import re
import signal
import sys
import time
import traceback
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_core import TauBot

from agent_console import loop_warning_display, tool_blocked, tool_error_detail, tool_output, tool_start, warning
from agent_session import (
    AuditWriter,
    SESSION_PREFIX,
    write_oversized_output,
)
from tools.validation import (
    _generate_validation_error,
    _get_tool_schema_info,
    _validate_tool_args,
    normalize_tool_call,
    get_valid_fields_from_tool,
)
from tools import (
    DEFAULT_TOOL_MAX_SIZE,
    TOOLS,
)

__all__ = [
    "execute_tool_call",
    "execute_tool_batch",
    "DEFAULT_TOOL_TIMEOUT",
    "ToolTimeout",
]


# Tool execution constants
DEFAULT_TOOL_TIMEOUT = 180

# Exception types caught during tool execution (shared by inner call and outer handler)
# Catches ALL exceptions (except BaseException subclasses like KeyboardInterrupt, SystemExit)
# to prevent any tool crash from killing the agent.
TOOL_EXEC_EXCEPTIONS = Exception
TOOL_EXEC_EXCEPTIONS_WITH_TIMEOUT = Exception  # Same — catches everything including TimeoutError


# ── Signal-based timeout infrastructure (Unix only) ──────────────────────────

# signal.setitimer() is Unix-only. On Windows, we fall back to thread-based timeouts.
_USE_SIGNALS = sys.platform != "win32"


class ToolTimeout(TimeoutError):
    """Raised when tool execution exceeds timeout."""
    __slots__ = ("tool_name", "timeout")

    def __init__(self, tool_name: str, timeout: int):
        self.tool_name = tool_name
        self.timeout = timeout
        super().__init__(f"Tool '{tool_name}' timed out after {timeout} seconds")


# Module-level state for signal handler (must be module-level for signal context)
# NOTE: Safe because execute_tool_batch() calls tools SEQUENTIALLY (not in parallel).
# If parallel tool execution is ever added, this must be replaced with threading.local().
_timeout_state: dict[str, Any] = {
    "active": False,
    "tool_name": "",
    "timeout": 0,
}


# ── Tool name sanitization ──────────────────────────────────────────────────

# Fragments that leak from LLM output into tool names.  Echoing them back
# creates a feedback loop — strip them to break the cycle.
_TOOL_NAME_FRAGMENTS = (
    "</parameter", "</function", "</tool_call", "</tool>",
    "</think", "<|end_of_thought", "<|end_of_turn",
)

# Regex to extract just the tool name from malformed calls like
# "end_turn(message=...)" or "bash(cmd=...)".
_TOOL_NAME_RE = re.compile(r"^([a-zA-Z_]\w*)")


def _sanitize_tool_name(raw: str) -> str:
    """Sanitise a tool name that may contain LLM-garbage.

    Handles:
    - XML/HTML fragments leaking from output (</parameter, </function, ...)
    - Parenthesised arguments: ``end_turn(message="...")`` → ``end_turn``
    - Trailing whitespace / newlines
    """
    name = raw
    for fragment in _TOOL_NAME_FRAGMENTS:
        name = name.replace(fragment, "")
    name = name.rstrip()
    # If name contains '(', extract just the tool name part
    if "(" in name:
        name = name[:name.index("(")]
    name = name.strip()
    # Final safety: extract only the leading identifier
    m = _TOOL_NAME_RE.match(name)
    if m:
        name = m.group(1)
    return name


def _signal_timeout_handler(signum: int, frame: Any) -> None:
    """Signal handler — raises ToolTimeout.
    
    CRITICAL: This runs in signal context. Only async-signal-safe functions
    are permitted. We only raise an exception here (async-signal-safe).
    """
    raise ToolTimeout(_timeout_state["tool_name"], _timeout_state["timeout"])


def _log_tool_error(
    audit_writer: "AuditWriter | None",
    call_id: str,
    error: Exception,
    duration_ms: float,
    stack_trace: str | None = None,
) -> None:
    """Log a tool execution error to the audit writer.

    Consolidates the repeated pattern of checking audit_writer
    and calling audit_writer.tool_error().
    """
    if audit_writer is not None:
        audit_writer.tool_error(
            call_id=call_id,
            error_type=type(error).__name__,
            error_message=str(error),
            stack_trace=stack_trace,
            duration_ms=duration_ms,
        )


# ── Args structure validation ──────────────────────────────────────────────

def _validate_args_structure(tc: dict, tool_name: str) -> list[str]:
    """Check for malformed args structure. Returns list of issues (empty if OK)."""
    issues: list[str] = []
    args = tc.get("args_dict")

    if args is None:
        issues.append(f"Tool '{tool_name}' called with None arguments")
    elif not isinstance(args, dict):
        issues.append(
            f"Tool '{tool_name}' arguments must be a JSON object, "
            f"got {type(args).__name__}: {args!r}"
        )

    return issues


# ── Console error display ──────────────────────────────────────────────────


def _display_tool_error(tool_name: str, error_msg: str, error_type: str) -> None:
    """Display a tool error on console with appropriate severity.

    Centralises console output for all early-return error paths so the operator
    sees the error immediately instead of wondering why the tool hung after
    tool_start().
    """
    if error_type == "TOOL_NOT_FOUND":
        clean = _sanitize_tool_name(tool_name)
        warning(f"Tool '{clean}' not found.")
    elif error_type == "VALIDATION_ERROR":
        warning(error_msg)
    # TOOL_BLOCKED is handled by tool_blocked() which already emits console output


# ── Single tool call execution ────────────────────────────────────────────


def execute_tool_call(tc: dict, agent: "TauBot", audit_writer: "AuditWriter | None" = None) -> str:
    """Execute a single tool call with timeout, validation, and error handling.

    Args:
        tc: Tool call dict with keys: id, name, args_dict.
        agent: TauBot instance for context, tool filter, and loop detection.

    Returns:
        Tool output string, or an error/warning message on failure.
    """
    call_id = tc["id"]
    tool_name = tc["name"]
    args = tc.get("args_dict", {})

    loop_prefix = ""
    loop_warning = agent.loop_detector.detect_tool_loop(tool_name, args)
    if loop_warning:
        loop_warning_display(loop_warning)
        loop_prefix = f"{loop_warning}\n\n"

    if not agent.tool_filter.should_include(tool_name):
        available = agent.tool_filter.get_available(agent.available_tool_names)
        available_str = ", ".join(available)
        tool_blocked(tool_name, available_str)

        if audit_writer:
            audit_writer.tool_blocked(
                call_id=call_id,
                tool_name=tool_name,
                available_str=available_str,
            )

        return f"{loop_prefix}{agent.tool_filter.format_denied(tool_name, available)}"

    # Validate args structure before proceeding
    args_issues = _validate_args_structure(tc, tool_name)
    if args_issues:
        issue_msg = "; ".join(args_issues)
        tool_start(tool_name, "")
        _display_tool_error(tool_name, issue_msg, "VALIDATION_ERROR")
        if audit_writer:
            audit_writer.tool_error(
                call_id=call_id,
                error_type="VALIDATION_ERROR",
                error_message=issue_msg,
            )
        return f"{loop_prefix}{issue_msg}"

    arg_parts = [
        f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}"
        for k, v in sorted(args.items())
    ]
    tool_start(tool_name, ", ".join(arg_parts))

    entry = TOOLS.get(tool_name)
    tool_func = entry.run if entry else None
    tool_module = entry.module if entry else None

    if tool_func is None:
        available_names = list(TOOLS.keys())
        clean_name = _sanitize_tool_name(tool_name)
        # Try multiple cutoff levels for better suggestions
        suggestions = get_close_matches(clean_name, available_names, n=3, cutoff=0.6)
        if not suggestions:
            suggestions = get_close_matches(clean_name, available_names, n=3, cutoff=0.4)
        # Also check for common typos and partial matches
        if not suggestions:
            clean_lower = clean_name.lower()
            for name in available_names:
                if clean_lower in name.lower() or name.lower() in clean_lower:
                    suggestions.append(name)
                    if len(suggestions) >= 3:
                        break

        error_msg = f"Tool '{clean_name}' not found."
        if suggestions:
            error_msg += (
                f" Did you mean: {', '.join(suggestions)}? "
                f"Please retry using one of the suggested tool names."
            )
        else:
            error_msg += (
                f" No similar tools found. "
                f"Available tools: {', '.join(sorted(available_names))}."
            )

        _display_tool_error(tool_name, error_msg, "TOOL_NOT_FOUND")

        if audit_writer:
            audit_writer.tool_error(
                call_id=call_id,
                error_type="TOOL_NOT_FOUND",
                error_message=error_msg,
                stack_trace=None,
            )
            audit_fields = (
                f"TOOL_NOT_FOUND_CONTEXT id={call_id} "
                f"tool='{clean_name}' "
                f"suggestions={suggestions} "
                f"available_count={len(available_names)}"
            )
            audit_writer._enqueue(f"  | {audit_fields}")
        return f"{loop_prefix}{error_msg}"

    valid_fields, required_fields, field_types = _get_tool_schema_info(tool_func)
    if valid_fields:
        is_valid, unknown_fields, missing_fields = _validate_tool_args(
            args, valid_fields, required_fields
        )
        if not is_valid:
            error_msg = _generate_validation_error(
                tool_name, unknown_fields, valid_fields,
                missing_fields=missing_fields, field_types=field_types,
            )
            _display_tool_error(tool_name, error_msg, "VALIDATION_ERROR")
            if audit_writer:
                audit_writer.tool_error(
                    call_id=call_id,
                    error_type="VALIDATION_ERROR",
                    error_message=error_msg,
                )
            return f"{loop_prefix}{error_msg}"

    _start_time = time.monotonic()

    try:
        # Timeout priority: args["timeout"] > entry.get_timeout() > DEFAULT
        if isinstance(args, dict) and "timeout" in args:
            cap_timeout = int(args["timeout"])
        elif entry is not None:
            cap_timeout = entry.get_timeout()
        else:
            cap_timeout = DEFAULT_TOOL_TIMEOUT

        if _USE_SIGNALS:
            result = _execute_with_signal_timeout(tool_func, args, agent, call_id, tool_name, cap_timeout)
        else:
            result = _execute_with_thread_timeout(tool_func, args, agent, call_id, tool_name, cap_timeout)

    except TOOL_EXEC_EXCEPTIONS_WITH_TIMEOUT as e:
        duration_ms = (time.monotonic() - _start_time) * 1000
        _log_tool_error(audit_writer, call_id, e, duration_ms, traceback.format_exc())
        tool_error_detail(tool_name, tc, error=e, duration_ms=duration_ms)
        return f"{loop_prefix}Error invoking tool '{tool_name}': {e}"

    tool_output_str = str(result)
    output_bytes = len(tool_output_str.encode("utf-8", errors="replace"))

    max_size = entry.max_size if entry else DEFAULT_TOOL_MAX_SIZE

    if output_bytes > max_size:
        file_path = write_oversized_output(tool_output_str, SESSION_PREFIX)

        # Preview: at most 10 lines and 500 bytes
        preview_lines = tool_output_str.split("\n")[:10]
        preview = "\n".join(preview_lines)
        preview_bytes = len(preview.encode("utf-8", errors="replace"))
        if preview_bytes > 500:
            truncated_preview = tool_output_str[:500]
            last_nl = truncated_preview.rfind("\n")
            if last_nl > 200:
                truncated_preview = truncated_preview[:last_nl]
            preview = truncated_preview

        file_note = (
            f"\n\nFull output saved to: {file_path}\n"
            f"Use file_read or read to inspect it. Be careful — it is large."
            if file_path else ""
        )
        trunc_msg = (
            f"⚠ TOOL OUTPUT TRUNCATED: '{tool_name}' produced {output_bytes} bytes (max: {max_size}).\n\n"
            f"First 10 lines (≤500 bytes):\n{preview}{file_note}"
        )

        if audit_writer:
            duration_ms = (time.monotonic() - _start_time) * 1000
            if file_path:
                audit_writer.tool_truncated(
                    call_id=call_id,
                    tool_name=tool_name,
                    output_bytes=output_bytes,
                    max_size=max_size,
                    file_path=file_path,
                )
            else:
                audit_writer.tool_result(
                    call_id=call_id,
                    status="success",
                    duration_ms=duration_ms,
                    output=tool_output_str,
                    output_bytes=output_bytes,
                    tool_name=tool_name,
                )

        tool_output(trunc_msg, tool_name)
        return f"{loop_prefix}{trunc_msg}"

    if audit_writer:
        duration_ms = (time.monotonic() - _start_time) * 1000
        audit_writer.tool_result(
            call_id=call_id,
            status="success",
            duration_ms=duration_ms,
            output=tool_output_str,
            output_bytes=output_bytes,
            tool_name=tool_name,
        )

    tool_output(tool_output_str, tool_name)
    return f"{loop_prefix}{result}"


def _execute_with_signal_timeout(tool_func, args, agent, call_id, tool_name, cap_timeout: int) -> Any:
    """Execute tool with signal-based timeout (Unix only).

    Uses signal.setitimer() for sub-second precision timeouts.
    No orphaned threads, no concurrent execution.
    """
    # Save current signal state.
    # getitimer returns (time_remaining, interval); setitimer takes (seconds, interval).
    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)  # (time_remaining, interval)

    try:
        _timeout_state["active"] = True
        _timeout_state["tool_name"] = tool_name
        _timeout_state["timeout"] = cap_timeout

        signal.signal(signal.SIGALRM, _signal_timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, cap_timeout)

        try:
            result = tool_func(**args, agent=agent, tool_call_id=call_id)
        finally:
            # ALWAYS cancel timer, restore old handler AND timer state.
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            signal.setitimer(signal.ITIMER_REAL, old_timer[0], old_timer[1])
            _timeout_state["active"] = False
    except ToolTimeout:
        # Re-raise ToolTimeout (caught by outer except)
        raise
    except TOOL_EXEC_EXCEPTIONS as e:
        raise RuntimeError(f"Error invoking tool '{tool_name}': {e}")

    return result


def _execute_with_thread_timeout(tool_func, args, agent, call_id, tool_name, cap_timeout: int) -> Any:
    """Execute tool with thread-based timeout (Windows fallback).
    
    Uses daemon threads with queue-based IPC. This is the old approach
    that can orphan threads on timeout. Used only on Windows where
    signal.setitimer() is unavailable.
    """
    import queue
    import threading

    result_queue = queue.Queue()

    def call_func(current_call_id=call_id):
        try:
            result = tool_func(**args, agent=agent, tool_call_id=current_call_id)
            result_queue.put(("success", result))
        except TOOL_EXEC_EXCEPTIONS as e:
            result_queue.put(("error", f"Error invoking tool '{tool_name}': {e}"))

    thread = threading.Thread(target=call_func, daemon=True)
    thread.start()

    elapsed = 0
    while thread.is_alive() and elapsed < cap_timeout:
        thread.join(timeout=0.5)
        elapsed += 0.5

    if thread.is_alive():
        thread.join(timeout=5)  # Grace period

    try:
        status, result = result_queue.get_nowait()
    except queue.Empty:
        raise TimeoutError(f"Tool '{tool_name}' timed out after {cap_timeout} seconds")

    if status == "error":
        raise RuntimeError(result)

    return result


# ── Batch tool execution ─────────────────────────────────────────────────


def _build_unknown_tool_question(
    tool_name: str,
    args: dict,
    available_tools: list[str],
) -> str:
    """Build a detailed think question for unknown tool replacement.

    CONTEXT/EXEC MISMATCH (by design):
    The question returned here is executed by the think fork with FULL context
    (tool name, args, available tools). However, the PARENT context sees only
    `think()` with empty args + the think result. This is intentional context
    economy — the full question would waste tokens in the parent conversation.
    Trade-off: replaying the conversation history won't reproduce the same think
    result because the question is missing from context. This is acceptable since
    the think result itself contains the recovery guidance.
    """
    args_summary = json.dumps(args, indent=2) if args else "(no args)"
    tools_list = ", ".join(sorted(available_tools))
    return (
        f"You repeatedly attempted to call unknown tool '{tool_name}'.\n\n"
        f"Original call: tool '{tool_name}' with arguments: {args_summary}\n\n"
        f"This tool does NOT exist. Available tools: {tools_list}\n\n"
        f"What you did wrong: Called a non-existent tool '{tool_name}'.\n"
        f"What you must NOT do: Call '{tool_name}' again — it will never work.\n"
        f"What you SHOULD do instead: Pick a tool from the available list above. "
        f"Analyze what you were trying to accomplish and use the correct tool.\n\n"
        f"Be concise: 2-3 sentences."
    )


def _deduplicate_tool_calls(tool_calls: list[dict]) -> list[dict]:
    """Remove duplicate tool calls within a single assistant message.

    Two calls are duplicates if they have the same tool name and identical
    arguments (same keys, same values). Keeps the first occurrence of each
    unique call. Logs a summary warning for dropped duplicates.
    """
    if len(tool_calls) <= 1:
        return tool_calls

    seen: dict[str, int] = {}  # signature -> index of kept call
    unique: list[dict] = []

    for tc in tool_calls:
        name = tc.get("name", "")
        # Use raw args string (always present) for canonical comparison.
        # args_dict is not yet populated at this point in the pipeline.
        raw_args = tc.get("args", "")
        sig = json.dumps({"name": name, "args": raw_args}, sort_keys=True)

        if sig not in seen:
            seen[sig] = len(unique)
            unique.append(tc)

    if len(unique) < len(tool_calls):
        dropped = len(tool_calls) - len(unique)
        warning(f"Deduplicated tool calls: {len(tool_calls)} → {len(unique)} ({dropped} dropped)")
    return unique


def execute_tool_batch(
    tool_calls: list[dict],
    agent: "TauBot",
    reasoning: str | None = None,
    audit_writer: "AuditWriter | None" = None,
) -> None:
    """Execute all tool calls and append results to agent context.

    Maintains OpenAI message alternation:
      1. Appends ONE assistant message with ALL tool_calls
      2. Appends ONE tool result for EACH tool_call_id
      3. Context ends with tool results, ready for next assistant message

    Unknown tool replacement (decoupled pattern):
      - Fork receives FULL detailed question (tool name, args, available tools)
      - Parent context receives MINIMAL: think() without args + think result
    """
    if not tool_calls:
        return

    # Deduplicate: drop calls with identical (name, args) within this batch.
    # Ensures context sees each unique call only once and we don't
    # execute the same tool twice with the same arguments.
    tool_calls = _deduplicate_tool_calls(tool_calls)
    if not tool_calls:
        return

    # Track normalization warnings separately (avoid polluting tc dicts)
    norm_warnings_map: dict[str, list[str]] = {}

    # Parse args and resolve aliases before building context
    for tc in tool_calls:
        try:
            tc["args_dict"] = json.loads(tc["args"]) if tc["args"] else {}
        except json.JSONDecodeError as e:
            raw = tc.get("args", "")
            warning(
                f"Malformed JSON args for '{tc['name']}' (id={tc['id']}): "
                f"{e.msg}; raw={raw[:80]!r}. Falling back to empty args."
            )
            tc["args_dict"] = {}

        original_name = tc["name"]
        original_args = dict(tc.get("args_dict", {}))

        # Normalize: resolve aliases, coerce types, fill defaults
        norm_warnings = normalize_tool_call(tc)

        if norm_warnings:
            for w in norm_warnings:
                warning(f"FIX: {w}")
            norm_warnings_map[tc["id"]] = norm_warnings

        if tc.get("args_dict"):
            tc["args"] = json.dumps(tc["args_dict"])

        if audit_writer:
            audit_writer.tool_call(
                call_id=tc["id"],
                original_name=original_name,
                original_args=original_args,
                final_name=tc["name"],
                final_args=tc.get("args_dict", {}),
                fixes=norm_warnings,
            )

    # ── Unknown tool replacement detection ──
    # replacement_map: call_id -> (detailed_question, original_tool_name)
    replacement_map: dict[str, tuple[str, str]] = {}

    for tc in tool_calls:
        entry = TOOLS.get(tc["name"])
        if entry is None:
            # Unknown tool — check if we should replace
            if agent.loop_detector.should_replace_unknown(tc["name"]):
                question = _build_unknown_tool_question(
                    tool_name=tc["name"],
                    args=tc.get("args_dict", {}),
                    available_tools=agent.available_tool_names,
                )
                replacement_map[tc["id"]] = (question, tc["name"])

    # ── Build context tool calls (with replacements, MINIMAL args) ──
    tool_calls_for_context = []
    for tc in tool_calls:
        if tc["id"] in replacement_map:
            # MINIMAL: think() without arguments in context
            tool_calls_for_context.append({
                "id": tc["id"],
                "type": "function",
                "function": {"name": "think", "arguments": "{}"},
            })
        else:
            tool_calls_for_context.append({
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["args"]},
            })

    agent.context.append_assistant(
        "",
        tool_calls_for_context,
        reasoning=reasoning,
    )

    # ── Execute tools (with replacements using FULL question) ──
    for tc in tool_calls:
        if tc["id"] in replacement_map:
            question, _ = replacement_map[tc["id"]]
            # Execute think with FULL detailed question
            result = execute_tool_call(
                {"id": tc["id"], "name": "think", "args_dict": {"question": question}},
                agent,
                audit_writer=audit_writer,
            )
        else:
            result = execute_tool_call(tc, agent, audit_writer=audit_writer)
            # Track unknown tools (for future replacement)
            if TOOLS.get(tc["name"]) is None:
                agent.loop_detector.record_unknown_tool(tc["name"])

        if tc["id"] in norm_warnings_map:
            fixes = "; ".join(norm_warnings_map[tc["id"]])
            result = f"[Aliases fixed: {fixes}]\n{result}"
        agent.context.append_tool(result, tc["id"])
