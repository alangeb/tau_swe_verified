#!/usr/bin/env python3
"""ast-grep pattern library — common AST search/rewrite patterns for Python."""

PATTERNS = {
    # Unused imports
    "unused_import_check": {
        "pattern": "import $X",
        "description": "Find all imports for usage checking",
    },
    "unused_from_import": {
        "pattern": "from $M import $X",
        "description": "Find all from-imports for usage checking",
    },
    # Function calls
    "function_calls": {
        "pattern": "$FUNC($*ARGS)",
        "description": "Find all function calls",
    },
    "method_calls": {
        "pattern": "$OBJ.$METHOD($*ARGS)",
        "description": "Find all method calls",
    },
    # Class definitions
    "class_def": {
        "pattern": "class $NAME:",
        "description": "Find all class definitions",
    },
    "class_with_base": {
        "pattern": "class $NAME($BASE):",
        "description": "Find classes with base classes",
    },
    # Control flow
    "if_else": {
        "pattern": "if $COND:\n    $BODY",
        "description": "Find if statements",
    },
    "try_except": {
        "pattern": "try:\n    $BODY\nexcept $EXC:",
        "description": "Find try/except blocks",
    },
    # Common rewrites
    "optional_call": {
        "pattern": "$A and $A()",
        "rewrite": "$A and $A()",
        "description": "Find 'x and x()' pattern for optional chaining",
    },
    "double_negation": {
        "pattern": "not not $X",
        "rewrite": "$X",
        "description": "Remove double negation",
    },
    "bool_comparison": {
        "pattern": "$X == True",
        "rewrite": "$X",
        "description": "Replace 'x == True' with 'x'",
    },
    "none_comparison": {
        "pattern": "$X == None",
        "rewrite": "$X is None",
        "description": "Replace 'x == None' with 'x is None'",
    },
}

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        name = sys.argv[1]
        if name in PATTERNS:
            p = PATTERNS[name]
            print(f"# {p['description']}")
            print(f"Pattern: {p['pattern']}")
            if "rewrite" in p:
                print(f"Rewrite: {p['rewrite']}")
        else:
            print(f"Unknown pattern: {name}")
            print("Available:", ", ".join(sorted(PATTERNS.keys())))
    else:
        print("AST-GREP Pattern Library")
        print("=" * 40)
        for name, p in sorted(PATTERNS.items()):
            print(f"  {name:30s} {p['description']}")
