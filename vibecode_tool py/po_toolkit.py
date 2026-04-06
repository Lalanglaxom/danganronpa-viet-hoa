"""
po_toolkit.py — Unified launcher for all PO tools.
====================================================
Place this file in the same folder as:
  po_validator.py
  po_linebreak_fixer.py
  po_mass_replace.py
  po_search.py
  po_backup_sync.py
  po_gemini_translator.py

Run this file to open the tabbed toolkit.
Each tool runs in its own tab and prints into its own log pane.
"""

import importlib
import io
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ── Optional drag-and-drop ─────────────────────────────────────────
try:
    from tkinterdnd2 import TkinterDnD
    _DND = True
except ImportError:
    _DND = False

# ══════════════════════════════════════════════════════════════════
#  THEME
# ══════════════════════════════════════════════════════════════════

BG       = "#1a0d12"   # very dark rose-black
BG2      = "#2b1320"   # dark pink-brown panel
BG3      = "#3d1c2e"   # slightly lighter panel
ACCENT   = "#e8629a"   # hot pink
ACCENT2  = "#c2185b"   # deeper pink
TEXT     = "#fce4ec"   # near-white rose
SUBTLE   = "#9e6070"   # muted pink-grey
GREEN    = "#a5d6a7"
RED      = "#ef9a9a"
TAB_BG   = "#3d1c2e"
TAB_SEL  = "#e8629a"
TAB_FG   = "#fce4ec"

FONT       = ("Segoe UI", 9)
FONT_BOLD  = ("Segoe UI", 9, "bold")
FONT_MONO  = ("Consolas",  8)
FONT_TITLE = ("Segoe UI", 13, "bold")

# ══════════════════════════════════════════════════════════════════
#  STDOUT REDIRECT
# ══════════════════════════════════════════════════════════════════

class _TabStream(io.StringIO):
    """Redirects print() calls to a tk.Text widget inside a tab."""
    def __init__(self, text_widget: tk.Text):
        super().__init__()
        self._w = text_widget

    def write(self, s: str):
        self._w.configure(state="normal")
        # Colour lines based on prefix
        if s.strip().startswith("✔") or s.strip().startswith("✓") or s.strip().startswith("[BACKUP]") or s.strip().startswith("[LIN"):
            tag = "good"
        elif s.strip().startswith("✗") or s.strip().startswith("[ERROR]") or s.strip().startswith("⚠"):
            tag = "bad"
        elif s.strip().startswith("!") or s.strip().startswith("[SKIP") or s.strip().startswith("[NOT"):
            tag = "warn"
        elif s.startswith("═") or s.startswith("="):
            tag = "head"
        else:
            tag = "info"
        self._w.insert("end", s, tag)
        self._w.see("end")
        self._w.configure(state="disabled")
        self._w.update_idletasks()

    def flush(self): pass


def _make_log(parent) -> tk.Text:
    """Create a styled log Text widget and return it."""
    frame = tk.Frame(parent, bg=BG2, highlightbackground=BG3, highlightthickness=1)
    frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    sb = tk.Scrollbar(frame, bg=BG3, troughcolor=BG2)
    sb.pack(side="right", fill="y")

    log = tk.Text(
        frame, bg=BG2, fg=TEXT,
        font=FONT_MONO, state="disabled",
        wrap="word", padx=8, pady=6,
        yscrollcommand=sb.set,
        insertbackground=TEXT, relief="flat",
    )
    sb.config(command=log.yview)
    log.pack(fill="both", expand=True)

    log.tag_config("good", foreground=GREEN)
    log.tag_config("bad",  foreground=RED)
    log.tag_config("warn", foreground="#ffcc80")
    log.tag_config("head", foreground=ACCENT)
    log.tag_config("info", foreground=SUBTLE)

    return log


def _run_in_thread(fn, log: tk.Text, btn: tk.Button, btn_text: str):
    """Run fn() in a thread, redirecting stdout to the log widget."""
    def _clear():
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")

    def _target():
        btn.configure(state="disabled", text="Running…")
        _clear()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        stream = _TabStream(log)
        sys.stdout = sys.stderr = stream
        try:
            fn()
        except Exception as e:
            print(f"\n⚠ Error: {e}")
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
            btn.configure(state="normal", text=btn_text)

    threading.Thread(target=_target, daemon=True).start()


# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════

def _styled_btn(parent, text, cmd, **kw) -> tk.Button:
    defaults = dict(bg=ACCENT, fg=BG, font=FONT_BOLD,
                    relief="flat", cursor="hand2",
                    activebackground=ACCENT2, activeforeground=TEXT,
                    padx=18, pady=6)
    defaults.update(kw)
    return tk.Button(parent, text=text, command=cmd, **defaults)


def _section_label(parent, text: str):
    tk.Label(parent, text=text, bg=BG, fg=SUBTLE,
             font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=12, pady=(8, 2))


def _tab_frame(notebook, title: str) -> tk.Frame:
    frame = tk.Frame(notebook, bg=BG)
    notebook.add(frame, text=f"  {title}  ")
    return frame


# ══════════════════════════════════════════════════════════════════
#  TAB BUILDERS
# ══════════════════════════════════════════════════════════════════

def _build_simple_run_tab(notebook, title: str, module_name: str, run_fn_name: str = "run"):
    frame = _tab_frame(notebook, title)

    hdr = tk.Frame(frame, bg=BG)
    hdr.pack(fill="x", padx=12, pady=(14, 6))
    tk.Label(hdr, text=title, bg=BG, fg=ACCENT, font=FONT_TITLE).pack(side="left")

    _section_label(frame, "LOG")
    log = _make_log(frame)

    btn_row = tk.Frame(frame, bg=BG)
    btn_row.pack(fill="x", padx=12, pady=(0, 12))

    def _run():
        try:
            mod = importlib.import_module(module_name)
            fn  = getattr(mod, run_fn_name)
        except Exception as e:
            log.configure(state="normal")
            log.insert("end", f"⚠ Could not load {module_name}: {e}\n", "bad")
            log.configure(state="disabled")
            return
        _run_in_thread(fn, log, btn, f"▶  Run {title}")

    btn = _styled_btn(btn_row, f"▶  Run {title}", _run)
    btn.pack(side="right")

    def _clear():
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")

    tk.Button(btn_row, text="Clear", command=_clear,
              bg=BG3, fg=TEXT, font=FONT, relief="flat",
              activebackground=BG2, padx=12, pady=6).pack(side="right", padx=(0, 8))


def _build_backup_tab(notebook):
    _build_simple_run_tab(notebook, "Backup & Sync", "po_backup_sync", "backup_and_sync")

def _build_validator_tab(notebook):
    _build_simple_run_tab(notebook, "Validator", "po_validator", "run")

def _build_linebreak_tab(notebook):
    _build_simple_run_tab(notebook, "Line-Break Fixer", "po_linebreak_fixer", "run")

def _build_gemini_tab(notebook):
    _build_simple_run_tab(notebook, "Gemini Translator", "po_gemini_translator", "run")


def _build_mass_replace_tab(notebook):
    """Natively renders the UI for Mass Replace without importing its old tkinter code."""
    frame = _tab_frame(notebook, "Mass Replace")

    hdr = tk.Frame(frame, bg=BG)
    hdr.pack(fill="x", padx=12, pady=(14, 6))
    tk.Label(hdr, text="Mass Replace", bg=BG, fg=ACCENT, font=FONT_TITLE).pack(side="left")

    path_frame = tk.Frame(frame, bg=BG)
    path_frame.pack(fill="x", padx=12, pady=4)
    tk.Label(path_frame, text="Folder/File:", bg=BG, fg=TEXT, font=FONT_BOLD).pack(side="left")
    path_var = tk.StringVar()
    tk.Entry(path_frame, textvariable=path_var, bg=BG2, fg=TEXT, insertbackground=TEXT, font=FONT).pack(side="left", fill="x", expand=True, padx=8)
    def _browse():
        d = filedialog.askdirectory()
        if d: path_var.set(d)
    tk.Button(path_frame, text="Browse", command=_browse, bg=BG3, fg=TEXT, font=FONT, relief="flat", padx=8).pack(side="left")

    _section_label(frame, "CRITERIA")
    
    # Outer frame for scrollable canvas
    scroll_container = tk.Frame(frame, bg=BG2, highlightbackground=BG3, highlightthickness=1, height=120)
    scroll_container.pack(fill="x", padx=12, pady=4)
    scroll_container.pack_propagate(False)

    canvas = tk.Canvas(scroll_container, bg=BG2, highlightthickness=0)
    scrollbar = tk.Scrollbar(scroll_container, orient="vertical", command=canvas.yview, bg=BG3)
    crit_inner = tk.Frame(canvas, bg=BG2)

    crit_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=crit_inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    check_vars = []
    criteria = []
    try:
        mod = importlib.import_module("po_mass_replace")
        if hasattr(mod, "CRITERIA"):
            criteria = mod.CRITERIA
            for c in criteria:
                var = tk.BooleanVar(value=False)
                check_vars.append(var)
                cb = tk.Checkbutton(crit_inner, text=c["label"], variable=var, bg=BG2, fg=TEXT, 
                                    selectcolor=BG3, activebackground=BG2, activeforeground=TEXT, font=FONT)
                cb.pack(anchor="w", padx=8, pady=2)
        else:
            tk.Label(crit_inner, text="⚠ CRITERIA not found in po_mass_replace.py", bg=BG2, fg=RED).pack()
    except Exception as e:
        tk.Label(crit_inner, text=f"⚠ Could not load po_mass_replace: {e}", bg=BG2, fg=RED).pack()

    _section_label(frame, "LOG")
    log = _make_log(frame)
    
    btn_row = tk.Frame(frame, bg=BG)
    btn_row.pack(fill="x", padx=12, pady=(0, 12))

    def _run():
        path = path_var.get().strip()
        if not path:
            print("⚠ Please select a folder.")
            return
        active = [c for c, v in zip(criteria, check_vars) if v.get()]
        if not active:
            print("⚠ No criteria selected.")
            return
        
        def _log_fn(msg, tag="info"):
            if tag == "good": print(f"✓ {msg}")
            elif tag == "warn": print(f"⚠ {msg}")
            elif tag == "head": print(f"═ {msg}")
            elif tag == "bad": print(f"✗ {msg}")
            else: print(msg)
            
        print(f"═ Starting mass replace in: {path}")
        print(f"  Criteria: {', '.join(c['label'] for c in active)}")
        try:
            mod.process_path(path, active, _log_fn)
        except Exception as e:
            print(f"✗ Error during processing: {e}")
        print("═ Finished.")

    btn = _styled_btn(btn_row, "▶  Run Mass Replace", lambda: _run_in_thread(_run, log, btn, "▶  Run Mass Replace"))
    btn.pack(side="right")


def _build_search_tab(notebook):
    """Natively renders the UI for Search without importing its old tkinter code."""
    frame = _tab_frame(notebook, "Search")

    hdr = tk.Frame(frame, bg=BG)
    hdr.pack(fill="x", padx=12, pady=(14, 6))
    tk.Label(hdr, text="Search", bg=BG, fg=ACCENT, font=FONT_TITLE).pack(side="left")

    ctrl_frame = tk.Frame(frame, bg=BG)
    ctrl_frame.pack(fill="x", padx=12, pady=4)
    
    tk.Label(ctrl_frame, text="Folder:", bg=BG, fg=TEXT, font=FONT_BOLD).grid(row=0, column=0, sticky="w", pady=4)
    path_var = tk.StringVar()
    tk.Entry(ctrl_frame, textvariable=path_var, bg=BG2, fg=TEXT, insertbackground=TEXT, font=FONT).grid(row=0, column=1, sticky="ew", padx=8)
    def _browse():
        d = filedialog.askdirectory()
        if d: path_var.set(d)
    tk.Button(ctrl_frame, text="Browse", command=_browse, bg=BG3, fg=TEXT, font=FONT, relief="flat", padx=8).grid(row=0, column=2)

    tk.Label(ctrl_frame, text="Phrase:", bg=BG, fg=TEXT, font=FONT_BOLD).grid(row=1, column=0, sticky="w", pady=4)
    phrase_var = tk.StringVar()
    phrase_entry = tk.Entry(ctrl_frame, textvariable=phrase_var, bg=BG2, fg=TEXT, insertbackground=TEXT, font=FONT)
    phrase_entry.grid(row=1, column=1, sticky="ew", padx=8)
    
    btn_search = _styled_btn(ctrl_frame, "Search", lambda: _run_search())
    btn_search.grid(row=1, column=2)

    opts_frame = tk.Frame(ctrl_frame, bg=BG)
    opts_frame.grid(row=2, column=1, sticky="w", pady=4)
    case_var = tk.BooleanVar(value=False)
    id_var = tk.BooleanVar(value=True)
    str_var = tk.BooleanVar(value=True)
    tk.Checkbutton(opts_frame, text="Case Sensitive", variable=case_var, bg=BG, fg=TEXT, selectcolor=BG2, activebackground=BG, activeforeground=TEXT).pack(side="left", padx=(0, 10))
    tk.Checkbutton(opts_frame, text="In msgid", variable=id_var, bg=BG, fg=TEXT, selectcolor=BG2, activebackground=BG, activeforeground=TEXT).pack(side="left", padx=(0, 10))
    tk.Checkbutton(opts_frame, text="In msgstr", variable=str_var, bg=BG, fg=TEXT, selectcolor=BG2, activebackground=BG, activeforeground=TEXT).pack(side="left")

    ctrl_frame.columnconfigure(1, weight=1)

    pw = tk.PanedWindow(frame, orient="vertical", bg=BG, sashwidth=6, sashpad=2)
    pw.pack(fill="both", expand=True, padx=12, pady=(8, 12))

    tree_frame = tk.Frame(pw, bg=BG2)
    pw.add(tree_frame, stretch="always", minsize=100)
    
    cols = ("file", "msgctxt")
    tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
    tree.heading("file", text="File")
    tree.heading("msgctxt", text="Context")
    tree.column("file", width=250, anchor="w")
    tree.column("msgctxt", width=150, anchor="w")
    
    sb = tk.Scrollbar(tree_frame, command=tree.yview, bg=BG3)
    tree.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)

    det_frame = tk.Frame(pw, bg=BG2)
    pw.add(det_frame, stretch="always", minsize=100)
    
    details = tk.Text(det_frame, bg=BG2, fg=TEXT, font=FONT_MONO, wrap="word", state="disabled", padx=8, pady=8, relief="flat")
    sb2 = tk.Scrollbar(det_frame, command=details.yview, bg=BG3)
    details.configure(yscrollcommand=sb2.set)
    sb2.pack(side="right", fill="y")
    details.pack(side="left", fill="both", expand=True)

    details.tag_config("head", foreground=ACCENT)
    details.tag_config("info", foreground=SUBTLE)

    results_data = []

    def _on_select(event):
        sel = tree.selection()
        if not sel: return
        idx = int(tree.item(sel[0], "tags")[0])
        res = results_data[idx]
        
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.insert("end", f"File: {res['file']}\nContext: {res['msgctxt']}\n\n", "head")
        details.insert("end", "msgid:\n", "info")
        details.insert("end", f"{res['msgid']}\n\n")
        details.insert("end", "msgstr:\n", "info")
        details.insert("end", f"{res['msgstr']}\n")
        details.configure(state="disabled")

    tree.bind("<<TreeviewSelect>>", _on_select)

    def _run_search():
        try:
            mod = importlib.import_module("po_search")
        except Exception as e:
            messagebox.showerror("Error", f"Could not load po_search: {e}")
            return
            
        path = path_var.get().strip()
        phrase = phrase_var.get()
        if not path or not phrase:
            return
            
        btn_search.configure(state="disabled", text="Searching...")
        frame.update_idletasks()
        
        for item in tree.get_children():
            tree.delete(item)
        results_data.clear()
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.configure(state="disabled")
        
        # Call the standalone search logic
        results = mod.search_all(path, phrase, case_var.get(), id_var.get(), str_var.get())
        results_data.extend(results)
        
        for i, res in enumerate(results):
            tree.insert("", "end", values=(res["file"], res["msgctxt"]), tags=(str(i),))
            
        btn_search.configure(state="normal", text="Search")
        
    phrase_entry.bind("<Return>", lambda e: _run_search())


# ══════════════════════════════════════════════════════════════════
#  MAIN APP
# ══════════════════════════════════════════════════════════════════

class ToolkitApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("PO Toolkit — Danganronpa TL")
        root.configure(bg=BG)
        root.geometry("820x640")
        root.minsize(700, 500)

        self._apply_theme()
        self._build()

    def _apply_theme(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=0)
        style.configure("TNotebook.Tab", background=TAB_BG, foreground=TAB_FG, font=FONT_BOLD, padding=(10, 6), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", BG)])

        # Treeview styling for search tab
        style.configure("Treeview", background=BG2, foreground=TEXT, fieldbackground=BG2, borderwidth=0, font=FONT)
        style.configure("Treeview.Heading", background=BG3, foreground=ACCENT, font=FONT_BOLD, relief="flat")
        style.map("Treeview", background=[("selected", TAB_SEL)], foreground=[("selected", BG)])

    def _build(self):
        hdr = tk.Frame(self.root, bg=ACCENT2, height=4)
        hdr.pack(fill="x")

        title_bar = tk.Frame(self.root, bg=BG)
        title_bar.pack(fill="x", padx=16, pady=(10, 4))
        tk.Label(title_bar, text="🌸  PO Toolkit", bg=BG, fg=ACCENT, font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Label(title_bar, text="Danganronpa Fan Translation", bg=BG, fg=SUBTLE, font=("Segoe UI", 9)).pack(side="left", padx=12)

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        _build_validator_tab(nb)
        _build_linebreak_tab(nb)
        _build_mass_replace_tab(nb)
        _build_search_tab(nb)
        _build_backup_tab(nb)
        _build_gemini_tab(nb)

# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    root = TkinterDnD.Tk() if _DND else tk.Tk()
    ToolkitApp(root)
    root.mainloop()