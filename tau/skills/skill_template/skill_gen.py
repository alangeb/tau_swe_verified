#!/usr/bin/env python3
"""Skill template helper - generate skill templates."""

from typing import Optional


def generate_skill_template(name: str, description: str, category: str) -> str:
    """Generate a skill template."""
    template = f"""---
name: {name}
description: {description} (also load: related_skill1, related_skill2)
category: {category}
---

# {name.title().replace('-', ' ')}

## When
"trigger keyword 1", "trigger keyword 2", "trigger keyword 3"

## Content
[Project-specific knowledge only]

## Related Skills
- `related_skill1` — description
- `related_skill2` — description
"""
    return template


if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "new_skill"
    description = sys.argv[2] if len(sys.argv) > 2 else "Brief description"
    category = sys.argv[3] if len(sys.argv) > 3 else "development"
    template = generate_skill_template(name, description, category)
    print(template)
