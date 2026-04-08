"""
po_mass_replace.py — Logic module for mass replacements in .po files.
Rules are loaded from mass_replace_rules.json.

Matching logic ignores:
- <CLT ...> tags
- [ and ]
- double quotes "
- newline / whitespace differences

Supports:
- character filter
- optional scope = "clt:N"
- whole_word matching
"""

import os
import re
import json

RULES_FILE = "mass_replace_rules.json"

Q = r'"(?:[^"\\]|\\.)*"'   # one PO quoted string, handles \" and \\

CLT_TAG_RE = re.compile(r"<CLT\b[^>]*>", re.IGNORECASE)
ENTRY_RE = re.compile(
    r"(?:#[^\n]*\n)*"
    r'msgctxt\s+' + Q + r'\n'
    r'msgid\s+(?:' + Q + r'\n?)+'
    r'msgstr\s+(?:' + Q + r'\n?)*',
    re.MULTILINE,
)

IGNORED_SINGLE_CHARS = {'[', ']', '"'}


# ════════════════════════════════════════════════════════════════════
#  RULE LOADING
# ════════════════════════════════════════════════════════════════════

def load_rules():
    """Loads criteria from JSON or creates a default one if missing."""
    if not os.path.exists(RULES_FILE):
        default_rules = [
            {
                "label": "Example: Change Hope's Peak to Đỉnh Hy Vọng",
                "character": None,
                "scope": None,
                "whole_word": True,
                "replace": [["Hope's Peak", "Đỉnh Hy Vọng"]]
            }
        ]
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(default_rules, f, indent=4, ensure_ascii=False)
        return default_rules

    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"✗ Error loading JSON rules: {e}")
        return []


# The Toolkit reads this variable to build the checkboxes
CRITERIA = load_rules()


# ════════════════════════════════════════════════════════════════════
#  PO STRING HELPERS
# ════════════════════════════════════════════════════════════════════

def _decode(raw_block: str) -> str:
    """Decode PO quoted content into normal Python text."""
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', raw_block)
    return (
        "".join(parts)
        .replace("\\n", "\n")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _encode_po_string(text: str) -> str:
    """Encode Python text as a single PO quoted string."""
    escaped = (
        text
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def _extract_entry_parts(block: str):
    """Extract decoded msgctxt, msgid, msgstr from one PO entry block."""
    ctx_m = re.search(r'msgctxt\s+(' + Q + r')', block)
    id_m = re.search(r'(msgid\s+(?:' + Q + r'\n?)+)', block)
    str_m = re.search(r'(msgstr\s+(?:' + Q + r'\n?)*)', block)

    if not ctx_m or not id_m:
        return None

    return {
        "msgctxt": _decode(ctx_m.group(1)),
        "msgid": _decode(id_m.group(1)),
        "msgstr": _decode(str_m.group(1)) if str_m else "",
    }


def _replace_msgstr_in_block(block: str, new_msgstr: str) -> str:
    """Replace only the msgstr portion inside a PO entry block."""
    encoded = _encode_po_string(new_msgstr)
    return re.sub(
        r'msgstr\s+(?:' + Q + r'\n?)*',
        f"msgstr {encoded}",
        block,
        count=1,
    )


# ════════════════════════════════════════════════════════════════════
#  NORMALIZED MATCH / REPLACE LOGIC
# ════════════════════════════════════════════════════════════════════

def _build_search_view(text: str):
    """
    Build a normalized visible-text view plus char-to-original mapping.

    Ignores:
    - CLT tags
    - [ ]
    - "
    - repeated whitespace / newlines (collapsed to single spaces)

    Returns:
        normalized_text, spans
    where spans[i] = (orig_start, orig_end) for normalized_text[i]
    """
    chars = []
    spans = []

    i = 0
    n = len(text)

    while i < n:
        tag_m = CLT_TAG_RE.match(text, i)
        if tag_m:
            i = tag_m.end()
            continue

        ch = text[i]

        if ch in IGNORED_SINGLE_CHARS:
            i += 1
            continue

        if ch.isspace():
            j = i + 1
            while j < n and text[j].isspace():
                j += 1

            if chars and chars[-1] != " ":
                chars.append(" ")
                spans.append((i, j))

            i = j
            continue

        chars.append(ch)
        spans.append((i, i + 1))
        i += 1

    # Trim leading/trailing spaces in normalized view
    while chars and chars[0] == " ":
        chars.pop(0)
        spans.pop(0)
    while chars and chars[-1] == " ":
        chars.pop()
        spans.pop()

    return "".join(chars), spans


def _normalize_phrase(text: str) -> str:
    """Normalize a search phrase using the same visible-text logic."""
    normalized, _ = _build_search_view(text)
    return normalized


def _find_match_spans(hay: str, needle: str, whole_word: bool):
    """
    Return list of (start, end) spans in normalized hay.
    Case-sensitive by design, matching previous replace behavior.
    """
    if not hay or not needle:
        return []

    if whole_word:
        pattern = r"(?<!\w)" + re.escape(needle) + r"(?!\w)"
    else:
        pattern = re.escape(needle)

    return [m.span() for m in re.finditer(pattern, hay)]


def _replace_in_visible_text(text: str, find_str: str, repl_str: str, whole_word=False):
    """
    Replace find_str in text using normalized visible-text matching.

    Matching ignores:
    - CLT tags
    - [ ]
    - "
    - whitespace/newline differences

    Returns:
        (new_text, replacement_count)
    """
    normalized_text, spans = _build_search_view(text)
    normalized_find = _normalize_phrase(find_str)

    if not normalized_text or not normalized_find:
        return text, 0

    match_spans = _find_match_spans(normalized_text, normalized_find, whole_word)
    if not match_spans:
        return text, 0

    # Convert normalized spans back to original text spans
    original_ranges = []
    for a, b in match_spans:
        orig_start = spans[a][0]
        orig_end = spans[b - 1][1]
        original_ranges.append((orig_start, orig_end))

    # Apply from right to left to preserve indices
    new_text = text
    for start, end in reversed(original_ranges):
        new_text = new_text[:start] + repl_str + new_text[end:]

    return new_text, len(original_ranges)


def _apply_in_clt_block(text, find_str, repl_str, clt_id, whole_word=False):
    """
    Targeted replacement only inside a specific <CLT N> ... <CLT> block.
    Preserves the wrapper tags themselves.
    """
    pattern = rf"(<CLT\s+{re.escape(str(clt_id))}>)(.*?)(<CLT>)"
    total = 0

    def replacer(match):
        nonlocal total
        prefix, content, suffix = match.groups()
        new_content, n = _replace_in_visible_text(content, find_str, repl_str, whole_word)
        total += n
        return f"{prefix}{new_content}{suffix}"

    updated = re.sub(pattern, replacer, text, flags=re.DOTALL | re.IGNORECASE)
    return updated, total


def _apply_replacements(text, rules_list):
    """
    Process decoded msgstr through active rules.

    Returns:
        (new_text, total_changes, triggered_labels)
    """
    total_changes = 0
    triggered_labels = set()

    for rule in rules_list:
        rule_changes = 0
        scope = rule.get("scope")
        is_whole = rule.get("whole_word", False)
        label = rule.get("label", "Unknown Rule")

        for pair in rule.get("replace", []):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue

            find_str, repl_str = pair

            if not find_str:
                continue

            if scope and str(scope).lower().startswith("clt:"):
                clt_id = str(scope).split(":", 1)[1].strip()
                text, n = _apply_in_clt_block(text, find_str, repl_str, clt_id, is_whole)
            else:
                text, n = _replace_in_visible_text(text, find_str, repl_str, is_whole)

            rule_changes += n

        if rule_changes > 0:
            total_changes += rule_changes
            triggered_labels.add(label)

    return text, total_changes, triggered_labels


# ════════════════════════════════════════════════════════════════════
#  FILE PROCESSING
# ════════════════════════════════════════════════════════════════════

def process_po_file(filepath, active_criteria, log):
    """
    Parse and update a single .po file.
    Returns list of change details.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    file_changes_details = []

    def outer_replacer(match):
        block = match.group(0)
        parts = _extract_entry_parts(block)
        if not parts:
            return block

        context = parts["msgctxt"]
        msgstr = parts["msgstr"]

        relevant_rules = []
        for crit in active_criteria:
            char_tag = crit.get("character")
            if not char_tag or str(char_tag).upper() in context.upper():
                relevant_rules.append(crit)

        if not relevant_rules:
            return block

        new_msgstr, n, labels = _apply_replacements(msgstr, relevant_rules)

        if n > 0:
            file_changes_details.append({
                "context": context,
                "old": msgstr,
                "new": new_msgstr,
                "count": n,
                "labels": sorted(labels),
            })
            return _replace_msgstr_in_block(block, new_msgstr)

        return block

    updated = ENTRY_RE.sub(outer_replacer, content)

    if file_changes_details:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated)

    return file_changes_details


def process_path(path, active_criteria, log):
    """Walk the path and apply selected JSON criteria."""
    if not active_criteria:
        log("⚠ No criteria selected.", "warn")
        return 0, 0

    total_files_changed = 0
    total_replacements = 0

    files_to_process = []

    if os.path.isfile(path) and path.lower().endswith(".po"):
        if "- copy" not in path.lower():
            files_to_process.append(path)

    elif os.path.isdir(path):
        for root, _, filenames in os.walk(path):
            for fname in filenames:
                if fname.lower().endswith(".po") and "- copy" not in fname.lower():
                    files_to_process.append(os.path.join(root, fname))

    for fpath in files_to_process:
        change_list = process_po_file(fpath, active_criteria, log)

        if change_list:
            log(f"✓ Updated: {os.path.basename(fpath)}", "good")

            for item in change_list:
                log(f"  [{item['context']}]", "head")
                log(f"    - Old: \"{item['old'].strip()}\"", "old_sent")
                log(f"    + New: \"{item['new'].strip()}\"", "new_sent")

                total_replacements += item["count"]

            total_files_changed += 1

    return total_files_changed, total_replacements