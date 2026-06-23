#!/usr/bin/env python3
"""Documentation helper - docstring generation and validation."""

import re
from typing import Optional


def generate_docstring(func_name: str, args: list[str], returns: str = "None") -> str:
    """Generate a Google-style docstring template."""
    lines = [f'def {func_name}({", ".join(args)}):', '    """Brief description.']
    if args:
        lines.append("")
        lines.append("    Args:")
        for arg in args:
            lines.append(f"        {arg}: Description")
    lines.append("")
    lines.append(f"    Returns:")
    lines.append(f"        {returns}")
    lines.append('    """')
    return "\n".join(lines)


def validate_docstring(source: str) -> list[str]:
    """Check if functions have proper docstrings."""
    issues = []
    # Simple regex-based check
    func_pattern = re.compile(r'def\s+(\w+)\s*\([^)]*\)\s*:')
    for match in func_pattern.finditer(source):
        func_name = match.group(1)
        # Check if next line has docstring
        pos = match.end()
        rest = source[pos:].lstrip()
        if not rest.startswith('"""') and not rest.startswith("'''"):
            issues.append(f"{func_name}: missing docstring")
    return issues


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            source = f.read()
        issues = validate_docstring(source)
        if issues:
            print("Missing docstrings:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("All functions have docstrings")
    else:
        print("Usage: python3 doc_helper.py <file.py>")
