#!/usr/bin/env python3
"""
Fix workflow for SWE-bench Verified adapter.

Handles prompt building, issue setup, and Tau execution inside Docker.
Adapted from ../swe/docker_fix.py — prompts are identical (generic).
"""
import io
import logging
import tarfile
from typing import Any

from config import TAU_DIR, TESTBED_PATH, BASE_DIR
from swe_docker import _write_tar_to_container

logger = logging.getLogger(__name__)

# Path to the ISSUE.md template on the host
ISSUE_MD_TEMPLATE = BASE_DIR / "ISSUE.md"


def build_docker_prompts(instance: dict[str, Any], repo_name: str) -> list[str]:
    """Build the single-shot prompt for Docker container.

    Single prompt with 4 phases: LOCALIZE → ROOT CAUSE → PLAN → IMPLEMENT/REVIEW.
    Framework handles patch generation — Tau only edits source files.

    Args:
        instance: SWE-bench Verified instance dict.
        repo_name: Repository name for the CODEBASE tag.

    Returns:
        List containing prompt strings (one per phase).

    Raises:
        ValueError: If instance is empty.
    """
    if not instance or not isinstance(instance, dict):
        raise ValueError("instance must be a non-empty dict")
    if not repo_name:
        repo_name = "unknown"

    codebase = repo_name
    working_dir = "/testbed"
    issue_file = "/testbed/ISSUE.txt"

    # Each element becomes a SEPARATE positional argument to tau.py.
    # Tau executes them in sequence — this is how multi-phase workflows work.
    prompt = [
f"""
/delegate
<CODEBASE>{codebase}</CODEBASE><WORKINGDIRECTORY>{working_dir}</WORKINGDIRECTORY><ISSUE>{issue_file}

You are tasked with fixing an issue in a codebase. Read ISSUE from file — this describes the problem you must solve.

## WORKFLOW

Execute these 5 phases in order. Use `fork` to delegate phases 1-4. **After phase 5, repeat the entire loop** (phases 1→5) until you are fully satisfied with the fix. Do not stop early.

### PHASE 1: LOCALIZE (use `fork`)
**Goal**: Find the exact files and functions responsible for the issue.
- Load relevant skills via `skill` tool
- Run `pyscan` to understand project structure
- Run `pyanalyze` to find unused code
- Use `grep`, `glob`, and `ast-grep` skill to search for relevant code
- Identify specific files, functions, classes, and code paths related to ISSUE
- **Do NOT fix anything** — only locate and summarize

### PHASE 2: ROOT CAUSE (use `fork`)
**Goal**: Distinguish symptoms from the underlying defect.
- Read ISSUE carefully. What is the actual bug, not just the symptom?
- Trace the code path that causes the issue. Where does it go wrong?
- Clarify and expand ISSUE: extend symptoms to root cause, edge cases to generic solutions, partial coverage to full coverage (read/write pairs, input/output symmetry)
- Make ISSUE self-contained: a developer should understand exactly what is broken and why

### PHASE 3: PLAN (use `fork`)
**Goal**: Define a minimal, targeted fix strategy.
- **Create a plan using the `plan` tool**. List the exact changes needed.
- Define success criteria: what proves the issue is fixed?
- **Keep the plan updated throughout** — add, complete, or adjust tasks as you work.
- **Revisit and refine the plan** as you discover new information.

### PHASE 4: INVESTIGATE (use `fork`)
**Goal**: Finalize the fix strategy before implementation.
- Based on your plan, describe the exact code changes needed
- Ensure the fix targets the root cause, not a symptom
- Keep changes minimal: edit only what is necessary
- **Do NOT implement yet** — only confirm your strategy

### PHASE 5: IMPLEMENT, REVIEW, CRITIQUE (use `fork`)
**Goal**: Ship a minimal, correct fix. Iterate until satisfied.
1. **Edit source code** to fix the root cause. Minimal changes only.
2. **Review your edits**: Read every changed line. Correct? Complete? Minimal?
3. **Verify**: Run existing tests or write a small verification script. Confirm the root cause is fixed.
4. **Critique**: Is the fix minimal? Does it generalize? Are there edge cases missed?
5. **Repeat steps 1-4** until you are confident. Do not stop until the fix is solid.

**CRITICAL**: You MUST iterate through this loop multiple times. Do not accept the first fix. Review, critique, and refine until you are fully satisfied.

## MANDATORY RULES
- **Use tools**: `skill` (load skills), `plan` (make and update plan), `pyscan`, `pyanalyze`, `fork`/`subagent`
- **ONLY modify source code files**. Never modify or create test files — the evaluation framework applies its own test patch.
- **Fix the root cause**. Do not refactor unrelated code.
- **Do NOT run git commands**. Do NOT commit. Do NOT generate patches. The framework handles patch creation automatically after you finish editing.
- **Do NOT modify**: `.gitignore`, `ISSUE.txt`, `patch.diff`, `build/`, `dist/`, `*.egg-info/`
"""
    ]

    return prompt


# ─── Container setup helpers ────────────────────────────────────────────────

def copy_issue_to_container(container: Any, problem_statement: str) -> None:
    """Copy ISSUE.txt into the container at /testbed/ISSUE.txt.

    Args:
        container: Docker container object.
        problem_statement: Issue description text.
    """
    if not problem_statement:
        logger.warning("Empty problem_statement, writing empty ISSUE.txt")
    _write_tar_to_container(container, problem_statement.encode('utf-8'), "/testbed/ISSUE.txt")


def copy_issue_md_to_container(container: Any) -> None:
    """Copy ISSUE.md template into the container at /testbed/ISSUE.md.

    The template is copied as-is — tau fills in all sections from scratch.

    Args:
        container: Docker container object.

    Raises:
        FileNotFoundError: If ISSUE.md template does not exist.
    """
    if not ISSUE_MD_TEMPLATE.exists():
        logger.warning(f"ISSUE.md template not found: {ISSUE_MD_TEMPLATE}, skipping")
        return
    content = ISSUE_MD_TEMPLATE.read_bytes()
    _write_tar_to_container(container, content, "/testbed/ISSUE.md")


def setup_gitignore(container: Any) -> None:
    """Append framework file exclusions to existing .gitignore.

    DO NOT overwrite — the repo already has a .gitignore. Appending ensures
    our framework files are excluded without deleting the original rules.

    Args:
        container: Docker container object.
    """
    extra = """
# SWE-bench Verified framework files
ISSUE.txt
ISSUE.md
patch.diff
ANALYSIS.txt
.BASE_COMMIT
.web/
.coverage
coverage.xml
*.pkl
"""
    # Read existing .gitignore if it exists, then append
    result = container.exec_run(["cat", "/testbed/.gitignore"], workdir="/testbed")
    existing = result.output.decode('utf-8', errors='replace') if result.output else ""
    combined = existing + extra
    _write_tar_to_container(container, combined.encode('utf-8'), "/testbed/.gitignore")


def store_base_commit(container: Any, base_commit: str) -> None:
    """Store base commit hash for patch generation.

    Args:
        container: Docker container object.
        base_commit: Git commit hash string.
    """
    if not base_commit:
        logger.warning("Empty base_commit, writing empty .BASE_COMMIT")
    _write_tar_to_container(container, base_commit.encode('utf-8'), "/testbed/.BASE_COMMIT")


def copy_tau_to_container(container: Any) -> None:
    """Copy Tau agent source into the container at /tau/.

    Tau lives outside /testbed/ so it does not pollute the repo working tree.

    Args:
        container: Docker container object.

    Raises:
        FileNotFoundError: If TAU_DIR does not exist.
        NotADirectoryError: If TAU_DIR is not a directory.
        RuntimeError: If copying fails.
    """
    if not TAU_DIR.exists():
        raise FileNotFoundError(f"Tau source directory not found: {TAU_DIR}")
    if not TAU_DIR.is_dir():
        raise NotADirectoryError(f"Tau path is not a directory: {TAU_DIR}")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        tar.add(str(TAU_DIR), arcname="tau")
    try:
        container.put_archive("/", buf.getvalue())
    except docker.errors.APIError as e:
        raise RuntimeError(f"Failed to copy tau to container: {e}") from e
    logger.info(f"Copied Tau from {TAU_DIR} to {container.id[:12]}")


def setup_container(container: Any, instance: dict[str, Any]) -> bool:
    """Full container setup: ISSUE.txt, gitignore, base commit, Tau source.

    Args:
        container: Docker container object.
        instance: SWE-bench Verified instance dict.

    Returns:
        True on success.

    Raises:
        RuntimeError: If ISSUE.txt is not found after setup.
    """
    problem_statement = instance.get("problem_statement", "")
    base_commit = instance.get("base_commit", "")

    copy_issue_to_container(container, problem_statement)
    copy_issue_md_to_container(container)
    setup_gitignore(container)
    store_base_commit(container, base_commit)
    copy_tau_to_container(container)

    # Containers already have Python 3.10+ (no apt-get needed)
    # Verify setup
    result = container.exec_run(["test", "-f", f"{TESTBED_PATH}/ISSUE.txt"])
    if result.exit_code != 0:
        raise RuntimeError("ISSUE.txt not found in container after setup")

    return True
