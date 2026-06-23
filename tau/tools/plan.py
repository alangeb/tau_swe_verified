"""Plan tool — hierarchical task plan management."""

from __future__ import annotations

from tools import ToolMetadata

import json
import os
from dataclasses import dataclass
from datetime import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

__all__ = ["name", "description", "Args", "run"]

# ── Tool metadata ────────────────────────────────────────────────

metadata = ToolMetadata(
    name="plan",
    description="""Manage hierarchical task plans. Actions: create, add, complete, block, unblock, status, next, progress, update, delete, clear.

Task IDs use dotted notation for hierarchy: "1", "1.1", "1.2.3".

ADD usage — task_id is the ID FOR THE NEW TASK (not a parent):
  add(description="...")                    → auto-ID: "1", "2", "3"...
  add(task_id="1", description="...")       → explicit root ID "1"
  add(task_id="1.1", description="...")    → explicit "1.1", auto-creates parent "1" if missing
  add(task_id="2.3.1", description="...")  → explicit "2.3.1", auto-creates "2" + "2.3" if missing

ALL OTHER actions — task_id identifies the TARGET task:
  complete(task_id="1.1")                  → marks "1.1" complete
  block(task_id="1.1", blocker_reason="...")
  status()                                  → shows full plan tree
  next()                                   → returns next actionable task ID
  progress()                                 → completion percentage
  update(task_id="1.1", description="...")  → updates existing task
  delete(task_id="1.1")                    → removes task + subtasks
  clear()                                  → removes all tasks
""",
    max_size=32768,
)

# ── Args schema ──────────────────────────────────────────────────

@dataclass
class Args:
    action: str
    task_id: str | None = None
    description: str | None = None
    priority: str | None = None
    blocker_reason: str | None = None
    notes: str | None = None



# ── Plan file helpers ────────────────────────────────────────────

def _get_plan_file_path() -> Path:
    from agent_session import LOG_DIR, SESSION_PREFIX
    from agent_console import warning

    if SESSION_PREFIX:
        return LOG_DIR / f"{SESSION_PREFIX}.plan"

    warning(
        "SESSION_PREFIX not set — plan file will use fallback naming. "
        "This indicates the agent was not initialized properly."
    )
    return LOG_DIR / f"{os.getppid()}_{dt.now().strftime('%Y%m%d%H%M%S')}_0.plan"


def _empty_plan() -> dict:
    return {"created_at": dt.now().isoformat(), "updated_at": dt.now().isoformat(), "tasks": []}


def _load_plan(plan_file: Path) -> dict:
    if not plan_file.exists():
        return _empty_plan()
    try:
        with open(plan_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return _empty_plan()


def _save_plan(plan_file: Path, plan: dict) -> None:
    plan["updated_at"] = dt.now().isoformat()
    try:
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, default=str)
    except IOError:
        pass


def _load_plan_if_has_tasks(plan_file: Path) -> dict | None:
    plan = _load_plan(plan_file)
    if not plan["tasks"]:
        return None
    return plan


# ── Task ID helpers ──────────────────────────────────────────────

def _generate_task_id(parent_id: str | None, existing_ids: list[str]) -> str:
    if parent_id is None:
        root_ids = [int(tid) for tid in existing_ids if "." not in tid]
        return str(max(root_ids, default=0) + 1)

    prefix = f"{parent_id}."
    sub_ids = [int(tid[len(prefix):]) for tid in existing_ids if tid.startswith(prefix)]
    return f"{parent_id}.{max(sub_ids, default=0) + 1}"


def _get_all_task_ids(tasks: list[dict]) -> list[str]:
    ids = []
    for task in tasks:
        task_id = task.get("id", "")
        if task_id:
            ids.append(task_id)
        ids.extend(_get_all_task_ids(task.get("subtasks", [])))
    return ids


# ── Task traversal ───────────────────────────────────────────────

def _find_task_by_id(tasks: list[dict], task_id: str) -> tuple[dict | None, list[dict] | None]:
    parts = task_id.split(".")

    if len(parts) == 1:
        for task in tasks:
            if task.get("id") == task_id:
                return task, tasks
        return None, None

    current_tasks = tasks
    current_id = ""

    for i, part in enumerate(parts):
        current_id = part if i == 0 else f"{current_id}.{part}"
        found = None
        for task in current_tasks:
            if task.get("id") == current_id:
                found = task
                break

        if found is None:
            return None, None

        if i < len(parts) - 1:
            current_tasks = found.get("subtasks", [])
        else:
            return found, current_tasks

    return None, None


def _delete_task_by_id(tasks: list[dict], task_id: str) -> int:
    parts = task_id.split(".")

    if len(parts) == 1:
        for i, task in enumerate(tasks):
            if task.get("id") == task_id:
                count = 1 + len(_get_all_task_ids(task.get("subtasks", [])))
                tasks.pop(i)
                return count
        return 0

    current_tasks = tasks
    current_id = ""

    for i, part in enumerate(parts):
        current_id = part if i == 0 else f"{current_id}.{part}"
        found_idx = None
        found_task = None

        for idx, task in enumerate(current_tasks):
            if task.get("id") == current_id:
                found_idx = idx
                found_task = task
                break

        if found_task is None:
            return 0

        if i < len(parts) - 1:
            current_tasks = found_task.get("subtasks", [])
        else:
            count = 1 + len(_get_all_task_ids(found_task.get("subtasks", [])))
            current_tasks.pop(found_idx)
            return count

    return 0


# ── Auto-create parent chain ─────────────────────────────────────

def _ensure_parent_chain(tasks: list[dict], task_id: str) -> list[dict]:
    """Ensure the full parent chain exists for a dotted task_id.

    For task_id "1.2.3", ensures tasks "1" and "1.2" exist.
    Auto-created parents get a generic description and are skipped by 'next'.

    Returns the container list (parent's subtasks) where the new task should be appended.
    """
    parts = task_id.split(".")
    if len(parts) == 1:
        # Root task — container is the top-level tasks list
        return tasks

    # Walk the dotted ID, creating parents as needed.
    # For "1.2.3": parents are "1", "1.2"; container is "1.2"'s subtasks.
    current_list: list[dict] = tasks
    parent_id = ""

    for i in range(len(parts) - 1):
        part = parts[i]
        parent_id = part if i == 0 else f"{parent_id}.{part}"

        # Find or create this parent
        parent = None
        for task in current_list:
            if task.get("id") == parent_id:
                parent = task
                break

        if parent is None:
            # Auto-create parent
            parent = {
                "id": parent_id,
                "description": f"Phase {parent_id}",
                "status": "pending",
                "blocker_reason": None,
                "priority": "medium",
                "notes": [],
                "subtasks": [],
                "_auto": True,  # marker: skip in 'next', show differently in status
            }
            current_list.append(parent)

        current_list = parent.get("subtasks", [])
        if not current_list:
            parent["subtasks"] = []
            current_list = parent["subtasks"]

    return current_list


# ── Action queries ───────────────────────────────────────────────

def _find_next_actionable_task(tasks: list[dict], parent_blocked: bool = False) -> str | None:
    for task in tasks:
        # Skip auto-created placeholder tasks — but still check their subtasks
        if task.get("_auto"):
            sub_next = _find_next_actionable_task(task.get("subtasks", []), parent_blocked=False)
            if sub_next:
                return sub_next
            continue

        status = task.get("status", "pending")
        is_blocked = task.get("blocker_reason") is not None

        if status == "completed":
            continue

        if is_blocked or parent_blocked:
            sub_next = _find_next_actionable_task(task.get("subtasks", []), parent_blocked=True)
            if sub_next:
                return sub_next
            continue

        return task.get("id", "")

    return None


def _count_tasks(tasks: list[dict]) -> dict[str, int]:
    counts = {"pending": 0, "in_progress": 0, "completed": 0, "blocked": 0, "total": 0}

    for task in tasks:
        status = task.get("status", "pending")
        counts[status] = counts.get(status, 0) + 1
        counts["total"] += 1

        sub_counts = _count_tasks(task.get("subtasks", []))
        for key in counts:
            if key != "total":
                counts[key] += sub_counts.get(key, 0)
        counts["total"] += sub_counts["total"]

    return counts


# ── Formatting ───────────────────────────────────────────────────

_STATUS_ICONS = {"pending": "○", "in_progress": "◐", "completed": "●", "blocked": "⊗"}
_PRIORITY_ICONS = {"high": "🔺", "medium": "■", "low": "▪"}


def _format_task_tree(tasks: list[dict], indent: int = 0) -> str:
    lines = []
    prefix = "  " * indent

    for task in tasks:
        # Skip auto-created placeholders in status output (they are noise)
        if task.get("_auto"):
            # Show them but dimmed
            task_id = task.get("id", "")
            subtasks = task.get("subtasks", [])
            if subtasks:
                lines.append(f"{prefix}  [{task_id}] (auto)")
                lines.append(_format_task_tree(subtasks, indent + 2))
            continue

        task_id = task.get("id", "")
        description = task.get("description", "No description")
        status = task.get("status", "pending")
        priority = task.get("priority")
        blocker = task.get("blocker_reason")

        status_icon = _STATUS_ICONS.get(status, "?")
        priority_icon = _PRIORITY_ICONS.get(priority, "")
        blocker_text = f" [BLOCKED: {blocker}]" if blocker else ""

        lines.append(f"{prefix}{status_icon} [{task_id}] {description} {priority_icon}{blocker_text}")

        subtasks = task.get("subtasks", [])
        if subtasks:
            lines.append(_format_task_tree(subtasks, indent + 1))

    return "\n".join(lines)


def _format_progress(counts: dict[str, int]) -> str:
    total = counts["total"]
    if total == 0:
        return "No tasks in plan. Use 'add' to add tasks."

    completed = counts["completed"]
    percentage = (completed / total) * 100

    filled = int(40 * percentage / 100)
    bar = "█" * filled + "░" * (40 - filled)

    lines = [
        f"Progress: {percentage:.1f}% ({completed} of {total} tasks completed)",
        "",
        f"┌{'─' * 40}┐",
        f"│{bar}│ {percentage:.1f}%",
        f"└{'─' * 40}┘",
        "",
        "Breakdown:",
        f"  ● Completed: {counts['completed']}",
        f"  ○ Pending:   {counts['pending']}",
        f"  ◐ In Progress: {counts['in_progress']}",
        f"  ⊗ Blocked:   {counts['blocked']}",
    ]

    return "\n".join(lines)


# ── Error message helpers ────────────────────────────────────────

def _existing_tasks_hint(plan_file: Path) -> str:
    """Append a hint showing existing task IDs for context."""
    plan = _load_plan(plan_file)
    ids = _get_all_task_ids(plan["tasks"])
    if ids:
        return f" Existing tasks: {', '.join(ids[:10])}{'...' if len(ids) > 10 else ''}. Use 'status' for full plan."
    return " Plan is empty. Use 'add' to add tasks."


# ── Action handlers ──────────────────────────────────────────────

def _handle_create(args: Args, plan_file: Path) -> str:
    plan = _load_plan(plan_file)
    if plan["tasks"]:
        return f"Plan already exists with {len(plan['tasks'])} root task(s). Use 'add' to add tasks or 'clear' to start fresh."

    plan["created_at"] = dt.now().isoformat()
    plan["updated_at"] = dt.now().isoformat()
    _save_plan(plan_file, plan)
    return "Plan created. Use 'add' to add tasks."


def _handle_add(args: Args, plan_file: Path) -> str:
    if not args.description:
        return "Error: 'description' is required for 'add' action."

    plan = _load_plan(plan_file)
    all_ids = _get_all_task_ids(plan["tasks"])

    if args.task_id is None:
        # No explicit ID → auto-generate next root ID
        task_id = _generate_task_id(None, all_ids)
    else:
        # Explicit ID provided — use it directly
        task_id = args.task_id

        # Check for collision
        if task_id in all_ids:
            existing, _ = _find_task_by_id(plan["tasks"], task_id)
            if existing:
                return (
                    f"Task '{task_id}' already exists: {existing.get('description', 'N/A')}. "
                    f"Use 'update' to modify or choose a different ID."
                )

    new_task = {
        "id": task_id,
        "description": args.description,
        "status": "pending",
        "blocker_reason": None,
        "priority": args.priority or "medium",
        "notes": [],
        "subtasks": [],
    }

    if "." not in task_id:
        # Root task — append to top-level
        plan["tasks"].append(new_task)
    else:
        # Hierarchical task — ensure parent chain exists, then append
        container = _ensure_parent_chain(plan["tasks"], task_id)
        container.append(new_task)

    _save_plan(plan_file, plan)
    return f"Task '{task_id}' added: {args.description}"


def _handle_complete(args: Args, plan_file: Path) -> str:
    if not args.task_id:
        return "Error: 'task_id' is required for 'complete' action."

    plan = _load_plan(plan_file)
    task, _ = _find_task_by_id(plan["tasks"], args.task_id)

    if task is None:
        return f"Error: Task '{args.task_id}' not found.{_existing_tasks_hint(plan_file)}"

    if task.get("blocker_reason"):
        return f"Error: Task '{args.task_id}' is blocked. Unblock it first."

    task["status"] = "completed"
    if args.notes:
        task["notes"].append({"timestamp": dt.now().isoformat(), "note": args.notes})

    _save_plan(plan_file, plan)
    return f"Task '{args.task_id}' marked complete."


def _handle_block(args: Args, plan_file: Path) -> str:
    if not args.task_id:
        return "Error: 'task_id' is required for 'block' action."
    if not args.blocker_reason:
        return "Error: 'blocker_reason' is required for 'block' action."

    plan = _load_plan(plan_file)
    task, _ = _find_task_by_id(plan["tasks"], args.task_id)

    if task is None:
        return f"Error: Task '{args.task_id}' not found.{_existing_tasks_hint(plan_file)}"

    task["status"] = "blocked"
    task["blocker_reason"] = args.blocker_reason

    _save_plan(plan_file, plan)
    return f"Task '{args.task_id}' blocked: {args.blocker_reason}"


def _handle_unblock(args: Args, plan_file: Path) -> str:
    if not args.task_id:
        return "Error: 'task_id' is required for 'unblock' action."

    plan = _load_plan(plan_file)
    task, _ = _find_task_by_id(plan["tasks"], args.task_id)

    if task is None:
        return f"Error: Task '{args.task_id}' not found.{_existing_tasks_hint(plan_file)}"

    task["status"] = "pending"
    task["blocker_reason"] = None

    _save_plan(plan_file, plan)
    return f"Task '{args.task_id}' unblocked."


def _handle_status(args: Args, plan_file: Path) -> str:
    plan = _load_plan_if_has_tasks(plan_file)
    if plan is None:
        return "No tasks in plan. Use 'add' to add tasks."

    counts = _count_tasks(plan["tasks"])
    tree = _format_task_tree(plan["tasks"])

    summary = f"Plan Summary: {counts['total']} tasks - {counts['pending']} pending, {counts['in_progress']} in_progress, {counts['completed']} completed, {counts['blocked']} blocked\n\n"
    return summary + tree


def _handle_next(args: Args, plan_file: Path) -> str:
    plan = _load_plan_if_has_tasks(plan_file)
    if plan is None:
        return "No tasks in plan. Use 'add' to add tasks."

    next_id = _find_next_actionable_task(plan["tasks"])

    if next_id is None:
        return "No actionable tasks. All tasks are completed or blocked."

    task, _ = _find_task_by_id(plan["tasks"], next_id)
    desc = task.get("description", "No description")
    priority = task.get("priority", "medium")

    return f"Next task: [{next_id}] {desc} (priority: {priority})"


def _handle_progress(args: Args, plan_file: Path) -> str:
    plan = _load_plan_if_has_tasks(plan_file)
    if plan is None:
        return "No tasks in plan. Use 'add' to add tasks."

    counts = _count_tasks(plan["tasks"])
    return _format_progress(counts)


def _handle_update(args: Args, plan_file: Path) -> str:
    if not args.task_id:
        return "Error: 'task_id' is required for 'update' action."

    plan = _load_plan(plan_file)
    task, _ = _find_task_by_id(plan["tasks"], args.task_id)

    if task is None:
        return f"Error: Task '{args.task_id}' not found.{_existing_tasks_hint(plan_file)}"

    updated = []
    if args.description:
        task["description"] = args.description
        updated.append("description")
    if args.priority:
        task["priority"] = args.priority
        updated.append("priority")
    if args.blocker_reason is not None:
        task["blocker_reason"] = args.blocker_reason
        if args.blocker_reason:
            task["status"] = "blocked"
            updated.append("blocker")
        else:
            if task["status"] == "blocked":
                task["status"] = "pending"
            updated.append("blocker cleared")

    _save_plan(plan_file, plan)

    if updated:
        return f"Task '{args.task_id}' updated: {', '.join(updated)}"
    return f"Task '{args.task_id}' updated (no changes)"


def _handle_delete(args: Args, plan_file: Path) -> str:
    if not args.task_id:
        return "Error: 'task_id' is required for 'delete' action."

    plan = _load_plan(plan_file)
    deleted_count = _delete_task_by_id(plan["tasks"], args.task_id)

    if deleted_count == 0:
        return f"Error: Task '{args.task_id}' not found.{_existing_tasks_hint(plan_file)}"

    _save_plan(plan_file, plan)
    return f"Task '{args.task_id}' and {deleted_count - 1} subtask(s) deleted."


def _handle_clear(args: Args, plan_file: Path) -> str:
    plan = _load_plan(plan_file)
    task_count = len(_get_all_task_ids(plan["tasks"]))

    plan["tasks"] = []
    plan["created_at"] = dt.now().isoformat()
    plan["updated_at"] = dt.now().isoformat()
    _save_plan(plan_file, plan)

    return f"Plan cleared. {task_count} tasks removed."


# ── Execution ────────────────────────────────────────────────────

ACTION_HANDLERS = {
    "create": _handle_create,
    "add": _handle_add,
    "complete": _handle_complete,
    "block": _handle_block,
    "unblock": _handle_unblock,
    "status": _handle_status,
    "next": _handle_next,
    "progress": _handle_progress,
    "update": _handle_update,
    "delete": _handle_delete,
    "clear": _handle_clear,
}


def run(
    action: str,
    task_id: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    blocker_reason: str | None = None,
    notes: str | None = None,
    agent: TauBot | None = None,
    tool_call_id: str | None = None,
) -> str:
    plan_file = _get_plan_file_path()

    if action not in ACTION_HANDLERS:
        return f"Error: Unknown action '{action}'. Valid actions: {', '.join(ACTION_HANDLERS.keys())}"

    handler = ACTION_HANDLERS[action]
    return handler(Args(
        action=action,
        task_id=task_id,
        description=description,
        priority=priority,
        blocker_reason=blocker_reason,
        notes=notes,
    ), plan_file)
