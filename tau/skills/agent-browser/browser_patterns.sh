#!/bin/bash
# browser_patterns.sh — Browser automation pattern helpers

# Open and snapshot
browser_open() {
    agent-browser open "$1"
    agent-browser snapshot -i
}

# Click and re-snapshot
browser_click() {
    agent-browser click "@$1"
    agent-browser snapshot -i
}

# Fill and submit
browser_fill_submit() {
    agent-browser fill "@$1" "$2"
    agent-browser press Enter
    agent-browser snapshot -i
}

# Extract text from elements
browser_extract() {
    agent-browser eval --stdin <<EVALEOF
JSON.stringify(Array.from(document.querySelectorAll('$1')).map(el => el.textContent.trim()))
EVALEOF
}
