"""SQLite database layer for the DS01 Job Submission API.

Uses aiosqlite for async access. WAL mode enabled for concurrent reads.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import aiosqlite

DB_PATH = Path("/var/lib/ds01/api/ds01-jobs.db")

# Allow tests to override the path
_db_path: Path = DB_PATH


async def init_db() -> None:
    """Create the database directory and initialise all tables.

    Safe to call on every startup — uses CREATE TABLE IF NOT EXISTS.
    """
    _db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(_db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                key_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                repo_url TEXT NOT NULL,
                branch TEXT NOT NULL,
                script_path TEXT NOT NULL,
                gpu_count INTEGER NOT NULL DEFAULT 1,
                dockerfile_content TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                username TEXT NOT NULL,
                window_date TEXT NOT NULL,
                daily_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (username, window_date)
            )
        """)

        await db.commit()


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """FastAPI dependency that yields an aiosqlite connection.

    Uses aiosqlite.Row as row_factory so rows are accessible by column name.
    """
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        yield db
