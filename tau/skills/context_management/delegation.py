#!/usr/bin/env python3
"""delegation.py — Context management delegation patterns."""

# Fork vs Subagent decision matrix
DECISION_MATRIX = {
    "needs_context": "fork",
    "needs_isolation": "subagent",
    "fire_and_forget": "background",
    "parallel_independent": "background",
    "parallel_dependent": "fork",
}

def choose_delegation(needs_context=False, needs_isolation=False, async_ok=False):
    """Choose best delegation method."""
    if async_ok:
        return "background"
    if needs_isolation:
        return "subagent"
    if needs_context:
        return "fork"
    return "subagent"  # default to cheapest

# Cost comparison (relative)
COST = {
    "background": 1,    # separate process, minimal context
    "subagent": 5,      # blank slate, task-only context
    "fork": 100,         # full context clone
}
