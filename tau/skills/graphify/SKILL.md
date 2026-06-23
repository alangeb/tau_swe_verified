---
name: graphify
description: "Turn codebases into persistent knowledge graphs — community detection, query/path/explain. Codebase architecture, file relationships, project content analysis." (also load: bug_investigation, web-research)
category: analysis
---

# /graphify

## When
"codebase graph", "knowledge graph", "graphify", "code architecture", "file relationships", "project analysis"

## Usage
```
/graphify                                             # current dir
/graphify <path>                                      # specific path
/graphify https://github.com/<owner>/<repo>           # clone + build
/graphify <path> --mode deep                          # richer INFERRED edges
/graphify <path> --update                             # incremental
/graphify <path> --directed                           # preserves edge direction
/graphify <path> --cluster-only                       # rerun clustering
/graphify <path> --no-viz                             # skip HTML
/graphify <path> --svg --graphml --neo4j --mcp        # export formats
/graphify <path> --obsidian --obsidian-dir <path>      # Obsidian vault
/graphify <path> --watch --wiki                       # auto-rebuild, wiki
/graphify query "<question>"                          # BFS query
/graphify query "<question>" --dfs --budget 1500      # DFS, token cap
/graphify path "A" "B"                               # shortest path
/graphify explain "Node"                              # plain explanation
/graphify add <url> [--author "Name"]                  # fetch URL
```

## Fast Path
`graphify-out/graph.json` exists + natural-language question → **skip pipeline, run `graphify query "<question>"` directly.**
No path → `.`. GitHub URL → clone first.

## Pipeline
Run helper: `python3 skills/graphify/pipeline.py <path> [flags...]`

Or manual steps:

### Step 0 — GitHub
See `references/github-and-merge.md`. Clone, resolve local path.

### Step 1 — Install
Resolve Python interpreter (uv tool → shebang → python3). Install `graphifyy` if missing. Save to `graphify-out/.graphify_python`.

### Step 2 — Detect
`graphify.detect.detect(Path('INPUT_PATH'))` → `graphify-out/.graphify_detect.json`.
- `total_files=0` → stop
- `>2M words` or `>500 files` → show top 5 subdirs, ask to narrow
- Video → `references/transcribe.md`

### Step 3 — Extract (parallel AST + semantic)
Note `--mode deep` → `DEEP_MODE=true` to subagents.
Check `GEMINI_API_KEY`/`GOOGLE_API_KEY` → use `graphify.llm.extract_corpus_parallel(backend="gemini")`.
No keys → dispatch subagents (host = LLM). **No other API keys read.**

**Part A** — AST: `graphify.extract.extract(code_files)` → `graphify-out/.graphify_ast.json`
**Part B** — Semantic: **MANDATORY subagent tool** (5-10x faster). Check cache, split 20-25/chunk, dispatch ALL in one message. Prompt: `references/extraction-spec.md`. Merge → `graphify-out/.graphify_semantic.json`.
**Part C** — Merge AST + semantic → `graphify-out/.graphify_extract.json`.

### Step 4 — Build Graph
`build_from_json(extraction, directed=True)` → cluster, score, analyze → `GRAPH_REPORT.md`, `graph.json`, `.graphify_analysis.json`.

### Step 5 — Label Communities
Read analysis JSON. Write 2-5 word names. Regenerate report → `.graphify_labels.json`.

### Step 6 — Export
`graphify export html` (always, unless `--no-viz`). `graphify export obsidian` (if `--obsidian`).
See `references/exports.md` for `--wiki`, `--neo4j`, `--svg`, `--graphml`, `--mcp`.

### Step 9 — Cleanup
Save manifest, update cost tracker, remove temp files. Report outputs.

## Subcommands
- **Query**: `graphify query "<question>"` — see `references/query.md`
- **Update/Cluster**: see `references/update.md`
- **Add/Watch**: see `references/add-watch.md`
- **Hooks**: see `references/hooks.md`

## Honesty Rules
- Never invent edge — use AMBIGUOUS if unsure
- Never skip corpus check warning
- Always show token cost
- Never hide cohesion scores — show raw number
- Never run HTML viz on >5,000 nodes without warning

## Related Skills
- `pyscan` — Python project structure
- `pygraph` — cross-file call graphs
- `bug_investigation` — graph-based bug analysis
