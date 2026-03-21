import os
import re
import time
import tkinter as tk
from tkinter import filedialog
from playwright.sync_api import sync_playwright

# ╔══════════════════════════════════════════════════════════════════╗
# ║                    ⚙  USER SETTINGS                             ║
# ╚══════════════════════════════════════════════════════════════════╝

# "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeDebug"

MAX_FILES_TO_TRANSLATE = 3      # How many .po files to process per run
MAX_LINES_PER_BATCH    = 600    # Max lines of .po content sent to Gemini per request
WAIT_BETWEEN_BATCHES   = 8      # Seconds to pause between Gemini calls

# ════════════════════════════════════════════════════════════════════

STOP_BTN_SEL = 'button[aria-label*="Stop"], button[aria-label*="Dừng"]'

TRANSLATE_PROMPT_TEMPLATE = """\
Translate the following .po file entries for the Danganronpa project into Vietnamese. \
Use my 'Saved Information' for character-specific tones (Makoto, Hina, etc.) and \
terminology (Ultimate, Hope's Peak).
Strict constraints for this session:
* Preserve all msgctxt and tags like <CLT X>.
* Limit to exactly one exclamation mark per sentence.
* Keep ellipses (...) only if present in the English source.
* Do not skip entries, even if they are duplicates.
* Return only the translated content inside a single code block.
File content:

{entries}"""

SUMMARY_PROMPT = """\
Based on these Danganronpa dialogue lines, give me a 2-3 word English label \
suitable for a folder name (e.g. "Sayaka Door Scare" or "Class Trial Vote").
Reply with ONLY those 2-3 words — nothing else.

Lines:
{samples}"""


# ════════════════════════════════════════════════════════════════════
#  PO  HELPERS
# ════════════════════════════════════════════════════════════════════

def _po_raw_to_text(raw_block: str) -> str:
    """
    Collapse a raw PO quoted string (single or multi-line) to a plain Python string.
    e.g.  "line1\\nline2"  →  "line1\nline2"
          ""\n"line1\\n"\n"line2"  →  "line1\nline2"
    """
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', raw_block)
    text  = "".join(parts)
    return text.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def _text_to_po_val(text: str) -> str:
    """
    Encode a plain Python string back to PO value format.
    Returns the part that follows the keyword, e.g.  "line1\\nline2"
    Multi-line strings use the leading-empty-string convention.
    """
    esc = text.replace("\\", "\\\\").replace('"', '\\"')

    if "\n" not in esc:
        return f'"{esc}"'

    lines = esc.split("\n")
    parts = ['""']
    for i, line in enumerate(lines):
        suffix = "\\n" if i < len(lines) - 1 else ""
        parts.append(f'"{line}{suffix}"')

    # Drop trailing redundant empty string
    if parts[-1] == '""':
        parts.pop()

    return "\n".join(parts)


def parse_po(filepath: str):
    """
    Parse a .po file into a list of entry dicts.
    Returns (raw_text, entries).

    Each entry dict:
        msgctxt   str
        msgid     str   (decoded plain text)
        msgstr    str   (decoded plain text)
        is_empty  bool
        block     str   (the raw text of this block, used to rebuild prompt)
    """
    with open(filepath, encoding="utf-8") as f:
        raw = f.read()

    raw = raw.replace("\r\n", "\n").replace("\r", "\n")

    entries = []

    # Q = one PO quoted string, handles escaped chars like \" and \\
    Q = r'"(?:[^"\\]|\\.)*"'

    pattern = re.compile(
        r"((?:#[^\n]*\n)*"              # optional #. comment lines
        r'msgctxt\s+' + Q + r'\n'      # msgctxt line
        r'msgid\s+(?:' + Q + r'\n?)+'  # msgid (possibly multi-line)
        r'msgstr\s+(?:' + Q + r'\n?)*)', # msgstr (possibly empty)
        re.MULTILINE,
    )

    for m in pattern.finditer(raw):
        block = m.group(1)

        ctx_m = re.search(r'msgctxt\s+"([^"]+)"', block)
        id_m  = re.search(r'(msgid\s+(?:' + Q + r'\n?)+)', block)
        str_m = re.search(r'(msgstr\s+(?:' + Q + r'\n?)*)', block)

        if not ctx_m or not id_m:
            continue

        msgctxt = ctx_m.group(1)
        msgid   = _po_raw_to_text(id_m.group(1))
        msgstr  = _po_raw_to_text(str_m.group(1)) if str_m else ""

        entries.append({
            "msgctxt":  msgctxt,
            "msgid":    msgid,
            "msgstr":   msgstr,
            "is_empty": msgstr.strip() == "",
            "block":    block,
        })

    return raw, entries


def build_po_block(entry: dict) -> str:
    """
    Reconstruct a clean .po entry block from a Copy.po entry
    (comments + msgctxt + msgid + empty msgstr) for pasting into Gemini.
    """
    comments    = re.findall(r"^#\.[^\n]*", entry["block"], re.MULTILINE)
    comment_str = "\n".join(comments) + "\n" if comments else ""
    msgid_val   = _text_to_po_val(entry["msgid"])
    return (
        f'{comment_str}'
        f'msgctxt "{entry["msgctxt"]}"\n'
        f'msgid {msgid_val}\n'
        f'msgstr ""\n'
    )


def write_translations_to_po(filepath: str, raw: str, translations: dict) -> None:
    """
    Patch `raw` PO content with new translations then save to `filepath`.
    Only touches entries whose msgctxt appears in `translations`.
    translations: { msgctxt_str → Vietnamese_text }
    """
    updated = raw

    for msgctxt, translated_text in translations.items():
        ctx_esc = re.escape(msgctxt)

        Q = r'"(?:[^"\\\\]|\\\\.)*"'
        entry_pat = re.compile(
            r'(msgctxt\s+"' + ctx_esc + r'"\n'
            r'(?:msgid\s+(?:' + Q + r'\n?)+)'
            r')(msgstr\s+(?:' + Q + r'\n?)*)',
            re.MULTILINE,
        )

        new_msgstr = f"msgstr {_text_to_po_val(translated_text)}"

        def _replacer(m, ns=new_msgstr):
            return m.group(1) + ns

        patched = entry_pat.sub(_replacer, updated, count=1)
        if patched == updated:
            print(f"    ⚠  Could not patch entry: {msgctxt}")
        updated = patched

    # DRAT rejects bare "#." lines — add a trailing space to make "#. "
    cleaned = "\n".join(
        "#. " if line.rstrip() == "#." else line
        for line in updated.split("\n")
    )
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(cleaned)


# ════════════════════════════════════════════════════════════════════
#  RESPONSE  PARSER
# ════════════════════════════════════════════════════════════════════

def parse_translated_block(response_text: str) -> dict:
    """
    Parse Gemini's response which contains .po entries.
    Handles both fenced (``` ```) and bare .po output.
    msgid is optional — Gemini sometimes omits it and returns only msgctxt + msgstr.
    Returns { msgctxt → Vietnamese msgstr text }.
    """
    # Normalise browser line endings before parsing
    response_text = response_text.replace("\r\n", "\n").replace("\r", "\n")

    # Strip code fences if present
    fenced = re.search(r"```[a-z]*\s*(.*?)\s*```", response_text, re.DOTALL)
    content = fenced.group(1) if fenced else response_text

    translations = {}

    # Q matches one PO quoted string including escaped chars like \" and \\
    Q = r'"(?:[^"\\\\]|\\\\.)*"'

    # Primary: msgctxt + optional msgid + msgstr
    entry_pat = re.compile(
        r'msgctxt\s+"([^"]+)"\s*\n'
        r'(?:msgid\s+(?:' + Q + r'\n?)+)?'   # msgid — optional
        r'(msgstr\s+(?:' + Q + r'\n?)*)',     # msgstr — required
        re.MULTILINE,
    )

    for m in entry_pat.finditer(content):
        msgctxt    = m.group(1)
        msgstr_raw = m.group(2)
        msgstr     = _po_raw_to_text(msgstr_raw)
        if msgstr.strip():
            translations[msgctxt] = msgstr

    return translations


# ════════════════════════════════════════════════════════════════════
#  GEMINI  HELPERS
# ════════════════════════════════════════════════════════════════════

def _wait_for_response(page) -> None:
    """Wait for Gemini to start and finish generating."""
    try:
        page.wait_for_selector(STOP_BTN_SEL, state="visible",  timeout=15_000)
        page.wait_for_selector(STOP_BTN_SEL, state="detached", timeout=180_000)
    except Exception:
        pass
    time.sleep(1.5)


def _count_responses(page) -> int:
    """Count how many model-response elements exist right now."""
    return page.locator("model-response").count()


def _extract_nth_response(page, index: int) -> str:
    """
    Extract text from the model-response at `index` (0-based).
    Falls back to the last response if the nth one isn't found.

    Gemini response structure:
      model-response
        └─ message-content
             └─ div.markdown
                  └─ <p data-path-to-node> (plain text)
                  └─ <pre>                 (code block)
    """
    SHORT = 5_000   # ms — for count/existence checks only

    all_resps = page.locator("model-response")

    # Resolve which bubble to read: prefer nth, fall back to last
    total = all_resps.count()
    if total == 0:
        return ""

    if index < total:
        resp = all_resps.nth(index)
    else:
        resp = all_resps.last

    # 1. Code block scoped to this bubble
    try:
        pre = resp.locator("pre")
        if pre.count() > 0:
            return pre.last.inner_text(timeout=SHORT)
    except Exception:
        pass

    # 2. message-content scoped to this bubble
    try:
        mc = resp.locator("message-content")
        if mc.count() > 0:
            return mc.last.inner_text(timeout=SHORT)
    except Exception:
        pass

    # 3. Paragraphs with data-path-to-node
    try:
        paras = resp.locator("p[data-path-to-node]")
        n = paras.count()
        if n > 0:
            return "\n".join(paras.nth(i).inner_text(timeout=SHORT) for i in range(n))
    except Exception:
        pass

    # 4. Last-resort: full bubble text with a short timeout
    try:
        return resp.inner_text(timeout=SHORT)
    except Exception:
        return ""


def send_to_gemini(page, text: str) -> str:
    """
    Paste `text` directly into the Gemini chatbox and submit.
    Gemini's chatbox is a contenteditable div — NOT <input>/<textarea>.
    Never call .input_value() on it; use JS innerText to verify instead.
    Returns Gemini's response string.
    """
    chatbox = page.get_by_role("textbox")
    chatbox.click()

    # JS paste — safe for contenteditable divs
    page.evaluate(
        """(txt) => {
            const el = document.activeElement;
            el.focus();
            document.execCommand('selectAll');
            document.execCommand('insertText', false, txt);
        }""",
        text,
    )

    # Verify via innerText (works on contenteditable); fallback to fill()
    placed = page.evaluate("() => document.activeElement.innerText || ''")
    if not placed.strip() and len(text) < 50_000:
        chatbox.fill(text)

    # Record how many responses exist before we submit
    response_index = _count_responses(page)

    page.keyboard.press("Enter")
    print("    → Waiting for Gemini response...")
    _wait_for_response(page)

    # Wait up to 15s for the new response bubble to appear
    try:
        page.locator("model-response").nth(response_index).wait_for(state="attached", timeout=15_000)
    except Exception:
        pass  # Already handled by fallback-to-last in _extract_nth_response

    # Extra small buffer for content to render inside the bubble
    time.sleep(0.5)

    return _extract_nth_response(page, response_index)


# ════════════════════════════════════════════════════════════════════
#  FILE  DISCOVERY
# ════════════════════════════════════════════════════════════════════

def discover_files(root_dir: str, limit: int) -> list:
    """
    Walk `root_dir` and collect up to `limit` segment folders whose
    working .po file has at least one empty msgstr.

    Rules (same convention as the validator):
      - Folder name must have NO spaces (folders with spaces are skipped
        but their children are still searched).
      - The working .po must be named exactly <folder_name>.po
      - The copy .po must be named exactly <folder_name> - Copy.po

    Each result dict:
        work_path      – path to the working .po  (read back + write)
        copy_path      – path to the Copy.po      (source for prompt)
        work_raw       – raw string of working .po
        copy_entries   – parsed entries from Copy.po (or work if no copy)
        missing_ctxts  – set of msgctxt values that are empty in work .po
        folder_path    – the direct parent folder containing the .po files
    """
    results = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames.sort()
        dirnames[:] = [d for d in dirnames if "SKIP" not in d]

        folder_name = os.path.basename(dirpath)
        if "SKIP" in folder_name:
            continue

        # Derive segment_id: part before first space, or full name if no spaces
        # e.g. "e00_001_000 trans" → "e00_001_000"
        # e.g. "00_System"        → "00_System"
        segment_id = folder_name.split()[0] if " " in folder_name else folder_name

        po_filename   = f"{segment_id}.po"
        copy_filename = f"{segment_id} - Copy.po"

        if po_filename not in filenames:
            continue

        work_path = os.path.join(dirpath, po_filename)
        copy_path = os.path.join(dirpath, copy_filename) if copy_filename in filenames else None

        try:
            work_raw, work_entries = parse_po(work_path)
        except Exception as e:
            print(f"  ⚠  Parse error ({work_path}): {e}")
            continue

        missing_ctxts = {e["msgctxt"] for e in work_entries if e["is_empty"]}
        if not missing_ctxts:
            continue

        if copy_path:
            try:
                _, copy_entries = parse_po(copy_path)
            except Exception as e:
                print(f"  ⚠  Could not parse Copy.po ({copy_path}): {e} — using work file")
                copy_entries = work_entries
        else:
            print(f"  ⚠  No Copy.po in {dirpath} — using working file as source")
            copy_entries = work_entries

        results.append({
            "work_path":     work_path,
            "copy_path":     copy_path,
            "work_raw":      work_raw,
            "copy_entries":  copy_entries,
            "missing_ctxts": missing_ctxts,
            "folder_path":   dirpath,
            "segment_id":    segment_id,
        })

        if len(results) >= limit:
            break

    return results


# ════════════════════════════════════════════════════════════════════
#  PER-FILE  PROCESSOR
# ════════════════════════════════════════════════════════════════════

def process_file(page, info: dict) -> None:
    """Translate one .po file and rename its containing folder."""
    work_path     = info["work_path"]
    work_raw      = info["work_raw"]
    copy_entries  = info["copy_entries"]
    missing_ctxts = info["missing_ctxts"]
    folder_path   = info["folder_path"]

    # Entries to translate: pull from Copy.po but only the ones missing in work .po
    to_translate = [e for e in copy_entries if e["msgctxt"] in missing_ctxts]

    print(f"\n{'─'*65}")
    print(f"  Folder : {folder_path}")
    print(f"  File   : {os.path.basename(work_path)}")
    print(f"  Missing: {len(to_translate)} / {len(copy_entries)} entries")

    if not to_translate:
        print("  ✓ Nothing to translate.")
        return

    # ── Split into batches by line count (max MAX_LINES_PER_BATCH) ─
    def make_batches(entries):
        batches, current, current_lines = [], [], 0
        for e in entries:
            block_lines = build_po_block(e).count("\n") + 1
            if current and current_lines + block_lines > MAX_LINES_PER_BATCH:
                batches.append(current)
                current, current_lines = [], 0
            current.append(e)
            current_lines += block_lines
        if current:
            batches.append(current)
        return batches
    batches = make_batches(to_translate)

    all_translations: dict = {}

    for idx, batch in enumerate(batches):
        print(f"\n  Batch {idx + 1}/{len(batches)}  ({len(batch)} entries)")

        entries_text = "\n".join(build_po_block(e) for e in batch)
        prompt       = TRANSLATE_PROMPT_TEMPLATE.format(entries=entries_text)

        response = send_to_gemini(page, prompt)
        parsed   = parse_translated_block(response)

        if not parsed:
            print(f"    ✗  No translations parsed.")
        else:
            print(f"    ✓  Got {len(parsed)} translation(s).")
            all_translations.update(parsed)

        if idx < len(batches) - 1:
            print(f"  ⏳ Waiting {WAIT_BETWEEN_BATCHES}s before next batch...")
            time.sleep(WAIT_BETWEEN_BATCHES)

    if not all_translations:
        print("  ✗ No translations to write — skipping write-back.")
    else:
        # ── Write back to the working .po file ────────────────────
        write_translations_to_po(work_path, work_raw, all_translations)
        print(f"\n  ✓ Written {len(all_translations)} translation(s) to {os.path.basename(work_path)}")

    # ── Ask Gemini for a 2-3 word folder summary (always runs) ────
    print("  Requesting folder name summary...")
    time.sleep(WAIT_BETWEEN_BATCHES)

    sample_lines = [
        e["msgid"].replace("\n", " ").strip()
        for e in copy_entries
        if e["msgid"].strip() and "<CLT" not in e["msgid"]
    ][:6]

    summary_prompt = SUMMARY_PROMPT.format(
        samples="\n".join(f"- {s}" for s in sample_lines)
    )
    summary_raw = send_to_gemini(page, summary_prompt)

    # Sanitise: ASCII words only — blocks Japanese/symbols from leaking in
    summary = summary_raw.strip().strip('"').strip("'")
    summary = summary.split("\n")[0].strip()
    summary = re.sub(r'[<>:"/\\|?*\[\]#.]', "", summary)
    summary = " ".join(w for w in summary.split() if w.isascii())[:50].strip()

    segment_id = os.path.basename(folder_path).split()[0]
    old_name   = os.path.basename(folder_path)

    if summary:
        # Only rename if folder has no label yet (bare segment_id)
        if old_name.strip() == segment_id.strip():
            parent   = os.path.dirname(folder_path)
            new_name = f"{segment_id} {summary}"
            new_path = os.path.join(parent, new_name)
            try:
                os.rename(folder_path, new_path)
                print(f"  ✓ Folder renamed:  {old_name}  →  {new_name}")
            except Exception as e:
                print(f"  ⚠  Rename failed: {e}")
        else:
            print(f"  ℹ  Already labelled — skipping rename: {old_name}")
    else:
        print("  ⚠  No valid ASCII summary — folder name unchanged.")


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def pick_folder(title: str) -> str:
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title=title)
    root.destroy()
    return folder


def run() -> None:
    print("═" * 65)
    print("  Gemini PO Translator — Danganronpa Fan TL")
    print("═" * 65)

    # ── Folder picker ──────────────────────────────────────────────
    translated_dir = pick_folder("Select your 'translated' working folder")
    if not translated_dir:
        print("No folder selected — exiting.")
        return

    print(f"\n  Folder : {translated_dir}")
    print(f"  Limit  : {MAX_FILES_TO_TRANSLATE} file(s)\n")

    # ── Scan for files that need translation ───────────────────────
    print("  Scanning for missing translations...")
    to_process = discover_files(translated_dir, MAX_FILES_TO_TRANSLATE)

    if not to_process:
        print("  ✓ All .po files are fully translated!")
        return

    print(f"\n  Found {len(to_process)} file(s) to translate:\n")
    for fi in to_process:
        rel = os.path.relpath(fi["work_path"], translated_dir)
        copy_note = "(Copy.po found ✓)" if fi["copy_path"] else "(no Copy.po — using work file)"
        print(f"    {rel}  —  {len(fi['missing_ctxts'])} missing  {copy_note}")

    # ── Connect to the already-open Chrome ────────────────────────
    print("\n  Connecting to Chrome on port 9222...")
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception as e:
            msg = (
                f"Cannot connect to Chrome.\n\n"
                f"Start Chrome with:\n"
                f"chrome.exe --remote-debugging-port=9222\n\n{e}"
            )
            print(f"\n  ERROR: {msg}")
            return

        context = browser.contexts[0]
        page    = context.pages[0] if context.pages else context.new_page()

        print(f"  Connected to: {page.title()}")
        print(
            "\n  ⚠  The script uses YOUR CURRENT Gemini tab and chat as-is.\n"
            "     Open a new chat manually in Chrome BEFORE running if you\n"
            "     want a clean context.\n"
        )

        # ── Translate each file without touching the chat session ─
        for fi in to_process:
            process_file(page, fi)

    # ── Summary ────────────────────────────────────────────────────
    msg = f"Processed {len(to_process)} file(s).\nCheck the console for details."
    print(f"\n{'═'*65}")
    print(f"  Done!  {msg}")
    print("═" * 65)




if __name__ == "__main__":
    run()
