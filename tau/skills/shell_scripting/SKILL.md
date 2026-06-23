---
name: shell_scripting
description: Common bash patterns — piping, text processing, file ops, process management (also load: background, signal-cli, agent-browser, reference, web-research, performance, search-replace, file-ops)
category: development
---

# Shell Scripting

## When
"bash pattern", "pipe commands", "text processing", "shell one-liner", "find and process"

## Text Processing
```bash
# Search and extract
grep -rn "pattern" . | grep -v "binary" | head -20
grep -oP "regex" file.txt
grep -c "pattern" file.txt                          # Count matches

# Transform
sed 's/old/new/g' file.txt                           # Replace
awk '{print $1, $3}' file.txt                        # Select columns
cut -d',' -f1 file.txt                               # CSV column

# Sort and unique
sort | uniq -c | sort -rn                            # Frequency count
sort -u                                               # Unique lines
```

## File Operations
```bash
# Find files
find . -name "*.py" -type f                           # By name
find . -name "*.py" -mtime -1                        # Modified in 1 day
find . -name "*.py" -size +100k                      # Large files

# Batch operations
find . -name "*.bak" -delete                          # Cleanup
find . -name "*.py" -exec grep "TODO" {} \;           # Search in found files

# Quick stats
wc -l *.py                                              # Line count
du -sh .                                                 # Directory size
```

## Process Management
```bash
# Background with PID
command &
PID=$!
kill $PID                                                # Stop
wait $PID                                                # Wait for finish

# Timeout
timeout 60 command                                       # Kill after 60s

# Check if running
ps -p $PID > /dev/null 2>&1 && echo "running"
```

## Gotchas
- **Quoting**: Use `'single'` for literal, `"double"` for expansion
- **Pipefail**: `set -o pipefail` to catch errors in pipes
- **Globbing**: `shopt -s nullglob` to handle empty globs
- **Exit codes**: Check `$?` after commands

## Related Skills
- `background` — tmux session management
- `grep` — pattern searching
- `agent-browser` — browser automation via shell
- `command_template` — shell-based commands
- `signal-cli` — signal messaging automation
