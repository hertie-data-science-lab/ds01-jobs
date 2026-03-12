"""Tests for ds01_jobs.rate_limit module - per-user rate limiting."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from ds01_jobs.config import Settings
from ds01_jobs.database import init_db
from ds01_jobs.rate_limit import (
    _get_user_group,
    check_rate_limits,
    get_user_job_counts,
    get_user_limits,
    get_user_quota_info,
)


def _test_settings(**overrides: object) -> Settings:
    """Create a Settings instance isolated from .env files."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


async def _insert_job(
    db: aiosqlite.Connection,
    username: str = "testuser",
    status: str = "queued",
    created_at: str | None = None,
) -> None:
    """Insert a test job row."""
    import uuid

    now = created_at or datetime.now(UTC).isoformat()
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
async def test_get_user_limits_defaults() -> None:
    """No resource-limits.yaml returns settings defaults (3, 10)."""
    settings = _test_settings(resource_limits_path=Path("/nonexistent/path.yaml"))
    with patch(
        "ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default"
    ):
        concurrent, daily = await get_user_limits("someuser", settings)
    assert concurrent == 3
    assert daily == 10


@pytest.mark.asyncio
async def test_get_user_limits_from_group(tmp_path: Path) -> None:
    """User resolved to student group gets group limits (2, 5)."""
    yaml_path = tmp_path / "resource-limits.yaml"
    yaml_path.write_text(
        "groups:\n"
        "  student:\n"
        "    max_concurrent_jobs: 2\n"
        "    max_daily_submissions: 5\n"
        "  researcher:\n"
        "    max_concurrent_jobs: 5\n"
        "    max_daily_submissions: 20\n"
    )
    settings = _test_settings(resource_limits_path=yaml_path)

    with patch(
        "ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="student"
    ):
        concurrent, daily = await get_user_limits("bob_unix", settings)
    assert concurrent == 2
    assert daily == 5

    with patch(
        "ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="researcher"
    ):
        concurrent, daily = await get_user_limits("alice_unix", settings)
    assert concurrent == 5
    assert daily == 20


@pytest.mark.asyncio
async def test_get_user_limits_unknown_group(tmp_path: Path) -> None:
    """User with group not in YAML falls back to defaults."""
    yaml_path = tmp_path / "resource-limits.yaml"
    yaml_path.write_text(
        "groups:\n  student:\n    max_concurrent_jobs: 2\n    max_daily_submissions: 5\n"
    )
    settings = _test_settings(resource_limits_path=yaml_path)
    with patch(
        "ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default"
    ):
        concurrent, daily = await get_user_limits("unknown_unix", settings)
    assert concurrent == 3
    assert daily == 10


@pytest.mark.asyncio
async def test_get_user_group_success() -> None:
    """_get_user_group returns group from subprocess stdout."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"student\n", b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
        group = await _get_user_group("testuser", Path("/some/bin.py"))
    assert group == "student"


@pytest.mark.asyncio
async def test_get_user_group_fallback_on_failure() -> None:
    """_get_user_group returns 'default' when subprocess fails."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"error\n")
    mock_proc.returncode = 1

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
        group = await _get_user_group("testuser", Path("/some/bin.py"))
    assert group == "default"


@pytest.mark.asyncio
async def test_get_user_group_fallback_on_file_not_found() -> None:
    """_get_user_group returns 'default' when binary not found."""
    with patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        side_effect=FileNotFoundError("no such file"),
    ):
        group = await _get_user_group("testuser", Path("/nonexistent/bin.py"))
    assert group == "default"


@pytest.mark.asyncio
async def test_get_user_job_counts_no_jobs(tmp_path: Path) -> None:
    """Empty jobs table returns (0, 0)."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    async with aiosqlite.connect(db_path) as db:
        concurrent, daily = await get_user_job_counts(db, "testuser")

    assert concurrent == 0
    assert daily == 0


@pytest.mark.asyncio
async def test_get_user_job_counts_with_active_jobs(tmp_path: Path) -> None:
    """2 active jobs + 1 succeeded -> concurrent = 2."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    async with aiosqlite.connect(db_path) as db:
        await _insert_job(db, status="queued")
        await _insert_job(db, status="running")
        await _insert_job(db, status="succeeded")

        concurrent, _daily = await get_user_job_counts(db, "testuser")

    assert concurrent == 2


@pytest.mark.asyncio
async def test_get_user_job_counts_daily_count(tmp_path: Path) -> None:
    """3 jobs today, 2 yesterday -> daily = 3."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)

    now = datetime.now(UTC)
    yesterday = (now - timedelta(days=1)).isoformat()

    async with aiosqlite.connect(db_path) as db:
        # 3 jobs today
        await _insert_job(db, status="queued")
        await _insert_job(db, status="running")
        await _insert_job(db, status="succeeded")
        # 2 jobs yesterday
        await _insert_job(db, status="succeeded", created_at=yesterday)
        await _insert_job(db, status="succeeded", created_at=yesterday)

        _concurrent, daily = await get_user_job_counts(db, "testuser")

    assert daily == 3


@pytest.mark.asyncio
async def test_check_rate_limits_passes(tmp_path: Path) -> None:
    """Under both limits returns counts without raising."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)
    settings = _test_settings(
        resource_limits_path=Path("/nonexistent"),
        default_concurrent_limit=3,
        default_daily_limit=10,
    )

    async with aiosqlite.connect(db_path) as db:
        await _insert_job(db, status="queued")
        with patch(
            "ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default"
        ):
            result = await check_rate_limits(db, "testuser_unix", "testuser", settings)

    concurrent_count, concurrent_limit, daily_count, daily_limit = result
    assert concurrent_count == 1
    assert concurrent_limit == 3
    assert daily_count == 1
    assert daily_limit == 10


@pytest.mark.asyncio
async def test_check_rate_limits_concurrent_exceeded(tmp_path: Path) -> None:
    """At concurrent limit raises 429 with limit_type='concurrent'."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)
    settings = _test_settings(
        resource_limits_path=Path("/nonexistent"),
        default_concurrent_limit=2,
        default_daily_limit=10,
    )

    async with aiosqlite.connect(db_path) as db:
        await _insert_job(db, status="queued")
        await _insert_job(db, status="running")

        with pytest.raises(Exception) as exc_info:
            with patch(
                "ds01_jobs.rate_limit._get_user_group",
                new_callable=AsyncMock,
                return_value="default",
            ):
                await check_rate_limits(db, "testuser_unix", "testuser", settings)

    exc = exc_info.value
    assert exc.status_code == 429  # type: ignore[union-attr]
    body = exc.detail  # type: ignore[union-attr]
    assert body["error"]["limit_type"] == "concurrent"
    assert body["error"]["current"] == 2
    assert body["error"]["limit"] == 2
    assert body["error"]["retry_after"] is None


@pytest.mark.asyncio
async def test_check_rate_limits_daily_exceeded(tmp_path: Path) -> None:
    """At daily limit raises 429 with limit_type='daily'."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)
    settings = _test_settings(
        resource_limits_path=Path("/nonexistent"),
        default_concurrent_limit=10,
        default_daily_limit=2,
    )

    async with aiosqlite.connect(db_path) as db:
        # Insert 2 succeeded jobs (count toward daily but not concurrent)
        await _insert_job(db, status="succeeded")
        await _insert_job(db, status="succeeded")

        with pytest.raises(Exception) as exc_info:
            with patch(
                "ds01_jobs.rate_limit._get_user_group",
                new_callable=AsyncMock,
                return_value="default",
            ):
                await check_rate_limits(db, "testuser_unix", "testuser", settings)

    exc = exc_info.value
    assert exc.status_code == 429  # type: ignore[union-attr]
    body = exc.detail  # type: ignore[union-attr]
    assert body["error"]["limit_type"] == "daily"
    assert body["error"]["current"] == 2
    assert body["error"]["limit"] == 2
    assert body["error"]["retry_after"] is not None
    assert body["error"]["retry_after"] > 0


@pytest.mark.asyncio
async def test_rate_limit_429_body_structure(tmp_path: Path) -> None:
    """Verify 429 response body matches the exact CONTEXT.md structure."""
    db_path = tmp_path / "test.db"
    await init_db(db_path=db_path)
    settings = _test_settings(
        resource_limits_path=Path("/nonexistent"),
        default_concurrent_limit=1,
        default_daily_limit=10,
    )

    async with aiosqlite.connect(db_path) as db:
        await _insert_job(db, status="queued")

        with pytest.raises(Exception) as exc_info:
            with patch(
                "ds01_jobs.rate_limit._get_user_group",
                new_callable=AsyncMock,
                return_value="default",
            ):
                await check_rate_limits(db, "testuser_unix", "testuser", settings)

    exc = exc_info.value
    body = exc.detail  # type: ignore[union-attr]

    # Verify structure: {error: {type, limit_type, message, limit, current, retry_after}}
    assert "error" in body
    error = body["error"]
    assert error["type"] == "rate_limit_error"
    assert "limit_type" in error
    assert "message" in error
    assert "limit" in error
    assert "current" in error
    assert "retry_after" in error


# --- get_user_quota_info tests ---


@pytest.mark.asyncio
async def test_get_user_quota_info_defaults() -> None:
    """No YAML file returns default group and settings defaults."""
    settings = _test_settings(resource_limits_path=Path("/nonexistent/path.yaml"))
    with patch(
        "ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="default"
    ):
        group, concurrent, daily, max_result_mb = await get_user_quota_info("someuser", settings)
    assert group == "default"
    assert concurrent == 3
    assert daily == 10
    assert max_result_mb == 1024


@pytest.mark.asyncio
async def test_get_user_quota_info_from_group(tmp_path: Path) -> None:
    """User resolved to student group with max_result_size_mb returns group values."""
    yaml_path = tmp_path / "resource-limits.yaml"
    yaml_path.write_text(
        "groups:\n"
        "  student:\n"
        "    max_concurrent_jobs: 2\n"
        "    max_daily_submissions: 5\n"
        "    max_result_size_mb: 512\n"
    )
    settings = _test_settings(resource_limits_path=yaml_path)
    with patch(
        "ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="student"
    ):
        group, concurrent, daily, max_result_mb = await get_user_quota_info("bob_unix", settings)
    assert group == "student"
    assert concurrent == 2
    assert daily == 5
    assert max_result_mb == 512


@pytest.mark.asyncio
async def test_get_user_quota_info_no_result_size_in_yaml(tmp_path: Path) -> None:
    """Group without max_result_size_mb falls back to settings default."""
    yaml_path = tmp_path / "resource-limits.yaml"
    yaml_path.write_text(
        "groups:\n  researcher:\n    max_concurrent_jobs: 5\n    max_daily_submissions: 20\n"
    )
    settings = _test_settings(resource_limits_path=yaml_path)
    with patch(
        "ds01_jobs.rate_limit._get_user_group", new_callable=AsyncMock, return_value="researcher"
    ):
        group, concurrent, daily, max_result_mb = await get_user_quota_info("alice_unix", settings)
    assert group == "researcher"
    assert concurrent == 5
    assert daily == 20
    assert max_result_mb == 1024
