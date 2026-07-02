from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


# These folders can be enormous and normally never contain project .po files.
# Skipping them makes scans much faster when the user accidentally selects a
# large project root or home folder.
DEFAULT_SKIPPED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "node_modules",
}


@dataclass(slots=True)
class SegmentFiles:
    root: Path
    folder: Path
    segment_id: str
    work_po: Path
    copy_po: Path | None

    @property
    def chapter(self) -> str:
        parent = self.folder.parent
        if parent == self.root:
            return self.folder.name
        return str(parent.relative_to(self.root))


def is_copy_po(path: str | Path) -> bool:
    name = Path(path).name.lower()
    return name.endswith(".po") and ("- copy" in name or "-copy" in name)


def _iter_po_files_fast(
    folder: Path,
    *,
    include_copy: bool,
    skipped_dirs: set[str],
) -> Iterator[Path]:
    """Fast recursive .po scanner using os.scandir.

    pathlib.rglob/os.walk with sorting is okay for small folders but becomes
    noticeably slow on large trees. scandir gives DirEntry metadata cheaply and
    lets us prune cache/vendor folders before walking into them.
    """
    try:
        with os.scandir(folder) as entries:
            dirs: list[os.DirEntry[str]] = []
            for entry in entries:
                name = entry.name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if name not in skipped_dirs:
                            dirs.append(entry)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue

                if not name.lower().endswith(".po"):
                    continue
                path = Path(entry.path)
                if not include_copy and is_copy_po(path):
                    continue
                yield path
    except OSError:
        return

    for entry in dirs:
        yield from _iter_po_files_fast(Path(entry.path), include_copy=include_copy, skipped_dirs=skipped_dirs)


def iter_po_files(
    path: str | Path,
    include_copy: bool = False,
    *,
    skip_common_dirs: bool = True,
) -> Iterator[Path]:
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() == ".po" and (include_copy or not is_copy_po(p)):
            yield p
        return
    if not p.exists():
        return

    skipped_dirs = set(DEFAULT_SKIPPED_DIRS) if skip_common_dirs else set()
    yield from _iter_po_files_fast(p, include_copy=include_copy, skipped_dirs=skipped_dirs)


def segment_id_from_folder(folder: Path) -> str:
    name = folder.name
    return name.split()[0] if " " in name else name


def find_segments(root: str | Path) -> Iterator[SegmentFiles]:
    base = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_SKIPPED_DIRS]
        folder = Path(dirpath)
        segment_id = segment_id_from_folder(folder)
        work_name = f"{segment_id}.po"
        copy_name = f"{segment_id} - Copy.po"
        if work_name not in filenames:
            continue
        work = folder / work_name
        copy = folder / copy_name
        yield SegmentFiles(
            root=base,
            folder=folder,
            segment_id=segment_id,
            work_po=work,
            copy_po=copy if copy.exists() else None,
        )


def find_backup_for_file(work_po: str | Path) -> Path | None:
    p = Path(work_po)
    stem = p.stem
    copy = p.with_name(f"{stem} - Copy.po")
    return copy if copy.exists() else None
