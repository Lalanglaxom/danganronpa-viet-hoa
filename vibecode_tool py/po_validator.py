import os
import re
import tkinter as tk
from tkinter import filedialog
from datetime import datetime


# ╔══════════════════════════════════════════════════════════════════════╗
# ║                                                                      ║
# ║   CUSTOM RULES — ADD YOUR OWN CHECKS HERE                           ║
# ║                                                                      ║
# ║   Each rule is a plain function decorated with @rule(...).          ║
# ║   It receives every entry pair and yields issue tuples.             ║
# ║                                                                      ║
# ║   @rule("rule_name", level="WARN")                                  ║
# ║   def my_rule(ctx, copy_entry, work_entry):                         ║
# ║       """Short description shown in the log header."""              ║
# ║       # ctx           — { "file": "e01/e00_001_000", ... }          ║
# ║       # copy_entry    — { "msgctxt", "msgid", "msgstr", "line" }    ║
# ║       # work_entry    — same, from the working (translated) file    ║
# ║       # yield a message string to report an issue, or yield nothing ║
# ║       if "badword" in work_entry["msgstr"]:                         ║
# ║           yield f'Entry "{work_entry["msgctxt"]}" contains badword' ║
# ║                                                                      ║
# ║   TIPS                                                               ║
# ║   • level can be "ERROR", "WARN", or "INFO"                         ║
# ║   • Set enabled=False to temporarily disable a rule                 ║
# ║   • copy_entry or work_entry may be None if the entry is missing    ║
# ║     from that side — always guard with:  if work_entry is None:     ║
# ║                                                                      ║
# ╚══════════════════════════════════════════════════════════════════════╝

CUSTOM_RULES = []   # auto-populated by @rule decorator — do not edit this line

def rule(name, level="WARN", enabled=True):
    """Decorator that registers a custom rule function."""
    def decorator(fn):
        if enabled:
            CUSTOM_RULES.append({"name": name, "level": level, "fn": fn, "doc": fn.__doc__ or ""})
        return fn
    return decorator


# ──────────────────────────────────────────────────────────────────────
#  EXAMPLE RULE 1 — Consistent character name translations
#  Edit the NAME_MAP dict to add your own name mappings.
#  key   = English name as it appears in msgid
#  value = what it should be translated to in msgstr
# ──────────────────────────────────────────────────────────────────────
NAME_MAP = {
    # "Sayaka":   "Sayaka",       # proper nouns usually stay the same
    # "Monokuma": "Monokuma",
    # "Makoto":   "Makoto",
    # "Hope":     "Hy vọng",      # example: enforce translated term
}

@rule("name_consistency", level="WARN", enabled=False)   # set enabled=True to activate
def check_name_consistency(ctx, copy_entry, work_entry):
    """Flags entries where a known name appears in msgid but its translation is missing in msgstr."""
    if work_entry is None or not work_entry["msgstr"].strip():
        return
    msgid  = work_entry["msgid"]
    msgstr = work_entry["msgstr"]
    for en_name, vn_name in NAME_MAP.items():
        if en_name in msgid and vn_name not in msgstr:
            yield (
                f'Entry "{work_entry["msgctxt"]}" (line {work_entry["line"]}): '
                f'"{en_name}" in source but "{vn_name}" not found in translation'
            )


# ──────────────────────────────────────────────────────────────────────
#  EXAMPLE RULE 2 — Forbidden words / phrases in translation
#  Add any strings you never want to appear in a msgstr.
# ──────────────────────────────────────────────────────────────────────
FORBIDDEN_IN_TRANSLATION = [
    # "TODO",
    # "FIXME",
    # "xxx",
]

@rule("forbidden_text", level="ERROR", enabled=False)   # set enabled=True to activate
def check_forbidden_text(ctx, copy_entry, work_entry):
    """Flags entries where the translation contains a forbidden string."""
    if work_entry is None or not work_entry["msgstr"].strip():
        return
    msgstr = work_entry["msgstr"]
    for word in FORBIDDEN_IN_TRANSLATION:
        if word.lower() in msgstr.lower():
            yield (
                f'Entry "{work_entry["msgctxt"]}" (line {work_entry["line"]}): '
                f'translation contains forbidden text "{word}"'
            )


# ──────────────────────────────────────────────────────────────────────
#  EXAMPLE RULE 3 — CLT tag integrity
#  Checks that every <CLT ...> tag in the source also appears in the
#  translation, so no game formatting codes are accidentally dropped.
# ──────────────────────────────────────────────────────────────────────
@rule("clt_tag_integrity", level="WARN", enabled=True)
def check_clt_tags(ctx, copy_entry, work_entry):
    """Checks that <CLT ...> tags in msgid are preserved in msgstr."""
    if work_entry is None or not work_entry["msgstr"].strip():
        return
    tags_in  = re.findall(r"<CLT[^>]*>", work_entry["msgid"])
    tags_out = re.findall(r"<CLT[^>]*>", work_entry["msgstr"])
    if sorted(tags_in) != sorted(tags_out):
        missing = [t for t in tags_in  if t not in tags_out]
        extra   = [t for t in tags_out if t not in tags_in]
        parts = []
        if missing: parts.append(f"missing: {', '.join(missing)}")
        if extra:   parts.append(f"extra: {', '.join(extra)}")
        yield (
            f'Entry "{work_entry["msgctxt"]}" (line {work_entry["line"]}): '
            f"CLT tag mismatch — " + "; ".join(parts)
        )


# ──────────────────────────────────────────────────────────────────────
#  EXAMPLE RULE 4 — Translation must not be identical to source
#  Catches copy-paste errors where msgstr was left as the English text.
# ──────────────────────────────────────────────────────────────────────
@rule("not_copy_of_source", level="INFO", enabled=True)
def check_not_copy_of_source(ctx, copy_entry, work_entry):
    """Flags entries where msgstr is identical to msgid (possible untranslated copy-paste)."""
    if work_entry is None:
        return
    src = work_entry["msgid"].strip()
    tgt = work_entry["msgstr"].strip()
    if src and tgt and src == tgt:
        yield (
            f'Entry "{work_entry["msgctxt"]}" (line {work_entry["line"]}): '
            f"translation is identical to source text"
        )


# ══════════════════════════════════════════════════════════════════════
#  END OF CUSTOM RULES — engine code below, edit with care
# ══════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────
#  PO PARSER
# ─────────────────────────────────────────────

def parse_po(filepath):
    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()

    header = {}
    entries = {}       # msgctxt → entry dict
    ordered_keys = []  # preserve order for line-number tracking

    # Parse header (the very first msgstr "" block)
    in_header = False
    header_done = False
    i = 0
    while i < len(lines) and not header_done:
        line = lines[i].rstrip("\n")
        if line.startswith('msgstr ""') and not header_done:
            in_header = True; i += 1; continue
        if in_header:
            m = re.match(r'^"(.+?):\s*(.*?)\\n"', line)
            if m:
                header[m.group(1)] = m.group(2); i += 1; continue
            else:
                header_done = True
        i += 1

    # Parse entries
    current = {}
    field   = None
    pending_comments = []

    def flush():
        if "msgctxt" in current:
            key = current["msgctxt"]
            entries[key] = {
                "msgctxt":  key,
                "msgid":    "".join(current.get("msgid",  [])),
                "msgstr":   "".join(current.get("msgstr", [])),
                "comments": current.get("comments", []),
                "line":     current.get("line", 0),
            }
            ordered_keys.append(key)

    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n")
        if line.startswith("#."):
            pending_comments.append(line[2:].strip())
        elif line.startswith("msgctxt "):
            flush()
            current = {"msgctxt": line[9:].strip('"'), "line": i + 1,
                       "comments": pending_comments[:]}
            pending_comments = []
            field = None
        elif line.startswith("msgid "):
            field = "msgid"
            raw = line[6:].strip()
            current[field] = [raw.strip('"')] if raw != '""' else []
        elif line.startswith("msgstr "):
            field = "msgstr"
            raw = line[7:].strip()
            current[field] = [raw.strip('"')] if raw != '""' else []
        elif line.startswith('"') and field:
            current[field].append(line.strip('"'))
        i += 1
    flush()

    return header, entries, ordered_keys


# ─────────────────────────────────────────────
#  BUILT-IN VALIDATORS
# ─────────────────────────────────────────────

REQUIRED_HEADER_KEYS = [
    "Project-Id-Version",
    "POT-Creation-Date",
    "MIME-Version",
    "Content-Type",
    "Content-Transfer-Encoding",
]

def run_builtin_checks(copy_header, copy_entries, work_header, work_entries):
    issues = []

    def issue(level, check, detail):
        issues.append({"level": level, "check": check, "detail": detail})

    copy_keys = set(copy_entries)
    work_keys = set(work_entries)

    # ── 1. Entry count ────────────────────────────────────────────────────
    if len(copy_entries) != len(work_entries):
        issue("ERROR", "entry_count",
              f"Entry count mismatch: Copy={len(copy_entries)}, Working={len(work_entries)}")

    # ── 2. Missing / extra entries (with renumber detection) ──────────────
    copy_msgid_map = {}
    for k, e in copy_entries.items():
        mid = e["msgid"].strip()
        if mid:
            copy_msgid_map.setdefault(mid, []).append(k)

    work_msgid_map = {}
    for k, e in work_entries.items():
        mid = e["msgid"].strip()
        if mid:
            work_msgid_map.setdefault(mid, []).append(k)

    explained = set()
    for key in sorted(copy_keys - work_keys):
        c_mid = copy_entries[key]["msgid"].strip()
        matches = [k for k in work_msgid_map.get(c_mid, []) if k != key]
        if matches:
            for wk in matches:
                issue("ERROR", "ctx_renumbered",
                      f'"{key}" renumbered in Working → found as "{wk}"\n'
                      f'    msgid: {repr(c_mid[:80])}')
                explained.add(wk)
        else:
            issue("ERROR", "missing_entry", f'"{key}" in Copy but MISSING in Working')

    for key in sorted(work_keys - copy_keys):
        if key in explained:
            continue
        w_mid = work_entries[key]["msgid"].strip()
        matches = [k for k in copy_msgid_map.get(w_mid, []) if k != key]
        if matches:
            issue("ERROR", "ctx_renumbered",
                  f'"{key}" has same source as Copy\'s "{matches[0]}" (renumbered?)\n'
                  f'    msgid: {repr(w_mid[:80])}')
        else:
            issue("WARN", "extra_entry", f'"{key}" in Working but NOT in Copy')

    # ── 3. English source text changed significantly ───────────────────────
    def normalize_msgid(s):
        """Strip noise: newlines, leading/trailing whitespace, repeated punctuation."""
        import re as _re
        s = s.replace("\\n", " ").replace("\n", " ")
        s = _re.sub(r'[\s]+', " ", s).strip()
        s = _re.sub(r'[.,?!\-]+$', "", s).strip()
        return s.lower()

    for key in sorted(copy_keys & work_keys):
        c_id = copy_entries[key]["msgid"]
        w_id = work_entries[key]["msgid"]
        if c_id == w_id:
            continue
        if normalize_msgid(c_id) == normalize_msgid(w_id):
            continue   # only whitespace/punctuation noise — ignore
        issue("WARN", "source_changed",
              f'"{key}" (line {work_entries[key]["line"]}): English source text changed\n'
              f'    Copy   : {repr(c_id[:120])}\n'
              f'    Working: {repr(w_id[:120])}')

    # ── 4. Untranslated entries ───────────────────────────────────────────
    untrans = [k for k in sorted(work_keys) if not work_entries[k]["msgstr"].strip()]
    if untrans:
        issue("WARN", "untranslated",
              f"{len(untrans)} untranslated: " + ", ".join(untrans))

    return issues


def run_custom_rules(copy_entries, work_entries, ctx):
    """Run all registered @rule functions across all entry pairs."""
    issues = []
    if not CUSTOM_RULES:
        return issues

    all_keys = sorted(set(copy_entries) | set(work_entries))
    for key in all_keys:
        c_entry = copy_entries.get(key)
        w_entry = work_entries.get(key)
        for r in CUSTOM_RULES:
            try:
                for msg in r["fn"](ctx, c_entry, w_entry):
                    issues.append({
                        "level":  r["level"],
                        "check":  r["name"],
                        "detail": msg,
                    })
            except Exception as e:
                issues.append({
                    "level":  "ERROR",
                    "check":  r["name"],
                    "detail": f'Rule crashed on "{key}": {e}',
                })
    return issues


# ─────────────────────────────────────────────
#  FOLDER WALKER
# ─────────────────────────────────────────────

def find_pairs(translated_dir, debug=False):
    """
    Yields (chapter_name, segment_id, work_path, copy_path_or_None).

    Works for any folder structure and naming convention:
      - Folder has no spaces  → segment_id = full folder name
                                e.g. "00_System"  → looks for "00_System.po"
      - Folder has spaces     → segment_id = part before first space
                                e.g. "e00_001_000 trans" → looks for "e00_001_000.po"
      - chapter_name = immediate parent folder relative to translated_dir.
      - Skips "- Copy.po" files.
    """
    if debug:
        print(f"\n[DEBUG] Scanning root: {translated_dir}")
        try:
            print(f"[DEBUG] Top-level contents: {os.listdir(translated_dir)}\n")
        except Exception as e:
            print(f"[DEBUG] Cannot list root: {e}\n")

    translated_dir = os.path.normpath(translated_dir)

    for dirpath, dirnames, filenames in os.walk(translated_dir):
        dirnames.sort()

        folder_name = os.path.basename(dirpath)

        # Derive segment_id: everything before the first space (or full name)
        segment_id = folder_name.split()[0] if " " in folder_name else folder_name

        po_filename   = f"{segment_id}.po"
        copy_filename = f"{segment_id} - Copy.po"

        if po_filename not in filenames:
            continue

        work_path = os.path.join(dirpath, po_filename)
        copy_path = os.path.join(dirpath, copy_filename)

        # chapter = immediate parent relative to root (or root itself)
        parent = os.path.dirname(dirpath)
        if parent == translated_dir:
            chapter_name = folder_name
        else:
            chapter_name = os.path.relpath(parent, translated_dir)

        if debug:
            print(f"[DEBUG] {chapter_name!r} / {segment_id!r}")
            print(f"[DEBUG]   .po exists  : {os.path.isfile(work_path)}")
            print(f"[DEBUG]   copy exists : {os.path.isfile(copy_path)}")

        yield (
            chapter_name,
            segment_id,
            work_path,
            copy_path if os.path.isfile(copy_path) else None,
        )


# ─────────────────────────────────────────────
#  REPORT
# ─────────────────────────────────────────────

LEVEL_ORDER  = {"ERROR": 0, "WARN": 1, "INFO": 2}
LEVEL_PREFIX = {"ERROR": "  ✗", "WARN": "  !", "INFO": "  i"}

def format_issues(issues):
    lines = []
    for iss in sorted(issues, key=lambda x: LEVEL_ORDER.get(x["level"], 9)):
        prefix = LEVEL_PREFIX.get(iss["level"], "  ?")
        detail_lines = iss["detail"].splitlines()
        lines.append(f"{prefix} [{iss['check']}] {detail_lines[0]}")
        for dl in detail_lines[1:]:
            lines.append(f"     {dl}")
    return lines



# ─────────────────────────────────────────────
#  HTML REPORT GENERATOR
# ─────────────────────────────────────────────

HTML_STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 14px;
         background: #f4f5f7; color: #1a1a2e; }
  header { background: #1a1a2e; color: #fff; padding: 20px 32px; }
  header h1 { font-size: 20px; font-weight: 600; margin-bottom: 6px; }
  header .meta { font-size: 12px; color: #aab; }
  .stat-bar { display: flex; gap: 12px; padding: 16px 32px;
              background: #fff; border-bottom: 1px solid #e0e0e0; flex-wrap: wrap;
              align-items: center; }
  .stat { padding: 6px 16px; border-radius: 20px; font-size: 13px;
          font-weight: 600; cursor: pointer; transition: opacity .15s; }
  .stat:hover { opacity: 0.75; }
  .stat.ok     { background: #e6f9f0; color: #1a7a4a; border: 1.5px solid #1a7a4a; }
  .stat.error  { background: #fdecea; color: #c0392b; border: 1.5px solid #c0392b; }
  .stat.warn   { background: #fff8e1; color: #b7860b; border: 1.5px solid #b7860b; }
  .stat.info   { background: #e8f0fe; color: #1a56c4; border: 1.5px solid #1a56c4; }
  .stat.active { opacity: 1; }
  .stat.dimmed { opacity: 0.35; }
  .search-wrap { margin-left: auto; }
  .search-wrap input { padding: 6px 12px; border: 1px solid #ccc; border-radius: 20px;
                        font-size: 13px; width: 220px; outline: none; }
  .search-wrap input:focus { border-color: #1a56c4; }
  .chapters { padding: 16px 32px; display: flex; flex-direction: column; gap: 12px; }
  .chapter { background: #fff; border-radius: 8px; border: 1px solid #e0e0e0;
             overflow: hidden; }
  .chapter-header { display: flex; align-items: center; gap: 10px;
                    padding: 10px 16px; cursor: pointer;
                    background: #f8f8fb; border-bottom: 1px solid #e8e8ee;
                    user-select: none; }
  .chapter-header:hover { background: #f0f0f8; }
  .chapter-name { font-weight: 600; font-size: 13px; flex: 1; }
  .chapter-counts { font-size: 12px; color: #888; }
  .chapter-toggle { font-size: 16px; color: #aaa; transition: transform .2s; }
  .chapter.collapsed .chapter-toggle { transform: rotate(-90deg); }
  .chapter.collapsed .chapter-body  { display: none; }
  .chapter-body { padding: 0; }
  .file-row { display: flex; align-items: flex-start; gap: 0;
              border-bottom: 1px solid #f0f0f0; padding: 8px 16px 8px 16px; }
  .file-row:last-child { border-bottom: none; }
  .file-badge { min-width: 100px; font-size: 11px; font-weight: 700;
                padding: 2px 8px; border-radius: 4px; text-align: center;
                margin-right: 12px; margin-top: 2px; flex-shrink: 0; }
  .badge-ok    { background: #e6f9f0; color: #1a7a4a; }
  .badge-error { background: #fdecea; color: #c0392b; }
  .badge-warn  { background: #fff8e1; color: #b7860b; }
  .badge-nobackup { background: #f3e8ff; color: #6d28d9; }
  .file-name { font-size: 13px; font-weight: 600; margin-bottom: 4px; color: #222; }
  .file-issues { display: flex; flex-direction: column; gap: 3px; flex: 1; }
  .issue { font-size: 12px; padding: 3px 8px; border-radius: 4px;
           border-left: 3px solid; line-height: 1.5; }
  .issue-ERROR { background: #fff5f5; border-color: #e74c3c; color: #922b21; }
  .issue-WARN  { background: #fffdf0; border-color: #f39c12; color: #7d6608; }
  .issue-INFO  { background: #f0f4ff; border-color: #5b8cff; color: #1a4db5; }
  .issue .check-tag { font-weight: 700; margin-right: 4px; }
  .issue .detail-line { display: block; padding-left: 12px;
                        color: #555; font-family: monospace; font-size: 11px; }
  .no-issues { font-size: 12px; color: #999; font-style: italic; }
  .hidden { display: none !important; }
  .debug-block { background: #f8f8fb; border: 1px dashed #ccc; border-radius: 6px;
                 padding: 12px 16px; font-family: monospace; font-size: 11px;
                 color: #666; margin: 0 32px 12px; }
  .debug-block summary { cursor: pointer; font-weight: 600; color: #888; }
</style>
"""

def html_escape(s):
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def build_html(results, translated_dir, timestamp, active_rules, debug_lines):
    total_ok  = sum(1 for r in results if r["status"] == "ok")
    total_err = sum(1 for r in results if r["status"] == "error")
    total_warn= sum(1 for r in results if r["status"] == "warn")

    # group by chapter
    from collections import OrderedDict
    chapters = OrderedDict()
    for r in results:
        chapters.setdefault(r["chapter"], []).append(r)

    lines = ["<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>",
             "<title>PO Validation Report</title>", HTML_STYLE, "</head><body>"]

    lines.append(f"""<header>
  <h1>PO Validation Report</h1>
  <div class="meta">
    {html_escape(translated_dir)} &nbsp;·&nbsp; {timestamp}
    {"&nbsp;·&nbsp; Rules: " + html_escape(", ".join(active_rules)) if active_rules else ""}
  </div>
</header>""")

    lines.append(f"""<div class="stat-bar">
  <span class="stat ok"    onclick="filterStatus('ok')"   >✔ OK &nbsp;{total_ok}</span>
  <span class="stat error" onclick="filterStatus('error')" >✗ Errors &nbsp;{total_err}</span>
  <span class="stat warn"  onclick="filterStatus('warn')"  >! Warnings &nbsp;{total_warn}</span>
  <span class="stat info"  onclick="filterStatus('all')"   >Show all</span>
  <span class="search-wrap"><input type="text" id="search" placeholder="Search segment…" oninput="filterSearch(this.value)"></span>
</div>""")

    if debug_lines:
        debug_html = html_escape("\n".join(debug_lines))
        lines.append(f'<details class="debug-block"><summary>Debug / scan info</summary><pre>{debug_html}</pre></details>')

    lines.append('<div class="chapters">')
    for chapter_name, rows in chapters.items():
        ch_err  = sum(1 for r in rows if r["status"] == "error")
        ch_warn = sum(1 for r in rows if r["status"] == "warn")
        ch_ok   = sum(1 for r in rows if r["status"] == "ok")
        counts  = f"{ch_err} err &nbsp; {ch_warn} warn &nbsp; {ch_ok} ok"
        lines.append(f"""<div class="chapter" data-chapter="{html_escape(chapter_name)}">
  <div class="chapter-header" onclick="toggleChapter(this)">
    <span class="chapter-toggle">▾</span>
    <span class="chapter-name">{html_escape(chapter_name)}</span>
    <span class="chapter-counts">{counts}</span>
  </div>
  <div class="chapter-body">""")

        for r in rows:
            status = r["status"]
            badge_cls = {"ok":"badge-ok","error":"badge-error","warn":"badge-warn","nobackup":"badge-nobackup"}.get(status,"badge-warn")
            badge_txt = {"ok":"OK","error":"ERROR","warn":"WARN","nobackup":"NO BACKUP"}.get(status, status.upper())
            issues_html = ""
            if r["issues"]:
                for iss in r["issues"]:
                    detail_parts = iss["detail"].splitlines()
                    first = html_escape(detail_parts[0])
                    rest  = "".join(f'<span class="detail-line">{html_escape(dl)}</span>' for dl in detail_parts[1:])
                    issues_html += f'<div class="issue issue-{iss["level"]}"><span class="check-tag">[{html_escape(iss["check"])}]</span>{first}{rest}</div>\n'
            else:
                issues_html = '<span class="no-issues">No issues found</span>'

            lines.append(f"""<div class="file-row" data-status="{status}" data-name="{html_escape(r['segment'])}">
  <span class="file-badge {badge_cls}">{badge_txt}</span>
  <div class="file-issues">
    <div class="file-name">{html_escape(r['segment'])}</div>
    {issues_html}
  </div>
</div>""")

        lines.append("</div></div>")  # chapter-body, chapter

    lines.append("</div>")  # chapters

    lines.append("""<script>
function toggleChapter(hdr) {
  hdr.closest('.chapter').classList.toggle('collapsed');
}
function filterStatus(status) {
  document.querySelectorAll('.file-row').forEach(row => {
    row.classList.toggle('hidden',
      status !== 'all' && row.dataset.status !== status);
  });
}
function filterSearch(q) {
  q = q.toLowerCase();
  document.querySelectorAll('.file-row').forEach(row => {
    row.classList.toggle('hidden', q && !row.dataset.name.toLowerCase().includes(q));
  });
  // Show chapters that have visible rows
  document.querySelectorAll('.chapter').forEach(ch => {
    const any = Array.from(ch.querySelectorAll('.file-row'))
                     .some(r => !r.classList.contains('hidden'));
    ch.classList.toggle('hidden', !any);
  });
}
</script></body></html>""")

    return "\n".join(lines)


# ─────────────────────────────────────────────
#  MAIN RUN
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
#  AUTO-FIX: INSERT MISSING ENTRIES
# ─────────────────────────────────────────────

def build_entry_block(entry: dict) -> str:
    """Reconstruct a .po entry block from a parsed entry dict."""
    lines = []
    for comment in entry.get("comments", []):
        lines.append(f"#. {comment}" if comment.strip() else "#. ")
    lines.append(f'msgctxt "{entry["msgctxt"]}"'  )
    # Encode msgid
    msgid = entry["msgid"]
    if "\n" in msgid or len(msgid) > 80:
        lines.append('msgid ""')
        for part in msgid.split("\n"):
            lines.append(f'"{part}\\n"') if part != msgid.split("\n")[-1] else lines.append(f'"{part}"')
    else:
        lines.append(f'msgid "{msgid}"')
    lines.append('msgstr ""')
    return "\n".join(lines)


def auto_fix_missing(work_path: str, copy_entries: dict, copy_keys: list,
                     work_entries: dict, work_keys: list) -> int:
    """
    Insert entries that exist in Copy but are missing in Work.
    Each missing entry is inserted as msgstr "" (untranslated).
    Tries to insert at the correct position based on Copy's ordering.
    Returns number of entries added.
    """
    missing_keys = [k for k in copy_keys if k not in work_entries]
    if not missing_keys:
        return 0

    with open(work_path, encoding="utf-8") as f:
        raw = f.read()

    added = 0
    for key in missing_keys:
        entry       = copy_entries[key]
        new_block   = build_entry_block(entry)
        key_pos     = copy_keys.index(key)

        # Find the closest preceding key that exists in work file
        insert_after_key = None
        for prev_key in reversed(copy_keys[:key_pos]):
            if prev_key in work_entries:
                insert_after_key = prev_key
                break

        if insert_after_key:
            # Find end of that entry's msgstr block in raw
            anchor = f'msgctxt "{insert_after_key}"' 
            anchor_pos = raw.find(anchor)
            if anchor_pos == -1:
                continue
            # Find the next blank line after this entry (end of entry block)
            search_from = anchor_pos + len(anchor)
            # Find double newline or next msgctxt
            next_entry  = raw.find("\nmsgctxt ", search_from)
            next_blank  = raw.find("\n\n", search_from)
            if next_blank != -1 and (next_entry == -1 or next_blank < next_entry):
                insert_pos = next_blank + 2  # after the blank line
            elif next_entry != -1:
                insert_pos = next_entry + 1  # before the next msgctxt
            else:
                insert_pos = len(raw)
        else:
            # No preceding key found — insert after header (first blank line)
            first_blank = raw.find("\n\n")
            insert_pos  = first_blank + 2 if first_blank != -1 else 0

        raw = raw[:insert_pos] + new_block + "\n\n" + raw[insert_pos:]
        added += 1

    if added:
        with open(work_path, "w", encoding="utf-8") as f:
            f.write(raw)

    return added

def run():
    root = tk.Tk()
    root.withdraw()
    translated_dir = filedialog.askdirectory(title="Select the 'translated' folder")
    root.destroy()
    if not translated_dir:
        print("No folder selected.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path  = os.path.join(translated_dir, f"validation_{timestamp}.log")
    html_path = os.path.join(translated_dir, f"validation_{timestamp}.html")

    active_rules  = [r["name"] for r in CUSTOM_RULES]
    debug_lines   = []
    results       = []   # list of result dicts for HTML

    total_errors = total_warns = files_ok = files_err = 0
    found_any = False

    # Debug folder scan info
    debug_lines.append(f"Selected path: {translated_dir}")
    try:
        top = os.listdir(translated_dir)
        debug_lines.append(f"Top-level folders: {[x for x in top if os.path.isdir(os.path.join(translated_dir, x))]}")
    except Exception as e:
        debug_lines.append(f"ERROR listing folder: {e}")

    # Plain-text log header
    out = []
    out.append("=" * 72)
    out.append("  PO VALIDATION REPORT")
    out.append(f"  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    out.append(f"  Folder    : {translated_dir}")
    out.append(f"  Rules     : built-in" + (f" + {', '.join(active_rules)}" if active_rules else ""))
    out.append("=" * 72)
    out.extend(debug_lines)
    out.append("")

    current_chapter = None

    for chapter_name, segment_id, work_path, copy_path in find_pairs(translated_dir, debug=False):
        found_any = True

        if chapter_name != current_chapter:
            current_chapter = chapter_name
            out.append(f"\n{'─' * 72}")
            out.append(f"  CHAPTER  {chapter_name}")
            out.append(f"{'─' * 72}")

        rel = f"{chapter_name}/{segment_id}"

        if copy_path is None:
            out.append(f"\n  [NO BACKUP]  {segment_id}")
            out.append(f"    ✗ [backup] Copy file not found — skipping.")
            total_errors += 1
            files_err += 1
            results.append({"chapter": chapter_name, "segment": segment_id,
                            "status": "nobackup", "issues": [
                                {"level":"ERROR","check":"backup",
                                 "detail":"Copy file not found — run backup script first"}]})
            continue

        try:
            copy_header, copy_entries, copy_keys = parse_po(copy_path)
            work_header, work_entries, _          = parse_po(work_path)
        except Exception as e:
            out.append(f"\n  [PARSE ERROR]  {segment_id}")
            out.append(f"    ✗ [parse] {e}")
            total_errors += 1
            files_err += 1
            results.append({"chapter": chapter_name, "segment": segment_id,
                            "status": "error", "issues": [
                                {"level":"ERROR","check":"parse","detail":str(e)}]})
            continue

        # ── Auto-fix missing entries before validation ────────────
        missing_keys = [k for k in copy_keys if k not in work_entries]
        if missing_keys:
            n_fixed = auto_fix_missing(
                work_path, copy_entries, copy_keys,
                work_entries, list(work_entries.keys())
            )
            if n_fixed:
                out.append(f"    ✚ [auto-fix] Inserted {n_fixed} missing entr{'y' if n_fixed==1 else 'ies'} — re-parsing...")
                work_header, work_entries, _ = parse_po(work_path)

        ctx    = {"file": rel, "chapter": chapter_name, "segment": segment_id}
        issues = run_builtin_checks(copy_header, copy_entries, work_header, work_entries)
        issues += run_custom_rules(copy_entries, work_entries, ctx)

        n_err  = sum(1 for i in issues if i["level"] == "ERROR")
        n_warn = sum(1 for i in issues if i["level"] == "WARN")
        total_errors += n_err
        total_warns  += n_warn

        if n_err:
            status = "error"; tag = f"[{n_err} ERROR{'S' if n_err>1 else ''}]"; files_err += 1
        elif n_warn:
            status = "warn";  tag = f"[{n_warn} WARN{'S' if n_warn>1 else ''}]"; files_err += 1
        else:
            status = "ok";    tag = "[OK]"; files_ok += 1

        out.append(f"\n  {tag:<16} {segment_id}")
        out.extend(f"  {ln}" for ln in format_issues(issues))
        results.append({"chapter": chapter_name, "segment": segment_id,
                        "status": status, "issues": issues})

    # Plain-text summary
    out.append(f"\n{'=' * 72}")
    if not found_any:
        out.append("  !! NO .PO FILES FOUND — check debug info above !!")
        out.append("  Make sure you selected the folder CONTAINING the chapter folders.")
        out.append("")
    out.append("  SUMMARY")
    out.append(f"{'=' * 72}")
    out.append(f"  Files OK          : {files_ok}")
    out.append(f"  Files with issues : {files_err}")
    out.append(f"  Total ERRORs      : {total_errors}")
    out.append(f"  Total WARNings    : {total_warns}")
    out.append(f"{'=' * 72}")

    report = "\n".join(out)
    print(report)

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(report)

    html = build_html(results, translated_dir,
                      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      active_rules, debug_lines)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nLog  saved → {log_path}")
    print(f"HTML saved → {html_path}")

    # Auto-open the HTML report in the default browser
    import webbrowser
    webbrowser.open(html_path)


if __name__ == "__main__":
    run()
