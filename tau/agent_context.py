"""Conversation context management for TauBot.

Core `TauContext` class maintains OpenAI-compatible conversation context with
strict validation for API compliance.

DESIGN INVARIANT: Message alternation is maintained via synthetic bridges.
See designs/DECISIONS.md §18 (Context Management) for the architectural rationale.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from agent_core import TauBot

from agent_console import context_append_warning, context_validation_warning
from agent_console_primitives import _role_color
from agent_models import Colors

# --- Module-level helpers ---

# Fork context markers — short, keyword-prefixed strings placed into tool results
# when preparing a fork's context. Kept concise to save tokens.
_PENDING_TOOL_MARKER = "[PENDING: deferred; resolves after fork. Do not assume result.]"
_FORK_TOOL_MARKER = "[FORK: You are the fork. THIS IS SYNCHRONOUS — you block until complete, then return your result directly. There is NO background execution, NO 'reporting back later'. You are the fork. Task: {task}]"

# ── Synthetic message protocol ──────────────────────────────────────────────────
# All system-injected messages use this prefix so they can be reliably detected
# and excluded from user-boundary calculations (undo, get_last_real_user_prompt).
# Format: [SYSTEM-SYNTHETIC: <category>] <content>
# Categories: end_turn_reminder, escalation, reflection, turn_started,
#             turn_closed, command, recovery
_SYNTHETIC_PREFIX = "[SYSTEM-SYNTHETIC: "

__all__ = ["TauContext", "ContextMessage", "TauContextInstance",
           "get_last_real_user_prompt", "is_synthetic_message",
           "make_synthetic_user"]

ContextMessage: TypeAlias = dict[str, Any]
TauContextInstance: TypeAlias = "TauContext"


def _emit_context_validation_warning(*message_lines: str) -> None:
    """Emit a validation warning to the console."""
    context_validation_warning(list(message_lines))


# ── TauContext ────────────────────────────────────────────────────────────────

class TauContext:
    """In-place conversation context with validation after every mutation.

    Each TauBot owns exactly one instance. Fork metadata is stored separately
    from the message list to avoid polluting conversation history.
    """

    def __init__(self, messages: list[dict] | None = None):
        self._messages: list[dict] = list(messages) if messages else []
        self._fork_metadata: dict[str, Any] = {
            "pending_tool_ids": set(),
            "fork_tool_call_id": None,
            "fork_task": None,
        }
        self._validate_on_mutation()

    # --- List protocol ---
    def __len__(self) -> int:
        return len(self._messages)

    def __iter__(self):
        return iter(self._messages)

    def __getitem__(self, idx: int) -> dict:
        return self._messages[idx]

    def __contains__(self, item: Any) -> bool:
        return item in self._messages

    def __repr__(self) -> str:
        return f"TauContext({len(self)} msgs)"

    # --- Validation ---
    def _validate_on_mutation(self) -> None:
        """Validate context after mutation, printing warnings for errors.

        Suppresses unresolved tool-call warnings when context is mid-batch
        (last message is assistant with tool_calls or a tool result).
        """
        errors = self.validate()
        if errors:
            last = self._messages[-1] if self._messages else None
            in_progress = last is not None and (
                last.get("role") == "tool"
                or (last.get("role") == "assistant" and last.get("tool_calls"))
            )
            if in_progress:
                errors = [e for e in errors if "unresolved tool call" not in e]
            if errors:
                # Audit: log context state alongside the warning
                from agent_audit_bridge import log_console_warning
                last_role = last.get("role", "none") if last else "empty"
                log_console_warning(
                    f"Context validation ({len(self._messages)} msgs, last={last_role}): "
                    + "; ".join(errors)
                )
                context_append_warning(errors)

    # --- Mutations ---
    def _append(self, msg: dict) -> None:
        """Internal method to append a message to the context."""
        self._messages.append(msg)

    def clear(self) -> None:
        """Clear all messages except the system prompt (preserved at index 0)."""
        system = (
            self._messages[0]
            if self._messages and self._messages[0].get("role") == "system"
            else None
        )
        self._messages.clear()
        if system is not None:
            self._messages.append(system)
        self._validate_on_mutation()

    def extend(self, msgs: list[dict]) -> None:
        """Extend the context by appending multiple messages at once."""
        self._messages.extend(msgs)
        self._validate_on_mutation()

    def append_synthetic_user(self, category: str, content: str) -> None:
        """Append a synthetic user message to the context.

        Synthetic messages are system-injected bridges that maintain valid
        OpenAI message alternation. They are marked with SYNTHETIC_PREFIX
        so they can be detected and excluded from undo boundaries and
        consecutive-role validation.

        Args:
            category: The synthetic message category (e.g., 'end_turn_reminder').
            content: The message content (without prefix).
        """
        self._append(make_synthetic_user(category, content))

    def undo(self) -> None:
        """Undo the last conversation turn by removing messages from the last user message onward.

        Preserves the system message (if present at index 0) and all messages
        up to but not including the last user message.

        Synthetic user messages (marked with SYNTHETIC_PREFIX) are skipped —
        they are system-injected and should not affect undo boundaries.
        """
        if len(self._messages) < 2:
            return
        last_user_idx = None
        for i in range(len(self._messages) - 1, -1, -1):
            msg = self._messages[i]
            if msg.get("role") == "user" and not is_synthetic_message(msg):
                last_user_idx = i
                break
        if last_user_idx is None:
            return
        if last_user_idx == 0 and self._messages[0].get("role") == "system":
            return
        self._messages = self._messages[:last_user_idx]
        self._validate_on_mutation()

    # --- Validation ---
    def validate(self) -> list[str]:
        """Validate the entire context against OpenAI API compliance rules.

        Checks:
        - Exactly one system message at index 0
        - No consecutive messages with same role (tool exceptions allowed)
        - Valid message structure and required fields
        - Proper tool call/tool result pairing
        - Valid tool_call_id references
        """
        errors: list[str] = []
        valid_roles = {"system", "user", "assistant", "tool"}

        if not self._messages:
            return errors

        # Exactly one system message required
        system_count = sum(1 for m in self._messages if isinstance(m, dict) and m.get("role") == "system")
        if system_count != 1:
            errors.append(
                f"Context must have exactly one system message (found {system_count})"
            )

        # Collect tool_call_ids from assistant messages
        tool_call_ids = set()
        for msg in self._messages:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                for tool_call in msg.get("tool_calls", []):
                    if isinstance(tool_call, dict):
                        tool_id = tool_call.get("id")
                        if tool_id:
                            tool_call_ids.add(tool_id)

        # Validate each message individually
        for i, msg in enumerate(self._messages):
            if not isinstance(msg, dict):
                errors.append(f"Message {i} is not a dictionary")
                continue

            role = msg.get("role")
            if role not in valid_roles:
                errors.append(
                    f"Message {i} has invalid role '{role}' "
                    "(must be system, user, assistant, or tool)"
                )
                continue

            # Content required for all roles
            if "content" not in msg:
                errors.append(f"Message {i} missing required 'content' field")
            else:
                content = msg["content"]
                if content is not None and not isinstance(content, (str, list)):
                    errors.append(
                        f"Message {i} has invalid content type: {type(content)} "
                        "(must be str or list)"
                    )

            # Validate assistant tool_calls
            if role == "assistant":
                tool_calls = msg.get("tool_calls", [])
                if not isinstance(tool_calls, list):
                    errors.append(f"Message {i} tool_calls is not a list")
                else:
                    seen_ids = set()
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict):
                            errors.append(f"Message {i} tool_call is not a dictionary")
                            continue

                        tool_call_id = tool_call.get("id")
                        if not tool_call_id:
                            errors.append(f"Message {i} tool_call missing 'id' field")
                        elif tool_call_id in seen_ids:
                            errors.append(
                                f"Message {i} has duplicate tool_call_id: {tool_call_id}"
                            )
                        else:
                            seen_ids.add(tool_call_id)

                        # OpenAI tool_call structure
                        if "id" not in tool_call:
                            errors.append("tool_call missing 'id' field")
                        if "function" not in tool_call:
                            errors.append("tool_call missing 'function' field")
                        else:
                            func = tool_call["function"]
                            if "name" not in func:
                                errors.append("function missing 'name' field")
                            if "arguments" not in func:
                                errors.append("function missing 'arguments' field")
                            else:
                                if not isinstance(func["arguments"], str):
                                    errors.append("function arguments must be string")
                                else:
                                    try:
                                        json.loads(func["arguments"])
                                    except json.JSONDecodeError:
                                        errors.append(
                                            "function arguments must be valid JSON"
                                        )

            # OpenAI content rules
            if role != "tool":
                content = msg.get("content")
                if content is None:
                    if not (role == "assistant" and msg.get("tool_calls")):
                        errors.append("Content cannot be None")
                elif isinstance(content, str) and not content.strip():
                    if not (role == "assistant" and msg.get("tool_calls")):
                        errors.append("Content cannot be empty string")
                elif isinstance(content, list) and not content:
                    errors.append("Content list cannot be empty")

            # Tool msg requires: tool_call_id, name, content
            if role == "tool":
                tool_call_id = msg.get("tool_call_id")
                if tool_call_id is None:
                    errors.append("tool message missing tool_call_id")
                elif not isinstance(tool_call_id, str) or not tool_call_id.strip():
                    errors.append("tool_call_id must be non-empty string")

                if "name" not in msg:
                    errors.append(
                        "tool message missing 'name' field (required by OpenAI spec)"
                    )
                elif not isinstance(msg["name"], str) or not msg["name"].strip():
                    errors.append("tool message 'name' must be non-empty string")

                content = msg.get("content")
                if content is None:
                    pass  # Allowed — treated as empty tool result
                elif not isinstance(content, str):
                    errors.append("tool message 'content' must be a string")

        # Validate cross-message sequencing rules
        last_role = None
        pending_tool_ids: set[str] = set()
        tool_call_info: dict[str, str] = {}

        for i, msg in enumerate(self._messages):
            if not isinstance(msg, dict):
                continue

            role = msg.get("role")

            if role == "assistant":
                if pending_tool_ids:
                    errors.append(
                        f"Message {i}: Assistant message with pending tool calls "
                        f"({pending_tool_ids}). All tool results must be received first."
                    )
                for tool_call in msg.get("tool_calls", []):
                    if isinstance(tool_call, dict) and tool_call.get("id"):
                        tool_id = tool_call["id"]
                        func = tool_call.get("function", {})
                        func_name = func.get("name", "unknown")
                        func_args = func.get("arguments", "")
                        tool_call_info[tool_id] = f"{func_name}({func_args})"
                        pending_tool_ids.add(tool_id)

            elif role == "tool":
                tool_call_id = msg.get("tool_call_id")
                if tool_call_id:
                    if tool_call_id not in pending_tool_ids:
                        errors.append(
                            f"Message {i}: Tool result references unknown "
                            f"tool_call_id '{tool_call_id}'"
                        )
                    else:
                        pending_tool_ids.remove(tool_call_id)

            if last_role == "tool" and role == "user" and not is_synthetic_message(msg):
                errors.append(
                    f"Message {i}: Tool message must be followed by assistant, not user"
                )

            if i > 0 and role == "system":
                errors.append(
                    f"Message {i} has 'system' role after first message "
                    "(should be user/assistant)"
                )

            if (
                i == 1
                and role == "assistant"
                and self._messages[0].get("role") == "system"
            ):
                errors.append(
                    f"Message {i}: Assistant message must be preceded by user "
                    "message after system prompt"
                )

            # Consecutive-role check: exclude synthetic messages (system-injected bridges)
            # BUT update last_role for synthetic messages so they break consecutive sequences
            if is_synthetic_message(msg):
                last_role = role  # Bridge breaks the chain
            elif last_role is not None and role == last_role and role != "tool":
                errors.append(
                    f"Message {i} has consecutive messages with same role '{role}'"
                )
                last_role = role
            else:
                last_role = role

        if pending_tool_ids:
            unresolved_details = [
                tool_call_info[tid] for tid in pending_tool_ids if tid in tool_call_info
            ]
            details_str = (
                ", ".join(unresolved_details)
                if unresolved_details
                else str(pending_tool_ids)
            )
            errors.append(
                f"Context has {len(pending_tool_ids)} unresolved tool call(s): "
                f"{pending_tool_ids}. Tool calls: {details_str}. "
                "All tool results should be received before next assistant message."
            )

        for i, msg in enumerate(self._messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "tool":
                tool_call_id = msg.get("tool_call_id")
                if tool_call_id and tool_call_id not in tool_call_ids:
                    errors.append(
                        f"Message {i} tool result references non-existent "
                        f"tool_call_id: {tool_call_id}"
                    )

        return errors

    # --- Recovery ---
    def attempt_recovery(self) -> tuple[bool, list[str]]:
        """Attempt to auto-repair common context validation errors.

        Fixes:
        - Missing system message (adds one at index 0)
        - Consecutive same-role messages (inserts synthetic user bridges)
        - Assistant without preceding user after system (inserts synthetic bridge)
        - Unresolved tool calls (adds placeholder tool results)
        - Malformed messages (non-dict entries are removed)

        Uses snapshot/rollback: if recovery makes context worse (more errors),
        the original state is restored.

        Returns:
            Tuple of (recovered: bool, fixes_applied: list[str])
            recovered=True means all fixable issues were addressed.
        """
        fixes: list[str] = []
        # Snapshot for rollback
        original_messages = list(self._messages)
        original_error_count = len(self.validate())
        try:
            # 1. Remove malformed messages (non-dict entries)
            original_len = len(self._messages)
            self._messages = [
                msg for msg in self._messages
                if isinstance(msg, dict)
            ]
            removed = original_len - len(self._messages)
            if removed:
                fixes.append(f"Removed {removed} malformed (non-dict) message(s)")

            # 2. Fix missing system message
            system_count = sum(1 for m in self._messages if m.get("role") == "system")
            if system_count == 0 and self._messages:
                self._messages.insert(0, {
                    "role": "system",
                    "content": "[Recovery: System message was missing]",
                })
                fixes.append("Added missing system message")

            # 3. Fix assistant at index 1 without preceding user (after system)
            if (len(self._messages) >= 2
                    and self._messages[0].get("role") == "system"
                    and self._messages[1].get("role") == "assistant"):
                self._messages.insert(1, make_synthetic_user(
                    "recovery",
                    "Context repair: inserted bridge between system and assistant"
                ))
                fixes.append("Inserted synthetic user bridge after system message")

            # 4. Fix consecutive same-role messages by inserting synthetic bridges
            i = 1
            while i < len(self._messages):
                if not isinstance(self._messages[i], dict):
                    i += 1
                    continue
                curr = self._messages[i]
                prev = self._messages[i - 1] if i > 0 else None
                if (prev is not None
                        and isinstance(prev, dict)
                        and curr.get("role") in {"user", "assistant"}
                        and prev.get("role") == curr.get("role")
                        and not is_synthetic_message(curr)):
                    role = curr.get("role", "unknown")
                    self._messages.insert(i, make_synthetic_user(
                        "recovery",
                        f"Context repair: bridge between consecutive {role} messages"
                    ))
                    fixes.append(
                        f"Inserted synthetic bridge between consecutive {role} messages at index {i}"
                    )
                    i += 2
                else:
                    i += 1

            # 5. Fix unresolved tool calls by adding placeholder results
            pending = self.get_pending_tool_ids()
            if pending:
                for tid in sorted(pending):
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": tid,
                        "name": "unknown",
                        "content": "[Recovery: placeholder result for unresolved tool call]",
                    })
                fixes.append(f"Added placeholder results for {len(pending)} unresolved tool call(s)")

            # 6. Fix tool message followed by user (non-synthetic)
            i = 0
            while i < len(self._messages) - 1:
                if (self._messages[i].get("role") == "tool"
                        and self._messages[i + 1].get("role") == "user"
                        and not is_synthetic_message(self._messages[i + 1])):
                    self._messages.insert(i + 1, make_synthetic_user(
                        "recovery",
                        "Context repair: bridge between tool and user message"
                    ))
                    fixes.append(
                        f"Inserted synthetic bridge between tool and user at index {i + 1}"
                    )
                    i += 2
                else:
                    i += 1

            # Rollback check: if recovery made things worse, restore original
            new_error_count = len(self.validate())
            if new_error_count > original_error_count:
                self._messages = original_messages
                return False, []

            if not fixes:
                return False, []
            return new_error_count == 0, fixes

        except Exception:
            # Recovery must never crash — restore original state
            self._messages = original_messages
            return False, ["Recovery failed — context may still be invalid"]

    # --- Pending state ---
    def get_pending_tool_ids(self) -> set[str]:
        """Return the set of tool_call_ids that have not yet received a matching tool result."""
        pending = set()
        for msg in self._messages:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls", []):
                    if isinstance(tc, dict) and tc.get("id"):
                        pending.add(tc["id"])
            elif msg.get("role") == "tool":
                tid = msg.get("tool_call_id")
                if tid:
                    pending.discard(tid)
        return pending

    def is_tool_pending(self) -> bool:
        """Check if any tool calls in the context are awaiting results."""
        return bool(self.get_pending_tool_ids())

    def validate_tool_resolution(self) -> list[str]:
        """Validate that all tool calls have been resolved with matching tool results."""
        pending = self.get_pending_tool_ids()
        if pending:
            return [
                f"Context has {len(pending)} unresolved tool call(s): {pending}. "
                "All tool results should be received before next assistant message."
            ]
        return []

    # --- Token estimation ---
    def estimate_tokens(self, pending_tokens: int = 0) -> int:
        """Estimate the total token count for the context.

        Uses character-based heuristic: ~3 chars per token, 15 tokens structural
        overhead per message.
        """
        total = 0
        for msg in self._messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content) // 3
            reasoning = msg.get("reasoning", "")
            if isinstance(reasoning, str):
                total += len(reasoning) // 3
            for tc in msg.get("tool_calls") or []:
                args = tc.get("function", {}).get("arguments", "")
                if isinstance(args, str):
                    total += len(args) // 3
            total += 15  # structural overhead (role, IDs, JSON framing)
        total += pending_tokens
        return total

    def get_usage_stats(
        self, max_tokens: int, exact_tokens: int | None = None
    ) -> tuple[int, float, int, bool]:
        """Get comprehensive token usage statistics.

        Returns (token_count, percentage, byte_count, is_exact).
        """
        if exact_tokens is not None and exact_tokens > 0:
            token_count, is_exact = exact_tokens, True
        else:
            token_count, is_exact = self.estimate_tokens(), False
        byte_count = self.bytes_size()
        percentage = token_count / max_tokens if max_tokens > 0 else 0.0
        return token_count, percentage, byte_count, is_exact

    # --- Persistence ---
    def load_from_file(self, context_file: Path) -> bool:
        """Load context messages from a JSON file. Returns True on success."""
        if not context_file.exists():
            return False
        try:
            with open(context_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.set_messages(data)
            return True
        except (json.JSONDecodeError, IOError):
            self.clear()
            return False

    def save_to_file(self, context_file: Path, force: bool = False) -> None:
        """Save the current context to a JSON file.

        Skips saving if there are pending tool calls (unless force=True).
        """
        pending = self.get_pending_tool_ids()
        if pending and not force:
            return
        if len(self._messages) >= 3:
            with open(context_file, "w", encoding="utf-8") as f:
                json.dump(self._messages, f, indent=2)

    def bytes_size(self) -> int:
        """Calculate the size of the serialized context in bytes."""
        return len(json.dumps(self._messages).encode("utf-8"))

    # --- Typed append methods ---
    def set_system(self, content: str) -> None:
        """Set the system message for the context. Must be first message."""
        if any(m.get("role") == "system" for m in self._messages):
            raise ValueError(
                "Cannot set system message - system message already exists"
            )
        self._append({"role": "system", "content": content})

    def append_user(self, content: str | list) -> None:
        """Append a user message to the context.

        Emits warnings for invalid sequences (consecutive users, tool->user, etc).
        """
        if not self._messages:
            _emit_context_validation_warning(
                "Attempting to append user message to empty context.",
                "Context should be initialized with set_system() first.",
            )
            return

        last_msg = self._messages[-1]
        last_role = last_msg.get("role")

        if last_role == "assistant" and last_msg.get("tool_calls"):
            _emit_context_validation_warning(
                "Attempting to append user message after assistant with tool calls.",
                "This violates the expected message sequence.",
                "All tool calls should be resolved with tool results first.",
            )
        elif last_role == "tool":
            _emit_context_validation_warning(
                "Attempting to append user message after tool result.",
                "Assistant response should come before next user message.",
            )
        elif last_role == "user":
            _emit_context_validation_warning(
                "Attempting to append consecutive user messages.",
                "Assistant response required between user messages.",
            )
        elif last_role not in ("system", "assistant"):
            _emit_context_validation_warning(
                f"Invalid context state: cannot append user after role '{last_role}'.",
            )

        self._append({"role": "user", "content": content})

    def append_assistant(
        self,
        content: str | None,
        tool_calls: list[dict] | None = None,
        reasoning: str | None = None,
    ) -> None:
        """Append an assistant message to the context.

        May include content, tool_calls, or both. Emits warnings for invalid sequences.
        """
        if not self._messages:
            _emit_context_validation_warning(
                "Attempting to append assistant message to empty context.",
                "Context should be initialized with set_system() first.",
            )
            return

        last_msg = self._messages[-1]
        last_role = last_msg.get("role")

        if last_role == "assistant":
            # Consecutive assistant messages — insert synthetic bridge
            self.append_synthetic_user("continuation", "Continuing conversation.")
        elif last_role == "system" and len(self._messages) > 1:
            # System → assistant with other messages present — insert bridge
            self.append_synthetic_user("turn_started", "Turn started.")
        elif last_role == "system" and len(self._messages) == 1:
            # Fresh context with only system prompt — insert synthetic user turn
            self.append_synthetic_user("turn_started", "Turn started.")
        elif last_role not in ("user", "tool"):
            # Invalid predecessor — insert bridge to maintain alternation
            self.append_synthetic_user("continuation", "Continuing conversation.")

        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls is not None:
            msg["tool_calls"] = tool_calls
        if reasoning is not None:
            msg["reasoning"] = reasoning
        self._append(msg)

    def append_tool(self, content: str | None, tool_call_id: str) -> None:
        """Append a tool result message to the context.

        Validates tool_call_id references a valid tool call from a preceding
        assistant message.
        """
        if not self._messages:
            _emit_context_validation_warning(
                "Attempting to append tool result to empty context.",
                "Assistant message with tool calls required first.",
            )
            return

        last_msg = self._messages[-1]
        last_role = last_msg.get("role")

        if last_role == "assistant":
            assistant_tool_calls = last_msg.get("tool_calls", [])
            if not assistant_tool_calls:
                _emit_context_validation_warning(
                    "Attempting to append tool result after assistant without tool calls.",
                    "Assistant message must have tool_calls to generate tool results.",
                )
            else:
                valid_ids = {
                    tc.get("id") for tc in assistant_tool_calls if tc.get("id")
                }
                if tool_call_id not in valid_ids:
                    _emit_context_validation_warning(
                        f"Tool result references invalid tool_call_id: '{tool_call_id}'.",
                        f"Valid IDs from assistant: {valid_ids}",
                    )
        elif last_role == "tool":
            # Batch: find most recent assistant to validate tool_call_id
            assistant_idx = next(
                (
                    i
                    for i in range(len(self._messages) - 1, -1, -1)
                    if self._messages[i].get("role") == "assistant"
                ),
                None,
            )
            if assistant_idx is None:
                _emit_context_validation_warning(
                    "No assistant message with tool calls found in context.",
                )
            else:
                valid_ids = {
                    tc.get("id")
                    for tc in self._messages[assistant_idx].get("tool_calls", [])
                    if tc.get("id")
                }
                if tool_call_id not in valid_ids:
                    _emit_context_validation_warning(
                        f"Tool result references invalid tool_call_id: '{tool_call_id}'.",
                        f"Valid IDs from assistant: {valid_ids}",
                    )
        elif last_role == "user":
            _emit_context_validation_warning(
                "Attempting to append tool result after user message.",
                "Assistant must generate tool calls first.",
            )
        elif last_role == "system":
            _emit_context_validation_warning(
                "Attempting to append tool result after system message.",
                "User message and assistant tool calls required first.",
            )
        else:
            _emit_context_validation_warning(
                f"Invalid context state: cannot append tool after role '{last_role}'.",
            )

        func_name = self._get_tool_function_name(tool_call_id) or "unknown_tool"
        self._append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": func_name,
                "content": content,
            }
        )

    def _get_tool_function_name(self, tool_call_id: str) -> str | None:
        """Look up the function name for a given tool_call_id from assistant messages."""
        for msg in self._messages:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls", []):
                    if isinstance(tc, dict) and tc.get("id") == tool_call_id:
                        func = tc.get("function", {})
                        if isinstance(func, dict):
                            return func.get("name")
        return None

    # --- Turn management ---
    def cleanup_synthetic(self) -> None:
        """Remove all synthetic messages from the context.

        Synthetic messages are system-injected bridges that maintain OpenAI
        message alternation (see designs/DECISIONS.md §18.5). They should not
        persist across turns, as they are internal-only and never sent to the LLM.

        **Explicit merge design:** This method removes bridges ONLY. It does NOT
        merge consecutive same-role messages. The caller must explicitly invoke
        `merge_consecutive_assistants()` to consolidate any consecutive assistant
        messages left behind.
        This separation prevents hidden mutations: cleanup is pure data removal,
        merge is an explicit policy decision controlled by the caller.

        See designs/DECISIONS.md §18.7 for the rationale.
        """
        self._messages = [m for m in self._messages if not is_synthetic_message(m)]
        # Intentionally NO _validate_on_mutation() here.
        # Removing bridges creates transient consecutive same-role messages.
        # Validation runs after merge_consecutive_assistants() in close_turn().

    def merge_consecutive_assistants(self) -> None:
        """Merge consecutive same-role messages into single messages.

        **Merges assistant and user messages.** After removing synthetic user
        bridges, consecutive assistant messages appear. Consecutive user messages
        can also appear from tool-result / post-parse edge cases. Both are
        merged gracefully (content concatenated) with a warning logged.

        **Consecutive tool messages are allowed** (batched tool calls: one
        assistant message with N tool_calls produces N tool results). They are
        NOT merged — each tool result has a unique tool_call_id/name and must
        be preserved.

        Merge strategy:
        - `content`: concatenated with newline separator
        - `tool_calls`: deduplicated by ID (assistant only)
        - `reasoning`: concatenated with newline separator (assistant only)
        - `refusal`: concatenated with newline separator (assistant only)
        - `usage_metadata`: token counts summed (assistant only)

        NOTE: This is a structural compromise. Merging two independent messages
        into one changes what the LLM sees.  It is necessary because
        cleanup_synthetic() removes the bridges that maintained alternation.
        """
        if len(self._messages) <= 1:
            return

        merged: list[dict] = [dict(self._messages[0])]

        for msg in self._messages[1:]:
            last = merged[-1]
            last_role = last.get("role")
            msg_role = msg.get("role")

            if last_role == msg_role and last_role == "assistant":
                # --- Merge content (string, list, or None) ---
                last_content = last.get("content")
                msg_content = msg.get("content")
                if last_content is not None and msg_content is not None:
                    last["content"] = self._merge_content(last_content, msg_content)
                elif msg_content is not None:
                    last["content"] = msg_content
                # If both None: leave as-is (assistant with only tool_calls)

                # --- Merge tool_calls (deduplicate by ID) ---
                if msg.get("tool_calls"):
                    if "tool_calls" not in last:
                        last["tool_calls"] = []
                    existing_ids = {
                        tc.get("id") for tc in last["tool_calls"] if tc.get("id")
                    }
                    for tc in msg.get("tool_calls", []):
                        tc_id = tc.get("id")
                        if tc_id not in existing_ids:
                            last["tool_calls"].append(tc)
                            if tc_id:
                                existing_ids.add(tc_id)

                # --- Merge reasoning ---
                if msg.get("reasoning") is not None:
                    last_reasoning = last.get("reasoning")
                    if last_reasoning is not None:
                        last["reasoning"] = self._merge_content(
                            last_reasoning, msg["reasoning"]
                        )
                    else:
                        last["reasoning"] = msg["reasoning"]

                # --- Merge refusal ---
                if msg.get("refusal") is not None:
                    last_refusal = last.get("refusal")
                    if last_refusal is not None:
                        last["refusal"] = self._merge_content(
                            last_refusal, msg["refusal"]
                        )
                    else:
                        last["refusal"] = msg["refusal"]

                # --- Merge usage_metadata (sum token counts) ---
                if msg.get("usage_metadata") is not None:
                    if "usage_metadata" not in last:
                        last["usage_metadata"] = dict(msg["usage_metadata"])
                    else:
                        for key, value in msg["usage_metadata"].items():
                            if isinstance(value, (int, float)):
                                last["usage_metadata"][key] = (
                                    last["usage_metadata"].get(key, 0) + value
                                )
                            else:
                                last["usage_metadata"][key] = value
            elif last_role == msg_role and last_role == "tool":
                # Consecutive tool messages are VALID (batched tool calls).
                # Do NOT merge — each has unique tool_call_id/name.
                merged.append(dict(msg))
            elif last_role == msg_role and last_role == "user":
                # Consecutive user messages — merge them gracefully instead of
                # crashing. This can happen when tool results or post-parse
                # recovery create adjacent user messages. Merge content and log
                # a warning so the session continues rather than aborting.
                last_content = last.get("content")
                msg_content = msg.get("content")
                if last_content is not None and msg_content is not None:
                    last["content"] = self._merge_content(last_content, msg_content)
                elif msg_content is not None:
                    last["content"] = msg_content
                # Emit a single-line warning (not an error) so the session
                # continues.  The caller can surface this however it likes.
                from agent_console import warning as _w
                _w(
                    f"merge_consecutive_assistants(): merged consecutive "
                    f"'user' messages — context state was non-alternating. "
                    f"Merged {len(merged)} messages so far."
                )
            elif last_role == msg_role:
                # Any other consecutive same-role (should not happen in practice).
                # Merge as a safety net rather than crashing.
                last_content = last.get("content")
                msg_content = msg.get("content")
                if last_content is not None and msg_content is not None:
                    last["content"] = self._merge_content(last_content, msg_content)
                elif msg_content is not None:
                    last["content"] = msg_content
                from agent_console import warning as _w
                _w(
                    f"merge_consecutive_assistants(): merged consecutive "
                    f"'{last_role}' messages (unexpected)."
                )
            else:
                merged.append(dict(msg))

        self._messages = merged

    @staticmethod
    def _merge_content(a: object, b: object) -> str:
        """Merge two content values into a single string."""
        return str(a) + "\n" + str(b)

    def close_turn(self, reason: str) -> None:
        """Close an incomplete turn to ensure the context ends in a valid terminal state.

        Explicit merge design: cleans up all synthetic messages via cleanup_synthetic(),
        then explicitly merges consecutive assistant messages via merge_consecutive_assistants().
        This two-step approach ensures no hidden mutations: cleanup removes internal-only
        synthetic bridges, merge consolidates the resulting consecutive assistant messages.

        Resolves pending tool calls with the provided reason, then appends an
        assistant message if the last role is user or tool.
        If the last role is "system" or "assistant", inserts a synthetic user
        message first to maintain valid message alternation.

        Idempotent: if context already ends with "assistant" and has no pending
        tool calls, this is a no-op (already in valid terminal state).
        """
        if not self._messages:
            return

        # Clean up synthetic messages before closing the turn.
        # Then explicitly merge consecutive assistant messages left behind.
        # This is the explicit merge design: cleanup_synthetic() removes bridges only;
        # merge_consecutive_assistants() is called explicitly by close_turn() to maintain alternation.
        self.cleanup_synthetic()
        self.merge_consecutive_assistants()
        # Repair: if cleanup removed a synthetic user bridge that was the only separator
        # between system and the first assistant, insert a minimal user message to
        # maintain valid alternation (system → user → assistant).
        if (
            len(self._messages) >= 2
            and self._messages[0].get("role") == "system"
            and self._messages[1].get("role") == "assistant"
        ):
            self._messages.insert(
                1, {"role": "user", "content": "[context boundary]"}
            )
        # Validate after merge — cleanup_synthetic() intentionally skips validation
        # because bridge removal creates transient consecutive same-role messages.
        self._validate_on_mutation()

        pending = self.get_pending_tool_ids()
        for tool_id in pending:
            func_name = self._get_tool_function_name(tool_id) or "unknown_tool"
            self.append_tool(reason, tool_id)
            if self._messages and self._messages[-1].get("role") == "tool":
                self._messages[-1]["name"] = func_name

        last_role = self._messages[-1].get("role")

        # Idempotent: if already in valid terminal state (assistant or resolved tool), do nothing
        if last_role == "assistant" and not pending:
            return

        if last_role in ("system", "assistant"):
            # Insert synthetic user message to maintain valid alternation
            self.append_synthetic_user("turn_closed", f"Turn closed: {reason}")
        if self._messages[-1].get("role") in ("user", "tool"):
            self.append_assistant(reason)

    # --- Fork context preparation ---
    def prepare_fork_context(
        self,
        task: str,
        fork_tool_call_id: str | None = None,
        nesting_suffix: str = "",
    ) -> None:
        """Prepare the context for agent forking.

        Marks pending tool calls with FORK/PENDING markers and closes the turn.
        NOT idempotent: caller must deep-copy context before calling.
        """
        if not self._messages:
            self._append({"role": "system", "content": "You are a helpful assistant"})

        self.set_fork_metadata(fork_tool_call_id=fork_tool_call_id, fork_task=task)

        pending = self.get_pending_tool_ids()
        fork_marked = False
        for tid in pending:
            if tid == fork_tool_call_id:
                fork_marked = True
                self.append_tool(_FORK_TOOL_MARKER.format(task=task), tid)
            else:
                self.append_tool(_PENDING_TOOL_MARKER, tid)

        # Warn if fork_tool_call_id was provided but not found in pending calls
        # (covers both: pending exists but ID missing, OR no pending calls at all)
        if fork_tool_call_id is not None and not fork_marked:
            if pending:
                _emit_context_validation_warning(
                    f"fork_tool_call_id '{fork_tool_call_id}' not found in pending "
                    f"tool calls (pending: {sorted(pending)}).",
                    "The fork call will not receive the FORK marker — treating as regular pending.",
                )
            else:
                _emit_context_validation_warning(
                    f"fork_tool_call_id '{fork_tool_call_id}' provided but there are no pending calls to mark.",
                    "The fork call will not receive the FORK marker — treating as regular pending.",
                )

        self.close_turn(f"Turn closed — forking to: {task}{nesting_suffix}")

    # --- Getters / setters ---
    def get_messages(self) -> list[dict]:
        """Return a copy of the internal message list."""
        return self._messages.copy()

    def set_messages(self, msgs: list[dict]) -> None:
        """Replace all messages in the context."""
        self._messages = list(msgs)
        self._validate_on_mutation()

    def copy(self) -> "TauContext":
        """Create a shallow copy of the context (messages only, not fork metadata)."""
        return TauContext(self._messages.copy())

    def get_fork_metadata(self) -> dict:
        """Return a copy of the fork metadata dictionary."""
        return self._fork_metadata.copy()

    def set_fork_metadata(
        self, fork_tool_call_id: str | None = None, fork_task: str | None = None
    ) -> None:
        """Update fork metadata and recompute pending tool IDs."""
        self._fork_metadata["fork_tool_call_id"] = fork_tool_call_id
        self._fork_metadata["fork_task"] = fork_task
        self._fork_metadata["pending_tool_ids"] = self.get_pending_tool_ids()

    def clear_fork_metadata(self) -> None:
        """Reset fork metadata to default empty values."""
        self._fork_metadata = {
            "pending_tool_ids": set(),
            "fork_tool_call_id": None,
            "fork_task": None,
        }

    # --- Compression ---
    def compress(
        self,
        target_percentage: float,
        agent: "TauBot",
        tools: list | None = None,
    ) -> bool:
        """Compress the context to reduce token usage to a target percentage.

        Uses an LLM to summarize and compress the conversation history.
        """
        from agent_context_compress import compress_context

        try:
            resolved = agent.resolve_group_params()
            compressed_messages, summary, metadata = compress_context(
                self._messages,
                agent.client,
                agent.model_name,
                target_percentage,
                tools or [],
                resolved,
                log_file=agent._session.audit_file,
                audit_writer=agent._session.audit_writer,
            )
            self.set_messages(compressed_messages)
            return summary is not None
        except (TypeError, ValueError, KeyError, RuntimeError, OSError):
            return False

    def to_list(self) -> list[dict]:
        """Convert the context to a plain list for JSON serialization.

        Alias of get_messages().
        """
        return self._messages.copy()

    def get_last_assistant(self) -> str | None:
        """Return the content of the last assistant message, or None."""
        for msg in reversed(self._messages):
            if msg.get("role") == "assistant":
                return msg.get("content")
        return None

    def get_system(self) -> str | None:
        """Return the system message content (at index 0), or None."""
        if self._messages and self._messages[0].get("role") == "system":
            return self._messages[0].get("content")
        return None

    # --- Context dump ---
    def dump(
        self,
        mode: str = "summary",
        max_tokens: int = 200000,
        exact_tokens: int | None = None,
    ) -> str:
        """Dump the context as a formatted string for display or debugging.

        Modes: summary, full, user, tool, assistant, trace.
        """
        valid_modes = {"summary", "full", "user", "tool", "assistant", "trace"}
        if mode not in valid_modes:
            return (
                f"{Colors.RED}Invalid mode '{mode}'. "
                f"Valid modes: {', '.join(sorted(valid_modes))}{Colors.RESET}"
            )

        if mode == "trace":
            return self._dump_trace()
        if mode == "summary":
            return self._dump_summary(max_tokens, exact_tokens)
        return self._dump_detail(mode)

    def _dump_summary(
        self, max_tokens: int = 200000, exact_tokens: int | None = None
    ) -> str:
        """Generate a compact summary of the context with truncated content and usage stats."""
        lines = []
        token_count, percentage, byte_count, is_exact = self.get_usage_stats(
            max_tokens, exact_tokens
        )
        token_display = f"{token_count:,}" if is_exact else f"~{token_count:,}"

        lines.append(f"\n{'=' * 60}")
        lines.append(f"CONTEXT SUMMARY ({len(self)} messages)")
        lines.append(
            f"Tokens: {token_display} ({percentage:.1%} of {max_tokens:,} max)"
        )
        lines.append(f"Bytes: {byte_count:,}")
        lines.append(f"{'=' * 60}")
        lines.append("")

        for i, msg in enumerate(self._messages):
            role = msg.get("role", "unknown")
            content_str = str(msg.get("content") or "")
            if len(content_str) > 100:
                content_str = content_str[:100] + "..."
            tool_calls = msg.get("tool_calls")
            tool_info = ""
            if tool_calls:
                tool_names = [
                    tc.get("function", {}).get("name", "?") for tc in tool_calls
                ]
                tool_info = f" [tools: {', '.join(tool_names)}]"
            color = _role_color(role)
            lines.append(
                f"{color}{i + 1}. [{role}]{tool_info} {content_str}{Colors.RESET}"
            )
        lines.append(Colors.RESET)
        return "\n".join(lines)

    def _dump_detail(self, mode: str) -> str:
        """Generate a detailed view of the context filtered by message role."""
        role_map = {
            "user": "user",
            "tool": ("tool", "tool_call"),
            "assistant": "assistant",
        }
        target = role_map.get(mode, None)
        if target:
            if isinstance(target, tuple):
                messages = [
                    (i, m)
                    for i, m in enumerate(self._messages)
                    if m.get("role") in target
                ]
            else:
                messages = [
                    (i, m)
                    for i, m in enumerate(self._messages)
                    if m.get("role") == target
                ]
            title = f"{mode.upper()} MESSAGES ONLY ({len(messages)} messages)"
        else:
            messages = list(enumerate(self._messages))
            title = f"CONTEXT ({len(messages)} messages)"

        lines = [f"\n{Colors.CYAN}{title}{Colors.RESET}"]
        for idx, (_, msg) in enumerate(messages):
            role = msg.get("role", "unknown")
            content = str(msg.get("content") or "(none)")
            lines.append(f"\n--- Message {idx + 1} [{role}] ---")
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                tc_lines = []
                for j, tc in enumerate(tool_calls):
                    name = tc.get("function", {}).get("name", "?")
                    args = tc.get("function", {}).get("arguments", "")
                    args_display = args if mode == "full" else (
                        args[:100] + ("..." if len(str(args)) > 100 else "")
                    )
                    tc_lines.append(
                        f"    {j + 1}. {name}({args_display})"
                    )
                lines.append(
                    f"{content}{Colors.CYAN}\n{''.join(tc_lines)}{Colors.RESET}"
                )
            else:
                lines.append(f"{content}{Colors.RESET}")
        lines.append(Colors.RESET)
        return "\n".join(lines)

    def _dump_trace(self) -> str:
        """Generate a debug trace view of the context with detailed formatting."""
        lines = []
        reset = Colors.RESET
        white = reset  # SYST, USER
        green = Colors.GREEN  # ASSI
        cyan = Colors.CYAN  # TOOL, tool call blocks
        yellow = Colors.YELLOW  # Pending indicator

        total = len(self._messages)
        width = max(3, len(str(total)))

        lines.append(f"\n{white}{'=' * 60}{reset}")
        lines.append(f"{white}CONTEXT TRACE (full){reset}")
        lines.append(f"{white}{'=' * 60}{reset}")

        # Show pending tool calls
        pending = self.get_pending_tool_ids()
        if pending:
            lines.append(f"{yellow}PENDING TOOL CALLS:{reset} {pending}")

        # Show validation errors
        errors = self.validate()
        if errors:
            lines.append(f"{Colors.RED}VALIDATION ERRORS ({len(errors)}):{reset}")
            for err in errors:
                lines.append(f"  {Colors.RED}- {err}{reset}")

        lines.append(f"{'─' * 60}")

        # Build tool_id -> tool result lookup (for inline linking)
        tool_id_to_result = {}
        for msg in self._messages:
            if msg.get("role") == "tool":
                tid = msg.get("tool_call_id", "")
                result_content = msg.get("content", "")
                if isinstance(result_content, str):
                    tool_id_to_result[tid] = result_content[:120] + (
                        "..." if len(str(result_content)) > 120 else ""
                    )
                else:
                    tool_id_to_result[tid] = str(result_content)[:120]

        # Show ALL messages from start to end
        for idx, msg in enumerate(self._messages):
            role = msg.get("role", "unknown")
            content = msg.get("content")
            if content is None:
                content = ""
            content_str = str(content)

            # Format message number: right-aligned, 3 characters minimum
            num_str = f"{idx + 1:>{width}}"

            if role == "system":
                clean = content_str.replace("\n", " ").replace("\r", " ")
                if len(clean) > 120:
                    clean = clean[:120] + "..."
                lines.append(f"\n{white}{num_str} [SYST] {clean}{reset}")

            elif role == "user":
                clean = content_str.replace("\n", " ").replace("\r", " ")
                if len(clean) > 120:
                    clean = clean[:120] + "..."
                lines.append(f"\n{white}{num_str} [USER] {clean}{reset}")

            elif role == "assistant":
                clean = content_str.replace("\n", " ").replace("\r", " ")
                if len(clean) > 120:
                    clean = clean[:120] + "..."

                tc = msg.get("tool_calls")
                if tc:
                    lines.append(f"\n{green}{num_str} [ASSI] {clean}{reset}")

                    for tool_call in tc:
                        tc_id = tool_call.get("id", "NO-ID")
                        func = tool_call.get("function", {})
                        tool_name = func.get("name", "unknown")
                        tool_args = func.get("arguments", "")

                        params = []
                        if tool_args:
                            try:
                                args_dict = json.loads(tool_args) if tool_args else {}
                                for k, v in args_dict.items():
                                    v_str = str(v)[:80]
                                    params.append(f"{k}={v_str}")
                            except (
                                json.JSONDecodeError,
                                ValueError,
                                TypeError,
                                KeyError,
                            ):
                                params.append(tool_args[:80])

                        param_str = ", ".join(params) if params else tool_args[:80]

                        lines.append(f"{cyan}└─ [{tool_name}] id={tc_id}{reset}")
                        if param_str:
                            lines.append(f"{cyan}    {param_str}{reset}")

                        if tc_id in tool_id_to_result:
                            result_preview = tool_id_to_result[tc_id]
                            lines.append(f"{cyan}    → result: {result_preview}{reset}")
                        else:
                            lines.append(f"{yellow}    → result: PENDING{reset}")
                else:
                    lines.append(f"\n{green}{num_str} [ASSI] {clean}{reset}")

            elif role == "tool":
                tc_id = msg.get("tool_call_id", "NO-ID")
                result_content = content_str[:120] + (
                    "..." if len(content_str) > 120 else ""
                )
                lines.append(f"\n{cyan}{num_str} [TOOL] id={tc_id}{reset}")
                lines.append(f"{cyan}    {result_content}{reset}")

            else:
                preview = content_str[:120] + ("..." if len(content_str) > 120 else "")
                lines.append(f"\n{white}{num_str} [{role.upper()}] {preview}{reset}")

        lines.append(f"\n{'─' * 60}")
        lines.append(f"{white}END TRACE{reset}")
        return "\n".join(lines)


# ── Context-aware user-prompt helpers ────────────────────────────────────────

def is_synthetic_message(msg: dict) -> bool:
    """Check if any message (user or assistant) is synthetic.

    Args:
        msg: A message dictionary to check.

    Returns:
        True if the message was system-injected, False otherwise.
    """
    content = msg.get("content", "")
    if isinstance(content, str):
        return content.startswith(_SYNTHETIC_PREFIX)
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                if part.get("text", "").startswith(_SYNTHETIC_PREFIX):
                    return True
    return False


def make_synthetic_user(category: str, content: str) -> dict:
    """Create a synthetic user message with the standard marker.

    Args:
        category: The synthetic message category (e.g., 'end_turn_reminder').
        content: The message content (without prefix).

    Returns:
        A message dict ready to append to context.
    """
    return {
        "role": "user",
        "content": f"{_SYNTHETIC_PREFIX}{category}] {content}",
    }


def _extract_text_content(msg: dict) -> str:
    """Extract text content from a message, handling multimodal content."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return " ".join(parts)
    return ""


def get_last_real_user_prompt(context_messages: list[dict]) -> str:
    """Find the last genuine user message in context (excluding synthetic messages).

    Scans the context backwards for the last user message that was NOT
    auto-generated by loop escalation or recovery. Returns the full content
    without truncation.

    Args:
        context_messages: List of context messages to search.

    Returns:
        str: The last real user prompt, or a fallback message if not found.
    """
    for msg in reversed(context_messages):
        if msg.get("role") == "user" and not is_synthetic_message(msg):
            return _extract_text_content(msg)
    return "(no real user prompt found)"
