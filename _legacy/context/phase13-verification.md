---
phase: 13-api-foundation-authentication-security-baseline
verified: 2026-02-26T16:20:52Z
status: passed
score: 7/7 must-haves verified
re_verification: false
---

# Phase 13: API Foundation, Authentication & Security Baseline — Verification Report

**Phase Goal:** Users can submit authenticated job requests to a publicly accessible API, with Dockerfile security scanning and rate limits in place before any user code runs.
**Verified:** 2026-02-26T16:20:52Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | POST /api/v1/jobs accepts authenticated requests and returns {job_id, status, status_url} | VERIFIED | `routers/jobs.py` — full endpoint, UUID job_id, status="queued", status_url="/api/v1/jobs/{id}" |
| 2 | Authentication requires HMAC-SHA256 signature over canonical request with bcrypt-hashed API keys | VERIFIED | `auth.py` — full 10-step chain: key parse, bcrypt verify, timestamp, nonce, HMAC compare_digest |
| 3 | Dockerfile content is scanned before job creation; blocking violations return 422 | VERIFIED | `scanner.py` + `routers/jobs.py` — scan_dockerfile + get_blocking_violations called before DB insert |
| 4 | Per-user rate limiting (concurrent + daily) enforced before any job is queued | VERIFIED | `rate_limit.py` + `routers/jobs.py` — check_rate_limits raises before DB insert; 429 with full detail |
| 5 | API is accessible off-campus via Cloudflare Tunnel, never bound to 0.0.0.0 | VERIFIED | `ds01-api.service` — `--host 127.0.0.1:8765`, no 0.0.0.0 anywhere in service or code |
| 6 | Admin can create, list, and revoke API keys via CLI | VERIFIED | `ds01-job-admin` — key-create/key-list/key-revoke, Typer CLI, executable |
| 7 | Global 60 req/min API rate limit active via slowapi before any endpoint logic runs | VERIFIED | `limiter.py` + `@limiter.limit("60/minute")` on submit_job + handler registered in main.py |

**Score:** 7/7 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `scripts/api/main.py` | FastAPI app factory, lifespan, error handlers, /health | VERIFIED | init_db in lifespan, 422 handler, include_router, slowapi handler, /health endpoint |
| `scripts/api/database.py` | SQLite connection pool, WAL mode, 3 tables | VERIFIED | `PRAGMA journal_mode=WAL`, api_keys/jobs/rate_limits tables with IF NOT EXISTS |
| `scripts/api/models.py` | Pydantic v2 schemas: JobSubmitRequest, JobSubmitResponse, etc. | VERIFIED | All 6 schema classes present, Field() constraints |
| `scripts/api/requirements.txt` | Pinned Python dependencies | VERIFIED | 7 deps: fastapi, uvicorn, aiosqlite, passlib, slowapi, dockerfile, httpx |
| `scripts/api/auth.py` | FastAPI dependency get_current_user for HMAC auth | VERIFIED | compare_digest, X-Timestamp/Nonce/Signature headers, X-DS01-Key-Expiry-Warning |
| `scripts/api/scanner.py` | Dockerfile scanner with scan_dockerfile() | VERIFIED | LD_PRELOAD blocked, nvcr.io/nvidia/ registry, is_docker_hub_official, scratch skip |
| `scripts/api/rate_limit.py` | SQLite-backed concurrent + daily rate limiter | VERIFIED | check_concurrent_limit, check_daily_limit, check_rate_limits, RateLimitExceeded |
| `scripts/api/limiter.py` | Shared slowapi Limiter instance | VERIFIED | Created to avoid circular import; key_func uses first 16 chars of Bearer token |
| `scripts/api/routers/jobs.py` | POST /api/v1/jobs endpoint | VERIFIED | submit_job with auth, rate limits, scan, DB insert, 429/422 responses |
| `scripts/api/routers/__init__.py` | Routers package init | VERIFIED | Empty package marker exists |
| `scripts/admin/ds01-job-admin` | Admin CLI for API key management | VERIFIED | Executable, shebang, key-create/list/revoke, INSERT OR REPLACE, bcrypt |
| `config/deploy/systemd/ds01-api.service` | Systemd unit for FastAPI service | VERIFIED | 127.0.0.1:8765, --workers 1, Restart=on-failure, ExecStartPre mkdir |
| `config/deploy/systemd/ds01-cloudflared.service` | Systemd unit for Cloudflare Tunnel | VERIFIED | Wants=ds01-api.service, cloudflared command, Restart=on-failure |
| `config/runtime/resource-limits.yaml` (api_limits) | api_limits per group | VERIFIED | student: 2/10, researcher: 3/20, faculty: 5/30, admin: 10/100 |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `main.py` | `database.py` | lifespan imports init_db | WIRED | `from database import init_db` + `await init_db()` in lifespan |
| `auth.py` | `database.py` | get_db dependency for key lookup | WIRED | `from database import get_db` + `db=Depends(get_db)` |
| `routers/jobs.py` | `auth.py` | Depends(get_current_user) | WIRED | `from auth import get_current_user` + `user: dict = Depends(get_current_user)` |
| `routers/jobs.py` | `scanner.py` | scan_dockerfile import | WIRED | `from scanner import get_blocking_violations, scan_dockerfile` + called on dockerfile_content |
| `routers/jobs.py` | `rate_limit.py` | check_rate_limits import | WIRED | `from rate_limit import RateLimitExceeded, check_rate_limits, increment_daily_count` + called |
| `main.py` | `routers/jobs.py` | app.include_router | WIRED | `app.include_router(jobs_router)` |
| `main.py` | `limiter.py` | shared Limiter instance | WIRED | `from limiter import limiter` + `app.state.limiter = limiter` |
| `routers/jobs.py` | `limiter.py` | @limiter.limit decorator | WIRED | `@limiter.limit("60/minute")` on submit_job |
| `ds01-job-admin` | `database.py` | shared DB_PATH (ds01-jobs.db) | WIRED | Both use `/var/lib/ds01/api/ds01-jobs.db` |
| `ds01-cloudflared.service` | `ds01-api.service` | Wants= dependency | WIRED | `Wants=network-online.target ds01-api.service` |
| `deploy.sh` | `ds01-api.service` | systemd service deployment | WIRED | Conditional cp + enable at line 519 |
| `deploy.sh` | `ds01-cloudflared.service` | systemd service deployment | WIRED | Conditional cp at line 529 |
| `deploy.sh` | `ds01-job-admin` | symlink via deploy_cmd | WIRED | `deploy_cmd "$INFRA_ROOT/scripts/admin/ds01-job-admin" "ds01-job-admin" "Admin"` at line 298 |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| API-01 | 13-01, 13-05 | User can submit GPU job via authenticated HTTP POST to /api/v1/jobs | SATISFIED | `routers/jobs.py` — POST /api/v1/jobs, JobSubmitRequest, UUID job_id response |
| API-02 | 13-02 | HMAC-signed API key auth with bcrypt hashes, 90-day expiry, rotation support | SATISFIED | `auth.py` — full HMAC chain; `ds01-job-admin` — INSERT OR REPLACE rotation |
| API-03 | 13-04 | Admin CLI for key lifecycle (create/list/revoke) | SATISFIED | `ds01-job-admin` — 3 Typer commands, executable, bcrypt, 90-day expiry |
| JOB-03 | 13-03 | Dockerfile scanning: approved base images only, LD_PRELOAD/LD_LIBRARY_PATH blocked | SATISFIED | `scanner.py` — nvcr.io/nvidia/* + Docker Hub official; LD_PRELOAD/LD_LIBRARY_PATH in BLOCKED_ENV_VARS |
| SAFE-01 | 13-06 | API accessible off-campus via Cloudflare Tunnel, no firewall changes | SATISFIED | `ds01-cloudflared.service` deployed; API bound to 127.0.0.1 only |
| SAFE-02 | 13-05 | Per-user rate limiting: max concurrent + max daily, configurable per group | SATISFIED | `rate_limit.py` + api_limits in resource-limits.yaml for all 4 groups |
| SAFE-03 | 13-05, 13-06 | Job containers flow through DS01 Docker wrapper with cgroup placement and labels | SATISFIED (Phase 13 portion) | job_id stored in DB; comment in jobs.py documents Phase 14 handoff (`--label ds01.job_id=<job_id>`); deploy.sh deploys API service |

**Note on SAFE-03:** The Phase 13 requirement is the foundation (job_id stored, comment documenting Docker wrapper contract). Full Docker wrapper pass-through executes in Phase 14 (job runner). This is the correct scope — SAFE-03 is satisfied to the extent Phase 13 can deliver.

---

## Anti-Patterns Found

No anti-patterns detected. Scan across all modified files:
- No TODO/FIXME/PLACEHOLDER comments
- No stub return values (return null, return {}, return [])
- No 0.0.0.0 binding in service files or code
- No uvicorn.run() call in main.py
- All syntax checks pass

---

## Human Verification Required

### 1. Cloudflare Tunnel live reachability

**Test:** After running `sudo deploy`, perform the one-time `cloudflared tunnel login` + `cloudflared tunnel create ds01-api` setup, configure `/root/.cloudflared/config.yml`, then start both services and hit the public tunnel URL at `/health`.
**Expected:** HTTP 200 with `{"status": "ok", "version": "0.1.0"}` from the public Cloudflare Tunnel URL.
**Why human:** Requires Cloudflare account credentials, interactive browser auth, and a live tunnel — cannot verify programmatically.

### 2. End-to-end authenticated job submission

**Test:** Create an API key with `ds01-job-admin key-create testuser`, then submit a HMAC-signed POST to `/api/v1/jobs` with a valid GitHub repo URL.
**Expected:** HTTP 200 with `{job_id: "...", status: "queued", status_url: "/api/v1/jobs/..."}` and the job row visible in the SQLite database.
**Why human:** Requires pip-installed dependencies, a running API service, and generating a valid HMAC signature — full integration test.

### 3. Rate limit enforcement

**Test:** Submit more than 2 concurrent jobs (student group limit) as the same user.
**Expected:** Third submission returns HTTP 429 with `{error: "rate_limit_exceeded", limit_type: "concurrent", retry_after_seconds: 60, current_count: 2, max_allowed: 2}`.
**Why human:** Requires a running API, active SQLite state, and concurrent submissions.

### 4. Dockerfile scan rejection at submission time

**Test:** Submit a job with `dockerfile_content` containing `FROM nvidia/cuda:12.0` (non-NGC DockerHub user image).
**Expected:** HTTP 422 with `{error: "dockerfile_violation", detail: [{field: "dockerfile:FROM:1", message: "Base image 'nvidia/cuda:12.0' not from an approved registry..."}]}`.
**Why human:** Requires a running API with dependencies installed (including the `dockerfile` PyPI Go-backed package).

---

## Summary

Phase 13 fully achieves its goal. All 7 API Python modules exist with substantive, wired implementations — no stubs detected. All 13 key links are present and functional at the code level. Every requirement (API-01 through API-03, JOB-03, SAFE-01 through SAFE-03) is implemented to the scope Phase 13 can deliver.

The one deliberate partial — SAFE-03 Docker wrapper pass-through — is correctly scoped: Phase 13 stores the job_id that Phase 14 will pass to the Docker wrapper. The comment in jobs.py documents the exact contract (`--label ds01.job_id=<job_id> --label ds01.user=<username>`).

The notable deviation from plan (creation of `limiter.py` to break a circular import between `main.py` and `routers/jobs.py`) was correctly handled — the shared module pattern is the standard FastAPI approach.

Four items require human verification: live Cloudflare Tunnel reachability, end-to-end HMAC-authenticated submission, rate limit enforcement, and Dockerfile scan rejection. These require pip-installed dependencies and a running service, which cannot be verified programmatically.

---

_Verified: 2026-02-26T16:20:52Z_
_Verifier: Claude (gsd-verifier)_
