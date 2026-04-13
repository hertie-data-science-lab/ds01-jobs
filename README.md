# ds01-jobs

Remote GPU job submission for Hertie School's DS01 server. Submit a job via CLI or GitHub Actions, get results back — no SSH or VPN required.

```bash
ds01-submit run https://github.com/hertie-data-science-lab/ds01-jobs --branch fixtures/smoke --gpus 1
```

## Quick start

### 1. Install the CLI

```bash
pip install git+https://github.com/hertie-data-science-lab/ds01-jobs.git
# or with uv:
uv tool install git+https://github.com/hertie-data-science-lab/ds01-jobs.git
```

### 2. Configure your API key

Get a key from your server administrator, then:

```bash
ds01-submit configure
# prompts: DS01 API key:
```

This validates the key against the server and saves credentials to `~/.config/ds01/credentials`.

To set the key without the prompt:

```bash
DS01_API_KEY=ds01_yourkey ds01-submit configure
```

### 3. Submit a job

Point `ds01-submit run` at a public GitHub repo containing a `Dockerfile`:

```bash
ds01-submit run https://github.com/hertie-data-science-lab/ds01-jobs --branch fixtures/smoke --gpus 1
# prints: d3a4b2c1-...
```

The command returns immediately with a job ID. The server clones the repo, builds the image, and runs it.

### 4. Check status

```bash
ds01-submit status d3a4b2c1-...
# or poll until done:
ds01-submit status d3a4b2c1-... --follow
```

Statuses: `queued` → `cloning` → `building` → `running` → `succeeded` / `failed`

### 5. Get results

```bash
ds01-submit results d3a4b2c1-... -o ./my-results
```

Downloads and extracts the `/output/` directory from the container into `./my-results/`.

## How it works

You submit a GitHub repo URL. The server clones it, builds the `Dockerfile`, and runs the container with access to the requested GPUs. Anything your job writes to `/output/` becomes downloadable via `ds01-submit results`. The server is accessible via Cloudflare Tunnel — no VPN or port forwarding needed.

For architecture detail see [docs/architecture.md](docs/architecture.md).

## Job repo conventions

Your repository needs:

- A `Dockerfile` at the root (or specify a path with `--dockerfile`)
- Write output files to `/output/` inside the container - these become the job results

The [`tests/integration/fixtures/scenarios/smoke/`](tests/integration/fixtures/scenarios/smoke/) directory in this repo is a minimal working example (published to the `fixtures/smoke` branch on origin):

```dockerfile
FROM nvcr.io/nvidia/cuda:12.6.3-base-ubuntu24.04

RUN mkdir -p /output

CMD nvidia-smi > /output/gpu.txt && echo "ok" > /output/status.txt
```

## CLI reference

### ds01-submit

| Command | Description |
|---------|-------------|
| `configure` | Store and validate API key |
| `run <repo_url>` | Submit a job |
| `status <job_id>` | Show job status |
| `results <job_id>` | Download and extract results |
| `list` | List submitted jobs |
| `cancel <job_id>` | Cancel a running job |

All commands accept `--json` for machine-readable output.

**`run` options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--gpus N` | `1` | Number of GPUs to request (1-8) |
| `--branch BRANCH` | `main` | Git branch to build |
| `--name NAME` | - | Human-readable job name |
| `--timeout SECONDS` | server default | Job timeout (60-86400) |
| `--dockerfile PATH` | - | Path to a local Dockerfile |

**`status` options:**

- `--follow` / `-f` - Poll until the job reaches a terminal state

**`results` options:**

- `-o PATH` / `--output PATH` - Output directory (default: `./results`)

**`list` options:**

- `--limit N` - Max jobs to return (default: 20)
- `--offset N` - Pagination offset

### ds01-job-admin

Admin CLI for server administrators. Requires access to the server's SQLite database.

| Command | Arguments | Options |
|---------|-----------|---------|
| `key-create <github_username> <unix_username>` | Both positional | `--expires 90d`, `--json` |
| `key-list` | - | `--json` |
| `key-revoke <username>` | GitHub username | `--yes`, `--json` |
| `key-rotate <username>` | GitHub username | `--expires 90d`, `--yes`, `--json` |

`key-create` verifies GitHub org membership (requires `gh` CLI or `GITHUB_TOKEN`) and checks that the Unix user exists on the server. Keys have format `ds01_<base64url_32bytes>` and default to 90-day expiry.

## API endpoints

All endpoints are under the `/api/v1` prefix. Requests are HMAC-SHA256 signed — `ds01-submit` handles this transparently.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | DB health probe |
| `POST` | `/api/v1/jobs` | Required | Submit a job |
| `GET` | `/api/v1/jobs` | Required | List jobs (paginated) |
| `GET` | `/api/v1/jobs/{job_id}` | Required | Job detail and phase timestamps |
| `GET` | `/api/v1/jobs/{job_id}/logs` | Required | Per-phase log content |
| `GET` | `/api/v1/jobs/{job_id}/results` | Required | Stream results as tar.gz |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | Required | Cancel an active job |
| `GET` | `/api/v1/users/me/quota` | Required | Quota usage and limits |

OpenAPI docs (Swagger UI) are available at `/docs` on a running server.

For full schemas, request/response examples, and status codes see [docs/api-reference.md](docs/api-reference.md).

## GitHub Actions

A composite action is bundled in this repo:

```yaml
- name: Run GPU job
  uses: hertie-data-science-lab/ds01-jobs@v1
  with:
    api-key: ${{ secrets.DS01_API_KEY }}
    repo-url: ${{ github.server_url }}/${{ github.repository }}
    branch: ${{ github.ref_name }}
    gpus: 1
    timeout: 14400
    commit-results: 'true'   # commit results back to the repo
    results-path: ./results
```

Outputs: `job-id`, `status`, `results-path`.

See [action/action.yml](action/action.yml) for full input documentation.

## Configuration

The CLI uses two environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DS01_API_KEY` | - | API key (overrides credentials file) |
| `DS01_API_URL` | `https://ds01.hertie-data-science-lab.org` | API base URL |

Credential resolution order: `DS01_API_KEY` env var → `~/.config/ds01/credentials` file.

For server configuration (all `DS01_JOBS_*` variables, systemd services, Cloudflare Tunnel setup) see [docs/deployment.md](docs/deployment.md).

## Documentation

| Document | Audience | Contents |
|----------|----------|----------|
| [docs/deployment.md](docs/deployment.md) | Server admins | `deploy.sh` usage, systemd services, Cloudflare Tunnel, full configuration reference |
| [docs/architecture.md](docs/architecture.md) | Developers | Component diagram, job lifecycle, auth flow, security model, database schema |
| [docs/api-reference.md](docs/api-reference.md) | API consumers | Full endpoint schemas, request/response examples, signing protocol |
