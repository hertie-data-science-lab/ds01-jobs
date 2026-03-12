#!/usr/bin/env bash
# deploy.sh - ds01-jobs deployment script
#
# Single entry point for installing and upgrading ds01-jobs on the production
# server. Run as root: sudo ./deploy.sh
#
# Idempotent - safe to run repeatedly. Handles both first-time setup and
# upgrades. Requires a clean git working tree.
#
# Usage:
#   sudo ./deploy.sh              # Interactive deployment
#   sudo ./deploy.sh --yes        # Skip confirmations (for CI)
#   sudo ./deploy.sh --dry-run    # Preview only, no changes
set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/ds01-jobs"
ENV_FILE="/etc/ds01-jobs/env"
DB_PATH="$INSTALL_DIR/data/jobs.db"
LOG_FILE="/tmp/ds01-jobs-deploy-$(date '+%Y%m%d-%H%M%S').log"
SERVICES=(ds01-api ds01-runner ds01-cloudflared)
UV_BIN=""

# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------
YES=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --yes)    YES=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help)
            echo "Usage: sudo ./deploy.sh [--yes] [--dry-run]"
            echo ""
            echo "Options:"
            echo "  --yes       Skip all confirmation prompts"
            echo "  --dry-run   Preview steps without making changes"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: sudo ./deploy.sh [--yes] [--dry-run]"
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

fail() {
    log "FAIL: $*"
    exit 1
}

confirm() {
    $YES && return 0
    local prompt="$1"
    read -rp "$prompt [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]]
}

step() {
    local name="$1"; shift
    log "STEP: $name"
    if $DRY_RUN; then
        log "  (dry-run: skipped)"
        return 0
    fi
    if "$@"; then
        log "  PASS: $name"
    else
        log "  FAIL: $name"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Pre-deploy checks (always run, even in dry-run)
# ---------------------------------------------------------------------------
check_root() {
    [[ $EUID -eq 0 ]] || fail "Must run as root (sudo ./deploy.sh)"
}

check_git_clean() {
    git -C "$SCRIPT_DIR" diff --quiet && git -C "$SCRIPT_DIR" diff --cached --quiet \
        || fail "Uncommitted changes detected. Commit or stash before deploying."
}

check_uv() {
    # Check PATH first
    if command -v uv &>/dev/null; then
        UV_BIN="$(command -v uv)"
        log "Found uv at $UV_BIN"
        return 0
    fi

    # Check common locations
    local candidates=(
        /usr/local/bin/uv
        /usr/bin/uv
    )

    # Check user-local locations
    for user_home in /home/datasciencelab ~datasciencelab; do
        if [[ -d "$user_home" ]]; then
            candidates+=(
                "$user_home/.local/bin/uv"
                "$user_home/.cargo/bin/uv"
                "$user_home/anaconda3/bin/uv"
            )
        fi
    done

    for candidate in "${candidates[@]}"; do
        if [[ -x "$candidate" ]]; then
            UV_BIN="$candidate"
            log "Found uv at $UV_BIN"
            return 0
        fi
    done

    fail "uv not found. Checked PATH and common locations."
}

check_active_jobs() {
    if [[ -f "$DB_PATH" ]]; then
        local count
        count=$(sqlite3 "$DB_PATH" \
            "SELECT COUNT(*) FROM jobs WHERE status IN ('cloning','building','running')" \
            2>/dev/null || echo "0")
        if [[ "$count" -gt 0 ]]; then
            log "WARNING: $count active job(s) running:"
            sqlite3 -column -header "$DB_PATH" \
                "SELECT id, status, repo_url, created_at FROM jobs WHERE status IN ('cloning','building','running')" \
                2>/dev/null || true
            confirm "Continue deployment? Active jobs will be interrupted." || {
                log "Deployment cancelled by user."
                exit 0
            }
        fi
    fi
}

# ---------------------------------------------------------------------------
# Deployment steps
# ---------------------------------------------------------------------------
setup_system_user() {
    if id ds01 &>/dev/null; then
        log "  User ds01 already exists"
    else
        useradd --system --shell /usr/sbin/nologin --home-dir /opt/ds01-jobs ds01
        log "  Created system user ds01"
    fi
    usermod -aG docker ds01 2>/dev/null || true
    log "  Ensured ds01 is in docker group"
}

setup_directories() {
    mkdir -p /etc/ds01-jobs
    chmod 0755 /etc/ds01-jobs

    mkdir -p /opt/ds01-jobs/data
    chown ds01:ds01 /opt/ds01-jobs/data

    mkdir -p /var/lib/ds01-jobs/workspaces
    chown ds01:ds01 /var/lib/ds01-jobs
    chown ds01:ds01 /var/lib/ds01-jobs/workspaces

    mkdir -p /var/log/ds01
    touch /var/log/ds01/events.jsonl
    chown ds01:ds01 /var/log/ds01/events.jsonl

    log "  Directories and permissions configured"
}

setup_env_file() {
    if [[ ! -f "$ENV_FILE" ]]; then
        cp "$SCRIPT_DIR/config/env.example" "$ENV_FILE"
        log "  Created $ENV_FILE from template"
    else
        log "  $ENV_FILE already exists (preserved)"
    fi

    # Check if TUNNEL_TOKEN is empty
    if grep -q '^TUNNEL_TOKEN=$' "$ENV_FILE" 2>/dev/null; then
        if $YES; then
            log "  WARNING: TUNNEL_TOKEN is empty in $ENV_FILE - set it manually"
        elif ! $DRY_RUN; then
            echo ""
            echo "Cloudflare Tunnel token is not set."
            echo "Get it from the Cloudflare Zero Trust dashboard -> Networks -> Tunnels."
            read -rsp "Enter tunnel token (input hidden): " token
            echo ""
            if [[ -n "$token" ]]; then
                sed -i "s|^TUNNEL_TOKEN=$|TUNNEL_TOKEN=$token|" "$ENV_FILE"
                log "  Tunnel token written to $ENV_FILE"
            else
                log "  WARNING: No token entered - TUNNEL_TOKEN remains empty"
            fi
        fi
    fi

    chmod 0600 "$ENV_FILE"
    chown root:root "$ENV_FILE"
    log "  $ENV_FILE permissions set to 0600 root:root"
}

install_cloudflared() {
    if command -v cloudflared &>/dev/null; then
        log "  cloudflared already installed ($(cloudflared --version 2>&1 | head -1))"
        return 0
    fi

    log "  Installing cloudflared from apt repository..."
    mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
        | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
        | tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
    apt-get update -qq
    apt-get install -y cloudflared
    log "  cloudflared installed ($(cloudflared --version 2>&1 | head -1))"
}

setup_python_env() {
    log "  Running uv sync --locked..."
    cd "$INSTALL_DIR"
    "$UV_BIN" sync --locked
    cd - >/dev/null

    # Verify entrypoints
    local entrypoints=(ds01-job-admin ds01-job-runner ds01-submit)
    for ep in "${entrypoints[@]}"; do
        if [[ ! -x "$INSTALL_DIR/.venv/bin/$ep" ]]; then
            fail "Entrypoint $ep not found in .venv/bin/"
        fi
    done
    log "  Python environment ready, all entrypoints verified"
}

install_sudoers() {
    cp "$SCRIPT_DIR/config/sudoers.d/ds01-jobs" /etc/sudoers.d/ds01-jobs
    chmod 0440 /etc/sudoers.d/ds01-jobs
    visudo -cf /etc/sudoers.d/ds01-jobs || fail "Sudoers file validation failed"
    log "  Sudoers drop-in installed and validated"
}

install_systemd_units() {
    cp "$SCRIPT_DIR/systemd/ds01-api.service" /etc/systemd/system/
    cp "$SCRIPT_DIR/systemd/ds01-runner.service" /etc/systemd/system/
    cp "$SCRIPT_DIR/systemd/ds01-cloudflared.service" /etc/systemd/system/
    log "  Systemd unit files copied"

    systemctl daemon-reload
    log "  systemctl daemon-reload complete"

    systemctl enable ds01-api ds01-runner ds01-cloudflared
    log "  Services enabled"

    systemctl restart ds01-api ds01-runner ds01-cloudflared
    log "  Services restarted"
}

verify_health() {
    log "Verifying deployment..."

    # Check all services are active
    local all_active=true
    for svc in "${SERVICES[@]}"; do
        if systemctl is-active --quiet "$svc"; then
            log "  $svc: active"
        else
            log "  $svc: INACTIVE"
            journalctl -u "$svc" --no-pager -n 10 2>/dev/null || true
            all_active=false
        fi
    done

    if ! $all_active; then
        fail "One or more services failed to start"
    fi

    # Wait for API health endpoint
    local health=""
    for i in $(seq 1 10); do
        health=$(curl -sf http://127.0.0.1:8765/health 2>/dev/null) && break
        sleep 1
    done

    if echo "$health" | grep -q '"status":"ok"'; then
        log "  /health: ok"
    else
        fail "API health check failed: ${health:-no response}"
    fi

    log "Deployment verified successfully"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    log "=== ds01-jobs deployment started ==="
    log "Script directory: $SCRIPT_DIR"
    log "Install directory: $INSTALL_DIR"
    log "Flags: --yes=$YES --dry-run=$DRY_RUN"

    # Pre-deploy checks (always run)
    log "--- Pre-deploy checks ---"
    check_root
    log "  Root check: PASS"
    check_git_clean
    log "  Git clean check: PASS"
    check_uv
    check_active_jobs

    if $DRY_RUN; then
        log ""
        log "--- Dry-run preview ---"
        log "The following steps would be executed:"
    fi

    # Deployment steps
    step "Create system user"       setup_system_user
    step "Setup directories"        setup_directories
    step "Setup environment file"   setup_env_file
    step "Install cloudflared"      install_cloudflared
    step "Setup Python environment" setup_python_env
    step "Install sudoers drop-in"  install_sudoers
    step "Install systemd units"    install_systemd_units

    if ! $DRY_RUN; then
        verify_health
    else
        log ""
        log "STEP: Verify deployment health"
        log "  (dry-run: skipped)"
    fi

    log ""
    log "=== Deployment complete ==="
    log "Deploy log: $LOG_FILE"
}

main
