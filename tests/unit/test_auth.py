"""Tests for ds01_jobs.auth module - HMAC-SHA256 authentication."""

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ds01_jobs.database import init_db
from tests.helpers import create_test_key, seed_key, sign_request


def _make_app(db_path: Path):
    """Create a minimal FastAPI app with the auth dependency for testing."""
    import aiosqlite
    from fastapi import Depends, FastAPI

    from ds01_jobs.auth import get_current_user
    from ds01_jobs.database import get_db

    app = FastAPI()

    async def _override_get_db():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    app.dependency_overrides[get_db] = _override_get_db

    @app.get("/protected")
    async def protected(user: dict = Depends(get_current_user)):
        return {"user": user}

    return app


@pytest.mark.asyncio
async def test_valid_hmac_auth_passes(tmp_path: Path):
    """A correctly signed request with a valid key authenticates successfully."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    headers = sign_request(raw_key, "GET", "/protected")
    headers["Authorization"] = f"Bearer {raw_key}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/protected", headers=headers)

    assert resp.status_code == 200
    assert resp.json()["user"]["username"] == "testuser"


@pytest.mark.asyncio
async def test_invalid_key_prefix_returns_401(tmp_path: Path):
    """A key without the 'ds01_' prefix returns 401."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    app = _make_app(db_path)
    headers = {"Authorization": "Bearer badprefix_abc12345"}
    headers.update(sign_request("badprefix_abc12345", "GET", "/protected"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/protected", headers=headers)

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Authentication failed"


@pytest.mark.asyncio
async def test_expired_key_returns_401(tmp_path: Path):
    """An expired key returns 401."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    expired = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await seed_key(db_path, key_id, key_hash, expires_at=expired)

    app = _make_app(db_path)
    headers = sign_request(raw_key, "GET", "/protected")
    headers["Authorization"] = f"Bearer {raw_key}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/protected", headers=headers)

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_revoked_key_returns_401(tmp_path: Path):
    """A revoked key (revoked=1) returns 401."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash, revoked=1)

    app = _make_app(db_path)
    headers = sign_request(raw_key, "GET", "/protected")
    headers["Authorization"] = f"Bearer {raw_key}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/protected", headers=headers)

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stale_timestamp_returns_401(tmp_path: Path):
    """A timestamp outside the 5-minute tolerance returns 401."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    stale_ts = time.time() - 600  # 10 minutes ago
    headers = sign_request(raw_key, "GET", "/protected", timestamp=stale_ts)
    headers["Authorization"] = f"Bearer {raw_key}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/protected", headers=headers)

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_nonce_replay_returns_401(tmp_path: Path):
    """Replaying the same nonce within 5 minutes returns 401."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    fixed_nonce = "replay-nonce-123"

    # First request should succeed
    headers1 = sign_request(raw_key, "GET", "/protected", nonce=fixed_nonce)
    headers1["Authorization"] = f"Bearer {raw_key}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp1 = await client.get("/protected", headers=headers1)
    assert resp1.status_code == 200

    # Second request with same nonce should fail
    headers2 = sign_request(raw_key, "GET", "/protected", nonce=fixed_nonce)
    headers2["Authorization"] = f"Bearer {raw_key}"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp2 = await client.get("/protected", headers=headers2)
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_invalid_hmac_signature_returns_401(tmp_path: Path):
    """A tampered HMAC signature returns 401."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    headers = sign_request(raw_key, "GET", "/protected")
    headers["X-Signature"] = "deadbeef" * 8  # tampered
    headers["Authorization"] = f"Bearer {raw_key}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/protected", headers=headers)

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_expiry_warning_header_set_when_within_14_days(tmp_path: Path):
    """Keys expiring within 14 days get X-DS01-Key-Expiry-Warning header with ISO date."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    expires = datetime.now(UTC) + timedelta(days=7)
    await seed_key(db_path, key_id, key_hash, expires_at=expires.isoformat())

    app = _make_app(db_path)
    headers = sign_request(raw_key, "GET", "/protected")
    headers["Authorization"] = f"Bearer {raw_key}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/protected", headers=headers)

    assert resp.status_code == 200
    warning = resp.headers.get("X-DS01-Key-Expiry-Warning")
    assert warning is not None
    assert warning == expires.date().isoformat()


@pytest.mark.asyncio
async def test_expiry_warning_header_not_set_when_far_from_expiry(tmp_path: Path):
    """Keys with more than 14 days remaining do NOT get the warning header."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    expires = datetime.now(UTC) + timedelta(days=60)
    await seed_key(db_path, key_id, key_hash, expires_at=expires.isoformat())

    app = _make_app(db_path)
    headers = sign_request(raw_key, "GET", "/protected")
    headers["Authorization"] = f"Bearer {raw_key}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/protected", headers=headers)

    assert resp.status_code == 200
    assert "X-DS01-Key-Expiry-Warning" not in resp.headers


@pytest.mark.asyncio
async def test_last_used_at_updated_after_successful_auth(tmp_path: Path):
    """last_used_at is updated in the database after successful authentication."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    headers = sign_request(raw_key, "GET", "/protected")
    headers["Authorization"] = f"Bearer {raw_key}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/protected", headers=headers)

    assert resp.status_code == 200

    # Verify last_used_at was set
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT last_used_at FROM api_keys WHERE key_id = ?", (key_id,))
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] is not None  # last_used_at should be set


@pytest.mark.asyncio
async def test_auth_returns_unix_username(tmp_path: Path):
    """Successful auth returns both username and unix_username."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash, username="ghuser", unix_username="unixuser")

    app = _make_app(db_path)
    headers = sign_request(raw_key, "GET", "/protected")
    headers["Authorization"] = f"Bearer {raw_key}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/protected", headers=headers)

    assert resp.status_code == 200
    user = resp.json()["user"]
    assert user["username"] == "ghuser"
    assert user["unix_username"] == "unixuser"
