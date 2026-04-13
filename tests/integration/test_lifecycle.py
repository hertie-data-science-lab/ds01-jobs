"""Full job lifecycle integration test - requires GPU runner (Tier 2).

Exercises the complete pipeline on the self-hosted GPU runner:
    key-create -> submit -> poll -> verify succeeded -> download results

Prerequisites:
    - ds01-jobs API running at http://127.0.0.1:8765
    - ds01-job-runner active and connected
    - Docker available with GPU access
    - The ``fixtures/smoke`` branch must exist on origin (this repo).
      Source-of-truth lives at ``tests/integration/fixtures/scenarios/smoke/``;
      publish via ``scripts/sync-test-fixtures.sh``.

Marked ``@pytest.mark.integration`` so it only runs on the self-hosted GPU
runner via Tier 2 CI.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

pytestmark = pytest.mark.integration

TEST_REPO_URL = "https://github.com/hertie-data-science-lab/ds01-jobs"
TEST_REPO_BRANCH = "fixtures/smoke"

API_BASE_URL = "http://127.0.0.1:8765"

# Maximum time to wait for a job to reach a terminal state (seconds)
POLL_TIMEOUT = 300

# Backoff parameters for polling (seconds)
INITIAL_BACKOFF = 2
MAX_BACKOFF = 30


def _run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with merged environment and return the result."""
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(args, capture_output=True, text=True, env=merged_env)


@pytest.fixture()
def api_key() -> str:
    """Return the pre-provisioned CI API key.

    Reads ``DS01_CI_API_KEY`` from the environment (set via the ``DS01_CI_API_KEY``
    GitHub Actions secret). The key is issued once by an admin via::

        ds01-job-admin key-create ds01-ci-bot[bot] datasciencelab --expires 365d --json

    and stored as a repository secret. Tests are skipped if the variable is unset
    (e.g. local runs without the secret configured).
    """
    key = os.environ.get("DS01_CI_API_KEY")
    if not key:
        pytest.skip(
            "DS01_CI_API_KEY not set — provision via 'ds01-job-admin key-create' "
            "and store as a GitHub Actions secret"
        )
    return key


@pytest.fixture()
def api_base_url() -> str:
    """Return the local API base URL."""
    return API_BASE_URL


def test_full_job_lifecycle(
    api_key: str,
    api_base_url: str,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Submit a job, wait for completion, verify success, download results."""
    env = {
        "DS01_API_KEY": api_key,
        "DS01_API_URL": api_base_url,
    }

    # 1. Submit a job via ds01-submit run
    result = _run(
        ["ds01-submit", "run", TEST_REPO_URL, "--branch", TEST_REPO_BRANCH, "--json"],
        env=env,
    )
    assert result.returncode == 0, f"Job submission failed: {result.stderr}"

    submit_data = json.loads(result.stdout)
    job_id = submit_data["job_id"]
    assert job_id, "No job_id returned from submission"

    # 2. Poll status until terminal state (max 5 minutes)
    backoff = INITIAL_BACKOFF
    deadline = time.monotonic() + POLL_TIMEOUT
    final_status = None

    while time.monotonic() < deadline:
        result = _run(["ds01-submit", "status", job_id, "--json"], env=env)
        assert result.returncode in (0, 2), f"Status check failed: {result.stderr}"

        status_data = json.loads(result.stdout)
        current_status = status_data["status"]

        if current_status in ("succeeded", "failed"):
            final_status = current_status
            break

        time.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF)
    else:
        pytest.fail(f"Job {job_id} did not reach terminal state within {POLL_TIMEOUT}s")

    # 3. Assert the job succeeded
    assert final_status == "succeeded", (
        f"Job {job_id} ended with status '{final_status}', expected 'succeeded'. "
        f"Status data: {json.dumps(status_data, indent=2)}"
    )

    # 4. Download results
    output_dir = tmp_path / "results"
    result = _run(
        ["ds01-submit", "results", job_id, "-o", str(output_dir)],
        env=env,
    )
    assert result.returncode == 0, f"Results download failed: {result.stderr}"

    # 5. Verify output directory is not empty
    assert output_dir.exists(), f"Output directory {output_dir} does not exist"
    result_files = list(output_dir.rglob("*"))
    assert len(result_files) > 0, f"Output directory {output_dir} is empty"
