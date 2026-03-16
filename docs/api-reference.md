# DS01 Jobs API Reference

Complete reference for the DS01 Job Submission Service REST API.

FastAPI auto-generates interactive docs at `/docs` (Swagger UI) and `/redoc` on any running instance.

---

## Base URL

```
https://ds01.hertie-data-science-lab.org
```

Override with the `DS01_API_URL` environment variable.

---

## Authentication

All endpoints except `GET /health` require authentication. The API uses **HMAC-SHA256 signed requests** — not bare Bearer tokens.

The `ds01-submit` CLI handles signing transparently. If you are making raw HTTP requests, you must construct the signature yourself (see below).

### Required headers on every authenticated request

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer ds01_<key>` |
| `X-Timestamp` | Unix timestamp as a float string (e.g. `"1710000000.123"`) |
| `X-Nonce` | Random string, unique per request |
| `X-Signature` | HMAC-SHA256 hex digest (see below) |

### Canonical string format

```
METHOD\nPATH\nTIMESTAMP\nNONCE\nBODY_SHA256_HEX
```

Where:
- `METHOD` — uppercase HTTP method (`GET`, `POST`, etc.)
- `PATH` — URL path only, no query string (e.g. `/api/v1/jobs`)
- `TIMESTAMP` — the same string sent in `X-Timestamp`
- `NONCE` — the same string sent in `X-Nonce`
- `BODY_SHA256_HEX` — SHA-256 hex digest of the raw request body (empty string body → SHA-256 of `""`)

Sign the canonical string with HMAC-SHA256 using the raw API key as the secret.

### Timestamp and nonce rules

- Timestamp must be within **±5 minutes** of server time — requests outside this window are rejected.
- Each nonce is accepted once and expires after **5 minutes**. Replayed nonces are rejected.

### Python signing example

```python
import hashlib
import hmac
import time
import uuid

def sign_request(api_key: str, method: str, path: str, body: bytes = b"") -> dict:
    timestamp = str(time.time())
    nonce = uuid.uuid4().hex
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}"
    signature = hmac.new(api_key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }
```

> For most use cases, use `ds01-submit` which handles signing automatically.

### Auth errors

All authentication failures return a generic `401 Authentication failed` regardless of the specific reason (invalid key, expired key, bad signature, stale timestamp, replayed nonce). Specific reasons are logged server-side only.

---

## Common response headers

### Rate limit headers (on `POST /api/v1/jobs` 202)

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit-Concurrent` | Maximum concurrent active jobs for this user |
| `X-RateLimit-Remaining-Concurrent` | Remaining concurrent job slots |
| `X-RateLimit-Limit-Daily` | Maximum daily submissions for this user |
| `X-RateLimit-Remaining-Daily` | Remaining daily submissions |
| `X-RateLimit-Reset-Daily` | ISO 8601 timestamp when daily limit resets (midnight UTC) |

### Key expiry warning (all authenticated responses)

When your API key expires within 14 days:

```
X-DS01-Key-Expiry-Warning: 2025-04-30
```

The value is the expiry date in `YYYY-MM-DD` format. Rotate your key before this date to avoid service interruption.

---

## Error format

All error responses use a Stripe-like nested structure:

```json
{
  "error": {
    "type": "validation_error",
    "message": "Human-readable summary",
    "errors": [
      {
        "field": "repo_url",
        "code": "invalid_url",
        "message": "URL must be a github.com repository"
      }
    ]
  }
}
```

### Error types

| Type | Description |
|------|-------------|
| `validation_error` | Invalid request field — `repo_url` format, inaccessible repo, etc. |
| `dockerfile_scan_error` | Custom Dockerfile failed security scan |
| `rate_limit_error` | Per-user concurrent or daily job limit reached |

Rate limit errors include additional fields:

```json
{
  "error": {
    "type": "rate_limit_error",
    "limit_type": "concurrent",
    "message": "Concurrent job limit reached (3/3)",
    "limit": 3,
    "current": 3,
    "retry_after": null
  }
}
```

`limit_type` is either `"concurrent"` or `"daily"`. Daily limit errors include a `retry_after` value in seconds until midnight UTC.

---

## Job lifecycle

Jobs progress through these states in order:

```
queued → cloning → building → running → succeeded
                                      ↘ failed
```

A job can transition to `failed` from any active state. `succeeded` and `failed` are terminal.

---

## Endpoints

### GET /health

Health probe. No authentication required.

**Response 200:**

```json
{
  "status": "ok",
  "version": "1.0.0",
  "db": "ok"
}
```

| Field | Values |
|-------|--------|
| `status` | `"ok"` or `"degraded"` |
| `db` | `"ok"` or `"error"` |

**Response 503** — database unreachable.

**curl:**

```bash
curl https://ds01.hertie-data-science-lab.org/health
```

---

### POST /api/v1/jobs

Submit a new GPU job. Returns immediately with a queued job ID.

**Auth:** Required

**Request body:**

```json
{
  "repo_url": "https://github.com/hertie-data-science-lab/my-project",
  "gpu_count": 1,
  "branch": "main",
  "job_name": "my-experiment",
  "timeout_seconds": 3600,
  "dockerfile_content": null
}
```

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `repo_url` | string | Yes | github.com URL | Repository to clone and run |
| `gpu_count` | integer | No | 1–8, default: 1 | Number of GPUs to allocate |
| `branch` | string | No | default: `"main"` | Git branch to check out |
| `job_name` | string | No | default: auto-generated | Human-readable label |
| `timeout_seconds` | integer | No | 60–86400 | Run phase timeout (seconds). Default: 4 hours. Hard ceiling: 24 hours. |
| `dockerfile_content` | string | No | — | Override the repo's Dockerfile with this content |

**Response 202:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "status_url": "/api/v1/jobs/550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2025-03-15T10:00:00.000000+00:00"
}
```

Rate limit headers are included on this response (see [Common response headers](#common-response-headers)).

**Status codes:**

| Code | Description |
|------|-------------|
| 202 | Job accepted and queued |
| 401 | Authentication failed |
| 422 | Validation error (invalid `repo_url`, inaccessible repo, Dockerfile scan failure) |
| 429 | Rate limit exceeded (concurrent or daily) |

**curl (signing handled externally):**

```bash
# ds01-submit is strongly recommended for raw HTTP — manual signing is non-trivial
# See the Authentication section for the signing protocol
```

**ds01-submit:**

```bash
# Submit with defaults (1 GPU, main branch)
ds01-submit run https://github.com/hertie-data-science-lab/my-project

# Submit with options
ds01-submit run https://github.com/hertie-data-science-lab/my-project \
  --gpus 2 \
  --branch experiment/gpu-test \
  --name my-experiment \
  --timeout 7200

# Submit with a custom Dockerfile
ds01-submit run https://github.com/hertie-data-science-lab/my-project \
  --dockerfile ./Dockerfile.gpu
```

---

### GET /api/v1/jobs

List your submitted jobs, newest first. Paginated.

**Auth:** Required

**Query parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 20 | Max results per page (max: 100) |
| `offset` | integer | 0 | Pagination offset |
| `status` | string | — | Filter by status (e.g. `queued`, `running`, `succeeded`) |

**Response 200:**

```json
{
  "jobs": [
    {
      "job_id": "550e8400-e29b-41d4-a716-446655440000",
      "status": "succeeded",
      "job_name": "my-project-550e8400",
      "repo_url": "https://github.com/hertie-data-science-lab/my-project",
      "created_at": "2025-03-15T10:00:00.000000+00:00",
      "completed_at": "2025-03-15T10:45:00.000000+00:00"
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

**Status codes:**

| Code | Description |
|------|-------------|
| 200 | Success |
| 401 | Authentication failed |

**curl:**

```bash
# Sign request using your signing helper, then:
curl -H "Authorization: Bearer $DS01_API_KEY" \
     -H "X-Timestamp: $TIMESTAMP" \
     -H "X-Nonce: $NONCE" \
     -H "X-Signature: $SIGNATURE" \
     "https://ds01.hertie-data-science-lab.org/api/v1/jobs?limit=20&offset=0"
```

**ds01-submit:**

```bash
ds01-submit list
ds01-submit list --limit 50 --offset 20
ds01-submit list --json
```

---

### GET /api/v1/jobs/{job_id}

Get detailed status for a single job, including phase timestamps and error information.

**Auth:** Required

**Path parameters:**

| Parameter | Description |
|-----------|-------------|
| `job_id` | UUID of the job |

**Response 200:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "succeeded",
  "job_name": "my-project-550e8400",
  "repo_url": "https://github.com/hertie-data-science-lab/my-project",
  "branch": "main",
  "gpu_count": 1,
  "submitted_by": "alice",
  "created_at": "2025-03-15T10:00:00.000000+00:00",
  "started_at": "2025-03-15T10:01:00.000000+00:00",
  "completed_at": "2025-03-15T10:45:00.000000+00:00",
  "phases": {
    "queued": {
      "started_at": "2025-03-15T10:00:00.000000+00:00",
      "ended_at": "2025-03-15T10:01:00.000000+00:00"
    },
    "cloning": {
      "started_at": "2025-03-15T10:01:00.000000+00:00",
      "ended_at": "2025-03-15T10:01:30.000000+00:00"
    },
    "building": {
      "started_at": "2025-03-15T10:01:30.000000+00:00",
      "ended_at": "2025-03-15T10:05:00.000000+00:00"
    },
    "running": {
      "started_at": "2025-03-15T10:05:00.000000+00:00",
      "ended_at": "2025-03-15T10:45:00.000000+00:00"
    }
  },
  "error": null,
  "queue_position": null
}
```

For queued jobs, `queue_position` is a 1-based integer showing position in the queue. For active or terminal jobs it is `null`.

For failed jobs, `error` is populated:

```json
{
  "error": {
    "phase": "build",
    "message": "Docker build failed",
    "exit_code": 1
  }
}
```

**Status codes:**

| Code | Description |
|------|-------------|
| 200 | Success |
| 401 | Authentication failed |
| 404 | Job not found (or belongs to another user) |

**curl:**

```bash
curl -H "Authorization: Bearer $DS01_API_KEY" \
     -H "X-Timestamp: $TIMESTAMP" \
     -H "X-Nonce: $NONCE" \
     -H "X-Signature: $SIGNATURE" \
     "https://ds01.hertie-data-science-lab.org/api/v1/jobs/550e8400-e29b-41d4-a716-446655440000"
```

**ds01-submit:**

```bash
# Single status check
ds01-submit status 550e8400-e29b-41d4-a716-446655440000

# Poll until job completes (with exponential backoff)
ds01-submit status 550e8400-e29b-41d4-a716-446655440000 --follow

# Machine-readable output
ds01-submit status 550e8400-e29b-41d4-a716-446655440000 --json
```

Exit code 2 when job has failed status.

---

### GET /api/v1/jobs/{job_id}/logs

Get log output for each phase of a job (clone, build, run).

**Auth:** Required

**Path parameters:**

| Parameter | Description |
|-----------|-------------|
| `job_id` | UUID of the job |

**Response 200:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "logs": {
    "clone": "Cloning into 'repo'...\nremote: Enumerating objects...",
    "build": "Step 1/5 : FROM nvcr.io/nvidia/cuda:12.6.3-base-ubuntu24.04\n...",
    "run": "Running nvidia-smi...\nTue Mar 15 10:05:00 2025\n..."
  },
  "truncated": {
    "build": true
  }
}
```

Logs are per-phase. Phases that have not started are omitted from `logs`. If a log exceeds 1 MB, the oldest content is dropped and the phase key appears in `truncated` as `true`. The `truncated` field is `null` if no truncation occurred.

**Status codes:**

| Code | Description |
|------|-------------|
| 200 | Success |
| 401 | Authentication failed |
| 404 | Job not found (or belongs to another user) |

**curl:**

```bash
curl -H "Authorization: Bearer $DS01_API_KEY" \
     -H "X-Timestamp: $TIMESTAMP" \
     -H "X-Nonce: $NONCE" \
     -H "X-Signature: $SIGNATURE" \
     "https://ds01.hertie-data-science-lab.org/api/v1/jobs/550e8400-e29b-41d4-a716-446655440000/logs"
```

**ds01-submit:** No dedicated logs command — use `--json` with `status` or call the API directly.

---

### GET /api/v1/jobs/{job_id}/results

Download job output as a `tar.gz` archive. Only available for `succeeded` jobs.

The archive contains all files written to `/output/` inside the container during the run phase. Structure: `results/<your files>`.

**Auth:** Required

**Path parameters:**

| Parameter | Description |
|-----------|-------------|
| `job_id` | UUID of the job |

**Response 200:**

Binary `application/gzip` stream.

```
Content-Disposition: attachment; filename="job-{job_id}-results.tar.gz"
Content-Type: application/gzip
```

**Status codes:**

| Code | Description |
|------|-------------|
| 200 | Success — streaming tar.gz body |
| 401 | Authentication failed |
| 404 | Job not found, belongs to another user, or produced no output files |
| 409 | Job is still running or failed — results not available |
| 413 | Results exceed your quota's `max_result_size_mb` limit |

**curl:**

```bash
curl -H "Authorization: Bearer $DS01_API_KEY" \
     -H "X-Timestamp: $TIMESTAMP" \
     -H "X-Nonce: $NONCE" \
     -H "X-Signature: $SIGNATURE" \
     -o results.tar.gz \
     "https://ds01.hertie-data-science-lab.org/api/v1/jobs/550e8400-e29b-41d4-a716-446655440000/results"

tar xzf results.tar.gz
```

**ds01-submit:**

```bash
# Download to ./results/
ds01-submit results 550e8400-e29b-41d4-a716-446655440000

# Download to a custom directory
ds01-submit results 550e8400-e29b-41d4-a716-446655440000 -o ./output/my-run
```

---

### POST /api/v1/jobs/{job_id}/cancel

Cancel an active job. The job must be in `queued`, `cloning`, `building`, or `running` state.

The API sets the job status to `failed` and the background runner detects the change and kills any running container.

**Auth:** Required

**Path parameters:**

| Parameter | Description |
|-----------|-------------|
| `job_id` | UUID of the job |

**Response 200:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failed",
  "message": "Job cancelled"
}
```

**Status codes:**

| Code | Description |
|------|-------------|
| 200 | Job cancelled successfully |
| 401 | Authentication failed |
| 403 | Job belongs to another user |
| 404 | Job not found |
| 409 | Job is already in a terminal state (`succeeded` or `failed`) |

**curl:**

```bash
curl -X POST \
     -H "Authorization: Bearer $DS01_API_KEY" \
     -H "X-Timestamp: $TIMESTAMP" \
     -H "X-Nonce: $NONCE" \
     -H "X-Signature: $SIGNATURE" \
     "https://ds01.hertie-data-science-lab.org/api/v1/jobs/550e8400-e29b-41d4-a716-446655440000/cancel"
```

**ds01-submit:**

```bash
ds01-submit cancel 550e8400-e29b-41d4-a716-446655440000
```

---

### GET /api/v1/users/me/quota

Get your current quota usage and limits.

**Auth:** Required

**Response 200:**

```json
{
  "username": "alice",
  "group": "phd-students",
  "concurrent": {
    "used": 1,
    "limit": 3
  },
  "daily": {
    "used": 4,
    "limit": 10
  },
  "max_result_size_mb": 1024
}
```

| Field | Description |
|-------|-------------|
| `username` | Your GitHub username |
| `group` | Your resource group (from ds01-infra configuration) |
| `concurrent.used` | Currently active jobs |
| `concurrent.limit` | Maximum concurrent active jobs |
| `daily.used` | Jobs submitted today (resets at midnight UTC) |
| `daily.limit` | Maximum daily submissions |
| `max_result_size_mb` | Maximum downloadable result archive size in MB |

**Status codes:**

| Code | Description |
|------|-------------|
| 200 | Success |
| 401 | Authentication failed |

**curl:**

```bash
curl -H "Authorization: Bearer $DS01_API_KEY" \
     -H "X-Timestamp: $TIMESTAMP" \
     -H "X-Nonce: $NONCE" \
     -H "X-Signature: $SIGNATURE" \
     "https://ds01.hertie-data-science-lab.org/api/v1/users/me/quota"
```

**ds01-submit:**

```bash
# Called implicitly by ds01-submit configure to validate your key
ds01-submit configure
```

---

## GitHub Actions

The composite action is bundled in this repository at `action/`.

### Usage

```yaml
- uses: hertie-data-science-lab/ds01-jobs@v1
  with:
    api-key: ${{ secrets.DS01_API_KEY }}
    repo-url: ${{ github.server_url }}/${{ github.repository }}
    branch: ${{ github.ref_name }}
    gpus: 1
    timeout: 14400
    commit-results: 'true'
    results-path: ./results
```

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `api-key` | Yes | — | DS01 API key (`DS01_API_KEY` secret) |
| `repo-url` | No | Current repository URL | GitHub repository to submit |
| `branch` | No | Current branch | Branch to build |
| `gpus` | No | `1` | Number of GPUs |
| `timeout` | No | `14400` | Job timeout in seconds (max 86400) |
| `commit-results` | No | `true` | `"true"` commits results back to the branch; `"false"` uploads as an artifact |
| `results-path` | No | `./results` | Local path where results are downloaded |

### Outputs

| Output | Description |
|--------|-------------|
| `job-id` | Submitted job UUID |
| `status` | Final job status (`succeeded` or `failed`) |
| `results-path` | Path where results were downloaded |

### Behaviour

1. Installs `ds01-submit` via pip from the action directory.
2. Submits the job and polls until terminal state.
3. Downloads results to `results-path`.
4. If `commit-results: 'true'` and job succeeded: commits results as `ds01-bot` and pushes.
5. If `commit-results: 'false'` and job succeeded: uploads results as a GitHub Actions artifact named `ds01-results-{job-id}`.

---

## Job repo conventions

For the DS01 runner to collect your output:

1. Your repository must have a `Dockerfile` at the root (or pass `--dockerfile` to override).
2. Write all output files to `/output/` inside the container.
3. The runner runs your container with `--gpus all` — all requested GPUs are available.

### Minimal example Dockerfile

```dockerfile
FROM nvcr.io/nvidia/cuda:12.6.3-base-ubuntu24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 && rm -rf /var/lib/apt/lists/*

COPY . /workspace
WORKDIR /workspace

RUN mkdir -p /output
CMD ["python3", "train.py", "--output", "/output"]
```

### Dockerfile restrictions

Custom Dockerfiles (submitted via `dockerfile_content`) are scanned before acceptance:

- **Blocked base registries** — Only these base image registries are allowed:
  - `docker.io/library/` (official Docker images)
  - `nvcr.io/nvidia/` (NVIDIA GPU images)
  - `ghcr.io/astral-sh/` (uv/Astral images)
  - `docker.io/pytorch/`
  - `docker.io/tensorflow/`
  - `docker.io/huggingface/`
- **Blocked `ENV` keys** — `LD_PRELOAD`, `LD_LIBRARY_PATH`, `LD_AUDIT` cause a `dockerfile_scan_error` rejection.
- **Warning `ENV` keys** — `LD_DEBUG`, `PYTHONPATH` generate scan warnings but do not block submission.

The repository's own `Dockerfile` (not submitted via `dockerfile_content`) is **not** scanned — only custom overrides are scanned at submission time.

---

## Rate limiting

Two layers of rate limiting apply to `POST /api/v1/jobs`:

1. **Global rate limit** — 60 requests/minute across all users (enforced by slowapi, returns `429`).
2. **Per-user concurrent limit** — Default 3 concurrent active jobs. Configurable per user group in `resource-limits.yaml`.
3. **Per-user daily limit** — Default 10 submissions per day (resets midnight UTC). Configurable per user group.

Limits can be raised for specific user groups by a server admin via the `resource-limits.yaml` configuration in ds01-infra.

---

## See also

- [README.md](../README.md) — Quick start and CLI reference
- [docs/deployment.md](deployment.md) — Server setup and configuration
- [docs/architecture.md](architecture.md) — System design and component overview
- Interactive API docs: `https://ds01.hertie-data-science-lab.org/docs`
