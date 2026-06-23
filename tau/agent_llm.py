"""Consolidated LLM invocation module.

Merges agent_llm_constants, agent_llm_postparse, agent_llm_validate,
agent_llm_http, and agent_llm into a single cohesive module.

Sections
--------
1. Constants — shared token/tag delimiters and protocol identifiers
2. Postparse — regex-based tool-call extraction and thought normalization
3. Validation — reply validators and InvalidReplyError
4. HTTP client — stdlib-only OpenAI-compatible API client with cache tracking
5. Data models — CallStats, CacheTracker, LLMCallConfig, LLMResponse
6. Invocation — _prepare_messages, _build_call_kwargs, _invoke_llm_with_retry

CRITICAL: Keep token delimiters obfuscated via constants.
Do not inline raw tag literals or "simplify" this during refactors.
Some model/runtime pathways are sensitive to direct tag tokens in source.
"""

from __future__ import annotations

import copy
import json
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
import urllib.error
import urllib.request

from agent_console import error_display, llm_timeout_message, llm_validation_retry, warning
from agent_model_health import get_health_monitor

if TYPE_CHECKING:
    from agent_context import TauContext


# === CONSTANTS ===

# ---------------------------------------------------------------------------
# Delimiter primitives — build tag strings without inlining raw literals.
# ---------------------------------------------------------------------------
LT = "<"
LT_SLASH = LT + "/"
GT = ">"
LT_PIPE = LT + "|"
PIPE_GT = "|" + GT

# ---------------------------------------------------------------------------
# Shared protocol identifiers
# ---------------------------------------------------------------------------
FUNCTION = "function"
ARGUMENTS = "arguments"
TOOL = "tool"
TOOLCALL = "toolcall"
TOOL_CALL_ALT = "tool_call"
PARAMETER_ALT = "parameter"

# ---------------------------------------------------------------------------
# Anthropic-style tool-use markers (pipe-delimited)
# ---------------------------------------------------------------------------
TOOL_USE = "tool_use"
TOOL_USE_OPEN = f"{LT_PIPE}{TOOL_USE}{PIPE_GT}"
TOOL_USE_CLOSE = f"{LT_PIPE}/{TOOL_USE}{PIPE_GT}"

# ---------------------------------------------------------------------------
# Thought markers
# ---------------------------------------------------------------------------
BEGIN_OF_THOUGHT = f"{LT_PIPE}begin_of_thought{PIPE_GT}"
END_OF_THOUGHT = f"{LT_PIPE}end_of_thought{PIPE_GT}"

# XML thinking tag names
THINKING = "thinking"
THINK = "think"
REASON = "reason"
REASONING = "reasoning"


def _tag_pair(name: str) -> tuple[str, str]:
    """Return (open_tag, close_tag) for an XML-style tag."""
    return (f"{LT}{name}{GT}", f"{LT_SLASH}{name}{GT}")


THINKING_TAG_PAIRS: tuple[tuple[str, str], ...] = (
    _tag_pair(THINKING),
    _tag_pair(THINK),
    _tag_pair(REASON),
    _tag_pair(REASONING),
    (BEGIN_OF_THOUGHT, END_OF_THOUGHT),
)


def _incomplete_tag(name: str) -> tuple[str, str]:
    """Return (open, close) raw-tag strings for incomplete tool-call detection."""
    return (f"{LT}{name}{GT}", f"{LT_SLASH}{name}{GT}")


INCOMPLETE_TOOL_CALL_PATTERNS: tuple[str, ...] = (
    *_incomplete_tag("function_call"),
    *_incomplete_tag("name"),
    *_incomplete_tag(ARGUMENTS),
    *_incomplete_tag("TOOLCALL"),
    *_incomplete_tag(TOOL_CALL_ALT),
    *_incomplete_tag(FUNCTION),
    *_incomplete_tag(PARAMETER_ALT),
    *_incomplete_tag(TOOL),
    TOOL_USE_OPEN,
    TOOL_USE_CLOSE,
)

TOOL_CALL_PATTERNS: tuple[str, ...] = (
    f"{LT}function_call{GT}",
    f"{LT}name{GT}",
    f"{LT}{ARGUMENTS}{GT}",
    f"{LT}{TOOL}{GT}",
)

# === POSTPARSE ===

# ── Regex patterns for tool-call extraction ──────────────────────────────

__function_pattern__ = re.compile(
    rf"<{TOOLCALL}>[ \t\n\r]*<{FUNCTION}=(\w+)>(.+?)</{FUNCTION}>[ \t\n\r]*</{TOOLCALL}>",
    re.DOTALL,
)

__function_alt_pattern__ = re.compile(
    rf"<{TOOL_CALL_ALT}>[ \t\n\r]*<{FUNCTION}=(\w+)>(.+?)</{FUNCTION}>[ \t\n\r]*</{TOOL_CALL_ALT}>",
    re.DOTALL,
)

__parameter_alt_pattern__ = re.compile(
    rf"<{PARAMETER_ALT}=([a-zA-Z_]\w*)>[ \t\n\r]*(.*?)[ \t\n\r]*</{PARAMETER_ALT}>",
    re.DOTALL,
)

__anthropic_tool_pattern__ = re.compile(
    rf"{re.escape(TOOL_USE_OPEN)}[ \t\n\r]*{FUNCTION}=(\w+)>(.+?){re.escape(TOOL_USE_CLOSE)}",
    re.DOTALL,
)

# ── Direct XML-style tool call pattern ─────────────────────────────────────
# Matches: <tool_name attr="value" ...>...</tool_name> or <tool_name attr="value" .../>
# Also handles nested tags: <tool_name><inner attr="value"></inner></tool_name>
__direct_xml_pattern__ = re.compile(
    rf"<(\w+)"  # Opening tag with tool name (group 1)
    rf"(?:\s+[^>]*?)?"  # Optional attributes on the opening tag
    rf"(?:/>"  # Self-closing: <tool_name .../>
    rf"|(?:>(.*?)</\1>)"  # Or: <tool_name>...</tool_name> (group 2 = content)
    rf")",
    re.DOTALL,
)

# Regex to extract attribute="value" pairs from XML tags
__xml_attr_pattern__ = re.compile(r'(\w+)="([^"]*)"')

# ── Block-style tool call pattern (U+2591 LIGHT SHADE + >) ──────────────────
# Matches: ░tool_name\n{json_args}
# The ░> is used by some LLMs as a tool-call delimiter.
__block_delim_pattern__ = re.compile(
    r"\u2591>"  # ░> delimiter
    r"(\w+)"  # Tool name (group 1)
    r"\s*\n"  # Newline
    r"(\{[\s\S]*?\})"  # JSON arguments (group 2)
)

# ── Function-tag tool call pattern ───────────────────────────────────────────
# Matches: <|begin_of_function|>{"name": "X", "arguments": {...}}<|end_of_function|>
# Some LLMs output tool calls wrapped in <|begin_of_function|> / <|end_of_function|> tags.
__function_tag_pattern__ = re.compile(
    r"<\|begin_of_function\|>"  # Opening tag
    r"\s*"  # Optional whitespace
    r"(\{[\s\S]*?\})"  # JSON payload (group 1)
    r"\s*"  # Optional whitespace
    r"<\|end_of_function\|>"  # Closing tag
)

# ── Bash command pattern (flexible) ─────────────────────────────────────────
# Matches: <bash><cmd> "command"</cmd></bash> or <bash><cmd="command" /></bash>
# Handles both attribute-style and content-style bash commands.
__bash_flex_pattern__ = re.compile(
    r"<bash>"  # Opening bash tag
    r"[\s\S]*?"  # Any content (non-greedy)
    r"<cmd"  # Opening cmd tag
    r"(?:\s*=\s*\"([^\"]*)\")?"  # Optional cmd="value" attribute (group 1)
    r"(?:\s*>[\s\S]*?\"([^\"]*)\")?"  # Optional content: > "value" (group 2)
    r"[\s\S]*?"  # Any trailing content
    r"</cmd>"  # Closing cmd tag
    r"[\s\S]*?"  # Any trailing content
    r"</bash>"  # Closing bash tag
)

# ── Builtins-style tool call pattern ────────────────────────────────────────
# Matches: <builtins.tool_name params="{...}">
# Some LLMs output tool calls with a "builtins." prefix and params attribute.
__builtins_pattern__ = re.compile(
    r"<builtins\.(\w+)"  # Opening tag with builtins. prefix, capture tool name (group 1)
    r"\s+params="  # params= attribute
    r"\"(\{[^}]*\})\""  # JSON arguments in quotes (group 2)
    r">"  # Closing >
)

# ── Inline JSON tool call pattern ───────────────────────────────────────────
# Matches: <tool_name>{"key": "value"} (no closing tag, JSON directly after >)
# Some LLMs output tool calls as <tool_name>{JSON} without a closing tag.
__inline_json_pattern__ = re.compile(
    r"<(\w+)>"  # Opening tag with tool name (group 1)
    r"(\{[\s\S]*?\})"  # JSON arguments (group 2)
)

# ── Markdown code block tool call pattern ─────────────────────────────────
# Matches: ```json\n{"tool_name": "...", "arguments": {...}}\n```
__markdown_json_pattern__ = re.compile(
    rf"```(?:json)?\s*\n?"  # Opening ``` with optional 'json' tag and newline
    rf"(\{{[\s\S]*?\"tool_name\"\s*:\s*\"(\w+)\"[\s\S]*?\}})"  # JSON with tool_name (group 2)
    rf"[\s\S]*?```",  # Closing ```
    re.DOTALL,
)

# ── Direct subagent/fork task pattern ──────────────────────────────────────
# Matches: <subagent task="..."> or <fork task="...">
__subagent_task_pattern__ = re.compile(
    rf"<(subagent|fork)\s+task=\"([^\"]+)\"[>]?",
    re.DOTALL,
)

# ── tool_name/args XML pattern ──────────────────────────────────────────────
# Matches: <tool_name="file_write"><args>{...JSON...}</args></tool_name>
# This is a common format the LLM uses for tool calls.
# CRITICAL: Must be tried BEFORE __direct_xml_pattern__ to avoid matching
# the inner <args> tag first (which would extract "args" as the tool name).
__tool_name_args_pattern__ = re.compile(
    r'<tool_name="(\w+)">'  # tool_name="X" (group 1)
    r'[\s\S]*?'  # any content (non-greedy)
    r'<args>'  # opening args tag
    r'[\s\S]*?'  # any content before JSON
    r'(\{[\s\S]*?\})'  # JSON content (group 2)
    r'[\s\S]*?'  # any content after JSON
    r'</args>'  # closing args tag
    r'[\s\S]*?'  # trailing content
    r'</tool_name>',  # closing tag
    re.DOTALL,
)

# ── Bash wrapper pattern ────────────────────────────────────────────────────
# Matches: <bash><cmd="command here" /></bash> or <bash><cmd="command here"></cmd></bash>
# The LLM sometimes wraps shell commands in <bash> tags with a <cmd> attribute.
# CRITICAL: Must be tried BEFORE __direct_xml_pattern__ to avoid matching
# the inner <cmd> tag first (which would extract "cmd" as the tool name).
__bash_wrapper_pattern__ = re.compile(
    r'<bash>'  # Opening bash tag
    r'[\s]*'  # Optional whitespace
    r'<cmd'  # Opening cmd tag
    r'\s*=\s*"([^"]*)"'  # cmd="value" (group 1)
    r'[\s]*(?:/?>'  # Self-closing /> or just >
    r'|[\s]*>(?:[\s\S]*?)</cmd>)'  # Or: >...</cmd>
    r'[\s]*'  # Optional trailing whitespace
    r'</bash>',  # Closing bash tag
    re.DOTALL,
)

# ── Block-style tool call pattern ──────────────────────────────────────────
# Matches: `·tool_name\n  key: "value"\n  key2: "value2"\n)`
# This is the format the LLM sometimes uses for tool calls.
# The `·` is U+2591 LIGHT SHADE, rendered as a shaded box in terminals.
__block_tool_pattern__ = re.compile(
    rf"[·]\s*"  # Block start marker (U+2591 LIGHT SHADE)
    rf"(\w+)"  # Tool name (group 1)
    rf"(\n[ \t]*(?:\w+:\s*\"[^\"]*\"[,\s]*)*)?"  # Arguments as key: "value" pairs (group 2)
    rf"\)",  # Closing paren
    re.DOTALL,
)

__thought_pattern__ = re.compile(
    rf"{re.escape(BEGIN_OF_THOUGHT)}(.*?){re.escape(END_OF_THOUGHT)}",
    re.DOTALL,
)

# Maximum body length to prevent pathological over-consumption
ANTHROPIC_MAX_BODY_LEN = 10000

# ── Tool-call validation ─────────────────────────────────────────────────

def _is_valid_toolcall_params(params_json: str) -> bool:
    """Validate JSON-form tool-call payload shape.

    Accepts any non-empty JSON object.  The function name is already captured
    from the ``<function=NAME>`` tag — the body just needs to be a dict of
    arguments (or a dict with a nested ``"arguments"`` string, which is
    unwrapped by ``_build_toolcall``).
    """
    try:
        params = json.loads(params_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False

    if not isinstance(params, dict):
        return False

    # Accept plain argument dicts (e.g. {"file_path": "test.py"})
    # or wrapped dicts with "arguments" field (e.g. from real LLM output).
    if ARGUMENTS in params:
        try:
            args = json.loads(params[ARGUMENTS])
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
        return isinstance(args, dict)

    return True

# ── Parameter coercion ───────────────────────────────────────────────────

# Data-driven coercion table: (regex_pattern, coerce_fn)
# Order matters — first match wins.
_COERCION_RULES = [
    (re.compile(r"^true$", re.IGNORECASE), lambda _: True),
    (re.compile(r"^false$", re.IGNORECASE), lambda _: False),
    (re.compile(r"^(null|none)$", re.IGNORECASE), lambda _: None),
    (re.compile(r"^-?\d+$"), int),
    (re.compile(r"^-?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?$"), float),
]

def _coerce_parameter_value(value: str):
    """Coerce alternate-tag parameter text into a JSON-compatible value.

    Handles booleans, null, integers, floats, and falls back to trimmed string.
    """
    text = value.strip()
    for pattern, coerce_fn in _COERCION_RULES:
        if pattern.fullmatch(text):
            try:
                return coerce_fn(text)
            except (ValueError, TypeError):
                pass
    return text

def _extract_params_from_alt_function_body(function_body: str) -> dict | None:
    """Extract and coerce `PARAMETER_ALT` tags from an alternate function body.

    Returns None if no parameters are found.
    """
    args: dict = {}
    for match in __parameter_alt_pattern__.finditer(function_body):
        param_name = match.group(1)
        raw_value = match.group(2)
        args[param_name] = _coerce_parameter_value(raw_value)

    if not args:
        return None

    return args


def _extract_params_from_direct_xml(match: re.Match[str]) -> dict | None:
    """Extract arguments from a direct XML-style tool call match.

    Handles: <tool_name attr="value" ...>...</tool_name>
    Also extracts attributes from nested tags within the content.

    Returns None if no arguments are found.
    """
    func_name = match.group(1)
    content = match.group(2) or ""

    # Extract attributes from the full match (opening tag attributes)
    full_match = match.group(0)
    args: dict = {}

    # Get the opening tag portion (everything before content or self-closing)
    open_tag_end = full_match.index(">") if ">" in full_match else len(full_match)
    open_tag = full_match[:open_tag_end]

    # Extract attributes from opening tag
    for attr_match in __xml_attr_pattern__.finditer(open_tag):
        attr_name = attr_match.group(1)
        attr_value = attr_match.group(2)
        # Skip if attribute name is the same as the tag name
        if attr_name != func_name:
            args[attr_name] = _coerce_parameter_value(attr_value)

    # Extract attributes from nested tags in content
    if content:
        for attr_match in __xml_attr_pattern__.finditer(content):
            attr_name = attr_match.group(1)
            attr_value = attr_match.group(2)
            args[attr_name] = _coerce_parameter_value(attr_value)

    if not args:
        return None

    return args

# ── Tool-call builders ───────────────────────────────────────────────────

def _build_toolcall(func_name: str, args_source: str | dict) -> dict:
    """Build a canonical tool-call dict.

    ``args_source`` may be a raw JSON string or a pre-parsed dict.
    """
    if isinstance(args_source, dict):
        args_json = json.dumps(args_source)
    else:
        args_json = args_source  # already validated JSON string
        params = json.loads(args_json)
        if ARGUMENTS in params:
            args_json = params[ARGUMENTS]

    result = {
        "id": f"tc_{uuid.uuid4().hex[:16]}",
        "type": FUNCTION,
        FUNCTION: {
            "name": func_name,
            ARGUMENTS: args_json,
        },
        "message_status": "tool_calls",
    }
    return result

# ── Tool-call extraction ─────────────────────────────────────────────────

# Each entry: (compiled_regex, kind_label)
# CRITICAL: __tool_name_args_pattern__ and __bash_wrapper_pattern__ must come BEFORE
# __direct_xml_pattern__ to avoid matching inner tags first.
_TOOL_PATTERNS = [
    (__function_pattern__, "json_block"),
    (__function_alt_pattern__, "alt_parameters"),
    (__anthropic_tool_pattern__, "anthropic_tool"),
    (__tool_name_args_pattern__, "tool_name_args"),
    (__bash_wrapper_pattern__, "bash_wrapper"),
    (__bash_flex_pattern__, "bash_flex"),
    (__function_tag_pattern__, "function_tag"),
    (__builtins_pattern__, "builtins"),
    (__inline_json_pattern__, "inline_json"),
    (__direct_xml_pattern__, "direct_xml"),
    (__block_tool_pattern__, "block_tool"),
    (__markdown_json_pattern__, "markdown_json"),
    (__block_delim_pattern__, "block_delim"),
]


# Auto-derived: all pattern kinds that need tool-name validation.
# Excludes structural patterns (bash_wrapper, bash_flex, tool_name_args)
# which match specific syntax, not arbitrary tag names.
_VALIDATION_KINDS = frozenset(
    k for _, k in _TOOL_PATTERNS
) - frozenset({"bash_wrapper", "bash_flex", "tool_name_args"})


def _extract_params_from_block_tool(args_str: str) -> dict | None:
    """Parse block-style tool call arguments: 'key: "value"\nkey2: "value2"'.

    Handles:
    - key: "value"
    - key: value (unquoted)
    - key: 123, key: true, key: false
    """
    args: dict = {}
    # Match key: "value" or key: value pairs
    for m in re.finditer(r'(\w+):\s*"?([^"\n]*)"?', args_str):
        key = m.group(1)
        value = m.group(2).strip()
        if value:
            args[key] = _coerce_parameter_value(value)

    if not args:
        return None
    return args


# ── Kind-specific tool-call handlers ──────────────────────────────────────
# Each handler: (match, func_name, body) -> dict | None
# Returns None to skip this candidate; returns tool-call dict on success.

def _handle_json_block(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    if not _is_valid_toolcall_params(body):
        return None
    return _build_toolcall(func_name, body)


def _handle_anthropic_tool(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    if body is None:
        return None
    if len(body) > ANTHROPIC_MAX_BODY_LEN:
        return None
    args_dict = _extract_params_from_alt_function_body(body)
    if args_dict is None:
        return None
    return _build_toolcall(func_name, args_dict)


def _handle_direct_xml(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    args_dict = _extract_params_from_direct_xml(match)
    if args_dict is None:
        return None
    return _build_toolcall(func_name, args_dict)


def _handle_block_tool(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    args_dict = _extract_params_from_block_tool(body or "")
    if args_dict is None:
        return None
    return _build_toolcall(func_name, args_dict)


def _handle_markdown_json(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    # body is the full JSON string, func_name is the tool_name from group 2
    try:
        json_obj = json.loads(body)
        args_dict = json_obj.get("arguments", {})
        if isinstance(args_dict, str):
            args_dict = json.loads(args_dict)
        if not isinstance(args_dict, dict):
            return None
        return _build_toolcall(func_name, args_dict)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _handle_tool_name_args(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    # <tool_name="X"><args>{JSON}</args></tool_name>
    # group(1) = tool name, group(2) = JSON string
    json_str = match.group(2) or "{}"
    try:
        args_dict = json.loads(json_str)
        if not isinstance(args_dict, dict):
            return None
        return _build_toolcall(match.group(1), args_dict)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _handle_bash_wrapper(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    # <bash><cmd="command here" /></bash>
    # group(1) = command string
    return _build_toolcall("bash", {"cmd": match.group(1)})


def _handle_bash_flex(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    # <bash><cmd> "command"</cmd></bash> or <bash><cmd="command" /></bash>
    # group(1) = cmd="value" attribute, group(2) = content "value"
    cmd_str = match.group(1) or match.group(2) or ""
    if not cmd_str:
        return None
    return _build_toolcall("bash", {"cmd": cmd_str})


def _handle_function_tag(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    # <|begin_of_function|>{"name": "X", "arguments": {...}}<|end_of_function|>
    # group(1) = JSON payload
    json_str = match.group(1) or "{}"
    try:
        json_obj = json.loads(json_str)
        name = json_obj.get("name", "")
        args_obj = json_obj.get("arguments", {})
        if not name:
            return None
        return _build_toolcall(name, args_obj)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _handle_builtins(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    # <builtins.tool_name params="{...}">
    # group(1) = tool name (without builtins. prefix)
    # group(2) = JSON arguments
    json_str = match.group(2) or "{}"
    try:
        args_dict = json.loads(json_str)
        if not isinstance(args_dict, dict):
            return None
        return _build_toolcall(func_name, args_dict)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _handle_inline_json(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    # <tool_name>{JSON} (no closing tag)
    # group(1) = tool name, group(2) = JSON arguments
    json_str = match.group(2) or "{}"
    try:
        args_dict = json.loads(json_str)
        if not isinstance(args_dict, dict):
            return None
        return _build_toolcall(func_name, args_dict)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _handle_block_delim(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    # ░tool_name\n{json_args}
    # group(1) = tool name, group(2) = JSON string
    json_str = match.group(2) or "{}"
    try:
        args_dict = json.loads(json_str)
        if not isinstance(args_dict, dict):
            return None
        return _build_toolcall(func_name, args_dict)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _handle_alt_parameters(match: re.Match[str], func_name: str, body: str | None) -> dict | None:
    args_dict = _extract_params_from_alt_function_body(body)
    if args_dict is None:
        return None
    return _build_toolcall(func_name, args_dict)


# Dispatch table: kind -> handler
_TOOL_KIND_HANDLERS: dict[str, Callable[[re.Match[str], str, str | None], dict | None]] = {
    "json_block": _handle_json_block,
    "alt_parameters": _handle_alt_parameters,
    "anthropic_tool": _handle_anthropic_tool,
    "tool_name_args": _handle_tool_name_args,
    "bash_wrapper": _handle_bash_wrapper,
    "bash_flex": _handle_bash_flex,
    "function_tag": _handle_function_tag,
    "builtins": _handle_builtins,
    "inline_json": _handle_inline_json,
    "direct_xml": _handle_direct_xml,
    "block_tool": _handle_block_tool,
    "markdown_json": _handle_markdown_json,
    "block_delim": _handle_block_delim,
}

# Invariant: every pattern kind must have a handler
assert set(k for _, k in _TOOL_PATTERNS) == set(
    _TOOL_KIND_HANDLERS.keys()
), f"Pattern kinds mismatch: {set(k for _, k in _TOOL_PATTERNS) ^ set(_TOOL_KIND_HANDLERS.keys())}"


def _extract_toolcall(text: str, valid_tool_names: set[str] | None = None) -> tuple[dict | None, str, bool, str | None]:
    """Extract the first valid tool call from text.

    Searches for JSON, alternate-parameter, Anthropic-format, direct XML, block,
    markdown, and block-delimiter tool call patterns, validates them in text order,
    and returns the first valid one removed from text.

    Args:
        text: Text to search for tool calls in.
        valid_tool_names: Optional set of registered tool names. If provided,
            handlers that match arbitrary tag names will reject non-tool matches.

    Returns:
        (tool_call, cleaned_text, found, kind) where kind is the extraction pattern
        label (e.g. "direct_xml") or None if not found.
    """
    candidates: list[tuple[int, int, str, re.Match[str]]] = []

    for pattern, kind in _TOOL_PATTERNS:
        for match in pattern.finditer(text):
            candidates.append((match.start(), match.end(), kind, match))

    candidates.sort(key=lambda item: item[0])

    for start, end, kind, match in candidates:
        # CRITICAL: Do NOT access group(2) unconditionally — some patterns
        # (e.g., bash_wrapper) only have group(1).  Defer group access to
        # the kind-specific handler below.
        func_name = match.group(1)
        body = None
        try:
            body = match.group(2)
        except IndexError:
            pass  # Pattern has only one capture group; body stays None

        # Tool name validation: reject non-tool names for patterns that can match arbitrary tags.
        # These are patterns where the tool name is a free-form identifier that could match
        # anything (e.g., <foo>{...}, <function name="foo">). Patterns with structural
        # constraints (bash_wrapper, bash_flex) don't need validation since they match
        # specific command syntax, not arbitrary tag names.
        #
        # Auto-derived: all kinds EXCEPT structural patterns (bash_wrapper, bash_flex, tool_name_args).
        if valid_tool_names is not None and kind in _VALIDATION_KINDS:
            if func_name not in valid_tool_names:
                continue

        handler = _TOOL_KIND_HANDLERS[kind]
        tool_call = handler(match, func_name, body)
        if tool_call is None:
            continue

        cleaned_text = text[:start] + text[end:]
        return tool_call, cleaned_text, True, kind

    return None, text, False, None

# ── Thought extraction ───────────────────────────────────────────────────

def _extract_enclosed_thought(
    content: str,
    reasoning: str | None,
) -> tuple[str, str | None, bool]:
    """Move one enclosed thought segment from content into reasoning."""
    match = __thought_pattern__.search(content)
    if match is None:
        return content, reasoning, False

    moved_thought = match.group(1).strip()
    new_content = (content[: match.start()] + content[match.end() :]).strip()

    if moved_thought:
        if isinstance(reasoning, str) and reasoning.strip():
            new_reasoning = f"{reasoning.rstrip()}\n{moved_thought}"
        else:
            new_reasoning = moved_thought
    else:
        new_reasoning = reasoning

    return new_content, new_reasoning, True

# ── Logging helpers ──────────────────────────────────────────────────────

def _truncate_json(safe_dump: str, max_len: int = 500) -> str:
    """Truncate a JSON string at a safe syntactic boundary."""
    if len(safe_dump) <= max_len:
        return safe_dump
    truncated = safe_dump[:max_len]
    for safe_char in (",", "}", "]", '"'):
        last_pos = truncated.rfind(safe_char)
        if last_pos > max_len * 0.8:
            return truncated[: last_pos + 1] + "... (truncated)"
    return truncated + "... (truncated)"

def _log_extracted_tool_call(tool_call: dict, kind: str, context_snippet: str = "") -> None:
    """Log a warning for an extracted tool call (JSON truncated for readability)."""
    safe_dump = json.dumps(tool_call, ensure_ascii=False)
    truncated = _truncate_json(safe_dump)
    msg = f"⚙ postparse extracted tool call [{kind}]: {truncated}"
    if context_snippet:
        msg += f" | context: ...{context_snippet}..."
    warning(msg)

# ── Core postparse logic ─────────────────────────────────────────────────

def _extract_all_tool_calls(text: str, tool_calls: list[dict], valid_tool_names: set[str] | None = None) -> str:
    """Repeatedly extract tool calls from *text*, appending to *tool_calls*.

    Returns the cleaned text after all tool calls are removed.
    """
    while True:
        tool_call, text, found, kind = _extract_toolcall(text, valid_tool_names=valid_tool_names)
        if not found:
            break
        tool_calls.append(tool_call)
        _log_extracted_tool_call(tool_call, kind)
    return text.strip()

def llm_postparse(
    content: str,
    reasoning: str | None,
    tool_calls: list[dict],
    valid_tool_names: set[str] | None = None,
) -> tuple[str, str | None, list[dict]]:
    """Recover missed tool calls and normalize thought segments from LLM output.

    Extracts thought blocks from content into reasoning, then extracts tool calls
    from both content and reasoning text. Modifies ``tool_calls`` in place.

    Args:
        content: Raw content text from the LLM.
        reasoning: Separate reasoning channel content (may be None).
        tool_calls: List to append extracted tool calls to.
        valid_tool_names: Optional set of registered tool names. If provided,
            postparse will reject extracted calls for non-tool names.
    """
    if not isinstance(content, str):
        return content, reasoning, tool_calls

    # Move enclosed thoughts from content into reasoning
    while True:
        content, reasoning, moved = _extract_enclosed_thought(content, reasoning)
        if not moved:
            break

    # Extract tool calls from content
    content = _extract_all_tool_calls(content, tool_calls, valid_tool_names=valid_tool_names)

    # Extract tool calls from reasoning (if present)
    if isinstance(reasoning, str):
        reasoning = _extract_all_tool_calls(reasoning, tool_calls, valid_tool_names=valid_tool_names)

    return content, reasoning, tool_calls


# === VALIDATION ===


class InvalidReplyError(Exception):
    """Raised when reply validation finds a retryable contract violation."""

def _validate_tool_call_json(
    _content: str,
    _reasoning: str | None,
    tool_calls: list[dict],
    _finish_reason: str | None = None,
) -> str | None:
    """Ensure every tool call's arguments are valid JSON."""
    for tc in tool_calls:
        args_raw = tc.get("function", {}).get("arguments", "")
        if not isinstance(args_raw, str):
            continue
        try:
            json.loads(args_raw)
        except (json.JSONDecodeError, ValueError):
            name = tc.get("function", {}).get("name", "?")
            dump = json.dumps(tc, ensure_ascii=False)
            if len(dump) > 300:
                dump = dump[:300] + "..."
            return (
                f"tool call arguments are not valid JSON "
                f"(function={name}, likely truncated output) "
                f"| full toolcall: {dump}"
            )
    return None

def _validate_empty_reply(
    content: str,
    _reasoning: str | None,
    tool_calls: list[dict],
    _finish_reason: str | None = None,
) -> str | None:
    """Reject replies with no text content and no tool calls."""
    if tool_calls:
        return None
    if (content or "").strip():
        return None
    return "empty reply (no content, no tool calls)"

def _validate_phantom_tool_calls(
    content: str,
    reasoning: str | None,
    tool_calls: list[dict],
    finish_reason: str | None = None,
) -> str | None:
    """Detect phantom tool-call-like XML tags that postparse missed.

    If detected, raises ``InvalidReplyError`` to trigger a retry (consuming
    from the same retry budget). After retries are exhausted, the caller
    strips phantoms silently.
    """
    from agent_phantom_detect import detect_phantoms, _load_rules

    # Only check if there are no valid tool calls (phantoms only matter
    # when the LLM output text instead of structured tool calls).
    if tool_calls:
        return None

    rules = _load_rules()
    phantoms = detect_phantoms(content, reasoning, rules)
    if not phantoms:
        return None

    tags = ", ".join(f"'{p.tag_name}'" for p in phantoms)
    return f"phantom tool-call-like tags detected ({tags}) — not executed"


def _strip_phantoms(
    content: str,
    reasoning: str | None,
) -> tuple[str, str | None]:
    """Strip all detected phantom tool calls from content and reasoning.

    Called after retries are exhausted. The LLM must NOT see the original
    phantom patterns — they would serve as bad examples and cause repetition.
    """
    from agent_phantom_detect import detect_phantoms, strip_phantoms, _load_rules

    rules = _load_rules()
    phantoms = detect_phantoms(content, reasoning, rules)
    if not phantoms:
        return content, reasoning
    return strip_phantoms(content, reasoning, phantoms)


# Validation pipeline: order matters (most critical first).
# NOTE: finish_reason="length" (truncation) is handled by is_valid_end_of_turn
# in agent_endofturn_validate.py → recovery loop, not by retrying the same call.
_VALIDATORS = [
    _validate_tool_call_json,
    _validate_empty_reply,
    _validate_phantom_tool_calls,
]

def llm_validate(
    content: str,
    reasoning: str | None,
    tool_calls: list[dict],
    finish_reason: str | None = None,
) -> None:
    """Run validators in order; raise `InvalidReplyError` on first failure."""
    for validator in _VALIDATORS:
        reason = validator(content, reasoning, tool_calls, finish_reason)
        if reason is not None:
            warning(reason)
            raise InvalidReplyError(reason)


# === HTTP CLIENT ===


# ---------------------------------------------------------------------------
# Prefix Cache Tracker — estimates expected prefix cache hit rate
# ---------------------------------------------------------------------------

class PrefixCacheTracker:
    """Track expected vs actual prefix cache hits by comparing request bodies.

    Warnings are always emitted (never suppressed):
    - Gap: expected - actual >= 20pp
    - Params: model or tools changed
    - Low expected: expected hit < 25%
    - Low actual: actual hit < 25%
    """

    _DIVERGENCE_CONTEXT_LEN = 15  # chars on each side of divergence point

    def __init__(self) -> None:
        self._last_request_body: bytes | None = None
        self._last_params_key: bytes | None = None
        self._prev_request_body: bytes | None = None
        self._prev_params_key: bytes | None = None

    def compute_expected_hit(self, body_bytes: bytes) -> tuple[float, str]:
        """Compute expected prefix cache hit rate from request body bytes.

        Compares current request body with the previous one to estimate
        what fraction of the context can be served from the prefix cache.

        Saves the previous body to ``_prev_request_body`` so ``diagnose_miss``
        can compare the current body against the one that was actually sent.

        Returns:
            (expected_hit_rate 0.0-1.0, reason_string)
        """
        try:
            body = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 0.0, "unparseable body"

        params_key = self._extract_params_key(body)

        if self._last_params_key is not None and params_key != self._last_params_key:
            changed = self._find_param_changes(self._last_params_key, params_key)
            self._prev_request_body = self._last_request_body
            self._prev_params_key = self._last_params_key
            self._last_params_key = params_key
            self._last_request_body = body_bytes
            return 0.0, f"params changed: {changed}"

        if self._last_request_body is None:
            # First call — no previous body to compare.
            # Store current body as _prev_request_body so diagnose_miss can use it
            # on the next call.
            self._prev_request_body = body_bytes
            self._prev_params_key = params_key
            self._last_params_key = params_key
            self._last_request_body = body_bytes
            return 0.0, "first call (no previous context)"

        # Save previous body and params for diagnosis BEFORE updating
        self._prev_request_body = self._last_request_body
        self._prev_params_key = self._last_params_key

        common = self._longest_common_prefix(self._last_request_body, body_bytes)
        total = len(body_bytes)
        expected = common / total if total > 0 else 0.0

        if expected < 0.25 and common < len(self._last_request_body):
            div_context = self._format_divergence(self._last_request_body, body_bytes, common)
            reason = f"prefix match: {common}/{total} bytes ({expected:.1%}) — {div_context}"
        else:
            reason = f"prefix match: {common}/{total} bytes ({expected:.1%})"

        self._last_params_key = params_key
        self._last_request_body = body_bytes
        return expected, reason

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def diagnose_miss(self, current_body_bytes: bytes) -> str:
        """Diagnose why a prefix cache miss may have occurred.

        Compares *current_body_bytes* against ``_prev_request_body`` (the body
        from the previous request that was actually sent to the LLM).

        Checks params stability, prefix match percentage, and body size delta.

        Args:
            current_body_bytes: The current request body bytes.

        Returns:
            Human-readable diagnosis string.
        """
        reasons: list[str] = []

        if self._prev_request_body is None:
            return "no previous request to compare"

        # Check params
        try:
            current_body = json.loads(current_body_bytes.decode("utf-8"))
            current_params = self._extract_params_key(current_body)
            if self._prev_params_key is not None and current_params != self._prev_params_key:
                changed = self._find_param_changes(self._prev_params_key, current_params)
                reasons.append(f"params changed: {changed}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            reasons.append("unparseable current body")

        # Prefix match — compare current against the PREVIOUS body
        prev_body = self._prev_request_body
        common = self._longest_common_prefix(prev_body, current_body_bytes)
        total = len(current_body_bytes)
        prev_total = len(prev_body)
        match_pct = (common / total * 100) if total > 0 else 0
        reasons.append(f"prefix match: {common}/{total} bytes ({match_pct:.1f}%)")

        # Size delta
        size_delta = total - prev_total
        if size_delta != 0:
            sign = "+" if size_delta > 0 else ""
            reasons.append(f"body size delta: {sign}{size_delta} bytes ({prev_total} -> {total})")

        # Divergence context
        if common < min(prev_total, total):
            div_ctx = self._format_divergence(prev_body, current_body_bytes, common)
            reasons.append(div_ctx)

        return "; ".join(reasons)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _format_divergence(self, old_body: bytes, new_body: bytes, divergence_pos: int) -> str:
        """Show ~15 chars around the divergence point in both bodies."""
        ctx = self._DIVERGENCE_CONTEXT_LEN
        old_snippet = old_body[max(0, divergence_pos - ctx):divergence_pos + ctx].decode("utf-8", errors="replace")
        new_snippet = new_body[max(0, divergence_pos - ctx):divergence_pos + ctx].decode("utf-8", errors="replace")
        return f"diverged@{divergence_pos}: '{old_snippet}' vs '{new_snippet}'"

    def _extract_params_key(self, body: dict) -> bytes:
        """Extract model+tools as a deterministic cache-invalidation key.

        Only 'model' and 'tools' invalidate prefix cache in major backends.
        Generation params (temperature, top_p, etc.) do not affect prefill KV cache.
        """
        params = {}
        if "model" in body:
            params["model"] = body["model"]
        if "tools" in body:
            params["tools"] = body["tools"]
        return json.dumps(params, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _find_param_changes(self, old_params_key: bytes, new_params_key: bytes) -> str:
        """Find which params changed between two parameter sets.

        Args:
            old_params_key: JSON-encoded params from the previous request.
            new_params_key: JSON-encoded params from the current request.
        """
        try:
            old = json.loads(old_params_key.decode("utf-8"))
            new = json.loads(new_params_key.decode("utf-8"))
            changed = [k for k in sorted(set(old.keys()) | set(new.keys())) if old.get(k) != new.get(k)]
            return ", ".join(changed) if changed else "unknown"
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "unparseable"

    @staticmethod
    def _longest_common_prefix(a: bytes, b: bytes) -> int:
        """Return length of longest common byte prefix."""
        length = 0
        for x, y in zip(a, b):
            if x == y:
                length += 1
            else:
                break
        return length

    def reset(self) -> None:
        """Clear all stored state."""
        self._last_request_body = None
        self._last_params_key = None
        self._prev_request_body = None
        self._prev_params_key = None

# ---------------------------------------------------------------------------
# Response data models
# ---------------------------------------------------------------------------

@dataclass
class Function:
    """Function definition for tool calls."""
    name: str | None = None
    arguments: str | None = None

@dataclass
class ToolCall:
    """Single tool call with function name and arguments."""
    id: str | None = None
    function: Function = field(default_factory=Function)
    type: str = "function"

@dataclass
class Message:
    """Chat message with content, tool calls, and role."""
    content: str | None = None
    reasoning_content: str | None = None  # vLLM="reasoning", llama.cpp="reasoning_content"
    tool_calls: list[ToolCall] = field(default_factory=list)
    role: str | None = "assistant"

@dataclass
class Choice:
    """Completion choice with message and finish reason."""
    message: Message = field(default_factory=Message)
    finish_reason: str | None = None
    index: int = 0

@dataclass
class Usage:
    """Token usage statistics for an API request."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_tokens_details: dict[str, Any] | None = None

@dataclass
class Response:
    """Complete API response with choices and usage."""
    choices: list[Choice] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    id: str | None = None
    model: str | None = None
    created: int | None = None

# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------

class APIError(Exception):
    """Base exception for all API-related errors."""

class APITimeoutError(APIError):
    """Raised when a request times out."""

class BadRequestError(APIError):
    """Raised for HTTP 400 Bad Request errors."""

class RateLimitError(APIError):
    """Raised for HTTP 429 Rate Limit errors."""

class UnauthorizedError(APIError):
    """Raised for HTTP 401 Unauthorized errors."""

class APIConnectionError(APIError):
    """Raised when the API endpoint is unreachable (connection refused, TCP RST)."""

# ---------------------------------------------------------------------------
# Retry backoff — configurable, jittered, interruptible
# ---------------------------------------------------------------------------

import random as _random
import signal as _signal


class RetryBackoff:
    """Configurable backoff with exponential growth, jitter, and interruptibility.

    Usage:
        backoff = RetryBackoff(base=5, max_wait=300, jitter=0.3)
        for attempt in range(max_retries):
            try:
                ...
            except TransientError:
                if attempt == max_retries - 1:
                    raise
                backoff.wait(attempt)
    """

    def __init__(
        self,
        base: float = 5.0,
        max_wait: float = 300.0,
        jitter: float = 0.3,
        multiplier: float = 2.0,
    ):
        self.base = base
        self.max_wait = max_wait
        self.jitter = jitter
        self.multiplier = multiplier

    def wait(self, attempt: int) -> None:
        """Sleep for the backoff interval, interruptible by SIGINT."""
        raw = min(self.base * (self.multiplier ** attempt), self.max_wait)
        # Apply jitter: ±jitter fraction
        if self.jitter > 0:
            swing = raw * self.jitter
            wait = raw + _random.uniform(-swing, swing)
        else:
            wait = raw
        wait = max(0.1, wait)  # Floor at 100ms

        # Interruptible sleep — check for SIGINT every second
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 1.0))

    def next_wait(self, attempt: int) -> float:
        """Return the wait time for *attempt* without sleeping."""
        raw = min(self.base * (self.multiplier ** attempt), self.max_wait)
        if self.jitter > 0:
            swing = raw * self.jitter
            return max(0.1, raw + _random.uniform(-swing, swing))
        return max(0.1, raw)

# ---------------------------------------------------------------------------
# Context overflow error indicators — substrings matched against any APIError
# ---------------------------------------------------------------------------

CONTEXT_OVERFLOW_INDICATORS: tuple[str, ...] = (
    "exceed_context_size_error",
    "exceeds the available context size",
    "maximum context length",
    "context size has been exceeded",
)


def _is_context_overflow(error_str: str) -> bool:
    """Return True if *error_str* contains a known context overflow indicator."""
    return any(indicator in error_str for indicator in CONTEXT_OVERFLOW_INDICATORS)

# ---------------------------------------------------------------------------
# Chat completion wrapper (provides chat.completions.create() interface)
# ---------------------------------------------------------------------------

class SimpleChatCompletion:
    """Wrapper providing chat.completions.create() interface."""

    def __init__(self, client: "SimpleOpenAIClient"):
        self._client = client

    def create(self, **kwargs) -> Any:
        return self._client.chat_completions_create(**kwargs)

# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

# HTTP error code → exception mapping
_HTTP_ERROR_MAP = {
    400: BadRequestError,
    401: UnauthorizedError,
    429: RateLimitError,
}

class SimpleOpenAIClient:
    """Drop-in replacement for OpenAI client using stdlib only (urllib + json)."""

    @staticmethod
    def _safe_get(data: dict, *keys, expected_type=None, default=None):
        """Traverse nested dict keys with optional type validation."""
        if not isinstance(data, dict):
            return default
        current = data
        for key in keys:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        if expected_type is not None:
            if current is None and type(None) in (
                expected_type if isinstance(expected_type, tuple) else (expected_type,)
            ):
                return current
            if not isinstance(current, expected_type):
                return default
        return current

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: int = 300,
        cache_tracker: PrefixCacheTracker | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("API_KEY", "")
        self.timeout = timeout
        self._cache_tracker = cache_tracker
        self.chat = type("Chat", (), {"completions": SimpleChatCompletion(self)})()

    def chat_completions_create(self, **kwargs) -> Any:
        url = f"{self.base_url}/chat/completions"
        data = json.dumps(kwargs, sort_keys=True).encode("utf-8")

        # Log full request body + audit one-liner for debugging/reproduction
        try:
            import agent_session as _sess
            from datetime import datetime as _dt

            log_dir = getattr(_sess, "LOG_DIR", None)
            prefix = getattr(_sess, "SESSION_PREFIX", None)
            if log_dir and prefix:
                # (1) Full request body → {prefix}.lr.json (for full reproduction)
                lr_file = log_dir / f"{prefix}.lr.json"
                lr_body = {
                    "url": url,
                    "timeout": self.timeout,
                    "kwargs": kwargs,
                }
                lr_file.write_text(
                    json.dumps(lr_body, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                # (2) Audit one-liner → {prefix}.audit (params only, no context)
                audit_file = log_dir / f"{prefix}.audit"
                params = {k: kwargs[k] for k in (
                    "model", "max_tokens", "temperature", "top_p", "top_k",
                    "min_p", "presence_penalty", "frequency_penalty",
                    "repetition_penalty", "repeat_penalty", "seed",
                ) if k in kwargs}
                if "chat_template_kwargs" in kwargs:
                    params["chat_template_kwargs"] = kwargs["chat_template_kwargs"]
                if "extra_body" in kwargs:
                    params["extra_body"] = kwargs["extra_body"]
                params_str = " ".join(
                    f"{k}={json.dumps(v)}" for k, v in sorted(params.items())
                )
                ts = _dt.now().isoformat()
                with open(audit_file, "a", encoding="utf-8") as af:
                    af.write(f"[{ts}] LLM_CALL {params_str}\n")
        except Exception:
            pass  # Don't let logging failures break the request

        tracker = self._cache_tracker
        expected_hit, hit_reason = 0.0, "no-tracker"
        if tracker is not None:
            expected_hit, hit_reason = tracker.compute_expected_hit(data)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                wrapped = self._wrap_response(result)
                self._report_cache_hit(wrapped, expected_hit, hit_reason, data)
                return wrapped

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except (OSError, ValueError):
                pass

            exc_class = _HTTP_ERROR_MAP.get(e.code, APIError)
            raise exc_class(f"{exc_class.__name__}: {error_body}") from e

        except urllib.error.URLError as e:
            if self._is_timeout(e):
                raise APITimeoutError("Request timed out") from e
            if self._is_connection_refused(e):
                raise APIConnectionError(f"Connection refused: {e}") from e
            raise APIError(f"Request failed: {e}") from e

    @staticmethod
    def _is_timeout(e: urllib.error.URLError) -> bool:
        """Check if a URLError represents a timeout."""
        exc_str = str(e).lower()
        reason_str = str(getattr(e, "reason", "")).lower()
        return (
            "timeout" in exc_str
            or "timed out" in exc_str
            or "timeout" in reason_str
            or "timed out" in reason_str
            or isinstance(getattr(e, "reason", None), socket.timeout)
        )

    @staticmethod
    def _is_connection_refused(e: urllib.error.URLError) -> bool:
        """Check if a URLError represents a connection refusal (endpoint unreachable)."""
        reason = getattr(e, "reason", None)
        if isinstance(reason, (ConnectionRefusedError, socket.error)):
            return True
        reason_str = str(reason).lower()
        return (
            "connection refused" in reason_str
            or "errno 111" in reason_str
            or "errno 61" in reason_str  # macOS connection refused
        )

    def _report_cache_hit(
        self, response: Any, expected_hit: float, hit_reason: str, body_bytes: bytes
    ) -> None:
        """Warn on cache hit anomalies (warnings are always emitted).

        Passes *body_bytes* to ``diagnose_miss`` so it can compare the current
        request body against the previous one (``_prev_request_body``).
        """
        try:
            usage = getattr(response, "usage", None)
            if not usage:
                return
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            pt_details = getattr(usage, "prompt_tokens_details", None)
            cached_tokens = 0
            if pt_details and isinstance(pt_details, dict):
                cached_tokens = pt_details.get("cached_tokens", 0) or 0
            if prompt_tokens == 0:
                return
            actual_hit = cached_tokens / prompt_tokens

            gap = expected_hit - actual_hit
            if gap >= 0.20:
                warning(
                    f":: cache: expected {expected_hit:.0%} -> actual {actual_hit:.0%} "
                    f"(gap {gap:.0%}, prefix cache MISS)"
                )
                # Diagnose miss with divergence context
                tracker = self._cache_tracker
                if tracker is not None:
                    diag = tracker.diagnose_miss(body_bytes)
                    warning(f":: cache diag: {diag}")
            if "params changed" in hit_reason:
                warning(f":: cache: invalidated — {hit_reason}")
            if expected_hit < 0.25:
                warning(f":: cache: low expected {expected_hit:.0%} ({hit_reason})")
            if actual_hit < 0.25:
                warning(f":: cache: low actual {actual_hit:.0%} (cache underperforming)")
        except Exception:
            pass

    def _wrap_response(self, data: dict) -> Any:
        """Wrap raw API response dict into structured Response object."""
        if not isinstance(data, dict):
            return Response(choices=[], usage=Usage())

        choices = [self._wrap_choice(cd) for cd in self._safe_get(data, "choices", expected_type=list, default=[]) if isinstance(cd, dict)]

        usage_data = self._safe_get(data, "usage", expected_type=dict, default={})
        usage = Usage(
            prompt_tokens=self._safe_get(usage_data, "prompt_tokens", expected_type=int, default=0),
            completion_tokens=self._safe_get(usage_data, "completion_tokens", expected_type=int, default=0),
            total_tokens=self._safe_get(usage_data, "total_tokens", expected_type=int, default=0),
            prompt_tokens_details=self._safe_get(usage_data, "prompt_tokens_details", expected_type=dict, default=None),
        )

        return Response(
            choices=choices,
            usage=usage,
            id=self._safe_get(data, "id", expected_type=str),
            model=self._safe_get(data, "model", expected_type=str),
            created=self._safe_get(data, "created", expected_type=int),
        )

    def _wrap_choice(self, choice_data: dict) -> Choice:
        """Wrap a single choice dict into a Choice object."""
        message_data = self._safe_get(choice_data, "message", expected_type=dict, default={})
        tool_calls = [self._wrap_tool_call(tc) for tc in self._safe_get(message_data, "tool_calls", expected_type=list, default=[]) if isinstance(tc, dict)]

        reasoning_content = (
            self._safe_get(message_data, "reasoning", expected_type=str)
            or self._safe_get(message_data, "reasoning_content", expected_type=str)
        )

        return Choice(
            message=Message(
                content=self._safe_get(message_data, "content", expected_type=str),
                reasoning_content=reasoning_content,
                tool_calls=tool_calls,
                role=self._safe_get(message_data, "role", expected_type=str, default="assistant"),
            ),
            finish_reason=self._safe_get(choice_data, "finish_reason", expected_type=str),
            index=self._safe_get(choice_data, "index", expected_type=int, default=0),
        )

    def _wrap_tool_call(self, tc_data: dict) -> ToolCall:
        """Wrap a single tool call dict into a ToolCall object."""
        func_data = self._safe_get(tc_data, "function", expected_type=dict, default={})
        func_obj = Function(
            name=self._safe_get(func_data, "name", expected_type=str),
            arguments=self._safe_get(func_data, "arguments", expected_type=str),
        )
        return ToolCall(
            id=self._safe_get(tc_data, "id", expected_type=str),
            function=func_obj,
            type=self._safe_get(tc_data, "type", expected_type=str, default="function"),
        )

# === DATA MODELS ===


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

# [REMOVED] OverflowInfo — truncation replaced by in-place compression.

class EmptyModelResponse(ValueError):
    """Model returned no choices. Subclasses ValueError for backward compatibility."""

    pass

@dataclass
class _ToolCallFunction:
    """Tool function with name and arguments (mimics openai.types.chat.Function)."""

    name: str
    arguments: str

@dataclass
class _ToolCall:
    """Tool call with id, type, and function."""

    id: str
    type: str
    function: _ToolCallFunction

@dataclass
class CallStats:
    """Token usage statistics from a single LLM invocation.

    Token fields are ``None`` when the API does not provide usage data,
    allowing callers to distinguish "API returned 0" from "API returned no data".
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    finish_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    @property
    def hit_rate(self) -> float:
        """Cache hit rate (0.0–1.0), or 0.0 if no cache data."""
        if self.cached_tokens is None or not self.prompt_tokens:
            return 0.0
        return self.cached_tokens / self.prompt_tokens

@dataclass
class CacheTracker:
    """Session-wide cache hit rate statistics.

    Tracks cumulative, sliding-window (last 5 calls), and last-call hit rates.
    Records where ``cached_tokens is None`` are excluded from calculations.
    """

    _records: list[CallStats] = field(default_factory=list)

    def record(self, stats: CallStats) -> None:
        self._records.append(stats)

    def clear(self) -> None:
        self._records.clear()

    def _hit_rate(self, records: list[CallStats]) -> float | None:
        """Compute hit rate for a list of records, excluding None cache data."""
        valid = [r for r in records if r.cached_tokens is not None]
        total_prompt = sum(r.prompt_tokens or 0 for r in valid)
        total_cached = sum(r.cached_tokens or 0 for r in valid)
        return total_cached / total_prompt if total_prompt > 0 else None

    @property
    def cumulative_hit_rate(self) -> float | None:
        """Hit rate across all recorded calls."""
        return self._hit_rate(self._records)

    @property
    def sliding_hit_rate(self) -> float | None:
        """Hit rate over the last 5 calls."""
        return self._hit_rate(self._records[-5:])

    @property
    def last_hit_rate(self) -> float | None:
        """Hit rate of the most recent call with valid cache data."""
        for record in reversed(self._records):
            if record.cached_tokens is not None:
                return record.hit_rate
        return None

    @property
    def call_count(self) -> int:
        return len(self._records)

    @property
    def has_cache_data(self) -> bool:
        """True if any record has valid cache data (cached_tokens is not None)."""
        return any(r.cached_tokens is not None for r in self._records)

# Allowed fields in messages sent to the LLM API.
ALLOWED_MESSAGE_FIELDS: frozenset[str] = frozenset(
    ["role", "content", "name", "tool_calls", "tool_call_id"]
)

# Allowed parameters in the OpenAI chat completion body.
OPENAI_BODY_PARAMS: frozenset[str] = frozenset(
    [
        "model",
        "messages",
        "tools",
        "tool_choice",
        "stream",
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "repetition_penalty",
        "max_tokens",
        "seed",
    ]
)

# Allowed fields in tool_calls dicts sent to the API.
_ALLOWED_TOOL_CALL_FIELDS: frozenset[str] = frozenset(["id", "type", "function"])

# ---------------------------------------------------------------------------
# Config / Response dataclasses
# ---------------------------------------------------------------------------

@dataclass
class LLMCallConfig:
    """Configuration for an LLM invocation."""

    max_retries: int = 10
    disable_thinking_after: int = 5
    log_on_failure: bool = True
    log_file: Path | None = None
    min_response_bytes: int | None = None
    context: "TauContext | None" = None
    extra_kwargs: dict[str, Any] | None = None
    compress_client: Any = None
    compress_model: str | None = None
    compress_tools: list | None = None
    compress_extra_kwargs: dict | None = None
    compress_audit_writer: Any = None

@dataclass
class LLMResponse:
    """Structured LLM response with text, stats, and tool calls."""

    raw: Any
    text: str
    reasoning: str | None
    stats: CallStats
    success: bool = True
    error: Exception | None = None
    tool_calls: list[dict] = field(default_factory=list)

# ---------------------------------------------------------------------------
# Stats extraction
# ---------------------------------------------------------------------------

def _extract_call_stats(response: Any, response_text: str) -> CallStats:
    """Extract token usage and finish_reason from LLM response.

    Returns ``None`` for token fields when the API provides no usage data,
    so callers can distinguish "API returned 0" from "API returned no data".
    """
    usage = getattr(response, "usage", None)

    if usage:
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        pt_details = getattr(usage, "prompt_tokens_details", None)
        # Distinguish "API returned 0 cached" from "API didn't report cached".
        cached_tokens = pt_details.get("cached_tokens", 0) if isinstance(pt_details, dict) else None
    else:
        prompt_tokens = completion_tokens = cached_tokens = None

    finish_reason = None
    choices = getattr(response, "choices", None)
    if choices:
        finish_reason = getattr(choices[0], "finish_reason", None)

    return CallStats(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        finish_reason=finish_reason,
    )

def _extract_token_usage(response: Any, response_text: str) -> tuple[int, int, int]:
    """Legacy wrapper: returns (prompt_tokens, completion_tokens, cached_tokens)."""
    stats = _extract_call_stats(response, response_text)
    return (stats.prompt_tokens, stats.completion_tokens, stats.cached_tokens)

# ---------------------------------------------------------------------------
# Internal helpers for _invoke_llm_with_retry
# ---------------------------------------------------------------------------

def _prepare_messages(messages: Any) -> list[dict]:
    """Normalize messages: merge reasoning, strip non-API fields.

    Works on the REAL message list (no copy) so compression is in-place.
    """
    msg_list = messages.to_list() if hasattr(messages, "to_list") else messages
    # Shallow copy of list (not deep copy) — isolates mutations from caller's list
    # while allowing in-place compression to modify message dicts directly.
    msg_list = list(msg_list)

    # Merge reasoning into content with thinking markers (vLLM ignores top-level "reasoning").
    for msg in msg_list:
        reasoning = msg.pop("reasoning", None)
        if reasoning:
            content = msg.get("content", "") or ""
            msg["content"] = f"{BEGIN_OF_THOUGHT}{reasoning}{END_OF_THOUGHT}\n{content}"

    # Strip non-OpenAI fields from messages and nested tool_calls.
    for msg in msg_list:
        for key in list(msg.keys()):
            if key not in ALLOWED_MESSAGE_FIELDS:
                msg.pop(key, None)
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                for key in list(tc.keys()):
                    if key not in _ALLOWED_TOOL_CALL_FIELDS:
                        tc.pop(key, None)

    return msg_list

def _build_call_kwargs(
    model_name: str,
    messages: list[dict],
    tools: list,
    tool_choice: str,
    stream: bool,
    extra_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Build the kwargs dict for client.chat.completions.create()."""
    call_kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "stream": stream,
    }

    if not extra_kwargs:
        return call_kwargs

    # Split: standard params -> body, non-standard -> extra_body (+ body for llama.cpp compat).
    body_updates: dict[str, Any] = {}
    extra_body: dict[str, Any] = {}

    for k, v in extra_kwargs.items():
        if k == "_extra_body":
            extra_body = v
        elif k in OPENAI_BODY_PARAMS:
            body_updates[k] = v
        else:
            extra_body[k] = v
            body_updates[k] = v

    if "repetition_penalty" in extra_body:
        body_updates["repeat_penalty"] = extra_body["repetition_penalty"]

    call_kwargs.update(body_updates)
    if extra_body:
        call_kwargs["extra_body"] = extra_body

    return call_kwargs

# [REMOVED] _truncate_largest_message — replaced by in-place compression.

# ---------------------------------------------------------------------------
# Public API — _invoke_llm_with_retry
# ---------------------------------------------------------------------------

def _build_llm_response(
    response: Any,
    response_text: str,
    reasoning_content: str | None,
    call_stats: CallStats,
    tool_calls: list[dict],
    success: bool,
) -> LLMResponse:
    """Construct an LLMResponse from the common response components."""
    return LLMResponse(
        raw=response,
        text=response_text,
        reasoning=reasoning_content,
        stats=call_stats,
        success=success,
        tool_calls=tool_calls,
    )


# ---------------------------------------------------------------------------
# Overflow compression helpers — invoked before naive truncation
# ---------------------------------------------------------------------------

OVERSIZED_THRESHOLD = 0.20
COMPRESSION_TARGET_RATIO = 0.30


def _try_oversized_redaction(msg_list: list[dict]) -> list[dict] | None:
    """Redact tool results that exceed OVERSIZED_THRESHOLD of total context bytes.

    Returns a NEW list with redacted content if any redaction occurred, else None.
    """
    total_bytes = sum(len(str(m.get("content", ""))) for m in msg_list)
    threshold = int(total_bytes * OVERSIZED_THRESHOLD)
    redacted = False
    result: list[dict] = []

    for msg in msg_list:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if len(content) > threshold:
                result.append({
                    **msg,
                    "content": f"[redacted tool result: {len(content)} bytes]",
                })
                redacted = True
                continue
        result.append(msg)

    return result if redacted else None


def _try_context_compress(
    msg_list: list[dict],
    client: Any,
    model_name: str,
    tools: list | None,
    extra_kwargs: dict | None,
    log_file: Path | None,
    audit_writer: Any,
) -> list[dict] | None:
    """Invoke the full compression pipeline on the message list.

    Returns compressed messages, or None on failure (best-effort fallback).
    """
    if client is None:
        return None

    try:
        from agent_context_compress import compress_context

        compressed, _summary, _meta = compress_context(
            msg_list,
            client,
            model_name,
            COMPRESSION_TARGET_RATIO,
            tools or [],
            extra_kwargs,
            log_file=log_file,
            audit_writer=audit_writer,
        )
        return compressed
    except Exception:
        return None


def _handle_context_overflow(
    msg_list: list[dict],
    config: LLMCallConfig,
    attempt: int,
) -> list[dict] | None:
    """Handle context overflow by escalating: redaction → compression → give up.

    Returns compressed message list if recovery succeeded, None to abort.
    Modifies msg_list in-place when compression succeeds.
    """
    if not msg_list:
        return None

    # Escalating overflow strategy:
    #   attempts 0-2: oversized tool redaction (cheap)
    #   attempts 3+: full compress via context (uses LLM)
    compressed = None
    if 0 <= attempt <= 2:
        compressed = _try_oversized_redaction(msg_list)
    else:
        compressed = _try_context_compress(
            msg_list,
            config.compress_client,
            config.compress_model or "",
            config.compress_tools,
            config.compress_extra_kwargs,
            config.log_file,
            config.compress_audit_writer,
        )

    if compressed is not None:
        return compressed
    return None  # Compression failed — caller should check max_retries


def _invoke_llm_with_retry(
    client: Any,
    model_name: str,
    messages: list,
    tools: list,
    tool_choice: str,
    stream: bool = False,
    config: LLMCallConfig | None = None,
    valid_tool_names: set[str] | None = None,
) -> tuple[LLMResponse, list[dict] | None]:
    """Invoke LLM with retry logic and error handling.

    Returns ``(LLMResponse, compressed_messages)``. The second element is
    ``None`` when no compression occurred, or the compressed message list
    when context overflow triggered compression. Caller must sync the
    compressed messages back to the agent context (e.g.
    ``context.set_messages(compressed)``) to make compression persistent.
    """
    if config is None:
        config = LLMCallConfig()

    # Deep-copy to prevent mutating the caller's config during retries.
    effective_extra = copy.deepcopy(config.extra_kwargs) if config.extra_kwargs else {}
    effective_max_retries = config.max_retries
    effective_disable_after = config.disable_thinking_after
    effective_log_on_failure = config.log_on_failure
    effective_log_file = config.log_file
    effective_min_bytes = config.min_response_bytes

    # Track compressed messages — None means no compression occurred.
    compressed_messages: list[dict] | None = None
    last_error: Exception | None = None
    consecutive_length_errors = 0  # Track consecutive finish_reason=length

    # Health monitoring — track connection health (advisory, not blocking)
    health_monitor = get_health_monitor()

    for attempt in range(effective_max_retries + 1):
        # Advisory warning if circuit is open — don't block, let retry logic handle backoff
        if not health_monitor.is_healthy():
            status = health_monitor.get_status()
            warning(
                f"  :: LLM circuit OPEN (consecutive_failures={status.consecutive_failures}) — "
                f"proceeding with retry (existing backoff handles wait)"
            )

        # Prepare messages fresh each iteration so compressed context is used.
        msg_list = _prepare_messages(messages)

        # Disable thinking after N retries to force decisive responses.
        if (
            attempt >= effective_disable_after
            and effective_extra
            and effective_extra.get("chat_template_kwargs")
        ):
            effective_extra["chat_template_kwargs"]["enable_thinking"] = False

        try:
            call_kwargs = _build_call_kwargs(
                model_name, msg_list, tools, tool_choice, stream, effective_extra
            )

            response = client.chat.completions.create(**call_kwargs)
            if not response.choices:
                raise EmptyModelResponse("Empty response from model")

            choice = response.choices[0]
            response_text = choice.message.content or ""
            reasoning_content = getattr(choice.message, "reasoning_content", None)
            call_stats = _extract_call_stats(response, response_text)

            # Check minimum response length.
            if effective_min_bytes is not None and len(response_text) < effective_min_bytes:
                last_error = Exception(
                    f"Response too short ({len(response_text)} bytes < {effective_min_bytes} required)"
                )
                warning(
                    f"  :: LLM attempt {attempt + 1}/{effective_max_retries + 1}: "
                    f"response too short"
                )
                continue

            # Convert SDK tool calls to dicts.
            sdk_tool_calls = choice.message.tool_calls or []
            tool_calls_dicts = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in sdk_tool_calls
            ]

            response_text, reasoning_content, _ = llm_postparse(
                response_text, reasoning_content, tool_calls_dicts,
                valid_tool_names=valid_tool_names,
            )

            # Validate reply.
            try:
                llm_validate(
                    response_text,
                    reasoning_content,
                    tool_calls_dicts,
                    call_stats.finish_reason,
                )
            except InvalidReplyError as e:
                # Detect stuck-loop: consecutive finish_reason=length with same context
                if call_stats.finish_reason == "length":
                    consecutive_length_errors += 1
                    if consecutive_length_errors >= 3:
                        # Context too large — compress, then give up if still failing
                        compressed = _try_context_compress(
                            messages,
                            config.compress_client,
                            config.compress_model or "",
                            config.compress_tools,
                            config.compress_extra_kwargs,
                            config.log_file,
                            config.compress_audit_writer,
                        )
                        if compressed is not None:
                            messages = compressed
                            compressed_messages = compressed
                            continue  # Retry with compressed context
                        # Compression failed — give up
                        return _build_llm_response(
                            response, response_text, reasoning_content,
                            call_stats, tool_calls_dicts,
                            success=False,
                        ), compressed_messages
                    # < 3 consecutive: likely just a long answer, retry normally
                else:
                    consecutive_length_errors = 0  # Reset on non-length error

                if attempt >= effective_max_retries:
                    # Retries exhausted — if this was a phantom detection,
                    # strip phantoms silently before returning.
                    if "phantom tool-call-like" in str(e):
                        response_text, reasoning_content = _strip_phantoms(
                            response_text, reasoning_content,
                        )
                    return _build_llm_response(
                        response,
                        response_text,
                        reasoning_content,
                        call_stats,
                        tool_calls_dicts,
                        success=False,
                    ), compressed_messages
                llm_validation_retry(attempt, effective_max_retries, str(e))
                continue

            # Successful validation — reset counter
            consecutive_length_errors = 0
            health_monitor.record_success()
            return _build_llm_response(
                response,
                response_text,
                reasoning_content,
                call_stats,
                tool_calls_dicts,
                success=True,
            ), compressed_messages

        except (APITimeoutError, TimeoutError, EmptyModelResponse) as e:
            last_error = e

            if attempt >= effective_max_retries:
                error_display("MODEL ERROR", str(e))
                if effective_log_on_failure:
                    from agent_session import log_failed_api_request

                    log_failed_api_request(call_kwargs, effective_log_file)
                raise last_error from e

            llm_timeout_message(attempt, effective_max_retries)

        except BadRequestError as e:
            error_str = str(e)

            if _is_context_overflow(error_str):
                if attempt >= effective_max_retries:
                    error_display("CONTEXT OVERFLOW", str(e))
                    raise

                recovered = _handle_context_overflow(messages, config, attempt)
                if recovered is not None:
                    messages = recovered
                    compressed_messages = recovered
                    continue
                # Compression failed — fall through to max_retries check below

            if attempt >= effective_max_retries:
                error_display("BAD REQUEST", str(e))
                raise
            llm_validation_retry(attempt, effective_max_retries, str(e))
            continue

        except APIError as e:
            # HTTP 500 "context size has been exceeded" — route through
            # the same overflow recovery pipeline as BadRequestError.
            error_str = str(e)

            if _is_context_overflow(error_str):
                if attempt >= effective_max_retries:
                    error_display("CONTEXT OVERFLOW (500)", str(e))
                    raise

                recovered = _handle_context_overflow(messages, config, attempt)
                if recovered is not None:
                    messages = recovered
                    compressed_messages = recovered
                    continue
                # Compression failed — fall through to max_retries check below

            if attempt >= effective_max_retries:
                error_display("SERVER ERROR", str(e))
                raise
            llm_validation_retry(attempt, effective_max_retries, str(e))
            continue

        except APIConnectionError as e:
            last_error = e
            health_monitor.record_failure(str(e))

            if attempt >= effective_max_retries:
                error_display("CONNECTION ERROR", str(e))
                if effective_log_on_failure:
                    from agent_session import log_failed_api_request

                    log_failed_api_request(call_kwargs, effective_log_file)
                raise last_error from e

            backoff = RetryBackoff(base=5, max_wait=120, jitter=0.3)
            wait = backoff.next_wait(attempt)
            warning(
                f"  :: LLM attempt {attempt + 1}/{effective_max_retries + 1}: "
                f"endpoint unreachable — waiting {wait:.0f}s before retry"
            )
            backoff.wait(attempt)
            continue

    raise RuntimeError(
        f"_invoke_llm_with_retry exited loop without returning "
        f"(effective_max_retries={effective_max_retries}). This should not happen."
    )

# =============================================================================
# === PUBLIC API ===
# =============================================================================

__all__ = [
    # Postparse
    "llm_postparse",
    # Validation
    "llm_validate",
    "InvalidReplyError",
    # Error constants
    "CONTEXT_OVERFLOW_INDICATORS",
    # HTTP client
    "SimpleOpenAIClient",
    "APIError",
    "APITimeoutError",
    "APIConnectionError",
    "BadRequestError",
    "RateLimitError",
    "UnauthorizedError",
    "PrefixCacheTracker",
    "Response",
    "Choice",
    "Message",
    "ToolCall",
    "Function",
    "Usage",
    # Data models & invocation
    "CallStats",
    "CacheTracker",
    "LLMCallConfig",
    "LLMResponse",
    "_invoke_llm_with_retry",
    "_extract_token_usage",
]
