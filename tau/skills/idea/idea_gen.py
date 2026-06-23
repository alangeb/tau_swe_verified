#!/usr/bin/env python3
"""Idea capture helper - generate idea templates."""

from datetime import datetime
from typing import Optional


def generate_idea_template(title: str, problem: str = "", target: str = "", change: str = "") -> str:
    """Generate an idea capture template."""
    now = datetime.now().strftime("%Y-%m-%d")
    idea_id = f"{title.lower().replace(' ', '-')}-{datetime.now().strftime('%Y%m%d')}"
    
    template = f"""---
id: "{idea_id}"
title: "{title}"
status: "new"
created: "{now}"
---

# Idea: {title}

## What
{problem or "[Problem or opportunity]"}

## Target
{target or "[Component/file/system affected]"}

## Change
{change or "[Specific scope]"}

## Success Criteria
[Definition of done]

## Testing
[Verification steps]
"""
    return template


if __name__ == "__main__":
    import sys
    title = sys.argv[1] if len(sys.argv) > 1 else "Untitled Idea"
    template = generate_idea_template(title)
    print(template)
