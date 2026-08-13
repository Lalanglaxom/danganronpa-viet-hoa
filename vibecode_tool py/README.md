# DR PO Toolkit — Refactored Base

This is a safer base refactor of the original loose-script toolkit.

Main change: every tool now uses one shared PO reader/writer: `src/dr_po_toolkit/po_io.py`.
That removes the old problem where each script parsed PO files differently.

## What changed

- One package: `dr_po_toolkit`
- One PO parser/writer: `po_io.py`
- Safer translation flow: Gemini returns JSON, not PO text
- Replacement rules and Mass Replace are combined in the GUI `Rules & Replace` tab
- Rule list order is weak-to-strong from top to bottom, with drag-and-drop reordering and no stored ID/priority fields
- One rule supports multiple ordered find/replace pairs separated by `;` (`\;` inserts a literal semicolon)
- Validator checks source drift, missing entries, duplicate contexts, CLT tags, placeholders, Unicode, whitespace
- Line wrapper only touches `msgstr`
- New GUI `Translafixer` tab copies known-good translations by matching original `msgid` text
- New GUI `PO Viewer` tab opens one `.po` file with compact English/Vietnamese editing
- `po_empty_entry_remover.py` is not part of the new workflow; it is kept only in `legacy/`

## Run GUI

From this folder:

```bash
python -m pip install -r requirements.txt
python run_toolkit.py
```

If the package import fails, install editable mode once:

```bash
python -m pip install -e .
python run_toolkit.py
```

## Run CLI

```bash
python run_cli.py validate "path/to/folder" --reports "path/to/folder"
python run_cli.py replace "path/to/folder" --rules rules/mass_replace_rules.json --dry-run
python run_cli.py linewrap "path/to/folder" --dry-run
python run_cli.py search "path/to/folder" "Taka"
python run_cli.py search "path/to/folder" "Taka | Kyoko & goodbye" --raw
python run_cli.py backup "path/to/folder"  # creates missing Copy.po only
```

Search syntax uses `|` for OR and `&` for AND; AND is evaluated first. Use `\|` or `\&` for literal operator characters. The GUI and CLI `Raw`/`--raw` option searches original parsed PO text without removing CLT tags, brackets, quotes, or line breaks. Semicolons are ordinary searchable text.

After editable install:

```bash
dr-po validate "path/to/folder"
dr-po replace "path/to/folder" --rules rules/mass_replace_rules.json --dry-run
```


## Task progress

The GUI shows shared progress text in the top toolbar for every long-running action: Validate, Mass Replace, Line Wrap, Search, Translafixer, PO Viewer load/save/wrap/fill/replace/AI tasks, duplicate scans and batch edits, Gemini/ChatGPT Web, Gemini API, manual filename sync, and the complete Repack pipeline. File- or entry-based work shows current/total counts and percentages without a filled bar or animated texture. Search keeps its detailed local progress text and mirrors the same state in the shared indicator.

## Search / Sync / Repack performance

Large folders are faster in this build because scanners prune common cache/vendor folders such as `.git`, `node_modules`, `__pycache__`, and `.venv`. Search, duplicate/diff review, Translafixer maps, and PO Viewer suggestions now share one bounded incremental text/PO index. Decoded fields, parsed entries, visible/folded search forms, suggestion corpora, and duplicate results are reused across screens. Larger searches run in parallel and reject impossible files before entry parsing. Cache entries invalidate automatically after toolkit saves and whenever file time, change time, or size changes. The GUI now warms every configured Working folder automatically in a background thread immediately after the app opens; later searches, diff dialogs, and suggestion rebuilds over unchanged files are normally immediate.

`Sync by Filename` now refuses nested source/target folders, skips self-copy, skips duplicate source filenames, and avoids rewriting target files that are already identical. This reduces disk writes a lot when syncing the same folder repeatedly.

In GUI Settings, set each Danganronpa file group's `Working` folder, plus `DRAT Folder`, `Script`, and `Game Folder`. `DRAT Folder` may point directly to a game manual-mode folder such as `DR1 (PC) [MANUAL MODE]`, or to its parent DRAT installation folder.

The `Repack` button runs one complete build:

1. sync selected Working `.po` files into DRAT `EXTRACTED` by filename
2. fingerprint LIN/PAK source folders and rebuild only changed jobs
3. resolve generated files to matching `Script` filenames without copying them yet
4. rebuild only changed WADs, reading generated LIN/PAK bytes through a virtual overlay
5. after every repack and filename check succeeds, deploy Script and Game files together

Incremental state is stored in `REPACKED/.drat_repack_cache.json`. Existing outputs are verified before reuse, identical rebuilt outputs are kept without rewriting, and changed outputs are staged before replacement. The pipeline stops without deploying anything when a selected Working file has no DRAT target, a generated filename is ambiguous, a Script/Game target is missing, or any repack fails. `Sync by Filename` remains as a separate manual action.

## Translafixer GUI tab

Use `Translafixer` when you have known-good `.po` translations and another folder needs to be fixed.

1. Drag multiple correct source `.po` files or folders into the source list, or click `Add .po files` / `Add folder`. Dropped folders are expanded recursively into `.po` files.
2. Choose the `Target folder` to fix.
3. Run with `Dry run` checked to preview.
4. Uncheck `Dry run` to rewrite target `msgstr` values.

Matching uses original text / `msgid`; CLT tags such as `<CLT 4>` and `<CLT>` are ignored while comparing, so tagged and untagged originals can match. `Copy.po` target files are skipped. Dropped source folders skip their own `Copy.po` files, while explicitly selected `Copy.po` files are allowed. Selected source files are also skipped during the target scan, so they are not rewritten even if they live inside the target folder. If source files contain the same `msgid` with different translations, that source text is treated as ambiguous and skipped instead of writing a possibly wrong translation. When writing, the tab can create `*.po.translafixer.bak` backups before changing files.



## Validator report links

Validator HTML reports include an **Open in app** link on every issue. On Windows, the toolkit registers the per-user `drpo://` protocol automatically. Clicking a report link opens the matching `.po` file and selects the entry by context and line. When the toolkit is already running, the link is forwarded to that window instead of leaving the entry in a separate app instance.

## PO Viewer GUI tab

Use `PO Viewer` for quick manual edits in one `.po` file.

1. Choose a `.po` file and click `Load`.
2. The table shows every entry with English `msgid` and Vietnamese `msgstr`.
3. English/original text is read-only. Edit only the Vietnamese side, either in the table or in the bottom Vietnamese editor.
4. `Visual wrap` toggles display wrapping without changing the file.
5. Four compact, editable wrap presets are available in Search, PO Viewer, and duplicate/diff views. Each Wrap button shows only its soft-cut value. Preset 1 starts at the base-64 values (`58/64`, 2 cuts), and all four presets can be edited in the Line Wrap tab. `All` wraps the whole current file with the active preset.
6. `Preset Replace` applies every enabled rule from `Rules & Replace` to the selected rows, or the current row when nothing is selected. Search saves immediately; PO Viewer and duplicate/diff views keep their normal undo/save flow.
   In `Rules & Replace`, named rules show a compact purpose + pair-count label for easier scanning. `Create Rule` inserts a new rule directly below the current rule (or at the end when nothing is selected).
7. `Translafix from sources` uses the source list from the `Translafixer` tab. Selected rows are overwritten from matching source translations; if no rows are selected, empty translations are filled only.
8. Red numbered `\n[1]`, `\n[2]` markers and the EN/VI line-count label show real line breaks. Search also shows live visible-character counts for each English and Vietnamese line. CLT color view preserves repeated spaces around hidden tags so double spaces remain visible.
9. Click `Save` or press `Ctrl+S` to write the edited `.po` file. Search also supports `Ctrl+S` for the current result's file.
10. Click `Shortcuts` beside Settings to assign wrap and previous/next-file actions. Assignments may be a three-key chord such as `Ctrl+Shift+1`, or a sequence of up to three chords. Direct one-chord wrap keys remain repeatable while their modifier is held. Reset restores `Shift+1/2/3/4`, `Shift+Return`, `Alt+Up`, and `Alt+Down`. Hold `Ctrl` and press `1/2/3` repeatedly to apply PO Viewer suggestions; `Alt+0` refreshes them. `Ctrl+R` applies preset replacements, and `Ctrl+G` runs Gemini translation. Parsed files and the suggestion index are cached, and suggestion indexing runs in the background.

## Automatic Gemini or ChatGPT Web workflow

The `AI Translation` tab keeps the existing Gemini Web workflow and adds a `Use ChatGPT Web instead of Gemini` toggle. The toggle affects Web mode only; Gemini API mode always remains Gemini.

It will automatically:

1. scan the selected folder for `.po` files
2. skip `Copy.po` files and `SKIP` folders
3. find entries with empty `msgstr`
4. use the working `.po` as web-model input while leaving any `Copy.po` untouched
5. protect angle-bracket tags like `<CLT 4>` as safe tokens before pasting, so the web editor does not strip the PO body
6. split into smaller batches using both max lines and max entries
7. paste batches into your current Gemini or ChatGPT tab
8. verify the textbox really contains `msgctxt` / `msgid` / `msgstr` before pressing Send
9. wait with a real timeout and retry stuck/empty batches
10. parse the PO-code-block response and decode safe tokens back to real tags
11. validate CLT tags/placeholders
12. write safe translations back into the working `.po` file
13. save raw Gemini output to `*_translated.txt`
14. ask the selected web provider for a 2-3 word folder label and rename the folder only if it is not already renamed
15. optionally remove duplicate ` (1)` suffixes from files/folders

Install optional dependency:

```bash
python -m pip install playwright
```

In `AI Translation`, select `Web tab`, leave the ChatGPT toggle off for Gemini or turn it on for ChatGPT, then use `Open Chrome`. You may also start Chrome manually with remote debugging and open the selected provider:

```bat
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeDebug"
```

Run from GUI:

```bash
python run_toolkit.py
```

Go to `AI Translation` and click `Run Gemini Web` or `Run ChatGPT Web`.

Run from CLI:

```bash
python run_cli.py gemini-web "path/to/translated_folder" --max-files 59 --max-lines 600 --max-entries 40 --wait 8 --timeout 180 --retries 2
```

Use `--no-folder-rename` if you only want translation and no summary rename.

The CLI command remains Gemini Web only. The provider toggle is currently in the GUI.


### Gemini Web anti-stuck safeguards

This build limits each Gemini batch to `Max entries` as well as `Max lines`.
Default is 40 entries per batch, because very large batches like 100+ entries can make Gemini Web stall or return partial DOM output.

If Chrome/Gemini is interrupted while generating, the app now:

- uses a wall-clock response timeout instead of waiting forever
- detects when no new response appears
- detects when response text stops changing for too long
- tries to click Gemini's Stop button
- clears the input box
- retries the same batch using the `Retries` value
- writes attempt errors into `*_translated.txt` for debugging

If all retries fail, it stops with a clear batch error instead of hanging silently.

### ChatGPT Web duplicate-send safeguards

ChatGPT Web uses the reference ProseMirror/CDP automation path. It never navigates or reloads the active ChatGPT tab. Once ChatGPT accepts a prompt, the toolkit does not resend it after a response-monitoring or parsing failure. Raw output is saved to `*_translated.txt` so the failure can be inspected safely.

### Gemini Web prompt safety

Gemini Web can treat raw angle tags like `<CLT 4>` as HTML when pasted through automation.
To avoid blank prompts, this build sends tags as safe visible tokens like `⟦CLT 4⟧`, then decodes Gemini's response back to `<CLT 4>` before writing the `.po`.
If the textbox does not contain real PO fields (`msgctxt`, `msgid`, `msgstr`) after paste, the app stops before sending instead of submitting an empty prompt.

The run action only connects to an already-open matching provider tab. `Open Chrome` is a separate explicit setup action, so translation itself does not navigate a random tab or open a provider page.


Backup safety: `backup` now creates missing `Copy.po` files only. Existing `Copy.po` files are not overwritten unless you explicitly run:

```bash
python run_cli.py backup "path/to/folder" --overwrite
```

## Gemini JSON workflow

Do **not** ask Gemini to write PO files.

Use manual job mode:

```bash
python run_cli.py make-jobs "path/to/folder_or_file" jobs --batch-size 20 --max-files 3
```

This writes pairs of files:

- `*.request.json` — structured request data
- `*.prompt.txt` — prompt to paste into Gemini

Gemini should return:

```json
{
  "entries": [
    {
      "uid": "00001|0001 | MAKOTO NAEGI",
      "translation": "<CLT 4>Đây là bản dịch.\n<CLT>"
    }
  ]
}
```

Apply response:

```bash
python run_cli.py apply-response "path/to/file.po" "path/to/response.json"
```

The toolkit refuses to write translations if validation fails, unless you pass:

```bash
--allow-partial
```

## Rules & Replace

Open GUI → `Rules & Replace`. The upper panel edits the same ordered rules used by the lower Mass Replace runner and by every `Preset Replace` button.

Rule fields:

```json
{
  "enabled": true,
  "speaker": "MAKOTO",
  "scope": "",
  "find": "Tôi;tôi",
  "replace": "Tớ;tớ",
  "whole_word": true,
  "case_sensitive": true,
  "stop_after": false,
  "notes": "Makoto pronouns"
}
```

Rules run from top to bottom. The top is weakest; lower rules run later and can override earlier results. Drag rows to change strength. Existing version-2 files are migrated automatically from numeric priority into this ordered version-3 format.

Use semicolons for multiple ordered pairs in one rule. Missing replacement pieces reuse the final replacement; use `\;` for a literal semicolon.

## Recommended workflow

```text
1. backup: create missing Copy.po only
2. make-jobs: export untranslated entries to JSON prompts
3. translate with Gemini JSON
4. apply-response
5. replace: dry-run first, then apply
6. linewrap: dry-run first, then apply
7. validate
8. test in game
```

## Files kept from original

Original scripts are in `legacy/` for reference only.
Use the refactored modules instead.

## Notes

The base toolkit uses only the Python standard library.
Direct Gemini API mode is optional and requires:

```bash
pip install google-genai
```

The AI Translation tab has two independent Gemini API profiles:

- **Single-entry API** is used by AI buttons in PO Viewer, Search, and
  duplicate/diff views. Each selected entry is one request. Its default context
  is 3 previous English/Vietnamese entries and its default inter-request delay
  is 0 seconds.
- **Mass-translation API** is used only by `Run Gemini API`. It sends 40 current
  entries per request by default. Up to 3 prior translated entries are sent once
  before each batch, rather than repeating the same context for every entry.

Both profiles have separate model, timeout, context, cross-file context, delay,
thinking, and maximum-output settings. The API key is shared. Set previous
context to 0 to disable continuity. `Include previous files` is opt-in for each
profile.

Interactive Gemini API requests run on background workers, so Search, PO Viewer,
and duplicate/diff views remain responsive while an entry is translating. Each
request timeout defaults to 90 seconds. A stalled request ends with a
visible error instead of blocking the interface; Stop is checked between
requests/batches and during configured delays.

Gemini API translation treats the current English `source_en` as the absolute
source of truth. Extracted Japanese comments are cleaned and sent only as a
compact ambiguity hint; the prompt explicitly forbids Japanese from overriding,
adding to, shortening, or changing the English. Previous English/Vietnamese
entries remain continuity context only. Direct API requests use minified
order-based JSON plus a structured-output schema requiring `{"t":[...]}` with
the same item count. Thinking defaults to off for Gemini 2.5 and the lowest
supported level for Gemini 3 (`minimal` for Flash/Lite, `low` for Pro).
`Max output tokens: Auto` adds a source-sized
safety cap. The GUI reports request, input, output, thinking, cached, and total
token usage returned by the API. Legacy UID-based JSON remains supported for
exported manual jobs.

Search, PO Viewer, and duplicate/diff views keep up to 500 unified undo actions.
`Ctrl+Z` is routed through that history before the focused editor consumes it, so
normal typing undo still works and saved actions such as line wrapping, suggestion
apply, find/replace, preset replacement, Translafixer fill, and Gemini translation
can also be reversed. Toolbar Undo controls use the same history.

### Copy.po safety

Gemini and ChatGPT Web modes never translate, overwrite, edit, or rename existing `- Copy.po` files.
The `Create Copy.po if missing` option only creates a new backup when no backup exists yet.
If a `- Copy.po` already exists, it is left untouched. Web translation uses the working `.po` as its input source.

`Rename (1)` skips all Copy.po files, including names like `chapter - Copy (1).po`.

`Rename segment folders` can rename the containing segment folder, but it does not edit the files inside it.
`Open Chrome` starts a separate Chrome debug profile and opens the selected web provider. The run button connects only to an already-open matching tab. Gemini recovery may refresh a stuck Gemini page; ChatGPT recovery never navigates or reloads the active ChatGPT page.


### Stop Current Action

The GUI has a global `Stop Current Action` button above all tabs. It requests a safe cooperative stop for the active task.
Web translation checks the stop request between files, between batches, during wait timers, and while waiting for the selected provider to finish. If generation is active, the tool tries to click the provider's Stop button before exiting.

### Restore working PO from Copy.po

The restore command scans every selected folder recursively and overwrites each working `.po` with clean content copied from its matching `- Copy.po` file. It never modifies, deletes, renames, or overwrites the `- Copy.po` files.

CLI:

```bash
python run_cli.py restore-from-copy "folder1" "folder2"
```

Preview only:

```bash
python run_cli.py restore-from-copy "folder1" "folder2" --dry-run
```


## Windows launch notes

If `run_toolkit.vbs` does not open the app after the PyQt UI update, PyQt6 is usually missing. Run `install_requirements.bat`, or run `run_toolkit_debug.bat` to see the error in a console. Startup errors are also written to `toolkit_launch_error.log`.

## Danganronpa Việt Hóa Git controls

In **Settings**, select the cloned `danganronpa-viet-hoa` repository under **danganviethoa folder**. **Git Pull** opens Command Prompt in that folder and runs `git fetch` followed by `git pull --rebase --autostash`, which temporarily protects uncommitted edits and reapplies them after updating.

**Git Push** asks for a commit message and opens a real Command Prompt window so Git runs directly in front of the user. The window reports four concise steps—scanning files, checking staged changes, creating the commit, and uploading to the remote—without listing every staged filename in the app UI.

The push flow is:

```cmd
git add .
git diff --cached --quiet
git commit --quiet -F <temporary-message-file>
git push origin main
```

`git commit -F` is used instead of embedding the message in the command line, which safely supports quotes and special characters. `--quiet` suppresses Git's per-file commit summary. All repository folders, including e04, e05, and e06, remain included by `git add .` unless excluded by the repository's `.gitignore` rules.

The configured repository is:

`https://github.com/Lalanglaxom/danganronpa-viet-hoa.git`

### Keep local work while updating from GitHub

Preferred flow:

```cmd
git status
git add -A
git commit -m "Save local work"
git pull --rebase
# Resolve conflicts if Git reports any, then:
git add <resolved-files>
git rebase --continue
git push
```

For uncommitted edits, the app's **Git Pull** button uses `--autostash`. For the first cleanup after adding `.gitignore`, stop tracking already committed cache files without deleting local copies:

```cmd
git rm -r --cached .
git add .
git commit -m "Stop tracking ignored files"
git push
```

## Third-party code

The DRAT-compatible LIN/PAK/WAD repacking implementation is based on the
MIT-licensed Danganronpa Another Tool. See `THIRD_PARTY_NOTICES.md`.
