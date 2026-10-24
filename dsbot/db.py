"""SQLite persistence for dsbot's core records."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_PATH = Path("data/dsbot.db")


def utc_now() -> str:
    """Return a timezone-aware UTC timestamp in ISO-8601 format."""

    return datetime.now(UTC).isoformat()


class SQLiteStore:
    """Small SQLite store with schema initialization and row helpers."""

    def __init__(self, path: Path | str = DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create all core tables if they do not exist."""

        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS emails (
                    id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS classifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_id TEXT NOT NULL UNIQUE,
                    classification_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (email_id) REFERENCES emails(id)
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected')),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    ref_id TEXT NOT NULL,
                    original TEXT NOT NULL,
                    final TEXT NOT NULL,
                    label TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS llm_daily_usage (
                    usage_date TEXT PRIMARY KEY,
                    token_count INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    paused INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS llm_request_timestamps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requested_at REAL NOT NULL
                );
                """
            )
            connection.execute(
                "DELETE FROM classifications WHERE id NOT IN "
                "(SELECT MAX(id) FROM classifications GROUP BY email_id)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_classifications_email_id "
                "ON classifications (email_id)"
            )

    def execute(
        self,
        sql: str,
        parameters: Sequence[Any] = (),
    ) -> int:
        """Execute a write statement and return its last inserted row id."""

        with self.connection() as connection:
            cursor = connection.execute(sql, parameters)
            return int(cursor.lastrowid or 0)

    def fetch_all(
        self,
        sql: str,
        parameters: Sequence[Any] = (),
    ) -> list[dict[str, Any]]:
        """Execute a query and return plain dictionaries."""

        with self.connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
            return [dict(row) for row in rows]

    def fetch_one(
        self,
        sql: str,
        parameters: Sequence[Any] = (),
    ) -> dict[str, Any] | None:
        """Execute a query and return one plain dictionary, if present."""

        with self.connection() as connection:
            row = connection.execute(sql, parameters).fetchone()
            return dict(row) if row is not None else None


def get_db(path: Path | str | None = None) -> SQLiteStore:
    """Create a store using an optional path or ``DSBOT_DB_PATH`` override."""

    configured_path = path or os.getenv("DSBOT_DB_PATH") or DB_PATH
    return SQLiteStore(configured_path)


def json_dumps(value: Any) -> str:
    """Serialize a JSON value consistently for SQLite columns."""

    return json.dumps(value, ensure_ascii=False, default=str)
