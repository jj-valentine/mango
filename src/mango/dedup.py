"""SQLite deduplication cache — tracks seen content IDs across runs."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path


_DEFAULT_DB = Path(__file__).parent.parent.parent / "data" / "seen.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_items (
    content_hash TEXT PRIMARY KEY,
    entity_name  TEXT NOT NULL,
    source_url   TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    seen_at      TIMESTAMP DEFAULT (datetime('now'))
)
"""


class SeenDB:
    def __init__(self, db_path: Path | None = None):
        self._path = db_path or _DEFAULT_DB
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def seen_ids_for(self, entity_name: str) -> set[str]:
        """Return all content hashes recorded for this entity."""
        rows = self._conn.execute(
            "SELECT source_url FROM seen_items WHERE entity_name = ?", (entity_name,)
        ).fetchall()
        return {row[0] for row in rows}

    def is_new(self, entity_name: str, url: str) -> bool:
        h = _hash(url)
        row = self._conn.execute(
            "SELECT 1 FROM seen_items WHERE content_hash = ? AND entity_name = ?",
            (h, entity_name),
        ).fetchone()
        return row is None

    def mark_seen(self, entity_name: str, url: str, title: str = "") -> None:
        h = _hash(url)
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_items (content_hash, entity_name, source_url, title) "
            "VALUES (?, ?, ?, ?)",
            (h, entity_name, url, title),
        )
        self._conn.commit()

    def mark_many_seen(self, entity_name: str, items: list[tuple[str, str]]) -> None:
        """Mark a batch of (url, title) pairs as seen."""
        rows = [(_hash(url), entity_name, url, title) for url, title in items]
        self._conn.executemany(
            "INSERT OR IGNORE INTO seen_items (content_hash, entity_name, source_url, title) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()


def _hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()
