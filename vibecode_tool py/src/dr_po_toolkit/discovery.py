from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


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


def iter_po_files(path: str | Path, include_copy: bool = False) -> Iterator[Path]:
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() == ".po" and (include_copy or not is_copy_po(p)):
            yield p
        return

    for dirpath, dirnames, filenames in os.walk(p):
        dirnames.sort()
        for fname in sorted(filenames):
            f = Path(dirpath) / fname
            if f.suffix.lower() != ".po":
                continue
            if not include_copy and is_copy_po(f):
                continue
            yield f


def segment_id_from_folder(folder: Path) -> str:
    name = folder.name
    return name.split()[0] if " " in name else name


def find_segments(root: str | Path) -> Iterator[SegmentFiles]:
    base = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
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
