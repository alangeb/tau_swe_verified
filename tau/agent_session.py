"""Session lifecycle management for TauBot.

Encapsulates session file paths, audit writer management, error burst
detection, token tracking, and structured audit logging that were
previously inline in the TauBot god class or scattered across
agent_logging.py.

Responsibilities:
- Session file path resolution (audit, context) with env overrides
- Audit writer management
- Error burst detection
- Token tracking (session-wide totals + per-turn snapshots + cache tracking)
- Structured audit log writing with synchronous flush-at-turn-boundaries

This consolidates agent_logging.py into agent_session.py to eliminate
the unnecessary module boundary between tightly-coupled concerns.

# ============================================================================
# AUDIT LOGGING — NON-NEGOTIABLE DESIGN REQUIREMENTS
# ============================================================================
#
# The audit log is the ABSOLUTE SOURCE OF TRUTH for every agent session.
# It is the single, complete, immutable record of what happened.
#
# These requirements are MANDATORY and must NEVER be relaxed:
#
#   1. NEVER TRUNCATE — Tool outputs, user messages, assistant responses,
#      stack traces, system prompts, tool schemas: everything is logged in
#      full. No character limits, no byte limits, no line limits.
#
#   2. NEVER ROTATE — The audit file grows for the lifetime of the session.
#      No log rotation, no archival, no compression-on-write. A 100 MB file
#      is acceptable. A 1 GB file is acceptable. Disk space is cheap; data
#      loss is not.
#
#   3. NEVER REVERT — The audit log is append-only. Once written, a record
#      is immutable. Never overwrite, never delete, never "correct" past
#      entries. If something was wrong, log a new record describing the
#      correction — but never change what was already recorded.
#
#   4. AUDIT IS THE SOURCE OF TRUTH — All debugging, post-mortem analysis,
#      and LLM learning signals derive from the audit log. If it is incomplete
#      or inaccurate, everything downstream is compromised.
#
# We are AWARE and ACCEPT that audit files may grow very large. This is a
# deliberate trade-off: completeness over storage efficiency.
#
# ============================================================================
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime as dt
from pathlib import Path
from typing import Any

from agent_console import error, log_dir_error, warning
from agent_audit_bridge import emit_console_warning, set_audit_writer
from agent_llm import CacheTracker, CallStats
from agent_token_tracker import TokenTracker

__all__ = [
    "LOG_DIR",
    "SESSION_PREFIX",
    "AuditWriter",
    "ErrorRateTracker",
    "AgentSessionManager",
    "write_oversized_output",
    "log_failed_api_request",
    "_get_log_filename_prefix",
    "_classify_error",
]

# ── Directories ────────────────────────────────────────────────────────────────

_LOG_DIR_DEFAULT = Path.home() / ".local" / "tau" / "log"
LOG_DIR = Path(os.getenv("TAU_LOG_DIR", str(_LOG_DIR_DEFAULT)))

# Global session prefix — set ONCE by the root agent, inherited by all children.
# Never overwritten. Guarantees all session files share the same prefix.
SESSION_PREFIX: str | None = None


# ── Filename helpers ───────────────────────────────────────────────────────


def _get_log_filename_prefix() -> str:
    """Generate unique filename prefix: {ppid}_{YYYYMMDDHHMMSS}_{counter}."""
    ppid = os.getppid()
    dt_str = dt.now().strftime("%Y%m%d%H%M%S")
    counter = 1

    while True:
        prefix = f"{ppid}_{dt_str}_{counter}"
        ctx_file = LOG_DIR / f"{prefix}.context"
        if not ctx_file.exists():
            return prefix
        counter += 1


# ── Error categorization ────────────────────────────────────────────────────

# Structured error categories for audit tracking and analysis.
# Each category groups related error types for meaningful reporting.
_ERROR_CATEGORIES = {
    "timeout": {"TimeoutError", "ToolTimeout"},
    "validation": {"ValidationError", "TypeError", "ValueError", "KeyError"},
    "connection": {"ConnectionError", "OSError", "URLError"},
    "file_operation": {"FileNotFoundError", "PermissionError", "IsADirectoryError"},
    "tool_not_found": {"AttributeError"},  # e.g., tool function missing
    "argument_error": {"IndexError"},
    "execution": {"RuntimeError", "RecursionError", "MemoryError"},
    "api": {"APIError", "APITimeoutError", "APIConnectionError", "RateLimitError", "UnauthorizedError", "BadRequestError"},
}

# Reverse lookup: error_type -> category
_ERROR_TYPE_TO_CATEGORY: dict[str, str] = {}
for _cat, _types in _ERROR_CATEGORIES.items():
    for _t in _types:
        _ERROR_TYPE_TO_CATEGORY[_t] = _cat


def _classify_error(error_type: str) -> str:
    """Classify an error type string into a structured category.

    Returns the category name, or 'unknown' if the error type has no
    matching category.  Matches are done by exact name and by substring
    (e.g. 'ToolTimeout' -> 'timeout', 'FileNotFoundError' -> 'file_operation').
    """
    # Exact match first
    cat = _ERROR_TYPE_TO_CATEGORY.get(error_type)
    if cat:
        return cat

    # Substring match for compound names like 'some.ModuleError'
    for _cat, _types in _ERROR_CATEGORIES.items():
        for _t in _types:
            if _t in error_type:
                return _cat

    return "unknown"


# ── ErrorRateTracker ──────────────────────────────────────────────────────


class ErrorRateTracker:
    """Thread-safe error rate tracker with sliding window calculations.

    Timestamps are NEVER cleared — they persist for accurate rate calculation.
    Burst detection uses a cooldown mechanism to avoid re-triggering.
    """

    def __init__(
        self,
        window_size: float = 300.0,
        alert_threshold: float = 10.0,
        burst_window: float = 5.0,
        burst_threshold: int = 3,
    ):
        self.window_size = window_size
        self.alert_threshold = alert_threshold
        self.burst_window = burst_window
        self.burst_threshold = burst_threshold
        self._lock = threading.Lock()

        self._error_timestamps: list[float] = []
        self._tool_error_timestamps: dict[str, list[float]] = {}
        self._error_type_counts: dict[str, int] = {}
        # Per-category tracking for structured error analysis
        self._category_counts: dict[str, int] = {}
        self._category_timestamps: dict[str, list[float]] = {}
        self._recent_bursts: list[dict] = []
        self._last_alert_time: float = 0.0
        self._alert_cooldown: float = 60.0
        # Cooldown for burst detection: after a burst fires, wait this long
        # before allowing another burst to be detected. Prevents re-triggering
        # WITHOUT clearing timestamps (which would corrupt rate calculation).
        self._last_burst_time: float = 0.0
        self._burst_cooldown: float = 30.0

    def record_error(self, tool_name: str | None, error_type: str) -> bool:
        """Record an error. Returns True if an alert should be triggered."""
        now = time.time()
        category = _classify_error(error_type)

        with self._lock:
            self._error_timestamps.append(now)
            if tool_name:
                self._tool_error_timestamps.setdefault(tool_name, []).append(now)

            self._error_type_counts[error_type] = (
                self._error_type_counts.get(error_type, 0) + 1
            )

            # Track category counts and timestamps
            self._category_counts[category] = (
                self._category_counts.get(category, 0) + 1
            )
            self._category_timestamps.setdefault(category, []).append(now)

            self._check_burst(now)
            return self._check_alert(now)

    def _check_burst(self, now: float) -> None:
        """Check if recent errors constitute a burst (must hold self._lock).

        Uses cooldown to avoid re-triggering — NEVER clears timestamps.
        """
        # Cooldown: don't re-trigger if we just detected a burst.
        if now - self._last_burst_time < self._burst_cooldown:
            return

        window_start = now - self.burst_window
        recent = [t for t in self._error_timestamps if t >= window_start]

        if len(recent) >= self.burst_threshold:
            summary = {
                "timestamp": now,
                "size": len(recent),
                "window": self.burst_window,
                "error_types": dict(self._error_type_counts),
                "error_categories": dict(self._category_counts),
                "tools_affected": list(self._tool_error_timestamps.keys()),
            }
            self._recent_bursts.append(summary)
            self._last_burst_time = now

    def _check_alert(self, now: float) -> bool:
        """Check if error rate exceeds alert threshold (must hold self._lock)."""
        if now - self._last_alert_time < self._alert_cooldown:
            return False

        recent = [t for t in self._error_timestamps if t >= now - self.window_size]
        error_rate = len(recent) / max(self.window_size / 60.0, 0.001)

        if error_rate >= self.alert_threshold:
            self._last_alert_time = now
            return True

        return False

    def get_error_rate(self, tool_name: str | None = None) -> float:
        """Calculate error rate (errors/minute) over sliding window."""
        now = time.time()
        cutoff = now - self.window_size

        with self._lock:
            timestamps = (
                self._tool_error_timestamps.get(tool_name, [])
                if tool_name
                else self._error_timestamps
            )
            recent = [t for t in timestamps if t >= cutoff]
            return len(recent) / max(self.window_size / 60.0, 0.001)

    def get_session_error_rate(self) -> float:
        """Overall session error rate (errors/minute)."""
        return self.get_error_rate()

    def get_tool_error_rates(self) -> dict[str, float]:
        """Error rates for all tracked tools."""
        now = time.time()
        cutoff = now - self.window_size
        rates: dict[str, float] = {}

        with self._lock:
            for tool, timestamps in self._tool_error_timestamps.items():
                recent = [t for t in timestamps if t >= cutoff]
                rates[tool] = len(recent) / max(self.window_size / 60.0, 0.001)

        return rates

    def get_category_error_rates(self) -> dict[str, float]:
        """Error rates per category over sliding window."""
        now = time.time()
        cutoff = now - self.window_size
        rates: dict[str, float] = {}

        with self._lock:
            for cat, timestamps in self._category_timestamps.items():
                recent = [t for t in timestamps if t >= cutoff]
                rates[cat] = len(recent) / max(self.window_size / 60.0, 0.001)

        return rates

    def get_top_error_categories(self, n: int = 5) -> list[tuple[str, int]]:
        """Return top-N error categories by count."""
        with self._lock:
            sorted_cats = sorted(self._category_counts.items(), key=lambda x: x[1], reverse=True)
        return sorted_cats[:n]

    def get_summary(self) -> dict:
        """Comprehensive error summary for debugging/monitoring."""
        with self._lock:
            return {
                "total_errors": len(self._error_timestamps),
                "error_rate_per_min": self.get_session_error_rate(),
                "error_type_distribution": dict(self._error_type_counts),
                "error_category_distribution": dict(self._category_counts),
                "tool_error_rates": self.get_tool_error_rates(),
                "bursts_detected": len(self._recent_bursts),
            }

    def should_alert(self) -> bool:
        """Check if current error rate exceeds alert threshold."""
        now = time.time()
        return self.get_error_rate() >= self.alert_threshold


# ── AuditWriter ───────────────────────────────────────────────────────────


class AuditWriter:
    """Buffered writer for structured audit log records.

    Writes structured text records to an audit file with synchronous
    flush-at-turn-boundaries. No truncation — audit is never truncated.
    """

    def __init__(self, audit_file: Path, pid: int | None = None, initial_nesting: int = 0):
        self._file = audit_file
        self._pid = pid or os.getppid()
        self._buffer: list[str] = []
        self._total_tool_calls = 0
        self._session_start_time = dt.now()

        # Single source of truth for error rates — AuditWriter delegates here.
        self._error_tracker = ErrorRateTracker()
        self._lock = threading.Lock()
        self._nesting_level = initial_nesting

        try:
            audit_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    # --- Timestamp & buffering -------------------------------------------------------

    def _ts(self) -> str:
        return dt.now().isoformat(timespec="milliseconds")

    def _enqueue(self, line: str) -> None:
        self._buffer.append(line)

    def _enqueue_indented(self, label: str, text: str) -> None:
        """Enqueue a labeled block of indented lines."""
        self._enqueue(f"  | {label}:\n")
        for line in text.split("\n"):
            self._enqueue(f"  |   {line}\n")

    def _flush(self) -> None:
        if not self._buffer:
            return
        data = "".join(self._buffer)
        try:
            with open(self._file, "a", encoding="utf-8") as f:
                f.write(data)
            self._buffer.clear()  # Only clear after successful write
        except Exception:
            # Don't clear buffer — data is preserved for next attempt
            pass

    def _emit(self, record_type: str, fields: str, continuations: list[str] | None = None) -> None:
        ts = self._ts()
        nesting = f"nesting={self._nesting_level}"
        header = f"[{ts}] {record_type} {fields} {nesting}\n" if fields else f"[{ts}] {record_type} {nesting}\n"
        self._enqueue(header)
        if continuations:
            for line in continuations:
                self._enqueue(f"  | {line}\n")

    # --- Session lifecycle ---------------------------------------------------------

    def session_start(
        self,
        model: str,
        tool_count: int,
        cwd: str,
        system_prompt: str,
        tool_schema: list[dict],
    ) -> None:
        # Import here to avoid circular dependencies
        from agent_version import get_version_info
        version_info = get_version_info()
        fields = f"version={version_info['version']} branch={version_info['branch']!r} hash={version_info['hash']!r} pid={self._pid} model={model!r} tools={tool_count} cwd={cwd!r}"
        self._emit("SESSION_START", fields)
        self._enqueue_indented("system_prompt", system_prompt)

        schema_json = json.dumps(tool_schema, default=str)
        self._enqueue_indented("tool_schema", schema_json)

    def session_end(self, reason: str, context_msgs: int, total_tool_calls: int) -> None:
        duration = (dt.now() - self._session_start_time).total_seconds()
        fields = f"reason={reason!r} duration_s={duration:.1f} context_msgs={context_msgs} total_tool_calls={total_tool_calls}"
        self._emit("SESSION_END", fields)
        self.flush()

    # --- Message logging -----------------------------------------------------------

    def user(self, content: str) -> None:
        self._emit("USER", "", [content])

    def assistant(self, content: str, reasoning: str | None = None) -> None:
        parts = [f"content_len={len(content)}"]
        if reasoning:
            parts.append(f"reasoning_len={len(reasoning)}")
        fields = " ".join(parts)
        self._emit("ASSISTANT", fields)
        if content:
            self._enqueue_indented("content", content)
        if reasoning:
            self._enqueue_indented("reasoning", reasoning)

    def turn_end(
        self,
        tokens_in: int,
        tokens_out: int,
        cached: int,
        context_msgs: int,
    ) -> None:
        fields = f"tokens_in={tokens_in} tokens_out={tokens_out} cached={cached} context_msgs={context_msgs}"
        self._emit("TURN_END", fields)

    # --- Tool logging ---------------------------------------------------------------

    def tool_call(
        self,
        call_id: str,
        original_name: str,
        original_args: dict,
        final_name: str,
        final_args: dict,
        fixes: list[str],
    ) -> None:
        self._total_tool_calls += 1
        orig_json = json.dumps(original_args, default=str)
        final_json = json.dumps(final_args, default=str)
        fixes_str = "; ".join(fixes) if fixes else "none"
        fields = f"id={call_id} original_name={original_name!r} final_name={final_name!r} fixes={fixes_str}"
        self._emit("TOOL_CALL", fields)
        self._enqueue(f"  | original_args: {orig_json}\n")
        self._enqueue(f"  | final_args: {final_json}\n")

    def tool_result(
        self,
        call_id: str,
        status: str,
        duration_ms: float,
        output: str,
        output_bytes: int,
        tool_name: str | None = None,
    ) -> None:
        parts = [f"id={call_id} status={status} duration_ms={duration_ms:.0f} bytes={output_bytes}"]
        if tool_name is not None:
            parts.append(f"tool={tool_name!r}")
        fields = " ".join(parts)
        self._emit("TOOL_RESULT", fields)
        self._enqueue_indented("output", output)

    def tool_error(
        self,
        call_id: str,
        error_type: str,
        error_message: str,
        stack_trace: str | None = None,
        tool_name: str | None = None,
        tool_args: dict | None = None,
        parent_chain: list[str] | None = None,
        nesting_level: int = 0,
        session_duration_s: float | None = None,
        concurrent_ops: int | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Log tool execution error with optional stack trace and rich context."""
        category = _classify_error(error_type)
        parts = [f"id={call_id} error_type={error_type} category={category}"]
        if tool_name is not None:
            parts.append(f"tool={tool_name!r}")
        if tool_args is not None:
            parts.append(f"args={json.dumps(tool_args, default=str)}")
        if parent_chain:
            parts.append(f"chain={'→'.join(parent_chain)}")
        if nesting_level:
            parts.append(f"nesting={nesting_level}")
        if session_duration_s is not None:
            parts.append(f"session_dur={session_duration_s:.1f}s")
        if duration_ms is not None:
            parts.append(f"tool_dur={duration_ms:.0f}ms")
        if concurrent_ops is not None:
            parts.append(f"concurrent={concurrent_ops}")
        fields = " ".join(parts)

        self._emit("TOOL_ERROR", fields)
        self._enqueue(f"  | error_message: {error_message}\n")
        if stack_trace:
            self._enqueue_indented("stack_trace", stack_trace)

        # Delegate to ErrorRateTracker — it handles burst detection internally.
        self._error_tracker.record_error(tool_name=tool_name, error_type=error_type)

    def tool_blocked(
        self,
        call_id: str,
        tool_name: str,
        available_str: str,
    ) -> None:
        """Log a blocked tool invocation (expected, not an error).

        Unlike ``tool_error``, this does NOT record against ErrorRateTracker
        or trigger burst detection.  Blocked tools are an expected outcome
        of the filter, not a failure.
        """
        fields = (
            f"id={call_id} tool={tool_name!r} "
            f"available={available_str}"
        )
        self._emit("TOOL_BLOCKED", fields)

    # --- Error tracking (delegates to ErrorRateTracker) -----------------------------

    def should_alert(self) -> bool:
        """Return True if the error rate exceeds the alert threshold.

        Public facade for ``_error_tracker.should_alert()`` so callers
        never need to peer into ``audit_writer._error_tracker``.
        """
        return self._error_tracker.should_alert()

    def get_error_summary(self) -> dict:
        """Comprehensive error summary for debugging/monitoring."""
        session_dur = (dt.now() - self._session_start_time).total_seconds()
        base = self._error_tracker.get_summary()
        return {
            **base,
            "session_duration_s": session_dur,
        }

    # --- Subagent / fork logging ---------------------------------------------------

    def fork_start(self, task: str) -> None:
        self._emit("FORK_START", f"task={task!r}")
        self._nesting_level += 1
        self._flush()  # Ensure FORK_START is on disk before fork writes

    def fork_end(self, duration_s: float) -> None:
        if self._nesting_level <= 0:
            warning("Audit nesting underflow: fork_end() without matching fork_start()")
        self._nesting_level = max(0, self._nesting_level - 1)
        self._emit("FORK_END", f"duration_s={duration_s:.1f}")

    def subagent_start(self, task: str) -> None:
        self._emit("SUBAGENT_START", f"task={task!r}")
        self._nesting_level += 1
        self._flush()  # Ensure SUBAGENT_START is on disk before subagent writes

    def subagent_end(self, duration_s: float) -> None:
        if self._nesting_level <= 0:
            warning("Audit nesting underflow: subagent_end() without matching subagent_start()")
        self._nesting_level = max(0, self._nesting_level - 1)
        self._emit("SUBAGENT_END", f"duration_s={duration_s:.1f}")

    # --- Misc logging ------------------------------------------------------------

    def tool_truncated(
        self,
        call_id: str,
        tool_name: str,
        output_bytes: int,
        max_size: int,
        file_path: str,
    ) -> None:
        fields = (
            f"id={call_id} tool={tool_name!r} output_bytes={output_bytes} "
            f"max_size={max_size} file={file_path!r}"
        )
        self._emit("TOOL_TRUNCATED", fields)

    def context_compress(
        self,
        before_tokens: int,
        after_tokens: int,
        ratio: float,
    ) -> None:
        fields = f"before_tokens={before_tokens} after_tokens={after_tokens} ratio={ratio:.2f}"
        self._emit("CONTEXT_COMPRESS", fields)

    # --- Compression logging ----------------------------------------------------------------

    def compress_start(self, step_name: str, bytes_before: int, msgs_before: int) -> None:
        """Log the start of a compression pipeline step."""
        fields = f"step={step_name!r} bytes_before={bytes_before} msgs_before={msgs_before}"
        self._emit("COMPRESS_STEP_START", fields)

    def compress_action(self, step_name: str, action_type: str, details: str) -> None:
        """Log an individual compression action within a step."""
        fields = f"step={step_name!r} action={action_type} details={details}"
        self._emit("COMPRESS_ACTION", fields)

    def compress_step_end(self, step_name: str, bytes_after: int, msgs_after: int, status: str) -> None:
        """Log the end of a compression pipeline step."""
        fields = f"step={step_name!r} bytes_after={bytes_after} msgs_after={msgs_after} status={status!r}"
        self._emit("COMPRESS_STEP_END", fields)

    # --- Console-to-audit bridging -------------------------------------------------

    def console_error(self, message: str) -> None:
        """Log a console error message to audit."""
        self._emit("CONSOLE_ERROR", "", [message])

    def console_warning(self, message: str) -> None:
        """Log a console warning message to audit."""
        self._emit("CONSOLE_WARNING", "", [message])

    # --- Flush / close ------------------------------------------------------------

    def flush(self) -> None:
        self._flush()

    def close(self) -> None:
        self._flush()

    @property
    def total_tool_calls(self) -> int:
        return self._total_tool_calls


# ── Utility functions ────────────────────────────────────────────────────


def write_oversized_output(output: str, prefix: str | None) -> str | None:
    """Write oversized tool output to LOG_DIR/{prefix}.toolout.{NNN}.

    Returns the file path, or None if writing failed.
    """
    if prefix is None:
        return None
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        counter = 1
        while counter <= 1000:
            fname = f"{prefix}.toolout.{counter:03d}"
            filepath = LOG_DIR / fname
            if not filepath.exists():
                filepath.write_text(output, encoding="utf-8")
                return str(filepath)
            counter += 1
        emit_console_warning(
            f"Oversized output discarded — exhausted 1000 toolout slots for {prefix}. "
            "Consider cleaning old log files."
        )
        return None
    except Exception:
        return None


def log_failed_api_request(request_body: dict, log_file: Path | None = None) -> None:
    """Write failed LLM request body to a JSON file for debugging."""
    try:
        if log_file is not None:
            out_dir = log_file.parent
            prefix = log_file.stem
        elif SESSION_PREFIX is not None:
            out_dir = LOG_DIR
            prefix = SESSION_PREFIX
        else:
            out_dir = LOG_DIR
            prefix = f"{os.getppid()}_{dt.now().strftime('%Y%m%d%H%M%S')}"

        out_dir.mkdir(parents=True, exist_ok=True)
        filepath = out_dir / f"{prefix}.failed_request.json"

        record = {
            "timestamp": dt.now().isoformat(),
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "request": request_body,
        }
        filepath.write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        pass


# ── AgentSessionManager ────────────────────────────────────────────────────


class AgentSessionManager:
    """Manages session lifecycle: file paths, audit writer, error detection,
    and token tracking.

    Extracted from TauBot to reduce the god class. Provides a focused
    interface for session file management, audit logging, and token accounting.
    """

    def __init__(
        self,
        setup_files: bool = True,
        audit_file: Path | None = None,
        context_file: Path | None = None,
    ) -> None:
        """Initialise session manager.

        Args:
            setup_files: If True, ensure LOG_DIR exists and resolve file paths
                from the session prefix. Set to False when paths are provided
                explicitly (e.g., during tests).
            audit_file: Explicit audit file path (overrides env / prefix logic).
            context_file: Explicit context file path (overrides env / prefix logic).
        """
        self._audit_file = audit_file
        self._context_file = context_file
        self._audit_writer: AuditWriter | None = None
        self._tokens = TokenTracker()

        if setup_files:
            global SESSION_PREFIX
            try:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                log_dir_error(LOG_DIR, str(e))
                raise RuntimeError(f"Cannot create log directory {LOG_DIR}: {e}") from e

            # Resolve session prefix (global, set once by root agent).
            if SESSION_PREFIX is None:
                prefix = _get_log_filename_prefix()
                SESSION_PREFIX = prefix
            else:
                prefix = SESSION_PREFIX

            self._audit_file = LOG_DIR / f"{prefix}.audit"
            self._context_file = LOG_DIR / f"{prefix}.context"

            # Environment-variable overrides (checked after prefix resolution).
            if env_audit := os.getenv("TAU_AUDIT_LOG_FILE"):
                self._audit_file = Path(env_audit)
            if env_ctx := os.getenv("TOOL_CONTEXT_FILE"):
                self._context_file = Path(env_ctx)

            # Parent audit file inheritance (for fork unification).
            # TAU_PARENT_AUDIT_FILE takes highest priority — forks append to parent's file.
            parent_audit = os.getenv("TAU_PARENT_AUDIT_FILE")
            if parent_audit:
                self._audit_file = Path(parent_audit)

    # TokenTracker attributes delegated via __getattr__/__setattr__ (whitelist-based).
    _TOKEN_ATTRS = frozenset((
        "input_tokens", "output_tokens", "cached_tokens",
        "last_turn_input_tokens", "last_turn_output_tokens",
        "last_turn_cached_tokens", "last_exact_context_tokens",
        "cache_tracker",
        "record_call_stats", "clear_tokens", "reset_last_turn",
    ))

    def __getattr__(self, name: str) -> Any:
        """Delegate TokenTracker attributes to the internal tracker."""
        if name in self._TOKEN_ATTRS:
            return getattr(self._tokens, name)
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        """Delegate TokenTracker attribute writes to the internal tracker."""
        if name in self._TOKEN_ATTRS:
            setattr(self._tokens, name, value)
        else:
            super().__setattr__(name, value)

    # ── File paths ──────────────────────────────────────────────────────────
    @property
    def audit_file(self) -> Path:
        """Path to the audit log file."""
        if self._audit_file is None:
            raise RuntimeError("Session files not initialised")
        return self._audit_file

    @audit_file.setter
    def audit_file(self, path: Path) -> None:
        self._audit_file = path

    @property
    def context_file(self) -> Path:
        """Path to the context file."""
        if self._context_file is None:
            raise RuntimeError("Session files not initialised")
        return self._context_file

    @context_file.setter
    def context_file(self, path: Path) -> None:
        self._context_file = path

    # ── Audit writer ────────────────────────────────────────────────────────

    @property
    def audit_writer(self) -> AuditWriter:
        """Lazy-initialised AuditWriter for the session."""
        if self._audit_writer is None:
            initial_nesting = int(os.getenv("TAU_FORK_NESTING", "0"))
            self._audit_writer = AuditWriter(self.audit_file, initial_nesting=initial_nesting)
            set_audit_writer(self._audit_writer)
        return self._audit_writer

    def init_audit_writer(self) -> None:
        """Eagerly initialise the audit writer."""
        initial_nesting = int(os.getenv("TAU_FORK_NESTING", "0"))
        self._audit_writer = AuditWriter(self.audit_file, initial_nesting=initial_nesting)
        set_audit_writer(self._audit_writer)

    # ── Error burst detection ────────────────────────────────────────────────

    def has_error_burst(self) -> bool:
        """Return True if the audit writer's error tracker should alert.

        Delegates to ``AuditWriter.should_alert()`` — no more three-level
        private access into ``audit_writer._error_tracker``.
        """
        return self.audit_writer.should_alert()
