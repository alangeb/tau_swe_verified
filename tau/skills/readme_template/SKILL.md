---
name: readme_template
description: Template and structure for README.md files (also load: project-onboard, skill_template, command_template, documentation)
category: documentation
---

# README.md Structure

## When
"write readme", "readme structure", "document project", "update readme", "readme template"

## Sections (in order)
1. **Table of Contents** — Links to all major sections
2. **Overview** — What it is, key features, goals
3. **Core Concepts** — Architecture principles, design decisions
4. **Command System** — Built-in commands, custom commands, placeholders
5. **Tool Ecosystem** — File ops, process mgmt, AI/Research, self-mgmt, subagents, schema gen
6. **Testing** — Suite overview, coverage, running instructions
7. **Development** — Code style, adding tools/commands/skills
8. **Architecture** — Component descriptions, design decisions
9. **Design Decisions** — Specific choices, trade-offs
10. **Advanced Topics** — Special features, edge cases, performance

## Rules
- Status quo only — no history, migration guides, dates, deprecated features
- Use `pyscan` + `pyanalyze` for accurate code info
- Update TOC when sections change
- Consistent formatting, clear examples, actionable content

## Related Skills
- `project-onboard` — gather project info for README
- `skill_template` — document skills in README
- `command_template` — document commands in README
