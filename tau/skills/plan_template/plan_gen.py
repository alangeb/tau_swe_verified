#!/usr/bin/env python3
"""Plan template helper - generate and manage plan files."""

from datetime import datetime
from typing import Optional


def generate_plan(task: str, phases: list[dict] = None) -> str:
    """Generate a plan file structure."""
    now = datetime.now().strftime("%Y-%m-%d")
    
    plan = f"""# Plan: {task}

Created: {now}

## TASK DOCUMENTATION
{task}

## PLAN
"""
    
    if phases:
        for i, phase in enumerate(phases, 1):
            plan += f"\n### Phase {i}: {phase.get('name', f'Phase {i}')}\n"
            for item in phase.get('items', []):
                plan += f"- [ ] {item}\n"
    else:
        plan += """- [ ] Analyze requirements
- [ ] Design solution
- [ ] Implement
- [ ] Test
- [ ] Review
"""
    
    plan += """
## PYSCAN TREE
# Run pyscan to populate

## REQUIREMENTS
- Purpose:
- Inputs:
- Outputs:
- Side Effects:
- Errors:
- Tests:
- Dependencies:

## DECISIONS
| Context | Options | Chosen | Rationale | Trade-offs | Impact |
|---------|---------|--------|-----------|------------|--------|

## TASKS
| Step | Action | Tool | Expected | Verification |
|------|--------|------|----------|--------------|

## QUESTIONS

## RISKS
| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
"""
    
    return plan


if __name__ == "__main__":
    import sys
    task = sys.argv[1] if len(sys.argv) > 1 else "Untitled Task"
    plan = generate_plan(task)
    print(plan)
