from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from .discovery import iter_po_files
from .po_io import parse_po_text
from .text_utils import user_multiline_text, visible_text


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


PreparedCriterion: TypeAlias = tuple[str, re.Pattern[str] | None]


def split_search_criteria(text: str) -> list[str]:
    """Split user search text into non-empty semicolon-separated criteria."""
    criteria: list[str] = []
    for part in (text or "").split(";"):
        value = visible_text(user_multiline_text(part))
        if value:
            criteria.append(value)
    return criteria


def _prepare_criteria(text: str, *, case_sensitive: bool, whole_word: bool) -> list[PreparedCriterion]:
    prepared: list[PreparedCriterion] = []
    for value in split_search_criteria(text):
        pattern = _compile_whole_word(value, case_sensitive) if whole_word else None
        needle = value if case_sensitive or whole_word else value.lower()
        prepared.append((needle, pattern))
    return prepared


def _raw_visible_text(text: str) -> str:
    # Convert common PO escapes before visible_text so the file-level prefilter
    # matches the same user-visible text that parsed entries would show.
    # return visible_text(
    #     text.replace("\\n", " ")
    #     .replace("\\r", " ")
    #     .replace("\\t", " ")
    #     .replace('\\"', '"')
    # )
    return text.replace("\\n", " ").replace("\\r", " ").replace("\\t", " ").replace('\\"', '"')


def _compile_whole_word(needle: str, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", flags)


# def _matches_prepared(
#     text: str,
#     needle: str,
#     *,
#     case_sensitive: bool,
#     whole_word_pattern: re.Pattern[str] | None,
# ) -> bool:
#     hay = visible_text(text)
#     if not hay:
#         return False
def _matches_prepared(
    text: str,
    needle: str,
    *,
    case_sensitive: bool,
    whole_word_pattern: re.Pattern[str] | None,
) -> bool:
    hay = visible_text(text)
    if not hay:
        return False
    if whole_word_pattern is not None:
        return whole_word_pattern.search(hay) is not None
    if not case_sensitive:
        hay = hay.lower()
    return needle in hay


def _file_can_contain_match(
    raw_text: str,
    needle: str,
    *,
    case_sensitive: bool,
    whole_word_pattern: re.Pattern[str] | None,
) -> bool:
    hay = _raw_visible_text(raw_text)
    if not hay:
        return False
    if whole_word_pattern is not None:
        if whole_word_pattern.search(hay) is not None:
            return True
        cmp_hay = hay if case_sensitive else hay.lower()
        parts = [part for part in re.split(r"\s+", needle) if part]
        if not case_sensitive:
            parts = [part.lower() for part in parts]
        return bool(parts) and all(part in cmp_hay for part in parts)
    if not case_sensitive:
        hay = hay.lower()
    if needle in hay:
        return True
    parts = [part for part in re.split(r"\s+", needle) if part]
    return bool(parts) and all(part in hay for part in parts)


def _matches_any_prepared(
    text: str,
    criteria: list[PreparedCriterion],
    *,
    case_sensitive: bool,
) -> bool:
    return any(
        _matches_prepared(
            text,
            needle,
            case_sensitive=case_sensitive,
            whole_word_pattern=whole_word_pattern,
        )
        for needle, whole_word_pattern in criteria
    )


def _file_can_contain_any_match(
    raw_text: str,
    criteria: list[PreparedCriterion],
    *,
    case_sensitive: bool,
) -> bool:
    return any(
        _file_can_contain_match(
            raw_text,
            needle,
            case_sensitive=case_sensitive,
            whole_word_pattern=whole_word_pattern,
        )
        for needle, whole_word_pattern in criteria
    )


# def search_path(
#     root: str | Path,
#     phrase: str,
#     search_msgid: bool = True,
#     search_msgstr: bool = True,
#     case_sensitive: bool = False,
#     whole_word: bool = False,
# ) -> list[SearchResult]:
#     results: list[SearchResult] = []
#     base = Path(root)
#     needle_visible = visible_text(user_multiline_text(phrase))
#     if not needle_visible or not (search_msgid or search_msgstr):
#         return results
def search_path(
    root: str | Path,
    phrase: str,
    search_msgid: bool = True,
    search_msgstr: bool = True,
    case_sensitive: bool = False,
    whole_word: bool = False,
    speaker: str = "",
) -> list[SearchResult]:
    results: list[SearchResult] = []
    base = Path(root)
    phrase_criteria = _prepare_criteria(
        phrase,
        case_sensitive=case_sensitive,
        whole_word=whole_word,
    )
    speaker_criteria = _prepare_criteria(
        speaker,
        case_sensitive=case_sensitive,
        whole_word=whole_word,
    )
    if not phrase_criteria and not speaker_criteria:
        return results
    if phrase_criteria and not (search_msgid or search_msgstr):
        return results

    for path in iter_po_files(base):
        try:
            raw_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                raw_text = path.read_text(encoding="utf-8-sig")
            except Exception:
                continue
        except OSError:
            continue

        if phrase_criteria and not _file_can_contain_any_match(
            raw_text,
            phrase_criteria,
            case_sensitive=case_sensitive,
        ):
            continue
        if speaker_criteria and not _file_can_contain_any_match(
            raw_text,
            speaker_criteria,
            case_sensitive=case_sensitive,
        ):
            continue

        po = parse_po_text(raw_text, path)
        for entry in po.entries:
            speaker_text = "\n".join(
                value
                for value in (entry.speaker, entry.msgctxt or "")
                if value
            )
            hit_speaker = bool(speaker_criteria) and _matches_any_prepared(
                speaker_text,
                speaker_criteria,
                case_sensitive=case_sensitive,
            )
            if speaker_criteria and not hit_speaker:
                continue

            if phrase_criteria:
                hit_id = search_msgid and _matches_any_prepared(
                    entry.msgid,
                    phrase_criteria,
                    case_sensitive=case_sensitive,
                )
                hit_str = search_msgstr and _matches_any_prepared(
                    entry.msgstr,
                    phrase_criteria,
                    case_sensitive=case_sensitive,
                )
                if not (hit_id or hit_str):
                    continue
            else:
                hit_id = False
                hit_str = False

            results.append(
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
    return results
