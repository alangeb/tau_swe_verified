#!/usr/bin/env python3
"""
SWE-bench Verified configuration constants.

Centralized configuration for all SWE-bench Verified adapter modules.
All paths, timeouts, and Docker settings are defined here so they can be
changed in one place rather than scattered across multiple files.
"""
from pathlib import Path

# ─── Dataset ────────────────────────────────────────────────────────────────
# Hugging Face dataset for SWE-bench Verified.
DATASET_NAME = "SWE-bench/SWE-bench_Verified"
DATASET_SPLIT = "test"

# ─── Paths ──────────────────────────────────────────────────────────────────
# Project root — auto-detected from this file's location
BASE_DIR = Path(__file__).resolve().parent

# Results tracking files
RESULTS_FILE = BASE_DIR / "results.jsonl"
REPORT_FILE = BASE_DIR / "report.json"

# Per-instance artifacts (patches, logs, meta.json)
ARTIFACTS_DIR = BASE_DIR / "artifacts"

# Live status file (updated during execution)
STATUS_FILE = BASE_DIR / "status.json"

# SWE-bench Verified submission format
PREDICTIONS_FILE = BASE_DIR / "predictions.jsonl"

# Tau agent source directory (locked copy inside project root)
TAU_DIR = BASE_DIR / "tau"

# ─── Docker ─────────────────────────────────────────────────────────────────
# Container network mode — "host" allows LLM API access from inside container
CONTAINER_NETWORK = "host"

# Paths inside the container
TAU_CONTAINER_PATH = "/tau"
TESTBED_PATH = "/testbed"
TESTBED_PYTHON = "/usr/bin/python3"
TESTBED_BIN = "/opt/miniconda3/envs/testbed/bin"
TAU_LOG_PATH = ".local/tau/log"

# SWE-bench Verified image naming convention:
#   swebench/sweb.eval.x86_64.{instance_id with __ → _1776_}:latest
# Example: astropy__astropy-12907 → swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest
IMAGE_NAMESPACE = "swebench"
IMAGE_PREFIX = "sweb.eval"
IMAGE_ARCH = "x86_64"

# ─── Timeouts (seconds) ────────────────────────────────────────────────────
TAU_TIMEOUT = 1800       # 30 min — fix phase (Tau agent execution)
EVAL_TIMEOUT = 300        # 5 min — evaluation (test execution)
ANALYSIS_TIMEOUT = 600    # 10 min — failure analysis
DEFAULT_TIMEOUT = 1800

# ─── Model ──────────────────────────────────────────────────────────────────
MODEL_NAME = "tau-agent"
AGENT_NAME = "tau-agent"
DEFAULT_LLM_GROUP = "cuda"

# ─── Disk Space ─────────────────────────────────────────────────────────────
# Minimum free disk space (GB) before aborting. Docker images + containers
# can consume 2-5GB per instance. We abort early to avoid OOM/corruption.
DISK_MIN_FREE_GB = 10
# Warn if free space drops below this threshold
DISK_WARN_FREE_GB = 30


# SWE-bench Verified eval repo ────────────────────────────────────────────────
# Clone this to run official evaluation
SWE_BENCH_REPO = "https://github.com/princeton-nlp/SWE-bench"
SWE_BENCH_DIR = BASE_DIR / "SWE-bench"


def artifact_dir_name(instance_index: int, instance_id: str) -> str:
    """Return numbered artifact directory name: '{index}_{instance_id}'."""
    return f"{instance_index}_{instance_id}"


def instance_id_from_dir(dir_name: str) -> str:
    """Strip numeric prefix from artifact directory name.

    '3_reflex-dev__reflex-4129' → 'reflex-dev__reflex-4129'
    'reflex-dev__reflex-4129'   → 'reflex-dev__reflex-4129'  (no-op)
    """
    # Strip leading digits and underscore: "3_reflex-dev__reflex-4129"
    if "_" in dir_name:
        prefix, rest = dir_name.split("_", 1)
        if prefix.isdigit():
            return rest
    return dir_name
