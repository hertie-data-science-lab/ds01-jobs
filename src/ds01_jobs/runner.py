"""DS01 job runner - polls SQLite and executes Docker jobs.

Long-running service that polls for queued jobs, checks GPU availability,
dispatches jobs via JobExecutor, and handles graceful shutdown.
"""

import asyncio
import logging
import signal
from datetime import UTC, datetime

import aiosqlite

from ds01_jobs.config import Settings
from ds01_jobs.executor import JobExecutor
from ds01_jobs.gpu import get_available_gpu_count

logger = logging.getLogger(__name__)


class JobRunner:
    """Async poll-dispatch loop for queued GPU jobs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.shutdown_event = asyncio.Event()
        self.active_jobs: dict[str, asyncio.Task[None]] = {}
        self.active_executors: dict[str, JobExecutor] = {}

    async def run(self) -> None:
        """Main entry point - poll loop with signal handling and shutdown drain."""
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, self._handle_sigterm)
        loop.add_signal_handler(signal.SIGINT, self._handle_sigterm)

        await self._recover_orphaned_jobs()

        while not self.shutdown_event.is_set():
            await self._poll_and_dispatch()
            self._cleanup_completed_tasks()
            try:
                await asyncio.wait_for(
                    self.shutdown_event.wait(),
                    timeout=self.settings.runner_poll_interval,
                )
            except asyncio.TimeoutError:
                pass  # Normal - poll interval elapsed

        active_count = len(self.active_jobs)
        if active_count > 0:
            logger.info("Shutting down, waiting for %d active jobs to drain...", active_count)
            await asyncio.gather(*self.active_jobs.values(), return_exceptions=True)

        logger.info("Runner stopped.")

    def _handle_sigterm(self) -> None:
        """Signal handler for SIGTERM and SIGINT."""
        self.shutdown_event.set()
        logger.info("SIGTERM received, draining active jobs...")

    async def _recover_orphaned_jobs(self) -> None:
        """Mark in-progress jobs as failed on startup (orphan recovery)."""
        async with aiosqlite.connect(self.settings.db_path) as db:
            now = datetime.now(UTC).isoformat()
            cursor = await db.execute(
                "UPDATE jobs SET status='failed', updated_at=?, "
                "error_summary='Runner restarted - job interrupted' "
                "WHERE status IN ('cloning', 'building', 'running')",
                (now,),
            )
            if cursor.rowcount > 0:
                logger.info("Recovered %d orphaned jobs on startup", cursor.rowcount)
            await db.commit()

    def _cleanup_completed_tasks(self) -> None:
        """Remove finished tasks from active_jobs and log any exceptions."""
        done_ids = [jid for jid, task in self.active_jobs.items() if task.done()]
        for jid in done_ids:
            task = self.active_jobs.pop(jid)
            self.active_executors.pop(jid, None)
            exc = task.exception() if not task.cancelled() else None
            if exc is not None:
                logger.error("Job %s task raised exception: %s", jid, exc)

    async def _poll_and_dispatch(self) -> None:
        """Query queued jobs and dispatch those that fit available GPUs."""
        available = await get_available_gpu_count()
        if available <= 0:
            return

        async with aiosqlite.connect(self.settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, username, repo_url, branch, gpu_count, timeout_seconds "
                "FROM jobs WHERE status='queued' ORDER BY created_at ASC"
            )
            rows = await cursor.fetchall()

        for row in rows:
            job_id: str = row["id"]
            gpu_count: int = row["gpu_count"]

            if gpu_count > available:
                continue

            if job_id in self.active_jobs:
                continue

            executor = JobExecutor(self.settings)
            task = asyncio.create_task(
                executor.execute(
                    job_id,
                    row["repo_url"],
                    row["branch"],
                    gpu_count,
                    row["timeout_seconds"],
                    self.settings.db_path,
                )
            )
            self.active_jobs[job_id] = task
            self.active_executors[job_id] = executor
            available -= gpu_count

            if available <= 0:
                break

    async def cancel_job(self, job_id: str) -> bool:
        """Kill a running job's executor process.

        Returns True if the job was found and killed, False otherwise.
        """
        executor = self.active_executors.get(job_id)
        if executor is not None:
            await executor.kill_current_process(job_id)
            self.active_executors.pop(job_id, None)
            return True
        return False


def cli_main() -> None:
    """CLI entry point for ds01-job-runner."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings()
    runner = JobRunner(settings)
    asyncio.run(runner.run())
