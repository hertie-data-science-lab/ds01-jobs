"""CLI integration tests exercising ds01-submit commands against in-process FastAPI app.

Uses a sync transport wrapper around httpx.ASGITransport to route DS01Client
requests to the real FastAPI app, validating full request signing, auth, and
database round-trips.
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import bcrypt
import httpx
import pytest
from typer.testing import CliRunner

from ds01_jobs.app import create_app
from ds01_jobs.database import SCHEMA_SQL
from ds01_jobs.submit import app as submit_app

pytestmark = pytest.mark.integration

runner = CliRunner()


class SyncASGITransport(httpx.BaseTransport):
    """Sync transport that bridges to ASGITransport via asyncio.run().

    Allows httpx.Client (sync) to make requests against an ASGI app.
    Converts async response streams to sync byte streams.
    """

    def __init__(self, app: object) -> None:
        self._async_transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Handle a sync request by running the async transport in an event loop."""

        async def _handle() -> tuple[int, list[tuple[bytes, bytes]], bytes]:
            resp = await self._async_transport.handle_async_request(request)
            # Read the full async stream body into bytes
            body_parts = []
            async for chunk in resp.stream:
                body_parts.append(chunk)
            await resp.stream.aclose()
            body = b"".join(body_parts)
            return resp.status_code, list(resp.headers.raw), body

        loop = asyncio.new_event_loop()
        try:
            status_code, headers, body = loop.run_until_complete(_handle())
        finally:
            loop.close()

        return httpx.Response(
            status_code=status_code,
            headers=headers,
            content=body,
        )

    def close(self) -> None:
        pass


def _generate_test_key() -> tuple[str, str, str]:
    """Generate a test API key and return (raw_key, key_id, bcrypt_hash)."""
    import base64
    import secrets

    raw_bytes = secrets.token_bytes(32)
    encoded = base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode()
    raw_key = f"ds01_{encoded}"
    key_id = encoded[:8]
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=4)).decode()
    return raw_key, key_id, key_hash


def _seed_api_key(db_path: Path) -> tuple[str, str]:
    """Seed the database with a test API key. Returns (raw_key, username)."""
    raw_key, key_id, key_hash = _generate_test_key()
    username = "testuser"
    now = datetime.now(UTC).isoformat()
    expires = (datetime.now(UTC) + timedelta(days=90)).isoformat()

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO api_keys (username, key_id, key_hash, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (username, key_id, key_hash, now, expires),
    )
    conn.commit()
    conn.close()
    return raw_key, username


def _seed_job(db_path: Path, username: str, status: str = "queued") -> str:
    """Insert a test job and return its ID."""
    import uuid

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (id, username, repo_url, branch, gpu_count, job_name, "
        "status, created_at, updated_at, phase_timestamps) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            username,
            "https://github.com/test-org/test-repo",
            "main",
            1,
            f"test-job-{job_id[:8]}",
            status,
            now,
            now,
            "{}",
        ),
    )
    conn.commit()
    conn.close()
    return job_id


@pytest.fixture()
def integration_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up an in-process FastAPI app with a temp database and patched DS01Client.

    Returns (raw_key, username, db_path, fastapi_app).
    """
    db_path = tmp_path / "test.db"

    # Initialise database schema
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

    # Seed API key
    raw_key, username = _seed_api_key(db_path)

    # Patch settings to use temp DB and skip resource-limits file
    monkeypatch.setenv("DS01_JOBS_DB_PATH", str(db_path))
    monkeypatch.setenv("DS01_JOBS_RESOURCE_LIMITS_PATH", str(tmp_path / "nonexistent.yaml"))
    monkeypatch.setenv("DS01_JOBS_WORKSPACE_ROOT", str(tmp_path / "workspaces"))

    # Clear lru_cache on settings/db path resolvers
    from ds01_jobs.database import _get_db_path
    from ds01_jobs.jobs import _get_settings

    _get_db_path.cache_clear()
    _get_settings.cache_clear()

    # Create the FastAPI app
    fastapi_app = create_app()

    # Create sync ASGI transport for the test app
    transport = SyncASGITransport(app=fastapi_app)
    original_init = httpx.Client.__init__

    def _patched_client_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = transport
        # Ensure base_url is a valid HTTP URL for the transport
        if "base_url" in kwargs:
            base = kwargs["base_url"]
            if not str(base).startswith("http"):
                kwargs["base_url"] = f"http://testserver{base}"
            elif "testserver" not in str(base):
                kwargs["base_url"] = "http://testserver"
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", _patched_client_init)

    # Set env vars for CLI credential resolution
    monkeypatch.setenv("DS01_API_KEY", raw_key)
    monkeypatch.setenv("DS01_API_URL", "http://testserver")

    yield raw_key, username, db_path, fastapi_app

    # Cleanup caches
    _get_db_path.cache_clear()
    _get_settings.cache_clear()


def test_configure_valid_key(integration_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """configure command with a valid key succeeds and writes credentials file."""
    raw_key, username, db_path, _ = integration_env
    creds_path = tmp_path / "creds" / "credentials"
    monkeypatch.setattr("ds01_jobs.submit.CREDENTIALS_PATH", creds_path)

    result = runner.invoke(submit_app, ["configure"], input=f"{raw_key}\n")
    assert result.exit_code == 0, f"stdout: {result.output}"
    assert "Authenticated as" in result.output
    assert creds_path.exists()
    assert creds_path.read_text() == raw_key


def test_configure_invalid_key(integration_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """configure with invalid key prints error and exits 1."""
    creds_path = tmp_path / "creds" / "credentials"
    monkeypatch.setattr("ds01_jobs.submit.CREDENTIALS_PATH", creds_path)

    result = runner.invoke(submit_app, ["configure"], input="invalid_key_abc\n")
    assert result.exit_code == 1
    assert not creds_path.exists()


def test_run_submit_job(integration_env, monkeypatch: pytest.MonkeyPatch):
    """run command submits a job and prints job ID only (one line)."""
    raw_key, username, db_path, _ = integration_env
    # Need allowed_github_orgs to be empty (allows any) and skip preflight
    monkeypatch.setenv("DS01_JOBS_ALLOWED_GITHUB_ORGS", "[]")

    from ds01_jobs.jobs import _get_settings

    _get_settings.cache_clear()

    # Patch verify_repo_accessible and check_ssrf to skip network calls
    with (
        patch("ds01_jobs.jobs.verify_repo_accessible", return_value=None),
        patch("ds01_jobs.jobs.check_ssrf", return_value=None),
    ):
        result = runner.invoke(
            submit_app,
            ["run", "https://github.com/test-org/test-repo"],
        )
    assert result.exit_code == 0, f"stdout: {result.output}"
    # Default output is just the job ID (UUID format)
    output = result.output.strip()
    assert len(output) == 36  # UUID length
    assert "-" in output


def test_run_submit_json(integration_env, monkeypatch: pytest.MonkeyPatch):
    """run with --json prints full JSON response."""
    raw_key, username, db_path, _ = integration_env
    monkeypatch.setenv("DS01_JOBS_ALLOWED_GITHUB_ORGS", "[]")

    from ds01_jobs.jobs import _get_settings

    _get_settings.cache_clear()

    with (
        patch("ds01_jobs.jobs.verify_repo_accessible", return_value=None),
        patch("ds01_jobs.jobs.check_ssrf", return_value=None),
    ):
        result = runner.invoke(
            submit_app,
            ["run", "https://github.com/test-org/test-repo", "--json"],
        )
    assert result.exit_code == 0, f"stdout: {result.output}"
    data = json.loads(result.output)
    assert "job_id" in data
    assert data["status"] == "queued"


def test_status_snapshot(integration_env):
    """status command shows job details in human-readable format."""
    raw_key, username, db_path, _ = integration_env
    job_id = _seed_job(db_path, username, status="queued")

    result = runner.invoke(submit_app, ["status", job_id])
    assert result.exit_code == 0, f"stdout: {result.output}"
    assert f"Job:     {job_id}" in result.output
    assert "Status:  queued" in result.output


def test_status_json(integration_env):
    """status --json prints raw JSON response."""
    raw_key, username, db_path, _ = integration_env
    job_id = _seed_job(db_path, username, status="queued")

    result = runner.invoke(submit_app, ["status", job_id, "--json"])
    assert result.exit_code == 0, f"stdout: {result.output}"
    data = json.loads(result.output)
    assert data["job_id"] == job_id
    assert data["status"] == "queued"


def test_list_jobs(integration_env):
    """list command shows columnar output."""
    raw_key, username, db_path, _ = integration_env
    _seed_job(db_path, username, status="queued")
    _seed_job(db_path, username, status="succeeded")

    result = runner.invoke(submit_app, ["list"])
    assert result.exit_code == 0, f"stdout: {result.output}"
    assert "JOB ID" in result.output
    assert "STATUS" in result.output


def test_cancel_job(integration_env):
    """cancel command succeeds on a queued job."""
    raw_key, username, db_path, _ = integration_env
    job_id = _seed_job(db_path, username, status="queued")

    result = runner.invoke(submit_app, ["cancel", job_id])
    assert result.exit_code == 0, f"stdout: {result.output}"
    assert f"Job {job_id} cancelled" in result.output

    # Verify status changed in DB
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    assert row["status"] == "failed"


def test_no_credentials_error(monkeypatch: pytest.MonkeyPatch):
    """Commands without credentials print helpful error."""
    monkeypatch.delenv("DS01_API_KEY", raising=False)
    # Ensure credentials file doesn't exist
    monkeypatch.setattr(
        "ds01_jobs.submit.CREDENTIALS_PATH",
        Path("/nonexistent/path/credentials"),
    )

    result = runner.invoke(submit_app, ["status", "fake-job-id"])
    assert result.exit_code == 1
    assert "No API key found" in result.output
