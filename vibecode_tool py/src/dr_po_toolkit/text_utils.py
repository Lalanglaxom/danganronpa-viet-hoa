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
LINEBREAK_TOKEN_RE = re.compile(r"(?:\r\n|\r|\n|\\[nr])", re.IGNORECASE)
OPTIONAL_LINEBREAK_PATTERN = r"(?:\r\n|\r|\n|\\[nr])*"


def strip_linebreak_tokens(text: str) -> str:
    """Remove real and escaped line-break markers, leaving other spaces intact."""
    return LINEBREAK_TOKEN_RE.sub("", text or "")


def linebreak_insensitive_visible_text(text: str) -> str:
    """Visible text with real/escaped line breaks removed before normalization."""
    return visible_text(strip_linebreak_tokens(user_multiline_text(text)))


def user_multiline_text(text: str) -> str:
    """Return user-entered text with common escaped line breaks expanded.

    GUI search fields are often single-line widgets, so users type ``\\n`` to
    mean a real line break.  Keeping this conversion in one place makes Search,
    PO Viewer, and Duplicate/Diff dialogs behave the same way.
    """
    return (text or "").replace(r"\r", "\r").replace(r"\n", "\n").replace(r"\t", "\t")


def split_semicolon_parts(text: str, *, keep_empty: bool = False) -> list[str]:
    """Split text on unescaped semicolons.

    ``\\;`` inserts a literal semicolon.  Empty pieces are kept only when
    requested, which is useful for replacements that intentionally delete text.
    """
    raw = text or ""
    parts: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and index + 1 < len(raw) and raw[index + 1] == ";":
            current.append(";")
            index += 2
            continue
        if char == ";":
            value = user_multiline_text("".join(current))
            if keep_empty or value.strip():
                parts.append(value)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    value = user_multiline_text("".join(current))
    if keep_empty or value.strip() or (keep_empty and raw == ""):
        parts.append(value)
    if keep_empty and not parts:
        parts.append("")
    return parts


def search_replace_pairs(find_text: str, replace_text: str) -> list[tuple[str, str]]:
    """Return ordered find/replacement pairs split with semicolons.

    Find pieces are stripped because they are criteria.  Replacement pieces are
    kept verbatim except for shared ``\n``/``\r``/``\t`` shortcuts.  If fewer
    replacements than find terms are provided, the last replacement is reused.
    """
    needles = [part.strip() for part in split_semicolon_parts(find_text) if part.strip()]
    if not needles:
        return []
    replacements = split_semicolon_parts(replace_text, keep_empty=True)
    if not replacements:
        replacements = [""]
    pairs: list[tuple[str, str]] = []
    for index, needle in enumerate(needles):
        replacement = replacements[index] if index < len(replacements) else replacements[-1]
        pairs.append((needle, replacement))
    return pairs


def _literal_piece_ignoring_linebreaks(text: str) -> str:
    """Escape literal text while allowing line breaks between characters."""
    if not text:
        return ""
    return OPTIONAL_LINEBREAK_PATTERN.join(re.escape(char) for char in text)


def flexible_whitespace_pattern(text: str) -> str:
    """Build a literal-search regex that ignores line-break markers.

    Non-regex GUI searches now treat real line breaks and escaped ``\\n``/``\\r``
    as optional separators inside literal text.  Spaces and tabs still require
    visible whitespace, so ``hello world`` matches ``hello\nworld`` while
    ``helloworld`` can also match ``hello\nworld``.
    """
    value = user_multiline_text(text).strip()
    pieces: list[str] = []
    pos = 0
    for match in re.finditer(r"\s+", value):
        if match.start() > pos:
            pieces.append(_literal_piece_ignoring_linebreaks(value[pos:match.start()]))
        pieces.append(r"(?:\s+|\\[nrt])+")
        pos = match.end()
    if pos < len(value):
        pieces.append(_literal_piece_ignoring_linebreaks(value[pos:]))
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


class SearchReplaceCompileError(ValueError):
    """Raised when one item in a GUI search/replace sequence is invalid."""

    def __init__(self, index: int, error: Exception) -> None:
        self.index = index
        self.error = error
        super().__init__(str(error))


SearchReplaceSequence = list[tuple[re.Pattern[str], object]]


def compile_search_replace_sequence(
    find_text: str,
    replace_text: str,
    *,
    case_sensitive: bool = False,
    whole_word: bool = False,
    regex: bool = False,
) -> SearchReplaceSequence:
    """Compile ordered ``;`` separated find/replacement pairs for GUI views."""
    compiled: SearchReplaceSequence = []
    for index, (needle_text, replacement_text) in enumerate(search_replace_pairs(find_text, replace_text), start=1):
        try:
            pattern = compile_search_replace_pattern(
                needle_text,
                case_sensitive=case_sensitive,
                whole_word=whole_word,
                regex=regex,
            )
        except re.error as exc:
            raise SearchReplaceCompileError(index, exc) from exc
        compiled.append((pattern, search_replace_replacement(replacement_text, regex=regex)))
    return compiled


def apply_search_replace_sequence(
    text_value: str,
    compiled: SearchReplaceSequence,
    *,
    count_per_pattern: int = 0,
) -> tuple[str, int]:
    """Apply a compiled search/replace sequence in order."""
    total_hits = 0
    updated = text_value
    for pattern, replacement in compiled:
        updated, hits = pattern.subn(replacement, updated, count=count_per_pattern)
        total_hits += hits
    return updated, total_hits


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
