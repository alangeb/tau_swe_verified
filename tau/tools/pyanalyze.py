"""Analyze Python code for unused functions and imports."""

from __future__ import annotations

from tools import ToolMetadata

import ast
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ────────────────────────────────────────────────

metadata = ToolMetadata(
    name="pyanalyze",
    description=(
        "Analyze Python code for unused functions and imports. "
        "Use alongside pyscan for complete analysis (pyscan=structure, pyanalyze=usage). "
        "Cannot detect callbacks or thread targets — verify with grep before removing code."
    ),
    max_size=131072,
)

# ── Args schema ──────────────────────────────────────────────────

@dataclass
class Args:
    path: str = field(metadata={"description": "File or directory to analyze"})
    check_unused: bool = field(default=True, metadata={"description": "Check for unused functions"})
    check_imports: bool = field(default=True, metadata={"description": "Check for unused imports"})
    verify_findings: bool = field(default=True, metadata={"description": "Verify findings with grep"})
    output_format: str = field(default="markdown", metadata={"description": "Output format (markdown/json)"})



# ── AST helpers ──────────────────────────────────────────────────

def _collect_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            obj = node
            while isinstance(obj, ast.Attribute):
                obj = obj.value
            if isinstance(obj, ast.Name):
                names.add(obj.id)
    return names


# ── Formatting ───────────────────────────────────────────────────

def _format_markdown(results: dict[str, list | str]) -> str:
    output = [
        "# Code Analysis Report\n",
        f"**Path**: {results['path']}\n",
    ]

    if results["unused_functions"]:
        output.append("\n## ⚠️ Potentially Unused Functions\n")
        for func in results["unused_functions"]:
            output.append(f"- **{func['file']}:{func['line']}** - `{func['function']}`")
            if func.get("note"):
                output.append(f"  - Note: {func['note']}")
    else:
        output.append("\n## ✅ No Unused Functions Found\n")

    if results["unused_imports"]:
        output.append("\n## ⚠️ Potentially Unused Imports\n")
        for imp in results["unused_imports"]:
            output.append(f"- **{imp['file']}:{imp['line']}** - `{imp['import']}`")
            if imp.get("note"):
                output.append(f"  - Note: {imp['note']}")
    else:
        output.append("\n## ✅ No Unused Imports Found\n")

    if results["warnings"]:
        output.append("\n## ⚠️ Warnings\n")
        for warning in results["warnings"]:
            output.append(f"- {warning}")

    if results["recommendations"]:
        output.append("\n## 💡 Recommendations\n")
        for rec in results["recommendations"]:
            output.append(f"- {rec}")

    output.extend([
        "\n## 📝 Limitations\n",
        "- Thread targets, callbacks, and higher-order function usage are not detected",
        "- Manual verification recommended for all findings",
    ])

    return "\n".join(output)


# ── Execution ────────────────────────────────────────────────────

def run(
    path: str,
    check_unused: bool = True,
    check_imports: bool = True,
    verify_findings: bool = True,
    output_format: str = "markdown",
    agent: TauBot = None,
    tool_call_id: str | None = None,
) -> str:
    target_path = Path(path)
    if not target_path.exists():
        return f"ERROR: Path not found: {path}"

    results: dict[str, list | str] = {
        "path": str(target_path),
        "unused_functions": [],
        "unused_imports": [],
        "warnings": [],
        "recommendations": [],
    }

    files = [target_path] if target_path.is_file() else list(target_path.glob("*.py"))

    # Collect all function defs and calls
    all_functions: dict[tuple[str, str], int] = {}
    all_calls: set[str] = set()

    for pyfile in files:
        try:
            tree = ast.parse(pyfile.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    all_functions[(pyfile.name, node.name)] = node.lineno
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        all_calls.add(node.func.id)
                    elif isinstance(node.func, ast.Attribute):
                        all_calls.add(node.func.attr)
        except Exception as e:
            results["warnings"].append(f"Error parsing {pyfile}: {e}")

    if check_unused:
        for (filename, funcname), lineno in sorted(all_functions.items()):
            if funcname.startswith("_") and not funcname.startswith("__"):
                continue
            if funcname not in all_calls:
                is_thread_target = False
                if verify_findings:
                    grep = subprocess.run(
                        ["grep", "-n", f"target={funcname}", str(target_path)],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        start_new_session=True,
                    )
                    is_thread_target = grep.returncode == 0 and funcname in grep.stdout
                if not is_thread_target:
                    note = None
                    if funcname.startswith(("call_", "handler")):
                        note = "May be used as thread target - verify manually"
                    results["unused_functions"].append({
                        "file": filename,
                        "function": funcname,
                        "line": lineno,
                        "note": note,
                    })

    if check_imports:
        for pyfile in files:
            try:
                tree = ast.parse(pyfile.read_text())
                names_used = _collect_names(tree)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            name = alias.asname or alias.name
                            if name not in names_used and name != "annotations":
                                results["unused_imports"].append({
                                    "file": pyfile.name,
                                    "import": name,
                                    "line": node.lineno,
                                    "note": "Verify if needed for type hints or future use",
                                })
                    elif isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            name = alias.asname or alias.name
                            if name not in names_used and name != "annotations":
                                results["unused_imports"].append({
                                    "file": pyfile.name,
                                    "import": f"from {node.module} import {name}",
                                    "line": node.lineno,
                                    "note": "Verify if needed for type hints or future use",
                                })
            except Exception as e:
                results["warnings"].append(f"Error analyzing imports in {pyfile}: {e}")

    if results["unused_functions"]:
        results["recommendations"].append(
            "Review unused functions - verify they're not used as thread targets or callbacks before removing"
        )
    if results["unused_imports"]:
        results["recommendations"].append(
            "Remove unused imports to clean up codebase, but keep 'from __future__ import annotations'"
        )

    if output_format == "json":
        return json.dumps(results, indent=2)
    return _format_markdown(results)
