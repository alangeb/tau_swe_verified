---
name: error-recovery
description: Handle tool errors, API failures, session recovery, context overflow (also load: bug_investigation, tau_audit, context_management)
category: resilience
---

# Error Recovery

## When
"tool error", "API failure", "session crashed", "context full", "recover session"

## Error Types
| Type | Pattern | Recovery |
|------|---------|----------|
| Tool error | `TOOL_ERROR` | Retry with corrected params |
| API failure | `Connection refused` | Wait, retry with backoff |
| Context overflow | `TOOL_BLOCKED` | Compress, delegate, clear |
| Session crash | Missing output | Restart from last checkpoint |

## Recovery Patterns
- **Tool error**: Check params, retry with correction
- **API failure**: Exponential backoff (1s, 2s, 4s, 8s)
- **Context overflow**: `context_management` — fork/subagent delegation
- **Session crash**: Check audit log for last state, resume

## Checklist
- [ ] Error type identified
- [ ] Root cause determined
- [ ] Recovery strategy selected
- [ ] Retry with correction
- [ ] Verify recovery success

## Related Skills
- `bug_investigation` — systematic error analysis
- `tau_audit` — analyze error patterns in logs
- `context_management` — handle context overflow
- `background` — recover background sessions
