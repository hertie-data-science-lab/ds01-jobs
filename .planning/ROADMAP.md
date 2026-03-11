# Roadmap: ds01-jobs — v0.1.0

## Overview

Seven phases take ds01-jobs from empty repository to a deployed GPU job submission service with usable clients (v0.1.0). Foundation establishes the package structure and CI pipelines that every subsequent phase depends on. Authentication and the job submission API are built as the API gateway. The job runner — the highest-risk component — gets its own phase with full operational hardening. Status and result retrieval complete the server-side workflow. A CLI client and GitHub Action make the API accessible to researchers (tested against a local dev server). Finally, production deployment wires the three systemd services and Cloudflare Tunnel into a live service.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation** - Package layout, uv, pre-commit, CI pipelines (Tier 1 + Tier 2), config
- [x] **Phase 2: Authentication** - HMAC auth, API key lifecycle, admin CLI, health endpoint, global rate limit
- [x] **Phase 3: Job Submission** - Submission endpoint, Dockerfile scanning, per-user rate limiting
- [x] **Phase 4: Job Runner** - Polling runner, job lifecycle execution, GPU slot management, operational hardening
- [x] **Phase 5: Status and Results** - Job status polling, log retrieval, result download, quota endpoint
- [x] **Phase 6: Clients** - ds01-submit CLI tool, GitHub Action in action/ subdirectory (tested against local dev server)
- [ ] **Phase 7: Deployment** - deploy.sh, systemd units, Cloudflare Tunnel service, pre-deploy safety check

## Phase Details

### Phase 1: Foundation
**Goal**: The project skeleton is fully operational — importable package, enforced code quality, and both CI tiers passing before any feature code is written
**Depends on**: Nothing (first phase)
**Requirements**: FOUND-01, FOUND-02, FOUND-03, FOUND-04, FOUND-05, FOUND-06, NET-01
**Success Criteria** (what must be TRUE):
  1. `import ds01_jobs` succeeds in a clean venv created by `uv sync`
  2. `ruff check` and `mypy` pass with zero errors on an empty-but-typed package skeleton
  3. A PR to main triggers Tier 1 CI (lint, type check, unit tests) on a GitHub-hosted runner and goes green
  4. A push to main triggers Tier 2 CI (integration tests) on the self-hosted GPU runner and goes green
  5. Configuration values (DB path, API host/port, resource-limits.yaml path) are readable from environment variables via Pydantic Settings
**Plans**: 2 plans
Plans:
- [x] 01-01-PLAN.md — Package skeleton, pyproject.toml, Pydantic Settings, test suite
- [x] 01-02-PLAN.md — Pre-commit verification, CI workflows (Tier 1 + Tier 2)

### Phase 2: Authentication
**Goal**: Users can authenticate via signed API keys, and admins can provision and revoke those keys via a CLI
**Depends on**: Phase 1
**Requirements**: AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, AUTH-06, AUTH-07, NET-03, RATE-05
**Success Criteria** (what must be TRUE):
  1. A signed request with a valid API key passes authentication; an unsigned or tampered request returns 401
  2. A replayed request (duplicate nonce within 5 minutes) is rejected with 401
  3. A request made with an API key within 14 days of expiry receives the `X-DS01-Key-Expiry-Warning` header
  4. `ds01-job-admin key-create <username>` prints the raw key once and stores only the bcrypt hash
  5. `ds01-job-admin key-revoke <username>` causes that key to be rejected on subsequent requests
  6. `GET /health` returns `{status, version}` with no authentication required
  7. The global rate limiter returns 429 after 60 requests per minute from the same API key
**Plans**: 3 plans
Plans:
- [x] 02-01-PLAN.md — Database layer, response models, config extensions, dependency updates
- [x] 02-02-PLAN.md — HMAC auth dependency, health endpoint, rate limiter, app factory
- [x] 02-03-PLAN.md — Admin CLI (key-create, key-list, key-revoke, key-rotate)

### Phase 3: Job Submission
**Goal**: Authenticated users can submit GPU jobs, with Dockerfile scanning and rate limit enforcement before any job reaches the queue
**Depends on**: Phase 2
**Requirements**: JOB-01, JOB-02, JOB-03, JOB-04, JOB-05, SCAN-01, SCAN-02, SCAN-03, SCAN-04, SCAN-05, RATE-01, RATE-02, RATE-03, RATE-04
**Success Criteria** (what must be TRUE):
  1. `POST /api/v1/jobs` with a valid repo URL and Dockerfile returns `{job_id, status: "queued", status_url}` immediately
  2. A submission with a non-GitHub URL (or a private IP) is rejected with a structured 422 before any network request is made
  3. A Dockerfile containing a disallowed base image or `ENV LD_PRELOAD` is rejected with a structured error showing line number, directive, and reason
  4. A user who has reached their concurrent job limit receives a 429 with `retry_after_seconds`, `limit_type`, `current_count`, and `max_allowed`
  5. Rate limits for a user reflect their group membership as defined in `resource-limits.yaml`
**Plans**: 2 plans
Plans:
- [x] 03-01-PLAN.md — Contracts, Dockerfile scanner, URL validation (models, config, schema, scanner.py, url_validation.py)
- [x] 03-02-PLAN.md — Per-user rate limiter, jobs router, app wiring (rate_limit.py, jobs.py, endpoint tests)

### Phase 4: Job Runner
**Goal**: Queued jobs execute reliably through the full Docker lifecycle, with the runner surviving restarts and shutdowns without leaving orphaned containers or stuck job states
**Depends on**: Phase 3
**Requirements**: RUN-01, RUN-02, RUN-03, RUN-04, RUN-05, RUN-06, RUN-07, RUN-08, RUN-09, RUN-10, RUN-11
**Success Criteria** (what must be TRUE):
  1. A queued job progresses through `queued → cloning → building → running → succeeded` with each transition committed to SQLite before the corresponding subprocess starts
  2. A job that exceeds its build timeout (15 min) or run timeout is killed via process group and transitions to `failed`
  3. Killing and restarting the runner marks any in-progress jobs as `failed` rather than leaving them stuck in `running`
  4. SIGTERM causes the runner to stop accepting new jobs and exit cleanly after draining
  5. A `POST /api/v1/jobs/{id}/cancel` on a running job kills the process group and transitions status to `failed`
  6. All docker commands are invoked via `/usr/local/bin/docker` (the DS01 wrapper), never the real Docker binary
**Plans**: 3 plans
Plans:
- [x] 04-01-PLAN.md — Database schema extensions, runner config, GPU availability module
- [x] 04-02-PLAN.md — Job executor (clone/build/run subprocess pipeline, timeouts, cleanup)
- [x] 04-03-PLAN.md — Runner poll loop, signal handling, startup recovery, cancel endpoint

### Phase 5: Status and Results
**Goal**: Users can observe job progress, retrieve logs for debugging, download result files, and check their remaining quota
**Depends on**: Phase 4
**Requirements**: STAT-01, STAT-02, STAT-03, STAT-04
**Success Criteria** (what must be TRUE):
  1. `GET /api/v1/jobs/{id}` returns current status, per-phase timestamps, and a human-readable error message on failure
  2. `GET /api/v1/jobs/{id}/logs` returns captured stdout/stderr for each completed phase (clone, build, run)
  3. `GET /api/v1/jobs/{id}/results` delivers the job's output files as a downloadable artifact
  4. `GET /api/v1/users/me/quota` returns the user's current concurrent job count, daily count, and configured limits
**Plans**: 3 plans
Plans:
- [x] 05-01-PLAN.md — Schema extension, config, response models, helpers, executor phase timestamps
- [x] 05-02-PLAN.md — Status, logs, listing, and quota endpoints
- [x] 05-03-PLAN.md — Results download endpoint with tar.gz streaming

### Phase 05.1: API integration tests (INSERTED)

**Goal**: The server-side API surface is verified end-to-end via Tier 1 integration tests that exercise real wiring between auth, submission, status, logs, cancel, quota, and results — catching integration bugs before clients are built on top
**Depends on**: Phase 5
**Requirements**: INT-LIFECYCLE, INT-AUTH, INT-RATE, INT-CI
**Success Criteria** (what must be TRUE):
  1. Integration tests exercise the full API round-trip: create key → submit job → check status → list jobs → check quota → cancel, using the real FastAPI app and SQLite
  2. Auth integration: signed requests succeed, unsigned/tampered/expired requests fail, nonce replay is rejected
  3. Rate limiting integration: submitting past configured limits returns 429 with correct headers
  4. All integration tests run in Tier 1 CI (no Docker/GPU required) and pass
**Plans**: 1 plan
Plans:
- [x] 05.1-01-PLAN.md — Shared integration fixtures and API round-trip test suite

### Phase 6: Clients
**Goal**: Researchers can submit jobs, check status, and retrieve results without constructing HMAC-signed requests by hand — via either a CLI tool or a GitHub Action (tested against local dev server)
**Depends on**: Phase 5.1
**Requirements**: CLI-01, CLI-02, GHA-01, GHA-02
**Success Criteria** (what must be TRUE):
  1. `ds01-submit run https://github.com/user/repo --gpus 1` submits a job and prints the job ID and status URL
  2. `ds01-submit status <job-id>` shows current job status and phase timestamps
  3. `ds01-submit results <job-id> -o ./output/` downloads result files to the local directory
  4. The CLI reads the API key from `~/.config/ds01/credentials` or `DS01_API_KEY` env var and handles all HMAC signing transparently
  5. The GitHub Action (`uses: hertie-data-science-lab/ds01-jobs/action@v0.1.0`) submits a job from a CI workflow and commits results back to the triggering repo
  6. Integration tests for CLI commands (submit, status, results) against a local dev server are added to the Tier 1 integration test suite
**Plans**: 2 plans
Plans:
- [x] 06-01-PLAN.md — Signing HTTP client, ds01-submit CLI (configure, run, status, results, list, cancel), unit tests
- [x] 06-02-PLAN.md — GitHub Action (composite action + entrypoint), CLI integration tests, admin CLI instruction update

### Phase 06.1: ds01-infra container integration (INSERTED)

**Goal**: Job containers launched by ds01-jobs are fully integrated with the ds01-infra ecosystem — using `sudo -u {unix_username}` so the Docker wrapper automatically handles labels, cgroup, GPU allocation, and event logging identically to containers launched via ds01-infra's own tools
**Depends on**: Phase 6
**Requirements**: INFRA-IDENTITY, INFRA-SUDO, INFRA-LIMITS, INFRA-EVENTS, INFRA-RATELIMIT-FIX
**Success Criteria** (what must be TRUE):
  1. API keys store both `github_username` (display/auth) and `unix_username` (server identity) as mandatory fields; `key-create` validates the Unix user exists on the server
  2. All docker commands (build, run, rm) execute via `sudo -u {unix_username}` so the Docker wrapper sees the correct user identity
  3. `docker inspect` on a ds01-jobs container shows correct `ds01.*` labels (`ds01.user={unix_username}`, `ds01.managed=true`, GPU labels) — all injected automatically by the wrapper
  4. Per-container resource limits (`--memory`, `--shm-size`, `--pids-limit`) are read from `get_resource_limits.py {unix_username}` and passed to docker run
  5. `container-owner-tracker` daemon correctly identifies ds01-jobs containers and attributes them to the submitting Unix user
  6. Rate limiting group resolution delegates to `get_resource_limits.py` instead of the broken `users:` section lookup in resource-limits.yaml
**Plans**: 2 plans

Plans:
- [ ] 06.1-01-PLAN.md — Database dual identity, auth, CLI key-create, rate limit fix, config (INFRA-IDENTITY, INFRA-RATELIMIT-FIX)
- [ ] 06.1-02-PLAN.md — Executor sudo -u, resource limits, interface label, runner propagation (INFRA-SUDO, INFRA-LIMITS, INFRA-EVENTS)

### Phase 7: Deployment
**Goal**: The complete service — API server, job runner, and Cloudflare Tunnel — can be installed and upgraded on the production server with a single script and zero manual steps
**Depends on**: Phase 6.1
**Requirements**: DEP-01, DEP-02, DEP-03, DEP-04, DEP-05, NET-02
**Success Criteria** (what must be TRUE):
  1. Running `deploy.sh` on the server creates the venv, installs deps via `uv sync`, copies systemd units, and symlinks the admin CLI — with no manual steps after
  2. `ds01-api.service`, `ds01-runner.service`, and `ds01-cloudflared.service` all start, pass health checks, and survive a server reboot
  3. `ds01-cloudflared.service` lists `ds01-api.service` as a dependency and only starts after the API is healthy
  4. Running `deploy.sh` while jobs are active prints a warning and requires confirmation before proceeding
  5. The API is reachable from an off-campus network via the Cloudflare Tunnel URL without VPN
  6. Tier 2 integration tests exercise full job lifecycle on the self-hosted GPU runner (submit → clone → build → run → succeed → download results) and are added to the existing integration test suite
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 5.1 → 6 → 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 2/2 | Complete | 2026-03-05 |
| 2. Authentication | 3/3 | Complete | 2026-03-06 |
| 3. Job Submission | 2/2 | Complete | 2026-03-06 |
| 4. Job Runner | 3/3 | Complete | 2026-03-07 |
| 5. Status and Results | 3/3 | Complete | 2026-03-07 |
| 5.1 API Integration Tests | 1/1 | Complete | 2026-03-08 |
| 6. Clients | 2/2 | Complete | 2026-03-10 |
| 7. Deployment | 0/TBD | Not started | - |
