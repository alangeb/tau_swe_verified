---
name: agent-browser
description: Browser automation CLI for AI agents — navigate websites, web scraping, fill forms, click elements (also load: bug_investigation, web-research, search-replace)
category: development
---

# agent-browser

## When
"browser automation", "web scraping", "navigate website", "fill form", "click element"

## Workflow
1. `open <url>` → 2. `snapshot -i` → 3. interact via `@refs` → 4. re-snapshot after DOM changes

## Commands
```bash
agent-browser open <url>              # Navigate
agent-browser close                   # Close
agent-browser snapshot -i              # Interactive element refs
agent-browser snapshot -i -C           # + cursor-interactive
agent-browser snapshot -s "selector"  # Scope to CSS selector
agent-browser click @e1               # Click
agent-browser click @e1 --new-tab     # New tab
agent-browser fill @e2 "text"          # Clear + type
agent-browser type @e2 "text"         # Type w/o clearing
agent-browser select @e1 "option"     # Dropdown
agent-browser check @e1               # Checkbox
agent-browser press Enter             # Key press
agent-browser get text @e1            # Element text
agent-browser get url                 # Current URL
agent-browser get title               # Page title
agent-browser wait @e1                # Wait for element
agent-browser wait --load networkidle # Wait network idle
agent-browser wait 2000               # Wait ms
agent-browser eval 'document.title'   # JS eval
agent-browser eval --stdin <<'EOF'   # Complex JS heredoc
```

## JS Extraction
```bash
agent-browser eval --stdin <<'EVALEOF'
JSON.stringify(Array.from(document.querySelectorAll('.item')).map(el => el.textContent.trim()))
EVALEOF
```

## Gotchas
- **Always re-snapshot** after navigation or DOM changes
- Dismiss cookies/modals before interacting
- Use `wait --load networkidle` after `open` for slow pages
- Close browser when done to avoid leaked processes
- Complex JS: use `eval --stdin` with heredoc to avoid shell quoting

## Related Skills
- `fetch` — simpler web content extraction
- `shell_scripting` — automate browser workflows
