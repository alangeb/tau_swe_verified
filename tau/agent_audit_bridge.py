"""Audit bridge — breaks the circular dependency between console modules and agent_session.

This module provides a clean interface for console-to-audit bridging WITHOUT
creating circular imports. It contains NO imports from agent_console,
agent_console_messages, or agent_session, making the dependency graph acyclic.

Dependency graph (acyclic):
    agent_audit_bridge  (no deps on console/session)
    ↑                     ↑
    agent_console agent_session
    (facade imports bridge)   (imports bridge)
"""

from __future__ import annotations

import time
import threading
from typing import Any, Callable

__all__ = [
    "set_audit_writer",
    "register_console_warning_callback",
    "emit_console_warning",
    "log_console_error",
    "log_console_warning",
]


# ── Audit writer protocol ────────────────────────────────────────────────────
# We use a string-based protocol description instead of a formal Protocol class
# to avoid importing from agent_session (which would create the cycle).
#
# The audit writer object must support:
#   console_error(message: str) -> None
#   console_warning(message: str) -> None


# ── Warning throttle ─────────────────────────────────────────────────────────
# Rate-limits audit log warning entries to reduce CONSOLE_WARNING volume by ~90%.
# Tracks warnings by normalized key (first _KEY_LEN chars of message).
# Allows first occurrence always, then 1 per _INTERVAL_SECONDS per pattern.
# After _SUPPRESS_AFTER occurrences, suppresses and counts.


class _WarningThrottle:
    """Thread-safe warning rate limiter.

    Reduces warning noise by:
    - Allowing first occurrence of each pattern always
    - Allowing 1 per interval per pattern
    - Suppressing after threshold and counting
    - Pruning stale entries to prevent unbounded growth
    """

    _KEY_LEN = 80  # Normalize key to first N chars
    _INTERVAL = 30.0  # Seconds between allowed repeats
    _SUPPRESS_AFTER = 5  # Count after N occurrences before suppressing
    _PRUNE_INTERVAL = 60.0  # Seconds between prune checks

    def __init__(self):
        self._lock = threading.Lock()
        # pattern_key -> {count, last_time, suppressed}
        self._patterns: dict[str, dict[str, Any]] = {}
        self._total_suppressed = 0
        self._last_prune = 0.0

    def _maybe_prune(self, now: float) -> None:
        """Remove stale entries to prevent unbounded growth.

        Prunes incrementally (max 10 at a time) to avoid pauses.
        """
        if now - self._last_prune < self._PRUNE_INTERVAL:
            return
        self._last_prune = now
        stale_threshold = now - 300.0
        # Collect stale keys first, then delete (avoid mutation during iteration)
        stale = [k for k, v in self._patterns.items() if v["last_time"] < stale_threshold]
        for k in stale[:10]:  # Limit per-prune to avoid pauses
            del self._patterns[k]

    def should_emit(self, message: str) -> bool:
        """Return True if this warning should be emitted."""
        key = message[:self._KEY_LEN]
        now = time.time()

        with self._lock:
            self._maybe_prune(now)

            entry = self._patterns.get(key)
            if entry is None:
                # First occurrence — always allow
                self._patterns[key] = {"count": 1, "last_time": now, "suppressed": 0}
                return True

            entry["count"] += 1
            count = entry["count"]

            # If already suppressed, never re-emit
            if entry["suppressed"] > 0:
                entry["suppressed"] += 1
                self._total_suppressed += 1
                return False

            # Allow if within interval
            if now - entry["last_time"] >= self._INTERVAL:
                entry["last_time"] = now
                return True

            # Suppress after threshold
            if count > self._SUPPRESS_AFTER:
                entry["suppressed"] += 1
                self._total_suppressed += 1
                return False

            # Allow up to threshold
            return True

    def get_summary(self) -> dict[str, Any]:
        """Get throttle statistics."""
        with self._lock:
            return {
                "total_patterns": len(self._patterns),
                "total_suppressed": self._total_suppressed,
                "top_patterns": sorted(
                    [
                        {
                            "key": k,
                            "count": v["count"],
                            "suppressed": v["suppressed"],
                        }
                        for k, v in self._patterns.items()
                        if v["suppressed"] > 0
                    ],
                    key=lambda x: x["suppressed"],
                    reverse=True,
                )[:10],
            }

    def reset(self):
        """Clear all throttle state. Used for testing."""
        with self._lock:
            self._patterns.clear()
            self._total_suppressed = 0


# Global throttle instance — shared across the process.
_warning_throttle = _WarningThrottle()


# ── Global state ─────────────────────────────────────────────────────────────
# Single audit writer reference, shared across the process.
# Set once during AgentSessionManager initialization.
# Forks inherit via subprocess isolation (separate address spaces).
_audit_writer: Any = None

# Callback registered by agent_console_messages to emit console warnings without
# importing agent_session directly. This breaks the cycle.
#
# NOTE: agent_console_messages registers its warning() function at module load time.
# If emit_console_warning() is called before the callback is registered, the
# warning is silently dropped. This is acceptable: agent_console_messages is always
# imported early via agent_console during normal startup.
_console_warning_callback: Callable[[str], None] | None = None


def set_audit_writer(writer: Any) -> None:
    """Set the global audit writer reference for console-to-audit bridging.

    This allows console functions (error, warning) to log to audit
    without requiring a direct dependency on AuditWriter.

    Args:
        writer: An AuditWriter instance, or None to clear.
    """
    global _audit_writer
    _audit_writer = writer


def register_console_warning_callback(callback: Callable[[str], None]) -> None:
    """Register a callback for emitting console warnings.

    agent_session calls this to emit warnings without importing agent_session.
    agent_console_messages registers its warning() function here.

    Args:
        callback: A function that accepts a warning message string.
    """
    global _console_warning_callback
    _console_warning_callback = callback


def emit_console_warning(message: str) -> None:
    """Emit a console warning via the registered callback.

    No-op if no callback is registered (e.g., before agent_console_messages is loaded).
    Exceptions from the callback are caught to prevent audit/logging failures
    from breaking tool execution.
    """
    if _console_warning_callback is not None:
        try:
            _console_warning_callback(message)
        except Exception:
            # Callback failure must never break tool execution.
            pass


def log_console_error(message: str) -> None:
    """Log a console error to the audit writer.

    Called by agent_console_messages.error() to bridge console output to audit.
    No-op if no writer is set.
    """
    global _audit_writer
    writer = _audit_writer
    if writer is not None:
        try:
            writer.console_error(message)
        except Exception:
            # Audit failure must never suppress console output.
            pass


def log_console_warning(message: str) -> None:
    """Log a console warning to the audit writer.

    Called by agent_console_messages.warning() to bridge console output to audit.
    Applies throttle to reduce audit log noise.
    No-op if no writer is set.
    """
    global _audit_writer
    writer = _audit_writer
    if writer is not None:
        # Check throttle — suppress if rate-limited
        if not _warning_throttle.should_emit(message):
            return
        try:
            writer.console_warning(message)
        except Exception:
            # Audit failure must never suppress console output.
            pass
