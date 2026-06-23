"""Input processing and run loop for TauBot.

Manages user input from stdin, context file operations, and the main agent run loop.

Input modes: interactive stdin (with '#' multiline blocks), '/commands', '!shell'.
Context files: JSON arrays stored in LOG_DIR as {ppid}_{timestamp}_{counter}.context.
"""

import json
import os
import queue
import re
import select
import signal
import sys
import threading
import time
import traceback
from io import StringIO
from pathlib import Path

from agent_heartbeat import HeartbeatResponse
from agent_lifecycle import AgentLifecycle
from agent_console import (
    assistant_message_display,
    context_restored,
    error,
    force_exit_message,
    interrupted_message,
    print_agent_exit_summary,
    print_context_status,
    shell_command_usage,
    shell_tool_not_available,
    tool_result,
    user_echo,
    warning,
)
from agent_console_primitives import blank_line, echo_no_newline, prompt, status
from agent_session import LOG_DIR
from agent_models import InputMessage
from tools import TOOLS

__all__ = [
    "OutputCapture",
    "get_context_file_by_parent_ppid",
    "list_context_files",
    "preview_context",
    "InputHandler",
]


# ── Output capture ─────────────────────────────────────────────────────────


class OutputCapture:
    """Thread-safe context manager for capturing stdout output.

    Used for A2A response handling to capture tool output without
    polluting the main stdout stream.
    """

    def __init__(self):
        self.captured: list[str] = []
        self._original_stdout: object | None = None
        self._buffer: StringIO | None = None
        self._lock = threading.Lock()

    def __enter__(self):
        with self._lock:
            self._original_stdout = sys.stdout
            self._buffer = StringIO()
            sys.stdout = self._buffer
            self.captured.append("")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        with self._lock:
            if self._buffer is not None:
                self.captured.append(self._buffer.getvalue())
                sys.stdout = self._original_stdout
                self._buffer = None
            return False

    def get_last(self) -> str:
        with self._lock:
            return self.captured[-1] if self.captured else ""


# ── Context file helpers ──────────────────────────────────────────────────


def get_context_file_by_parent_ppid() -> Path | None:
    """Get the most recent context file matching the current parent PID."""
    ppid = os.getppid()
    ctx_pattern = re.compile(rf"^{ppid}_\d+_\d+\.context$")
    ctx_files = [f for f in LOG_DIR.glob("*.context") if ctx_pattern.match(f.name)]

    return max(ctx_files, key=lambda f: f.stat().st_mtime) if ctx_files else None


def _get_all_context_files() -> list[Path]:
    """Get all context files in LOG_DIR, sorted newest first."""
    ctx_pattern = re.compile(r"^\d+_\d+_\d+\.context$")
    ctx_files = [f for f in LOG_DIR.glob("*.context") if ctx_pattern.match(f.name)]
    return sorted(ctx_files, key=lambda f: f.stat().st_mtime, reverse=True)


def _format_age(seconds: float) -> str:
    """Format a duration in seconds into a human-readable string."""
    if seconds < 60:
        return f"{int(seconds)}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours // 24
    return f"{int(days)}d ago"


def _read_context_metadata(context_file: Path) -> tuple[int, str]:
    """Read message count and last user message from a context file."""
    try:
        with open(context_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return (0, "")

    msg_count = len(data)
    last_user = ""
    for msg in reversed(data):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if len(content) > 80:
                content = content[:77] + "..."
            last_user = content
            break
    return (msg_count, last_user)


def list_context_files(
    ppid_filter: int | None = None, limit: int | None = None
) -> list[dict]:
    """List context files with metadata, sorted newest first."""
    ctx_files = _get_all_context_files()
    results: list[dict] = []
    now = time.time()
    for f in ctx_files:
        if ppid_filter is not None:
            if int(f.name.split("_")[0]) != ppid_filter:
                continue
        msg_count, last_user = _read_context_metadata(f)
        results.append(
            {
                "file": f,
                "name": f.name,
                "age": _format_age(now - f.stat().st_mtime),
                "msg_count": msg_count,
                "last_user": last_user,
            }
        )
        if limit is not None and len(results) >= limit:
            break
    for idx, entry in enumerate(results, start=1):
        entry["id"] = idx
    return results


def preview_context(context_file: Path, n_messages: int = 3) -> list[dict]:
    """Preview the last N messages from a context file."""
    try:
        with open(context_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    preview = []
    for msg in data[-n_messages:]:
        content = msg.get("content", "")
        if len(content) > 200:
            content = content[:197] + "..."
        preview.append({"role": msg.get("role", "unknown"), "content": content})
    return preview


# ── InputHandler ──────────────────────────────────────────────────────────


class InputHandler:
    """Manage stdin thread, signal handling, and input dispatch for TauBot.

    Handles interactive input with multiline support ('#' prefix for blocks),
    signal handling (Ctrl+C for interrupt, double Ctrl+C for force exit),
    input queue management, and heartbeat processing.
    """

    def __init__(self, agent):
        self.agent = agent
        self.input_queue: queue.Queue = agent.input_queue
        self.input_thread: threading.Thread | None = None
        self._input_thread_stop = threading.Event()

    # --- Signal handling ---
    def _signal_handler(self, _signum: int, _frame) -> None:
        """Handle SIGINT (Ctrl+C): first press interrupts, second forces exit."""
        if AgentLifecycle.is_exit_requested():
            AgentLifecycle.set_exit_requested(True)
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            raise SystemExit(0)
        elif AgentLifecycle.is_interrupted():
            AgentLifecycle.set_exit_requested(True)
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            force_exit_message()
            raise SystemExit(0)
        else:
            AgentLifecycle.set_interrupted(True)
            interrupted_message()

    # --- Input thread ---
    def _start_input_thread(self) -> None:
        """Spawn daemon thread for reading stdin with multiline '#' block support."""

        def input_handler():
            # State machine: active=True means inside '#' multiline block
            buffer: list[str] = []  # Collected lines
            active = False  # Inside multiline block?
            blanks = 0  # Consecutive blank line counter

            while (
                not AgentLifecycle.is_exit_requested() and not self._input_thread_stop.is_set()
            ):
                try:
                    ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                    if ready:
                        line = sys.stdin.readline()
                        if not line:
                            break
                        content = line.rstrip("\n")

                        if active:
                            if content == "":
                                # Increment first, then check: ensures 2 blank lines terminates (blanks reaches 2 on the 2nd blank)
                                blanks += 1
                                if blanks >= 2:
                                    self.input_queue.put(
                                        InputMessage.from_interactive("\n".join(buffer))
                                    )
                                    buffer = []
                                    blanks = 0
                                    active = False
                            else:
                                blanks = 0
                                buffer.append(
                                    content[1:] if content.startswith("#") else content
                                )
                        elif content.startswith("#"):
                            active = True
                            buffer = [content[1:]]
                        elif content:
                            self.input_queue.put(InputMessage.from_interactive(content))
                except (EOFError, OSError, KeyboardInterrupt):
                    break
                except (RuntimeError, ValueError, TypeError):
                    break

        self.input_thread = threading.Thread(target=input_handler, daemon=True)
        self.input_thread.start()

    # --- Main run loop ---
    def run(
        self,
        inputs: list[str] | None = None,
        a2a_server=None,
        keep_alive: bool = False,
        interactive: bool = True,
    ):
        """Run the main input handling loop."""
        signal.signal(signal.SIGINT, self._signal_handler)

        if interactive:
            self._start_input_thread()
            time.sleep(0.05)

        need_prompt = True

        while not AgentLifecycle.is_exit_requested():
            try:
                signal.signal(signal.SIGINT, self._signal_handler)

                try:
                    msg = self.input_queue.get(timeout=0.1)
                    need_prompt = True
                except queue.Empty:
                    stdin_done = self.input_thread and not self.input_thread.is_alive()

                    if not interactive and not keep_alive:
                        self._exit()
                        return
                    if not keep_alive and stdin_done:
                        self._exit()
                        return

                    # Heartbeat check (only while waiting for input)
                    hb_result = self.agent._heartbeat.run_heartbeat()
                    if hb_result:
                        self._handle_heartbeat_result(hb_result)
                        continue

                    # One-shot prompt: ensure cursor is on a fresh line, then
                    # display >>>. The \n handles prior output that didn't end
                    # with \n (cursor mid-line). At worst one extra blank line.
                    if need_prompt:
                        sys.stdout.write("\n")
                        prompt(">>> ")
                        sys.stdout.flush()
                        need_prompt = False
                    continue

                if not msg.content.strip():
                    need_prompt = True
                    continue

                AgentLifecycle.set_interrupted(False)
                self.agent._heartbeat.touch_activity()
                user_echo(msg.content)

                if msg.source == "a2a":
                    with OutputCapture() as capture:
                        result = self._process_input(msg)
                    captured = capture.get_last()
                    echo_no_newline(captured)
                    if captured and not captured.endswith("\n"):
                        sys.stdout.write("\n")

                    a2a_response = (
                        result if result else (captured if captured else "No response")
                    )
                    self.agent._pending_a2a_responses[msg.request_id] = (  # pylint: disable=W0212
                        {
                            "type": "response",
                            "id": msg.request_id,
                            "query": msg.content,
                            "response": a2a_response,
                            "context_length": len(self.agent.context),
                        }
                    )
                else:
                    result = self._process_input(msg)

                if result:
                    assistant_message_display(result)
                need_prompt = True

            except KeyboardInterrupt:
                print_agent_exit_summary(self.agent)
                interrupted_message()
                self._input_thread_stop.set()
                try:
                    sys.stdin.close()
                except (OSError, IOError):
                    pass
                break
            except EOFError:
                if keep_alive:
                    continue
                print_agent_exit_summary(self.agent)
                interrupted_message()
                break

        # Exit cleanup: delegate to _exit() to avoid duplicating close_turn/save logic
        self._exit()

    # --- Input processing ---
    def _process_input(self, msg: InputMessage) -> str | None:
        """Process an input message: regular, /command, or !shell."""
        if AgentLifecycle.is_interrupted():
            return None

        if not msg.content.strip():
            return None

        content_stripped = msg.content.strip()
        if content_stripped.startswith("/"):
            cmd = content_stripped[1:].strip()
            cmd_name = cmd.split()[0] if cmd else ""
            self.agent._handle_command(
                cmd_name, content_stripped, msg
            )  # pylint: disable=W0212
            return None
        elif content_stripped.startswith("!"):
            command = content_stripped[1:].strip()
            if not command:
                shell_command_usage()
                return None
            shell_entry = TOOLS.get("bash")
            if shell_entry:
                result = shell_entry.run(
                    cmd=command, timeout=30, agent=self.agent, tool_call_id=None
                )
                tool_result(result)
            else:
                shell_tool_not_available()
            return None

        try:
            self.agent.invoke_with_tools(msg.content)
            last_assistant = self.agent.context.get_last_assistant()

            if msg.source in ["interactive", "command_line", "system", "a2a"]:
                print_context_status(self.agent.get_status())

            self.agent.context.save_to_file(self.agent._session.context_file)
            self.agent._heartbeat.touch_activity()
            return last_assistant
        except Exception as e:
            # Catch ALL exceptions (not just RuntimeError/ValueError/TypeError/OSError)
            # so that ANY crash during tool invocation is logged and handled gracefully.
            error(f"invoke_with_tools failed: {type(e).__name__}: {e}")
            traceback.print_exc()
            return None

    # --- Heartbeat handling ---
    def _handle_heartbeat_result(self, result: HeartbeatResponse | None) -> None:
        """Handle structured heartbeat result.

        A ``HeartbeatResponse`` with ``action="prompt"`` auto-injects the task
        into the input queue.  ``action="no_action"`` is displayed silently.
        ``None`` means the fork failed or validation was exhausted — display
        a brief warning and move on.
        """
        # CRITICAL: reset idle timer BEFORE any branching.
        # Without this, a fast-returning fork (e.g. "No action needed") would
        # leave the heartbeat's last_activity_time unchanged, causing the next
        # queue.Empty iteration to fire another heartbeat immediately.
        self.agent._heartbeat.touch_activity()

        if result is None:
            warning("[HEARTBEAT] Fork returned no valid response — skipping")
            return

        if result.action == "prompt":
            task = result.task or ""
            blank_line()
            status("[HEARTBEAT]")
            warning(f"Executing: {task}")
            blank_line()
            # Auto-execute: inject into input queue.
            # The task will be processed normally, creating its own context entries.
            self.input_queue.put(InputMessage.from_interactive(task))
        else:
            # action == "no_action" — silent, no context pollution.
            blank_line()
            status("[HEARTBEAT] No action needed.")
            blank_line()

    # --- Exit ---
    def _exit(self):
        """Graceful shutdown: close turn, force-save context, stop A2A, exit."""
        self.agent.context.close_turn("[Session ended]")
        if hasattr(self.agent, "a2a_server") and self.agent.a2a_server:
            self.agent.a2a_server.stop()
        try:
            self.agent.context.save_to_file(self.agent._session.context_file, force=True)
        except Exception as exc:  # pragma: no cover
            error(f"Failed to save context on exit: {exc}")
        try:
            print_agent_exit_summary(self.agent)
        except Exception as exc:  # pragma: no cover
            error(f"Unable to display exit summary: {exc}")
        # Flush and close audit writer to ensure all buffered data is written
        if hasattr(self.agent, "_session"):
            self.agent._session.audit_writer.flush()
        sys.exit(0)
