"""Shared test helpers for ds01-jobs API authentication."""

import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import bcrypt


def create_test_key() -> tuple[str, str, str]:
    """Generate a test API key, key_id, and bcrypt hash."""
    random_part = secrets.token_urlsafe(32)
    raw_key = f"ds01_{random_part}"
    key_id = random_part[:8]
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()
    return raw_key, key_id, key_hash


def sign_request(
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


async def seed_key(
    db_path: Path,
    key_id: str,
    key_hash: str,
    username: str = "testuser",
    unix_username: str | None = None,
    expires_at: str | None = None,
    revoked: int = 0,
) -> None:
    """Insert a test API key into the database."""
    if unix_username is None:
        unix_username = f"{username}_unix"
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
                revoked,
            ),
        )
        await db.commit()
