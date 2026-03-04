"""HMAC-SHA256 authentication dependency for the DS01 Job Submission API.

Every authenticated endpoint uses `get_current_user` as a FastAPI dependency.
Authentication flow:
  1. Extract raw API key from Bearer token
  2. Parse key format: ds01_<username>_<random32>
  3. Look up bcrypt hash by username in SQLite
  4. Check revoked and expiry status
  5. bcrypt verify raw key against stored hash
  6. Validate HMAC signature over canonical request
  7. Reject stale timestamps and replayed nonces
  8. Set X-DS01-Key-Expiry-Warning header if key expires within 14 days
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone

import aiosqlite
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPBearer
from passlib.context import CryptContext

from database import get_db

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HMAC_TOLERANCE_SECONDS = 300  # 5-minute window for timestamp freshness
NONCE_EXPIRY_SECONDS = 300  # Match HMAC tolerance — nonces expire after 5 min
KEY_EXPIRY_WARNING_DAYS = 14  # Warn when key expires within this many days

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# In-memory nonce cache: nonce -> expiry_timestamp (float epoch seconds)
_used_nonces: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Nonce replay protection
# ---------------------------------------------------------------------------


def _cleanup_nonces() -> None:
    """Remove expired nonces from the in-memory cache.

    Called on every request — lightweight since the window is only 5 minutes.
    """
    now = time.monotonic()
    expired = [nonce for nonce, expiry in _used_nonces.items() if expiry <= now]
    for nonce in expired:
        del _used_nonces[nonce]


def _check_and_store_nonce(nonce: str) -> bool:
    """Check if nonce is fresh; store it if so.

    Returns:
        True if nonce is fresh (first use within window).
        False if nonce has already been seen (replay attack).
    """
    _cleanup_nonces()
    if nonce in _used_nonces:
        return False
    _used_nonces[nonce] = time.monotonic() + NONCE_EXPIRY_SECONDS
    return True


# ---------------------------------------------------------------------------
# Key lookup
# ---------------------------------------------------------------------------


async def _get_key_record(db: aiosqlite.Connection, username: str) -> dict | None:
    """Fetch the API key record for a username.

    Returns dict with keys: key_hash, expires_at, revoked.
    Returns None if no key exists for the username.
    """
    cursor = await db.execute(
        "SELECT key_hash, expires_at, revoked FROM api_keys WHERE username = ?",
        (username,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {"key_hash": row[0], "expires_at": row[1], "revoked": row[2]}


# ---------------------------------------------------------------------------
# HMAC helpers
# ---------------------------------------------------------------------------


def _build_canonical(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    """Build the canonical request string for HMAC signing.

    Format: METHOD\\nPATH\\nTIMESTAMP\\nNONCE\\nBODY_SHA256_HEX
    """
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join([method.upper(), path, timestamp, nonce, body_hash])


def _verify_signature(raw_key: str, canonical: str, provided_signature: str) -> bool:
    """Verify HMAC-SHA256 signature using constant-time comparison.

    Uses hmac.compare_digest to prevent timing attacks.
    """
    expected = base64.b64encode(
        hmac.new(raw_key.encode(), canonical.encode(), hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, provided_signature)


# ---------------------------------------------------------------------------
# Main dependency
# ---------------------------------------------------------------------------


async def get_current_user(
    request: Request,
    response: Response,
    credentials=Depends(security),
    db=Depends(get_db),
) -> dict:
    """FastAPI dependency: authenticate request via HMAC-signed API key.

    Validates key format, bcrypt hash, timestamp freshness, nonce uniqueness,
    and HMAC signature. Sets X-DS01-Key-Expiry-Warning header when key is
    within KEY_EXPIRY_WARNING_DAYS of expiry.

    Returns:
        {"username": str} on success.

    Raises:
        HTTPException(401) on any authentication failure.
    """
    raw_key: str = credentials.credentials

    # 1. Parse key format: ds01_<username>_<random32>
    parts = raw_key.split("_", 2)
    if len(parts) != 3 or parts[0] != "ds01":
        raise HTTPException(status_code=401, detail="Invalid key format")
    username = parts[1]

    # 2. Look up key record by username
    row = await _get_key_record(db, username)
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # 3. Check revoked
    if row["revoked"]:
        raise HTTPException(status_code=401, detail="API key revoked")

    # 4. Check expiry
    now = datetime.now(timezone.utc)
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
        # Ensure timezone-aware
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid API key")

    if now > expires_at:
        raise HTTPException(status_code=401, detail="API key expired")

    # 5. bcrypt verify raw key against stored hash
    if not pwd_context.verify(raw_key, row["key_hash"]):
        raise HTTPException(status_code=401, detail="Invalid API key")

    # 6. Extract HMAC headers
    timestamp = request.headers.get("X-Timestamp", "")
    nonce = request.headers.get("X-Nonce", "")
    signature = request.headers.get("X-Signature", "")

    if not timestamp or not nonce or not signature:
        raise HTTPException(status_code=401, detail="Missing authentication headers")

    # 7. Timestamp freshness check
    try:
        req_time = datetime.fromisoformat(timestamp.rstrip("Z"))
        if req_time.tzinfo is None:
            req_time = req_time.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid timestamp format")

    if abs((now - req_time).total_seconds()) > HMAC_TOLERANCE_SECONDS:
        raise HTTPException(status_code=401, detail="Request timestamp expired")

    # 8. Nonce replay check
    if not _check_and_store_nonce(nonce):
        raise HTTPException(status_code=401, detail="Nonce already used")

    # 9. Build canonical request and verify HMAC signature
    body = await request.body()
    canonical = _build_canonical(request.method, request.url.path, timestamp, nonce, body)

    if not _verify_signature(raw_key, canonical, signature):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")

    # 10. Set expiry warning header if within 14 days
    days_remaining = (expires_at - now).days
    if days_remaining <= KEY_EXPIRY_WARNING_DAYS:
        response.headers["X-DS01-Key-Expiry-Warning"] = f"{days_remaining} days remaining"

    return {"username": username}
