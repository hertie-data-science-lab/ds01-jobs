"""Tests for ds01_jobs.database module."""

import sqlite3
from pathlib import Path

import pytest

from ds01_jobs.database import get_db, get_db_sync, init_db


@pytest.mark.asyncio
async def test_init_db_creates_api_keys_table(tmp_path: Path):
    """init_db creates the api_keys table with the correct columns."""
    db_path = tmp_path / "test.db"

    await init_db(db_path=db_path)

    # Verify table exists and has the right columns
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("PRAGMA table_info(api_keys)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()

    expected = {
        "id",
        "username",
        "key_id",
        "key_hash",
        "created_at",
        "expires_at",
        "revoked",
        "last_used_at",
    }
    assert columns == expected


@pytest.mark.asyncio
async def test_init_db_creates_key_id_index(tmp_path: Path):
    """init_db creates an index on the key_id column."""
    db_path = tmp_path / "test.db"

    await init_db(db_path=db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_api_keys_key_id'"
    )
    indexes = cursor.fetchall()
    conn.close()

    assert len(indexes) == 1


@pytest.mark.asyncio
async def test_init_db_enables_wal_mode(tmp_path: Path):
    """init_db enables WAL journal mode."""
    db_path = tmp_path / "test.db"

    await init_db(db_path=db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.execute("PRAGMA journal_mode")
    mode = cursor.fetchone()[0]
    conn.close()

    assert mode == "wal"


@pytest.mark.asyncio
async def test_init_db_creates_parent_directories(tmp_path: Path):
    """init_db creates parent directories if they don't exist."""
    db_path = tmp_path / "nested" / "dir" / "test.db"

    await init_db(db_path=db_path)

    assert db_path.exists()


@pytest.mark.asyncio
async def test_get_db_yields_connection_with_row_factory(tmp_path: Path):
    """get_db yields a connection with row_factory set."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    async for db in get_db(db_path=db_path):
        assert db.row_factory is not None
        # Verify we can query
        cursor = await db.execute("SELECT 1 AS value")
        row = await cursor.fetchone()
        assert row["value"] == 1


def test_get_db_sync_yields_connection_with_row_factory(tmp_path: Path):
    """get_db_sync yields a sync connection with row_factory set."""
    db_path = tmp_path / "test.db"
    # Create the DB first using sync sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS api_keys (id INTEGER PRIMARY KEY, username TEXT);"
    )
    conn.close()

    with get_db_sync(db_path=db_path) as db:
        assert db.row_factory is sqlite3.Row
        cursor = db.execute("SELECT 1 AS value")
        row = cursor.fetchone()
        assert row["value"] == 1


@pytest.mark.asyncio
async def test_init_db_is_idempotent(tmp_path: Path):
    """Calling init_db multiple times does not raise errors."""
    db_path = tmp_path / "test.db"

    await init_db(db_path=db_path)
    await init_db(db_path=db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.execute("PRAGMA table_info(api_keys)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()

    assert "key_id" in columns
