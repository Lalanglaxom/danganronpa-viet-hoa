from __future__ import annotations

from email.mime import text
import re
from dataclasses import dataclass
from pathlib import Path

from .discovery import iter_po_files
from .po_io import parse_po_text
from .text_utils import visible_text


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
    hay = text
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
        return whole_word_pattern.search(hay) is not None
    if not case_sensitive:
        hay = hay.lower()
    return needle in hay


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
#     needle_visible = visible_text(phrase)
#     if not needle_visible or not (search_msgid or search_msgstr):
#         return results
def search_path(
    root: str | Path,
    phrase: str,
    search_msgid: bool = True,
    search_msgstr: bool = True,
    case_sensitive: bool = False,
    whole_word: bool = False,
) -> list[SearchResult]:
    results: list[SearchResult] = []
    base = Path(root)
    needle_visible = phrase
    if not needle_visible or not (search_msgid or search_msgstr):
        return results
    
    whole_word_pattern = _compile_whole_word(needle_visible, case_sensitive) if whole_word else None
    needle = needle_visible if case_sensitive or whole_word else needle_visible.lower()

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

        if not _file_can_contain_match(
            raw_text,
            needle,
            case_sensitive=case_sensitive,
            whole_word_pattern=whole_word_pattern,
        ):
            continue

        po = parse_po_text(raw_text, path)
        for entry in po.entries:
            hit_id = search_msgid and _matches_prepared(
                entry.msgid,
                needle,
                case_sensitive=case_sensitive,
                whole_word_pattern=whole_word_pattern,
            )
            hit_str = search_msgstr and _matches_prepared(
                entry.msgstr,
                needle,
                case_sensitive=case_sensitive,
                whole_word_pattern=whole_word_pattern,
            )
            if hit_id or hit_str:
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
                    )
                )
    return results
