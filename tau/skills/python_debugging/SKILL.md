---
name: python_debugging
description: Debug Python programs with background tools (also load: bug_investigation, code-review-workflow)
category: development
---

# Python Debugging

## When
"debug python", "interactive debugging", "breakpoint", "pdb session", "trace execution"

## Basic Debug Session
```python
session_name = background_new(command="bash")
background_exec(session_name=session_name, command="cd $HOME/tau/src", wait=True)
background_exec(session_name=session_name, command="python3 -i script.py", wait=False)
# Interact with running process
background_send_keys(session_name=session_name, text="breakpoint()\n")
background_send_keys(session_name=session_name, text="result = function(data)\n")
background_capture(session_name=session_name, scrollback=50)
background_kill(session_name=session_name)
```

## Test Script Pattern
```python
#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
from module import function_to_test
test_data = [...]
breakpoint()
result = function_to_test(test_data)
print(f"Result: {result}")
```

## Common Patterns

### Lost Return Values
```python
# Check if function returns value
python3 -c "import inspect; from module import func; print(inspect.signature(func))"
# Trace usage
grep -n 'func(' code.py | grep -v 'def func'
grep -B1 -A1 'result = func(' code.py
```

### Thread Target Detection
```bash
grep -n 'threading.Thread(target=' *.py
grep -n 'target=function_name' *.py
```

### Callback Detection
```bash
grep -n '= function_name' *.py | grep -v 'def'
grep -n 'on_.*=' *.py
```

## Key Files
| Task | Command |
|------|---------|
| View execution log | `cat ~/.local/tau/log/*.audit` |
| Check errors | `grep "TOOL_ERROR" ~/.local/tau/log/*.audit` |
| View context usage | `grep "TURN_END" ~/.local/tau/log/*.audit` |

## Checklist
1. `background_new()` → 2. `cd` to correct dir → 3. Set up Python path → 4. Run with `breakpoint()` or `pdb` → 5. Inspect interactively → 6. `background_capture()` → 7. `background_kill()`

## Related Skills
- `bug_investigation` — systematic bug investigation workflow
- `code-review-workflow` — automated code analysis
- `background` — tmux session management
