from __future__ import annotations

import filecmp
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .discovery import is_copy_po, iter_po_files


def make_backups(path: str | Path, overwrite: bool = False) -> int:
    count = 0
    for po_path in iter_po_files(path):
        target = po_path.with_name(f"{po_path.stem} - Copy.po")
        if target.exists() and not overwrite:
            continue
        if target.exists():
            try:
                if po_path.samefile(target):
                    continue
            except OSError:
                pass
        shutil.copy2(po_path, target)
        count += 1
    return count


@dataclass(slots=True)
class SyncByFilenameResult:
    copied: int = 0
    skipped_identical: int = 0
    skipped_self: int = 0
    source_files: int = 0
    target_files: int = 0
    duplicate_source_names: int = 0


def _is_nested_or_same(a: Path, b: Path) -> bool:
    """Return True when either folder is inside the other."""
    try:
        ar = a.resolve(strict=False)
        br = b.resolve(strict=False)
    except OSError:
        ar = a.absolute()
        br = b.absolute()
    return ar == br or ar.is_relative_to(br) or br.is_relative_to(ar)


def _same_file_content(src: Path, target: Path) -> bool:
    try:
        if src.samefile(target):
            return True
    except OSError:
        pass

    try:
        s_stat = src.stat()
        t_stat = target.stat()
    except OSError:
        return False

    if s_stat.st_size != t_stat.st_size:
        return False

    # Same size + same timestamp is enough for already synced files.
    if s_stat.st_mtime_ns == t_stat.st_mtime_ns:
        return True

    # Same size but different timestamp: deep compare avoids needless rewrites.
    try:
        return filecmp.cmp(src, target, shallow=False)
    except OSError:
        return False


def sync_by_filename_report(source_folder: str | Path, target_folder: str | Path) -> SyncByFilenameResult:
    source_folder = Path(source_folder)
    target_folder = Path(target_folder)

    if not source_folder.exists() or not target_folder.exists():
        raise ValueError("source and target folders must exist")
    if not source_folder.is_dir() or not target_folder.is_dir():
        raise ValueError("source and target must both be folders")
    if _is_nested_or_same(source_folder, target_folder):
        raise ValueError("source and target folders must be separate, not nested")

    result = SyncByFilenameResult()
    source_index: dict[str, Path] = {}
    duplicate_names: set[str] = set()
    for p in iter_po_files(source_folder):
        result.source_files += 1
        if p.name in source_index:
            duplicate_names.add(p.name)
            # Ambiguous source filename: do not use either file.
            source_index.pop(p.name, None)
            continue
        if p.name not in duplicate_names:
            source_index[p.name] = p
    result.duplicate_source_names = len(duplicate_names)

    for target in iter_po_files(target_folder):
        result.target_files += 1
        src = source_index.get(target.name)
        if not src:
            continue
        try:
            if src.samefile(target):
                result.skipped_self += 1
                continue
        except OSError:
            pass
        if _same_file_content(src, target):
            result.skipped_identical += 1
            continue
        shutil.copy2(src, target)
        result.copied += 1
    return result


def sync_by_filename(source_folder: str | Path, target_folder: str | Path) -> int:
    return sync_by_filename_report(source_folder, target_folder).copied


@dataclass(slots=True)
class RestoreCopyResult:
    copy_po: Path
    work_po: Path
    action: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def working_path_from_copy(copy_po: str | Path) -> Path | None:
    """Return the working .po path for a Copy.po backup path.

    Examples:
    - e03_001_000 - Copy.po -> e03_001_000.po
    - e03_001_000-Copy.po  -> e03_001_000.po

    Copy.po itself is never modified.
    """
    copy = Path(copy_po)
    if copy.suffix.lower() != ".po" or not is_copy_po(copy):
        return None
    stem = re.sub(r"\s*-\s*copy(?:\s*\(\d+\))?$", "", copy.stem, flags=re.IGNORECASE)
    if not stem or stem == copy.stem:
        return None
    return copy.with_name(stem + copy.suffix)


def iter_copy_po_files(path: str | Path):
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() == ".po" and is_copy_po(p):
            yield p
        return
    if not p.exists():
        return
    for f in iter_po_files(p, include_copy=True):
        if is_copy_po(f):
            yield f


def restore_working_po_from_copies(paths: list[str | Path] | tuple[str | Path, ...] | str | Path, dry_run: bool = False) -> list[RestoreCopyResult]:
    """Overwrite working .po files with clean content copied from matching Copy.po files.

    This is recursive for folder inputs. It is intentionally one-way and safe for
    backups: Copy.po files are read only and are never edited, renamed, deleted,
    or overwritten.
    """
    if isinstance(paths, (str, Path)):
        path_list = [paths]
    else:
        path_list = list(paths)

    results: list[RestoreCopyResult] = []
    seen_targets: set[Path] = set()

    for root in path_list:
        for copy_po in iter_copy_po_files(root):
            work_po = working_path_from_copy(copy_po)
            if work_po is None:
                results.append(RestoreCopyResult(copy_po, copy_po, "skipped", "cannot derive working path"))
                continue

            resolved_target = work_po.resolve()
            if resolved_target in seen_targets:
                results.append(RestoreCopyResult(copy_po, work_po, "skipped", "duplicate target"))
                continue
            seen_targets.add(resolved_target)

            action = "replace" if work_po.exists() else "create"
            if dry_run:
                results.append(RestoreCopyResult(copy_po, work_po, action))
                continue

            try:
                work_po.parent.mkdir(parents=True, exist_ok=True)
                tmp = work_po.with_name(work_po.name + ".restore_tmp")
                shutil.copy2(copy_po, tmp)
                os.replace(tmp, work_po)
                results.append(RestoreCopyResult(copy_po, work_po, action))
            except Exception as exc:
                try:
                    tmp = work_po.with_name(work_po.name + ".restore_tmp")
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
                results.append(RestoreCopyResult(copy_po, work_po, action, str(exc)))

    return results
