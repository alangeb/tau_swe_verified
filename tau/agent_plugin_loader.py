"""Shared plugin loading infrastructure for tools and commands.

Provides standardized module discovery, loading, validation, and metadata
extraction used by both the tool system (tools/) and command system (commands/).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import traceback
from pathlib import Path
from typing import Any

# ── Logging helper (avoids circular import with console modules) ────────────


def _warn(msg: str) -> None:
    """Emit a warning to stderr (no circular import with console modules)."""
    print(f"[plugin_loader] WARNING: {msg}", file=sys.stderr)


# ── Plugin metadata ──────────────────────────────────────────────────────

def extract_module_metadata(
    mod: Any,
    fallback_name: str = "",
    name_attrs: tuple[str, ...] = ("name", "NAME"),
    description_attrs: tuple[str, ...] = ("description", "DESCRIPTION"),
) -> dict[str, Any]:
    """Extract standard metadata from a plugin module.

    Returns a dict with keys: name, description, run, file_path (if available).
    Missing optional fields default to empty strings.

    Supports both legacy modules (name/description attributes) and modern
    ToolMetadata-based modules (metadata.name/metadata.description).
    """
    # Check for ToolMetadata first (modern tool modules)
    metadata = getattr(mod, "metadata", None)
    if metadata is not None and hasattr(metadata, "name"):
        module_name = metadata.name
        description = getattr(metadata, "description", "")
    else:
        # Legacy: try NAME first (commands convention), then name (tools convention)
        module_name = fallback_name
        for attr in name_attrs:
            val = getattr(mod, attr, None)
            if val:
                module_name = val
                break

        description = ""
        for attr in description_attrs:
            val = getattr(mod, attr, None)
            if val:
                description = val
                break

    return {
        "name": module_name,
        "description": description,
        "run": getattr(mod, "run", None),
        "module": mod,
    }


# ── Validation ──────────────────────────────────────────────────────────

def validate_module_has(
    mod: Any,
    required: tuple[str, ...] = ("name", "description", "run"),
    callable_attrs: tuple[str, ...] = ("run",),
) -> list[str]:
    """Validate that a module has the required attributes.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []

    for attr in required:
        if not hasattr(mod, attr):
            errors.append(f"Missing '{attr}' attribute")
        elif attr in callable_attrs and not callable(getattr(mod, attr, None)):
            errors.append(f"'{attr}' is not callable")

    return errors


# ── Module loading ───────────────────────────────────────────────────────

def load_module_from_path(file_path: Path, module_name: str | None = None) -> Any | None:
    """Dynamically load a .py file as a module (fresh each time, no caching).

    Uses importlib.util.spec_from_file_location for absolute-path loading.
    Removes any cached entry from sys.modules before loading.
    Logs a warning (with traceback) if the module fails to load.
    """
    mod_name = module_name or file_path.stem

    # Ensure fresh load: remove cached module
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    spec = importlib.util.spec_from_file_location(mod_name, file_path)
    if spec is None or spec.loader is None:
        return None

    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        _warn(f"Failed to load {file_path.name}:\n{traceback.format_exc()}")
        return None
    return mod


def load_module_from_package(
    module_name: str, package: str
) -> Any | None:
    """Load a module by name relative to a package (cached, package-relative).

    Uses importlib.import_module for package-relative imports.
    """
    try:
        return importlib.import_module(f".{module_name}", package)
    except ImportError:
        return None


# ── Directory scanning ───────────────────────────────────────────────────

def scan_python_files(
    directory: Path, exclude_prefix: str = "_", exclude: tuple[str, ...] = ()
) -> list[Path]:
    """Return sorted list of .py files in *directory*, excluding hidden files."""
    exclude_set = set(exclude)
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.glob("*.py")
        if not p.name.startswith(exclude_prefix) and p.stem not in exclude_set
    )


def discover_modules(
    directory: Path,
    *,
    exclude_prefix: str = "_",
    exclude: tuple[str, ...] = (),
    required_attrs: tuple[str, ...] = ("name", "description", "run"),
    callable_attrs: tuple[str, ...] = ("run",),
    load_fresh: bool = True,
    package: str | None = None,
) -> list[dict[str, Any]]:
    """Discover valid plugin modules in a directory.

    Args:
        directory: Directory to scan for .py files.
        exclude_prefix: Skip files starting with this prefix (default "_").
        required_attrs: Attributes every module must have.
        callable_attrs: Attributes that must be callable.
        load_fresh: If True, use spec_from_file_location (fresh).
                   If False, use importlib.import_module (cached, package-relative).
        package: Package name for cached imports (ignored if load_fresh=True).

    Returns:
        List of metadata dicts for valid modules.
    """
    discovered: list[dict[str, Any]] = []

    # Ensure directory is a Path object (handles string paths)
    directory = Path(directory)

    for file_path in scan_python_files(directory, exclude_prefix, exclude):
        if load_fresh:
            mod = load_module_from_path(file_path)
        else:
            mod = load_module_from_package(file_path.stem, package)

        if mod is None:
            continue

        errors = validate_module_has(mod, required_attrs, callable_attrs)
        if errors:
            _warn(
                f"Module {file_path.name} validation failed: {'; '.join(errors)}"
            )
            continue

        meta = extract_module_metadata(mod, fallback_name=file_path.stem)
        meta["file_path"] = str(file_path)
        discovered.append(meta)

    return discovered
