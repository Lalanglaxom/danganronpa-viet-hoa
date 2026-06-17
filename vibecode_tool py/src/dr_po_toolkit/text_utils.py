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
