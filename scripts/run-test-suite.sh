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
TEST_REPO="https://github.com/hertie-data-science-lab/ds01-jobs"
RESULTS_DIR="/tmp/ds01-test-results"

mkdir -p "$RESULTS_DIR"

# Colours
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass_count=0
fail_count=0
skip_count=0

log()  { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $*"; }
pass() { echo -e "  ${GREEN}PASS${NC}: $1"; pass_count=$((pass_count + 1)); }
fail() { echo -e "  ${RED}FAIL${NC}: $1"; fail_count=$((fail_count + 1)); }
skip() { echo -e "  ${YELLOW}SKIP${NC}: $1"; skip_count=$((skip_count + 1)); }

# Submit a job, follow it, return job_id and final status via globals.
# Always returns 0 if the job was submitted — caller checks JOB_STATUS.
JOB_ID=""
JOB_STATUS=""
submit_and_follow() {
    local repo_url="$1"; shift
    local out
    out=$(DS01_API_URL="$API_URL" $SUBMIT run "$repo_url" "$@" --json 2>&1) || { JOB_ID=""; JOB_STATUS="submit_failed: $out"; return 1; }
    JOB_ID=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
    log "  Job ID: $JOB_ID — following..."
    DS01_API_URL="$API_URL" $SUBMIT status "$JOB_ID" --follow 2>&1 || true
    local json_out
    json_out=$(DS01_API_URL="$API_URL" $SUBMIT status "$JOB_ID" --json 2>&1) || true
    JOB_STATUS=$(echo "$json_out" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])" 2>/dev/null) || JOB_STATUS="unknown"
    return 0
}

# Download results into $RESULTS_DIR/<name>/
download_results() {
    local name="$1"
    DS01_API_URL="$API_URL" $SUBMIT results "$JOB_ID" -o "$RESULTS_DIR/$name" 2>&1 || { fail "$name: results download failed"; return 1; }
}

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
        submit_and_follow "$TEST_REPO" --branch fixtures/cpu-quick --gpus 1 || { fail "cpu-quick: $JOB_STATUS"; return; }
        if [[ "$JOB_STATUS" == "succeeded" ]]; then
            download_results "cpu-quick"
            if [[ -f "$RESULTS_DIR/cpu-quick/results/result.json" ]]; then
                log "  $(cat "$RESULTS_DIR/cpu-quick/results/result.json")"
                pass "cpu-quick succeeded, result.json collected"
            else
                pass "cpu-quick succeeded (results: $(ls -R "$RESULTS_DIR/cpu-quick/" 2>/dev/null))"
            fi
        else
            fail "cpu-quick status=$JOB_STATUS (expected succeeded)"
        fi
        ;;

    2)
        log "Test 2: Long-running job (~2min, 12 epochs)"
        submit_and_follow "$TEST_REPO" --branch fixtures/long-running --gpus 1 || { fail "long-running: $JOB_STATUS"; return; }
        if [[ "$JOB_STATUS" == "succeeded" ]]; then
            download_results "long-running"
            if [[ -f "$RESULTS_DIR/long-running/results/training_results.json" ]]; then
                local final_loss
                final_loss=$(python3 -c "import json; print(json.load(open('$RESULTS_DIR/long-running/results/training_results.json'))['final_loss'])")
                pass "long-running succeeded, final_loss=$final_loss"
            else
                pass "long-running succeeded (results: $(ls -R "$RESULTS_DIR/long-running/" 2>/dev/null))"
            fi
        else
            fail "long-running status=$JOB_STATUS (expected succeeded)"
        fi
        ;;

    3)
        log "Test 3: Multi-file output (CSV + PNG + JSON)"
        submit_and_follow "$TEST_REPO" --branch fixtures/multi-file --gpus 1 || { fail "multi-file: $JOB_STATUS"; return; }
        if [[ "$JOB_STATUS" == "succeeded" ]]; then
            download_results "multi-file"
            local rdir="$RESULTS_DIR/multi-file/results"
            if [[ -f "$rdir/dataset.csv" && -f "$rdir/analysis.png" && -f "$rdir/summary.json" ]]; then
                local rows
                rows=$(wc -l < "$rdir/dataset.csv")
                pass "multi-file: all 3 output files present ($rows rows in CSV)"
            else
                fail "multi-file: missing output files (got: $(ls "$rdir/" 2>/dev/null || ls -R "$RESULTS_DIR/multi-file/" 2>/dev/null))"
            fi
        else
            fail "multi-file status=$JOB_STATUS (expected succeeded)"
        fi
        ;;

    4)
        log "Test 4: Large output (~50MB)"
        submit_and_follow "$TEST_REPO" --branch fixtures/large-output --gpus 1 || { fail "large-output: $JOB_STATUS"; return; }
        if [[ "$JOB_STATUS" == "succeeded" ]]; then
            download_results "large-output"
            local rdir="$RESULTS_DIR/large-output/results"
            local file_count
            file_count=$(ls "$rdir/"*.bin 2>/dev/null | wc -l)
            if [[ "$file_count" -eq 50 ]]; then
                local total_size
                total_size=$(du -sh "$rdir" | cut -f1)
                pass "large-output: 50 files collected ($total_size)"
            else
                fail "large-output: expected 50 .bin files, got $file_count ($(ls "$rdir/" 2>/dev/null | head -5))"
            fi
        else
            fail "large-output status=$JOB_STATUS (expected succeeded)"
        fi
        ;;

    5)
        log "Test 5: Runtime failure (Python exception)"
        submit_and_follow "$TEST_REPO" --branch fixtures/failing-runtime --gpus 1 || { fail "runtime-failure: $JOB_STATUS"; return; }
        if [[ "$JOB_STATUS" == "failed" ]]; then
            pass "runtime-failure correctly reported as failed"
        else
            fail "runtime-failure status=$JOB_STATUS (expected failed)"
        fi
        ;;

    6)
        log "Test 6: Build failure (COPY nonexistent file)"
        submit_and_follow "$TEST_REPO" --branch fixtures/failing-build --gpus 1 || { fail "build-failure: $JOB_STATUS"; return; }
        if [[ "$JOB_STATUS" == "failed" ]]; then
            pass "build-failure correctly reported as failed"
        else
            fail "build-failure status=$JOB_STATUS (expected failed)"
        fi
        ;;

    7)
        log "Test 7: Scanner rejection (inline Dockerfile with disallowed base image)"
        # Scanner only runs on inline dockerfile_content, not cloned repos.
        # docker.io/library/* is allowed (all official images), so we use a
        # non-official image to trigger the scanner: bitnami/python
        local bad_df="/tmp/ds01-bad-dockerfile"
        printf 'FROM bitnami/python:latest\nCMD echo "should never run"\n' > "$bad_df"
        local out exit_code
        out=$(DS01_API_URL="$API_URL" $SUBMIT run "$TEST_REPO" --branch fixtures/cpu-quick --gpus 1 --dockerfile "$bad_df" --json 2>&1) && exit_code=0 || exit_code=$?
        if [[ $exit_code -ne 0 ]]; then
            log "  Rejected: $out"
            pass "scanner rejection: disallowed base image blocked at submission"
        else
            local job_status
            job_status=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null) || job_status="unknown"
            if [[ "$job_status" == "queued" ]]; then
                fail "scanner rejection: job was accepted (should have been blocked)"
            else
                pass "scanner rejection: submission returned non-queued status=$job_status"
            fi
        fi
        ;;

    8)
        log "Test 8: GPU compute (PyTorch matmul + training)"
        log "  NOTE: First run pulls ~8GB image — may take several minutes"
        submit_and_follow "$TEST_REPO" --branch fixtures/gpu-compute --gpus 1 || { fail "gpu-compute: $JOB_STATUS"; return; }
        if [[ "$JOB_STATUS" == "succeeded" ]]; then
            download_results "gpu-compute"
            local rdir="$RESULTS_DIR/gpu-compute/results"
            if [[ -f "$rdir/benchmark.json" ]]; then
                log "  Benchmark results:"
                cat "$rdir/benchmark.json"
                echo ""
                pass "gpu-compute succeeded with benchmark results"
            else
                pass "gpu-compute succeeded (results: $(ls -R "$RESULTS_DIR/gpu-compute/" 2>/dev/null))"
            fi
        else
            fail "gpu-compute status=$JOB_STATUS (expected succeeded)"
        fi
        ;;

    9)
        log "Test 9: Timeout enforcement (submit with 60s timeout)"
        submit_and_follow "$TEST_REPO" --branch fixtures/timeout --gpus 1 --timeout 60 || { fail "timeout: $JOB_STATUS"; return; }
        if [[ "$JOB_STATUS" == "failed" ]]; then
            pass "timeout correctly enforced — job killed"
        else
            fail "timeout status=$JOB_STATUS (expected failed)"
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
    for num in "$@"; do
        run_test "$num"
        echo ""
    done
else
    for num in $(seq 1 9); do
        run_test "$num"
        echo ""
    done
fi

echo "========================================="
echo -e " Results: ${GREEN}${pass_count} passed${NC}, ${RED}${fail_count} failed${NC}, ${YELLOW}${skip_count} skipped${NC}"
echo "========================================="

[[ $fail_count -eq 0 ]] || exit 1
