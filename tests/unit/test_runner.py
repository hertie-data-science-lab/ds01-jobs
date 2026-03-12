"""Unit tests for the job runner module."""

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from ds01_jobs.config import Settings
from ds01_jobs.database import SCHEMA_SQL
from ds01_jobs.runner import JobRunner


@pytest.fixture
def runner_settings(tmp_path: Path) -> Settings:
    """Settings configured for test use."""
    return Settings(
        _env_file=None,
        db_path=tmp_path / "test.db",
        runner_poll_interval=0.1,
        workspace_root=tmp_path / "workspaces",
        docker_bin=Path("/usr/local/bin/docker"),
    )


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    """Initialise DB with 2 queued jobs and return db_path."""
    db_path = tmp_path / "test.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    for i in range(2):
        conn.execute(
            "INSERT INTO jobs (id, username, unix_username, repo_url, branch, gpu_count, "
            "job_name, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"job-{i}",
                "testuser",
                "testuser_unix",
                "https://github.com/test/repo.git",
                "main",
                1,
                f"test-job-{i}",
                "queued",
                f"2026-01-01T00:00:0{i}",
                f"2026-01-01T00:00:0{i}",
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def _init_db_sync(db_path: Path) -> None:
    """Initialise schema synchronously for tests."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_recover_orphaned_jobs(runner_settings: Settings) -> None:
    """Startup recovery marks in-progress jobs as failed."""
    db_path = runner_settings.db_path
    _init_db_sync(db_path)

    conn = sqlite3.connect(db_path)
    for status in ("running", "building", "cloning"):
        conn.execute(
            "INSERT INTO jobs (id, username, repo_url, branch, gpu_count, "
            "job_name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"orphan-{status}",
                "testuser",
                "https://github.com/test/repo.git",
                "main",
                1,
                f"orphan-{status}",
                status,
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
    # Also add a queued job that should NOT be affected
    conn.execute(
        "INSERT INTO jobs (id, username, repo_url, branch, gpu_count, "
        "job_name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "queued-job",
            "testuser",
            "https://github.com/test/repo.git",
            "main",
            1,
            "queued-job",
            "queued",
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00",
        ),
    )
    conn.commit()
    conn.close()

    runner = JobRunner(runner_settings)
    await runner._recover_orphaned_jobs()

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, status, error_summary FROM jobs ORDER BY id")
        rows = await cursor.fetchall()

    results = {row["id"]: (row["status"], row["error_summary"]) for row in rows}
    assert results["orphan-running"] == ("failed", "Runner restarted - job interrupted")
    assert results["orphan-building"] == ("failed", "Runner restarted - job interrupted")
    assert results["orphan-cloning"] == ("failed", "Runner restarted - job interrupted")
    assert results["queued-job"][0] == "queued"


@pytest.mark.asyncio
@patch("ds01_jobs.runner.get_available_gpu_count", new_callable=AsyncMock)
@patch("ds01_jobs.runner.JobExecutor")
async def test_poll_dispatches_queued_jobs(
    mock_executor_cls: AsyncMock,
    mock_gpu: AsyncMock,
    runner_settings: Settings,
    populated_db: Path,
) -> None:
    """Poll dispatches queued jobs when GPUs are available."""
    runner_settings.db_path = populated_db
    mock_gpu.return_value = 4

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock()
    mock_executor_cls.return_value = mock_executor

    runner = JobRunner(runner_settings)
    await runner._poll_and_dispatch()

    assert len(runner.active_jobs) == 2
    assert mock_executor_cls.call_count == 2


@pytest.mark.asyncio
@patch("ds01_jobs.runner.get_available_gpu_count", new_callable=AsyncMock)
async def test_poll_skips_when_no_gpus(
    mock_gpu: AsyncMock,
    runner_settings: Settings,
    populated_db: Path,
) -> None:
    """No GPUs available means no jobs dispatched."""
    runner_settings.db_path = populated_db
    mock_gpu.return_value = 0

    runner = JobRunner(runner_settings)
    await runner._poll_and_dispatch()

    assert len(runner.active_jobs) == 0


@pytest.mark.asyncio
@patch("ds01_jobs.runner.get_available_gpu_count", new_callable=AsyncMock)
@patch("ds01_jobs.runner.JobExecutor")
async def test_poll_skips_oversized_jobs(
    mock_executor_cls: AsyncMock,
    mock_gpu: AsyncMock,
    runner_settings: Settings,
    tmp_path: Path,
) -> None:
    """Jobs requiring more GPUs than available are skipped; smaller ones run."""
    db_path = tmp_path / "test.db"
    runner_settings.db_path = db_path
    _init_db_sync(db_path)

    conn = sqlite3.connect(db_path)
    # Job needing 2 GPUs
    conn.execute(
        "INSERT INTO jobs (id, username, unix_username, repo_url, branch, gpu_count, "
        "job_name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "big-job",
            "testuser",
            "testuser_unix",
            "https://github.com/test/repo.git",
            "main",
            2,
            "big-job",
            "queued",
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00",
        ),
    )
    # Job needing 1 GPU
    conn.execute(
        "INSERT INTO jobs (id, username, unix_username, repo_url, branch, gpu_count, "
        "job_name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "small-job",
            "testuser",
            "testuser_unix",
            "https://github.com/test/repo.git",
            "main",
            1,
            "small-job",
            "queued",
            "2026-01-01T00:00:01",
            "2026-01-01T00:00:01",
        ),
    )
    conn.commit()
    conn.close()

    mock_gpu.return_value = 1
    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock()
    mock_executor_cls.return_value = mock_executor

    runner = JobRunner(runner_settings)
    await runner._poll_and_dispatch()

    assert "small-job" in runner.active_jobs
    assert "big-job" not in runner.active_jobs


@pytest.mark.asyncio
@patch("ds01_jobs.runner.get_available_gpu_count", new_callable=AsyncMock)
async def test_shutdown_event_stops_loop(
    mock_gpu: AsyncMock,
    runner_settings: Settings,
) -> None:
    """Setting shutdown_event before run() causes immediate exit."""
    _init_db_sync(runner_settings.db_path)
    mock_gpu.return_value = 0

    runner = JobRunner(runner_settings)
    runner.shutdown_event.set()

    # run() should exit without blocking
    await asyncio.wait_for(runner.run(), timeout=5.0)

    # No jobs dispatched
    assert len(runner.active_jobs) == 0


@pytest.mark.asyncio
async def test_sigterm_sets_shutdown_event(runner_settings: Settings) -> None:
    """_handle_sigterm sets the shutdown event."""
    runner = JobRunner(runner_settings)
    assert not runner.shutdown_event.is_set()
    runner._handle_sigterm()
    assert runner.shutdown_event.is_set()


@pytest.mark.asyncio
@patch("ds01_jobs.runner.get_available_gpu_count", new_callable=AsyncMock)
async def test_completed_tasks_cleaned_up(
    mock_gpu: AsyncMock,
    runner_settings: Settings,
) -> None:
    """Completed asyncio tasks are removed from active_jobs."""
    _init_db_sync(runner_settings.db_path)
    mock_gpu.return_value = 0

    runner = JobRunner(runner_settings)

    # Create a done task
    async def noop() -> None:
        pass

    done_task = asyncio.create_task(noop())
    await done_task  # Let it finish

    runner.active_jobs["done-job"] = done_task
    runner.active_executors["done-job"] = AsyncMock()

    # Run one poll cycle (no jobs to dispatch since GPU=0)
    await runner._poll_and_dispatch()
    runner._cleanup_completed_tasks()

    assert "done-job" not in runner.active_jobs
    assert "done-job" not in runner.active_executors


@pytest.mark.asyncio
@patch("ds01_jobs.runner.get_available_gpu_count", new_callable=AsyncMock)
@patch("ds01_jobs.runner.JobExecutor")
async def test_poll_passes_unix_username_to_executor(
    mock_executor_cls: AsyncMock,
    mock_gpu: AsyncMock,
    runner_settings: Settings,
    tmp_path: Path,
) -> None:
    """Runner passes unix_username from DB to executor.execute()."""
    db_path = tmp_path / "test.db"
    runner_settings.db_path = db_path
    _init_db_sync(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (id, username, unix_username, repo_url, branch, gpu_count, "
        "job_name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "unix-test-job",
            "alice_github",
            "alice_unix",
            "https://github.com/test/repo.git",
            "main",
            1,
            "unix-test",
            "queued",
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00",
        ),
    )
    conn.commit()
    conn.close()

    mock_gpu.return_value = 4
    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock()
    mock_executor_cls.return_value = mock_executor

    runner = JobRunner(runner_settings)
    await runner._poll_and_dispatch()

    # Verify executor.execute was called with unix_username="alice_unix"
    assert len(runner.active_jobs) == 1
    mock_executor.execute.assert_called_once()
    call_kwargs = mock_executor.execute.call_args
    # unix_username should be passed as a keyword argument
    assert call_kwargs[1]["unix_username"] == "alice_unix"
