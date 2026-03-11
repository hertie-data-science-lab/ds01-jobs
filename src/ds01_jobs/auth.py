"""HMAC-SHA256 authentication dependency for FastAPI.

Validates API key format, bcrypt hash, timestamp freshness, nonce
uniqueness, and HMAC signature. All auth failures return a generic 401.
"""

import asyncio
import hashlib
import hmac
import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import aiosqlite
import bcrypt
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ds01_jobs.database import get_db

logger = logging.getLogger(__name__)

HMAC_TOLERANCE_SECONDS = 300
NONCE_EXPIRY_SECONDS = 300
KEY_EXPIRY_WARNING_DAYS = 14

security = HTTPBearer()

# In-memory nonce cache: nonce string -> monotonic expiry timestamp
_used_nonces: dict[str, float] = {}


def _cleanup_nonces() -> None:
    """Remove expired nonces from the cache."""
    now = time.monotonic()
    expired = [n for n, exp in _used_nonces.items() if exp <= now]
    for n in expired:
        del _used_nonces[n]


def _check_and_store_nonce(nonce: str) -> bool:
    """Check if a nonce is fresh and store it.

    Returns True if the nonce is fresh (not seen before), False if replay.
    """
    _cleanup_nonces()
    if nonce in _used_nonces:
        return False
    _used_nonces[nonce] = time.monotonic() + NONCE_EXPIRY_SECONDS
    return True


async def _get_key_record(db: aiosqlite.Connection, key_id: str) -> sqlite3.Row | None:
    """Look up an active (non-revoked) API key by key_id."""
    cursor = await db.execute(
        "SELECT * FROM api_keys WHERE key_id = ? AND revoked = 0",
        (key_id,),
    )
    return await cursor.fetchone()


def _build_canonical(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    """Build the canonical string for HMAC signing.

    Format: METHOD\\nPATH\\nTIMESTAMP\\nNONCE\\nBODY_SHA256_HEX
    """
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}"


def _verify_signature(raw_key: str, canonical: str, provided_signature: str) -> bool:
    """Verify the HMAC-SHA256 signature using the raw API key."""
    expected = hmac.new(raw_key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided_signature)


def _auth_failed(request: Request, reason: str, username: str | None = None) -> HTTPException:
    """Log the specific failure reason and return a generic 401."""
    ip = request.client.host if request.client else "unknown"
    user_info = f" for {username}" if username else ""
    logger.warning("Auth failed%s: %s", user_info, reason, extra={"ip": ip})
    return HTTPException(status_code=401, detail="Authentication failed")


async def get_current_user(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict[str, str]:
    """FastAPI dependency that authenticates requests via HMAC-SHA256.

    Returns a dict with the authenticated username on success.
    Raises HTTP 401 on any authentication failure.
    """
    raw_key = credentials.credentials

    # 1. Validate prefix
    if not raw_key.startswith("ds01_"):
        raise _auth_failed(request, "invalid key prefix")

    # 2. Extract key_id (first 8 chars of base64url portion)
    key_id = raw_key[5:13]

    # 3. Look up key record
    row = await _get_key_record(db, key_id)
    if row is None:
        raise _auth_failed(request, "key_id not found", username=None)

    username: str = row["username"]

    # 4. Check expiry
    expires_at = datetime.fromisoformat(row["expires_at"])
    now = datetime.now(UTC)
    if now >= expires_at:
        raise _auth_failed(request, "key expired", username=username)

    # 5. bcrypt verify (run in thread pool to avoid blocking)
    key_hash: str = row["key_hash"]
    valid = await asyncio.to_thread(bcrypt.checkpw, raw_key.encode(), key_hash.encode())
    if not valid:
        raise _auth_failed(request, "bcrypt mismatch", username=username)

    # 6. Extract signing headers
    timestamp_str = request.headers.get("X-Timestamp")
    nonce = request.headers.get("X-Nonce")
    signature = request.headers.get("X-Signature")

    if not timestamp_str or not nonce or not signature:
        raise _auth_failed(request, "missing signing headers", username=username)

    # 7. Timestamp freshness
    try:
        req_time = float(timestamp_str)
    except ValueError:
        raise _auth_failed(request, "invalid timestamp format", username=username) from None

    if abs(time.time() - req_time) > HMAC_TOLERANCE_SECONDS:
        raise _auth_failed(request, "stale timestamp", username=username)

    # 8. Nonce replay check
    if not _check_and_store_nonce(nonce):
        raise _auth_failed(request, "nonce replay", username=username)

    # 9. Build canonical string and verify HMAC signature
    body = await request.body()
    canonical = _build_canonical(request.method, request.url.path, timestamp_str, nonce, body)
    if not _verify_signature(raw_key, canonical, signature):
        raise _auth_failed(request, "invalid signature", username=username)

    # 10. Update last_used_at
    now_iso = now.isoformat()
    await db.execute(
        "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
        (now_iso, key_id),
    )
    await db.commit()

    # 11. Set expiry warning header if within 14 days
    if expires_at - now <= timedelta(days=KEY_EXPIRY_WARNING_DAYS):
        response.headers["X-DS01-Key-Expiry-Warning"] = expires_at.date().isoformat()

    return {"username": username, "unix_username": row["unix_username"]}
