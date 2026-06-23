"""Shared skill discovery utilities.

Provides a single source of truth for skill path resolution, name extraction,
and discovery across both folder-per-skill and legacy flat-file formats.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import TypedDict


class SkillInfo(TypedDict):
    """Metadata for a discovered skill."""
    name: str
    description: str
    category: str
    file: str


def skill_name_from_path(skill_file: Path) -> str:
    """Resolve skill name from path: folder-per-skill → parent dir, legacy → stem.

    This is the canonical name extraction used by both tools/skill.py and
    validate_skills.py.  Do NOT duplicate this logic elsewhere.
    """
    return skill_file.parent.name if skill_file.name == "SKILL.md" else skill_file.stem


def discover_skills(directory: Path) -> list[SkillInfo]:
    """Discover skills in both formats, deduplicated (folder-per-skill wins).

    Returns skills sorted by name.  Emits a warning on collisions.
    """
    if not directory.exists():
        return []

    seen_names: set[str] = set()
    skills: list[SkillInfo] = []

    def _parse(skill_file: Path) -> SkillInfo | None:
        try:
            content = skill_file.read_text(encoding="utf-8")
        except Exception:
            return None

        name = skill_name_from_path(skill_file)
        if name.startswith("_"):
            return None

        desc, cat = name, "general"
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                for line in content[3:end].strip().split("\n"):
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip()
                    elif line.startswith("category:"):
                        cat = line.split(":", 1)[1].strip()
        return {"name": name, "description": desc, "category": cat, "file": str(skill_file)}

    # 1. Folder-per-skill: skills/<name>/SKILL.md
    for skill_file in sorted(directory.glob("*/SKILL.md")):
        info = _parse(skill_file)
        if info and info["name"] not in seen_names:
            seen_names.add(info["name"])
            skills.append(info)

    # 2. Legacy flat files: skills/<name>.md (backward compatibility)
    for md_file in sorted(directory.glob("*.md")):
        if md_file.name.startswith("_"):
            continue
        info = _parse(md_file)
        if info:
            if info["name"] in seen_names:
                warnings.warn(
                    f"Skill '{info['name']}' in {md_file} conflicts with previously discovered skill; "
                    f"ignoring duplicate.",
                    stacklevel=2,
                )
            elif info["name"] not in seen_names:
                seen_names.add(info["name"])
                skills.append(info)

    return skills
