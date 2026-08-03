from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TypeAlias

from .discovery import iter_po_files
from .text_index import (
    TextDocument,
    TextVariants,
    ensure_indexed,
    get_text_document,
    invalidate_text_index,
    make_text_variants,
)
from .text_utils import linebreak_insensitive_visible_text, user_multiline_text, visible_text


@dataclass(slots=True)
class SearchResult:
    file: Path
    uid: str
    msgctxt: str
    msgid: str
    msgstr: str
    line: int
    hit_msgid: bool
    hit_msgstr: bool
    hit_speaker: bool = False


PreparedCriterion: TypeAlias = tuple[str, str, re.Pattern[str] | None, re.Pattern[str] | None]
PreparedExpression: TypeAlias = list[list[PreparedCriterion]]
SearchProgressCallback: TypeAlias = Callable[[int, int, Path], None]

_SEARCH_PARALLEL_THRESHOLD = 12
_SEARCH_MAX_WORKERS = 8


def split_search_expression(text: str) -> list[list[str]]:
    """Parse ``|`` as OR and ``&`` as AND, with escaped operators literal."""
    expression: list[list[str]] = []
    and_terms: list[str] = []
    current: list[str] = []

    def finish_term() -> None:
        value = user_multiline_text("".join(current).strip())
        current.clear()
        if value:
            and_terms.append(value)

    def finish_group() -> None:
        finish_term()
        if and_terms:
            expression.append(and_terms.copy())
            and_terms.clear()

    source = text or ""
    index = 0
    while index < len(source):
        char = source[index]
        if char == "\\" and index + 1 < len(source) and source[index + 1] in {"|", "&", "\\"}:
            current.append(source[index + 1])
            index += 2
            continue
        if char == "&":
            finish_term()
        elif char == "|":
            finish_group()
        else:
            current.append(char)
        index += 1
    finish_group()
    return expression


def split_search_criteria(text: str) -> list[str]:
    """Return all terms from the current ``|``/``&`` search expression."""
    return [term for group in split_search_expression(text) for term in group]


def _prepare_expression(
    text: str,
    *,
    case_sensitive: bool,
    whole_word: bool,
    raw: bool,
) -> PreparedExpression:
    prepared_expression: PreparedExpression = []
    for group in split_search_expression(text):
        prepared_group: list[PreparedCriterion] = []
        for source_value in group:
            value = source_value if raw else visible_text(source_value)
            if not value:
                continue
            folded_value = "" if raw else linebreak_insensitive_visible_text(value)
            pattern = _compile_whole_word(value, case_sensitive) if whole_word else None
            folded_pattern = _compile_whole_word(folded_value, case_sensitive) if whole_word and folded_value else None
            needle = value if case_sensitive or whole_word else value.lower()
            folded_needle = folded_value if case_sensitive or whole_word else folded_value.lower()
            prepared_group.append((needle, folded_needle, pattern, folded_pattern))
        if prepared_group:
            prepared_expression.append(prepared_group)
    return prepared_expression


def _compile_whole_word(needle: str, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", flags)


def _as_variants(text: str | TextVariants) -> TextVariants:
    return text if isinstance(text, TextVariants) else make_text_variants(text)


def _matches_prepared(
    text: str | TextVariants,
    needle: str,
    folded_needle: str,
    *,
    case_sensitive: bool,
    raw: bool,
    whole_word_pattern: re.Pattern[str] | None,
    folded_whole_word_pattern: re.Pattern[str] | None,
) -> bool:
    variants = _as_variants(text)
    hay = variants.raw if raw else variants.visible
    folded_hay = "" if raw else variants.folded
    if not hay and not folded_hay:
        return False
    if whole_word_pattern is not None:
        if whole_word_pattern.search(hay) is not None:
            return True
        return folded_whole_word_pattern is not None and folded_whole_word_pattern.search(folded_hay) is not None
    if not case_sensitive:
        hay = variants.raw_lower if raw else variants.visible_lower
        folded_hay = "" if raw else variants.folded_lower
    return needle in hay or (bool(folded_needle) and folded_needle in folded_hay)


def clear_search_cache() -> None:
    """Clear the shared reusable text index, mainly for tests and diagnostics."""
    invalidate_text_index()


def _file_can_contain_match(
    document: TextDocument,
    needle: str,
    folded_needle: str,
    *,
    case_sensitive: bool,
    raw: bool,
    whole_word_pattern: re.Pattern[str] | None,
    folded_whole_word_pattern: re.Pattern[str] | None,
) -> bool:
    variants = document.fields
    hay = variants.raw if raw else variants.visible
    folded_hay = "" if raw else variants.folded
    if not hay and not folded_hay:
        return False
    if whole_word_pattern is not None:
        if whole_word_pattern.search(hay) is not None:
            return True
        return folded_whole_word_pattern is not None and folded_whole_word_pattern.search(folded_hay) is not None
    if not case_sensitive:
        hay = variants.raw_lower if raw else variants.visible_lower
        folded_hay = "" if raw else variants.folded_lower
    return needle in hay or (bool(folded_needle) and folded_needle in folded_hay)


def _matches_expression(
    text: str | TextVariants,
    expression: PreparedExpression,
    *,
    case_sensitive: bool,
    raw: bool,
) -> bool:
    return any(
        all(
            _matches_prepared(
                text,
                needle,
                folded_needle,
                case_sensitive=case_sensitive,
                raw=raw,
                whole_word_pattern=whole_word_pattern,
                folded_whole_word_pattern=folded_whole_word_pattern,
            )
            for needle, folded_needle, whole_word_pattern, folded_whole_word_pattern in group
        )
        for group in expression
    )


def _matches_expression_across_texts(
    texts: list[str | TextVariants],
    expression: PreparedExpression,
    *,
    case_sensitive: bool,
    raw: bool,
) -> tuple[bool, list[bool]]:
    """Evaluate one expression across all enabled fields and report field hits."""
    matched = False
    field_hits = [False] * len(texts)
    for group in expression:
        group_hits = [False] * len(texts)
        group_matches = True
        for needle, folded_needle, whole_word_pattern, folded_whole_word_pattern in group:
            criterion_hits = [
                _matches_prepared(
                    text,
                    needle,
                    folded_needle,
                    case_sensitive=case_sensitive,
                    raw=raw,
                    whole_word_pattern=whole_word_pattern,
                    folded_whole_word_pattern=folded_whole_word_pattern,
                )
                for text in texts
            ]
            if not any(criterion_hits):
                group_matches = False
                break
            group_hits = [old or new for old, new in zip(group_hits, criterion_hits)]
        if group_matches:
            matched = True
            field_hits = [old or new for old, new in zip(field_hits, group_hits)]
    return matched, field_hits


def _file_can_contain_expression(
    document: TextDocument,
    expression: PreparedExpression,
    *,
    case_sensitive: bool,
    raw: bool,
) -> bool:
    return any(
        all(
            _file_can_contain_match(
                document,
                needle,
                folded_needle,
                case_sensitive=case_sensitive,
                raw=raw,
                whole_word_pattern=whole_word_pattern,
                folded_whole_word_pattern=folded_whole_word_pattern,
            )
            for needle, folded_needle, whole_word_pattern, folded_whole_word_pattern in group
        )
        for group in expression
    )


def _search_one_file(
    index: int,
    path: Path,
    *,
    phrase_expression: PreparedExpression,
    speaker_expression: PreparedExpression,
    search_msgid: bool,
    search_msgstr: bool,
    case_sensitive: bool,
    raw: bool,
) -> tuple[int, list[SearchResult]]:
    file_results: list[SearchResult] = []
    try:
        document = get_text_document(path)
        if phrase_expression and not _file_can_contain_expression(
            document,
            phrase_expression,
            case_sensitive=case_sensitive,
            raw=raw,
        ):
            return index, file_results
        if speaker_expression and not _file_can_contain_expression(
            document,
            speaker_expression,
            case_sensitive=case_sensitive,
            raw=raw,
        ):
            return index, file_results

        indexed = ensure_indexed(document)
        assert indexed.entries is not None
        for indexed_entry in indexed.entries:
            entry = indexed_entry.entry
            hit_speaker = bool(speaker_expression) and _matches_expression(
                indexed_entry.speaker,
                speaker_expression,
                case_sensitive=case_sensitive,
                raw=raw,
            )
            if speaker_expression and not hit_speaker:
                continue

            if phrase_expression:
                selected_texts: list[TextVariants] = []
                selected_fields: list[str] = []
                if search_msgid:
                    selected_texts.append(indexed_entry.msgid)
                    selected_fields.append("msgid")
                if search_msgstr:
                    selected_texts.append(indexed_entry.msgstr)
                    selected_fields.append("msgstr")
                phrase_matches, field_hits = _matches_expression_across_texts(
                    selected_texts,
                    phrase_expression,
                    case_sensitive=case_sensitive,
                    raw=raw,
                )
                if not phrase_matches:
                    continue
                hit_id = any(field == "msgid" and hit for field, hit in zip(selected_fields, field_hits))
                hit_str = any(field == "msgstr" and hit for field, hit in zip(selected_fields, field_hits))
            else:
                hit_id = False
                hit_str = False

            file_results.append(
                SearchResult(
                    file=path,
                    uid=entry.uid,
                    msgctxt=entry.msgctxt or "",
                    msgid=entry.msgid,
                    msgstr=entry.msgstr,
                    line=entry.line,
                    hit_msgid=hit_id,
                    hit_msgstr=hit_str,
                    hit_speaker=hit_speaker,
                )
            )
    except (OSError, UnicodeError):
        return index, file_results
    return index, file_results


def search_files(
    files: Iterable[str | Path],
    phrase: str,
    search_msgid: bool = True,
    search_msgstr: bool = True,
    case_sensitive: bool = False,
    whole_word: bool = False,
    speaker: str = "",
    raw: bool = False,
    progress: SearchProgressCallback | None = None,
) -> list[SearchResult]:
    results: list[SearchResult] = []
    paths = [Path(path) for path in files]
    phrase_expression = _prepare_expression(
        phrase,
        case_sensitive=case_sensitive,
        whole_word=whole_word,
        raw=raw,
    )
    speaker_expression = _prepare_expression(
        speaker,
        case_sensitive=case_sensitive,
        whole_word=whole_word,
        raw=raw,
    )
    if not phrase_expression and not speaker_expression:
        return results
    if phrase_expression and not (search_msgid or search_msgstr):
        return results

    total = len(paths)
    worker_kwargs = {
        "phrase_expression": phrase_expression,
        "speaker_expression": speaker_expression,
        "search_msgid": search_msgid,
        "search_msgstr": search_msgstr,
        "case_sensitive": case_sensitive,
        "raw": raw,
    }

    if total < _SEARCH_PARALLEL_THRESHOLD:
        for done, path in enumerate(paths, start=1):
            _index, file_results = _search_one_file(done - 1, path, **worker_kwargs)
            results.extend(file_results)
            if progress is not None:
                progress(done, total, path)
        return results

    buckets: dict[int, list[SearchResult]] = {}
    max_workers = min(_SEARCH_MAX_WORKERS, total, max(2, os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="po-search") as executor:
        future_to_item = {
            executor.submit(_search_one_file, index, path, **worker_kwargs): (index, path)
            for index, path in enumerate(paths)
        }
        for done, future in enumerate(as_completed(future_to_item), start=1):
            index, path = future_to_item[future]
            try:
                result_index, file_results = future.result()
            except Exception:
                result_index, file_results = index, []
            buckets[result_index] = file_results
            if progress is not None:
                progress(done, total, path)

    for index in range(total):
        results.extend(buckets.get(index, []))
    return results


def search_path(
    root: str | Path,
    phrase: str,
    search_msgid: bool = True,
    search_msgstr: bool = True,
    case_sensitive: bool = False,
    whole_word: bool = False,
    speaker: str = "",
    raw: bool = False,
    progress: SearchProgressCallback | None = None,
) -> list[SearchResult]:
    return search_files(
        iter_po_files(Path(root)),
        phrase,
        search_msgid=search_msgid,
        search_msgstr=search_msgstr,
        case_sensitive=case_sensitive,
        whole_word=whole_word,
        speaker=speaker,
        raw=raw,
        progress=progress,
    )
