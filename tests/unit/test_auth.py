"""Tests for ds01_jobs.auth module - HMAC-SHA256 authentication."""

import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import bcrypt
import pytest
from httpx import ASGITransport, AsyncClient

from ds01_jobs.database import init_db


def _create_test_key() -> tuple[str, str, str]:
    """Generate a test API key, key_id, and bcrypt hash.

    Returns (raw_key, key_id, key_hash).
    """
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
    """Build HMAC signing headers for a test request.

    Returns dict of headers: X-Timestamp, X-Nonce, X-Signature.
    """
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
    revoked: int = 0,
) -> None:
    """Insert a test API key into the database."""
    import aiosqlite

    if expires_at is None:
        expires_at = (datetime.now(UTC) + timedelta(days=90)).isoformat()

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO api_keys (username, key_id, key_hash, created_at, expires_at, revoked) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, key_id, key_hash, datetime.now(UTC).isoformat(), expires_at, revoked),
        )
        await db.commit()


def _make_app(db_path: Path):
    """Create a minimal FastAPI app with the auth dependency for testing."""
    from fastapi import Depends, FastAPI

    from ds01_jobs.auth import get_current_user
    from ds01_jobs.database import get_db

    app = FastAPI()

    async def _override_get_db():
        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
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

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    headers = _sign_request(raw_key, "GET", "/protected")
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
    headers.update(_sign_request("badprefix_abc12345", "GET", "/protected"))

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

    raw_key, key_id, key_hash = _create_test_key()
    expired = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await _seed_key(db_path, key_id, key_hash, expires_at=expired)

    app = _make_app(db_path)
    headers = _sign_request(raw_key, "GET", "/protected")
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

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash, revoked=1)

    app = _make_app(db_path)
    headers = _sign_request(raw_key, "GET", "/protected")
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

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    stale_ts = time.time() - 600  # 10 minutes ago
    headers = _sign_request(raw_key, "GET", "/protected", timestamp=stale_ts)
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

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    fixed_nonce = "replay-nonce-123"

    # First request should succeed
    headers1 = _sign_request(raw_key, "GET", "/protected", nonce=fixed_nonce)
    headers1["Authorization"] = f"Bearer {raw_key}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp1 = await client.get("/protected", headers=headers1)
    assert resp1.status_code == 200

    # Second request with same nonce should fail
    headers2 = _sign_request(raw_key, "GET", "/protected", nonce=fixed_nonce)
    headers2["Authorization"] = f"Bearer {raw_key}"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp2 = await client.get("/protected", headers=headers2)
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_invalid_hmac_signature_returns_401(tmp_path: Path):
    """A tampered HMAC signature returns 401."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    headers = _sign_request(raw_key, "GET", "/protected")
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

    raw_key, key_id, key_hash = _create_test_key()
    expires = datetime.now(UTC) + timedelta(days=7)
    await _seed_key(db_path, key_id, key_hash, expires_at=expires.isoformat())

    app = _make_app(db_path)
    headers = _sign_request(raw_key, "GET", "/protected")
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

    raw_key, key_id, key_hash = _create_test_key()
    expires = datetime.now(UTC) + timedelta(days=60)
    await _seed_key(db_path, key_id, key_hash, expires_at=expires.isoformat())

    app = _make_app(db_path)
    headers = _sign_request(raw_key, "GET", "/protected")
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

    raw_key, key_id, key_hash = _create_test_key()
    await _seed_key(db_path, key_id, key_hash)

    app = _make_app(db_path)
    headers = _sign_request(raw_key, "GET", "/protected")
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
