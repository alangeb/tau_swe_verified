---
name: web-research
description: Web research workflow — search, lookup, fetch, crawl for information gathering (also load: agent-browser, shell_scripting)
category: research
---

# Web Research

## When
"search web", "look up", "research topic", "find information", "web search"

## Tool Sequence
```
search(query="...")              # DuckDuckGo search
lookup(query="...")             # Wikipedia/DDG instant answer
fetch(url="...")                 # Extract page content
crawl(url="...", depth=2)        # Multi-page crawl
```

## Patterns
- **Fact lookup**: `lookup` → fast, structured results
- **Deep research**: `search` → `fetch` → `crawl`
- **Browser automation**: `agent-browser` for interactive tasks
- **Batch research**: `search` → extract URLs → `fetch` multiple

## Related Skills
- `agent-browser` — browser automation for interactive research
- `shell_scripting` — automate research workflows
- `tau_audit` — analyze research patterns
- `graphify` — build knowledge graphs from research
