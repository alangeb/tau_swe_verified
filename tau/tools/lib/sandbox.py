"""Sandbox utilities for validating file paths against the working directory.

Entry points:
- ``validate_path`` — resolve + sandbox check (no double-call). For read-only tools.
- ``check_path`` — same + double-call confirmation. For write tools.

State lives on ``agent._sandbox_last_call`` so forked agents stay independent.
Double-call compares *raw* file_path (not resolved) — the LLM must repeat the
exact same call, not just an equivalent one.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

logger = logging.getLogger(__name__)

# ── Whitelist helpers ────────────────────────────────────────────────────

# Cache keyed by frozenset of allowed paths to handle different configs.
_resolve_whitelist_cache: dict[tuple[str, ...], list[Path]] = {}


def _resolve_whitelist(allowed_paths: list[str]) -> list[Path]:
    """Resolve and cache whitelist paths, keyed by the input tuple."""
    key = tuple(allowed_paths)
    if key not in _resolve_whitelist_cache:
        _resolve_whitelist_cache[key] = [
            Path(p).expanduser().resolve() for p in allowed_paths
        ]
    return _resolve_whitelist_cache[key]


def _is_whitelisted(path: Path, allowed_paths: list[str]) -> bool:
    """Check if *path* is inside any whitelisted directory.

    Symlinks are resolved before checking to prevent traversal attacks.
    """
    resolved = path.resolve()
    for wl_path in _resolve_whitelist(allowed_paths):
        try:
            resolved.relative_to(wl_path)
            return True
        except ValueError:
            continue
    return False


# ── Rejection messages ───────────────────────────────────────────────────

def _build_rejection(file_path: str, allowed_paths: list[str] | None = None) -> str:
    msg = (
        f"❌ PATH REJECTED: Path is outside working directory.\n"
        f"\nPath: '{file_path}'\n"
        f"Working directory: {Path.cwd()}\n"
    )

    if allowed_paths:
        wl_display = ", ".join(f"'{p}'" for p in allowed_paths[:5])
        if len(allowed_paths) > 5:
            wl_display += f" ... (+{len(allowed_paths) - 5} more)"
        msg += (
            f"\nWhitelisted paths: {wl_display}\n"
            f"\nTo allow this path, add it to 'path_security.allowed_paths' in tau.json,\n"
            f"or issue the EXACT SAME call again (same tool, same arguments) to confirm."
        )
    else:
        msg += (
            f"\nTo allow this path, issue the EXACT SAME call again (same tool, same arguments).\n"
            f"The second identical call will be allowed."
        )
    return msg


# ── Path resolution ──────────────────────────────────────────────────────

def _resolve(file_path: str) -> tuple[Path, str | None]:
    """Return ``(resolved_path, None)`` or ``(Path(""), error_msg)``."""
    try:
        path = Path(file_path)
        if not path.is_absolute():
            path = Path.cwd() / file_path
        return (path.resolve(), None)
    except Exception as e:
        return (Path(""), f"ERROR: Invalid path '{file_path}': {e}")


# ── Double-call signature ────────────────────────────────────────────────

def _make_signature(tool_name: str, file_path: str) -> str:
    """Serializable signature for double-call comparison."""
    try:
        return f"{tool_name}:{json.dumps({'file_path': file_path}, sort_keys=True)}"
    except (TypeError, ValueError):
        return f"{tool_name}:{file_path}"


# ── Sandbox check ────────────────────────────────────────────────────────

def _check_sandbox(
    tool_name: str,
    agent: "TauBot",
    file_path: str,
    path: Path,
    cwd: Path,
    allowed_paths: list[str] | None = None,
    write_operation: bool = False,
) -> str | None:
    """Check sandbox; return rejection message or ``None``.

    Args:
        allowed_paths: Whitelisted paths from config. If provided, whitelisted
            paths bypass double-call for reads but NOT for writes.
        write_operation: If True, double-call confirmation is ALWAYS required
            even for whitelisted paths.
    """
    # Path is inside working directory — always allowed.
    if path.is_relative_to(cwd):
        agent._sandbox_last_call = None
        return None

    # Whitelist check: reads from whitelisted paths are allowed directly.
    # Writes still require double-call confirmation.
    if allowed_paths and not write_operation:
        if _is_whitelisted(path, allowed_paths):
            logger.debug("sandbox_whitelist_hit: path=%s", file_path)
            agent._sandbox_last_call = None
            return None

    # Double-call confirmation required.
    current_call = _make_signature(tool_name, file_path)
    last = getattr(agent, "_sandbox_last_call", None)
    if current_call == last:
        agent._sandbox_last_call = None
        return None

    agent._sandbox_last_call = current_call
    logger.debug("sandbox_whitelist_miss: path=%s", file_path)
    return _build_rejection(file_path, allowed_paths)


# ── Public API ───────────────────────────────────────────────────────────

def get_allowed_paths(agent: "TauBot") -> list[str] | None:
    """Extract whitelist paths from agent config, or None if unavailable."""
    if hasattr(agent, "config"):
        return agent.config.path_security.allowed_paths
    return None


def validate_path(
    file_path: str,
    allowed_paths: list[str] | None = None,
) -> tuple[Path | None, str | None]:
    """Resolve and validate path stays within cwd or whitelist. No double-call.

    Args:
        allowed_paths: Whitelisted paths. If provided, paths inside whitelisted
            directories are allowed without double-call confirmation.
    """
    path, err = _resolve(file_path)
    if err:
        return (None, err)

    cwd = Path.cwd().resolve()
    if path.is_relative_to(cwd):
        return (path, None)

    # Check whitelist for read operations.
    if allowed_paths and _is_whitelisted(path, allowed_paths):
        return (path, None)

    return (None, f"ERROR: Path '{file_path}' is outside working directory")


def check_path(
    tool_name: str,
    agent: "TauBot",
    file_path: str,
    allowed_paths: list[str] | None = None,
    write_operation: bool = True,
) -> tuple[Path | None, str | None]:
    """Resolve, validate sandbox, enforce double-call confirmation.

    Args:
        allowed_paths: Whitelisted paths. Reads from whitelisted paths bypass
            double-call. Writes ALWAYS require double-call confirmation.
        write_operation: If True, double-call is required even for whitelisted
            paths.
    """
    path, err = _resolve(file_path)
    if err:
        return (None, err)

    cwd = Path.cwd().resolve()
    rejection = _check_sandbox(
        tool_name, agent, file_path, path, cwd,
        allowed_paths=allowed_paths,
        write_operation=write_operation,
    )
    if rejection:
        return (None, rejection)
    return (path, None)
