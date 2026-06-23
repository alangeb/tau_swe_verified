---
name: performance
description: Performance profiling, benchmarking, optimization patterns (also load: bug_investigation, code-review-workflow)
category: development
---

# Performance

## When
"slow code", "performance issue", "profile", "benchmark", "optimize"

## Profiling
```python
# cProfile for function-level profiling
python3 -m cProfile -s cumtime script.py

# Time specific sections
import time
start = time.perf_counter()
# ... code ...
print(f"Duration: {time.perf_counter() - start:.3f}s")
```

## Benchmarking
```python
import timeit
timeit.timeit('function()', globals=globals(), number=1000)
```

## Common Patterns
- **Bottleneck detection**: Profile → identify slow functions → optimize
- **Memory profiling**: `tracemalloc` for memory allocation tracking
- **I/O optimization**: Buffering, batching, async I/O
- **Algorithm optimization**: Better data structures, caching

## Checklist
- [ ] Profiled to identify bottlenecks
- [ ] Measured before/after optimization
- [ ] No regression in functionality
- [ ] Documented performance characteristics

## Related Skills
- `bug_investigation` — investigate performance issues
- `code-review-workflow` — review code for performance
- `python_best_practices` — follow performance best practices
- `shell_scripting` — system-level performance monitoring
