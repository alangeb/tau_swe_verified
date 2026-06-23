#!/usr/bin/env python3
"""_taudoc helper - documentation structure validation."""

import os
from pathlib import Path


def validate_structure(src_path: str = ".") -> dict:
    """Validate Tau documentation structure."""
    required_files = [
        "AGENT.md",
        "TAU.md",
        "README.md",
        "designs/INDEX.md",
        "designs/ARCHITECTURE.md",
        "designs/DECISIONS.md",
        "designs/CONTEXT.md",
        "designs/COMMANDS.md",
        "designs/SKILLS.md",
        "designs/TESTING.md",
        "designs/TOOLS.md",
    ]
    
    issues = []
    for file in required_files:
        path = Path(src_path) / file
        if not path.exists():
            issues.append(f"Missing: {file}")
    
    return {
        "issues": issues,
        "status": "valid" if not issues else "invalid",
    }


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "."
    result = validate_structure(src)
    print(f"Status: {result['status']}")
    if result['issues']:
        print("Issues:")
        for issue in result['issues']:
            print(f"  - {issue}")
