#!/usr/bin/env python3
"""
SWE-bench Verified evaluation bridge.

This module handles two critical tasks:
1. Generating preds.json from artifacts (for submission)
2. Running the official SWE-bench Verified evaluation harness

The official eval script (evaluation.evaluation) runs inside Docker containers
from pre-built SWE-bench Verified images, applying patches and running tests.
"""
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from config import (
    ARTIFACTS_DIR,
    BASE_DIR,
    PREDICTIONS_FILE,
    SWE_BENCH_DIR,
    SWE_BENCH_REPO,
    instance_id_from_dir,
)

logger = logging.getLogger(__name__)


def ensure_eval_repo() -> Path:
    """Clone SWE-bench eval repo if not already cloned.

    The eval repo contains the official evaluation script and Docker image
    naming conventions. It's cloned once and reused across runs.

    Returns:
        Path to the cloned repo directory.

    Raises:
        RuntimeError: If git clone or pip install fails.
    """
    if not SWE_BENCH_DIR.exists():
        logger.info(f"Cloning SWE-bench repo to {SWE_BENCH_DIR}...")
        git_path = shutil.which("git")
        if not git_path:
            raise RuntimeError("git not found in PATH")
        try:
            subprocess.run(
                [git_path, "clone", "--recursive", SWE_BENCH_REPO, str(SWE_BENCH_DIR)],
                check=True, timeout=120
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Git clone timed out: {e}") from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git clone failed: {e}") from e

        # Install the eval package in editable mode
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", "swebench"],
                cwd=SWE_BENCH_DIR, check=True, timeout=120
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Pip install timed out: {e}") from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Pip install failed: {e}") from e
    return SWE_BENCH_DIR


def unwrap_patch(raw_patch: str) -> str:
    """Extract clean unified diff from potentially wrapped patch.

    Tau may produce patches in various formats. This function normalizes them
    to a clean unified diff that git apply can consume.

    Handles:
    - CRLF line endings (normalized to LF)
    - Double-wrapped patches (diff of patch.diff)
    - Git log headers (commit hash, author, etc.)
    - Already clean diffs (passthrough)
    - Patches with leading/trailing whitespace

    Args:
        raw_patch: Raw patch content from Tau.

    Returns:
        Clean unified diff string, or empty string if no valid diff found.
    """
    if not raw_patch:
        return ""

    # Normalize CRLF to LF
    raw_patch = raw_patch.replace("\r\n", "\n").replace("\r", "\n")
    raw_patch = raw_patch.strip()
    if not raw_patch:
        return ""

    # Double-wrapped: entire file is diff of patch.diff
    if raw_patch.startswith("diff --git a/patch.diff b/patch.diff"):
        inner_lines = []
        for line in raw_patch.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                inner_lines.append(line[1:])
        if inner_lines:
            return "\n".join(inner_lines)
        return ""

    # Git log headers — skip everything before the first "diff --git "
    if raw_patch.startswith("commit ") or raw_patch.startswith("Author:"):
        diff_start = raw_patch.find("diff --git ")
        if diff_start >= 0:
            return raw_patch[diff_start:]
        return ""

    # Already clean
    if raw_patch.startswith("diff --git "):
        return raw_patch

    # Try to find a diff block anywhere in the content
    diff_match = re.search(r"(diff --git .+)", raw_patch, re.DOTALL)
    if diff_match:
        return diff_match.group(1)

    return ""


def generate_preds_json(instance_ids: list[str] | None = None) -> dict[str, dict[str, str]]:
    """Generate predictions.jsonl from artifacts.

    Reads all patch files from artifacts/ and produces the SWE-bench Verified
    submission format: JSONL with {"instance_id": ..., "model_patch": ...}

    IMPORTANT: Instances with empty/missing patches are included with
    model_patch="" so the official eval counts them as failures.
    Skipping them would inflate the success rate.

    Args:
        instance_ids: Optional list of instance IDs to include.
            If None, includes all instances with artifacts.

    Returns:
        Dict mapping instance_id to {"model_patch": diff_string}.
    """
    preds = {}

    if not ARTIFACTS_DIR.exists():
        logger.warning(f"Artifacts directory not found: {ARTIFACTS_DIR}")
        return preds

    if instance_ids:
        artifact_dirs = [ARTIFACTS_DIR / iid for iid in instance_ids]
    else:
        try:
            artifact_dirs = sorted(ARTIFACTS_DIR.iterdir())
        except PermissionError as e:
            logger.error(f"Cannot list artifacts directory: {e}")
            return preds

    for artifact_dir in artifact_dirs:
        if not artifact_dir.is_dir():
            continue

        instance_id = instance_id_from_dir(artifact_dir.name)
        patch_file = artifact_dir / "patches" / "patch.diff"

        # If no patch file exists at all, include as empty (failure)
        if not patch_file.exists():
            logger.warning(f"No patch for {instance_id}, submitting as empty (failure)")
            preds[instance_id] = {"model_patch": ""}
            continue

        try:
            raw_content = patch_file.read_text()
        except OSError as e:
            logger.warning(f"Failed to read patch for {instance_id}: {e}")
            preds[instance_id] = {"model_patch": ""}
            continue

        # Empty patch → submit as empty (correct: counts as failure)
        if not raw_content.strip():
            logger.warning(f"Empty patch for {instance_id}, submitting as empty (failure)")
            preds[instance_id] = {"model_patch": ""}
            continue

        # Unwrap if needed
        patch_content = unwrap_patch(raw_content)
        # If unwrap produces nothing, submit as empty (failure)
        if not patch_content.strip():
            logger.warning(f"Could not extract valid diff for {instance_id}, submitting as empty")
            preds[instance_id] = {"model_patch": ""}
            continue

        preds[instance_id] = {
            "model_patch": patch_content
        }

    # Write predictions.jsonl
    try:
        PREDICTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PREDICTIONS_FILE, "w", encoding="utf-8") as f:
            for instance_id, pred in preds.items():
                f.write(json.dumps({"instance_id": instance_id, "model_patch": pred["model_patch"]}) + "\n")
    except OSError as e:
        logger.error(f"Failed to write predictions.jsonl: {e}")

    non_empty = sum(1 for v in preds.values() if v.get("model_patch", "").strip())
    logger.info(f"Generated predictions.jsonl with {len(preds)} predictions ({non_empty} non-empty) → {PREDICTIONS_FILE}")
    return preds


def _build_eval_cmd(
    dataset: str,
    split: str,
    platform: str,
    patch_dir: str,
    output_dir: str,
    workers: int,
    overwrite: bool,
    instance_ids: list[str] | None = None,
    start_month: str | None = None,
    end_month: str | None = None,
) -> list[str]:
    """Build the command for the official SWE-bench Verified evaluation.

    Uses swebench.harness.run_evaluation (SWE-bench v4.x API).

    Args:
        dataset: HuggingFace dataset name or path.
        split: Dataset split to evaluate.
        platform: Platform (unused in new API, kept for compat).
        patch_dir: Path to predictions file or "gold".
        output_dir: Directory for eval output (unused - logs go to logs/run_evaluation/).
        workers: Number of parallel workers.
        overwrite: Whether to overwrite existing results (unused in new API).
        instance_ids: Optional list of instance IDs to evaluate.
        start_month: Unused in new API.
        end_month: Unused in new API.

    Returns:
        Command list for subprocess.
    """
    import uuid
    run_id = f"eval-{uuid.uuid4().hex[:8]}"

    cmd = [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "--dataset_name", dataset,
        "--split", split,
        "--predictions_path", patch_dir,
        "--max_workers", str(workers),
        "--run_id", run_id,
        "--cache_level", "instance",
    ]

    if instance_ids:
        cmd.extend(["--instance_ids"] + instance_ids)

    # Store run_id in environment so callers can find results
    import os
    os.environ["SWE_EVAL_RUN_ID"] = run_id

    return cmd


def run_official_eval(
    dataset: str = "SWE-bench/SWE-bench_Verified",
    split: str = "test",
    platform: str = "linux",
    workers: int = 4,
    output_dir: str | None = None,
    overwrite: bool = False,
    instance_ids: list[str] | None = None,
    start_month: str | None = None,
    end_month: str | None = None,
) -> dict[str, Any]:
    """Run the official SWE-bench Verified evaluation.

    This invokes the evaluation.evaluation module from the SWE-bench Verified repo,
    which runs each instance in its own Docker container, applies the model patch,
    runs tests, and determines pass/fail.

    Args:
        dataset: HuggingFace dataset name or path.
        split: Dataset split to evaluate.
        platform: Platform (linux/windows).
        workers: Number of parallel workers.
        output_dir: Directory for eval output.
        overwrite: Whether to overwrite existing results.
        instance_ids: Optional list of instance IDs to evaluate.
        start_month: Optional start month filter (YYYY-MM).
        end_month: Optional end month filter (YYYY-MM).

    Returns:
        Results dict with success/failure counts and instance-level details.
    """
    try:
        eval_repo = ensure_eval_repo()
    except RuntimeError as e:
        logger.error(f"Failed to setup eval repo: {e}")
        return {"error": str(e)}

    output_dir = output_dir or str(BASE_DIR / "eval_output")

    cmd = _build_eval_cmd(
        dataset=dataset,
        split=split,
        platform=platform,
        patch_dir=str(PREDICTIONS_FILE),
        output_dir=output_dir,
        workers=workers,
        overwrite=overwrite,
        instance_ids=instance_ids,
        start_month=start_month,
        end_month=end_month,
    )

    logger.info(f"Running official eval: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=eval_repo, capture_output=True, text=True, timeout=7200)
    except subprocess.TimeoutExpired:
        logger.error("Official evaluation timed out (2 hours)")
        return {"error": "Evaluation timed out"}
    except Exception as e:
        logger.error(f"Failed to run evaluation: {e}")
        return {"error": str(e)}

    if result.stdout:
        logger.info(result.stdout[-2000:])
    if result.stderr:
        logger.warning(f"Eval stderr: {result.stderr[-1000:]}")

    # Parse results
    results_file = Path(output_dir) / "results.json"
    if results_file.exists():
        try:
            with open(results_file) as f:
                eval_results = json.load(f)
            logger.info(f"Eval results: {eval_results.get('success', 0)} success, {eval_results.get('failure', 0)} failure")
            return eval_results
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse results.json: {e}")
            return {"error": f"Invalid results.json: {e}"}
    else:
        logger.warning(f"No results file at {results_file}")
        return {"error": "No results file generated"}


def _run_single_gold_eval(
    eval_repo: Path,
    dataset: str,
    split: str,
    platform: str,
    workers: int,
    output_dir: str,
    run_num: int,
) -> set[str]:
    """Run a single gold validation pass and return the set of passing instance IDs.

    Args:
        eval_repo: Path to the cloned SWE-bench Verified repo.
        dataset: HuggingFace dataset name.
        split: Dataset split.
        platform: Platform (linux/windows).
        workers: Number of parallel workers.
        output_dir: Base output directory (appended with run number).
        run_num: Run number (1, 2, 3) for unique output dirs.

    Returns:
        Set of instance IDs that passed this gold run.
    """
    run_output = f"{output_dir}_run{run_num}"

    cmd = _build_eval_cmd(
        dataset=dataset,
        split=split,
        platform=platform,
        patch_dir="gold",
        output_dir=run_output,
        workers=workers,
        overwrite=True,
    )

    logger.info(f"Gold validation run {run_num}/3: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=eval_repo, capture_output=True, text=True, timeout=7200)
    except subprocess.TimeoutExpired:
        logger.error(f"Gold validation run {run_num} timed out (2 hours)")
        return set()
    except Exception as e:
        logger.error(f"Gold validation run {run_num} failed: {e}")
        return set()

    if result.stderr:
        logger.warning(f"Gold run {run_num} stderr: {result.stderr[-500:]}")

    gold_file = Path(run_output) / "gold_patch_evaluated_instances.jsonl"
    if gold_file.exists():
        try:
            passing = set()
            with open(gold_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            # The instance ID field name can vary
                            iid = entry.get("instance_id") or entry.get("instanceId") or entry.get("id")
                            if iid:
                                passing.add(iid)
                        except json.JSONDecodeError:
                            # Try treating the whole line as an instance ID
                            passing.add(line)
            logger.info(f"Gold run {run_num}: {len(passing)} instances passed")
            return passing
        except Exception as e:
            logger.error(f"Failed to parse gold results from run {run_num}: {e}")
            return set()
    else:
        logger.warning(f"No gold results file from run {run_num}: {gold_file}")
        return set()


def run_gold_validation(
    dataset: str = "SWE-bench/SWE-bench_Verified",
    split: str = "test",
    platform: str = "linux",
    workers: int = 4,
    output_dir: str | None = None,
    runs: int = 3,
) -> dict[str, Any]:
    """Run gold patch validation to filter flaky instances.

    SWE-bench Verified requires running gold patches 3× to filter invalid instances.
    The intersection of passing instances across all runs is the valid set.
    Your success rate denominator = len(intersection).

    Args:
        dataset: HuggingFace dataset name or path.
        split: Dataset split to validate.
        platform: Platform (linux/windows).
        workers: Number of parallel workers.
        output_dir: Base directory for validation output.
        runs: Number of gold validation runs (default 3).

    Returns:
        Dict with:
            - "valid_instances": set of instance IDs that passed all runs
            - "per_run": dict mapping run number to set of passing IDs
            - "output_file": path to the final intersection file
    """
    try:
        eval_repo = ensure_eval_repo()
    except RuntimeError as e:
        logger.error(f"Failed to setup eval repo: {e}")
        return {"error": str(e), "valid_instances": set(), "per_run": {}, "output_file": Path("/dev/null")}

    output_dir = output_dir or str(BASE_DIR / "gold_output")

    # Run gold validation N times
    per_run = {}
    for i in range(1, runs + 1):
        passing = _run_single_gold_eval(
            eval_repo=eval_repo,
            dataset=dataset,
            split=split,
            platform=platform,
            workers=workers,
            output_dir=output_dir,
            run_num=i,
        )
        per_run[i] = passing

        # Compute running intersection
        running_intersection = set.intersection(*per_run.values())
        logger.info(f"After run {i}/{runs}: {len(running_intersection)} instances pass all runs so far")

    # Final intersection
    valid_instances = set.intersection(*per_run.values()) if per_run else set()

    # Write the final intersection file
    output_file = Path(output_dir) / "gold_patch_evaluated_instances.jsonl"
    try:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            for iid in sorted(valid_instances):
                f.write(json.dumps({"instance_id": iid}) + "\n")
        logger.info(f"Gold validation complete: {len(valid_instances)} stable instances → {output_file}")
    except Exception as e:
        logger.error(f"Failed to write gold validation results: {e}")

    return {
        "valid_instances": valid_instances,
        "per_run": per_run,
        "output_file": output_file,
    }
