"""Unit tests for the job executor module."""

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from ds01_jobs.config import Settings
from ds01_jobs.database import SCHEMA_SQL
from ds01_jobs.executor import JobExecutor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

JOB_ID = "test-job-001"
REPO_URL = "https://github.com/example/repo.git"
BRANCH = "main"


@pytest.fixture
def executor_settings(tmp_path: Path) -> Settings:
    """Settings configured for test use."""
    return Settings(
        _env_file=None,
        workspace_root=tmp_path / "workspaces",
        docker_bin=Path("/usr/local/bin/docker"),
        build_timeout_seconds=60.0,
        clone_timeout_seconds=30.0,
        default_job_timeout_seconds=120.0,
        max_job_timeout_seconds=300.0,
        db_path=tmp_path / "test.db",
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Initialise a test database with a queued job row (sync)."""
    path = tmp_path / "test.db"
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO jobs (id, username, repo_url, branch, gpu_count, "
        "job_name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            JOB_ID,
            "testuser",
            REPO_URL,
            BRANCH,
            1,
            "test-job",
            "queued",
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00",
        ),
    )
    conn.commit()
    conn.close()
    return path


def _mock_process(returncode: int = 0) -> AsyncMock:
    """Create a mock subprocess with the given return code."""
    proc = AsyncMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.wait.return_value = returncode
    proc.communicate.return_value = (b"", b"")
    return proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_execute_success(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Successful execution completes with status=succeeded."""
    mock_exec.return_value = _mock_process(0)
    executor = JobExecutor(executor_settings)

    await executor.execute(
        JOB_ID, REPO_URL, BRANCH, gpu_count=1, timeout_seconds=None, db_path=db_path
    )

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT status, completed_at FROM jobs WHERE id=?", (JOB_ID,))
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "succeeded"
    assert row[1] is not None  # completed_at set

    # Workspace was created
    workspace = executor_settings.workspace_root / JOB_ID
    assert workspace.exists()


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.sleep", new_callable=AsyncMock)
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_execute_clone_failure_retries(
    mock_exec: AsyncMock,
    mock_sleep: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Clone retries once on failure and succeeds on second attempt."""
    # First clone call fails, second succeeds, rest succeed
    fail_proc = _mock_process(128)
    ok_proc = _mock_process(0)
    mock_exec.side_effect = [fail_proc, ok_proc, ok_proc, ok_proc, ok_proc, ok_proc, ok_proc]

    executor = JobExecutor(executor_settings)
    await executor.execute(
        JOB_ID, REPO_URL, BRANCH, gpu_count=1, timeout_seconds=None, db_path=db_path
    )

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT status FROM jobs WHERE id=?", (JOB_ID,))
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "succeeded"

    # Verify sleep was called for the retry delay
    mock_sleep.assert_awaited_with(10)


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.sleep", new_callable=AsyncMock)
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_execute_clone_failure_after_retry(
    mock_exec: AsyncMock,
    mock_sleep: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Clone fails both attempts, job transitions to failed with clone phase."""
    fail_proc = _mock_process(128)
    ok_proc = _mock_process(0)
    # Both clone attempts fail; cleanup procs succeed
    mock_exec.side_effect = [fail_proc, fail_proc, ok_proc, ok_proc, ok_proc]

    executor = JobExecutor(executor_settings)
    await executor.execute(
        JOB_ID, REPO_URL, BRANCH, gpu_count=1, timeout_seconds=None, db_path=db_path
    )

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT status, failed_phase FROM jobs WHERE id=?", (JOB_ID,))
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "failed"
    assert row[1] == "clone"


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_execute_build_failure(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Build failure sets status=failed with failed_phase=build."""
    ok_proc = _mock_process(0)
    fail_proc = _mock_process(1)
    # clone ok, build fails, cleanup procs
    mock_exec.side_effect = [ok_proc, fail_proc, ok_proc, ok_proc, ok_proc]

    executor = JobExecutor(executor_settings)
    await executor.execute(
        JOB_ID, REPO_URL, BRANCH, gpu_count=1, timeout_seconds=None, db_path=db_path
    )

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT status, failed_phase, exit_code FROM jobs WHERE id=?", (JOB_ID,)
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "failed"
    assert row[1] == "build"
    assert row[2] == 1


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_execute_run_failure(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Run failure sets status=failed with failed_phase=run."""
    ok_proc = _mock_process(0)
    fail_proc = _mock_process(1)
    # clone ok, build ok, run fails, cleanup procs
    mock_exec.side_effect = [ok_proc, ok_proc, fail_proc, ok_proc, ok_proc, ok_proc]

    executor = JobExecutor(executor_settings)
    await executor.execute(
        JOB_ID, REPO_URL, BRANCH, gpu_count=1, timeout_seconds=None, db_path=db_path
    )

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT status, failed_phase FROM jobs WHERE id=?", (JOB_ID,))
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "failed"
    assert row[1] == "run"


@pytest.mark.asyncio
@patch("ds01_jobs.executor.os.killpg")
@patch("ds01_jobs.executor.asyncio.wait_for")
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_execute_build_timeout(
    mock_exec: AsyncMock,
    mock_wait_for: AsyncMock,
    mock_killpg: MagicMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Build timeout kills process group and transitions to failed."""
    ok_proc = _mock_process(0)

    # Build proc - will be "timed out" by mocking wait_for
    timeout_proc = AsyncMock()
    timeout_proc.pid = 99999
    timeout_proc.returncode = None
    timeout_proc.wait.return_value = -9

    cleanup_proc = _mock_process(0)
    # clone ok, build times out, cleanup procs
    mock_exec.side_effect = [ok_proc, timeout_proc, cleanup_proc, cleanup_proc, cleanup_proc]

    # First wait_for call (clone) succeeds, second (build) times out
    call_count = 0

    async def selective_wait_for(coro: object, *, timeout: float) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Clone phase - return success
            return await coro  # type: ignore[misc]
        # Build phase - raise TimeoutError
        raise asyncio.TimeoutError

    mock_wait_for.side_effect = selective_wait_for

    executor = JobExecutor(executor_settings)
    await executor.execute(
        JOB_ID, REPO_URL, BRANCH, gpu_count=1, timeout_seconds=None, db_path=db_path
    )

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT status, failed_phase, error_summary FROM jobs WHERE id=?", (JOB_ID,)
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "failed"
    assert row[1] == "build"
    assert "timed out" in row[2]

    # Verify process group kill was called
    mock_killpg.assert_called()


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_status_transitions_order(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Status transitions happen in the correct order for a successful job."""
    mock_exec.return_value = _mock_process(0)
    executor = JobExecutor(executor_settings)

    # Spy on _update_status to track call order
    statuses: list[str] = []
    original_update = executor._update_status

    async def tracking_update(db_path_: Path, job_id: str, status: str, **kwargs: object) -> None:
        statuses.append(status)
        await original_update(db_path_, job_id, status, **kwargs)

    executor._update_status = tracking_update  # type: ignore[assignment]

    await executor.execute(
        JOB_ID, REPO_URL, BRANCH, gpu_count=1, timeout_seconds=None, db_path=db_path
    )

    assert statuses == ["cloning", "building", "running", "succeeded"]


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_cleanup_called_on_failure(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Cleanup subprocess calls are made even when build fails."""
    ok_proc = _mock_process(0)
    fail_proc = _mock_process(1)
    cleanup_proc = _mock_process(0)
    # clone ok, build fails, then 3 cleanup calls (rm, image rm, builder prune)
    mock_exec.side_effect = [ok_proc, fail_proc, cleanup_proc, cleanup_proc, cleanup_proc]

    executor = JobExecutor(executor_settings)
    await executor.execute(
        JOB_ID, REPO_URL, BRANCH, gpu_count=1, timeout_seconds=None, db_path=db_path
    )

    # Check cleanup calls were made (the last 3 create_subprocess_exec calls)
    calls = mock_exec.call_args_list
    docker = str(executor_settings.docker_bin)

    # Find cleanup calls - they use docker rm, docker image rm, docker builder prune
    cleanup_cmds = []
    for c in calls:
        args = c[0] if c[0] else ()
        if args and args[0] == docker:
            if len(args) > 1 and args[1] in ("rm", "image", "builder"):
                cleanup_cmds.append(args[1])

    assert "rm" in cleanup_cmds
    assert "image" in cleanup_cmds
    assert "builder" in cleanup_cmds


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_docker_bin_path_used(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """All docker subprocess calls use settings.docker_bin, not a hardcoded path."""
    mock_exec.return_value = _mock_process(0)
    executor = JobExecutor(executor_settings)

    await executor.execute(
        JOB_ID, REPO_URL, BRANCH, gpu_count=1, timeout_seconds=None, db_path=db_path
    )

    docker = str(executor_settings.docker_bin)
    for c in mock_exec.call_args_list:
        args = c[0] if c[0] else ()
        # Skip the git clone call
        if args and args[0] == "git":
            continue
        # All other calls should use the docker wrapper path
        if args:
            assert args[0] == docker, f"Expected {docker} but got {args[0]} in call {args}"


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_log_files_created(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Log files are created at expected paths for each phase."""
    mock_exec.return_value = _mock_process(0)
    executor = JobExecutor(executor_settings)

    await executor.execute(
        JOB_ID, REPO_URL, BRANCH, gpu_count=1, timeout_seconds=None, db_path=db_path
    )

    workspace = executor_settings.workspace_root / JOB_ID
    assert (workspace / "clone.log").exists()
    assert (workspace / "build.log").exists()
    assert (workspace / "run.log").exists()


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_cancel_check_stops_execution(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """If job is cancelled between phases, executor stops before the next phase."""
    ok_proc = _mock_process(0)
    cleanup_proc = _mock_process(0)
    mock_exec.side_effect = [ok_proc, cleanup_proc, cleanup_proc, cleanup_proc]

    executor = JobExecutor(executor_settings)

    # After clone succeeds, set the job status to failed (simulating cancel)
    original_check = executor._check_cancelled

    call_count = 0

    async def cancel_after_clone(db_path_: Path, job_id: str) -> bool:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First check (before build) - simulate cancel
            async with aiosqlite.connect(db_path_) as db:
                await db.execute(
                    "UPDATE jobs SET status='failed', error_summary='Cancelled by user' WHERE id=?",
                    (job_id,),
                )
                await db.commit()
            return True
        return await original_check(db_path_, job_id)

    executor._check_cancelled = cancel_after_clone  # type: ignore[assignment]

    await executor.execute(
        JOB_ID, REPO_URL, BRANCH, gpu_count=1, timeout_seconds=None, db_path=db_path
    )

    # Verify build was never started - no docker build call should exist
    calls = mock_exec.call_args_list
    docker_cmds = [c[0] for c in calls if c[0] and c[0][0] == str(executor_settings.docker_bin)]
    build_calls = [c for c in docker_cmds if len(c) > 1 and c[1] == "build"]
    assert len(build_calls) == 0, "Build should not have been called after cancel"
