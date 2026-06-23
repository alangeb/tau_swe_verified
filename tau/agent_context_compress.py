"""Context compression algorithms for LLM conversation management.

Eight sequential strategies applied until target size is reached:

1. compress_oversized_tool_redaction — redact single oversized tool results
2. compress_drop_reasoning — strip reasoning fields from assistant messages
3. compress_last_transaction — LLM summarization of completed turns
4. compress_tool_pruning — replace large tool outputs with placeholders (50% boundary)
5. compress_redact_blocks — strip intermediate messages from completed blocks (50% boundary)
6. compress_tool_pruning_full — same as #4 but scans entire context (no boundary)
7. compress_redact_blocks_full — same as #5 but scans entire context (no boundary)
8. compress_full_reset — full context rebuild (last resort)

ARCHITECTURE:

- Fixed 50% boundary: protects recent messages (current task state).
  Steps 1-5 operate within the boundary. Steps 6-7 ignore it.
- Right-to-left scanning: preserves KV cache prefix (step 3).
  Steps 4-7 scan left-to-right from index 0.
- Parameter consistency: same model/tools/params across compression calls.
- Compression prompt stability: prompts must not change between calls.

LOGGING:

- Console: One-liner per pipeline step via compression_step_summary().
- Audit: Per-action detail via audit_writer.compress_start/action/step_end().
- Errors/warnings: Continue via error() / warning() as before.
"""

from pathlib import Path
from typing import Any

# Import directly from leaf modules to break the diamond circular-import:
# agent_context_compress → agent_console → agent_console_messages
# agent_context_compress → agent_llm → agent_console → agent_console_messages
# By importing error & compression_step_summary directly, agent_console is never
# pulled in at module level, so agent_console_messages always finishes loading
# before any path tries to re-import it.
from agent_console_messages import error
from agent_console_display import compression_step_summary
from agent_console_primitives import echo, verbose
from agent_llm import LLMCallConfig, LLMResponse, _invoke_llm_with_retry
from agent_models import Colors

# --- Constants ---

MAX_ITERATIONS = 100
MIN_BLOCK_SIZE = 300
OVERSIZED_THRESHOLD = 0.20
PRUNE_THRESHOLD = 100

__all__ = [
    "compress_context",
]


def _invoke_llm_with_retry_compression(
    client,
    model_name: str,
    messages: list,
    tools: list,
    tool_choice: str | None,
    stream: bool,
    max_retries: int = 5,
    min_response_bytes: int = 10,
    extra_kwargs: dict | None = None,
    log_file: Path | None = None,
) -> LLMResponse:
    """Thin wrapper around ``_invoke_llm_with_retry`` for compression calls."""
    config = LLMCallConfig(
        max_retries=max_retries,
        min_response_bytes=min_response_bytes,
        log_on_failure=True,
        log_file=log_file,
        extra_kwargs=extra_kwargs,
    )

    resp, _compressed = _invoke_llm_with_retry(
        client=client,
        model_name=model_name,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        stream=stream,
        config=config,
    )
    # Compression callers don't need the compressed messages — they pass
    # temporary lists, not the agent's persistent context.

    if not resp.success:
        raise resp.error or RuntimeError("Compression LLM call failed")
    return resp


# --- Helpers ---


def _calculate_context_bytes(context: list[dict]) -> int:
    """Total byte size of serialized context messages."""
    return sum(len(str(m)) for m in context)


def _compute_50_boundary(context: list[dict]) -> tuple[int, int]:
    """Return (boundary_50_bytes, boundary_idx) for the current context."""
    boundary_50_bytes = _calculate_context_bytes(context) // 2
    boundary_idx = _find_50_boundary(context, boundary_50_bytes)
    return boundary_50_bytes, boundary_idx


def _has_unresolved_tool_calls(block: list[dict]) -> bool:
    """True if any assistant tool_call in *block* lacks a matching tool result."""
    pending: set[str] = set()
    for msg in block:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                if isinstance(tc, dict) and tc.get("id"):
                    pending.add(tc["id"])
        elif msg.get("role") == "tool":
            tid = msg.get("tool_call_id")
            if tid:
                pending.discard(tid)
    return bool(pending)


def _has_orphaned_tool_results(block: list[dict]) -> bool:
    """True if any tool result references a tool_call_id from outside *block*."""
    ids_in_block = {
        tc["id"]
        for msg in block
        if msg.get("role") == "assistant"
        for tc in msg.get("tool_calls", [])
        if isinstance(tc, dict) and tc.get("id")
    }
    return any(
        msg.get("tool_call_id") and msg.get("tool_call_id") not in ids_in_block
        for msg in block
        if msg.get("role") == "tool"
    )


def _find_50_boundary(context: list[dict], boundary_bytes: int) -> int:
    """Return the index where cumulative bytes first exceed *boundary_bytes*."""
    cumulative = 0
    for i, msg in enumerate(context):
        cumulative += len(str(msg))
        if cumulative > boundary_bytes:
            return i
    return len(context)


def _find_user_assistant_block(context: list[dict], pointer: int) -> tuple[int | None, int | None, int]:
    """Find a completed turn (user...assistant) scanning right-to-left from *pointer*.

    Returns (user_idx, assistant_idx, block_end) or (None, None, pointer).
    """
    assistant_idx = None
    for i in range(pointer - 1, -1, -1):
        if context[i].get("role") == "assistant" and not context[i].get("tool_calls"):
            assistant_idx = i
            break
    if assistant_idx is None:
        return None, None, pointer

    user_idx = None
    for i in range(assistant_idx - 1, -1, -1):
        if context[i].get("role") == "user":
            user_idx = i
            break
    if user_idx is None:
        return None, None, pointer

    next_user_idx = next(
        (i for i in range(assistant_idx + 1, len(context)) if context[i].get("role") == "user"),
        None,
    )
    block_end = next_user_idx if next_user_idx else len(context)
    return user_idx, assistant_idx, block_end


# --- Metadata helper ---


def _make_metadata(
    step_name: str,
    bytes_before: int,
    bytes_after: int,
    msgs_before: int,
    msgs_after: int,
    actions: list[str],
    status: str,
) -> dict:
    return {
        "step_name": step_name,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "msgs_before": msgs_before,
        "msgs_after": msgs_after,
        "actions": actions,
        "status": status,
    }


# --- Algorithm 1: Oversized Tool Redaction ---


def compress_oversized_tool_redaction(
    context: list[dict],
    client,
    model_name: str,
    target_size_bytes: int,
    verbose: bool = False,
    audit_writer: Any = None,
) -> tuple[list[dict], dict]:
    """Redact tool results >20% of remaining context bytes (within 50% boundary).

    Returns (context, metadata).
    """
    step_name = "OVERSIZED_TOOL_REDACTION"
    original_size = _calculate_context_bytes(context)
    msgs_before = len(context)
    current_context = list(context)
    actions: list[str] = []

    boundary_50_bytes, boundary_idx = _compute_50_boundary(current_context)
    limit = min(boundary_idx, len(current_context) - 1)

    i = 0
    while i <= limit:
        msg = current_context[i]
        if msg.get("role") == "tool":
            msg_bytes = len(str(msg.get("content", "")))
            rest_bytes = _calculate_context_bytes(current_context) - msg_bytes
            if rest_bytes > 0 and msg_bytes / rest_bytes > OVERSIZED_THRESHOLD:
                tool_name = msg.get("name", "unknown")
                pct = msg_bytes / rest_bytes * 100
                redacted_content = (
                    f"[REDACTED: {tool_name} result was {msg_bytes} bytes "
                    f"({pct:.1f}% of context). Content removed by compression.]"
                )
                old_bytes = msg_bytes
                current_context[i] = {
                    "role": "tool",
                    "content": redacted_content,
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "name": msg.get("name", ""),
                }
                new_bytes = len(str(redacted_content))
                savings = old_bytes - new_bytes
                action_desc = f"redacted tool '{tool_name}' at msg {i}: {old_bytes}B → {new_bytes}B"
                actions.append(action_desc)
                if audit_writer is not None:
                    audit_writer.compress_action(step_name, "redact_tool", action_desc)
                if verbose:
                    verbose(f"  :: REDACTED tool@{i}: {tool_name} {old_bytes:,} -> {new_bytes:,} bytes (saved {savings:,})")
                boundary_50_bytes = original_size // 2
                boundary_idx = _find_50_boundary(current_context, boundary_50_bytes)
        i += 1

    final_size = _calculate_context_bytes(current_context)
    msgs_after = len(current_context)
    status = "NO_REDUCTION" if final_size == original_size else "REDUCED"

    if audit_writer is not None:
        audit_writer.compress_start(step_name, original_size, msgs_before)
        audit_writer.compress_step_end(step_name, final_size, msgs_after, status)

    metadata = _make_metadata(step_name, original_size, final_size, msgs_before, msgs_after, actions, status)
    return current_context, metadata


# --- Algorithm 1b: Drop Reasoning ---


def compress_drop_reasoning(
    context: list[dict],
    client,
    model_name: str,
    target_size_bytes: int,
    verbose: bool = False,
    audit_writer: Any = None,
) -> tuple[list[dict], dict]:
    """Drop ``reasoning`` fields from assistant messages within the 50% boundary.

    Keeps all messages intact — only strips the reasoning content.
    Returns (context, metadata).
    """
    step_name = "DROP_REASONING"
    original_size = _calculate_context_bytes(context)
    msgs_before = len(context)
    current_context = list(context)
    actions: list[str] = []

    boundary_50_bytes, boundary_idx = _compute_50_boundary(current_context)

    for i in range(min(boundary_idx, len(current_context))):
        msg = current_context[i]
        if msg.get("role") == "assistant" and "reasoning" in msg:
            reasoning_text = msg["reasoning"]
            reasoning_bytes = len(str(reasoning_text))
            del current_context[i]["reasoning"]
            action_desc = f"dropped reasoning at msg {i}: {reasoning_bytes}B"
            actions.append(action_desc)
            if audit_writer is not None:
                audit_writer.compress_action(step_name, "drop_reasoning", action_desc)
            if verbose:
                verbose(f"  :: DROPPED reasoning@{i}: {reasoning_bytes:,} bytes")

    final_size = _calculate_context_bytes(current_context)
    msgs_after = len(current_context)
    status = "NO_REDUCTION" if final_size == original_size else "REDUCED"

    if audit_writer is not None:
        audit_writer.compress_start(step_name, original_size, msgs_before)
        audit_writer.compress_step_end(step_name, final_size, msgs_after, status)

    metadata = _make_metadata(step_name, original_size, final_size, msgs_before, msgs_after, actions, status)
    return current_context, metadata


# --- Algorithm 2: Last Transaction Compression ---


COMPRESSION_PROMPT = """You are an expert conversation summarizer. Your task is to compress a completed user-assistant interaction while preserving ALL valuable information.

## WHAT TO PRESERVE (CRITICAL):

### 1. USER'S GOAL/INTENT
- What was the user trying to accomplish?

### 2. ASSISTANT'S ACTION PLAN
- What approach did the assistant decide on?
- What tools were selected and why?

### 3. TOOL EXECUTION DETAILS
- Which tools were called with what arguments?
- What were the actual tool results/output?

### 4. KEY FINDINGS & INSIGHTS
- What important information was discovered?

### 5. FILE OPERATIONS (CRITICAL)
- Which files were read, written, created, or modified?
- What were the file paths and key contents?

### 6. COMMANDS RUN & OUTPUTS
- What shell commands were executed?
- What were the key outputs?

### 7. TECHNICAL DECISIONS
- What architectural or implementation decisions were made?

### 8. OPEN QUESTIONS & NEXT STEPS
- What remains unresolved?
- What should the next turn focus on?

### 9. MODEL REASONING / THINKING (if present)
- Some messages may include a "reasoning" field containing the model's
  chain-of-thought.
- If present, either:
  a) Preserve key reasoning steps in your summary, OR
  b) Note that reasoning was present without including verbatim text
- Do NOT discard reasoning content — it contains important decision process
  that may be needed for subsequent tool calls or continuation

## SUMMARY STRUCTURE (markdown format):

## GOAL
[User's objective in 1-2 sentences]

## ACTIONS TAKEN
[What the assistant did, step by step]

## TOOL CALLS & RESULTS
- TOOL_NAME(args) -> [key result or error]

## KEY FINDINGS
[All important discoveries]

## DECISIONS MADE
[Technical choices and their rationale]

## FILES AFFECTED
[File paths and what happened to each]

## COMMANDS EXECUTED
[Commands run with their significance]

## CURRENT STATE
[Where we are now, what's completed, what remains]

## NEXT STEPS
[What should happen next]

## ESSENTIAL REFERENCES
[Any specific code sections, line numbers, concepts]

## MODEL REASONING (if present in original)
[Key reasoning steps or "Model was reasoning internally (content compressed)" if reasoning was present but not critical]

REPLY WITH SUMMARY ONLY - no tool calls, no extra text. DO NOT USE TOOLS! ONLY REPLY FROM MEMORY!"""


def compress_last_transaction(
    context: list[dict],
    client,
    model_name: str,
    target_size_bytes: int,
    tools: list = None,
    extra_kwargs: dict[str, Any] | None = None,
    verbose: bool = False,
    log_file: Path | None = None,
    audit_writer: Any = None,
) -> tuple[list[dict], dict]:
    """Rewrite completed turns (right-to-left, within first 50%) via LLM summary.

    Returns (context, metadata).
    """
    step_name = "LAST_TRANSACTION"
    original_size = _calculate_context_bytes(context)
    msgs_before = len(context)
    current_context = list(context)
    actions: list[str] = []
    blocks_compressed = 0
    blocks_skipped = 0

    boundary_50_bytes, boundary_msg_idx = _compute_50_boundary(current_context)

    if verbose:
        echo(f"{Colors.CYAN}{'='*70}{Colors.RESET}")
        echo(f"{Colors.CYAN}>>> LAST_TRANSACTION START >>>{Colors.RESET}")
        echo(
            f"{Colors.CYAN}  target={target_size_bytes:,} bytes, current={_calculate_context_bytes(current_context):,} bytes, 50%={original_size // 2:,} bytes{Colors.RESET}"
        )
        echo(
            f"{Colors.CYAN}  50% boundary at msg index {boundary_msg_idx} (of {len(context)-1}){Colors.RESET}"
        )
        echo(f"{Colors.CYAN}{'='*70}{Colors.RESET}")

    pointer = len(current_context)

    iteration = 0
    while _calculate_context_bytes(current_context) > target_size_bytes and iteration < MAX_ITERATIONS:
        iteration += 1

        user_idx, assistant_idx, block_end = _find_user_assistant_block(current_context, pointer)

        if user_idx is None:
            if verbose:
                verbose(f"  :: Iteration {iteration}: no completed block found before pointer={pointer} - STOP")
            break

        if boundary_msg_idx is not None and user_idx >= boundary_msg_idx:
            blocks_skipped += 1
            if verbose:
                verbose(
                    f"  :: Iteration {iteration}: user@{user_idx} at or right of 50% boundary@{boundary_msg_idx} - SKIP, scanning left"
                )
            pointer = user_idx
            continue

        block = current_context[user_idx:block_end]
        assistant_tool_part = current_context[user_idx + 1:block_end]
        block_bytes = _calculate_context_bytes(block)
        user_msg = current_context[user_idx]

        if verbose:
            verbose(f"  :: Iteration {iteration}: block [{user_idx}:{block_end}] = {block_bytes} bytes ({len(block)} msgs)")

        skip_reason = None
        if not any(m.get("role") == "tool" for m in assistant_tool_part):
            skip_reason = "no tool calls in block"
        elif _has_unresolved_tool_calls(block):
            skip_reason = "unresolved tool calls inside block"
        elif _has_orphaned_tool_results(block):
            skip_reason = "orphaned tool results referencing assistants outside block"
        elif block_bytes < MIN_BLOCK_SIZE:
            skip_reason = f"block too small ({block_bytes} bytes < {MIN_BLOCK_SIZE})"

        if skip_reason:
            blocks_skipped += 1
            if verbose:
                verbose(f"  :: SKIP (reason: {skip_reason}) - keeping block as-is, moving left")
            pointer = user_idx
            continue

        if verbose:
            verbose(
                f"  :: Calling LLM to compress {len(assistant_tool_part)} messages ({_calculate_context_bytes(assistant_tool_part):,} bytes)..."
            )

        context_for_summary = [
            {"role": "system", "content": COMPRESSION_PROMPT},
            {
                "role": "user",
                "content": "Compress the following conversation:\n\n"
                + "\n".join(
                    f"[{m.get('role', 'unknown').upper()}]\n"
                    f"content: {m.get('content', '')}\n"
                    f"reasoning: {m.get('reasoning', '')}"
                    for m in assistant_tool_part
                ),
            },
        ]

        try:
            resp = _invoke_llm_with_retry_compression(
                client, model_name, context_for_summary, tools, "auto",
                stream=False, extra_kwargs=extra_kwargs, log_file=log_file,
            )
            response = resp.raw
            response_text = resp.text

            if not response or not response.choices or not response_text or not response_text.strip():
                blocks_skipped += 1
                if verbose:
                    verbose("  :: LLM returned empty/invalid - keeping block as-is, moving left")
                pointer = user_idx
                continue

            summary = response_text.strip()
            new_block = [
                user_msg,
                {"role": "assistant", "content": summary},
            ]
            new_block_bytes = _calculate_context_bytes(new_block)

            if new_block_bytes >= block_bytes:
                blocks_skipped += 1
                if verbose:
                    verbose(
                        f"  :: Compression not beneficial: {block_bytes:,} -> {new_block_bytes:,} bytes - keeping block as-is, moving left"
                    )
                pointer = user_idx
                continue

            savings = block_bytes - new_block_bytes
            blocks_compressed += 1
            action_desc = f"compressed block [{user_idx}:{block_end}]: {block_bytes}B → {new_block_bytes}B (saved {savings}B)"
            actions.append(action_desc)
            if audit_writer is not None:
                audit_writer.compress_action(step_name, "compress_block", action_desc)
            if verbose:
                verbose(
                    f"  :: COMPRESSED [{user_idx}:{block_end}]: {block_bytes:,} -> {new_block_bytes:,} bytes (saved {savings:,})"
                )
            current_context = (
                current_context[:user_idx] + new_block + current_context[block_end:]
            )

        except Exception as e:
            blocks_skipped += 1
            if verbose:
                verbose(f"  :: LLM error: {e} - keeping block as-is, moving left")

        pointer = user_idx

    final_size = _calculate_context_bytes(current_context)
    msgs_after = len(current_context)
    status = "ACHIEVED" if final_size <= target_size_bytes else "NO_MORE_BLOCKS"

    if audit_writer is not None:
        audit_writer.compress_start(step_name, original_size, msgs_before)
        audit_writer.compress_step_end(step_name, final_size, msgs_after, status)

    if verbose:
        compression_end_msg = f"[COMPRESS] LAST_TRANSACTION: {msgs_after} msgs, {final_size:,} bytes -> target {target_size_bytes / original_size * 100 if original_size > 0 else 0:.0f}% ({target_size_bytes:,} bytes) [{status}]"
        verbose(compression_end_msg)

    metadata = _make_metadata(step_name, original_size, final_size, msgs_before, msgs_after, actions, status)
    return current_context, metadata


# --- Algorithm 2b: Tool Pruning (with 50% boundary) ---


def compress_tool_pruning(
    context: list[dict],
    client,
    model_name: str,
    target_size_bytes: int,
    verbose: bool = False,
    audit_writer: Any = None,
) -> tuple[list[dict], dict]:
    """Replace tool outputs >100 bytes (within 50% boundary) with a placeholder.

    Returns (context, metadata).
    """
    step_name = "TOOL_PRUNING"
    original_size = _calculate_context_bytes(context)
    msgs_before = len(context)
    current_context = list(context)
    actions: list[str] = []
    tools_pruned = 0

    boundary_50_bytes, boundary_idx = _compute_50_boundary(current_context)

    iteration = 0
    while _calculate_context_bytes(current_context) > target_size_bytes:
        iteration += 1
        if iteration > MAX_ITERATIONS:
            if verbose:
                error("  :: ITERATION LIMIT REACHED (100), STOPPING")
            break

        if verbose:
            echo(f"{Colors.YELLOW}{'-'*70}{Colors.RESET}")
            echo(
                f"  :: ITERATION #{iteration} START: current_size={_calculate_context_bytes(current_context):,} bytes, target={target_size_bytes:,} bytes"
            )

        for i in range(boundary_idx + 1):
            msg = current_context[i]
            if msg.get("role") == "tool":
                tool_content = msg.get("content", "")
                tool_bytes = len(str(tool_content))

                if tool_bytes > PRUNE_THRESHOLD:
                    tool_name = msg.get("name", "unknown")
                    if verbose:
                        verbose(f"  :: Found prunable tool at msg #{i}, content_size={tool_bytes:,} bytes")

                    current_context[i] = {
                        "role": "tool",
                        "content": "COMPRESSION: CALL RESULT NO LONGER AVAILABLE",
                        "tool_call_id": current_context[i].get("tool_call_id", ""),
                        "name": current_context[i].get("name", ""),
                    }

                    new_bytes = len("COMPRESSION: CALL RESULT NO LONGER AVAILABLE")
                    savings = tool_bytes - new_bytes
                    tools_pruned += 1
                    action_desc = f"pruned tool '{tool_name}' at msg {i}: {tool_bytes}B → {new_bytes}B"
                    actions.append(action_desc)
                    if audit_writer is not None:
                        audit_writer.compress_action(step_name, "prune_tool", action_desc)

                    if verbose:
                        verbose(f"  :: TOOL_PRUNED msg #{i}: SAVED {savings:,} bytes")

                    break
        else:
            # No prunable tool found in this iteration
            if verbose:
                verbose("  :: No more tools with content > 100 bytes found within boundary")
            break

    final_size = _calculate_context_bytes(current_context)
    msgs_after = len(current_context)
    status = "ACHIEVED" if final_size <= target_size_bytes else "NO_MORE_TOOLS"

    if audit_writer is not None:
        audit_writer.compress_start(step_name, original_size, msgs_before)
        audit_writer.compress_step_end(step_name, final_size, msgs_after, status)

    metadata = _make_metadata(step_name, original_size, final_size, msgs_before, msgs_after, actions, status)
    return current_context, metadata


# --- Algorithm 6: Tool Pruning Full (no boundary) ---


def compress_tool_pruning_full(
    context: list[dict],
    client,
    model_name: str,
    target_size_bytes: int,
    verbose: bool = False,
    audit_writer: Any = None,
) -> tuple[list[dict], dict]:
    """Replace tool outputs >100 bytes across the ENTIRE context (no 50% boundary).

    Same logic as compress_tool_pruning but scans all the way to the end.
    Returns (context, metadata).
    """
    step_name = "TOOL_PRUNING_FULL"
    original_size = _calculate_context_bytes(context)
    msgs_before = len(context)
    current_context = list(context)
    actions: list[str] = []
    tools_pruned = 0

    iteration = 0
    while _calculate_context_bytes(current_context) > target_size_bytes:
        iteration += 1
        if iteration > MAX_ITERATIONS:
            if verbose:
                error("  :: ITERATION LIMIT REACHED (100), STOPPING")
            break

        if verbose:
            echo(f"{Colors.YELLOW}{'-'*70}{Colors.RESET}")
            echo(
                f"  :: ITERATION #{iteration} START: current_size={_calculate_context_bytes(current_context):,} bytes, target={target_size_bytes:,} bytes"
            )

        for i in range(len(current_context)):
            msg = current_context[i]
            if msg.get("role") == "tool":
                tool_content = msg.get("content", "")
                tool_bytes = len(str(tool_content))

                if tool_bytes > PRUNE_THRESHOLD:
                    tool_name = msg.get("name", "unknown")
                    if verbose:
                        verbose(f"  :: Found prunable tool at msg #{i}, content_size={tool_bytes:,} bytes")

                    current_context[i] = {
                        "role": "tool",
                        "content": "COMPRESSION: CALL RESULT NO LONGER AVAILABLE",
                        "tool_call_id": current_context[i].get("tool_call_id", ""),
                        "name": current_context[i].get("name", ""),
                    }

                    new_bytes = len("COMPRESSION: CALL RESULT NO LONGER AVAILABLE")
                    savings = tool_bytes - new_bytes
                    tools_pruned += 1
                    action_desc = f"pruned tool '{tool_name}' at msg {i}: {tool_bytes}B → {new_bytes}B"
                    actions.append(action_desc)
                    if audit_writer is not None:
                        audit_writer.compress_action(step_name, "prune_tool", action_desc)

                    if verbose:
                        verbose(f"  :: TOOL_PRUNED msg #{i}: SAVED {savings:,} bytes")

                    break
        else:
            # No prunable tool found in this iteration
            if verbose:
                verbose("  :: No more tools with content > 100 bytes found in context")
            break

    final_size = _calculate_context_bytes(current_context)
    msgs_after = len(current_context)
    status = "ACHIEVED" if final_size <= target_size_bytes else "NO_MORE_TOOLS"

    if audit_writer is not None:
        audit_writer.compress_start(step_name, original_size, msgs_before)
        audit_writer.compress_step_end(step_name, final_size, msgs_after, status)

    metadata = _make_metadata(step_name, original_size, final_size, msgs_before, msgs_after, actions, status)
    return current_context, metadata


# --- Algorithm 3: Redact Blocks (with 50% boundary) ---


def compress_redact_blocks(
    context: list[dict],
    client,
    model_name: str,
    target_size_bytes: int,
    verbose: bool = False,
    audit_writer: Any = None,
) -> tuple[list[dict], dict]:
    """Strip intermediate messages from completed blocks, keeping only USER + ASSISTANT.

    Returns (context, metadata).
    """
    step_name = "REDACT_BLOCKS"
    original_size = _calculate_context_bytes(context)
    msgs_before = len(context)
    current_context = list(context)
    actions: list[str] = []
    blocks_redacted = 0

    boundary_50_bytes, boundary_idx = _compute_50_boundary(current_context)

    iteration = 0
    while _calculate_context_bytes(current_context) > target_size_bytes:
        iteration += 1
        if iteration > MAX_ITERATIONS:
            if verbose:
                error("  :: ITERATION LIMIT REACHED (100), STOPPING")
            break

        if verbose:
            echo(f"{Colors.YELLOW}{'-'*70}{Colors.RESET}")
            echo(
                f"  :: ITERATION #{iteration} START: current_size={_calculate_context_bytes(current_context):,} bytes, target={target_size_bytes:,} bytes"
            )

        found_block = False
        i = 0
        while i <= boundary_idx:
            if current_context[i].get("role") == "user":
                user_idx = i
                assistant_idx = None

                for j in range(i + 1, boundary_idx):
                    if current_context[j].get("role") == "user":
                        break
                    if current_context[j].get("role") == "assistant" and not current_context[j].get("tool_calls"):
                        assistant_idx = j
                        break

                if assistant_idx is not None:
                    block = current_context[user_idx:assistant_idx + 1]
                    block_bytes = _calculate_context_bytes(block)

                    if block_bytes < MIN_BLOCK_SIZE:
                        if verbose:
                            verbose(f"     ✗ Block too small (< {MIN_BLOCK_SIZE} bytes) - SKIP")
                        i = assistant_idx + 1
                        continue

                    if _has_unresolved_tool_calls(block):
                        if verbose:
                            verbose("     ✗ Block contains unresolved tool calls - SKIP")
                        i = assistant_idx + 1
                        continue

                    if _has_orphaned_tool_results(block):
                        if verbose:
                            verbose("     ✗ Block contains orphaned tool results - SKIP")
                        i = assistant_idx + 1
                        continue

                    removed_count = len(block) - 2
                    blocks_redacted += 1
                    new_block = [
                        current_context[user_idx],
                        current_context[assistant_idx],
                    ]
                    new_bytes = _calculate_context_bytes(new_block)
                    savings = block_bytes - new_bytes
                    action_desc = f"redacted block [{user_idx}:{assistant_idx}]: {block_bytes}B → {new_bytes}B (removed {removed_count} intermediates)"
                    actions.append(action_desc)
                    if audit_writer is not None:
                        audit_writer.compress_action(step_name, "redact_block", action_desc)

                    if verbose:
                        verbose(f"  :: BLOCK #{user_idx}-{assistant_idx} REDACTED: SAVED {savings:,} bytes (removed {removed_count} intermediates)")

                    current_context = (
                        current_context[:user_idx]
                        + new_block
                        + current_context[assistant_idx + 1:]
                    )
                    found_block = True
                    break

            i += 1

        if not found_block:
            if verbose:
                verbose("  :: No more completed blocks to redact within boundary")
            break

    final_size = _calculate_context_bytes(current_context)
    msgs_after = len(current_context)
    status = "ACHIEVED" if final_size <= target_size_bytes else "NO_MORE_BLOCKS"

    if audit_writer is not None:
        audit_writer.compress_start(step_name, original_size, msgs_before)
        audit_writer.compress_step_end(step_name, final_size, msgs_after, status)

    metadata = _make_metadata(step_name, original_size, final_size, msgs_before, msgs_after, actions, status)
    return current_context, metadata


# --- Algorithm 7: Redact Blocks Full (no boundary) ---


def compress_redact_blocks_full(
    context: list[dict],
    client,
    model_name: str,
    target_size_bytes: int,
    verbose: bool = False,
    audit_writer: Any = None,
) -> tuple[list[dict], dict]:
    """Strip intermediate messages from completed blocks across the ENTIRE context (no 50% boundary).

    Same logic as compress_redact_blocks but scans all the way to the end.
    Returns (context, metadata).
    """
    step_name = "REDACT_BLOCKS_FULL"
    original_size = _calculate_context_bytes(context)
    msgs_before = len(context)
    current_context = list(context)
    actions: list[str] = []
    blocks_redacted = 0

    iteration = 0
    while _calculate_context_bytes(current_context) > target_size_bytes:
        iteration += 1
        if iteration > MAX_ITERATIONS:
            if verbose:
                error("  :: ITERATION LIMIT REACHED (100), STOPPING")
            break

        if verbose:
            echo(f"{Colors.YELLOW}{'-'*70}{Colors.RESET}")
            echo(
                f"  :: ITERATION #{iteration} START: current_size={_calculate_context_bytes(current_context):,} bytes, target={target_size_bytes:,} bytes"
            )

        found_block = False
        i = 0
        while i < len(current_context):
            if current_context[i].get("role") == "user":
                user_idx = i
                assistant_idx = None

                for j in range(i + 1, len(current_context)):
                    if current_context[j].get("role") == "user":
                        break
                    if current_context[j].get("role") == "assistant" and not current_context[j].get("tool_calls"):
                        assistant_idx = j
                        break

                if assistant_idx is not None:
                    block = current_context[user_idx:assistant_idx + 1]
                    block_bytes = _calculate_context_bytes(block)

                    if block_bytes < MIN_BLOCK_SIZE:
                        if verbose:
                            verbose(f"     ✗ Block too small (< {MIN_BLOCK_SIZE} bytes) - SKIP")
                        i = assistant_idx + 1
                        continue

                    if _has_unresolved_tool_calls(block):
                        if verbose:
                            verbose("     ✗ Block contains unresolved tool calls - SKIP")
                        i = assistant_idx + 1
                        continue

                    if _has_orphaned_tool_results(block):
                        if verbose:
                            verbose("     ✗ Block contains orphaned tool results - SKIP")
                        i = assistant_idx + 1
                        continue

                    removed_count = len(block) - 2
                    blocks_redacted += 1
                    new_block = [
                        current_context[user_idx],
                        current_context[assistant_idx],
                    ]
                    new_bytes = _calculate_context_bytes(new_block)
                    savings = block_bytes - new_bytes
                    action_desc = f"redacted block [{user_idx}:{assistant_idx}]: {block_bytes}B → {new_bytes}B (removed {removed_count} intermediates)"
                    actions.append(action_desc)
                    if audit_writer is not None:
                        audit_writer.compress_action(step_name, "redact_block", action_desc)

                    if verbose:
                        verbose(f"  :: BLOCK #{user_idx}-{assistant_idx} REDACTED: SAVED {savings:,} bytes (removed {removed_count} intermediates)")

                    current_context = (
                        current_context[:user_idx]
                        + new_block
                        + current_context[assistant_idx + 1:]
                    )
                    found_block = True
                    break

            i += 1

        if not found_block:
            if verbose:
                verbose("  :: No more completed blocks to redact in context")
            break

    final_size = _calculate_context_bytes(current_context)
    msgs_after = len(current_context)
    status = "ACHIEVED" if final_size <= target_size_bytes else "NO_MORE_BLOCKS"

    if audit_writer is not None:
        audit_writer.compress_start(step_name, original_size, msgs_before)
        audit_writer.compress_step_end(step_name, final_size, msgs_after, status)

    metadata = _make_metadata(step_name, original_size, final_size, msgs_before, msgs_after, actions, status)
    return current_context, metadata


# --- Algorithm 4: Full Reset (Last Resort) ---


SUMMARY_PROMPT = """You are an expert conversation summarizer. Summarize everything that has been accomplished so far in this conversation.

Focus on:

- Key accomplishments and results

- Files modified and their contents

- Commands executed and their outputs

- Decisions made

- Current state of the work

Be comprehensive but concise. Include all critical information that would be needed to continue this work."""


PLAN_PROMPT = """Based on the conversation history, what are the next steps needed to complete the task?
Provide a clear plan with concrete actions."""


def compress_full_reset(
    context: list[dict],
    client,
    model_name: str,
    target_size_bytes: int,
    tools: list = None,
    extra_kwargs: dict[str, Any] | None = None,
    verbose: bool = False,
    log_file: Path | None = None,
    audit_writer: Any = None,
) -> tuple[list[dict], dict]:
    """Full context rebuild: LLM generates summary + plan, replaces entire context.

    Returns (context, metadata).
    """
    step_name = "FULL_RESET"
    original_size = _calculate_context_bytes(context)
    msgs_before = len(context)
    actions: list[str] = []

    system_msg = context[0] if context and context[0].get("role") == "system" else None

    first_user_idx = next((i for i, msg in enumerate(context) if msg.get("role") == "user"), None)
    if first_user_idx is None:
        if verbose:
            error("  :: No user prompt found! Returning unchanged context.")
        metadata = _make_metadata(step_name, original_size, original_size, msgs_before, msgs_before, [], "FAILED_NO_USER")
        if audit_writer is not None:
            audit_writer.compress_start(step_name, original_size, msgs_before)
            audit_writer.compress_step_end(step_name, original_size, msgs_before, "FAILED_NO_USER")
        return context, metadata

    first_user_content = context[first_user_idx].get("content", "")

    # LLM Request 1: summary
    context_for_summary = (
        [{"role": "system", "content": SUMMARY_PROMPT},
         {"role": "user", "content": "Summarize everything you have done so far:"}]
        + context[1:first_user_idx]
        + [context[first_user_idx]]
    )

    try:
        resp = _invoke_llm_with_retry_compression(
            client, model_name, context_for_summary, tools, "auto",
            stream=False, extra_kwargs=extra_kwargs, log_file=log_file,
        )
        summary = resp.text.strip() if resp.text else ""
        if verbose:
            verbose(f"  :: LLM SUMMARY received ({len(summary):,} bytes)")
    except Exception as e:
        if verbose:
            error(f"  :: LLM SUMMARY FAILED: {e}")
        summary = ""

    # LLM Request 2: next steps plan
    context_for_plan = [
        {"role": "system", "content": PLAN_PROMPT},
        {
            "role": "user",
            "content": f"Current state summary:\n\n{summary}\n\nTell me about next steps to finish.",
        },
    ]

    try:
        resp = _invoke_llm_with_retry_compression(
            client, model_name, context_for_plan, tools, "auto",
            stream=False, extra_kwargs=extra_kwargs, log_file=log_file,
        )
        plan = resp.text.strip() if resp.text else ""
        if verbose:
            verbose(f"  :: LLM NEXT STEPS received ({len(plan):,} bytes)")
    except Exception as e:
        if verbose:
            error(f"  :: LLM NEXT STEPS FAILED: {e}")
        plan = ""

    new_content = f"""# COMPREHENSION SUMMARY

{summary}

# NEXT STEPS & PLAN

{plan}

# ORIGINAL USER REQUEST

{first_user_content}"""

    if not summary and not plan:
        if verbose:
            error("  :: FULL_RESET FAILED - Both summary and plan empty. Returning unchanged context.")
        metadata = _make_metadata(step_name, original_size, original_size, msgs_before, msgs_before, [], "FAILED_EMPTY")
        if audit_writer is not None:
            audit_writer.compress_start(step_name, original_size, msgs_before)
            audit_writer.compress_step_end(step_name, original_size, msgs_before, "FAILED_EMPTY")
        return context, metadata

    new_context = []
    if system_msg:
        new_context.append(system_msg)
    new_context.append({"role": "user", "content": new_content})

    new_bytes = _calculate_context_bytes(new_context)
    msgs_after = len(new_context)
    action_desc = f"full reset: {msgs_before} msgs ({original_size}B) → {msgs_after} msgs ({new_bytes}B)"
    actions.append(action_desc)

    if verbose:
        verbose(f"  :: NEW CONTEXT: {len(new_context)} msgs, {new_bytes:,} bytes (from {len(context)} msgs)")

    status = "RESET"

    if audit_writer is not None:
        audit_writer.compress_start(step_name, original_size, msgs_before)
        audit_writer.compress_action(step_name, "full_reset", action_desc)
        audit_writer.compress_step_end(step_name, new_bytes, msgs_after, status)

    metadata = _make_metadata(step_name, original_size, new_bytes, msgs_before, msgs_after, actions, status)
    return new_context, metadata


# --- Orchestrator ---


def _format_success(
    name: str,
    size: int,
    original_size: int,
    target_percentage: float,
    msg_count: int,
    algorithms_used: list[str],
    verbose: bool,
) -> tuple[str, dict]:
    """Build success summary string and metadata dict."""
    summary = f"✓ COMPRESSION ACHIEVED by {name} ({size:,} bytes)"
    if verbose:
        echo(f"\n{Colors.GREEN}{'='*70}{Colors.RESET}")
        echo(f"{Colors.GREEN}  COMPRESSION COMPLETE - EARLY SUCCESS!{Colors.RESET}")
        echo(f"{Colors.GREEN}{'='*70}{Colors.RESET}\n")
        echo(
            f"{Colors.GREEN}    Original: {original_size:,} bytes -> {size:,} bytes{Colors.RESET}"
        )
        echo(
            f"{Colors.GREEN}    Reduction: {(1 - size / original_size) * 100 if original_size > 0 else 0:.1f}% (target was {target_percentage*100:.0f}%) {Colors.RESET}"
        )
        echo(
            f"{Colors.GREEN}    Algorithms used: {' + '.join(algorithms_used)}{Colors.RESET}"
        )
        echo(
            f"{Colors.GREEN}    Final context: {size:,} bytes, {msg_count} messages{Colors.RESET}\n"
        )
    metadata = {
        "bytes_before": original_size,
        "bytes_after": size,
        "algorithms_used": algorithms_used,
    }
    return summary, metadata


def _build_action_summary(actions: list[str]) -> str:
    """Build a concise action summary string from the actions list."""
    if not actions:
        return "no changes"
    if len(actions) <= 3:
        return "; ".join(actions)
    return f"{actions[0]}; {actions[1]} ... ({len(actions)} total)"


def compress_context(
    context: list[dict],
    client,
    model_name: str,
    target_percentage: float,
    tools: list,
    extra_kwargs: dict | None = None,
    verbose: bool = False,
    log_file: Path | None = None,
    audit_writer: Any = None,
) -> tuple[list[dict], str, dict[str, Any]]:
    """Run compression algorithms in sequence until target size is reached.

    Pipeline (least to most aggressive):
    1. OVERSIZED_TOOL_REDACTION — redact single oversized tool results
    2. DROP_REASONING — strip reasoning fields from assistant messages
    3. LAST_TRANSACTION — LLM summarization of completed turns
    4. TOOL_PRUNING — replace large tool outputs with placeholders (50% boundary)
    5. REDACT_BLOCKS — strip intermediate messages from completed blocks (50% boundary)
    6. TOOL_PRUNING_FULL — same as #4 but scans entire context (no boundary)
    7. REDACT_BLOCKS_FULL — same as #5 but scans entire context (no boundary)
    8. FULL_RESET — full context rebuild (last resort)

    Stops early if target is reached.  Original context preserved if all fail.
    """
    original_size = _calculate_context_bytes(context)
    original_message_count = len(context)
    target_size = int(original_size * (1 - target_percentage))

    if verbose:
        echo(f"[COMPRESS] ORCHESTRATOR: {original_message_count} msgs, {original_size:,} bytes → target {target_percentage*100:.0f}% ({target_size:,} bytes)")

    result = list(context)
    algorithms_used: list[str] = []
    bytes_per_algo: dict[str, int] = {}

    # Define compression pipeline with consistent signatures
    # Each function returns (context, metadata)
    pipeline = [
        ("OVERSIZED_TOOL_REDACTION", lambda ctx: compress_oversized_tool_redaction(ctx, client, model_name, target_size, verbose, audit_writer)),
        ("DROP_REASONING", lambda ctx: compress_drop_reasoning(ctx, client, model_name, target_size, verbose, audit_writer)),
        ("LAST_TRANSACTION", lambda ctx: compress_last_transaction(ctx, client, model_name, target_size, tools, extra_kwargs, verbose, log_file=log_file, audit_writer=audit_writer)),
        ("TOOL_PRUNING", lambda ctx: compress_tool_pruning(ctx, client, model_name, target_size, verbose, audit_writer)),
        ("REDACT_BLOCKS", lambda ctx: compress_redact_blocks(ctx, client, model_name, target_size, verbose, audit_writer)),
        ("TOOL_PRUNING_FULL", lambda ctx: compress_tool_pruning_full(ctx, client, model_name, target_size, verbose, audit_writer)),
        ("REDACT_BLOCKS_FULL", lambda ctx: compress_redact_blocks_full(ctx, client, model_name, target_size, verbose, audit_writer)),
    ]

    total_steps = len(pipeline) + 1  # +1 for FULL_RESET

    for idx, (name, run_algo) in enumerate(pipeline, 1):
        result, step_metadata = run_algo(result)
        size = step_metadata["bytes_after"]
        msg_count = step_metadata["msgs_after"]
        action_summary = _build_action_summary(step_metadata["actions"])

        # Always emit console one-liner for this step
        compression_step_summary(
            step_name=step_metadata["step_name"],
            step_idx=idx,
            total_steps=total_steps,
            bytes_before=step_metadata["bytes_before"],
            msgs_before=step_metadata["msgs_before"],
            bytes_after=step_metadata["bytes_after"],
            msgs_after=step_metadata["msgs_after"],
            action_summary=action_summary,
            status=step_metadata["status"],
        )

        if verbose:
            verbose(f"[STEP {idx}/{total_steps} {name}] Entry: {step_metadata['msgs_before']} msgs, {step_metadata['bytes_before']:,} bytes")
            verbose(f"[STEP {idx}/{total_steps} {name}] Exit: {msg_count} msgs, {size:,} bytes")

        algorithms_used.append(name)
        bytes_per_algo[name] = step_metadata["bytes_before"] - step_metadata["bytes_after"]

        if size <= target_size:
            summary, metadata = _format_success(
                name, size, original_size, target_percentage, msg_count,
                algorithms_used, verbose,
            )
            return result, summary, metadata

    # Last resort: full reset
    result, step_metadata = compress_full_reset(
        result, client, model_name, target_size, tools, extra_kwargs, verbose,
        log_file=log_file, audit_writer=audit_writer,
    )
    size = step_metadata["bytes_after"]
    msg_count = step_metadata["msgs_after"]
    action_summary = _build_action_summary(step_metadata["actions"])

    # Always emit console one-liner for full reset
    compression_step_summary(
        step_name=step_metadata["step_name"],
        step_idx=total_steps,
        total_steps=total_steps,
        bytes_before=step_metadata["bytes_before"],
        msgs_before=step_metadata["msgs_before"],
        bytes_after=step_metadata["bytes_after"],
        msgs_after=step_metadata["msgs_after"],
        action_summary=action_summary,
        status=step_metadata["status"],
    )

    if verbose:
        verbose(f"[STEP {total_steps}/{total_steps} FULL_RESET] Exit: {msg_count} msgs, {size:,} bytes")

    algorithms_used.append("FULL_RESET")

    if verbose and bytes_per_algo:
        for algo_name, saved in bytes_per_algo.items():
            verbose(f"  :: {algo_name}: saved {saved:,} bytes")

    final_summary = f"FINAL: Full context reset ({size:,} bytes)"

    if verbose:
        echo(f"\n{'='*70}")
        echo("  COMPRESSION COMPLETE - FINAL")
        echo(f"{'='*70}\n")
        reduction_pct = (1 - size / original_size) * 100 if original_size > 0 else 0
        verbose(f"    Original: {original_size:,} bytes -> {size:,} bytes")
        verbose(f"    Reduction: {reduction_pct:.1f}% (target was {target_percentage*100:.0f}%)")
        verbose("    Algorithms used: ALL 4 (FULL_RESET was decisive)")
        verbose(f"    Final context: {size:,} bytes, {msg_count} messages\n")

    metadata = {
        "bytes_before": original_size,
        "bytes_after": size,
        "algorithms_used": algorithms_used,
        "bytes_per_algo": bytes_per_algo,
    }
    return result, final_summary, metadata
