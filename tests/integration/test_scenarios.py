"""Parametrised Tier 2 scenarios covering the fixture branches.

One test per scenario from ``tests/integration/fixtures/scenarios/``: submit
the job, poll to a terminal state, download results where relevant, and
verify scenario-specific output shape.

The scanner-rejection path is a separate test because it doesn't follow the
submit-poll-download pattern — it asserts the inline dockerfile is blocked
at submission time.

Requires the live API on ``http://127.0.0.1:8765`` and the self-hosted GPU
runner. Marked ``@pytest.mark.integration`` so only Tier 2 CI runs these.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

TEST_REPO_URL = "https://github.com/hertie-data-science-lab/ds01-jobs"

POLL_TIMEOUT = 600  # gpu-compute + large-output need more headroom than lifecycle
INITIAL_BACKOFF = 2
MAX_BACKOFF = 30


@dataclass(frozen=True)
class Scenario:
    name: str
    branch: str
    expected_status: str
    submit_args: tuple[str, ...] = ()
    verify: Callable[[Path], None] | None = None
    poll_timeout: int = POLL_TIMEOUT


def _verify_result_json(results: Path) -> None:
    assert (results / "result.json").is_file(), list(results.iterdir())


def _verify_training_results(results: Path) -> None:
    payload = json.loads((results / "training_results.json").read_text())
    assert "final_loss" in payload, payload


def _verify_multi_file(results: Path) -> None:
    for name in ("dataset.csv", "analysis.png", "summary.json"):
        assert (results / name).is_file(), f"missing {name}: {list(results.iterdir())}"


def _verify_large_output(results: Path) -> None:
    bins = sorted(results.glob("*.bin"))
    assert len(bins) == 50, f"expected 50 .bin files, got {len(bins)}"


def _verify_benchmark(results: Path) -> None:
    payload = json.loads((results / "benchmark.json").read_text())
    assert payload, "benchmark.json is empty"


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="cpu-quick",
        branch="fixtures/cpu-quick",
        expected_status="succeeded",
        verify=_verify_result_json,
    ),
    Scenario(
        name="long-running",
        branch="fixtures/long-running",
        expected_status="succeeded",
        verify=_verify_training_results,
    ),
    Scenario(
        name="multi-file",
        branch="fixtures/multi-file",
        expected_status="succeeded",
        verify=_verify_multi_file,
    ),
    Scenario(
        name="large-output",
        branch="fixtures/large-output",
        expected_status="succeeded",
        verify=_verify_large_output,
    ),
    Scenario(
        name="gpu-compute",
        branch="fixtures/gpu-compute",
        expected_status="succeeded",
        verify=_verify_benchmark,
        # Cold pull of the PyTorch image can take several minutes on a fresh host.
        poll_timeout=900,
    ),
    Scenario(
        name="failing-runtime",
        branch="fixtures/failing-runtime",
        expected_status="failed",
    ),
    Scenario(
        name="failing-build",
        branch="fixtures/failing-build",
        expected_status="failed",
    ),
    Scenario(
        name="timeout",
        branch="fixtures/timeout",
        expected_status="failed",
        submit_args=("--timeout", "60"),
    ),
)


def _poll_for_terminal(job_id: str, env: dict[str, str], runner, timeout: int) -> tuple[str, dict]:
    backoff = INITIAL_BACKOFF
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = runner(["ds01-submit", "status", job_id, "--json"], env=env)
        assert result.returncode in (0, 2), f"Status check failed: {result.stderr}"
        status_data = json.loads(result.stdout)
        if status_data["status"] in ("succeeded", "failed"):
            return status_data["status"], status_data
        time.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF)
    pytest.fail(f"Job {job_id} did not reach terminal state within {timeout}s")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_scenario(
    scenario: Scenario,
    api_key: str,
    api_base_url: str,
    subprocess_runner,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    env = {"DS01_API_KEY": api_key, "DS01_API_URL": api_base_url}

    submit_cmd = [
        "ds01-submit",
        "run",
        TEST_REPO_URL,
        "--branch",
        scenario.branch,
        "--gpus",
        "1",
        *scenario.submit_args,
        "--json",
    ]
    result = subprocess_runner(submit_cmd, env=env)
    assert result.returncode == 0, f"Submit failed ({scenario.name}): {result.stderr}"
    job_id = json.loads(result.stdout)["job_id"]

    final_status, status_data = _poll_for_terminal(
        job_id, env, subprocess_runner, scenario.poll_timeout
    )
    assert final_status == scenario.expected_status, (
        f"{scenario.name}: expected {scenario.expected_status}, got {final_status}. "
        f"Status: {json.dumps(status_data, indent=2)}"
    )

    if scenario.verify is not None:
        out_dir = tmp_path_factory.mktemp(scenario.name)
        result = subprocess_runner(["ds01-submit", "results", job_id, "-o", str(out_dir)], env=env)
        assert result.returncode == 0, f"Results download failed: {result.stderr}"
        results_subdir = out_dir / "results"
        assert results_subdir.is_dir(), f"no results/ subdir under {out_dir}"
        scenario.verify(results_subdir)


def test_scanner_rejects_disallowed_base_image(
    api_key: str,
    api_base_url: str,
    subprocess_runner,
    tmp_path: Path,
) -> None:
    """Inline Dockerfile with a non-allowlisted base image is rejected at submit.

    The scanner only applies to ``--dockerfile`` overrides, not repo
    Dockerfiles. We therefore pair the scanner-probe with an innocuous
    fixture branch (``fixtures/cpu-quick``); the override is what drives the
    rejection.
    """
    env = {"DS01_API_KEY": api_key, "DS01_API_URL": api_base_url}

    bad_dockerfile = tmp_path / "Dockerfile.bad"
    bad_dockerfile.write_text('FROM bitnami/python:latest\nCMD echo "never runs"\n')

    result = subprocess_runner(
        [
            "ds01-submit",
            "run",
            TEST_REPO_URL,
            "--branch",
            "fixtures/cpu-quick",
            "--gpus",
            "1",
            "--dockerfile",
            str(bad_dockerfile),
            "--json",
        ],
        env=env,
    )
    assert result.returncode != 0, (
        f"Submission should have been rejected by the scanner. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
