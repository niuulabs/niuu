#!/usr/bin/env bash
# Deterministic resident Valkyrie Environment demo.
#
# This composes the existing Sleipnir/NATS event path instead of creating a
# demo-only bus. By default it writes replayable JSONL artifacts only. With
# --publish-nats it starts a local nats-server when one is not already present,
# publishes through the Sleipnir NatsPublisher, then tears the local server down.
#
# Usage:
#   scripts/valkyrie-environment-demo.sh
#   scripts/valkyrie-environment-demo.sh --publish-nats
#   scripts/valkyrie-environment-demo.sh --out-dir logs/demo

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PUBLISH_NATS=false
OUT_DIR=""
NATS_URL="${NATS_URL:-nats://127.0.0.1:4222}"
NATS_PID=""
ARGS=()

for arg in "$@"; do
    case "${arg}" in
        --publish-nats)
            PUBLISH_NATS=true
            ARGS+=("--publish-nats" "--nats-url" "${NATS_URL}")
            ;;
        --out-dir=*)
            OUT_DIR="${arg#--out-dir=}"
            ARGS+=("--out-dir" "${OUT_DIR}")
            ;;
        --out-dir)
            # Preserve the pair for the Python CLI.
            ARGS+=("${arg}")
            ;;
        *)
            ARGS+=("${arg}")
            ;;
    esac
done

cleanup() {
    if [[ -n "${NATS_PID}" ]]; then
        kill "${NATS_PID}" 2>/dev/null || true
        wait "${NATS_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT

wait_for_nats() {
    local deadline=$((SECONDS + 20))
    until python - <<'PY' >/dev/null 2>&1
import socket
sock = socket.create_connection(("127.0.0.1", 4222), timeout=1)
sock.close()
PY
    do
        if [[ ${SECONDS} -gt ${deadline} ]]; then
            echo "Timed out waiting for local NATS on 127.0.0.1:4222" >&2
            exit 1
        fi
        sleep 0.5
    done
}

if ${PUBLISH_NATS}; then
    if ! python - <<'PY' >/dev/null 2>&1
import socket
sock = socket.create_connection(("127.0.0.1", 4222), timeout=1)
sock.close()
PY
    then
        if ! command -v nats-server >/dev/null 2>&1; then
            echo "NATS is not reachable and nats-server is not installed." >&2
            echo "Start NATS yourself or install nats-server, then retry --publish-nats." >&2
            exit 1
        fi
        mkdir -p "${REPO_ROOT}/logs"
        nats-server -js -p 4222 > "${REPO_ROOT}/logs/valkyrie-demo-nats.log" 2>&1 &
        NATS_PID=$!
        wait_for_nats
    fi
fi

cd "${REPO_ROOT}"
uv run python scripts/valkyrie-environment-demo.py "${ARGS[@]}"

