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


def run_backup(translated_dir):
    """Chỉ thực hiện sao lưu các file .po hiện tại thành file - Copy.po"""
    if not translated_dir: return "⚠ Chưa chọn thư mục!"
    
    count = 0
    for dirpath, _, filenames in os.walk(translated_dir):
        for fname in filenames:
            if fname.endswith(".po") and "- Copy" not in fname:
                src = os.path.join(dirpath, fname)
                dst = src.replace(".po", "- Copy.po")
                shutil.copy2(src, dst)
                count += 1
    return f"✅ Đã tạo {count} bản sao lưu (- Copy.po) thành công!"

def run_sync(translated_dir, lin_dir):
    """Chỉ thực hiện đồng bộ từ 'translated' sang 'LIN'"""
    if not translated_dir or not lin_dir: return "⚠ Thiếu thư mục nguồn hoặc đích!"
    
    lin_index = build_lin_index(lin_dir)
    updated = 0
    
    for dirpath, _, filenames in os.walk(translated_dir):
        for fname in filenames:
            if fname.endswith(".po") and "- Copy" not in fname:
                if fname in lin_index:
                    src = os.path.join(dirpath, fname)
                    for dest_path in lin_index[fname]:
                        shutil.copy2(src, dest_path)
                        updated += 1
    
    return f"✅ Đã đồng bộ {updated} file sang thư mục LIN!"

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

    translated_segments = set()  # track every segment_id found in translated_dir

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

        translated_segments.add(po_filename)  # e.g. "e01_038_156.po"
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

    # ── Folder structure check ──────────────────────────────────────
    lin_only   = sorted(set(lin_index) - translated_segments)   # in LIN but missing from translated
    trans_only = sorted(translated_segments - set(lin_index))   # in translated but not in LIN

    print("\n" + "─" * 40)
    print("FOLDER STRUCTURE CHECK")
    print("─" * 40)
    if not lin_only and not trans_only:
        print("  ✓ Both folders contain the same set of .po files.")
    if lin_only:
        print(f"\n  MISSING from your translated folder ({len(lin_only)}) — present in LIN:")
        for fname in lin_only:
            print(f"    - {fname}")
    if trans_only:
        print(f"\n  EXTRA in your translated folder ({len(trans_only)}) — not in LIN:")
        for fname in trans_only:
            print(f"    + {fname}")

    # ── Summary ─────────────────────────────────────────────────────
    summary = (
        f"\n{'='*40}\n"
        f"Done!\n\n"
        f"Backups created  : {backup_created}\n"
        f"Backups skipped  : {backup_skipped}  (copy already existed)\n"
        f"LIN files updated: {lin_updated}\n"
        f"Not found in LIN : {lin_not_found}\n"
        f"\nFolder check:\n"
        f"  Missing from translated : {len(lin_only)}\n"
        f"  Extra in translated     : {len(trans_only)}\n"
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
