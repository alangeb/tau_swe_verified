#!/usr/bin/env python3
"""graphify pipeline helper — resolves interpreter, runs detect/extract/build."""
from __future__ import annotations
import json, sys, os, glob
from pathlib import Path
from datetime import datetime, timezone


def resolve_python() -> str:
    """Find correct Python interpreter for graphify."""
    python = os.environ.get("PYTHON")
    # uv tool
    if not python:
        try:
            import shutil
            if shutil.which("uv"):
                import subprocess
                result = subprocess.run(
                    ["uv", "tool", "run", "graphifyy", "python", "-c",
                     "import sys; print(sys.executable)"],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0:
                    python = result.stdout.strip()
        except Exception:
            pass
    # shebang
    if not python:
        graphify_bin = shutil.which("graphify")
        if graphify_bin:
            shebang = Path(graphify_bin).read_text().splitlines()[0].lstrip("#!")
            if shebang and all(c.isalnum() or c in "/_.-" for c in shebang):
                try:
                    import subprocess
                    subprocess.run([shebang, "-c", "import graphify"],
                                   capture_output=True, check=True, timeout=10)
                    python = shebang
                except Exception:
                    pass
    return python or "python3"


def ensure_installed(python: str) -> None:
    """Install graphifyy if missing."""
    import subprocess
    try:
        subprocess.run([python, "-c", "import graphify"],
                       capture_output=True, check=True, timeout=10)
        return
    except subprocess.CalledProcessError:
        pass
    import shutil
    if shutil.which("uv"):
        subprocess.run(["uv", "tool", "install", "--upgrade", "graphifyy", "-q"],
                       capture_output=True)
    else:
        subprocess.run([python, "-m", "pip", "install", "graphifyy", "-q"],
                       capture_output=True)


def step_detect(path: str) -> dict:
    """Step 2: detect files."""
    from graphify.detect import detect
    result = detect(Path(path))
    return result


def step_ast(code_files: list[Path]) -> dict:
    """Step 3A: AST extraction."""
    from graphify.extract import extract
    result = extract(code_files, cache_root=Path("."))
    return result


def step_build(extraction: dict, directed: bool = False) -> tuple:
    """Step 4: build graph, cluster, analyze."""
    from graphify.build import build_from_json
    from graphify.cluster import cluster, score_all
    from graphify.analyze import god_nodes, surprising_connections, suggest_questions
    from graphify.report import generate
    from graphify.export import to_json

    G = build_from_json(extraction, directed=directed)
    communities = cluster(G)
    cohesion = score_all(G, communities)
    gods = god_nodes(G)
    surprises = surprising_connections(G, communities)
    labels = {cid: "Community " + str(cid) for cid in communities}
    questions = suggest_questions(G, communities, labels)
    tokens = {"input": extraction.get("input_tokens", 0),
              "output": extraction.get("output_tokens", 0)}
    report = generate(G, communities, cohesion, labels, gods, surprises,
                      extraction.get("detection", {}), tokens, ".",
                      suggested_questions=questions)
    return G, communities, labels, report, questions


def save_outputs(report: str, G, communities: dict, analysis: dict) -> None:
    """Save all outputs."""
    out = Path("graphify-out")
    out.mkdir(exist_ok=True)
    (out / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
    from graphify.export import to_json
    to_json(G, communities, str(out / "graph.json"))
    (out / ".graphify_analysis.json").write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")


def update_cost(extract_data: dict, detect_data: dict) -> None:
    """Step 9: update cost tracker."""
    from graphify.detect import save_manifest
    out = Path("graphify-out")
    out.mkdir(exist_ok=True)
    save_manifest(detect_data.get("all_files") or detect_data["files"])
    cost_path = out / "cost.json"
    cost = json.loads(cost_path.read_text(encoding="utf-8")) if cost_path.exists() else {
        "runs": [], "total_input_tokens": 0, "total_output_tokens": 0}
    cost["runs"].append({
        "date": datetime.now(timezone.utc).isoformat(),
        "input_tokens": extract_data.get("input_tokens", 0),
        "output_tokens": extract_data.get("output_tokens", 0),
        "files": detect_data.get("total_files", 0)})
    cost["total_input_tokens"] += extract_data.get("input_tokens", 0)
    cost["total_output_tokens"] += extract_data.get("output_tokens", 0)
    cost_path.write_text(json.dumps(cost, indent=2, ensure_ascii=False), encoding="utf-8")


def cleanup() -> None:
    """Remove temp files."""
    for pattern in [".graphify_detect.json", ".graphify_extract.json",
                    ".graphify_ast.json", ".graphify_semantic.json",
                    ".graphify_analysis.json", ".graphify_chunk_*.json",
                    ".needs_update"]:
        for f in glob.glob(f"graphify-out/{pattern}"):
            Path(f).unlink(missing_ok=True)
