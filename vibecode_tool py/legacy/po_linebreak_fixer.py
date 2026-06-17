"""
po_linebreak_fixer.py — Auto-fix line lengths in Vietnamese .po files
======================================================================

Wraps msgstr lines based on 128-char logic, ignoring <CLT> tags.
Excludes the header/metadata block and leaves msgid COMPLETELY untouched.
Scans all subfolders but strictly IGNORES any file with "-Copy" in the name.
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
MAX_CUTS:   int = 2    # Maximum line breaks (1 = 2 lines max, 2 = 3 lines max, etc.)

_CLT_RE = re.compile(r"<CLT(?:\s+\d+)?>|<CLT_\d+>")

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

def fix_msgstr(msgstr_text: str, soft: int = SOFT_LIMIT, hard: int = HARD_LIMIT, max_cuts: int = MAX_CUTS) -> tuple[str, bool]:
    original_text = msgstr_text

    if not _CLT_RE.sub("", msgstr_text).strip():
        return original_text, False

    # STEP 1: Detach the final <CLT> or \n<CLT>
    end_tag = ""
    if msgstr_text.endswith("\n<CLT>"):
        end_tag = "\n<CLT>"
        msgstr_text = msgstr_text[:-6]
    elif msgstr_text.endswith("<CLT>"):
        end_tag = "<CLT>"
        msgstr_text = msgstr_text[:-5]

    # STEP 2: Flatten — remove all existing line breaks
    flat = re.sub(r"\s+", " ", msgstr_text.replace("\n", " ")).strip()

    # STEP 3: No wrapping needed if already within hard limit
    if visible_len(flat) <= hard:
        fixed = flat + end_tag
        return fixed, fixed != original_text

    # STEP 4: Wrap
    #   Line 1: try soft cap first; if remainder > soft, fall back to hard cap
    #   Line 2: always cut at hard cap (no even-split check)

    def find_cut(word_list: list[str], limit: int) -> int:
        """Index of first word that pushes the line over *limit*. len(word_list) if all fit."""
        vis = 0
        for i, w in enumerate(word_list):
            vis += (1 if i else 0) + visible_len(w)
            if vis > limit:
                return i
        return len(word_list)

    # Protect space inside <CLT 3> tags so split(" ") does not tear them apart
    flat = re.sub(r"<CLT\s+(\d+)>", r"<CLT_\1>", flat)
    words = flat.split(" ")
    lines: list[str] = []

    for cut_num in range(max_cuts):
        soft_cut = find_cut(words, soft)
        if soft_cut == len(words):
            break  # everything fits, no cut needed

        if cut_num == 0:
            # Line 1: even split if possible, else hard cap
            remainder = words[soft_cut:]
            if visible_len(" ".join(remainder)) <= soft:
                cut_at = soft_cut
            else:
                hard_cut = find_cut(words, hard)
                cut_at = hard_cut if hard_cut < len(words) else soft_cut
        else:
            # Line 2: always hard cap
            hard_cut = find_cut(words, hard)
            cut_at = hard_cut 

        lines.append(" ".join(words[:cut_at]))
        words = words[cut_at:]

    if words:
        lines.append(" ".join(words))

    # Restore protected tags
    fixed_text = re.sub(r"<CLT_(\d+)>", r"<CLT \1>", "\n".join(lines)) + end_tag
    return fixed_text, fixed_text != original_text

# ════════════════════════════════════════════════════════════════════
#  PO  FILE  PROCESSING
# ════════════════════════════════════════════════════════════════════

def fix_po_file(filepath: str, dry_run: bool = False, max_cuts: int = MAX_CUTS) -> int:
    try:
        with open(filepath, encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return 0

    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    Q = r'"(?:[^"\\]|\\.)*"'
    
    entry_pat = re.compile(
        r"((?:#[^\n]*\n)*(?:msgctxt\s+" + Q + r"\n)?)"  # Group 1: Comments and msgctxt
        r"(msgid\s+)((?:" + Q + r"\s*)+)"              # Group 2 & 3: 'msgid' and its untouched text
        r"(msgstr\s+)((?:" + Q + r"\s*)*)",            # Group 4 & 5: 'msgstr' and its text
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

        # 🛡️ THE HEADER SHIELD: Skip if msgid is empty (Header/Metadata) 🛡️
        if not msgid_text.strip():
            continue

        changed = False
        
        # Strip trailing \n ONLY from msgstr now! Never touch msgid! 🛑
        if msgstr_text.endswith('\n'):
            msgstr_text = msgstr_text.rstrip('\n')
            changed = True

        if msgstr_text.strip():
            fixed_msgstr_text, wrapped = fix_msgstr(msgstr_text, max_cuts=max_cuts)
            if wrapped:
                msgstr_text = fixed_msgstr_text
                changed = True

        if not changed:
            continue

        str_ws_match = re.search(r'\s*$', msgstr_raw)
        str_ws = str_ws_match.group() if str_ws_match else ""

        # 🎀 We strictly rebuild ONLY the msgstr block. msgid stays exactly as group(3)! 🎀
        new_str_block = _text_to_po_val(msgstr_text) + str_ws
        
        # Stitch it together: Group 1, 2, 3 (UNTOUCHED msgid), 4, and new 5 (msgstr)
        new_entry = m.group(1) + m.group(2) + m.group(3) + m.group(4) + new_str_block

        if not dry_run:
            start = m.start(0) + offset
            end   = m.end(0)   + offset
            updated = updated[:start] + new_entry + updated[end:]
            offset += len(new_entry) - (m.end(0) - m.start(0))
        fixed_count += 1

    if not dry_run and fixed_count:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated)
    return fixed_count

def fix_po_directory(root_dir: str, max_cuts: int = MAX_CUTS) -> None:
    total_files = 0
    total_entries = 0

    # 🎀 Deep recursive scan of all folders! 🎀
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames.sort()
        for fname in sorted(filenames):
            # 🛑 Skip if it's not a .po file OR if it has "-Copy" in the name! 🛑
            if not fname.lower().endswith(".po") or "-copy" in fname.lower():
                continue
            
            fpath = os.path.join(dirpath, fname)
            rel   = os.path.relpath(fpath, root_dir)
            
            print(f"\n{'─' * 65}")
            print(f"  File: {rel}")
            
            n = fix_po_file(fpath, max_cuts=max_cuts)
            if n:
                print(f"  → Fixed {n} entry/entries. ♡")
                total_entries += n
                total_files   += 1
            else:
                print("  ✓ All lines within limits or already perfect.")

    print(f"\n{'═' * 65}")
    print(f"  Total files with changes : {total_files}")
    print(f"  Total entries updated : {total_entries}")
    print("═" * 65)

def _pick(title: str, kind: str) -> str:
    root = tk.Tk()
    root.withdraw()
    if kind == "file":
        path = filedialog.askopenfilename(title=title, filetypes=[("PO files", "*.po")])
    else:
        path = filedialog.askdirectory(title=title)
    root.destroy()
    return path

def _ask_max_cuts() -> int:
    """Show a small dialog to pick max line breaks. Returns chosen value."""
    root = tk.Tk()
    root.title("Max Line Breaks")
    root.resizable(False, False)

    tk.Label(root, text="Maximum line breaks per entry:",
             font=("Segoe UI", 10), pady=10, padx=16).pack()

    var = tk.IntVar(value=MAX_CUTS)
    frame = tk.Frame(root, padx=16, pady=4)
    frame.pack()
    for val, label in [(1, "1  (max 2 lines)"),
                       (2, "2  (max 3 lines)"),
                       (3, "3  (max 4 lines)"),
                       (4, "4  (max 5 lines)")]:
        tk.Radiobutton(frame, text=label, variable=var, value=val,
                       font=("Segoe UI", 9), anchor="w").pack(fill="x")

    result = {"v": MAX_CUTS}
    def confirm():
        result["v"] = var.get()
        root.destroy()
    tk.Button(root, text="OK", command=confirm,
              font=("Segoe UI", 9, "bold"), padx=20, pady=4).pack(pady=10)
    root.mainloop()
    return result["v"]


def run() -> None:
    print("═" * 65)
    print("  PO Line-Break Fixer (Ultimate Safe Version! 🎀)")
    print(f"  Soft limit : {SOFT_LIMIT} visible chars")
    print(f"  Hard limit : {HARD_LIMIT} visible chars  (CLT tags excluded)")
    print("═" * 65)

    root = tk.Tk()
    root.withdraw()
    choice = messagebox.askquestion("Target", "Fix a single .po file?\n\nYes = Single File\nNo = Entire Folder")
    root.destroy()

    max_cuts = _ask_max_cuts()
    print(f"  Max line breaks : {max_cuts}  (max {max_cuts + 1} lines per entry)")

    if choice == "yes":
        path = _pick("Select .po file", "file")
        if path:
            print(f"\n  File : {path}")
            n = fix_po_file(path, max_cuts=max_cuts)
            print(f"\nDone! Fixed {n} entries. Original text protected! ♡")
    else:
        path = _pick("Select folder", "folder")
        if path:
            print(f"\n  Folder : {path}")
            fix_po_directory(path, max_cuts=max_cuts)

if __name__ == "__main__":
    run()