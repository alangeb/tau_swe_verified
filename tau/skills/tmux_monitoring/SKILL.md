---
name: tmux_monitoring
description: Best practices for monitoring tmux sessions in background tasks (also load: background, test-suite-monitor)
category: development
---

# Tmux Monitoring

## When
"monitor tmux", "poll background", "check session status", "background monitoring", "session polling"

## Timing
| Task Duration | Poll Interval |
|--------------|---------------|
| < 5s | 30s |
| 5-60s | 60-120s |
| 60-300s | 120-180s |
| > 300s | 300s+ |

**Default**: `sleep 120` for most background tasks.

## Pattern
```bash
background_new(command="your_command")
# Initial check
background_tail {"session_name": "...", "lines": 10}
# Poll loop
while session_active; do
    sleep 120
    background_tail {"session_name": "...", "lines": 10}
done
```

## Completion Detection
- Session not found: "can't find pane" error
- Output shows "Test Suite Completed!" or similar
- Status files updated in output directories

## Anti-Pattern
```bash
# WRONG — too frequent, creates entropy warnings
while true; do
    background_tail {"session_name": "...", "lines": 10}
    sleep 5
done
```

## Related Skills
- `background` — tmux session management
- `test-suite-monitor` — complete test monitoring workflow
