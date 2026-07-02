from __future__ import annotations

import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from .models import POEntry

SCHEMA = """
CREATE TABLE IF NOT EXISTS translation_memory (
  source TEXT NOT NULL,
  speaker TEXT NOT NULL DEFAULT '',
  translation TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (source, speaker)
);
"""


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    return re.sub(r"\s+", " ", text).strip()


class TranslationMemory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def get(self, source: str, speaker: str = "") -> str | None:
        row = self.conn.execute(
            "SELECT translation FROM translation_memory WHERE source=? AND speaker=?",
            (_norm(source), speaker or ""),
        ).fetchone()
        return row[0] if row else None

    def set(self, source: str, translation: str, speaker: str = "") -> None:
        source = _norm(source)
        translation = unicodedata.normalize("NFC", translation or "")
        if not source or not translation.strip():
            return
        self.conn.execute(
            """INSERT INTO translation_memory(source, speaker, translation, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(source, speaker) DO UPDATE SET translation=excluded.translation, updated_at=CURRENT_TIMESTAMP""",
            (source, speaker or "", translation),
        )
        self.conn.commit()

    def add_entries(self, entries: Iterable[POEntry]) -> int:
        count = 0
        for entry in entries:
            if entry.msgid.strip() and entry.msgstr.strip():
                self.set(entry.msgid, entry.msgstr, entry.speaker)
                count += 1
        return count

    def suggest(self, source: str, speaker: str = "", min_score: float = 0.70, limit: int = 10) -> list[dict[str, object]]:
        source_key = _norm(source).lower()
        if not source_key:
            return []
        rows = self.conn.execute(
            "SELECT source, speaker, translation FROM translation_memory WHERE translation <> ''"
        ).fetchall()
        ranked: list[tuple[float, str, str, str]] = []
        for cand_source, cand_speaker, translation in rows:
            if speaker and cand_speaker and cand_speaker != speaker:
                speaker_bonus = 0.0
            else:
                speaker_bonus = 0.03
            score = min(1.0, SequenceMatcher(None, source_key, _norm(cand_source).lower()).ratio() + speaker_bonus)
            if score >= min_score:
                ranked.append((score, cand_source, cand_speaker, translation))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [
            {"score": score, "source": cand_source, "speaker": cand_speaker, "translation": translation}
            for score, cand_source, cand_speaker, translation in ranked[:limit]
        ]

    def close(self) -> None:
        self.conn.close()
