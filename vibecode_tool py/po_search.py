import os
import re
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, ttk

# ════════════════════════════════════════════════════════════════════
#  PO PARSER  (same escaped-quote-safe regex as the translator)
# ════════════════════════════════════════════════════════════════════

Q = r'"(?:[^"\\]|\\.)*"'   # one PO quoted string, handles \" and \\

def _decode(raw_block: str) -> str:
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', raw_block)
    return "".join(parts).replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")

def parse_po(filepath: str) -> list:
    """Return list of {msgctxt, msgid, msgstr} dicts."""
    try:
        with open(filepath, encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return []

    pattern = re.compile(
        r"(?:#[^\n]*\n)*"
        r'msgctxt\s+' + Q + r'\n'
        r'msgid\s+(?:' + Q + r'\n?)+'
        r'msgstr\s+(?:' + Q + r'\n?)*',
        re.MULTILINE,
    )

    entries = []
    for m in pattern.finditer(raw):
        block   = m.group(0)
        ctx_m   = re.search(r'msgctxt\s+(' + Q + r')', block)
        id_m    = re.search(r'(msgid\s+(?:' + Q + r'\n?)+)', block)
        str_m   = re.search(r'(msgstr\s+(?:' + Q + r'\n?)*)', block)
        if not ctx_m or not id_m:
            continue
        entries.append({
            "msgctxt": _decode(ctx_m.group(1)),
            "msgid":   _decode(id_m.group(1)),
            "msgstr":  _decode(str_m.group(1)) if str_m else "",
        })
    return entries


def search_all(root_dir: str, phrase: str, case_sensitive: bool,
               search_msgid: bool, search_msgstr: bool) -> list:
    """
    Walk root_dir, search every .po file (skipping - Copy.po).
    Returns list of result dicts.
    """
    if not phrase:
        return []

    needle = phrase if case_sensitive else phrase.lower()
    results = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames.sort()
        for fname in sorted(filenames):
            if not fname.endswith(".po"):
                continue
            if "- Copy" in fname or "- copy" in fname:
                continue

            filepath = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(filepath, root_dir)
            entries  = parse_po(filepath)

            for entry in entries:
                msgid  = entry["msgid"]
                msgstr = entry["msgstr"]

                hit_id  = False
                hit_str = False

                if search_msgid:
                    hay = msgid if case_sensitive else msgid.lower()
                    hit_id = needle in hay

                if search_msgstr:
                    hay = msgstr if case_sensitive else msgstr.lower()
                    hit_str = needle in hay

                if hit_id or hit_str:
                    results.append({
                        "file":    rel_path,
                        "msgctxt": entry["msgctxt"],
                        "msgid":   msgid,
                        "msgstr":  msgstr,
                        "hit_id":  hit_id,
                        "hit_str": hit_str,
                    })

    return results


# ════════════════════════════════════════════════════════════════════
#  GUI
# ════════════════════════════════════════════════════════════════════

class POSearchApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PO Search — Danganronpa")
        self.geometry("1200x780")
        self.minsize(800, 500)
        self.configure(bg="#1e1e2e")

        self.root_dir   = tk.StringVar(value="(no folder selected)")
        self.phrase_var = tk.StringVar()
        self.case_var   = tk.BooleanVar(value=False)
        self.in_id_var  = tk.BooleanVar(value=True)
        self.in_str_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Pick a folder and enter a search phrase.")

        self._build_ui()
        self.bind("<Return>", lambda e: self._run_search())

    # ── colours ─────────────────────────────────────────────────────
    BG      = "#1e1e2e"
    PANEL   = "#2a2a3e"
    ACCENT  = "#89b4fa"
    FG      = "#cdd6f4"
    FG_DIM  = "#6c7086"
    HIT_EN  = "#a6e3a1"   # green  – English hit
    HIT_VI  = "#f9e2af"   # yellow – Vietnamese hit
    HIT_BOT = "#cba6f7"   # purple – both
    SEL_BG  = "#313244"
    FONT    = ("Segoe UI", 10)
    MONO    = ("Consolas", 9)

    def _build_ui(self):
        # ── Top bar ─────────────────────────────────────────────────
        top = tk.Frame(self, bg=self.BG, pady=8, padx=12)
        top.pack(fill="x")

        tk.Button(top, text="📂 Choose Folder", command=self._pick_folder,
                  bg=self.ACCENT, fg=self.BG, font=self.FONT,
                  relief="flat", padx=10, cursor="hand2"
                  ).pack(side="left")

        self._folder_lbl = tk.Label(top, textvariable=self.root_dir,
                                    bg=self.BG, fg=self.FG_DIM,
                                    font=("Segoe UI", 9), anchor="w")
        self._folder_lbl.pack(side="left", padx=10, fill="x", expand=True)

        # ── Search bar ──────────────────────────────────────────────
        bar = tk.Frame(self, bg=self.PANEL, pady=8, padx=12)
        bar.pack(fill="x")

        tk.Label(bar, text="Search:", bg=self.PANEL, fg=self.FG,
                 font=self.FONT).pack(side="left")

        entry = tk.Entry(bar, textvariable=self.phrase_var,
                         font=("Segoe UI", 11), bg="#313244", fg=self.FG,
                         insertbackground=self.FG, relief="flat",
                         width=40)
        entry.pack(side="left", padx=8, ipady=4)
        entry.focus_set()

        tk.Button(bar, text="🔍 Search", command=self._run_search,
                  bg=self.ACCENT, fg=self.BG, font=self.FONT,
                  relief="flat", padx=12, cursor="hand2"
                  ).pack(side="left")

        tk.Button(bar, text="✕ Clear", command=self._clear,
                  bg="#45475a", fg=self.FG, font=self.FONT,
                  relief="flat", padx=8, cursor="hand2"
                  ).pack(side="left", padx=6)

        # Options
        opts = tk.Frame(bar, bg=self.PANEL)
        opts.pack(side="left", padx=16)

        for text, var in [("Case sensitive", self.case_var),
                          ("English (msgid)", self.in_id_var),
                          ("Vietnamese (msgstr)", self.in_str_var)]:
            tk.Checkbutton(opts, text=text, variable=var,
                           bg=self.PANEL, fg=self.FG, selectcolor="#313244",
                           activebackground=self.PANEL, font=("Segoe UI", 9)
                           ).pack(side="left", padx=4)

        # ── Legend ──────────────────────────────────────────────────
        legend = tk.Frame(self, bg=self.BG, padx=12, pady=2)
        legend.pack(fill="x")
        for colour, label in [(self.HIT_EN,  "● Hit in English"),
                              (self.HIT_VI,  "● Hit in Vietnamese"),
                              (self.HIT_BOT, "● Hit in both")]:
            tk.Label(legend, text=label, bg=self.BG, fg=colour,
                     font=("Segoe UI", 8)).pack(side="left", padx=8)





        # ── Status bar (packed first so it anchors to bottom) ──────
        tk.Label(self, textvariable=self.status_var,
                 bg="#181825", fg=self.FG_DIM,
                 font=("Segoe UI", 8), anchor="w", padx=12
                 ).pack(fill="x", side="bottom")

        # ── Detail panel (packed before tree so it stays visible) ───
        detail = tk.Frame(self, bg=self.BG, padx=12, pady=6)
        detail.pack(fill="x", side="bottom")

        detail_top = tk.Frame(detail, bg=self.BG)
        detail_top.pack(fill="x", pady=(0, 4))

        tk.Label(detail_top, text="Selected entry:", bg=self.BG,
                 fg=self.FG_DIM, font=("Segoe UI", 8)).pack(side="left")

        self.btn_open_folder = tk.Button(
            detail_top, text="📁 Open Folder", command=self._open_folder,
            bg="#45475a", fg="#888888", font=("Segoe UI", 10, "bold"),
            relief="raised", padx=14, pady=4, cursor="hand2", state="disabled",
            disabledforeground="#555566")
        self.btn_open_folder.pack(side="right", padx=(8, 0))

        self.btn_open_file = tk.Button(
            detail_top, text="📄 Open File", command=self._open_file,
            bg="#2a4a6a", fg="#888888", font=("Segoe UI", 10, "bold"),
            relief="raised", padx=14, pady=4, cursor="hand2", state="disabled",
            disabledforeground="#555566")
        self.btn_open_file.pack(side="right")

        self.detail_text = tk.Text(detail, height=4, bg="#252535",
                                   fg=self.FG, font=self.MONO,
                                   relief="flat", wrap="word",
                                   state="disabled")
        self.detail_text.pack(fill="x")

        # ── Results tree ────────────────────────────────────────────
        tree_frame = tk.Frame(self, bg=self.BG)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=(4, 0))

        cols = ("file", "msgctxt", "msgid", "msgstr")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                 selectmode="browse")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview",
                        background=self.PANEL, fieldbackground=self.PANEL,
                        foreground=self.FG, rowheight=52,
                        font=self.MONO, borderwidth=0)
        style.configure("Treeview.Heading",
                        background="#313244", foreground=self.ACCENT,
                        font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Treeview",
                  background=[("selected", self.SEL_BG)],
                  foreground=[("selected", self.FG)])

        headers = {"file": ("File", 220), "msgctxt": ("Entry", 160),
                   "msgid": ("English", 300), "msgstr": ("Vietnamese", 300)}
        for col, (heading, width) in headers.items():
            self.tree.heading(col, text=heading,
                              command=lambda c=col: self._sort(c))
            self.tree.column(col, width=width, minwidth=80, anchor="w")

        self.tree.tag_configure("hit_en",  background="#1a3a2a", foreground=self.HIT_EN)
        self.tree.tag_configure("hit_vi",  background="#3a3010", foreground=self.HIT_VI)
        self.tree.tag_configure("hit_both",background="#2a1a40", foreground=self.HIT_BOT)
        self.tree.tag_configure("odd",     background="#252535")
        self.tree.tag_configure("even",    background=self.PANEL)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                            command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal",
                            command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side="right",  fill="y")
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(fill="both", expand=True)

        self.tree.bind("<ButtonRelease-1>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)

        # sort state
        self._sort_col = None
        self._sort_rev = False

    # ── actions ─────────────────────────────────────────────────────

    def _pick_folder(self):
        folder = filedialog.askdirectory(title="Select root folder")
        if folder:
            self.root_dir.set(folder)
            self.status_var.set(f"Folder set: {folder}")

    def _run_search(self):
        folder = self.root_dir.get()
        if not os.path.isdir(folder):
            self.status_var.set("⚠  Please pick a valid folder first.")
            return

        phrase = self.phrase_var.get().strip()
        if not phrase:
            self.status_var.set("⚠  Please enter a search phrase.")
            return

        if not self.in_id_var.get() and not self.in_str_var.get():
            self.status_var.set("⚠  Select at least one field to search.")
            return

        self.status_var.set("Searching…")
        self.update_idletasks()

        results = search_all(
            root_dir       = folder,
            phrase         = phrase,
            case_sensitive = self.case_var.get(),
            search_msgid   = self.in_id_var.get(),
            search_msgstr  = self.in_str_var.get(),
        )

        self._populate(results, phrase)

    def _populate(self, results: list, phrase: str):
        for row in self.tree.get_children():
            self.tree.delete(row)

        for i, r in enumerate(results):
            # Truncate long text for display (full text shown in detail panel)
            def trunc(s, n=80):
                s = s.replace("\n", " ↵ ")
                return s[:n] + "…" if len(s) > n else s

            if r["hit_id"] and r["hit_str"]:
                tag = "hit_both"
            elif r["hit_id"]:
                tag = "hit_en"
            else:
                tag = "hit_vi"

            self.tree.insert("", "end", iid=str(i), tags=(tag,), values=(
                r["file"],
                r["msgctxt"],
                trunc(r["msgid"]),
                trunc(r["msgstr"]),
            ))
            # Store full data as item data
            self.tree.set(str(i), "file", r["file"])

        # Stash full results for detail panel
        self._results = results

        total = len(results)
        self.status_var.set(
            f'Found {total} result{"s" if total != 1 else ""} for "{phrase}"'
            + ("  (no results)" if total == 0 else "")
        )

    def _on_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        r   = self._results[idx]

        # Build absolute path for open buttons
        base = self.root_dir.get()
        self._selected_path = os.path.join(base, r["file"])

        self.btn_open_file.configure(
            state="normal", bg=self.ACCENT, fg=self.BG)
        self.btn_open_folder.configure(
            state="normal", bg="#45475a", fg=self.FG)

        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("end", f"File   : {r['file']}\n")
        self.detail_text.insert("end", f"Entry  : {r['msgctxt']}\n")
        self.detail_text.insert("end", f"English: {r['msgid']}\n")
        self.detail_text.insert("end", f"Viet   : {r['msgstr']}\n")
        self.detail_text.configure(state="disabled")

    def _on_double_click(self, event):
        self._on_select()

    def _open_file(self):
        path = getattr(self, "_selected_path", None)
        if not path or not os.path.isfile(path):
            self.status_var.set("⚠  File not found.")
            return
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        self.status_var.set(f"Opened: {path}")

    def _open_folder(self):
        path = getattr(self, "_selected_path", None)
        if not path:
            self.status_var.set("⚠  No file selected.")
            return
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            self.status_var.set("⚠  Folder not found.")
            return
        if sys.platform == "win32":
            # Open Explorer with the file highlighted
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
        self.status_var.set(f"Opened folder: {folder}")

    def _clear(self):
        self.phrase_var.set("")
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.configure(state="disabled")
        self.btn_open_file.configure(state="disabled", bg="#2a4a6a", fg="#888888")
        self.btn_open_folder.configure(state="disabled", bg="#45475a", fg="#888888")
        self._selected_path = None
        self.status_var.set("Cleared.")
        self._results = []

    def _sort(self, col):
        rows = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        self._sort_rev = (col == self._sort_col) and not self._sort_rev
        self._sort_col = col
        rows.sort(reverse=self._sort_rev)
        for rank, (_, k) in enumerate(rows):
            self.tree.move(k, "", rank)


# ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = POSearchApp()
    app.mainloop()
