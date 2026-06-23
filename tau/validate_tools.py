#!/usr/bin/env python3
"""Validate tools/ directory conventions.

Ensures that every .py file in tools/ (excluding tools/lib/) conforms to
the ToolModule protocol.  Uses package-relative imports (same as runtime)
to avoid import errors from missing dependencies.

Exit codes:
    0 — All tools valid
    1 — Validation failures found
"""

import importlib
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent / "tools"


def main() -> int:
    errors: list[str] = []

    # Find all .py files in tools/ (not in tools/lib/, not __init__, not _*)
    tool_files = sorted(
        f for f in TOOLS_DIR.glob("*.py")
        if f.parent == TOOLS_DIR and not f.name.startswith("_")
    )

    for f in tool_files:
        stem = f.stem
        mod_name = f"tools.{stem}"

        try:
            mod = importlib.import_module(mod_name)
        except ImportError as e:
            errors.append(f"{f.name}: import failed: {e}")
            continue

        # Validate ToolModule protocol
        for attr in ("name", "description", "run"):
            if not hasattr(mod, attr):
                errors.append(f"{f.name}: missing '{attr}'")
            elif attr == "run" and not callable(getattr(mod, attr)):
                errors.append(f"{f.name}: 'run' is not callable")

        if not hasattr(mod, "Args"):
            errors.append(f"{f.name}: missing 'Args' dataclass")

    if errors:
        print(f"tools/ validation failed ({len(errors)} issues):")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    print(f"tools/ validation passed — {len(tool_files)} files checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
