#!/usr/bin/env python3
"""Caveman style helper - text analysis and style checking."""

import re
from typing import Optional


def check_style(text: str) -> dict:
    """Check text for caveman style violations."""
    violations = {
        "articles": re.findall(r'\b(a|an|the)\b', text, re.IGNORECASE),
        "fillers": re.findall(r'\b(just|really|basically|actually|simply|merely)\b', text, re.IGNORECASE),
        "pleasantries": re.findall(r'\b(hello|hi|hey|thanks|thank you|please|sorry|apologies)\b', text, re.IGNORECASE),
        "uncertainty": re.findall(r'\b(I think|it seems|note that|perhaps|maybe|possibly)\b', text, re.IGNORECASE),
    }
    
    total_violations = sum(len(v) for v in violations.values())
    return {
        "violations": violations,
        "total": total_violations,
        "density": total_violations / max(len(text.split()), 1) * 100,
    }


def make_concise(text: str) -> str:
    """Make text more concise (basic transformations)."""
    # Remove common articles
    text = re.sub(r'\b(a|an|the)\s+', '', text, flags=re.IGNORECASE)
    # Remove fillers
    text = re.sub(r'\b(just|really|basically|actually|simply|merely)\s+', '', text, flags=re.IGNORECASE)
    # Remove pleasantries
    text = re.sub(r'\b(hello|hi|hey|thanks|thank you|please|sorry|apologies)\b', '', text, flags=re.IGNORECASE)
    # Clean up extra spaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            text = f.read()
        result = check_style(text)
        print(f"Violations: {result['total']}")
        print(f"Density: {result['density']:.2f}%")
    else:
        print("Usage: python3 style_check.py <file.txt>")
