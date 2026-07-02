from __future__ import annotations

import re
from pathlib import Path

from .discovery import iter_po_files
from .po_io import load_po, save_po
from .text_utils import visible_len

CLT_SPACE_RE = re.compile(r"<CLT\s+(\d+)>")
# visible_len only strips <CLT> and <CLT N>; the protected form <CLT_N>
# (used internally during wrapping) must be stripped separately.
_PROTECTED_CLT_RE = re.compile(r"<CLT_\d+>")


def wrap_msgstr(text: str, soft: int = 58, hard: int = 64, max_cuts: int = 2) -> tuple[str, bool]:
    original = text
    if not text.strip():
        return text, False

    end_tag = ""
    if text.endswith("\n<CLT>"):
        end_tag = "\n<CLT>"
        text = text[:-6]
    elif text.endswith("<CLT>"):
        end_tag = "<CLT>"
        text = text[:-5]

    flat = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    total_len = visible_len(flat)

    # Dùng 64 cho các câu nhét vừa 2 dòng để tối đa diện tích dòng đầu.
    # Chỉ bóp về 58 khi tổng chữ quá dài (> 122) để các dòng nhìn đều nhau hơn.
    if total_len <= soft + hard:
        limit = hard
        n_cuts = 1
    else:
        limit = soft
        n_cuts = max_cuts

    if total_len <= limit:
        fixed = flat + end_tag
        return fixed, fixed != original

    protected = CLT_SPACE_RE.sub(r"<CLT_\1>", flat)
    words = protected.split(" ")
    lines: list[str] = []

    def _vis(word: str) -> int:
        return visible_len(_PROTECTED_CLT_RE.sub("", word))

    def find_cut(items: list[str], lim: int) -> int:
        vis = 0
        for i, word in enumerate(items):
            vis += (1 if i else 0) + _vis(word)
            if vis > lim:
                return max(i, 1)
        return len(items)

    for _ in range(n_cuts):
        cut_at = find_cut(words, limit)
        if cut_at >= len(words):
            break
        lines.append(" ".join(words[:cut_at]))
        words = words[cut_at:]

    if words:
        lines.append(" ".join(words))

    fixed = re.sub(r"<CLT_(\d+)>", r"<CLT \1>", "\n".join(lines)) + end_tag
    return fixed, fixed != original


def wrap_po_file(path: str | Path, soft: int = 58, hard: int = 64, max_cuts: int = 2, dry_run: bool = False) -> int:
    po = load_po(path)
    changed = 0
    for entry in po.entries:
        if not entry.msgstr.strip():
            continue
        fixed, did_change = wrap_msgstr(entry.msgstr, soft=soft, hard=hard, max_cuts=max_cuts)
        if did_change:
            entry.msgstr = fixed
            changed += 1
    if changed and not dry_run:
        save_po(po, path)
    return changed


def wrap_path(path: str | Path, soft: int = 58, hard: int = 64, max_cuts: int = 2, dry_run: bool = False) -> dict[Path, int]:
    results: dict[Path, int] = {}
    for po_path in iter_po_files(path):
        count = wrap_po_file(po_path, soft=soft, hard=hard, max_cuts=max_cuts, dry_run=dry_run)
        results[po_path] = count
    return results