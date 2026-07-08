#!/bin/sh
set -eu

WORKSPACE="${SKULD_BOOTSTRAP_WORKSPACE:-${SKULD__SESSION__WORKSPACE_DIR:-/sandbox/workspace}}"
VENV="${SKULD_BOOTSTRAP_VENV:-/tmp/skuld-venv}"
LOG_FILE="${SKULD_BOOTSTRAP_LOG:-/tmp/skuld.log}"
PORT="${SKULD__PORT:-9200}"

python3 -m venv "${VENV}"
"${VENV}/bin/python" -m pip install --upgrade pip
"${VENV}/bin/pip" install "${WORKSPACE}"

TOOLS_DIR="${WORKSPACE}/containers/skuld/npm-tools"
if command -v npm >/dev/null 2>&1 && [ -f "${TOOLS_DIR}/package-lock.json" ]; then
  (cd "${TOOLS_DIR}" && npm ci --omit=dev)
  export PATH="${TOOLS_DIR}/node_modules/.bin:${PATH}"
fi

setup_cli_home() {
  env_name="$1"
  default_path="$2"
  writable_path="$3"
  current_value="$(eval "printf '%s' \"\${${env_name}:-}\"")"
  if [ -n "${current_value}" ]; then
    mkdir -p "${current_value}"
    return
  fi
  if [ -d "${default_path}" ]; then
    mkdir -p "${writable_path}"
    cp -R "${default_path}/." "${writable_path}/" 2>/dev/null || true
    export "${env_name}=${writable_path}"
    return
  fi
  mkdir -p "${default_path}"
  export "${env_name}=${default_path}"
}

setup_cli_home CODEX_HOME "${HOME}/.codex" /tmp/codex-home
setup_cli_home CLAUDE_CONFIG_DIR "${HOME}/.claude" /tmp/claude-home

nohup "${VENV}/bin/python" -m skuld >"${LOG_FILE}" 2>&1 &
pid="$!"

i=0
while [ "${i}" -lt "${SKULD_BOOTSTRAP_HEALTH_ATTEMPTS:-120}" ]; do
  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    tail -n 80 "${LOG_FILE}" >&2 || true
    exit 1
  fi
  if "${VENV}/bin/python" - "${PORT}" <<'PY' >/dev/null 2>&1
import http.client
import sys

conn = http.client.HTTPConnection("127.0.0.1", int(sys.argv[1]), timeout=1)
conn.request("GET", "/health")
response = conn.getresponse()
sys.exit(0 if 200 <= response.status < 500 else 1)
PY
  then
    exit 0
  fi
  i=$((i + 1))
  sleep 1
done

tail -n 80 "${LOG_FILE}" >&2 || true
exit 1
