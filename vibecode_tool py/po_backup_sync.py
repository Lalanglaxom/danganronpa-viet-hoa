import os
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox


def pick_folder(title):
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title=title)
    root.destroy()
    return folder


def build_lin_index(lin_dir: str) -> dict:
    """
    Walk the LIN folder recursively and build a map of:
        filename.po  →  full path to that file
    So we can find where any .po lives regardless of folder structure.
    If the same filename appears more than once, all paths are kept.
    """
    index = {}  # { "e00_001_000.po": ["D:/LIN/.../e00_001_000.po", ...] }
    for dirpath, _, filenames in os.walk(lin_dir):
        for fname in filenames:
            if fname.endswith(".po") and "- Copy" not in fname:
                index.setdefault(fname, []).append(os.path.join(dirpath, fname))
    return index


def backup_and_sync():
    print("=== PO File Backup & Sync Tool ===\n")

    print("Step 1: Select your 'translated' working folder...")
    translated_dir = pick_folder("Select the 'translated' folder")
    if not translated_dir:
        print("No folder selected. Exiting.")
        return

    print("Step 2: Select your LIN/destination folder...")
    lin_dir = pick_folder("Select the LIN destination folder")
    if not lin_dir:
        print("No LIN folder selected. Exiting.")
        return

    print(f"\nWorking folder : {translated_dir}")
    print(f"LIN folder     : {lin_dir}")
    print("\nBuilding LIN file index...")
    lin_index = build_lin_index(lin_dir)
    print(f"  Found {len(lin_index)} unique .po filename(s) in LIN.\n")

    backup_created  = 0
    backup_skipped  = 0
    lin_updated     = 0
    lin_not_found   = 0
    errors          = []

    for dirpath, dirnames, filenames in os.walk(translated_dir):
        dirnames.sort()

        folder_name = os.path.basename(dirpath)
        # segment_id = part before first space, or full name if no spaces
        # e.g. "e00_001_000 trans" -> "e00_001_000"
        # e.g. "00_System"        -> "00_System"
        segment_id  = folder_name.split()[0] if " " in folder_name else folder_name
        po_filename = f"{segment_id}.po"
        po_path     = os.path.join(dirpath, po_filename)

        if not os.path.isfile(po_path):
            continue

        rel = os.path.relpath(po_path, translated_dir)

        # ── 1. Create a backup copy inside the same folder ──────────
        copy_name = f"{segment_id} - Copy.po"
        copy_path = os.path.join(dirpath, copy_name)

        if os.path.isfile(copy_path):
            print(f"  [SKIP backup]  {rel}  (copy already exists)")
            backup_skipped += 1
        else:
            try:
                shutil.copy2(po_path, copy_path)
                print(f"  [BACKUP]       {rel}")
                backup_created += 1
            except Exception as e:
                msg = f"Could not backup {po_path}: {e}"
                print(f"  [ERROR]  {msg}")
                errors.append(msg)

        # ── 2. Find matching file(s) in LIN and overwrite them ──────
        matches = lin_index.get(po_filename, [])

        if not matches:
            print(f"  [NOT IN LIN]   {po_filename}  — no matching file found in LIN, skipping")
            lin_not_found += 1
            continue

        for lin_dest in matches:
            try:
                shutil.copy2(po_path, lin_dest)
                lin_rel = os.path.relpath(lin_dest, lin_dir)
                print(f"  [LIN UPDATE]   {lin_rel}")
                lin_updated += 1
            except Exception as e:
                msg = f"Could not copy {po_path} → {lin_dest}: {e}"
                print(f"  [ERROR]  {msg}")
                errors.append(msg)

    # ── Summary ─────────────────────────────────────────────────────
    summary = (
        f"\n{'='*40}\n"
        f"Done!\n\n"
        f"Backups created  : {backup_created}\n"
        f"Backups skipped  : {backup_skipped}  (copy already existed)\n"
        f"LIN files updated: {lin_updated}\n"
        f"Not found in LIN : {lin_not_found}\n"
    )
    if errors:
        summary += f"\nErrors ({len(errors)}):\n" + "\n".join(f"  - {e}" for e in errors)

    print(summary)

    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo("PO Backup & Sync — Done", summary)
    root.destroy()


if __name__ == "__main__":
    backup_and_sync()
