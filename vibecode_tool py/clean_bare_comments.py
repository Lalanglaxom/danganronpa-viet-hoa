import os
import tkinter as tk
from tkinter import filedialog

def pick_folder():
    root = tk.Tk(); root.withdraw()
    folder = filedialog.askdirectory(title="Select folder to fix bare #. lines")
    root.destroy()
    return folder

def fix_file(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    lines   = content.split("\n")
    fixed   = ["#. " if l.rstrip() == "#." else l for l in lines]
    count   = sum(1 for a, b in zip(lines, fixed) if a != b)
    if count:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(fixed))
    return count

if __name__ == "__main__":
    folder = pick_folder()
    if not folder:
        print("No folder selected.")
    else:
        total_files = total_lines = 0
        for dirpath, _, filenames in os.walk(folder):
            for fname in sorted(filenames):
                if not fname.endswith(".po") or "- Copy" in fname:
                    continue
                path  = os.path.join(dirpath, fname)
                count = fix_file(path)
                if count:
                    rel = os.path.relpath(path, folder)
                    print(f"  Fixed {count:3d} bare #. lines  →  {rel}")
                    total_files += 1
                    total_lines += count
        print(f"\nDone. {total_lines} line(s) fixed across {total_files} file(s).")
