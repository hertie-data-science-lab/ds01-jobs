"""Database layer for ds01-jobs.

Provides SQLite initialisation, async connection dependency for FastAPI,
and sync connection context manager for CLI usage.
"""

import sqlite3
from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager
from pathlib import Path

import aiosqlite

from ds01_jobs.config import Settings

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    key_id TEXT NOT NULL UNIQUE,
    key_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_id ON api_keys(key_id);
"""


def _get_db_path() -> Path:
    """Return the database path from settings."""
    return Settings(_env_file=None).db_path


async def init_db(db_path: Path | None = None) -> None:
    """Initialise the database, creating parent dirs and schema.

    Args:
        db_path: Override for the database path. Defaults to Settings.db_path.
    """
    path = db_path or _get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(SCHEMA_SQL)
        await db.commit()


async def get_db(db_path: Path | None = None) -> AsyncGenerator[aiosqlite.Connection, None]:
    """FastAPI dependency yielding an async database connection.

    Args:
        db_path: Override for the database path. Defaults to Settings.db_path.
    """
    path = db_path or _get_db_path()

    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        yield db


@contextmanager
def get_db_sync(db_path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Sync context manager for CLI database access.

    Args:
        db_path: Override for the database path. Defaults to Settings.db_path.
    """
    path = db_path or _get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()
