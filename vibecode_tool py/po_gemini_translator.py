import os
import re
import time
import tkinter as tk
from tkinter import filedialog
from playwright.sync_api import sync_playwright

# ╔══════════════════════════════════════════════════════════════════╗
# ║                    ⚙  DEFAULT SETTINGS                          ║
# ╚══════════════════════════════════════════════════════════════════╝

# "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeDebug"

# Default values (can be overridden via parameters)
MAX_FILES_TO_TRANSLATE = 59      # How many .po files to process per run
MAX_LINES_PER_BATCH    = 600    # Max lines of .po content sent to Gemini per request
WAIT_BETWEEN_BATCHES   = 8      # Seconds to pause between Gemini calls

# ════════════════════════════════════════════════════════════════════

STOP_BTN_SEL = 'button[aria-label*="Stop"], button[aria-label*="Dừng"]'

TRANSLATE_PROMPT_TEMPLATE = """Translate the following .po file entries for the Danganronpa project into Vietnamese.
Use my 'Saved Information' for character-specific tones (Makoto, Hina, etc.) and terminology (Ultimate, Hope's Peak).

OUTPUT FORMAT — follow this exactly, no exceptions:
- Leave msgid completely unchanged. Never translate or modify it.
- Put your Vietnamese translation ONLY in msgstr, replacing the empty "".
- Never put Vietnamese text in msgid. Never leave msgstr empty on a translated entry.

EXAMPLE:
Input:
  msgctxt "0003 | MAKOTO NAEGI"
  msgid "I hope we can all get along!"
  msgstr ""

Required output:
  msgctxt "0003 | MAKOTO NAEGI"
  msgid "I hope we can all get along!"
  msgstr "Tôi hy vọng chúng ta có thể hòa thuận!"

ADDITIONAL RULES:
* Preserve all tags like <CLT X> and <CLT> exactly as they appear in msgid.
* Use the Japanese #. comment lines for translation context only — do not output them.
* Limit to exactly one exclamation mark per sentence.
* Keep ellipses (...) only if present in the English source.
* Do not skip any entries, even duplicates.
* Return all entries inside a single code block.

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


def batch_entries(entries: list) -> list:
    """
    Groups entries into batches to avoid hitting Gemini's context window limits.
    Uses MAX_LINES_PER_BATCH as a guide for when to split.
    """
    batches = []
    current_batch = []
    current_line_count = 0

    for entry in entries:
        # Estimate lines (msgid lines + metadata)
        entry_lines = entry["msgid"].count("\n") + 3 
        
        if current_line_count + entry_lines > MAX_LINES_PER_BATCH and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_line_count = 0
            
        current_batch.append(entry)
        current_line_count += entry_lines

    if current_batch:
        batches.append(current_batch)
    return batches


def write_translations_to_po(filepath: str, raw: str, translations: dict) -> None:
    """
    Patch `raw` PO content with new translations then save to `filepath`.
    """
    updated = raw

    for msgctxt, translated_text in translations.items():
        ctx_esc = re.escape(msgctxt)

        Q = r'"(?:[^"\\]|\\.)*"'
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
    Handles two formats Gemini may return:
      - Normal:   translation is in msgstr  (correct)
      - Inverted: translation is in msgid, msgstr left empty  (Gemini mistake)
    """
    response_text = response_text.replace("\r\n", "\n").replace("\r", "\n")

    fenced = re.search(r"```[a-z]*\s*(.*?)\s*```", response_text, re.DOTALL)
    content = fenced.group(1) if fenced else response_text

    translations = {}

    Q = r'"(?:[^"\\]|\\.)*"'

    entry_pat = re.compile(
        r'msgctxt\s+"([^"]+)"\s*\n'
        r'((msgid\s+(?:' + Q + r'\n?)+))'
        r'(msgstr\s+(?:' + Q + r'\n?)*)',
        re.MULTILINE,
    )

    for m in entry_pat.finditer(content):
        msgctxt    = m.group(1)
        msgid_raw  = m.group(2) or ""
        msgstr_raw = m.group(4)
        msgstr     = _po_raw_to_text(msgstr_raw)
        msgid      = _po_raw_to_text(msgid_raw)

        if msgstr.strip():
            # Normal: translation in msgstr
            translations[msgctxt] = msgstr
        elif msgid.strip():
            # Inverted: Gemini put translation in msgid, left msgstr empty
            translations[msgctxt] = msgid

    return translations


def _count_responses(page) -> int:
    """Count how many model-response elements exist right now."""
    return page.locator("model-response").count()


def _extract_nth_response(page, index: int) -> str:
    """
    Extract text from the model-response at `index` (0-based).
    """
    SHORT = 5_000   # ms — for count/existence checks only

    all_resps = page.locator("model-response")

    total = all_resps.count()
    if total == 0:
        return ""

    if index < total:
        resp = all_resps.nth(index)
    else:
        resp = all_resps.last

    try:
        pre = resp.locator("pre")
        if pre.count() > 0:
            return pre.last.inner_text(timeout=SHORT)
    except Exception:
        pass

    try:
        mc = resp.locator("message-content")
        if mc.count() > 0:
            return mc.last.inner_text(timeout=SHORT)
    except Exception:
        pass

    try:
        paras = resp.locator("p[data-path-to-node]")
        n = paras.count()
        if n > 0:
            return "\n".join(paras.nth(i).inner_text(timeout=SHORT) for i in range(n))
    except Exception:
        pass

    try:
        return resp.inner_text(timeout=SHORT)
    except Exception:
        return ""


def _wait_for_generation_to_finish(page, response_index: int) -> str:
    """Wait by monitoring the text output until it stops changing."""
    try:
        page.locator("model-response").nth(response_index).wait_for(state="attached", timeout=15_000)
    except Exception:
        pass

    last_text = ""
    stable_count = 0
    max_wait_seconds = 180

    for _ in range(max_wait_seconds):
        current_text = _extract_nth_response(page, response_index)
        
        if current_text and current_text == last_text:
            stable_count += 1
            if stable_count >= 8:  
                return current_text
        else:
            stable_count = 0
            last_text = current_text
            
        time.sleep(1)
        
    return last_text


def send_to_gemini(page, text: str) -> str:
    """
    Paste `text` directly into the Gemini chatbox and submit.
    Returns Gemini's response string.
    """
    chatbox = page.get_by_role("textbox")
    chatbox.click()

    page.evaluate(
        """(txt) => {
            const el = document.activeElement;
            el.focus();
            document.execCommand('selectAll');
            document.execCommand('insertText', false, txt);
        }""",
        text,
    )

    placed = page.evaluate("() => document.activeElement.innerText || ''")
    if not placed.strip() and len(text) < 50_000:
        chatbox.fill(text)

    response_index = _count_responses(page)
    time.sleep(0.5) 
    page.keyboard.press("Enter")
    print("    → Waiting for Gemini response...")
    
    final_text = _wait_for_generation_to_finish(page, response_index)
    time.sleep(0.5)

    return final_text

# ════════════════════════════════════════════════════════════════════
#  FILE  DISCOVERY
# ════════════════════════════════════════════════════════════════════

def discover_files(root_dir: str, limit: int) -> list:
    """
    Walk `root_dir` and collect up to `limit` segment folders whose
    working .po file has at least one empty msgstr.
    """
    results = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames.sort()
        dirnames[:] = [d for d in dirnames if "SKIP" not in d]

        folder_name = os.path.basename(dirpath)
        if "SKIP" in folder_name:
            continue

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

        # Get missing msgctxts
        missing_ctxts_set = {e["msgctxt"] for e in work_entries if e["is_empty"]}
        if not missing_ctxts_set:
            continue

        # Use Copy.po as source for Japanese context if it exists
        if copy_path:
            try:
                _, copy_entries = parse_po(copy_path)
            except Exception as e:
                print(f"  ⚠  Could not parse Copy.po ({copy_path}): {e} — using work file")
                copy_entries = work_entries
        else:
            print(f"  ⚠  No Copy.po in {dirpath} — using working file as source")
            copy_entries = work_entries

        # Extract only the required entry dictionaries to translate
        missing_entries = [e for e in copy_entries if e["msgctxt"] in missing_ctxts_set]

        results.append({
            "work_path":       work_path,
            "copy_path":       copy_path,
            "work_raw":        work_raw,
            "missing_entries": missing_entries,
            "total_entries":   len(work_entries),
            "folder_path":     dirpath,
            "segment_id":      segment_id,
        })

        if len(results) >= limit:
            break

    return results

# ════════════════════════════════════════════════════════════════════
#  PER-FILE  PROCESSOR
# ════════════════════════════════════════════════════════════════════

def process_file(page, fi: dict):
    """
    Translates missing entries in a single .po file using batches.
    Raw Gemini responses are saved to a .txt file for debugging/review.
    """
    work_path = fi["work_path"]
    base_name = os.path.splitext(os.path.basename(work_path))[0]
    dir_path  = os.path.dirname(work_path)
    
    # Path for the debug text file
    debug_txt_path = os.path.join(dir_path, f"{base_name}_translated.txt")
    
    print(f"\n  File   : {os.path.basename(work_path)}")
    print(f"  Missing: {len(fi['missing_entries'])} / {fi['total_entries']} entries")

    if not fi["missing_entries"]:
        return

    # Initialize/Clear the debug log
    with open(debug_txt_path, "w", encoding="utf-8") as f:
        f.write(f"--- Gemini Translation Raw Output for {os.path.basename(work_path)} ---\n\n")

    batches = batch_entries(fi["missing_entries"])
    all_translations = {}

    for idx, batch in enumerate(batches):
        print(f"\n  Batch {idx + 1}/{len(batches)}  ({len(batch)} entries)")

        entries_text = "\n".join(build_po_block(e) for e in batch)
        prompt       = TRANSLATE_PROMPT_TEMPLATE.format(entries=entries_text)

        response = send_to_gemini(page, prompt)

        # Write to debug file instead of terminal
        with open(debug_txt_path, "a", encoding="utf-8") as f:
            f.write(f"==================== BATCH {idx + 1} ====================\n")
            f.write(response)
            f.write("\n\n")

        parsed = parse_translated_block(response)

        if not parsed:
            print(f"    ✗  No translations parsed (Check {os.path.basename(debug_txt_path)})")
        else:
            requested = {e["msgctxt"] for e in batch}
            valid  = {k: v for k, v in parsed.items() if k in requested}
            
            if not valid:
                print(f"    ✗  None of {len(parsed)} parsed entries matched this batch.")
            else:
                last_ctxt = batch[-1]["msgctxt"]
                if last_ctxt not in valid:
                    print(f"    ⚠  Got {len(valid)}/{len(batch)} — response may be truncated.")
                else:
                    print(f"    ✓  Got {len(valid)}/{len(batch)} translation(s).")
                all_translations.update(valid)

        if idx < len(batches) - 1:
            print(f"  ⏳ Waiting {WAIT_BETWEEN_BATCHES}s before next batch...")
            time.sleep(WAIT_BETWEEN_BATCHES)

    # ── Write back to PO file ──
    if all_translations:
        write_translations_to_po(work_path, fi["work_raw"], all_translations)
        print(f"  ✓  Saved {len(all_translations)} translations to {os.path.basename(work_path)}")
    else:
        print("  ✗  No translations to write — skipping write-back.")

    # ── Handle Folder Summary ──
    print(f"  Requesting folder name summary...")
    context_entries = fi["missing_entries"][:6]
    summary_prompt = SUMMARY_PROMPT.format(
        samples="\n".join(f"- {e['msgid']}" for e in context_entries)
    )

    summary_raw = send_to_gemini(page, summary_prompt)
    
    # Save the summary to the log file too
    with open(debug_txt_path, "a", encoding="utf-8") as f:
        f.write("==================== FOLDER SUMMARY ====================\n")
        f.write(summary_raw)
        f.write("\n\n")

    folder_label = summary_raw.strip().strip('"').replace(":", "").replace("/", "")

    if folder_label and all(ord(c) < 128 for c in folder_label) and len(folder_label) < 50:
        # Check if folder has already been renamed
        if not dir_path.endswith(f" {folder_label}"):
            new_dir_name = f"{dir_path} {folder_label}"
            try:
                os.rename(dir_path, new_dir_name)
                print(f"  ✓  Renamed folder to: ...{folder_label}")
            except Exception as e:
                print(f"  ⚠  Could not rename folder: {e}")
        else:
            print(f"  ℹ  Folder already named with summary — skipping rename.")
    else:
        print(f"  ⚠  No valid ASCII summary — folder name unchanged.")


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def pick_folder(title: str) -> str:
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title=title)
    root.destroy()
    return folder


def run(
    max_files_to_translate: int = None,
    max_lines_per_batch: int = None,
    wait_between_batches: int = None
) -> None:
    """
    Run the Gemini translator with optional parameters.
    
    Args:
        max_files_to_translate: How many .po files to process (default: 59)
        max_lines_per_batch: Max lines per Gemini request (default: 600)
        wait_between_batches: Seconds to wait between batches (default: 8)
    """
    global MAX_FILES_TO_TRANSLATE, MAX_LINES_PER_BATCH, WAIT_BETWEEN_BATCHES
    
    # Set parameters if provided, otherwise use defaults
    if max_files_to_translate is not None:
        MAX_FILES_TO_TRANSLATE = max_files_to_translate
    if max_lines_per_batch is not None:
        MAX_LINES_PER_BATCH = max_lines_per_batch
    if wait_between_batches is not None:
        WAIT_BETWEEN_BATCHES = wait_between_batches
    
    print("═" * 65)
    print("  Gemini PO Translator — Danganronpa Fan TL")
    print("═" * 65)
    print(f"\n  Settings:")
    print(f"    Max Files: {MAX_FILES_TO_TRANSLATE}")
    print(f"    Max Lines Per Batch: {MAX_LINES_PER_BATCH}")
    print(f"    Wait Between Batches: {WAIT_BETWEEN_BATCHES}s\n")

    translated_dir = pick_folder("Select your 'translated' working folder")
    if not translated_dir:
        print("No folder selected — exiting.")
        return

    print(f"\n  Folder : {translated_dir}")
    print(f"  Limit  : {MAX_FILES_TO_TRANSLATE} file(s)\n")

    print("  Scanning for missing translations...")
    to_process = discover_files(translated_dir, MAX_FILES_TO_TRANSLATE)

    if not to_process:
        print("  ✓ All .po files are fully translated!")
        return

    print(f"\n  Found {len(to_process)} file(s) to translate:\n")
    for fi in to_process:
        rel = os.path.relpath(fi["work_path"], translated_dir)
        copy_note = "(Copy.po found ✓)" if fi["copy_path"] else "(no Copy.po — using work file)"
        print(f"    {rel}  —  {len(fi['missing_entries'])} missing  {copy_note}")

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

        for fi in to_process:
            process_file(page, fi)

    msg = f"Processed {len(to_process)} file(s).\nCheck the console for details."
    print(f"\n{'═'*65}")
    print(f"  Done!  {msg}")
    print("═" * 65)


if __name__ == "__main__":
    run()