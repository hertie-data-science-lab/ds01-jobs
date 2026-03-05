# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-04)

**Core value:** Researchers can submit GPU jobs remotely and get results back without direct server access.
**Current focus:** Phase 2 — Authentication

## Current Position

Phase: 2 of 7 (Authentication)
Plan: 1 of 3 in current phase
Status: In Progress
Last activity: 2026-03-05 — Completed 02-01 (auth foundation - database, models, deps)

Progress: [███░░░░░░░] 20%

## Performance Metrics

**Velocity:**
- Total plans completed: 3
- Average duration: 2min
- Total execution time: 6min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-foundation | 2 | 4min | 2min |
| 02-authentication | 1 | 2min | 2min |

**Recent Trend:**
- Last 5 plans: 01-01 (2min), 01-02 (2min), 02-01 (2min)
- Trend: stable

*Updated after each plan completion*

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

### Pending Todos

None yet.

### Blockers/Concerns

- Max job duration per group: confirm whether resource-limits.yaml defines `max_duration_minutes` per group before runner implementation.
- Shallow clone assumption: confirm whether `--recurse-submodules` is needed for primary users.
- Docker wrapper path (`/usr/local/bin/docker`): confirm correct on production server before runner executor code.
- GPU availability check mechanism: confirm whether nvidia-smi parsing or allocator state file is the better approach during Phase 4 research.

## Session Continuity

Last session: 2026-03-05
Stopped at: Completed 02-01-PLAN.md (auth foundation - database, models, deps)
Resume file: None
