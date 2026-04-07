import os, shutil

def run_backup(td):
    if not td: return "⚠ No folder!"
    c = 0
    for d, _, fs in os.walk(td):
        for f in fs:
            if f.endswith(".po") and "- Copy" not in f:
                shutil.copy2(os.path.join(d, f), os.path.join(d, f.replace(".po", " - Copy.po")))
                c += 1
    return f"✅ Backup {c} files!"

def run_sync(td, ld):
    if not td or not ld: return "⚠ Need both folders!"
    idx = {}
    for d, _, fs in os.walk(ld):
        for f in fs:
            if f.endswith(".po") and "- Copy" not in f:
                idx.setdefault(f, []).append(os.path.join(d, f))
    c = 0
    for d, _, fs in os.walk(td):
        for f in fs:
            if f.endswith(".po") and "- Copy" not in f and f in idx:
                for p in idx[f]:
                    shutil.copy2(os.path.join(d, f), p)
                    c += 1
    return f"✅ Sync {c} files!"
