# DS01 Jobs - User Guide

This guide covers everything you need to submit GPU jobs to the DS01 compute cluster, from initial setup through to downloading your results. It is aimed at researchers who want to run containerised workloads on shared GPU hardware.

## Prerequisites

Before you begin, make sure you have:

- **Python 3.10 or later** installed on your local machine
- **Access to the DS01 cluster** - you must be a member of the [Hertie Data Science Lab GitHub organisation](https://github.com/hertie-data-science-lab). If you are not yet a member, ask your supervisor or lab administrator to invite you.
- **An API key** issued by a cluster administrator (see below)

## Getting an API Key

API keys authenticate your CLI requests to the DS01 cluster. You cannot submit jobs without one.

1. **Contact a cluster administrator** and provide your GitHub username and your Unix username on the server.
2. The administrator runs:
   ```bash
   ds01-job-admin key-create <github_username> <unix_username>
   ```
3. You receive a key in the format `ds01_XXXXXXXXXXXX...` (a `ds01_` prefix followed by a base64url-encoded token).
4. Keys expire after **90 days** by default. The administrator can set a custom expiry (e.g. `--expires 180d`). When your key is within 14 days of expiry, the API will include an `X-DS01-Key-Expiry-Warning` header in responses - contact your administrator to rotate the key before it expires.
5. **Store your key securely.** Never commit it to Git, paste it in Slack messages, or include it in scripts that are checked into version control.

## Installing the CLI

The `ds01-submit` CLI is distributed as a Python package:

```bash
pip install ds01-jobs
```

Or, if you use [uv](https://docs.astral.sh/uv/):

```bash
uv pip install ds01-jobs
```

This installs the `ds01-submit` command, which is your main interface to the cluster.

## Configuring Credentials

You need to tell the CLI your API key. There are two ways to do this:

### Option 1: Environment Variable

Set the `DS01_API_KEY` environment variable. This is useful for CI pipelines or temporary sessions:

```bash
export DS01_API_KEY=ds01_your_key_here
```

### Option 2: The `configure` Command (Recommended)

Run the interactive configure command, which validates your key against the API and saves it locally:

```bash
ds01-submit configure
```

You will be prompted to enter your API key. On success, the CLI displays your username and group, and saves the key to `~/.config/ds01/credentials` (with `0600` permissions so only you can read it).

**Credential resolution order:** The CLI checks `DS01_API_KEY` first, then falls back to the saved credentials file. The environment variable always takes precedence.

## Preparing Your Repository

The DS01 cluster builds and runs Docker containers from your GitHub repository. Your repo must meet a few requirements:

### Dockerfile

Your repository must contain a `Dockerfile` at the root (or you can provide one via the `--dockerfile` flag). The Dockerfile is statically scanned before the build starts, and the following rules are enforced:

**Allowed base images** - your `FROM` directive must use an image from one of these registries:

| Registry prefix | Description |
|---|---|
| `docker.io/library/*` | Official Docker Hub images (e.g. `python:3.12`, `ubuntu:22.04`) |
| `nvcr.io/nvidia/*` | NVIDIA NGC images (e.g. `nvcr.io/nvidia/pytorch:24.01-py3`) |
| `docker.io/pytorch/*` | PyTorch images |
| `docker.io/tensorflow/*` | TensorFlow images |
| `docker.io/huggingface/*` | Hugging Face images |
| `ghcr.io/astral-sh/*` | Astral/uv images |

Images from other registries will be rejected with a `BLOCKED_BASE_IMAGE` error.

**Blocked ENV directives** - the following environment variables are blocked in your Dockerfile's final stage for security reasons:

- `LD_PRELOAD`
- `LD_LIBRARY_PATH`
- `LD_AUDIT`

Setting any of these produces an error. Additionally, `LD_DEBUG` and `PYTHONPATH` will generate warnings but are not blocked outright.

### Output Directory

Write any results you want to retrieve to `/output/` inside your container. After your job completes, the cluster copies everything from `/output/` and makes it available for download. If your container does not write to `/output/`, there will be nothing to download.

Example in your Dockerfile or entrypoint script:

```bash
mkdir -p /output
python train.py --save-dir /output
```

### Repository Visibility

The repository URL must be a valid GitHub HTTPS URL (e.g. `https://github.com/org/repo`). The cluster performs a pre-flight check to verify the repository exists and is accessible.

## Submitting a Job

Use the `run` command to submit a job:

```bash
# Basic submission (1 GPU, main branch)
ds01-submit run https://github.com/org/repo

# With options
ds01-submit run https://github.com/org/repo \
  --gpus 2 \
  --branch feature/experiment \
  --name "my-training-job" \
  --timeout 3600 \
  --dockerfile path/to/Dockerfile
```

On success, the command prints the job ID (a UUID). Use this ID to check status, download results, or cancel the job.

### Options

| Flag | Description | Default |
|---|---|---|
| `--gpus N` | Number of GPUs to allocate (1-8) | `1` |
| `--branch NAME` | Git branch to clone and build | `main` |
| `--name TEXT` | Human-readable job name (auto-generated if omitted) | `<repo>-<id prefix>` |
| `--timeout SECONDS` | Maximum run time in seconds (60-86,400) | 4 hours (14,400s) |
| `--dockerfile PATH` | Path to a local Dockerfile to use instead of the repo's | Repo's `Dockerfile` |
| `--json` | Output the full response as JSON instead of just the job ID | Off |

### What Happens After Submission

When you submit a job, the cluster:

1. Validates the repository URL format and accessibility
2. Scans the Dockerfile (if provided) for disallowed base images and blocked ENV directives
3. Checks your quota (concurrent and daily limits)
4. Queues the job and returns a job ID

The job then proceeds through these phases:

```
queued -> cloning -> building -> running -> succeeded / failed
```

- **queued** - waiting for GPU availability
- **cloning** - shallow-cloning your repository (retries once on failure)
- **building** - running `docker build` with your Dockerfile (15-minute timeout)
- **running** - executing the container with GPU access
- **succeeded** - container exited cleanly; results are available
- **failed** - something went wrong; check the error details

## Complete Worked Example

This section walks through a full end-to-end job, from writing a training script to downloading the results.

### Step 1: Create a Training Script

Create a file called `train.py` that trains a tiny model on synthetic data:

```python
"""Minimal PyTorch training example for DS01."""

import torch
import torch.nn as nn

model = nn.Linear(10, 1).cuda()
optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
loss_fn = nn.MSELoss()

for epoch in range(100):
    x = torch.randn(32, 10, device="cuda")
    y = torch.randn(32, 1, device="cuda")
    loss = loss_fn(model(x), y)
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

print(f"Final loss: {loss.item():.4f}")
torch.save(model.state_dict(), "/output/model.pt")
print("Model saved to /output/model.pt")
```

### Step 2: Write a Dockerfile

Create a `Dockerfile` in the repository root:

```dockerfile
FROM nvcr.io/nvidia/pytorch:24.01-py3

WORKDIR /workspace

# Copy and install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the training script
COPY train.py .

# Ensure the output directory exists
RUN mkdir -p /output

CMD ["python", "train.py"]
```

If your script has no additional dependencies beyond what the base image provides, your `requirements.txt` can be empty or omitted (and you can remove the `COPY requirements.txt` and `RUN pip install` lines).

### Step 3: Push to GitHub

Commit everything and push to a GitHub repository:

```bash
git init
git add train.py Dockerfile requirements.txt
git commit -m "Minimal training example"
git remote add origin https://github.com/your-org/gpu-example.git
git push -u origin main
```

### Step 4: Submit the Job

```bash
ds01-submit run https://github.com/your-org/gpu-example \
  --name "tiny-model-test" \
  --timeout 600
```

Output:

```
Job submitted: a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

Save the job ID - you will need it for the next steps.

### Step 5: Monitor Progress

Watch the job move through its phases:

```bash
ds01-submit status a1b2c3d4-e5f6-7890-abcd-ef1234567890 --follow
```

You will see output similar to:

```
Job:     a1b2c3d4-e5f6-7890-abcd-ef1234567890
Name:    tiny-model-test
Status:  cloning
...
Status:  building
...
Status:  running
...
Status:  succeeded
```

The CLI polls with exponential back-off until the job reaches a terminal state.

### Step 6: Download Results

```bash
ds01-submit results a1b2c3d4-e5f6-7890-abcd-ef1234567890 -o ./my-results
```

This streams and extracts the results archive into `./my-results/`.

### Step 7: Inspect the Output

```bash
ls ./my-results/
# model.pt

python -c "
import torch, torch.nn as nn
model = nn.Linear(10, 1)
model.load_state_dict(torch.load('./my-results/model.pt', map_location='cpu'))
print('Model loaded successfully')
print('Weight shape:', model.weight.shape)
"
```

You now have a trained model on your local machine that was computed on the cluster's GPU.

## Checking Job Status

```bash
# One-time status check
ds01-submit status <job_id>

# Poll until the job reaches a terminal state (succeeded or failed)
ds01-submit status <job_id> --follow
```

The status output includes the job ID, current status, repo URL, branch, GPU count, creation time, phase timestamps, queue position (if queued), and error details (if failed).

With `--follow`, the CLI polls the API with exponential back-off (starting at 2 seconds, up to 30 seconds) until the job either succeeds or fails. If the job fails, the CLI exits with code 2.

Use `--json` for machine-readable output.

## Downloading Results

Once a job has succeeded, download its output:

```bash
# Download and extract to ./results/
ds01-submit results <job_id>

# Download to a custom directory
ds01-submit results <job_id> -o ./my-results
```

The results are streamed as a `.tar.gz` archive and automatically extracted into the target directory. For large downloads (over 1 MB), a progress bar is shown.

**Note:** Results are only available for jobs with status `succeeded`. Attempting to download results for a failed or still-running job will return an error.

## Listing Jobs

View your submitted jobs:

```bash
# List recent jobs (default: 20)
ds01-submit list

# List more jobs
ds01-submit list --limit 50
```

Output is a table showing job ID, status, name, and creation time. Use `--json` for the full response including pagination metadata (`total`, `limit`, `offset`).

## Cancelling a Job

Cancel a job that is still queued or running:

```bash
ds01-submit cancel <job_id>
```

Cancellation sets the job status to `failed` with the message "Cancelled by user". You can only cancel your own jobs, and only while they are in an active state (`queued`, `cloning`, `building`, or `running`).

## Quotas and Rate Limits

Each user has two types of quota:

| Limit | Description | Default |
|---|---|---|
| **Concurrent jobs** | Maximum number of active jobs at once | 3 |
| **Daily submissions** | Maximum jobs submitted per day (resets at midnight UTC) | 10 |
| **Result size** | Maximum download size per job | 1024 MB |

These limits may vary by user group. The API includes rate limit headers in every job submission response:

- `X-RateLimit-Limit-Concurrent` / `X-RateLimit-Remaining-Concurrent`
- `X-RateLimit-Limit-Daily` / `X-RateLimit-Remaining-Daily`
- `X-RateLimit-Reset-Daily` (ISO timestamp of next midnight UTC)

Check your current quota usage:

```bash
ds01-submit configure
```

The configure command displays your username, group, and current limits.

There is also a global rate limit of 60 requests per minute across all API endpoints, applied per API key.

## API URL

By default, the CLI connects to the production cluster:

```
https://ds01.hertie-data-science-lab.org
```

To point at a different server (e.g. for local development or testing):

```bash
export DS01_API_URL=http://127.0.0.1:8765
```

## Troubleshooting

### 401 Authentication failed

Your API key is invalid, expired, or has been revoked. Run `ds01-submit configure` to re-validate your key. If the key has expired, contact an administrator to rotate it.

### 422 Validation error

The request failed validation. Common causes:

- **Invalid repository URL** - must be `https://github.com/owner/repo` format
- **Blocked base image** - your Dockerfile uses an image from a disallowed registry
- **Blocked ENV directive** - your Dockerfile sets `LD_PRELOAD`, `LD_LIBRARY_PATH`, or `LD_AUDIT`
- **Repository not found** - the repository does not exist or is not accessible

The error response includes specific field-level details explaining what went wrong.

### 429 Rate limit exceeded

You have hit your concurrent job limit or daily submission limit. The response body includes:

- `limit_type` - whether it is a `concurrent` or `daily` limit
- `current` / `limit` - your usage versus the cap
- `retry_after` - seconds until the daily limit resets (for daily limits)

Wait for active jobs to complete, or wait for the daily reset at midnight UTC.

### 409 Conflict

You are trying to cancel a job that has already finished (`succeeded` or `failed`). Only active jobs can be cancelled.

### 404 Job not found

The job ID is invalid, or the job belongs to a different user. You can only view and manage your own jobs.

### Job failed in the "clone" phase

The repository could not be cloned. Check that:

- The repository URL is correct and the repository exists
- The branch name is correct
- The repository is accessible (not private, or credentials are configured)

### Job failed in the "build" phase

The Docker build failed. This usually means there is an error in your Dockerfile. Check the error message for the exit code. Common causes include missing dependencies, syntax errors in the Dockerfile, or network issues during package installation.

The build phase has a 15-minute timeout. If your build takes longer than this, consider using a pre-built base image with your dependencies already installed.

### Job failed in the "run" phase

Your container exited with a non-zero exit code, or hit the timeout. Check:

- Your entrypoint script exits cleanly on success
- Your code does not run out of GPU memory
- The timeout is long enough for your workload (default: 4 hours, maximum: 24 hours)

## Best Practices for Dockerfiles (GPU Workloads)

Writing good Dockerfiles makes your jobs build faster, fail less often, and use less disk space on the cluster. Here are practical tips for GPU workloads.

### Start from NVIDIA Base Images

NVIDIA NGC images come with CUDA, cuDNN, and GPU drivers pre-configured. You do not need to install these yourself:

```dockerfile
# Good - includes CUDA 12.x, cuDNN, PyTorch, and common Python packages
FROM nvcr.io/nvidia/pytorch:24.01-py3

# Bad - you'll need to install CUDA/cuDNN manually, which is fragile
FROM ubuntu:22.04
```

The NVIDIA images are large (several GB) but they save you from debugging CUDA version mismatches.

### Install Dependencies Before Copying Code

Docker caches each layer. If you copy your code before installing dependencies, every code change invalidates the pip install cache:

```dockerfile
# Good - dependencies are cached unless requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Bad - changing any source file re-runs pip install
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
```

### Handle /dev/shm Size

Some frameworks (notably PyTorch DataLoader with `num_workers > 0`) use shared memory (`/dev/shm`). Docker containers have a small default `/dev/shm`. If your training crashes with a "Bus error" or shared memory errors, reduce `num_workers` or use `--shm-size`-friendly patterns in your code:

```python
# Safer default for containerised training
dataloader = DataLoader(dataset, batch_size=32, num_workers=0)
```

The cluster allocates per-user resource limits including shared memory, but it is best not to assume a large `/dev/shm` is available.

### Write All Outputs to /output/

The cluster only collects files from `/output/`. Files written to `/tmp/`, `/home/`, or anywhere else are lost when the container exits:

```python
# Good
torch.save(model.state_dict(), "/output/model.pt")

# Bad - this file will not be in your results
torch.save(model.state_dict(), "/tmp/model.pt")
```

### Keep Your Docker Image Small

Large images take longer to build and consume cluster disk space:

- **Use a `.dockerignore` file** to exclude `.git/`, `data/`, `__pycache__/`, and other unnecessary files from the build context
- **Use `--no-cache-dir`** with pip to avoid storing wheel caches in the image
- **Consider multi-stage builds** if you need build tools that are not required at runtime

Example `.dockerignore`:

```
.git
__pycache__
*.pyc
data/
notebooks/
.venv/
```

### Pin Dependency Versions

Reproducible builds require pinned versions. Without them, a new package release can break your build between runs:

```
# Good - requirements.txt with pinned versions
torch==2.2.0
numpy==1.26.3
pandas==2.1.5

# Bad - unpinned, could break at any time
torch
numpy
pandas
```

Use `pip freeze > requirements.txt` or `uv pip compile requirements.in -o requirements.txt` to generate pinned versions.

### Test Locally First

Before submitting to the cluster, verify your Dockerfile builds and runs:

```bash
docker build -t test-job .
docker run --gpus all test-job
```

If you do not have a GPU locally, you can at least verify the build succeeds and the entrypoint runs (it will fail on CUDA calls, but you can catch Dockerfile errors early).

## Viewing Job Logs

When a job fails (or even when it succeeds), you may want to see the logs from each execution phase.

### How Logs Work

The API provides per-phase logs via `GET /api/v1/jobs/{id}/logs`. Each phase of execution (clone, build, run) has its own log. Logs are tail-truncated to **1 MB per phase** to keep response sizes manageable.

### Accessing Logs

Currently the CLI does not have a dedicated `logs` command. You can access logs via the API directly using `curl` with the appropriate HMAC signing headers, or ask a cluster administrator to retrieve them for you.

Example (if you have the signing headers available):

```bash
curl -H "Authorization: Bearer $DS01_API_KEY" \
     -H "X-Timestamp: ..." \
     -H "X-Nonce: ..." \
     -H "X-Signature: ..." \
     https://ds01.hertie-data-science-lab.org/api/v1/jobs/<job_id>/logs
```

The response is JSON with a `logs` object containing keys for each phase that has a log:

```json
{
  "job_id": "a1b2c3d4-...",
  "logs": {
    "clone": "Cloning into '/workspace/repo'...\n",
    "build": "Step 1/5 : FROM nvcr.io/nvidia/pytorch:24.01-py3\n...",
    "run": "Final loss: 0.0234\nModel saved to /output/model.pt\n"
  }
}
```

### Common Log Patterns

When investigating failures, look for these patterns in the logs:

- **"No space left on device"** - the Docker build or run exhausted disk space. Reduce your image size or contact an administrator.
- **"OOM killed"** or **"Killed"** - your process ran out of memory. Reduce batch size, model size, or request fewer concurrent operations.
- **"permission denied"** - a file or directory permission issue inside the container. Ensure your Dockerfile does not rely on specific user IDs.
- **"CUDA out of memory"** - GPU memory exhaustion. Reduce batch size or model complexity.
- **"No module named ..."** - a missing Python dependency. Check your `requirements.txt`.

## FAQ

**Q: How long can my job run?**
A: The default timeout is 4 hours (14,400 seconds). The maximum is 24 hours (86,400 seconds). Set a custom timeout with the `--timeout` flag in seconds.

**Q: Can I use private repositories?**
A: The repository must be accessible via HTTPS without authentication. This means public repositories work out of the box. Private repositories are only supported if the cluster has deploy keys configured for them - ask your cluster administrator.

**Q: Why was my Dockerfile rejected?**
A: The Dockerfile scanner checks two things before your job is queued. First, the `FROM` directive must reference an allowed base image registry (see the table in "Preparing Your Repository"). If it does not, you get a `BLOCKED_BASE_IMAGE` error. Second, certain `ENV` directives (`LD_PRELOAD`, `LD_LIBRARY_PATH`, `LD_AUDIT`) are blocked for security. Check the specific error message in the API response for details.

**Q: Can I SSH into my running container?**
A: No. The cluster runs containers non-interactively. There is no SSH access, no interactive terminal, and no way to attach to a running container. Design your workload to run unattended and write all outputs to `/output/`.

**Q: How do I know which GPUs my job will get?**
A: The cluster assigns available GPUs automatically. Your container sees all allocated GPUs via the `--gpus all` Docker flag. You do not get to choose specific GPU models or IDs. Use `torch.cuda.device_count()` in your code to check how many GPUs are available.

**Q: My job succeeded but there are no results.**
A: Your code must write files to `/output/` inside the container. If your script writes to any other directory (e.g. `/tmp/`, `/home/`, or a relative path), those files are not collected. Check your training script and ensure all saved artefacts go to `/output/`.

**Q: Can I run multiple jobs in parallel?**
A: Yes, up to your concurrent job limit (default: 3). Each job is independent and gets its own container. You can submit several jobs and monitor them individually.

## Try It Yourself

1. **Validate your credentials.** Run `ds01-submit configure` and verify that it displays your username and group.

2. **Check your quota.** After configuring, note your concurrent and daily limits. These constrain how many jobs you can run.

3. **Submit a test job.** Pick one of the lab's example repositories and submit a job:
   ```bash
   ds01-submit run https://github.com/hertie-data-science-lab/ds01-test-job
   ```
   Note the job ID printed on success.

4. **Watch a job through all phases.** Use `--follow` to see the job progress from `queued` through to `succeeded` or `failed`:
   ```bash
   ds01-submit status <job_id> --follow
   ```

5. **Download and inspect results.** Once the job succeeds, download the output and look at what your container produced:
   ```bash
   ds01-submit results <job_id> -o ./test-results
   ls ./test-results/
   ```

6. **Write a minimal Dockerfile from scratch.** Create a simple Python script that writes "Hello from GPU" to `/output/result.txt`, wrap it in a Dockerfile, push it to a GitHub repository, and submit it as a job:
   ```python
   # hello_gpu.py
   import torch
   device = "cuda" if torch.cuda.is_available() else "cpu"
   with open("/output/result.txt", "w") as f:
       f.write(f"Hello from GPU (device: {device})\n")
   ```
   ```dockerfile
   FROM nvcr.io/nvidia/pytorch:24.01-py3
   COPY hello_gpu.py .
   RUN mkdir -p /output
   CMD ["python", "hello_gpu.py"]
   ```
   After the job succeeds, download the results and verify that `result.txt` contains the expected message.

7. **Submit a job that intentionally fails.** Test your understanding of error handling by submitting a job with an invalid branch name:
   ```bash
   ds01-submit run https://github.com/hertie-data-science-lab/ds01-test-job \
     --branch nonexistent-branch-xyz
   ```
   Then check the status:
   ```bash
   ds01-submit status <job_id>
   ```
   The job should fail in the "clone" phase. Read the error details and understand what went wrong. This is a safe way to familiarise yourself with how the cluster reports failures.
