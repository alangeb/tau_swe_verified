"""Health command — model server health monitoring dashboard."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

from agent_console_primitives import blank_line, echo, status, display_success, display_warning, display_error
from agent_model_health import get_health_monitor, CircuitState, HealthStatus

name = "health"
description = "Model server health monitoring dashboard"
subcommands = ("status", "reset", "check")
help_text = """Usage: /health [status|reset|check]

Monitor model server health:
  /health          Show health status dashboard
  /health status   Show detailed health status
  /health reset    Reset health monitoring state
  /health check    Run a connection check
"""

# ── Status icons ──
_STATUS_ICONS = {
    CircuitState.CLOSED: "\u2705",   # check mark
    CircuitState.OPEN: "\u274c",     # cross mark
    CircuitState.HALF_OPEN: "\u26a0", # warning
}


def _display_status_dashboard(status: HealthStatus) -> None:
    """Display the health monitoring dashboard."""
    blank_line()
    echo("=" * 50)
    echo("  MODEL SERVER HEALTH DASHBOARD")
    echo("=" * 50)
    blank_line()

    # Circuit state
    icon = _STATUS_ICONS.get(status.circuit_state, "?")
    state_label = status.circuit_state.value.upper()
    if status.circuit_state == CircuitState.CLOSED:
        display_success(f"  Circuit: {icon} {state_label}")
    elif status.circuit_state == CircuitState.OPEN:
        display_error(f"  Circuit: {icon} {state_label}")
    else:
        display_warning(f"  Circuit: {icon} {state_label}")

    # Counts
    echo(f"  Total successes: {status.total_successes}")
    echo(f"  Total failures:  {status.total_failures}")
    echo(f"  Consecutive failures: {status.consecutive_failures}")
    echo(f"  Consecutive successes: {status.consecutive_successes}")

    # Failure rate
    total = status.total_failures + status.total_successes
    rate = status.total_failures / total if total > 0 else 0.0
    echo(f"  Failure rate: {rate:.1%}")

    # Recovery
    echo(f"  Recovery attempts: {status.recovery_attempts}")

    # Last events
    if status.last_error:
        display_warning(f"  Last error: {status.last_error}")

    blank_line()


def run(agent: "TauBot", args: str) -> None:
    """Execute the health command."""
    parts = shlex.split(args.strip()) if args.strip() else []
    subcommand = parts[0] if parts else "status"

    monitor = get_health_monitor()

    if subcommand in ("status", ""):
        status_data = monitor.get_status()
        _display_status_dashboard(status_data)

    elif subcommand == "reset":
        monitor.reset()
        display_success("  Health monitoring state reset")
        blank_line()

    elif subcommand == "check":
        status("  Checking model server connection...")
        result = monitor.check_connection()
        if result:
            display_success("  Server is reachable")
        else:
            display_error("  Server is unreachable")
        blank_line()

    else:
        display_error(f"  Unknown subcommand: {subcommand}")
        echo("  Available: status, reset, check")
        blank_line()
