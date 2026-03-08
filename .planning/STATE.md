---
gsd_state_version: 1.0
milestone: v0.1
milestone_name: milestone
status: executing
last_updated: "2026-03-08T15:55:30.000Z"
progress:
  total_phases: 6
  completed_phases: 5
  total_plans: 14
  completed_plans: 13
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-04)

**Core value:** Researchers can submit GPU jobs remotely and get results back without direct server access.
**Current focus:** Phase 05.1 — API Integration Tests

## Current Position

Phase: 5.1 of 7 (API Integration Tests)
Plan: 1 of 1 in current phase - COMPLETE
Status: Phase 05.1 complete
Last activity: 2026-03-08 — Completed 05.1-01 (API integration tests)

Progress: [█████████░] 90%

## Performance Metrics

**Velocity:**
- Total plans completed: 12
- Average duration: 3min
- Total execution time: 39min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-foundation | 2 | 4min | 2min |
| 02-authentication | 3 | 12min | 4min |
| 03-job-submission | 1 | 4min | 4min |
| 04-job-runner | 3 | 9min | 3min |
| 05-status-and-results | 2 | 6min | 3min |
| 05.1-api-integration-tests | 1 | 4min | 4min |

**Recent Trend:**
- Last 5 plans: 04-02 (5min), 04-03 (3min), 05-01 (2min), 05-03 (4min), 05.1-01 (4min)
- Trend: stable

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- mypy scoped to src/ds01_jobs/ — brownfield files at src/ level have pre-existing errors (out of scope)
- Settings uses _env_file=None in tests for isolation from .env files
- Ruff excludes brownfield src/ files — consistent with mypy scoping
- CI mypy scoped to src/ds01_jobs/ — brownfield files out of scope
- Tier 2 handles pytest exit code 5 (no tests collected) as success
- Result delivery: server stores output files, serves via API endpoint. CLI and GitHub Action handle retrieval client-side.
- Clients (Phase 6) before Deployment (Phase 7): build CLI + Action against local dev server, validate end-to-end before production deploy.
- CLI client (`ds01-submit`) and GitHub Action both in v0.1.0 scope (Phase 6). CLI handles HMAC signing. Action lives in action/ subdirectory — extract to ds01-actions when org has 3+ actions.
- Runner is separate systemd service with `KillMode=process` (not control-group) — preserves Docker containers for startup recovery.
- GPU availability: runner checks real GPU state (nvidia-smi / allocator), not internal SUM query. Retries on rejection.
- Auth: GitHub org membership (hertie-data-science-lab) verified at key creation time, not on every request. Admin roles deferred to v0.2.0+.
- Phase 4 (runner) is highest-risk phase — needs its own phase-level research pass before implementation.
- Database functions accept optional db_path param for testability, defaulting to Settings
- Async/sync database split: aiosqlite for FastAPI handlers, sqlite3 for Typer CLI commands
- Nonce cache is in-memory dict with monotonic clock TTL - cleared on restart
- bcrypt.checkpw runs via asyncio.to_thread to avoid blocking event loop
- All auth failures return generic 401 - specific reasons logged server-side
- Rate limiter keyed by key_id from Bearer token, falls back to client IP
- key-rotate uses UPDATE approach (overwrites existing row) for simplicity - single-key-per-user means no history needed
- Added @contextmanager decorator to get_db_sync for proper with-statement usage
- Non-greedy regex for repo name to correctly strip .git suffix in URL validation
- Unresolved build args in FROM produce info-level violation, not error
- [Phase 04-job-runner]: GPU idle threshold set at 100 MiB - GPUs below this are considered available
- [Phase 04-job-runner]: nvidia-smi query uses asyncio.create_subprocess_exec - output is small so PIPE is fine
- [Phase 04-job-runner]: Cancel endpoint uses DB status update only - runner detects on next poll cycle (up to 5s latency)
- [Phase 04-job-runner]: Completed task cleanup happens synchronously in poll loop, not via task callbacks
- [Phase 05-status-and-results]: queued phase started_at uses job created_at timestamp for accurate queue time measurement
- [Phase 05-status-and-results]: Phase timestamps stored as JSON dict with started_at/ended_at per phase
- [Phase 05-status-and-results]: Used unittest.mock.patch for _get_settings override in tests since it is called directly (not via Depends)
- [Phase 05]: Used unittest.mock.patch for _get_settings in log tests since lru_cache prevents FastAPI dependency_overrides
- [Phase 05.1]: Used pytest_asyncio.fixture for async fixtures - required for pytest-asyncio strict mode with pytest 9.x

### Roadmap Evolution

- Phase 05.1 inserted after Phase 05: API integration tests (URGENT) — Tier 1 round-trip tests for complete server-side API surface before building clients

### Pending Todos

None yet.

### Blockers/Concerns

- Max job duration per group: confirm whether resource-limits.yaml defines `max_duration_minutes` per group before runner implementation.
- Shallow clone assumption: confirm whether `--recurse-submodules` is needed for primary users.
- Docker wrapper path (`/usr/local/bin/docker`): confirm correct on production server before runner executor code.
- GPU availability check mechanism: confirm whether nvidia-smi parsing or allocator state file is the better approach during Phase 4 research.

## Session Continuity

Last session: 2026-03-08
Stopped at: Completed 05.1-01-PLAN.md (API integration tests)
Resume file: None
