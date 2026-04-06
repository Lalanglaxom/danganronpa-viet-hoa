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
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.filedialog as fd
from tkinter import ttk, filedialog, messagebox

# ── Optional drag-and-drop ─────────────────────────────────────────
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND = True
except ImportError:
    _DND = False

# ══════════════════════════════════════════════════════════════════
#  CONFIG MANAGER (PERSISTENCE)
# ══════════════════════════════════════════════════════════════════
CONFIG_FILE = "po_toolkit_config.json"
CONFIG = {}

try:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            CONFIG = json.load(f)
except Exception:
    pass

def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=4)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
#  THEME: ULTIMATE GAMER (CHIAKI NANAMI) 🎮👾
# ══════════════════════════════════════════════════════════════════

BG       = "#1c1c22"   # Dark handheld console grey
BG2      = "#282a36"   # Deep slate (UI panels)
BG3      = "#3b3e4f"   # Lighter slate (highlights)
ACCENT   = "#98d9d6"   # Chiaki's Teal hoodie color
ACCENT2  = "#f4b3c2"   # Soft Hair/Ribbon Pink
TEXT     = "#eef2f3"   # Soft white
SUBTLE   = "#7b8296"   # Muted blue-grey
GREEN    = "#a5d6a7"   # Level up green
RED      = "#ffab91"   # Game over red
TAB_BG   = "#282a36"   # Inactive tab
TAB_SEL  = "#98d9d6"   # Selected tab (Teal)
TAB_FG   = "#1c1c22"   # Dark text on Teal tab

# Custom sentence colors for Mass Replace
OLD_SENT_CLR = "#f4b3c2" # Soft Pink (Old)
NEW_SENT_CLR = "#98d9d6" # Teal (New)

FONT       = ("Segoe UI", 9)
FONT_BOLD  = ("Segoe UI", 9, "bold")
FONT_MONO  = ("Consolas", 8)
FONT_TITLE = ("Segoe UI", 13, "bold")

# ══════════════════════════════════════════════════════════════════
#  STDOUT REDIRECT & HELPERS
# ══════════════════════════════════════════════════════════════════

class _TabStream(io.StringIO):
    def __init__(self, text_widget: tk.Text):
        super().__init__()
        self._w = text_widget

    def write(self, s: str):
        self._w.configure(state="normal")
        if s.strip().startswith("✔") or s.strip().startswith("✓") or s.strip().startswith("[BACKUP]") or s.strip().startswith("[LIN"): tag = "good"
        elif s.strip().startswith("✗") or s.strip().startswith("[ERROR]") or s.strip().startswith("⚠"): tag = "bad"
        elif s.strip().startswith("!") or s.strip().startswith("[SKIP") or s.strip().startswith("[NOT"): tag = "warn"
        elif s.startswith("═") or s.startswith("="): tag = "head"
        elif s.strip().startswith("-"):
            tag = "old_sent"
        elif s.strip().startswith("+"):
            tag = "new_sent"
        else: tag = "info"
        self._w.insert("end", s, tag)
        self._w.see("end")
        self._w.configure(state="disabled")
        self._w.update_idletasks()
    def flush(self): pass

def _make_log(parent) -> tk.Text:
    frame = tk.Frame(parent, bg=BG2, highlightbackground=BG3, highlightthickness=1)
    frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
    sb = tk.Scrollbar(frame, bg=BG3, troughcolor=BG2)
    sb.pack(side="right", fill="y")
    log = tk.Text(frame, bg=BG2, fg=TEXT, font=FONT_MONO, state="disabled", wrap="word", padx=8, pady=6, yscrollcommand=sb.set, insertbackground=TEXT, relief="flat")
    sb.config(command=log.yview)
    log.pack(fill="both", expand=True)
    log.tag_config("good", foreground=GREEN)
    log.tag_config("bad",  foreground=RED)
    log.tag_config("warn", foreground="#ffcc80")
    log.tag_config("head", foreground=ACCENT)
    log.tag_config("info", foreground=SUBTLE)
    log.tag_config("old_sent", foreground=OLD_SENT_CLR) # Custom tag for old text
    log.tag_config("new_sent", foreground=NEW_SENT_CLR) # Custom tag for new text
    return log

def _run_in_thread(fn, log: tk.Text, btn: tk.Button, btn_text: str):
    def _target():
        btn.configure(state="disabled", text="Running…")
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = _TabStream(log)
        try: fn()
        except Exception as e: print(f"\n⚠ Error: {e}")
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
            btn.configure(state="normal", text=btn_text)
    threading.Thread(target=_target, daemon=True).start()

def _styled_btn(parent, text, cmd, **kw) -> tk.Button:
    defaults = dict(bg=ACCENT, fg=BG, font=FONT_BOLD, relief="flat", cursor="hand2", activebackground=ACCENT2, activeforeground=TEXT, padx=18, pady=6)
    defaults.update(kw)
    return tk.Button(parent, text=text, command=cmd, **defaults)

def _section_label(parent, text: str):
    tk.Label(parent, text=text, bg=BG, fg=SUBTLE, font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=12, pady=(8, 2))

def _tab_frame(notebook, title: str) -> tk.Frame:
    frame = tk.Frame(notebook, bg=BG)
    notebook.add(frame, text=f"  {title}  ")
    return frame

def _build_path_row(parent, label_text, config_key):
    frame = tk.Frame(parent, bg=BG)
    frame.pack(fill="x", padx=12, pady=4)
    tk.Label(frame, text=label_text, bg=BG, fg=TEXT, font=FONT_BOLD, width=16, anchor="w").pack(side="left")
    var = tk.StringVar()
    saved = CONFIG.get(config_key, "")
    var.set(saved if saved else "Drop a folder/file here...")
    def _on_change(*args):
        val = var.get()
        if val and "Drop" not in val:
            CONFIG[config_key] = val
            save_config()
    var.trace_add("write", _on_change)
    entry = tk.Entry(frame, textvariable=var, bg=BG2, fg=TEXT, insertbackground=TEXT, font=FONT)
    entry.pack(side="left", fill="x", expand=True, padx=8)
    if _DND:
        entry.drop_target_register(DND_FILES)
        def _on_drop(event):
            path = event.data
            if path.startswith('{') and path.endswith('}'): path = path[1:-1]
            var.set(path)
        entry.dnd_bind('<<Drop>>', _on_drop)
    def _browse():
        d = filedialog.askdirectory()
        if d: var.set(d)
    tk.Button(frame, text="Browse", command=_browse, bg=BG3, fg=TEXT, font=FONT, relief="flat", padx=8).pack(side="left")
    return var

def _open_file_system(filepath, mode="file"):
    if not os.path.exists(filepath):
        messagebox.showerror("Error", f"File no longer exists:\n{filepath}")
        return
    try:
        if mode == "file":
            if sys.platform == "win32": os.startfile(filepath)
            else: subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", filepath])
        else:
            if sys.platform == "win32": subprocess.Popen(["explorer", "/select,", os.path.normpath(filepath)])
            else: subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", os.path.dirname(filepath)])
    except Exception as e: messagebox.showerror("Error", f"Could not open: {e}")

def _launch_chrome_debug():
    chrome_cmd = r'"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeDebug"'
    try:
        subprocess.Popen(chrome_cmd, shell=True)
        print("═ [CHROME] Attempting to launch Chrome Debug mode...")
        print("  Command: " + chrome_cmd)
    except Exception as e:
        print(f"✗ [ERROR] Could not launch Chrome: {e}")

# ══════════════════════════════════════════════════════════════════
#  TAB BUILDERS
# ══════════════════════════════════════════════════════════════════

def _build_simple_run_tab(notebook, title: str, module_name: str, run_fn_name: str, path_configs: list):
    frame = _tab_frame(notebook, title)
    hdr = tk.Frame(frame, bg=BG)
    hdr.pack(fill="x", padx=12, pady=(14, 6))
    tk.Label(hdr, text=title, bg=BG, fg=ACCENT, font=FONT_TITLE).pack(side="left")
    
    path_vars = [_build_path_row(frame, label, config_key) for label, config_key in path_configs]
    
    # Pack btn_row to bottom FIRST
    btn_row = tk.Frame(frame, bg=BG)
    btn_row.pack(side="bottom", fill="x", padx=12, pady=(0, 12))

    _section_label(frame, "LOG")
    log = _make_log(frame)

    def _run():
        try:
            mod = importlib.import_module(module_name)
            fn = getattr(mod, run_fn_name)
            paths = [v.get() for v in path_vars]
            valid_paths = [p for p in paths if p and "Drop" not in p]
            if len(path_vars) > 0 and len(valid_paths) != len(path_vars):
                print("⚠ Please set all required paths above."); return

            def _wrapper():
                orig_dir, orig_file = fd.askdirectory, fd.askopenfilename
                def mock_ask(*args, **kwargs): return valid_paths.pop(0) if valid_paths else ""
                fd.askdirectory = fd.askopenfilename = mock_ask
                try: fn()
                finally: fd.askdirectory, fd.askopenfilename = orig_dir, orig_file
            _run_in_thread(_wrapper, log, btn, f"▶  Run {title}")
        except Exception as e: print(f"⚠ Could not load {module_name}: {e}")

    btn = _styled_btn(btn_row, f"▶  Run {title}", _run)
    btn.pack(side="right")

def _build_backup_tab(notebook): _build_simple_run_tab(notebook, "Backup & Sync", "po_backup_sync", "backup_and_sync", [("Translated Folder:", "backup_trans"), ("LIN Folder:", "backup_lin")])
def _build_validator_tab(notebook): _build_simple_run_tab(notebook, "Validator", "po_validator", "run", [("Validation Folder:", "validator_folder")])
def _build_linebreak_tab(notebook): _build_simple_run_tab(notebook, "Line-Break Fixer", "po_linebreak_fixer", "run", [("Target Folder/File:", "linebreak_path")])

def _build_gemini_tab(notebook):
    frame = _tab_frame(notebook, "Gemini Translator")
    hdr = tk.Frame(frame, bg=BG)
    hdr.pack(fill="x", padx=12, pady=(14, 6))
    tk.Label(hdr, text="Gemini Translator", bg=BG, fg=ACCENT, font=FONT_TITLE).pack(side="left")
    path_var = _build_path_row(frame, "Translated Folder:", "gemini_folder")
    
    # Pack btn_row to bottom FIRST
    btn_row = tk.Frame(frame, bg=BG)
    btn_row.pack(side="bottom", fill="x", padx=12, pady=(0, 12))

    _section_label(frame, "LOG")
    log = _make_log(frame)

    def _run():
        try:
            mod = importlib.import_module("po_gemini_translator")
            path = path_var.get().strip()
            if not path or "Drop" in path: print("⚠ Set path first."); return
            def _wrapper():
                orig = fd.askdirectory
                fd.askdirectory = lambda *a,**k: path
                try: mod.run()
                finally: fd.askdirectory = orig
            _run_in_thread(_wrapper, log, btn, "▶  Run Gemini Translator")
        except Exception as e: print(f"⚠ Error: {e}")

    btn = _styled_btn(btn_row, "▶  Run Gemini Translator", _run)
    btn.pack(side="right")
    btn_chrome = tk.Button(btn_row, text="🌐 Launch Chrome Debug", 
                           command=lambda: _run_in_thread(_launch_chrome_debug, log, btn_chrome, "🌐 Launch Chrome Debug"),
                           bg=BG3, fg=ACCENT, font=FONT_BOLD, relief="flat", cursor="hand2", padx=15, pady=6, activebackground=BG2)
    btn_chrome.pack(side="right", padx=8)

def _build_mass_replace_tab(notebook):
    frame = _tab_frame(notebook, "Mass Replace")
    hdr = tk.Frame(frame, bg=BG); hdr.pack(fill="x", padx=12, pady=(14, 6))
    tk.Label(hdr, text="Mass Replace", bg=BG, fg=ACCENT, font=FONT_TITLE).pack(side="left")
    path_var = _build_path_row(frame, "Folder / File:", "mass_replace_path")
    _section_label(frame, "CRITERIA")
    scroll_container = tk.Frame(frame, bg=BG2, highlightbackground=BG3, highlightthickness=1, height=120)
    scroll_container.pack(fill="x", padx=12, pady=4); scroll_container.pack_propagate(False)
    canvas = tk.Canvas(scroll_container, bg=BG2, highlightthickness=0)
    scrollbar = tk.Scrollbar(scroll_container, orient="vertical", command=canvas.yview, bg=BG3)
    crit_inner = tk.Frame(canvas, bg=BG2)
    crit_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=crit_inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set); canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    check_vars, criteria = [], []
    try:
        mod = importlib.import_module("po_mass_replace")
        if hasattr(mod, "CRITERIA"):
            criteria = mod.CRITERIA
            for c in criteria:
                # --- DYNAMIC LABEL GENERATION ---
                char = f"[{c.get('character')}] " if c.get('character') else ""
                scope = f"({c.get('scope')}) " if c.get('scope') else ""
                whole = "[Whole] " if c.get('whole_word') else ""
                
                # Show the first replacement pair as the main label
                repls = c.get('replace', [])
                pairs = f"{repls[0][0]} → {repls[0][1]}" if repls else "No Rules"
                
                # Combine them: [CHAR] (scope) [W] Find -> Replace
                display_label = f"{char}{scope}{whole}{pairs}"
                
                var = tk.BooleanVar(value=False)
                check_vars.append(var)
                tk.Checkbutton(crit_inner, text=display_label, variable=var, 
                               bg=BG2, fg=TEXT, selectcolor=BG3, 
                               activebackground=BG2, font=FONT).pack(anchor="w", padx=8, pady=2)
    except Exception: pass
    
    # Pack btn_row to bottom FIRST
    btn_row = tk.Frame(frame, bg=BG)
    btn_row.pack(side="bottom", fill="x", padx=12, pady=(0, 12))

    _section_label(frame, "LOG")
    log = _make_log(frame)

    def _run():
        path = path_var.get().strip()
        if not path or "Drop" in path: print("⚠ Set path first."); return
        active = [c for c, v in zip(criteria, check_vars) if v.get()]
        if not active: print("⚠ Check criteria."); return
        def _log_fn(m, t="info"): print(f"✓ {m}" if t=="good" else f"✗ {m}" if t=="bad" else m)
        print(f"═ Starting: {path}")
        mod.process_path(path, active, _log_fn)
        print("═ Finished.")
    
    btn = _styled_btn(btn_row, "▶  Run Mass Replace", lambda: _run_in_thread(_run, log, btn, "▶  Run Mass Replace"))
    btn.pack(side="right")

def _build_search_tab(notebook):
    frame = _tab_frame(notebook, "Search")
    hdr = tk.Frame(frame, bg=BG); hdr.pack(fill="x", padx=12, pady=(14, 6))
    tk.Label(hdr, text="Search", bg=BG, fg=ACCENT, font=FONT_TITLE).pack(side="left")
    path_var = _build_path_row(frame, "Search Folder:", "search_path")
    phrase_frame = tk.Frame(frame, bg=BG); phrase_frame.pack(fill="x", padx=12, pady=4)
    tk.Label(phrase_frame, text="Phrase:", bg=BG, fg=TEXT, font=FONT_BOLD, width=16, anchor="w").pack(side="left")
    phrase_var = tk.StringVar()
    phrase_entry = tk.Entry(phrase_frame, textvariable=phrase_var, bg=BG2, fg=TEXT, insertbackground=TEXT, font=FONT)
    phrase_entry.pack(side="left", fill="x", expand=True, padx=8)
    btn_search = _styled_btn(phrase_frame, "Search", lambda: _run_search())
    btn_search.pack(side="left")
    opts_frame = tk.Frame(frame, bg=BG); opts_frame.pack(fill="x", padx=12, pady=(0, 4))
    tk.Label(opts_frame, text="", bg=BG, width=16).pack(side="left") 
    case_var, id_var, str_var = tk.BooleanVar(value=False), tk.BooleanVar(value=True), tk.BooleanVar(value=True)
    tk.Checkbutton(opts_frame, text="Case Sensitive", variable=case_var, bg=BG, fg=TEXT, selectcolor=BG2).pack(side="left", padx=(8, 10))
    tk.Checkbutton(opts_frame, text="In msgid", variable=id_var, bg=BG, fg=TEXT, selectcolor=BG2).pack(side="left", padx=(0, 10))
    tk.Checkbutton(opts_frame, text="In msgstr", variable=str_var, bg=BG, fg=TEXT, selectcolor=BG2).pack(side="left")
    pw = tk.PanedWindow(frame, orient="vertical", bg=BG, sashwidth=6); pw.pack(fill="both", expand=True, padx=12, pady=(8, 12))
    tree_frame = tk.Frame(pw, bg=BG2); pw.add(tree_frame, stretch="always")
    tree = ttk.Treeview(tree_frame, columns=("file", "msgctxt"), show="headings", selectmode="browse")
    tree.heading("file", text="File"); tree.heading("msgctxt", text="Context")
    tree.column("file", width=250); tree.column("msgctxt", width=150)
    sb = tk.Scrollbar(tree_frame, command=tree.yview, bg=BG3); tree.configure(yscrollcommand=sb.set); sb.pack(side="right", fill="y"); tree.pack(side="left", fill="both", expand=True)
    det_frame = tk.Frame(pw, bg=BG2); pw.add(det_frame, stretch="always")
    action_frame = tk.Frame(det_frame, bg=BG3); action_frame.pack(fill="x", side="bottom")
    sel_path = [None]
    def _op_f(): 
        if sel_path[0]: _open_file_system(sel_path[0], "file")
    def _op_d():
        if sel_path[0]: _open_file_system(sel_path[0], "folder")
    b1 = tk.Button(action_frame, text="Open File", command=_op_f, bg=BG2, fg=TEXT, relief="flat", font=FONT, padx=12)
    b1.pack(side="left", padx=(8, 4), pady=6); b1.configure(state="disabled")
    b2 = tk.Button(action_frame, text="Open Folder", command=_op_d, bg=BG2, fg=TEXT, relief="flat", font=FONT, padx=12)
    b2.pack(side="left", padx=4, pady=6); b2.configure(state="disabled")
    dt_f = tk.Frame(det_frame, bg=BG2); dt_f.pack(fill="both", expand=True)
    details = tk.Text(dt_f, bg=BG2, fg=TEXT, font=FONT_MONO, wrap="word", state="disabled", padx=8, pady=8, relief="flat")
    sb2 = tk.Scrollbar(dt_f, command=details.yview, bg=BG3); details.configure(yscrollcommand=sb2.set); sb2.pack(side="right", fill="y"); details.pack(side="left", fill="both", expand=True)
    details.tag_config("head", foreground=ACCENT); details.tag_config("info", foreground=SUBTLE)
    # Thêm vào dưới nó (hoặc thay thế info):
    details.tag_config("msgid_clr", foreground=OLD_SENT_CLR) # Màu Hồng nhạt giống Mass Replace
    details.tag_config("msgstr_clr", foreground=GREEN)        # Màu Xanh lá "Level up" cho rực rỡ
    results_data = []
    def _on_s(e):
            sel = tree.selection()
            if not sel: b1.configure(state="disabled"); b2.configure(state="disabled"); return
            res = results_data[int(tree.item(sel[0], "tags")[0])]
            sel_path[0] = os.path.join(path_var.get().strip(), res['file'])
            b1.configure(state="normal"); b2.configure(state="normal")
            details.configure(state="normal"); details.delete("1.0", "end")
            
            # Tiêu đề File và Context (Giữ màu ACCENT)
            details.insert("end", f"File: {res['file']}\nContext: {res['msgctxt']}\n\n", "head")
            
            # Phần msgid (Dùng màu hồng mới)
            details.insert("end", "msgid:\n", "head")
            details.insert("end", f"{res['msgid']}\n\n", "msgid_clr")
            
            # Phần msgstr (Dùng màu xanh lá mới)
            details.insert("end", "msgstr:\n", "head")
            details.insert("end", f"{res['msgstr']}\n", "msgstr_clr")
            
            details.configure(state="disabled")
    tree.bind("<<TreeviewSelect>>", _on_s)
    def _run_search():
        mod = importlib.import_module("po_search")
        path, phr = path_var.get().strip(), phrase_var.get()
        if not path or "Drop" in path or not phr: return
        btn_search.configure(state="disabled", text="..."); tree.delete(*tree.get_children()); results_data.clear()
        res = mod.search_all(path, phr, case_var.get(), id_var.get(), str_var.get()); results_data.extend(res)
        for i, r in enumerate(res): tree.insert("", "end", values=(r["file"], r["msgctxt"]), tags=(str(i),))
        btn_search.configure(state="normal", text="Search")
    phrase_entry.bind("<Return>", lambda e: _run_search())

# ══════════════════════════════════════════════════════════════════
#  MAIN APP
# ══════════════════════════════════════════════════════════════════

class ToolkitApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("🎮  PO Toolkit — Nanami Edition")
        root.configure(bg=BG); root.geometry("820x660"); root.minsize(700, 500)
        self._apply_theme(); self._build()

    def _apply_theme(self):
        style = ttk.Style()
        try: style.theme_use("clam")
        except: pass
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=0)
        style.configure("TNotebook.Tab", background=TAB_BG, foreground=TEXT, font=FONT_BOLD, padding=(15, 8), borderwidth=0, focuscolor=TAB_BG)
        style.map("TNotebook.Tab", 
                  background=[("selected", ACCENT)], 
                  foreground=[("selected", BG)],
                  padding=[("selected", (15, 8))],
                  expand=[("selected", (0, 0, 0, 0))])
        style.configure("Treeview", background=BG2, foreground=TEXT, fieldbackground=BG2, borderwidth=0, font=FONT)
        style.configure("Treeview.Heading", background=BG3, foreground=ACCENT, font=FONT_BOLD, relief="flat")
        style.map("Treeview", background=[("selected", TAB_SEL)], foreground=[("selected", BG)])

    def _build(self):
        tk.Frame(self.root, bg=ACCENT2, height=4).pack(fill="x")
        title_bar = tk.Frame(self.root, bg=BG); title_bar.pack(fill="x", padx=16, pady=(10, 4))
        tk.Label(title_bar, text="🎮  PO Toolkit — Nanami Edition", bg=BG, fg=ACCENT, font=("Segoe UI", 16, "bold")).pack(side="left")
        nb = ttk.Notebook(self.root); nb.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        _build_validator_tab(nb); _build_linebreak_tab(nb); _build_mass_replace_tab(nb); _build_search_tab(nb); _build_backup_tab(nb); _build_gemini_tab(nb)

if __name__ == "__main__":
    root = TkinterDnD.Tk() if _DND else tk.Tk()
    ToolkitApp(root)
    root.mainloop()