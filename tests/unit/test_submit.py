"""Tests for ds01_jobs.submit module - ds01-submit CLI commands."""

import io
import json
import tarfile
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from ds01_jobs.submit import app

runner = CliRunner()


# --- Fixtures ---


@pytest.fixture()
def mock_api_key(monkeypatch):
    """Set a fake API key in the environment."""
    monkeypatch.setenv("DS01_API_KEY", "ds01_testkey123")
    monkeypatch.setenv("DS01_API_URL", "http://localhost:8765")


def _mock_response(status_code=200, json_data=None, text=""):
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx

        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="error", request=MagicMock(), response=resp
        )
    return resp


# --- configure tests ---


@patch("ds01_jobs.submit.DS01Client")
def test_configure_success(MockClient, tmp_path, monkeypatch):
    """configure validates key and saves credentials."""
    monkeypatch.delenv("DS01_API_KEY", raising=False)
    monkeypatch.setenv("DS01_API_URL", "http://localhost:8765")
    creds_path = tmp_path / "credentials"
    monkeypatch.setattr("ds01_jobs.submit.CREDENTIALS_PATH", creds_path)
    monkeypatch.setattr("ds01_jobs.client.CREDENTIALS_PATH", creds_path)

    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(
        json_data={"username": "researcher1", "group": "lab-a"}
    )
    MockClient.return_value = mock_client

    result = runner.invoke(app, ["configure"], input="ds01_mykey\n")
    assert result.exit_code == 0
    assert "Authenticated as researcher1" in result.output
    assert "group: lab-a" in result.output
    assert creds_path.read_text() == "ds01_mykey"


@patch("ds01_jobs.submit.DS01Client")
def test_configure_invalid_key(MockClient, monkeypatch):
    """configure with invalid key prints error and exits 1."""
    monkeypatch.delenv("DS01_API_KEY", raising=False)
    monkeypatch.setenv("DS01_API_URL", "http://localhost:8765")

    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(status_code=401)
    MockClient.return_value = mock_client

    result = runner.invoke(app, ["configure"], input="bad_key\n")
    assert result.exit_code == 1
    assert "Invalid or expired API key" in result.output


# --- run tests ---


@patch("ds01_jobs.submit._get_client")
def test_run_prints_job_id_only(mock_get_client, mock_api_key):
    """run prints only the job ID by default (pipeable)."""
    mock_client = MagicMock()
    mock_client.post.return_value = _mock_response(
        status_code=202,
        json_data={
            "job_id": "job-abc123",
            "status": "queued",
            "status_url": "/api/v1/jobs/job-abc123",
            "created_at": "2026-03-10T00:00:00Z",
        },
    )
    mock_get_client.return_value = mock_client

    result = runner.invoke(app, ["run", "https://github.com/org/repo"])
    assert result.exit_code == 0
    assert result.output.strip() == "job-abc123"


@patch("ds01_jobs.submit._get_client")
def test_run_json_output(mock_get_client, mock_api_key):
    """run --json prints full JSON response."""
    response_data = {
        "job_id": "job-abc123",
        "status": "queued",
        "status_url": "/api/v1/jobs/job-abc123",
        "created_at": "2026-03-10T00:00:00Z",
    }
    mock_client = MagicMock()
    mock_client.post.return_value = _mock_response(status_code=202, json_data=response_data)
    mock_get_client.return_value = mock_client

    result = runner.invoke(app, ["run", "https://github.com/org/repo", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["job_id"] == "job-abc123"


@patch("ds01_jobs.submit._get_client")
def test_run_with_options(mock_get_client, mock_api_key):
    """run passes --gpus, --branch, --name to the API."""
    mock_client = MagicMock()
    mock_client.post.return_value = _mock_response(
        status_code=202,
        json_data={"job_id": "job-xyz", "status": "queued", "status_url": "/", "created_at": "now"},
    )
    mock_get_client.return_value = mock_client

    result = runner.invoke(
        app,
        [
            "run",
            "https://github.com/org/repo",
            "--gpus",
            "4",
            "--branch",
            "dev",
            "--name",
            "test-job",
        ],
    )
    assert result.exit_code == 0

    call_kwargs = mock_client.post.call_args
    body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert body["gpu_count"] == 4
    assert body["branch"] == "dev"
    assert body["job_name"] == "test-job"


@patch("ds01_jobs.submit._get_client")
def test_run_api_error(mock_get_client, mock_api_key):
    """run handles API error with structured message."""
    mock_client = MagicMock()
    mock_client.post.return_value = _mock_response(
        status_code=422,
        json_data={"error": {"type": "validation_error", "message": "Invalid repo URL"}},
    )
    mock_get_client.return_value = mock_client

    result = runner.invoke(app, ["run", "not-a-url"])
    assert result.exit_code == 1


# --- status tests ---


@patch("ds01_jobs.submit._get_client")
def test_status_snapshot(mock_get_client, mock_api_key):
    """status prints human-readable snapshot."""
    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(
        json_data={
            "job_id": "job-abc123",
            "status": "running",
            "job_name": "my-job",
            "repo_url": "https://github.com/org/repo",
            "branch": "main",
            "gpu_count": 2,
            "submitted_by": "researcher1",
            "created_at": "2026-03-10T00:00:00Z",
            "started_at": "2026-03-10T00:01:00Z",
            "completed_at": None,
            "phases": {
                "queued": {"started_at": "2026-03-10T00:00:00Z", "ended_at": "2026-03-10T00:01:00Z"}
            },
            "error": None,
            "queue_position": None,
        },
    )
    mock_get_client.return_value = mock_client

    result = runner.invoke(app, ["status", "job-abc123"])
    assert result.exit_code == 0
    assert "job-abc123" in result.output
    assert "running" in result.output
    assert "my-job" in result.output


@patch("ds01_jobs.submit._get_client")
def test_status_json(mock_get_client, mock_api_key):
    """status --json prints raw JSON."""
    detail = {"job_id": "job-abc123", "status": "succeeded"}
    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(json_data=detail)
    mock_get_client.return_value = mock_client

    result = runner.invoke(app, ["status", "job-abc123", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["job_id"] == "job-abc123"


@patch("ds01_jobs.submit._get_client")
def test_status_failed_exit_code_2(mock_get_client, mock_api_key):
    """status of a failed job exits with code 2."""
    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(
        json_data={
            "job_id": "job-fail",
            "status": "failed",
            "job_name": "broken",
            "repo_url": "https://github.com/org/repo",
            "branch": "main",
            "gpu_count": 1,
            "submitted_by": "researcher1",
            "created_at": "2026-03-10T00:00:00Z",
            "phases": {},
            "error": {"phase": "build", "message": "Build failed", "exit_code": 1},
        },
    )
    mock_get_client.return_value = mock_client

    result = runner.invoke(app, ["status", "job-fail"])
    assert result.exit_code == 2


# --- results tests ---


@patch("ds01_jobs.submit._get_client")
def test_results_download_and_extract(mock_get_client, mock_api_key, tmp_path):
    """results downloads tar.gz and extracts to output directory."""
    # Create a tar.gz in memory
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        data = b"hello results"
        info = tarfile.TarInfo(name="results/output.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    tar_bytes = tar_buffer.getvalue()

    mock_client = MagicMock()
    mock_stream_response = MagicMock()
    mock_stream_response.status_code = 200
    mock_stream_response.headers = {"content-length": str(len(tar_bytes))}
    mock_stream_response.iter_bytes.return_value = [tar_bytes]
    mock_stream_response.raise_for_status = MagicMock()
    mock_stream_response.__enter__ = MagicMock(return_value=mock_stream_response)
    mock_stream_response.__exit__ = MagicMock(return_value=False)

    mock_client.stream.return_value = mock_stream_response
    mock_get_client.return_value = mock_client

    output_dir = tmp_path / "output"
    result = runner.invoke(app, ["results", "job-abc123", "-o", str(output_dir)])
    assert result.exit_code == 0
    assert "Results downloaded" in result.output
    assert (output_dir / "results" / "output.txt").exists()


@patch("ds01_jobs.submit._get_client")
def test_results_not_found(mock_get_client, mock_api_key):
    """results handles 404 with clear error."""
    mock_client = MagicMock()
    mock_stream_response = MagicMock()
    mock_stream_response.status_code = 404
    mock_stream_response.__enter__ = MagicMock(return_value=mock_stream_response)
    mock_stream_response.__exit__ = MagicMock(return_value=False)

    mock_client.stream.return_value = mock_stream_response
    mock_get_client.return_value = mock_client

    result = runner.invoke(app, ["results", "job-nonexistent"])
    assert result.exit_code == 1
    assert "No results found" in result.output


# --- list tests ---


@patch("ds01_jobs.submit._get_client")
def test_list_columnar_output(mock_get_client, mock_api_key):
    """list prints columnar output."""
    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(
        json_data={
            "jobs": [
                {
                    "job_id": "job-001",
                    "status": "succeeded",
                    "job_name": "train-model",
                    "repo_url": "https://github.com/org/repo",
                    "created_at": "2026-03-10T00:00:00Z",
                    "completed_at": "2026-03-10T01:00:00Z",
                },
                {
                    "job_id": "job-002",
                    "status": "running",
                    "job_name": "eval-model",
                    "repo_url": "https://github.com/org/repo",
                    "created_at": "2026-03-10T02:00:00Z",
                    "completed_at": None,
                },
            ],
            "total": 2,
            "limit": 20,
            "offset": 0,
        },
    )
    mock_get_client.return_value = mock_client

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "JOB ID" in result.output
    assert "STATUS" in result.output
    assert "job-001" in result.output
    assert "job-002" in result.output
    assert "train-model" in result.output


@patch("ds01_jobs.submit._get_client")
def test_list_json(mock_get_client, mock_api_key):
    """list --json prints raw JSON."""
    list_data = {"jobs": [], "total": 0, "limit": 20, "offset": 0}
    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(json_data=list_data)
    mock_get_client.return_value = mock_client

    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["total"] == 0


@patch("ds01_jobs.submit._get_client")
def test_list_empty(mock_get_client, mock_api_key):
    """list with no jobs prints message."""
    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(
        json_data={"jobs": [], "total": 0, "limit": 20, "offset": 0}
    )
    mock_get_client.return_value = mock_client

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


# --- cancel tests ---


@patch("ds01_jobs.submit._get_client")
def test_cancel_success(mock_get_client, mock_api_key):
    """cancel prints confirmation."""
    mock_client = MagicMock()
    mock_client.post.return_value = _mock_response(
        json_data={"job_id": "job-abc123", "status": "cancelled"}
    )
    mock_get_client.return_value = mock_client

    result = runner.invoke(app, ["cancel", "job-abc123"])
    assert result.exit_code == 0
    assert "cancelled" in result.output


@patch("ds01_jobs.submit._get_client")
def test_cancel_json(mock_get_client, mock_api_key):
    """cancel --json prints JSON response."""
    cancel_data = {"job_id": "job-abc123", "status": "cancelled"}
    mock_client = MagicMock()
    mock_client.post.return_value = _mock_response(json_data=cancel_data)
    mock_get_client.return_value = mock_client

    result = runner.invoke(app, ["cancel", "job-abc123", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["status"] == "cancelled"


# --- credential resolution failure ---


def test_no_credentials_exits_with_message(monkeypatch, tmp_path):
    """Commands exit with helpful message when no credentials found."""
    monkeypatch.delenv("DS01_API_KEY", raising=False)
    monkeypatch.setattr("ds01_jobs.client.CREDENTIALS_PATH", tmp_path / "nonexistent")

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 1
    assert "No API key found" in result.output
