"""Tests for ds01_jobs.jobs module - POST /api/v1/jobs endpoint."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from ds01_jobs.database import init_db
from tests.helpers import build_headers, create_test_key, make_app, seed_key


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

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    headers = build_headers(raw_key, body)

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

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = make_app(db_path)
    body = {"repo_url": "https://evil.com/hacked"}
    headers = build_headers(raw_key, body)

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

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = make_app(db_path)
    body = {
        "repo_url": "https://github.com/testorg/myrepo",
        "dockerfile_content": "FROM evil.registry.io/hacker/image:latest\nRUN echo hi",
    }
    headers = build_headers(raw_key, body)

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

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = make_app(db_path)
    body = {
        "repo_url": "https://github.com/testorg/myrepo",
        "dockerfile_content": "FROM python:3.13-slim\nRUN pip install torch",
    }
    headers = build_headers(raw_key, body)

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

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    # Insert 3 active jobs (default concurrent limit)
    for _ in range(3):
        await _insert_job(db_path, status="queued")

    app = make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    headers = build_headers(raw_key, body)

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

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    # Insert 20 completed jobs today (default daily limit)
    for _ in range(20):
        await _insert_job(db_path, status="succeeded")

    app = make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    headers = build_headers(raw_key, body)

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

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    headers = build_headers(raw_key, body)

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

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    headers = build_headers(raw_key, body)

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

    app = make_app(db_path)

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

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    headers = build_headers(raw_key, body)

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


@pytest.mark.asyncio
@patch("ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default")
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.get_gpu_count", new_callable=AsyncMock, return_value=4)
async def test_submit_job_gpu_count_exceeds_total(
    mock_gpu: AsyncMock,
    mock_ssrf: AsyncMock,
    mock_verify: AsyncMock,
    mock_group: AsyncMock,
    tmp_path: Path,
) -> None:
    """Requesting more GPUs than available returns 422 with exceeds_total."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = make_app(db_path)
    body = {"repo_url": "https://github.com/testorg/myrepo", "gpu_count": 8}
    headers = build_headers(raw_key, body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/jobs", content=json.dumps(body), headers=headers)

    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["type"] == "validation_error"
    assert data["error"]["errors"][0]["field"] == "gpu_count"
    assert data["error"]["errors"][0]["code"] == "exceeds_total"
