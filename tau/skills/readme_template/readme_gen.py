#!/usr/bin/env python3
"""README template helper - generate README structure."""

from typing import Optional


def generate_readme_template(project_name: str, description: str = "") -> str:
    """Generate a README template."""
    template = f"""# {project_name}

{description}

## Table of Contents
- [Overview](#overview)
- [Core Concepts](#core-concepts)
- [Command System](#command-system)
- [Tool Ecosystem](#tool-ecosystem)
- [Testing](#testing)
- [Development](#development)
- [Architecture](#architecture)
- [Design Decisions](#design-decisions)
- [Advanced Topics](#advanced-topics)

## Overview
[What it is, key features, goals]

## Core Concepts
[Architecture principles, design decisions]

## Command System
[Built-in commands, custom commands, placeholders]

## Tool Ecosystem
[File ops, process mgmt, AI/Research, self-mgmt, subagents, schema gen]

## Testing
[Suite overview, coverage, running instructions]

## Development
[Code style, adding tools/commands/skills]

## Architecture
[Component descriptions, design decisions]

## Design Decisions
[Specific choices, trade-offs]

## Advanced Topics
[Special features, edge cases, performance]
"""
    return template


if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "Project"
    desc = sys.argv[2] if len(sys.argv) > 2 else ""
    template = generate_readme_template(name, desc)
    print(template)
