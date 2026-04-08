import os
import re

# ════════════════════════════════════════════════════════════════════
#  PO PARSER
# ════════════════════════════════════════════════════════════════════

Q = r'"(?:[^"\\]|\\.)*"'   # one PO quoted string, handles \" and \\


def _decode(raw_block: str) -> str:
    """
    Decode PO quoted content into normal Python text.
    - joins multiline quoted parts
    - converts \\n -> real newline
    - unescapes \\" and \\\\
    """
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', raw_block)
    return (
        "".join(parts)
        .replace("\\n", "\n")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _normalize_for_search(text: str) -> str:
    """
    Normalize text for search only.

    Ignores:
    - newlines
    - <CLT ...> tags
    - square brackets [ ]
    - double quotes "

    Also collapses repeated whitespace.
    """
    if not text:
        return ""

    # Turn newlines into spaces
    text = text.replace("\n", " ")

    # Remove CLT tags like <CLT 3>, <CLT 12>, etc.
    text = re.sub(r"<CLT\b[^>]*>", " ", text, flags=re.IGNORECASE)

    # Ignore square brackets and double quotes
    text = text.replace("[", " ")
    text = text.replace("]", " ")
    text = text.replace('"', " ")

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def _match_text(hay: str, needle: str, case_sensitive: bool, whole_word: bool) -> bool:
    """
    Match needle inside hay.

    whole_word=False:
        normal substring search

    whole_word=True:
        require word boundaries around the full phrase
    """
    if not hay or not needle:
        return False

    if not whole_word:
        return needle in hay

    pattern = r"(?<!\w)" + re.escape(needle) + r"(?!\w)"
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.search(pattern, hay, flags) is not None


def parse_po(filepath: str) -> list:
    """
    Return a list of:
        {
            "msgctxt": str,
            "msgid": str,
            "msgstr": str,
        }
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return []

    pattern = re.compile(
        r"(?:#[^\n]*\n)*"
        r"msgctxt\s+" + Q + r"\n"
        r"msgid\s+(?:" + Q + r"\n?)+"
        r"msgstr\s+(?:" + Q + r"\n?)*",
        re.MULTILINE,
    )

    entries = []
    for m in pattern.finditer(raw):
        block = m.group(0)

        ctx_m = re.search(r"msgctxt\s+(" + Q + r")", block)
        id_m = re.search(r"(msgid\s+(?:" + Q + r"\n?)+)", block)
        str_m = re.search(r"(msgstr\s+(?:" + Q + r"\n?)*)", block)

        if not ctx_m or not id_m:
            continue

        entries.append({
            "msgctxt": _decode(ctx_m.group(1)),
            "msgid": _decode(id_m.group(1)),
            "msgstr": _decode(str_m.group(1)) if str_m else "",
        })

    return entries


# ════════════════════════════════════════════════════════════════════
#  SEARCH
# ════════════════════════════════════════════════════════════════════

def search_all(
    root_dir: str,
    phrase: str,
    case_sensitive: bool,
    search_msgid: bool,
    search_msgstr: bool,
    whole_word: bool = False,
) -> list:
    """
    Walk root_dir and search every .po file (skipping - Copy.po).

    Search behavior:
    - ignores newlines
    - ignores <CLT ...> tags
    - ignores [ ] and "
    - optional whole-word matching

    Returns:
        [
            {
                "file": str,
                "msgctxt": str,
                "msgid": str,
                "msgstr": str,
                "hit_id": bool,
                "hit_str": bool,
            },
            ...
        ]
    """
    if not phrase:
        return []

    normalized_phrase = _normalize_for_search(phrase)
    if not normalized_phrase:
        return []

    needle = normalized_phrase if case_sensitive else normalized_phrase.lower()
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
            entries = parse_po(filepath)

            for entry in entries:
                msgid = entry["msgid"]
                msgstr = entry["msgstr"]

                hit_id = False
                hit_str = False

                if search_msgid:
                    hay = _normalize_for_search(msgid)
                    hay = hay if case_sensitive else hay.lower()
                    hit_id = _match_text(hay, needle, case_sensitive, whole_word)

                if search_msgstr:
                    hay = _normalize_for_search(msgstr)
                    hay = hay if case_sensitive else hay.lower()
                    hit_str = _match_text(hay, needle, case_sensitive, whole_word)

                if hit_id or hit_str:
                    results.append({
                        "file": rel_path,
                        "msgctxt": entry["msgctxt"],
                        "msgid": msgid,
                        "msgstr": msgstr,
                        "hit_id": hit_id,
                        "hit_str": hit_str,
                    })

    return results