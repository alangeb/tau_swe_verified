"""Ralph Loop command — iterative task execution with explicit confirmation."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from agent_core import TauBot

from agent_console import error, warning
from agent_console_primitives import blank_line, echo, status

# ── Constants ──
name = "ralph"
description = (
    "Iterative task execution with explicit confirmation and acceptance criteria"
)
subcommands = ()
help_text = """Usage: /ralph <task description>

The Ralph Loop will:
  1. Create a task with acceptance criteria
  2. Execute the task iteratively
  3. Verify against acceptance criteria
  4. Repeat until explicitly confirmed complete
"""

RALPH_LOOP_DIR = Path(".ralph_loop")
MAX_ITERATIONS = 20
TASK_FILE = "tasks.json"
TASK_SPEC_DIR = "tasks"
LOG_FILE = "logs/iteration.log"
SUMMARY_FILE = "SUMMARY.md"

_RE_CONTINUE = re.compile(r"<CONTINUE>")
_RE_FINISHED = re.compile(r"<FINISHED>")
MAX_RETRIES = 3
MAX_REPORTS = 5


# ── Exceptions ──
class RalphError(Exception):
    """Base exception for Ralph Loop operations."""


class RalphFileError(RalphError):
    """Raised when file operations fail."""


class RalphValidationError(RalphError):
    """Raised when input validation fails."""


# ── Data models ──
class IterationResult(NamedTuple):
    """Result of a single Ralph Loop iteration."""
    result: str
    is_complete: bool
    error: str | None = None


# ── File helpers ──
def _atomic_write_json(file_path: Path, data: dict) -> None:
    try:
        dir_path = file_path.parent
        dir_path.mkdir(parents=True, exist_ok=True)

        fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, file_path)
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
    except OSError as e:
        raise RalphFileError(f"Failed to write to {file_path}: {e}")


def _read_json(file_path: Path) -> dict:
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        raise RalphFileError(f"File not found: {file_path}")
    except json.JSONDecodeError as e:
        raise RalphFileError(f"Invalid JSON in {file_path}: {e}")
    except OSError as e:
        raise RalphFileError(f"Failed to read {file_path}: {e}")


# ── Ralph Loop init ──
def _init_ralph_loop() -> None:
    try:
        RALPH_LOOP_DIR.mkdir(exist_ok=True)
        (RALPH_LOOP_DIR / TASK_SPEC_DIR).mkdir(exist_ok=True)
        (RALPH_LOOP_DIR / "logs").mkdir(exist_ok=True)

        tasks_file = RALPH_LOOP_DIR / TASK_FILE
        if not tasks_file.exists():
            _atomic_write_json(tasks_file, {"tasks": []})
    except OSError as e:
        raise RalphFileError(f"Failed to initialize Ralph Loop directory: {e}")


# ── Task management ──
def _create_task(description: str) -> str:
    if not description or not description.strip():
        raise RalphValidationError("Task description cannot be empty.")

    task_id = str(uuid.uuid4())[:8]

    task_spec = {
        "id": task_id,
        "description": description,
        "status": "pending",
        "iterations": 0,
        "acceptance_criteria": _generate_acceptance_criteria(description),
        "created_at": datetime.now().isoformat(),
    }

    spec_file = RALPH_LOOP_DIR / TASK_SPEC_DIR / f"{task_id}.json"
    _atomic_write_json(spec_file, task_spec)

    tasks_file = RALPH_LOOP_DIR / TASK_FILE
    tasks_data = _read_json(tasks_file)
    tasks_data["tasks"].append(
        {"id": task_id, "description": description, "status": "pending"}
    )
    _atomic_write_json(tasks_file, tasks_data)

    return task_id


def _generate_acceptance_criteria(description: str) -> list[str]:
    keywords = re.split(r"[,.;:!?]+", description)
    keywords = [k.strip() for k in keywords if len(k.strip()) > 3]

    criteria = [
        "Task objective is achieved",
        "No errors in execution",
        "Output matches expected format",
    ]

    if keywords:
        criteria.append(
            f"Requirements from description are met: {', '.join(keywords[:3])}"
        )

    return criteria


def _check_task_complete(task_id: str) -> tuple[bool, str]:
    spec_file = RALPH_LOOP_DIR / TASK_SPEC_DIR / f"{task_id}.json"
    if not spec_file.exists():
        return False, "Task not found"

    task_spec = _read_json(spec_file)
    if task_spec.get("status") == "complete":
        return True, task_spec.get("confirmation", "Task completed successfully")

    return False, "Task not yet confirmed complete"


def _verify_task_status(task_id: str) -> str:
    spec_file = RALPH_LOOP_DIR / TASK_SPEC_DIR / f"{task_id}.json"
    task_spec = _read_json(spec_file)
    return task_spec.get("status", "pending")


def _finalize_task(task_id: str, incomplete: bool = False) -> None:
    spec_file = RALPH_LOOP_DIR / TASK_SPEC_DIR / f"{task_id}.json"
    task_spec = _read_json(spec_file)

    if incomplete:
        task_spec["status"] = "incomplete"
        task_spec["final_note"] = (
            "Maximum iterations reached without explicit confirmation"
        )
    else:
        task_spec["status"] = "complete"

    _atomic_write_json(spec_file, task_spec)

    tasks_file = RALPH_LOOP_DIR / TASK_FILE
    tasks_data = _read_json(tasks_file)
    for task in tasks_data["tasks"]:
        if task["id"] == task_id:
            task["status"] = "complete" if not incomplete else "incomplete"
            break

    _atomic_write_json(tasks_file, tasks_data)


# ── Iteration ──
def _execute_iteration(
    agent: "TauBot", task_id: str, iteration: int
) -> IterationResult:
    spec_file = RALPH_LOOP_DIR / TASK_SPEC_DIR / f"{task_id}.json"
    task_spec = _read_json(spec_file)

    prompt = f"""
TASK: {task_spec['description']}

ITERATION: {iteration}

PREVIOUS WORK: Review any previous attempts and learn from them.

ACCEPTANCE CRITERIA:
{chr(10).join(["- " + c for c in task_spec['acceptance_criteria']])}

INSTRUCTIONS:
1. Work towards completing the task
2. Verify against acceptance criteria
3. If task is complete, explicitly confirm with: <complete>Yes, the task is complete.</complete>
4. If task is not complete, explain what remains to be done

Focus on making progress. If you've made significant progress, confirm completion.
"""

    try:
        result = agent.invoke_with_tools(prompt)

        echo(f"  ── Iteration {iteration} Output ──")
        echo(result)
        blank_line()

        complete_pattern = re.compile(
            r"<complete>.*?</complete>", re.IGNORECASE | re.DOTALL
        )
        is_complete = bool(complete_pattern.search(result))

        if is_complete:
            status("  ✅ Agent confirmed task completion!")
            task_spec = _read_json(spec_file)
            task_spec["status"] = "complete"
            task_spec["confirmation"] = (
                f"Agent confirmed completion at iteration {iteration}"
            )
            _atomic_write_json(spec_file, task_spec)
        else:
            echo("  ℹ️  No completion tag found in response")

        return IterationResult(result=result, is_complete=is_complete, error=None)

    except Exception as e:
        error_msg = f"Error: {str(e)}"
        error(f"  ✗ {error_msg}")
        return IterationResult(result=error_msg, is_complete=False, error=str(e))


def _log_iteration(task_id: str, iteration: int, result: str) -> None:
    log_file = RALPH_LOOP_DIR / LOG_FILE
    log_entry = f"=== Iteration {iteration} ===\nTask: {task_id}\nResult: {result}\n\n"

    try:
        with open(log_file, "a") as f:
            f.write(log_entry)
    except OSError as e:
        raise RalphFileError(f"Failed to write iteration log: {e}")


# ── Fork helpers ──
def _record_report(reports: list[str], report: str) -> None:
    reports.append(report)
    if len(reports) > MAX_REPORTS:
        del reports[: len(reports) - MAX_REPORTS]


def _spawn_and_check(prompt: str, agent: "TauBot") -> tuple[str | None, str]:
    report = _spawn_fork(prompt, agent)
    if report is None:
        return None, ""
    return report, _check_tag(report)


def _spawn_fork(prompt: str, agent: "TauBot") -> str | None:
    from agent_subagent import invoke_fork_sync

    try:
        return invoke_fork_sync(
            prompt=prompt,
            parent_context=agent.context,
            parent_agent=agent,
            nesting_count=agent.nesting_count,
        )
    except Exception as e:

        error(f"  ✗ Fork failed: {e}")
        return None


def _check_tag(report: str) -> str:
    if _RE_FINISHED.search(report):
        return "FINISHED"
    if _RE_CONTINUE.search(report):
        return "CONTINUE"
    return ""


def _retry_for_tag(agent: "TauBot") -> tuple[str | None, str]:
    for retry in range(1, MAX_RETRIES + 1):
        warning(
            f"  ⚠️  No <CONTINUE>/<FINISHED> tag — retry {retry}/{MAX_RETRIES}"
        )
        retry_prompt = (
            "Your previous response was missing the required "
            "<CONTINUE> or <FINISHED> tag.\n"
            "Please re-read your work and append either:\n"
            "  <CONTINUE> — if more work remains\n"
            "  <FINISHED> — if the task is complete\n"
            "Include your progress summary before the tag."
        )
        report, tag = _spawn_and_check(retry_prompt, agent)
        if report is None:
            return None, ""

        if tag in ("CONTINUE", "FINISHED"):
            return report, tag

        error(f"  ⚠️  Still no tag after {retry} retries")

    return None, ""


def _build_fork_prompt(task: str, iteration: int, previous_reports: list[str]) -> str:
    prompt = f"""
TASK: {task}

ITERATION: {iteration}

PREVIOUS WORK: Review any previous attempts and learn from them.

ACCEPTANCE CRITERIA:
- Task objective is achieved
- No errors in execution
- Output matches expected format

INSTRUCTIONS:
1. Work towards completing the task
2. Verify against acceptance criteria
3. If task is complete, explicitly confirm with: <FINISHED>Yes, the task is complete.</FINISHED>
4. If task is not complete, explain what remains to be done and append: <CONTINUE>

Focus on making progress. If you've made significant progress, confirm completion.
""".strip()
    return prompt


# ── Main entry point ──
def run(agent: "TauBot", args: list[str]) -> None:


    if not args or args[0] in ("help", "-h", "h"):
        error("Task description required.")
        echo("Usage: /ralph <task description>")
        echo("")
        echo("The Ralph Loop will:")
        echo("  1. Create a task with acceptance criteria")
        echo("  2. Execute the task iteratively")
        echo("  3. Verify against acceptance criteria")
        echo("  4. Repeat until explicitly confirmed complete")
        return

    task_description = " ".join(args).strip()

    if not task_description:
        raise RalphValidationError(
            "Task description cannot be empty or whitespace only."
        )

    status("🔄 Starting Ralph Loop...")
    echo(f"Task: {task_description}")
    blank_line()

    _init_ralph_loop()
    task_id = _create_task(task_description)

    status(f"📋 Task created: {task_id}")
    echo("Starting iterative execution...")
    blank_line()

    previous_reports: list[str] = []

    iteration = 0
    while iteration < MAX_ITERATIONS:
        iteration += 1
        status(f"🔄 Iteration {iteration}/{MAX_ITERATIONS}")
        echo(f"  Starting iteration {iteration}...")

        is_complete, confirmation = _check_task_complete(task_id)
        if is_complete:
            status("✅ Task confirmed complete!")
            echo(f"Confirmation: {confirmation}")
            _finalize_task(task_id)
            return

        iteration_result = _execute_iteration(agent, task_id, iteration)

        if iteration_result.is_complete:
            status(f"✅ Task completed at iteration {iteration}!")
            _finalize_task(task_id)
            return

        _log_iteration(task_id, iteration, iteration_result.result)

        task_status = _verify_task_status(task_id)
        echo(f"  Status: {task_status}")
        blank_line()

        fork_prompt = _build_fork_prompt(task_description, iteration, previous_reports)
        report, tag = _spawn_and_check(fork_prompt, agent)
        if report is None:
            return

        if tag == "FINISHED":
            status("✅ Fork reported <FINISHED>")
            echo(f"  Report: {report[:500]}...")
            blank_line()
            _finalize_task(task_id)
            return

        if tag == "CONTINUE":
            clean = _RE_CONTINUE.sub("", report).strip()
            _record_report(previous_reports, clean)
            echo(f"  ℹ️  Progress: {clean[:200]}...")
            blank_line()
            continue

        retry_report, retry_tag = _retry_for_tag(agent)

        if retry_tag == "FINISHED":
            status("✅ Fork reported <FINISHED> (via retry)")
            echo(f"  Report: {retry_report[:500]}...")
            blank_line()
            _finalize_task(task_id)
            return

        if retry_tag == "CONTINUE":
            clean = _RE_CONTINUE.sub("", retry_report).strip()
            _record_report(previous_reports, clean)
            echo(f"  ℹ️  Progress (via retry): {clean[:200]}...")
            blank_line()
            continue

        warning("  ⚠️  Fork failed to signal after retries")
        _record_report(previous_reports, report)

    warning(f"⚠️  Max iterations ({MAX_ITERATIONS}) reached")
    status("📊 Ralph Loop Summary")
    blank_line()
    for i, r in enumerate(previous_reports, 1):
        echo(f"  Iteration {i}: {r[:200]}...")
    blank_line()

    _finalize_task(task_id, incomplete=True)
