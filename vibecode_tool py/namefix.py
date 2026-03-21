import os
import tkinter as tk
from tkinter import filedialog

def pick_folder():
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title="Select the folder to clean up")
    root.destroy()
    return folder

def remove_number_suffix(root_dir):
    # Process bottom-up so renaming folders doesn't break paths
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        # Rename files first
        for fname in filenames:
            new_name = fname.replace(" (1)", "")
            if new_name != fname:
                old = os.path.join(dirpath, fname)
                new = os.path.join(dirpath, new_name)
                os.rename(old, new)
                print(f"  FILE: {fname}  →  {new_name}")

        # Rename folders
        for dname in dirnames:
            new_name = dname.replace(" (1)", "")
            if new_name != dname:
                old = os.path.join(dirpath, dname)
                new = os.path.join(dirpath, new_name)
                os.rename(old, new)
                print(f"  DIR : {dname}  →  {new_name}")

if __name__ == "__main__":
    folder = pick_folder()
    if not folder:
        print("No folder selected.")
    else:
        print(f"Cleaning: {folder}\n")
        remove_number_suffix(folder)
        print("\nDone!")