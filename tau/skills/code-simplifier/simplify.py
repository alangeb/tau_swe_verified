#!/usr/bin/env python3
"""Code simplifier helper - analyze and suggest code simplifications."""

import ast
import sys
from typing import Optional


def analyze_complexity(source: str) -> dict:
    """Analyze code complexity and suggest simplifications."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {"error": str(e)}
    
    results = {
        "nesting_depth": 0,
        "line_lengths": [],
        "function_lengths": [],
        "complex_patterns": [],
    }
    
    for node in ast.walk(tree):
        # Check nesting depth
        if isinstance(node, (ast.If, ast.For, ast.While, ast.With)):
            results["nesting_depth"] = max(results["nesting_depth"], _get_nesting_depth(node))
        
        # Check function lengths
        if isinstance(node, ast.FunctionDef):
            start = node.lineno
            end = node.end_lineno if hasattr(node, 'end_lineno') else start
            results["function_lengths"].append((node.name, end - start))
    
    return results


def _get_nesting_depth(node: ast.AST, depth: int = 0) -> int:
    """Get maximum nesting depth of a node."""
    max_depth = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.With)):
            max_depth = max(max_depth, _get_nesting_depth(child, depth + 1))
        else:
            max_depth = max(max_depth, _get_nesting_depth(child, depth))
    return max_depth


if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            source = f.read()
        result = analyze_complexity(source)
        print(f"Nesting depth: {result['nesting_depth']}")
        print(f"Function lengths: {result['function_lengths']}")
    else:
        print("Usage: python3 simplify.py <file.py>")
