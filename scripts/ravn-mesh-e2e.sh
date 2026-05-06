#!/usr/bin/env bash
# End-to-end test for the Ravn mesh architecture.
#
# Tests the full event cascade:
#   test-publisher → code.changed ──→ reviewer ──→ review.passed / review.changes_requested
#                          │                                              ↓
#                          └──→ security ──→ security.passed / security.changes_requested
#                                                                         ↓
#                                                                       coder (only on .changes_requested)
#                                                                         ↓
#                                                                    code.changed (fix applied)
#
# Usage:
#   scripts/ravn-mesh-e2e.sh          — run full e2e test
#   scripts/ravn-mesh-e2e.sh --quick  — just verify mesh starts and discovers
#
# Prerequisites:
#   - Python 3.12+ with ravn installed (or PYTHONPATH set)
#   - No other ravn processes running on the mesh ports

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

LOG_DIR="/tmp/ravn-mesh"
SAVE_DIR="${REPO_ROOT}/logs/mesh-e2e-$(date +%Y%m%d-%H%M%S)"
TIMEOUT_DISCOVERY=20
TIMEOUT_CASCADE=300  # 5 min timeout for full fix cycle with re-review
QUICK_MODE=false

# Parse args
[[ "${1:-}" == "--quick" ]] && QUICK_MODE=true

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

cleanup() {
    log_info "Cleaning up..."
    "${SCRIPT_DIR}/ravn-mesh.sh" stop 2>/dev/null || true
    # Kill any lingering test publisher
    pkill -f "test-mesh-publisher" 2>/dev/null || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log_info "=== Ravn Mesh E2E Test ==="
echo ""

# 1. Clean slate
log_info "Cleaning up previous runs..."
rm -rf /tmp/ravn-mesh/* 2>/dev/null || true
pkill -f "ravn daemon" 2>/dev/null || true
sleep 1

# 2. Start mesh
log_info "Starting 3-node mesh (coder, reviewer, security)..."
"${SCRIPT_DIR}/ravn-mesh.sh" start
echo ""

# 3. Wait for discovery
log_info "Waiting ${TIMEOUT_DISCOVERY}s for mesh discovery..."
sleep "${TIMEOUT_DISCOVERY}"

# 4. Verify all nodes running
log_info "Verifying nodes are running..."
node_count=$(pgrep -f "ravn daemon" | wc -l)
if [[ "${node_count}" -lt 3 ]]; then
    log_error "Expected 3 nodes, found ${node_count}"
    exit 1
fi
log_info "All 3 nodes running"

# 5. Check registry has all publishers
log_info "Checking service registry..."
if [[ -f "${LOG_DIR}/sleipnir-registry.json" ]]; then
    pub_count=$(grep -c '"pub_address"' "${LOG_DIR}/sleipnir-registry.json" 2>/dev/null || echo 0)
    log_info "Registry has ${pub_count} publishers"
else
    log_warn "Registry file not found (may be using mDNS only)"
fi

if $QUICK_MODE; then
    log_info "Quick mode — skipping cascade test"
    "${SCRIPT_DIR}/ravn-mesh.sh" stop
    log_info "=== QUICK TEST PASSED ==="
    exit 0
fi

# 6. Create and publish test event
log_info "Publishing test event (code.changed)..."

# Write test publisher inline
TEST_PUBLISHER=$(mktemp /tmp/test-mesh-publisher.XXXXXX.py)
cat > "${TEST_PUBLISHER}" << 'PYEOF'
#!/usr/bin/env python3
"""Test publisher for mesh e2e — publishes code.changed outcome event."""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, UTC

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleipnir.adapters.discovery import ServiceRegistry
from sleipnir.adapters.nng_transport import NngTransport
from sleipnir.domain.events import SleipnirEvent


async def main():
    print("Creating test publisher...")

    registry_path = Path("/tmp/ravn-mesh/sleipnir-registry.json")
    registry = ServiceRegistry(registry_path)

    transport = NngTransport(
        address="ipc:///tmp/ravn-mesh/test-pub.ipc",
        service_id="test-publisher",
        registry=registry,
    )

    await transport.start()
    print("Test publisher started and registered")

    print("Waiting 15 seconds for mesh nodes to discover and dial...")
    await asyncio.sleep(15)

    print("=== Publishing code.changed event ===")

    # IMPORTANT: Must be RavnEventType.OUTCOME format with proper payload structure
    event = SleipnirEvent(
        event_type="ravn.mesh.code.changed",
        source="ravn:test-publisher",
        payload={
            "ravn_event": {
                "event_type": "code.changed",
                "persona": "developer",
                "outcome": {
                    "file": "/tmp/hello.py",
                    "summary": "Test file with intentional bugs for review",
                    "changes": "Added initial code",
                },
            },
            "ravn_type": "outcome",  # MUST be "outcome" — handler filters on this
            "ravn_source": "ravn:test-publisher",
            "ravn_urgency": 0.8,
            "ravn_session_id": "",
            "ravn_task_id": "e2e-test-001",
        },
        summary="Code changed: /tmp/hello.py needs review",
        urgency=0.8,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id="e2e-test-run",
    )

    await transport.publish(event)
    print("Event published!")

    print("Keeping alive for 60 seconds to observe event flow...")
    await asyncio.sleep(60)

    await transport.stop()
    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
PYEOF

# Create test file for reviewer to inspect
cat > /tmp/hello.py << 'PYCODE'
import subprocess
import sqlite3

def get_user(user_id):
    conn = sqlite3.connect("users.db")
    query = f"SELECT * FROM users WHERE id = {user_id}"  # SQL injection
    return conn.execute(query).fetchone()

def run_command(cmd):
    subprocess.call(cmd, shell=True)  # Command injection

API_KEY = "sk-secret-12345"  # Hardcoded secret

def process():
    try:
        return get_user(1)
    except:  # Bare except
        pass
PYCODE

# Show starting file content
echo ""
log_info "=== STARTING FILE CONTENT ==="
echo "--- /tmp/hello.py ---"
cat /tmp/hello.py
echo "--- end ---"
echo ""

# Run test publisher
PYTHONPATH="${REPO_ROOT}/src" python "${TEST_PUBLISHER}" &
PUB_PID=$!

# 7. Wait and monitor cascade
log_info "Monitoring cascade (timeout: ${TIMEOUT_CASCADE}s)..."

cascade_complete=false
start_time=$(date +%s)

while true; do
    elapsed=$(($(date +%s) - start_time))

    if [[ $elapsed -gt $TIMEOUT_CASCADE ]]; then
        log_error "Cascade timeout after ${TIMEOUT_CASCADE}s"
        break
    fi

    # Count how many code.changed events coder has published (fix cycles)
    coder_fix_count=$(grep -c "publishing outcome event_type=code.changed" "${LOG_DIR}/ravn-mesh-1.log" 2>/dev/null | tr -d '\n' || true)
    coder_fix_count=${coder_fix_count:-0}

    # Count reviewer verdicts
    reviewer_pass_count=$(grep -c "publishing outcome event_type=review.passed" "${LOG_DIR}/ravn-mesh-2.log" 2>/dev/null | tr -d '\n' || true)
    reviewer_pass_count=${reviewer_pass_count:-0}
    reviewer_changes_count=$(grep -c "publishing outcome event_type=review.changes_requested" "${LOG_DIR}/ravn-mesh-2.log" 2>/dev/null | tr -d '\n' || true)
    reviewer_changes_count=${reviewer_changes_count:-0}

    # Count security verdicts
    security_pass_count=$(grep -c "publishing outcome event_type=security.passed" "${LOG_DIR}/ravn-mesh-3.log" 2>/dev/null | tr -d '\n' || true)
    security_pass_count=${security_pass_count:-0}
    security_changes_count=$(grep -c "publishing outcome event_type=security.changes_requested" "${LOG_DIR}/ravn-mesh-3.log" 2>/dev/null | tr -d '\n' || true)
    security_changes_count=${security_changes_count:-0}

    # Show progress
    if [[ $((reviewer_pass_count + reviewer_changes_count + security_pass_count + security_changes_count)) -gt 0 ]]; then
        log_info "Progress: reviewer(pass=${reviewer_pass_count}, changes=${reviewer_changes_count}) security(pass=${security_pass_count}, changes=${security_changes_count}) coder_fixes=${coder_fix_count}"
    fi

    # Cascade is SETTLED when we have .passed from BOTH reviewer and security
    # This means either:
    # 1. Initial review passed (no issues found)
    # 2. Issues were found, coder fixed them, re-review passed
    if [[ $reviewer_pass_count -gt 0 ]] && [[ $security_pass_count -gt 0 ]]; then
        log_info "CASCADE SETTLED: Both reviewer and security published .passed"
        cascade_complete=true
        break
    fi

    # If we have changes_requested but no .passed yet, check if coder is working
    if [[ $reviewer_changes_count -gt 0 ]] || [[ $security_changes_count -gt 0 ]]; then
        if [[ $coder_fix_count -gt 0 ]]; then
            log_info "Coder applied fix #${coder_fix_count}, waiting for re-review..."
        else
            log_info "Changes requested, waiting for coder to fix..."
        fi
    fi

    sleep 5
done

# Kill test publisher
kill $PUB_PID 2>/dev/null || true

# Show ending file content
echo ""
log_info "=== ENDING FILE CONTENT ==="
echo "--- /tmp/hello.py ---"
cat /tmp/hello.py
echo "--- end ---"
echo ""

# 8. Verify cascade
echo ""
log_info "=== Cascade Results ==="

# Full settle flow:
# 1. Reviewer receives code.changed
# 2. Reviewer publishes initial verdict (changes_requested or passed)
# 3. Security receives code.changed
# 4. Security publishes initial verdict (changes_requested or passed)
# 5. If changes_requested: Coder fixes and publishes code.changed
# 6. After fix: Reviewer re-reviews → review.passed
# 7. After fix: Security re-reviews → security.passed
# 8. CASCADE SETTLED

steps_passed=0
steps_total=6  # Full settle requires both .passed events

# Count events (use tr to strip newlines, default to 0)
reviewer_changes=$(grep -c "publishing outcome event_type=review.changes_requested" "${LOG_DIR}/ravn-mesh-2.log" 2>/dev/null | tr -d '\n' || true)
reviewer_changes=${reviewer_changes:-0}
reviewer_passed=$(grep -c "publishing outcome event_type=review.passed" "${LOG_DIR}/ravn-mesh-2.log" 2>/dev/null | tr -d '\n' || true)
reviewer_passed=${reviewer_passed:-0}
security_changes=$(grep -c "publishing outcome event_type=security.changes_requested" "${LOG_DIR}/ravn-mesh-3.log" 2>/dev/null | tr -d '\n' || true)
security_changes=${security_changes:-0}
security_passed=$(grep -c "publishing outcome event_type=security.passed" "${LOG_DIR}/ravn-mesh-3.log" 2>/dev/null | tr -d '\n' || true)
security_passed=${security_passed:-0}
coder_fixes=$(grep -c "publishing outcome event_type=code.changed" "${LOG_DIR}/ravn-mesh-1.log" 2>/dev/null | tr -d '\n' || true)
coder_fixes=${coder_fixes:-0}

# Step 1: Reviewer received code.changed
if grep -q "mesh: received outcome event_type=code.changed" "${LOG_DIR}/ravn-mesh-2.log" 2>/dev/null; then
    log_info "1. Reviewer received code.changed"
    steps_passed=$((steps_passed + 1))
else
    log_error "1. Reviewer did NOT receive code.changed"
fi

# Step 2: Reviewer published initial verdict
if [[ $reviewer_changes -gt 0 ]] || [[ $reviewer_passed -gt 0 ]]; then
    log_info "2. Reviewer published initial verdict (changes_requested=${reviewer_changes}, passed=${reviewer_passed})"
    steps_passed=$((steps_passed + 1))
else
    log_error "2. Reviewer did NOT publish any verdict"
fi

# Step 3: Security received code.changed
if grep -q "mesh: received outcome event_type=code.changed" "${LOG_DIR}/ravn-mesh-3.log" 2>/dev/null; then
    log_info "3. Security received code.changed"
    steps_passed=$((steps_passed + 1))
else
    log_error "3. Security did NOT receive code.changed"
fi

# Step 4: Security published initial verdict
if [[ $security_changes -gt 0 ]] || [[ $security_passed -gt 0 ]]; then
    log_info "4. Security published initial verdict (changes_requested=${security_changes}, passed=${security_passed})"
    steps_passed=$((steps_passed + 1))
else
    log_error "4. Security did NOT publish any verdict"
fi

# Step 5: Coder applied fixes (if needed)
if [[ $reviewer_changes -gt 0 ]] || [[ $security_changes -gt 0 ]]; then
    if [[ $coder_fixes -gt 0 ]]; then
        log_info "5. Coder applied ${coder_fixes} fix(es)"
        steps_passed=$((steps_passed + 1))
    else
        log_error "5. Coder did NOT apply fixes (changes were requested)"
    fi
else
    log_info "5. No fixes needed (initial review passed)"
    steps_passed=$((steps_passed + 1))
fi

# Step 6: CASCADE SETTLED (both reviewer and security published .passed)
if [[ $reviewer_passed -gt 0 ]] && [[ $security_passed -gt 0 ]]; then
    log_info "6. CASCADE SETTLED: review.passed=${reviewer_passed}, security.passed=${security_passed}"
    steps_passed=$((steps_passed + 1))
else
    log_error "6. CASCADE NOT SETTLED: review.passed=${reviewer_passed}, security.passed=${security_passed}"
fi

echo ""

# 9. Save logs
log_info "Saving logs to ${SAVE_DIR}..."
mkdir -p "${SAVE_DIR}"
cp "${LOG_DIR}"/*.log "${SAVE_DIR}/" 2>/dev/null || true
cp "${LOG_DIR}"/*.yaml "${SAVE_DIR}/" 2>/dev/null || true

# Write summary
cat > "${SAVE_DIR}/README.md" << EOF
# Mesh E2E Test Results

**Date:** $(date -Iseconds)
**Result:** ${steps_passed}/${steps_total} steps passed

## Cascade Flow

\`\`\`
test-publisher → code.changed ──→ reviewer (node 2) ──→ review.passed / review.changes_requested
                       │                                               ↓
                       └──→ security (node 3) ──→ security.passed / security.changes_requested
                                                                       ↓
                                                     coder (node 1) — only on .changes_requested
                                                                       ↓
                                                                  code.changed (fix applied)
\`\`\`

## Steps

1. Reviewer received code.changed: $(grep -q "mesh: received outcome event_type=code.changed" "${LOG_DIR}/ravn-mesh-2.log" 2>/dev/null && echo "PASS" || echo "FAIL")
2. Reviewer published verdict: $(grep -qE "publishing outcome event_type=review\.(passed|changes_requested)" "${LOG_DIR}/ravn-mesh-2.log" 2>/dev/null && echo "PASS" || echo "FAIL")
3. Security received code.changed: $(grep -q "mesh: received outcome event_type=code.changed" "${LOG_DIR}/ravn-mesh-3.log" 2>/dev/null && echo "PASS" || echo "FAIL")
4. Security published verdict: $(grep -qE "publishing outcome event_type=security\.(passed|changes_requested)" "${LOG_DIR}/ravn-mesh-3.log" 2>/dev/null && echo "PASS" || echo "FAIL")

## Settle Status

- Reviewer: changes_requested=$(grep -c "event_type=review.changes_requested" "${LOG_DIR}/ravn-mesh-2.log" 2>/dev/null || echo 0), passed=$(grep -c "event_type=review.passed" "${LOG_DIR}/ravn-mesh-2.log" 2>/dev/null || echo 0)
- Security: changes_requested=$(grep -c "event_type=security.changes_requested" "${LOG_DIR}/ravn-mesh-3.log" 2>/dev/null || echo 0), passed=$(grep -c "event_type=security.passed" "${LOG_DIR}/ravn-mesh-3.log" 2>/dev/null || echo 0)
- Coder fixes: $(grep -c "event_type=code.changed" "${LOG_DIR}/ravn-mesh-1.log" 2>/dev/null || echo 0)
- Settled: $(test "$(grep -c 'event_type=review.passed' "${LOG_DIR}/ravn-mesh-2.log" 2>/dev/null || echo 0)" -gt 0 && test "$(grep -c 'event_type=security.passed' "${LOG_DIR}/ravn-mesh-3.log" 2>/dev/null || echo 0)" -gt 0 && echo "YES" || echo "NO")

## Error Check

- Node 1 errors: $(grep -c " ERROR " "${LOG_DIR}/ravn-mesh-1.log" 2>/dev/null || echo "0")
- Node 2 errors: $(grep -c " ERROR " "${LOG_DIR}/ravn-mesh-2.log" 2>/dev/null || echo "0")
- Node 3 errors: $(grep -c " ERROR " "${LOG_DIR}/ravn-mesh-3.log" 2>/dev/null || echo "0")
- Tracebacks: $( (grep -l "Traceback" "${LOG_DIR}"/ravn-mesh-*.log 2>/dev/null | wc -l) || echo "0")
EOF

echo ""

# 10. Check for errors in logs
log_info "=== Error Check ==="
errors_found=0

for n in 1 2 3; do
    log_file="${LOG_DIR}/ravn-mesh-${n}.log"
    # Count ERROR level entries (excluding expected/benign mDNS errors)
    # Note: Need || true because pipefail causes failure when grep finds no matches
    error_count=$(grep " ERROR " "${log_file}" 2>/dev/null | grep -v "mdns_discovery: responder error" | wc -l | tr -d ' ' || true)
    error_count=${error_count:-0}
    if [[ "${error_count}" -gt 0 ]]; then
        log_error "Node ${n} has ${error_count} ERROR entries:"
        grep " ERROR " "${log_file}" | grep -v "mdns_discovery: responder error" | head -5
        errors_found=$((errors_found + error_count))
    else
        log_info "Node ${n}: no errors"
    fi
done

# Also check for Python exceptions/tracebacks
# Note: Need || true because pipefail causes failure when grep finds no matches
traceback_count=$(grep -l "Traceback" "${LOG_DIR}"/ravn-mesh-*.log 2>/dev/null | wc -l | tr -d ' ' || true)
traceback_count=${traceback_count:-0}
if [[ "${traceback_count}" -gt 0 ]]; then
    log_error "Found Python tracebacks in ${traceback_count} log file(s)"
    errors_found=$((errors_found + traceback_count))
fi

echo ""

# 11. Final result
if [[ $steps_passed -eq $steps_total ]] && [[ $errors_found -eq 0 ]]; then
    log_info "=== E2E TEST PASSED (${steps_passed}/${steps_total}, no errors) ==="
    exit 0
elif [[ $steps_passed -eq $steps_total ]]; then
    log_warn "=== E2E TEST PASSED WITH WARNINGS (${steps_passed}/${steps_total}, ${errors_found} errors) ==="
    exit 0  # Still pass but warn
else
    log_error "=== E2E TEST FAILED (${steps_passed}/${steps_total}) ==="
    exit 1
fi
