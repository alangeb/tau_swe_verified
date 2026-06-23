"""Phantom tool call detection — fuzzy detection of tool-call-like XML tags
that were not extracted by postparse.

Detects patterns like ``<bash_command>git add ...</bash_command>`` that resemble
tool calls but don't match any known postparse pattern. When detected, raises
``InvalidReplyError`` to trigger a retry (consuming from the same retry budget).
After retries are exhausted, phantoms are stripped silently from content.

Zero external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Rule file loading ──────────────────────────────────────────────────────

_DEFAULT_RULES_PATH = Path(__file__).parent / "phantom_rules.json"


def _load_rules(path: Path | None = None) -> PhantomRules:
    """Load phantom detection rules from JSON file. Falls back to defaults."""
    rules_path = path or _DEFAULT_RULES_PATH
    try:
        data = json.loads(rules_path.read_text())
        return PhantomRules(**data)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return PhantomRules()  # Defaults


# ── Data structures ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PhantomRules:
    """Configuration for phantom tool call detection."""

    enabled: bool = True
    confidence_threshold: float = 0.6
    levenshtein_threshold: int = 2
    suffix_patterns: list[str] = field(default_factory=lambda: [
        "_command", "_tool", "_call", "_exec", "_run",
    ])
    prefix_patterns: list[str] = field(default_factory=lambda: [
        "cmd_", "tool_", "exec_", "run_",
    ])
    command_keywords: list[str] = field(default_factory=lambda: [
        "git", "rm", "mv", "cp", "chmod", "mkdir", "ls", "cat",
        "find", "grep", "pip", "apt", "npm", "install", "clone",
        "commit", "push", "pull", "checkout", "merge",
    ])
    whitelist_tags: list[str] = field(default_factory=lambda: [
        # Common HTML/XML tags that are NOT tool calls
        "code", "pre", "div", "span", "table", "ul", "ol", "li",
        "p", "br", "hr", "img", "a", "b", "i", "em", "strong",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "cite",
        "abbr", "kbd", "var", "samp", "sub", "sup", "del", "ins",
        "mark", "small", "big", "u", "s", "details", "summary",
        "figure", "figcaption", "main", "header", "footer", "nav",
        "section", "article", "aside", "form", "input", "label",
        "select", "button", "textarea", "script", "style", "meta",
        "link", "head", "body", "html", "title",
    ])

    def __post_init__(self) -> None:
        # Compile patterns for performance
        object.__setattr__(self, "_suffix_re", re.compile(
            r"(?:%s)$" % "|".join(re.escape(s) for s in self.suffix_patterns)
        ))
        object.__setattr__(self, "_prefix_re", re.compile(
            r"^(?:%s)" % "|".join(re.escape(p) for p in self.prefix_patterns)
        ))
        object.__setattr__(self, "_whitelist", frozenset(self.whitelist_tags))
        object.__setattr__(self, "_keywords", frozenset(
            k.lower() for k in self.command_keywords
        ))


@dataclass(frozen=True)
class PhantomMatch:
    """A detected phantom tool call."""

    tag_name: str
    original_text: str
    confidence: float
    reasons: list[str]


# ── Detection ──────────────────────────────────────────────────────────────

# Matches XML-like tag pairs: <tagname>...</tagname>
# Also matches self-closing: <tagname ... />
# Two separate patterns for clarity and correctness.
_XML_PAIRED_RE = re.compile(
    r"<([a-zA-Z_][a-zA-Z0-9_-]*)"  # opening tag name (group 1)
    r"[^>]*>"                        # attributes + >
    r"(.*?)"                         # content (group 2, non-greedy)
    r"</\1>",                         # closing tag (backref to group 1)
    re.DOTALL,
)
_XML_SELF_CLOSING_RE = re.compile(
    r"<([a-zA-Z_][a-zA-Z0-9_-]*)[^>]*/>"  # self-closing <tag ... />
)


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein distance between two strings. Stdlib only."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = range(len(b) + 1)
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = i + 1 if ca != cb else prev[j]
            curr.append(min(cost, curr[j], prev[j + 1]) + 0)
        prev = curr
    return prev[-1]


def _score_phantom(
    tag_name: str,
    body: str,
    rules: PhantomRules,
    known_tools: frozenset[str] | None = None,
) -> tuple[float, list[str]]:
    """Score how likely a tag is a phantom tool call.

    Returns (confidence, reasons). Confidence 0.0–1.0.
    """
    score = 0.0
    reasons: list[str] = []

    # Whitelist check — skip known HTML/XML tags
    if tag_name in rules._whitelist:
        return 0.0, []

    # Suffix patterns: *_command, *_tool, *_call, etc.
    if rules._suffix_re.search(tag_name):
        score += 0.6
        reasons.append(f"suffix '{tag_name}'")

    # Prefix patterns: cmd_*, tool_*, exec_*, etc.
    if rules._prefix_re.search(tag_name):
        score += 0.6
        reasons.append(f"prefix '{tag_name}'")

    # Command keywords in body
    body_lower = body.lower()
    for kw in rules._keywords:
        if kw in body_lower:
            score += 0.6
            reasons.append(f"keyword '{kw}' in body")
            break  # One keyword hit is enough

    # Proximity to known tool names
    if known_tools:
        for tool in known_tools:
            dist = _levenshtein(tag_name.lower(), tool.lower())
            if dist <= rules.levenshtein_threshold:
                score += 0.6
                reasons.append(f"close to tool '{tool}' (dist={dist})")
                break
            # Substring containment
            if tool.lower() in tag_name.lower() or tag_name.lower() in tool.lower():
                score += 0.5
                reasons.append(f"contains tool '{tool}'")
                break

    return min(score, 1.0), reasons


def detect_phantoms(
    content: str,
    reasoning: str | None,
    rules: PhantomRules,
    known_tools: frozenset[str] | None = None,
) -> list[PhantomMatch]:
    """Detect phantom tool calls in content and/or reasoning.

    Scans both content and reasoning for XML-like tags that resemble tool calls
    but were not extracted by postparse. Returns all matches above the confidence
    threshold.
    """
    if not rules.enabled:
        return []

    matches: list[PhantomMatch] = []
    for text in [content] + ([reasoning] if reasoning else []):
        # Scan paired tags: <tag>...</tag>
        for match in _XML_PAIRED_RE.finditer(text):
            tag_name = match.group(1)
            body = match.group(2)
            original = match.group(0)
            confidence, reasons = _score_phantom(tag_name, body, rules, known_tools)
            if confidence >= rules.confidence_threshold:
                matches.append(PhantomMatch(tag_name, original, confidence, reasons))

        # Scan self-closing tags: <tag ... />
        for match in _XML_SELF_CLOSING_RE.finditer(text):
            tag_name = match.group(1)
            original = match.group(0)
            confidence, reasons = _score_phantom(tag_name, "", rules, known_tools)
            if confidence >= rules.confidence_threshold:
                matches.append(PhantomMatch(tag_name, original, confidence, reasons))

    return matches


def strip_phantoms(
    content: str,
    reasoning: str | None,
    phantoms: list[PhantomMatch],
) -> tuple[str, str | None]:
    """Strip all detected phantoms from content and reasoning.

    Replaces phantom XML tags with empty string. The LLM must NOT see the
    original phantom patterns — they would serve as bad examples and cause
    repetition.
    """
    for phantom in phantoms:
        content = content.replace(phantom.original_text, "")
        if reasoning:
            reasoning = reasoning.replace(phantom.original_text, "")

    return content.strip(), (reasoning.strip() if reasoning else None)
