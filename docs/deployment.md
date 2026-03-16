# Deployment Guide

This guide covers deploying ds01-jobs from scratch on a Linux server. It targets admins
with Linux, systemd, and Docker familiarity. All steps are handled by `deploy.sh` unless
stated otherwise.

---

## Prerequisites

The following must be in place **before** running `deploy.sh`. The script does not install
these dependencies.

### uv

`uv` must be available somewhere in the system PATH or in a common location under
`/home/datasciencelab`. Install it via the official installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify: `uv --version`

### Docker

The Docker daemon must be running. ds01-jobs uses Docker for building and running job
containers.

```bash
systemctl status docker
```

### ds01-infra

ds01-infra must be deployed on the same server **before** deploying ds01-jobs. Two
ds01-infra components are required at runtime:

- `/usr/local/bin/docker` - Docker wrapper that injects cgroup constraints and GPU allocation
- `/opt/ds01-infra/config/runtime/resource-limits.yaml` - Per-user resource group definitions
- `/opt/ds01-infra/scripts/docker/get_resource_limits.py` - Resource limits resolver script

If ds01-infra is not deployed, jobs will still run but without per-user resource limits or
cgroup isolation. The executor falls back to running Docker directly.

### Git

Git must be installed for `deploy.sh` to verify a clean working tree before deploying.

```bash
git --version
```

---

## Quick Deploy

`deploy.sh` is idempotent - safe to run on first install and for upgrades alike. It requires
a clean git working tree and must run as root.

```bash
# Interactive - prompts for confirmation and Cloudflare Tunnel token
sudo ./deploy.sh

# Non-interactive - skip all prompts (suitable for CI)
sudo ./deploy.sh --yes

# Dry run - preview steps without making changes
sudo ./deploy.sh --dry-run
```

The script logs to `/tmp/ds01-jobs-deploy-<timestamp>.log` for post-deploy review.

---

## What deploy.sh Does

The script runs these steps in order:

### 1. Create system user

Creates a `ds01` system user (no login shell, home at `/opt/ds01-jobs`) if it does not
already exist. Adds `ds01` to the `docker` and `ds-admin` groups.

### 2. Set up directories

| Path | Owner | Mode | Purpose |
|------|-------|------|---------|
| `/etc/ds01-jobs/` | root:root | 0755 | Config directory |
| `/opt/ds01-jobs/data/` | ds01:ds-admin | 770 | SQLite database |
| `/var/lib/ds01-jobs/workspaces/` | ds01:ds-admin | 0750 | Job workspaces |
| `/var/log/ds01/events.jsonl` | ds01:ds01 | - | Event log |

### 3. Set up environment file

Copies `config/env.example` to `/etc/ds01-jobs/env` if the file does not already exist
(upgrades preserve the existing file). Sets permissions to `0600 root:root`.

In interactive mode, prompts for the Cloudflare Tunnel token if `TUNNEL_TOKEN` is empty.
With `--yes`, leaves `TUNNEL_TOKEN` empty and logs a warning - set it manually afterwards.

### 4. Install cloudflared

Installs `cloudflared` from the Cloudflare apt repository if not already present.

### 5. Set up Python environment

Runs `uv sync --locked` in `/opt/ds01-jobs` to install the exact dependency versions from
`uv.lock`. Makes the `.venv` group-readable for the `ds01` service user. Verifies that all
three entrypoints exist: `ds01-job-admin`, `ds01-job-runner`, `ds01-submit`.

### 6. Install sudoers drop-in

Copies `config/sudoers.d/ds01-jobs` to `/etc/sudoers.d/ds01-jobs` (0440). Validates with
`visudo -c` before proceeding.

### 7. Install and start systemd services

Copies the three unit files to `/etc/systemd/system/`, runs `daemon-reload`, enables all
three services, then restarts them. Verifies that all services are active and that the API
health endpoint returns `{"status":"ok"}`.

---

## Systemd Services

Three services run as the `ds01` user. All load environment from `/etc/ds01-jobs/env`.

| Service | Binary | Purpose |
|---------|--------|---------|
| `ds01-api` | `uvicorn ds01_jobs.app:app --host 127.0.0.1 --port 8765` | FastAPI job submission API |
| `ds01-runner` | `ds01-job-runner` | Asyncio poll loop - dispatches queued jobs to Docker |
| `ds01-cloudflared` | `cloudflared --no-autoupdate tunnel run` | Cloudflare Tunnel - public HTTPS ingress |

All three services restart automatically on failure (`Restart=always`, `RestartSec=5`).

`ds01-cloudflared` will not start until the API health endpoint responds. Its `ExecStartPre`
polls `http://127.0.0.1:8765/health` for up to 30 seconds before allowing the tunnel to
connect. This prevents the tunnel from forwarding traffic to an API that is still
initialising.

`ds01-runner` requires `docker.service` to be running before it starts.

---

## Configuration Reference

All configuration is loaded from `/etc/ds01-jobs/env`. The file must be `0600 root:root` -
systemd reads it before dropping privileges to the `ds01` user.

### Core settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DS01_JOBS_DB_PATH` | `/opt/ds01-jobs/data/jobs.db` | SQLite database path |
| `DS01_JOBS_API_HOST` | `127.0.0.1` | API bind address - never set to `0.0.0.0` |
| `DS01_JOBS_API_PORT` | `8765` | API bind port |
| `DS01_JOBS_WORKSPACE_ROOT` | `/var/lib/ds01-jobs/workspaces` | Per-job working directory root |

### ds01-infra integration

| Variable | Default | Description |
|----------|---------|-------------|
| `DS01_JOBS_RESOURCE_LIMITS_PATH` | `/opt/ds01-infra/config/runtime/resource-limits.yaml` | Resource limits config from ds01-infra |
| `DS01_JOBS_GET_RESOURCE_LIMITS_BIN` | `/opt/ds01-infra/scripts/docker/get_resource_limits.py` | Script to resolve per-user resource limits |
| `DS01_JOBS_DOCKER_BIN` | `/usr/local/bin/docker` | Docker wrapper path from ds01-infra |

### Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `DS01_JOBS_GITHUB_ORG` | `hertie-data-science-lab` | GitHub org for API key validation |
| `DS01_JOBS_KEY_EXPIRY_DAYS` | `90` | Default API key lifetime in days |

### Rate limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `DS01_JOBS_DEFAULT_CONCURRENT_LIMIT` | `3` | Per-user concurrent job limit (override via resource-limits.yaml) |
| `DS01_JOBS_DEFAULT_DAILY_LIMIT` | `10` | Per-user daily submission limit (override via resource-limits.yaml) |
| `DS01_JOBS_DEFAULT_MAX_RESULT_SIZE_MB` | `1024` | Maximum downloadable result archive size (MB) |

### Job execution timeouts

| Variable | Default | Description |
|----------|---------|-------------|
| `DS01_JOBS_CLONE_TIMEOUT_SECONDS` | `120` | Git clone timeout (2 minutes) |
| `DS01_JOBS_BUILD_TIMEOUT_SECONDS` | `900` | Docker build timeout (15 minutes) |
| `DS01_JOBS_DEFAULT_JOB_TIMEOUT_SECONDS` | `14400` | Default container run timeout (4 hours) |
| `DS01_JOBS_MAX_JOB_TIMEOUT_SECONDS` | `86400` | Hard ceiling on job timeout (24 hours) |
| `DS01_JOBS_RUNNER_POLL_INTERVAL` | `5.0` | Seconds between runner polls for new jobs |

### Tunnel

| Variable | Default | Description |
|----------|---------|-------------|
| `TUNNEL_TOKEN` | _(empty)_ | Cloudflare Tunnel token - required for public access |

### Advanced (not in env.example)

These variables are configurable but not included in the default env file. Override them
only if your deployment differs from the defaults.

| Variable | Default | Description |
|----------|---------|-------------|
| `DS01_JOBS_ALLOWED_GITHUB_ORGS` | _(empty - all orgs allowed)_ | Restrict job repo URLs to specific GitHub orgs (comma-separated) |
| `DS01_JOBS_ALLOWED_BASE_REGISTRIES` | See config.py | Docker registry prefixes permitted in Dockerfiles |
| `DS01_JOBS_BLOCKED_ENV_KEYS` | `LD_PRELOAD,LD_LIBRARY_PATH,LD_AUDIT` | ENV keys that cause Dockerfile scan errors |
| `DS01_JOBS_WARNING_ENV_KEYS` | `LD_DEBUG,PYTHONPATH` | ENV keys that cause Dockerfile scan warnings |

---

## Cloudflare Tunnel Setup

ds01-jobs exposes its API publicly via Cloudflare Tunnel. The API itself binds only to
`127.0.0.1` - there is no firewall port to open.

### Getting the token

1. Log in to the [Cloudflare Zero Trust dashboard](https://one.cloudflare.com)
2. Navigate to **Networks** - **Tunnels**
3. Create a new tunnel (or select an existing one)
4. Copy the tunnel token from the install instructions

### Configuring the token

Set `TUNNEL_TOKEN` in `/etc/ds01-jobs/env`:

```bash
# Edit as root
sudo nano /etc/ds01-jobs/env

# Add or update:
TUNNEL_TOKEN=eyJhIjo...
```

Restart the cloudflared service after changing the token:

```bash
sudo systemctl restart ds01-cloudflared
```

The tunnel automatically routes traffic to `http://127.0.0.1:8765`. Configure the
public hostname in the Cloudflare dashboard - point it at this tunnel, with no additional
proxy settings required.

---

## API Key Management

API keys are managed with the `ds01-job-admin` CLI, located at
`/opt/ds01-jobs/.venv/bin/ds01-job-admin`. Run it as a user who can read
`/etc/ds01-jobs/env`, or with the database path set via `DS01_JOBS_DB_PATH`.

### Create a key

```bash
# Creates a key for <github_username>, running jobs as <unix_username>
ds01-job-admin key-create <github_username> <unix_username>

# With custom expiry
ds01-job-admin key-create alice alice_user --expires 180d
```

Both positional arguments are required. `<github_username>` is the user's GitHub handle
(membership in `DS01_JOBS_GITHUB_ORG` is verified). `<unix_username>` is the local POSIX
account that Docker builds and runs will execute as via `sudo -u`.

The key is printed once - save it or share it securely with the user.

### List keys

```bash
ds01-job-admin key-list
```

### Revoke a key

```bash
ds01-job-admin key-revoke <github_username>

# Skip confirmation prompt
ds01-job-admin key-revoke <github_username> --yes
```

### Rotate a key

Issues a new key and revokes the old one atomically.

```bash
ds01-job-admin key-rotate <github_username>
```

All commands support `--json` for machine-readable output.

---

## Verify Your Deployment

Run these checks immediately after deploying:

```bash
# Check all three services are active
systemctl status ds01-api ds01-runner ds01-cloudflared

# API health endpoint (should return {"status":"ok","db_ok":true})
curl -s http://127.0.0.1:8765/health | python3 -m json.tool

# Follow API logs
journalctl -u ds01-api -f

# Follow runner logs
journalctl -u ds01-runner -f
```

A healthy deployment shows all three services as `active (running)` and the health endpoint
returns HTTP 200 with `"db_ok": true`.

---

## Troubleshooting

### Service not starting

Check the service logs for the specific error:

```bash
journalctl -u ds01-api --no-pager -n 50
journalctl -u ds01-runner --no-pager -n 50
journalctl -u ds01-cloudflared --no-pager -n 50
```

Common causes:

- **Missing env file** - `/etc/ds01-jobs/env` does not exist or has wrong permissions.
  The file must be `0600 root:root`. Recreate from `config/env.example` and rerun `deploy.sh`.
- **uv not found** - `uv` was not on PATH during deploy, so `.venv` was not created.
  Install `uv`, then rerun `deploy.sh`.
- **Port already bound** - Something else is on port 8765. Check with
  `ss -tlnp | grep 8765` and resolve the conflict.

### Tunnel not connecting

```bash
journalctl -u ds01-cloudflared --no-pager -n 50
```

Common causes:

- **Invalid token** - `TUNNEL_TOKEN` in `/etc/ds01-jobs/env` is empty or incorrect.
  Get a fresh token from the Cloudflare dashboard and update the env file.
- **API not ready** - `ds01-cloudflared` waits up to 30 seconds for `ds01-api` to be
  healthy. If the API is still starting, cloudflared will retry. Check `ds01-api` logs first.
- **DNS propagation** - After creating a new tunnel hostname in Cloudflare, DNS can take
  a few minutes to propagate. The tunnel itself connects immediately; the public hostname
  may not resolve yet.

### API not responding

```bash
curl -v http://127.0.0.1:8765/health
```

Common causes:

- **Wrong host/port** - Verify `DS01_JOBS_API_HOST` and `DS01_JOBS_API_PORT` in env file.
- **Env file permissions too restrictive** - The `ds01` user must be able to read the env
  file at startup (systemd reads it as root and passes the variables to the process).
  Check `0600 root:root` is set: `stat /etc/ds01-jobs/env`.
- **Database not writable** - `/opt/ds01-jobs/data/` must be writable by `ds01`.
  Check ownership: `ls -la /opt/ds01-jobs/data/`.

### Job stuck in queued

```bash
# Check runner is running
systemctl status ds01-runner

# Check runner logs for GPU availability messages
journalctl -u ds01-runner --no-pager -n 50
```

Common causes:

- **No GPUs available** - The runner polls for available GPUs and only dispatches jobs when
  a GPU is free. Check `nvidia-smi` for GPU utilisation.
- **Runner not polling** - If the runner has no log output, it may be stuck. Restart it:
  `sudo systemctl restart ds01-runner`. Note: restarting the runner marks any in-progress
  jobs as failed (orphan recovery).
- **ds01-infra not available** - If `get_resource_limits.py` is failing on every poll,
  check that ds01-infra is deployed and the paths in the env file are correct.
