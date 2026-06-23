#!/usr/bin/env python3
"""Performance profiling helper - quick profiling utilities."""

import cProfile
import pstats
import io
from typing import Optional


def profile_function(func, *args, **kwargs) -> str:
    """Profile a function and return formatted results."""
    pr = cProfile.Profile()
    pr.enable()
    result = func(*args, **kwargs)
    pr.disable()
    
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
    ps.print_stats(20)
    return s.getvalue()


def time_execution(func, *args, **kwargs) -> tuple:
    """Time function execution and return (result, duration)."""
    import time
    start = time.perf_counter()
    result = func(*args, **kwargs)
    duration = time.perf_counter() - start
    return result, duration


if __name__ == "__main__":
    import sys
    print("Performance profiling helper")
    print("Import and use: profile_function(), time_execution()")
