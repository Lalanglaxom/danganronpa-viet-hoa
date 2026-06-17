from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class RenameChange:
    kind: str
    old: Path
    new: Path
    skipped: bool = False
    reason: str = ""


def _clean_name(name: str) -> str:
    return name.replace(" (1)", "")


def _is_copy_po_name(name: str) -> bool:
    low = name.lower()
    return low.endswith(".po") and ("- copy" in low or "-copy" in low)


def normalize_duplicate_names(root_dir: str | Path) -> list[RenameChange]:
    """Remove Windows duplicate suffixes like ' (1)' from files/folders.

    This is intentionally conservative: existing targets are never overwritten.
    Copy.po files are never renamed or modified.
    The traversal runs bottom-up so folder renames do not break child paths.
    """
    root = Path(root_dir)
    if not root.exists():
        raise FileNotFoundError(root)

    changes: list[RenameChange] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        base = Path(dirpath)

        for fname in sorted(filenames):
            new_name = _clean_name(fname)
            if new_name == fname:
                continue
            old = base / fname
            new = base / new_name
            if _is_copy_po_name(fname) or _is_copy_po_name(new_name):
                changes.append(RenameChange("file", old, new, skipped=True, reason="Copy.po protected"))
                continue
            if new.exists():
                changes.append(RenameChange("file", old, new, skipped=True, reason="target exists"))
                continue
            old.rename(new)
            changes.append(RenameChange("file", old, new))

        for dname in sorted(dirnames):
            new_name = _clean_name(dname)
            if new_name == dname:
                continue
            old = base / dname
            new = base / new_name
            if new.exists():
                changes.append(RenameChange("dir", old, new, skipped=True, reason="target exists"))
                continue
            old.rename(new)
            changes.append(RenameChange("dir", old, new))

    return changes
