"""GitHub Action entrypoint: submit a GPU job, poll until done, download results.

Uses argparse (stdlib) for lightweight CLI parsing in CI environments.
Imports DS01Client from the installed ds01-jobs package for HMAC-signed requests.
"""

import argparse
import io
import os
import sys
import tarfile
import time
from pathlib import Path

from ds01_jobs.client import TERMINAL_STATES, DS01Client, resolve_api_key, resolve_api_url


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments passed from action.yml composite step."""
    parser = argparse.ArgumentParser(description="DS01 job submit + wait + download")
    parser.add_argument("--repo-url", required=True, help="GitHub repository URL")
    parser.add_argument("--branch", default="main", help="Branch to build")
    parser.add_argument("--gpus", type=int, default=1, help="Number of GPUs")
    parser.add_argument("--timeout", type=int, default=14400, help="Job timeout in seconds")
    parser.add_argument("--results-path", default="./results", help="Path to download results to")
    return parser.parse_args()


def main() -> int:
    """Submit job, poll until terminal state, download results on success."""
    args = parse_args()

    # Resolve credentials
    api_key = resolve_api_key()
    if not api_key:
        print("Error: DS01_API_KEY not set", file=sys.stderr)
        return 1

    api_url = resolve_api_url()
    client = DS01Client(api_key=api_key, base_url=api_url)

    try:
        # Submit job
        body = {
            "repo_url": args.repo_url,
            "branch": args.branch,
            "gpu_count": args.gpus,
            "timeout_seconds": args.timeout,
        }
        resp = client.post("/api/v1/jobs", json=body)
        if resp.status_code != 202:
            msg = f"Error: job submission failed ({resp.status_code}): {resp.text}"
            print(msg, file=sys.stderr)
            return 1

        data = resp.json()
        job_id = data["job_id"]
        print(f"Submitted job {job_id}")

        # Poll with exponential backoff
        backoff = 2.0
        max_backoff = 30.0
        poll_count = 0

        while True:
            poll_count += 1
            time.sleep(backoff)

            resp = client.get(f"/api/v1/jobs/{job_id}")
            if resp.status_code != 200:
                print(
                    f"Error: status check failed ({resp.status_code}): {resp.text}",
                    file=sys.stderr,
                )
                return 1

            status_data = resp.json()
            current_status = status_data["status"]
            print(f"Status: {current_status} (poll {poll_count})")

            if current_status in TERMINAL_STATES:
                break

            backoff = min(backoff * 2, max_backoff)

        final_status = current_status

        # Download results on success
        results_path = Path(args.results_path)
        if final_status == "succeeded":
            with client.stream("GET", f"/api/v1/jobs/{job_id}/results") as results_resp:
                if results_resp.status_code == 200:
                    buffer = io.BytesIO()
                    for chunk in results_resp.iter_bytes():
                        buffer.write(chunk)
                    buffer.seek(0)
                    results_path.mkdir(parents=True, exist_ok=True)
                    with tarfile.open(fileobj=buffer, mode="r:gz") as tar:
                        tar.extractall(path=results_path, filter="data")
                    print(f"Results downloaded to {results_path}")
                else:
                    print(f"Warning: could not download results ({results_resp.status_code})")

        # Write GitHub Action outputs
        output_file = os.environ.get("GITHUB_OUTPUT", "")
        if output_file:
            with open(output_file, "a") as f:
                f.write(f"job-id={job_id}\n")
                f.write(f"status={final_status}\n")
                f.write(f"results-path={results_path}\n")

        if final_status == "succeeded":
            print(f"Job {job_id} completed successfully")
            return 0
        else:
            print(f"Job {job_id} finished with status: {final_status}", file=sys.stderr)
            return 1

    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
