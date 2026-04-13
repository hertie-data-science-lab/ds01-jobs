"""Nightly revalidation of GitHub access for active API keys.

For each active key, queries GitHub for the user's current org/team
membership and revokes the key if they've lost access. Runs daily via a
systemd timer (``ds01-revalidate.timer``).

Revocations are written to ``/var/log/ds01/events.jsonl`` alongside the
API's existing event log.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer

from ds01_jobs.cli import verify_github_access
from ds01_jobs.config import Settings
from ds01_jobs.database import _MIGRATIONS, SCHEMA_SQL, get_db_sync

app = typer.Typer(
    name="ds01-job-revalidate",
    help="Revalidate active API keys against GitHub org/team membership.",
)

DEFAULT_EVENTS_LOG = Path("/var/log/ds01/events.jsonl")


def _log_revocation(username: str, reason: str, events_log: Path) -> None:
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "key_revoked",
        "username": username,
        "reason": reason,
        "source": "revalidate",
    }
    events_log.parent.mkdir(parents=True, exist_ok=True)
    with events_log.open("a") as fh:
        fh.write(json.dumps(event) + "\n")


def _active_usernames(conn: sqlite3.Connection) -> list[str]:
    """Return distinct usernames with at least one active key."""
    now = datetime.now(UTC).isoformat()
    cursor = conn.execute(
        "SELECT DISTINCT username FROM api_keys "
        "WHERE revoked = 0 AND expires_at > ? ORDER BY username",
        (now,),
    )
    return [row["username"] for row in cursor.fetchall()]


def _safe_check_access(username: str, settings: Settings) -> bool | None:
    """Return True/False if GitHub answered, None if the call failed."""
    try:
        return verify_github_access(username, settings)
    except (typer.Exit, httpx.HTTPError):
        return None


@app.command()
def main(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report actions without modifying the database."),
    ] = False,
    events_log: Annotated[
        Path,
        typer.Option("--events-log", help="Path to the JSONL event log."),
    ] = DEFAULT_EVENTS_LOG,
) -> None:
    """Revalidate every active key; revoke keys for users who lost access."""
    settings = Settings(_env_file=None)

    checked = 0
    revoked = 0
    skipped = 0

    with get_db_sync() as conn:
        conn.executescript(SCHEMA_SQL)
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass
        conn.commit()

        for username in _active_usernames(conn):
            checked += 1
            has_access = _safe_check_access(username, settings)

            if has_access is None:
                typer.echo(f"skip {username}: github check failed")
                skipped += 1
                continue
            if has_access:
                continue

            if dry_run:
                typer.echo(f"[dry-run] would revoke {username}")
            else:
                conn.execute(
                    "UPDATE api_keys SET revoked = 1 WHERE username = ? AND revoked = 0",
                    (username,),
                )
                _log_revocation(username, "github_access_lost", events_log)
                typer.echo(f"revoked {username}")
            revoked += 1

        conn.commit()

    typer.echo(
        f"revalidate: checked={checked} revoked={revoked} skipped={skipped} dry_run={dry_run}"
    )


if __name__ == "__main__":
    app()
