"""Tests for ds01_jobs.revalidate module."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ds01_jobs.database import SCHEMA_SQL
from ds01_jobs.revalidate import app

runner = CliRunner()


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Temporary DB patched into revalidate's Settings + get_db_sync."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.close()

    monkeypatch.setattr(
        "ds01_jobs.revalidate.Settings",
        lambda _env_file=None, **kw: type(
            "S", (), {"db_path": db_path, "github_org": "org", "github_team": ""}
        )(),
    )

    @contextmanager
    def _get_db_sync(_path=None):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr("ds01_jobs.revalidate.get_db_sync", _get_db_sync)
    return db_path


def _insert_key(db: Path, username: str, revoked: int = 0, expires_days: int = 30) -> None:
    now = datetime.now(UTC)
    expires = now + timedelta(days=expires_days)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO api_keys (username, unix_username, key_id, key_hash, "
        "created_at, expires_at, revoked) VALUES (?, 'u', ?, 'h', ?, ?, ?)",
        (username, username[:8].ljust(8, "x"), now.isoformat(), expires.isoformat(), revoked),
    )
    conn.commit()
    conn.close()


def _active_count(db: Path) -> int:
    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM api_keys WHERE revoked = 0").fetchone()[0]
    conn.close()
    return int(count)


def test_revalidate_revokes_user_without_access(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Users who fail the access check get their keys revoked."""
    _insert_key(db, "alice")
    _insert_key(db, "bob")
    monkeypatch.setattr(
        "ds01_jobs.revalidate.verify_github_access",
        lambda u, s: u != "bob",
    )

    events_log = tmp_path / "events.jsonl"
    result = runner.invoke(app, ["--events-log", str(events_log)])
    assert result.exit_code == 0
    assert "revoked bob" in result.output
    assert _active_count(db) == 1

    entries = [json.loads(line) for line in events_log.read_text().splitlines()]
    assert len(entries) == 1
    assert entries[0]["username"] == "bob"
    assert entries[0]["event"] == "key_revoked"
    assert entries[0]["source"] == "revalidate"


def test_revalidate_dry_run_reports_but_does_not_revoke(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run reports what would happen without touching the DB."""
    _insert_key(db, "alice")
    monkeypatch.setattr("ds01_jobs.revalidate.verify_github_access", lambda u, s: False)

    events_log = tmp_path / "events.jsonl"
    result = runner.invoke(app, ["--dry-run", "--events-log", str(events_log)])
    assert result.exit_code == 0
    assert "[dry-run] would revoke alice" in result.output
    assert _active_count(db) == 1
    assert not events_log.exists()


def test_revalidate_skips_on_github_error(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When GitHub check raises, user is skipped (not revoked)."""
    import httpx

    _insert_key(db, "alice")

    def _boom(u, s):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr("ds01_jobs.revalidate.verify_github_access", _boom)

    result = runner.invoke(app, ["--events-log", str(tmp_path / "events.jsonl")])
    assert result.exit_code == 0
    assert "skip alice" in result.output
    assert _active_count(db) == 1


def test_revalidate_ignores_revoked_and_expired(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Already-revoked or expired keys don't get re-checked."""
    _insert_key(db, "alice", revoked=1)
    _insert_key(db, "bob", expires_days=-1)  # expired yesterday
    _insert_key(db, "carol")

    seen: list[str] = []
    monkeypatch.setattr(
        "ds01_jobs.revalidate.verify_github_access",
        lambda u, s: seen.append(u) or True,
    )

    result = runner.invoke(app, ["--events-log", str(tmp_path / "events.jsonl")])
    assert result.exit_code == 0
    assert seen == ["carol"]
