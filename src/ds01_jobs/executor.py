"""Job executor - runs a single job through clone, build, run, collect, cleanup."""

import asyncio
import json
import logging
import os
import signal
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from ds01_jobs.config import Settings

logger = logging.getLogger(__name__)


class PhaseError(Exception):
    """A job phase (clone/build/run) failed with a non-zero exit code."""

    def __init__(self, phase: str, exit_code: int, summary: str) -> None:
        self.phase = phase
        self.exit_code = exit_code
        self.summary = summary
        super().__init__(summary)


class PhaseTimeoutError(PhaseError):
    """A job phase exceeded its timeout and was killed."""

    def __init__(self, phase: str, timeout: float) -> None:
        super().__init__(phase, -1, f"{phase} timed out after {timeout:.0f}s")
        self.timeout = timeout


class JobExecutor:
    """Executes a single job through the clone -> build -> run pipeline."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._current_process: asyncio.subprocess.Process | None = None
        self._unix_username: str = ""

    def _sudo_docker(self, unix_username: str) -> list[str]:
        """Return the sudo -u {unix_username} docker prefix."""
        return ["sudo", "-u", unix_username, str(self.settings.docker_bin)]

    async def _get_resource_limits(self, unix_username: str) -> list[str]:
        """Get Docker resource limit args from get_resource_limits.py.

        Returns a list of Docker CLI args (e.g. ['--memory=32g', '--shm-size=16g']).
        Strips --cgroup-parent since the Docker wrapper injects it automatically.
        Returns empty list on failure (job runs with wrapper defaults).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3",
                str(self.settings.get_resource_limits_bin),
                unix_username,
                "--docker-args",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode != 0:
                logger.warning(
                    "get_resource_limits.py failed for %s: %s",
                    unix_username,
                    stderr.decode().strip(),
                )
                return []
            args = stdout.decode().strip().split()
            return [a for a in args if not a.startswith("--cgroup-parent=")]
        except (TimeoutError, FileNotFoundError, OSError) as exc:
            logger.warning("get_resource_limits.py error for %s: %s", unix_username, exc)
            return []

    async def execute(
        self,
        job_id: str,
        repo_url: str,
        branch: str,
        gpu_count: int,
        timeout_seconds: int | None,
        db_path: Path,
        unix_username: str = "",
    ) -> None:
        """Run a job through the full execution pipeline.

        Args:
            job_id: Unique job identifier.
            repo_url: Git repository URL to clone.
            branch: Git branch to check out.
            gpu_count: Number of GPUs requested.
            timeout_seconds: Job run timeout (None = use default).
            db_path: Path to the SQLite database.
            unix_username: Unix username for sudo -u Docker execution.
        """
        self._unix_username = unix_username
        workspace = self.settings.workspace_root / job_id
        workspace.mkdir(parents=True, exist_ok=True)

        try:
            # Set started_at and record queued phase timestamp
            async with aiosqlite.connect(db_path) as db:
                now = datetime.now(UTC).isoformat()
                # Read existing created_at for the queued started_at
                cursor = await db.execute("SELECT created_at FROM jobs WHERE id=?", (job_id,))
                row = await cursor.fetchone()
                created_at = row[0] if row else now
                timestamps = {"queued": {"started_at": created_at, "ended_at": now}}
                await db.execute(
                    "UPDATE jobs SET started_at=?, updated_at=?, phase_timestamps=? WHERE id=?",
                    (now, now, json.dumps(timestamps), job_id),
                )
                await db.commit()

            await self._clone(job_id, repo_url, branch, workspace, db_path)
            await self._build(job_id, workspace, db_path, unix_username)
            await self._run_container(
                job_id, workspace, gpu_count, timeout_seconds, db_path, unix_username
            )
            await self._collect_results(job_id, workspace)

            # Success
            await self._update_status(db_path, job_id, "succeeded")
            logger.info("Job %s succeeded", job_id)

        except PhaseError as exc:
            logger.error("Job %s failed in %s: %s", job_id, exc.phase, exc.summary)
            await self._update_status(
                db_path,
                job_id,
                "failed",
                failed_phase=exc.phase,
                exit_code=exc.exit_code,
                error_summary=exc.summary,
            )
        except Exception:
            logger.exception("Job %s failed with unexpected error", job_id)
            await self._update_status(
                db_path,
                job_id,
                "failed",
                error_summary="Unexpected executor error",
            )
        finally:
            await self._cleanup(job_id)
            self._unix_username = ""

    async def _update_status(
        self,
        db_path: Path,
        job_id: str,
        status: str,
        *,
        failed_phase: str | None = None,
        exit_code: int | None = None,
        error_summary: str | None = None,
    ) -> None:
        """Atomically update job status in SQLite."""
        now = datetime.now(UTC).isoformat()
        phase_order = ["cloning", "building", "running"]

        async with aiosqlite.connect(db_path) as db:
            # Read current phase_timestamps
            cursor = await db.execute("SELECT phase_timestamps FROM jobs WHERE id=?", (job_id,))
            row = await cursor.fetchone()
            timestamps: dict[str, dict[str, str | None]] = (
                json.loads(row[0]) if row and row[0] else {}
            )

            # Update timestamps based on the new status
            if status in phase_order:
                # Close the previous phase if applicable
                idx = phase_order.index(status)
                if idx > 0:
                    prev_phase = phase_order[idx - 1]
                    if prev_phase in timestamps and timestamps[prev_phase].get("ended_at") is None:
                        timestamps[prev_phase]["ended_at"] = now
                # Start the new phase
                timestamps[status] = {"started_at": now, "ended_at": None}
            elif status in ("succeeded", "failed"):
                # Close whatever phase was last active
                for phase in reversed(phase_order):
                    if phase in timestamps and timestamps[phase].get("ended_at") is None:
                        timestamps[phase]["ended_at"] = now
                        break

            ts_json = json.dumps(timestamps)

            if status in ("succeeded", "failed"):
                await db.execute(
                    "UPDATE jobs SET status=?, updated_at=?, completed_at=?, "
                    "failed_phase=?, exit_code=?, error_summary=?, phase_timestamps=? WHERE id=?",
                    (status, now, now, failed_phase, exit_code, error_summary, ts_json, job_id),
                )
            elif status == "cloning":
                await db.execute(
                    "UPDATE jobs SET status=?, updated_at=?, started_at=?, "
                    "failed_phase=?, exit_code=?, error_summary=?, phase_timestamps=? WHERE id=?",
                    (status, now, now, failed_phase, exit_code, error_summary, ts_json, job_id),
                )
            else:
                await db.execute(
                    "UPDATE jobs SET status=?, updated_at=?, "
                    "failed_phase=?, exit_code=?, error_summary=?, phase_timestamps=? WHERE id=?",
                    (status, now, failed_phase, exit_code, error_summary, ts_json, job_id),
                )
            await db.commit()

    async def _check_cancelled(self, db_path: Path, job_id: str) -> bool:
        """Check if the job has been cancelled (status set to failed externally)."""
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT status FROM jobs WHERE id=?", (job_id,))
            row = await cursor.fetchone()
            if row and row[0] == "failed":
                return True
        return False

    async def _run_phase(
        self,
        job_id: str,
        cmd: list[str],
        log_path: Path,
        timeout: float,
    ) -> int:
        """Execute a subprocess phase with process group isolation and timeout.

        Args:
            job_id: Job identifier (for logging).
            cmd: Command and arguments to execute.
            log_path: Path to write stdout/stderr.
            timeout: Maximum seconds before killing the process group.

        Returns:
            The subprocess return code.

        Raises:
            PhaseTimeoutError: If the process exceeds the timeout.
        """
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w") as log_file:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                process_group=0,
            )
            self._current_process = proc
            try:
                returncode = await asyncio.wait_for(proc.wait(), timeout=timeout)
                return returncode
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
                raise
            finally:
                self._current_process = None

    async def _clone(
        self,
        job_id: str,
        repo_url: str,
        branch: str,
        workspace: Path,
        db_path: Path,
    ) -> None:
        """Clone repository with shallow depth, retrying once on failure."""
        await self._update_status(db_path, job_id, "cloning")

        cmd = [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            branch,
            repo_url,
            str(workspace / "repo"),
        ]
        log_path = workspace / "clone.log"

        exit_code = await self._run_phase(
            job_id, cmd, log_path, self.settings.clone_timeout_seconds
        )

        if exit_code != 0:
            logger.warning("Job %s clone failed (exit %d), retrying in 10s", job_id, exit_code)
            await asyncio.sleep(10)
            exit_code = await self._run_phase(
                job_id, cmd, log_path, self.settings.clone_timeout_seconds
            )
            if exit_code != 0:
                raise PhaseError("clone", exit_code, "Clone failed after retry")

    async def _build(
        self, job_id: str, workspace: Path, db_path: Path, unix_username: str = ""
    ) -> None:
        """Build Docker image from the repo Dockerfile."""
        if await self._check_cancelled(db_path, job_id):
            raise PhaseError("build", -1, "Job cancelled before build")

        await self._update_status(db_path, job_id, "building")

        image_tag = f"ds01-job-{job_id}"
        if unix_username:
            docker_prefix = self._sudo_docker(unix_username)
        else:
            docker_prefix = [str(self.settings.docker_bin)]
        cmd = [
            *docker_prefix,
            "build",
            "-t",
            image_tag,
            "-f",
            str(workspace / "repo" / "Dockerfile"),
            str(workspace / "repo"),
        ]
        log_path = workspace / "build.log"

        try:
            exit_code = await self._run_phase(
                job_id, cmd, log_path, self.settings.build_timeout_seconds
            )
        except asyncio.TimeoutError:
            raise PhaseTimeoutError("build", self.settings.build_timeout_seconds)

        if exit_code != 0:
            raise PhaseError("build", exit_code, "Docker build failed")

    async def _run_container(
        self,
        job_id: str,
        workspace: Path,
        gpu_count: int,
        timeout_seconds: int | None,
        db_path: Path,
        unix_username: str = "",
    ) -> None:
        """Run the Docker container with GPU access."""
        if await self._check_cancelled(db_path, job_id):
            raise PhaseError("run", -1, "Job cancelled before run")

        await self._update_status(db_path, job_id, "running")

        image_tag = f"ds01-job-{job_id}"
        container_name = f"ds01-job-{job_id}"

        # Resolve timeout
        resolved_timeout = float(
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.default_job_timeout_seconds
        )
        resolved_timeout = min(resolved_timeout, self.settings.max_job_timeout_seconds)

        # Get per-user resource limits
        resource_args: list[str] = []
        if unix_username:
            resource_args = await self._get_resource_limits(unix_username)
            docker_prefix = self._sudo_docker(unix_username)
        else:
            docker_prefix = [str(self.settings.docker_bin)]

        cmd = [
            *docker_prefix,
            "run",
            "--name",
            container_name,
            "--label",
            "ds01.interface=api",
            *resource_args,
            "--gpus",
            "all",
            image_tag,
        ]
        log_path = workspace / "run.log"

        try:
            exit_code = await self._run_phase(job_id, cmd, log_path, resolved_timeout)
        except asyncio.TimeoutError:
            raise PhaseTimeoutError("run", resolved_timeout)

        if exit_code != 0:
            raise PhaseError("run", exit_code, "Container exited with error")

    async def _collect_results(self, job_id: str, workspace: Path) -> None:
        """Copy results from container /output/ to workspace/results/."""
        container_name = f"ds01-job-{job_id}"
        results_dir = workspace / "results"
        results_dir.mkdir(exist_ok=True)

        unix_username = self._unix_username
        if unix_username:
            docker_prefix = self._sudo_docker(unix_username)
        else:
            docker_prefix = [str(self.settings.docker_bin)]

        proc = await asyncio.create_subprocess_exec(
            *docker_prefix,
            "cp",
            f"{container_name}:/output/.",
            str(results_dir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.warning("Failed to collect results for %s: %s", job_id, stderr.decode().strip())

    async def _cleanup(self, job_id: str) -> None:
        """Remove container, image, and prune build cache."""
        unix_username = self._unix_username
        if unix_username:
            docker_prefix = self._sudo_docker(unix_username)
        else:
            docker_prefix = [str(self.settings.docker_bin)]

        # Remove container
        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_prefix,
                "rm",
                "-f",
                f"ds01-job-{job_id}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            logger.debug("Failed to remove container for job %s", job_id, exc_info=True)

        # Remove image
        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_prefix,
                "image",
                "rm",
                "-f",
                f"ds01-job-{job_id}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            logger.debug("Failed to remove image for job %s", job_id, exc_info=True)

        # Prune build cache
        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_prefix,
                "builder",
                "prune",
                "--force",
                "--filter",
                "until=1h",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            logger.debug("Failed to prune build cache for job %s", job_id, exc_info=True)

    async def kill_current_process(self, job_id: str) -> None:
        """Kill the currently running subprocess and its Docker container.

        Used by the runner for cancel and shutdown.
        """
        proc = self._current_process
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()

        # Also force-remove any Docker container (survives process group kill)
        unix_username = self._unix_username
        if unix_username:
            docker_prefix = self._sudo_docker(unix_username)
        else:
            docker_prefix = [str(self.settings.docker_bin)]
        try:
            rm_proc = await asyncio.create_subprocess_exec(
                *docker_prefix,
                "rm",
                "-f",
                f"ds01-job-{job_id}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await rm_proc.wait()
        except Exception:
            pass
