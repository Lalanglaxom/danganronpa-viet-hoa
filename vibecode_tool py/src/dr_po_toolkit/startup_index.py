from __future__ import annotations

"""Discover the corpus that should be warmed as soon as the GUI opens."""

from collections.abc import Mapping
from pathlib import Path

from .discovery import iter_po_files
from .dr_options import DR_FILE_OPTION_KEYS

_EXTRA_PATH_FLAGS = (
    "validate_include_extra_path",
    "replace_include_extra_path",
    "linewrap_include_extra_path",
    "search_include_extra_path",
    "translafixer_include_extra_path",
    "po_viewer_include_extra_path",
    "gemini_web_include_extra_path",
)


def _path_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve(strict=False))
    except Exception:
        return str(path.expanduser().absolute())


def configured_index_roots(config: Mapping[str, object]) -> list[Path]:
    """Return unique, existing roots configured for normal toolkit work.

    Every configured Working folder is included because the shared index is used
    across tabs. The optional shared ``last_path`` is included only when at least
    one feature has its extra-path switch enabled.
    """

    raw_paths = [config.get(f"working_{key}_path", "") for key in DR_FILE_OPTION_KEYS]
    if any(bool(config.get(flag, False)) for flag in _EXTRA_PATH_FLAGS):
        raw_paths.append(config.get("last_path", ""))

    roots: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        value = str(raw or "").strip()
        if not value:
            continue
        path = Path(value).expanduser()
        if not path.exists():
            continue
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        roots.append(path)
    return roots


def configured_po_files(config: Mapping[str, object]) -> list[Path]:
    """Collect unique non-copy PO files from all startup index roots."""

    files: list[Path] = []
    seen: set[str] = set()
    for root in configured_index_roots(config):
        for path in iter_po_files(root):
            key = _path_key(path)
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    return files
