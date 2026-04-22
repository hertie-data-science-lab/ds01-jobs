#!/usr/bin/env bash
# Start the ds01-jobs API server and job runner for local development/testing.
# Logs to /var/log/ds01-jobs/ and stdout.
#
# Usage:
#   ./scripts/dev-server.sh          # start both services
#   ./scripts/dev-server.sh stop     # stop both services
#   ./scripts/dev-server.sh status   # check if running

set -euo pipefail

PROJECT_DIR="/opt/ds01-jobs"
# Local dev uses in-tree .venv; production uses /var/lib/ds01-jobs/venv (owned by ds01).
VENV="${PROJECT_DIR}/.venv"
LOG_DIR="/var/log/ds01-jobs"
API_PID_FILE="${LOG_DIR}/api.pid"
RUNNER_PID_FILE="${LOG_DIR}/runner.pid"

# Ensure dirs exist (log dir needs manual creation with correct ownership)
if [[ ! -d "$LOG_DIR" ]]; then
    echo "Error: ${LOG_DIR} does not exist. Create it with:"
    echo "  sudo mkdir -p ${LOG_DIR} && sudo chown \$USER:\$(id -gn) ${LOG_DIR}"
    exit 1
fi

if [[ ! -d "/var/lib/ds01-jobs" ]]; then
    echo "Error: /var/lib/ds01-jobs does not exist. Create it with:"
    echo "  sudo mkdir -p /var/lib/ds01-jobs && sudo chown \$USER:\$(id -gn) /var/lib/ds01-jobs"
    exit 1
fi

stop_services() {
    local stopped=0
    for pidfile in "$API_PID_FILE" "$RUNNER_PID_FILE"; do
        if [[ -f "$pidfile" ]]; then
            pid=$(cat "$pidfile")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid"
                echo "Stopped PID $pid ($(basename "$pidfile" .pid))"
                stopped=1
            fi
            rm -f "$pidfile"
        fi
    done
    [[ $stopped -eq 0 ]] && echo "No services running."
}

check_status() {
    for pidfile in "$API_PID_FILE" "$RUNNER_PID_FILE"; do
        name=$(basename "$pidfile" .pid)
        if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
            echo "${name}: running (PID $(cat "$pidfile"))"
        else
            echo "${name}: not running"
            rm -f "$pidfile"
        fi
    done

    # Quick health check
    if /usr/bin/curl -sf http://127.0.0.1:8765/health >/dev/null 2>&1; then
        echo "health: ok"
    else
        echo "health: not responding"
    fi
}

start_services() {
    # Stop any existing instances first
    stop_services 2>/dev/null

    # Sync venv to pick up code changes
    echo "Syncing venv..."
    cd "$PROJECT_DIR"
    uv sync --quiet

    # Start API server (bump daily limit for testing)
    echo "Starting API server..."
    DS01_JOBS_DEFAULT_DAILY_LIMIT="${DS01_JOBS_DEFAULT_DAILY_LIMIT:-100}" \
    "${VENV}/bin/uvicorn" ds01_jobs.app:app \
        --host 127.0.0.1 --port 8765 \
        >> "${LOG_DIR}/api.log" 2>&1 &
    echo $! > "$API_PID_FILE"
    echo "API server started (PID $!, log: ${LOG_DIR}/api.log)"

    # Start job runner
    echo "Starting job runner..."
    "${VENV}/bin/ds01-job-runner" \
        >> "${LOG_DIR}/runner.log" 2>&1 &
    echo $! > "$RUNNER_PID_FILE"
    echo "Job runner started (PID $!, log: ${LOG_DIR}/runner.log)"

    # Wait for health check
    sleep 2
    if /usr/bin/curl -sf http://127.0.0.1:8765/health >/dev/null 2>&1; then
        echo "Health check: ok"
    else
        echo "Warning: health check failed - check ${LOG_DIR}/api.log"
    fi
}

case "${1:-start}" in
    start)  start_services ;;
    stop)   stop_services ;;
    status) check_status ;;
    *)
        echo "Usage: $0 {start|stop|status}"
        exit 1
        ;;
esac
