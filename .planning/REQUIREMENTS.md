# Requirements: ds01-jobs

**Defined:** 2026-03-04
**Core Value:** Researchers can submit GPU jobs remotely and get results back without direct server access.

## v1 Requirements

Requirements for v0.1.0 milestone. Each maps to roadmap phases.

### Project Foundation

- [x] **FOUND-01**: Project uses src/ds01_jobs/ package layout with pyproject.toml and uv for dependency management
- [x] **FOUND-02**: Pre-commit hooks installed (ruff format + lint via existing dotconfigs pattern)
- [x] **FOUND-03**: Tier 1 CI runs on every PR: ruff lint, ruff format check, mypy type check, pytest unit tests (GitHub-hosted runner)
- [x] **FOUND-04**: Tier 2 CI runs on push to main: integration tests on self-hosted GPU runner
- [x] **FOUND-05**: Deploy script (deploy.sh) installs venv, deps, systemd units, admin CLI symlink
- [x] **FOUND-06**: Pydantic Settings for configuration (DB path, API host/port, resource-limits.yaml path) with env var overrides

### Authentication

- [x] **AUTH-01**: User can authenticate via HMAC-SHA256 signed API key (Bearer token + X-Timestamp + X-Nonce + X-Signature headers)
- [x] **AUTH-02**: API keys stored as bcrypt hashes with 90-day expiry and single-key-per-user rotation
- [x] **AUTH-03**: Expiry warning header (X-DS01-Key-Expiry-Warning) set when key is within 14 days of expiry
- [x] **AUTH-04**: Nonce replay protection via in-memory cache with 5-minute TTL
- [x] **AUTH-05**: Admin can create API keys via `ds01-job-admin key-create <username>` — verifies GitHub org membership (hertie-data-science-lab) before creation; key printed once, never stored in plaintext
- [x] **AUTH-06**: Admin can list all API keys with status via `ds01-job-admin key-list`
- [x] **AUTH-07**: Admin can revoke API keys via `ds01-job-admin key-revoke <username>`

### Job Submission

- [ ] **JOB-01**: User can submit a GPU job via POST /api/v1/jobs with repo_url, branch, script_path, gpu_count
- [ ] **JOB-02**: Immediate response with {job_id, status: "queued", status_url} on successful submission
- [x] **JOB-03**: Structured 422 response with machine-parseable field-level errors on validation failure
- [x] **JOB-04**: GitHub URL validation (only https://github.com/owner/repo accepted — SSRF prevention)
- [x] **JOB-05**: Optional inline dockerfile_content field for pre-scan at submission time

### Dockerfile Scanning

- [x] **SCAN-01**: All FROM directives scanned across all stages (multi-stage bypass prevention)
- [x] **SCAN-02**: Only approved base registries allowed: nvcr.io/nvidia/* and Docker Hub official images
- [x] **SCAN-03**: ENV LD_PRELOAD and LD_LIBRARY_PATH blocked outright (CVE-2025-23266 mitigation)
- [x] **SCAN-04**: USER root produces warning (non-blocking) — cgroup constraints apply regardless
- [x] **SCAN-05**: Scan violations return structured error with line number, directive, and reason

### Rate Limiting

- [ ] **RATE-01**: Per-user concurrent job limit enforced before any job is queued
- [ ] **RATE-02**: Per-user daily job limit enforced before any job is queued
- [ ] **RATE-03**: Limits configurable per group via ds01-infra's resource-limits.yaml (read directly, no import hack)
- [ ] **RATE-04**: 429 response includes retry_after_seconds, limit_type, current_count, max_allowed
- [x] **RATE-05**: Global API rate limit (60 req/min per API key) via slowapi for brute-force protection

### Job Runner

- [x] **RUN-01**: Runner polls SQLite for queued jobs and executes them (separate systemd service)
- [x] **RUN-02**: Job lifecycle: clone repo → docker build (no --gpus) → docker run (via wrapper) → capture output
- [x] **RUN-03**: Status transitions committed to SQLite before each subprocess starts (queued → cloning → building → running → succeeded/failed)
- [x] **RUN-04**: stdout/stderr written to log files per phase (clone.log, build.log, run.log), not PIPE
- [x] **RUN-05**: GPU slot management: check real GPU availability (nvidia-smi / allocator state) before dispatch, not internal SUM query — respects GPUs used by interactive containers
- [x] **RUN-06**: Build timeout (15 min) and configurable job timeout enforced via process group kill
- [x] **RUN-07**: Startup recovery: detect orphaned running/building/cloning jobs on restart, mark as failed
- [x] **RUN-08**: Graceful shutdown: SIGTERM handler drains running jobs before stopping
- [x] **RUN-09**: Job cancellation via POST /api/v1/jobs/{id}/cancel — kills process group
- [x] **RUN-10**: Docker build cache cleanup after each job to prevent disk exhaustion
- [x] **RUN-11**: All docker commands go through /usr/local/bin/docker (wrapper) for cgroup/GPU enforcement

### Status & Results

- [x] **STAT-01**: User can poll job status via GET /api/v1/jobs/{id} (status, phase timestamps, error message)
- [x] **STAT-02**: User can retrieve stdout/stderr logs via GET /api/v1/jobs/{id}/logs
- [x] **STAT-03**: User can download result files via GET /api/v1/jobs/{id}/results
- [x] **STAT-04**: User can check remaining quota via GET /api/v1/users/me/quota

### Network & Access

- [x] **NET-01**: API bound to 127.0.0.1:8765 only — never 0.0.0.0
- [ ] **NET-02**: Cloudflare Tunnel (named, not quick) proxies inbound HTTPS to the API
- [x] **NET-03**: Health check endpoint at GET /health returns {status, version}

### Deployment

- [ ] **DEP-01**: deploy.sh creates venv, installs deps via uv sync, copies systemd units, symlinks CLI
- [ ] **DEP-02**: ds01-api.service runs uvicorn from venv with proper ExecStart path
- [ ] **DEP-03**: ds01-runner.service runs job runner with KillMode=process (preserves Docker containers for startup recovery)
- [ ] **DEP-04**: ds01-cloudflared.service with Wants=ds01-api.service dependency
- [ ] **DEP-05**: Pre-deploy check: warn if jobs are currently running (prevent data loss)

### Clients

- [ ] **CLI-01**: `ds01-submit` CLI tool handles HMAC request signing, job submission, status polling, and result download
- [ ] **CLI-02**: CLI reads API key from `~/.config/ds01/credentials` or `DS01_API_KEY` env var
- [ ] **GHA-01**: GitHub Action in action/ subdirectory for job submission from CI workflows (uses: hertie-data-science-lab/ds01-jobs/action@v0.1.0)
- [ ] **GHA-02**: Action handles result retrieval and commits back to user's repo

## v2 Requirements

Deferred to future milestones. Not in current roadmap.

### Multi-Machine

- **MULTI-01**: Dispatch jobs to secondary GPU server (Drew's office A5500)
- **MULTI-02**: Machine health monitoring and failover

### GitHub Actions Extraction

- **GHA-03**: Extract to ds01-actions repo when org has 3+ actions (ds01-infra already has docs-push action)

### Enhanced Features

- **ENH-01**: Scheduled/recurring job submissions
- **ENH-02**: Job pipelines (A finishes → B starts)
- **ENH-03**: Usage analytics dashboard
- **ENH-04**: Private GitHub repo support (credential handling)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Slurm integration | ds01-infra already provides resource management; Slurm conflicts with cgroup/GPU allocation |
| Interactive containers | Handled by ds01-infra's container-deploy; ds01-jobs is batch-only |
| OAuth/SSO | API key auth sufficient for small user base |
| Cloud bursting | Future scope; single server first |
| Web UI | API-first; GitHub Actions is the primary client |
| Real-time log streaming | WebSockets add complexity; polling is appropriate for batch jobs |
| Monitoring/admin API | Prometheus/Grafana already handles this; expose Grafana via Cloudflare Tunnel |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| FOUND-01 | Phase 1 | Complete |
| FOUND-02 | Phase 1 | Complete |
| FOUND-03 | Phase 1 | Complete |
| FOUND-04 | Phase 1 | Complete |
| FOUND-05 | Phase 1 | Complete |
| FOUND-06 | Phase 1 | Complete |
| AUTH-01 | Phase 2 | Complete |
| AUTH-02 | Phase 2 | Complete |
| AUTH-03 | Phase 2 | Complete |
| AUTH-04 | Phase 2 | Complete |
| AUTH-05 | Phase 2 | Complete |
| AUTH-06 | Phase 2 | Complete |
| AUTH-07 | Phase 2 | Complete |
| JOB-01 | Phase 3 | Pending |
| JOB-02 | Phase 3 | Pending |
| JOB-03 | Phase 3 | Complete |
| JOB-04 | Phase 3 | Complete |
| JOB-05 | Phase 3 | Complete |
| SCAN-01 | Phase 3 | Complete |
| SCAN-02 | Phase 3 | Complete |
| SCAN-03 | Phase 3 | Complete |
| SCAN-04 | Phase 3 | Complete |
| SCAN-05 | Phase 3 | Complete |
| RATE-01 | Phase 3 | Pending |
| RATE-02 | Phase 3 | Pending |
| RATE-03 | Phase 3 | Pending |
| RATE-04 | Phase 3 | Pending |
| RATE-05 | Phase 2 | Complete |
| RUN-01 | Phase 4 | Complete |
| RUN-02 | Phase 4 | Complete |
| RUN-03 | Phase 4 | Complete |
| RUN-04 | Phase 4 | Complete |
| RUN-05 | Phase 4 | Complete |
| RUN-06 | Phase 4 | Complete |
| RUN-07 | Phase 4 | Complete |
| RUN-08 | Phase 4 | Complete |
| RUN-09 | Phase 4 | Complete |
| RUN-10 | Phase 4 | Complete |
| RUN-11 | Phase 4 | Complete |
| STAT-01 | Phase 5 | Complete |
| STAT-02 | Phase 5 | Complete |
| STAT-03 | Phase 5 | Complete |
| STAT-04 | Phase 5 | Complete |
| NET-01 | Phase 1 | Complete |
| NET-02 | Phase 7 | Pending |
| NET-03 | Phase 2 | Complete |
| CLI-01 | Phase 6 | Pending |
| CLI-02 | Phase 6 | Pending |
| GHA-01 | Phase 6 | Pending |
| GHA-02 | Phase 6 | Pending |
| DEP-01 | Phase 7 | Pending |
| DEP-02 | Phase 7 | Pending |
| DEP-03 | Phase 7 | Pending |
| DEP-04 | Phase 7 | Pending |
| DEP-05 | Phase 7 | Pending |

**Coverage:**
- v1 requirements: 55 total
- Mapped to phases: 55
- Unmapped: 0

---
*Requirements defined: 2026-03-04*
*Last updated: 2026-03-04 — 55 requirements mapped to phases 1-7 (clients added to v0.1.0)*
