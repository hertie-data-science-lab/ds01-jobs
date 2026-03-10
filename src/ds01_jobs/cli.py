"""Admin CLI for ds01-jobs API key management.

Provides key-create, key-list, key-revoke, and key-rotate commands
for managing researcher API keys.
"""

import base64
import json
import os
import re
import secrets
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
import httpx
import typer

from ds01_jobs.config import Settings
from ds01_jobs.database import SCHEMA_SQL, get_db_sync

app = typer.Typer(
    name="ds01-job-admin",
    help="DS01 Job Submission Service - Admin CLI",
)


def _hash_key(raw_key: str) -> str:
    """Hash an API key with bcrypt (rounds=12)."""
    return bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12)).decode()


def _print_key_result(
    username: str,
    raw_key: str,
    key_id: str,
    expires_date: str,
    action: str,
    json_output: bool,
) -> None:
    """Display a key creation/rotation result."""
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "username": username,
                    "key": raw_key,
                    "key_id": key_id,
                    "expires_at": expires_date,
                },
                indent=2,
            )
        )
    else:
        typer.echo(f"API Key {action} successfully")
        typer.echo("")
        typer.echo(f"Key:     {raw_key}")
        typer.echo("")
        typer.echo(f"User:    {username}")
        typer.echo(f"Expires: {expires_date}")
        typer.echo("")
        typer.echo("Setup instructions (send to researcher):")
        typer.echo("\u2500" * 41)
        typer.echo("pip install ds01-jobs")
        typer.echo(f"DS01_API_KEY={raw_key} ds01-submit configure")
        typer.echo("\u2500" * 41)


def generate_api_key() -> tuple[str, str]:
    """Generate an API key with ds01_ prefix.

    Returns:
        Tuple of (raw_key, key_id) where key_id is first 8 chars of the
        base64url portion.
    """
    raw_bytes = secrets.token_bytes(32)
    encoded = base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode()
    raw_key = f"ds01_{encoded}"
    key_id = encoded[:8]
    return raw_key, key_id


def parse_duration(duration: str) -> int:
    """Parse a duration string like '90d' into days.

    Args:
        duration: Duration string in Nd format (e.g. '90d', '30d', '180d').

    Returns:
        Number of days.

    Raises:
        typer.BadParameter: If the format is invalid.
    """
    match = re.match(r"^(\d+)d$", duration)
    if not match:
        raise typer.BadParameter(f"Invalid duration format: {duration!r}. Use Nd format (e.g. 90d)")
    return int(match.group(1))


def _resolve_github_token() -> str | None:
    """Resolve a GitHub token from GITHUB_TOKEN env var or gh CLI."""
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token

    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


def check_org_membership(username: str, org: str) -> bool:
    """Check GitHub organisation membership.

    Resolves a GitHub token from (in order): GITHUB_TOKEN env var, gh CLI.
    With a token, uses the authenticated members endpoint (sees private
    memberships). Without a token, falls back to the public members endpoint.

    Args:
        username: GitHub username to check.
        org: GitHub organisation name.

    Returns:
        True if user is a member, False otherwise.
    """
    token = _resolve_github_token()
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}

    if token:
        url = f"https://api.github.com/orgs/{org}/members/{username}"
        headers["Authorization"] = f"Bearer {token}"
    else:
        url = f"https://api.github.com/orgs/{org}/public_members/{username}"

    try:
        response = httpx.get(url, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        typer.echo(f"Error checking GitHub org membership: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if response.status_code == 204:
        return True
    if response.status_code == 404:
        return False

    typer.echo(
        f"Unexpected response from GitHub API: {response.status_code}",
        err=True,
    )
    raise typer.Exit(code=1)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure the api_keys table exists."""
    conn.executescript(SCHEMA_SQL)


def _get_active_key(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    """Look up an active (non-revoked, non-expired) key for a user."""
    now = datetime.now(UTC).isoformat()
    cursor = conn.execute(
        "SELECT * FROM api_keys WHERE username = ? AND revoked = 0 AND expires_at > ?",
        (username, now),
    )
    return cursor.fetchone()  # type: ignore[no-any-return]


@app.command("key-create")
def key_create(
    username: Annotated[str, typer.Argument(help="GitHub username (must be a member of the org)")],
    expires: Annotated[
        str, typer.Option(help="Key validity duration (e.g. 90d, 30d, 180d)")
    ] = "90d",
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
) -> None:
    """Create a new API key for a researcher.

    USERNAME must be the researcher's GitHub username. Org membership is
    verified via the GitHub API (requires gh CLI or GITHUB_TOKEN).
    """
    settings = Settings(_env_file=None)

    # Check GitHub org membership
    if not check_org_membership(username, settings.github_org):
        typer.echo(f"Error: {username} is not a member of {settings.github_org}", err=True)
        raise typer.Exit(code=1)

    days = parse_duration(expires)

    with get_db_sync() as conn:
        _ensure_schema(conn)

        # Check for existing active key
        if _get_active_key(conn, username):
            typer.echo(
                f"Error: User {username} already has an active key. "
                "Use key-revoke or key-rotate first.",
                err=True,
            )
            raise typer.Exit(code=1)

        # Generate and hash key
        raw_key, key_id = generate_api_key()
        key_hash = _hash_key(raw_key)

        now = datetime.now(UTC)
        expires_dt = now + timedelta(days=days)

        conn.execute(
            "INSERT INTO api_keys (username, key_id, key_hash, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, key_id, key_hash, now.isoformat(), expires_dt.isoformat()),
        )
        conn.commit()

    _print_key_result(
        username, raw_key, key_id, expires_dt.strftime("%Y-%m-%d"), "created", json_output
    )


@app.command("key-list")
def key_list(
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
) -> None:
    """List all API keys."""
    with get_db_sync() as conn:
        _ensure_schema(conn)
        cursor = conn.execute(
            "SELECT username, key_id, created_at, expires_at, revoked, last_used_at "
            "FROM api_keys ORDER BY created_at DESC"
        )
        rows = cursor.fetchall()

    now = datetime.now(UTC)
    keys = []
    for row in rows:
        if row["revoked"]:
            status = "revoked"
        elif datetime.fromisoformat(row["expires_at"]) <= now:
            status = "expired"
        else:
            status = "active"

        keys.append(
            {
                "username": row["username"],
                "status": status,
                "created": row["created_at"][:10],
                "expires": row["expires_at"][:10],
                "last_used": row["last_used_at"][:10] if row["last_used_at"] else "never",
            }
        )

    if json_output:
        typer.echo(json.dumps(keys, indent=2))
    else:
        if not keys:
            typer.echo("No API keys found.")
            return

        # Aligned columnar output
        headers = ["USERNAME", "STATUS", "CREATED", "EXPIRES", "LAST USED"]
        col_widths = [
            max(len(headers[0]), *(len(k["username"]) for k in keys)),
            max(len(headers[1]), *(len(k["status"]) for k in keys)),
            max(len(headers[2]), *(len(k["created"]) for k in keys)),
            max(len(headers[3]), *(len(k["expires"]) for k in keys)),
            max(len(headers[4]), *(len(k["last_used"]) for k in keys)),
        ]

        header_line = "  ".join(h.ljust(w) for h, w in zip(headers, col_widths, strict=True))
        typer.echo(header_line)

        for key in keys:
            vals = [
                key["username"],
                key["status"],
                key["created"],
                key["expires"],
                key["last_used"],
            ]
            line = "  ".join(v.ljust(w) for v, w in zip(vals, col_widths, strict=True))
            typer.echo(line)


@app.command("key-revoke")
def key_revoke(
    username: str,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
) -> None:
    """Revoke an API key for a user."""
    with get_db_sync() as conn:
        _ensure_schema(conn)

        cursor = conn.execute(
            "SELECT * FROM api_keys WHERE username = ? AND revoked = 0",
            (username,),
        )
        row = cursor.fetchone()

        if not row:
            typer.echo(f"Error: No active key found for {username}", err=True)
            raise typer.Exit(code=1)

        if not yes:
            typer.confirm(f"Revoke key for {username}?", abort=True)

        conn.execute(
            "UPDATE api_keys SET revoked = 1 WHERE id = ?",
            (row["id"],),
        )
        conn.commit()

    if json_output:
        typer.echo(json.dumps({"username": username, "status": "revoked"}, indent=2))
    else:
        typer.echo(f"Key revoked for {username}")


@app.command("key-rotate")
def key_rotate(
    username: str,
    expires: Annotated[
        str, typer.Option(help="Key validity duration (e.g. 90d, 30d, 180d)")
    ] = "90d",
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
) -> None:
    """Rotate an API key (revoke old, create new)."""
    days = parse_duration(expires)

    with get_db_sync() as conn:
        _ensure_schema(conn)

        # Check for existing active key
        active = _get_active_key(conn, username)
        if not active:
            typer.echo(f"Error: No active key found for {username}", err=True)
            raise typer.Exit(code=1)

        if not yes:
            typer.confirm(f"Rotate key for {username}?", abort=True)

        # Generate new key
        raw_key, key_id = generate_api_key()
        key_hash = _hash_key(raw_key)

        now = datetime.now(UTC)
        expires_dt = now + timedelta(days=days)

        # Atomic rotation: update the existing row with new key data
        conn.execute(
            "UPDATE api_keys SET key_id = ?, key_hash = ?, created_at = ?, "
            "expires_at = ?, revoked = 0, last_used_at = NULL WHERE username = ?",
            (key_id, key_hash, now.isoformat(), expires_dt.isoformat(), username),
        )
        conn.commit()

    _print_key_result(
        username, raw_key, key_id, expires_dt.strftime("%Y-%m-%d"), "rotated", json_output
    )
