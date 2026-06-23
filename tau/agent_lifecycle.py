"""Agent lifecycle management — system-wide interrupt/exit flags.

Consolidates the module-level _interrupted and _exit_requested flags into
a proper class with class-attribute-based state ownership.

Thread safety
-------------
All attribute access uses plain class attributes (bool).  Under CPython's
GIL, bool reads and writes are atomic — no lock is needed.  This class is
NOT safe for use with alternative Python implementations that lack a GIL
(e.g., Jython, PyPy with --no-gil).
"""

__all__ = ["AgentLifecycle"]


class AgentLifecycle:
    """Manage system-wide interrupt/exit flags for cooperative shutdown.

    Uses class attributes for state ownership — no module-level variables
    or `global` statements required.

    Thread safety: safe under CPython's GIL (bool reads/writes are atomic).
    Not safe for GIL-free interpreters.
    """

    _interrupted: bool = False
    _exit_requested: bool = False

    # ── Interrupt ──────────────────────────────────────────────────────────

    @classmethod
    def is_interrupted(cls) -> bool:
        """Return whether an interrupt (first Ctrl+C) has been received."""
        return cls._interrupted

    @classmethod
    def set_interrupted(cls, value: bool) -> None:
        """Set the interrupt flag."""
        cls._interrupted = value

    # ── Exit ───────────────────────────────────────────────────────────────

    @classmethod
    def is_exit_requested(cls) -> bool:
        """Return whether an exit (second Ctrl+C or /exit) has been requested."""
        return cls._exit_requested

    @classmethod
    def set_exit_requested(cls, value: bool) -> None:
        """Set the exit-requested flag."""
        cls._exit_requested = value

    # ── Reset (testing only — private) ─────────────────────────────────────

    @classmethod
    def _reset(cls) -> None:
        """Reset both flags to False.  Testing only — not part of public API."""
        cls._interrupted = False
        cls._exit_requested = False
