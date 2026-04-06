import os
import re
import subprocess
import sys

# ════════════════════════════════════════════════════════════════════
#  PO PARSER  (same escaped-quote-safe regex as the translator)
# ════════════════════════════════════════════════════════════════════

Q = r'"(?:[^"\\]|\\.)*"'   # one PO quoted string, handles \" and \\

def _decode(raw_block: str) -> str:
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', raw_block)
    return "".join(parts).replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")

def parse_po(filepath: str) -> list:
    """Return list of {msgctxt, msgid, msgstr} dicts."""
    try:
        with open(filepath, encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return []

    pattern = re.compile(
        r"(?:#[^\n]*\n)*"
        r'msgctxt\s+' + Q + r'\n'
        r'msgid\s+(?:' + Q + r'\n?)+'
        r'msgstr\s+(?:' + Q + r'\n?)*',
        re.MULTILINE,
    )

    entries = []
    for m in pattern.finditer(raw):
        block   = m.group(0)
        ctx_m   = re.search(r'msgctxt\s+(' + Q + r')', block)
        id_m    = re.search(r'(msgid\s+(?:' + Q + r'\n?)+)', block)
        str_m   = re.search(r'(msgstr\s+(?:' + Q + r'\n?)*)', block)
        if not ctx_m or not id_m:
            continue
        entries.append({
            "msgctxt": _decode(ctx_m.group(1)),
            "msgid":   _decode(id_m.group(1)),
            "msgstr":  _decode(str_m.group(1)) if str_m else "",
        })
    return entries


def search_all(root_dir: str, phrase: str, case_sensitive: bool,
               search_msgid: bool, search_msgstr: bool) -> list:
    """
    Walk root_dir, search every .po file (skipping - Copy.po).
    Returns list of result dicts.
    """
    if not phrase:
        return []

    needle = phrase if case_sensitive else phrase.lower()
    results = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames.sort()
        for fname in sorted(filenames):
            if not fname.endswith(".po"):
                continue
            if "- Copy" in fname or "- copy" in fname:
                continue

            filepath = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(filepath, root_dir)
            entries  = parse_po(filepath)

            for entry in entries:
                msgid  = entry["msgid"]
                msgstr = entry["msgstr"]

                hit_id  = False
                hit_str = False

                if search_msgid:
                    hay = msgid if case_sensitive else msgid.lower()
                    hit_id = needle in hay

                if search_msgstr:
                    hay = msgstr if case_sensitive else msgstr.lower()
                    hit_str = needle in hay

                if hit_id or hit_str:
                    results.append({
                        "file":    rel_path,
                        "msgctxt": entry["msgctxt"],
                        "msgid":   msgid,
                        "msgstr":  msgstr,
                        "hit_id":  hit_id,
                        "hit_str": hit_str,
                    })

    return results