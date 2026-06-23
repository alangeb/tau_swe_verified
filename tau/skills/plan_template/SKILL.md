---
name: plan_template
description: Plan file structure, workflow, state management (also load: code-review-workflow, idea, context_management)
category: planning
---

# Plan File Structure

## When
"create plan", "plan file", "task checklist", "project plan", "step-by-step plan"

## 8 Sections (in order)
1. **TASK DOCUMENTATION** — Original request + recent updates
2. **PLAN** — Phases with checklists `[ ]`, `[>]`, `[x]`, `[?]`
3. **PYSCAN TREE** — Code structure with line references
4. **REQUIREMENTS** — Low/Medium/High (Purpose, Inputs, Outputs, Side Effects, Errors, Tests, Dependencies)
5. **DECISIONS** — Context, Options, Chosen, Rationale, Trade-offs, Impact
6. **TASKS** — Step-by-step (Action, Tool, Expected, Verification)
7. **QUESTIONS** — Open items
8. **RISKS** — Risk, Probability, Impact, Mitigation

## State Markers
- `[ ]` Not started
- `[>]` In progress (max 1 at a time)
- `[x]` Completed
- `[?]` Blocked/Needs clarification

## Workflow
### Create
1. Copy structure, fill TASK DOCUMENTATION
2. Define phases with `[ ]` items
3. Add upfront decisions/questions

### Update
1. Read current content
2. Add new phases at top
3. Mark completed `[x]`
4. Add "Recent Updates"
5. Preserve all history

### Execute
1. Review → 2. Select `[ ]` → 3. Mark `[>]` → 4. Execute → 5. Verify → 6. Mark `[x]`, next `[>]` → 7. Document

## Rules
- Max one `[>]` at a time across entire file
- Update each session (start or end)
- Every `[x]` needs "Recent Updates"
- Preserve all history, never delete old content

## Quick Queries
```bash
grep -n "\[>\]" PLAN.md   # In-progress
grep -n "\[ \]" PLAN.md   # Pending
grep -n "\[x\]" PLAN.md   # Completed
```

## Related Skills
- `code-review-workflow` — plan code review phases
- `bug_investigation` — plan investigation steps
- `context_management` — delegate planned tasks
