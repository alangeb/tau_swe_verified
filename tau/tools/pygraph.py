"""Python knowledge graph queries — cross-file call analysis."""

from __future__ import annotations

from tools import ToolMetadata
from dataclasses import dataclass, field

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Tool metadata ────────────────────────────────────────────────

metadata = ToolMetadata(
    name="pygraph",
    description=(
        "Query cross-file call graphs for Python projects. Builds a knowledge graph "
        "from AST analysis (zero external deps, always fresh) and answers relationship questions.\n\n"
        "Use pygraph for structural queries (who calls what, impact analysis). "
        "Always verify with grep for completeness — pygraph misses dynamic dispatch, "
        "string references, and callback registrations.\n\n"
        "Query types:\n"
        "  callers    — Who calls this symbol? (use: symbol)\n"
        "  callees    — Who does this symbol call? (use: symbol)\n"
        "  path       — Shortest path between two symbols (use: from_symbol, to_symbol)\n"
        "  impact     — What breaks if this changes? Transitive callers. (use: symbol)\n"
        "  god        — Top-N most-called symbols. (use: top)\n"
        "  summary    — Graph overview: node/edge counts, top hubs.\n\n"
        "Callers/callees/impact results are limited to `top` entries (default: 20).\n"
        "Increase `top` to see more results.\n\n"
        "Symbol matching is partial: 'echo' matches 'echo', 'user_echo', etc.\n"
        "Use 'exact_' prefix for exact match: symbol='exact_echo' (recommended for common names like 'run', 'get').\n\n"
        "Examples:\n"
        "  callers(symbol='echo')           — who calls echo?\n"
        "  impact(symbol='authenticate')   — what breaks if auth changes?\n"
        "  path(from='main', to='db')       — shortest path\n"
        "  god(top=5)                       — top 5 hubs\n"
        "  summary()                        — graph overview"
    ),
    max_size=200000,
)


# ── Args ─────────────────────────────────────────────────────────

@dataclass
class Args:
    path: str = field(metadata={"description": "Path to a Python file or directory to analyze"})
    query_type: str = field(default="summary", metadata={"description": "Query type: callers, callees, path, impact, god, summary"})
    symbol: str = field(default="", metadata={"description": "Symbol name for callers/callees/impact queries. Partial match supported. Prefix 'exact_' for exact match."})
    from_symbol: str = field(default="", metadata={"description": "Source symbol for path queries"})
    to_symbol: str = field(default="", metadata={"description": "Target symbol for path queries"})
    top: int = field(default=20, metadata={"description": "Max results for callers/callees/god queries (default: 20)"})
    exclude_dirs: str = field(default="", metadata={"description": "Comma-separated list of additional directory names to exclude"})
    include_tests: bool = field(default=False, metadata={"description": "Include tests directory in analysis"})

def run(
    agent: TauBot,
    tool_call_id: str | None = None,
    path: str = ".",
    query_type: str = "summary",
    symbol: str = "",
    from_symbol: str = "",
    to_symbol: str = "",
    top: int = 10,
    exclude_dirs: str = "",
    include_tests: bool = False,
) -> str:
    """Execute a graph query on a Python project."""
    if not os.path.exists(path):
        return f"Error: Path does not exist: {path}"

    try:
        from tools.graph import build_graph, Graph
    except ImportError:
        return "Error: graph module not available"

    g = build_graph(path, exclude_dirs=exclude_dirs, include_tests=include_tests)

    if query_type == "summary":
        return _summary(g)

    if query_type == "callers":
        return _callers(g, symbol, top)

    if query_type == "callees":
        return _callees(g, symbol, top)

    if query_type == "path":
        return _path(g, from_symbol, to_symbol)

    if query_type == "impact":
        return _impact(g, symbol)

    if query_type == "god":
        return _god(g, top)

    return f"Unknown query_type: '{query_type}'. Use: callers, callees, path, impact, god, summary"


# ── Query implementations ────────────────────────────────────────

def _summary(g: Graph) -> str:
    lines = [
        "## Knowledge Graph Summary",
        f"- **Nodes:** {len(g.nodes)}",
        f"- **Edges:** {len(g.edges)}",
        f"- **Files:** {len({n.file for n in g.nodes})}",
        "",
    ]
    from collections import Counter
    kinds = Counter(n.kind for n in g.nodes)
    lines.append("### Node Types")
    for kind, count in kinds.most_common():
        lines.append(f"- {kind}: {count}")
    lines.append("")
    edge_kinds = Counter(e.kind for e in g.edges)
    lines.append("### Edge Types")
    for kind, count in edge_kinds.most_common():
        lines.append(f"- {kind}: {count}")
    lines.append("")
    god = g.god_nodes(5)
    if god:
        lines.append("### Most Connected (by fan-in)")
        for node_id, count in god:
            node = g.node_by_id(node_id)
            name = node.name if node else node_id
            lines.append(f"- {count} callers ← `{name}` ({node.file if node else '?'})")
        lines.append("")
    return "\n".join(lines)


def _callers(g: Graph, symbol: str, top: int = 20) -> str:
    if not symbol:
        return "Usage: callers(symbol='name')"
    matches = _resolve(g, symbol)
    if not matches:
        return f"No nodes matching '{symbol}'"
    results = []
    for node in matches:
        callers = g.callers(node.id)
        if callers:
            results.append(f"📞 {node.name} ({node.file}:{node.line}) is called by:")
            for c in callers[:top]:
                results.append(f"   ← {c}")
            if len(callers) > top:
                results.append(f"   ... and {len(callers) - top} more")
        else:
            results.append(f"📞 {node.name} ({node.file}:{node.line}) — no callers found")
    return "\n".join(results)


def _callees(g: Graph, symbol: str, top: int = 20) -> str:
    if not symbol:
        return "Usage: callees(symbol='name')"
    matches = _resolve(g, symbol)
    if not matches:
        return f"No nodes matching '{symbol}'"
    results = []
    for node in matches:
        callees = g.callees(node.id)
        if callees:
            results.append(f"→ {node.name} ({node.file}:{node.line}) calls:")
            for c in callees[:top]:
                results.append(f"   → {c}")
            if len(callees) > top:
                results.append(f"   ... and {len(callees) - top} more")
        else:
            results.append(f"→ {node.name} ({node.file}:{node.line}) — no callees found")
    return "\n".join(results)


def _path(g: Graph, from_symbol: str, to_symbol: str) -> str:
    if not from_symbol or not to_symbol:
        return "Usage: path(from='A', to='B')"
    from_matches = _resolve(g, from_symbol)
    to_matches = _resolve(g, to_symbol)
    if not from_matches:
        return f"No nodes matching '{from_symbol}'"
    if not to_matches:
        return f"No nodes matching '{to_symbol}'"
    # Try all combinations, return first path found
    for fn in from_matches:
        for tn in to_matches:
            p = g.shortest_path(fn.id, tn.id)
            if p:
                return f"Path from '{fn.name}' to '{tn.name}' ({len(p)} hops):\n" + "\n→ ".join(p)
    return f"No path found between '{from_symbol}' and '{to_symbol}'"


def _impact(g: Graph, symbol: str) -> str:
    if not symbol:
        return "Usage: impact(symbol='name')"
    matches = _resolve(g, symbol)
    if not matches:
        return f"No nodes matching '{symbol}'"
    results = []
    for node in matches:
        impacted = g.impact(node.id)
        results.append(f"💥 Changing '{node.name}' would affect {len(impacted)} node(s):")
        for i in sorted(impacted)[:30]:
            results.append(f"   ⚠ {i}")
        if len(impacted) > 30:
            results.append(f"   ... and {len(impacted) - 30} more")
    return "\n".join(results)


def _god(g: Graph, top: int) -> str:
    nodes = g.god_nodes(top)
    if not nodes:
        return "No call edges found"
    results = [f"🔥 Top {top} most-called nodes (by fan-in):"]
    for node_id, count in nodes:
        node = g.node_by_id(node_id)
        name = node.name if node else node_id
        results.append(f"   {count} callers ← {name}")
    return "\n".join(results)


def _resolve(g: Graph, symbol: str) -> list:
    """Resolve symbol name, supporting 'exact_' prefix for exact match."""
    if symbol.startswith("exact_"):
        target = symbol[6:]
        return [n for n in g.nodes if n.name == target]
    return g.node_by_name(symbol)
