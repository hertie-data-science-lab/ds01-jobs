"""ds01-submit CLI for researchers to submit GPU jobs and retrieve results.

Commands: configure, run, status, results, list, cancel.
All commands support --json for machine-readable output.
"""

import io
import json
import tarfile
import time
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.progress import BarColumn, DownloadColumn, Progress, TransferSpeedColumn

from ds01_jobs.client import (
    CREDENTIALS_PATH,
    TERMINAL_STATES,
    DS01Client,
    resolve_api_key,
    resolve_api_url,
)

app = typer.Typer(
    name="ds01-submit",
    help="DS01 Job Submission Service - Researcher CLI",
)

JsonOption = Annotated[bool, typer.Option("--json", help="JSON output")]


def _get_client() -> DS01Client:
    """Resolve credentials and return a signing client.

    Exits with a helpful message if no API key is found.
    """
    api_key = resolve_api_key()
    if not api_key:
        typer.echo(
            "Error: No API key found. Set DS01_API_KEY or run 'ds01-submit configure'.",
            err=True,
        )
        raise typer.Exit(code=1)
    return DS01Client(api_key=api_key, base_url=resolve_api_url())


def _api_call(func: object, *args: object, **kwargs: object) -> httpx.Response:
    """Wrap an API call with ConnectError handling."""
    try:
        return func(*args, **kwargs)  # type: ignore[operator]
    except httpx.ConnectError:
        typer.echo(f"Error: Could not connect to server at {resolve_api_url()}", err=True)
        raise typer.Exit(code=1)


def _handle_error(resp: httpx.Response) -> None:
    """Print a structured error message from an API error response and exit."""
    try:
        body = resp.json()
        if "error" in body and isinstance(body["error"], dict):
            typer.echo(f"Error: {body['error'].get('message', resp.status_code)}", err=True)
        elif "detail" in body:
            typer.echo(f"Error: {body['detail']}", err=True)
        else:
            typer.echo(f"Error: {resp.status_code} {resp.text}", err=True)
    except Exception:
        typer.echo(f"Error: {resp.status_code} {resp.text}", err=True)
    raise typer.Exit(code=1)


@app.command()
def configure() -> None:
    """Set up API credentials for ds01-submit."""
    api_key = resolve_api_key() or typer.prompt("DS01 API key")
    api_url = resolve_api_url()

    client = DS01Client(api_key=api_key, base_url=api_url)
    try:
        resp = client.get("/api/v1/users/me/quota")
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        typer.echo("Error: Invalid or expired API key", err=True)
        raise typer.Exit(code=1)
    except httpx.ConnectError:
        typer.echo(f"Error: Could not connect to server at {api_url}", err=True)
        raise typer.Exit(code=1)
    finally:
        client.close()

    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(api_key)
    CREDENTIALS_PATH.chmod(0o600)

    quota = resp.json()
    typer.echo(f"Authenticated as {quota['username']} (group: {quota['group']})")
    typer.echo(f"Credentials saved to {CREDENTIALS_PATH}")


@app.command("run")
def run_job(
    repo_url: Annotated[str, typer.Argument(help="GitHub repository URL")],
    gpus: Annotated[int, typer.Option(help="Number of GPUs")] = 1,
    branch: Annotated[str, typer.Option(help="Git branch to build")] = "main",
    name: Annotated[str | None, typer.Option(help="Job name")] = None,
    timeout: Annotated[int | None, typer.Option(help="Job timeout in seconds")] = None,
    dockerfile: Annotated[Path | None, typer.Option(help="Path to Dockerfile")] = None,
    json_output: JsonOption = False,
) -> None:
    """Submit a GPU job and print the job ID."""
    client = _get_client()
    try:
        body: dict[str, object] = {
            "repo_url": repo_url,
            "gpu_count": gpus,
            "branch": branch,
        }
        if name is not None:
            body["job_name"] = name
        if timeout is not None:
            body["timeout_seconds"] = timeout
        if dockerfile is not None:
            body["dockerfile_content"] = dockerfile.read_text()

        resp = _api_call(client.post, "/api/v1/jobs", json=body)
        if resp.status_code != 202:
            _handle_error(resp)

        data = resp.json()
        if json_output:
            typer.echo(json.dumps(data, indent=2))
        else:
            typer.echo(data["job_id"])
    finally:
        client.close()


@app.command()
def status(
    job_id: Annotated[str, typer.Argument(help="Job ID")],
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Poll until terminal state")
    ] = False,
    json_output: JsonOption = False,
) -> None:
    """Show job status. Use --follow to poll until completion."""
    client = _get_client()
    try:
        backoff = 2.0
        max_backoff = 30.0

        while True:
            resp = _api_call(client.get, f"/api/v1/jobs/{job_id}")
            if resp.status_code != 200:
                _handle_error(resp)

            data = resp.json()
            current_status = data["status"]

            if not follow or current_status in TERMINAL_STATES:
                if json_output:
                    typer.echo(json.dumps(data, indent=2))
                else:
                    _print_status(data)

                if current_status == "failed":
                    raise typer.Exit(code=2)
                return

            # Polling mode: print a short update
            typer.echo(f"Status: {current_status} (polling...)")
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
    finally:
        client.close()


def _print_status(data: dict[str, object]) -> None:
    """Print human-readable job status snapshot."""
    typer.echo(f"Job:     {data['job_id']}")
    typer.echo(f"Status:  {data['status']}")
    typer.echo(f"Name:    {data.get('job_name', '-')}")
    typer.echo(f"Repo:    {data.get('repo_url', '-')}")
    typer.echo(f"Branch:  {data.get('branch', '-')}")
    typer.echo(f"GPUs:    {data.get('gpu_count', '-')}")
    typer.echo(f"Created: {data.get('created_at', '-')}")

    if data.get("queue_position") is not None:
        typer.echo(f"Queue:   #{data['queue_position']}")

    phases = data.get("phases")
    if phases and isinstance(phases, dict):
        typer.echo("")
        typer.echo("Phases:")
        for phase_name, ts in phases.items():
            if isinstance(ts, dict):
                started = ts.get("started_at", "-")
                ended = ts.get("ended_at", "-")
                typer.echo(f"  {phase_name}: started={started} ended={ended}")

    error = data.get("error")
    if error and isinstance(error, dict):
        typer.echo("")
        typer.echo(f"Error:   [{error.get('phase', '?')}] {error.get('message', '-')}")
        if error.get("exit_code") is not None:
            typer.echo(f"         exit code {error['exit_code']}")


@app.command()
def results(
    job_id: Annotated[str, typer.Argument(help="Job ID")],
    output: Annotated[Path, typer.Option("-o", "--output", help="Output directory")] = Path(
        "./results"
    ),
    json_output: JsonOption = False,
) -> None:
    """Download and extract job results."""
    client = _get_client()
    try:
        path = f"/api/v1/jobs/{job_id}/results"
        with client.stream("GET", path) as resp:
            if resp.status_code == 404:
                typer.echo(f"Error: No results found for job {job_id}", err=True)
                raise typer.Exit(code=1)
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", "0"))
            buffer = io.BytesIO()

            if total > 1_048_576:
                with Progress(
                    "[progress.description]{task.description}",
                    BarColumn(),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                ) as progress:
                    task = progress.add_task("Downloading results", total=total)
                    for chunk in resp.iter_bytes():
                        buffer.write(chunk)
                        progress.update(task, advance=len(chunk))
            else:
                for chunk in resp.iter_bytes():
                    buffer.write(chunk)

        buffer.seek(0)
        output.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=buffer, mode="r:gz") as tar:
            tar.extractall(path=output, filter="data")

        if json_output:
            typer.echo(json.dumps({"job_id": job_id, "path": str(output)}))
        else:
            typer.echo(f"Results downloaded to {output}")
    finally:
        client.close()


@app.command("list")
def list_jobs(
    limit: Annotated[int, typer.Option(help="Max jobs to return")] = 20,
    offset: Annotated[int, typer.Option(help="Pagination offset")] = 0,
    json_output: JsonOption = False,
) -> None:
    """List submitted jobs."""
    client = _get_client()
    try:
        resp = _api_call(client.get, f"/api/v1/jobs?limit={limit}&offset={offset}")
        if resp.status_code != 200:
            _handle_error(resp)

        data = resp.json()

        if json_output:
            typer.echo(json.dumps(data, indent=2))
        else:
            jobs = data.get("jobs", [])
            if not jobs:
                typer.echo("No jobs found.")
                return

            headers = ["JOB ID", "STATUS", "NAME", "CREATED"]
            rows = []
            for j in jobs:
                rows.append(
                    [
                        j.get("job_id", "-"),
                        j.get("status", "-"),
                        j.get("job_name", "-"),
                        j.get("created_at", "-")[:19],
                    ]
                )

            col_widths = [
                max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))
            ]

            header_line = "  ".join(h.ljust(w) for h, w in zip(headers, col_widths, strict=True))
            typer.echo(header_line)

            for row in rows:
                line = "  ".join(v.ljust(w) for v, w in zip(row, col_widths, strict=True))
                typer.echo(line)
    finally:
        client.close()


@app.command()
def cancel(
    job_id: Annotated[str, typer.Argument(help="Job ID")],
    json_output: JsonOption = False,
) -> None:
    """Cancel a running job."""
    client = _get_client()
    try:
        resp = _api_call(client.post, f"/api/v1/jobs/{job_id}/cancel")
        if resp.status_code not in (200, 202):
            _handle_error(resp)

        data = resp.json()
        if json_output:
            typer.echo(json.dumps(data, indent=2))
        else:
            typer.echo(f"Job {job_id} cancelled")
    finally:
        client.close()


if __name__ == "__main__":
    app()
