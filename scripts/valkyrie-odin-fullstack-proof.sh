#!/usr/bin/env bash
# Full-stack ODIN review proof — every layer real, everything local:
#
#   guarded valkyrie daemons (NATS JetStream)  +  the platform (start-dev)
#   +  the web UI (playwright)
#
# Chain proven:
#   OOM signal -> guarded teacher builds, HOLDS the install
#              -> odin.review.requested travels NATS into the central queue
#              -> the operator approves IN THE WEB INBOX (real click)
#              -> odin.review.decided travels NATS back to the resident
#              -> teacher canaries, installs, proposes to the flock
#              -> student adopts; resident confirms odin.review.resolved
#              -> the queue settles to applied; Activity shows the ledger
#
# Usage: scripts/valkyrie-odin-fullstack-proof.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="/tmp/valkyrie-odin-fullstack-proof"
FLOCK_OUT="/tmp/valkyrie-flock-proof-nats"
NATS_URL="nats://127.0.0.1:4222"
BASE_URL="http://127.0.0.1:8080"
NATS_PID=""

cleanup() {
    echo "Cleaning up..."
    if [[ -f "${FLOCK_OUT}/pids" ]]; then
        while read -r pid; do kill "${pid}" 2>/dev/null || true; done < "${FLOCK_OUT}/pids"
    fi
    "${REPO_ROOT}/stop-dev" >/dev/null 2>&1 || true
    if [[ -n "${NATS_PID}" ]]; then
        kill "${NATS_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT

rm -rf "${OUT_DIR}"
mkdir -p "${OUT_DIR}/logs"

# ---------------------------------------------------------------------------
# 0. Clean slate, then NATS first so the platform subscribes before any
#    daemon publishes the review request.
# ---------------------------------------------------------------------------

"${REPO_ROOT}/stop-dev" >/dev/null 2>&1 || true

if ! command -v nats-server >/dev/null 2>&1; then
    echo "nats-server is required (brew install nats-server)" >&2
    exit 1
fi
echo "Starting nats-server (fresh JetStream store)..."
nats-server -js -sd "${OUT_DIR}/nats-store" -p 4222 -m 8222 \
    > "${OUT_DIR}/logs/nats-server.log" 2>&1 &
NATS_PID=$!
for _ in $(seq 1 20); do
    curl -sf "http://127.0.0.1:8222/healthz" >/dev/null 2>&1 && break
    sleep 0.5
done

# ---------------------------------------------------------------------------
# 1. Platform with the telemetry AND command channels on the proof NATS.
# ---------------------------------------------------------------------------

echo "Starting the platform (start-dev)..."
# The daemons create the ravn_environment stream after the platform boots;
# the subscription retries until it exists and replays the last 10 minutes so
# a review request published before the consumer attached is still ingested.
NIUU_SERVER__HOST=127.0.0.1 \
RAVN_VALKYRIE_TELEMETRY_NATS_URL="${NATS_URL}" \
RAVN_VALKYRIE_TELEMETRY_REPLAY_SECONDS=600 \
RAVN_VALKYRIE_COMMAND_NATS_URL="${NATS_URL}" \
RAVN_VALKYRIE_COMMAND_NATS_ENSURE_STREAM=1 \
RAVN_ODIN_REVIEW_STORE_PATH="${OUT_DIR}/odin_review_queue.json" \
    "${REPO_ROOT}/start-dev" > "${OUT_DIR}/logs/start-dev.log" 2>&1

for _ in $(seq 1 60); do
    curl -sf "${BASE_URL}/api/v1/ravn/valkyrie/dashboard" >/dev/null 2>&1 && break
    sleep 2
done
curl -sf "${BASE_URL}/api/v1/ravn/valkyrie/dashboard" >/dev/null
echo "Platform is up at ${BASE_URL}"

# ---------------------------------------------------------------------------
# 2. Guarded flock over the same NATS: build happens, install is HELD.
#    (--keep + skip-verify: the operator approval belongs to the platform.)
# ---------------------------------------------------------------------------

echo "Starting the guarded valkyrie flock..."
VALKYRIE_PROOF_TEACHER_AUTONOMY=guarded \
VALKYRIE_PROOF_SKIP_VERIFY=1 \
VALKYRIE_PROOF_WAIT_SECONDS=20 \
NATS_URL="${NATS_URL}" \
    bash "${SCRIPT_DIR}/valkyrie-flock-proof.sh" --transport nats --keep \
    > "${OUT_DIR}/logs/flock.log" 2>&1

# ---------------------------------------------------------------------------
# 3. The review request must land in the central queue.
# ---------------------------------------------------------------------------

echo "Waiting for the held build to reach the central review queue..."
echo "(the teacher's investigation session is authoring the tool on a live LLM)"
ITEM_ID=""
for _ in $(seq 1 240); do
    ITEM_ID="$(curl -sf "${BASE_URL}/api/v1/ravn/odin/reviews?status=pending" \
        | python3 -c 'import json,sys; rows=json.load(sys.stdin); print(rows[0]["item_id"] if rows else "")' \
        2>/dev/null || true)"
    [[ -n "${ITEM_ID}" ]] && break
    sleep 2
done
if [[ -z "${ITEM_ID}" ]]; then
    echo "FAIL: no pending review item reached the central queue" >&2
    exit 1
fi
echo "Pending review item in the queue: ${ITEM_ID}"
curl -sf "${BASE_URL}/api/v1/ravn/odin/reviews/${ITEM_ID}" \
    > "${OUT_DIR}/review-item-pending.json"

# ---------------------------------------------------------------------------
# 4. The operator approves in the real web inbox.
# ---------------------------------------------------------------------------

echo "Driving the web inbox with playwright..."
(cd "${REPO_ROOT}/web-next" && node e2e/valkyrie-odin-ui-proof.mjs \
    "${BASE_URL}" "${OUT_DIR}") 2>&1 | tee "${OUT_DIR}/logs/ui-proof.log"

# ---------------------------------------------------------------------------
# 5. The loop must close: resident applied, queue settled, student adopted.
# ---------------------------------------------------------------------------

echo "Waiting for the resident to apply and the queue to settle..."
ITEM_STATUS=""
for _ in $(seq 1 45); do
    ITEM_STATUS="$(curl -sf "${BASE_URL}/api/v1/ravn/odin/reviews/${ITEM_ID}" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' \
        2>/dev/null || true)"
    [[ "${ITEM_STATUS}" == "applied" ]] && break
    sleep 2
done

# The investigation session authors an agent tool with an LLM-chosen name; it
# installs into learned_tools/ rather than a fixed skill path. The review item
# we already fetched names the tool (title = manifest name), and the installer
# maps dots/dashes in the name to underscores in the filename.
TOOL_FILE="$(python3 -c '
import json, sys
title = json.load(open(sys.argv[1])).get("title", "")
print(title.replace(".", "_").replace("-", "_"))
' "${OUT_DIR}/review-item-pending.json")"
if [[ -z "${TOOL_FILE}" ]]; then
    echo "FAIL: pending review item has no title to derive the tool name from" >&2
    exit 1
fi
TEACHER_TOOL="${FLOCK_OUT}/k8s-a-state/learned_tools/${TOOL_FILE}.py"
STUDENT_TOOL="${FLOCK_OUT}/k8s-b-state/learned_tools/${TOOL_FILE}.py"
STUDENT_ADOPTED=0
for _ in $(seq 1 30); do
    [[ -f "${STUDENT_TOOL}" ]] && STUDENT_ADOPTED=1 && break
    sleep 2
done
TEACHER_INSTALLED=0
[[ -f "${TEACHER_TOOL}" ]] && TEACHER_INSTALLED=1

curl -sf "${BASE_URL}/api/v1/ravn/odin/reviews/${ITEM_ID}" \
    > "${OUT_DIR}/review-item-final.json" || true
curl -sf "${BASE_URL}/api/v1/ravn/odin/reviews/summary" \
    > "${OUT_DIR}/review-summary-final.json" || true
cp "${FLOCK_OUT}/events.jsonl" "${OUT_DIR}/events.jsonl" 2>/dev/null || true
cp "${FLOCK_OUT}/k8s-b-state/flock_learning.json" "${OUT_DIR}/student-ledger.json" 2>/dev/null || true

FAILURES=0
report_check() {
    local name="$1" ok="$2" detail="${3:-}"
    if [[ "${ok}" == "1" ]]; then
        echo "  PASS  ${name}${detail:+  [${detail}]}"
    else
        echo "  FAIL  ${name}${detail:+  [${detail}]}"
        FAILURES=$((FAILURES + 1))
    fi
}

echo ""
echo "============================================================"
report_check "review item settled to applied" \
    "$([[ "${ITEM_STATUS}" == "applied" ]] && echo 1 || echo 0)" "status=${ITEM_STATUS}"
report_check "teacher installed the approved tool" "${TEACHER_INSTALLED}" "${TEACHER_TOOL}"
report_check "student adopted after the approval" "${STUDENT_ADOPTED}" "${STUDENT_TOOL}"
report_check "inbox screenshots captured" \
    "$([[ -f "${OUT_DIR}/odin-inbox-pending.png" && -f "${OUT_DIR}/odin-inbox-after-approve.png" ]] && echo 1 || echo 0)"
report_check "activity ledger screenshot captured" \
    "$([[ -f "${OUT_DIR}/odin-activity.png" ]] && echo 1 || echo 0)"
echo "============================================================"
echo "Artifacts: ${OUT_DIR}"

exit "$([[ "${FAILURES}" == "0" ]] && echo 0 || echo 1)"
