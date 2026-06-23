from __future__ import annotations

from tools import ToolMetadata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

# ── Tool metadata ──

metadata = ToolMetadata(
    name="skill",
    description=(
        "Unified skill tool. Empty string lists all available skills. "
        "Exact name loads skill content. Falls back to intelligent fork-based discovery on no match."
    ),
    max_size=16384,
)

_SKILLS_DIR = Path(__file__).parent.parent / "skills"
# NOTE: _skills_cache is intentionally never invalidated.  Skills are loaded
# once at agent startup and the skills directory is static during runtime.
_skills_cache: list[dict] | None = None


# ── Skill discovery (delegated to shared utility) ──

_skills_lock = threading.Lock()

def _get_skill_list() -> list[dict]:
    """Get list of skill info (name, description, category). Cached after first call.

    Discovers skills in two formats:
    1. Folder-per-skill (standard): ``skills/<name>/SKILL.md``
    2. Legacy flat files: ``skills/<name>.md`` (backward compatibility)

    Uses double-check locking to prevent double-initialization races on first access.
    """
    global _skills_cache
    if _skills_cache is not None:
        return _skills_cache
    with _skills_lock:
        if _skills_cache is not None:
            return _skills_cache
        from lib.skill_discovery import discover_skills
        _skills_cache = discover_skills(_SKILLS_DIR)
        return _skills_cache


def _format_skill_list() -> str:
    """Format skill list as markdown."""
    skills = _get_skill_list()
    if not skills:
        return "No skills found."
    return "Available skills:\n\n" + "".join(
        f"- **{s['name']}**: {s['description']}\n" for s in skills
    )


def _load_skill(skill_name: str) -> str | None:
    """Load skill content by exact name, then case-insensitive/fuzzy match.

    Supports two formats:
    1. Folder-per-skill (standard): ``skills/<name>/SKILL.md``
    2. Legacy flat files: ``skills/<name>.md``
    """
    if not _SKILLS_DIR.exists():
        return None

    # 1. Try folder-per-skill: skills/<name>/SKILL.md
    folder_skill = _SKILLS_DIR / skill_name / "SKILL.md"
    if folder_skill.exists():
        return folder_skill.read_text(encoding="utf-8")

    # 2. Try legacy flat file: skills/<name>.md
    legacy_file = _SKILLS_DIR / f"{skill_name}.md"
    if legacy_file.exists():
        return legacy_file.read_text(encoding="utf-8")

    # 3. Case-insensitive / fuzzy match against discovered skills
    clean_query = re.sub(r"[^a-zA-Z0-9]", "", skill_name.lower())
    for skill in _get_skill_list():
        clean_name = re.sub(r"[^a-zA-Z0-9]", "", skill["name"].lower())
        if clean_name == clean_query or skill["name"].lower() == skill_name.lower():
            # Try both formats for matched skill
            for candidate in [
                _SKILLS_DIR / skill["name"] / "SKILL.md",
                _SKILLS_DIR / f"{skill['name']}.md",
            ]:
                if candidate.exists():
                    return candidate.read_text(encoding="utf-8")
    return None


# ── Args schema ──

@dataclass
class Args:
    skill_name: str = field(
        default="",
        metadata={
            "description": (
                "Skill name to load. Empty string lists all available skills. "
                "Exact name loads skill content. Falls back to fork-based discovery on no match."
            )
        },
    )



# ── Execution ──

def run(skill_name: str, agent: TauBot, tool_call_id: str | None = None) -> str:
    """List skills (empty string) or load skill content by name."""
    if not skill_name:
        return _format_skill_list()

    content = _load_skill(skill_name)
    if content:
        return content

    # Fork-based discovery: spawn a fork to search for the skill
    from agent_tool_filter import ToolFilter
    from agent_subagent import invoke_fork_sync

    prompt = (
        f"Find and load the skill named '{skill_name}'. "
        "Search the skills directory for matching files. "
        "If found, return the skill content. If not found, explain what's available."
    )

    try:
        return invoke_fork_sync(
            prompt=prompt,
            parent_context=agent.context,
            parent_agent=agent,
            nesting_count=agent.nesting_count,
            tool_call_id=tool_call_id,
            tool_filter=ToolFilter(
                allowlist={"file_read", "glob", "ls", "end_turn"},
                denied_message=(
                    "Tool '{tool_name}' is not permitted in skill discovery. "
                    "Use only: {available_tools}."
                ),
            ),
            config=agent.config,
            nesting_threshold=agent.config.nesting.depth_threshold,
        )
    except Exception as e:
        return f"ERROR: Skill '{skill_name}' not found and fork discovery failed: {type(e).__name__}: {e}"
