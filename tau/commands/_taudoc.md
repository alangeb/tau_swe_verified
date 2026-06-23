---
description: Read, review, compare and cleanly update Tau documentation (uses _taudoc skill)
---

# /_taudoc — Documentation Maintenance

Load skill `_taudoc` first. Then DO THIS NOW:

## Step 1: Read Current State

1. Read `TAU.md` (developer index)
2. Read all `designs/*.md` files
3. Run `pyscan` on `src/` and `pyanalyze` on `src/`

## Step 2: Compare Code vs Docs

Map actual code against documentation:
- Module dependencies → `designs/ARCHITECTURE.md`
- Design decisions → `designs/DECISIONS.md`
- Context patterns → `designs/CONTEXT.md`
- Tool contracts → `designs/TOOLS.md`
- Command dispatch → `designs/COMMANDS.md`
- Skill contracts → `designs/SKILLS.md`
- Testing → `designs/TESTING.md`

## Step 3: Update Docs

- Add new decisions to `designs/DECISIONS.md` (next number in category)
- Update `designs/ARCHITECTURE.md` module references
- Remove overlaps between files
- Update `TAU.md` quick reference table
- Verify all cross-links resolve

## Step 4: Verify

```bash
# Verify no broken references
grep -rn "DESIGNDECISIONS\|DESIGN_SYNTHETIC" src/ --include="*.py" --include="*.md" | grep -v "was DESIGNDECISIONS"

# Run tests
cd src && pytest tests/test_context_synthetic_bridge.py tests/test_recover_invalid_end_of_turn.py
```

## Hard Rules

- **NEVER** modify `AGENT.md` except the TAU.md reference line
- **NEVER** create docs outside `designs/`
- **NEVER** remove old entries from `designs/DECISIONS.md`
- **ALWAYS** use paths relative to `src/`
- **ALWAYS** verify cross-links before committing
