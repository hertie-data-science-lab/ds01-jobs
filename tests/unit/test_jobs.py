"""Tests for ds01_jobs.jobs module - POST /api/v1/jobs endpoint."""

import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import bcrypt
import pytest
from httpx import ASGITransport, AsyncClient

from ds01_jobs.database import get_db, init_db


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
    unix_username: str = "testuser_unix",
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
                unix_username,
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
    username: str = "testuser",
    status: str = "queued",
    created_at: str | None = None,
) -> None:
    """Insert a test job row directly into the database."""
    now = created_at or datetime.now(UTC).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO jobs (id, username, unix_username, repo_url, branch, gpu_count, job_name, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                username,
                "testuser_unix",
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


def _make_app(db_path: Path):
    """Create a test app with jobs router and mocked external calls."""
    from ds01_jobs.app import create_app
    from ds01_jobs.jobs import _get_settings

    app = create_app()

    async def _override_get_db():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    def _override_settings():
        from ds01_jobs.config import Settings

        return Settings(_env_file=None)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[_get_settings] = _override_settings

    return app


def _build_headers(raw_key: str, body_dict: dict) -> dict[str, str]:  # type: ignore[type-arg]
    """Build auth + signing headers for a POST /api/v1/jobs request."""
    body_bytes = json.dumps(body_dict).encode()
    headers = _sign_request(raw_key, "POST", "/api/v1/jobs", body=body_bytes)
    headers["Authorization"] = f"Bearer {raw_key}"
    headers["Content-Type"] = "application/json"
    return headers


@pytest.mark.asyncio
@patch("ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default")
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_submit_job_success(
    mock_ssrf: AsyncMock, mock_verify: AsyncMock, mock_group: AsyncMock, tmp_path: Path
) -> None:
    """Valid auth + valid repo_url returns 202 with job record in DB."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    headers = _build_headers(raw_key, body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/jobs", content=json.dumps(body), headers=headers)

    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "queued"
    assert data["status_url"].startswith("/api/v1/jobs/")
    assert "created_at" in data

    # Verify job in DB
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (data["job_id"],))
        row = await cursor.fetchone()
        assert row is not None


@pytest.mark.asyncio
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_submit_job_invalid_url(
    mock_ssrf: AsyncMock, mock_verify: AsyncMock, tmp_path: Path
) -> None:
    """Bad URL returns 422 with Stripe-like error body."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    body = {"repo_url": "https://evil.com/hacked"}
    headers = _build_headers(raw_key, body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/jobs", content=json.dumps(body), headers=headers)

    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["type"] == "validation_error"
    assert data["error"]["errors"][0]["field"] == "repo_url"


@pytest.mark.asyncio
@patch("ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default")
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_submit_job_with_dockerfile_scan_error(
    mock_ssrf: AsyncMock, mock_verify: AsyncMock, mock_group: AsyncMock, tmp_path: Path
) -> None:
    """Dockerfile with blocked base image returns 422 with dockerfile_scan_error."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    body = {
        "repo_url": "https://github.com/testorg/myrepo",
        "dockerfile_content": "FROM evil.registry.io/hacker/image:latest\nRUN echo hi",
    }
    headers = _build_headers(raw_key, body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/jobs", content=json.dumps(body), headers=headers)

    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["type"] == "dockerfile_scan_error"
    assert len(data["error"]["errors"]) > 0
    assert data["error"]["errors"][0]["code"] == "BLOCKED_BASE_IMAGE"


@pytest.mark.asyncio
@patch("ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default")
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_submit_job_with_clean_dockerfile(
    mock_ssrf: AsyncMock, mock_verify: AsyncMock, mock_group: AsyncMock, tmp_path: Path
) -> None:
    """Valid Dockerfile returns 202 (scan passes)."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    body = {
        "repo_url": "https://github.com/testorg/myrepo",
        "dockerfile_content": "FROM python:3.13-slim\nRUN pip install torch",
    }
    headers = _build_headers(raw_key, body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/jobs", content=json.dumps(body), headers=headers)

    assert resp.status_code == 202


@pytest.mark.asyncio
@patch("ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default")
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_submit_job_concurrent_limit_exceeded(
    mock_ssrf: AsyncMock, mock_verify: AsyncMock, mock_group: AsyncMock, tmp_path: Path
) -> None:
    """User at concurrent limit gets 429 with limit_type='concurrent'."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    # Insert 3 active jobs (default concurrent limit)
    for _ in range(3):
        await _insert_job(db_path, status="queued")

    app = _make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    headers = _build_headers(raw_key, body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/jobs", content=json.dumps(body), headers=headers)

    assert resp.status_code == 429
    data = resp.json()
    assert data["detail"]["error"]["limit_type"] == "concurrent"


@pytest.mark.asyncio
@patch("ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default")
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_submit_job_daily_limit_exceeded(
    mock_ssrf: AsyncMock, mock_verify: AsyncMock, mock_group: AsyncMock, tmp_path: Path
) -> None:
    """User at daily limit gets 429 with limit_type='daily'."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    # Insert 20 completed jobs today (default daily limit)
    for _ in range(20):
        await _insert_job(db_path, status="succeeded")

    app = _make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    headers = _build_headers(raw_key, body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/jobs", content=json.dumps(body), headers=headers)

    assert resp.status_code == 429
    data = resp.json()
    assert data["detail"]["error"]["limit_type"] == "daily"


@pytest.mark.asyncio
@patch("ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default")
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_submit_job_rate_limit_headers_on_success(
    mock_ssrf: AsyncMock, mock_verify: AsyncMock, mock_group: AsyncMock, tmp_path: Path
) -> None:
    """Successful 202 includes X-RateLimit-* headers."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    headers = _build_headers(raw_key, body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/jobs", content=json.dumps(body), headers=headers)

    assert resp.status_code == 202
    assert "x-ratelimit-limit-concurrent" in resp.headers
    assert "x-ratelimit-remaining-concurrent" in resp.headers
    assert "x-ratelimit-limit-daily" in resp.headers
    assert "x-ratelimit-remaining-daily" in resp.headers
    assert "x-ratelimit-reset-daily" in resp.headers


@pytest.mark.asyncio
@patch("ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default")
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_submit_job_auto_generated_job_name(
    mock_ssrf: AsyncMock, mock_verify: AsyncMock, mock_group: AsyncMock, tmp_path: Path
) -> None:
    """Omitting job_name auto-generates from repo name + short ID."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    headers = _build_headers(raw_key, body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/jobs", content=json.dumps(body), headers=headers)

    assert resp.status_code == 202
    data = resp.json()
    job_id = data["job_id"]

    # Verify auto-generated name in DB
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT job_name FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        assert row is not None
        name = row[0]
        assert name.startswith("myrepo-")
        assert job_id[:8] in name


@pytest.mark.asyncio
async def test_submit_job_unauthenticated(tmp_path: Path) -> None:
    """No auth headers returns 401 or 403."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    app = _make_app(db_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/jobs",
            json={"repo_url": "https://github.com/testorg/myrepo"},
        )

    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
@patch("ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default")
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_submit_job_repo_not_found(
    mock_ssrf: AsyncMock, mock_group: AsyncMock, tmp_path: Path
) -> None:
    """verify_repo_accessible raising ValueError returns 422 with repo_not_found."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    headers = _build_headers(raw_key, body)

    with patch(
        "ds01_jobs.jobs.verify_repo_accessible",
        new_callable=AsyncMock,
        side_effect=ValueError("Repository not found"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/jobs", content=json.dumps(body), headers=headers)

    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["errors"][0]["code"] == "repo_not_found"
