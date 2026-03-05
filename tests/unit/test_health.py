"""Tests for the GET /health endpoint."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from ds01_jobs.database import get_db, init_db


def _make_app(db_path: Path):
    """Create the app with DB overridden to use a test database."""
    import aiosqlite
    from fastapi import FastAPI

    from ds01_jobs.health import router
    from ds01_jobs.middleware import limiter

    app = FastAPI()

    async def _override_get_db():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            yield db

    app.dependency_overrides[get_db] = _override_get_db
    app.state.limiter = limiter
    app.include_router(router)

    return app


def _make_broken_app():
    """Create the app with DB dependency that raises an error."""
    from fastapi import FastAPI

    from ds01_jobs.health import router
    from ds01_jobs.middleware import limiter

    app = FastAPI()

    async def _broken_get_db():
        mock = AsyncMock()
        mock.execute = AsyncMock(side_effect=Exception("DB unreachable"))
        yield mock

    app.dependency_overrides[get_db] = _broken_get_db
    app.state.limiter = limiter
    app.include_router(router)

    return app


@pytest.mark.asyncio
async def test_health_returns_200_when_db_reachable(tmp_path: Path):
    """GET /health returns 200 with status=ok when DB is reachable."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    app = _make_app(db_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert data["db"] == "ok"


@pytest.mark.asyncio
async def test_health_returns_503_when_db_unreachable():
    """GET /health returns 503 with status=degraded when DB is unreachable."""
    app = _make_broken_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["db"] == "error"


@pytest.mark.asyncio
async def test_health_requires_no_authentication(tmp_path: Path):
    """GET /health works without any Authorization header."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    app = _make_app(db_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # No Authorization header at all
        resp = await client.get("/health")

    assert resp.status_code == 200
