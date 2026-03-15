# DS01 Jobs - Contributor Guide

This guide is for developers who want to contribute to the `ds01-jobs` codebase itself. It covers environment setup, project structure, testing, code quality tooling, and the workflow for getting changes merged.

## Prerequisites

- **Python 3.13** - the project pins this version in `.python-version`
- **[uv](https://docs.astral.sh/uv/)** - the package manager and build tool used throughout
- **Git** - with access to the [hertie-data-science-lab/ds01-jobs](https://github.com/hertie-data-science-lab/ds01-jobs) repository

## Setting Up the Development Environment

```bash
# Clone the repository
git clone https://github.com/hertie-data-science-lab/ds01-jobs.git
cd ds01-jobs

# Install all dependencies (including dev group)
uv sync --locked --all-groups

# Verify setup - run unit tests
uv run pytest -m 'not integration' --tb=short
```

If the tests pass, your environment is ready. The `uv sync --locked` flag ensures you get the exact dependency versions from `uv.lock`.

## Project Structure

The project uses a `src` layout with the package at `src/ds01_jobs/`:

```
ds01-jobs/
├── pyproject.toml               # Project metadata, dependencies, tool config
├── uv.lock                      # Locked dependency versions
├── .python-version              # Python 3.13
├── src/ds01_jobs/
│   ├── __init__.py              # Package version (__version__)
│   ├── app.py                   # FastAPI application factory (create_app)
│   ├── config.py                # Settings from environment (pydantic-settings)
│   ├── models.py                # Pydantic request/response schemas
│   ├── auth.py                  # HMAC-SHA256 authentication dependency
│   ├── jobs.py                  # API endpoints (submit, status, logs, results, cancel, list, quota)
│   ├── executor.py              # Single-job execution pipeline (clone -> build -> run -> collect -> cleanup)
│   ├── runner.py                # Long-running poll-dispatch service (JobRunner)
│   ├── database.py              # SQLite schema, async/sync connection providers
│   ├── client.py                # HTTP client with transparent HMAC signing
│   ├── submit.py                # ds01-submit CLI (researcher-facing)
│   ├── cli.py                   # ds01-job-admin CLI (admin-facing)
│   ├── scanner.py               # Dockerfile static analysis (base images, blocked ENVs)
│   ├── url_validation.py        # GitHub URL validation + SSRF prevention
│   ├── gpu.py                   # nvidia-smi GPU availability queries
│   ├── rate_limit.py            # Per-user quota enforcement (concurrent + daily)
│   ├── middleware.py            # slowapi global rate limiting setup
│   └── health.py                # GET /health endpoint
├── tests/
│   ├── conftest.py              # Shared fixtures
│   ├── unit/                    # Unit tests (no GPU/Docker needed)
│   └── integration/             # Integration tests (require GPU runner)
├── .github/workflows/
│   ├── ci.yml                   # Tier 1: lint, format, types, unit tests (ubuntu-latest)
│   └── ci-integration.yml       # Tier 2: integration tests (self-hosted GPU runner)
├── systemd/                     # Service unit files for deployment
├── config/                      # Configuration files
└── data/                        # SQLite database (runtime, not committed)
```

### Three Entry Points

The package installs three CLI commands (defined in `pyproject.toml` under `[project.scripts]`):

| Command | Module | Purpose |
|---|---|---|
| `ds01-submit` | `submit.py` | Researcher CLI - submit jobs, check status, download results |
| `ds01-job-admin` | `cli.py` | Admin CLI - create/revoke/rotate API keys |
| `ds01-job-runner` | `runner.py` | Background service - polls for queued jobs and executes them |

### How the Pieces Fit Together

The system has two long-running processes:

1. **API server** (`app.py`) - a FastAPI application serving the REST API. It handles authentication (via `auth.py`), job submission and validation (via `jobs.py`, `scanner.py`, `url_validation.py`), rate limiting (via `rate_limit.py`, `middleware.py`), and job status/results queries. All state lives in SQLite (via `database.py`).

2. **Runner** (`runner.py`) - an async poll-dispatch loop that reads queued jobs from SQLite, checks GPU availability (via `gpu.py`), and dispatches them through `executor.py`. The executor clones the repo, builds a Docker image, runs the container with GPU access, collects results from `/output/`, then cleans up.

The researcher CLI (`submit.py`) and admin CLI (`cli.py`) are client-side tools. The researcher CLI uses `client.py` to sign every HTTP request with HMAC-SHA256, matching the server's `auth.py` verification.

## Architecture Overview

The following diagram shows how requests flow through the system:

```
Researcher CLI (ds01-submit)
     |
     | HTTPS + HMAC signing
     v
Reverse Proxy (443) --> uvicorn (127.0.0.1:8765) --> FastAPI (app.py)
     |                                                  |
     |                                          Depends(get_current_user) --> auth.py
     |                                          Depends(get_db) --> database.py
     |                                                  |
     |                                          jobs.py endpoints
     |                                                  |
     v                                                  v
SQLite DB  <--- --- --- --- --- --- --- --- --- runner.py (polls for queued jobs)
(data/jobs.db)                                          |
                                                 executor.py
                                                        |
                                                 Docker (git clone --> build --> run)
                                                        |
                                                 GPU (via --gpus all)
```

**Key architectural points:**

- The **API server** and the **runner** are separate processes. They do not communicate directly - they share state through the SQLite database. The API writes job records (with status `queued`), and the runner polls for jobs in that status.
- **SQLite** is the single source of truth for all state. WAL mode is enabled at init to allow concurrent reads during writes, which is essential since the API and runner access the database simultaneously.
- The **reverse proxy** (nginx) handles TLS termination on port 443 and forwards to uvicorn on `127.0.0.1:8765`. The API server never handles TLS directly.
- **Authentication** uses HMAC-SHA256 request signing. The CLI (`client.py`) signs each request with the API key, and the server (`auth.py`) verifies the signature. This prevents replay attacks via timestamps and nonces.
- The **executor** runs each job through a pipeline: clone the repository, build a Docker image, run the container with GPU access, copy results from `/output/`, then clean up the container and image. Each phase has its own timeout and log file.
- Docker commands are executed via `sudo -u <unix_username>` so that each researcher's containers run under their own Unix user, providing process-level isolation.

## Database Schema

The database has two tables, defined in `database.py` (`SCHEMA_SQL`).

### `api_keys` Table

Stores API key credentials for authentication.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Auto-incrementing primary key |
| `username` | TEXT | GitHub username (the identity used for job ownership) |
| `unix_username` | TEXT | Unix username on the server (used for `sudo -u` in Docker execution) |
| `key_id` | TEXT | First 8 characters of the token, used for key lookup (UNIQUE, indexed) |
| `key_hash` | TEXT | bcrypt hash of the full API key |
| `created_at` | TEXT | ISO timestamp of key creation |
| `expires_at` | TEXT | ISO timestamp of key expiry |
| `revoked` | INTEGER | 0 = active, 1 = revoked |
| `last_used_at` | TEXT | ISO timestamp of last successful authentication (nullable) |

### `jobs` Table

Stores all job records and their execution state.

| Column | Type | Description |
|---|---|---|
| `id` | TEXT | UUID primary key (generated at submission time) |
| `username` | TEXT | GitHub username of the submitter (FK to `api_keys.username`) |
| `unix_username` | TEXT | Unix username (copied from the API key at submission) |
| `repo_url` | TEXT | GitHub repository URL |
| `branch` | TEXT | Git branch to clone (default: `main`) |
| `gpu_count` | INTEGER | Number of GPUs requested (default: 1) |
| `job_name` | TEXT | Human-readable job name |
| `timeout_seconds` | INTEGER | Maximum run time in seconds (nullable - uses default) |
| `dockerfile_content` | TEXT | Inline Dockerfile content if provided via `--dockerfile` (nullable) |
| `status` | TEXT | Current job status (default: `queued`) |
| `created_at` | TEXT | ISO timestamp of job creation |
| `updated_at` | TEXT | ISO timestamp of last status change |
| `failed_phase` | TEXT | Which phase failed: `clone`, `build`, or `run` (nullable) |
| `exit_code` | INTEGER | Process exit code on failure (nullable) |
| `error_summary` | TEXT | Human-readable error message (nullable) |
| `started_at` | TEXT | ISO timestamp when execution began (nullable) |
| `completed_at` | TEXT | ISO timestamp when the job reached a terminal state (nullable) |
| `phase_timestamps` | TEXT | JSON object with per-phase `started_at`/`ended_at` timestamps |

### Job Status State Machine

Jobs progress through these states:

```
              +----------+
              |  queued   |  (initial state, set at submission)
              +----+-----+
                   |
                   v
              +----------+
              | cloning   |  (shallow git clone, retries once)
              +----+-----+
                   |
                   v
              +----------+
              | building  |  (docker build, 15 min timeout)
              +----+-----+
                   |
                   v
              +----------+
              | running   |  (container execution, user-defined timeout)
              +----+-----+
                   |
            +------+------+
            |             |
            v             v
      +-----------+  +--------+
      | succeeded |  | failed |
      +-----------+  +--------+
```

A job can transition to `failed` from any active state (including via user cancellation). Once a job reaches `succeeded` or `failed`, it is terminal and cannot change state.

## Running Locally

### API Server

```bash
uv run uvicorn ds01_jobs.app:app --host 127.0.0.1 --port 8765 --reload
```

The `--reload` flag enables auto-reload on code changes. The API will be available at `http://127.0.0.1:8765`, with interactive docs at `http://127.0.0.1:8765/docs`.

### Runner

In a separate terminal:

```bash
uv run ds01-job-runner
```

**Note:** The runner requires GPU access (nvidia-smi) and Docker. For local development without a GPU, you can work on the API layer and unit tests without starting the runner.

### Configuration

All settings are loaded from environment variables with the `DS01_JOBS_` prefix (see `config.py`). The defaults are sensible for the production server. Key settings you might override locally:

- `DS01_JOBS_DB_PATH` - SQLite database path (default: `/opt/ds01-jobs/data/jobs.db`)
- `DS01_JOBS_API_HOST` / `DS01_JOBS_API_PORT` - API bind address (default: `127.0.0.1:8765`)
- `DS01_JOBS_WORKSPACE_ROOT` - where job workspaces are created (default: `/var/lib/ds01-jobs/workspaces`)

You can also place a `.env` file in the project root.

## Environment Variables for Local Development

The following table lists all settings you are likely to override during local development. Each maps to a field in the `Settings` class in `config.py` with the `DS01_JOBS_` prefix.

| Variable | Description | Default | Local dev suggestion |
|---|---|---|---|
| `DS01_JOBS_DB_PATH` | Path to the SQLite database file | `/opt/ds01-jobs/data/jobs.db` | `/tmp/ds01-dev.db` |
| `DS01_JOBS_WORKSPACE_ROOT` | Directory where job workspaces are created | `/var/lib/ds01-jobs/workspaces` | `/tmp/ds01-workspaces` |
| `DS01_JOBS_DOCKER_BIN` | Path to the Docker binary | `/usr/local/bin/docker` | `docker` (if on PATH) |
| `DS01_JOBS_API_HOST` | Host address for the API server | `127.0.0.1` | `127.0.0.1` |
| `DS01_JOBS_API_PORT` | Port for the API server | `8765` | `8765` |
| `DS01_JOBS_BUILD_TIMEOUT_SECONDS` | Docker build timeout | `900` (15 min) | `300` (5 min, faster feedback) |
| `DS01_JOBS_DEFAULT_JOB_TIMEOUT_SECONDS` | Default job run timeout | `14400` (4 hours) | `600` (10 min) |

### Minimal `.env` File for Local Development

Create a `.env` file in the project root:

```bash
DS01_JOBS_DB_PATH=/tmp/ds01-dev.db
DS01_JOBS_WORKSPACE_ROOT=/tmp/ds01-workspaces
```

The `.env` file is already in `.gitignore`, so it will not be committed.

## Running Tests

### Unit Tests

Unit tests do not require a GPU, Docker, or a running API server:

```bash
# Run all unit tests
uv run pytest -m 'not integration' --tb=short

# Verbose output
uv run pytest -m 'not integration' -v

# Single test file
uv run pytest tests/unit/test_auth.py -v

# Single test function
uv run pytest tests/unit/test_auth.py::test_valid_request -v

# With coverage report
uv run pytest -m 'not integration' --cov=ds01_jobs --cov-report=term-missing
```

### Integration Tests

Integration tests require the full stack (API server, runner, GPU, Docker) and run on the self-hosted GPU runner in CI. You generally do not need to run these locally:

```bash
uv run pytest -m integration --tb=short
```

### Test Organisation

Each source module has a corresponding test file:

| Source | Tests |
|---|---|
| `auth.py` | `tests/unit/test_auth.py` |
| `jobs.py` | `tests/unit/test_jobs.py` |
| `scanner.py` | `tests/unit/test_scanner.py` |
| `executor.py` | `tests/unit/test_executor.py` |
| ... | ... |

## Code Quality Tools

### Formatting

Ruff is the formatter, configured to a 100-character line length:

```bash
uv run ruff format .
```

### Linting

Ruff also handles linting. The `--fix` flag auto-corrects what it can:

```bash
uv run ruff check --fix .
```

Enabled rule sets: `E` (pycodestyle errors), `F` (pyflakes), `I` (isort), `W` (pycodestyle warnings), `UP` (pyupgrade).

### Type Checking

mypy runs in strict mode with the Pydantic plugin:

```bash
uv run mypy src/ds01_jobs/
```

### Pre-commit Hooks

Git hooks run automatically on every `git commit`:

- **Identity check** - verifies your Git author identity
- **Secrets scan** - blocks commits containing API keys, passwords, etc.
- **Branch protection** - blocks direct commits to `main`
- **Ruff format + lint** - auto-formats and lints staged files

If a hook fails, fix the issue and try committing again. The hooks only run on staged files, so they are fast.

### Full Quality Check

Run everything in sequence before opening a PR:

```bash
uv run ruff format . && uv run ruff check --fix . && uv run mypy src/ds01_jobs/ && uv run pytest -m 'not integration' --tb=short
```

## Git Workflow

### Branching

All work happens on feature branches. Branch naming convention:

| Prefix | Use for |
|---|---|
| `feature/*` | New features |
| `fix/*` | Bug fixes |
| `refactor/*` | Code restructuring |
| `docs/*` | Documentation changes |

### Development Flow

1. **Create a branch from an up-to-date main:**
   ```bash
   git checkout main && git pull
   git checkout -b feature/my-feature
   ```

2. **Commit freely on your branch.** WIP commits, experiments, and notes are all fine on feature branches. There is no need to follow strict commit message conventions here.

3. **Open a PR when ready:**
   - Title: conventional commit format (e.g. `feat: add job priority support`)
   - Description: explain what changed, why, and how you tested it
   - CI must pass (lint, format, types, unit tests)

4. **Squash merge to main** after approval. The squash commit message should follow conventional commit format:
   ```
   type(scope): description
   ```
   Types: `feat`, `fix`, `docs`, `refactor`, `test`. Subject line under 72 characters, imperative mood.

5. **Delete the branch** after merging.

### Branch Protection

Main branch has these protections:

- PR required (no direct pushes)
- CI status checks must pass
- Review approval required before merge

## CI Pipeline

### Tier 1 - PR Checks (every PR)

Runs on `ubuntu-latest`:

1. Install dependencies (`uv sync --locked --all-groups`)
2. Lint (`ruff check`)
3. Format check (`ruff format --check`)
4. Type check (`mypy src/ds01_jobs/`)
5. Unit tests (`pytest -m 'not integration'`)

All four steps must pass for the PR to be mergeable.

### Tier 2 - Integration Tests (push to main)

Runs on the self-hosted GPU runner:

1. Install dependencies
2. Unit tests (sanity check on the real hardware)
3. Restart services (API + runner) with updated code
4. Integration tests (end-to-end job submission and execution)

## Adding a New Endpoint

Here is a step-by-step example of adding a new API endpoint.

### 1. Define the Response Model

In `models.py`, add a Pydantic model for the response:

```python
class MyResponse(BaseModel):
    """Response schema for GET /api/v1/my-endpoint."""

    field: str
    count: int
```

### 2. Add the Endpoint

In `jobs.py` (or a new router file if the feature is unrelated to jobs), add the route:

```python
@router.get("/my-endpoint", response_model=MyResponse)
async def my_endpoint(
    user: dict[str, str] = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> MyResponse:
    """Short description of what this endpoint does."""
    # Your logic here
    return MyResponse(field="value", count=42)
```

Key points:

- Use `Depends(get_current_user)` to require authentication. This injects a dict with `username` and `unix_username` keys.
- Use `Depends(get_db)` for an async SQLite connection.
- Return Pydantic model instances, not raw dicts.
- For validation errors, return a `JSONResponse` with the Stripe-like error structure (see existing endpoints in `jobs.py` for examples).
- For auth/not-found errors, raise `HTTPException`.

### 3. Write Tests

Create `tests/unit/test_my_feature.py`:

```python
import pytest
from fastapi.testclient import TestClient


def test_my_endpoint(client: TestClient, auth_headers: dict[str, str]):
    resp = client.get("/api/v1/my-endpoint", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["field"] == "value"
    assert data["count"] == 42
```

Check `tests/conftest.py` for available fixtures (test client, auth headers, database setup, etc.).

### 4. Run the Full Check

```bash
uv run ruff format . && uv run ruff check --fix . && uv run mypy src/ds01_jobs/ && uv run pytest -m 'not integration' --tb=short
```

## Adding a New CLI Command

### Researcher CLI (`submit.py`)

Add a new command using Typer:

```python
@app.command()
def my_command(
    arg: Annotated[str, typer.Argument(help="Description of the argument")],
    json_output: JsonOption = False,
) -> None:
    """Short description of what this command does."""
    client = _get_client()
    try:
        resp = client.get("/api/v1/my-endpoint")
        if resp.status_code != 200:
            _handle_error(resp)

        data = resp.json()
        if json_output:
            typer.echo(json.dumps(data, indent=2))
        else:
            typer.echo(f"Result: {data['field']}")
    finally:
        client.close()
```

Key patterns:

- Always use `_get_client()` to create a signing HTTP client, and close it in a `finally` block.
- Support `--json` output for machine-readable results using the `JsonOption` type alias.
- Use `_handle_error(resp)` for non-success status codes - it prints structured error messages and exits.

### Admin CLI (`cli.py`)

The admin CLI follows the same Typer pattern but uses `get_db_sync()` for direct SQLite access rather than the HTTP client.

## Debugging Tips

### Auto-reload for Fast Iteration

Use the `--reload` flag with uvicorn during development. The server automatically restarts when you save changes to any Python file:

```bash
uv run uvicorn ds01_jobs.app:app --host 127.0.0.1 --port 8765 --reload
```

### Throwaway Database

Point at a temporary database so you can experiment freely without affecting any real data:

```bash
DS01_JOBS_DB_PATH=/tmp/test.db uv run uvicorn ds01_jobs.app:app --host 127.0.0.1 --port 8765
```

The database is created automatically on startup (via `init_db`). Delete it and restart to get a clean slate.

### Interactive API Testing

FastAPI's built-in `/docs` endpoint provides a Swagger UI where you can try out endpoints interactively. Navigate to `http://127.0.0.1:8765/docs` in your browser. Note that authenticated endpoints require HMAC signing headers, so `/docs` is most useful for unauthenticated endpoints (like `/health`) or for understanding request/response schemas.

### Inspecting the Database Directly

Use the SQLite CLI to query the database:

```bash
sqlite3 /tmp/test.db
```

Useful queries:

```sql
-- View all jobs
SELECT id, status, job_name, created_at FROM jobs ORDER BY created_at DESC;

-- View active API keys
SELECT username, key_id, expires_at, revoked FROM api_keys WHERE revoked = 0;

-- Check job phase timestamps
SELECT id, status, phase_timestamps FROM jobs WHERE id = '<job_id>';
```

### Verbose Logging

Set `LOG_LEVEL=DEBUG` for detailed log output from the API server and runner:

```bash
LOG_LEVEL=DEBUG uv run uvicorn ds01_jobs.app:app --host 127.0.0.1 --port 8765
```

### Debugging Tests

Use pytest flags to get more information when tests fail:

- **`-s`** - shows print statements and log output during test execution (pytest captures stdout by default)
- **`--pdb`** - drops into the Python debugger on the first test failure, so you can inspect variables interactively
- **`-v`** - verbose mode, shows each test name and result
- **`-x`** - stop on first failure (useful when debugging one specific issue)

```bash
# Show all output and drop into debugger on failure
uv run pytest tests/unit/test_auth.py -s --pdb -v
```

## Performance Considerations

Understanding these design choices helps when working on performance-sensitive parts of the codebase.

### SQLite WAL Mode

The database is initialised with `PRAGMA journal_mode=WAL` in `init_db()`. Write-Ahead Logging allows the API server to read the database concurrently while the runner writes status updates. Without WAL, readers would be blocked during writes, causing API latency spikes.

### bcrypt in Thread Pool

Password hashing with bcrypt is CPU-bound and deliberately slow (that is the point of bcrypt). The auth module offloads `bcrypt.checkpw()` via `asyncio.to_thread()` so it does not block the event loop. Without this, a single authentication request would block all other requests for the duration of the hash check.

### aiosqlite for Async Database Access

All API endpoint database access uses `aiosqlite`, which wraps SQLite calls in a background thread. This prevents database I/O from blocking the async event loop, allowing the server to handle other requests concurrently.

### TLS Termination at the Proxy

TLS handshakes are CPU-intensive. The nginx reverse proxy handles TLS termination, so uvicorn only deals with plain HTTP on the loopback interface. This keeps the Python process focused on application logic.

### Docker Build Cache Pruning

After each job completes, the executor runs `docker builder prune --force --filter until=1h` to remove build cache entries older than one hour. This prevents Docker's disk usage from growing without bound on a shared machine. The `--filter until=1h` keeps recent cache entries so that back-to-back builds of similar images remain fast.

## Common Gotchas

These are issues that have caught contributors in the past.

### Forgetting to `await` an Async Call

If you call an async function without `await`, Python returns a coroutine object instead of the actual result. This often manifests as a test passing (because a coroutine object is truthy) but the actual database operation never happening:

```python
# Wrong - returns a coroutine object, does nothing
db.execute("UPDATE jobs SET status=? WHERE id=?", ("failed", job_id))

# Correct
await db.execute("UPDATE jobs SET status=? WHERE id=?", ("failed", job_id))
```

### Modifying the Database Schema

If you add a column to a table, you must also add an `ALTER TABLE` migration to the `_MIGRATIONS` list in `database.py`. The `CREATE TABLE IF NOT EXISTS` statement only runs on first creation - existing databases will not pick up new columns from the schema definition alone.

### Not Running `uv sync` After Pulling

If someone has added a new dependency and you pull their changes, your local environment will be missing the package. Always run `uv sync --locked --all-groups` after pulling changes that modify `pyproject.toml` or `uv.lock`.

### Pre-commit Hook Failures

If a commit is rejected by the pre-commit hooks, run the formatters and linters manually to fix the issues:

```bash
uv run ruff format . && uv run ruff check --fix .
```

Then stage the fixed files and commit again. The hooks only check staged files, so they are fast.

## Common Patterns

### Authentication Dependency

```python
user: dict[str, str] = Depends(get_current_user)
```

Returns `{"username": "github_user", "unix_username": "unix_user"}` on success. Raises HTTP 401 on any authentication failure (invalid key, expired, bad signature, replay attack, etc.). All 401 responses are intentionally generic to avoid leaking information.

### Database Access

**Async (API endpoints):**
```python
db: aiosqlite.Connection = Depends(get_db)
```

**Sync (CLI tools):**
```python
with get_db_sync() as conn:
    cursor = conn.execute("SELECT ...")
```

Both use `row_factory` so rows are accessible by column name (e.g. `row["username"]`).

### Error Responses

The API uses a Stripe-like error structure for validation errors:

```json
{
  "error": {
    "type": "validation_error",
    "message": "Request validation failed",
    "errors": [
      {
        "field": "repo_url",
        "code": "invalid_url",
        "message": "Must be a valid GitHub repository URL"
      }
    ]
  }
}
```

For simple errors (auth, not found), use `HTTPException`. For structured validation errors, return `JSONResponse` directly with the error body.

### Async Subprocesses

The executor uses `asyncio.create_subprocess_exec` with `process_group=0` for process isolation. This ensures that if a process needs to be killed (timeout or cancellation), the entire process group is terminated:

```python
proc = await asyncio.create_subprocess_exec(
    *cmd,
    stdout=log_file,
    stderr=asyncio.subprocess.STDOUT,
    process_group=0,
)
```

### Settings

All configuration is centralised in `config.py` via Pydantic Settings:

```python
settings = Settings()  # reads DS01_JOBS_* env vars + .env file
```

Environment variables use the `DS01_JOBS_` prefix. For example, `DS01_JOBS_DB_PATH` maps to `settings.db_path`.

## Style Guide

- **Type hints required** on all public function signatures
- **Python 3.10+ syntax** - use `list[str]` not `List[str]`, `X | None` not `Optional[X]`
- **pathlib.Path** over `os.path` for file system operations
- **f-strings** for string formatting
- **Google-style docstrings** for complex functions; brief one-liners for simple ones
- **Ruff enforces formatting** at 100-character line length - do not manually review formatting
- **mypy strict mode** - all type errors must be resolved

## Try It Yourself

1. **Set up the dev environment.** Clone the repo, run `uv sync --locked --all-groups`, and verify that the unit test suite passes:
   ```bash
   uv run pytest -m 'not integration' --tb=short
   ```

2. **Trace the application wiring.** Read `app.py`'s `create_app()` function and follow how it wires together the lifespan (database init), rate limiter, validation error handler, and routers. Then look at how `jobs.py` uses `Depends(get_current_user)` and `Depends(get_db)`.

3. **Add a toy endpoint.** Create a new endpoint at `GET /api/v1/hello` that returns `{"hello": "world"}` with authentication required. Write a unit test for it. Run the full quality pipeline to make sure everything passes.

4. **Run the full quality check.** Execute each step and fix any issues:
   ```bash
   uv run ruff format .
   uv run ruff check --fix .
   uv run mypy src/ds01_jobs/
   uv run pytest -m 'not integration' --tb=short
   ```

5. **Practice the Git workflow.** Create a feature branch, make a small change (e.g. improve a docstring or add a test), commit it, and open a draft PR against `main`. Check that CI passes on your PR.

6. **Explore the API via /docs.** Start the API server locally with a throwaway database and open `http://127.0.0.1:8765/docs` in your browser. Find the job submission endpoint and study its request schema. To actually submit a job, you would need to construct HMAC signing headers - try writing a small Python script that uses `client.py`'s signing logic to authenticate a request against your local server.

7. **Read the test fixtures and explain the test infrastructure.** Open `tests/integration/conftest.py` and trace how it sets up an isolated test environment. Note how it creates a temporary SQLite database (`db_path` fixture), generates test API keys with `create_test_key()` and seeds them with `seed_key()`, overrides FastAPI dependencies so the app uses the test database, builds HMAC-signed headers with `build_signed_headers()`, and clears the nonce cache between tests. Understanding this infrastructure is essential for writing new tests - write a brief summary of how a test request flows from the `client` fixture through authentication to a database query and back.
