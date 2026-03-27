"""
po_linebreak_fixer.py — Auto-fix line lengths in Vietnamese .po files
======================================================================

Wraps msgstr lines that are too long, ignoring <CLT N> / <CLT> colour tags
when measuring length. Automatically strips trailing \n from both msgid and msgstr.
"""

import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox

# ╔══════════════════════════════════════════════════════════════════╗
# ║                      ⚙  SETTINGS                                ║
# ╚══════════════════════════════════════════════════════════════════╝

SOFT_LIMIT: int = 58   
HARD_LIMIT: int = 64

_CLT_RE = re.compile(r"<CLT(?:\s+\d+)?>")

# ════════════════════════════════════════════════════════════════════
#  PO  LOW-LEVEL  HELPERS  
# ════════════════════════════════════════════════════════════════════

def _po_raw_to_text(raw_block: str) -> str:
    """Collapse a raw PO quoted string to a plain Python string."""
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', raw_block)
    text  = "".join(parts)
    return text.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def _text_to_po_val(text: str) -> str:
    """Encode a plain Python string back to PO value format."""
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
#  LINE-LENGTH  HELPERS
# ════════════════════════════════════════════════════════════════════

def visible_len(text: str) -> int:
    """Return display length of *text*, stripping all CLT colour tags."""
    return len(_CLT_RE.sub("", text))


def wrap_display_line(line: str, soft: int = SOFT_LIMIT, hard: int = HARD_LIMIT) -> list[str]:
    if not _CLT_RE.sub("", line).strip():
        return [line]

    if visible_len(line) < hard:
        return [line]

    words = line.split(" ")
    result: list[str] = []
    current_words: list[str] = []
    current_vis: int = 0

    for word in words:
        word_vis = visible_len(word)
        space_vis = 1 if current_words else 0

        if current_words and current_vis + space_vis + word_vis > soft:
            result.append(" ".join(current_words))
            current_words = [word]
            current_vis = word_vis
        else:
            current_words.append(word)
            current_vis += space_vis + word_vis

    if current_words:
        result.append(" ".join(current_words))

    return result


def fix_msgstr(msgstr_text: str, soft: int = SOFT_LIMIT, hard: int = HARD_LIMIT) -> tuple[str, bool]:
    display_lines = msgstr_text.split("\n")
    new_lines: list[str] = []
    changed = False

    for dline in display_lines:
        wrapped = wrap_display_line(dline, soft, hard)
        new_lines.extend(wrapped)
        if len(wrapped) > 1:
            changed = True

    fixed = "\n".join(new_lines)
    return fixed, changed


# ════════════════════════════════════════════════════════════════════
#  PO  FILE  PROCESSING
# ════════════════════════════════════════════════════════════════════

def fix_po_file(filepath: str, dry_run: bool = False) -> int:
    with open(filepath, encoding="utf-8") as f:
        raw = f.read()

    raw = raw.replace("\r\n", "\n").replace("\r", "\n")

    Q = r'"(?:[^"\\]|\\.)*"'
    
    # ── Updated Regex to safely isolate blocks without eating layout newlines ──
    entry_pat = re.compile(
        r"((?:#[^\n]*\n)*(?:msgctxt\s+" + Q + r"\n)?)"  # group 1: comments & msgctxt
        r"(msgid\s+)((?:" + Q + r"\s*)+)"              # group 2: 'msgid', group 3: strings
        r"(msgstr\s+)((?:" + Q + r"\s*)*)",            # group 4: 'msgstr', group 5: strings
        re.MULTILINE,
    )

    updated = raw
    fixed_count = 0
    offset = 0

    for m in entry_pat.finditer(raw):
        msgid_raw = m.group(3)
        msgstr_raw = m.group(5)

        msgid_text = _po_raw_to_text(msgid_raw)
        msgstr_text = _po_raw_to_text(msgstr_raw)

        changed = False
        report_wrapping = False
        old_msgstr_text = msgstr_text

        # 1. Force strip trailing \n from both strings to fix game engine errors
        if msgid_text.endswith('\n'):
            msgid_text = msgid_text.rstrip('\n')
            changed = True

        if msgstr_text.endswith('\n'):
            msgstr_text = msgstr_text.rstrip('\n')
            changed = True

        # 2. Fix line breaks in msgstr
        if msgstr_text.strip():
            fixed_msgstr_text, wrapped = fix_msgstr(msgstr_text)
            if wrapped:
                msgstr_text = fixed_msgstr_text
                changed = True
                report_wrapping = True

        if not changed:
            continue

        # Preserve the structural newlines and spaces that separated the PO entries
        id_ws = re.search(r'\s*$', msgid_raw).group()
        str_ws = re.search(r'\s*$', msgstr_raw).group()

        # Rebuild the entry blocks flawlessly 
        new_id_block = _text_to_po_val(msgid_text) + id_ws
        new_str_block = _text_to_po_val(msgstr_text) + str_ws

        new_entry = m.group(1) + m.group(2) + new_id_block + m.group(4) + new_str_block

        # ── Report only if line-wrapping happened (ignore silent \n strips) ──
        if report_wrapping:
            ctx_m = re.search(r'msgctxt\s+"([^"]+)"', m.group(1))
            ctx   = ctx_m.group(1) if ctx_m else "???"

            print(f"\n  Entry : {ctx}")
            old_lines = old_msgstr_text.split("\n")
            new_lines = msgstr_text.split("\n")
            print("    Before:")
            for ln in old_lines:
                flag = " ⚠" if visible_len(ln) > HARD_LIMIT else ""
                print(f"      [{visible_len(ln):3d}]{flag}  {ln!r}")
            print("    After:")
            for ln in new_lines:
                print(f"      [{visible_len(ln):3d}]   {ln!r}")

        # ── Patch the raw text ──
        if not dry_run:
            start = m.start(0) + offset
            end   = m.end(0)   + offset

            updated = updated[:start] + new_entry + updated[end:]
            offset += len(new_entry) - (m.end(0) - m.start(0))

        fixed_count += 1

    if not dry_run and fixed_count:
        cleaned = "\n".join(
            "#. " if line.rstrip() == "#." else line
            for line in updated.split("\n")
        )
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(cleaned)

    return fixed_count


def fix_po_directory(root_dir: str, dry_run: bool = False) -> None:
    total_files   = 0
    total_entries = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames.sort()
        for fname in sorted(filenames):
            if not fname.lower().endswith(".po"):
                continue
            fpath = os.path.join(dirpath, fname)
            rel   = os.path.relpath(fpath, root_dir)
            print(f"\n{'─' * 65}")
            print(f"  File: {rel}")
            n = fix_po_file(fpath, dry_run=dry_run)
            if n:
                print(f"\n  → {'Would fix' if dry_run else 'Fixed'} {n} entry/entries.")
                total_entries += n
                total_files   += 1
            else:
                print("  ✓ All lines within limits.")

    print(f"\n{'═' * 65}")
    if dry_run:
        print(f"  DRY RUN — no files written.")
    print(f"  Total files with changes : {total_files}")
    print(f"  Total entries updated : {total_entries}")
    print("═" * 65)


# ════════════════════════════════════════════════════════════════════
#  MAIN  (GUI  picker)
# ════════════════════════════════════════════════════════════════════

def _pick(title: str, kind: str) -> str:
    root = tk.Tk()
    root.withdraw()
    if kind == "file":
        path = filedialog.askopenfilename(
            title=title,
            filetypes=[("PO files", "*.po"), ("All files", "*.*")],
        )
    else:
        path = filedialog.askdirectory(title=title)
    root.destroy()
    return path


def run() -> None:
    print("═" * 65)
    print("  PO Line-Break Fixer")
    print(f"  Soft limit : {SOFT_LIMIT} visible chars")
    print(f"  Hard limit : {HARD_LIMIT} visible chars  (CLT tags excluded)")
    print("═" * 65)

    root = tk.Tk()
    root.withdraw()
    choice = messagebox.askquestion(
        "Target",
        "Fix a single .po file?\n\n"
        "Yes  → pick a single file\n"
        "No   → pick a folder (all .po files inside)",
    )
    root.destroy()

    if choice == "yes":
        path = _pick("Select .po file to fix", "file")
        if not path:
            print("No file selected — exiting.")
            return
        print(f"\n  File : {path}\n")
        n = fix_po_file(path)
        print(f"\n{'═' * 65}")
        if n:
            print(f"  Done!  Fixed {n} entry/entries.")
        else:
            print("  Done!  All lines were already within limits.")
        print("═" * 65)
    else:
        path = _pick("Select folder containing .po files", "folder")
        if not path:
            print("No folder selected — exiting.")
            return
        print(f"\n  Folder : {path}\n")
        fix_po_directory(path)


if __name__ == "__main__":
    run()