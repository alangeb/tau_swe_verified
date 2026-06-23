#!/usr/bin/env python3
"""Automated skill validation.

Validates:
  1. Frontmatter integrity (name, description present)
  2. Cross-reference integrity (also load: references exist)
  3. Content thresholds (minimum line counts)
  4. Orphan detection (skills with no incoming references)

Exit codes:
    0 — All checks passed
    1 — Validation failures found
"""

import re
import sys
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent / "skills"

MIN_RUNTIME_LINES = 15  # Runtime skills must have substantive content
WARNING_THRESHOLD = 25  # Skills below this get a warning (not error)


def parse_frontmatter(text: str) -> dict[str, str]:
    """Extract frontmatter between --- markers."""
    # Match frontmatter: starts with ---, ends with ---
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not m:
        return {}
    result: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip().lower()] = val.strip()
    return result


def extract_also_loads(text: str) -> list[str]:
    """Extract skill names from '(also load: ...)' patterns in description."""
    # Match '(also load: skill1, skill2, ...)'
    matches = re.findall(r"\(also load:\s*([^)]+)\)", text)
    skills: list[str] = []
    for match in matches:
        # Split on comma or ' and '
        for part in re.split(r"[\s,]+", match):
            part = part.strip().rstrip(",")
            if part and " " not in part:  # Only single-word skill names
                skills.append(part)
    return skills


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    # ── Discover runtime skills (delegated to shared utility) ──
    from lib.skill_discovery import discover_skills

    skill_infos = discover_skills(SKILLS_DIR)
    runtime_skills: set[str] = set()
    skill_path_map: dict[str, Path] = {}
    for info in skill_infos:
        fpath = Path(info["file"])
        stem = fpath.parent.name if fpath.name == "SKILL.md" else fpath.stem
        if stem not in runtime_skills:
            runtime_skills.add(stem)
            skill_path_map[stem] = fpath

    # ── Validate runtime skills ───────────────────────────────────────────
    referenced_skills: set[str] = set()  # Track which skills are referenced
    for stem in sorted(runtime_skills):
        fpath = skill_path_map[stem]
        content = fpath.read_text()
        lines = content.splitlines()
        fm = parse_frontmatter(content)

        # Frontmatter checks
        if "name" not in fm:
            errors.append(f"{stem}: missing 'name' in frontmatter")
        if "description" not in fm:
            errors.append(f"{stem}: missing 'description' in frontmatter")

        # Content threshold — warning for borderline, error for too short
        if len(lines) < MIN_RUNTIME_LINES:
            errors.append(f"{stem}: only {len(lines)} lines (min {MIN_RUNTIME_LINES})")
        elif len(lines) < WARNING_THRESHOLD:
            warnings.append(
                f"{stem}: only {len(lines)} lines (below warning threshold "
                f"{WARNING_THRESHOLD}). Consider expanding or consolidating."
            )

        # Cross-reference integrity
        for ref in extract_also_loads(content):
            if ref not in runtime_skills:
                errors.append(f"{stem}: references non-existent skill '{ref}'")
            else:
                referenced_skills.add(ref)

    # ── Orphan detection (skills not referenced by any other skill) ────────
    # _taudoc and _tauskillmaintenance are infrastructure skills — expected orphans
    _INFRA_SKILLS = {"_taudoc", "_tauskillmaintenance"}
    for stem in sorted(runtime_skills):
        if stem in _INFRA_SKILLS:
            continue
        if stem not in referenced_skills:
            warnings.append(
                f"{stem}: not referenced by any other skill (orphan). "
                f"Is it still needed?"
            )

    # ── Report ─────────────────────────────────────────────────────────────
    if warnings:
        print(f"skills/ validation warnings ({len(warnings)}):")
        for w in warnings:
            print(f"  ⚠ {w}")

    if errors:
        print(f"skills/ validation failed ({len(errors)} issues):")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    print(f"skills/ validation passed — {len(runtime_skills)} skills")
    return 0


if __name__ == "__main__":
    sys.exit(main())
