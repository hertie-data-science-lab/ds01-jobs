#!/usr/bin/env bash
# Run the ds01-jobs test suite against a live server.
# Submits jobs covering different scenarios and tracks results.
#
# Prerequisites:
#   - API server running (./scripts/dev-server.sh)
#   - API key configured (~/.config/ds01/credentials)
#   - DS01_API_URL set (defaults to http://127.0.0.1:8765)
#
# Usage:
#   ./scripts/run-test-suite.sh              # run all tests
#   ./scripts/run-test-suite.sh <test_num>   # run a specific test (1-9)

set -euo pipefail

API_URL="${DS01_API_URL:-http://127.0.0.1:8765}"
SUBMIT="ds01-submit"
ORG="hertie-data-science-lab"
RESULTS_DIR="/tmp/ds01-test-results"

mkdir -p "$RESULTS_DIR"

# Colours
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass=0
fail=0
skip=0

log() { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $*"; }
pass() { echo -e "  ${GREEN}PASS${NC}: $1"; ((pass++)); }
fail() { echo -e "  ${RED}FAIL${NC}: $1"; ((fail++)); }
skip() { echo -e "  ${YELLOW}SKIP${NC}: $1"; ((skip++)); }

# Check server is up
if ! /usr/bin/curl -sf "$API_URL/health" >/dev/null 2>&1; then
    echo "Error: Server not responding at $API_URL"
    echo "Start it with: ./scripts/dev-server.sh"
    exit 1
fi

run_test() {
    local num="$1"
    case "$num" in

    1)
        log "Test 1: CPU quick job (python:3.12-slim, ~30s)"
        local out
        out=$(DS01_API_URL="$API_URL" $SUBMIT run "https://github.com/$ORG/ds01-test-cpu-quick" --gpus 1 --json 2>&1) || { fail "submit failed: $out"; return; }
        local job_id=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
        log "  Job ID: $job_id — following..."
        DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --follow 2>&1 || true
        local status=$(DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --json 2>&1 | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
        if [[ "$status" == "succeeded" ]]; then
            pass "cpu-quick succeeded"
            DS01_API_URL="$API_URL" $SUBMIT results "$job_id" -o "$RESULTS_DIR/cpu-quick" 2>&1 || fail "results download"
        else
            fail "cpu-quick status=$status (expected succeeded)"
        fi
        ;;

    2)
        log "Test 2: Long-running job (~2min, 12 epochs)"
        local out
        out=$(DS01_API_URL="$API_URL" $SUBMIT run "https://github.com/$ORG/ds01-test-long-running" --gpus 1 --json 2>&1) || { fail "submit failed: $out"; return; }
        local job_id=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
        log "  Job ID: $job_id — following (expect ~2min)..."
        DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --follow 2>&1 || true
        local status=$(DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --json 2>&1 | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
        if [[ "$status" == "succeeded" ]]; then
            pass "long-running succeeded"
            DS01_API_URL="$API_URL" $SUBMIT results "$job_id" -o "$RESULTS_DIR/long-running" 2>&1 || fail "results download"
        else
            fail "long-running status=$status (expected succeeded)"
        fi
        ;;

    3)
        log "Test 3: Multi-file output (CSV + PNG + JSON)"
        local out
        out=$(DS01_API_URL="$API_URL" $SUBMIT run "https://github.com/$ORG/ds01-test-multi-file" --gpus 1 --json 2>&1) || { fail "submit failed: $out"; return; }
        local job_id=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
        log "  Job ID: $job_id — following..."
        DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --follow 2>&1 || true
        local status=$(DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --json 2>&1 | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
        if [[ "$status" == "succeeded" ]]; then
            DS01_API_URL="$API_URL" $SUBMIT results "$job_id" -o "$RESULTS_DIR/multi-file" 2>&1 || fail "results download"
            if [[ -f "$RESULTS_DIR/multi-file/dataset.csv" && -f "$RESULTS_DIR/multi-file/analysis.png" && -f "$RESULTS_DIR/multi-file/summary.json" ]]; then
                pass "multi-file: all 3 output files present"
            else
                fail "multi-file: missing output files ($(ls "$RESULTS_DIR/multi-file/" 2>/dev/null))"
            fi
        else
            fail "multi-file status=$status (expected succeeded)"
        fi
        ;;

    4)
        log "Test 4: Large output (~50MB)"
        local out
        out=$(DS01_API_URL="$API_URL" $SUBMIT run "https://github.com/$ORG/ds01-test-large-output" --gpus 1 --json 2>&1) || { fail "submit failed: $out"; return; }
        local job_id=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
        log "  Job ID: $job_id — following..."
        DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --follow 2>&1 || true
        local status=$(DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --json 2>&1 | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
        if [[ "$status" == "succeeded" ]]; then
            DS01_API_URL="$API_URL" $SUBMIT results "$job_id" -o "$RESULTS_DIR/large-output" 2>&1 || fail "results download"
            local file_count=$(ls "$RESULTS_DIR/large-output/"*.bin 2>/dev/null | wc -l)
            if [[ "$file_count" -eq 50 ]]; then
                pass "large-output: 50 files collected"
            else
                fail "large-output: expected 50 files, got $file_count"
            fi
        else
            fail "large-output status=$status (expected succeeded)"
        fi
        ;;

    5)
        log "Test 5: Runtime failure (Python exception)"
        local out
        out=$(DS01_API_URL="$API_URL" $SUBMIT run "https://github.com/$ORG/ds01-test-failing" --gpus 1 --json 2>&1) || { fail "submit failed: $out"; return; }
        local job_id=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
        log "  Job ID: $job_id — following..."
        DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --follow 2>&1 || true
        local status=$(DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --json 2>&1 | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
        if [[ "$status" == "failed" ]]; then
            pass "runtime-failure correctly reported as failed"
        else
            fail "runtime-failure status=$status (expected failed)"
        fi
        ;;

    6)
        log "Test 6: Build failure (COPY nonexistent file)"
        local out
        out=$(DS01_API_URL="$API_URL" $SUBMIT run "https://github.com/$ORG/ds01-test-failing" --gpus 1 --branch bad-dockerfile --json 2>&1) || { fail "submit failed: $out"; return; }
        local job_id=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
        log "  Job ID: $job_id — following..."
        DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --follow 2>&1 || true
        local status=$(DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --json 2>&1 | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
        if [[ "$status" == "failed" ]]; then
            pass "build-failure correctly reported as failed"
        else
            fail "build-failure status=$status (expected failed)"
        fi
        ;;

    7)
        log "Test 7: Scanner rejection (disallowed base image)"
        local out
        out=$(DS01_API_URL="$API_URL" $SUBMIT run "https://github.com/$ORG/ds01-test-failing" --gpus 1 --branch bad-base-image --json 2>&1)
        local exit_code=$?
        if [[ $exit_code -ne 0 ]] || echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if d.get('status')=='failed' else 1)" 2>/dev/null; then
            pass "scanner rejection: disallowed base image blocked"
        else
            # It might get accepted but fail during build — check status
            local job_id=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)
            if [[ -n "$job_id" ]]; then
                DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --follow 2>&1 || true
                local status=$(DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --json 2>&1 | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
                fail "scanner rejection: job was accepted (status=$status) — scanner should have blocked it"
            else
                pass "scanner rejection: submission rejected"
            fi
        fi
        ;;

    8)
        log "Test 8: GPU compute (PyTorch matmul + training)"
        local out
        out=$(DS01_API_URL="$API_URL" $SUBMIT run "https://github.com/$ORG/ds01-test-gpu-compute" --gpus 1 --json 2>&1) || { fail "submit failed: $out"; return; }
        local job_id=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
        log "  Job ID: $job_id — following (may take a while for image pull)..."
        DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --follow 2>&1 || true
        local status=$(DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --json 2>&1 | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
        if [[ "$status" == "succeeded" ]]; then
            DS01_API_URL="$API_URL" $SUBMIT results "$job_id" -o "$RESULTS_DIR/gpu-compute" 2>&1 || fail "results download"
            if [[ -f "$RESULTS_DIR/gpu-compute/benchmark.json" ]]; then
                cat "$RESULTS_DIR/gpu-compute/benchmark.json"
                pass "gpu-compute succeeded with benchmark results"
            else
                fail "gpu-compute: no benchmark.json in results"
            fi
        else
            fail "gpu-compute status=$status (expected succeeded)"
        fi
        ;;

    9)
        log "Test 9: Timeout enforcement (submit with 60s timeout)"
        local out
        out=$(DS01_API_URL="$API_URL" $SUBMIT run "https://github.com/$ORG/ds01-test-timeout" --gpus 1 --timeout 60 --json 2>&1) || { fail "submit failed: $out"; return; }
        local job_id=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
        log "  Job ID: $job_id — following (should timeout after ~60s)..."
        DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --follow 2>&1 || true
        local status=$(DS01_API_URL="$API_URL" $SUBMIT status "$job_id" --json 2>&1 | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
        if [[ "$status" == "failed" ]]; then
            pass "timeout correctly enforced — job failed"
        else
            fail "timeout status=$status (expected failed)"
        fi
        ;;

    *)
        echo "Unknown test: $num (valid: 1-9)"
        return 1
        ;;
    esac
}

echo ""
echo "========================================="
echo " ds01-jobs Integration Test Suite"
echo " Server: $API_URL"
echo " Results: $RESULTS_DIR"
echo "========================================="
echo ""

if [[ $# -gt 0 ]]; then
    # Run specific test(s)
    for num in "$@"; do
        run_test "$num"
        echo ""
    done
else
    # Run all tests
    for num in $(seq 1 9); do
        run_test "$num"
        echo ""
    done
fi

echo "========================================="
echo -e " Results: ${GREEN}${pass} passed${NC}, ${RED}${fail} failed${NC}, ${YELLOW}${skip} skipped${NC}"
echo "========================================="

[[ $fail -eq 0 ]] || exit 1
