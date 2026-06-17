from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS translation_memory (
  source TEXT NOT NULL,
  speaker TEXT NOT NULL DEFAULT '',
  translation TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (source, speaker)
);
"""


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
            (source, speaker or ""),
        ).fetchone()
        return row[0] if row else None

    def set(self, source: str, translation: str, speaker: str = "") -> None:
        self.conn.execute(
            """INSERT INTO translation_memory(source, speaker, translation, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(source, speaker) DO UPDATE SET translation=excluded.translation, updated_at=CURRENT_TIMESTAMP""",
            (source, speaker or "", translation),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
