from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .discovery import iter_po_files
from .po_io import load_po
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


def _matches(text: str, phrase: str, case_sensitive: bool, whole_word: bool) -> bool:
    hay = visible_text(text)
    needle = visible_text(phrase)
    if not case_sensitive:
        hay = hay.lower()
        needle = needle.lower()
    if not hay or not needle:
        return False
    if not whole_word:
        return needle in hay
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", hay, flags) is not None


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
    for path in iter_po_files(base):
        po = load_po(path)
        for entry in po.entries:
            hit_id = search_msgid and _matches(entry.msgid, phrase, case_sensitive, whole_word)
            hit_str = search_msgstr and _matches(entry.msgstr, phrase, case_sensitive, whole_word)
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
