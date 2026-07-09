from __future__ import annotations

import re
import unicodedata

CLT_RE = re.compile(r"<CLT(?:\s+\d+)?>", re.IGNORECASE)
ANY_TAG_RE = re.compile(r"<[^>\n]+>")
PLACEHOLDER_PATTERNS = {
    "printf": re.compile(r"%(?:\d+\$)?[sdifouxX]"),
    "brace": re.compile(r"\{[^{}\n]+\}"),
    "escaped": re.compile(r"\\[ntr\"\\]"),
}


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


ESCAPED_LINEBREAK_RE = re.compile(r"\\[nrt]", re.IGNORECASE)


def user_multiline_text(text: str) -> str:
    """Return user-entered text with common escaped line breaks expanded.

    GUI search fields are often single-line widgets, so users type ``\\n`` to
    mean a real line break.  Keeping this conversion in one place makes Search,
    PO Viewer, and Duplicate/Diff dialogs behave the same way.
    """
    return (text or "").replace(r"\r", "\r").replace(r"\n", "\n").replace(r"\t", "\t")


def flexible_whitespace_pattern(text: str) -> str:
    """Build a literal-search regex where whitespace and line breaks are equal.

    Any typed spaces, pasted line breaks, or typed ``\\n``/``\\r``/``\\t`` will
    match real whitespace in loaded PO text and literal escaped line-break text.
    This lets a search for ``hello world`` match ``hello\nworld``.
    """
    value = user_multiline_text(text).strip()
    pieces: list[str] = []
    pos = 0
    for match in re.finditer(r"\s+", value):
        if match.start() > pos:
            pieces.append(re.escape(value[pos:match.start()]))
        pieces.append(r"(?:\s+|\\[nrt])+")
        pos = match.end()
    if pos < len(value):
        pieces.append(re.escape(value[pos:]))
    return "".join(pieces)


def compile_search_replace_pattern(
    text: str,
    *,
    case_sensitive: bool = False,
    whole_word: bool = False,
    regex: bool = False,
) -> re.Pattern[str]:
    """Compile a shared search/replace pattern for all GUI find dialogs."""
    source = text or ""
    if regex:
        pattern = source
    else:
        pattern = flexible_whitespace_pattern(source)
    if whole_word:
        pattern = rf"(?<!\w)(?:{pattern})(?!\w)"
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(pattern, flags)


def search_replace_replacement(text: str, *, regex: bool = False):
    """Return a replacement value suitable for ``re.sub``.

    Non-regex replacements are returned as a callable so backslash sequences are
    inserted literally except for our shared ``\\n``/``\\r``/``\\t`` shortcuts.
    Regex mode returns the raw string so Python's regex replacement syntax still
    works for backreferences.
    """
    if regex:
        return text or ""
    replacement = user_multiline_text(text)
    return lambda _match: replacement


def clt_tags(text: str) -> list[str]:
    return CLT_RE.findall(text or "")


def generic_tags(text: str) -> list[str]:
    return [t for t in ANY_TAG_RE.findall(text or "") if not t.upper().startswith("<CLT")]


def visible_text(text: str) -> str:
    text = text or ""
    text = CLT_RE.sub(" ", text)
    text = text.replace("[", " ").replace("]", " ").replace('"', " ")
    text = text.replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def visible_len(text: str) -> int:
    return len(CLT_RE.sub("", text or ""))


def placeholders_by_type(text: str) -> dict[str, list[str]]:
    return {name: pattern.findall(text or "") for name, pattern in PLACEHOLDER_PATTERNS.items()}


def order_number(msgctxt: str | None) -> int | None:
    if not msgctxt:
        return None
    m = re.match(r"^\s*(\d+)\b", msgctxt)
    return int(m.group(1)) if m else None


def has_bad_unicode(text: str) -> list[str]:
    bad: list[str] = []
    if "\uFFFD" in text:
        bad.append("replacement character U+FFFD")
    for ch in text:
        if unicodedata.category(ch) == "Cc" and ch != "\n":
            bad.append(f"control character U+{ord(ch):04X}")
    return sorted(set(bad))
