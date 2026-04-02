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
import tkinter as tk
from tkinter import filedialog

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

# ════════════════════════════════════════════════════════════════════
#  UI
# ════════════════════════════════════════════════════════════════════

BG      = "#1e1e2e"
BG2     = "#2a2a3e"
BG3     = "#313145"
ACCENT  = "#cba6f7"
GREEN   = "#a6e3a1"
RED     = "#f38ba8"
BLUE    = "#89b4fa"
PEACH   = "#fab387"
TEXT    = "#cdd6f4"
SUBTLE  = "#6c7086"

def _btn(parent, text, cmd, **kw):
    defaults = dict(bg=BG3, fg=TEXT, activebackground=ACCENT, activeforeground=BG,
                    relief="flat", cursor="hand2", font=("Segoe UI", 9), padx=10, pady=4)
    defaults.update(kw)
    return tk.Button(parent, text=text, command=cmd, **defaults)

def _label(parent, text, **kw):
    defaults = dict(bg=BG, fg=TEXT, font=("Segoe UI", 9))
    defaults.update(kw)
    return tk.Label(parent, text=text, **defaults)

class App:
    def __init__(self, root):
        self.root = root
        root.title("PO Mass Replace")
        root.configure(bg=BG)
        root.resizable(True, True)
        root.minsize(580, 500)

        self.path_var   = tk.StringVar()
        self.check_vars = [tk.BooleanVar(value=True) for _ in CRITERIA]

        self._build()
        self._setup_dnd()

    def _build(self):
        # Title
        hdr = tk.Frame(self.root, bg=BG)
        hdr.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(hdr, text="PO Mass Replace", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 14, "bold")).pack(side="left")
        if not DND_AVAILABLE:
            tk.Label(hdr, text="  (install tkinterdnd2 for drag-and-drop)",
                     bg=BG, fg=SUBTLE, font=("Segoe UI", 8)).pack(side="left")

        # Drop zone
        self.drop_outer = tk.Frame(self.root, bg=ACCENT)          # 1px border trick
        self.drop_outer.pack(fill="x", padx=16, pady=4)
        self.drop_inner = tk.Frame(self.drop_outer, bg=BG2)
        self.drop_inner.pack(fill="x", padx=1, pady=1)
        self.drop_lbl = tk.Label(
            self.drop_inner,
            text="📂  Drop a .po file or folder here",
            bg=BG2, fg=SUBTLE, font=("Segoe UI", 10), pady=16,
        )
        self.drop_lbl.pack(fill="x")

        # Path entry + browse buttons
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=16, pady=(4, 8))
        self.entry = tk.Entry(row, textvariable=self.path_var,
                              bg=BG2, fg=TEXT, insertbackground=TEXT,
                              relief="flat", font=("Segoe UI", 9),
                              highlightbackground=BG3, highlightthickness=1)
        self.entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        _btn(row, "File…",   lambda: self._browse("file")).pack(side="left", padx=(0, 4))
        _btn(row, "Folder…", lambda: self._browse("dir")).pack(side="left")

        # Criteria section
        sec = tk.Frame(self.root, bg=BG)
        sec.pack(fill="x", padx=16, pady=(0, 6))
        tk.Label(sec, text="CRITERIA", bg=BG, fg=SUBTLE,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 3))

        cbox = tk.Frame(sec, bg=BG2, highlightbackground=BG3, highlightthickness=1)
        cbox.pack(fill="x")

        if CRITERIA:
            for criterion, var in zip(CRITERIA, self.check_vars):
                r = tk.Frame(cbox, bg=BG2)
                r.pack(fill="x", padx=8, pady=4)
                tk.Checkbutton(r, variable=var, text=criterion["label"],
                               bg=BG2, fg=TEXT, selectcolor=BG3,
                               activebackground=BG2, activeforeground=ACCENT,
                               relief="flat", font=("Segoe UI", 9)).pack(side="left")
                # Badges
                char = criterion.get("character")
                if char:
                    self._badge(r, f"👤 {char}", ACCENT)
                scope = criterion.get("scope")
                if scope:
                    self._badge(r, scope, BLUE)
                ww = criterion.get("whole_word", True)
                self._badge(r, "whole-word" if ww else "substring", SUBTLE if ww else PEACH)
        else:
            tk.Label(cbox, text="No criteria — add entries to CRITERIA in the script.",
                     bg=BG2, fg=RED, font=("Segoe UI", 9), pady=10).pack()

        # Run button
        run_row = tk.Frame(self.root, bg=BG)
        run_row.pack(fill="x", padx=16, pady=(4, 8))
        _btn(run_row, "▶  Run", self._run,
             bg=ACCENT, fg=BG, font=("Segoe UI", 10, "bold"),
             padx=28, pady=6).pack(side="right")
        _btn(run_row, "Clear log", self._clear_log).pack(side="right", padx=(0, 8))

        # Log
        log_sec = tk.Frame(self.root, bg=BG)
        log_sec.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        tk.Label(log_sec, text="LOG", bg=BG, fg=SUBTLE,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 3))

        log_box = tk.Frame(log_sec, bg=BG2, highlightbackground=BG3, highlightthickness=1)
        log_box.pack(fill="both", expand=True)

        self.log = tk.Text(log_box, bg=BG2, fg=TEXT, insertbackground=TEXT,
                           relief="flat", font=("Consolas", 8),
                           state="disabled", wrap="word", padx=8, pady=6)
        sb = tk.Scrollbar(log_box, command=self.log.yview, bg=BG3, troughcolor=BG2)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)

        self.log.tag_config("add",  foreground=GREEN)
        self.log.tag_config("del",  foreground=RED)
        self.log.tag_config("good", foreground=GREEN)
        self.log.tag_config("warn", foreground=RED)
        self.log.tag_config("head", foreground=ACCENT)
        self.log.tag_config("info", foreground=SUBTLE)

    def _badge(self, parent, text, color):
        tk.Label(parent, text=text, bg=BG3, fg=color,
                 font=("Segoe UI", 7), padx=5, pady=1).pack(side="left", padx=(5, 0))

    # ── DnD ───────────────────────────────────────────────────────

    def _setup_dnd(self):
        if not DND_AVAILABLE:
            return
        for widget in (self.drop_outer, self.drop_inner, self.drop_lbl):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>",      self._on_drop)
            widget.dnd_bind("<<DragEnter>>", self._on_enter)
            widget.dnd_bind("<<DragLeave>>", self._on_leave)

    def _on_enter(self, event):
        self.drop_inner.configure(bg=BG3)
        self.drop_lbl.configure(bg=BG3, fg=ACCENT, text="📂  Release to load")

    def _on_leave(self, event):
        self.drop_inner.configure(bg=BG2)
        self.drop_lbl.configure(bg=BG2, fg=SUBTLE, text="📂  Drop a .po file or folder here")

    def _on_drop(self, event):
        self._on_leave(event)
        raw = event.data.strip()
        # Handle multiple files wrapped in braces: {path one} {path two}
        if raw.startswith("{"):
            raw = raw[1:raw.index("}")] if "}" in raw else raw[1:]
        self._set_path(raw)

    # ── Browse ────────────────────────────────────────────────────

    def _browse(self, kind: str):
        p = (filedialog.askopenfilename(filetypes=[("PO files", "*.po"), ("All", "*.*")])
             if kind == "file" else filedialog.askdirectory())
        if p:
            self._set_path(p)

    def _set_path(self, path: str):
        self.path_var.set(path)
        icon = "📄" if os.path.isfile(path) else "📁"
        self.drop_lbl.configure(fg=TEXT, text=f"{icon}  {os.path.basename(path)}")

    # ── Log ───────────────────────────────────────────────────────

    def _write(self, msg: str, tag: str = "info"):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.configure(state="disabled")
        self.log.see("end")
        self.root.update_idletasks()

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ── Run ───────────────────────────────────────────────────────

    def _run(self):
        path = self.path_var.get().strip()
        if not path:
            self._write("⚠  No path selected.", "warn"); return

        active = [c for c, v in zip(CRITERIA, self.check_vars) if v.get()]
        if not active:
            self._write("⚠  No criteria checked.", "warn"); return

        self._clear_log()
        self._write(f"═ Target: {path}", "head")
        self._write(f"  Criteria: {', '.join(c['label'] for c in active)}", "info")
        self._write("", "info")

        files, entries = process_path(path, active, self._write)

        self._write("", "info")
        self._write(f"═ Done — files changed: {files}  |  entries changed: {entries}", "head")


# ════════════════════════════════════════════════════════════════════
#  ENTRY  POINT
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    App(root)
    root.geometry("640x580")
    root.mainloop()
