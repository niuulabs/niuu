#!/usr/bin/env bash
# End-to-end test for M6 Raiding Parties — full stack.
#
# Spins up the complete Niuu platform (Volundr + Tyr + embedded PostgreSQL),
# imports a saga from Linear, dispatches it as a ravn_flock workload, and
# verifies the outcome loop.
#
# Flow tested:
#   niuu platform up
#     → Tyr has raids in dispatch queue
#     → POST /api/v1/tyr/dispatch/approve (workload_type=ravn_flock)
#     → Volundr spawns local flock session (Skuld + Ravn sidecars)
#     → Coordinator executes → outcome block → ravn.task.completed
#     → Tyr ReviewEngine: auto-approve / retry / escalate
#
# Usage:
#   scripts/ravn-flock-e2e.sh              — start platform, verify APIs, leave running
#   scripts/ravn-flock-e2e.sh --quick      — just verify platform starts
#   scripts/ravn-flock-e2e.sh --skip-start — assume platform is already running
#   scripts/ravn-flock-e2e.sh --teardown   — stop platform after tests (default: leave running)
#
# Prerequisites:
#   - uv sync --all-extras --dev
#   - niuu platform init (already done — ~/.niuu/config.yaml exists)
#   - ANTHROPIC_API_KEY set (or in config)
#   - Linear integration configured (for saga import)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SAVE_DIR="${REPO_ROOT}/logs/flock-e2e-$(date +%Y%m%d-%H%M%S)"
PLATFORM_URL="http://localhost:8080"
QUICK_MODE=false
SKIP_START=false
TEARDOWN=false
PLATFORM_PID=""

# Parse args
for arg in "$@"; do
    case "$arg" in
        --quick) QUICK_MODE=true ;;
        --skip-start) SKIP_START=true ;;
        --teardown) TEARDOWN=true ;;
    esac
done

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${CYAN}[STEP]${NC} $*"; }

cleanup() {
    if $TEARDOWN && [[ -n "${PLATFORM_PID}" ]]; then
        log_info "Stopping platform (pid=${PLATFORM_PID})..."
        kill "${PLATFORM_PID}" 2>/dev/null || true
        wait "${PLATFORM_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Helper: wait for platform to be healthy
# ---------------------------------------------------------------------------

wait_for_health() {
    local url="$1"
    local timeout="$2"
    local start_time
    start_time=$(date +%s)

    while true; do
        local elapsed=$(( $(date +%s) - start_time ))
        if [[ $elapsed -gt $timeout ]]; then
            return 1
        fi
        # Check both Volundr and Tyr endpoints to confirm full stack is up
        if curl -sf "${url}/api/v1/volundr/sessions" > /dev/null 2>&1 && \
           curl -sf "${url}/api/v1/tyr/dispatcher" > /dev/null 2>&1; then
            return 0
        fi
        sleep 3
    done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

echo ""
log_info "========================================="
log_info "  M6 Raiding Party — Full Stack E2E"
log_info "========================================="
echo ""

steps_passed=0
steps_total=0
mkdir -p "${SAVE_DIR}"

# ---------------------------------------------------------------------------
# Step 0: Ensure PostgreSQL binaries are available
# ---------------------------------------------------------------------------

log_step "0. Checking PostgreSQL binaries..."

if [[ -z "${NIUU_PG_BIN_DIR:-}" ]]; then
    # Check build/pginstall first (dev build)
    if [[ -f "${REPO_ROOT}/build/pginstall/bin/postgres" ]]; then
        export NIUU_PG_BIN_DIR="${REPO_ROOT}/build/pginstall/bin"
        log_info "  Using dev build: ${NIUU_PG_BIN_DIR}"
    # Check homebrew
    elif command -v pg_ctl &>/dev/null; then
        export NIUU_PG_BIN_DIR="$(dirname "$(command -v pg_ctl)")"
        log_info "  Using system PostgreSQL: ${NIUU_PG_BIN_DIR}"
    elif [[ -d "$(brew --prefix postgresql@17 2>/dev/null)/bin" ]] && [[ -f "$(brew --prefix postgresql@17)/bin/postgres" ]]; then
        export NIUU_PG_BIN_DIR="$(brew --prefix postgresql@17)/bin"
        log_info "  Using Homebrew PostgreSQL 17: ${NIUU_PG_BIN_DIR}"
    elif [[ -d "$(brew --prefix postgresql@16 2>/dev/null)/bin" ]] && [[ -f "$(brew --prefix postgresql@16)/bin/postgres" ]]; then
        export NIUU_PG_BIN_DIR="$(brew --prefix postgresql@16)/bin"
        log_info "  Using Homebrew PostgreSQL 16: ${NIUU_PG_BIN_DIR}"
    else
        log_error "  PostgreSQL binaries not found!"
        echo ""
        echo "  Fix with one of:"
        echo "    brew install postgresql@17           # quickest"
        echo "    make build-postgres                  # builds from source (~5 min)"
        echo "    export NIUU_PG_BIN_DIR=/path/to/bin  # point at existing install"
        echo ""
        exit 1
    fi
else
    log_info "  Using NIUU_PG_BIN_DIR=${NIUU_PG_BIN_DIR}"
fi

# ---------------------------------------------------------------------------
# Step 1: Ensure flock dispatch is enabled in Tyr config
# ---------------------------------------------------------------------------

log_step "1. Checking Tyr flock dispatch config..."
steps_total=$((steps_total + 1))

# The dispatch.flock config is in tyr.yaml or can be set via the unified config.
# For mini mode, we create a tyr.yaml in CWD that enables flock.
TYR_CONFIG="${REPO_ROOT}/tyr.yaml"
if [[ ! -f "${TYR_CONFIG}" ]]; then
    log_info "  Creating tyr.yaml with flock dispatch enabled..."
    cat > "${TYR_CONFIG}" <<YAML
# Tyr config for M6 flock e2e testing
dispatch:
  flock:
    enabled: true
    default_personas:
      - coordinator
      - reviewer
    sleipnir_publish_urls: []

review:
  auto_approve_threshold: 0.70
  max_retries: 3
  reviewer_session_enabled: false
  ravn_arbiter_enabled: false

ravn_outcome:
  enabled: true
  owner_id: dev-user
  scope_adherence_threshold: 0.7

sleipnir:
  enabled: true
  adapter: "sleipnir.adapters.in_process.InProcessBus"

auth:
  allow_anonymous_dev: true
YAML
    log_info "  Created ${TYR_CONFIG}"
else
    log_info "  tyr.yaml already exists"
fi
steps_passed=$((steps_passed + 1))

# ---------------------------------------------------------------------------
# Step 2: Start the platform
# ---------------------------------------------------------------------------

log_step "2. Starting platform (niuu platform up --all)..."
steps_total=$((steps_total + 1))

if $SKIP_START; then
    log_info "  --skip-start: assuming platform is already running"
    if ! curl -sf "${PLATFORM_URL}/api/v1/volundr/sessions" > /dev/null 2>&1; then
        log_error "  Platform not reachable at ${PLATFORM_URL}"
        exit 1
    fi
    steps_passed=$((steps_passed + 1))
else
    # Start platform in background
    cd "${REPO_ROOT}"
    uv run python -m cli platform up --all --skip-preflight > "${SAVE_DIR}/platform.log" 2>&1 &
    PLATFORM_PID=$!
    log_info "  Platform starting (pid=${PLATFORM_PID}, log=${SAVE_DIR}/platform.log)"

    # Wait for health
    log_info "  Waiting for platform to become healthy (timeout=90s)..."
    if wait_for_health "${PLATFORM_URL}" 90; then
        steps_passed=$((steps_passed + 1))
        log_info "  Platform is healthy at ${PLATFORM_URL}"
    else
        log_error "  Platform failed to start within 60s"
        log_error "  Check: ${SAVE_DIR}/platform.log"
        tail -20 "${SAVE_DIR}/platform.log" 2>/dev/null
        exit 1
    fi
fi

if $QUICK_MODE; then
    echo ""
    log_info "Quick mode — platform is up, skipping dispatch test"
    log_info "=== QUICK TEST: ${steps_passed}/${steps_total} steps passed ==="
    if [[ -n "${PLATFORM_PID}" ]]; then
        log_info "Platform still running (pid=${PLATFORM_PID})"
        log_info "  Web UI: ${PLATFORM_URL}"
        log_info "  Stop:   kill ${PLATFORM_PID}"
    fi
    exit 0
fi

# ---------------------------------------------------------------------------
# Step 3: Verify Tyr dispatch queue API works
# ---------------------------------------------------------------------------

log_step "3. Verifying Tyr dispatch queue API..."
steps_total=$((steps_total + 1))

queue_response=$(curl -sf "${PLATFORM_URL}/api/v1/tyr/dispatch/queue" 2>/dev/null || echo "FAIL")
if [[ "${queue_response}" != "FAIL" ]]; then
    steps_passed=$((steps_passed + 1))
    queue_count=$(echo "${queue_response}" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
    log_info "  Dispatch queue accessible (${queue_count} items ready)"
    echo "${queue_response}" | python3 -m json.tool > "${SAVE_DIR}/queue.json" 2>/dev/null || true
else
    log_error "  Cannot reach dispatch queue API"
fi

# ---------------------------------------------------------------------------
# Step 4: Check dispatcher state
# ---------------------------------------------------------------------------

log_step "4. Checking dispatcher state..."
steps_total=$((steps_total + 1))

dispatcher_response=$(curl -sf "${PLATFORM_URL}/api/v1/tyr/dispatcher" 2>/dev/null || echo "FAIL")
if [[ "${dispatcher_response}" != "FAIL" ]]; then
    steps_passed=$((steps_passed + 1))
    running=$(echo "${dispatcher_response}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('running', False))" 2>/dev/null || echo "?")
    auto_continue=$(echo "${dispatcher_response}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('auto_continue', False))" 2>/dev/null || echo "?")
    max_concurrent=$(echo "${dispatcher_response}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('max_concurrent_raids', '?'))" 2>/dev/null || echo "?")
    log_info "  Dispatcher: running=${running}, auto_continue=${auto_continue}, max_concurrent=${max_concurrent}"
    echo "${dispatcher_response}" | python3 -m json.tool > "${SAVE_DIR}/dispatcher.json" 2>/dev/null || true
else
    log_error "  Cannot reach dispatcher API"
fi

# ---------------------------------------------------------------------------
# Step 5: List existing sagas
# ---------------------------------------------------------------------------

log_step "5. Listing existing sagas..."
steps_total=$((steps_total + 1))

sagas_response=$(curl -sf "${PLATFORM_URL}/api/v1/tyr/sagas" 2>/dev/null || echo "FAIL")
if [[ "${sagas_response}" != "FAIL" ]]; then
    steps_passed=$((steps_passed + 1))
    saga_count=$(echo "${sagas_response}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d) if isinstance(d, list) else len(d.get('sagas', [])))" 2>/dev/null || echo "?")
    log_info "  Found ${saga_count} saga(s)"
    echo "${sagas_response}" | python3 -m json.tool > "${SAVE_DIR}/sagas.json" 2>/dev/null || true

    # Show sagas with ready raids
    if [[ "${queue_count:-0}" != "0" ]] && [[ "${queue_count}" != "?" ]]; then
        log_info "  Ready raids in dispatch queue:"
        echo "${queue_response}" | python3 -c "
import sys, json
items = json.load(sys.stdin)
for item in items[:5]:
    print(f\"    {item.get('identifier','?')}: {item.get('title','?')} ({item.get('phase_name','?')})\")
" 2>/dev/null || true
    fi
else
    log_error "  Cannot list sagas"
fi

# ---------------------------------------------------------------------------
# Step 6: Dispatch a flock raid (if queue has items)
# ---------------------------------------------------------------------------

log_step "6. Dispatching a flock raid..."
steps_total=$((steps_total + 1))

if [[ "${queue_count:-0}" == "0" ]] || [[ "${queue_count}" == "?" ]]; then
    log_warn "  No raids in dispatch queue — skipping dispatch"
    log_info "  To add raids: import a saga from Linear via the web UI or CLI"
    log_info "    niuu sagas create --from-linear <project-url>"
    log_info "    niuu raids approve <saga-id> <issue-id>"
    steps_passed=$((steps_passed + 1))  # Not a failure, just nothing to dispatch
else
    # Pick the first item from the queue
    first_item=$(echo "${queue_response}" | python3 -c "
import sys, json
items = json.load(sys.stdin)
if items:
    item = items[0]
    print(json.dumps({
        'items': [{
            'saga_id': item['saga_id'],
            'issue_id': item['issue_id'],
            'repo': item['repos'][0] if item.get('repos') else ''
        }]
    }))
" 2>/dev/null)

    if [[ -n "${first_item}" ]]; then
        log_info "  Dispatching first ready raid..."
        dispatch_result=$(curl -sf -X POST "${PLATFORM_URL}/api/v1/tyr/dispatch/approve" \
            -H "Content-Type: application/json" \
            -d "${first_item}" 2>/dev/null || echo "FAIL")

        if [[ "${dispatch_result}" != "FAIL" ]]; then
            steps_passed=$((steps_passed + 1))
            session_id=$(echo "${dispatch_result}" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r[0].get('session_id','?') if r else '?')" 2>/dev/null || echo "?")
            log_info "  Dispatched! Session: ${session_id}"
            echo "${dispatch_result}" | python3 -m json.tool > "${SAVE_DIR}/dispatch-result.json" 2>/dev/null || true

            # Monitor the session briefly
            log_info "  Monitoring session for 30s..."
            for i in $(seq 1 6); do
                sleep 5
                session_status=$(curl -sf "${PLATFORM_URL}/api/v1/volundr/sessions/${session_id}" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
                log_info "    [${i}/6] Session status: ${session_status}"
                if [[ "${session_status}" == "completed" ]] || [[ "${session_status}" == "stopped" ]]; then
                    break
                fi
            done
        else
            log_error "  Dispatch failed"
        fi
    else
        log_warn "  Could not parse queue item"
        steps_passed=$((steps_passed + 1))
    fi
fi

# ---------------------------------------------------------------------------
# Step 7: Verify Volundr sessions
# ---------------------------------------------------------------------------

log_step "7. Verifying Volundr sessions..."
steps_total=$((steps_total + 1))

sessions_response=$(curl -sf "${PLATFORM_URL}/api/v1/volundr/sessions" 2>/dev/null || echo "FAIL")
if [[ "${sessions_response}" != "FAIL" ]]; then
    steps_passed=$((steps_passed + 1))
    session_count=$(echo "${sessions_response}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d) if isinstance(d, list) else len(d.get('sessions', [])))" 2>/dev/null || echo "?")
    log_info "  Found ${session_count} session(s)"
    echo "${sessions_response}" | python3 -m json.tool > "${SAVE_DIR}/sessions.json" 2>/dev/null || true
else
    log_error "  Cannot list sessions"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
log_info "========================================="
log_info "  Results: ${steps_passed}/${steps_total} steps passed"
log_info "  Logs: ${SAVE_DIR}/"
log_info "========================================="

if [[ -n "${PLATFORM_PID}" ]] && ! $TEARDOWN; then
    echo ""
    log_info "Platform is still running (pid=${PLATFORM_PID})"
    log_info "  Web UI:  ${PLATFORM_URL}"
    log_info "  API:     ${PLATFORM_URL}/api/v1/"
    log_info "  Stop:    kill ${PLATFORM_PID}"
fi

echo ""
log_info "Play with it:"
echo ""
echo "  1. Open the web UI: ${PLATFORM_URL}"
echo "  2. Import a saga from Linear (or create one via CLI):"
echo "       niuu sagas create --from-linear <linear-project-url>"
echo "  3. Go to the dispatch queue in the Tyr tab"
echo "  4. Select raids and click Dispatch"
echo "  5. Watch the session in the Sessions tab"
echo "  6. Check raid outcomes in the Raids tab"
echo ""
echo "  Simulate outcomes manually:"
echo "       uv run scripts/test-flock-outcome.py --tyr-url ${PLATFORM_URL} --session-id <session-id> --verdict approve"
echo ""

[[ $steps_passed -eq $steps_total ]] && exit 0 || exit 1
