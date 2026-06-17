from __future__ import annotations

import re
from pathlib import Path

from .discovery import iter_po_files
from .po_io import load_po, save_po
from .text_utils import visible_len

CLT_SPACE_RE = re.compile(r"<CLT\s+(\d+)>")


def wrap_msgstr(text: str, soft: int = 58, hard: int = 64, max_cuts: int = 2) -> tuple[str, bool]:
    """Wrap display text while ignoring CLT tags for visible length.

    This intentionally rewrites display line breaks. Run validator afterward.
    """
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
    if visible_len(flat) <= hard:
        fixed = flat + end_tag
        return fixed, fixed != original

    protected = CLT_SPACE_RE.sub(r"<CLT_\1>", flat)
    words = protected.split(" ")
    lines: list[str] = []

    def find_cut(items: list[str], limit: int) -> int:
        vis = 0
        for i, word in enumerate(items):
            vis += (1 if i else 0) + visible_len(word)
            if vis > limit:
                return max(i, 1)
        return len(items)

    for cut_num in range(max_cuts):
        soft_cut = find_cut(words, soft)
        if soft_cut >= len(words):
            break
        if cut_num == 0 and visible_len(" ".join(words[soft_cut:])) <= soft:
            cut_at = soft_cut
        else:
            hard_cut = find_cut(words, hard)
            cut_at = hard_cut if hard_cut < len(words) else soft_cut
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
