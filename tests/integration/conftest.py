"""Shared fixtures and helpers for API integration tests.

Provides test key generation, HMAC signing, database seeding, and
app/client fixtures that wire up a real FastAPI app against a temporary
SQLite database. These are Tier 1 tests - no @pytest.mark.integration.
"""

import hashlib
import hmac
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import bcrypt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from ds01_jobs.app import create_app
from ds01_jobs.config import Settings
from ds01_jobs.database import _get_db_path, get_db, init_db
from ds01_jobs.jobs import _get_settings

# ---------------------------------------------------------------------------
# Helper functions (module-level, not fixtures)
# ---------------------------------------------------------------------------


def create_test_key() -> tuple[str, str, str]:
    """Generate a test API key, key_id, and bcrypt hash.

    Returns:
        (raw_key, key_id, key_hash)
    """
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
    """Build HMAC signing headers for a test request.

    Each call generates a fresh nonce by default, which is critical for
    multi-step tests to avoid nonce replay rejection.
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


def build_signed_headers(
    raw_key: str,
    method: str,
    path: str,
    body: bytes = b"",
) -> dict[str, str]:
    """Convenience wrapper: signing headers + Authorization + Content-Type.

    Adds Content-Type: application/json for POST requests with a body.
    """
    headers = sign_request(raw_key, method, path, body=body)
    headers["Authorization"] = f"Bearer {raw_key}"
    if method.upper() == "POST" and body:
        headers["Content-Type"] = "application/json"
    return headers


async def seed_key(
    db_path: Path,
    key_id: str,
    key_hash: str,
    username: str = "testuser",
    expires_at: str | None = None,
) -> None:
    """Insert an API key row into the database."""
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


async def insert_job(
    db_path: Path,
    username: str = "testuser",
    status: str = "queued",
    created_at: str | None = None,
) -> str:
    """Insert a test job row and return the job_id."""
    job_id = str(uuid.uuid4())
    now = created_at or datetime.now(UTC).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO jobs (id, username, repo_url, branch, gpu_count, job_name, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
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
    return job_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with the full schema."""
    path = tmp_path / "test.db"
    await init_db(db_path=path)
    return path


@pytest_asyncio.fixture
async def auth_key(db_path: Path) -> tuple[str, str, str]:
    """Generate and seed a test API key. Returns (raw_key, key_id, key_hash)."""
    raw_key, key_id, key_hash = create_test_key()
    await seed_key(db_path, key_id, key_hash)
    return raw_key, key_id, key_hash


@pytest_asyncio.fixture
async def app(db_path: Path, tmp_path: Path):
    """Create a real FastAPI app wired to the test database."""
    application = create_app()

    async def _override_get_db():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    application.dependency_overrides[get_db] = _override_get_db

    _get_settings.cache_clear()
    _get_db_path.cache_clear()

    settings_patch = patch(
        "ds01_jobs.jobs._get_settings",
        return_value=Settings(_env_file=None, workspace_root=tmp_path / "workspaces"),
    )
    db_path_patch = patch(
        "ds01_jobs.database._get_db_path",
        return_value=db_path,
    )

    settings_patch.start()
    db_path_patch.start()

    yield application

    settings_patch.stop()
    db_path_patch.stop()
    _get_settings.cache_clear()
    _get_db_path.cache_clear()
    application.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app):
    """Async HTTP client backed by the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_nonces():
    """Clear the auth nonce cache before each test."""
    import ds01_jobs.auth

    ds01_jobs.auth._used_nonces.clear()
    yield
    ds01_jobs.auth._used_nonces.clear()
