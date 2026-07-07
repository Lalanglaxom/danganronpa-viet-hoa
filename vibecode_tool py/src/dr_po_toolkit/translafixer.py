from __future__ import annotations

import re
import shutil
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable

from .discovery import iter_po_files
from .po_io import load_po, save_po
from .text_utils import visible_text


@dataclass(slots=True)
class TranslafixSourceConflict:
    msgid: str
    first_translation: str
    other_translation: str
    file: Path
    line: int


@dataclass(slots=True)
class TranslafixFileResult:
    file: Path
    matched: int = 0
    changed: int = 0
    unchanged: int = 0
    error: str = ""
    backup_path: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass(slots=True)
class TranslationSuggestion:
    score: float
    source: str
    translation: str
    speaker: str
    file: Path
    row: int
    uid: str
    key: str


@dataclass(slots=True)
class ReferenceTranslationConflictEntry:
    """One reference entry whose msgid has multiple different translations."""

    key: str
    source: str
    translation: str
    speaker: str
    file: Path
    row: int
    uid: str
    line: int
    msgctxt: str
    variants: int = 0


@dataclass(slots=True)
class _SuggestionCandidate:
    key: str
    source: str
    translation: str
    speaker: str
    file: Path
    row: int
    uid: str
    token_count: int


@dataclass(slots=True)
class TranslafixResult:
    source_files: int = 0
    target_files: int = 0
    source_entries: int = 0
    usable_translations: int = 0
    empty_source_entries: int = 0
    duplicate_same: int = 0
    conflicts: list[TranslafixSourceConflict] = field(default_factory=list)
    files: list[TranslafixFileResult] = field(default_factory=list)
    source_paths: list[Path] = field(default_factory=list)
    skipped_source_targets: int = 0

    @property
    def ambiguous_msgids(self) -> int:
        return len({c.msgid for c in self.conflicts})

    @property
    def total_matched(self) -> int:
        return sum(item.matched for item in self.files)

    @property
    def total_changed(self) -> int:
        return sum(item.changed for item in self.files)

    @property
    def total_unchanged(self) -> int:
        return sum(item.unchanged for item in self.files)

    @property
    def total_errors(self) -> int:
        return sum(1 for item in self.files if item.error)


CLT_MATCH_RE = re.compile(r"<\s*clt(?:[\s_]*(?:\d+|n))?\s*>", re.IGNORECASE)


WORD_RE = re.compile(r"\w+", re.UNICODE)


def _canonical_clt_tag(match: re.Match[str]) -> str:
    """Return a stable visible CLT tag for duplicate-review grouping."""
    raw = match.group(0)
    code_match = re.search(r"clt[\s_]*(\d+|n)?", raw, re.IGNORECASE)
    code = (code_match.group(1) if code_match else "") or ""
    return f"<CLT {code.lower()}>" if code else "<CLT>"


def suggestion_match_key(text: str) -> str:
    """Return the normalized source text used for fuzzy suggestions."""
    key = visible_text(msgid_match_key(text)).lower()
    key = re.sub(r"\s+", " ", key).strip()
    return key


def _suggestion_tokens(key: str) -> set[str]:
    tokens = {token for token in WORD_RE.findall(key) if len(token) >= 2}
    if tokens:
        return tokens
    # Fallback for very short strings like "OK" or punctuation-heavy game UI text.
    compact = re.sub(r"\s+", "", key)
    return {compact} if compact else set()


class TranslationSuggestionIndex:
    """Fast local fuzzy index built from Translafixer source .po files.

    A full SequenceMatcher pass over every translated line can lag on big folders.
    This index first narrows candidates by shared word tokens and approximate length,
    then runs SequenceMatcher only over the smaller candidate set.
    """

    def __init__(self, candidates: Iterable[_SuggestionCandidate] = ()):  # noqa: B008
        self.candidates: list[_SuggestionCandidate] = []
        self.by_token: dict[str, list[int]] = defaultdict(list)
        self.by_first_char: dict[str, list[int]] = defaultdict(list)
        self.by_key: dict[str, list[int]] = defaultdict(list)
        self._dedupe: set[tuple[str, str]] = set()
        for candidate in candidates:
            self.add(candidate)

    def add(self, candidate: _SuggestionCandidate) -> None:
        if not candidate.key or not candidate.translation.strip():
            return
        dedupe = (candidate.key, candidate.translation)
        if dedupe in self._dedupe:
            return
        self._dedupe.add(dedupe)
        idx = len(self.candidates)
        self.candidates.append(candidate)
        self.by_key[candidate.key].append(idx)
        tokens = _suggestion_tokens(candidate.key)
        for token in tokens:
            self.by_token[token].append(idx)
        if candidate.key:
            self.by_first_char[candidate.key[:1]].append(idx)

    @classmethod
    def from_translafixer_sources(
        cls,
        sources: str | Path | Iterable[str | Path],
        *,
        exclude_files: Iterable[str | Path] = (),
        log: Callable[[str], None] | None = None,
    ) -> tuple["TranslationSuggestionIndex", TranslafixResult]:
        result = TranslafixResult()
        index = cls()
        excluded = {_resolve(Path(path)) for path in exclude_files}

        for po_path in _iter_source_po_files(sources):
            result.source_files += 1
            result.source_paths.append(po_path)
            if _resolve(po_path) in excluded:
                result.skipped_source_targets += 1
                continue
            try:
                po_file = load_po(po_path)
            except Exception as exc:
                if log is not None:
                    log(f"ERROR reading suggestion source {po_path}: {exc}")
                continue
            for row, entry in enumerate(po_file.entries):
                result.source_entries += 1
                source = _norm(entry.msgid)
                translation = _norm(entry.msgstr)
                if not source.strip():
                    continue
                if not translation.strip():
                    result.empty_source_entries += 1
                    continue
                key = suggestion_match_key(source)
                if not key:
                    continue
                tokens = _suggestion_tokens(key)
                index.add(
                    _SuggestionCandidate(
                        key=key,
                        source=entry.msgid,
                        translation=entry.msgstr,
                        speaker=entry.speaker,
                        file=po_path,
                        row=row,
                        uid=entry.uid,
                        token_count=len(tokens),
                    )
                )
        result.usable_translations = len(index.candidates)
        return index, result

    def _prefilter_indexes(self, target_key: str, *, max_candidates: int) -> list[int]:
        tokens = _suggestion_tokens(target_key)
        if not tokens:
            return []
        max_candidates = max(1, max_candidates)
        counts: dict[int, int] = defaultdict(int)

        # Exact msgid matches are always worth scoring first. They are usually the
        # best suggestion and this avoids losing them in very large folders full of
        # common words.
        exact_indexes = self.by_key.get(target_key, [])
        exact_set = set(exact_indexes)

        # Large source folders can contain very common words such as "the", "you",
        # or speaker names. Counting the rarest shared tokens first keeps the
        # candidate pool small without the old early-stop behavior that could miss
        # a better match later in the list.
        ordered_tokens = sorted(tokens, key=lambda token: len(self.by_token.get(token, ())))
        useful_tokens = [token for token in ordered_tokens if self.by_token.get(token)]
        if len(useful_tokens) > 8:
            useful_tokens = useful_tokens[:8]

        for token in useful_tokens:
            for idx in self.by_token.get(token, []):
                counts[idx] += 1

        # If there is no shared token, still try tiny UI strings by first char.
        if not counts and len(target_key) <= 16:
            return self.by_first_char.get(target_key[:1], [])[:max_candidates]

        target_len = len(target_key)
        min_len = max(1, int(target_len * 0.45))
        max_len = max(min_len, int(target_len * 1.90) + 8)
        target_token_count = len(tokens)
        ranked: list[tuple[float, int]] = []
        for idx, shared in counts.items():
            candidate = self.candidates[idx]
            candidate_len = len(candidate.key)
            if candidate_len < min_len or candidate_len > max_len:
                continue
            overlap = shared / max(1, min(target_token_count, candidate.token_count))
            len_ratio = min(target_len, candidate_len) / max(target_len, candidate_len)
            # Cheap approximate rank. Real score is SequenceMatcher below.
            exact_bonus = 1.0 if idx in exact_set else 0.0
            ranked.append((exact_bonus + overlap * 0.75 + len_ratio * 0.25, idx))
        ranked.sort(reverse=True)

        picked: list[int] = []
        seen: set[int] = set()
        for idx in exact_indexes:
            if idx not in seen:
                picked.append(idx)
                seen.add(idx)
        for _approx, idx in ranked:
            if idx in seen:
                continue
            picked.append(idx)
            seen.add(idx)
            if len(picked) >= max_candidates:
                break
        return picked[:max_candidates]

    def suggest(
        self,
        source: str,
        *,
        min_score: float = 0.70,
        limit: int = 5,
        max_candidates: int = 800,
        excellent_score: float = 0.95,
        excellent_limit: int = 3,
        distinct_limit: int = 5,
    ) -> list[TranslationSuggestion]:
        target_key = suggestion_match_key(source)
        if not target_key:
            return []
        # The UI default is still 70%, but callers may search below 70% for hard
        # cases. We score the whole prefiltered pool instead of stopping after a
        # small number of >70% hits, so late better matches are not missed.
        min_score = max(0.0, min(1.0, min_score))
        excellent_score = max(min_score, min(1.0, excellent_score))
        ranked: list[tuple[float, _SuggestionCandidate]] = []
        seen_translations: set[str] = set()

        for idx in self._prefilter_indexes(target_key, max_candidates=max_candidates):
            candidate = self.candidates[idx]
            # Show the same translated suggestion only once, even if many source lines/files have it.
            translation_key = re.sub(r"\s+", " ", _norm(candidate.translation)).strip().casefold()
            if not translation_key or translation_key in seen_translations:
                continue

            matcher = SequenceMatcher(None, target_key, candidate.key)
            if matcher.quick_ratio() < min_score:
                continue
            score = matcher.ratio()
            if score < min_score:
                continue

            seen_translations.add(translation_key)
            ranked.append((score, candidate))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [
            TranslationSuggestion(
                score=score,
                source=candidate.source,
                translation=candidate.translation,
                speaker=candidate.speaker,
                file=candidate.file,
                row=candidate.row,
                uid=candidate.uid,
                key=candidate.key,
            )
            for score, candidate in ranked[:limit]
        ]




def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def msgid_match_key(text: str) -> str:
    """Return the Translafixer comparison key for original text.

    CLT tags are layout/control markers, not real English text. Matching ignores
    them and collapses whitespace so these two originals match:
    ``<CLT 4>Hello\n<CLT>`` and ``Hello``.
    """
    stripped = CLT_MATCH_RE.sub(" ", _norm(text))
    return re.sub(r"\s+", " ", stripped).strip()


def reference_duplicate_msgid_key(text: str) -> str:
    """Return the duplicate-review key while preserving CLT tags.

    Unlike Translafixer fill/suggestions, the reference duplicate view treats CLT
    tags as part of the source sentence, so ``<CLT 4>Hello<CLT>`` and ``Hello``
    are reviewed as different source groups. Whitespace is still collapsed, and
    CLT tag spelling/spacing is canonicalized to avoid noisy differences such as
    ``<clt_4>`` versus ``<CLT 4>``.
    """
    normalized = CLT_MATCH_RE.sub(_canonical_clt_tag, _norm(text))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"(<CLT(?: (?:\d+|n))?>)\s+", r"\1", normalized)
    normalized = re.sub(r"\s+(<CLT(?: (?:\d+|n))?>)", r"\1", normalized)
    return normalized


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def iter_source_po_files(sources: str | Path | Iterable[str | Path]) -> Iterable[Path]:
    """Yield unique source .po files from files and/or folders.

    Explicit .po files are accepted even when named ``- Copy.po`` because choosing
    a file is intentional. Folders are expanded recursively with normal scanner
    safety rules and ``Copy.po`` files skipped.
    """
    if isinstance(sources, (str, Path)):
        source_items: Iterable[str | Path] = [sources]
    else:
        source_items = sources

    seen: set[Path] = set()
    for item in source_items:
        p = Path(item).expanduser()
        candidates: Iterable[Path]
        if p.is_dir():
            candidates = iter_po_files(p, include_copy=False)
        elif p.is_file() and p.suffix.lower() == ".po":
            candidates = [p]
        else:
            continue

        for candidate in candidates:
            resolved = _resolve(candidate)
            if resolved in seen:
                continue
            seen.add(resolved)
            yield Path(candidate)


def collect_source_po_files(sources: str | Path | Iterable[str | Path]) -> list[Path]:
    """Return source .po files from selected files/folders as a concrete list."""
    return list(iter_source_po_files(sources))


def _iter_source_po_files(sources: str | Path | Iterable[str | Path]) -> Iterable[Path]:
    # Backward-compatible internal alias.
    yield from iter_source_po_files(sources)


def build_translation_map(
    source_files: str | Path | Iterable[str | Path],
    *,
    include_empty: bool = False,
) -> tuple[dict[str, str], TranslafixResult]:
    """Build msgid -> msgstr from selected correct translation files.

    Matching intentionally uses the original text (msgid), not file name or context.
    Empty source msgstr values are always ignored so Translafixer cannot blank out
    target translations. ``include_empty`` is accepted only for compatibility and
    is intentionally ignored. If the same msgid has different non-empty
    translations in the source files, that msgid is marked ambiguous and removed
    from the usable map so the fixer does not write a wrong translation into
    another context.
    """
    result = TranslafixResult()
    translations: dict[str, str] = {}
    ambiguous: set[str] = set()

    for po_path in _iter_source_po_files(source_files):
        result.source_files += 1
        result.source_paths.append(po_path)
        po_file = load_po(po_path)
        for entry in po_file.entries:
            result.source_entries += 1
            msgid = msgid_match_key(entry.msgid)
            msgstr = _norm(entry.msgstr)
            if not msgid:
                continue
            if not msgstr.strip():
                result.empty_source_entries += 1
                continue
            if msgid in ambiguous:
                continue
            existing = translations.get(msgid)
            if existing is None:
                translations[msgid] = msgstr
                continue
            if existing == msgstr:
                result.duplicate_same += 1
                continue
            ambiguous.add(msgid)
            translations.pop(msgid, None)
            result.conflicts.append(
                TranslafixSourceConflict(
                    msgid=msgid,
                    first_translation=existing,
                    other_translation=msgstr,
                    file=po_path,
                    line=entry.line,
                )
            )

    result.usable_translations = len(translations)
    return translations, result


def _find_reference_duplicate_source_entries(
    reference_files: str | Path | Iterable[str | Path],
    *,
    include_empty: bool = True,
    only_conflicts: bool = True,
) -> tuple[list[ReferenceTranslationConflictEntry], TranslafixResult]:
    """Return repeated reference-source entries.

    When ``only_conflicts`` is true, only duplicate source groups with different
    translations are returned. When false, every duplicate source group is
    returned, even when all translations are identical. Empty msgstr rows are
    included by default so missing translations can be fixed from nearby
    duplicates, but groups where every translation is empty are skipped.
    """
    result = TranslafixResult()
    grouped: dict[str, list[ReferenceTranslationConflictEntry]] = defaultdict(list)

    for po_path in _iter_source_po_files(reference_files):
        result.source_files += 1
        result.source_paths.append(po_path)
        po_file = load_po(po_path)
        for row, entry in enumerate(po_file.entries):
            result.source_entries += 1
            key = reference_duplicate_msgid_key(entry.msgid)
            if not key:
                continue
            translation = _norm(entry.msgstr)
            if not translation.strip():
                result.empty_source_entries += 1
                if not include_empty:
                    continue
            grouped[key].append(
                ReferenceTranslationConflictEntry(
                    key=key,
                    source=entry.msgid,
                    translation=entry.msgstr,
                    speaker=entry.speaker,
                    file=po_path,
                    row=row,
                    uid=entry.uid,
                    line=entry.line,
                    msgctxt=entry.msgctxt or "",
                )
            )

    duplicate_entries: list[ReferenceTranslationConflictEntry] = []
    for key, entries in sorted(grouped.items(), key=lambda item: item[0].casefold()):
        if len(entries) <= 1:
            continue
        distinct_with_empty = {re.sub(r"\s+", " ", _norm(entry.translation)).strip() for entry in entries}
        distinct_non_empty = {value for value in distinct_with_empty if value}
        if not distinct_non_empty:
            # All translations for this duplicate source are empty. They are not
            # actionable in the duplicate view because there is no translation to
            # compare or copy from.
            continue
        distinct = distinct_with_empty if include_empty else distinct_non_empty
        variants = len(distinct)
        has_conflict = variants > 1
        if not has_conflict:
            result.duplicate_same += len(entries) - 1
            if only_conflicts:
                continue
        entries = sorted(entries, key=lambda entry: (str(entry.file).casefold(), entry.row))
        for entry in entries:
            entry.variants = max(1, variants)
        duplicate_entries.extend(entries)
        if has_conflict:
            for entry in entries:
                result.conflicts.append(
                    TranslafixSourceConflict(
                        msgid=entry.key,
                        first_translation="",
                        other_translation=entry.translation,
                        file=entry.file,
                        line=entry.line,
                    )
                )

    result.usable_translations = len(duplicate_entries)
    return duplicate_entries, result


def find_reference_translation_conflicts(
    reference_files: str | Path | Iterable[str | Path],
    *,
    include_empty: bool = True,
) -> tuple[list[ReferenceTranslationConflictEntry], TranslafixResult]:
    """Return reference entries where the same msgid has different translations.

    Grouping preserves CLT tags, so the duplicate-conflict view treats strings
    with different CLT markup as different source sentences. Whitespace is still
    normalized. Empty msgstr values are included by default unless every entry
    in the duplicate group is empty.
    """
    return _find_reference_duplicate_source_entries(
        reference_files,
        include_empty=include_empty,
        only_conflicts=True,
    )


def find_reference_duplicate_sources(
    reference_files: str | Path | Iterable[str | Path],
    *,
    include_empty: bool = True,
) -> tuple[list[ReferenceTranslationConflictEntry], TranslafixResult]:
    """Return repeated reference sources, excluding all-empty groups."""
    return _find_reference_duplicate_source_entries(
        reference_files,
        include_empty=include_empty,
        only_conflicts=False,
    )


def apply_translafix(
    source_files: str | Path | Iterable[str | Path],
    target_folder: str | Path,
    *,
    dry_run: bool = True,
    create_backup: bool = True,
    include_empty: bool = False,
    log: Callable[[str], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> TranslafixResult:
    """Rewrite target msgstr values when target msgid exists in source files.

    Selected source files supply known-good translations. Empty source msgstr values
    are always skipped, even if ``include_empty`` is passed by old callers, so the
    fixer never copies blank translations over real target text. The target folder
    is scanned recursively and every non-Copy .po file is updated by matching
    original msgid. If a selected source file also lives inside the target folder,
    it is skipped so the fixer never rewrites its own source material.
    """
    translations, result = build_translation_map(source_files)
    if log is not None:
        log(f"Source files: {result.source_files} | usable msgid translations: {result.usable_translations}")
        if result.conflicts:
            log(f"Ambiguous source msgid skipped: {result.ambiguous_msgids}")

    source_resolved = {_resolve(path) for path in result.source_paths}
    target_paths = []
    for po_path in iter_po_files(target_folder):
        if _resolve(po_path) in source_resolved:
            result.skipped_source_targets += 1
            continue
        target_paths.append(po_path)
    result.target_files = len(target_paths)

    if log is not None and result.skipped_source_targets:
        log(f"Skipped selected source files inside target folder: {result.skipped_source_targets}")

    for po_path in target_paths:
        if stop_requested is not None and stop_requested():
            break

        file_result = TranslafixFileResult(file=po_path)
        result.files.append(file_result)
        try:
            po_file = load_po(po_path)
            changed = False
            for entry in po_file.entries:
                if stop_requested is not None and stop_requested():
                    break
                msgid = msgid_match_key(entry.msgid)
                if msgid not in translations:
                    continue
                file_result.matched += 1
                replacement = translations[msgid]
                if _norm(entry.msgstr) == replacement:
                    file_result.unchanged += 1
                    continue
                entry.msgstr = replacement
                file_result.changed += 1
                changed = True

            if changed and not dry_run:
                if create_backup:
                    backup_path = po_path.with_name(po_path.name + ".translafixer.bak")
                    if not backup_path.exists():
                        shutil.copy2(po_path, backup_path)
                    file_result.backup_path = backup_path
                save_po(po_file, po_path)

            if log is not None and (file_result.changed or file_result.matched):
                action = "would change" if dry_run else "changed"
                log(f"{po_path}: matched={file_result.matched} | {action}={file_result.changed} | unchanged={file_result.unchanged}")
        except Exception as exc:
            file_result.error = str(exc)
            if log is not None:
                log(f"ERROR {po_path}: {exc}")

    return result
