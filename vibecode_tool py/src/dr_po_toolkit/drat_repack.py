from __future__ import annotations

import filecmp
import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from .cancel import OperationCancelled
from .po_io import load_po

ProgressFn = Callable[[int, int, Path], None]
CancelFn = Callable[[], None]


class DratRepackError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DratGameProfile:
    name: str
    is_dr1: bool = False
    is_dr2: bool = False
    is_drae: bool = False

    @property
    def text_padding(self) -> int:
        return 2 if self.is_dr2 else 4


@dataclass(frozen=True, slots=True)
class DratWorkspace:
    configured_root: Path
    manual_root: Path
    extracted_root: Path
    repacked_root: Path
    profile: DratGameProfile

    @property
    def wad_extracted_root(self) -> Path:
        return _child_case_insensitive(self.extracted_root, "WAD") or self.extracted_root / "WAD"

    @property
    def wad_repacked_root(self) -> Path:
        return _child_case_insensitive(self.repacked_root, "WAD") or self.repacked_root / "WAD"


@dataclass(slots=True)
class RepackFormatsResult:
    outputs: list[Path] = field(default_factory=list)
    built_outputs: list[Path] = field(default_factory=list)
    unchanged_outputs: list[Path] = field(default_factory=list)
    categories_processed: list[str] = field(default_factory=list)
    categories_missing: list[str] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)


@dataclass(slots=True)
class WadRepackResult:
    outputs: list[Path] = field(default_factory=list)
    built_outputs: list[Path] = field(default_factory=list)
    unchanged_outputs: list[Path] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)


@dataclass(slots=True)
class FilenameDeployResult:
    source_files: int = 0
    copied: int = 0
    overwritten: int = 0
    skipped_identical: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)
    copied_files: list[tuple[Path, Path]] = field(default_factory=list)
    skipped_identical_files: list[tuple[Path, Path]] = field(default_factory=list)


@dataclass(slots=True)
class FilenameMatchPlan:
    target_root: Path
    source_files: int = 0
    matches: list[tuple[Path, Path]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)


_NATURAL_SPLIT_RE = re.compile(r"(\d+)")
_TEXT_CLONE_EXTENSIONS = {".lin", ".pak", ".po", ".bytecode", ".unknown"}
_IMAGE_CLONE_EXTENSIONS = {
    ".llfs",
    ".gmo",
    ".gxt",
    ".btx",
    ".shtxfs",
    ".shtxff",
    ".shtx",
    ".font",
    ".gim",
    ".unknown",
    ".tga",
    ".pak",
    ".png",
    ".cmp",
    ".gx3",
    ".bmp",
}
_CACHE_VERSION = 2
_CACHE_FILENAME = ".drat_repack_cache.json"
_COPY_CHUNK_SIZE = 8 * 1024 * 1024
_COPY_PROGRESS_STEP = 16 * 1024 * 1024
_EXACT_COMPARE_LIMIT = 16 * 1024 * 1024
_PROGRESS_UNIT_BYTES = 1024 * 1024


def natural_key(value: str | Path) -> tuple[object, ...]:
    text = str(value).replace("\\", "/").lower()
    return tuple(int(part) if part.isdigit() else part for part in _NATURAL_SPLIT_RE.split(text))


def _check_cancel(cancel: CancelFn | None) -> None:
    if cancel is not None:
        cancel()


def _child_case_insensitive(parent: Path, name: str) -> Path | None:
    if not parent.exists() or not parent.is_dir():
        return None
    wanted = name.casefold()
    try:
        for child in parent.iterdir():
            if child.is_dir() and child.name.casefold() == wanted:
                return child
    except OSError:
        return None
    return None


def _manual_root_candidates(root: Path) -> list[Path]:
    candidates: list[Path] = []
    extracted = _child_case_insensitive(root, "EXTRACTED")
    if extracted is not None:
        candidates.append(root)

    if root.name.casefold() in {"extracted", "repacked"}:
        candidates.append(root.parent)

    if root.exists() and root.is_dir():
        try:
            children = [child for child in root.iterdir() if child.is_dir()]
        except OSError:
            children = []
        for child in children:
            if "manual mode" in child.name.casefold() and _child_case_insensitive(child, "EXTRACTED") is not None:
                candidates.append(child)
        # Accept a DRAT root whose game folder name differs slightly from the
        # original release, as long as it directly contains EXTRACTED.
        for child in children:
            if _child_case_insensitive(child, "EXTRACTED") is not None:
                candidates.append(child)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve(strict=False)).casefold()
        except OSError:
            key = str(candidate.absolute()).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _profile_from_path(path: Path) -> DratGameProfile:
    name = path.name.casefold()
    if "drae" in name or "another episode" in name:
        return DratGameProfile("DRAE", is_drae=True)
    if "dr2" in name or "danganronpa 2" in name:
        return DratGameProfile("DR2", is_dr2=True)
    return DratGameProfile("DR1", is_dr1=True)


def resolve_drat_workspace(path: str | Path) -> DratWorkspace:
    configured = Path(path).expanduser()
    if not configured.exists() or not configured.is_dir():
        raise DratRepackError(f"DRAT Folder does not exist or is not a folder: {configured}")

    candidates = _manual_root_candidates(configured)
    if not candidates:
        raise DratRepackError(
            "DRAT Folder must be a DRAT manual-mode folder containing EXTRACTED, "
            "or a parent folder containing one."
        )

    def score(candidate: Path) -> tuple[int, tuple[object, ...]]:
        lowered = candidate.name.casefold()
        preferred = 0
        if "dr1" in lowered and "pc" in lowered:
            preferred = -3
        elif "dr1" in lowered:
            preferred = -2
        elif "manual mode" in lowered:
            preferred = -1
        return preferred, natural_key(candidate.name)

    manual_root = sorted(candidates, key=score)[0]
    extracted = _child_case_insensitive(manual_root, "EXTRACTED")
    if extracted is None:
        raise DratRepackError(f"EXTRACTED folder not found under: {manual_root}")
    repacked = _child_case_insensitive(manual_root, "REPACKED") or manual_root / "REPACKED"
    repacked.mkdir(parents=True, exist_ok=True)
    return DratWorkspace(
        configured_root=configured,
        manual_root=manual_root,
        extracted_root=extracted,
        repacked_root=repacked,
        profile=_profile_from_path(manual_root),
    )


def _same_file_content(
    source: Path,
    target: Path,
    *,
    exact_compare_limit: int | None = None,
) -> bool:
    try:
        if source.samefile(target):
            return True
    except OSError:
        pass
    try:
        source_stat = source.stat()
        target_stat = target.stat()
    except OSError:
        return False
    if source_stat.st_size != target_stat.st_size:
        return False
    if exact_compare_limit is not None and source_stat.st_size > exact_compare_limit:
        # Large deployment artifacts are copied with copy2, so equal size and
        # mtime is a reliable fast path on subsequent runs. If metadata differs,
        # copying is cheaper than reading both multi-gigabyte files to compare.
        return source_stat.st_mtime_ns == target_stat.st_mtime_ns
    try:
        return filecmp.cmp(source, target, shallow=False)
    except OSError:
        return False


def _path_identity(path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path.absolute()
    return os.path.normcase(str(resolved))


def _fast_path_identity(path: Path) -> str:
    """Return a stable absolute identity without resolving every path on disk."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _copy_stream(
    source,
    destination,
    *,
    progress_bytes: Callable[[int], None] | None = None,
    cancel: CancelFn | None = None,
) -> int:
    """Copy a stream efficiently, with optional cancellable byte progress."""
    if progress_bytes is None and cancel is None:
        shutil.copyfileobj(source, destination, length=_COPY_CHUNK_SIZE)
        return 0

    buffer = bytearray(_COPY_CHUNK_SIZE)
    view = memoryview(buffer)
    copied = 0
    last_reported = 0
    last_report_time = time.monotonic()
    while True:
        _check_cancel(cancel)
        size = source.readinto(buffer)
        if not size:
            break
        destination.write(view[:size])
        copied += size
        now = time.monotonic()
        if progress_bytes is not None and (
            copied - last_reported >= _COPY_PROGRESS_STEP or now - last_report_time >= 0.15
        ):
            progress_bytes(copied)
            last_reported = copied
            last_report_time = now
    if progress_bytes is not None:
        progress_bytes(copied)
    return copied


def _cache_path(workspace: DratWorkspace) -> Path:
    return workspace.repacked_root / _CACHE_FILENAME


def _load_repack_cache(workspace: DratWorkspace) -> dict[str, object]:
    path = _cache_path(workspace)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": _CACHE_VERSION, "jobs": {}}
    if not isinstance(raw, dict) or raw.get("version") != _CACHE_VERSION:
        return {"version": _CACHE_VERSION, "jobs": {}}
    jobs = raw.get("jobs")
    if not isinstance(jobs, dict):
        raw["jobs"] = {}
    return raw


def _save_repack_cache(workspace: DratWorkspace, cache: Mapping[str, object]) -> None:
    path = _cache_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(cache, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _job_cache(cache: Mapping[str, object], job_key: str) -> dict[str, object]:
    jobs = cache.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    value = jobs.get(job_key)
    return value if isinstance(value, dict) else {}


def _snapshot_file_pairs(
    file_pairs: Iterable[tuple[str, Path]],
    *,
    previous_files: Mapping[str, object] | None = None,
    salt: str,
) -> tuple[str, dict[str, dict[str, object]]]:
    # Version 2 intentionally fingerprints metadata instead of reading every
    # input file. The previous implementation SHA-256 hashed the entire WAD
    # tree before packing, then hashed the finished WAD again. On large games
    # that added multiple full-disk passes to every repack.
    _ = previous_files
    records: dict[str, dict[str, object]] = {}
    digest = hashlib.sha256()
    digest.update(salt.encode("utf-8"))
    digest.update(b"\0")

    for logical_name, physical_path in sorted(file_pairs, key=lambda item: natural_key(item[0])):
        stat = physical_path.stat()
        source_identity = _fast_path_identity(physical_path)
        record = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns,
            "source": source_identity,
        }
        records[logical_name] = record
        for value in (
            logical_name,
            source_identity,
            str(stat.st_size),
            str(stat.st_mtime_ns),
            str(stat.st_ctime_ns),
        ):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")

    return digest.hexdigest(), records


def _output_state(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
    }


def _output_matches_cached_state(path: Path, job_cache: Mapping[str, object]) -> bool:
    output = job_cache.get("output")
    if not isinstance(output, dict) or not path.exists() or not path.is_file():
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    if output.get("size") != stat.st_size:
        return False
    return output.get("mtime_ns") == stat.st_mtime_ns


def _make_job_cache_entry(
    fingerprint: str,
    files: Mapping[str, Mapping[str, object]],
    output: Path,
) -> dict[str, object]:
    return {
        "fingerprint": fingerprint,
        "files": dict(files),
        "output": _output_state(output),
    }


def _cache_hit(output: Path, job_cache: Mapping[str, object], fingerprint: str) -> bool:
    return job_cache.get("fingerprint") == fingerprint and _output_matches_cached_state(output, job_cache)


def _stage_copy(
    source: Path,
    target: Path,
    *,
    progress_bytes: Callable[[int], None] | None = None,
    cancel: CancelFn | None = None,
) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.drat-stage-", dir=target.parent)
    os.close(fd)
    staged = Path(name)
    try:
        with source.open("rb") as source_stream, staged.open("wb") as destination_stream:
            _copy_stream(
                source_stream,
                destination_stream,
                progress_bytes=progress_bytes,
                cancel=cancel,
            )
        shutil.copystat(source, staged)
        return staged
    except Exception:
        try:
            staged.unlink()
        except FileNotFoundError:
            pass
        raise


def _commit_staged_files(staged_files: Sequence[tuple[Path, Path]]) -> None:
    """Atomically replace a batch as far as the host filesystem allows.

    Every staged file must be on the same filesystem as its final target. Existing
    files are moved to per-target backups first and restored if any replacement
    fails, preventing a half-deployed repack.
    """
    touched: list[tuple[Path, Path | None]] = []
    try:
        for staged, final in staged_files:
            final.parent.mkdir(parents=True, exist_ok=True)
            backup: Path | None = None
            if final.exists():
                fd, backup_name = tempfile.mkstemp(prefix=f".{final.name}.drat-backup-", dir=final.parent)
                os.close(fd)
                backup = Path(backup_name)
                backup.unlink()
                os.replace(final, backup)
            touched.append((final, backup))
            os.replace(staged, final)
    except Exception:
        for final, backup in reversed(touched):
            try:
                if final.exists():
                    final.unlink()
                if backup is not None and backup.exists():
                    os.replace(backup, final)
            except OSError:
                pass
        raise
    else:
        for _final, backup in touched:
            if backup is not None:
                try:
                    backup.unlink()
                except FileNotFoundError:
                    pass
    finally:
        for staged, _final in staged_files:
            try:
                staged.unlink()
            except FileNotFoundError:
                pass


def _files_with_extensions(root: Path, extensions: set[str]) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for current, _dirnames, filenames in os.walk(root):
        current_path = Path(current)
        for filename in filenames:
            path = current_path / filename
            if path.suffix.casefold() in extensions:
                files.append((path.relative_to(root).as_posix(), path))
    return files


def _matching_named_file(folder: Path, suffix: str) -> Path:
    return folder / f"{folder.name}{suffix}"


def _po_sentences(po_path: Path, *, profile: DratGameProfile, trim: bool = True) -> list[str]:
    po = load_po(po_path)
    parse_errors = [issue for issue in po.issues if issue.level.upper() == "ERROR"]
    if parse_errors:
        first = parse_errors[0]
        raise DratRepackError(f"Invalid PO at line {first.line}: {first.message}")

    sentences: list[str] = []
    is_bnd = po_path.parent.parent.name.casefold() == "bnd"
    novel = "novel" in po_path.stem.casefold()
    for entry in po.entries:
        if entry.msgid == "[EMPTY_LINE]" or entry.msgstr == "[EMPTY_LINE]":
            sentence = ""
        elif entry.msgstr.strip():
            sentence = entry.msgstr
        else:
            sentence = entry.msgid

        if trim and sentence and not is_bnd:
            sentence = sentence.strip()
        if sentence and not is_bnd and (profile.is_drae or novel):
            sentence += "\n"
        sentences.append(sentence)
    return sentences


def repack_text_folder(
    source_folder: str | Path,
    destination_folder: str | Path,
    *,
    profile: DratGameProfile,
    trim_extra_linefeeds: bool = True,
    include_lin_without_text: bool = False,
) -> Path:
    """Port of DRAT CommonTextStuff.RePackText for LIN and simple-text PAK."""
    source = Path(source_folder)
    destination = Path(destination_folder)
    if not source.exists() or not source.is_dir():
        raise DratRepackError(f"Text source folder not found: {source}")
    destination.mkdir(parents=True, exist_ok=True)

    bytecode_path = _matching_named_file(source, ".bytecode")
    po_path = _matching_named_file(source, ".po")
    is_lin = bytecode_path.exists()
    extension = ".lin" if is_lin else ".pak"
    output = destination / f"{source.name}{extension}"

    if not po_path.exists() and not is_lin:
        raise DratRepackError(f"Missing matching PO file: {po_path}")
    if is_lin and not po_path.exists() and not include_lin_without_text:
        raise DratRepackError(f"LIN has no matching PO file and is configured to be ignored: {source}")

    translated = _po_sentences(po_path, profile=profile, trim=trim_extra_linefeeds) if po_path.exists() else None
    n_parts = 2
    header_size = 0x10
    if not po_path.exists() and not profile.is_dr1:
        n_parts = 1
        header_size = 0x0C

    with output.open("wb+") as stream:
        file_size_pos: int | None = None
        if is_lin:
            stream.write(struct.pack("<II", n_parts, header_size))
            bytecode_size = bytecode_path.stat().st_size
            if header_size == 0x10:
                stream.write(struct.pack("<I", header_size + bytecode_size))
            file_size_pos = stream.tell()
            stream.write(struct.pack("<I", 0))
            with bytecode_path.open("rb") as bytecode:
                _copy_stream(bytecode, stream)

        if translated is not None:
            stream.write(struct.pack("<I", len(translated)))
            offsets_pos = stream.tell()
            offsets = [0] * (len(translated) + 1)
            stream.write(b"\x00" * (4 * len(offsets)))

            # DRAT offsets are relative to the beginning of the text section for
            # LIN files, and absolute for simple text PAK files.
            text_base = offsets_pos - 4
            offsets[0] = stream.tell() - text_base

            for index, sentence in enumerate(translated):
                if not profile.is_drae:
                    stream.write(struct.pack("<H", 0xFEFF))
                stream.write(sentence.encode("utf-16le"))
                stream.write(struct.pack("<H", 0))

                if not is_lin:
                    padding = (-stream.tell()) % 4
                    if padding:
                        stream.write(b"\x00" * padding)
                    offsets[index + 1] = stream.tell()
                else:
                    offsets[index + 1] = stream.tell() - text_base

            padding = (-stream.tell()) % profile.text_padding
            if padding:
                stream.write(b"\x00" * padding)
            if profile.is_drae and offsets:
                offsets[-1] = 0

            end_pos = stream.tell()
            stream.seek(offsets_pos)
            stream.write(struct.pack(f"<{len(offsets)}I", *offsets))
            stream.seek(end_pos)

        if is_lin:
            if translated is None and header_size == 0x10:
                stream.write(struct.pack("<II", 0, 8))
            if file_size_pos is None:
                raise AssertionError("LIN file-size pointer was not initialized")
            end_pos = stream.tell()
            stream.seek(file_size_pos)
            stream.write(struct.pack("<I", end_pos))
            stream.seek(end_pos)

    return output


def _align(stream, boundary: int) -> None:
    padding = (-stream.tell()) % boundary
    if padding:
        stream.write(b"\x00" * padding)


def repack_pak_folder(
    source_folder: str | Path,
    destination_folder: str | Path,
    *,
    repack_subdirs: bool = False,
) -> Path:
    """Port of DRAT PAK.RePackPAK for generic PAK containers."""
    source = Path(source_folder)
    destination = Path(destination_folder)
    if not source.exists() or not source.is_dir():
        raise DratRepackError(f"PAK source folder not found: {source}")
    destination.mkdir(parents=True, exist_ok=True)

    if repack_subdirs:
        child_dirs = sorted((p for p in source.iterdir() if p.is_dir()), key=natural_key)
        for child in child_dirs:
            repack_pak_folder(child, source, repack_subdirs=True)

    files = sorted((p for p in source.iterdir() if p.is_file()), key=natural_key)
    if not files:
        raise DratRepackError(f"PAK source folder contains no files: {source}")

    output = destination / f"{source.name}.pak"
    with output.open("wb+") as stream:
        stream.write(struct.pack("<I", len(files)))
        offsets_pos = stream.tell()
        stream.write(b"\x00" * (4 * len(files)))
        _align(stream, 0x10)

        offsets: list[int] = []
        for index, path in enumerate(files):
            offsets.append(stream.tell())
            with path.open("rb") as handle:
                _copy_stream(handle, stream)
            if index < len(files) - 1:
                _align(stream, 0x10)

        end_pos = stream.tell()
        stream.seek(offsets_pos)
        stream.write(struct.pack(f"<{len(offsets)}I", *offsets))
        stream.seek(end_pos)
    return output


def _copy_filtered_tree(source: Path, destination: Path, extensions: set[str]) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for current, dirnames, filenames in os.walk(source):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        target_current = destination / relative
        target_current.mkdir(parents=True, exist_ok=True)
        for dirname in dirnames:
            (target_current / dirname).mkdir(parents=True, exist_ok=True)
        for filename in filenames:
            source_file = current_path / filename
            if source_file.suffix.casefold() not in extensions:
                continue
            shutil.copy2(source_file, target_current / filename)


def _top_level_directories(path: Path) -> list[Path]:
    if not path.exists() or not path.is_dir():
        return []
    return sorted((p for p in path.iterdir() if p.is_dir()), key=natural_key)


def _category(root: Path, name: str) -> Path | None:
    return _child_case_insensitive(root, name)


def _category_jobs(workspace: DratWorkspace) -> list[tuple[str, Path]]:
    jobs: list[tuple[str, Path]] = []
    for category_name in (
        "LIN",
        "TEXT PAK TYPE 1",
        "TEXT PAK TYPE 2",
        "TEXT PAK TYPE 3",
        "TEXTURE PAK (W-O CONVERT)",
    ):
        category = _category(workspace.extracted_root, category_name)
        if category is None:
            continue
        jobs.extend((category_name, folder) for folder in _top_level_directories(category))
    return jobs


def _format_job_key(workspace: DratWorkspace, category_name: str, source_folder: Path) -> str:
    relative = source_folder.relative_to(workspace.extracted_root).as_posix()
    return f"format:{category_name.casefold()}:{relative.casefold()}"


def _format_job_inputs(category_name: str, source_folder: Path) -> list[tuple[str, Path]]:
    if category_name in {"LIN", "TEXT PAK TYPE 1"}:
        files: list[tuple[str, Path]] = []
        for suffix in (".po", ".bytecode"):
            path = _matching_named_file(source_folder, suffix)
            if path.exists() and path.is_file():
                files.append((path.name, path))
        return files
    if category_name in {"TEXT PAK TYPE 2", "TEXT PAK TYPE 3"}:
        return _files_with_extensions(source_folder, _TEXT_CLONE_EXTENSIONS)
    if category_name == "TEXTURE PAK (W-O CONVERT)":
        return _files_with_extensions(source_folder, _IMAGE_CLONE_EXTENSIONS)
    return []


def _format_output_path(workspace: DratWorkspace, category_name: str, source_folder: Path) -> Path:
    destination = workspace.repacked_root / category_name
    if category_name in {"LIN", "TEXT PAK TYPE 1"}:
        extension = ".lin" if _matching_named_file(source_folder, ".bytecode").exists() else ".pak"
    else:
        extension = ".pak"
    return destination / f"{source_folder.name}{extension}"


def _build_format_job(
    workspace: DratWorkspace,
    category_name: str,
    source_folder: Path,
    staging_root: Path,
    cancel: CancelFn | None,
) -> Path:
    destination = staging_root / category_name
    destination.mkdir(parents=True, exist_ok=True)

    if category_name == "LIN":
        return repack_text_folder(source_folder, destination, profile=workspace.profile)
    if category_name == "TEXT PAK TYPE 1":
        return repack_text_folder(source_folder, destination, profile=workspace.profile)
    if category_name in {"TEXT PAK TYPE 2", "TEXT PAK TYPE 3"}:
        with tempfile.TemporaryDirectory(prefix="drat_text_pak_", dir=staging_root) as tmp:
            temp_container = Path(tmp) / source_folder.name
            _copy_filtered_tree(source_folder, temp_container, _TEXT_CLONE_EXTENSIONS)
            for child in _top_level_directories(temp_container):
                _check_cancel(cancel)
                repack_text_folder(child, temp_container, profile=workspace.profile)
            return repack_pak_folder(temp_container, destination, repack_subdirs=False)
    if category_name == "TEXTURE PAK (W-O CONVERT)":
        with tempfile.TemporaryDirectory(prefix="drat_texture_pak_", dir=staging_root) as tmp:
            temp_container = Path(tmp) / source_folder.name
            _copy_filtered_tree(source_folder, temp_container, _IMAGE_CLONE_EXTENSIONS)
            return repack_pak_folder(temp_container, destination, repack_subdirs=True)
    raise DratRepackError(f"Unsupported DRAT category: {category_name}")


def repack_all_formats(
    workspace: DratWorkspace,
    *,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> RepackFormatsResult:
    """Incrementally repack all supported DRAT formats as one staged batch.

    Source fingerprints are cached under REPACKED. Jobs with unchanged inputs and
    a verified existing output are skipped. Changed outputs are built first, then
    committed only after every job succeeds.
    """
    result = RepackFormatsResult()
    jobs = _category_jobs(workspace)
    total = len(jobs)
    if progress is not None and jobs:
        progress(0, total, jobs[0][1])

    present_categories = {category for category, _folder in jobs}
    for category_name in (
        "LIN",
        "TEXT PAK TYPE 1",
        "TEXT PAK TYPE 2",
        "TEXT PAK TYPE 3",
        "TEXTURE PAK (W-O CONVERT)",
    ):
        category = _category(workspace.extracted_root, category_name)
        if category is None:
            result.categories_missing.append(category_name)
        elif category_name not in present_categories:
            result.skipped.append((category, "category contains no top-level source folders"))

    cache = _load_repack_cache(workspace)
    cache_jobs = cache.setdefault("jobs", {})
    if not isinstance(cache_jobs, dict):
        cache_jobs = {}
        cache["jobs"] = cache_jobs

    successful: list[tuple[Path, str, str, dict[str, dict[str, object]], Path | None]] = []
    # final output, job key, fingerprint, source records, staged output (or None for cache hit)

    workspace.repacked_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".drat_format_build_", dir=workspace.repacked_root) as tmp:
        staging_root = Path(tmp)
        for index, (category_name, source_folder) in enumerate(jobs, start=1):
            _check_cancel(cancel)
            try:
                if category_name == "LIN":
                    po = _matching_named_file(source_folder, ".po")
                    bytecode = _matching_named_file(source_folder, ".bytecode")
                    if not po.exists() and bytecode.exists():
                        result.skipped.append((source_folder, "LIN without text ignored, matching DRAT default"))
                        continue

                final_output = _format_output_path(workspace, category_name, source_folder)
                job_key = _format_job_key(workspace, category_name, source_folder)
                previous = _job_cache(cache, job_key)
                previous_files = previous.get("files") if isinstance(previous.get("files"), dict) else {}
                salt = (
                    f"format-v2|{category_name}|profile={workspace.profile.name}|"
                    f"padding={workspace.profile.text_padding}|trim=1"
                )
                fingerprint, source_records = _snapshot_file_pairs(
                    _format_job_inputs(category_name, source_folder),
                    previous_files=previous_files,
                    salt=salt,
                )

                if _cache_hit(final_output, previous, fingerprint):
                    result.unchanged_outputs.append(final_output)
                    result.skipped.append((source_folder, "unchanged inputs; existing output reused"))
                    successful.append((final_output, job_key, fingerprint, source_records, None))
                else:
                    staged_output = _build_format_job(
                        workspace,
                        category_name,
                        source_folder,
                        staging_root,
                        cancel,
                    )
                    if final_output.exists() and _same_file_content(
                        staged_output,
                        final_output,
                        exact_compare_limit=_EXACT_COMPARE_LIMIT,
                    ):
                        staged_output.unlink()
                        result.unchanged_outputs.append(final_output)
                        result.skipped.append((source_folder, "rebuilt output is identical; existing file kept"))
                        successful.append((final_output, job_key, fingerprint, source_records, None))
                    else:
                        successful.append((final_output, job_key, fingerprint, source_records, staged_output))

                if category_name not in result.categories_processed:
                    result.categories_processed.append(category_name)
            except OperationCancelled:
                raise
            except Exception as exc:
                result.errors.append((source_folder, str(exc)))
            finally:
                if progress is not None:
                    progress(index, total, source_folder)

        if result.errors:
            return result

        staged_pairs = [(staged, final) for final, _key, _fp, _records, staged in successful if staged]
        _commit_staged_files(staged_pairs)

    result.outputs = [final for final, _key, _fp, _records, _staged in successful]
    result.built_outputs = [final for final, _key, _fp, _records, staged in successful if staged is not None]

    for final, job_key, fingerprint, source_records, _staged in successful:
        cache_jobs[job_key] = _make_job_cache_entry(fingerprint, source_records, final)
    try:
        _save_repack_cache(workspace, cache)
    except OSError:
        # Cache failure must never invalidate successfully built game assets.
        pass
    return result

def _walk_files_filesystem_order(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, _dirnames, filenames in os.walk(root):
        current_path = Path(current)
        files.extend(current_path / name for name in filenames)
    return files


def _all_dirs_natural(root: Path) -> list[Path]:
    dirs: list[Path] = []
    for current, dirnames, _filenames in os.walk(root):
        current_path = Path(current)
        dirs.extend(current_path / name for name in dirnames)
    return sorted(dirs, key=lambda path: natural_key(path.relative_to(root)))


def _root_child_dirs_natural(root: Path) -> list[Path]:
    try:
        return sorted((p for p in root.iterdir() if p.is_dir()), key=natural_key)
    except OSError:
        return []


def _root_child_files_filesystem_order(root: Path) -> list[Path]:
    try:
        return [p for p in root.iterdir() if p.is_file()]
    except OSError:
        return []


def _write_wad_name(stream, name: str) -> None:
    encoded = name.encode("utf-8")
    stream.write(struct.pack("<I", len(encoded)))
    stream.write(encoded)


def _normalize_file_overrides(
    file_overrides: Mapping[str | Path, str | Path] | None,
) -> dict[str, Path]:
    normalized: dict[str, Path] = {}
    for target, source in (file_overrides or {}).items():
        source_path = Path(source)
        if not source_path.exists() or not source_path.is_file():
            raise DratRepackError(f"WAD override source does not exist: {source_path}")
        normalized[_path_identity(Path(target))] = source_path
    return normalized


def _effective_wad_file(path: Path, overrides: Mapping[str, Path]) -> Path:
    return overrides.get(_path_identity(path), path)


def _wad_file_pairs(source: Path, overrides: Mapping[str, Path]) -> list[tuple[Path, Path]]:
    return [
        (path, _effective_wad_file(path, overrides))
        for path in _walk_files_filesystem_order(source)
    ]


def _wad_job_inputs(source: Path, file_pairs: Sequence[tuple[Path, Path]]) -> list[tuple[str, Path]]:
    return [(path.relative_to(source).as_posix(), effective) for path, effective in file_pairs]


def _repack_wad_prepared(
    source: Path,
    destination: Path,
    file_pairs: Sequence[tuple[Path, Path]],
    *,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    dirs = _all_dirs_natural(source)
    output = destination / f"{source.name}.wad"
    prepared_pairs = [
        (file_path, effective_path, effective_path.stat().st_size)
        for file_path, effective_path in file_pairs
    ]
    total_bytes = sum(size for _file_path, _effective_path, size in prepared_pairs)
    total_units = max(1, (total_bytes + _PROGRESS_UNIT_BYTES - 1) // _PROGRESS_UNIT_BYTES)
    copied_before = 0

    if progress is not None:
        first = prepared_pairs[0][1] if prepared_pairs else source
        progress(0, total_units, first)

    with output.open("wb") as stream:
        stream.write(struct.pack("<IIII", 0x52414741, 1, 1, 0))
        stream.write(struct.pack("<I", len(file_pairs)))

        offset = 0
        for index, (file_path, _effective_path, size) in enumerate(prepared_pairs, start=1):
            if index % 256 == 0:
                _check_cancel(cancel)
            relative = file_path.relative_to(source).as_posix()
            encoded = relative.encode("utf-8")
            stream.write(struct.pack("<I", len(encoded)))
            stream.write(encoded)
            stream.write(struct.pack("<IIII", size, 0, offset, 0))
            offset += size

        stream.write(struct.pack("<I", len(dirs) + 1))

        # Root directory record.
        stream.write(struct.pack("<I", 0))
        root_dirs = _root_child_dirs_natural(source)
        root_files = _root_child_files_filesystem_order(source)
        stream.write(struct.pack("<I", len(root_dirs) + len(root_files)))
        for child in root_dirs:
            _write_wad_name(stream, child.name)
            stream.write(b"\x01")
        for child in root_files:
            _write_wad_name(stream, child.name)
            stream.write(b"\x00")

        # All subdirectory records. Folder records are naturally sorted, while
        # files retain filesystem order, matching DRAT's implementation.
        for index, directory in enumerate(dirs, start=1):
            if index % 256 == 0:
                _check_cancel(cancel)
            relative_dir = directory.relative_to(source).as_posix()
            _write_wad_name(stream, relative_dir)
            child_dirs = _root_child_dirs_natural(directory)
            child_files = _root_child_files_filesystem_order(directory)
            stream.write(struct.pack("<I", len(child_dirs) + len(child_files)))
            for child in child_dirs:
                _write_wad_name(stream, child.name)
                stream.write(b"\x01")
            for child in child_files:
                _write_wad_name(stream, child.name)
                stream.write(b"\x00")

        for _file_path, effective_path, file_size in prepared_pairs:
            _check_cancel(cancel)

            def report_file_bytes(file_bytes: int, *, base: int = copied_before, path: Path = effective_path) -> None:
                if progress is None:
                    return
                done_bytes = min(total_bytes, base + file_bytes)
                done_units = min(total_units, done_bytes // _PROGRESS_UNIT_BYTES)
                if done_bytes >= total_bytes:
                    done_units = total_units
                progress(done_units, total_units, path)

            with effective_path.open("rb") as handle:
                _copy_stream(
                    handle,
                    stream,
                    progress_bytes=report_file_bytes if progress is not None else None,
                    cancel=cancel,
                )
            copied_before += file_size

    if progress is not None:
        progress(total_units, total_units, source)

    return output


def repack_wad_folder(
    source_folder: str | Path,
    destination_folder: str | Path,
    *,
    file_overrides: Mapping[str | Path, str | Path] | None = None,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> Path:
    """Port of DRAT WAD.RePackWAD with optional virtual file replacements.

    ``file_overrides`` maps files inside the extracted WAD tree to generated
    files. The generated bytes are packed without modifying the extracted tree.
    """
    source = Path(source_folder)
    destination = Path(destination_folder)
    if not source.exists() or not source.is_dir():
        raise DratRepackError(f"WAD source folder not found: {source}")
    overrides = _normalize_file_overrides(file_overrides)
    return _repack_wad_prepared(
        source,
        destination,
        _wad_file_pairs(source, overrides),
        progress=progress,
        cancel=cancel,
    )


def repack_all_wads(
    workspace: DratWorkspace,
    *,
    file_overrides: Mapping[str | Path, str | Path] | None = None,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> WadRepackResult:
    """Incrementally repack all WAD roots using optional virtual replacements."""
    source_root = workspace.wad_extracted_root
    if not source_root.exists() or not source_root.is_dir():
        raise DratRepackError(f"DRAT EXTRACTED/WAD folder not found: {source_root}")
    folders = _top_level_directories(source_root)
    if not folders:
        raise DratRepackError(f"No extracted WAD folders found in: {source_root}")

    destination = workspace.wad_repacked_root
    destination.mkdir(parents=True, exist_ok=True)
    overrides = _normalize_file_overrides(file_overrides)
    result = WadRepackResult()
    cache = _load_repack_cache(workspace)
    cache_jobs = cache.setdefault("jobs", {})
    if not isinstance(cache_jobs, dict):
        cache_jobs = {}
        cache["jobs"] = cache_jobs

    if progress is not None:
        progress(0, 0, folders[0])

    successful: list[tuple[Path, str, str, dict[str, dict[str, object]], Path | None]] = []
    with tempfile.TemporaryDirectory(prefix=".drat_wad_build_", dir=destination) as tmp:
        staging_root = Path(tmp)
        for folder in folders:
            _check_cancel(cancel)
            try:
                if progress is not None:
                    progress(0, 0, folder)
                final_output = destination / f"{folder.name}.wad"
                relative = folder.relative_to(source_root).as_posix()
                job_key = f"wad:{relative.casefold()}"
                previous = _job_cache(cache, job_key)
                previous_files = previous.get("files") if isinstance(previous.get("files"), dict) else {}
                file_pairs = _wad_file_pairs(folder, overrides)
                fingerprint, source_records = _snapshot_file_pairs(
                    _wad_job_inputs(folder, file_pairs),
                    previous_files=previous_files,
                    salt="wad-v2|agar-1.1|metadata-cache",
                )

                if _cache_hit(final_output, previous, fingerprint):
                    result.unchanged_outputs.append(final_output)
                    result.skipped.append((folder, "unchanged WAD inputs; existing output reused"))
                    successful.append((final_output, job_key, fingerprint, source_records, None))
                    if progress is not None:
                        progress(1, 1, folder)
                else:
                    staged_output = _repack_wad_prepared(
                        folder,
                        staging_root,
                        file_pairs,
                        progress=progress,
                        cancel=cancel,
                    )
                    # A cache miss already means an input changed. Avoid a full
                    # second read of both huge WAD files just to discover that
                    # replacement is needed.
                    successful.append((final_output, job_key, fingerprint, source_records, staged_output))
            except OperationCancelled:
                raise
            except Exception as exc:
                result.errors.append((folder, str(exc)))

        if result.errors:
            return result

        staged_pairs = [(staged, final) for final, _key, _fp, _records, staged in successful if staged]
        _commit_staged_files(staged_pairs)

    result.outputs = [final for final, _key, _fp, _records, _staged in successful]
    result.built_outputs = [final for final, _key, _fp, _records, staged in successful if staged is not None]
    for final, job_key, fingerprint, source_records, _staged in successful:
        cache_jobs[job_key] = _make_job_cache_entry(fingerprint, source_records, final)
    try:
        _save_repack_cache(workspace, cache)
    except OSError:
        pass
    return result

def plan_files_by_filename(
    source_files: Sequence[str | Path] | Iterable[str | Path],
    target_folder: str | Path,
    *,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> FilenameMatchPlan:
    """Resolve generated files to unique existing targets without copying."""
    target_root = Path(target_folder).expanduser()
    if not target_root.exists() or not target_root.is_dir():
        raise DratRepackError(f"Target folder does not exist or is not a folder: {target_root}")

    sources = [Path(path) for path in source_files]
    plan = FilenameMatchPlan(target_root=target_root, source_files=len(sources))

    source_names: dict[str, list[Path]] = {}
    for source in sources:
        source_names.setdefault(source.name.casefold(), []).append(source)
    ambiguous_sources = {name for name, paths in source_names.items() if len(paths) > 1}
    wanted_names = set(source_names)

    target_map: dict[str, list[Path]] = {}
    scanned = 0
    if progress is not None:
        progress(0, 0, target_root)
    for current, _dirnames, filenames in os.walk(target_root):
        _check_cancel(cancel)
        current_path = Path(current)
        for filename in filenames:
            scanned += 1
            path = current_path / filename
            key = filename.casefold()
            if key in wanted_names:
                target_map.setdefault(key, []).append(path)
            if progress is not None and scanned % 500 == 0:
                progress(scanned, 0, path)
    if progress is not None:
        progress(scanned, scanned or 1, target_root)

    for source in sources:
        if not source.exists() or not source.is_file():
            plan.errors.append((source, "generated source file does not exist"))
            continue
        key = source.name.casefold()
        if key in ambiguous_sources:
            plan.errors.append((source, f"duplicate generated filename is ambiguous: {source.name}"))
            continue
        targets = target_map.get(key, [])
        if not targets:
            plan.errors.append((source, f"filename not found anywhere under target: {source.name}"))
            continue
        if len(targets) > 1:
            locations = "; ".join(str(path) for path in targets[:5])
            plan.errors.append((source, f"target filename is ambiguous ({len(targets)} matches): {locations}"))
            continue
        plan.matches.append((source, targets[0]))
    return plan


def deploy_filename_plans(
    plans: Sequence[FilenameMatchPlan],
    *,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> FilenameDeployResult:
    """Deploy one or more prevalidated plans as one staged transaction."""
    result = FilenameDeployResult(source_files=sum(plan.source_files for plan in plans))
    for plan in plans:
        result.errors.extend(plan.errors)
    if result.errors:
        return result

    matches = [match for plan in plans for match in plan.matches]
    target_owners: dict[str, Path] = {}
    for source, target in matches:
        key = _path_identity(target)
        previous = target_owners.get(key)
        if previous is not None:
            result.errors.append((source, f"deployment target is duplicated by {previous}: {target}"))
        else:
            target_owners[key] = source
    if result.errors:
        return result

    staged: list[tuple[Path, Path]] = []
    changed: list[tuple[Path, Path]] = []
    try:
        for source, target in matches:
            _check_cancel(cancel)
            try:
                if not source.exists() or not source.is_file():
                    result.errors.append((source, "generated source file no longer exists"))
                    continue
                if not target.exists() or not target.is_file():
                    result.errors.append((source, f"deployment target no longer exists: {target}"))
                    continue
                if _same_file_content(source, target, exact_compare_limit=_EXACT_COMPARE_LIMIT):
                    result.skipped_identical += 1
                    result.skipped_identical_files.append((source, target))
                    continue
                changed.append((source, target))
            except Exception as exc:
                result.errors.append((source, str(exc)))

        if result.errors:
            return result

        total_bytes = sum(source.stat().st_size for source, _target in changed)
        total_units = max(1, (total_bytes + _PROGRESS_UNIT_BYTES - 1) // _PROGRESS_UNIT_BYTES)
        copied_before = 0
        if progress is not None:
            first = changed[0][0] if changed else (matches[0][0] if matches else Path("deployment"))
            progress(0, total_units, first)

        for source, target in changed:
            _check_cancel(cancel)
            source_size = source.stat().st_size

            def report_file_bytes(file_bytes: int, *, base: int = copied_before, path: Path = source) -> None:
                if progress is None:
                    return
                done_bytes = min(total_bytes, base + file_bytes)
                done_units = min(total_units, done_bytes // _PROGRESS_UNIT_BYTES)
                if done_bytes >= total_bytes:
                    done_units = total_units
                progress(done_units, total_units, path)

            try:
                staged_file = _stage_copy(
                    source,
                    target,
                    progress_bytes=report_file_bytes if progress is not None else None,
                    cancel=cancel,
                )
                staged.append((staged_file, target))
                copied_before += source_size
            except OperationCancelled:
                raise
            except Exception as exc:
                result.errors.append((source, str(exc)))

        if result.errors:
            return result

        try:
            _commit_staged_files(staged)
        except Exception as exc:
            source = changed[0][0] if changed else Path("deployment")
            result.errors.append((source, f"transactional deployment failed: {exc}"))
            return result

        result.copied = len(changed)
        result.overwritten = len(changed)
        result.copied_files.extend(changed)
        if progress is not None:
            final_path = changed[-1][0] if changed else (matches[-1][0] if matches else Path("deployment"))
            progress(total_units, total_units, final_path)
        return result
    finally:
        for staged_file, _target in staged:
            try:
                staged_file.unlink()
            except FileNotFoundError:
                pass


def deploy_files_by_filename(
    source_files: Sequence[str | Path] | Iterable[str | Path],
    target_folder: str | Path,
    *,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> FilenameDeployResult:
    """Plan and transactionally copy generated files to unique filename matches."""
    plan = plan_files_by_filename(source_files, target_folder)
    return deploy_filename_plans([plan], progress=progress, cancel=cancel)
