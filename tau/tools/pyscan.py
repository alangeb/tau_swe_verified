"""Python project analyzer using AST."""

from __future__ import annotations

from tools import ToolMetadata

import ast
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ────────────────────────────────────────────────

metadata = ToolMetadata(
    name="pyscan",
    description=(
        "Analyze Python project structure using AST. Extracts classes, functions, variables, "
        "imports, call hierarchy, and optionally unused code. Excludes .git, venv, __pycache__, etc.\n\n"
        "Always run FIRST on any Python project — gives per-file detail (LOC, symbols, docstrings) "
        "that pygraph cannot provide. Follow up with pygraph for cross-file relationship analysis.\n\n"
        "Use compact=True for concise output (names, return types, line numbers, deps only — ~60% smaller)."
    ),
    max_size=400000,
)

# ── Data models ──────────────────────────────────────────────────

@dataclass
class ProjectStats:
    files: int = 0
    classes: int = 0
    functions: int = 0
    loc: int = 0


@dataclass
class Args:
    path: str = field(metadata={"description": "Path to a Python file or directory to analyze"})
    check_usage: bool = field(default=False, metadata={"description": "Check for potentially unused functions and imports"})
    compact: bool = field(default=False, metadata={"description": "Compact output: names, return types, line numbers, deps only (no full type annotations, docstrings, or import lists)"})
    exclude_dirs: str = field(default="", metadata={"description": "Comma-separated list of additional directory names to exclude (e.g., 'tests,fixtures,examples')"})
    include_tests: bool = field(default=False, metadata={"description": "Include tests directory in analysis (default: excluded)"})


# ── Analyzer ─────────────────────────────────────────────────────

class _AIProjectAnalyzer:
    DEFAULT_EXCLUDE_DIRS = {
        ".git", "venv", "__pycache__", "dist", "build",
        ".idea", ".vscode", "node_modules", ".tox", "tests", "test",
    }

    def __init__(
        self,
        target_path: str,
        exclude_dirs: set[str] | None = None,
        include_tests: bool = False,
    ) -> None:
        self.target_path = os.path.abspath(target_path)
        self.is_single_file = os.path.isfile(self.target_path)
        self.internal_names: set[str] = set()
        self.stats = ProjectStats()
        self.EXCLUDE_DIRS = set(self.DEFAULT_EXCLUDE_DIRS)
        if exclude_dirs:
            self.EXCLUDE_DIRS.update(exclude_dirs)
        if include_tests:
            self.EXCLUDE_DIRS -= {"tests", "test"}

    def _get_type_name(self, node: ast.AST | None, depth: int = 0) -> str:
        if depth > 50 or node is None:
            return "Any"
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{self._get_type_name(node.value, depth + 1)}.{node.attr}"
        if isinstance(node, ast.Subscript):
            inner = node.slice.value if isinstance(node.slice, ast.Index) else node.slice
            return f"{self._get_type_name(node.value, depth + 1)}[{self._get_type_name(inner, depth + 1)}]"
        if isinstance(node, ast.Constant):
            val = repr(node.value)
            return val[:47] + "..." if len(val) > 50 else val
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return f"{self._get_type_name(node.left, depth + 1)} | {self._get_type_name(node.right, depth + 1)}"
        return "Any"

    def _get_docstring_first_line(self, node: ast.AST) -> str:
        docstring = ast.get_docstring(node)
        if not docstring:
            return ""
        stripped = docstring.strip()
        if not stripped:
            return ""
        first_line = stripped.splitlines()[0].strip()
        if not first_line:
            return ""
        return f"  # {first_line[:80]}..." if len(first_line) > 80 else f"  # {first_line}"

    def _collect_internal_names(self) -> None:
        if self.is_single_file:
            self._collect_from_file(self.target_path)
        else:
            for root, dirs, files in os.walk(self.target_path):
                dirs[:] = [d for d in dirs if d not in self.EXCLUDE_DIRS]
                for filename in files:
                    if filename.endswith(".py"):
                        self._collect_from_file(os.path.join(root, filename))

    def _collect_from_file(self, filepath: str) -> None:
        source = self._read_file(filepath)
        if source is None:
            return
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.internal_names.add(node.name)
            elif isinstance(node, ast.ClassDef):
                self.internal_names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.internal_names.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                self.internal_names.add(node.target.id)

    def _read_file(self, filepath: str) -> str | None:
        for encoding in ("utf-8", "latin-1"):
            try:
                with open(filepath, encoding=encoding) as f:
                    return f.read()
            except (OSError, UnicodeDecodeError):
                continue
        return None

    def _get_function_calls(self, node: ast.AST) -> set[str]:
        return {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }

    def _format_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, include_lineno: bool
    ) -> list[str]:
        args: list[str] = []
        for arg in node.args.args:
            annotation = self._get_type_name(arg.annotation) if arg.annotation else ""
            args.append(f"{arg.arg}: {annotation}" if annotation else arg.arg)
        if node.args.vararg:
            annotation = self._get_type_name(node.args.vararg.annotation) if node.args.vararg.annotation else ""
            args.append(f"*{node.args.vararg.arg}: {annotation}" if annotation else f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            annotation = self._get_type_name(node.args.kwarg.annotation) if node.args.kwarg.annotation else ""
            args.append(f"**{node.args.kwarg.arg}: {annotation}" if annotation else f"**{node.args.kwarg.arg}")

        return_annotation = f" -> {self._get_type_name(node.returns)}" if node.returns else ""
        calls = self._get_function_calls(node)
        internal_deps = calls.intersection(self.internal_names)
        doc_str = self._get_docstring_first_line(node)
        lineno_str = f" (L{node.lineno})" if include_lineno else ""

        lines = [f"{node.name}({', '.join(args)}){return_annotation}{doc_str}{lineno_str}"]
        for dep in sorted(internal_deps):
            lines.append(f"      └─ {dep}")
        return lines

    def _format_function_compact(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> str:
        """Compact format: name(arg_names...) -> return_type :line [deps]"""
        arg_names = [arg.arg for arg in node.args.args]
        if node.args.vararg:
            arg_names.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            arg_names.append(f"**{node.args.kwarg.arg}")
        args_str = ", ".join(arg_names) if arg_names else ""
        return_annotation = f" -> {self._get_type_name(node.returns)}" if node.returns else ""
        calls = self._get_function_calls(node)
        internal_deps = calls.intersection(self.internal_names)
        deps_str = f" [{', '.join(sorted(internal_deps))}]" if internal_deps else ""
        return f"{node.name}({args_str}){return_annotation} :{node.lineno}{deps_str}"

    def _format_class(self, node: ast.ClassDef) -> str:
        lines: list[str] = []
        doc_str = self._get_docstring_first_line(node)
        lines.append(f"  - [Class] {node.name}{doc_str} (L{node.lineno})")
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.stats.functions += 1
                method_lines = self._format_function(item, include_lineno=True)
                lines.append(f"    - [Method] {method_lines[0]}")
                lines.extend(method_lines[1:])
        return "\n".join(lines)

    def _format_class_compact(self, node: ast.ClassDef) -> str:
        """Compact format: Class :line [methods...]"""
        methods = []
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.stats.functions += 1
                methods.append(self._format_function_compact(item))
        if methods:
            methods_str = f" [{methods[0]}]"
            if len(methods) > 1:
                for m in methods[1:]:
                    methods_str += f" | {m}"
        else:
            methods_str = ""
        return f"  {node.name} :{node.lineno}{methods_str}"

    def _analyze_file(self, filepath: str, rel_path: str, compact: bool = False) -> list[str]:
        lines: list[str] = []
        source = self._read_file(filepath)
        if source is None:
            return ["  - [Error] Cannot read file", ""]
        self.stats.loc += len(source.splitlines())
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return [f"  - [Error] Syntax error: {e}", ""]

        lines.append(f"### File: `{rel_path}`")

        if not compact:
            imports: list[str] = []
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.Import):
                    imports.extend(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                if not compact:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            lines.append(f"  - [Var] {target.id} (L{node.lineno})")
                else:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            lines.append(f"  {target.id} :{node.lineno}")
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if not compact:
                    type_hint = self._get_type_name(node.annotation) if node.annotation else ""
                    var_line = f"  - [Var] {node.target.id}"
                    if type_hint:
                        var_line += f": {type_hint}"
                    lines.append(f"{var_line} (L{node.lineno})")
                else:
                    lines.append(f"  {node.target.id} :{node.lineno}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.stats.functions += 1
                if compact:
                    lines.append(f"  {self._format_function_compact(node)}")
                else:
                    func_lines = self._format_function(node, include_lineno=True)
                    lines.append(f"  - [Func] {func_lines[0]}")
                    lines.extend(func_lines[1:])
            elif isinstance(node, ast.ClassDef):
                self.stats.classes += 1
                if compact:
                    lines.append(self._format_class_compact(node))
                else:
                    lines.append(self._format_class(node))

        if not compact:
            if imports:
                lines.append(f"  *Imports: {', '.join(sorted(set(imports)))}*")
        lines.append("")
        return lines

    def run(self, compact: bool = False) -> str:
        self._collect_internal_names()
        output: list[str] = []
        excluded_info = (
            f" (excluding: {', '.join(sorted(self.EXCLUDE_DIRS))})"
            if not self.is_single_file else ""
        )
        mode_label = " (compact)" if compact else ""
        output.append(f"# AI Project Index - {os.path.basename(self.target_path)}{excluded_info}{mode_label}")
        output.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        output.append("")

        if self.is_single_file:
            self.stats.files += 1
            output.extend(self._analyze_file(self.target_path, os.path.basename(self.target_path), compact=compact))
        else:
            for root, dirs, files in os.walk(self.target_path):
                dirs[:] = [d for d in dirs if d not in self.EXCLUDE_DIRS]
                for filename in sorted(files):
                    if not filename.endswith(".py"):
                        continue
                    filepath = os.path.join(root, filename)
                    rel_path = os.path.relpath(filepath, self.target_path)
                    self.stats.files += 1
                    output.extend(self._analyze_file(filepath, rel_path, compact=compact))

        output.append("## Project Summary")
        output.append(f"- **Total Files:** {self.stats.files}")
        output.append(f"- **Total Lines of Code:** {self.stats.loc}")
        output.append(f"- **Classes:** {self.stats.classes} / **Functions:** {self.stats.functions}")
        output.extend(["", "---"])
        return "\n".join(output)


# ── Args schema ──────────────────────────────────────────────────


# ── Usage analysis ───────────────────────────────────────────────

def _analyze_usage(path: str) -> str:
    output = [
        "## 🔍 Usage Analysis\n",
        "\n*Note: Functions used as thread targets or callbacks may be flagged as unused but are actually in use.*\n",
    ]

    target_path = Path(path)
    files = [target_path] if target_path.is_file() else list(target_path.glob("*.py"))

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
        except Exception:
            pass

    unused_functions = [
        (fn, fname, ln)
        for (fn, fname), ln in sorted(all_functions.items())
        if not (fname.startswith("_") and not fname.startswith("__"))
        and fname not in all_calls
    ]

    if unused_functions:
        output.append("\n### ⚠️ Potentially Unused Functions\n")
        for filename, funcname, lineno in unused_functions:
            output.append(f"- **{filename}:{lineno}** - `{funcname}`")
            if funcname.startswith(("call_", "handler")):
                output.append(f"  - *May be used as thread target - verify with: `grep -n 'target={funcname}'`*")
    else:
        output.append("\n### ✅ No Unused Functions Found\n")

    output.extend([
        "\n### 📝 Verification Commands\n",
        "```bash",
        "# Verify thread targets",
        "grep -rn 'threading.Thread(target=' .",
        "grep -rn 'target=function_name' .",
        "",
        "# Verify callbacks",
        "grep -rn 'callback=' .",
        "```",
    ])

    return "\n".join(output)


# ── Execution ────────────────────────────────────────────────────

def run(
    agent: TauBot,
    tool_call_id: str | None = None,
    path: str = ".",
    check_usage: bool = False,
    compact: bool = False,
    exclude_dirs: str = "",
    include_tests: bool = False,
) -> str:
    if not os.path.exists(path):
        return f"Error: Path does not exist: {path}"

    exclude_dirs_set = (
        set(d.strip() for d in exclude_dirs.split(",") if d.strip())
        if exclude_dirs else set()
    )
    analyzer = _AIProjectAnalyzer(path, exclude_dirs=exclude_dirs_set, include_tests=include_tests)
    result = analyzer.run(compact=compact)

    if check_usage:
        result += "\n\n" + _analyze_usage(path)
    return result
