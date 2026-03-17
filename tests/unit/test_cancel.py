"""Tests for POST /api/v1/jobs/{job_id}/cancel endpoint."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from ds01_jobs.database import init_db
from tests.helpers import create_test_key, make_app, seed_key, sign_request


async def _insert_job(
    db_path: Path,
    job_id: str | None = None,
    username: str = "testuser",
    status: str = "running",
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


def _build_cancel_headers(raw_key: str, job_id: str) -> dict[str, str]:
    """Build auth + signing headers for a POST cancel request."""
    path = f"/api/v1/jobs/{job_id}/cancel"
    headers = sign_request(raw_key, "POST", path, body=b"")
    headers["Authorization"] = f"Bearer {raw_key}"
    return headers


@pytest.mark.asyncio
async def test_cancel_running_job(tmp_path: Path) -> None:
    """Cancel a running job returns 200 with status=failed."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)
    job_id = await _insert_job(db_path, status="running")

    app = make_app(db_path)
    headers = _build_cancel_headers(raw_key, job_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert data["message"] == "Job cancelled"

    # Verify DB state
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT status, error_summary FROM jobs WHERE id=?", (job_id,))
        row = await cursor.fetchone()
    assert row["status"] == "failed"
    assert row["error_summary"] == "Cancelled by user"


@pytest.mark.asyncio
async def test_cancel_queued_job(tmp_path: Path) -> None:
    """Cancel a queued job returns 200."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)
    job_id = await _insert_job(db_path, status="queued")

    app = make_app(db_path)
    headers = _build_cancel_headers(raw_key, job_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_cancel_completed_job(tmp_path: Path) -> None:
    """Cancel a succeeded job returns 409."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)
    job_id = await _insert_job(db_path, status="succeeded")

    app = make_app(db_path)
    headers = _build_cancel_headers(raw_key, job_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)

    assert resp.status_code == 409
    assert "already succeeded" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cancel_failed_job(tmp_path: Path) -> None:
    """Cancel an already-failed job returns 409."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)
    job_id = await _insert_job(db_path, status="failed")

    app = make_app(db_path)
    headers = _build_cancel_headers(raw_key, job_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)

    assert resp.status_code == 409
    assert "already failed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cancel_not_found(tmp_path: Path) -> None:
    """Cancel a non-existent job returns 404."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    fake_id = str(uuid.uuid4())
    app = make_app(db_path)
    headers = _build_cancel_headers(raw_key, fake_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/v1/jobs/{fake_id}/cancel", headers=headers)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_other_users_job(tmp_path: Path) -> None:
    """Cancel another user's job returns 403."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    # Alice's key
    alice_key, alice_kid, alice_hash = create_test_key()
    await seed_key(db_path, alice_kid, alice_hash, username="alice")

    # Bob's key
    bob_key, bob_kid, bob_hash = create_test_key()
    await seed_key(db_path, bob_kid, bob_hash, username="bob")

    # Alice's job
    job_id = await _insert_job(db_path, username="alice", status="running")

    # Bob tries to cancel Alice's job
    app = make_app(db_path)
    headers = _build_cancel_headers(bob_key, job_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cancel_unauthenticated(tmp_path: Path) -> None:
    """Cancel without auth returns 401 or 403."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    job_id = await _insert_job(db_path, status="running")

    app = make_app(db_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert resp.status_code in (401, 403)
