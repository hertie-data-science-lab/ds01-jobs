"""Unit tests for the job executor module."""

import asyncio
import json
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
UNIX_USER = "testuser"


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
        "INSERT INTO jobs (id, username, unix_username, repo_url, branch, gpu_count, "
        "job_name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            JOB_ID,
            "testuser",
            UNIX_USER,
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
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
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
    # clone fail, clone retry ok, build ok, get_resource_limits ok, run ok, cp ok, cleanup x3
    fail_proc = _mock_process(128)
    ok_proc = _mock_process(0)
    mock_exec.side_effect = [
        fail_proc,
        ok_proc,
        ok_proc,
        ok_proc,
        ok_proc,
        ok_proc,
        ok_proc,
        ok_proc,
    ]

    executor = JobExecutor(executor_settings)
    await executor.execute(
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
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
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
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
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
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
    # clone ok, build ok, get_resource_limits ok, run fails, cleanup procs
    mock_exec.side_effect = [ok_proc, ok_proc, ok_proc, fail_proc, ok_proc, ok_proc, ok_proc]

    executor = JobExecutor(executor_settings)
    await executor.execute(
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
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
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
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
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
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
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
    )

    # Check cleanup calls were made (the last 3 create_subprocess_exec calls)
    calls = mock_exec.call_args_list
    docker = str(executor_settings.docker_bin)

    # Find cleanup calls - they use sudo -u ... docker rm/image/builder
    cleanup_cmds = []
    for c in calls:
        args = c[0] if c[0] else ()
        # With sudo prefix, docker bin is at index 3, subcommand at index 4
        if len(args) >= 5 and args[3] == docker:
            cleanup_cmds.append(args[4])

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
    """All docker subprocess calls use settings.docker_bin via sudo -u prefix."""
    mock_exec.return_value = _mock_process(0)
    executor = JobExecutor(executor_settings)

    await executor.execute(
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
    )

    docker = str(executor_settings.docker_bin)
    for c in mock_exec.call_args_list:
        args = c[0] if c[0] else ()
        # Skip the git clone call and get_resource_limits call
        if args and args[0] in ("git", "python3"):
            continue
        # All docker calls should have docker_bin at index 3 (after sudo -u user)
        if args:
            assert docker in args, f"Expected {docker} in call {args}"


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
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
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
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
    )

    # Verify build was never started - no docker build call should exist
    calls = mock_exec.call_args_list
    build_calls = [
        c[0]
        for c in calls
        if c[0] and "build" in c[0] and str(executor_settings.docker_bin) in c[0]
    ]
    assert len(build_calls) == 0, "Build should not have been called after cancel"


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_phase_timestamps_recorded(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Successful execution records phase timestamps for queued, cloning, building, running."""
    mock_exec.return_value = _mock_process(0)
    executor = JobExecutor(executor_settings)

    await executor.execute(
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
    )

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT phase_timestamps FROM jobs WHERE id=?", (JOB_ID,))
        row = await cursor.fetchone()

    assert row is not None
    timestamps = json.loads(row[0])

    # All four phases should be present
    for phase in ("queued", "cloning", "building", "running"):
        assert phase in timestamps, f"Missing phase: {phase}"
        assert "started_at" in timestamps[phase], f"{phase} missing started_at"

    # Completed phases should have ended_at set
    for phase in ("queued", "cloning", "building", "running"):
        assert timestamps[phase]["ended_at"] is not None, f"{phase} missing ended_at"


# ---------------------------------------------------------------------------
# New tests for sudo -u, resource limits, and interface label
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_build_uses_sudo_prefix(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Docker build command starts with sudo -u {unix_username} {docker_bin}."""
    mock_exec.return_value = _mock_process(0)
    executor = JobExecutor(executor_settings)

    await executor.execute(
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
    )

    docker = str(executor_settings.docker_bin)
    # Find the build call - it has "build" in args
    build_call = None
    for c in mock_exec.call_args_list:
        args = c[0] if c[0] else ()
        if "build" in args and docker in args:
            build_call = args
            break

    assert build_call is not None, "No docker build call found"
    assert build_call[:4] == ("sudo", "-u", UNIX_USER, docker)
    assert build_call[4] == "build"


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_run_uses_sudo_with_interface_label(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Docker run includes sudo -u prefix and --label ds01.interface=api."""
    mock_exec.return_value = _mock_process(0)
    executor = JobExecutor(executor_settings)

    await executor.execute(
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
    )

    docker = str(executor_settings.docker_bin)
    # Find the run call
    run_call = None
    for c in mock_exec.call_args_list:
        args = c[0] if c[0] else ()
        if "run" in args and docker in args:
            run_call = args
            break

    assert run_call is not None, "No docker run call found"
    # Verify sudo prefix
    assert run_call[:4] == ("sudo", "-u", UNIX_USER, docker)
    assert run_call[4] == "run"
    # Verify interface label
    assert "--label" in run_call
    label_idx = run_call.index("--label")
    assert run_call[label_idx + 1] == "ds01.interface=api"


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_run_includes_resource_limits(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Docker run includes resource limit args from _get_resource_limits."""
    mock_exec.return_value = _mock_process(0)
    executor = JobExecutor(executor_settings)

    # Mock _get_resource_limits to return specific args
    with patch.object(
        executor,
        "_get_resource_limits",
        new_callable=AsyncMock,
        return_value=["--memory=32g", "--shm-size=16g"],
    ):
        await executor.execute(
            JOB_ID,
            REPO_URL,
            BRANCH,
            gpu_count=1,
            timeout_seconds=None,
            db_path=db_path,
            unix_username=UNIX_USER,
        )

    docker = str(executor_settings.docker_bin)
    # Find the run call
    run_call = None
    for c in mock_exec.call_args_list:
        args = c[0] if c[0] else ()
        if "run" in args and docker in args:
            run_call = args
            break

    assert run_call is not None, "No docker run call found"
    assert "--memory=32g" in run_call
    assert "--shm-size=16g" in run_call


async def _passthrough_wait_for(coro: object, **_kwargs: object) -> object:
    """Await and return the coroutine (bypass real timeout)."""
    return await coro  # type: ignore[misc]


@pytest.mark.asyncio
async def test_get_resource_limits_strips_cgroup_parent(
    executor_settings: Settings,
) -> None:
    """_get_resource_limits strips --cgroup-parent from output."""
    executor = JobExecutor(executor_settings)

    # Mock the subprocess to return output with --cgroup-parent
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (
        b"--cpus=32 --memory=32g --cgroup-parent=ds01-student-testuser.slice --shm-size=16g",
        b"",
    )
    mock_proc.returncode = 0

    with patch("ds01_jobs.executor.asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("ds01_jobs.executor.asyncio.wait_for", side_effect=_passthrough_wait_for):
            result = await executor._get_resource_limits(UNIX_USER)

    assert "--cpus=32" in result
    assert "--memory=32g" in result
    assert "--shm-size=16g" in result
    # cgroup-parent should be stripped
    assert not any(a.startswith("--cgroup-parent=") for a in result)


@pytest.mark.asyncio
async def test_get_resource_limits_fallback_on_failure(
    executor_settings: Settings,
) -> None:
    """_get_resource_limits returns empty list when subprocess fails."""
    executor = JobExecutor(executor_settings)

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"error message")
    mock_proc.returncode = 1

    with patch("ds01_jobs.executor.asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("ds01_jobs.executor.asyncio.wait_for", side_effect=_passthrough_wait_for):
            result = await executor._get_resource_limits(UNIX_USER)

    assert result == []


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_cleanup_uses_sudo(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """Cleanup commands (rm, image rm, builder prune) all use sudo -u prefix."""
    ok_proc = _mock_process(0)
    fail_proc = _mock_process(1)
    cleanup_proc = _mock_process(0)
    # clone ok, build fails, then 3 cleanup calls
    mock_exec.side_effect = [ok_proc, fail_proc, cleanup_proc, cleanup_proc, cleanup_proc]

    executor = JobExecutor(executor_settings)
    await executor.execute(
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
    )

    docker = str(executor_settings.docker_bin)
    calls = mock_exec.call_args_list

    # Get only cleanup calls (after git clone and docker build)
    # Cleanup calls are docker rm, docker image rm, docker builder prune
    cleanup_calls = []
    for c in calls:
        args = c[0] if c[0] else ()
        if len(args) >= 5 and args[0] == "sudo" and args[3] == docker:
            subcmd = args[4]
            if subcmd in ("rm", "image", "builder"):
                cleanup_calls.append(args)

    assert len(cleanup_calls) == 3, f"Expected 3 cleanup calls, got {len(cleanup_calls)}"

    for call_args in cleanup_calls:
        assert call_args[:4] == ("sudo", "-u", UNIX_USER, docker), (
            f"Cleanup call missing sudo prefix: {call_args}"
        )


@pytest.mark.asyncio
@patch("ds01_jobs.executor.asyncio.create_subprocess_exec")
async def test_collect_results_uses_sudo(
    mock_exec: AsyncMock,
    executor_settings: Settings,
    db_path: Path,
) -> None:
    """docker cp for results collection uses sudo -u prefix."""
    mock_exec.return_value = _mock_process(0)
    executor = JobExecutor(executor_settings)

    await executor.execute(
        JOB_ID,
        REPO_URL,
        BRANCH,
        gpu_count=1,
        timeout_seconds=None,
        db_path=db_path,
        unix_username=UNIX_USER,
    )

    docker = str(executor_settings.docker_bin)
    # Find the cp call
    cp_call = None
    for c in mock_exec.call_args_list:
        args = c[0] if c[0] else ()
        if "cp" in args and docker in args:
            cp_call = args
            break

    assert cp_call is not None, "No docker cp call found"
    assert cp_call[:4] == ("sudo", "-u", UNIX_USER, docker)
