#!/usr/bin/env bash
# End-to-end Valkyrie flock proof: a resident Valkyrie self-improves, writes
# its own tool, and teaches peer Valkyries — over the local nng flock or NATS.
#
# Topology (three real `ravn daemon` processes + one observer):
#   valkyrie-k8s-a   teacher   yolo        flock:k8s-valkyries  (gets the signal)
#   valkyrie-k8s-b   student   autonomous  flock:k8s-valkyries  (adopts via canary)
#   valkyrie-printer control   autonomous  flock:printer-cell   (rejects: wrong flock)
#
# Chain proven:
#   signal -> micro-dream -> skill + executable probe on disk
#          -> flock.learning.proposed (tool code + canary sample)
#          -> student canaries, installs, ACKs adoption
#          -> printer rejects (flock mismatch)
#          -> replayed signal handled by the built tool
#
# Usage:
#   scripts/valkyrie-flock-proof.sh                    # nng (local flock)
#   scripts/valkyrie-flock-proof.sh --transport nats   # NATS JetStream
#   scripts/valkyrie-flock-proof.sh --keep             # leave daemons running

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

TRANSPORT="nng"
KEEP_RUNNING=0
BUILDER="template"   # template | agent (agent authors the tool with a real LLM)
WAIT_SECONDS="${VALKYRIE_PROOF_WAIT_SECONDS:-}"

# LLM used by --builder agent (any OpenAI-compatible endpoint).
# When OPENAI_API_KEY is set the proof defaults to the OpenAI API; otherwise
# it uses the local vLLM. Override with VALKYRIE_PROOF_LLM_* env vars.
if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    PROOF_LLM_BASE_URL="${VALKYRIE_PROOF_LLM_BASE_URL:-https://api.openai.com}"
    PROOF_LLM_MODEL="${VALKYRIE_PROOF_LLM_MODEL:-gpt-5.2}"
    PROOF_LLM_API_KEY_ENV="${VALKYRIE_PROOF_LLM_API_KEY_ENV:-OPENAI_API_KEY}"
else
    PROOF_LLM_BASE_URL="${VALKYRIE_PROOF_LLM_BASE_URL:-https://qwen36-coder-llmd.valaskjalf.asgard.niuu.world}"
    PROOF_LLM_MODEL="${VALKYRIE_PROOF_LLM_MODEL:-Qwen/Qwen3.6-35B-A3B-FP8}"
    PROOF_LLM_API_KEY_ENV="${VALKYRIE_PROOF_LLM_API_KEY_ENV:-}"
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --transport) TRANSPORT="$2"; shift 2 ;;
        --keep) KEEP_RUNNING=1; shift ;;
        --builder) BUILDER="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

# Real LLM generation needs more time than the deterministic template.
if [[ -z "${WAIT_SECONDS}" ]]; then
    if [[ "${BUILDER}" == "agent" ]]; then
        WAIT_SECONDS=150
    else
        WAIT_SECONDS=25
    fi
fi

OUT_DIR="/tmp/valkyrie-flock-proof-${TRANSPORT}"
PIDS_FILE="${OUT_DIR}/pids"
NATS_URL="${NATS_URL:-nats://127.0.0.1:4222}"
NATS_PID=""

_ravn() { uv run --project "${REPO_ROOT}" python -m ravn "$@"; }

cleanup() {
    if [[ "${KEEP_RUNNING}" == "1" ]]; then
        echo "Leaving daemons running (--keep). PIDs in ${PIDS_FILE}"
        return
    fi
    if [[ -f "${PIDS_FILE}" ]]; then
        while read -r pid; do
            kill "${pid}" 2>/dev/null || true
        done < "${PIDS_FILE}"
    fi
    if [[ -n "${NATS_PID}" ]]; then
        kill "${NATS_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT

rm -rf "${OUT_DIR}"
mkdir -p "${OUT_DIR}/logs"

# ---------------------------------------------------------------------------
# Transport plumbing
# ---------------------------------------------------------------------------

if [[ "${TRANSPORT}" == "nng" ]]; then
    cat > "${OUT_DIR}/cluster.yaml" <<YAML
peers:
  - peer_id: valkyrie-k8s-a
    persona: valkyrie
    display_name: Eir
    pub_address: "ipc://${OUT_DIR}/k8s-a-pub.sock"
    rep_address: "ipc://${OUT_DIR}/k8s-a-rep.sock"
  - peer_id: valkyrie-k8s-b
    persona: valkyrie
    display_name: Kara
    pub_address: "ipc://${OUT_DIR}/k8s-b-pub.sock"
    rep_address: "ipc://${OUT_DIR}/k8s-b-rep.sock"
  - peer_id: valkyrie-printer
    persona: valkyrie
    display_name: Rota
    pub_address: "ipc://${OUT_DIR}/printer-pub.sock"
    rep_address: "ipc://${OUT_DIR}/printer-rep.sock"
YAML
elif [[ "${TRANSPORT}" == "nats" ]]; then
    if ! curl -sf "http://127.0.0.1:8222/healthz" >/dev/null 2>&1; then
        if ! command -v nats-server >/dev/null 2>&1; then
            echo "NATS is not reachable and nats-server is not installed." >&2
            echo "Install nats-server (brew install nats-server) or start one, then retry." >&2
            exit 1
        fi
        echo "Starting local nats-server with JetStream..."
        # Fresh store dir per run: persisted streams/consumers from a previous
        # (killed) run otherwise replay into this one and pollute the evidence.
        nats-server -js -sd "${OUT_DIR}/nats-store" -p 4222 -m 8222 \
            > "${OUT_DIR}/logs/nats-server.log" 2>&1 &
        NATS_PID=$!
        for _ in $(seq 1 20); do
            curl -sf "http://127.0.0.1:8222/healthz" >/dev/null 2>&1 && break
            sleep 0.5
        done
    fi
else
    echo "unknown transport: ${TRANSPORT}" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Node configs
# ---------------------------------------------------------------------------

BUILDER_ADAPTER="ravn.valkyrie_evolution.adapters.TemplateToolBuilder"
BUILDER_KWARGS=""
LLM_MODEL="proof-disabled"
LLM_BASE_URL="http://127.0.0.1:1"
LLM_SECRET_BLOCK=""
if [[ "${BUILDER}" == "agent" ]]; then
    BUILDER_ADAPTER="ravn.valkyrie_evolution.adapters.AgentToolBuilder"
    # Thinking models burn output budget on hidden reasoning before any
    # visible JSON appears; 4096 truncates mid-think and yields empty content.
    BUILDER_KWARGS=$'\n  builder_kwargs:\n    max_tokens: 16384'
    LLM_MODEL="${PROOF_LLM_MODEL}"
    LLM_BASE_URL="${PROOF_LLM_BASE_URL}"
    if [[ -n "${PROOF_LLM_API_KEY_ENV}" ]]; then
        LLM_SECRET_BLOCK=$'\n    secret_kwargs_env:\n      api_key: '"${PROOF_LLM_API_KEY_ENV}"
    fi
    echo "Agent builder LLM: ${LLM_MODEL} @ ${LLM_BASE_URL}"
fi

write_config() {
    local node="$1" peer_id="$2" env_id="$3" env_type="$4" flock="$5" autonomy="$6" sources="$7"
    local config_path="${OUT_DIR}/${node}.yaml"
    local mesh_block

    if [[ "${TRANSPORT}" == "nng" ]]; then
        mesh_block=$(cat <<MESH
mesh:
  enabled: true
  adapter: nng
  own_peer_id: ${peer_id}
  nng:
    pub_sub_address: "ipc://${OUT_DIR}/${node}-pub.sock"
    req_rep_address: "ipc://${OUT_DIR}/${node}-rep.sock"

discovery:
  enabled: false
  adapters:
    - adapter: ravn.adapters.discovery.static.StaticDiscoveryAdapter
      cluster_file: "${OUT_DIR}/cluster.yaml"
      poll_interval_s: 10
MESH
)
    else
        mesh_block=$(cat <<MESH
mesh:
  enabled: true
  adapter: nats
  own_peer_id: ${peer_id}

discovery:
  enabled: false
MESH
)
    fi

    cat > "${config_path}" <<YAML
# Valkyrie flock proof node: ${peer_id} (auto-generated by valkyrie-flock-proof.sh)

${mesh_block}

environment:
  id: ${env_id}
  name: ${env_id}
  type: ${env_type}
  resident_name: ${peer_id}
  flocks:
    - ${flock}
  signal_poll_interval_seconds: 2.0
  signal_task_severities: []
${sources}

dream_cycle:
  autonomy_mode: ${autonomy}
  builder_adapter: ${BUILDER_ADAPTER}${BUILDER_KWARGS}

resident_wakefulness:
  enabled: true
  tick_interval_seconds: 1.0
  wakeful_window_seconds: 5.0
  dream_interval_seconds: 12.0
  dream_min_idle_seconds: 3.0

skill:
  backend: file
  include_builtin: false

cascade:
  enabled: false

gateway:
  enabled: false

initiative:
  enabled: false

memory:
  backend: sqlite
  sqlite:
    path: "${OUT_DIR}/${node}.db"

llm:
  model: "${LLM_MODEL}"
  provider:
    adapter: ravn.adapters.llm.openai.OpenAICompatibleAdapter
    kwargs:
      base_url: "${LLM_BASE_URL}"
      api_key: ""${LLM_SECRET_BLOCK}

permission:
  workspace_root: ${OUT_DIR}/${node}-workspace

logging:
  level: INFO
YAML
    echo "${config_path}"
}

K8S_A_SOURCES=$(cat <<SRC
  signal_sources:
    - id: k8s-events
      name: Kubernetes Events
      adapter: ravn.adapters.environment_signals.KubernetesSignalAdapter
      kind: kubernetes
      kwargs:
        raw_items_file: "${OUT_DIR}/inject-k8s-a.json"
SRC
)

CONFIG_A="$(write_config k8s-a valkyrie-k8s-a cluster-a k8s k8s-valkyries yolo "${K8S_A_SOURCES}")"
CONFIG_B="$(write_config k8s-b valkyrie-k8s-b cluster-b k8s k8s-valkyries autonomous "")"
CONFIG_P="$(write_config printer valkyrie-printer printer-cell printer.pi printer-cell-valkyries autonomous "")"

mkdir -p "${OUT_DIR}/k8s-a-workspace" "${OUT_DIR}/k8s-b-workspace" "${OUT_DIR}/printer-workspace"

# ---------------------------------------------------------------------------
# Start observer + daemons (signal file does not exist yet)
# ---------------------------------------------------------------------------

EVENTS_FILE="${OUT_DIR}/events.jsonl"

if [[ "${TRANSPORT}" == "nng" ]]; then
    uv run --project "${REPO_ROOT}" python "${SCRIPT_DIR}/valkyrie_flock_proof_observer.py" \
        --transport nng --out "${EVENTS_FILE}" \
        --cluster-file "${OUT_DIR}/cluster.yaml" \
        --own-address "ipc://${OUT_DIR}/observer.sock" \
        > "${OUT_DIR}/logs/observer.log" 2>&1 &
else
    uv run --project "${REPO_ROOT}" python "${SCRIPT_DIR}/valkyrie_flock_proof_observer.py" \
        --transport nats --out "${EVENTS_FILE}" --nats-url "${NATS_URL}" \
        > "${OUT_DIR}/logs/observer.log" 2>&1 &
fi
OBSERVER_PID=$!
echo "${OBSERVER_PID}" > "${PIDS_FILE}"
sleep 2

start_daemon() {
    local node="$1" config="$2"
    RAVN_CONFIG="${config}" \
    RAVN_STATE_DIR="${OUT_DIR}/${node}-state" \
    NATS_URL="${NATS_URL}" \
        _ravn daemon > "${OUT_DIR}/logs/${node}.log" 2>&1 &
    local pid=$!
    echo "${pid}" >> "${PIDS_FILE}"
    echo "  ${node}: pid=${pid} config=${config}"
}

echo "Starting Valkyrie flock (transport=${TRANSPORT}, builder=${BUILDER})..."
start_daemon printer "${CONFIG_P}"
start_daemon k8s-b "${CONFIG_B}"
start_daemon k8s-a "${CONFIG_A}"

echo "Waiting for residents to subscribe..."
sleep 8

# ---------------------------------------------------------------------------
# Inject the signal: two OOMKilled events (same capability, distinct ids).
# Item 1 triggers the micro-dream; item 2 replays through the built tool.
# ---------------------------------------------------------------------------

cat > "${OUT_DIR}/inject-k8s-a.json" <<'JSON'
[
  {
    "metadata": {"name": "payments-api-oom.1", "uid": "oom-uid-1", "namespace": "payments"},
    "involvedObject": {"kind": "Pod", "name": "payments-api-7d9f", "namespace": "payments"},
    "reason": "OOMKilled",
    "message": "Container payments-api was OOM killed (memory limit 512Mi)",
    "type": "Warning"
  },
  {
    "metadata": {"name": "payments-api-oom.2", "uid": "oom-uid-2", "namespace": "payments"},
    "involvedObject": {"kind": "Pod", "name": "payments-api-b41c", "namespace": "payments"},
    "reason": "OOMKilled",
    "message": "Container payments-api was OOM killed again after restart",
    "type": "Warning"
  }
]
JSON

echo "Signal injected. Letting the learning loop run for ${WAIT_SECONDS}s..."
sleep "${WAIT_SECONDS}"

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

uv run --project "${REPO_ROOT}" python "${SCRIPT_DIR}/valkyrie_flock_proof_verify.py" \
    --events "${EVENTS_FILE}" \
    --out-dir "${OUT_DIR}" \
    --teacher-state "${OUT_DIR}/k8s-a-state" \
    --student-state "${OUT_DIR}/k8s-b-state" \
    --teacher-id valkyrie-k8s-a \
    --student-id valkyrie-k8s-b \
    --control-id valkyrie-printer \
    --transport "${TRANSPORT}"
VERIFY_EXIT=$?

echo ""
echo "Artifacts: ${OUT_DIR}"
exit "${VERIFY_EXIT}"
