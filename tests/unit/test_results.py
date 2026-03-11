"""Tests for GET /api/v1/jobs/{job_id}/results endpoint."""

import hashlib
import hmac
import io
import secrets
import tarfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import bcrypt
import pytest
from httpx import ASGITransport, AsyncClient

from ds01_jobs.database import get_db, init_db
from ds01_jobs.jobs import _get_settings


def _create_test_key() -> tuple[str, str, str]:
    """Generate a test API key, key_id, and bcrypt hash."""
    random_part = secrets.token_urlsafe(32)
    raw_key = f"ds01_{random_part}"
    key_id = random_part[:8]
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()
    return raw_key, key_id, key_hash


def _sign_request(
    raw_key: str,
    method: str,
    path: str,
    body: bytes = b"",
    timestamp: float | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Build HMAC signing headers for a test request."""
    ts = str(timestamp if timestamp is not None else time.time())
    n = nonce or secrets.token_urlsafe(16)
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = f"{method}\n{path}\n{ts}\n{n}\n{body_hash}"
    sig = hmac.new(raw_key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Timestamp": ts,
        "X-Nonce": n,
        "X-Signature": sig,
    }


async def _seed_key(
    db_path: Path,
    key_id: str,
    key_hash: str,
    username: str = "testuser",
    expires_at: str | None = None,
) -> None:
    """Insert a test API key into the database."""
    if expires_at is None:
        expires_at = (datetime.now(UTC) + timedelta(days=90)).isoformat()

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO api_keys "
            "(username, unix_username, key_id, key_hash, created_at, expires_at, revoked) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                username,
                f"{username}_unix",
                key_id,
                key_hash,
                datetime.now(UTC).isoformat(),
                expires_at,
                0,
            ),
        )
        await db.commit()


async def _insert_job(
    db_path: Path,
    job_id: str | None = None,
    username: str = "testuser",
    status: str = "succeeded",
) -> str:
    """Insert a test job and return its ID."""
    jid = job_id or str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO jobs (id, username, repo_url, branch, gpu_count, job_name, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                jid,
                username,
                "https://github.com/test/repo",
                "main",
                1,
                "test-job",
                status,
                now,
                now,
            ),
        )
        await db.commit()
    return jid


def _make_app(db_path: Path, workspace_root: Path | None = None):
    """Create a test app with results endpoint."""
    from unittest.mock import patch

    from ds01_jobs.app import create_app
    from ds01_jobs.config import Settings

    app = create_app()

    async def _override_get_db():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    app.dependency_overrides[get_db] = _override_get_db

    if workspace_root is not None:
        settings = Settings(_env_file=None, workspace_root=workspace_root)
        # Clear LRU cache and patch the function so direct calls use test settings
        _get_settings.cache_clear()
        patcher = patch("ds01_jobs.jobs._get_settings", return_value=settings)
        patcher.start()
        # Store patcher on app so caller could stop it if needed
        app._test_patcher = patcher  # type: ignore[attr-defined]

    return app


def _build_get_headers(raw_key: str, path: str) -> dict[str, str]:
    """Build auth + signing headers for a GET request."""
    headers = _sign_request(raw_key, "GET", path, body=b"")
    headers["Authorization"] = f"Bearer {raw_key}"
    return headers


@pytest.mark.asyncio
async def test_download_results_success(tmp_path: Path) -> None:
    """Successful download produces a valid tar.gz with expected files."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)
    job_id = await _insert_job(db_path, status="succeeded")

    # Create workspace results directory with files
    workspace_root = tmp_path / "workspaces"
    results_dir = workspace_root / job_id / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "output.txt").write_text("Hello")
    (results_dir / "data.csv").write_text("a,b,c")

    app = _make_app(db_path, workspace_root=workspace_root)
    path = f"/api/v1/jobs/{job_id}/results"
    headers = _build_get_headers(raw_key, path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/gzip"
    assert job_id in resp.headers["content-disposition"]

    # Verify tar.gz content
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        names = tar.getnames()
        assert "results/output.txt" in names
        assert "results/data.csv" in names


@pytest.mark.asyncio
async def test_download_results_no_results_dir(tmp_path: Path) -> None:
    """No workspace directory returns 404 with no_results error."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)
    job_id = await _insert_job(db_path, status="succeeded")

    workspace_root = tmp_path / "workspaces"
    # Don't create any workspace directory

    app = _make_app(db_path, workspace_root=workspace_root)
    path = f"/api/v1/jobs/{job_id}/results"
    headers = _build_get_headers(raw_key, path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "no_results"


@pytest.mark.asyncio
async def test_download_results_empty_results_dir(tmp_path: Path) -> None:
    """Empty results directory returns 404 with no_results error."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)
    job_id = await _insert_job(db_path, status="succeeded")

    workspace_root = tmp_path / "workspaces"
    results_dir = workspace_root / job_id / "results"
    results_dir.mkdir(parents=True)
    # Directory exists but is empty

    app = _make_app(db_path, workspace_root=workspace_root)
    path = f"/api/v1/jobs/{job_id}/results"
    headers = _build_get_headers(raw_key, path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "no_results"


@pytest.mark.asyncio
async def test_download_results_size_exceeded(tmp_path: Path) -> None:
    """Oversized results return 413."""
    from unittest.mock import patch

    from ds01_jobs.config import Settings

    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)
    job_id = await _insert_job(db_path, status="succeeded")

    workspace_root = tmp_path / "workspaces"
    results_dir = workspace_root / job_id / "results"
    results_dir.mkdir(parents=True)
    # Write a file that exceeds the limit (we'll set limit to 0 MB via settings)
    (results_dir / "big.bin").write_bytes(b"x" * 1024)

    # Settings with 0 MB limit so any file exceeds it
    settings = Settings(_env_file=None, workspace_root=workspace_root, default_max_result_size_mb=0)

    from ds01_jobs.app import create_app

    app = create_app()

    async def _override_get_db():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    app.dependency_overrides[get_db] = _override_get_db

    _get_settings.cache_clear()
    with patch("ds01_jobs.jobs._get_settings", return_value=settings):
        path = f"/api/v1/jobs/{job_id}/results"
        headers = _build_get_headers(raw_key, path)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(path, headers=headers)

    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_download_results_not_found(tmp_path: Path) -> None:
    """Non-existent job returns 404."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    fake_id = str(uuid.uuid4())
    app = _make_app(db_path)
    path = f"/api/v1/jobs/{fake_id}/results"
    headers = _build_get_headers(raw_key, path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_results_other_users_job(tmp_path: Path) -> None:
    """Other user's job returns 404 (not 403)."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    # Alice's key
    alice_key, alice_kid, alice_hash = _create_test_key()
    await _seed_key(db_path, alice_kid, alice_hash, username="alice")

    # Bob's key
    bob_key, bob_kid, bob_hash = _create_test_key()
    await _seed_key(db_path, bob_kid, bob_hash, username="bob")

    # Alice's job
    job_id = await _insert_job(db_path, username="alice", status="succeeded")

    # Bob tries to download Alice's results
    app = _make_app(db_path)
    path = f"/api/v1/jobs/{job_id}/results"
    headers = _build_get_headers(bob_key, path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_results_running_job(tmp_path: Path) -> None:
    """Running job returns 409."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)
    job_id = await _insert_job(db_path, status="running")

    app = _make_app(db_path)
    path = f"/api/v1/jobs/{job_id}/results"
    headers = _build_get_headers(raw_key, path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 409
    assert "still running" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_download_results_failed_job(tmp_path: Path) -> None:
    """Failed job returns 409."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)
    job_id = await _insert_job(db_path, status="failed")

    app = _make_app(db_path)
    path = f"/api/v1/jobs/{job_id}/results"
    headers = _build_get_headers(raw_key, path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 409
    assert "failed" in resp.json()["detail"]
