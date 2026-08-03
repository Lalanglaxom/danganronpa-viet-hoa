from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .models import POEntry, POFile, ParseIssue

_FIELD_RE = re.compile(r'^(msgctxt|msgid|msgstr)\s+(".*")\s*$')
_QUOTED_RE = re.compile(r'^"(.*)"\s*$')


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def po_unescape_quoted(quoted: str) -> str:
    """Decode one PO quoted string line, preserving unknown escapes.

    Only common gettext escapes are interpreted. Unknown escapes are kept as the
    escaped character with its backslash so a weird control code is not lost.
    """
    m = _QUOTED_RE.match(quoted.strip())
    if not m:
        raise ValueError(f"not a PO quoted string: {quoted!r}")
    s = m.group(1)
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(s):
            out.append("\\")
            i += 1
            continue
        nxt = s[i + 1]
        if nxt == "n":
            out.append("\n")
        elif nxt == "r":
            out.append("\r")
        elif nxt == "t":
            out.append("\t")
        elif nxt == '"':
            out.append('"')
        elif nxt == "\\":
            out.append("\\")
        else:
            out.append("\\" + nxt)
        i += 2
    return "".join(out)


def po_escape_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )


def encode_po_string(text: str) -> str:
    """Encode Python text as PO quoted string value after keyword."""
    if text == "":
        return '""'
    if "\n" not in text:
        return f'"{po_escape_text(text)}"'

    parts = ['""']
    chunks = text.split("\n")
    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1 and chunk == "":
            continue
        suffix = "\\n" if i < len(chunks) - 1 else ""
        parts.append(f'"{po_escape_text(chunk)}{suffix}"')
    return "\n".join(parts)


def format_field(keyword: str, text: str) -> str:
    encoded = encode_po_string(text)
    if "\n" not in encoded:
        return f"{keyword} {encoded}"
    first, *rest = encoded.split("\n")
    return "\n".join([f"{keyword} {first}", *rest])


def _parse_field(lines: list[str], i: int, keyword: str, issues: list[ParseIssue]) -> tuple[str, int]:
    if i >= len(lines):
        issues.append(ParseIssue("ERROR", f"expected {keyword}, found end of file", i + 1))
        return "", i

    line = lines[i]
    m = re.match(rf'^{re.escape(keyword)}\s+(".*")\s*$', line)
    if not m:
        issues.append(ParseIssue("ERROR", f"expected {keyword}, got {line!r}", i + 1))
        return "", i + 1

    values: list[str] = []
    try:
        values.append(po_unescape_quoted(m.group(1)))
    except Exception as exc:
        issues.append(ParseIssue("ERROR", f"bad quoted string for {keyword}: {exc}", i + 1))

    i += 1
    while i < len(lines):
        cont = lines[i]
        if cont.startswith('"'):
            try:
                values.append(po_unescape_quoted(cont))
            except Exception as exc:
                issues.append(ParseIssue("ERROR", f"bad continuation string: {exc}", i + 1))
            i += 1
            continue
        break

    return "".join(values), i


def parse_po_text(text: str, path: str | Path | None = None, normalize_unicode: bool = True) -> POFile:
    raw = normalize_newlines(text)
    if normalize_unicode:
        raw = unicodedata.normalize("NFC", raw)
    lines = raw.split("\n")
    issues: list[ParseIssue] = []
    entries: list[POEntry] = []
    header: POEntry | None = None

    i = 0
    pending_comments: list[str] = []
    pending_extracted: list[str] = []
    entry_index = 0

    def consume_comments(start: int) -> int:
        nonlocal pending_comments, pending_extracted
        j = start
        while j < len(lines):
            line = lines[j]
            if line.startswith("#"):
                pending_comments.append(line)
                if line.startswith("#."):
                    pending_extracted.append(line[2:].lstrip())
                j += 1
                continue
            break
        return j

    while i < len(lines):
        # Skip blank lines between entries.
        if not lines[i].strip():
            i += 1
            continue

        i = consume_comments(i)
        if i >= len(lines) or not lines[i].strip():
            continue

        line = lines[i]
        start_line = i + 1
        msgctxt: str | None = None

        if line.startswith("msgctxt "):
            msgctxt, i = _parse_field(lines, i, "msgctxt", issues)
            if i >= len(lines) or not lines[i].startswith("msgid "):
                issues.append(ParseIssue("ERROR", "entry has msgctxt but no following msgid", start_line))
                pending_comments = []
                pending_extracted = []
                continue
        elif not line.startswith("msgid "):
            issues.append(ParseIssue("WARN", f"skipping unexpected line: {line!r}", start_line))
            i += 1
            pending_comments = []
            pending_extracted = []
            continue

        msgid, i = _parse_field(lines, i, "msgid", issues)
        if i >= len(lines) or not lines[i].startswith("msgstr "):
            issues.append(ParseIssue("ERROR", "entry has no msgstr", start_line))
            pending_comments = []
            pending_extracted = []
            continue
        msgstr, i = _parse_field(lines, i, "msgstr", issues)

        entry = POEntry(
            index=entry_index,
            msgctxt=msgctxt,
            msgid=msgid,
            msgstr=msgstr,
            comments=pending_comments,
            extracted_comments=pending_extracted,
            line=start_line,
        )
        entry_index += 1
        pending_comments = []
        pending_extracted = []

        if msgctxt is None and msgid == "" and header is None:
            header = entry
        else:
            entries.append(entry)

    # Detect duplicates early for all tools.
    seen: dict[str, int] = {}
    for entry in entries:
        if entry.msgctxt is None:
            continue
        if entry.msgctxt in seen:
            issues.append(
                ParseIssue(
                    "ERROR",
                    f'duplicate msgctxt "{entry.msgctxt}"; first line {seen[entry.msgctxt]}',
                    entry.line,
                )
            )
        else:
            seen[entry.msgctxt] = entry.line

    return POFile(path=Path(path) if path else None, header=header, entries=entries, issues=issues)


def load_po(path: str | Path, normalize_unicode: bool = True) -> POFile:
    p = Path(path)
    return parse_po_text(p.read_text(encoding="utf-8"), p, normalize_unicode=normalize_unicode)


def dump_po(po_file: POFile) -> str:
    blocks: list[str] = []

    def block_for(entry: POEntry) -> str:
        parts: list[str] = []
        parts.extend(entry.comments)
        if entry.msgctxt is not None:
            parts.append(format_field("msgctxt", entry.msgctxt))
        parts.append(format_field("msgid", entry.msgid))
        parts.append(format_field("msgstr", entry.msgstr))
        return "\n".join(parts)

    if po_file.header is not None:
        blocks.append(block_for(po_file.header))
    for entry in po_file.entries:
        blocks.append(block_for(entry))
    return "\n\n".join(blocks).rstrip() + "\n"


def save_po(po_file: POFile, path: str | Path | None = None) -> None:
    target = Path(path) if path else po_file.path
    if target is None:
        raise ValueError("save_po needs a path")
    target.write_text(dump_po(po_file), encoding="utf-8")
    # Search, duplicate review, and suggestions share an incremental read cache.
    # Invalidate explicitly after writes so even coarse filesystem timestamps
    # cannot leave a stale parsed document behind. The local import avoids a
    # module cycle because text_index itself uses this parser.
    try:
        from .text_index import invalidate_text_index

        invalidate_text_index(target)
    except Exception:
        # Cache invalidation is an optimisation and must never make saving fail.
        pass


def clone_untranslated_from_source(entry: POEntry) -> POEntry:
    return POEntry(
        index=entry.index,
        msgctxt=entry.msgctxt,
        msgid=entry.msgid,
        msgstr="",
        comments=list(entry.comments),
        extracted_comments=list(entry.extracted_comments),
        line=entry.line,
    )


def patch_msgstr_by_uid(po_file: POFile, translations: dict[str, str]) -> int:
    changed = 0
    by_uid = po_file.by_uid()
    for uid, translation in translations.items():
        entry = by_uid.get(uid)
        if entry is None:
            continue
        if entry.msgstr != translation:
            entry.msgstr = unicodedata.normalize("NFC", translation)
            changed += 1
    return changed


def patch_msgstr_by_msgctxt(po_file: POFile, translations: dict[str, str]) -> int:
    changed = 0
    by_ctx = po_file.by_msgctxt()
    for msgctxt, translation in translations.items():
        entry = by_ctx.get(msgctxt)
        if entry is None:
            continue
        if entry.msgstr != translation:
            entry.msgstr = unicodedata.normalize("NFC", translation)
            changed += 1
    return changed
