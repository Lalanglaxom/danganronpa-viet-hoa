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

BASE64_WRAP_PRESET = {"soft": 58, "hard": 64, "max_cuts": 2}


def normalize_wrap_preset(value: object, fallback: object = BASE64_WRAP_PRESET) -> dict[str, int]:
    """Return a safe ``soft/hard/max_cuts`` preset dictionary.

    Presets are stored in JSON config, so older or hand-edited config files may
    contain missing keys, strings, or list/tuple values.  Keep the GUI resilient
    and clamp values to the same ranges used by its spin boxes.
    """

    if isinstance(fallback, dict):
        fallback_values = fallback
    elif isinstance(fallback, (list, tuple)) and len(fallback) >= 3:
        fallback_values = {"soft": fallback[0], "hard": fallback[1], "max_cuts": fallback[2]}
    else:
        fallback_values = BASE64_WRAP_PRESET

    if isinstance(value, dict):
        source = value
    elif isinstance(value, (list, tuple)) and len(value) >= 3:
        source = {"soft": value[0], "hard": value[1], "max_cuts": value[2]}
    else:
        source = {}

    def integer(key: str, minimum: int, maximum: int) -> int:
        raw = source.get(key, fallback_values.get(key, BASE64_WRAP_PRESET[key]))
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = int(fallback_values.get(key, BASE64_WRAP_PRESET[key]))
        return max(minimum, min(maximum, parsed))

    return {
        "soft": integer("soft", 1, 999),
        "hard": integer("hard", 1, 999),
        "max_cuts": integer("max_cuts", 1, 20),
    }


def default_wrap_presets(
    legacy_soft: object = 54,
    legacy_hard: object = 64,
    legacy_max_cuts: object = 2,
) -> list[dict[str, int]]:
    """Return four editable line-wrap presets.

    The first preset starts with the base-64 defaults, while all presets can be
    customized from the Line Wrap tab.
    """

    legacy = normalize_wrap_preset(
        {"soft": legacy_soft, "hard": legacy_hard, "max_cuts": legacy_max_cuts},
        BASE64_WRAP_PRESET,
    )
    return [dict(BASE64_WRAP_PRESET), dict(legacy), dict(legacy), dict(legacy)]


def normalize_wrap_presets(
    value: object,
    *,
    legacy_soft: object = 54,
    legacy_hard: object = 64,
    legacy_max_cuts: object = 2,
) -> list[dict[str, int]]:
    """Normalize config data into exactly four line-wrap presets."""

    defaults = default_wrap_presets(legacy_soft, legacy_hard, legacy_max_cuts)
    raw_presets = value if isinstance(value, list) else []
    presets = []
    for index in range(0, 4):
        raw = raw_presets[index] if index < len(raw_presets) else defaults[index]
        presets.append(normalize_wrap_preset(raw, defaults[index]))
    return presets


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
    if total_len <= soft * 2:
        limit = soft
        n_cuts = 1
    else:
        limit = hard
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