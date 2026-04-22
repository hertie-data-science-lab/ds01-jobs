"""Tests for GET endpoints: status detail, logs, listing, and quota."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from ds01_jobs.database import get_db, init_db
from ds01_jobs.jobs import _get_settings
from tests.helpers import create_test_key, seed_key, sign_request


async def _insert_job(
    db_path: Path,
    job_id: str | None = None,
    username: str = "testuser",
    status: str = "succeeded",
    created_at: str | None = None,
    phase_timestamps: str | None = None,
    failed_phase: str | None = None,
    exit_code: int | None = None,
    error_summary: str | None = None,
) -> str:
    """Insert a test job and return its ID."""
    jid = job_id or str(uuid.uuid4())
    now = created_at or datetime.now(UTC).isoformat()
    pts = phase_timestamps or "{}"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO jobs (id, username, repo_url, branch, gpu_count, job_name, "
            "status, created_at, updated_at, phase_timestamps, failed_phase, exit_code, "
            "error_summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                pts,
                failed_phase,
                exit_code,
                error_summary,
            ),
        )
        await db.commit()
    return jid


def _make_app(db_path: Path, workspace_root: Path | None = None):
    """Create a test app with overridden DB and optionally settings."""
    from ds01_jobs.app import create_app
    from ds01_jobs.config import Settings

    app = create_app()

    async def _override_get_db():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    kwargs: dict[str, object] = {"_env_file": None}
    if workspace_root is not None:
        kwargs["workspace_root"] = workspace_root
    test_settings = Settings(**kwargs)  # type: ignore[arg-type]

    def _override_settings() -> Settings:
        return test_settings

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[_get_settings] = _override_settings

    # Also clear the lru_cache so direct calls pick up the override
    _get_settings.cache_clear()

    return app


def _build_get_headers(raw_key: str, method: str, path: str) -> dict[str, str]:
    """Build auth headers for a GET request (no body)."""
    headers = sign_request(raw_key, method, path, body=b"")
    headers["Authorization"] = f"Bearer {raw_key}"
    return headers


# ── Status detail tests ──


@pytest.mark.asyncio
async def test_get_status_succeeded_job(tmp_path: Path) -> None:
    """Succeeded job returns full detail with phases and no error."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    pts = json.dumps(
        {
            "queued": {"started_at": "2026-01-01T00:00:00", "ended_at": "2026-01-01T00:01:00"},
            "cloning": {"started_at": "2026-01-01T00:01:00", "ended_at": "2026-01-01T00:02:00"},
            "building": {"started_at": "2026-01-01T00:02:00", "ended_at": "2026-01-01T00:05:00"},
            "running": {"started_at": "2026-01-01T00:05:00", "ended_at": "2026-01-01T00:10:00"},
        }
    )
    job_id = await _insert_job(db_path, status="succeeded", phase_timestamps=pts)

    app = _make_app(db_path)
    path = f"/api/v1/jobs/{job_id}"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["status"] == "succeeded"
    assert data["submitted_by"] == "testuser"
    assert data["repo_url"] == "https://github.com/test/repo"
    assert "queued" in data["phases"]
    assert "running" in data["phases"]
    assert data["phases"]["queued"]["started_at"] == "2026-01-01T00:00:00"
    assert data["error"] is None
    assert data["queue_position"] is None


@pytest.mark.asyncio
async def test_get_status_failed_job(tmp_path: Path) -> None:
    """Failed job returns structured error info."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    job_id = await _insert_job(
        db_path,
        status="failed",
        failed_phase="build",
        exit_code=1,
        error_summary="Docker build failed",
    )

    app = _make_app(db_path)
    path = f"/api/v1/jobs/{job_id}"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["error"]["phase"] == "build"
    assert data["error"]["message"] == "Docker build failed"
    assert data["error"]["exit_code"] == 1


@pytest.mark.asyncio
async def test_get_status_queued_job_has_queue_position(tmp_path: Path) -> None:
    """Queued job returns correct 1-based queue position."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    # Insert 3 queued jobs with increasing timestamps
    base = datetime(2026, 1, 1, tzinfo=UTC)
    ids = []
    for i in range(3):
        ts = (base + timedelta(seconds=i)).isoformat()
        jid = await _insert_job(db_path, status="queued", created_at=ts)
        ids.append(jid)

    # Get status of the third job
    app = _make_app(db_path)
    path = f"/api/v1/jobs/{ids[2]}"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["queue_position"] == 3


@pytest.mark.asyncio
async def test_get_status_not_found(tmp_path: Path) -> None:
    """Non-existent job returns 404."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    fake_id = str(uuid.uuid4())
    app = _make_app(db_path)
    path = f"/api/v1/jobs/{fake_id}"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_status_other_users_job(tmp_path: Path) -> None:
    """Another user's job returns 404 (not 403)."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    # Bob's key
    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash, username="bob")

    # Alice's job
    job_id = await _insert_job(db_path, username="alice", status="succeeded")

    app = _make_app(db_path)
    path = f"/api/v1/jobs/{job_id}"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 404


# ── Log tests ──


@pytest.mark.asyncio
async def test_get_logs_with_log_files(tmp_path: Path) -> None:
    """Log endpoint returns content from existing log files."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    job_id = await _insert_job(db_path, status="succeeded")

    # Create workspace log files
    workspace = tmp_path / "workspaces" / job_id
    workspace.mkdir(parents=True)
    (workspace / "clone.log").write_text("Cloning into 'repo'...\n")
    (workspace / "build.log").write_text("Step 1/5: FROM python:3.13\n")

    from ds01_jobs.config import Settings

    test_settings = Settings(_env_file=None, workspace_root=tmp_path / "workspaces")

    app = _make_app(db_path, workspace_root=tmp_path / "workspaces")
    path = f"/api/v1/jobs/{job_id}/logs"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    with patch("ds01_jobs.jobs._get_settings", return_value=test_settings):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert "clone" in data["logs"]
    assert "build" in data["logs"]
    assert "Cloning" in data["logs"]["clone"]
    assert data["truncated"] is None


@pytest.mark.asyncio
async def test_get_logs_no_log_files(tmp_path: Path) -> None:
    """No log files returns 200 with empty logs dict."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    job_id = await _insert_job(db_path, status="queued")

    from ds01_jobs.config import Settings

    test_settings = Settings(_env_file=None, workspace_root=tmp_path / "workspaces")

    app = _make_app(db_path, workspace_root=tmp_path / "workspaces")
    path = f"/api/v1/jobs/{job_id}/logs"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    with patch("ds01_jobs.jobs._get_settings", return_value=test_settings):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["logs"] == {}


@pytest.mark.asyncio
async def test_get_logs_truncated(tmp_path: Path) -> None:
    """Large log file is truncated and flagged."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    job_id = await _insert_job(db_path, status="succeeded")

    # Create a >1MB log file
    workspace = tmp_path / "workspaces" / job_id
    workspace.mkdir(parents=True)
    (workspace / "build.log").write_bytes(b"x" * (1_048_576 + 1000))

    from ds01_jobs.config import Settings

    test_settings = Settings(_env_file=None, workspace_root=tmp_path / "workspaces")

    app = _make_app(db_path, workspace_root=tmp_path / "workspaces")
    path = f"/api/v1/jobs/{job_id}/logs"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    with patch("ds01_jobs.jobs._get_settings", return_value=test_settings):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["truncated"] is not None
    assert data["truncated"]["build"] is True


@pytest.mark.asyncio
async def test_get_logs_other_users_job(tmp_path: Path) -> None:
    """Logs for another user's job returns 404."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash, username="bob")

    job_id = await _insert_job(db_path, username="alice", status="succeeded")

    app = _make_app(db_path, workspace_root=tmp_path / "workspaces")
    path = f"/api/v1/jobs/{job_id}/logs"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 404


# ── Listing tests ──


@pytest.mark.asyncio
async def test_list_jobs_returns_own_jobs(tmp_path: Path) -> None:
    """Listing returns only the authenticated user's jobs."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    # 3 jobs for testuser, 2 for otheruser
    for _ in range(3):
        await _insert_job(db_path, username="testuser", status="succeeded")
    for _ in range(2):
        await _insert_job(db_path, username="otheruser", status="succeeded")

    app = _make_app(db_path)
    path = "/api/v1/jobs"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["jobs"]) == 3


@pytest.mark.asyncio
async def test_list_jobs_status_filter(tmp_path: Path) -> None:
    """Status filter returns only matching jobs."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    await _insert_job(db_path, status="running")
    await _insert_job(db_path, status="running")
    await _insert_job(db_path, status="succeeded")

    app = _make_app(db_path)
    path = "/api/v1/jobs"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, params={"status": "running"}, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert all(j["status"] == "running" for j in data["jobs"])


@pytest.mark.asyncio
async def test_list_jobs_pagination(tmp_path: Path) -> None:
    """Pagination returns correct pages."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        ts = (base + timedelta(seconds=i)).isoformat()
        await _insert_job(db_path, status="succeeded", created_at=ts)

    app = _make_app(db_path)
    path = "/api/v1/jobs"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Page 1
        headers1 = _build_get_headers(raw_key, "GET", path)
        resp = await client.get(path, params={"limit": 2, "offset": 0}, headers=headers1)
        data = resp.json()
        assert data["total"] == 5
        assert len(data["jobs"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0

        # Page 2 (fresh headers to avoid nonce replay)
        headers2 = _build_get_headers(raw_key, "GET", path)
        resp = await client.get(path, params={"limit": 2, "offset": 2}, headers=headers2)
        data = resp.json()
        assert data["total"] == 5
        assert len(data["jobs"]) == 2
        assert data["offset"] == 2


@pytest.mark.asyncio
async def test_list_jobs_empty(tmp_path: Path) -> None:
    """No jobs returns 200 with empty list and total=0."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    path = "/api/v1/jobs"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["jobs"] == []
    assert data["total"] == 0


# ── Quota tests ──


@pytest.mark.asyncio
@patch("ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default")
async def test_get_quota_defaults(mock_group: AsyncMock, tmp_path: Path) -> None:
    """Without resource-limits.yaml, quota returns defaults."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    path = "/api/v1/users/me/quota"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["group"] == "default"
    assert data["concurrent"]["limit"] == 3
    assert data["daily"]["limit"] == 20
    assert data["max_result_size_mb"] == 1024
    assert data["username"] == "testuser"


@pytest.mark.asyncio
@patch("ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default")
async def test_get_quota_with_active_jobs(mock_group: AsyncMock, tmp_path: Path) -> None:
    """Active jobs are reflected in concurrent.used."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    await _insert_job(db_path, status="queued")
    await _insert_job(db_path, status="running")

    app = _make_app(db_path)
    path = "/api/v1/users/me/quota"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["concurrent"]["used"] == 2


@pytest.mark.asyncio
@patch("ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default")
async def test_get_quota_other_user_isolation(mock_group: AsyncMock, tmp_path: Path) -> None:
    """Other user's jobs do not affect testuser's quota."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    # Insert active jobs for a different user
    await _insert_job(db_path, username="otheruser", status="running")
    await _insert_job(db_path, username="otheruser", status="queued")

    app = _make_app(db_path)
    path = "/api/v1/users/me/quota"
    headers = _build_get_headers(raw_key, "GET", path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["concurrent"]["used"] == 0
    assert data["daily"]["used"] == 0
