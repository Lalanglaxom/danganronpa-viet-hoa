from __future__ import annotations

import filecmp
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .discovery import is_copy_po, iter_po_files

ProgressFn = Callable[[int, int, Path], None]


def make_backups(
    path: str | Path,
    overwrite: bool = False,
    *,
    progress: ProgressFn | None = None,
) -> int:
    count = 0
    po_files = list(iter_po_files(path))
    total = len(po_files)
    if progress is not None and po_files:
        progress(0, total, po_files[0])
    for index, po_path in enumerate(po_files, start=1):
        try:
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
        finally:
            if progress is not None:
                progress(index, total, po_path)
    return count


@dataclass(slots=True)
class SyncByFilenameResult:
    copied: int = 0
    skipped_identical: int = 0
    skipped_self: int = 0
    source_files: int = 0
    target_files: int = 0
    duplicate_source_names: int = 0
    copied_files: list[tuple[Path, Path]] = field(default_factory=list)
    skipped_identical_files: list[tuple[Path, Path]] = field(default_factory=list)
    skipped_self_files: list[tuple[Path, Path]] = field(default_factory=list)
    duplicate_source_files: list[Path] = field(default_factory=list)
    source_without_target: list[Path] = field(default_factory=list)
    target_without_source: list[Path] = field(default_factory=list)


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


def sync_by_filename_report(
    source_folder: str | Path,
    target_folder: str | Path,
    *,
    progress: ProgressFn | None = None,
) -> SyncByFilenameResult:
    source_folder = Path(source_folder)
    target_folder = Path(target_folder)

    if not source_folder.exists() or not target_folder.exists():
        raise ValueError("source and target folders must exist")
    if not source_folder.is_dir() or not target_folder.is_dir():
        raise ValueError("source and target must both be folders")
    if _is_nested_or_same(source_folder, target_folder):
        raise ValueError("source and target folders must be separate, not nested")

    result = SyncByFilenameResult()
    source_files = list(iter_po_files(source_folder))
    target_files = list(iter_po_files(target_folder))
    total_progress = len(source_files) + len(target_files)
    completed = 0
    first_path = source_files[0] if source_files else (target_files[0] if target_files else source_folder)
    if progress is not None and total_progress:
        progress(0, total_progress, Path(first_path))

    source_index: dict[str, Path] = {}
    duplicate_names: set[str] = set()
    duplicate_sources: dict[str, list[Path]] = {}
    for p in source_files:
        result.source_files += 1
        try:
            if p.name in source_index:
                duplicate_names.add(p.name)
                # Ambiguous source filename: do not use either file.
                first = source_index.pop(p.name, None)
                bucket = duplicate_sources.setdefault(p.name, [])
                if first is not None:
                    bucket.append(first)
                bucket.append(p)
                continue
            if p.name not in duplicate_names:
                source_index[p.name] = p
        finally:
            completed += 1
            if progress is not None:
                progress(completed, total_progress, p)
    result.duplicate_source_names = len(duplicate_names)
    result.duplicate_source_files = [path for paths in duplicate_sources.values() for path in paths]

    matched_source_names: set[str] = set()
    for target in target_files:
        result.target_files += 1
        try:
            src = source_index.get(target.name)
            if not src:
                result.target_without_source.append(target)
                continue
            matched_source_names.add(target.name)
            try:
                if src.samefile(target):
                    result.skipped_self += 1
                    result.skipped_self_files.append((src, target))
                    continue
            except OSError:
                pass
            if _same_file_content(src, target):
                result.skipped_identical += 1
                result.skipped_identical_files.append((src, target))
                continue
            shutil.copy2(src, target)
            result.copied += 1
            result.copied_files.append((src, target))
        finally:
            completed += 1
            if progress is not None:
                progress(completed, total_progress, target)
    result.source_without_target = [src for name, src in source_index.items() if name not in matched_source_names]
    return result


def sync_by_filename(
    source_folder: str | Path,
    target_folder: str | Path,
    *,
    progress: ProgressFn | None = None,
) -> int:
    return sync_by_filename_report(source_folder, target_folder, progress=progress).copied


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


def restore_working_po_from_copies(
    paths: list[str | Path] | tuple[str | Path, ...] | str | Path,
    dry_run: bool = False,
    *,
    progress: ProgressFn | None = None,
) -> list[RestoreCopyResult]:
    """Overwrite working .po files with clean content copied from matching Copy.po files.

    This is recursive for folder inputs. It is intentionally one-way and safe for
    backups: Copy.po files are read only and are never edited, renamed, deleted,
    or overwritten.
    """
    if isinstance(paths, (str, Path)):
        path_list = [paths]
    else:
        path_list = list(paths)

    copy_files: list[Path] = []
    for root in path_list:
        copy_files.extend(iter_copy_po_files(root))

    results: list[RestoreCopyResult] = []
    seen_targets: set[Path] = set()
    total = len(copy_files)
    if progress is not None and copy_files:
        progress(0, total, copy_files[0])

    for index, copy_po in enumerate(copy_files, start=1):
        try:
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
        finally:
            if progress is not None:
                progress(index, total, copy_po)

    return results


@dataclass(slots=True)
class SyncOptionResult:
    option_key: str
    source_root: Path
    target_root: Path
    matched: int = 0
    copied: int = 0
    skipped_identical: int = 0
    skipped_self: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)
    copied_files: list[tuple[Path, Path]] = field(default_factory=list)
    skipped_identical_files: list[tuple[Path, Path]] = field(default_factory=list)
    skipped_self_files: list[tuple[Path, Path]] = field(default_factory=list)


@dataclass(slots=True)
class MoveCompileResult:
    source_root: Path
    target_root: Path
    scanned: int = 0
    moved: int = 0
    overwritten: int = 0
    skipped_identical: int = 0
    skipped_wad_repack: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)
    moved_files: list[tuple[Path, Path]] = field(default_factory=list)
    skipped_identical_files: list[tuple[Path, Path]] = field(default_factory=list)

    @property
    def copied(self) -> int:
        return self.moved

    @property
    def copied_files(self) -> list[tuple[Path, Path]]:
        return self.moved_files


def _norm_part(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _matches_dr_option(path: Path, root: Path, option_key: str) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    parts = [_norm_part(part) for part in rel.parts]
    name = _norm_part(path.name)
    stem = _norm_part(path.stem)
    suffix = path.suffix.lower()
    key = _norm_part(option_key)

    if re.fullmatch(r"e\d{2}", key):
        return (
            any(part == key for part in parts[:-1])
            or stem == key
            or stem.startswith(f"{key}_")
            or name.startswith(f"{key}_")
        )

    if key == "script_pak":
        return any(part == "script_pak" for part in parts) or suffix == ".pak" or name in {"script_pak", "script_pak_pak"}
    if key == "mtb":
        return any(part == "mtb" for part in parts) or suffix == ".mtb"
    if key == "system":
        return any(part == "system" for part in parts) or stem == "system" or stem.startswith("system_")
    if key == "tga":
        return any(part == "tga" for part in parts) or suffix == ".tga"
    return any(part == key for part in parts) or stem.startswith(f"{key}_")


def sync_option_from_working_folder(
    working_folder: str | Path,
    sync_folder: str | Path,
    option_key: str,
    *,
    filter_by_option: bool = True,
    progress: ProgressFn | None = None,
) -> SyncOptionResult:
    """Copy working .po files into a shared destination folder.

    When ``filter_by_option`` is true, only files whose path/name matches the option
    are copied. Existing destination files are matched by filename. A source file
    with no existing filename match is created at the same relative path beneath
    the destination folder.
    """
    source_root = Path(working_folder).expanduser()
    target_root = Path(sync_folder).expanduser()
    
    if not source_root.exists() or not source_root.is_dir():
        raise ValueError(f"working folder does not exist or is not a folder: {source_root}")
    
    target_root.mkdir(parents=True, exist_ok=True)
    
    if _is_nested_or_same(source_root, target_root):
        raise ValueError("working folder and sync folder must be separate, not nested")

    result = SyncOptionResult(option_key=option_key, source_root=source_root, target_root=target_root)
    
    # 1. Map all existing .po files in the sync folder by their filename
    target_po_map = {}
    for p in target_root.rglob("*.po"):
        if p.is_file():
            target_po_map[p.name] = p

    # 2. Iterate over source .po files
    source_files = [
        src
        for src in sorted(source_root.rglob("*.po"), key=lambda item: str(item).lower())
        if src.is_file()
        and not is_copy_po(src)
        and (not filter_by_option or _matches_dr_option(src, source_root, option_key))
    ]
    total = len(source_files)
    if progress is not None and source_files:
        progress(0, total, source_files[0])

    for index, src in enumerate(source_files, start=1):
        try:
            result.matched += 1

            # 3. Reuse an existing filename match. Otherwise preserve the
            # source path relative to its configured Working folder.
            dest = target_po_map.get(src.name)
            if dest is None:
                try:
                    relative = src.relative_to(source_root)
                except ValueError:
                    relative = Path(src.name)
                dest = target_root / relative

            try:
                if dest.exists():
                    try:
                        if src.samefile(dest):
                            result.skipped_self += 1
                            result.skipped_self_files.append((src, dest))
                            continue
                    except OSError:
                        pass
                    if _same_file_content(src, dest):
                        result.skipped_identical += 1
                        result.skipped_identical_files.append((src, dest))
                        continue

                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                result.copied += 1
                result.copied_files.append((src, dest))
            except Exception as exc:
                result.errors.append((src, str(exc)))
        finally:
            if progress is not None:
                progress(index, total, src)

    return result


def _is_under_or_same(path: Path, root: Path) -> bool:
    try:
        p = path.resolve(strict=False)
        r = root.resolve(strict=False)
    except OSError:
        p = path.absolute()
        r = root.absolute()
    return p == r or p.is_relative_to(r)


def _looks_like_wad_repack_path(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return any(_norm_part(part) in {"wad_repack", "wadrepack"} for part in rel.parts[:-1])


def _copy_tree_files(
    source_folder: str | Path,
    target_folder: str | Path,
    *,
    source_label: str,
    target_label: str,
    excluded_folder: str | Path | None = None,
    skip_wad_named_folders: bool = False,
    progress: ProgressFn | None = None,
) -> MoveCompileResult:
    source_root = Path(source_folder).expanduser()
    target_root = Path(target_folder).expanduser()
    if not source_root.exists() or not source_root.is_dir():
        raise ValueError(f"{source_label} folder does not exist or is not a folder: {source_root}")
    target_root.mkdir(parents=True, exist_ok=True)
    if _is_nested_or_same(source_root, target_root):
        raise ValueError(f"{source_label} and {target_label} folders must be separate, not nested")

    exclude_root: Path | None = None
    if excluded_folder:
        candidate = Path(excluded_folder).expanduser()
        if candidate.exists():
            exclude_root = candidate

    result = MoveCompileResult(source_root=source_root, target_root=target_root)
    
    # 1. Map all existing files in the target folder by their filename
    target_file_map = {}
    for p in target_root.rglob("*"):
        if p.is_file():
            target_file_map[p.name] = p

    files = sorted((path for path in source_root.rglob("*") if path.is_file()), key=lambda item: str(item).lower())
    total = len(files)
    if progress is not None and files:
        progress(0, total, files[0])
    for index, src in enumerate(files, start=1):
        try:
            if exclude_root is not None and _is_under_or_same(src, exclude_root):
                result.skipped_wad_repack += 1
                continue
            if skip_wad_named_folders and _looks_like_wad_repack_path(src, source_root):
                result.skipped_wad_repack += 1
                continue

            result.scanned += 1

            # 2. Find the destination path using the exact filename
            dest = target_file_map.get(src.name)

            # STRICT CHECK: If it doesn't exist anywhere in the target folder, report error and skip
            if not dest:
                result.errors.append((src, f"Strict sync failed: '{src.name}' does not exist anywhere in {target_label}."))
                continue

            try:
                if dest.exists():
                    if dest.is_dir():
                        result.errors.append((src, f"destination is a folder: {dest}"))
                        continue
                    if _same_file_content(src, dest):
                        result.skipped_identical += 1
                        result.skipped_identical_files.append((src, dest))
                        continue
                    result.overwritten += 1

                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                result.moved += 1
                result.moved_files.append((src, dest))
            except Exception as exc:
                result.errors.append((src, str(exc)))
        finally:
            if progress is not None:
                progress(index, total, src)

    return result


def move_repack_to_script(
    repack_folder: str | Path,
    script_folder: str | Path,
    *,
    wad_repack_folder: str | Path | None = None,
    progress: ProgressFn | None = None,
) -> MoveCompileResult:
    """Copy Repack files into Script, preserving relative paths and leaving Repack intact.

    WAD Repack files are skipped because those belong to the separate Game Folder deploy step.
    """
    return _copy_tree_files(
        repack_folder,
        script_folder,
        source_label="Repack",
        target_label="Script",
        excluded_folder=wad_repack_folder,
        skip_wad_named_folders=True,
        progress=progress,
    )


def copy_wad_repack_to_game(
    wad_repack_folder: str | Path,
    game_folder: str | Path,
    *,
    progress: ProgressFn | None = None,
) -> MoveCompileResult:
    """Copy WAD Repack files into the Game Folder, preserving relative paths."""
    return _copy_tree_files(
        wad_repack_folder,
        game_folder,
        source_label="WAD Repack",
        target_label="Game Folder",
        progress=progress,
    )
