# SWE-bench Verified — TauErgon Benchmark

[📄 License](LICENSE) · [📋 Disclaimer](DISCLAIMER.md) · [🔒 Security](SECURITY.md)

## ⚠️ Work in Progress

This benchmark is **work in progress**. I am not affiliated with academia and cannot submit to the official SWE-bench leaderboard. This run is not a perfect 1→500 straight pass — there were iterative tweaks, prompt improvements, and framework fixes along the way. The goal is to make the pipeline as "clean" and reproducible as possible, documenting everything transparently.

## Overview

This repository contains the complete SWE-bench Verified benchmark run using **TauErgon** as the fix engine. All 500 instances were processed through a Docker-based pipeline: TauErgon runs inside isolated containers to generate patches, which are then evaluated against the official SWE-bench test suite.

## Results Summary

| Metric | Value |
|---|---|
| Total instances | 500 |
| Resolved | **333 (66.6%)** |
| Eval failed | 160 |
| Patch created (no eval) | 5 |
| Patch failed | 2 |

### By Repository

| Repo | Total | Resolved | Rate |
|---|---|---|---|
| scikit-learn | 32 | 27 | 84.4% |
| pydata | 22 | 17 | 77.3% |
| pytest-dev | 19 | 14 | 73.7% |
| django | 231 | 159 | 68.8% |
| sympy | 75 | 49 | 65.3% |
| sphinx-doc | 44 | 28 | 63.6% |
| matplotlib | 34 | 20 | 58.8% |
| astropy | 22 | 11 | 50.0% |
| mwaskom | 2 | 1 | 50.0% |
| psf | 8 | 3 | 37.5% |
| pylint-dev | 10 | 3 | 30.0% |
| pallets | 1 | 1 | 100.0% |

### Detailed Results

Run `python3 status.py` for the full per-instance report (status, duration, patch size).

```
python3 status.py          # Full report
python3 status.py summary  # One-line summary
python3 status.py by_repo  # Grouped by repository
```

## Hardware & Software

### Hardware
- **GPU**: NVIDIA RTX 5090
- **OS**: Debian 13 (Trixie)
- **CUDA**: `cuda_13.3.0_610.43.02_linux.run`

### Model

| Setting | Value |
|---|---|
| Model | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` |
| Quantization | NVFP4 (modelopt) |
| Engine | vLLM v0.22.1 |
| Served name | `model` |
| Context | 200,000 tokens |
| GPU memory | 95% utilization |
| KV cache | fp8 |
| Speculative decoding | Qwen3.5 MTP, 3 tokens |
| Chat template | froggeric (`chat_template.jinja`) |

### Docker Compose

The exact model serving configuration is included in this repo:
```
docker_vllm_qwen36_27b_nvfp4_mtp-froggeric_v0.22.1/docker-compose.yaml
```

Start the model server:
```bash
cd docker_vllm_qwen36_27b_nvfp4_mtp-froggeric_v0.22.1
docker compose up -d
```

Key parameters: `--max-model-len 200000`, `--gpu-memory-utilization 0.95`, `--max-num-seqs 2`, `--kv-cache-dtype fp8`, `--calculate-kv-scales`, speculative config `{"method":"qwen3_5_mtp","num_speculative_tokens":3}`.

## Architecture

```
swe_adapter.py  -- Main orchestrator (CLI entry point)
  ├── swe_combined.py  -- Combined fix + eval workflow
  │   ├── swe_fix.py     -- Prompt building, container setup, TauErgon execution
  │   └── swe_docker.py  -- Docker container lifecycle
  └── swe_eval.py  -- Official evaluation, predictions generation
```

### Pipeline

1. **Fix Phase** — TauErgon runs inside a Docker container with a 5-phase prompt (LOCALIZE → ROOT CAUSE → PLAN → IMPLEMENT → REVIEW). It edits source files; the framework generates the patch.
2. **Eval Phase** — Patch is applied in a fresh container and tests are run via the official SWE-bench harness.
3. **Results** — `results.jsonl` captures all outcomes. `artifacts/` stores per-instance patches, logs, and metadata.

### Fix Prompt

The fix prompt in `swe_fix.py` uses a single-shot 5-phase workflow with `/delegate` and `fork`:

1. **LOCALIZE** — Find relevant files/functions (pyscan, grep, glob)
2. **ROOT CAUSE** — Trace the defect, expand edge cases
3. **PLAN** — Define minimal fix strategy with success criteria
4. **IMPLEMENT** — Apply changes, generate patch
5. **REVIEW** — Verify fix, loop back if unsatisfied

**Prompts must stay generic** — no issue-specific hardcoding. The LLM discovers relevant code from the issue itself.

## Reproduction

### Prerequisites

- Docker (for containerized fix + eval)
- NVIDIA GPU with CUDA 13.3+ (RTX 5090 tested)
- vLLM serving the model at `http://localhost:8000` (or your LLM endpoint)
- Python 3.12+

### Setup

```bash
# Clone this repo
git clone <this-repo>
cd swe

# Start the model server
cd docker_vllm_qwen36_27b_nvfp4_mtp-froggeric_v0.22.1
docker compose up -d
cd ..

# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Running

```bash
# Single instance
python swe_adapter.py --instance-id astropy__astropy-12907 --llm cuda

# Batch (N instances sequentially)
python swe_adapter.py --count 10 --llm cuda

# Resume from last processed
python swe_adapter.py --resume --count 1 --llm cuda

# Retry failed instances
python swe_adapter.py --retry --count 5 --llm cuda

# Check Docker images before running
python swe_adapter.py --check-images

# Skip evaluation (faster, just generate patches)
python swe_adapter.py --instance-id astropy__astropy-12907 --no-eval --llm cuda

# Generate predictions.jsonl for submission
python swe_adapter.py --generate-preds

# Run official SWE-bench evaluation
python swe_adapter.py --eval --eval-workers 4

# Gold patch validation (3x)
python swe_adapter.py --gold-validation --eval-workers 4
```

### Convenience Wrapper

```bash
./run.sh single <instance_id>    # Single instance
./run.sh batch <count>           # N instances
./run.sh resume <count>          # Resume
./run.sh retry [count]           # Retry failed
./run.sh check-images            # Check images
./run.sh eval [workers]          # Official eval
```

### Autonomous Improvement Loop

`evolve.sh` runs an infinite loop:
1. Pick an untested instance, run it, diagnose failures
2. Pick a previously-failed instance, investigate artifacts, improve `swe_fix.py` prompts, re-run

```bash
./evolve.sh
```

## File Layout

| Path | Description |
|---|---|
| `swe_adapter.py` | Main CLI orchestrator |
| `swe_combined.py` | Combined fix + eval workflow |
| `swe_docker.py` | Docker container management |
| `swe_eval.py` | Official evaluation bridge |
| `swe_fix.py` | Fix workflow + prompt building |
| `config.py` | Centralized configuration |
| `status.py` | Status reporter |
| `run.sh` | Convenience wrapper |
| `evolve.sh` | Autonomous improvement loop |
| `tau/` | TauErgon source (reproduction) |
| `artifacts/` | Per-instance patches, logs, metadata |
| `docker_vllm_qwen36_27b_nvfp4_mtp-froggeric_v0.22.1/` | Model serving config (docker-compose) |
| `requirements.txt` | Python dependencies |
| `results.jsonl` | All 500 results |

## Notes

- **Sequential execution only** — parallel runs saturate GPU memory and degrade quality.
- Each instance takes 1–30 minutes (fix + eval).
- `SWE-bench/` and `venv/` are auto-generated (gitignored).
- `artifacts/` is ~535MB (patches, logs, meta.json per instance).
- Docker images are pulled on first run (`--check-images` to verify).
