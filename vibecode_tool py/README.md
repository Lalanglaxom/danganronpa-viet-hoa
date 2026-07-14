# DR PO Toolkit — Refactored Base

This is a safer base refactor of the original loose-script toolkit.

Main change: every tool now uses one shared PO reader/writer: `src/dr_po_toolkit/po_io.py`.
That removes the old problem where each script parsed PO files differently.

## What changed

- One package: `dr_po_toolkit`
- One PO parser/writer: `po_io.py`
- Safer translation flow: Gemini returns JSON, not PO text
- Replacer rules are editable through the GUI Rule Editor
- Rule format now has priority, enabled flag, speaker filter, CLT scope, whole-word, case sensitivity
- Validator checks source drift, missing entries, duplicate contexts, CLT tags, placeholders, Unicode, whitespace
- Line wrapper only touches `msgstr`
- New GUI `Translafixer` tab copies known-good translations by matching original `msgid` text
- New GUI `PO Viewer` tab opens one `.po` file with compact English/Vietnamese editing
- `po_empty_entry_remover.py` is not part of the new workflow; it is kept only in `legacy/`

## Run GUI

From this folder:

```bash
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


## Search / Backup / Sync performance

Large folders are faster in this build because scanners now prune common cache/vendor folders such as `.git`, `node_modules`, `__pycache__`, and `.venv`. Search also does a quick file-level prefilter before fully parsing a `.po` file, so files that cannot contain the phrase are skipped early.

`Sync by Filename` now refuses nested source/target folders, skips self-copy, skips duplicate source filenames, and avoids rewriting target files that are already identical. This reduces disk writes a lot when syncing the same folder repeatedly.

In GUI Settings, each Danganronpa file group has only a Working folder. Set one shared `Extracted` folder under Other folders; `Sync Selected Options` sends every selected Working folder there by default. Existing destination files are matched by filename; new files keep their relative Working-folder path under `Extracted`.

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
5. `Wrap selected` / `Wrap all` applies the existing `msgstr` line wrapper.
6. `Translafix from sources` uses the source list from the `Translafixer` tab. Selected rows are overwritten from matching source translations; if no rows are selected, empty translations are filled only.
7. Click `Save` to write the edited `.po` file.
8. Use `Shift+Up` / `Shift+Down` to switch files. Parsed files and the suggestion index are cached, and suggestion indexing runs in the background.

## Automatic Gemini Web workflow

This keeps the old simple Gemini-web behavior, but uses the refactored PO parser/writer and validation.

It will automatically:

1. scan the selected folder for `.po` files
2. skip `Copy.po` files and `SKIP` folders
3. find entries with empty `msgstr`
4. use the working `.po` as Gemini input while leaving any `Copy.po` untouched
5. protect angle-bracket tags like `<CLT 4>` as safe tokens before pasting, so Gemini Web does not strip the PO body
6. split into smaller batches using both max lines and max entries
7. paste batches into your current Gemini web tab
8. verify the textbox really contains `msgctxt` / `msgid` / `msgstr` before pressing Send
9. wait with a real timeout and retry stuck/empty batches
10. parse Gemini's PO-code-block response and decode safe tokens back to real tags
11. validate CLT tags/placeholders
12. write safe translations back into the working `.po` file
13. save raw Gemini output to `*_translated.txt`
14. ask Gemini for a 2-3 word folder label and rename the folder only if it is not already renamed
15. optionally remove duplicate ` (1)` suffixes from files/folders

Install optional dependency:

```bash
python -m pip install playwright
```

Use the GUI `Open Chrome` button, or start Chrome manually with remote debugging, then log in to Gemini:

```bat
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeDebug"
```

Run from GUI:

```bash
python run_toolkit.py
```

Go to `Translate` → click `Run Gemini Web`.

Run from CLI:

```bash
python run_cli.py gemini-web "path/to/translated_folder" --max-files 59 --max-lines 600 --max-entries 40 --wait 8 --timeout 180 --retries 2
```

Use `--no-folder-rename` if you only want translation and no summary rename.


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

### Gemini Web prompt safety

Gemini Web can treat raw angle tags like `<CLT 4>` as HTML when pasted through automation.
To avoid blank prompts, this build sends tags as safe visible tokens like `⟦CLT 4⟧`, then decodes Gemini's response back to `<CLT 4>` before writing the `.po`.
If the textbox does not contain real PO fields (`msgctxt`, `msgid`, `msgstr`) after paste, the app stops before sending instead of submitting an empty prompt.

The translator no longer has an `Open Gemini` option. Run only connects to an already-open Gemini tab, so it will not navigate a random tab or open Gemini during translation.


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

## Rule editor

Open GUI → `Rule Editor` tab.

Rule fields:

```json
{
  "id": "makoto_toi_to_to",
  "enabled": true,
  "priority": 800,
  "speaker": "MAKOTO",
  "scope": null,
  "find": "Tôi",
  "replace": "Tớ",
  "whole_word": true,
  "case_sensitive": true,
  "stop_after": false,
  "notes": "Makoto pronoun"
}
```

Priority order:

- higher priority runs first
- character-specific rules should usually be high, e.g. `800`
- global cleanup rules should usually be low, e.g. `100`
- CLT thought-style rules can sit around `500`

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

### Copy.po safety

Gemini Web mode never translates, overwrites, edits, or renames existing `- Copy.po` files.
The `Create Copy.po if missing` option only creates a new backup when no backup exists yet.
If a `- Copy.po` already exists, it is left untouched. Gemini Web uses the working `.po` as its input source.

`Rename (1)` skips all Copy.po files, including names like `chapter - Copy (1).po`.

`Rename segment folders` can rename the containing segment folder, but it does not edit the files inside it.
`Open Chrome` starts a separate Chrome debug profile and opens Gemini. `Run Gemini Web` only connects to an already-open Gemini tab; it never opens or navigates tabs during translation.


### Stop Current Action

The GUI has a global `Stop Current Action` button above all tabs. It requests a safe cooperative stop for the active task.
Gemini Web checks the stop request between files, between batches, during wait timers, and while waiting for Gemini to finish. If Gemini is generating, the tool tries to click Gemini's Stop button before exiting.

### Restore working PO from Copy.po

GUI: `Backup / Sync` tab → add one or more `Restore folders` → click `Restore Working PO from Copy.po`.

The restore tool scans every selected folder recursively and overwrites each working `.po` with clean content copied from its matching `- Copy.po` file. It never modifies, deletes, renames, or overwrites the `- Copy.po` files.

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

In **Settings**, select the cloned `danganronpa-viet-hoa` repository under **danganviethoa folder**. **Git Pull** opens Command Prompt in that folder and runs remote/status checks followed by `git pull`. **Git Push** asks for a commit message, then opens Command Prompt and runs `git add -A`, shows the staged file summary, commits, and pushes. The configured repository is:

`https://github.com/Lalanglaxom/danganronpa-viet-hoa.git`
