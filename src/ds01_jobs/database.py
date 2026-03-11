"""Database layer for ds01-jobs.

Provides SQLite initialisation, async connection dependency for FastAPI,
and sync connection context manager for CLI usage.
"""

import sqlite3
from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import aiosqlite

from ds01_jobs.config import Settings

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    unix_username TEXT NOT NULL DEFAULT '',
    key_id TEXT NOT NULL UNIQUE,
    key_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_id ON api_keys(key_id);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    unix_username TEXT NOT NULL DEFAULT '',
    repo_url TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT 'main',
    gpu_count INTEGER NOT NULL DEFAULT 1,
    job_name TEXT NOT NULL,
    timeout_seconds INTEGER,
    dockerfile_content TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    failed_phase TEXT,
    exit_code INTEGER,
    error_summary TEXT,
    started_at TEXT,
    completed_at TEXT,
    phase_timestamps TEXT DEFAULT '{}',
    FOREIGN KEY (username) REFERENCES api_keys(username)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_username_status ON jobs(username, status);
CREATE INDEX IF NOT EXISTS idx_jobs_username_created ON jobs(username, created_at);
"""
# Note: Schema uses CREATE TABLE IF NOT EXISTS. For pre-v1 development,
# drop and recreate the DB if columns are missing after schema changes.


@lru_cache(maxsize=1)
def _get_db_path() -> Path:
    """Return the database path from settings (cached)."""
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
    try:
        yield conn
    finally:
        conn.close()
