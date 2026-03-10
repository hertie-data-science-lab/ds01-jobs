"""Tests for ds01_jobs.cli module."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import bcrypt
import pytest
from typer.testing import CliRunner

from ds01_jobs.cli import _resolve_github_token, app, generate_api_key, parse_duration
from ds01_jobs.database import SCHEMA_SQL

runner = CliRunner()


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create a temporary SQLite database with api_keys schema."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.close()

    # Monkeypatch Settings to use temp db path
    monkeypatch.setattr(
        "ds01_jobs.cli.Settings",
        lambda _env_file=None, **kw: type(
            "S",
            (),
            {"db_path": db_path, "github_org": "hertie-data-science-lab", "key_expiry_days": 90},
        )(),
    )
    # Also patch get_db_sync to use temp path
    from contextlib import contextmanager

    @contextmanager
    def _patched_get_db_sync(db_path_arg=None):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr("ds01_jobs.cli.get_db_sync", _patched_get_db_sync)

    return db_path


@pytest.fixture()
def mock_github_member(monkeypatch: pytest.MonkeyPatch):
    """Mock check_org_membership to return True."""
    monkeypatch.setattr("ds01_jobs.cli.check_org_membership", lambda u, o: True)


@pytest.fixture()
def mock_github_non_member(monkeypatch: pytest.MonkeyPatch):
    """Mock check_org_membership to return False."""
    monkeypatch.setattr("ds01_jobs.cli.check_org_membership", lambda u, o: False)


# --- Token resolution tests ---


def test_resolve_github_token_from_env(monkeypatch: pytest.MonkeyPatch):
    """GITHUB_TOKEN env var takes priority."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
    assert _resolve_github_token() == "ghp_test123"


def test_resolve_github_token_from_gh_cli(monkeypatch: pytest.MonkeyPatch):
    """Falls back to gh auth token when env var unset."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        "ds01_jobs.cli.subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "ghp_from_cli\n"})(),
    )
    assert _resolve_github_token() == "ghp_from_cli"


def test_resolve_github_token_none_when_unavailable(monkeypatch: pytest.MonkeyPatch):
    """Returns None when neither env var nor gh CLI available."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        "ds01_jobs.cli.subprocess.run",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert _resolve_github_token() is None


# --- Helper function tests ---


def test_generate_api_key_format():
    """Generated key has ds01_ prefix and key_id is 8 chars."""
    raw_key, key_id = generate_api_key()
    assert raw_key.startswith("ds01_")
    assert len(key_id) == 8
    # base64url portion should be substantial
    assert len(raw_key) > 20


def test_parse_duration_valid():
    """parse_duration parses valid Nd format."""
    assert parse_duration("90d") == 90
    assert parse_duration("30d") == 30
    assert parse_duration("180d") == 180
    assert parse_duration("1d") == 1


def test_parse_duration_invalid():
    """parse_duration raises BadParameter for invalid format."""
    import click

    with pytest.raises(click.exceptions.BadParameter):
        parse_duration("90")
    with pytest.raises(click.exceptions.BadParameter):
        parse_duration("abc")
    with pytest.raises(click.exceptions.BadParameter):
        parse_duration("90h")


# --- key-create tests ---


def test_key_create_success(tmp_db, mock_github_member):
    """key-create produces output with ds01_ key, username, and expiry."""
    result = runner.invoke(app, ["key-create", "researcher1"])
    assert result.exit_code == 0
    assert "ds01_" in result.output
    assert "researcher1" in result.output
    assert "Expires:" in result.output
    assert "API Key created successfully" in result.output


def test_key_create_json(tmp_db, mock_github_member):
    """key-create --json produces valid JSON with key field."""
    result = runner.invoke(app, ["key-create", "researcher1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["key"].startswith("ds01_")
    assert data["username"] == "researcher1"
    assert "key_id" in data
    assert "expires_at" in data


def test_key_create_non_member(tmp_db, mock_github_non_member):
    """key-create for non-member exits with error."""
    result = runner.invoke(app, ["key-create", "outsider"])
    assert result.exit_code == 1


def test_key_create_duplicate_active_key(tmp_db, mock_github_member):
    """key-create for user with existing active key exits with error."""
    # Create first key
    result1 = runner.invoke(app, ["key-create", "researcher1"])
    assert result1.exit_code == 0

    # Try to create second key
    result2 = runner.invoke(app, ["key-create", "researcher1"])
    assert result2.exit_code == 1


def test_key_create_after_revoke(tmp_db, mock_github_member):
    """key-create succeeds after the previous key is revoked."""
    runner.invoke(app, ["key-create", "researcher1"])
    runner.invoke(app, ["key-revoke", "researcher1", "--yes"])

    result = runner.invoke(app, ["key-create", "researcher1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["username"] == "researcher1"


def test_key_create_custom_expires(tmp_db, mock_github_member):
    """key-create with --expires 30d sets expiry ~30 days from now."""
    result = runner.invoke(app, ["key-create", "researcher1", "--expires", "30d", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)

    expected_date = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%d")
    assert data["expires_at"] == expected_date


def test_key_create_stores_bcrypt_hash(tmp_db, mock_github_member):
    """Created key hash is valid bcrypt - bcrypt.checkpw returns True."""
    result = runner.invoke(app, ["key-create", "researcher1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    raw_key = data["key"]

    # Read hash from DB
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT key_hash FROM api_keys WHERE username = 'researcher1'")
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    stored_hash = row["key_hash"]
    assert bcrypt.checkpw(raw_key.encode(), stored_hash.encode())


def test_key_create_setup_instructions(tmp_db, mock_github_member):
    """key-create output includes setup instructions block."""
    result = runner.invoke(app, ["key-create", "researcher1"])
    assert result.exit_code == 0
    assert "Setup instructions" in result.output
    assert "pip install ds01-jobs" in result.output
    assert "ds01-submit configure" in result.output
    assert "DS01_API_KEY=" in result.output


# --- key-list tests ---


def test_key_list_empty(tmp_db):
    """key-list with no keys shows empty message."""
    result = runner.invoke(app, ["key-list"])
    assert result.exit_code == 0
    assert "No API keys found" in result.output


def test_key_list_with_keys(tmp_db, mock_github_member):
    """key-list shows correct columns."""
    runner.invoke(app, ["key-create", "researcher1"])
    result = runner.invoke(app, ["key-list"])
    assert result.exit_code == 0
    assert "USERNAME" in result.output
    assert "STATUS" in result.output
    assert "CREATED" in result.output
    assert "EXPIRES" in result.output
    assert "LAST USED" in result.output
    assert "researcher1" in result.output
    assert "active" in result.output


def test_key_list_json(tmp_db, mock_github_member):
    """key-list --json produces valid JSON array."""
    runner.invoke(app, ["key-create", "researcher1"])
    result = runner.invoke(app, ["key-list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["username"] == "researcher1"
    assert data[0]["status"] == "active"


def test_key_list_shows_status(tmp_db, mock_github_member):
    """key-list shows correct status for active, revoked, expired keys."""
    # Create and revoke a key to test revoked status
    runner.invoke(app, ["key-create", "researcher1"])
    runner.invoke(app, ["key-revoke", "researcher1", "--yes"])

    result = runner.invoke(app, ["key-list", "--json"])
    data = json.loads(result.output)
    assert data[0]["status"] == "revoked"


def test_key_list_shows_expired_status(tmp_db, mock_github_member):
    """key-list shows 'expired' for keys past their expiry date."""
    # Create key then manually backdate its expiry
    runner.invoke(app, ["key-create", "researcher1"])
    conn = sqlite3.connect(tmp_db)
    past_date = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    conn.execute("UPDATE api_keys SET expires_at = ? WHERE username = 'researcher1'", (past_date,))
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["key-list", "--json"])
    data = json.loads(result.output)
    assert data[0]["status"] == "expired"


# --- key-revoke tests ---


def test_key_revoke_with_yes(tmp_db, mock_github_member):
    """key-revoke --yes revokes without prompt."""
    runner.invoke(app, ["key-create", "researcher1"])
    result = runner.invoke(app, ["key-revoke", "researcher1", "--yes"])
    assert result.exit_code == 0
    assert "Key revoked" in result.output


def test_key_revoke_nonexistent_user(tmp_db):
    """key-revoke for nonexistent user exits with error."""
    result = runner.invoke(app, ["key-revoke", "nobody", "--yes"])
    assert result.exit_code == 1


def test_revoked_key_shows_in_list(tmp_db, mock_github_member):
    """Revoked key shows as 'revoked' in key-list."""
    runner.invoke(app, ["key-create", "researcher1"])
    runner.invoke(app, ["key-revoke", "researcher1", "--yes"])
    result = runner.invoke(app, ["key-list", "--json"])
    data = json.loads(result.output)
    assert data[0]["status"] == "revoked"


def test_key_revoke_json(tmp_db, mock_github_member):
    """key-revoke --json produces valid JSON output."""
    runner.invoke(app, ["key-create", "researcher1"])
    result = runner.invoke(app, ["key-revoke", "researcher1", "--yes", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["username"] == "researcher1"
    assert data["status"] == "revoked"


# --- key-rotate tests ---


def test_key_rotate_with_yes(tmp_db, mock_github_member):
    """key-rotate --yes produces a new key."""
    runner.invoke(app, ["key-create", "researcher1"])
    result = runner.invoke(app, ["key-rotate", "researcher1", "--yes"])
    assert result.exit_code == 0
    assert "ds01_" in result.output
    assert "API Key rotated successfully" in result.output


def test_key_rotate_no_active_key(tmp_db):
    """key-rotate for user with no active key exits with error."""
    result = runner.invoke(app, ["key-rotate", "nobody", "--yes"])
    assert result.exit_code == 1


def test_key_rotate_produces_different_key(tmp_db, mock_github_member):
    """Rotated key is different from the original."""
    result1 = runner.invoke(app, ["key-create", "researcher1", "--json"])
    original_key = json.loads(result1.output)["key"]

    result2 = runner.invoke(app, ["key-rotate", "researcher1", "--yes", "--json"])
    assert result2.exit_code == 0
    new_key = json.loads(result2.output)["key"]
    assert new_key != original_key
    assert new_key.startswith("ds01_")


def test_key_rotate_json(tmp_db, mock_github_member):
    """key-rotate --json produces valid JSON with new key."""
    runner.invoke(app, ["key-create", "researcher1"])
    result = runner.invoke(app, ["key-rotate", "researcher1", "--yes", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["key"].startswith("ds01_")
    assert data["username"] == "researcher1"


def test_key_rotate_old_hash_changes(tmp_db, mock_github_member):
    """After rotation, the stored hash corresponds to the new key."""
    result1 = runner.invoke(app, ["key-create", "researcher1", "--json"])
    original_key = json.loads(result1.output)["key"]

    result2 = runner.invoke(app, ["key-rotate", "researcher1", "--yes", "--json"])
    new_key = json.loads(result2.output)["key"]

    # Read hash from DB
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT key_hash FROM api_keys WHERE username = 'researcher1'")
    row = cursor.fetchone()
    conn.close()

    stored_hash = row["key_hash"]
    # New key should match
    assert bcrypt.checkpw(new_key.encode(), stored_hash.encode())
    # Old key should NOT match
    assert not bcrypt.checkpw(original_key.encode(), stored_hash.encode())
