"""
Danganronpa Translation Hub
===========================
Supports two TXT formats used in Danganronpa translation work:

  PLAIN TEXT  (e.g. e00_001_000.txt)
      UTF-8, no wrapper opcodes, dialogue blocks separated by standalone
      <CLT> lines.  This is the human-editable translation format.

  LIN SCRIPT  (e.g. output.txt from lin_compiler -d)
      UTF-16 LE, full script with opcodes.  Dialogue text lives inside
      Text("...") calls.  Newlines inside Text() are literal \\n (two
      chars), not real newlines.  Strings may start with the UTF-8 BOM
      character \\ufeff.

Workflow overview:
  1.  lin_compiler -d original.lin  →  script.txt  (LIN SCRIPT format)
  2.  Extract Text() blocks to a .po for translation
  3.  Translate msgstrs
  4.  Patch plain translated TXT (or .po) back into the LIN script
  5.  lin_compiler patched_script.txt  →  patched.lin
  6.  Repack with wad_archiver / pak_archiver
"""

import tkinter as tk
from tkinter import ttk, filedialog
import subprocess
import os
import re
import polib


# ── ENCODING HELPERS ────────────────────────────────────────────────────────

def detect_open(path, mode='r'):
    """
    Open a text file, probing common encodings used by Danganronpa assets.
    Returns (file_object, encoding_name).
    Priority: utf-8-sig → utf-16 → latin-1.
    """
    for enc in ('utf-8-sig', 'utf-16', 'latin-1'):
        try:
            f = open(path, mode, encoding=enc)
            f.read()
            f.seek(0)
            return f, enc
        except (UnicodeDecodeError, UnicodeError):
            try:
                f.close()
            except Exception:
                pass
    f = open(path, mode, encoding='latin-1')
    return f, 'latin-1'


# ── LIN SCRIPT HELPERS ──────────────────────────────────────────────────────

# Matches Text("...") — content may contain \\ escapes
_TEXT_RE = re.compile(r'(Text\(")((?>(?:[^"\\]|\\.)*)?)("\))', re.DOTALL)
# Fallback without atomic group for Python < 3.11
_TEXT_RE_FALLBACK = re.compile(r'(Text\(")((?:[^"\\]|\\.)*?)("\))', re.DOTALL)


def _text_re():
    """Return the best available Text() regex."""
    try:
        re.compile(r'(?>a)')
        return _TEXT_RE
    except re.error:
        return _TEXT_RE_FALLBACK


def is_lin_script(content: str) -> bool:
    """Return True if the string looks like a LIN decompiled script."""
    return bool(re.search(r'Text\("', content[:4096]))


def lin_blocks(content: str) -> list:
    """Return list of re.Match objects for all Text("...") blocks."""
    return list(_text_re().finditer(content))


def lin_content_to_plain(raw: str) -> str:
    """
    Convert the raw string inside Text("...") to a human-readable string.
    - Strips leading \\ufeff BOM.
    - Converts literal \\n  →  real newline.
    - Unescapes \\' and \\".
    Trailing \\n<CLT> is preserved (it's meaningful).
    """
    s = raw.lstrip('\ufeff')
    s = s.replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')
    s = s.replace('\\n', '\n')
    return s


def plain_to_lin_content(plain: str) -> str:
    """
    Encode a plain-text segment back into LIN Text() format:
    - Escapes \\  →  \\\\,  "  →  \\",  '  →  \\'.
    - Replaces real newlines with literal \\n.
    - Strips trailing newlines, then appends \\n<CLT>.
    - Prepends \\ufeff BOM.
    """
    s = plain.rstrip('\n')
    s = s.replace('\\', '\\\\')
    s = s.replace('"', '\\"')
    s = s.replace("'", "\\'")
    s = s.replace('\n', '\\n')
    return '\ufeff' + s + '\\n<CLT>'


# ── PLAIN TEXT SEGMENT HELPERS ──────────────────────────────────────────────

def parse_plain_segments(content: str) -> list:
    """
    Split a plain-text translation file into dialogue segments.
    Each segment corresponds to one Text() slot in the LIN script.

    Rules:
      • A standalone <CLT> (on its own line, i.e. preceded AND followed
        by \\n or file boundaries) terminates a segment.
      • Inline <CLT> tags (e.g. <CLT><CLT 3>…<CLT><CLT 4>) do NOT split.
    """
    content = content.replace('\r\n', '\n')

    # Add sentinel newlines to simplify boundary handling
    padded = '\n' + content + '\n'

    # Split on  \n<CLT>\n  — that is, a <CLT> that has a real newline on
    # both sides, which means it is on its own line.
    parts = re.split(r'\n<CLT>\n', padded)

    segments = []
    for p in parts:
        s = p.strip('\n')
        if s.strip():
            segments.append(s)

    return segments


# ════════════════════════════════════════════════════════════════════════════
#  MAIN TOOL CLASS
# ════════════════════════════════════════════════════════════════════════════

class DanganronpaDarkTool:
    BG      = "#1e1e1e"
    FG      = "#e0e0e0"
    ACCENT  = "#ff0090"
    BLUE    = "#0f0094"
    BTN     = "#2e2e2e"
    LOG_BG  = "#151515"
    GREEN   = "#50fa7b"
    YELLOW  = "#f1fa8c"
    RED     = "#ff5555"

    def __init__(self, root):
        self.root = root
        self.root.title("Danganronpa Translation Hub — DARK MODE")
        self.root.geometry("780x800")
        self.root.configure(bg=self.BG)
        self.root.resizable(True, True)

        # ── Tool paths — update these ──────────────────────────────────
        self.tools_path   = r"D:\Danganronpa1Viet\danganronpa-tools"
        self.lin_compiler = os.path.join(self.tools_path, "lin_compiler.exe")
        self.wad_archiver = os.path.join(self.tools_path, "wad_archiver.exe")
        self.pak_archiver = os.path.join(self.tools_path, "pak_archiver.exe")

        self._build_ui()

    # ════════════════════════════════════════════════════
    #  UI
    # ════════════════════════════════════════════════════

    def _build_ui(self):
        tk.Label(self.root, text="DANGANRONPA  TRANSLATION  HUB",
                 font=('Consolas', 17, 'bold'), bg=self.BG, fg=self.ACCENT
                 ).pack(pady=10)

        style = ttk.Style()
        style.theme_use('default')
        style.configure('Dark.TNotebook', background=self.BG, borderwidth=0)
        style.configure('Dark.TNotebook.Tab', background=self.BTN,
                        foreground=self.FG, font=('Consolas', 10, 'bold'),
                        padding=[12, 5])
        style.map('Dark.TNotebook.Tab',
                  background=[('selected', self.ACCENT)],
                  foreground=[('selected', '#000')])

        nb = ttk.Notebook(self.root, style='Dark.TNotebook')
        nb.pack(padx=16, fill='both', expand=True)

        frames = [tk.Frame(nb, bg=self.BG) for _ in range(4)]
        for label, frame in zip(
            ["  CONVERT  ", "   PATCH   ", "   BUILD   ", "    PAK    "],
            frames
        ):
            nb.add(frame, text=label)

        self._tab_convert(frames[0])
        self._tab_patch(frames[1])
        self._tab_build(frames[2])
        self._tab_pak(frames[3])

        # Log
        tk.Label(self.root, text="── PROCESS LOG ──",
                 font=('Consolas', 9), bg=self.BG, fg=self.FG).pack(pady=(8, 0))
        self.log = tk.Text(self.root, height=9, state='disabled',
                           bg=self.LOG_BG, fg=self.GREEN,
                           font=('Consolas', 9), relief='flat', bd=0)
        self.log.pack(pady=3, padx=16, fill='both')
        self.log.tag_config('ok',   foreground=self.GREEN)
        self.log.tag_config('warn', foreground=self.YELLOW)
        self.log.tag_config('err',  foreground=self.RED)
        tk.Button(self.root, text="CLEAR LOG", command=self._clear_log,
                  bg=self.BTN, fg=self.FG, font=('Consolas', 8),
                  relief='flat', pady=3, cursor='hand2').pack(pady=(0, 6))

    # ── helpers ──────────────────────────────────────────────────────────────

    def _section(self, parent, title):
        f = tk.LabelFrame(parent, text=f"  {title}  ", padx=12, pady=8,
                          bg=self.BG, fg=self.YELLOW,
                          font=('Consolas', 9, 'bold'), relief='groove', bd=1)
        f.pack(padx=14, pady=6, fill='x')
        return f

    def _note(self, parent, text):
        tk.Label(parent, text=text, bg=self.BG, fg="#777777",
                 font=('Consolas', 8), justify='left').pack(anchor='w', pady=(0, 3))

    def _btn(self, parent, text, cmd, color=None):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=color or self.BTN, fg=self.FG,
                      font=('Consolas', 10, 'bold'), relief='flat',
                      pady=6, activebackground=self.ACCENT,
                      activeforeground='#000', cursor='hand2')
        b.pack(fill='x', pady=2)
        return b

    # ── tabs ─────────────────────────────────────────────────────────────────

    def _tab_convert(self, p):
        s = self._section(p, "SINGLE FILE")
        self._note(s, "Auto-detects LIN script vs plain text. PO->TXT needs only the .po.")
        self._btn(s, "TXT  ->  PO    (plain text OR LIN script)", self.single_txt_to_po)
        self._btn(s, "PO   ->  TXT   (rebuild plain text from .po)", self.single_po_to_txt)

        b = self._section(p, "BATCH  (folders)")
        self._btn(b, "TXT folder  ->  PO files  (auto-detects format)", self.batch_txt_to_po)
        self._btn(b, "PO folder   ->  TXT files", self.batch_po_to_txt)

    def _tab_patch(self, p):
        lin = self._section(p, "PATCH LIN SCRIPT  (main workflow)")
        self._note(lin,
            "Inject a translated plain TXT into the original LIN script.\n"
            "  Input A: translated plain .txt  (e.g. e00_001_000.txt)\n"
            "  Input B: original LIN script    (e.g. output.txt from lin_compiler -d)\n"
            "  Output:  patched LIN script ready to recompile.")
        self._btn(lin, "PATCH  plain TXT + LIN script  ->  patched LIN (single)", self.single_patch_plain_into_lin)
        self._btn(lin, "PATCH  plain TXT folder + LIN folder  ->  output folder  (batch)", self.batch_patch_plain_into_lin)

        s = self._section(p, "PATCH PLAIN TXT  (inject .po into existing .txt)")
        self._note(s, "Matches by exact line content — both files must use same format.")
        self._btn(s, "PATCH TXT  --  inject .po translations into an existing plain .txt", self.single_patch_po_into_txt)
        self._btn(s, "PATCH PO   --  fill empty msgstrs from another .po", self.single_patch_po_to_po)
        self._btn(s, "BATCH PATCH TXT  --  PO folder + plain TXT folder -> output", self.batch_patch_po_into_txt)

    def _tab_build(self, p):
        s = self._section(p, "LIN COMPILE  (lin_compiler)")
        self._note(s, "Compile TXT->LIN or decompile LIN->TXT.  Tick -dr2 for DR2.")
        row = tk.Frame(s, bg=self.BG)
        row.pack(fill='x', pady=(0, 3))
        self.dr2_var = tk.BooleanVar()
        tk.Checkbutton(row, text="Danganronpa 2 mode  (-dr2)",
                       variable=self.dr2_var, bg=self.BG, fg=self.FG,
                       selectcolor=self.BTN, activebackground=self.BG,
                       font=('Consolas', 9)).pack(side='left')
        self._btn(s, "COMPILE   TXT -> LIN  (single)", self.single_txt_compile)
        self._btn(s, "COMPILE   TXT -> LIN  (batch)",  self.batch_txt_compile)
        self._btn(s, "DECOMPILE LIN -> TXT  (single)", self.single_lin_decompile)
        self._btn(s, "DECOMPILE LIN -> TXT  (batch)",  self.batch_lin_decompile)

        w = self._section(p, "WAD ARCHIVE  (wad_archiver)")
        self._note(w, "Repack supports multiple source folders — cancel the folder picker to finish.")
        self._btn(w, "EXTRACT WAD -> folder",    self.extract_wad)
        self._btn(w, "REPACK folder(s) -> WAD",  self.repack_wad, color=self.BLUE)

    def _tab_pak(self, p):
        s = self._section(p, "PAK ARCHIVE  (pak_archiver)")
        self._note(s, "PAK entries use hex names (00, 1A, …).")
        self._btn(s, "EXTRACT PAK -> folder", self.pak_extract)
        self._btn(s, "CREATE  folder -> PAK", self.pak_create)
        r = self._section(p, "PAK REPLACE  (single entry patch)")
        self._note(r, "Replace one text entry by hex ID.")
        self._build_pak_replace(r)

    def _build_pak_replace(self, parent):
        for label_text, var_name, width, do_browse in [
            ("PAK file:",       "pak_replace_path", None, True),
            ("Entry ID (hex):", "pak_entry_id",     8,    False),
            ("New text:",       "pak_new_text",     None, False),
        ]:
            row = tk.Frame(parent, bg=self.BG)
            row.pack(fill='x', pady=2)
            tk.Label(row, text=label_text, bg=self.BG, fg=self.FG,
                     font=('Consolas', 9), width=14, anchor='w').pack(side='left')
            var = tk.StringVar()
            setattr(self, var_name, var)
            kw = dict(bg=self.BTN, fg=self.FG, font=('Consolas', 9), relief='flat')
            if width:
                kw['width'] = width
            tk.Entry(row, textvariable=var, **kw).pack(
                side='left', fill='x', expand=(width is None), padx=(4, 0))
            if do_browse:
                tk.Button(row, text="Browse", command=self._browse_pak_file,
                          bg=self.BTN, fg=self.FG, font=('Consolas', 8),
                          relief='flat', cursor='hand2').pack(side='left', padx=(4, 0))
        self._btn(parent, "REPLACE ENTRY IN PAK", self.pak_replace)

    def _browse_pak_file(self):
        p = filedialog.askopenfilename(
            title="Pick .PAK file",
            filetypes=[("PAK files", "*.pak"), ("All files", "*.*")])
        if p:
            self.pak_replace_path.set(p)

    # ════════════════════════════════════════════════════
    #  LOGGING
    # ════════════════════════════════════════════════════

    def _log(self, msg, tag=''):
        self.log.config(state='normal')
        self.log.insert(tk.END, f"[LOG] {msg}\n", tag)
        self.log.config(state='disabled')
        self.log.see(tk.END)
        self.root.update_idletasks()

    def log_ok(self,   m): self._log(f"OK   {m}", 'ok')
    def log_warn(self, m): self._log(f"WARN {m}", 'warn')
    def log_err(self,  m): self._log(f"ERR  {m}", 'err')
    def log_sep(self,  m): self._log(f"---- {m} ----")

    def _clear_log(self):
        self.log.config(state='normal')
        self.log.delete('1.0', tk.END)
        self.log.config(state='disabled')

    def _run(self, args):
        return subprocess.run(args, capture_output=True, text=True)

    # ════════════════════════════════════════════════════
    #  CORE: TXT -> PO
    #  Handles BOTH plain text files AND LIN script files.
    # ════════════════════════════════════════════════════

    def _txt_to_po(self, txt_path, po_path):
        f, enc = detect_open(txt_path)
        content = f.read()
        f.close()

        po = polib.POFile()
        po.metadata = {
            'Content-Type':              'text/plain; charset=utf-8',
            'Content-Transfer-Encoding': '8bit',
            'Source-File':               os.path.basename(txt_path),
            'Source-Encoding':           enc,
        }

        base = os.path.basename(txt_path)

        if is_lin_script(content):
            # ── LIN SCRIPT MODE ──────────────────────────────────────
            # Extract each Text("...") block as one PO entry.
            # Occurrence = (filename, block_index_as_string)  so we can
            # reconstruct the LIN file later.
            po.metadata['Source-Format'] = 'lin-script'
            seen: dict[str, polib.POEntry] = {}
            for idx, m in enumerate(lin_blocks(content)):
                msgid = lin_content_to_plain(m.group(2))
                occ   = (base, str(idx))
                if msgid in seen:
                    seen[msgid].occurrences.append(occ)
                else:
                    entry = polib.POEntry(
                        msgid=msgid, msgstr='', occurrences=[occ])
                    po.append(entry)
                    seen[msgid] = entry
            total = len(lin_blocks(content))
        else:
            # ── PLAIN TEXT MODE ──────────────────────────────────────
            # Store every line with its 1-based line number.
            po.metadata['Source-Format'] = 'plain-text'
            lines = content.splitlines(keepends=True)
            seen: dict[str, polib.POEntry] = {}
            for i, raw in enumerate(lines, 1):
                msgid = raw.rstrip('\r\n')
                occ   = (base, str(i))
                if msgid in seen:
                    seen[msgid].occurrences.append(occ)
                else:
                    entry = polib.POEntry(
                        msgid=msgid, msgstr='',
                        comment='BLANK_LINE' if msgid == '' else '',
                        occurrences=[occ])
                    po.append(entry)
                    seen[msgid] = entry
            total = len(lines)

        po.save(po_path)
        return total, enc

    # ════════════════════════════════════════════════════
    #  CORE: PO -> PLAIN TXT  (no original needed)
    # ════════════════════════════════════════════════════

    def _po_to_txt(self, po_path, out_path):
        po = polib.pofile(po_path)
        fmt = po.metadata.get('Source-Format', 'plain-text')

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        if fmt == 'lin-script':
            # We can't reconstruct a full LIN script from the PO alone —
            # we only have the dialogue strings.  Output a plain text list
            # of translated strings separated by blank lines.
            lines   = []
            translated = 0
            for entry in po:
                text = entry.msgstr.strip() or entry.msgid
                if entry.msgstr.strip():
                    translated += 1
                lines.append(text)
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write('\n\n'.join(lines) + '\n')
            return translated, len(lines)

        # ── PLAIN TEXT MODE ──────────────────────────────────────────
        # Attempt occurrence-based line reconstruction first.
        line_map: dict[int, str] = {}
        translated = 0

        for entry in po:
            is_blank = (entry.comment.strip() == 'BLANK_LINE')
            if is_blank:
                text = ''
            elif entry.msgstr.strip():
                text = entry.msgstr
                translated += 1
            else:
                text = entry.msgid

            for (_f, ln_str) in entry.occurrences:
                try:
                    line_map[int(ln_str)] = text
                except ValueError:
                    pass

        if line_map:
            max_line = max(line_map.keys())
            lines    = [line_map.get(n, '') for n in range(1, max_line + 1)]
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
            return translated, max_line

        # Fallback: no occurrences — write entries in PO order.
        lines = []
        translated = 0
        for entry in po:
            if entry.msgstr.strip():
                lines.append(entry.msgstr)
                translated += 1
            else:
                lines.append(entry.msgid)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        return translated, len(lines)

    # ════════════════════════════════════════════════════
    #  CORE: PATCH PLAIN TXT -> LIN SCRIPT
    #  The key workflow: translated plain txt + original LIN → patched LIN.
    # ════════════════════════════════════════════════════

    def _patch_plain_into_lin(self, plain_path, lin_path, out_path):
        """
        Replace Text() blocks in a LIN script with segments from a plain
        text translation file, matched positionally (block 0 ↔ segment 0).

        Returns (patched_count, total_plain_segs, total_lin_blocks).
        """
        f_lin, lin_enc = detect_open(lin_path)
        lin_content    = f_lin.read()
        f_lin.close()

        f_plain, _ = detect_open(plain_path)
        plain_text  = f_plain.read()
        f_plain.close()

        segments = parse_plain_segments(plain_text)
        blocks   = lin_blocks(lin_content)

        if not segments:
            raise ValueError("No dialogue segments found in plain text file.")
        if not blocks:
            raise ValueError("No Text() blocks found in the LIN script.")

        n_patch  = min(len(segments), len(blocks))
        # Build a list of (start, end, replacement_string) sorted in REVERSE
        # order so that replacing from the back doesn't shift earlier offsets.
        replacements = []
        for i in range(n_patch):
            m      = blocks[i]
            enc_s  = plain_to_lin_content(segments[i])
            # m.group(2) is the content between Text(" and ")
            replacements.append((m.start(2), m.end(2), enc_s))

        # Apply replacements from last to first
        result = lin_content
        for start, end, replacement in reversed(replacements):
            result = result[:start] + replacement + result[end:]

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, 'w', encoding=lin_enc) as f:
            f.write(result)

        return n_patch, len(segments), len(blocks)

    # ════════════════════════════════════════════════════
    #  CORE: PATCH PO -> PLAIN TXT  (inject by exact line match)
    # ════════════════════════════════════════════════════

    def _patch_po_into_txt(self, po_path, txt_path, out_path):
        po      = polib.pofile(po_path)
        lookup  = {e.msgid: e.msgstr for e in po if e.msgid and e.msgstr.strip()}
        f, enc  = detect_open(txt_path)
        lines   = f.readlines()
        f.close()
        new_lines = []
        replaced  = 0
        for raw in lines:
            key = raw.rstrip('\r\n')
            if key in lookup:
                ending = raw[len(key):]
                new_lines.append(lookup[key] + ending)
                replaced += 1
            else:
                new_lines.append(raw)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, 'w', encoding=enc) as f:
            f.writelines(new_lines)
        return replaced, len(lines)

    # ════════════════════════════════════════════════════
    #  CORE: PATCH PO -> PO  (fill empty msgstrs)
    # ════════════════════════════════════════════════════

    def _merge_po(self, src_path, tgt_path, out_path):
        src    = polib.pofile(src_path)
        tgt    = polib.pofile(tgt_path)
        lookup = {e.msgid: e.msgstr for e in src if e.msgid and e.msgstr.strip()}
        filled = 0
        for entry in tgt:
            if entry.msgid in lookup and not entry.msgstr.strip():
                entry.msgstr = lookup[entry.msgid]
                filled += 1
        tgt.save(out_path)
        return filled

    # ════════════════════════════════════════════════════
    #  SINGLE FILE — CONVERT
    # ════════════════════════════════════════════════════

    def single_txt_to_po(self):
        txt = filedialog.askopenfilename(
            title="Pick TXT file (plain text OR LIN script)",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not txt:
            return
        po = filedialog.asksaveasfilename(
            title="Save .PO as", defaultextension=".po",
            initialfile=os.path.splitext(os.path.basename(txt))[0] + ".po",
            filetypes=[("PO files", "*.po")])
        if not po:
            return
        try:
            n, enc = self._txt_to_po(txt, po)
            self.log_ok(f"TXT->PO: {os.path.basename(txt)}  [{enc}]  {n} entries")
        except Exception as e:
            self.log_err(f"TXT->PO failed: {e}")

    def single_po_to_txt(self):
        po = filedialog.askopenfilename(
            title="Pick .PO file",
            filetypes=[("PO files", "*.po"), ("All files", "*.*")])
        if not po:
            return
        out = filedialog.asksaveasfilename(
            title="Save output .TXT as", defaultextension=".txt",
            initialfile=os.path.splitext(os.path.basename(po))[0] + ".txt",
            filetypes=[("Text files", "*.txt")])
        if not out:
            return
        try:
            t, total = self._po_to_txt(po, out)
            self.log_ok(f"PO->TXT: {t}/{total} translated -> {os.path.basename(out)}")
            if t == 0:
                self.log_warn("0 translated — output uses original msgids as fallback.")
        except Exception as e:
            self.log_err(f"PO->TXT failed: {e}")

    # ════════════════════════════════════════════════════
    #  SINGLE FILE — PATCH
    # ════════════════════════════════════════════════════

    def single_patch_plain_into_lin(self):
        plain = filedialog.askopenfilename(
            title="Pick translated plain .TXT  (e.g. e00_001_000.txt)",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not plain:
            return
        lin = filedialog.askopenfilename(
            title="Pick original LIN script  (UTF-16 decompiled, e.g. output.txt)",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not lin:
            return
        out = filedialog.asksaveasfilename(
            title="Save patched LIN script as", defaultextension=".txt",
            initialfile=os.path.basename(lin),
            filetypes=[("Text files", "*.txt")])
        if not out:
            return
        try:
            patched, n_seg, n_block = self._patch_plain_into_lin(plain, lin, out)
            self.log_ok(f"LIN PATCH: {patched} blocks replaced  "
                        f"({n_seg} plain segs / {n_block} LIN blocks)")
            if n_seg != n_block:
                self.log_warn(
                    f"Count mismatch: {n_seg} translation segments vs "
                    f"{n_block} LIN Text() blocks.  "
                    f"First {patched} blocks were patched; the rest are unchanged.")
        except Exception as e:
            self.log_err(f"LIN Patch failed: {e}")

    def single_patch_po_into_txt(self):
        po = filedialog.askopenfilename(
            title="Pick .PO with translations",
            filetypes=[("PO files", "*.po"), ("All files", "*.*")])
        if not po:
            return
        txt = filedialog.askopenfilename(
            title="Pick existing plain .TXT to patch",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not txt:
            return
        out = filedialog.asksaveasfilename(
            title="Save patched .TXT as", defaultextension=".txt",
            initialfile=os.path.basename(txt),
            filetypes=[("Text files", "*.txt")])
        if not out:
            return
        try:
            rep, total = self._patch_po_into_txt(po, txt, out)
            self.log_ok(f"PATCH TXT: {rep}/{total} lines replaced -> {os.path.basename(out)}")
            if rep == 0:
                self.log_warn("0 lines matched — check that msgids match .txt lines exactly.")
        except Exception as e:
            self.log_err(f"Patch failed: {e}")

    def single_patch_po_to_po(self):
        src = filedialog.askopenfilename(
            title="Source .PO — WITH translations",
            filetypes=[("PO files", "*.po"), ("All files", "*.*")])
        if not src:
            return
        tgt = filedialog.askopenfilename(
            title="Target .PO — to fill (empty msgstrs)",
            filetypes=[("PO files", "*.po"), ("All files", "*.*")])
        if not tgt:
            return
        out = filedialog.asksaveasfilename(
            title="Save patched .PO as", defaultextension=".po",
            initialfile=os.path.basename(tgt),
            filetypes=[("PO files", "*.po")])
        if not out:
            return
        try:
            filled = self._merge_po(src, tgt, out)
            self.log_ok(f"PO Patch: {filled} entries filled -> {os.path.basename(out)}")
        except Exception as e:
            self.log_err(f"PO Merge failed: {e}")

    # ════════════════════════════════════════════════════
    #  BATCH — CONVERT
    # ════════════════════════════════════════════════════

    def batch_txt_to_po(self):
        txt_dir = filedialog.askdirectory(title="Folder with TXT files")
        if not txt_dir:
            return
        out_dir = filedialog.askdirectory(title="Folder to save PO files")
        if not out_dir:
            return
        files = [f for f in os.listdir(txt_dir) if f.lower().endswith('.txt')]
        if not files:
            self.log_warn("No .txt files found.")
            return
        done = 0
        for fname in files:
            try:
                n, enc = self._txt_to_po(
                    os.path.join(txt_dir, fname),
                    os.path.join(out_dir, os.path.splitext(fname)[0] + '.po'))
                self.log_ok(f"TXT->PO: {fname}  [{enc}]  {n} entries")
                done += 1
            except Exception as e:
                self.log_err(f"Failed {fname}: {e}")
        self.log_sep(f"Batch TXT->PO done — {done}/{len(files)}")

    def batch_po_to_txt(self):
        po_dir  = filedialog.askdirectory(title="Folder with .PO files")
        if not po_dir:
            return
        out_dir = filedialog.askdirectory(title="Folder to save .TXT files")
        if not out_dir:
            return
        files = [f for f in os.listdir(po_dir) if f.lower().endswith('.po')]
        if not files:
            self.log_warn("No .po files found.")
            return
        done = 0
        for fname in files:
            out = os.path.join(out_dir, os.path.splitext(fname)[0] + '.txt')
            try:
                t, total = self._po_to_txt(os.path.join(po_dir, fname), out)
                self.log_ok(f"PO->TXT: {fname}  {t}/{total} translated")
                done += 1
            except Exception as e:
                self.log_err(f"Failed {fname}: {e}")
        self.log_sep(f"Batch PO->TXT done — {done}/{len(files)}")

    # ════════════════════════════════════════════════════
    #  BATCH — PATCH
    # ════════════════════════════════════════════════════

    def batch_patch_plain_into_lin(self):
        """Match plain TXTs to LIN scripts by filename and patch each."""
        plain_dir = filedialog.askdirectory(
            title="Folder with translated plain TXT files")
        if not plain_dir:
            return
        lin_dir = filedialog.askdirectory(
            title="Folder with original LIN script TXT files")
        if not lin_dir:
            return
        out_dir = filedialog.askdirectory(
            title="Folder to save patched LIN scripts")
        if not out_dir:
            return
        files = [f for f in os.listdir(plain_dir) if f.lower().endswith('.txt')]
        if not files:
            self.log_warn("No .txt files found in plain TXT folder.")
            return
        done = 0
        for fname in files:
            lin_path = os.path.join(lin_dir, fname)
            if not os.path.exists(lin_path):
                self.log_warn(f"No matching LIN script for {fname} — skipped")
                continue
            out_path = os.path.join(out_dir, fname)
            try:
                patched, n_seg, n_block = self._patch_plain_into_lin(
                    os.path.join(plain_dir, fname), lin_path, out_path)
                msg = f"PATCH: {fname}  {patched} blocks"
                if n_seg != n_block:
                    msg += f"  (WARN: {n_seg} segs / {n_block} blocks)"
                self.log_ok(msg)
                done += 1
            except Exception as e:
                self.log_err(f"Failed {fname}: {e}")
        self.log_sep(f"Batch LIN Patch done — {done} files")

    def batch_patch_po_into_txt(self):
        po_dir  = filedialog.askdirectory(title="Folder with .PO files")
        if not po_dir:
            return
        txt_dir = filedialog.askdirectory(title="Folder with original plain TXT files")
        if not txt_dir:
            return
        out_dir = filedialog.askdirectory(title="Folder to save patched TXT files")
        if not out_dir:
            return
        files = [f for f in os.listdir(po_dir) if f.lower().endswith('.po')]
        if not files:
            self.log_warn("No .po files found.")
            return
        done = 0
        for fname in files:
            txt_name = os.path.splitext(fname)[0] + '.txt'
            txt_path = os.path.join(txt_dir, txt_name)
            if not os.path.exists(txt_path):
                self.log_warn(f"No matching TXT for {fname} — skipped")
                continue
            try:
                rep, total = self._patch_po_into_txt(
                    os.path.join(po_dir, fname), txt_path,
                    os.path.join(out_dir, txt_name))
                self.log_ok(f"PATCH: {fname}  {rep}/{total} lines")
                done += 1
            except Exception as e:
                self.log_err(f"Failed {fname}: {e}")
        self.log_sep(f"Batch PO->TXT patch done — {done} files")

    # ════════════════════════════════════════════════════
    #  BUILD — LIN
    # ════════════════════════════════════════════════════

    def _lin_args(self, extra=None):
        args = [self.lin_compiler] + (extra or [])
        if self.dr2_var.get():
            args.append('-dr2')
        return args

    def single_txt_compile(self):
        txt = filedialog.askopenfilename(
            title="Pick .TXT to compile",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not txt:
            return
        out = os.path.splitext(txt)[0] + '.lin'
        try:
            r = self._run(self._lin_args([txt, out]))
            if r.returncode == 0:
                self.log_ok(f"Compiled: {os.path.basename(txt)} -> .lin")
            else:
                self.log_err(f"Compiler error: {r.stderr.strip() or r.stdout.strip()}")
        except FileNotFoundError:
            self.log_err(f"lin_compiler not found: {self.lin_compiler}")

    def batch_txt_compile(self):
        d = filedialog.askdirectory(title="Folder with .TXT files to compile")
        if not d:
            return
        files = [f for f in os.listdir(d) if f.lower().endswith('.txt')]
        done  = 0
        for fname in files:
            ip = os.path.join(d, fname)
            op = os.path.splitext(ip)[0] + '.lin'
            try:
                r = self._run(self._lin_args([ip, op]))
                if r.returncode == 0:
                    self.log_ok(f"Compiled: {fname}")
                    done += 1
                else:
                    self.log_err(f"Error {fname}: {r.stderr.strip()}")
            except FileNotFoundError:
                self.log_err(f"lin_compiler not found: {self.lin_compiler}")
                return
        self.log_sep(f"Batch compile done — {done}/{len(files)}")

    def single_lin_decompile(self):
        lin = filedialog.askopenfilename(
            title="Pick .LIN to decompile",
            filetypes=[("LIN files", "*.lin"), ("All files", "*.*")])
        if not lin:
            return
        out = os.path.splitext(lin)[0] + '.txt'
        try:
            r = self._run(self._lin_args(['-d', lin, out]))
            if r.returncode == 0:
                self.log_ok(f"Decompiled: {os.path.basename(lin)} -> .txt")
            else:
                self.log_err(f"Decompiler error: {r.stderr.strip() or r.stdout.strip()}")
        except FileNotFoundError:
            self.log_err(f"lin_compiler not found: {self.lin_compiler}")

    def batch_lin_decompile(self):
        d = filedialog.askdirectory(title="Folder with .LIN files to decompile")
        if not d:
            return
        files = [f for f in os.listdir(d) if f.lower().endswith('.lin')]
        done  = 0
        for fname in files:
            ip = os.path.join(d, fname)
            op = os.path.splitext(ip)[0] + '.txt'
            try:
                r = self._run(self._lin_args(['-d', ip, op]))
                if r.returncode == 0:
                    self.log_ok(f"Decompiled: {fname}")
                    done += 1
                else:
                    self.log_err(f"Error {fname}: {r.stderr.strip()}")
            except FileNotFoundError:
                self.log_err(f"lin_compiler not found: {self.lin_compiler}")
                return
        self.log_sep(f"Batch decompile done — {done}/{len(files)}")

    # ════════════════════════════════════════════════════
    #  BUILD — WAD
    # ════════════════════════════════════════════════════

    def extract_wad(self):
        wad = filedialog.askopenfilename(
            title="Pick .WAD to extract",
            filetypes=[("WAD files", "*.wad"), ("All files", "*.*")])
        if not wad:
            return
        out_dir = filedialog.askdirectory(title="Extract into folder")
        if not out_dir:
            return
        try:
            r = self._run([self.wad_archiver, "extract", wad, out_dir + os.sep])
            if r.returncode == 0:
                self.log_ok(f"Extracted {os.path.basename(wad)} -> {out_dir}")
            else:
                self.log_err(f"WAD extract error: {r.stderr.strip() or r.stdout.strip()}")
        except FileNotFoundError:
            self.log_err(f"wad_archiver not found: {self.wad_archiver}")

    def repack_wad(self):
        dirs = []
        while True:
            d = filedialog.askdirectory(
                title=f"Source folder #{len(dirs)+1}  (Cancel when done)")
            if not d:
                break
            dirs.append(d)
        if not dirs:
            return
        wad_out = filedialog.asksaveasfilename(
            title="Save repacked .WAD as", defaultextension=".wad",
            filetypes=[("WAD files", "*.wad"), ("All files", "*.*")])
        if not wad_out:
            return
        self.log_sep("Packing WAD — please wait")
        try:
            r = self._run([self.wad_archiver, "create"] + dirs + [wad_out])
            if r.returncode == 0:
                self.log_ok("DESPAIR OVERCOME — WAD REPACKED!")
            else:
                self.log_err(f"WAD error: {r.stderr.strip() or r.stdout.strip()}")
        except FileNotFoundError:
            self.log_err(f"wad_archiver not found: {self.wad_archiver}")

    # ════════════════════════════════════════════════════
    #  PAK
    # ════════════════════════════════════════════════════

    def pak_extract(self):
        pak = filedialog.askopenfilename(
            title="Pick .PAK to extract",
            filetypes=[("PAK files", "*.pak"), ("All files", "*.*")])
        if not pak:
            return
        out_dir = filedialog.askdirectory(title="Extract into folder")
        if not out_dir:
            return
        try:
            r = self._run([self.pak_archiver, "extract", pak, out_dir + os.sep])
            if r.returncode == 0:
                self.log_ok(f"PAK extracted: {os.path.basename(pak)} -> {out_dir}")
            else:
                self.log_err(f"PAK extract error: {r.stderr.strip() or r.stdout.strip()}")
        except FileNotFoundError:
            self.log_err(f"pak_archiver not found: {self.pak_archiver}")

    def pak_create(self):
        src_dir = filedialog.askdirectory(title="Folder to pack into PAK")
        if not src_dir:
            return
        out = filedialog.asksaveasfilename(
            title="Save .PAK as", defaultextension=".pak",
            filetypes=[("PAK files", "*.pak"), ("All files", "*.*")])
        if not out:
            return
        try:
            r = self._run([self.pak_archiver, "create", src_dir + os.sep, out])
            if r.returncode == 0:
                self.log_ok(f"PAK created: {os.path.basename(out)}")
            else:
                self.log_err(f"PAK create error: {r.stderr.strip() or r.stdout.strip()}")
        except FileNotFoundError:
            self.log_err(f"pak_archiver not found: {self.pak_archiver}")

    def pak_replace(self):
        pak  = self.pak_replace_path.get().strip()
        eid  = self.pak_entry_id.get().strip()
        text = self.pak_new_text.get().strip()
        if not pak:
            self.log_err("PAK Replace: no PAK file selected.")
            return
        if not eid:
            self.log_err("PAK Replace: enter an entry ID (hex, e.g. 1A).")
            return
        if not text:
            self.log_err("PAK Replace: new text is empty.")
            return
        if not os.path.exists(pak):
            self.log_err(f"PAK Replace: file not found — {pak}")
            return
        try:
            r = self._run([self.pak_archiver, "replace", pak, eid, text])
            if r.returncode == 0:
                self.log_ok(f"PAK Replace: entry {eid} updated in {os.path.basename(pak)}")
            else:
                self.log_err(f"PAK replace error: {r.stderr.strip() or r.stdout.strip()}")
        except FileNotFoundError:
            self.log_err(f"pak_archiver not found: {self.pak_archiver}")


# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    root = tk.Tk()
    app  = DanganronpaDarkTool(root)
    root.mainloop()
