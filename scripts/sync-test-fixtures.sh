#!/usr/bin/env bash
# Publish integration test fixtures to fixtures/<name> orphan branches on origin.
#
# Source-of-truth: tests/integration/fixtures/scenarios/<name>/ on main.
# Each scenario is pushed as an orphan branch with a deterministic single commit
# (fixed author + dates) so re-runs from the same tree produce identical SHAs.
#
# Usage:
#   scripts/sync-test-fixtures.sh                 # sync every scenario
#   scripts/sync-test-fixtures.sh --only smoke    # sync just one
#   scripts/sync-test-fixtures.sh --dry-run       # show what would happen
#
# Run from the repo root. Requires git push access to origin.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SCENARIOS_DIR="$REPO_ROOT/tests/integration/fixtures/scenarios"
BRANCH_PREFIX="fixtures/"
FIXED_DATE="2020-01-01T00:00:00Z"
AUTHOR_NAME="ds01-test-fixtures"
AUTHOR_EMAIL="noreply@hertie-data-science-lab.invalid"

dry_run=0
only=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) dry_run=1; shift ;;
        --only) only="${2:-}"; shift 2 ;;
        -h|--help) sed -n '1,15p' "$0"; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ ! -d "$SCENARIOS_DIR" ]]; then
    echo "Error: $SCENARIOS_DIR not found" >&2
    exit 1
fi

scenarios=()
if [[ -n "$only" ]]; then
    if [[ ! -d "$SCENARIOS_DIR/$only" ]]; then
        echo "Error: scenario '$only' not found in $SCENARIOS_DIR" >&2
        exit 1
    fi
    scenarios=("$only")
else
    for d in "$SCENARIOS_DIR"/*/; do
        scenarios+=("$(basename "$d")")
    done
fi

sync_one() {
    local name="$1"
    local src="$SCENARIOS_DIR/$name"
    local branch="${BRANCH_PREFIX}${name}"
    local tmp
    tmp=$(mktemp -d)

    git -C "$tmp" init -q
    git -C "$tmp" symbolic-ref HEAD "refs/heads/$branch"
    git -C "$tmp" config user.name "$AUTHOR_NAME"
    git -C "$tmp" config user.email "$AUTHOR_EMAIL"
    cp -r "$src"/. "$tmp"/
    git -C "$tmp" add .
    GIT_COMMITTER_DATE="$FIXED_DATE" \
    GIT_AUTHOR_DATE="$FIXED_DATE" \
        git -C "$tmp" commit -q --author="$AUTHOR_NAME <$AUTHOR_EMAIL>" \
        -m "fixture: $name"

    local sha
    sha=$(git -C "$tmp" rev-parse HEAD)

    if [[ "$dry_run" -eq 1 ]]; then
        echo "[dry-run] would push $branch ($sha)"
    else
        git -C "$tmp" remote add origin "$(git -C "$REPO_ROOT" remote get-url origin)"
        # Use --force-with-lease to avoid silent clobber of concurrent edits.
        # Empty value means "fail if remote ref exists and we don't expect it"
        # — fine for our case because rebuild from same tree produces same SHA.
        if git -C "$tmp" push --force-with-lease "origin" "$branch" 2>&1; then
            echo "synced $branch ($sha)"
        else
            echo "Error: push failed for $branch" >&2
            rm -rf "$tmp"
            return 1
        fi
    fi

    rm -rf "$tmp"
}

for s in "${scenarios[@]}"; do
    sync_one "$s"
done

if [[ "$dry_run" -eq 1 ]]; then
    echo ""
    echo "Dry-run complete. ${#scenarios[@]} branches would be pushed."
else
    echo ""
    echo "Synced ${#scenarios[@]} scenario(s)."
fi
