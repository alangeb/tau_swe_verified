---
name: _taudoc
description: Maintain TauBot documentation structure — designs/, TAU.md, AGENT.md (also load: tau_audit, skill_template, documentation)
category: maintenance
---

# Tau Documentation Layout

## When
"update docs", "documentation structure", "designs folder", "TAU.md", "AGENT.md", "documentation maintenance"

## Structure (all paths relative to `src/`)

```
src/
├── AGENT.md              ← System prompt (always loaded). Points to TAU.md.
├── TAU.md                ← Developer index. Points to designs/ files.
├── README.md             ← Minimal pointer to TAU.md + designs/
└── designs/
    ├── INDEX.md          ← Navigation index for designs/
    ├── ARCHITECTURE.md   ← Request flow, modules, pipelines, patterns
    ├── DECISIONS.md      ← Design decisions (166+ across 20 categories)
    ├── CONTEXT.md        ← Context management patterns
    ├── COMMANDS.md       ← Command implementation guide
    ├── SKILLS.md         ← Skill implementation guide
    ├── TESTING.md        ← Testing guide
    └── TOOLS.md          ← Tool implementation guide
```

## Rules

1. **AGENT.md** is the system prompt. NEVER modify except to update the TAU.md reference line.
2. **TAU.md** is the developer index. It points to designs/ for all design documents.
3. **designs/** contains ALL design documents. No design docs outside this folder.
4. **README.md** is a minimal pointer file. Do NOT add content here — put it in TAU.md or designs/.
5. **commands/_taudoc.md** is the ONLY command for documentation maintenance.
6. **skills/_taudoc/** is the ONLY skill for documentation maintenance.
7. All paths in documentation are RELATIVE to `src/` folder.
8. **NEVER** create new documentation files outside `designs/` without explicit approval.
9. **NEVER** remove old entries from `designs/DECISIONS.md` — manual only.
10. **ALWAYS** verify cross-links resolve before committing.

## Workflow

When updating documentation:

1. Read `TAU.md` and all relevant `designs/*.md` files
2. Run `pyscan` and `pyanalyze` on `src/` to understand current code
3. Compare actual code against documentation
4. Update docs to match code (not code to match docs)
5. Remove overlaps between files
6. Verify all cross-links resolve
7. Commit with descriptive message

## What Goes Where

| Content | Location |
|---------|-----------|
| System prompt rules | `AGENT.md` |
| Developer quick reference | `TAU.md` |
| Architecture, flow, modules | `designs/ARCHITECTURE.md` |
| Design decisions | `designs/DECISIONS.md` |
| Context patterns | `designs/CONTEXT.md` |
| Tool implementation | `designs/TOOLS.md` |
| Command implementation | `designs/COMMANDS.md` |
| Skill implementation | `designs/SKILLS.md` |
| Testing guide | `designs/TESTING.md` |

## Related Skills
- `tau_audit` — analyze agent logs for behavior patterns
- `skill_template` — skill creation format
- `command_template` — command creation format
