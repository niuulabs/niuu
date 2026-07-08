#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
WORKSPACE_PATH="${WORKSPACE_PATH:-${ROOT_DIR}}"
START_STACK=1
CLEANUP=0
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-300}"
codex_id=""
claude_id=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [--no-start] [--cleanup]

Starts local-dev with the OpenShell runtime, launches one Codex and one Claude
Volundr session, and waits until both are running.

Environment:
  BASE_URL         Volundr base URL (default: ${BASE_URL})
  WORKSPACE_PATH  Local workspace mounted into both sessions (default: repo root)
  TIMEOUT_SECONDS Readiness timeout per session (default: ${TIMEOUT_SECONDS})
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-start)
      START_STACK=0
      shift
      ;;
    --cleanup)
      CLEANUP=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

cleanup() {
  if [[ "${CLEANUP}" != "1" ]]; then
    return 0
  fi
  if [[ -n "${codex_id}" ]]; then
    curl -fsS -X POST "${BASE_URL}/api/v1/forge/sessions/${codex_id}/stop" >/dev/null || true
  fi
  if [[ -n "${claude_id}" ]]; then
    curl -fsS -X POST "${BASE_URL}/api/v1/forge/sessions/${claude_id}/stop" >/dev/null || true
  fi
  "${ROOT_DIR}/stop-dev" || true
}

trap cleanup EXIT

if ! command -v openshell >/dev/null 2>&1; then
  echo "openshell is not on PATH. Install it first, then rerun this smoke." >&2
  echo "Official quickstart: curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh" >&2
  exit 1
fi

if [[ "${START_STACK}" == "1" ]]; then
  "${ROOT_DIR}/start-dev" --openshell
fi

openshell status

create_session() {
  local definition="$1"
  local name="$2"
  local model="$3"
  local payload

  payload="$(
    python3 - "${definition}" "${name}" "${model}" "${WORKSPACE_PATH}" <<'PY' \
    | curl -fsS -X POST "${BASE_URL}/api/v1/forge/sessions" \
      -H 'Content-Type: application/json' \
      --data-binary @-
import json
import sys

definition, name, model, workspace = sys.argv[1:]
print(json.dumps({
    "name": name,
    "definition": definition,
    "model": model,
    "source": {
        "type": "local_mount",
        "local_path": workspace,
    },
    "initial_prompt": "",
    "system_prompt": "Smoke test: start the runtime and wait for broker readiness.",
    "workload_type": "session",
}))
PY
  )"
  if [[ -n "${payload}" ]]; then
    printf '%s' "${payload}"
    return 0
  fi

  wait_session_named "${name}"
}

json_get() {
  curl -fsS "$1"
}

json_field() {
  local payload
  payload="$(cat)"
  python3 - "$1" "${payload}" <<'PY'
import json
import sys

field = sys.argv[1]
data = json.loads(sys.argv[2] or "{}")
value = data
for part in field.split("."):
    if not isinstance(value, dict):
        value = ""
        break
    value = value.get(part, "")
print(value or "")
PY
}

wait_session_named() {
  local name="$1"
  local deadline=$((SECONDS + 30))
  local payload

  while (( SECONDS < deadline )); do
    payload="$(json_get "${BASE_URL}/api/v1/forge/sessions")"
    if python3 - "${name}" "${payload}" <<'PY'
import json
import sys

name = sys.argv[1]
data = json.loads(sys.argv[2] or "{}")
items = data.get("items", data) if isinstance(data, dict) else data
if not isinstance(items, list):
    sys.exit(1)
for item in items:
    if isinstance(item, dict) and item.get("name") == name:
        print(json.dumps(item))
        sys.exit(0)
sys.exit(1)
PY
    then
      return 0
    fi
    sleep 1
  done

  echo "Session ${name} was not returned by list endpoint after create" >&2
  return 1
}

wait_running() {
  local session_id="$1"
  local label="$2"
  local deadline=$((SECONDS + TIMEOUT_SECONDS))

  while (( SECONDS < deadline )); do
    payload="$(json_get "${BASE_URL}/api/v1/forge/sessions/${session_id}")"
    status="$(printf '%s' "${payload}" | json_field status)"
    pod_name="$(printf '%s' "${payload}" | json_field pod_name)"
    chat_endpoint="$(printf '%s' "${payload}" | json_field chat_endpoint)"

    if [[ "${status}" == "running" ]]; then
      echo "${label} session running:"
      echo "  session_id: ${session_id}"
      echo "  sandbox: ${pod_name}"
      echo "  chat_endpoint: ${chat_endpoint}"
      openshell sandbox get "${pod_name}" >/dev/null
      return 0
    fi

    if [[ "${status}" == "failed" || "${status}" == "stopped" ]]; then
      echo "${label} session reached terminal status: ${status}" >&2
      printf '%s\n' "${payload}" >&2
      return 1
    fi

    sleep 2
  done

  echo "${label} session did not become running within ${TIMEOUT_SECONDS}s" >&2
  return 1
}

suffix="$(date +%H%M%S)"
codex_payload="$(create_session skuldCodex "openshell-codex-${suffix}" "gpt-5.4")"
claude_payload="$(create_session skuldClaude "openshell-claude-${suffix}" "claude-sonnet-4-6")"

codex_id="$(printf '%s' "${codex_payload}" | json_field id)"
claude_id="$(printf '%s' "${claude_payload}" | json_field id)"

wait_running "${codex_id}" "Codex"
wait_running "${claude_id}" "Claude"

echo
echo "OpenShell Volundr smoke passed."
openshell sandbox list --selector app.kubernetes.io/managed-by=volundr -o json || true

if [[ "${CLEANUP}" == "1" ]]; then
  cleanup
  trap - EXIT
fi
