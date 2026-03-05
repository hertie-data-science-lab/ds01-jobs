# ds01-jobs

## What This Is

A remote GPU job submission service for Hertie School's DS01 server. Researchers and faculty submit compute jobs via an authenticated HTTP API (or GitHub Actions), which queues, executes, and delivers results — without needing SSH access or VPN. Built as an independent service that integrates with ds01-infra's Docker wrapper for resource enforcement.

## Core Value

Researchers can submit GPU jobs remotely and get results back without direct server access.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Authenticated API for job submission (HMAC-signed API keys, bcrypt hashes, 90-day expiry)
- [ ] Job lifecycle management (accept → clone → build → run → deliver results)
- [ ] Dockerfile security scanning before any build executes
- [ ] Per-user rate limiting (concurrent + daily), configurable per group
- [ ] Off-campus access via Cloudflare Tunnel (no VPN required)
- [ ] Admin CLI for API key management (create/list/revoke)
- [ ] Job runner that picks up queued jobs and executes them via Docker wrapper
- [ ] Job status tracking with per-phase transitions (queued → cloning → building → running → succeeded/failed)
- [ ] Result delivery back to the user (mechanism TBD — git push, artifact, or API retrieval)
- [ ] Global API rate limiting (brute-force protection)

### Out of Scope

- Slurm integration — ds01-infra already provides resource management; Slurm would conflict with existing cgroup/GPU allocation. Revisit if multi-machine.
- GitHub Actions client — separate repo (e.g., hertie-data-science-lab/ds01-submit-job), built after API is stable.
- Interactive containers — handled by ds01-infra's container-deploy. ds01-jobs is batch-only.
- Multi-machine dispatch — single server first. Natural extension for later.
- Private GitHub repo support — requires credential handling, deferred.
- Cloud bursting — hybrid on-prem/cloud, future scope.
- Job pipelines/dependencies — workflow orchestration is overkill for v1.
- OAuth/SSO — API key auth is sufficient for the small user base.

## Context

### Origin

Extracted from ds01-infra's v1.1 milestone (Phase 13). The API code was built inside ds01-infra but is an application-layer concern, not infrastructure. Separated to avoid permanent coupling. Existing Phase 13 code serves as reference material — the project is being redesigned from scratch as an independent service.

### User

Drew Dimmery (faculty) — wants to submit GPU simulation jobs from phone via Claude Code, inspect results on GitHub. Workflow: set up simulations → submit to GPU server → results appear in GitHub. Other faculty/researchers will use it too.

### ds01-infra Integration

ds01-jobs integrates with ds01-infra at two points:

1. **Docker wrapper** (`/usr/local/bin/docker`) — all docker build/run commands go through the wrapper, which automatically injects cgroup placement, GPU allocation (via gpu_allocator_v2.py), resource limits, and ds01.* labels. No L2/L3 orchestrator commands needed — the wrapper provides full enforcement for batch jobs.

2. **resource-limits.yaml** (`/opt/ds01-infra/config/runtime/resource-limits.yaml`) — ds01-jobs reads this file directly via yaml.safe_load() for user group membership and API rate limits. ~40 lines of focused parsing, no import of ds01-infra's ResourceLimitParser. Path overridable via `DS01_RESOURCE_LIMITS_PATH` env var for testing.

### Existing Reference Code

Phase 13 prototype exists in `context/` and `src/` directories. Verified against requirements but never deployed or tested on the server. Design decisions from Phase 13 research (HMAC auth, Dockerfile scanning, SQLite rate limiting, slowapi for global limits) are validated and carry forward.

## Constraints

- **Server**: Single bare-metal Ubuntu server, 4x NVIDIA A100 GPUs
- **Python**: 3.10+ (system Python), deployed via venv, not containerised (needs host Docker access)
- **Database**: SQLite — appropriate for low-traffic batch job submission. WAL mode for concurrent API + runner access.
- **Network**: API bound to 127.0.0.1:8765 only — Cloudflare Tunnel proxies inbound. No 0.0.0.0 binding.
- **Auth**: HMAC-SHA256 signed requests with bcrypt-hashed API keys. Admin-provisioned, not self-service.
- **Dependencies**: ds01-infra must be deployed on the same server. Docker wrapper and resource-limits.yaml must exist.
- **Security**: Dockerfile scanning blocks unapproved base images and dangerous ENV vars (CVE-2025-23266 mitigation). Build runs without --gpus.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Separate repo from ds01-infra | Application layer, not infrastructure. Different dependency stacks (Python/FastAPI vs shell scripts). Clean subprocess boundary via Docker wrapper. | — Pending |
| Docker wrapper directly (not L2/L3 orchestrators) | Wrapper provides full resource enforcement (cgroups, GPU alloc, labels) on every docker command. L3 orchestrators designed for interactive containers, not batch jobs. | — Pending |
| Custom polling runner (not Slurm/Celery/RQ) | ds01-infra already handles resource management. Slurm conflicts with existing cgroup/GPU system. Task queues require Redis — overkill for low-traffic single server. | — Pending |
| Read resource-limits.yaml directly | ~40 lines of yaml.safe_load() replaces sys.path hack. No shared library, no config duplication. ds01-infra owns the file, ds01-jobs reads it. | — Pending |
| src/ds01_jobs/ package + pyproject.toml + uv | Proper imports, CLI as entrypoint, venv-based deployment, modern tooling. Not a library — packaging still needed for deployed service. | — Pending |
| Own deploy.sh | Independent deployment, decoupled from ds01-infra. git pull + uv sync + systemd reload. | — Pending |
| GitHub Actions client in separate repo | Standard pattern for GitHub Actions. Built after API is stable. Thin HTTP client calling /api/v1/jobs. | — Pending |

---
*Last updated: 2026-03-04 after initialization*
