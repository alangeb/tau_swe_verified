---
name: background
description: Run commands in background — tmux sessions, parallel tasks, long-running processes, monitor output, wait for completion. (also load: tmux_monitoring, test-suite-monitor, context_management, shell_scripting, error-recovery)
category: development
---

# background

## When
"background tasks", "tmux sessions", "monitor processes", "run in background", "parallel execution"

## Tools
| Tool | Purpose |
|------|---------|
| `background_new` | Create session (default: bash) |
| `background_ls` | List active agent sessions |
| `background_kill` | Kill session or all agent sessions |
| `background_exec` | Execute command in session |
| `background_capture` | Capture pane output with scrollback |
| `background_tail` | Show last N lines from output |
| `background_send_keys` | Send keystrokes without execution |
| `background_wait` | **Wait for session with idle/keyword detection (PREFERRED over `bash sleep`)** |

## `background_wait` — Smart Waiting for Background Tasks

**Use this INSTEAD of `bash sleep 180` or manual polling loops.** It automatically detects when a background task:
- Has hung (idle detection)
- Has produced expected output (keyword matching)
- Has exceeded maximum wait time (timeout)

### Parameters
| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `session_name` | Yes | — | tmux session (must start with `tmux-agent-`) |
| `max_seconds` | Yes | — | Maximum wait time in seconds |
| `idle_seconds` | Yes | — | Return early if no output for this many seconds |
| `keywords` | No | (empty) | Regex pattern to match (e.g., `"error\|warning\|done"`) |
| `tail_lines` | No | 30 | Lines of output to return |
| `poll_interval` | No | 1 | Seconds between output checks |

### Examples
```python
# Wait up to 5min, return early on idle (30s) or if "error|complete" seen
background_wait(session_name="tmux-agent-build", max_seconds=300, idle_seconds=30, keywords="error|complete")

# Wait for test suite, detect hangs after 60s idle
background_wait(session_name="tmux-agent-tests", max_seconds=1800, idle_seconds=60, keywords="FAILED|PASSED|ERROR", tail_lines=50)

# Simple timeout wait with idle detection
background_wait(session_name="tmux-agent-deploy", max_seconds=600, idle_seconds=45)
```

### Return Values
- `KEYWORD MATCH:` — keywords found in output, includes matching output
- `IDLE:` — no output for `idle_seconds`, likely hung, includes last output
- `TIMEOUT:` — max time reached, includes last output
- `SESSION DEAD:` — session disappeared, includes last known output

### When to Use
- **Always prefer** over `bash sleep N` for monitoring background tasks
- Use when you need to detect hangs, errors, or completion automatically
- Use when you want to avoid manual polling loops with repeated `background_tail` calls

## Key Encodings for send_keys
| Key | Syntax |
|-----|--------|
| ESC | `\033` |
| Enter | `C-m` |
| Ctrl+X | `C-x` |
| Ctrl+A | `C-a` |
| Ctrl+E | `C-e` |

**Common patterns:**
- vim save+quit: `\033:wq\r`
- nano save+quit: `C-o C-m C-x`
- Cancel: `C-c`

## Gotchas
- Sessions have independent working dirs — use absolute paths or explicit `cd`
- Don't chain commands with `&&` — use separate calls
- Session names auto-generated with `tmux-agent-` prefix
- `send_keys` = interactive input (no C-m), `exec` = execute command (adds C-m)
- `scrollback >= 30` for useful history
- Timeout: 10 seconds internally

## Polling
- `sleep 120` minimum between checks for most tasks
- Watch for session termination ("can't find pane") or completion messages

## Related Skills
- `tmux_monitoring` — polling best practices
- `test-suite-monitor` — complete test monitoring workflow
- `context_management` — when to use fork/subagent/background
- `shell_scripting` — commands run in background
- `python_debugging` — interactive debug sessions
- `tau_testsuite` — background test execution
- `signal-cli` — daemon management
