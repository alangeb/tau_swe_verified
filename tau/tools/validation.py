"""Unified tool validation and schema utilities.

Combines tool-call validation (aliases, coercion, normalization) with
JSON schema generation for tool argument dataclasses.
"""

from __future__ import annotations

import dataclasses
from difflib import SequenceMatcher, get_close_matches
from typing import Any, get_args, get_origin

from agent_console import warning

__all__ = [
    # Schema utilities
    "_dataclass_to_json_schema",
    "get_valid_fields_from_tool",
    # Validation
    "normalize_tool_call",
    "fix_tool_call",
    "validate_tool_name",
    "fill_defaults_from_args",
]


# ── Constants ─────────────────────────────────────────────────────────────

FUZZY_SUGGESTION_CUTOFF = 0.5  # Minimum ratio for get_close_matches suggestions


# ── Lazy-loaded registries ─────────────────────────────────────────────────
# Populated on first access to enforce import-order safety.
# This avoids silent failures if this module is imported before tools.__init__.

_CMD_ALIASES: dict[str, str] | None = None
_ARG_ALIASES: dict[str, dict[str, str]] | None = None
_TOOLS: dict[str, Any] | None = None


def _ensure_registries() -> None:
    """Ensure tools registries are populated.

    Fails fast with a clear error if the registry is empty, preventing
    silent capability loss from deferred failures.
    """
    global _CMD_ALIASES, _ARG_ALIASES, _TOOLS
    if _CMD_ALIASES is None:
        from tools import CMD_ALIASES, ARG_ALIASES, TOOLS
        _CMD_ALIASES = CMD_ALIASES
        _ARG_ALIASES = ARG_ALIASES
        _TOOLS = TOOLS
        if not TOOLS:
            raise RuntimeError(
                "Tool registry is empty — check tools/ directory is accessible "
                "and tool modules import cleanly."
            )


# ── Type mapping ───────────────────────────────────────────────────────────

_TYPE_MAP = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "None": type(None),
}


def _resolve_type_string(type_str: str) -> type | str:
    """Resolve a string type annotation to a type object."""
    if type_str in _TYPE_MAP:
        return _TYPE_MAP[type_str]

    if type_str.startswith("Optional[") or type_str.startswith("typing.Optional["):
        return _resolve_type_string(type_str[9:-1])

    if type_str.startswith("List[") or type_str.startswith("list[") or type_str == "typing.List":
        return list

    if " | " in type_str:
        parts = [p.strip() for p in type_str.split(" | ") if p.strip() != "None"]
        if parts:
            return _resolve_type_string(parts[0])

    return type_str


def _type_to_json_type(py_type: type) -> str:
    """Map a Python type to its JSON Schema type string."""
    if isinstance(py_type, str):
        resolved = _resolve_type_string(py_type)
        if resolved is not py_type:
            return _type_to_json_type(resolved)
        return "string"

    origin = get_origin(py_type)
    if origin is not None:
        args = get_args(py_type)
        if type(None) in args:
            non_null = [t for t in args if t is not type(None)]
            if non_null:
                return _type_to_json_type(non_null[0])
        if origin in (list, tuple, set):
            return "array"
        if origin is dict:
            return "object"

    return {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
        type(None): "null",
    }.get(py_type, "string")


# ── Schema generation ──────────────────────────────────────────────────────

def _dataclass_to_json_schema(dc_class: type) -> dict:
    """Convert a dataclass to a JSON Schema dict for LLM tool calling."""
    schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    for fld in dataclasses.fields(dc_class):
        prop: dict[str, Any] = {"type": _type_to_json_type(fld.type)}

        if "description" in fld.metadata:
            prop["description"] = fld.metadata["description"]

        if fld.default is not dataclasses.MISSING:
            prop["default"] = fld.default
        elif fld.default_factory is not dataclasses.MISSING:
            try:
                prop["default"] = fld.default_factory()
            except Exception:
                pass
        else:
            schema["required"].append(fld.name)

        schema["properties"][fld.name] = prop

    return schema


# ── Public helpers ──────────────────────────────────────────────────────────

def _get_tool_schema_info(tool_func: Any) -> tuple[list[str], list[str], dict[str, str]]:
    """Extract schema info from a tool's Args dataclass.

    Returns ``(valid_fields, required_fields, field_types)``.
    - valid_fields: all field names
    - required_fields: fields with no default (no default, no default_factory)
    - field_types: mapping of field name -> JSON Schema type string
    """
    if not hasattr(tool_func, "__globals__"):
        return [], [], {}

    globals_dict = tool_func.__globals__
    if "Args" not in globals_dict:
        return [], [], {}

    try:
        fields = dataclasses.fields(globals_dict["Args"])
    except Exception:
        return [], [], {}

    valid_fields: list[str] = []
    required_fields: list[str] = []
    field_types: dict[str, str] = {}

    for f in fields:
        valid_fields.append(f.name)
        field_types[f.name] = _type_to_json_type(f.type)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            required_fields.append(f.name)

    return valid_fields, required_fields, field_types


def get_valid_fields_from_tool(tool_func: Any) -> list[str]:
    """Extract valid field names from a tool's Args dataclass."""
    valid_fields, _, _ = _get_tool_schema_info(tool_func)
    return valid_fields


# ── Default filling ────────────────────────────────────────────────────────

def fill_defaults_from_args(args: dict[str, Any], tool_module: Any) -> None:
    """Fill missing optional args from the tool's Args dataclass defaults (mutates *args*)."""
    if not isinstance(args, dict):
        return
    if tool_module is None or not hasattr(tool_module, "Args"):
        return
    args_cls = tool_module.Args
    if not dataclasses.is_dataclass(args_cls):
        return
    for field in dataclasses.fields(args_cls):
        if field.name not in args:
            if field.default is not dataclasses.MISSING:
                args[field.name] = field.default
            elif field.default_factory is not dataclasses.MISSING:
                args[field.name] = field.default_factory()


# ── Tool name validation ──────────────────────────────────────────────────

def _adaptive_cutoff(query: str, candidates: list[str]) -> float:
    """Fuzzy-matching cutoff scaled by query length and candidate pool size."""
    base = 0.6
    length_bonus = min(0.1, len(query) * 0.02)
    density_penalty = max(0.0, (len(candidates) - 20) * 0.005)
    return max(0.5, min(0.8, base + length_bonus - density_penalty))


def _is_high_confidence(query: str, match: str) -> bool:
    """Match is high-confidence when SequenceMatcher ratio exceeds 0.8."""
    return SequenceMatcher(None, query, match).ratio() > 0.8


def validate_tool_name(
    tool_name: str,
    available_tools: list[str] | None = None,
    auto_correct: bool = True,
) -> tuple[bool, str | None, list[str]]:
    """Validate and optionally auto-correct a tool name via fuzzy matching.

    Returns ``(is_valid, corrected_name, suggestions)``.
    """
    _ensure_registries()

    if available_tools is None:
        available_tools = list(_TOOLS.keys())

    if tool_name in available_tools:
        return True, tool_name, []

    cutoff = _adaptive_cutoff(tool_name, available_tools)
    suggestions = get_close_matches(tool_name, available_tools, n=3, cutoff=cutoff)

    if not suggestions:
        return False, None, []

    if auto_correct and _is_high_confidence(tool_name, suggestions[0]):
        return True, suggestions[0], []

    return False, None, suggestions


# ── Coercion helpers ───────────────────────────────────────────────────────

def _coerce_and_warn(
    tool_name: str, field_name: str, value: str,
    coerced: object, label: str,
    warnings: list[str],
) -> None:
    """Apply a coerced value, log a warning, and record it."""
    msg = f"Arg '{tool_name}({field_name})' = '{value}' → {coerced} ({label} coercion)"
    warning(msg)
    warnings.append(msg)


# Data-driven type coercion table.
# Each entry: (coerce_fn, label) — coerce_fn(value: str) -> coerced_value or raises.
_COERCERS: dict[str, tuple] = {
    "integer": (lambda v: int(float(v)), "int"),
    "number": (lambda v: float(v), "number"),
    "boolean": (
        lambda v: (
            True if v.lower() in ("true", "t", "yes", "1")
            else False if v.lower() in ("false", "f", "no", "0")
            else None
        ),
        "bool",
    ),
}


# ── Normalization pipeline ─────────────────────────────────────────────────

def normalize_tool_call(tc: dict) -> list[str]:
    """Normalize a tool call: resolve aliases, coerce types, fill defaults.

    This is the single entry point for the complete normalization pipeline.
    It replaces the previous two-phase pattern of calling ``fix_tool_call`` twice
    with different arguments.

    Registries (CMD_ALIASES, ARG_ALIASES, TOOLS) are loaded lazily on first call.

    Mutates *tc* in-place; returns warning strings for every correction.
    """
    _ensure_registries()
    warnings: list[str] = []
    tool_name = tc["name"]
    args = tc.get("args_dict", {})
    if not isinstance(args, dict):
        return warnings

    # 1. Resolve command alias (CMD_ALIASES already includes _COMMON_ALIASES)
    if tool_name in _CMD_ALIASES:
        canonical = _CMD_ALIASES[tool_name]
        msg = f"Tool '{tool_name}' → '{canonical}' (alias resolved)"
        warning(msg)
        warnings.append(msg)
        tc["name"] = canonical
        tool_name = canonical

    # 2. Resolve argument aliases
    tool_arg_map = _ARG_ALIASES.get(tool_name, {})
    if tool_arg_map:
        renamed: dict[str, str] = {}
        for key in list(args.keys()):
            if key in tool_arg_map:
                canonical_arg = tool_arg_map[key]
                if canonical_arg in args and canonical_arg != key:
                    msg = (
                        f"Dropped '{key}', kept '{canonical_arg}' "
                        f"for '{tool_name}' (duplicate alias+canonical)"
                    )
                    warning(msg)
                    warnings.append(msg)
                    del args[key]
                    continue
                renamed[key] = canonical_arg

        for old_key, new_key in renamed.items():
            args[new_key] = args.pop(old_key)
            msg = f"Arg '{tool_name}({old_key})' → '{new_key}' (alias resolved)"
            warning(msg)
            warnings.append(msg)

    # 3. Look up tool entry and coerce types
    entry = _TOOLS.get(tool_name)
    if entry is not None:
        module = entry.module
        try:
            schema = entry.get_schema()
        except Exception:
            schema = {}

        properties = schema.get("properties", {})
        for field_name, value in list(args.items()):
            if not isinstance(value, str):
                continue

            prop_def = properties.get(field_name)
            if not isinstance(prop_def, dict):
                continue

            field_type = prop_def.get("type")
            coercer_info = _COERCERS.get(field_type)
            if coercer_info is None:
                continue

            coerce_fn, label = coercer_info
            try:
                coerced = coerce_fn(value)
            except (ValueError, TypeError):
                continue

            if coerced is None:
                continue  # boolean coercion returned None → not a valid bool string

            args[field_name] = coerced
            _coerce_and_warn(tool_name, field_name, value, coerced, label, warnings)

        # 3b. Reverse coercion — ensure schema-declared "string" fields are strings.
        # Covers cases where the caller passes int/bool/float but the tool expects str.
        for field_name, value in list(args.items()):
            if isinstance(value, str):
                continue  # already correct
            if value is None:
                continue  # leave None as-is for optional fields
            if not isinstance(value, (int, float, bool)):
                continue  # only coerce primitives (skip list, dict, etc.)

            prop_def = properties.get(field_name)
            if not isinstance(prop_def, dict):
                continue

            field_type = prop_def.get("type")
            if field_type != "string":
                continue  # only coerce when schema explicitly expects a string

            coerced = str(value)
            args[field_name] = coerced
            msg = f"Arg '{tool_name}({field_name})' = {value!r} → '{coerced}' (str coercion)"
            warning(msg)
            warnings.append(msg)

        # 4. Fill defaults from Args dataclass
        fill_defaults_from_args(args, module)

    return warnings


# ── Legacy entry point ────────────────────────────────────────────────────

def fix_tool_call(
    tc: dict,
    cmd_aliases: dict[str, str] | None = None,  # noqa: ARG001 — deprecated, ignored
    arg_aliases: dict[str, dict[str, str]] | None = None,  # noqa: ARG001 — deprecated, ignored
    tool_module: Any | None = None,  # noqa: ARG001 — deprecated, ignored
) -> list[str]:
    """Fix a parsed tool call: resolve aliases, then coerce types.

    .. deprecated::
        This function now delegates to :func:`normalize_tool_call` and ignores
        all parameters. The parameters are kept for backward compatibility only.
        Use :func:`normalize_tool_call` directly.
    """
    import warnings
    warnings.warn(
        "fix_tool_call is deprecated and ignores all parameters; "
        "use normalize_tool_call instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return normalize_tool_call(tc)


# ── Internal helpers (private) ────────────────────────────────────────────

def _validate_tool_args(
    tool_args: dict[str, Any],
    valid_fields: list[str],
    required_fields: list[str] | None = None,
) -> tuple[bool, list[str], list[str]]:
    """Check argument keys against an allowlist and required fields.

    Returns ``(is_valid, unknown_fields, missing_fields)``.
    - unknown_fields: keys in tool_args not in valid_fields
    - missing_fields: required fields not present in tool_args (only checked if required_fields provided)

    Backward compatible: when called with 2 args (no required_fields), missing_fields
    will be empty and behavior is identical to the original.
    """
    valid_set = set(valid_fields)
    unknown = [key for key in tool_args if key not in valid_set]

    missing: list[str] = []
    if required_fields is not None:
        # Only check required fields that haven't been filled by defaults
        # (defaults are already applied by normalize_tool_call before validation)
        missing = [f for f in required_fields if f not in tool_args]

    is_valid = len(unknown) == 0 and len(missing) == 0
    return is_valid, unknown, missing


def _generate_validation_error(
    tool_name: str,
    unknown_fields: list[str],
    valid_fields: list[str],
    missing_fields: list[str] | None = None,
    field_types: dict[str, str] | None = None,
) -> str:
    """Build an error message for tool parameter validation failures.

    Covers:
    - Unknown parameters (with fuzzy suggestions)
    - Missing required parameters (with type hints)
    """
    parts: list[str] = []

    # Section 1: Unknown parameters
    if unknown_fields:
        unknown_str = ", ".join(f"'{f}'" for f in unknown_fields)
        parts.append(f"Unknown parameters in tool '{tool_name}': {unknown_str}")

        # Fuzzy suggestions for unknown fields
        suggestions: list[str] = []
        for unknown in unknown_fields:
            matches = get_close_matches(unknown, valid_fields, n=1, cutoff=FUZZY_SUGGESTION_CUTOFF)
            if matches:
                suggestions.append(f"  Did you mean '{matches[0]}' instead of '{unknown}'?")
        if suggestions:
            parts.append("Suggestions:\n" + "\n".join(suggestions))

    # Section 2: Missing required parameters
    if missing_fields:
        missing_str = ", ".join(f"'{f}'" for f in missing_fields)
        parts.append(f"Missing required parameters for '{tool_name}': {missing_str}")
        if field_types:
            type_hints = [f"  '{f}' (expected: {field_types.get(f, 'unknown')})" for f in missing_fields]
            parts.append("Required parameters with types:\n" + "\n".join(type_hints))

    # Section 3: Valid parameters reference
    if valid_fields:
        if field_types:
            valid_with_types = [f"'{f}' ({field_types.get(f, '?')})" for f in valid_fields]
        else:
            valid_with_types = [f"'{f}'" for f in valid_fields]
        parts.append(f"Valid parameters: {', '.join(valid_with_types)}")

    return "\n".join(parts)
