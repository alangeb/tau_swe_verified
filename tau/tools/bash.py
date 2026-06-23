"""Execute shell commands with configurable timeout. Dangerous commands (rm -rf, sudo, git --force) are REJECTED on first attempt; identical repeat is ALLOWED (double-call confirmation)."""

from __future__ import annotations

from tools import ToolMetadata

import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from agent_core import TauBot


# ── Pattern definitions ──────────────────────────────────────────────────────

class DangerPattern(NamedTuple):
    regex: str
    category: str
    reason: str
    severity: str
    guidance: str


# ── Tool metadata ────────────────────────────────────────────────────────────

metadata = ToolMetadata(
    name="bash",
    description=(
        "Execute shell commands with configurable timeout. "
        "Dangerous commands (rm -rf, sudo, git --force) are REJECTED on first attempt; "
        "identical repeat is ALLOWED (double-call confirmation). "
        "Do NOT use for background tasks ('&') — use background_* tools instead."
    ),
    aliases_cmd=[
        "run_shell_command", "listdir", "list_directory", "listfiles",
        "run", "shell", "cmd",
    ],
    aliases_arg={"command": "cmd"},
    max_size=524288,
    timeout=300,  # Must match Args default timeout
)


# ── Dangerous command patterns ───────────────────────────────────────────────

DANGEROUS_PATTERNS: list[DangerPattern] = [
    DangerPattern(
        r":\(\)\s*\{\s*:\s*\|\s*:&\s*\}\s*:",
        "fork_bomb", "Fork bomb - can crash system by spawning infinite processes",
        "critical", "This will crash your system. Do not execute.",
    ),
    DangerPattern(
        r"truncate\s+-s\s+0\s+/dev/sd",
        "disk_wipe", "truncate on disk device - can wipe entire disk",
        "critical", "This will destroy all data on the target disk.",
    ),
    DangerPattern(
        r"shred\s+-u",
        "secure_delete", "shred with -u flag - securely deletes and removes files",
        "high", "This permanently destroys file data and removes the file.",
    ),
    DangerPattern(
        r"find\s+.+\s+-delete",
        "mass_deletion", "find with -delete - can mass delete files matching pattern",
        "high", "Verify find pattern carefully - this permanently deletes matching files.",
    ),
    DangerPattern(
        r"dd\s+.*?(if|of)=/dev/(sd|nvme|vd)",
        "dd_disk_write", "dd command with disk device - can destroy entire disk/partition",
        "critical", "Verify target device carefully - this can destroy entire disks.",
    ),
    DangerPattern(
        r"\bchmod\s+(-R\s+)?777\b",
        "chmod_777", "chmod 777 - sets world-writable permissions (major security risk)",
        "high", "Consider more restrictive permissions.",
    ),
    DangerPattern(
        r">(?:>>)?\s*/(etc|root|var)/",
        "redirect_sensitive", "Redirecting output to sensitive system path (/etc, /root, /var) - can overwrite system files",
        "high", "Verify target path carefully to avoid overwriting system files.",
    ),
    DangerPattern(
        r"fstrim\s+-v\s+/",
        "disk_formatting", "fstrim -v command - can trim filesystem data",
        "high", "Ensure this is intentional for the target filesystem.",
    ),
    DangerPattern(
        r"(curl|wget)\s+\S+\s*\|\s*(bash|sh|sh-)",
        "curl_wget_pipe", "curl/wget | bash/sh - downloads and executes remote script (security risk)",
        "critical", "Review downloaded script before executing.",
    ),
    DangerPattern(
        r"\beval\s+[\$\"']",
        "eval_exec", "eval/exec with command - executes dynamically constructed code (security risk)",
        "high", "Ensure input is trusted before evaluating.",
    ),
    DangerPattern(
        r"\b(apt\s+(remove|purge)|apt-get\s+autoremove|apt\s+purge)\b",
        "package_removal", "Package removal command - can break system dependencies",
        "medium", "Ensure you understand the impact on system dependencies.",
    ),
    DangerPattern(
        r"\bgit\s+(reset\s+--hard|push\s+--force|merge\s+--abort)\b",
        "git_dangerous", "Git destructive operation - can destroy local changes or force push unwanted commits",
        "critical", "Ensure you've committed/saved important work before proceeding.",
    ),
    DangerPattern(
        r"\b(mkfs|mkfs\.ext4|fdisk|parted)\b",
        "disk_formatting", "Disk formatting/partitioning tool - can destroy disk data",
        "critical", "Verify target device - this will destroy all data on the disk.",
    ),
    DangerPattern(
        r"systemctl\s+stop\s+(networking|network|NetworkManager)",
        "network_disruption", "Stopping network service - will disconnect network",
        "high", "This will disrupt network connectivity. Ensure this is intentional.",
    ),
    DangerPattern(
        r"iptables\s+-F",
        "firewall_flush", "iptables flush - removes all firewall rules",
        "high", "This removes all firewall protection. Ensure you have replacement rules ready.",
    ),
    DangerPattern(
        r"reboot\s+|-w\s+[0-9]+",
        "system_reboot", "System reboot command - will restart the system",
        "critical", "This will restart the system. Ensure all work is saved.",
    ),
]


# ── Pattern checking ─────────────────────────────────────────────────────────

def _check_command(cmd: str) -> dict | None:
    """Check command for dangerous patterns. Returns match dict or None."""
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern.regex, cmd):
            return {
                "regex": pattern.regex, "category": pattern.category,
                "reason": pattern.reason, "severity": pattern.severity,
                "guidance": pattern.guidance,
            }
    return None


def _build_rejection(danger: dict) -> str:
    """Build rejection message for dangerous command."""
    return (
        f"[DANGEROUS COMMAND DETECTED]\n"
        f"Category: {danger['category']}\n"
        f"Reason: {danger['reason']}\n"
        f"Severity: {danger['severity']}\n"
        f"Guidance: {danger['guidance']}\n"
        f"Command rejected to prevent potential system damage."
    )


def _check_and_block(agent: TauBot, cmd: str) -> str | None:
    """Block dangerous commands on first attempt; allow on retry (double-call confirmation)."""
    danger = _check_command(cmd)
    if danger:
        last_call = getattr(agent, "_bash_last_call", None)
        if last_call == cmd:
            return None  # Allow on second attempt
        setattr(agent, "_bash_last_call", cmd)
        return _build_rejection(danger)
    return None


# ── Args schema ──────────────────────────────────────────────────────────────

@dataclass
class Args:
    """Arguments for the bash tool."""
    cmd: str
    timeout: int = 300


# ── Execution ────────────────────────────────────────────────────────────────

def run(cmd: str, agent: TauBot, tool_call_id: str | None, timeout: int = 300, **kwargs) -> str:
    """Execute shell command with safety checks."""
    rejection = _check_and_block(agent, cmd)
    if rejection:
        return rejection

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, check=False, start_new_session=True,
        )
        output = result.stdout
        if result.stderr:
            output += result.stderr
        return output.strip()
    except subprocess.TimeoutExpired as e:
        partial = ""
        if e.stdout:
            partial += e.stdout if isinstance(e.stdout, str) else e.stdout.decode("utf-8", errors="replace")
        if e.stderr:
            partial += e.stderr if isinstance(e.stderr, str) else e.stderr.decode("utf-8", errors="replace")
        if partial.strip():
            return f"{partial.strip()}\n\n[TIMEOUT: Command timed out after {timeout} seconds]"
        return f"[TIMEOUT: Command timed out after {timeout} seconds]"
    except Exception as e:
        return f"Command execution failed: {str(e)}"
