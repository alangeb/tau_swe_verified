#!/usr/bin/env python3
"""
SWE-bench Verified Adapter — Main CLI entry point.

Orchestrates the full pipeline:
  Phase 1 — Fix:    Tau runs inside Docker to generate a patch
  Phase 2 — Eval:   Patch is applied and tests are run
  Phase 3 — Submit: Generate preds.json + run official evaluation

Usage:
    # Run a single instance
    python swe_adapter.py --instance-id django__django-12345 --llm cuda

    # Run first 5 instances
    python swe_adapter.py --count 5 --llm cuda

    # Resume from last completed
    python swe_adapter.py --resume --count 1 --llm cuda

    # Retry failed instances
    python swe_adapter.py --retry --max-retries 2 --llm cuda

    # Check Docker images
    python swe_adapter.py --check-images

    # Generate submission files
    python swe_adapter.py --generate-preds

    # Run official evaluation
    python swe_adapter.py --eval --workers 4

    # Run gold validation (3x)
    python swe_adapter.py --gold-validation --eval-workers 4

    # Prepare submission
    python swe_adapter.py 
"""
import argparse
import json
import gc
import logging
import signal
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from config import (
    ARTIFACTS_DIR,
    DATASET_NAME,
    DATASET_SPLIT,
    DEFAULT_LLM_GROUP,
    RESULTS_FILE,
    STATUS_FILE,
)
from swe_combined import run_combined
from swe_docker import (
    DockerManager,
    DiskSpaceError,
    check_disk_space,
    cleanup_all,
    get_docker_image_name,
)
from swe_eval import generate_preds_json, run_official_eval, run_gold_validation

logger = logging.getLogger(__name__)


# ─── Dataset ────────────────────────────────────────────────────────────────

def load_dataset(split: str | None = None):
    """Load SWE-bench Verified dataset from HuggingFace.

    Args:
        split: Dataset split name (defaults to DATASET_SPLIT from config).

    Returns:
        HuggingFace dataset object.

    Raises:
        SystemExit: If dataset loading fails.
    """
    from datasets import load_dataset
    split = split or DATASET_SPLIT
    try:
        ds = load_dataset(DATASET_NAME, split=split)
        logger.info(f"Loaded {DATASET_NAME} ({split}): {len(ds)} instances")
        return ds
    except Exception as e:
        logger.error(f"Failed to load dataset: {e}")
        sys.exit(1)


# ─── Results I/O ────────────────────────────────────────────────────────────

def load_results() -> list:
    """Load existing results from results.jsonl."""
    if not RESULTS_FILE.exists():
        return []
    results = []
    try:
        with open(RESULTS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError as e:
        logger.warning(f"Failed to read results file: {e}")
    return results


def save_result(result: dict[str, Any]) -> None:
    """Save result to results.jsonl (deduplicated by instance_id).

    Args:
        result: Result dict with instance_id and status fields.
    """
    instance_id = result.get("instance_id", "")
    existing = []
    if RESULTS_FILE.exists():
        try:
            with open(RESULTS_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            if entry.get("instance_id") == instance_id:
                                continue  # Skip old entry
                            existing.append(line)
                        except json.JSONDecodeError:
                            existing.append(line)
        except OSError as e:
            logger.warning(f"Failed to read existing results: {e}")

    try:
        with open(RESULTS_FILE, "w") as f:
            for line in existing:
                f.write(line + "\n")
            f.write(json.dumps(result) + "\n")
    except OSError as e:
        logger.error(f"Failed to save result for {instance_id}: {e}")


def get_completed_instance_ids() -> set:
    """Get set of instance IDs that have been processed."""
    results = load_results()
    return {r["instance_id"] for r in results if "instance_id" in r}


def get_failed_instance_ids() -> set:
    """Get set of instance IDs that failed."""
    results = load_results()
    return {
        r["instance_id"] for r in results
        if r.get("status") in ("failed", "patch_failed", "error", "timeout", "image_missing")
    }


def get_retry_candidates(max_retries: int = 3) -> set[str]:
    """Get instances that failed and haven't exceeded max retry count.

    Args:
        max_retries: Maximum number of retries per instance.

    Returns:
        Set of instance IDs eligible for retry.
    """
    results = load_results()
    # Count retries per instance
    retry_counts = {}
    for r in results:
        iid = r.get("instance_id")
        if iid and r.get("status") in ("failed", "patch_failed", "error", "timeout"):
            retry_counts[iid] = retry_counts.get(iid, 0) + 1

    # Find failed instances under retry limit
    failed_ids = {iid for iid, count in retry_counts.items() if count < max_retries}
    logger.info(f"Found {len(failed_ids)} retry candidates (max {max_retries} retries, "
                f"{len(retry_counts)} total failures)")
    return failed_ids


# ─── Status report ────────────────────────────────────────────────────────────

def _find_log_files(directory: Path, ext: str) -> list[str]:
    """Find log files with given extension in directory."""
    if not directory.exists():
        return []
    return sorted([str(f) for f in directory.glob(f"*{ext}")])


def print_instance_report(result: dict[str, Any], artifact_dir: Path) -> None:
    """Print a comprehensive status report for a single instance.

    Shows: status, durations, file paths, patch content, analysis results.
    """
    instance_id = result.get("instance_id", "unknown")
    status = result.get("status", "unknown")
    fix_status = result.get("fix_status", "unknown")
    eval_status = result.get("eval_status")
    total_dur = result.get("duration_seconds", 0)
    fix_dur = result.get("fix_duration", 0)
    eval_dur = result.get("eval_duration", 0)
    analysis = result.get("analysis") or {}
    analysis_dur = analysis.get("duration_seconds", 0) if analysis else 0

    # Color codes
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[90m"
    RESET = "\033[0m"

    status_color = GREEN if status == "resolved" else (RED if status in ("failed", "error") else YELLOW)

    print(f"\n{'='*70}")
    print(f"  {BOLD}INSTANCE REPORT{RESET} {DIM}({instance_id}){RESET}")
    print(f"{'='*70}")

    # Overall status
    print(f"  {BOLD}Status:{RESET}     {status_color}{status}{RESET}")
    print(f"  {BOLD}Total time:{RESET} {total_dur:.1f}s")
    print()

    # Fix phase
    fix_color = GREEN if fix_status == "resolved" else (RED if fix_status == "failed" else YELLOW)
    print(f"  {BOLD}── FIX PHASE ──{RESET}")
    print(f"    Status:   {fix_color}{fix_status}{RESET}")
    print(f"    Duration: {fix_dur:.1f}s")

    # File paths
    patch_file = artifact_dir / "patches" / "patch.diff"
    fix_stdout = artifact_dir / "fix" / "stdout.log"
    audit_files = _find_log_files(artifact_dir / "fix" / "logs", ".audit")
    context_files = _find_log_files(artifact_dir / "fix" / "logs", ".context")

    print(f"    Patch:    {patch_file}")
    print(f"    Stdout:   {fix_stdout}")
    if audit_files:
        print(f"    Audit:    {audit_files[0]}")
    if context_files:
        print(f"    Context:  {context_files[0]}")
    print()

    # Eval phase
    if eval_status:
        eval_color = GREEN if eval_status == "pass" else RED
        print(f"  {BOLD}── EVAL PHASE ──{RESET}")
        print(f"    Status:   {eval_color}{eval_status}{RESET}")
        print(f"    Duration: {eval_dur:.1f}s")

        eval_stdout = artifact_dir / "eval" / "testlog.out"
        eval_resolution = artifact_dir / "eval" / "resolution.json"
        print(f"    Testlog:  {eval_stdout}")
        print(f"    Resolution: {eval_resolution}")

        # Show FAIL_TO_PASS / PASS_TO_PASS details
        eval_info = result.get("eval_info") or {}
        f2p = eval_info.get("fail_to_pass", [])
        p2p = eval_info.get("pass_to_pass", [])
        if f2p:
            # fail_to_pass is a list of test names (strings)
            print(f"    F2P:      {len(f2p)} tests")
        if p2p:
            # pass_to_pass is a list of test names (strings)
            print(f"    P2P:      {len(p2p)} tests")
        print()

    # Analysis phase
    if analysis and analysis.get("analysis_text"):
        print(f"  {BOLD}── ANALYSIS PHASE ──{RESET}")
        print(f"    Duration: {analysis_dur:.1f}s")
        analysis_stdout = artifact_dir / "analysis" / "stdout.log"
        analysis_file = artifact_dir / "analysis" / "analysis.txt"
        print(f"    Stdout:   {analysis_stdout}")
        print(f"    Report:   {analysis_file}")
        print()

    # Full patch
    if patch_file.exists():
        patch_content = patch_file.read_text(errors="replace")
        if patch_content.strip():
            print(f"  {BOLD}── PATCH ({len(patch_content)} bytes) ──{RESET}")
            print(patch_content)
            print()

    # Analysis results
    if analysis and analysis.get("analysis_text"):
        analysis_text = analysis["analysis_text"]
        print(f"  {BOLD}── ANALYSIS RESULTS ({len(analysis_text)} chars) ──{RESET}")
        print(analysis_text)
        print()

    print(f"{'='*70}\n")

def update_status(status_data: dict[str, Any]) -> None:
    """Update the live status.json file atomically.

    Args:
        status_data: Status dict to write.
    """
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(status_data, f, indent=2)
        shutil.move(str(tmp), str(STATUS_FILE))
    except OSError as e:
        logger.warning(f"Failed to update status: {e}")


# ─── Docker images ───────────────────────────────────────────────────────────

def check_images(dataset: Any, pull: bool = False) -> dict[str, str | None]:
    """Check which Docker images exist locally.

    Args:
        dataset: HuggingFace dataset object.
        pull: If True, attempt to pull missing images.

    Returns:
        Dict mapping instance_id to image name (or None if missing).
    """
    with DockerManager() as dm:
        results = {}
        for instance in dataset:
            instance_id = instance["instance_id"]
            hub_image, local_image = get_docker_image_name(instance_id)

            if dm.image_exists(local_image):
                results[instance_id] = local_image
            elif dm.image_exists(hub_image):
                results[instance_id] = hub_image
            elif pull:
                try:
                    logger.info(f"Pulling {hub_image}...")
                    dm._pull_with_retry(hub_image)
                    results[instance_id] = hub_image
                except Exception:
                    results[instance_id] = None
            else:
                results[instance_id] = None
        return results


# ─── Instance resolution ────────────────────────────────────────────────────

def resolve_instances(
    args: argparse.Namespace, dataset: Any, total: int
) -> list[tuple[int, dict[str, Any]]]:
    """Resolve origin + count to list of (index, instance) tuples.

    Args:
        args: Parsed CLI arguments.
        dataset: HuggingFace dataset object.
        total: Total number of instances in the dataset.

    Returns:
        List of (instance_index, instance_dict) tuples to process.
    """
    # Determine start index
    if args.instance_id:
        found_idx = None
        for idx, ds in enumerate(dataset):
            if ds["instance_id"] == args.instance_id:
                found_idx = idx
                break
        if found_idx is None:
            logger.error(f"Instance '{args.instance_id}' not found in dataset")
            sys.exit(1)
        start = found_idx
    else:
        start = args.start
        if start < 0:
            logger.error("--start must be >= 0")
            sys.exit(1)
        if start >= total:
            logger.error(f"--start {start} exceeds dataset size ({total})")
            sys.exit(1)

    # Determine count
    if args.all:
        count = None
    else:
        count = args.count
        if count <= 0:
            logger.error("--count must be positive")
            sys.exit(1)

    if args.resume:
        completed = get_completed_instance_ids()
        result = []
        for i in range(start, total):
            if dataset[i]["instance_id"] not in completed:
                result.append((i, dataset[i]))
                if count is not None and len(result) >= count:
                    break
        return result
    else:
        if count is None:
            end = total
        else:
            end = min(start + count, total)
            if end < start + count:
                logger.warning(f"--start {start} --count {count} exceeds dataset size ({total}), capping")
        return [(i, dataset[i]) for i in range(start, end)]


def resolve_retry_instances(
    args: argparse.Namespace, dataset: Any
) -> list[tuple[int, dict[str, Any]]]:
    """Resolve retry instances: failed instances that haven't exceeded max retries.

    Args:
        args: Parsed CLI arguments.
        dataset: HuggingFace dataset object.

    Returns:
        List of (instance_index, instance_dict) tuples to retry.
    """
    max_retries = args.max_retries
    retry_ids = get_retry_candidates(max_retries)

    if not retry_ids:
        logger.info("No retry candidates found (all failures already at max retries or no failures)")
        return []

    result = []
    for i, ds in enumerate(dataset):
        if ds["instance_id"] in retry_ids:
            result.append((i, ds))
            if args.count and len(result) >= args.count:
                break
    return result


# ─── Single instance runner ─────────────────────────────────────────────────

def run_instance(
    instance: dict[str, Any],
    instance_index: int,
    llm_group: str | None = None,
    do_eval: bool = True,
    retry_count: int = 0,
    stream: bool = False,
) -> dict[str, Any]:
    """Run a single instance through the workflow.

    Args:
        instance: Instance dict from dataset.
        instance_index: Display index for logging.
        llm_group: LLM group to use (e.g. "cuda").
        do_eval: Whether to run evaluation phase.
        retry_count: Current retry attempt number (0 = first run).
        stream: If True, stream agent stdout to terminal in real-time.

    Returns:
        Result dict with status, duration, fix_status, eval_status, etc.
    """
    instance_id = instance["instance_id"]
    artifact_dir = ARTIFACTS_DIR / f"{instance_index + 1}_{instance_id}"

    # Clean previous artifacts to avoid stale data on re-run
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"[retry {retry_count}]" if retry_count > 0 else ""
    start_time = time.time()
    logger.info(f"\n{'=' * 60}")
    logger.info(f"[{instance_index}] {prefix} Instance: {instance_id}")
    logger.info(f"Artifacts: {artifact_dir}")
    logger.info(f"{'=' * 60}")

    try:
        result = run_combined(
            instance, artifact_dir, llm_group,
            fix_timeout=1800,
            eval_timeout=300 if do_eval else 0,
            stream=stream,
        )
    except RuntimeError as e:
        if "No Docker image found" in str(e):
            logger.error(f"Skipping {instance_id}: {e}")
            duration = time.time() - start_time
            result = {
                "instance_id": instance_id,
                "repo": instance.get("repo", ""),
                "base_commit": instance.get("base_commit", ""),
                "status": "image_missing",
                "timestamp": datetime.now().isoformat(),
                "duration_seconds": round(duration, 2),
                "fix_status": "image_missing",
                "eval_status": None,
                "error_message": str(e),
                "artifact_dir": str(artifact_dir),
            }
        else:
            raise

    # Track retry count in result
    result["retry_count"] = retry_count

    # Persist results
    save_result(result)
    meta_file = artifact_dir / "meta.json"
    try:
        with open(meta_file, "w") as f:
            json.dump(result, f, indent=2)
    except OSError as e:
        logger.warning(f"Failed to write meta.json for {instance_id}: {e}")

    total_duration = result.get("duration_seconds", 0)
    logger.info(f"Result: {result['status']} in {total_duration:.1f}s")
    return result


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    """CLI entry point for the SWE-bench Verified adapter."""

    # Signal handlers to clean up containers on SIGTERM/SIGINT
    def _signal_handler(signum, frame):
        cleanup_all()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    parser = argparse.ArgumentParser(description="SWE-bench Verified Adapter for Tau Agent")

    # Origin
    origin = parser.add_mutually_exclusive_group()
    origin.add_argument("--start", type=int, default=0, help="Start index (0-based)")
    origin.add_argument("--instance-id", type=str, default=None, help="Start at this instance ID")

    # Count
    parser.add_argument("--count", type=int, default=1, help="Number of instances")
    parser.add_argument("--all", action="store_true", help="All remaining from start")

    # Mode
    parser.add_argument("--resume", action="store_true", help="Skip completed instances")
    parser.add_argument("--llm", type=str, default=None, help=f"LLM group (default: {DEFAULT_LLM_GROUP})")
    parser.add_argument("--no-eval", action="store_true", help="Skip evaluation phase")
    parser.add_argument("--stream", action="store_true", help="Stream agent stdout to terminal in real-time")

    # Retry
    parser.add_argument("--retry", action="store_true", help="Retry failed instances")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries per instance (default: 3)")

    # Special modes
    parser.add_argument("--check-images", action="store_true", help="Check Docker images and exit")
    parser.add_argument("--prebuild-images", action="store_true", help="Pull all images before testing")
    parser.add_argument("--generate-preds", action="store_true", help="Generate preds.json from artifacts")
    parser.add_argument("--eval", action="store_true", help="Run official SWE-bench Verified evaluation")
    parser.add_argument("--eval-workers", type=int, default=4, help="Workers for official eval")
    parser.add_argument("--gold-validation", action="store_true", help="Run gold patch validation (3x)")

    # Official eval extra args
    parser.add_argument("--eval-instance-ids", type=str, default=None,
                         help="Comma-separated instance IDs for official eval")
    parser.add_argument("--start-month", type=str, default=None,
                         help="Start month filter for eval (YYYY-MM)")
    parser.add_argument("--end-month", type=str, default=None,
                         help="End month filter for eval (YYYY-MM)")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    llm_group = args.llm or DEFAULT_LLM_GROUP

    # ─── Special modes ────────────────────────────────────────────────────


    if args.generate_preds:
        preds = generate_preds_json()
        print(f"Generated preds.json with {len(preds)} predictions")
        return

    if args.eval:
        # Parse instance IDs if provided
        instance_ids = None
        if args.eval_instance_ids:
            instance_ids = [s.strip() for s in args.eval_instance_ids.split(",") if s.strip()]

        results = run_official_eval(
            dataset=DATASET_NAME,
            split=DATASET_SPLIT,
            workers=args.eval_workers,
            overwrite=True,
            instance_ids=instance_ids,
            start_month=args.start_month,
            end_month=args.end_month,
        )
        print(json.dumps(results, indent=2))
        return

    if args.gold_validation:
        gold_result = run_gold_validation(
            dataset=DATASET_NAME,
            split=DATASET_SPLIT,
            workers=args.eval_workers,
            runs=3,
        )
        if "error" in gold_result:
            print(f"Gold validation error: {gold_result['error']}")
            return
        valid = gold_result.get("valid_instances", set())
        per_run = gold_result.get("per_run", {})
        output_file = gold_result.get("output_file", Path("/dev/null"))

        print(f"\nGold validation complete (3 runs):")
        for run_num, passing in per_run.items():
            print(f"  Run {run_num}: {len(passing)} passed")
        print(f"  Intersection (stable): {len(valid)} instances")
        print(f"  Output: {output_file}")
        return


    # ─── Normal run mode ──────────────────────────────────────────────────

    dataset = load_dataset()
    total = len(dataset)
    logger.info(f"Dataset: {DATASET_NAME} ({DATASET_SPLIT}), {total} instances")

    # ─── Check images mode ────────────────────────────────────────────────
    if args.check_images:
        instance_ids = [ds["instance_id"] for ds in dataset]
        image_status = check_images(dataset)

        found = sum(1 for v in image_status.values() if v is not None)
        missing = sum(1 for v in image_status.values() if v is None)

        logger.info(f"Image check: {found} found, {missing} missing")
        for iid, img in image_status.items():
            status = img if img else "MISSING"
            logger.info(f"  {iid}: {status}")
        return

    # ─── Prebuild images mode ────────────────────────────────────────────
    if args.prebuild_images:
        logger.info("Pulling all images...")
        check_images(dataset, pull=True)
        return

    # ─── Retry mode ───────────────────────────────────────────────────────
    if args.retry:
        todos = resolve_retry_instances(args, dataset)
        if not todos:
            logger.info("No instances to retry")
            return

        # Initial disk space check
        try:
            free_gb = check_disk_space(warn=True)
            logger.info(f"Disk space: {free_gb:.1f}GB free before retry")
        except DiskSpaceError as e:
            logger.error(f"Cannot start: {e}")
            sys.exit(1)

        logger.info(f"Retrying {len(todos)} failed instances (llm={llm_group})")

        # Cleanup orphaned containers
        with DockerManager() as cleanup_dm:
            cleanup_dm.cleanup_orphaned_containers()

        update_status({
            "pipeline_start": datetime.now().isoformat(),
            "total_instances": total,
            "to_retry": len(todos),
            "status": "retrying",
        })

        for idx, (instance_idx, instance) in enumerate(todos, 1):
            try:
                check_disk_space(warn=True)
            except DiskSpaceError as e:
                logger.error(f"Disk space critically low before retry {idx}/{len(todos)}: {e}")
                sys.exit(1)

            update_status({
                "current": idx,
                "total_to_retry": len(todos),
                "current_instance": instance["instance_id"],
                "status": "retrying",
            })

            # Determine current retry count
            results = load_results()
            retry_count = sum(1 for r in results
                             if r.get("instance_id") == instance["instance_id"])

            try:
                result = run_instance(instance, instance_idx, llm_group,
                                        do_eval=not args.no_eval,
                                        retry_count=retry_count,
                                        stream=args.stream)
            except Exception as e:
                logger.exception(f"Retry failed for {instance['instance_id']}: {e}")
                result = {
                    "instance_id": instance["instance_id"],
                    "status": "error",
                    "error_message": str(e),
                    "timestamp": datetime.now().isoformat(),
                    "retry_count": retry_count,
                }
                save_result(result)

        completed = get_completed_instance_ids()
        failed = get_failed_instance_ids()
        update_status({
            "pipeline_end": datetime.now().isoformat(),
            "total_instances": total,
            "completed": len(completed),
            "failed": len(failed),
            "status": "retry_completed",
        })
        logger.info(f"\nRetry done. Completed: {len(completed)}, Failed: {len(failed)}")
        return

    # ─── Run instances ───────────────────────────────────────────────────
    todos = resolve_instances(args, dataset, total)
    if not todos:
        logger.info("No instances to run")
        return

    # Initial disk space check
    try:
        free_gb = check_disk_space(warn=True)
        logger.info(f"Disk space: {free_gb:.1f}GB free before starting")
    except DiskSpaceError as e:
        logger.error(f"Cannot start: {e}")
        sys.exit(1)

    # Cleanup orphaned containers from previous runs
    with DockerManager() as cleanup_dm:
        cleanup_dm.cleanup_orphaned_containers()

    logger.info(f"Running {len(todos)} instances (llm={llm_group})")

    # Update status
    update_status({
        "pipeline_start": datetime.now().isoformat(),
        "total_instances": total,
        "to_run": len(todos),
        "status": "running",
    })

    # Run instances
    for idx, (instance_idx, instance) in enumerate(todos, 1):
        # Check disk space before each instance
        try:
            check_disk_space(warn=True)
        except DiskSpaceError as e:
            logger.error(f"Disk space critically low before instance {idx}/{len(todos)}: {e}")
            logger.info(f"Stopping pipeline. Completed: {idx-1}/{len(todos)} instances.")
            # Save final status and exit gracefully
            completed = get_completed_instance_ids()
            failed = get_failed_instance_ids()
            update_status({
                "pipeline_end": datetime.now().isoformat(),
                "total_instances": total,
                "completed": len(completed),
                "failed": len(failed),
                "status": "disk_space_exhausted",
                "message": str(e),
            })
            sys.exit(1)

        update_status({
            "pipeline_start": datetime.now().isoformat(),
            "total_instances": total,
            "current": idx,
            "total_to_run": len(todos),
            "current_instance": instance["instance_id"],
            "status": "running",
        })

        try:
            result = run_instance(instance, instance_idx, llm_group, do_eval=not args.no_eval, stream=args.stream)
        except Exception as e:
            logger.exception(f"Unexpected error on {instance['instance_id']}: {e}")
            result = {
                "instance_id": instance["instance_id"],
                "status": "error",
                "error_message": str(e),
                "timestamp": datetime.now().isoformat(),
            }
            save_result(result)

        # Print detailed status report
        artifact_dir = ARTIFACTS_DIR / f"{instance_idx + 1}_{instance['instance_id']}"
        print_instance_report(result, artifact_dir)

        # Update status
        completed = get_completed_instance_ids()
        failed = get_failed_instance_ids()
        update_status({
            "pipeline_start": datetime.now().isoformat(),
            "total_instances": total,
            "current": idx,
            "total_to_run": len(todos),
            "completed": len(completed),
            "failed": len(failed),
            "current_instance": instance["instance_id"],
            "status": "running",
        })

    # Final status
    completed = get_completed_instance_ids()
    failed = get_failed_instance_ids()
    update_status({
        "pipeline_end": datetime.now().isoformat(),
        "total_instances": total,
        "completed": len(completed),
        "failed": len(failed),
        "status": "completed",
    })

    logger.info(f"\nDone. Completed: {len(completed)}, Failed: {len(failed)}")

    # Suppress harmless "I/O operation on closed file" stderr noise during Python 3.13
    # interpreter teardown. MUST happen BEFORE gc.collect() because urllib3 objects
    # get garbage collected during gc.collect() and print to stderr.
    # __del__ errors print directly to sys.stderr (bypass sys.excepthook), so we
    # replace sys.stderr with a silent object and redirect fd 2 to /dev/null.
    class _SilentStderr:
        def write(self, *args): pass
        def flush(self, *args): pass
        def isatty(self): return False
        def close(self): pass
        def readable(self): return False
        def writable(self): return False
        def seekable(self): return False
    sys.stderr = _SilentStderr()
    _devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(_devnull, 2)
    os.close(_devnull)

    # Explicitly close all Docker clients to prevent urllib3 shutdown race
    cleanup_all()

    # Force garbage collection after cleanup
    gc.collect()


if __name__ == "__main__":
    main()
