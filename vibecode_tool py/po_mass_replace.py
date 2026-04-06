"""
po_mass_replace.py — Mass replace inside .po msgstr fields with criteria.
=========================================================================

Run this file directly — a UI will appear.

Drag a .po file or folder onto the drop zone, OR use the Browse buttons.
Check/uncheck criteria in the UI to enable/disable them per run.

Dependencies:
  pip install tkinterdnd2      ← enables drag-and-drop (optional)
  (standard tkinter is always required)
"""

import os
import re

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

# ╔══════════════════════════════════════════════════════════════════╗
# ║                      ⚙  CRITERIA                                ║
# ║                                                                  ║
# ║  Each entry:                                                     ║
# ║    "label"      – shown as a checkbox in the UI                  ║
# ║    "character"  – substring matched against msgctxt,             ║
# ║                   e.g. "MAKOTO NAEGI". None = all characters.    ║
# ║    "scope"      – "clt:N" = only inside <CLT N>…<CLT> blocks,   ║
# ║                   None = entire msgstr.                          ║
# ║    "whole_word" – True  = only replace standalone words          ║
# ║                           (bounded by spaces / punctuation)      ║
# ║                   False = replace even inside other words        ║
# ║    "replace"    – list of (find, replacement) tuples,            ║
# ║                   applied in order, case-sensitive.              ║
# ╚══════════════════════════════════════════════════════════════════╝

CRITERIA = [
    # {
    #     "label":      'General: Tớ → mình',
    #     "character":  None,
    #     "scope":      "clt:4",
    #     "whole_word": True,
    #     "replace":    [("Tớ", "Mình"), ("tớ", "mình")],
    # },
    {
        "label":      'General: Tôi → mình',
        "character":  None,
        "scope":      "clt:4",
        "whole_word": True,
        "replace":    [("Tôi", "Mình"), ("tôi", "mình")],
    },
    # {
    #     "label":      'General: Tôi → mình',
    #     "character":  "KYOUKO",
    #     "scope":      None,
    #     "whole_word": None,
    #     "replace":    [("Tôi", "Tớ"), ("tôi", "tớ")],
    # },
    # {
    #     "label":      'MAKOTO: Tôi → tớ',
    #     "character":  "MAKOTO",
    #     "scope":      None,
    #     "whole_word": True,
    #     "replace":    [("Tôi", "Tớ"), ("tôi", "tớ")],
    # },
    # ── Add more criteria below ────────────────────────────────────
    # Whole-word, specific character, entire msgstr:
    # {
    #     "label":      'KIYOTAKA: Tôi → tớ',
    #     "character":  "KIYOTAKA",
    #     "scope":      None,
    #     "whole_word": True,
    #     "replace":    [("Tôi", "Tớ"), ("tôi", "tớ")],
    # },
    # Whole-word, specific character, entire msgstr:
    # {
    #     "label":      'TOUKO: Mình → Tôi',
    #     "character":  "TOUKO",
    #     "scope":      None,
    #     "whole_word": True,
    #     "replace":    [("Mình", "Tôi"), ("mình", "tôi")],
    # },
    # # Substring (replaces even inside other words):
    # {
    #     "label":      'Jack change',
    #     "character":  None,
    #     "scope":      None,
    #     "whole_word": False,
    #     "replace":    [("Genocide Jack", "Jack Đồ Tể")],
    # },
    # {
    #     "label":      'Fix truth bullet',
    #     "character":  None,
    #     "scope":      None,
    #     "whole_word": False,
    #     "replace":    [("Đạn Hy Vọng", "Đạn Sự Thật")],
    # },
    # {
    #     "label":      'Fix class trial',
    #     "character":  None,
    #     "scope":      None,
    #     "whole_word": False,
    #     "replace":    [("Lớp Xét Xử", "lớp xét xử")],
    # },
    # {
    #     "label":      'Thể thao → thể dục',
    #     "character":  None,
    #     "scope":      None,
    #     "whole_word": False,
    #     "replace":    [("thể thao", "thể dục"), ("Thể thao", "Thể dục"), ("thể chất", "thể dục")],
    # },
    # {
    #     "label":      'Đồng Monokuma → Xu Monokuma',
    #     "character":  None,
    #     "scope":      None,
    #     "whole_word": False,
    #     "replace":    [("Đồng Monokuma", "Xu Monokuma"), ("đồng Monokuma", "xu Monokuma")],
    # },
    {
        "label":      'Thực đơn → Menu',
        "character":  None,
        "scope":      None,
        "whole_word": False,
        "replace":    [("Thực đơn", "Menu"), ("thực đơn", "menu")],
    },
]

# ════════════════════════════════════════════════════════════════════
#  PO  HELPERS
# ════════════════════════════════════════════════════════════════════

def _po_raw_to_text(raw_block: str) -> str:
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', raw_block)
    text  = "".join(parts)
    return text.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")

def _text_to_po_val(text: str) -> str:
    esc = text.replace("\\", "\\\\").replace('"', '\\"')
    if "\n" not in esc:
        return f'"{esc}"'
    lines = esc.split("\n")
    parts = ['""']
    for i, line in enumerate(lines):
        suffix = "\\n" if i < len(lines) - 1 else ""
        parts.append(f'"{line}{suffix}"')
    if parts[-1] == '""':
        parts.pop()
    return "\n".join(parts)

# ════════════════════════════════════════════════════════════════════
#  REPLACEMENT  HELPERS
# ════════════════════════════════════════════════════════════════════

_PUNCT = r'\s.,!?…""\'\'()[\]{}<>:;—–\-\n'

def _apply_replacements(text: str, pairs: list[tuple[str, str]], whole_word: bool) -> str:
    for find, replacement in pairs:
        if whole_word:
            pattern = rf'(?:(?<=[{_PUNCT}])|(?:^))({re.escape(find)})(?=[{_PUNCT}]|$)'
            text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
        else:
            text = text.replace(find, replacement)
    return text

def _apply_in_clt_block(text: str, clt_n: int, pairs: list[tuple[str, str]], whole_word: bool) -> str:
    open_tag  = f"<CLT {clt_n}>"
    close_tag = "<CLT>"
    result = []
    pos = 0
    while pos < len(text):
        start = text.find(open_tag, pos)
        if start == -1:
            result.append(text[pos:])
            break
        result.append(text[pos:start + len(open_tag)])
        pos = start + len(open_tag)
        end = text.find(close_tag, pos)
        if end == -1:
            result.append(_apply_replacements(text[pos:], pairs, whole_word))
            break
        result.append(_apply_replacements(text[pos:end], pairs, whole_word))
        result.append(close_tag)
        pos = end + len(close_tag)
    return "".join(result)

def apply_criterion(text: str, criterion: dict) -> str:
    scope      = criterion["scope"]
    pairs      = criterion["replace"]
    whole_word = criterion.get("whole_word", True)
    if scope is None:
        return _apply_replacements(text, pairs, whole_word)
    if scope.startswith("clt:"):
        return _apply_in_clt_block(text, int(scope.split(":")[1]), pairs, whole_word)
    raise ValueError(f"Unknown scope: {scope!r}")

# ════════════════════════════════════════════════════════════════════
#  FILE  PROCESSING
# ════════════════════════════════════════════════════════════════════

def process_po_file(filepath: str, active_criteria: list[dict], log) -> int:
    try:
        with open(filepath, encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        log(f"  ⚠  Cannot read {filepath}: {e}", "warn")
        return 0

    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    Q = r'"(?:[^"\\]|\\.)*"'
    entry_pat = re.compile(
        r"((?:#[^\n]*\n)*(?:msgctxt\s+" + Q + r"\n)?)"
        r"(msgid\s+)((?:" + Q + r"\s*)+)"
        r"(msgstr\s+)((?:" + Q + r"\s*)*)",
        re.MULTILINE,
    )

    updated = raw
    changed = 0
    offset  = 0

    for m in entry_pat.finditer(raw):
        msgid_text  = _po_raw_to_text(m.group(3))
        msgstr_raw  = m.group(5)
        msgstr_text = _po_raw_to_text(msgstr_raw)
        if not msgid_text.strip() or not msgstr_text.strip():
            continue

        ctx_m = re.search(r'msgctxt\s+"([^"]+)"', m.group(1))
        ctx   = ctx_m.group(1) if ctx_m else ""

        new_msgstr = msgstr_text
        for criterion in active_criteria:
            char_filter = criterion.get("character")
            if char_filter and char_filter.upper() not in ctx.upper():
                continue
            new_msgstr = apply_criterion(new_msgstr, criterion)

        if new_msgstr == msgstr_text:
            continue

        log(f"  ✎  {ctx or '???'}", "info")
        for ol, nl in zip(msgstr_text.splitlines(), new_msgstr.splitlines()):
            if ol != nl:
                log(f"       - {ol}", "del")
                log(f"       + {nl}", "add")

        trailing_ws = re.search(r'\s*$', msgstr_raw).group()
        new_block   = _text_to_po_val(new_msgstr) + trailing_ws
        new_entry   = m.group(1) + m.group(2) + m.group(3) + m.group(4) + new_block
        start = m.start(0) + offset
        end   = m.end(0)   + offset
        updated = updated[:start] + new_entry + updated[end:]
        offset += len(new_entry) - (m.end(0) - m.start(0))
        changed += 1

    if changed:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated)
    return changed

def process_path(path: str, active_criteria: list[dict], log) -> tuple[int, int]:
    if not active_criteria:
        log("  ⚠  No criteria selected.", "warn")
        return 0, 0

    total_files = total_entries = 0

    if os.path.isfile(path):
        if not path.lower().endswith(".po"):
            log(f"  ⚠  Not a .po file: {path}", "warn")
            return 0, 0
        log(f"  File: {path}", "info")
        n = process_po_file(path, active_criteria, log)
        if n:
            log(f"  → {n} entry/entries updated.", "good")
            total_files += 1; total_entries += n
        else:
            log("  ✓ Nothing to change.", "good")

    elif os.path.isdir(path):
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames.sort()
            for fname in sorted(filenames):
                if not fname.lower().endswith(".po") or "-copy" in fname.lower():
                    continue
                fpath = os.path.join(dirpath, fname)
                rel   = os.path.relpath(fpath, path)
                n = process_po_file(fpath, active_criteria, log)
                if n:
                    log(f"\n  {'─'*55}", "info")
                    log(f"  {rel}  →  {n} updated.", "good")
                    total_files += 1; total_entries += n
    else:
        log(f"  ⚠  Path not found: {path}", "warn")

    return total_files, total_entries
