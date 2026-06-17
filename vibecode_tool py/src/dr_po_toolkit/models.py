from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ParseIssue:
    level: str
    message: str
    line: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "message": self.message, "line": self.line}


@dataclass(slots=True)
class POEntry:
    """One gettext PO entry used by DR script files.

    The toolkit mainly targets the DR format:
      comments + msgctxt + msgid + msgstr
    Header entries have msgctxt=None and are stored separately on POFile.header.
    """

    index: int
    msgctxt: str | None
    msgid: str
    msgstr: str
    comments: list[str] = field(default_factory=list)
    extracted_comments: list[str] = field(default_factory=list)
    line: int = 0

    @property
    def uid(self) -> str:
        """Stable batch key. Includes index to avoid duplicate msgctxt collisions."""
        ctx = self.msgctxt or ""
        return f"{self.index:05d}|{ctx}"

    @property
    def speaker(self) -> str:
        if not self.msgctxt:
            return ""
        if "|" in self.msgctxt:
            return self.msgctxt.split("|", 1)[1].strip()
        return ""

    @property
    def is_translated(self) -> bool:
        return bool(self.msgstr.strip())

    @property
    def japanese_context(self) -> str:
        return "\n".join(self.extracted_comments)


@dataclass(slots=True)
class POFile:
    path: Path | None
    header: POEntry | None
    entries: list[POEntry]
    issues: list[ParseIssue] = field(default_factory=list)

    def by_uid(self) -> dict[str, POEntry]:
        return {entry.uid: entry for entry in self.entries}

    def by_msgctxt(self) -> dict[str, POEntry]:
        result: dict[str, POEntry] = {}
        for entry in self.entries:
            if entry.msgctxt is not None and entry.msgctxt not in result:
                result[entry.msgctxt] = entry
        return result

    def duplicate_contexts(self) -> dict[str, list[POEntry]]:
        seen: dict[str, list[POEntry]] = {}
        for entry in self.entries:
            if entry.msgctxt is not None:
                seen.setdefault(entry.msgctxt, []).append(entry)
        return {k: v for k, v in seen.items() if len(v) > 1}


@dataclass(slots=True)
class ReplacementRule:
    id: str
    find: str
    replace: str
    enabled: bool = True
    priority: int = 100
    speaker: str | None = None
    scope: str | None = None
    whole_word: bool = False
    case_sensitive: bool = True
    stop_after: bool = False
    notes: str = ""


@dataclass(slots=True)
class ReplacementChange:
    file: Path
    uid: str
    msgctxt: str
    rule_id: str
    before: str
    after: str
    count: int


@dataclass(slots=True)
class ValidationIssue:
    level: str
    check: str
    detail: str
    file: Path | None = None
    msgctxt: str | None = None
    line: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "check": self.check,
            "detail": self.detail,
            "file": str(self.file) if self.file else "",
            "msgctxt": self.msgctxt or "",
            "line": self.line,
        }
