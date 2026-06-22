from __future__ import annotations

import json
import re
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:  # tkinterdnd2 is optional; Windows native drop is used as fallback.
    DND_FILES = None
    TkinterDnD = None

from .backup import make_backups, restore_working_po_from_copies, sync_by_filename
from .cancel import OperationCancelled
from .config import load_config, save_config
from .linewrap import wrap_msgstr, wrap_path
from .rules import apply_rules_to_path, load_rules, rule_to_dict, save_rules
from .search import search_path
from .text_utils import visible_len
from .gemini_web import DEFAULT_BATCH_RETRIES, DEFAULT_CHROME_USER_DATA_DIR, DEFAULT_MAX_ENTRIES_PER_BATCH, open_chrome_debug, run_gemini_web_path
from .translator import apply_response_to_file, write_manual_jobs
from .validation import format_text_report, validate_path, write_reports

BG = "#1f2230"
PANEL = "#2a2e3f"
TEXT = "#f1f3f6"
ACCENT = "#98d9d6"
WARN = "#ffd37a"
BAD = "#ff9f9f"
GOOD = "#a7e8a3"
FONT = ("Segoe UI", 9)
MONO = ("Consolas", 9)


class ToolkitGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = load_config()
        self._stop_event = threading.Event()
        self._active_thread: threading.Thread | None = None
        self._active_log: tk.Text | None = None
        self._drop_refs: list[object] = []
        self.stop_button: tk.Button | None = None
        root.title("DR PO Toolkit — Refactored Base")
        root.geometry("980x700")
        root.configure(bg=BG)
        self._apply_style()
        self._build()

    def _apply_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook", background=BG)
        style.configure("TNotebook.Tab", padding=(14, 7), font=("Segoe UI", 9, "bold"))
        style.configure("Treeview", font=FONT, rowheight=24)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build(self) -> None:
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=16, pady=(12, 4))
        title = tk.Label(top, text="DR PO Toolkit — Refactored Base", bg=BG, fg=ACCENT, font=("Segoe UI", 16, "bold"))
        title.pack(side="left")
        self.stop_button = tk.Button(
            top,
            text="Stop Current Action",
            command=self._request_stop,
            bg=BAD,
            fg="#111",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=14,
            pady=6,
            state="disabled",
        )
        self.stop_button.pack(side="right")
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        self._build_validate_tab(nb)
        self._build_replace_tab(nb)
        self._build_rule_editor_tab(nb)
        self._build_linewrap_tab(nb)
        self._build_search_tab(nb)
        self._build_translate_tab(nb)
        self._build_backup_tab(nb)

    def _frame(self, nb: ttk.Notebook, title: str) -> tk.Frame:
        frame = tk.Frame(nb, bg=BG)
        nb.add(frame, text=title)
        return frame

    def _log(self, parent: tk.Frame) -> tk.Text:
        log = tk.Text(parent, bg=PANEL, fg=TEXT, insertbackground=TEXT, font=MONO, wrap="word", height=16, relief="flat")
        log.tag_config("good", foreground=GOOD)
        log.tag_config("bad", foreground=BAD)
        log.tag_config("warn", foreground=WARN)
        log.pack(fill="both", expand=True, padx=12, pady=12)
        return log

    def _write_log(self, log: tk.Text, text: str, tag: str | None = None) -> None:
        log.configure(state="normal")
        log.insert("end", text + ("" if text.endswith("\n") else "\n"), tag)
        log.see("end")
        log.configure(state="disabled")

    def _clear_log(self, log: tk.Text) -> None:
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")

    def _request_stop(self) -> None:
        self._stop_event.set()
        if self._active_log is not None:
            self._write_log(self._active_log, "Stop requested. Waiting for current safe checkpoint...", "warn")

    def _check_stop(self) -> None:
        if self._stop_event.is_set():
            raise OperationCancelled("Stopped by user.")

    def _run_threaded(self, button: tk.Button, log: tk.Text, fn) -> None:
        if self._active_thread is not None and self._active_thread.is_alive():
            messagebox.showwarning("Busy", "Another action is running. Press Stop Current Action first.")
            return

        def logwrite(text, tag=None):
            self.root.after(0, lambda: self._write_log(log, text, tag))

        def worker():
            self._stop_event.clear()
            self._active_log = log
            self.root.after(0, lambda: (
                button.configure(state="disabled"),
                self.stop_button.configure(state="normal") if self.stop_button else None,
                self._clear_log(log),
            ))
            try:
                self._check_stop()
                fn(logwrite)
            except OperationCancelled:
                self.root.after(0, lambda: self._write_log(log, "Stopped by user.", "warn"))
            except Exception as exc:
                self.root.after(0, lambda: self._write_log(log, f"ERROR: {exc}", "bad"))
            finally:
                self.root.after(0, lambda: (
                    button.configure(state="normal"),
                    self.stop_button.configure(state="disabled") if self.stop_button else None,
                ))
                self._active_log = None
        self._active_thread = threading.Thread(target=worker, daemon=True)
        self._active_thread.start()

    def _path_row(self, parent: tk.Frame, label: str, key: str, file: bool = False) -> tk.StringVar:
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=12, pady=5)
        tk.Label(row, text=label, bg=BG, fg=TEXT, font=("Segoe UI", 9, "bold"), width=14, anchor="w").pack(side="left")
        var = tk.StringVar(value=self.config.get(key, ""))
        ent = tk.Entry(row, textvariable=var, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", font=FONT)
        ent.pack(side="left", fill="x", expand=True, padx=8, ipady=5)

        def browse():
            path = filedialog.askopenfilename(filetypes=[("PO/JSON/text", "*.po *.json *.txt"), ("All", "*.*")]) if file else filedialog.askdirectory()
            if path:
                var.set(path)
                self.config[key] = path
                save_config(self.config)

        tk.Button(row, text="Browse", command=browse, bg=ACCENT, fg="#111", relief="flat", padx=10).pack(side="left")
        return var

    def _button(self, parent: tk.Frame, text: str, command) -> tk.Button:
        btn = tk.Button(parent, text=text, command=command, bg=ACCENT, fg="#111", font=("Segoe UI", 9, "bold"), relief="flat", padx=14, pady=6)
        return btn

    def _parse_dropped_paths(self, data: str) -> list[str]:
        """Parse TkDND file/folder drop payload into clean paths."""
        try:
            return [str(item) for item in self.root.tk.splitlist(data)]
        except Exception:
            # Conservative fallback for braced Windows paths: {C:/Long Path} C:/Other
            out: list[str] = []
            buf: list[str] = []
            in_brace = False
            token: list[str] = []
            for ch in data:
                if ch == "{" and not in_brace:
                    in_brace = True
                    token = []
                    continue
                if ch == "}" and in_brace:
                    in_brace = False
                    out.append("".join(token))
                    token = []
                    continue
                if ch.isspace() and not in_brace:
                    if buf:
                        out.append("".join(buf))
                        buf = []
                    continue
                (token if in_brace else buf).append(ch)
            if buf:
                out.append("".join(buf))
            return out

    def _ask_restore_folders(self, title: str) -> list[str]:
        """Ask for one or more folders.

        Windows gets a real multi-folder picker. Other platforms keep the
        standard Tk single-folder picker, but drag/drop can still add many paths
        when tkinterdnd2 is installed.
        """
        if sys.platform == "win32":
            try:
                paths = self._ask_windows_multiple_directories(title)
                if paths:
                    return paths
            except Exception:
                pass
        folder = filedialog.askdirectory(title=title)
        return [folder] if folder else []

    def _ask_windows_multiple_directories(self, title: str) -> list[str]:
        """Native Windows folder picker with multi-select, no extra package."""
        if sys.platform != "win32":
            return []

        import ctypes
        import uuid
        from ctypes import wintypes

        HRESULT = ctypes.c_long
        DWORD = wintypes.DWORD
        ULONG = wintypes.ULONG
        UINT = wintypes.UINT
        HWND = wintypes.HWND
        LPCWSTR = wintypes.LPCWSTR

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        def guid(value: str) -> GUID:
            return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)

        def failed(hr: int) -> bool:
            return ctypes.c_long(hr).value < 0

        def hresult_u32(hr: int) -> int:
            return ctypes.c_ulong(hr).value

        def com_method(ptr: ctypes.c_void_p, index: int, restype, *argtypes):
            vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
            return prototype(vtbl[index])

        CLSCTX_INPROC_SERVER = 0x1
        COINIT_APARTMENTTHREADED = 0x2
        ERROR_CANCELLED = 0x800704C7
        SIGDN_FILESYSPATH = 0x80058000
        FOS_PICKFOLDERS = 0x00000020
        FOS_FORCEFILESYSTEM = 0x00000040
        FOS_ALLOWMULTISELECT = 0x00000200
        FOS_PATHMUSTEXIST = 0x00000800

        CLSID_FileOpenDialog = guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")
        IID_IFileOpenDialog = guid("D57C7288-D4AD-4768-BE02-9D969532D960")

        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, DWORD]
        ole32.CoInitializeEx.restype = HRESULT
        ole32.CoUninitialize.argtypes = []
        ole32.CoUninitialize.restype = None
        ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        ole32.CoCreateInstance.restype = HRESULT
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole32.CoTaskMemFree.restype = None

        initialized = False
        dialog = ctypes.c_void_p()
        results_array = ctypes.c_void_p()
        try:
            hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            # RPC_E_CHANGED_MODE means COM is already initialized differently;
            # still try to use the dialog and do not CoUninitialize in that case.
            if hresult_u32(hr) != 0x80010106:
                initialized = True
            hr = ole32.CoCreateInstance(
                ctypes.byref(CLSID_FileOpenDialog),
                None,
                CLSCTX_INPROC_SERVER,
                ctypes.byref(IID_IFileOpenDialog),
                ctypes.byref(dialog),
            )
            if failed(hr) or not dialog.value:
                return []

            get_options = com_method(dialog, 10, HRESULT, ctypes.POINTER(DWORD))
            set_options = com_method(dialog, 9, HRESULT, DWORD)
            set_title = com_method(dialog, 17, HRESULT, LPCWSTR)
            show = com_method(dialog, 3, HRESULT, HWND)
            get_results = com_method(dialog, 27, HRESULT, ctypes.POINTER(ctypes.c_void_p))

            options = DWORD(0)
            if not failed(get_options(dialog, ctypes.byref(options))):
                set_options(dialog, options.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_ALLOWMULTISELECT | FOS_PATHMUSTEXIST)
            else:
                set_options(dialog, FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_ALLOWMULTISELECT | FOS_PATHMUSTEXIST)
            set_title(dialog, title)

            owner = HWND(self.root.winfo_id())
            hr = show(dialog, owner)
            if hresult_u32(hr) == ERROR_CANCELLED:
                return []
            if failed(hr):
                return []

            hr = get_results(dialog, ctypes.byref(results_array))
            if failed(hr) or not results_array.value:
                return []

            array_get_count = com_method(results_array, 7, HRESULT, ctypes.POINTER(DWORD))
            array_get_item_at = com_method(results_array, 8, HRESULT, DWORD, ctypes.POINTER(ctypes.c_void_p))
            release = com_method(results_array, 2, ULONG)

            count = DWORD(0)
            if failed(array_get_count(results_array, ctypes.byref(count))):
                return []

            selected: list[str] = []
            for i in range(count.value):
                item = ctypes.c_void_p()
                if failed(array_get_item_at(results_array, i, ctypes.byref(item))) or not item.value:
                    continue
                try:
                    item_get_display_name = com_method(item, 5, HRESULT, DWORD, ctypes.POINTER(ctypes.c_void_p))
                    item_release = com_method(item, 2, ULONG)
                    raw_path = ctypes.c_void_p()
                    if not failed(item_get_display_name(item, SIGDN_FILESYSPATH, ctypes.byref(raw_path))) and raw_path.value:
                        selected.append(ctypes.wstring_at(raw_path))
                        ole32.CoTaskMemFree(raw_path)
                finally:
                    try:
                        item_release(item)
                    except Exception:
                        pass
            return selected
        finally:
            if results_array.value:
                try:
                    release(results_array)
                except Exception:
                    pass
            if dialog.value:
                try:
                    dialog_release = com_method(dialog, 2, ULONG)
                    dialog_release(dialog)
                except Exception:
                    pass
            if initialized:
                ole32.CoUninitialize()

    def _enable_path_drop(self, widget: tk.Widget, callback) -> bool:
        """Enable file/folder drop for a widget.

        Uses tkinterdnd2 when available, then falls back to native Windows
        WM_DROPFILES support. The callback receives list[str] paths.
        """
        enabled = False
        if DND_FILES is not None and hasattr(widget, "drop_target_register"):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", lambda event: callback(self._parse_dropped_paths(event.data)))
                enabled = True
            except Exception:
                enabled = False
        if not enabled:
            enabled = self._enable_windows_file_drop(widget, callback)
        return enabled

    def _enable_windows_file_drop(self, widget: tk.Widget, callback) -> bool:
        """Windows Explorer drag/drop support without third-party packages."""
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            from ctypes import wintypes

            widget.update_idletasks()
            hwnd = wintypes.HWND(widget.winfo_id())
            shell32 = ctypes.windll.shell32
            user32 = ctypes.windll.user32
            WM_DROPFILES = 0x0233
            GWLP_WNDPROC = -4

            WNDPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
            call_window_proc = user32.CallWindowProcW
            call_window_proc.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            call_window_proc.restype = wintypes.LPARAM

            if ctypes.sizeof(ctypes.c_void_p) == 8:
                get_window_long = user32.GetWindowLongPtrW
                set_window_long = user32.SetWindowLongPtrW
                get_window_long.restype = ctypes.c_void_p
                get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
                set_window_long.restype = ctypes.c_void_p
                set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            else:
                get_window_long = user32.GetWindowLongW
                set_window_long = user32.SetWindowLongW
                get_window_long.restype = ctypes.c_void_p
                get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
                set_window_long.restype = ctypes.c_void_p
                set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

            old_proc = get_window_long(hwnd, GWLP_WNDPROC)
            if not old_proc:
                return False

            def wndproc(window, msg, wparam, lparam):
                if msg == WM_DROPFILES:
                    hdrop = wparam
                    count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                    paths: list[str] = []
                    for index in range(count):
                        length = shell32.DragQueryFileW(hdrop, index, None, 0)
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        shell32.DragQueryFileW(hdrop, index, buffer, length + 1)
                        paths.append(buffer.value)
                    shell32.DragFinish(hdrop)
                    self.root.after(0, lambda dropped=paths: callback(dropped))
                    return 0
                return call_window_proc(old_proc, window, msg, wparam, lparam)

            new_proc = WNDPROC(wndproc)
            set_window_long(hwnd, GWLP_WNDPROC, ctypes.cast(new_proc, ctypes.c_void_p))
            shell32.DragAcceptFiles(hwnd, True)
            self._drop_refs.append((widget, hwnd, old_proc, new_proc))
            return True
        except Exception:
            return False

    def _build_validate_tab(self, nb: ttk.Notebook) -> None:
        frame = self._frame(nb, "Validate")
        path_var = self._path_row(frame, "Folder/File", "last_path")
        log = self._log(frame)
        btnrow = tk.Frame(frame, bg=BG)
        btnrow.pack(fill="x", padx=12, pady=(0, 12))
        reports_var = tk.BooleanVar(value=True)
        tk.Checkbutton(btnrow, text="Save reports", variable=reports_var, bg=BG, fg=TEXT, selectcolor=PANEL).pack(side="left")

        def run(logwrite):
            self._check_stop()
            path = path_var.get().strip()
            if not path:
                logwrite("Set path first.", "warn")
                return
            results = validate_path(path)
            logwrite(format_text_report(results, path))
            if reports_var.get():
                out_dir = Path(path) if Path(path).is_dir() else Path(path).parent
                txt, html = write_reports(results, out_dir, path)
                logwrite(f"Saved report: {txt}", "good")
                logwrite(f"Saved report: {html}", "good")

        btn = self._button(btnrow, "Run Validate", lambda: self._run_threaded(btn, log, run))
        btn.pack(side="right")

    def _build_replace_tab(self, nb: ttk.Notebook) -> None:
        frame = self._frame(nb, "Mass Replace")
        path_var = self._path_row(frame, "Folder/File", "last_path")
        rules_var = self._path_row(frame, "Rules JSON", "rules_file", file=True)
        opts = tk.Frame(frame, bg=BG)
        opts.pack(fill="x", padx=12, pady=5)
        dry_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opts, text="Dry run", variable=dry_var, bg=BG, fg=TEXT, selectcolor=PANEL).pack(side="left")
        log = self._log(frame)
        btnrow = tk.Frame(frame, bg=BG)
        btnrow.pack(fill="x", padx=12, pady=(0, 12))

        def run(logwrite):
            self._check_stop()
            path = path_var.get().strip()
            rules_path = rules_var.get().strip()
            rules = load_rules(rules_path)
            changes = apply_rules_to_path(path, rules, dry_run=dry_var.get())
            logwrite(f"Rules loaded: {len(rules)}")
            logwrite(f"Changes: {len(changes)}", "good" if changes else None)
            for ch in changes[:200]:
                self._check_stop()
                logwrite(f"{ch.file.name} | {ch.msgctxt} | {ch.rule_id} | {ch.count}")
                logwrite(f"- {ch.before}", "warn")
                logwrite(f"+ {ch.after}", "good")
            if dry_var.get():
                logwrite("Dry run only. Uncheck Dry run to write files.", "warn")

        btn = self._button(btnrow, "Run Replace", lambda: self._run_threaded(btn, log, run))
        btn.pack(side="right")

    def _build_rule_editor_tab(self, nb: ttk.Notebook) -> None:
        frame = self._frame(nb, "Rule Editor")
        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", padx=12, pady=8)
        rules_file = tk.StringVar(value=self.config.get("rules_file", "rules/mass_replace_rules.json"))
        tk.Entry(top, textvariable=rules_file, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", font=FONT).pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=5)
        rules: list[dict] = []

        body = tk.Frame(frame, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=6)
        listbox = tk.Listbox(body, bg=PANEL, fg=TEXT, selectbackground=ACCENT, selectforeground="#111", font=MONO, width=42)
        listbox.pack(side="left", fill="both", padx=(0, 8))
        form = tk.Frame(body, bg=BG)
        form.pack(side="left", fill="both", expand=True)

        fields: dict[str, tk.Variable] = {}
        specs = [
            ("id", tk.StringVar), ("priority", tk.StringVar), ("speaker", tk.StringVar),
            ("scope", tk.StringVar), ("find", tk.StringVar), ("replace", tk.StringVar),
            ("notes", tk.StringVar),
        ]
        for name, varcls in specs:
            row = tk.Frame(form, bg=BG)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=name, bg=BG, fg=TEXT, width=12, anchor="w").pack(side="left")
            var = varcls()
            fields[name] = var
            tk.Entry(row, textvariable=var, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", font=FONT).pack(side="left", fill="x", expand=True, ipady=4)
        for name in ["enabled", "whole_word", "case_sensitive", "stop_after"]:
            var = tk.BooleanVar(value=True if name in ["enabled", "case_sensitive"] else False)
            fields[name] = var
            tk.Checkbutton(form, text=name, variable=var, bg=BG, fg=TEXT, selectcolor=PANEL).pack(anchor="w")

        def refresh_list():
            listbox.delete(0, "end")
            for r in rules:
                label = f"{r.get('priority', 100):>4} | {r.get('speaker') or 'GLOBAL':<10} | {r.get('find','')} → {r.get('replace','')}"
                listbox.insert("end", label)

        def load_file():
            path = Path(rules_file.get())
            if not path.exists():
                messagebox.showwarning("Rules", "Rules file not found.")
                return
            loaded = load_rules(path)
            rules.clear()
            rules.extend(rule_to_dict(r) for r in loaded)
            refresh_list()
            self.config["rules_file"] = str(path)
            save_config(self.config)

        def save_file():
            path = Path(rules_file.get())
            normalized = []
            for r in rules:
                r.setdefault("enabled", True)
                r.setdefault("priority", 100)
                r.setdefault("case_sensitive", True)
                normalized.append(r)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"version": 2, "rules": normalized}, ensure_ascii=False, indent=2), encoding="utf-8")
            messagebox.showinfo("Rules", "Rules saved.")

        def selected_index() -> int | None:
            sel = listbox.curselection()
            return int(sel[0]) if sel else None

        def load_selected(_event=None):
            idx = selected_index()
            if idx is None:
                return
            r = rules[idx]
            for name, var in fields.items():
                if isinstance(var, tk.BooleanVar):
                    var.set(bool(r.get(name, False)))
                else:
                    val = r.get(name, "")
                    var.set("" if val is None else str(val))

        def collect_rule() -> dict:
            data = {}
            for name, var in fields.items():
                data[name] = var.get()
            data["priority"] = int(data.get("priority") or 100)
            data["speaker"] = data.get("speaker") or None
            data["scope"] = data.get("scope") or None
            return data

        def add_rule():
            rules.append(collect_rule())
            refresh_list()

        def update_rule():
            idx = selected_index()
            if idx is None:
                add_rule()
            else:
                rules[idx] = collect_rule()
                refresh_list()
                listbox.selection_set(idx)

        def delete_rule():
            idx = selected_index()
            if idx is not None:
                rules.pop(idx)
                refresh_list()

        listbox.bind("<<ListboxSelect>>", load_selected)
        btnrow = tk.Frame(frame, bg=BG)
        btnrow.pack(fill="x", padx=12, pady=(0, 12))
        for text, cmd in [("Load", load_file), ("Save", save_file), ("Add", add_rule), ("Update", update_rule), ("Delete", delete_rule)]:
            self._button(btnrow, text, cmd).pack(side="left", padx=4)
        try:
            load_file()
        except Exception:
            pass

    def _build_linewrap_tab(self, nb: ttk.Notebook) -> None:
        frame = self._frame(nb, "Line Wrap")
        path_var = self._path_row(frame, "Folder/File", "last_path")
        opts = tk.Frame(frame, bg=BG)
        opts.pack(fill="x", padx=12, pady=6)
        soft = tk.IntVar(value=int(self.config.get("soft_limit", 58)))
        hard = tk.IntVar(value=int(self.config.get("hard_limit", 64)))
        cuts = tk.IntVar(value=int(self.config.get("max_cuts", 2)))
        dry = tk.BooleanVar(value=True)
        for label, var in [("Soft", soft), ("Hard", hard), ("Max cuts", cuts)]:
            tk.Label(opts, text=label, bg=BG, fg=TEXT).pack(side="left")
            tk.Spinbox(opts, from_=1, to=200, textvariable=var, width=6, bg=PANEL, fg=TEXT).pack(side="left", padx=(4, 12))
        tk.Checkbutton(opts, text="Dry run", variable=dry, bg=BG, fg=TEXT, selectcolor=PANEL).pack(side="left")

        tester = tk.LabelFrame(frame, text="Line Wrap Test", bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold"), bd=1, relief="groove")
        tester.pack(fill="x", padx=12, pady=(4, 0))

        test_input = tk.Text(tester, bg=PANEL, fg=TEXT, insertbackground=TEXT, font=MONO, wrap="word", height=3, relief="flat")
        test_input.pack(fill="x", padx=8, pady=(6, 4))
        test_input.tag_configure("clt_tag", foreground="#1e90ff", font=(MONO[0], MONO[1], "bold"))

        highlight_after_id = None

        def highlight_clt_tags() -> None:
            nonlocal highlight_after_id
            highlight_after_id = None
            content = test_input.get("1.0", "end-1c")
            test_input.tag_remove("clt_tag", "1.0", "end")
            # Match real PO CLT forms used by the tool: <CLT>, <CLT 1>, <CLT_1>.
            # Also accepts <clt_N> typed as a placeholder while testing.
            for match in re.finditer(r"<\s*clt(?:[\s_]*(?:\d+|n))?\s*>", content, flags=re.IGNORECASE):
                start = f"1.0+{match.start()}c"
                end = f"1.0+{match.end()}c"
                test_input.tag_add("clt_tag", start, end)
            test_input.tag_raise("clt_tag")

        def schedule_clt_highlight(_event=None) -> None:
            nonlocal highlight_after_id
            if highlight_after_id is not None:
                try:
                    test_input.after_cancel(highlight_after_id)
                except Exception:
                    pass
            highlight_after_id = test_input.after_idle(highlight_clt_tags)

        def on_test_input_modified(_event=None) -> None:
            if test_input.edit_modified():
                test_input.edit_modified(False)
                schedule_clt_highlight()

        test_input.bind("<<Modified>>", on_test_input_modified)
        test_input.edit_modified(False)

        test_bottom = tk.Frame(tester, bg=BG)
        test_bottom.pack(fill="x", padx=8, pady=(0, 6))

        test_status = tk.StringVar(value="")
        tk.Label(test_bottom, textvariable=test_status, bg=BG, fg=WARN, anchor="w", font=FONT).pack(side="left", fill="x", expand=True)

        test_btnrow = tk.Frame(test_bottom, bg=BG)
        test_btnrow.pack(side="right")

        def apply_test_line_wrap() -> None:
            raw = test_input.get("1.0", "end-1c")
            fixed, did_change = wrap_msgstr(raw, soft=soft.get(), hard=hard.get(), max_cuts=cuts.get())
            line_lengths = [visible_len(line) for line in fixed.splitlines()] or [0]
            test_input.delete("1.0", "end")
            test_input.insert("1.0", fixed)
            highlight_clt_tags()
            changed_text = "changed" if did_change else "unchanged"
            test_status.set(f"Applied: {changed_text}. Lines: {line_lengths}")

        def clear_test_text() -> None:
            test_input.delete("1.0", "end")
            highlight_clt_tags()
            test_status.set("")

        self._button(test_btnrow, "Apply Wrap", apply_test_line_wrap).pack(side="left", padx=(0, 5))
        self._button(test_btnrow, "Clear", clear_test_text).pack(side="left")

        log = self._log(frame)
        btnrow = tk.Frame(frame, bg=BG)
        btnrow.pack(fill="x", padx=12, pady=(0, 12))

        def run(logwrite):
            self._check_stop()
            results = wrap_path(path_var.get(), soft=soft.get(), hard=hard.get(), max_cuts=cuts.get(), dry_run=dry.get())
            for path, n in results.items():
                self._check_stop()
                if n:
                    logwrite(f"{path}: {n}", "good")
            logwrite(f"Total wrapped: {sum(results.values())}")
            if dry.get():
                logwrite("Dry run only.", "warn")

        btn = self._button(btnrow, "Run Line Wrap", lambda: self._run_threaded(btn, log, run))
        btn.pack(side="right")

    def _build_search_tab(self, nb: ttk.Notebook) -> None:
        frame = self._frame(nb, "Search")
        path_var = self._path_row(frame, "Folder/File", "last_path")
        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x", padx=12, pady=5)
        phrase = tk.StringVar()
        tk.Label(row, text="Phrase", bg=BG, fg=TEXT, width=14, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=phrase, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", font=FONT).pack(side="left", fill="x", expand=True, padx=8, ipady=5)
        log = self._log(frame)
        btnrow = tk.Frame(frame, bg=BG)
        btnrow.pack(fill="x", padx=12, pady=(0, 12))

        def run(logwrite):
            self._check_stop()
            results = search_path(path_var.get(), phrase.get())
            for r in results[:500]:
                self._check_stop()
                logwrite(f"{r.file} | {r.msgctxt}")
                logwrite(f"msgid : {r.msgid}", "warn" if r.hit_msgid else None)
                logwrite(f"msgstr: {r.msgstr}", "good" if r.hit_msgstr else None)
            logwrite(f"Results: {len(results)}")

        btn = self._button(btnrow, "Search", lambda: self._run_threaded(btn, log, run))
        btn.pack(side="right")

    def _build_translate_tab(self, nb: ttk.Notebook) -> None:
        frame = self._frame(nb, "Gemini Web")
        path_var = self._path_row(frame, "Folder", "last_path")

        web1 = tk.Frame(frame, bg=BG)
        web1.pack(fill="x", padx=12, pady=(8, 4))
        cdp_var = tk.StringVar(value=str(self.config.get("gemini_web_cdp_url", "http://localhost:9222")))
        tk.Label(web1, text="Chrome CDP", bg=BG, fg=TEXT, font=("Segoe UI", 9, "bold"), width=14, anchor="w").pack(side="left")
        tk.Entry(web1, textvariable=cdp_var, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", font=FONT).pack(side="left", fill="x", expand=True, padx=8, ipady=5)

        web2 = tk.Frame(frame, bg=BG)
        web2.pack(fill="x", padx=12, pady=4)
        max_files = tk.IntVar(value=int(self.config.get("gemini_web_max_files", 59)))
        max_lines = tk.IntVar(value=int(self.config.get("gemini_web_max_lines", 600)))
        max_entries = tk.IntVar(value=int(self.config.get("gemini_web_max_entries", DEFAULT_MAX_ENTRIES_PER_BATCH)))
        wait_seconds = tk.DoubleVar(value=float(self.config.get("gemini_web_wait_seconds", 8.0)))
        timeout_seconds = tk.IntVar(value=int(self.config.get("gemini_web_timeout_seconds", 180)))
        retries = tk.IntVar(value=int(self.config.get("gemini_web_retries", DEFAULT_BATCH_RETRIES)))
        rename_dupes = tk.BooleanVar(value=True)
        backup_missing = tk.BooleanVar(value=True)
        rename_folders = tk.BooleanVar(value=True)
        allow_invalid = tk.BooleanVar(value=False)
        allow_invalid_state = {"value": False}

        def sync_allow_invalid_state() -> None:
            # Tk variables should stay on the UI thread. Keep a tiny shared
            # mirror so the running worker can read Allow invalid live.
            allow_invalid_state["value"] = bool(allow_invalid.get())

        sync_allow_invalid_state()

        for label, var, width in (("Max files", max_files, 5), ("Max lines", max_lines, 6), ("Max entries", max_entries, 5), ("Wait", wait_seconds, 5), ("Timeout", timeout_seconds, 5), ("Retries", retries, 5)):
            tk.Label(web2, text=label, bg=BG, fg=TEXT).pack(side="left")
            tk.Spinbox(web2, from_=0 if label == "Max files" else 1, to=9999, textvariable=var, width=width, bg=PANEL, fg=TEXT).pack(side="left", padx=(4, 10))

        web3 = tk.Frame(frame, bg=BG)
        web3.pack(fill="x", padx=12, pady=(0, 6))
        tk.Checkbutton(web3, text="Rename (1)", variable=rename_dupes, bg=BG, fg=TEXT, selectcolor=PANEL).pack(side="left")
        tk.Checkbutton(web3, text="Create Copy.po if missing", variable=backup_missing, bg=BG, fg=TEXT, selectcolor=PANEL).pack(side="left", padx=(12, 0))
        tk.Checkbutton(web3, text="Rename segment folders", variable=rename_folders, bg=BG, fg=TEXT, selectcolor=PANEL).pack(side="left", padx=(12, 0))
        tk.Checkbutton(web3, text="Allow invalid", variable=allow_invalid, command=sync_allow_invalid_state, bg=BG, fg=TEXT, selectcolor=PANEL).pack(side="left", padx=(12, 0))

        log = self._log(frame)
        btnrow = tk.Frame(frame, bg=BG)
        btnrow.pack(fill="x", padx=12, pady=(0, 12))

        def save_web_config():
            self.config["gemini_web_cdp_url"] = cdp_var.get().strip() or "http://localhost:9222"
            self.config["gemini_web_max_files"] = int(max_files.get())
            self.config["gemini_web_max_lines"] = int(max_lines.get())
            self.config["gemini_web_max_entries"] = int(max_entries.get())
            self.config["gemini_web_wait_seconds"] = float(wait_seconds.get())
            self.config["gemini_web_timeout_seconds"] = int(timeout_seconds.get())
            self.config["gemini_web_retries"] = int(retries.get())
            save_config(self.config)

        def run_web(logwrite):
            save_web_config()
            limit = int(max_files.get())
            result = run_gemini_web_path(
                path_var.get(),
                max_files=None if limit <= 0 else limit,
                max_lines_per_batch=int(max_lines.get()),
                max_entries_per_batch=int(max_entries.get()),
                wait_between_batches=float(wait_seconds.get()),
                cdp_url=cdp_var.get().strip() or "http://localhost:9222",
                allow_invalid=lambda: bool(allow_invalid_state["value"]),
                rename_duplicates=rename_dupes.get(),
                create_missing_backups=backup_missing.get(),
                rename_folders=rename_folders.get(),
                response_timeout_seconds=int(timeout_seconds.get()),
                retry_count=int(retries.get()),
                log=lambda msg: logwrite(msg),
                stop_requested=self._stop_event.is_set,
            )
            if not result.files:
                logwrite("No untranslated PO files found.", "warn")
                return
            for item in result.files:
                self._check_stop()
                tag = "good" if not item.errors else "warn"
                logwrite(f"{item.file} | missing={item.missing_before} | applied={item.translated} | errors={len(item.errors)}", tag)
                if item.debug_log:
                    logwrite(f"  debug: {item.debug_log}")
                if item.backup_created:
                    logwrite("  backup: created missing Copy.po only; existing Copy.po was not touched", "good")
                if item.folder_renamed_to:
                    logwrite(f"  folder: {item.folder_renamed_from} -> {item.folder_renamed_to}", "good")
                elif item.folder_rename_skipped_reason:
                    logwrite(f"  folder: skipped ({item.folder_rename_skipped_reason})", "warn")
                for e in item.errors[:30]:
                    logwrite(f"  {e.uid} | {e.msgctxt} | {e.reason}", "bad")
                if len(item.errors) > 30:
                    logwrite(f"  ... {len(item.errors)-30} more errors", "warn")
            logwrite(f"Total translated: {result.total_translated}", "good")
            if result.total_errors:
                logwrite(f"Total errors: {result.total_errors}", "bad")

        def open_chrome(logwrite):
            save_web_config()
            cmd = open_chrome_debug(
                cdp_url=cdp_var.get().strip() or "http://localhost:9222",
                user_data_dir=DEFAULT_CHROME_USER_DATA_DIR,
            )
            logwrite("Chrome opened with remote debugging.", "good")
            logwrite("Login to Gemini in that Chrome window, then click Run Gemini Web.")
            logwrite("Command: " + " ".join(str(x) for x in cmd))

        btn_open = self._button(btnrow, "Open Chrome", lambda: self._run_threaded(btn_open, log, open_chrome))
        btn_open.pack(side="right", padx=(8, 0))
        btn0 = self._button(btnrow, "Run Gemini Web", lambda: self._run_threaded(btn0, log, run_web))
        btn0.pack(side="right")

    def _build_backup_tab(self, nb: ttk.Notebook) -> None:
        frame = self._frame(nb, "Backup / Sync")
        path_var = self._path_row(frame, "Backup path", "last_path")
        source_var = self._path_row(frame, "Sync source", "sync_source")
        target_var = self._path_row(frame, "Sync target", "sync_target")

        restore_box_frame = tk.Frame(frame, bg=BG)
        restore_box_frame.pack(fill="x", padx=12, pady=5)
        tk.Label(restore_box_frame, text="Restore paths", bg=BG, fg=TEXT, font=("Segoe UI", 9, "bold"), width=14, anchor="w").pack(side="left", anchor="n")
        restore_list = tk.Listbox(restore_box_frame, bg=PANEL, fg=TEXT, selectbackground=ACCENT, selectforeground="#111", font=MONO, height=5, selectmode="extended")
        restore_list.pack(side="left", fill="x", expand=True, padx=8)
        restore_btns = tk.Frame(restore_box_frame, bg=BG)
        restore_btns.pack(side="left", fill="y")

        restore_paths: list[str] = list(self.config.get("restore_copy_paths", []))

        def refresh_restore_list() -> None:
            restore_list.delete(0, "end")
            for item in restore_paths:
                restore_list.insert("end", item)

        def save_restore_paths() -> None:
            self.config["restore_copy_paths"] = restore_paths
            save_config(self.config)

        def add_restore_paths(paths: list[str] | tuple[str, ...]) -> int:
            added = 0
            for raw in paths:
                raw = str(raw).strip()
                if not raw:
                    continue
                try:
                    path = Path(raw).expanduser()
                except Exception:
                    continue
                if not path.exists():
                    continue
                if path.is_file() and path.suffix.lower() != ".po":
                    continue
                try:
                    path_text = str(path.resolve())
                except Exception:
                    path_text = str(path)
                if path_text not in restore_paths:
                    restore_paths.append(path_text)
                    added += 1
            if added:
                refresh_restore_list()
                save_restore_paths()
            return added

        def add_restore_folder() -> None:
            folders = self._ask_restore_folders("Select one or more folders containing Copy.po backups")
            add_restore_paths(folders)

        def remove_restore_folder() -> None:
            selected = list(restore_list.curselection())
            for idx in reversed(selected):
                restore_paths.pop(idx)
            refresh_restore_list()
            save_restore_paths()

        def clear_restore_folders() -> None:
            restore_paths.clear()
            refresh_restore_list()
            save_restore_paths()

        tk.Button(restore_btns, text="Add Folders", command=add_restore_folder, bg=ACCENT, fg="#111", relief="flat", padx=10).pack(fill="x", pady=(0, 4))
        tk.Button(restore_btns, text="Remove", command=remove_restore_folder, bg=ACCENT, fg="#111", relief="flat", padx=10).pack(fill="x", pady=(0, 4))
        tk.Button(restore_btns, text="Clear", command=clear_restore_folders, bg=ACCENT, fg="#111", relief="flat", padx=10).pack(fill="x")
        restore_list.bind("<Delete>", lambda _event: remove_restore_folder())
        refresh_restore_list()

        drop_enabled = self._enable_path_drop(restore_list, add_restore_paths)
        drop_enabled = self._enable_path_drop(restore_box_frame, add_restore_paths) or drop_enabled
        drop_text = "Drag folders or - Copy.po files into the restore list. " if drop_enabled else ""
        hint = tk.Label(
            frame,
            text=drop_text + "Restore scans selected folders recursively. It overwrites working .po from matching - Copy.po. Copy.po files are never changed.",
            bg=BG,
            fg=WARN,
            anchor="w",
        )
        hint.pack(fill="x", padx=12, pady=(0, 5))

        log = self._log(frame)
        btnrow = tk.Frame(frame, bg=BG)
        btnrow.pack(fill="x", padx=12, pady=(0, 12))

        def backup(logwrite):
            self._check_stop()
            n = make_backups(path_var.get(), overwrite=False)
            self._check_stop()
            logwrite(f"Missing Copy.po backups written: {n}", "good")
            logwrite("Existing Copy.po files were not touched.", "warn")

        def sync(logwrite):
            self._check_stop()
            n = sync_by_filename(source_var.get(), target_var.get())
            self._check_stop()
            logwrite(f"Files synced: {n}", "good")

        def restore_from_copy(logwrite):
            paths = list(restore_paths)
            if not paths:
                logwrite("Add or drag one or more restore folders / - Copy.po files first.", "warn")
                return
            self._check_stop()
            results = restore_working_po_from_copies(paths)
            ok = 0
            failed = 0
            for r in results:
                self._check_stop()
                if r.ok:
                    ok += 1
                    logwrite(f"OK {r.action}: {r.copy_po} -> {r.work_po}", "good")
                else:
                    failed += 1
                    logwrite(f"ERR {r.action}: {r.copy_po} -> {r.work_po} | {r.error}", "bad")
            logwrite(f"Restored working PO files: {ok}", "good")
            if failed:
                logwrite(f"Failed/skipped: {failed}", "bad")
            if not results:
                logwrite("No Copy.po files found in selected folders.", "warn")

        b1 = self._button(btnrow, "Create Missing Copy.po Backups", lambda: self._run_threaded(b1, log, backup))
        b1.pack(side="right")
        b2 = self._button(btnrow, "Sync by Filename", lambda: self._run_threaded(b2, log, sync))
        b2.pack(side="right", padx=8)
        def start_restore_from_copy() -> None:
            if not messagebox.askyesno(
                "Restore working PO",
                "This will overwrite working .po files with clean content copied from matching - Copy.po files.\n\nCopy.po files will NOT be modified. Continue?",
            ):
                self._write_log(log, "Restore cancelled before start.", "warn")
                return
            self._run_threaded(b3, log, restore_from_copy)

        b3 = self._button(btnrow, "Restore Working PO from Copy.po", start_restore_from_copy)
        b3.pack(side="right", padx=8)


def main() -> None:
    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    ToolkitGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
