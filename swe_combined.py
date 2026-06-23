#!/usr/bin/env python3
"""
Combined fix+eval+analysis workflow for SWE-bench Verified.

Three-phase pipeline:
  1. Fix — Tau agent generates a patch (in fix container)
  2. Eval — official SWE-bench Verified eval (in separate eval container)
  3. Analysis — if patch creation failed, analyze in fix container;
     if eval failed, analyze in eval container with Tau + logs copied in
"""
import json
import logging
import os
import re
import subprocess
import sys
import tarfile
import time
import io
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config import (
    ANALYSIS_TIMEOUT,
    EVAL_TIMEOUT,
    TAU_TIMEOUT,
    TESTBED_PATH,
    TESTBED_PYTHON,
    BASE_DIR,
    ARTIFACTS_DIR,
    PREDICTIONS_FILE,
    TAU_DIR,
    IMAGE_NAMESPACE,
    IMAGE_PREFIX,
)
from swe_docker import DockerManager, _write_tar_to_container
from swe_fix import build_docker_prompts, setup_container, copy_issue_to_container, copy_tau_to_container, ISSUE_MD_TEMPLATE
from swe_eval import ensure_eval_repo, generate_preds_json, _build_eval_cmd

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """Base exception for pipeline errors with structured context."""

    def __init__(self, message: str, instance_id: str = "", phase: str = "", cause: Exception | None = None) -> None:
        super().__init__(message)
        self.instance_id = instance_id
        self.phase = phase
        self.cause = cause


def run_combined(
    instance: dict[str, Any],
    artifact_dir: Path,
    llm_group: Optional[str] = None,
    fix_timeout: float = TAU_TIMEOUT,
    eval_timeout: float = EVAL_TIMEOUT,
    analysis_timeout: float = ANALYSIS_TIMEOUT,
    stream: bool = False,
) -> dict[str, Any]:
    """Run fix + official eval + analysis.

    Three-phase pipeline:
      1. Fix — Tau agent generates a patch (in fix container)
      2. Eval — official SWE-bench Verified eval (in eval container)
      3. Analysis — if patch creation failed, analyze in fix container;
         if eval failed, analyze in eval container with Tau + logs copied in

    Args:
        instance: SWE-bench Verified instance dict.
        artifact_dir: Directory for storing patches, logs, meta.json.
        llm_group: LLM group name for Tau (None = default).
        fix_timeout: Timeout for fix phase in seconds.
        eval_timeout: Timeout for official eval phase in seconds.
        analysis_timeout: Timeout for analysis phase in seconds.
        stream: If True, stream agent stdout to terminal in real-time.

    Returns:
        Result dict with status, duration, fix_status, eval_status, etc.
    """
    if not instance or not isinstance(instance, dict):
        raise ValueError("instance must be a non-empty dict")
    if not artifact_dir:
        raise ValueError("artifact_dir cannot be None")

    instance_id = instance.get("instance_id", "unknown")
    repo_name = instance.get("repo", "")
    base_commit = instance.get("base_commit", "")

    start_time = time.time()
    logger.info(f"Running fix+eval for {instance_id}")

    with DockerManager() as dm:
        # Find image
        image_name = dm.find_image(instance_id, skip_disk_check=True)
        if image_name is None:
            raise RuntimeError(f"No Docker image found for {instance_id}")

        logger.info(f"Using image: {image_name}")

        # Start container
        container = dm.start_container(image_name, name=f"swe-fix-{instance_id}", skip_disk_check=True)

        try:
            # === SETUP ===
            try:
                setup_container(container, instance)
            except Exception as e:
                raise PipelineError(
                    f"Container setup failed for {instance_id}: {e}",
                    instance_id=instance_id,
                    phase="setup",
                    cause=e,
                ) from e

            testbed_python = dm.find_testbed_python(container)
            logger.info(f"Using testbed Python: {testbed_python}")

            # === PHASE 1: FIX ===
            logger.info("Phase 1: Fixing...")
            fix_start = time.time()

            try:
                prompts = build_docker_prompts(instance, repo_name)
            except Exception as e:
                raise PipelineError(
                    f"Failed to build prompts for {instance_id}: {e}",
                    instance_id=instance_id,
                    phase="prompts",
                    cause=e,
                ) from e

            returncode, stdout, tau_duration = dm.run_tau(
                container, prompts, artifact_dir, llm_group, fix_timeout, phase="fix", stream=stream,
                python_path=testbed_python
            )

            # Extract patch
            patch = dm.extract_patch(container, artifact_dir)
            fix_duration = time.time() - fix_start

            has_patch = bool(patch.strip())
            if returncode == 0 and has_patch:
                fix_status = "resolved"
            elif has_patch:
                fix_status = "failed"
            else:
                fix_status = "patch_failed"

            logger.info(f"Fix: {fix_status} in {fix_duration:.1f}s (tau={tau_duration:.1f}s, patch={len(patch)}B)")

            # === PHASE 2: OFFICIAL EVAL ===
            eval_result = None
            if has_patch:
                logger.info("Phase 2: Running official eval...")
                eval_start = time.time()
                eval_result = _run_official_eval_single(
                    dm, container, instance, patch, artifact_dir, eval_timeout,
                )
                eval_duration = time.time() - eval_start
                logger.info(f"Eval: {eval_result.get('status')} in {eval_duration:.1f}s")
            else:
                logger.info("Phase 2: Skipped (no patch)")

            # === PHASE 3: ANALYSIS ===
            analysis_result = None

            # Determine what kind of analysis is needed
            needs_test_failure_analysis = (
                eval_result is not None
                and not eval_result.get("test_passed")
            )
            needs_patch_failure_analysis = (
                fix_status == "patch_failed"
                and not needs_test_failure_analysis
            )

            if needs_test_failure_analysis:
                logger.info("Phase 3: Running eval failure analysis...")
                analysis_result = _run_failure_analysis(
                    dm, container, instance, patch, eval_result, artifact_dir,
                    llm_group=llm_group, stream=stream, analysis_timeout=analysis_timeout,
                    python_path=testbed_python,
                )
            elif needs_patch_failure_analysis:
                logger.info("Phase 3: Running patch failure analysis...")
                analysis_result = _run_patch_failure_analysis(
                    dm, container, instance, fix_status, artifact_dir,
                    llm_group=llm_group, stream=stream,
                    analysis_timeout=analysis_timeout,
                    python_path=testbed_python,
                )
            else:
                logger.info("Phase 3: Skipped (no analysis needed)")

            total_duration = time.time() - start_time

            # Determine overall status
            if eval_result and eval_result.get("test_passed"):
                status = "resolved"
            elif eval_result and eval_result.get("status") == "eval_failed":
                status = "eval_failed"
            elif has_patch:
                status = "patch_created"
            else:
                status = "patch_failed"

            result: dict[str, Any] = {
                "instance_id": instance_id,
                "repo": repo_name,
                "base_commit": base_commit,
                "status": status,
                "timestamp": datetime.now().isoformat(),
                "duration_seconds": round(total_duration, 2),
                "fix_status": fix_status,
                "fix_duration": round(fix_duration, 2),
                "eval_status": eval_result.get("status") if eval_result else None,
                "eval_duration": eval_result.get("duration", 0) if eval_result else None,
                "patch_size": len(patch),
                "error_message": (stdout[-500:] if stdout and not has_patch else None),
                "analysis": analysis_result,
                "artifact_dir": str(artifact_dir),
            }

            logger.info(f"Result: {status} in {total_duration:.1f}s")
            return result

        except PipelineError:
            raise
        except Exception as e:
            logger.exception(f"Error running {instance_id}")
            total_duration = time.time() - start_time
            return {
                "instance_id": instance_id,
                "repo": repo_name,
                "base_commit": base_commit,
                "status": "error",
                "timestamp": datetime.now().isoformat(),
                "duration_seconds": round(total_duration, 2),
                "fix_status": "error",
                "fix_duration": round(total_duration, 2),
                "patch_size": 0,
                "error_message": str(e),
                "analysis": None,
                "artifact_dir": str(artifact_dir),
            }

        finally:
            dm.safe_cleanup(container)


def _run_official_eval_single(
    dm: DockerManager,
    container: Any,
    instance: dict[str, Any],
    patch_content: str,
    artifact_dir: Path,
    timeout: float = EVAL_TIMEOUT,
) -> dict[str, Any]:
    """Run official SWE-bench Verified eval for a single instance.

    Uses the swebench evaluation harness which runs the instance in its own
    Docker container with proper test dependencies (pytest, etc).

    Args:
        dm: DockerManager instance.
        container: Fix container (for reference only).
        instance: SWE-bench Verified instance dict.
        patch_content: Model-generated patch content.
        artifact_dir: Directory for storing artifacts.
        timeout: Eval timeout in seconds.

    Returns:
        Result dict with status, test_passed, resolved, duration, etc.
    """
    instance_id = instance.get("instance_id", "unknown")
    start_time = time.time()

    # Ensure eval repo is available
    try:
        eval_repo = ensure_eval_repo()
    except RuntimeError as e:
        logger.error(f"Failed to setup eval repo: {e}")
        return {
            "status": "eval_setup_failed",
            "test_passed": False,
            "resolved": False,
            "duration": round(time.time() - start_time, 2),
            "error_message": str(e),
        }

    # Write predictions.jsonl for this single instance
    preds_dir = BASE_DIR / "eval_preds"
    preds_dir.mkdir(parents=True, exist_ok=True)
    preds_file = preds_dir / "predictions.jsonl"
    preds_file.write_text(
        json.dumps({
            "instance_id": instance_id,
            "model_name_or_path": "tau",
            "model_patch": patch_content,
        }) + "\n"
    )

    # Build eval command and extract run_id
    cmd = _build_eval_cmd(
        dataset="SWE-bench/SWE-bench_Verified",
        split="test",
        platform="linux",
        patch_dir=str(preds_file),
        output_dir=str(preds_dir / "eval_output"),
        workers=1,
        overwrite=True,
        instance_ids=[instance_id],
    )

    # Extract run_id from environment variable set by _build_eval_cmd
    run_id = os.environ.get("SWE_EVAL_RUN_ID")

    logger.info(f"Running official eval for {instance_id} (run_id={run_id}): {' '.join(cmd[:8])}...")
    try:
        result = subprocess.run(
            cmd, cwd=eval_repo, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "eval_timeout",
            "test_passed": False,
            "resolved": False,
            "duration": round(time.time() - start_time, 2),
            "error_message": f"Eval timed out after {timeout}s",
        }
    except Exception as e:
        return {
            "status": "eval_error",
            "test_passed": False,
            "resolved": False,
            "duration": round(time.time() - start_time, 2),
            "error_message": str(e),
        }

    # Parse results from the new SWE-bench v4.x format
    # Summary report: <model_name>.<run_id>.json in eval_repo
    # Per-instance report: logs/run_evaluation/<run_id>/<model_name>/<instance_id>/report.json
    if run_id:
        # Try summary report first (always created)
        summary_file = eval_repo / f"tau.{run_id}.json"
        if summary_file.exists():
            try:
                with open(summary_file) as f:
                    summary = json.load(f)
                resolved_ids = summary.get("resolved_ids", [])
                error_ids = summary.get("error_ids", [])
                if instance_id in resolved_ids:
                    return {
                        "status": "eval_passed",
                        "test_passed": True,
                        "resolved": True,
                        "duration": round(time.time() - start_time, 2),
                        "eval_results": summary,
                    }
                elif instance_id in error_ids:
                    return {
                        "status": "eval_failed",
                        "test_passed": False,
                        "resolved": False,
                        "duration": round(time.time() - start_time, 2),
                        "eval_results": summary,
                        "error_message": "Instance evaluation error",
                    }
                else:
                    # Instance not in any list - incomplete
                    return {
                        "status": "eval_failed",
                        "test_passed": False,
                        "resolved": False,
                        "duration": round(time.time() - start_time, 2),
                        "eval_results": summary,
                        "error_message": "Instance not completed",
                    }
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse summary report: {e}")

        # Try per-instance report
        log_base = eval_repo / "logs" / "run_evaluation" / run_id
        if log_base.exists():
            for model_dir in sorted(log_base.iterdir()):
                if not model_dir.is_dir():
                    continue
                instance_dir = model_dir / instance_id
                report_file = instance_dir / "report.json"
                if report_file.exists():
                    try:
                        with open(report_file) as f:
                            report = json.load(f)
                        instance_report = report.get(instance_id, {})
                        resolved = instance_report.get("resolved", False)
                        return {
                            "status": "eval_passed" if resolved else "eval_failed",
                            "test_passed": resolved,
                            "resolved": resolved,
                            "duration": round(time.time() - start_time, 2),
                            "eval_results": instance_report,
                        }
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.error(f"Failed to parse report.json: {e}")

    # Fallback: check for any *.<run_id>.json summary reports in eval_repo
    if run_id:
        for summary_candidate in eval_repo.glob(f"*.{run_id}.json"):
            try:
                with open(summary_candidate) as f:
                    summary = json.load(f)
                resolved_ids = summary.get("resolved_ids", [])
                error_ids = summary.get("error_ids", [])
                if instance_id in resolved_ids:
                    return {
                        "status": "eval_passed",
                        "test_passed": True,
                        "resolved": True,
                        "duration": round(time.time() - start_time, 2),
                        "eval_results": summary,
                    }
                elif instance_id in error_ids:
                    return {
                        "status": "eval_failed",
                        "test_passed": False,
                        "resolved": False,
                        "duration": round(time.time() - start_time, 2),
                        "eval_results": summary,
                        "error_message": "Instance evaluation error",
                    }
            except (json.JSONDecodeError, KeyError):
                continue

    logger.warning(f"No results file found for {instance_id}")
    if result.stderr:
        logger.warning(f"Eval stderr: {result.stderr[-500:]}")
    return {
        "status": "eval_no_results",
        "test_passed": False,
        "resolved": False,
        "duration": round(time.time() - start_time, 2),
        "error_message": "No results file generated",
    }


def _copy_analysis_files_to_container(container, artifact_dir: Path) -> dict[str, str]:
    """Copy analysis-relevant files from host artifacts into the container.

    Copies all logs and artifacts to /tmp/analysis/ in the container so the
    analysis agent can read full files instead of truncated excerpts.

    Returns dict mapping logical names to container paths.
    """
    paths: dict[str, str] = {}
    # Create /tmp/analysis/ directory
    container.exec_run(["mkdir", "-p", "/tmp/analysis"])

    # Copy fix phase logs
    fix_dir = artifact_dir / "fix"
    for fname in ["stdout.log", "stderr.log"]:
        fpath = fix_dir / fname
        if fpath.exists():
            content = fpath.read_bytes()
            _write_tar_to_container(container, content, f"/tmp/analysis/fix_{fname}")
            paths[f"fix_{fname}"] = f"/tmp/analysis/fix_{fname}"

    # Copy fix phase audit/context logs
    audit_dir = fix_dir / "logs"
    if audit_dir.exists():
        for audit_file in sorted(audit_dir.glob("*.audit")):
            content = audit_file.read_bytes()
            _write_tar_to_container(container, content, "/tmp/analysis/fix_audit.log")
            paths["fix_audit"] = "/tmp/analysis/fix_audit.log"
            break
        for ctx_file in sorted(audit_dir.glob("*.context")):
            content = ctx_file.read_bytes()
            _write_tar_to_container(container, content, "/tmp/analysis/fix_context.log")
            paths["fix_context"] = "/tmp/analysis/fix_context.log"
            break

    # Copy ISSUE.md
    issue_md = audit_dir / "ISSUE.md" if audit_dir.exists() else None
    if issue_md and issue_md.exists():
        content = issue_md.read_bytes()
        _write_tar_to_container(container, content, "/tmp/analysis/ISSUE.md")
        paths["issue_md"] = "/tmp/analysis/ISSUE.md"

    # Copy patch
    patch_path = artifact_dir / "patches" / "patch.diff"
    if patch_path.exists():
        content = patch_path.read_bytes()
        _write_tar_to_container(container, content, "/tmp/analysis/patch.diff")
        paths["patch"] = "/tmp/analysis/patch.diff"

    # Copy eval logs
    eval_dir = artifact_dir / "eval"
    for fname in ["testlog.out", "stdout.log", "stderr.log", "resolution.json"]:
        fpath = eval_dir / fname
        if fpath.exists():
            content = fpath.read_bytes()
            _write_tar_to_container(container, content, f"/tmp/analysis/eval_{fname}")
            paths[f"eval_{fname}"] = f"/tmp/analysis/eval_{fname}"

    return paths


def _run_patch_failure_analysis(
    dm: DockerManager,
    container: Any,
    instance: dict[str, Any],
    fix_status: str,
    artifact_dir: Path,
    llm_group: Optional[str] = None,
    stream: bool = False,
    analysis_timeout: float = ANALYSIS_TIMEOUT,
    python_path: str | None = None,
) -> dict[str, Any]:
    """Run Tau analysis when the agent failed to produce a valid patch.

    Copies full fix-phase logs into the container so the analysis agent
    can read complete data. Runs in the SAME container.

    Artifacts written to artifact_dir/analysis/:
      - stdout.log, stderr.log  (from run_tau)
      - analysis.txt            (the failure analysis report)
    """
    instance_id = instance.get("instance_id", "unknown")
    start_time = time.time()

    # Copy all analysis files into container
    file_paths = _copy_analysis_files_to_container(container, artifact_dir)

    # Build analysis prompt — single-shot, reference full files via paths
    analysis_path = "/testbed/ANALYSIS.txt"

    # List available files for the agent
    available_files = "\n".join(f"- {v}" for v in file_paths.values())

    analysis_prompt = f"""<CODEBASE>{instance.get('repo', '')}</CODEBASE><WORKINGDIRECTORY>{TESTBED_PATH}</WORKINGDIRECTORY>

PATCH FAILURE ANALYSIS — write your report to {analysis_path}

The agent attempted to fix this issue but FAILED to produce a valid patch.
Read the full log files below to analyze WHY it failed.

AVAILABLE LOG FILES (read them with file_read or cat):
{available_files}

INSTRUCTIONS:
- Read the logs to understand what the agent did
- Analyze WHY the agent failed to produce a valid patch
- Look for: errors, incorrect tool usage, wrong file paths, incomplete changes
- Check if the agent understood the issue correctly
- Identify the ROOT CAUSE of the failure
- Suggest HOW a future attempt could succeed
- Be CONCISE: 15-30 lines max
- Use file_write to create {analysis_path} with your analysis

Write {analysis_path} now.
"""

    # Run Tau for analysis
    logger.info(f"Running patch failure analysis for {instance_id} (timeout={analysis_timeout}s)...")
    returncode, stdout, tau_duration = dm.run_tau(
        container, [analysis_prompt], artifact_dir, llm_group, analysis_timeout, phase="analysis", stream=stream,
        python_path=python_path
    )

    # Extract analysis report from container
    analysis_text = ""
    try:
        result = container.exec_run(["cat", analysis_path], demux=True)
        if result.exit_code == 0 and result.output[0]:
            analysis_text = result.output[0].decode("utf-8", errors="replace")
            logger.info(f"Analysis file extracted: {len(analysis_text)} chars")
        else:
            logger.warning(f"Analysis file read failed (exit={result.exit_code})")
    except Exception as e:
        logger.warning(f"Could not extract analysis file: {e}")

    # Fallback: use stdout if file is empty/missing
    if not analysis_text.strip():
        analysis_text = stdout[:2000] if stdout else ""

    duration = time.time() - start_time
    logger.info(f"Patch failure analysis complete in {duration:.1f}s ({len(analysis_text)} chars)")

    return {
        "duration_seconds": round(duration, 2),
        "tau_duration": round(tau_duration, 2),
        "analysis_text": analysis_text,
        "returncode": returncode,
    }


def _write_patch_to_container(container, patch_content: str, patch_name: str) -> None:
    """Write a patch file to the container via tar stream.

    Args:
        container: Docker container object.
        patch_content: Patch content as a string.
        patch_name: Filename inside the container (e.g., 'model.patch').
    """
    _write_tar_to_container(container, patch_content.encode("utf-8"), f"/testbed/{patch_name}")


def _run_failure_analysis(
    dm: DockerManager,
    container: Any,
    instance: dict[str, Any],
    patch_content: str,
    eval_result: dict[str, Any],
    artifact_dir: Path,
    llm_group: Optional[str] = None,
    stream: bool = False,
    analysis_timeout: float = ANALYSIS_TIMEOUT,
    python_path: str | None = None,
) -> dict[str, Any]:
    """Run Tau failure analysis in a SEPARATE eval container.

    When official eval fails, we start a new container from the same image,
    copy Tau + fix-phase logs + eval results into it, and run analysis.

    This ensures the eval container has pytest + all test dependencies
    (unlike the fix container which is a minimal testbed).

    Artifacts written to artifact_dir/analysis/:
      - stdout.log, stderr.log  (from run_tau)
      - analysis.txt            (the failure analysis report)
    """
    instance_id = instance.get("instance_id", "unknown")
    start_time = time.time()
    image_name = dm.find_image(instance_id, skip_disk_check=True)

    # Start a new eval container for analysis
    eval_container = dm.start_container(
        image_name, name=f"swe-eval-analysis-{instance_id}", skip_disk_check=True
    )

    try:
        # Copy Tau into the eval container
        copy_tau_to_container(eval_container)

        # Copy fix-phase logs into the eval container
        file_paths = _copy_analysis_files_to_container(eval_container, artifact_dir)

        # Build analysis prompt — single-shot, reference full files via paths
        analysis_path = "/testbed/ANALYSIS.txt"

        # List available files for the agent
        available_files = "\n".join(f"- {v}" for v in file_paths.values())

        analysis_prompt = f"""<CODEBASE>{instance.get('repo', '')}</CODEBASE><WORKINGDIRECTORY>{TESTBED_PATH}</WORKINGDIRECTORY>

EVAL FAILURE ANALYSIS — write your report to {analysis_path}

A fix was attempted but official evaluation FAILED. Read the full log files below to analyze WHY.

AVAILABLE LOG FILES (read them with file_read or cat):
{available_files}

INSTRUCTIONS:
- Read the fix logs to understand what the agent did
- Read the eval logs to understand what tests failed
- Analyze WHY the patch didn't pass evaluation
- Be CONCISE: 10-20 lines max, focus on root cause
- Use file_write to create {analysis_path} with your analysis
- Cover: what failed, why the patch didn't work, how to fix it

Write {analysis_path} now.
"""

        # Find Python in the eval container
        eval_python = dm.find_testbed_python(eval_container)
        logger.info(f"Running eval failure analysis for {instance_id} (timeout={analysis_timeout}s)...")
        returncode, stdout, tau_duration = dm.run_tau(
            eval_container, [analysis_prompt], artifact_dir, llm_group, analysis_timeout, phase="analysis", stream=stream,
            python_path=eval_python
        )

        # Extract analysis report from container
        analysis_text = ""
        try:
            result = eval_container.exec_run(["cat", analysis_path], demux=True)
            if result.exit_code == 0 and result.output[0]:
                analysis_text = result.output[0].decode("utf-8", errors="replace")
                logger.info(f"Analysis file extracted: {len(analysis_text)} chars")
            else:
                logger.warning(f"Analysis file read failed (exit={result.exit_code})")
        except Exception as e:
            logger.warning(f"Could not extract analysis file: {e}")

        # Fallback: use stdout if file is empty/missing
        if not analysis_text.strip():
            logger.info("Analysis file empty/missing, using stdout")
            lines = stdout.strip().split("\n")
            content_lines = []
            in_analysis = False
            for line in lines:
                if "FAILURE ANALYSIS" in line or "ROOT CAUSE:" in line:
                    in_analysis = True
                if in_analysis:
                    if not line.startswith("[REASON]") and not line.startswith("[ASSISTANT]") and not line.startswith("[TOOL]"):
                        content_lines.append(line)
            if content_lines:
                analysis_text = "\n".join(content_lines)
            else:
                analysis_text = f"[No analysis captured. Tau exit code: {returncode}]"

        # Save analysis report to artifact_dir/analysis/analysis.txt
        analysis_dir = artifact_dir / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        analysis_file = analysis_dir / "analysis.txt"
        analysis_file.write_text(analysis_text)

        result = {
            "instance_id": instance_id,
            "analysis_duration": round(time.time() - start_time, 2),
            "tau_duration": tau_duration,
            "report": analysis_text[:2000],
        }

        logger.info(f"Eval analysis complete in {result['analysis_duration']:.1f}s ({len(analysis_text)} chars)")
        return result

    finally:
        dm.safe_cleanup(eval_container)