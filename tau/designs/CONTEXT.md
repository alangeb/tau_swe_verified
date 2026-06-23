# Context Management

## Common Patterns

```python
# Append messages (maintains alternation)
agent.context.append_user("User message")
agent.context.append_assistant("Assistant response", tool_calls=None)
agent.context.append_tool("Tool result", tool_call_id="xxx")

# Check context size
tokens = agent.context.estimate_tokens(pending_tokens=0)
agent.context.compress(0.30, agent, tools)  # Target 30% reduction
```

## Synthetic Bridge Cleanup & Explicit Merge

After synthetic bridges are removed, consecutive assistant messages may appear.
The merge is **explicit** — the caller decides when to merge.

```python
# Remove synthetic bridges only (no automatic merge)
ctx.cleanup_synthetic()

# Explicitly merge consecutive assistant messages (optional, caller decides)
ctx.merge_consecutive_assistants()
```

**Merge behavior:** `merge_consecutive_assistants()` merges assistant and user messages.
Consecutive assistant messages are merged (content, tool_calls deduplicated by ID, reasoning, refusal, usage_metadata summed).
Consecutive user messages are merged gracefully (content concatenated) with a warning logged.
Consecutive tool messages are NOT merged — each tool result has a unique tool_call_id/name.

- **assistant**: Merge `content`, `tool_calls` (deduplicated by ID), `reasoning`, `refusal`, `usage_metadata` (summed)
- **user**: Merge `content` (concatenated with newline), log warning
- **tool**: NOT merged — preserved as separate messages (batched tool calls)

**Used in `close_turn()`:**
```python
def close_turn(self, reason):
    self.cleanup_synthetic()           # remove bridges
    self.merge_consecutive_assistants()  # explicit merge after cleanup
```

## Subagent Invocation

```python
# Fork — inherits full context
from agent_subagent import invoke_fork_sync
result = invoke_fork_sync(
    prompt="Review the changes",
    parent_context=agent.context,
    parent_agent=agent,
    nesting_count=0,
    tool_call_id=None,
    tool_filter=None,
    config=None,
    nesting_threshold=2,
)

# Subagent — blank slate
from agent_subagent import invoke_subagent_sync
result = invoke_subagent_sync(
    prompt="Write a unit test",
    system_prompt="You are a testing assistant",
    parent_agent=agent,
    nesting_count=0,
    tool_filter=None,
    config=None,
    nesting_threshold=2,
)
```

## End-Turn Recovery

The agent uses a recovery mechanism to handle cases where the model returns
plain text without calling `end_turn`.

**Normal flow:**
- Model returns text with tool calls → tools execute, loop continues
- Model returns text with `end_turn` → turn ends immediately
- Model returns plain text (no tools, no `end_turn`) → recovery is triggered

**Recovery flow (when `_recovery_active` is True):**
- Model must explicitly call `end_turn` to end the turn
- `last_substantive_response` is locked (not overwritten during recovery)
- Reminder includes first 40 chars of stored response as preview
- If recovery budget exhausted, best-effort response is returned

**Key invariants:**
- `_recovery_active` resets at start of each `invoke_with_tools_loop()`
- `_recovery_active` is set to `True` in `_recover_from_missing_end_turn()`
- `last_substantive_response` only updates when NOT in recovery mode
- Empty responses still trigger recovery (don't short-circuit)
- Content-only end-of-turn was REVERTED (broke subagent tests) — all responses require `end_turn`
