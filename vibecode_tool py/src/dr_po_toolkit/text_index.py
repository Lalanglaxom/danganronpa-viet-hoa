from __future__ import annotations

"""Shared, incremental text/PO cache used by search, diff, and suggestions.

The toolkit repeatedly scans the same PO corpus from several screens.  Keeping a
single cache avoids re-reading, re-parsing, and re-normalising every file for each
feature.  Entries are treated as read-only; callers that need to edit a PO should
use :func:`load_po_clone`.
"""

import os
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import POEntry, POFile, ParseIssue
from .po_io import parse_po_text, po_unescape_quoted
from .text_utils import linebreak_insensitive_visible_text, visible_text

FileSignature = tuple[int, int, int]
CorpusSignature = tuple[tuple[str, FileSignature], ...]

_CACHE_MAX_FILES = 2048
_CACHE_MAX_CHARS = 256 * 1024 * 1024
_CACHE_LOCK = threading.RLock()
_CACHE_CHARS = 0
_CACHE_GENERATION = 0

_PO_FIELD_RE = re.compile(r'^(?:msgctxt|msgid|msgstr)\s+(".*")\s*$')
_PO_CONTINUATION_RE = re.compile(r'^(".*")\s*$')


@dataclass(frozen=True, slots=True)
class TextVariants:
    """Precomputed forms used by all text lookup features."""

    raw: str
    visible: str
    folded: str
    raw_lower: str
    visible_lower: str
    folded_lower: str


@dataclass(frozen=True, slots=True)
class IndexedPOEntry:
    entry: POEntry
    msgid: TextVariants
    msgstr: TextVariants
    speaker: TextVariants


@dataclass(slots=True)
class TextDocument:
    path: Path
    signature: FileSignature
    fields: TextVariants
    raw_text: str | None
    weight: int
    po: POFile | None = None
    entries: tuple[IndexedPOEntry, ...] | None = None
    parse_lock: threading.RLock | None = None

    def __post_init__(self) -> None:
        if self.parse_lock is None:
            self.parse_lock = threading.RLock()


_TEXT_CACHE: OrderedDict[str, TextDocument] = OrderedDict()


def path_cache_key(path: str | Path) -> str:
    p = Path(path).expanduser()
    try:
        return str(p.resolve(strict=False))
    except OSError:
        return str(p.absolute())


def file_signature(path: str | Path) -> FileSignature:
    stat = Path(path).stat()
    return int(stat.st_mtime_ns), int(stat.st_ctime_ns), int(stat.st_size)


def corpus_signature(paths: Iterable[str | Path]) -> CorpusSignature:
    """Return a stable signature for an ordered collection of existing files."""

    items: list[tuple[str, FileSignature]] = []
    for raw_path in paths:
        path = Path(raw_path)
        try:
            items.append((path_cache_key(path), file_signature(path)))
        except OSError:
            # Missing/unreadable paths still participate so a later appearance
            # invalidates any aggregate cache built from this signature.
            items.append((path_cache_key(path), (0, 0, 0)))
    return tuple(items)


def make_text_variants(text: str) -> TextVariants:
    raw = text or ""
    visible = visible_text(raw)
    folded = linebreak_insensitive_visible_text(raw)
    return TextVariants(
        raw=raw,
        visible=visible,
        folded=folded,
        raw_lower=raw.lower(),
        visible_lower=visible.lower(),
        folded_lower=folded.lower(),
    )


def _variants_weight(variants: TextVariants) -> int:
    return (
        len(variants.raw)
        + len(variants.visible)
        + len(variants.folded)
        + len(variants.raw_lower)
        + len(variants.visible_lower)
        + len(variants.folded_lower)
    )


def _extract_po_fields(raw_text: str) -> str:
    """Decode PO fields cheaply before constructing entry objects."""

    fields: list[str] = []
    current: list[str] | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            fields.append("".join(current))
            current = None

    for raw_line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        field_match = _PO_FIELD_RE.match(line)
        if field_match is not None:
            flush()
            try:
                current = [po_unescape_quoted(field_match.group(1))]
            except Exception:
                current = [field_match.group(1)[1:-1]]
            continue
        continuation_match = _PO_CONTINUATION_RE.match(line)
        if continuation_match is not None and current is not None:
            try:
                current.append(po_unescape_quoted(continuation_match.group(1)))
            except Exception:
                current.append(continuation_match.group(1)[1:-1])
            continue
        flush()
    flush()
    return "\n".join(fields)


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def _evict_if_needed_locked() -> None:
    global _CACHE_CHARS
    while len(_TEXT_CACHE) > _CACHE_MAX_FILES or _CACHE_CHARS > _CACHE_MAX_CHARS:
        _old_key, old_document = _TEXT_CACHE.popitem(last=False)
        _CACHE_CHARS = max(0, _CACHE_CHARS - old_document.weight)


def get_text_document(path: str | Path) -> TextDocument:
    """Return a cached file-level text document, refreshing it on file changes."""

    p = Path(path)
    key = path_cache_key(p)
    signature = file_signature(p)
    with _CACHE_LOCK:
        cached = _TEXT_CACHE.get(key)
        if cached is not None and cached.signature == signature:
            _TEXT_CACHE.move_to_end(key)
            return cached

    raw_text = _read_utf8(p)
    fields = make_text_variants(_extract_po_fields(raw_text))
    document = TextDocument(
        path=p,
        signature=signature,
        fields=fields,
        raw_text=raw_text,
        weight=len(raw_text) + _variants_weight(fields),
    )

    global _CACHE_CHARS
    with _CACHE_LOCK:
        # Another worker may have loaded the same current version while this one
        # was reading. Reuse that object instead of replacing parsed state.
        existing = _TEXT_CACHE.get(key)
        if existing is not None and existing.signature == signature:
            _TEXT_CACHE.move_to_end(key)
            return existing
        replaced = _TEXT_CACHE.pop(key, None)
        if replaced is not None:
            _CACHE_CHARS = max(0, _CACHE_CHARS - replaced.weight)
        _TEXT_CACHE[key] = document
        _TEXT_CACHE.move_to_end(key)
        _CACHE_CHARS += document.weight
        _evict_if_needed_locked()
    return document


def ensure_indexed(document: TextDocument) -> TextDocument:
    """Parse and pre-index entry fields once, then reuse them across features."""

    if document.po is not None and document.entries is not None:
        return document
    assert document.parse_lock is not None
    with document.parse_lock:
        if document.po is not None and document.entries is not None:
            return document
        raw_text = document.raw_text
        if raw_text is None:
            raw_text = _read_utf8(document.path)
        po = parse_po_text(raw_text, document.path)
        indexed_entries: list[IndexedPOEntry] = []
        entry_weight = 0
        for entry in po.entries:
            speaker_text = "\n".join(value for value in (entry.speaker, entry.msgctxt or "") if value)
            msgid = make_text_variants(entry.msgid)
            msgstr = make_text_variants(entry.msgstr)
            speaker = make_text_variants(speaker_text)
            indexed_entries.append(IndexedPOEntry(entry=entry, msgid=msgid, msgstr=msgstr, speaker=speaker))
            entry_weight += _variants_weight(msgid) + _variants_weight(msgstr) + _variants_weight(speaker)

        old_weight = document.weight
        # The parsed PO already owns all source strings; release the full raw file
        # copy after indexing to reduce memory pressure.
        document.raw_text = None
        document.po = po
        document.entries = tuple(indexed_entries)
        document.weight = _variants_weight(document.fields) + entry_weight

        global _CACHE_CHARS
        key = path_cache_key(document.path)
        with _CACHE_LOCK:
            if _TEXT_CACHE.get(key) is document:
                _CACHE_CHARS += document.weight - old_weight
                _TEXT_CACHE.move_to_end(key)
                _evict_if_needed_locked()
        return document


def get_indexed_document(path: str | Path) -> TextDocument:
    return ensure_indexed(get_text_document(path))


def get_cached_po(path: str | Path) -> POFile:
    document = get_indexed_document(path)
    assert document.po is not None
    return document.po


def _clone_entry(entry: POEntry | None) -> POEntry | None:
    if entry is None:
        return None
    return POEntry(
        index=entry.index,
        msgctxt=entry.msgctxt,
        msgid=entry.msgid,
        msgstr=entry.msgstr,
        comments=list(entry.comments),
        extracted_comments=list(entry.extracted_comments),
        line=entry.line,
    )


def clone_po_file(po_file: POFile, *, path: str | Path | None = None) -> POFile:
    """Deep-enough clone for callers that will edit entries in memory."""

    return POFile(
        path=Path(path) if path is not None else po_file.path,
        header=_clone_entry(po_file.header),
        entries=[_clone_entry(entry) for entry in po_file.entries if entry is not None],  # type: ignore[list-item]
        issues=[ParseIssue(issue.level, issue.message, issue.line) for issue in po_file.issues],
    )


def load_po_clone(path: str | Path) -> POFile:
    return clone_po_file(get_cached_po(path), path=path)


def release_raw_text(document: TextDocument) -> None:
    """Drop an unneeded source copy after a file-level prefilter rejects it."""

    if document.raw_text is None:
        return
    assert document.parse_lock is not None
    with document.parse_lock:
        if document.raw_text is None:
            return
        removed = len(document.raw_text)
        document.raw_text = None
        document.weight = max(0, document.weight - removed)
        global _CACHE_CHARS
        key = path_cache_key(document.path)
        with _CACHE_LOCK:
            if _TEXT_CACHE.get(key) is document:
                _CACHE_CHARS = max(0, _CACHE_CHARS - removed)


def invalidate_text_index(path: str | Path | None = None) -> None:
    """Invalidate one file or the whole shared text cache."""

    global _CACHE_CHARS, _CACHE_GENERATION
    with _CACHE_LOCK:
        _CACHE_GENERATION += 1
        if path is None:
            _TEXT_CACHE.clear()
            _CACHE_CHARS = 0
            return
        removed = _TEXT_CACHE.pop(path_cache_key(path), None)
        if removed is not None:
            _CACHE_CHARS = max(0, _CACHE_CHARS - removed.weight)


def text_index_generation() -> int:
    with _CACHE_LOCK:
        return _CACHE_GENERATION


def prime_text_index(paths: Iterable[str | Path], *, parse_entries: bool = True, workers: int | None = None) -> None:
    """Warm the shared cache, optionally in parallel.

    This is safe to call from a GUI background thread.  Normal feature calls also
    populate the cache lazily, so warming is an optimisation rather than a
    requirement.
    """

    path_list = [Path(path) for path in paths]
    if not path_list:
        return
    worker_count = workers or min(8, max(2, os.cpu_count() or 2), len(path_list))
    loader = get_indexed_document if parse_entries else get_text_document
    if len(path_list) < 12 or worker_count <= 1:
        for path in path_list:
            try:
                loader(path)
            except (OSError, UnicodeError):
                pass
        return
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="po-index") as executor:
        list(executor.map(_safe_prime_one, ((loader, path) for path in path_list)))


def _safe_prime_one(item: tuple[object, Path]) -> None:
    loader, path = item
    try:
        loader(path)  # type: ignore[operator]
    except (OSError, UnicodeError):
        pass


def text_index_stats() -> dict[str, int]:
    with _CACHE_LOCK:
        return {
            "files": len(_TEXT_CACHE),
            "parsed_files": sum(1 for document in _TEXT_CACHE.values() if document.po is not None),
            "weight_chars": _CACHE_CHARS,
        }
