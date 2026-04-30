"""SQLite storage for seen ads, filters, and stats."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_FILTERS = {
    "hledat": "",
    "hlokalita": "04001",
    "humkreis": "10",
    "cenaod": "",
    "cenado": "550",
    "order": "",
}

FILTER_KEYS = list(DEFAULT_FILTERS.keys())


class Storage:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._init()

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS subscribers (
                    chat_id     INTEGER PRIMARY KEY,
                    filters     TEXT    NOT NULL,
                    enabled     INTEGER NOT NULL DEFAULT 1,
                    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_check  TEXT,
                    ticks       INTEGER NOT NULL DEFAULT 0,
                    sent        INTEGER NOT NULL DEFAULT 0,
                    errors      INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS seen (
                    chat_id      INTEGER NOT NULL,
                    ad_id        TEXT    NOT NULL,
                    seen_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    hidden       INTEGER NOT NULL DEFAULT 0,
                    status       TEXT    NOT NULL DEFAULT 'new',
                    status_at    TEXT,
                    title        TEXT,
                    price        INTEGER,
                    location     TEXT,
                    url          TEXT,
                    author       TEXT,
                    PRIMARY KEY (chat_id, ad_id)
                );
                """
            )
            # migrations for existing DBs that pre-date these columns
            cols = {r["name"] for r in c.execute("PRAGMA table_info(subscribers)").fetchall()}
            for col, ddl in (
                ("created_at", "ALTER TABLE subscribers ADD COLUMN created_at TEXT"),
                ("last_check", "ALTER TABLE subscribers ADD COLUMN last_check TEXT"),
                ("ticks",      "ALTER TABLE subscribers ADD COLUMN ticks INTEGER NOT NULL DEFAULT 0"),
                ("sent",       "ALTER TABLE subscribers ADD COLUMN sent INTEGER NOT NULL DEFAULT 0"),
                ("errors",     "ALTER TABLE subscribers ADD COLUMN errors INTEGER NOT NULL DEFAULT 0"),
            ):
                if col not in cols:
                    c.execute(ddl)
            seen_cols = {r["name"] for r in c.execute("PRAGMA table_info(seen)").fetchall()}
            for col, ddl in (
                ("hidden",    "ALTER TABLE seen ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0"),
                ("status",    "ALTER TABLE seen ADD COLUMN status TEXT NOT NULL DEFAULT 'new'"),
                ("status_at", "ALTER TABLE seen ADD COLUMN status_at TEXT"),
                ("title",     "ALTER TABLE seen ADD COLUMN title TEXT"),
                ("price",     "ALTER TABLE seen ADD COLUMN price INTEGER"),
                ("location",  "ALTER TABLE seen ADD COLUMN location TEXT"),
                ("url",       "ALTER TABLE seen ADD COLUMN url TEXT"),
                ("author",    "ALTER TABLE seen ADD COLUMN author TEXT"),
            ):
                if col not in seen_cols:
                    c.execute(ddl)
            c.execute("CREATE INDEX IF NOT EXISTS idx_seen_status ON seen(chat_id, status)")

    def add_subscriber(self, chat_id: int) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO subscribers (chat_id, filters) VALUES (?, ?)",
                (chat_id, json.dumps(DEFAULT_FILTERS)),
            )

    def remove_subscriber(self, chat_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))
            c.execute("DELETE FROM seen WHERE chat_id = ?", (chat_id,))

    def set_enabled(self, chat_id: int, enabled: bool) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE subscribers SET enabled = ? WHERE chat_id = ?",
                (1 if enabled else 0, chat_id),
            )

    def is_enabled(self, chat_id: int) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT enabled FROM subscribers WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return bool(row["enabled"]) if row else False

    def get_filters(self, chat_id: int) -> dict:
        with self._conn() as c:
            row = c.execute(
                "SELECT filters FROM subscribers WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        if not row:
            return dict(DEFAULT_FILTERS)
        return json.loads(row["filters"])

    def update_filter(self, chat_id: int, key: str, value: str) -> dict:
        if key not in FILTER_KEYS:
            raise ValueError(f"unknown filter: {key}")
        filters = self.get_filters(chat_id)
        filters[key] = value
        with self._conn() as c:
            c.execute(
                "UPDATE subscribers SET filters = ? WHERE chat_id = ?",
                (json.dumps(filters), chat_id),
            )
        return filters

    def reset_filters(self, chat_id: int) -> dict:
        with self._conn() as c:
            c.execute(
                "UPDATE subscribers SET filters = ? WHERE chat_id = ?",
                (json.dumps(DEFAULT_FILTERS), chat_id),
            )
        return dict(DEFAULT_FILTERS)

    def active_subscribers(self) -> list[tuple[int, dict]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT chat_id, filters FROM subscribers WHERE enabled = 1"
            ).fetchall()
        return [(r["chat_id"], json.loads(r["filters"])) for r in rows]

    def filter_unseen(self, chat_id: int, ad_ids: list[str]) -> set[str]:
        if not ad_ids:
            return set()
        with self._conn() as c:
            placeholders = ",".join("?" * len(ad_ids))
            rows = c.execute(
                f"SELECT ad_id FROM seen WHERE chat_id = ? AND ad_id IN ({placeholders})",
                (chat_id, *ad_ids),
            ).fetchall()
        seen = {r["ad_id"] for r in rows}
        return set(ad_ids) - seen

    def mark_seen(self, chat_id: int, ad_ids: list[str]) -> None:
        if not ad_ids:
            return
        with self._conn() as c:
            c.executemany(
                "INSERT OR IGNORE INTO seen (chat_id, ad_id) VALUES (?, ?)",
                [(chat_id, a) for a in ad_ids],
            )

    def mark_seen_with_meta(
        self,
        chat_id: int,
        ad_id: str,
        *,
        title: str = "",
        price: int | None = None,
        location: str = "",
        url: str = "",
        author: str = "",
    ) -> None:
        """Insert/update a seen row with full ad metadata (used when sending)."""
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO seen (chat_id, ad_id, title, price, location, url, author)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, ad_id) DO UPDATE SET
                    title    = COALESCE(excluded.title,    seen.title),
                    price    = COALESCE(excluded.price,    seen.price),
                    location = COALESCE(excluded.location, seen.location),
                    url      = COALESCE(excluded.url,      seen.url),
                    author   = COALESCE(NULLIF(excluded.author, ''), seen.author)
                """,
                (chat_id, ad_id, title, price, location, url, author),
            )

    def update_author(self, chat_id: int, ad_id: str, author: str) -> None:
        if not author:
            return
        with self._conn() as c:
            c.execute(
                "UPDATE seen SET author = ? WHERE chat_id = ? AND ad_id = ?",
                (author, chat_id, ad_id),
            )

    def set_status(self, chat_id: int, ad_id: str, status: str) -> None:
        if status not in ("new", "contacted", "disliked"):
            raise ValueError(f"unknown status: {status}")
        with self._conn() as c:
            # Upsert: keep the row if present, else create with this status.
            c.execute(
                """
                INSERT INTO seen (chat_id, ad_id, status, status_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id, ad_id) DO UPDATE SET
                    status = excluded.status,
                    status_at = excluded.status_at
                """,
                (chat_id, ad_id, status),
            )

    def get_ad_status(self, chat_id: int, ad_id: str) -> str:
        with self._conn() as c:
            row = c.execute(
                "SELECT status FROM seen WHERE chat_id = ? AND ad_id = ?",
                (chat_id, ad_id),
            ).fetchone()
        return row["status"] if row else "new"

    def list_by_status(
        self, chat_id: int, status: str, limit: int = 10, offset: int = 0
    ) -> tuple[list[dict], int]:
        with self._conn() as c:
            total = c.execute(
                "SELECT COUNT(*) AS n FROM seen WHERE chat_id = ? AND status = ?",
                (chat_id, status),
            ).fetchone()["n"]
            rows = c.execute(
                "SELECT ad_id, title, price, location, url, author, status_at "
                "FROM seen WHERE chat_id = ? AND status = ? "
                "ORDER BY status_at DESC LIMIT ? OFFSET ?",
                (chat_id, status, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], total

    def prime_seen(self, chat_id: int, ad_ids: list[str]) -> None:
        self.mark_seen(chat_id, ad_ids)

    def has_seen_any(self, chat_id: int) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM seen WHERE chat_id = ? LIMIT 1", (chat_id,)
            ).fetchone()
        return row is not None

    def hide_ad(self, chat_id: int, ad_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO seen (chat_id, ad_id, hidden) VALUES (?, ?, 1) "
                "ON CONFLICT(chat_id, ad_id) DO UPDATE SET hidden = 1",
                (chat_id, ad_id),
            )

    def clear_seen(self, chat_id: int) -> int:
        with self._conn() as c:
            cur = c.execute("DELETE FROM seen WHERE chat_id = ?", (chat_id,))
            return cur.rowcount

    # --- stats ---

    def record_tick(self, chat_id: int, sent: int = 0, error: bool = False) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE subscribers SET "
                "ticks = ticks + 1, "
                "sent = sent + ?, "
                "errors = errors + ?, "
                "last_check = CURRENT_TIMESTAMP "
                "WHERE chat_id = ?",
                (sent, 1 if error else 0, chat_id),
            )

    def get_stats(self, chat_id: int) -> dict:
        with self._conn() as c:
            row = c.execute(
                "SELECT enabled, created_at, last_check, ticks, sent, errors "
                "FROM subscribers WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            seen_count = c.execute(
                "SELECT COUNT(*) AS n FROM seen WHERE chat_id = ?", (chat_id,)
            ).fetchone()["n"]
            status_rows = c.execute(
                "SELECT status, COUNT(*) AS n FROM seen WHERE chat_id = ? GROUP BY status",
                (chat_id,),
            ).fetchall()
        if not row:
            return {}
        by_status = {r["status"]: r["n"] for r in status_rows}
        return {
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "last_check": row["last_check"],
            "ticks": row["ticks"],
            "sent": row["sent"],
            "errors": row["errors"],
            "seen_count": seen_count,
            "contacted": by_status.get("contacted", 0),
            "disliked":  by_status.get("disliked", 0),
        }
