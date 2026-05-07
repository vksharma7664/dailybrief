"""SQLite-backed deduplication store.

Hash key: sha256(title.lower().strip() + body[:500])
This matches the spec in CLAUDE.md and handles re-published RBI circulars.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..models import Item


class DedupeStore:
    def __init__(self, db_path: Path | str = Path("data/seen.db")) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._create_table()

    def _create_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_items (
                hash          TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL,
                title         TEXT,
                source_id     TEXT
            )
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_seen(self, item: Item) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_items WHERE hash = ?", (item.content_hash,)
        ).fetchone()
        return row is not None

    def mark_seen(self, item: Item) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_items (hash, first_seen_at, title, source_id) "
            "VALUES (?, ?, ?, ?)",
            (
                item.content_hash,
                datetime.now(timezone.utc).isoformat(),
                item.title,
                item.source_id,
            ),
        )
        self._conn.commit()

    def filter_new(self, items: list[Item]) -> list[Item]:
        """Return only unseen items, marking each one seen as we go."""
        new: list[Item] = []
        for item in items:
            if not self.is_seen(item):
                new.append(item)
                self.mark_seen(item)
        return new

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DedupeStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
